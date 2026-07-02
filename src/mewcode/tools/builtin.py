from __future__ import annotations

import asyncio
import queue
import re
import shlex
import shutil
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mewcode.tools.base import ToolContext, ToolExecutionError, ToolSpec


async def _run_blocking(function: Any, *args: Any, **kwargs: Any) -> Any:
    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            result = function(*args, **kwargs)
        except BaseException as exc:
            results.put((False, exc))
            return
        results.put((True, result))

    threading.Thread(target=runner, name="mewcode-tool-io", daemon=True).start()
    while True:
        try:
            ok, value = results.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.001)
            continue
        if ok:
            return value
        raise value


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _resolve_path(cwd: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path


def _display_path(cwd: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except ValueError:
        return str(path)


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _positive_int(value: Any, default: int) -> int:
    if value is None:
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ToolExecutionError("max_results 必须大于 0", error_type="invalid_arguments")
    return parsed


class ReadFileTool:
    spec = ToolSpec(
        name="read_file",
        description="读取已知路径的 UTF-8 文本文件内容；编辑或总结文件前优先用它确认当前内容。",
        parameters_schema=_schema(
            {"path": {"type": "string", "description": "要读取的文件路径"}},
            ["path"],
        ),
        safety="read_only",
    )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        path = _resolve_path(context.cwd, str(arguments["path"]))
        if not path.exists():
            raise ToolExecutionError(f"文件不存在: {path}", error_type="not_found")
        if not path.is_file():
            raise ToolExecutionError(f"路径不是文件: {path}", error_type="not_file")
        content = context.read_cache.get(path) if context.read_cache is not None else None
        if content is None:
            try:
                content = await _run_blocking(path.read_text, encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ToolExecutionError(f"文件不是有效 UTF-8 文本: {path}", error_type="decode_error") from exc
            except OSError as exc:
                raise ToolExecutionError(f"无法读取文件: {exc}", error_type="read_error") from exc
            if context.read_cache is not None:
                context.read_cache.put(path, content)
        content, truncated = _truncate(content, context.max_output_chars)
        return {"path": _display_path(context.cwd, path), "content": content, "truncated": truncated}


class WriteFileTool:
    spec = ToolSpec(
        name="write_file",
        description="按 UTF-8 创建或覆盖写入完整文件内容；会替换目标文件全文，只在需要完整写入时使用。",
        parameters_schema=_schema(
            {
                "path": {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            ["path", "content"],
        ),
        safety="side_effect",
    )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        path = _resolve_path(context.cwd, str(arguments["path"]))
        content = str(arguments["content"])
        created = not path.exists()
        try:
            await _run_blocking(path.parent.mkdir, parents=True, exist_ok=True)
            await _run_blocking(path.write_text, content, encoding="utf-8")
        except OSError as exc:
            raise ToolExecutionError(f"无法写入文件: {exc}", error_type="write_error") from exc
        return {
            "path": _display_path(context.cwd, path),
            "bytes_written": len(content.encode("utf-8")),
            "created": created,
        }


class EditFileTool:
    spec = ToolSpec(
        name="edit_file",
        description="按原文唯一匹配替换文件内容；修改前应先读取或搜索目标文件，匹配不到或匹配多次都不会写入。",
        parameters_schema=_schema(
            {
                "path": {"type": "string", "description": "要修改的文件路径"},
                "old_text": {"type": "string", "description": "要被替换的原文，必须唯一出现"},
                "new_text": {"type": "string", "description": "替换后的新文本"},
            },
            ["path", "old_text", "new_text"],
        ),
        safety="side_effect",
    )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        path = _resolve_path(context.cwd, str(arguments["path"]))
        old_text = str(arguments["old_text"])
        new_text = str(arguments["new_text"])
        if not old_text:
            raise ToolExecutionError("old_text 不能为空", error_type="invalid_arguments")
        if not path.exists():
            raise ToolExecutionError(f"文件不存在: {path}", error_type="not_found")
        if not path.is_file():
            raise ToolExecutionError(f"路径不是文件: {path}", error_type="not_file")
        try:
            content = await _run_blocking(path.read_text, encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolExecutionError(f"文件不是有效 UTF-8 文本: {path}", error_type="decode_error") from exc
        except OSError as exc:
            raise ToolExecutionError(f"无法读取文件: {exc}", error_type="read_error") from exc

        count = content.count(old_text)
        if count == 0:
            raise ToolExecutionError("未找到要替换的原文，文件未修改", error_type="no_match", data={"matches": 0})
        if count > 1:
            raise ToolExecutionError("原文匹配到多处，文件未修改", error_type="multiple_matches", data={"matches": count})
        try:
            await _run_blocking(path.write_text, content.replace(old_text, new_text, 1), encoding="utf-8")
        except OSError as exc:
            raise ToolExecutionError(f"无法写入文件: {exc}", error_type="write_error") from exc
        return {"path": _display_path(context.cwd, path), "replacements": 1}


class RunCommandTool:
    spec = ToolSpec(
        name="run_command",
        description="在当前项目目录执行本地命令，返回退出码、标准输出和标准错误；适合构建、测试、检查或用户明确要求的命令，可能有副作用。",
        parameters_schema=_schema(
            {
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout_seconds": {"type": "number", "description": "可选超时时间，单位秒"},
            },
            ["command"],
        ),
        timeout_seconds=10.0,
        safety="side_effect",
    )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        command = str(arguments["command"])
        timeout = float(arguments.get("timeout_seconds") or self.spec.timeout_seconds)
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ToolExecutionError(f"命令解析失败: {exc}", error_type="invalid_arguments") from exc
        if not argv:
            raise ToolExecutionError("command 不能为空", error_type="invalid_arguments")

        try:
            completed = await _run_blocking(
                subprocess.run,
                argv,
                cwd=str(context.cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolExecutionError(
                f"命令执行超时，超过 {timeout:g} 秒",
                error_type="timeout",
                data={
                    "timed_out": True,
                    "stdout": _decode_timeout_output(exc.stdout),
                    "stderr": _decode_timeout_output(exc.stderr),
                },
            ) from exc

        stdout_text, stdout_truncated = _truncate(completed.stdout, context.max_output_chars)
        stderr_text, stderr_truncated = _truncate(completed.stderr, context.max_output_chars)
        return {
            "exit_code": completed.returncode,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "timed_out": False,
            "truncated": stdout_truncated or stderr_truncated,
        }


class FindFilesTool:
    spec = ToolSpec(
        name="find_files",
        description="按 glob 模式定位当前项目内的文件路径；不知道准确文件名时优先使用它查找候选文件。",
        parameters_schema=_schema(
            {
                "pattern": {"type": "string", "description": "glob 模式"},
                "max_results": {"type": "number", "description": "最多返回条数"},
            },
            ["pattern"],
        ),
        safety="read_only",
    )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        pattern = str(arguments["pattern"])
        max_results = _positive_int(arguments.get("max_results"), 100)
        matches = await _run_blocking(_find_files, context.cwd, pattern, max_results)
        return {"matches": matches, "count": len(matches)}


def _find_files(cwd: Path, pattern: str, max_results: int) -> list[str]:
    return [
        _display_path(cwd, path)
        for path in sorted(cwd.glob(pattern))
        if path.is_file()
    ][:max_results]


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class SearchCodeTool:
    spec = ToolSpec(
        name="search_code",
        description="搜索代码或文本内容，返回匹配文件、行列和文本摘要；需要定位符号、配置或待编辑原文时优先使用。",
        parameters_schema=_schema(
            {
                "pattern": {"type": "string", "description": "要搜索的文本或正则模式"},
                "path": {"type": "string", "description": "可选搜索起点"},
                "glob": {"type": "string", "description": "可选文件 glob 过滤"},
                "max_results": {"type": "number", "description": "最多返回条数"},
            },
            ["pattern"],
        ),
        safety="read_only",
    )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        pattern = str(arguments["pattern"])
        max_results = _positive_int(arguments.get("max_results"), 100)
        search_root = _resolve_path(context.cwd, str(arguments.get("path") or "."))
        glob = arguments.get("glob")
        if shutil.which("rg"):
            return await self._search_with_rg(pattern, search_root, str(glob) if glob else None, max_results, context)
        return await _run_blocking(
            self._search_with_python,
            pattern,
            search_root,
            str(glob) if glob else None,
            max_results,
            context,
        )

    async def _search_with_rg(
        self,
        pattern: str,
        search_root: Path,
        glob: str | None,
        max_results: int,
        context: ToolContext,
    ) -> Mapping[str, Any]:
        command = [
            "rg",
            "--column",
            "--line-number",
            "--no-heading",
            "--color",
            "never",
        ]
        if glob:
            command.extend(["-g", glob])
        command.extend(["-e", pattern, str(search_root)])
        try:
            completed = await _run_blocking(
                subprocess.run,
                command,
                cwd=str(context.cwd),
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return await _run_blocking(self._search_with_python, pattern, search_root, glob, max_results, context)
        if completed.returncode not in {0, 1}:
            detail = completed.stderr.strip()
            raise ToolExecutionError(f"搜索失败: {detail}", error_type="search_error")
        matches = []
        for line in completed.stdout.splitlines():
            parts = line.split(":", 3)
            if len(parts) != 4:
                continue
            path, line_number, column, text = parts
            matches.append(
                {
                    "path": _display_path(context.cwd, Path(path)),
                    "line": int(line_number),
                    "column": int(column),
                    "text": text,
                }
            )
            if len(matches) >= max_results:
                break
        return {"matches": matches, "count": len(matches)}

    def _search_with_python(
        self,
        pattern: str,
        search_root: Path,
        glob: str | None,
        max_results: int,
        context: ToolContext,
    ) -> Mapping[str, Any]:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolExecutionError(f"搜索模式不是合法正则: {exc}", error_type="invalid_arguments") from exc
        files = [search_root] if search_root.is_file() else sorted(search_root.rglob(glob or "*"))
        matches = []
        for path in files:
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, text in enumerate(lines, start=1):
                match = regex.search(text)
                if not match:
                    continue
                matches.append(
                    {
                        "path": _display_path(context.cwd, path),
                        "line": line_number,
                        "column": match.start() + 1,
                        "text": text,
                    }
                )
                if len(matches) >= max_results:
                    return {"matches": matches, "count": len(matches)}
        return {"matches": matches, "count": len(matches)}
