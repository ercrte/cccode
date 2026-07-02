from __future__ import annotations

from pathlib import Path
from collections.abc import AsyncIterator
import asyncio

import pytest

from mewcode.teams.locking import ProcessLease
from mewcode.teams.models import TeamConfig, TeamDataError
from mewcode.teams.manager import TeamManager
from mewcode.teams.models import MemberSpawnRequest, MessageDraft, TeamActor
from mewcode.teams.runtime import TeamMemberRunnerFactory, TeamRuntimeSupervisor, _worktree_owner_id
from mewcode.teams.sessions import TeamMemberSessionStore
from mewcode.teams.store import TeamStore
from mewcode.teams.tools import create_team_tools
from mewcode.subagents.models import SubAgentRoleDefinition, SubAgentRoleFrontmatter
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import create_default_registry
from mewcode.worktrees import WorktreeManager
from mewcode.providers.base import ChatMessage, ChatRequest, StreamEvent
from tests.test_subagents import FakeProvider, make_app_config
from tests.test_worktrees import init_repository


class BlockingProvider:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            await self.release.wait()
            yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="done"))
        finally:
            self.active -= 1


def worker_role() -> SubAgentRoleDefinition:
    return SubAgentRoleDefinition(
        frontmatter=SubAgentRoleFrontmatter(
            name="worker",
            description="worker",
            tools_allow=("read_file",),
            tools_deny=(),
            model="inherit",
            max_iterations=4,
            permission_mode="inherit",
        ),
        body="处理团队任务。",
        source_scope="project",
        source_path="worker.md",
    )


async def setup_runtime(tmp_path: Path, provider):
    repo = init_repository(tmp_path / "repo")
    config = make_app_config(tmp_path)
    store = TeamStore(repo, root=tmp_path / "teams")
    manager = TeamManager(repo, store=store)
    await manager.create_team("demo")
    registry = create_default_registry()
    for tool in create_team_tools(manager):
        registry.register(tool)
    executor = ToolExecutor(registry, ToolContext(repo))
    factory = TeamMemberRunnerFactory(
        registry=registry,
        executor=executor,
        config=config,
        provider=provider,
        provider_resolver=lambda _model: provider,
    )
    role = worker_role()
    runtime = TeamRuntimeSupervisor(
        manager=manager,
        worktrees=WorktreeManager(repo, config.sub_agents.worktree),
        runner_factory=factory,
        role_provider=lambda name: role if name == "worker" else None,
    )
    manager.set_runtime(runtime)
    return repo, config, store, manager, runtime, role


@pytest.mark.asyncio
async def test_process_lease_allows_only_one_owner(tmp_path: Path) -> None:
    settings = TeamConfig(lock_timeout_seconds=0.03, lock_retry_interval_seconds=0.005, stale_lock_seconds=0.1)
    first = ProcessLease(tmp_path / "worker.lease", settings)
    second = ProcessLease(tmp_path / "worker.lease", settings)
    await first.acquire()
    with pytest.raises(TeamDataError, match="超时"):
        await second.acquire()
    await first.release()
    await second.acquire()
    await second.release()


@pytest.mark.asyncio
async def test_idle_resume_keeps_session_and_worktree(tmp_path: Path) -> None:
    repo = init_repository(tmp_path / "repo")
    config = make_app_config(tmp_path)
    store = TeamStore(repo, root=tmp_path / "teams")
    manager = TeamManager(repo, store=store)
    await manager.create_team("demo")
    registry = create_default_registry()
    for tool in create_team_tools(manager):
        registry.register(tool)
    provider = FakeProvider("成员处理完成")
    executor = ToolExecutor(registry, ToolContext(repo))
    factory = TeamMemberRunnerFactory(
        registry=registry,
        executor=executor,
        config=config,
        provider=provider,
        provider_resolver=lambda _model: provider,
    )
    role = SubAgentRoleDefinition(
        frontmatter=SubAgentRoleFrontmatter(
            name="worker",
            description="worker",
            tools_allow=("read_file",),
            tools_deny=(),
            model="inherit",
            max_iterations=4,
            permission_mode="inherit",
        ),
        body="处理团队任务。",
        source_scope="project",
        source_path="worker.md",
    )
    runtime = TeamRuntimeSupervisor(
        manager=manager,
        worktrees=WorktreeManager(repo, config.sub_agents.worktree),
        runner_factory=factory,
        role_provider=lambda name: role if name == "worker" else None,
    )
    manager.set_runtime(runtime)
    member = await manager.spawn_member(MemberSpawnRequest("worker", "worker"))
    lead = TeamActor("demo", "lead", "lead", repo)
    await manager.send_message(lead, MessageDraft("worker", "message", "记住代号 A"))
    for _ in range(100):
        if (await store.get_member("demo", "worker")).status == "idle" and not runtime._tasks:
            break
        await __import__("asyncio").sleep(0.01)
    first_session = Path(member.session_path).read_text(encoding="utf-8")
    await manager.send_message(lead, MessageDraft("worker", "message", "继续"))
    for _ in range(100):
        if (await store.get_member("demo", "worker")).status == "idle" and not runtime._tasks:
            break
        await __import__("asyncio").sleep(0.01)
    second_session = Path(member.session_path).read_text(encoding="utf-8")
    assert len(second_session) > len(first_session)
    assert Path(member.worktree_root).exists()
    assert len(provider.requests) == 2
    await manager.shutdown()


