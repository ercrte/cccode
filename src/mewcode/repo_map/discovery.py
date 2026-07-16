from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable

from mewcode.repo_map.models import (
    DiscoveryResult,
    FileFingerprint,
    RepoMapDiagnostic,
    RepositoryIdentity,
    ScannedFile,
    WorkspaceState,
)


DISCOVERY_VERSION = "discovery-v1"
DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".mewcode",
        "__pycache__",
        ".venv",
        "venv",
        "build",
        "dist",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
    }
)


class RepositoryDiscovery:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()

    def identify(self) -> tuple[RepositoryIdentity, tuple[RepoMapDiagnostic, ...]]:
        root_result = self._run_git(("rev-parse", "--show-toplevel"), cwd=self.cwd)
        if root_result.returncode != 0:
            identity = RepositoryIdentity(
                root=self.cwd,
                repo_id=_digest_text(f"non-git:{self.cwd.as_posix()}"),
                worktree_id=_digest_text(f"cwd:{self.cwd.as_posix()}"),
                head_id="non-git",
                is_git=False,
            )
            return identity, ()

        root = Path(root_result.stdout.decode(errors="surrogateescape").strip()).resolve()
        git_dir = self._git_path(root, "--absolute-git-dir", fallback=root / ".git")
        common_dir = self._git_path(root, "--git-common-dir", fallback=git_dir)
        head_result = self._run_git(("rev-parse", "--verify", "HEAD"), cwd=root)
        commit = head_result.stdout.decode(errors="replace").strip() if head_result.returncode == 0 else "unborn"
        ref_result = self._run_git(("symbolic-ref", "-q", "HEAD"), cwd=root)
        ref = ref_result.stdout.decode(errors="replace").strip() if ref_result.returncode == 0 else "detached"
        identity = RepositoryIdentity(
            root=root,
            repo_id=_digest_text(f"git:{common_dir.as_posix()}"),
            worktree_id=_digest_text(f"worktree:{root.as_posix()}:{git_dir.as_posix()}"),
            head_id=f"{ref}:{commit}",
            is_git=True,
        )
        return identity, ()

    def discover(self) -> DiscoveryResult:
        identity, identity_diagnostics = self.identify()
        diagnostics = list(identity_diagnostics)
        if identity.is_git:
            paths, git_diagnostics = self._git_paths(identity.root)
            diagnostics.extend(git_diagnostics)
            if paths is None:
                paths = self._filesystem_paths(identity.root)
        else:
            paths = self._filesystem_paths(identity.root)

        files: list[ScannedFile] = []
        for relative_path in sorted(paths):
            candidate = _safe_candidate(identity.root, relative_path)
            if candidate is None:
                diagnostics.append(
                    RepoMapDiagnostic("unsafe-path", "已排除符号链接或越界路径", relative_path)
                )
                continue
            try:
                source = candidate.read_bytes()
            except OSError as exc:
                diagnostics.append(
                    RepoMapDiagnostic("read-error", f"无法读取文件：{exc}", relative_path, "error")
                )
                continue
            files.append(
                ScannedFile(
                    fingerprint=FileFingerprint(
                        relative_path=relative_path,
                        content_hash=hashlib.sha256(source).hexdigest(),
                        size=len(source),
                    ),
                    source_bytes=source,
                )
            )
        files.sort(key=lambda item: item.fingerprint.relative_path)
        return DiscoveryResult(identity=identity, files=tuple(files), diagnostics=tuple(diagnostics))

    def _git_paths(
        self,
        root: Path,
    ) -> tuple[set[str] | None, tuple[RepoMapDiagnostic, ...]]:
        result = self._run_git(
            ("ls-files", "-co", "--exclude-standard", "-z", "--", "*.py", "*.pyi"),
            cwd=root,
        )
        if result.returncode != 0:
            message = result.stderr.decode(errors="replace").strip() or "git ls-files 执行失败"
            return None, (RepoMapDiagnostic("git-files-error", message, level="error"),)
        paths: set[str] = set()
        for raw_path in result.stdout.split(b"\0"):
            if not raw_path:
                continue
            value = os.fsdecode(raw_path).replace("\\", "/")
            normalized = _normalize_relative(value)
            if normalized is not None and Path(normalized).suffix in {".py", ".pyi"}:
                paths.add(normalized)
        return paths, ()

    def _filesystem_paths(self, root: Path) -> set[str]:
        discovered: set[str] = set()
        pending = [root]
        while pending:
            current = pending.pop()
            try:
                entries = sorted(os.scandir(current), key=lambda item: item.name)
            except OSError:
                continue
            for entry in entries:
                if entry.is_symlink():
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in DEFAULT_EXCLUDED_DIRS:
                        pending.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False) or path.suffix not in {".py", ".pyi"}:
                    continue
                discovered.add(path.relative_to(root).as_posix())
        return discovered

    def _git_path(self, root: Path, option: str, *, fallback: Path) -> Path:
        result = self._run_git(("rev-parse", option), cwd=root)
        if result.returncode != 0:
            return fallback.resolve()
        raw = Path(result.stdout.decode(errors="surrogateescape").strip())
        return (raw if raw.is_absolute() else root / raw).resolve()

    @staticmethod
    def _run_git(args: tuple[str, ...], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ("git", "-C", str(cwd), *args),
                check=False,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(("git", *args), 1, b"", str(exc).encode())


def scan_repository(cwd: Path) -> DiscoveryResult:
    return RepositoryDiscovery(cwd).discover()


def build_workspace_state(
    discovery: DiscoveryResult,
    *,
    rules_version: str = DISCOVERY_VERSION,
) -> WorkspaceState:
    fingerprints = tuple(sorted((item.fingerprint for item in discovery.files), key=lambda item: item.relative_path))
    payload = {
        "repo_id": discovery.identity.repo_id,
        "worktree_id": discovery.identity.worktree_id,
        "head_id": discovery.identity.head_id,
        "rules_version": rules_version,
        "files": [
            [item.relative_path, item.content_hash, item.size]
            for item in fingerprints
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return WorkspaceState(
        identity=discovery.identity,
        ordered_fingerprints=fingerprints,
        revision=hashlib.sha256(encoded).hexdigest(),
    )


def _safe_candidate(root: Path, relative_path: str) -> Path | None:
    normalized = _normalize_relative(relative_path)
    if normalized is None:
        return None
    candidate = root.joinpath(*PurePosixPath(normalized).parts)
    current = root
    try:
        for part in PurePosixPath(normalized).parts:
            current = current / part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                return None
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve()):
            return None
        if not resolved.is_file():
            return None
    except (OSError, RuntimeError):
        return None
    return resolved


def _normalize_relative(value: str) -> str | None:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()

