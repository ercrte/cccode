from __future__ import annotations

from pathlib import Path

from mewcode.memory.index import MemoryIndexBuilder
from mewcode.memory.models import SessionMemoryConfig
from mewcode.memory.notes import MemoryNoteStore
from tests.test_memory_notes import note


def test_builds_memory_index_by_category(tmp_path: Path) -> None:
    store = MemoryNoteStore(tmp_path, SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".mewcode")))
    store.write_note(note("pref", scope="user", category="preference", body="偏好"))
    store.write_note(note("corr", scope="user", category="correction", body="纠正"))
    builder = MemoryIndexBuilder(store)

    index = builder.build("user")

    assert "## 用户偏好" in index.content
    assert "## 纠正反馈" in index.content
    assert index.content.index("## 用户偏好") < index.content.index("## 纠正反馈")
    assert index.path.exists()


def test_read_index_returns_existing_index(tmp_path: Path) -> None:
    store = MemoryNoteStore(tmp_path, SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".mewcode")))
    builder = MemoryIndexBuilder(store)
    store.write_note(note("knowledge"))
    built = builder.build("project")

    read = builder.read_index("project")

    assert read is not None
    assert read.content == built.content


def test_memory_index_is_limited_by_lines_and_bytes(tmp_path: Path) -> None:
    config = SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".mewcode"), index_max_lines=10, index_max_bytes=300)
    store = MemoryNoteStore(tmp_path, config)
    for index in range(50):
        store.write_note(note(f"note-{index}", body="很长的内容" * 20))
    builder = MemoryIndexBuilder(store, config)

    built = builder.build("project")

    assert built.line_count <= 10
    assert built.byte_count <= 300
    assert built.warnings


def test_critical_memory_is_indexed_before_regular_memory(tmp_path: Path) -> None:
    store = MemoryNoteStore(tmp_path, SessionMemoryConfig(user_dir=str(tmp_path / "home" / ".mewcode")))
    store.write_note(note("regular", scope="user", category="preference", body="普通偏好"))
    store.write_note(note("critical", scope="user", category="preference", body="关键偏好", critical=True))

    index = MemoryIndexBuilder(store).build("user")

    assert "**[关键]**" in index.content
    assert "`tags:" in index.content
    assert index.content.index("关键偏好") < index.content.index("普通偏好")
