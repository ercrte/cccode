from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from julycode.context.models import ContextSummary
from julycode.memory.models import SessionMemoryConfig
from julycode.memory.session_store import SessionJsonlStore, message_from_json, message_to_json
from julycode.providers.base import ChatMessage
from julycode.session_id import SessionId
from julycode.tools.base import ToolCall


def test_chat_message_round_trip_json() -> None:
    message = ChatMessage(
        role="assistant",
        content="调用工具",
        thinking="思考",
        tool_calls=(ToolCall("call-1", "read_file", {"path": "README.md"}, "{}"),),
        provider_payload={"signature": "sig"},
        metadata={"team_message_id": "msg-1"},
    )

    restored = message_from_json(message_to_json(message))

    assert restored == message


def test_rejects_invalid_message_json() -> None:
    with pytest.raises(ValueError):
        message_from_json({"role": "bad", "content": "x"})

    with pytest.raises(ValueError):
        message_from_json({"role": "assistant", "content": "x", "tool_calls": ["bad"]})


def test_store_appends_messages_as_jsonl(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path)
    session = store.create_session(SessionId("20260612-080910-abcd"))

    session.append_user_message("第一条")
    session.append_assistant_message(ChatMessage(role="assistant", content="第二条"))

    lines = _session_file(tmp_path, "20260612-080910-abcd").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["kind"] for line in lines] == ["message", "message"]


def test_store_uses_project_sessions_dir(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path)
    session = store.create_session(SessionId("20260612-080910-abcd"))

    session.append_user_message("hello")

    assert (tmp_path / ".julycode" / "sessions" / "20260612-080910-abcd.jsonl").exists()


def test_store_appends_checkpoint(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path)
    session = store.create_session(SessionId("20260612-080910-abcd"))
    session.append_user_message("hello")
    session.set_context_summary(
        ContextSummary(
            content="摘要",
            boundary_notice="边界",
            created_at="now",
            source_message_count=1,
            kept_message_count=1,
        )
    )

    session.append_checkpoint()

    records = [json.loads(line) for line in _session_file(tmp_path, "20260612-080910-abcd").read_text().splitlines()]
    assert records[-1]["kind"] == "checkpoint"
    assert records[-1]["context_summary"]["content"] == "摘要"


def test_checkpoint_restores_messages_and_summary(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path)
    session = store.create_session(SessionId("20260612-080910-abcd"))
    session.append_user_message("old")
    session.replace_messages([ChatMessage(role="user", content="new")])
    session.set_context_summary(
        ContextSummary(
            content="正式摘要",
            boundary_notice="边界",
            created_at="now",
            source_message_count=2,
            kept_message_count=1,
        )
    )
    session.append_checkpoint()

    restored, _ = store.load_session(SessionId("20260612-080910-abcd"))

    assert [message.content for message in restored.messages] == ["new"]
    assert restored.context_state.summary is not None
    assert restored.context_state.summary.content == "正式摘要"


def test_list_sessions_scans_jsonl_for_title_count_and_time(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path)
    first = store.create_session(SessionId("20260612-080910-abcd"))
    first.append_user_message("标题一\n详情")
    first.append_assistant_message(ChatMessage(role="assistant", content="ok"))
    second = store.create_session(SessionId("20260612-080911-abcd"))
    second.append_user_message("标题二")

    infos = store.list_sessions(now=datetime.now(timezone.utc))

    assert infos[0].title == "标题二"
    assert infos[1].title == "标题一"
    assert infos[1].message_count == 2


def test_list_sessions_does_not_require_meta_file(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path)
    session = store.create_session(SessionId("20260612-080910-abcd"))
    session.append_user_message("hello")
    (tmp_path / ".julycode" / "sessions" / "meta.json").write_text("{}", encoding="utf-8")

    [info] = store.list_sessions(now=datetime.now(timezone.utc))

    assert info.session_id == "20260612-080910-abcd"


def test_load_session_skips_bad_lines(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path)
    session = store.create_session(SessionId("20260612-080910-abcd"))
    session.append_user_message("valid")
    path = _session_file(tmp_path, "20260612-080910-abcd")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{bad\n")

    restored, report = store.load_session(SessionId("20260612-080910-abcd"))

    assert [message.content for message in restored.messages] == ["valid"]
    assert report.skipped_bad_lines == 1


def test_load_session_keeps_valid_lines_after_bad_line(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path)
    session = store.create_session(SessionId("20260612-080910-abcd"))
    path = _session_file(tmp_path, "20260612-080910-abcd")
    session.append_user_message("before")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{bad\n")
    session.append_assistant_message(ChatMessage(role="assistant", content="after"))

    restored, report = store.load_session(SessionId("20260612-080910-abcd"))

    assert [message.content for message in restored.messages] == ["before", "after"]
    assert report.skipped_bad_lines == 1


def test_latest_unexpired_session(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path, SessionMemoryConfig(retention_days=30))
    old_time = datetime.now(timezone.utc) - timedelta(days=40)
    new_time = datetime.now(timezone.utc)
    _write_message_record(tmp_path, "20260501-080910-abcd", "old", old_time)
    _write_message_record(tmp_path, "20260612-080910-abcd", "new", new_time)

    latest = store.latest_unexpired(now=new_time)

    assert latest is not None
    assert latest.session_id == "20260612-080910-abcd"


def test_cleanup_expired_sessions_keeps_memory_files(tmp_path: Path) -> None:
    store = SessionJsonlStore(tmp_path, SessionMemoryConfig(retention_days=30))
    now = datetime.now(timezone.utc)
    _write_message_record(tmp_path, "20260501-080910-abcd", "old", now - timedelta(days=40))
    _write_message_record(tmp_path, "20260612-080910-abcd", "new", now)
    memory_file = tmp_path / ".julycode" / "memory" / "project_knowledge" / "note.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("note", encoding="utf-8")

    removed = store.cleanup_expired(now=now)

    assert [info.session_id for info in removed] == ["20260501-080910-abcd"]
    assert not _session_file(tmp_path, "20260501-080910-abcd").exists()
    assert _session_file(tmp_path, "20260612-080910-abcd").exists()
    assert memory_file.exists()


def _session_file(tmp_path: Path, session_id: str) -> Path:
    return tmp_path / ".julycode" / "sessions" / f"{session_id}.jsonl"


def _write_message_record(tmp_path: Path, session_id: str, content: str, created_at: datetime) -> None:
    path = _session_file(tmp_path, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "message",
        "session_id": session_id,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "message": message_to_json(ChatMessage(role="user", content=content)),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
