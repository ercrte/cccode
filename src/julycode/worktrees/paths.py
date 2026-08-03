from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath

from julycode.worktrees.models import RepositoryLayout, WorktreeError


WORKTREE_STORAGE_RELATIVE = Path(".julycode/worktrees")
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", re.ASCII)


def validate_relative_name(value: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise WorktreeError("path_validation", "名称必须是字符串")
    if not value or len(value) > 200:
        raise WorktreeError("path_validation", "名称总长度必须为 1–200 个字符")
    if "\\" in value:
        raise WorktreeError("path_validation", "名称不能包含反斜杠")
    pure = PurePosixPath(value)
    if pure.is_absolute() or value.startswith("/"):
        raise WorktreeError("path_validation", "名称必须是相对路径")
    raw_segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in raw_segments):
        raise WorktreeError("path_validation", "名称不能包含空段、. 或 ..")
    if any(_SAFE_SEGMENT_RE.fullmatch(segment) is None for segment in raw_segments):
        raise WorktreeError(
            "path_validation",
            "每段必须以 ASCII 字母或数字开头，且只能包含字母、数字、下划线和连字符",
        )
    return tuple(raw_segments)


def validate_config_path(value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise WorktreeError("config_path", "配置路径必须是非空字符串")
    if "\\" in value or "\x00" in value:
        raise WorktreeError("config_path", "配置路径不能包含反斜杠或空字节")
    pure = PurePosixPath(value)
    segments = value.split("/")
    if pure.is_absolute() or value.startswith("/"):
        raise WorktreeError("config_path", "配置路径必须相对仓库根目录")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise WorktreeError("config_path", "配置路径不能包含空段、. 或 ..")
    return Path(*segments)


def discover_repository_layout(main_cwd: Path) -> RepositoryLayout:
    resolved_cwd = main_cwd.expanduser().resolve()
    repository_root: Path | None = None
    for candidate in (resolved_cwd, *resolved_cwd.parents):
        if (candidate / ".git").exists():
            repository_root = candidate
            break
    if repository_root is None:
        raise WorktreeError("repository_discovery", f"当前目录不在 Git 仓库内: {resolved_cwd}")
    relative_cwd = resolved_cwd.relative_to(repository_root)
    repository_id = hashlib.sha256(str(repository_root).encode("utf-8")).hexdigest()
    return RepositoryLayout(
        main_cwd=resolved_cwd,
        repository_root=repository_root,
        relative_cwd=relative_cwd,
        storage_root=(repository_root / WORKTREE_STORAGE_RELATIVE).resolve(),
        repository_id=repository_id,
    )


def resolve_inside(root: Path, relative: Path, *, follow_leaf: bool = True) -> Path:
    resolved_root = root.resolve()
    if relative.is_absolute():
        raise WorktreeError("path_boundary", f"路径必须是相对路径: {relative}")
    candidate = resolved_root / relative
    resolved = candidate.resolve() if follow_leaf else candidate.parent.resolve() / candidate.name
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise WorktreeError("path_boundary", f"路径越过允许边界: {relative}") from exc
    return resolved


def worktree_name(role: str, task_id: str) -> str:
    name = f"{role}/{task_id}"
    validate_relative_name(name)
    return name


def branch_name(relative_name: str) -> str:
    validate_relative_name(relative_name)
    return f"julycode/{relative_name}"