def test_member_session_preserves_session_id(tmp_path: Path) -> None:
    store = TeamMemberSessionStore(tmp_path / "member.jsonl")
    session = store.create()
    original_id = session.context_state.session_id
    session.append_user_message("第一条消息")
    session.append_checkpoint()

    restored, report = store.load()

    assert report.restored
    assert restored.context_state.session_id == original_id
    assert restored.messages[0].content == "第一条消息"


def test_member_worktree_owner_id_is_unambiguous_and_bounded() -> None:
    assert _worktree_owner_id("a-b", "c") != _worktree_owner_id("a", "b-c")
    assert len(_worktree_owner_id("a" * 64, "b" * 64)) <= 64


@pytest.mark.asyncio
async def test_terminate_running_member_keeps_artifacts_and_releases_runtime(tmp_path: Path) -> None:
    provider = BlockingProvider()
    repo, _config, store, manager, runtime, _role = await setup_runtime(tmp_path, provider)
    member = await manager.spawn_member(MemberSpawnRequest("worker", "worker"))
    lead = TeamActor("demo", "lead", "lead", repo)
    await manager.send_message(lead, MessageDraft("worker", "message", "开始执行"))
    await asyncio.wait_for(provider.started.wait(), timeout=1)

    terminated = await manager.terminate_member("worker")

    assert terminated.status == "terminated"
    assert ("demo", "worker") not in runtime._tasks
    assert not (store.root / "demo/runtime/worker.lease").exists()
    assert Path(member.worktree_root).exists()
    assert Path(member.session_path).exists()
    unread = await manager._service("demo").mailbox.unread(lead)
    assert any(message.protocol == "member_terminated" for message in unread)
    assert not any(message.protocol == "member_idle" for message in unread)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_concurrent_wake_never_runs_two_member_instances(tmp_path: Path) -> None:
    provider = BlockingProvider()
    repo, _config, store, manager, runtime, _role = await setup_runtime(tmp_path, provider)
    await manager.spawn_member(MemberSpawnRequest("worker", "worker"))
    lead = TeamActor("demo", "lead", "lead", repo)
    await manager.send_message(lead, MessageDraft("worker", "message", "first"))
    await asyncio.wait_for(provider.started.wait(), timeout=1)
    await manager.send_message(lead, MessageDraft("worker", "message", "second"))

    assert len(provider.requests) == 1
    assert provider.max_active == 1
    provider.release.set()
    for _ in range(200):
        if len(provider.requests) >= 2 and not runtime._tasks:
            break
        await asyncio.sleep(0.01)

    assert len(provider.requests) == 2
    assert provider.max_active == 1
    assert (await store.get_member("demo", "worker")).status == "idle"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_idle_member_recovers_across_process_restart(tmp_path: Path) -> None:
    first_provider = FakeProvider("first done")
    repo, config, store, first, first_runtime, role = await setup_runtime(tmp_path, first_provider)
    member = await first.spawn_member(MemberSpawnRequest("worker", "worker"))
    lead = TeamActor("demo", "lead", "lead", repo)
    await first.send_message(lead, MessageDraft("worker", "message", "记住历史"))
    for _ in range(100):
        if (await store.get_member("demo", "worker")).status == "idle" and not first_runtime._tasks:
            break
        await asyncio.sleep(0.01)
    before, _ = TeamMemberSessionStore(Path(member.session_path)).load()
    await first.shutdown()

    second_provider = FakeProvider("second done")
    second = TeamManager(repo, store=TeamStore(repo, root=tmp_path / "teams"))
    await second.open_team("demo")
    registry = create_default_registry()
    for tool in create_team_tools(second):
        registry.register(tool)
    factory = TeamMemberRunnerFactory(
        registry=registry,
        executor=ToolExecutor(registry, ToolContext(repo)),
        config=config,
        provider=second_provider,
        provider_resolver=lambda _model: second_provider,
    )
    runtime = TeamRuntimeSupervisor(
        manager=second,
        worktrees=WorktreeManager(repo, config.sub_agents.worktree),
        runner_factory=factory,
        role_provider=lambda name: role if name == "worker" else None,
    )
    second.set_runtime(runtime)
    await second.send_message(lead, MessageDraft("worker", "message", "继续"))
    for _ in range(100):
        if (await second.store.get_member("demo", "worker")).status == "idle" and not runtime._tasks:
            break
        await asyncio.sleep(0.01)
    after, _ = TeamMemberSessionStore(Path(member.session_path)).load()

    assert before.context_state.session_id == after.context_state.session_id
    assert Path(member.worktree_root).exists()
    unread = await second._service("demo").mailbox.unread(lead)
    assert any(message.protocol == "member_resumed" for message in unread)
    assert len(second_provider.requests) == 1
    await second.shutdown()
