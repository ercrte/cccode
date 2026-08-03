from __future__ import annotations

from pathlib import Path

from julycode.memory.models import MemoryNote, SessionMemoryConfig
from julycode.memory.notes import MemoryNoteStore
from julycode.session_id import SessionId


def note(
    note_id: str,
    *,
    scope: str = "project",
    category: str = "project_knowledge",
    body: str = "正文",
    critical: bool = False,
    evidence: tuple[str, ...] = (),
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
        source_evidence=evidence,
        critical=critical,
        confidence=0.99 if critical else 0.9,
    )


def test_write_and_read_memory_note(tmp_path: Path) -> None:
    store = MemoryNoteStore(tmp_path, SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".julycode")))

    path = store.write_note(note("rule"))
    restored = store.read_note("project", "rule")

    assert path.exists()
    assert restored is not None
    assert restored.title == "标题 rule"
    assert restored.body == "正文"


def test_notes_are_grouped_by_scope_and_category(tmp_path: Path) -> None:
    store = MemoryNoteStore(tmp_path, SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".julycode")))
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
        SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".julycode")),
        secrets=("plain-secret-value",),
    )
    store.write_note(
        note(
            "secret",
            body="api sk-test-secret-1234567890 Bearer abcdefghijklmnop plain-secret-value 普通文本",
            evidence=("password=abcdefghi 普通证据",),
        )
    )

    restored = store.read_note("project", "secret")

    assert restored is not None
    assert "sk-test-secret" not in restored.body
    assert "abcdefghijklmnop" not in restored.body
    assert "plain-secret-value" not in restored.body
    assert "普通文本" in restored.body
    assert "abcdefghi" not in restored.source_evidence[0]


def test_note_metadata_round_trip(tmp_path: Path) -> None:
    store = MemoryNoteStore(tmp_path, SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".julycode")))

    store.write_note(note("critical", scope="user", category="preference", critical=True, evidence=("以后始终用中文",)))
    restored = store.read_note("user", "critical")

    assert restored is not None
    assert restored.source_evidence == ("以后始终用中文",)
    assert restored.critical is True
    assert restored.confidence == 0.99


def test_reads_legacy_note_without_quality_metadata(tmp_path: Path) -> None:
    store = MemoryNoteStore(tmp_path, SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".julycode")))
    path = tmp_path / ".julycode" / "memory" / "project_knowledge" / "legacy.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nnote_id: legacy\nscope: project\ncategory: project_knowledge\ntitle: 旧笔记\n"
        "source_session_id: old\ncreated_at: old\nupdated_at: old\ntags: []\n---\n正文\n",
        encoding="utf-8",
    )

    restored = store.read_note("project", "legacy")

    assert restored is not None
    assert restored.source_evidence == ()
    assert restored.critical is False
    assert restored.confidence is None


def test_contains_sensitive_and_deletes_note(tmp_path: Path) -> None:
    store = MemoryNoteStore(
        tmp_path,
        SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".julycode")),
        secrets=("known-secret",),
    )
    store.write_note(note("delete-me"))

    assert store.contains_sensitive("Bearer abcdefghijklmnop")
    assert store.contains_sensitive("-----BEGIN PRIVATE KEY-----")
    assert store.contains_sensitive("password=hunter22")
    assert store.contains_sensitive("known-secret")
    assert not store.contains_sensitive("普通项目事实")
    assert store.delete_note("project", "delete-me") is True
    assert store.delete_note("project", "delete-me") is False
