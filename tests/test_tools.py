from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from mewcode.tools import ToolCall, ToolContext, ToolExecutionError, ToolResult, ToolSpec
from mewcode.tools.builtin import (
    EditFileTool,
    FindFilesTool,
    ReadFileTool,
    RunCommandTool,
    SearchCodeTool,
    WriteFileTool,
)
from mewcode.subagents.cache import FileReadCache
from mewcode.tools.registry import ToolRegistry, create_default_registry
from mewcode.tools.validation import validate_arguments


def context(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, max_output_chars=20000)


async def _collect_ticks(duration: float = 0.18, interval: float = 0.02) -> list[float]:
    started = time.monotonic()
    ticks: list[float] = []
    while time.monotonic() - started < duration:
        ticks.append(time.monotonic() - started)
        await asyncio.sleep(interval)
    return ticks


def test_tool_context_normalizes_absolute_cwd_without_chdir(tmp_path: Path) -> None:
    before = Path.cwd()
    relative = Path(os.path.relpath(tmp_path, before))

    tool_context = ToolContext(cwd=relative)

    assert tool_context.cwd == tmp_path.resolve()
    assert tool_context.cwd.is_absolute()
    assert Path.cwd() == before


def test_tool_result_serializes_success() -> None:
    result = ToolResult("call-1", "read_file", True, {"content": "你好"}, elapsed_ms=12)

    payload = json.loads(result.to_model_content())

    assert payload["success"] is True
    assert payload["tool_name"] == "read_file"
    assert payload["data"] == {"content": "你好"}
    assert payload["elapsed_ms"] == 12


def test_tool_result_serializes_error() -> None:
    result = ToolResult("call-1", "read_file", False, {}, error_type="not_found", error="文件不存在")

    payload = json.loads(result.to_model_content())

    assert payload["success"] is False
    assert payload["error_type"] == "not_found"
    assert payload["error"] == "文件不存在"


def test_validate_arguments_accepts_valid_object() -> None:
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "limit": {"type": "integer"},
            "mode": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    assert validate_arguments(schema, {"path": "x", "limit": 2, "mode": "a"}) == []


def test_validate_arguments_reports_errors() -> None:
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "limit": {"type": "integer"},
            "mode": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    errors = validate_arguments(schema, {"limit": "2", "mode": "c", "extra": True})

    assert any("path" in error and "必填" in error for error in errors)
    assert any("limit" in error and "整数" in error for error in errors)
    assert any("mode" in error and "之一" in error for error in errors)
    assert any("extra" in error and "允许" in error for error in errors)


@pytest.mark.asyncio
async def test_read_file_returns_content(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("hello", encoding="utf-8")

    data = await ReadFileTool().execute({"path": "demo.txt"}, context(tmp_path))

    assert data["path"] == "demo.txt"
    assert data["content"] == "hello"
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_read_file_uses_independent_cache_instances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "demo.txt"
    target.write_text("one", encoding="utf-8")
    original_read_text = Path.read_text
    reads: list[Path] = []

    def counting_read_text(self: Path, *args, **kwargs):
        if self.resolve() == target.resolve():
            reads.append(self)
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    tool = ReadFileTool()
    cache = FileReadCache()

    first = await tool.execute({"path": "demo.txt"}, ToolContext(cwd=tmp_path, read_cache=cache))
    second = await tool.execute({"path": "demo.txt"}, ToolContext(cwd=tmp_path, read_cache=cache))
    target.write_text("second", encoding="utf-8")
    third = await tool.execute({"path": "demo.txt"}, ToolContext(cwd=tmp_path, read_cache=cache))
    fourth = await tool.execute({"path": "demo.txt"}, ToolContext(cwd=tmp_path, read_cache=FileReadCache()))

    assert [first["content"], second["content"], third["content"], fourth["content"]] == [
        "one",
        "one",
        "second",
        "second",
    ]
    assert len(reads) == 3


@pytest.mark.asyncio
async def test_read_file_reports_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ToolExecutionError) as exc_info:
        await ReadFileTool().execute({"path": "missing.txt"}, context(tmp_path))

    assert exc_info.value.error_type == "not_found"


