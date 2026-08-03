from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from julycode.tools import ToolCall, ToolContext, ToolExecutionError, ToolResult, ToolSpec
from julycode.tools.builtin import (
    EditFileTool,
    FindFilesTool,
    ReadFileTool,
    RunCommandTool,
    SearchCodeTool,
    WriteFileTool,
)
from julycode.subagents.cache import FileReadCache
from julycode.tools.registry import ToolRegistry, create_default_registry
from julycode.tools.validation import validate_arguments


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
async def test_read_file_partial_preserves_newline_and_metadata(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("one\n二\nthree\nfour", encoding="utf-8")

    data = await ReadFileTool().execute(
        {"path": "demo.txt", "offset": 2, "limit": 2},
        context(tmp_path),
    )

    assert data == {
        "path": "demo.txt",
        "content": "二\nthree\n",
        "truncated": False,
        "start_line": 2,
        "end_line": 3,
        "total_lines": 4,
        "has_more": True,
    }


@pytest.mark.asyncio
async def test_read_file_partial_supports_offset_or_limit_independently(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    tool = ReadFileTool()

    from_offset = await tool.execute({"path": "demo.txt", "offset": 3}, context(tmp_path))
    with_limit = await tool.execute({"path": "demo.txt", "limit": 2}, context(tmp_path))

    assert from_offset["content"] == "three\nfour\n"
    assert from_offset["start_line"] == 3
    assert from_offset["end_line"] == 4
    assert from_offset["has_more"] is False
    assert with_limit["content"] == "one\ntwo\n"
    assert with_limit["start_line"] == 1
    assert with_limit["end_line"] == 2
    assert with_limit["has_more"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"offset": 0},
        {"offset": -1},
        {"offset": 4},
        {"offset": 1.5},
        {"limit": 0},
        {"limit": -1},
        {"limit": True},
    ],
)
async def test_read_file_rejects_invalid_partial_ranges(
    tmp_path: Path,
    arguments: dict[str, object],
) -> None:
    (tmp_path / "demo.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")

    with pytest.raises(ToolExecutionError) as exc_info:
        await ReadFileTool().execute({"path": "demo.txt", **arguments}, context(tmp_path))

    assert exc_info.value.error_type == "invalid_arguments"


@pytest.mark.asyncio
async def test_read_file_partial_empty_file_has_stable_metadata(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")

    data = await ReadFileTool().execute(
        {"path": "empty.txt", "offset": 20, "limit": 5},
        context(tmp_path),
    )

    assert data == {
        "path": "empty.txt",
        "content": "",
        "truncated": False,
        "start_line": 1,
        "end_line": 0,
        "total_lines": 0,
        "has_more": False,
    }


@pytest.mark.asyncio
async def test_read_file_partial_character_truncated_updates_metadata(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    tool_context = ToolContext(cwd=tmp_path, max_output_chars=8)

    data = await ReadFileTool().execute(
        {"path": "demo.txt", "offset": 1, "limit": 3},
        tool_context,
    )

    assert data["content"] == "alpha\nbe"
    assert data["truncated"] is True
    assert data["start_line"] == 1
    assert data["end_line"] == 2
    assert data["total_lines"] == 3
    assert data["has_more"] is True


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
async def test_find_files_uses_git_ignore_and_includes_untracked_files(tmp_path: Path) -> None:
    subprocess.run(("git", "-C", str(tmp_path), "init"), check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (tmp_path / "tracked.py").write_text("", encoding="utf-8")
    (tmp_path / "untracked.py").write_text("", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("", encoding="utf-8")
    subprocess.run(
        ("git", "-C", str(tmp_path), "add", ".gitignore", "tracked.py"),
        check=True,
        capture_output=True,
    )

    data = await FindFilesTool().execute({"pattern": "**/*.py"}, context(tmp_path))

    assert data["matches"] == ["tracked.py", "untracked.py"]


@pytest.mark.asyncio
async def test_find_files_excludes_runtime_and_build_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("", encoding="utf-8")
    for name in (".julycode", "node_modules", "build", "__pycache__"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "hidden.py").write_text("", encoding="utf-8")

    data = await FindFilesTool().execute({"pattern": "**/*.py"}, context(tmp_path))

    assert data["matches"] == ["src/app.py"]


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
async def test_search_code_no_candidates_returns_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".julycode").mkdir()
    (tmp_path / ".julycode/hidden.py").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr("julycode.tools.builtin.shutil.which", lambda name: None)

    data = await SearchCodeTool().execute({"pattern": "needle"}, context(tmp_path))

    assert data == {"matches": [], "count": 0}


@pytest.mark.asyncio
async def test_search_code_directory_scope_respects_path_and_max_results(tmp_path: Path) -> None:
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
async def test_search_code_single_file_returns_path_line_column_and_colon_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "single.py"
    target.write_text("prefix needle:value\n", encoding="utf-8")
    observed_commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        _ = kwargs
        observed_commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{target}:1:8:prefix needle:value\n",
            stderr="",
        )

    monkeypatch.setattr("julycode.tools.builtin.shutil.which", lambda name: "/fake/rg")
    monkeypatch.setattr("julycode.tools.builtin.subprocess.run", fake_run)

    data = await SearchCodeTool().execute(
        {"pattern": "needle", "path": "single.py"},
        context(tmp_path),
    )

    assert "--with-filename" in observed_commands[0]
    assert data["matches"] == [
        {
            "path": "single.py",
            "line": 1,
            "column": 8,
            "text": "prefix needle:value",
        }
    ]


@pytest.mark.asyncio
async def test_search_code_single_file_without_match_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "single.py").write_text("hello\n", encoding="utf-8")

    data = await SearchCodeTool().execute(
        {"pattern": "missing", "path": "single.py"},
        context(tmp_path),
    )

    assert data == {"matches": [], "count": 0}


@pytest.mark.asyncio
async def test_search_code_filters_explicit_scope_with_glob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "src/app.txt").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr("julycode.tools.builtin.shutil.which", lambda name: None)

    data = await SearchCodeTool().execute(
        {"pattern": "needle", "path": "src", "glob": "*.py"},
        context(tmp_path),
    )

    assert [match["path"] for match in data["matches"]] == ["src/app.py"]


