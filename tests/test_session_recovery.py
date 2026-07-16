from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mewcode.context.models import ContextCompactionReport, ContextConfig, ContextLimitError, PreparedChatRequest, RequestFootprint
from mewcode.memory.index import MemoryIndexBuilder
from mewcode.memory.manager import SessionMemoryManager
from mewcode.memory.models import BootstrapOptions, MemoryUpdateJob, SessionMemoryConfig
from mewcode.memory.notes import MemoryNoteStore
from mewcode.memory.recovery import SessionBootstrapper, SessionHistoryValidator
from mewcode.memory.session_store import SessionJsonlStore, message_to_json
from mewcode.providers.base import ChatMessage, ChatRequest, StreamEvent
from mewcode.prompting.base import GeneratedContextBlock, PromptBundle
from mewcode.session import ChatSession
from mewcode.session_id import SessionId
from mewcode.tools.base import ToolCall
from tests.test_memory_notes import note


class FakeProvider:
    async def stream_chat(self, request: ChatRequest):
        _ = request
        yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="ok"))


class FakeContextManager:
    def __init__(self, *, compacted: bool = False, fail: bool = False) -> None:
        self.compacted = compacted
        self.fail = fail
        self.prepare_called = False

    async def prepare_request(self, *, session, provider, tools, prompt_factory, mode="auto"):
        _ = provider, tools, prompt_factory, mode
        self.prepare_called = True
        if self.fail:
            raise ContextLimitError("too large")
        return PreparedChatRequest(
            request=session.build_request(),
            footprint=RequestFootprint(chars=1, estimated_tokens=1),
            report=ContextCompactionReport(
                mode="manual",
                light_compacted=False,
                heavy_compacted=self.compacted,
            ),
        )


def test_repo_map_prompt_is_not_saved_or_restored(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path, _config(tmp_path))
    session_id = SessionId("20260612-080910-abcd")
    session = store.create_session(session_id)
    session.append_user_message("请定位目标函数")
    repo_map = GeneratedContextBlock(
        name="repo_map",
        title="仓库地图",
        text='<mewcode_repo_map revision="abc123">\ntarget.py:1\n</mewcode_repo_map>',
        kind="repo_map",
        snapshot_id="snapshot-secret-id",
    )
    session.build_request(
        prompt=PromptBundle(stable_blocks=(), runtime_blocks=(), generated_context_blocks=(repo_map,))
    )
    session.append_assistant_message(ChatMessage(role="assistant", content="请先读取 target.py"))
    session.append_checkpoint()

    persisted = (store.sessions_dir / f"{session_id}.jsonl").read_text(encoding="utf-8")
    restored, _report = store.load_session(session_id)
    restored_text = "\n".join(message.content for message in restored.messages)

    assert "<mewcode_repo_map" not in persisted
    assert "snapshot-secret-id" not in persisted
    assert "<mewcode_repo_map" not in restored_text
    assert "snapshot-secret-id" not in restored_text


def test_validator_keeps_complete_tool_segments() -> None:
    messages = [
        ChatMessage(role="user", content="u"),
        ChatMessage(role="assistant", tool_calls=(ToolCall("a", "read"), ToolCall("b", "search"))),
        ChatMessage(role="tool", content="ra", tool_call_id="a"),
        ChatMessage(role="tool", content="rb", tool_call_id="b"),
        ChatMessage(role="assistant", content="done"),
    ]

    result = SessionHistoryValidator().truncate_to_protocol_safe(messages)

    assert result.messages == tuple(messages)
    assert result.truncated_count == 0


def test_validator_truncates_invalid_tool_history() -> None:
    validator = SessionHistoryValidator()
    missing_result = [
        ChatMessage(role="user", content="u"),
        ChatMessage(role="assistant", tool_calls=(ToolCall("a", "read"),)),
    ]
    orphan_tool = [ChatMessage(role="user", content="u"), ChatMessage(role="tool", content="r", tool_call_id="a")]
    duplicate_result = [
        ChatMessage(role="user", content="u"),
        ChatMessage(role="assistant", tool_calls=(ToolCall("a", "read"),)),
        ChatMessage(role="tool", content="r", tool_call_id="a"),
        ChatMessage(role="tool", content="r2", tool_call_id="a"),
    ]

    assert [item.content for item in validator.truncate_to_protocol_safe(missing_result).messages] == ["u"]
    assert [item.content for item in validator.truncate_to_protocol_safe(orphan_tool).messages] == ["u"]
    assert len(validator.truncate_to_protocol_safe(duplicate_result).messages) == 3


@pytest.mark.asyncio
async def test_bootstrap_restores_latest_session_by_default(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path, _config(tmp_path))
    old_session = store.create_session(SessionId("20260612-080910-abcd"))
    old_session.append_user_message("old")
    new_session = store.create_session(SessionId("20260612-080911-abcd"))
    new_session.append_user_message("new")
    bootstrapper = _bootstrapper(tmp_path, store)

    result = await bootstrapper.bootstrap(
        options=BootstrapOptions(),
        provider=FakeProvider(),
        context_manager=FakeContextManager(),
    )

    assert result.restore_report.restored is True
    assert result.session.messages[0].content == "new"


