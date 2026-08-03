from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from julycode.config import AgentConfig, AppConfig
from julycode.context.models import ContextConfig
from julycode.memory.models import SessionMemoryConfig
from julycode.permissions import PermissionConfig
from julycode.permissions.controller import create_permission_controller
from julycode.providers.base import ChatMessage, ChatRequest, StreamEvent
from julycode.providers.openai import OpenAIProvider
from julycode.session import ChatSession
from julycode.subagents.models import SubAgentConfig
from julycode.teams.models import MessageDraft
from julycode.teams.sessions import TeamMemberSessionStore
from julycode.tools.base import RuntimePrincipal, ToolCall, ToolContext
from julycode.tools.executor import ToolExecutor
from julycode.tools.registry import create_default_registry
from julycode.tui.app import JulyCodeApp
from julycode.tui.widgets import Composer, MessageView
from tests.e2e_mock_openai_server import Handler, _team_e2e_tool_calls
from tests.test_worktrees import git, init_repository


class InProcessTeamMockProvider:
    def __init__(self, config: AppConfig) -> None:
        self.encoder = OpenAIProvider(config)
        self.calls = 0

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        body = self.encoder._payload(request)
        scripted = _team_e2e_tool_calls(body)
        tool_calls = ()
        if scripted:
            self.calls += 1
            tool_calls = tuple(
                ToolCall(
                    f"call-team-{self.calls}-{index}",
                    str(item["name"]),
                    dict(item["arguments"]),
                    raw_arguments=json.dumps(item["arguments"], ensure_ascii=False),
                )
                for index, item in enumerate(scripted)
            )
        text = "" if tool_calls else Handler.__new__(Handler)._response_text(body)
        yield StreamEvent(
            type="message_done",
            message=ChatMessage(role="assistant", content=text, tool_calls=tool_calls),
        )


@pytest.mark.asyncio
async def test_real_tui_team_end_to_end_without_tmux_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """沙箱不能创建 socket 时，仍验证同一 TUI、mock 决策和 Git 完整链路。"""
    repository = init_repository(tmp_path / "repo")
    role_dir = repository / ".julycode" / "agents"
    role_dir.mkdir(parents=True)
    (role_dir / "team-writer.md").write_text(
        """---
name: team-writer
description: 实现并提交团队代码任务。
tools_allow: [read_file, find_files, search_code, write_file, edit_file, run_command]
tools_deny: []
model: inherit
max_iterations: 20
permission_mode: permissive
---
只处理已领取的团队任务，修改后提交并报告 commit。
""",
        encoding="utf-8",
    )
    git(repository, "add", ".julycode/agents/team-writer.md")
    git(repository, "commit", "-qm", "add team writer role")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)

    config = AppConfig(
        protocol="openai",
        model="mock-team",
        base_url="http://mock.invalid/v1",
        api_key="test-key",
        agent=AgentConfig(max_iterations=40),
        permissions=PermissionConfig(mode="permissive"),
        context=ContextConfig(enabled=False),
        memory=SessionMemoryConfig(enabled=False),
        sub_agents=SubAgentConfig(default_max_iterations=20),
    )
    registry = create_default_registry()
    executor = ToolExecutor(registry, ToolContext(repository))
    app = JulyCodeApp(
        ChatSession(),
        InProcessTeamMockProvider(config),
        config,
        registry,
        executor,
    )
    app.set_permission_controller(create_permission_controller(repository, config.permissions, app))

    async with app.run_test(size=(180, 50)) as pilot:
        composer = app.query_one(Composer)
        composer.value = "团队端到端：创建长期团队，拆成两个并行代码任务和一个依赖任务；bob 需要审批，成员直接协作并汇总。"
        await pilot.press("enter")
        for _ in range(1500):
            if app._generation_task is None and not composer.disabled:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("团队端到端任务在 15 秒内未完成")
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    snapshot = await app.team_manager.status()
    assert len(snapshot.tasks) == 3
    assert all(task.status == "completed" for task in snapshot.tasks)
    assert {task.kind for task in snapshot.tasks} == {"code", "research"}
    assert all(task.commit for task in snapshot.tasks if task.kind == "code")
    assert set(snapshot.team.members) == {"alice", "bob"}
    assert all(member.status == "idle" for member in snapshot.team.members.values())
    approval = await app.team_manager._service("e2e-team").approvals.current_for_member(
        await app.team_manager.actor_for(
            RuntimePrincipal("team_member", "e2e-team", "bob")
        )
    )
    assert approval is not None and approval.status == "approved"
    assert "本阶段未自动执行 Git 合并" in rendered

    mailbox_root = home / ".julycode" / "teams" / "e2e-team" / "mailboxes"
    direct_messages = []
    for path in mailbox_root.glob("*.json"):
        direct_messages.extend(json.loads(path.read_text(encoding="utf-8"))["messages"])
    assert any(message["protocol"] == "message" and message["sender"] in {"alice", "bob"} for message in direct_messages)
    for member in snapshot.team.members.values():
        assert Path(member.worktree_root).exists()
        assert Path(member.session_path).exists()

    alice = snapshot.team.members["alice"]
    before, _ = TeamMemberSessionStore(Path(alice.session_path)).load()
    before_size = Path(alice.session_path).stat().st_size
    second_registry = create_default_registry()
    second_executor = ToolExecutor(second_registry, ToolContext(repository))
    second = JulyCodeApp(
        ChatSession(),
        InProcessTeamMockProvider(config),
        config,
        second_registry,
        second_executor,
    )
    second.set_permission_controller(create_permission_controller(repository, config.permissions, second))
    await second.team_manager.open_team("e2e-team")

    async with second.run_test(size=(180, 50)):
        lead = await second.team_manager.actor_for(RuntimePrincipal())
        await second.team_manager.send_message(
            lead,
            MessageDraft("alice", "message", "团队端到端：继续说明你之前改了什么。"),
        )
        for _ in range(500):
            runtime = second.team_manager.runtime
            if runtime is not None and not runtime._tasks:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("重启后的成员在 5 秒内未恢复为空闲")

    after, _ = TeamMemberSessionStore(Path(alice.session_path)).load()
    assert after.context_state.session_id == before.context_state.session_id
    assert Path(alice.session_path).stat().st_size > before_size
    lead = await second.team_manager.actor_for(RuntimePrincipal())
    unread = await second.team_manager._service("e2e-team").mailbox.unread(lead)
    assert any(message.protocol == "member_resumed" and message.sender == "alice" for message in unread)
    assert any("恢复原上下文" in message.body for message in unread)
