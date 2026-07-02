from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mewcode.memory.index import MemoryIndexBuilder
from mewcode.memory.manager import SessionMemoryManager
from mewcode.memory.models import KnowledgeContext, MemoryUpdateJob, SessionMemoryConfig
from mewcode.memory.notes import MemoryNoteStore
from mewcode.memory.updater import MemoryNoteUpdater, MemoryUpdateError
from mewcode.providers.base import ChatMessage, ChatRequest, StreamEvent
from mewcode.session_id import SessionId
from tests.test_memory_notes import note


class FakeProvider:
    def __init__(self, content: str, *, tool_call: bool = False, fail: bool = False) -> None:
        self.content = content
        self.tool_call = tool_call
        self.fail = fail
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        await asyncio.sleep(0)
        if self.fail:
            yield StreamEvent(type="error", error="bad")
            return
        message = ChatMessage(role="assistant", content=self.content)
        if self.tool_call:
            from mewcode.tools.base import ToolCall

            message.tool_calls = (ToolCall("call-1", "read_file"),)
        yield StreamEvent(type="message_done", message=message)


def make_updater(tmp_path: Path) -> tuple[MemoryNoteUpdater, MemoryNoteStore]:
    config = SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".mewcode"))
    store = MemoryNoteStore(tmp_path, config)
    return MemoryNoteUpdater(store, MemoryIndexBuilder(store, config)), store


def make_job(tmp_path: Path) -> MemoryUpdateJob:
    return MemoryUpdateJob(
        session_id=SessionId("20260612-080910-abcd"),
        cwd=tmp_path,
        turn_messages=(ChatMessage(role="user", content="以后默认中文"),),
        final_message=ChatMessage(role="assistant", content="已记住"),
        knowledge_context=KnowledgeContext(),
    )


@pytest.mark.asyncio
async def test_updater_requests_without_tools(tmp_path: Path) -> None:
    updater, _store = make_updater(tmp_path)
    provider = FakeProvider('{"operations": []}')

    await updater.update(job=make_job(tmp_path), provider=provider)

    assert provider.requests[0].tools == ()


@pytest.mark.asyncio
async def test_updater_prompt_contains_categories_and_scopes(tmp_path: Path) -> None:
    updater, _store = make_updater(tmp_path)
    provider = FakeProvider('{"operations": []}')

    await updater.update(job=make_job(tmp_path), provider=provider)

    prompt = provider.requests[0].messages[0].content
    assert "preference" in prompt
    assert "correction" in prompt
    assert "project_knowledge" in prompt
    assert "reference" in prompt
    assert "scope 只能是 user 或 project" in prompt


@pytest.mark.asyncio
async def test_updater_creates_and_updates_notes(tmp_path: Path) -> None:
    updater, store = make_updater(tmp_path)
    store.write_note(note("language", scope="user", category="preference", body="旧"))
    operations = {
        "operations": [
            {
                "action": "create",
                "scope": "project",
                "category": "project_knowledge",
                "note_id": "rule",
                "title": "测试规则",
                "body": "测试以 test_memory_ 开头",
            },
            {
                "action": "update",
                "scope": "user",
                "category": "preference",
                "note_id": "language",
                "title": "语言偏好",
                "body": "默认中文",
            },
        ]
    }

    indexes = await updater.update(job=make_job(tmp_path), provider=FakeProvider(json.dumps(operations)))

    assert store.read_note("project", "rule") is not None
    assert store.read_note("user", "language").body == "默认中文"  # type: ignore[union-attr]
    assert {item.scope for item in indexes} == {"project", "user"}


@pytest.mark.asyncio
async def test_updater_skip_does_not_write_note(tmp_path: Path) -> None:
    updater, store = make_updater(tmp_path)

    await updater.update(job=make_job(tmp_path), provider=FakeProvider('{"operations": [{"action": "skip"}]}'))

    assert store.list_notes("project") == ()
    assert store.list_notes("user") == ()


@pytest.mark.asyncio
async def test_updater_deduplicates_by_model_operations(tmp_path: Path) -> None:
    updater, store = make_updater(tmp_path)
    operations = {
        "operations": [
            {
                "action": "create",
                "scope": "user",
                "category": "preference",
                "note_id": "language",
                "title": "语言偏好",
                "body": "默认中文",
            },
            {"action": "skip"},
        ]
    }

    await updater.update(job=make_job(tmp_path), provider=FakeProvider(json.dumps(operations)))

    assert len(store.list_notes("user")) == 1


@pytest.mark.asyncio
async def test_updater_fails_without_partial_writes(tmp_path: Path) -> None:
    updater, store = make_updater(tmp_path)

    with pytest.raises(MemoryUpdateError):
        await updater.update(job=make_job(tmp_path), provider=FakeProvider('{"operations": [{"action": "create"}]}'))

    assert store.list_notes("project") == ()

    with pytest.raises(MemoryUpdateError):
        await updater.update(job=make_job(tmp_path), provider=FakeProvider("bad"))

    with pytest.raises(MemoryUpdateError):
        await updater.update(job=make_job(tmp_path), provider=FakeProvider("{}", tool_call=True))

    with pytest.raises(MemoryUpdateError):
        await updater.update(job=make_job(tmp_path), provider=FakeProvider("{}", fail=True))


@pytest.mark.asyncio
async def test_memory_manager_background_update_failure_is_captured(tmp_path: Path) -> None:
    config = SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".mewcode"))
    updater, _store = make_updater(tmp_path)

    class FailingUpdater:
        async def update(self, *, job, provider):
            _ = job, provider
            raise RuntimeError("boom")

    manager = SessionMemoryManager(tmp_path, config, updater=FailingUpdater())  # type: ignore[arg-type]
    manager.schedule_update(job=make_job(tmp_path), provider=FakeProvider("{}"))

    await manager.wait_for_updates()

    assert manager.warnings