@pytest.mark.asyncio
async def test_bootstrap_can_start_new_empty_session(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path, _config(tmp_path))
    session = store.create_session(SessionId("20260612-080910-abcd"))
    session.append_user_message("old")

    bootstrapper = _bootstrapper(tmp_path, store)
    bootstrapper.note_store.write_note(note("user-rule", scope="user", category="preference", body="默认中文"))
    bootstrapper.note_store.write_note(note("project-rule", body="使用 pytest"))

    result = await bootstrapper.bootstrap(
        options=BootstrapOptions(new_session=True),
        provider=FakeProvider(),
        context_manager=FakeContextManager(),
    )

    assert result.restore_report.restored is False
    assert result.session.messages == []
    assert result.session.context_state.session_id != "20260612-080910-abcd"
    assert result.knowledge_context.user_memory_index is not None
    assert "默认中文" in result.knowledge_context.user_memory_index.content
    assert result.knowledge_context.project_memory_index is not None
    assert "使用 pytest" in result.knowledge_context.project_memory_index.content
    assert "old" not in result.knowledge_context.user_memory_index.content


@pytest.mark.asyncio
async def test_bootstrap_adds_time_gap_notice_for_old_session(tmp_path: Path) -> None:
    config = _config(tmp_path, time_gap_hours=1)
    store = SessionJsonlStore(tmp_path, config)
    _write_message_record(tmp_path, "20260612-080910-abcd", "old", datetime.now(timezone.utc) - timedelta(hours=2))

    result = await _bootstrapper(tmp_path, store, config).bootstrap(
        options=BootstrapOptions(),
        provider=FakeProvider(),
        context_manager=FakeContextManager(),
    )

    assert "上次活动" in result.restore_report.time_gap_notice
    assert result.knowledge_context.restore_report is not None


@pytest.mark.asyncio
async def test_bootstrap_compacts_oversized_restored_session(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path, _config(tmp_path))
    session = store.create_session(SessionId("20260612-080910-abcd"))
    session.append_user_message("x" * 100)

    result = await _bootstrapper(tmp_path, store).bootstrap(
        options=BootstrapOptions(),
        provider=FakeProvider(),
        context_manager=FakeContextManager(compacted=True),
    )

    assert result.restore_report.compacted is True


@pytest.mark.asyncio
async def test_bootstrap_starts_empty_when_restored_session_still_over_limit(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path, _config(tmp_path))
    session = store.create_session(SessionId("20260612-080910-abcd"))
    session.append_user_message("x" * 100)

    result = await _bootstrapper(tmp_path, store).bootstrap(
        options=BootstrapOptions(),
        provider=FakeProvider(),
        context_manager=FakeContextManager(fail=True),
    )

    assert result.restore_report.restored is False
    assert "超过上下文预算" in result.restore_report.started_empty_reason
    assert result.session.messages == []


@pytest.mark.asyncio
async def test_bootstrap_disabled_memory_starts_plain_session(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=False)

    result = await _bootstrapper(tmp_path, SessionJsonlStore(tmp_path, _config(tmp_path)), config).bootstrap(
        options=BootstrapOptions(),
        provider=FakeProvider(),
        context_manager=FakeContextManager(),
    )

    assert result.session.recorder is None
    assert result.restore_report.restored is False


@pytest.mark.asyncio
async def test_memory_manager_returns_runtime_context(tmp_path: Path) -> None:
    manager = SessionMemoryManager(tmp_path, _config(tmp_path))
    store = manager.bootstrapper.store
    session = store.create_session(SessionId("20260612-080910-abcd"))
    session.append_user_message("hello")

    await manager.bootstrap(
        options=BootstrapOptions(),
        provider=FakeProvider(),
        context_manager=FakeContextManager(),
    )

    assert manager.runtime_context().restore_report is not None


def _config(tmp_path: Path, **overrides) -> SessionMemoryConfig:
    data = {"user_dir": str(tmp_path / "home" / ".mewcode")}
    data.update(overrides)
    return SessionMemoryConfig(**data)


def _bootstrapper(
    tmp_path: Path,
    store: SessionJsonlStore,
    config: SessionMemoryConfig | None = None,
) -> SessionBootstrapper:
    config = config or _config(tmp_path)
    note_store = MemoryNoteStore(tmp_path, config)
    return SessionBootstrapper(
        tmp_path,
        config,
        store=store,
        note_store=note_store,
        index_builder=MemoryIndexBuilder(note_store, config),
    )


def _write_message_record(tmp_path: Path, session_id: str, content: str, created_at: datetime) -> None:
    path = tmp_path / ".mewcode" / "sessions" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "message",
        "session_id": session_id,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "message": message_to_json(ChatMessage(role="user", content=content)),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