@pytest.mark.asyncio
async def test_read_file_rejects_directory(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()

    with pytest.raises(ToolExecutionError) as exc_info:
        await ReadFileTool().execute({"path": "folder"}, context(tmp_path))

    assert exc_info.value.error_type == "not_file"


@pytest.mark.asyncio
async def test_write_file_creates_file(tmp_path: Path) -> None:
    data = await WriteFileTool().execute({"path": "demo.txt", "content": "hello"}, context(tmp_path))

    assert (tmp_path / "demo.txt").read_text(encoding="utf-8") == "hello"
    assert data["created"] is True
    assert data["bytes_written"] == 5


@pytest.mark.asyncio
async def test_write_file_overwrites_file(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("old", encoding="utf-8")

    data = await WriteFileTool().execute({"path": "demo.txt", "content": "new"}, context(tmp_path))

    assert (tmp_path / "demo.txt").read_text(encoding="utf-8") == "new"
    assert data["created"] is False


@pytest.mark.asyncio
async def test_write_file_creates_parent_directories(tmp_path: Path) -> None:
    await WriteFileTool().execute({"path": "a/b/demo.txt", "content": "ok"}, context(tmp_path))

    assert (tmp_path / "a/b/demo.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.asyncio
async def test_edit_file_replaces_unique_text(tmp_path: Path) -> None:
    path = tmp_path / "demo.txt"
    path.write_text("one OLD two", encoding="utf-8")

    data = await EditFileTool().execute(
        {"path": "demo.txt", "old_text": "OLD", "new_text": "NEW"},
        context(tmp_path),
    )

    assert path.read_text(encoding="utf-8") == "one NEW two"
    assert data["replacements"] == 1


@pytest.mark.asyncio
async def test_edit_file_rejects_missing_text_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "demo.txt"
    path.write_text("one two", encoding="utf-8")

    with pytest.raises(ToolExecutionError) as exc_info:
        await EditFileTool().execute(
            {"path": "demo.txt", "old_text": "OLD", "new_text": "NEW"},
            context(tmp_path),
        )

    assert exc_info.value.error_type == "no_match"
    assert path.read_text(encoding="utf-8") == "one two"


@pytest.mark.asyncio
async def test_edit_file_rejects_multiple_matches_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "demo.txt"
    path.write_text("OLD one OLD", encoding="utf-8")

    with pytest.raises(ToolExecutionError) as exc_info:
        await EditFileTool().execute(
            {"path": "demo.txt", "old_text": "OLD", "new_text": "NEW"},
            context(tmp_path),
        )

    assert exc_info.value.error_type == "multiple_matches"
    assert path.read_text(encoding="utf-8") == "OLD one OLD"


@pytest.mark.asyncio
async def test_run_command_returns_exit_code_and_output(tmp_path: Path) -> None:
    data = await RunCommandTool().execute(
        {"command": "python -c \"import sys; print('out'); print('err', file=sys.stderr)\""},
        context(tmp_path),
    )

    assert data["exit_code"] == 0
    assert data["stdout"].strip() == "out"
    assert data["stderr"].strip() == "err"


@pytest.mark.asyncio
async def test_run_command_uses_context_cwd(tmp_path: Path) -> None:
    data = await RunCommandTool().execute(
        {"command": "python -c \"from pathlib import Path; print(Path.cwd().name)\""},
        context(tmp_path),
    )

    assert data["stdout"].strip() == tmp_path.name


@pytest.mark.asyncio
async def test_run_command_times_out(tmp_path: Path) -> None:
    with pytest.raises(ToolExecutionError) as exc_info:
        await RunCommandTool().execute(
            {"command": "python -c \"import time; time.sleep(2)\"", "timeout_seconds": 0.05},
            context(tmp_path),
        )

    assert exc_info.value.error_type == "timeout"


@pytest.mark.asyncio
async def test_run_command_does_not_block_event_loop(tmp_path: Path) -> None:
    ticker = asyncio.create_task(_collect_ticks())
    await asyncio.sleep(0)

    await RunCommandTool().execute(
        {"command": f"{sys.executable} -c \"import time; time.sleep(0.2)\"", "timeout_seconds": 1},
        context(tmp_path),
    )

    ticks = await ticker
    assert len(ticks) >= 4


@pytest.mark.asyncio
async def test_find_files_returns_matching_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.txt").write_text("", encoding="utf-8")
    (tmp_path / "folder.py").mkdir()

    data = await FindFilesTool().execute({"pattern": "*.py"}, context(tmp_path))

    assert data["matches"] == ["a.py"]


@pytest.mark.asyncio
async def test_find_files_returns_empty_matches(tmp_path: Path) -> None:
    data = await FindFilesTool().execute({"pattern": "*.missing"}, context(tmp_path))

    assert data["matches"] == []
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_find_files_respects_max_results(tmp_path: Path) -> None:
    for name in ["a.py", "b.py", "c.py"]:
        (tmp_path / name).write_text("", encoding="utf-8")

    data = await FindFilesTool().execute({"pattern": "*.py", "max_results": 2}, context(tmp_path))

    assert data["matches"] == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_search_code_returns_matches(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("class ChatSession:\n    pass\n", encoding="utf-8")

    data = await SearchCodeTool().execute({"pattern": "ChatSession"}, context(tmp_path))

    assert data["matches"][0]["path"] == "a.py"
    assert data["matches"][0]["line"] == 1
    assert "ChatSession" in data["matches"][0]["text"]


@pytest.mark.asyncio
async def test_search_code_returns_empty_matches(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("hello\n", encoding="utf-8")

    data = await SearchCodeTool().execute({"pattern": "MissingThing"}, context(tmp_path))

    assert data["matches"] == []


@pytest.mark.asyncio
async def test_search_code_respects_path_and_max_results(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a/one.py").write_text("needle\nneedle\n", encoding="utf-8")
    (tmp_path / "b/two.py").write_text("needle\n", encoding="utf-8")

    data = await SearchCodeTool().execute(
        {"pattern": "needle", "path": "a", "max_results": 1},
        context(tmp_path),
    )

    assert len(data["matches"]) == 1
    assert data["matches"][0]["path"] == "a/one.py"


@pytest.mark.asyncio
async def test_search_code_python_fallback_does_not_block_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_python_search(self, pattern, search_root, glob, max_results, tool_context):
        _ = self, pattern, search_root, glob, max_results, tool_context
        time.sleep(0.2)
        return {"matches": [], "count": 0}

    monkeypatch.setattr("mewcode.tools.builtin.shutil.which", lambda name: None)
    monkeypatch.setattr(SearchCodeTool, "_search_with_python", slow_python_search)
    ticker = asyncio.create_task(_collect_ticks())
    await asyncio.sleep(0)

    data = await SearchCodeTool().execute({"pattern": "needle"}, context(tmp_path))

    ticks = await ticker
    assert data == {"matches": [], "count": 0}
    assert len(ticks) >= 4


def test_registry_returns_registered_tool() -> None:
    registry = ToolRegistry()
    tool = ReadFileTool()
    registry.register(tool)

    assert registry.get("read_file") is tool
    assert registry.list() == (tool,)


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry()
    registry.register(ReadFileTool())

    with pytest.raises(ValueError, match="read_file"):
        registry.register(ReadFileTool())


def test_default_registry_contains_six_core_tools() -> None:
    names = {spec.name for spec in create_default_registry().specs()}

    assert names == {
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
        "find_files",
        "search_code",
    }


def test_default_registry_marks_tool_safety() -> None:
    specs = {spec.name: spec for spec in create_default_registry().specs()}

    assert specs["read_file"].safety == "read_only"
    assert specs["find_files"].safety == "read_only"
    assert specs["search_code"].safety == "read_only"
    assert specs["write_file"].safety == "side_effect"
    assert specs["edit_file"].safety == "side_effect"
    assert specs["run_command"].safety == "side_effect"


def test_builtin_tool_descriptions_include_operational_rules() -> None:
    specs = {spec.name: spec for spec in create_default_registry().specs()}

    assert "编辑或总结文件前" in specs["read_file"].description
    assert "覆盖写入完整文件内容" in specs["write_file"].description
    assert "修改前应先读取或搜索目标文件" in specs["edit_file"].description
    assert "可能有副作用" in specs["run_command"].description
    assert "不知道准确文件名时优先使用" in specs["find_files"].description
    assert "待编辑原文时优先使用" in specs["search_code"].description


def test_registry_filters_specs_by_safety() -> None:
    registry = create_default_registry()

    read_only_names = {spec.name for spec in registry.specs_by_safety("read_only")}
    side_effect_names = {spec.name for spec in registry.specs_by_safety("side_effect")}

    assert read_only_names == {"read_file", "find_files", "search_code"}
    assert side_effect_names == {"write_file", "edit_file", "run_command"}


def test_tools_package_exports_base_types() -> None:
    assert ToolCall(id="1", name="read_file").name == "read_file"
    assert ToolSpec("x", "desc", {"type": "object"}).name == "x"
    assert ToolSpec("x", "desc", {"type": "object"}).safety == "side_effect"
