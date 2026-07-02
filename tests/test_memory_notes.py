from __future__ import annotations

from pathlib import Path

from mewcode.memory.models import MemoryNote, SessionMemoryConfig
from mewcode.memory.notes import MemoryNoteStore
from mewcode.session_id import SessionId


def note(
    note_id: str,
    *,
    scope: str = "project",
    category: str = "project_knowledge",
    body: str = "正文",
) -> MemoryNote:
    return MemoryNote(
        note_id=note_id,
        scope=scope,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        title=f"标题 {note_id}",
        body=body,
        source_session_id=SessionId("20260612-080910-abcd"),
        created_at="2026-06-12T08:09:10Z",
        updated_at="2026-06-12T08:09:10Z",
        tags=("tag",),
    )


def test_write_and_read_memory_note(tmp_path: Path) -> None:
    store = MemoryNoteStore(tmp_path, SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".mewcode")))

    path = store.write_note(note("rule"))
    restored = store.read_note("project", "rule")

    assert path.exists()
    assert restored is not None
    assert restored.title == "标题 rule"
    assert restored.body == "正文"


def test_notes_are_grouped_by_scope_and_category(tmp_path: Path) -> None:
    store = MemoryNoteStore(tmp_path, SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".mewcode")))
    store.write_note(note("pref", scope="user", category="preference"))
    store.write_note(note("corr", scope="user", category="correction"))
    store.write_note(note("knowledge", scope="project", category="project_knowledge"))
    store.write_note(note("ref", scope="project", category="reference"))

    user_notes = store.list_notes("user")
    project_notes = store.list_notes("project")

    assert {item.category for item in user_notes} == {"preference", "correction"}
    assert {item.category for item in project_notes} == {"project_knowledge", "reference"}


def test_note_store_redacts_sensitive_values(tmp_path: Path) -> None:
    store = MemoryNoteStore(
        tmp_path,
        SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".mewcode")),
        secrets=("plain-secret-value",),
    )
    store.write_note(
        note(
            "secret",
            body="api sk-test-secret-1234567890 Bearer abcdefghijklmnop plain-secret-value 普通文本",
        )
    )

    restored = store.read_note("project", "secret")

    assert restored is not None
    assert "sk-test-secret" not in restored.body
    assert "abcdefghijklmnop" not in restored.body
    assert "plain-secret-value" not in restored.body
    assert "普通文本" in restored.body
