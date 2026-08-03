from __future__ import annotations

import fnmatch
import os
import subprocess
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path, PurePosixPath


DEFAULT_SEARCH_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".julycode",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "build",
        "dist",
        "target",
    }
)


class FileCatalog:
    """为只读定位工具提供安全、稳定的项目文件候选集合。"""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()

    def default_files(self) -> tuple[Path, ...]:
        git_files = self._git_files()
        if git_files is not None:
            return git_files
        return self._filesystem_files(self.cwd, apply_default_excludes=True)

    def explicit_files(self, target: Path) -> tuple[Path, ...]:
        lexical_target = target.expanduser()
        if not lexical_target.is_absolute():
            lexical_target = self.cwd / lexical_target
        resolved = lexical_target.resolve(strict=False)
        if not resolved.is_relative_to(self.cwd):
            raise PermissionError(f"路径超出项目目录: {target}")
        if self._contains_symlink(lexical_target):
            return ()
        if resolved == self.cwd:
            return self.default_files()
        if not resolved.exists():
            raise FileNotFoundError(str(target))
        if resolved.is_file():
            safe = self._safe_file(resolved)
            return () if safe is None else (safe,)
        if not resolved.is_dir():
            return ()
        return self._filesystem_files(resolved, apply_default_excludes=False)

    def matching_files(
        self,
        pattern: str,
        *,
        files: Sequence[Path] | None = None,
        base: Path | None = None,
        max_results: int | None = None,
    ) -> tuple[Path, ...]:
        normalized_pattern = _normalize_pattern(pattern)
        candidates = self.default_files() if files is None else files
        match_base = (base or self.cwd).resolve()
        matches: list[Path] = []
        for path in sorted(candidates, key=lambda item: self._display_path(item)):
            try:
                relative = path.resolve().relative_to(match_base).as_posix()
            except ValueError:
                continue
            if not matches_glob(relative, normalized_pattern):
                continue
            matches.append(path)
            if max_results is not None and len(matches) >= max_results:
                break
        return tuple(matches)

    def _git_files(self) -> tuple[Path, ...] | None:
        root_result = self._run_git(("rev-parse", "--show-toplevel"), cwd=self.cwd)
        if root_result is None or root_result.returncode != 0:
            return None
        raw_root = root_result.stdout.decode("utf-8", errors="surrogateescape").strip()
        if not raw_root:
            return None
        repository_root = Path(raw_root).resolve()
        if not self.cwd.is_relative_to(repository_root):
            return None
        files_result = self._run_git(
            ("ls-files", "-co", "--exclude-standard", "-z"),
            cwd=repository_root,
        )
        if files_result is None or files_result.returncode != 0:
            return None

        files: list[Path] = []
        for raw_path in files_result.stdout.split(b"\0"):
            if not raw_path:
                continue
            normalized = _normalize_git_path(os.fsdecode(raw_path))
            if normalized is None:
                continue
            candidate = repository_root.joinpath(*PurePosixPath(normalized).parts)
            try:
                relative = candidate.relative_to(self.cwd)
            except ValueError:
                continue
            if _has_excluded_directory(relative.parts):
                continue
            safe = self._safe_file(candidate)
            if safe is not None:
                files.append(safe)
        return self._sorted_unique(files)

    def _filesystem_files(self, root: Path, *, apply_default_excludes: bool) -> tuple[Path, ...]:
        files: list[Path] = []
        pending = [root]
        while pending:
            current = pending.pop()
            try:
                entries = sorted(os.scandir(current), key=lambda item: item.name, reverse=True)
            except OSError:
                continue
            for entry in entries:
                if entry.is_symlink():
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if not apply_default_excludes or entry.name not in DEFAULT_SEARCH_EXCLUDED_DIRS:
                        pending.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                safe = self._safe_file(path)
                if safe is not None:
                    files.append(safe)
        return self._sorted_unique(files)

    def _safe_file(self, path: Path) -> Path | None:
        if self._contains_symlink(path):
            return None
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not resolved.is_relative_to(self.cwd) or not resolved.is_file():
            return None
        return resolved

    def _contains_symlink(self, path: Path) -> bool:
        try:
            relative = path.absolute().relative_to(self.cwd)
        except ValueError:
            return True
        current = self.cwd
        for part in relative.parts:
            current = current / part
            try:
                if current.is_symlink():
                    return True
            except OSError:
                return True
        return False

    def _sorted_unique(self, files: Sequence[Path]) -> tuple[Path, ...]:
        return tuple(sorted(set(files), key=self._display_path))

    def _display_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.cwd).as_posix()
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _run_git(
        args: tuple[str, ...],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[bytes] | None:
        try:
            return subprocess.run(
                ("git", "-C", str(cwd), *args),
                check=False,
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None


def _normalize_git_path(value: str) -> str | None:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _has_excluded_directory(parts: Sequence[str]) -> bool:
    return any(part in DEFAULT_SEARCH_EXCLUDED_DIRS for part in parts[:-1])


def _normalize_pattern(pattern: str) -> str:
    normalized = str(pattern).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def matches_glob(relative_path: str, pattern: str) -> bool:
    pattern = _normalize_pattern(pattern)
    if not pattern:
        return False
    path_parts = tuple(part for part in relative_path.split("/") if part)
    pattern_parts = tuple(part for part in pattern.split("/") if part)
    if not pattern_parts:
        return False

    @lru_cache(maxsize=None)
    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        current_pattern = pattern_parts[pattern_index]
        if current_pattern == "**":
            return matches(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and matches(path_index + 1, pattern_index)
            )
        if path_index >= len(path_parts):
            return False
        return fnmatch.fnmatchcase(path_parts[path_index], current_pattern) and matches(
            path_index + 1,
            pattern_index + 1,
        )

    return matches(0, 0)
