from __future__ import annotations

import os
from pathlib import Path

from julycode.permissions.models import PermissionDecision, PermissionSubject
from julycode.tools.base import ToolCall
from julycode.tools.file_catalog import DEFAULT_SEARCH_EXCLUDED_DIRS, matches_glob


_PATH_TOOLS = {"read_file", "write_file", "edit_file"}


class ProjectSandbox:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve(strict=False)

    @property
    def resolved_root(self) -> Path:
        return self._root

    def resolve_inside(self, raw_path: str) -> Path:
        path = Path(str(raw_path)).expanduser()
        if not path.is_absolute():
            path = self._root / path
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self._root):
            raise PermissionError(f"路径超出项目目录: {raw_path}")
        return resolved

    def relative_display(self, path: Path) -> str:
        return str(path.resolve(strict=False).relative_to(self._root)) or "."

    def check_tool_call(self, call: ToolCall) -> PermissionDecision | None:
        try:
            if call.name in _PATH_TOOLS:
                self.resolve_inside(str(call.arguments.get("path", "")))
            elif call.name == "search_code":
                self.resolve_inside(str(call.arguments.get("path") or "."))
            elif call.name == "find_files":
                self._check_find_pattern(str(call.arguments.get("pattern", "")))
        except PermissionError as exc:
            return PermissionDecision(
                kind="deny",
                reason=str(exc),
                error_type="permission_sandbox_violation",
            )
        return None

    def subject_for(self, call: ToolCall) -> PermissionSubject:
        if call.name == "run_command":
            command = _normalize_command(str(call.arguments.get("command", "")))
            return PermissionSubject(tool_name=call.name, targets=(command,), summary=command)
        if call.name in _PATH_TOOLS:
            path = self.resolve_inside(str(call.arguments.get("path", "")))
            display = self.relative_display(path)
            return PermissionSubject(tool_name=call.name, targets=(display,), summary=display)
        if call.name == "find_files":
            pattern = str(call.arguments.get("pattern", ""))
            return PermissionSubject(tool_name=call.name, targets=(pattern,), summary=pattern)
        if call.name == "search_code":
            path = self.relative_display(self.resolve_inside(str(call.arguments.get("path") or ".")))
            glob = call.arguments.get("glob")
            targets = (path,) if glob is None else (path, f"{path} {glob}", str(glob))
            summary = path if glob is None else f"{path} {glob}"
            return PermissionSubject(tool_name=call.name, targets=targets, summary=summary)
        return PermissionSubject(tool_name=call.name, targets=("*",), summary=call.name)

    def _check_find_pattern(self, pattern: str) -> None:
        path = Path(pattern).expanduser()
        if path.is_absolute():
            raise PermissionError(f"glob 模式不能是绝对路径: {pattern}")
        if ".." in Path(pattern).parts:
            raise PermissionError(f"glob 模式不能包含上级目录: {pattern}")
        pending = [self._root]
        while pending:
            current = pending.pop()
            try:
                entries = os.scandir(current)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    if entry.is_symlink():
                        relative = entry_path.relative_to(self._root).as_posix()
                        if matches_glob(relative, pattern):
                            resolved = entry_path.resolve(strict=False)
                            if not resolved.is_relative_to(self._root):
                                raise PermissionError(f"glob 匹配到项目目录外文件: {pattern}")
                        continue
                    if (
                        entry.is_dir(follow_symlinks=False)
                        and entry.name not in DEFAULT_SEARCH_EXCLUDED_DIRS
                    ):
                        pending.append(entry_path)


def _normalize_command(command: str) -> str:
    return " ".join(str(command).strip().split())
