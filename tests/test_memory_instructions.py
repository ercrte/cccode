from __future__ import annotations

from pathlib import Path

from mewcode.memory.instructions import InstructionLoader
from mewcode.memory.manager import SessionMemoryManager
from mewcode.memory.models import SessionMemoryConfig


def test_loads_three_instruction_layers_in_priority_order(tmp_path: Path) -> None:
    user_dir = tmp_path / "home" / ".mewcode"
    (tmp_path / ".mewcode").mkdir()
    user_dir.mkdir(parents=True)
    (tmp_path / ".mewcode" / "AGENTS.md").write_text("project private", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("project root", encoding="utf-8")
    (user_dir / "AGENTS.md").write_text("user", encoding="utf-8")

    bundle = InstructionLoader(tmp_path, SessionMemoryConfig(user_dir=str(user_dir))).load()

    assert [block.scope for block in bundle.blocks] == ["project_private", "project_root", "user"]
    assert [block.content for block in bundle.blocks] == ["project private", "project root", "user"]


def test_missing_instruction_files_are_silent(tmp_path: Path) -> None:
    bundle = InstructionLoader(tmp_path, SessionMemoryConfig(user_dir=str(tmp_path / "home"))).load()

    assert bundle.blocks == ()
    assert bundle.warnings == ()


def test_include_expands_relative_file(tmp_path: Path) -> None:
    (tmp_path / ".mewcode" / "rules").mkdir(parents=True)
    (tmp_path / ".mewcode" / "rules" / "python.md").write_text("使用中文注释", encoding="utf-8")
    (tmp_path / ".mewcode" / "AGENTS.md").write_text(
        "项目规则\n@include <rules/python.md>\n结束",
        encoding="utf-8",
    )

    bundle = InstructionLoader(tmp_path, SessionMemoryConfig(user_dir=str(tmp_path / "home"))).load()

    assert "项目规则\n使用中文注释\n结束" == bundle.blocks[0].content


def test_include_blocks_cycle_depth_and_path_escape(tmp_path: Path) -> None:
    user_dir = tmp_path / "home" / ".mewcode"
    (tmp_path / ".mewcode").mkdir()
    user_dir.mkdir(parents=True)
    (tmp_path / ".mewcode" / "AGENTS.md").write_text(
        "@include <../../outside.md>\n@include <deep1.md>",
        encoding="utf-8",
    )
    (tmp_path / ".mewcode" / "deep1.md").write_text("@include <deep2.md>", encoding="utf-8")
    (tmp_path / ".mewcode" / "deep2.md").write_text("too deep", encoding="utf-8")

    bundle = InstructionLoader(
        tmp_path,
        SessionMemoryConfig(user_dir=str(user_dir), include_max_depth=1),
    ).load()

    warning_text = "\n".join(bundle.warnings)
    assert "越界" in warning_text
    assert "嵌套过深" in warning_text


def test_include_blocks_cycle(tmp_path: Path) -> None:
    user_dir = tmp_path / "home" / ".mewcode"
    (tmp_path / ".mewcode").mkdir()
    user_dir.mkdir(parents=True)
    (tmp_path / ".mewcode" / "AGENTS.md").write_text("@include <cycle.md>", encoding="utf-8")
    (tmp_path / ".mewcode" / "cycle.md").write_text("@include <AGENTS.md>", encoding="utf-8")

    bundle = InstructionLoader(
        tmp_path,
        SessionMemoryConfig(user_dir=str(user_dir), include_max_depth=5),
    ).load()

    assert "循环引用" in "\n".join(bundle.warnings)


def test_worktree_knowledge_isolated_by_absolute_directory(tmp_path: Path) -> None:
    user_dir = tmp_path / "home/.mewcode"
    user_dir.mkdir(parents=True)
    first = tmp_path / "worktree-one"
    second = tmp_path / "worktree-two"
    first.mkdir()
    second.mkdir()
    (first / "AGENTS.md").write_text("仅适用于 one", encoding="utf-8")
    (second / "AGENTS.md").write_text("仅适用于 two", encoding="utf-8")
    config = SessionMemoryConfig(user_dir=str(user_dir), auto_notes_enabled=False)

    first_manager = SessionMemoryManager(first, config)
    second_manager = SessionMemoryManager(second, config)
    first_context = first_manager.load_runtime_context()
    second_context = second_manager.load_runtime_context()

    assert [block.content for block in first_context.instructions.blocks] == ["仅适用于 one"]
    assert [block.content for block in second_context.instructions.blocks] == ["仅适用于 two"]
    assert first_context.project_memory_index is not None
    assert second_context.project_memory_index is not None
    assert first_context.project_memory_index.path != second_context.project_memory_index.path
    assert first_context.project_memory_index.path.is_absolute()
    assert second_context.project_memory_index.path.is_absolute()
    assert not (first / ".mewcode/sessions").exists()
    assert not (second / ".mewcode/sessions").exists()