@pytest.mark.asyncio
async def test_search_code_large_excluded_runtime_without_rg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / ".julycode").mkdir()
    runtime_file = tmp_path / ".julycode/huge-session.txt"
    runtime_file.write_text("needle\n" * 1000, encoding="utf-8")
    original_read_text = Path.read_text
    reads: list[Path] = []

    def guarded_read_text(self: Path, *args, **kwargs):
        reads.append(self.resolve())
        if self.resolve() == runtime_file.resolve():
            raise AssertionError("默认代码搜索不应读取 .julycode 运行数据")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr("julycode.tools.builtin.shutil.which", lambda name: None)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    data = await SearchCodeTool().execute({"pattern": "needle"}, context(tmp_path))

    assert [match["path"] for match in data["matches"]] == ["src/app.py"]
    assert runtime_file.resolve() not in reads


@pytest.mark.asyncio
async def test_search_code_ignored_scope_explicit_target_is_searchable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(("git", "-C", str(tmp_path), "init"), check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored/secret.py").write_text("needle\n", encoding="utf-8")
    subprocess.run(
        ("git", "-C", str(tmp_path), "add", ".gitignore"),
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr("julycode.tools.builtin.shutil.which", lambda name: None)

    default_data = await SearchCodeTool().execute({"pattern": "needle"}, context(tmp_path))
    explicit_data = await SearchCodeTool().execute(
        {"pattern": "needle", "path": "ignored"},
        context(tmp_path),
    )

    assert default_data["matches"] == []
    assert [match["path"] for match in explicit_data["matches"]] == ["ignored/secret.py"]


@pytest.mark.asyncio
async def test_search_code_backend_parity_uses_same_ignored_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / ".julycode").mkdir()
    (tmp_path / ".julycode/hidden.py").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr("julycode.tools.builtin.shutil.which", lambda name: None)
    python_data = await SearchCodeTool().execute({"pattern": "needle"}, context(tmp_path))

    def fake_run(command, **kwargs):
        _ = kwargs
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"")
        separator = command.index("--")
        paths = command[separator + 1 :]
        stdout = "".join(f"{path}:1:1:needle\n" for path in paths)
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("julycode.tools.builtin.shutil.which", lambda name: "/fake/rg")
    monkeypatch.setattr("julycode.tools.builtin.subprocess.run", fake_run)
    rg_data = await SearchCodeTool().execute({"pattern": "needle"}, context(tmp_path))

    assert rg_data == python_data
    assert [match["path"] for match in rg_data["matches"]] == ["src/app.py"]


