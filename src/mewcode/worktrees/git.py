from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from mewcode.errors import redact_secret
from mewcode.worktrees.models import WorktreeChangeState, WorktreeError


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: str
    stderr: str


class GitClient:
    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def run(self, args: Sequence[str], *, cwd: Path) -> GitCommandResult:
        resolved_cwd = cwd.resolve()
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                process = subprocess.Popen(
                    ("git", *args),
                    cwd=resolved_cwd,
                    stdout=stdout_file,
                    stderr=stderr_file,
                )
            except OSError as exc:
                raise WorktreeError("git", f"无法启动 Git: {exc}") from exc
            deadline = time.monotonic() + self.timeout_seconds
            try:
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        process.kill()
                        process.wait()
                        raise WorktreeError("git", f"Git 命令超时: {' '.join(args[:3])}")
                    await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                raise
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
        return GitCommandResult(
            returncode=process.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace").strip(),
            stderr=stderr.decode("utf-8", errors="replace").strip(),
        )

    async def repository_root(self, *, cwd: Path) -> Path:
        result = await self._checked(("rev-parse", "--show-toplevel"), cwd=cwd, stage="repository")
        return Path(result.stdout).resolve()

    async def head_commit(self, *, cwd: Path) -> str:
        result = await self._checked(("rev-parse", "HEAD"), cwd=cwd, stage="head")
        return result.stdout

    async def commit_exists(self, *, cwd: Path, commit: str) -> bool:
        result = await self.run(("cat-file", "-e", f"{commit}^{{commit}}"), cwd=cwd)
        if result.returncode == 0:
            return True
        if result.returncode in {1, 128}:
            return False
        self._raise_result("commit_check", result)
        return False

    async def is_ancestor(self, *, cwd: Path, ancestor: str, descendant: str) -> bool:
        result = await self.run(("merge-base", "--is-ancestor", ancestor, descendant), cwd=cwd)
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        self._raise_result("ancestor_check", result)
        return False

    async def branch_exists(self, *, cwd: Path, branch: str) -> bool:
        result = await self.run(("show-ref", "--verify", "--quiet", f"refs/heads/{branch}"), cwd=cwd)
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        self._raise_result("branch_check", result)
        return False

    async def ensure_local_exclude(self, *, repository_root: Path) -> None:
        result = await self._checked(
            ("rev-parse", "--git-common-dir"),
            cwd=repository_root,
            stage="exclude",
        )
        common = Path(result.stdout)
        if not common.is_absolute():
            common = repository_root / common
        exclude_path = common.resolve() / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        lines = existing.splitlines()
        required = (
            "/.mewcode/worktrees/",
            "/.mewcode/context/",
            "/.mewcode/sessions/",
            "/.mewcode/memory/",
            "/.mewcode-worktree.json",
        )
        missing = [pattern for pattern in required if pattern not in lines]
        if not missing:
            return
        prefix = existing
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        exclude_path.write_text(prefix + "\n".join(missing) + "\n", encoding="utf-8")

    async def create_worktree(
        self,
        *,
        cwd: Path,
        path: Path,
        branch: str,
        base: str,
    ) -> None:
        if await self.branch_exists(cwd=cwd, branch=branch):
            raise WorktreeError("create", f"分支已存在，拒绝覆盖: {branch}")
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._checked(
            ("worktree", "add", "-b", branch, str(path.resolve()), base),
            cwd=cwd,
            stage="create",
        )

    async def configure_hooks(self, *, main_root: Path, worktree_root: Path) -> None:
        current = await self.run(("config", "--path", "--get", "core.hooksPath"), cwd=main_root)
        if current.returncode == 1:
            return
        if current.returncode != 0:
            self._raise_result("hooks", current)
        raw_path = Path(current.stdout).expanduser()
        effective = raw_path.resolve() if raw_path.is_absolute() else (main_root / raw_path).resolve()
        await self._checked(
            ("config", "extensions.worktreeConfig", "true"),
            cwd=main_root,
            stage="hooks",
        )
        await self._checked(
            ("config", "--worktree", "core.hooksPath", str(effective)),
            cwd=worktree_root,
            stage="hooks",
        )

    async def is_ignored(self, *, cwd: Path, path: Path) -> bool:
        result = await self.run(("check-ignore", "--quiet", "--", str(path)), cwd=cwd)
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        self._raise_result("ignored_check", result)
        return False

    async def change_state(self, *, worktree_root: Path, base: str) -> WorktreeChangeState:
        status = await self._checked(
            ("status", "--porcelain=v1", "--untracked-files=all"),
            cwd=worktree_root,
            stage="status",
        )
        dirty = False
        untracked: list[str] = []
        for line in status.stdout.splitlines():
            if line.startswith("?? "):
                untracked.append(line[3:])
            elif line:
                dirty = True

        new_count_result = await self._checked(
            ("rev-list", "--count", f"{base}..HEAD"),
            cwd=worktree_root,
            stage="status",
        )
        new_commit_count = self._parse_count(new_count_result.stdout, "新增提交")
        upstream: str | None = None
        unpushed_commit_count = 0
        if new_commit_count:
            upstream_result = await self.run(
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
                cwd=worktree_root,
            )
            if upstream_result.returncode == 0:
                upstream = upstream_result.stdout
                unpushed_result = await self._checked(
                    ("rev-list", "--count", f"{base}..HEAD", "--not", upstream),
                    cwd=worktree_root,
                    stage="status",
                )
                unpushed_commit_count = self._parse_count(unpushed_result.stdout, "未推送提交")
            elif upstream_result.returncode == 1 or "no upstream" in upstream_result.stderr.lower():
                unpushed_commit_count = new_commit_count
            else:
                self._raise_result("status", upstream_result)

        return WorktreeChangeState(
            dirty=dirty,
            untracked=tuple(untracked),
            new_commit_count=new_commit_count,
            upstream=upstream,
            unpushed_commit_count=unpushed_commit_count,
        )

    async def remove_worktree(self, *, main_root: Path, path: Path) -> None:
        await self._checked(
            ("worktree", "remove", str(path.resolve())),
            cwd=main_root,
            stage="delete",
        )

    async def delete_branch(self, *, main_root: Path, branch: str) -> None:
        if not await self.branch_exists(cwd=main_root, branch=branch):
            return
        await self._checked(("branch", "-D", branch), cwd=main_root, stage="delete")

    async def _checked(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        stage: str,
    ) -> GitCommandResult:
        result = await self.run(args, cwd=cwd)
        if result.returncode != 0:
            self._raise_result(stage, result)
        return result

    def _raise_result(self, stage: str, result: GitCommandResult) -> None:
        detail = redact_secret(result.stderr or result.stdout or f"exit={result.returncode}")
        raise WorktreeError(stage, detail[:1000])

    def _parse_count(self, value: str, label: str) -> int:
        try:
            return int(value)
        except ValueError as exc:
            raise WorktreeError("status", f"无法解析{label}数量: {value!r}") from exc
