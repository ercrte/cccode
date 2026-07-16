from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

from mewcode.agent import AgentLoopRunner
from mewcode.commands import AgentCommand
from mewcode.config import AgentConfig, RepoMapConfig
from mewcode.context.manager import ContextManager
from mewcode.context.models import ContextConfig
from mewcode.memory.models import KnowledgeContext, MemoryUpdateJob, SessionMemoryConfig
from mewcode.memory.session_store import SessionJsonlStore
from mewcode.providers.base import ChatMessage, ChatRequest, StreamEvent
from mewcode.repo_map.manager import RepoMapManager
from mewcode.session_id import SessionId
from mewcode.tools.base import ToolCall, ToolContext, ToolSpec
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry


class StubProvider:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        index = len(self.requests)
        if index == 1:
            message = ChatMessage(
                role="assistant",
                tool_calls=(ToolCall("read-1", "read_marker"),),
            )
        elif index == 2:
            message = ChatMessage(
                role="assistant",
                tool_calls=(ToolCall("edit-1", "rename_symbol"),),
            )
        else:
            message = ChatMessage(role="assistant", content="已完成签名更新。")
        await asyncio.sleep(0)
        yield StreamEvent(type="message_done", message=message)


class ReadMarkerTool:
    spec = ToolSpec(
        "read_marker",
        "读取目标源码",
        {"type": "object", "properties": {}, "additionalProperties": False},
        safety="read_only",
    )

    async def execute(self, arguments: Mapping[str, object], context: ToolContext) -> Mapping[str, object]:
        _ = arguments
        return {"content": (context.cwd / "target.py").read_text(encoding="utf-8")}


class RenameSymbolTool:
    spec = ToolSpec(
        "rename_symbol",
        "修改目标源码中的函数签名",
        {"type": "object", "properties": {}, "additionalProperties": False},
        safety="side_effect",
    )

    async def execute(self, arguments: Mapping[str, object], context: ToolContext) -> Mapping[str, object]:
        _ = arguments
        path = context.cwd / "target.py"
        source = path.read_text(encoding="utf-8")
        path.write_text(source.replace("old_signature", "new_signature"), encoding="utf-8")
        return {"path": "target.py"}


class CapturingMemoryManager:
    def __init__(self) -> None:
        self.jobs: list[MemoryUpdateJob] = []

    def runtime_context(self) -> KnowledgeContext:
        return KnowledgeContext()

    def schedule_update(self, *, job: MemoryUpdateJob, provider: object) -> None:
        _ = provider
        self.jobs.append(job)


@pytest.mark.asyncio
async def test_repo_map_refreshes_after_edit_and_stays_out_of_persistence(tmp_path: Path) -> None:
    source_path = tmp_path / "target.py"
    source_path.write_text(
        "def old_signature(alpha: int = 123) -> str:\n    return str(alpha)\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    registry.register(ReadMarkerTool())
    registry.register(RenameSymbolTool())
    executor = ToolExecutor(registry, ToolContext(cwd=tmp_path))
    provider = StubProvider()
    memory = CapturingMemoryManager()
    store = SessionJsonlStore(
        tmp_path,
        SessionMemoryConfig(project_dir=".mewcode", user_dir=str(tmp_path / "user-memory")),
    )
    session = store.create_session(SessionId("20260716-010203-abcd"))
    manager = RepoMapManager(tmp_path, RepoMapConfig(enabled=True, max_tokens=2000))
    await manager.start()
    assert await manager.wait_until_ready() is True

    runner = AgentLoopRunner(
        session,
        provider,
        registry,
        executor,
        AgentConfig(max_iterations=5),
        context_manager=ContextManager(ContextConfig(), tmp_path, max_output_tokens=4096),
        memory_manager=memory,  # type: ignore[arg-type]
        repo_map_manager=manager,
    )
    events = [
        event
        async for event in runner.run(
            AgentCommand(mode="normal", visible_text="请更新目标函数签名", model_text="请更新目标函数签名")
        )
    ]

    assert events[-1].type == "message_done"
    assert events[-1].message is not None
    assert events[-1].message.content == "已完成签名更新。"
    assert len(provider.requests) == 3
    blocks = [request.prompt.generated_context_blocks for request in provider.requests]  # type: ignore[union-attr]
    assert all(len(items) == 1 for items in blocks)
    first, second, third = (items[0] for items in blocks)
    assert first.text == second.text
    assert first.snapshot_id == second.snapshot_id
    assert "old_signature" in first.text
    assert third.snapshot_id != first.snapshot_id
    assert "old_signature" not in third.text
    assert "new_signature" in third.text

    persisted_path = store.sessions_dir / "20260716-010203-abcd.jsonl"
    persisted = persisted_path.read_text(encoding="utf-8")
    history_text = "\n".join(message.content for message in session.messages)
    assert "<mewcode_repo_map" not in history_text
    assert first.snapshot_id not in history_text
    assert "<mewcode_repo_map" not in persisted
    assert first.snapshot_id not in persisted
    restored, _report = store.load_session(SessionId("20260716-010203-abcd"))
    restored_text = "\n".join(message.content for message in restored.messages)
    assert "<mewcode_repo_map" not in restored_text
    assert first.snapshot_id not in restored_text
    assert len(memory.jobs) == 1
    memory_payload = "\n".join(
        [*(message.content for message in memory.jobs[0].turn_messages), memory.jobs[0].final_message.content]
    )
    assert "<mewcode_repo_map" not in memory_payload
    assert first.snapshot_id not in memory_payload

    await manager.close()