@pytest.mark.asyncio
async def test_search_code_rejects_invalid_regex_before_search(tmp_path: Path) -> None:
    with pytest.raises(ToolExecutionError) as exc_info:
        await SearchCodeTool().execute({"pattern": "["}, context(tmp_path))

    assert exc_info.value.error_type == "invalid_arguments"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        OSError("rg 启动失败"),
        subprocess.TimeoutExpired(["rg"], 5),
    ],
)
async def test_search_code_rg_failures_fall_back_to_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    target = tmp_path / "single.py"
    target.write_text("needle\n", encoding="utf-8")

    def failing_run(command, **kwargs):
        _ = command, kwargs
        raise failure

    monkeypatch.setattr("julycode.tools.builtin.shutil.which", lambda name: "/fake/rg")
    monkeypatch.setattr("julycode.tools.builtin.subprocess.run", failing_run)

    data = await SearchCodeTool().execute(
        {"pattern": "needle", "path": "single.py"},
        context(tmp_path),
    )

    assert [match["path"] for match in data["matches"]] == ["single.py"]


@pytest.mark.asyncio
async def test_search_code_rg_error_or_malformed_output_falls_back_to_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "single.py"
    target.write_text("needle\n", encoding="utf-8")
    results = iter(
        [
            subprocess.CompletedProcess(["rg"], 2, stdout="", stderr="unsupported"),
            subprocess.CompletedProcess(["rg"], 0, stdout="malformed\n", stderr=""),
        ]
    )

    def fake_run(command, **kwargs):
        _ = command, kwargs
        return next(results)

    monkeypatch.setattr("julycode.tools.builtin.shutil.which", lambda name: "/fake/rg")
    monkeypatch.setattr("julycode.tools.builtin.subprocess.run", fake_run)
    tool = SearchCodeTool()

    from_error = await tool.execute(
        {"pattern": "needle", "path": "single.py"},
        context(tmp_path),
    )
    from_malformed = await tool.execute(
        {"pattern": "needle", "path": "single.py"},
        context(tmp_path),
    )

    assert from_error["matches"] == from_malformed["matches"]
    assert from_error["matches"][0]["path"] == "single.py"


@pytest.mark.asyncio
async def test_search_code_rg_batches_candidates_and_stops_after_max_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "src").mkdir()
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / "src" / name).write_text("needle\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        _ = kwargs
        commands.append(command)
        separator = command.index("--")
        paths = command[separator + 1 :]
        stdout = "".join(f"{path}:1:1:needle\n" for path in paths)
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("julycode.tools.builtin.shutil.which", lambda name: "/fake/rg")
    monkeypatch.setattr("julycode.tools.builtin.subprocess.run", fake_run)
    monkeypatch.setattr(SearchCodeTool, "_rg_batch_size", 2)

    data = await SearchCodeTool().execute(
        {"pattern": "needle", "path": "src", "max_results": 3},
        context(tmp_path),
    )

    assert len(commands) == 2
    assert [match["path"] for match in data["matches"]] == [
        "src/a.py",
        "src/b.py",
        "src/c.py",
    ]


@pytest.mark.asyncio
async def test_search_code_python_fallback_does_not_block_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_python_search(self, regex, candidates, max_results, tool_context):
        _ = self, regex, candidates, max_results, tool_context
        time.sleep(0.2)
        return {"matches": [], "count": 0}

    monkeypatch.setattr("julycode.tools.builtin.shutil.which", lambda name: None)
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
    read_properties = specs["read_file"].parameters_schema["properties"]

    assert "编辑或总结文件前" in specs["read_file"].description
    assert "offset" in specs["read_file"].description
    assert read_properties["offset"]["type"] == "integer"
    assert read_properties["limit"]["type"] == "integer"
    assert "覆盖写入完整文件内容" in specs["write_file"].description
    assert "修改前应先读取或搜索目标文件" in specs["edit_file"].description
    assert "可能有副作用" in specs["run_command"].description
    assert "不要用它替代" in specs["run_command"].description
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
