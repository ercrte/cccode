from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from julycode.worktrees.environment import WorktreeEnvironmentInitializer
from julycode.worktrees.git import GitClient
from julycode.worktrees.models import (
    CleanupItemResult,
    CleanupReport,
    RepositoryLayout,
    WorktreeChangeState,
    WorktreeConfig,
    WorktreeDisposition,
    WorktreeError,
    WorktreeLease,
    WorktreeMetadata,
)
from julycode.worktrees.paths import (
    branch_name,
    discover_repository_layout,
    resolve_inside,
    worktree_name,
)


METADATA_FILENAME = ".julycode-worktree.json"
METADATA_VERSION = 1
_OBJECT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class WorktreeManager:
    def __init__(
        self,
        main_cwd: Path,
        config: WorktreeConfig,
        *,
        git: GitClient | None = None,
        initializer: WorktreeEnvironmentInitializer | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.main_cwd = main_cwd.resolve()
        self.config = config
        self.git = git or GitClient()
        self.initializer = initializer or WorktreeEnvironmentInitializer(self.git)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._locks: dict[Path, asyncio.Lock] = {}
        self._active: dict[str, Path] = {}
        self._layout_error: WorktreeError | None = None
        try:
            self.layout: RepositoryLayout | None = discover_repository_layout(self.main_cwd)
        except WorktreeError as exc:
            self.layout = None
            self._layout_error = exc

    async def acquire(
        self,
        *,
        task_id: str,
        role: str,
        retention: str = "ephemeral",
    ) -> WorktreeLease:
        if retention not in {"ephemeral", "persistent"}:
            raise WorktreeError("acquire", f"未知 Worktree retention: {retention}")
        layout = self._require_layout()
        relative_name = worktree_name(role, task_id)
        branch = branch_name(relative_name)
        target = resolve_inside(layout.storage_root, Path(relative_name), follow_leaf=False)
        lock = self._lock_for(target)
        async with lock:
            if task_id in self._active:
                raise WorktreeError("acquire", f"任务已有活动 Worktree: {task_id}")
            if target.exists():
                metadata = self._read_metadata(target)
                self._validate_metadata(
                    metadata,
                    task_id=task_id,
                    role=role,
                    relative_name=relative_name,
                    branch=branch,
                    retention=retention,
                )
                cwd = self._lease_cwd(target, layout)
                lease = WorktreeLease(metadata=metadata, root=target, cwd=cwd, recovered=True)
                self._active[task_id] = target
                return lease

            git_root = await self.git.repository_root(cwd=layout.main_cwd)
            if git_root != layout.repository_root:
                raise WorktreeError(
                    "repository",
                    f"Git 仓库根与文件系统边界不一致: {git_root} != {layout.repository_root}",
                )
            base = await self.git.head_commit(cwd=layout.repository_root)
            metadata = WorktreeMetadata(
                version=METADATA_VERSION,
                repository_id=layout.repository_id,
                task_id=task_id,
                role=role,
                relative_name=relative_name,
                branch=branch,
                base_commit=base,
                created_at=self._now().isoformat(),
                retention=retention,  # type: ignore[arg-type]
            )
            await self.git.ensure_local_exclude(repository_root=layout.repository_root)
            await self.git.create_worktree(
                cwd=layout.repository_root,
                path=target,
                branch=branch,
                base=base,
            )
            lease = WorktreeLease(
                metadata=metadata,
                root=target,
                cwd=self._lease_cwd(target, layout),
                recovered=False,
            )
            try:
                self._write_metadata(target, metadata)
                await self.initializer.initialize(layout=layout, lease=lease, config=self.config)
            except Exception:
                await self._cleanup_failed_create(lease)
                raise
            self._active[task_id] = target
            return lease

    async def release(self, lease: WorktreeLease) -> None:
        lock = self._lock_for(lease.root)
        async with lock:
            if self._active.get(lease.metadata.task_id) == lease.root:
                self._active.pop(lease.metadata.task_id, None)

    async def finish(self, lease: WorktreeLease) -> WorktreeDisposition:
        lock = self._lock_for(lease.root)
        async with lock:
            try:
                state = await self.git.change_state(
                    worktree_root=lease.root,
                    base=lease.metadata.base_commit,
                )
                reason = self._retention_reason(state, allow_pushed_commits=False)
                if reason is not None:
                    return self._retained(lease, reason, state)
                return await self._delete_locked(
                    lease,
                    state=state,
                    allow_pushed_commits=False,
                    owner_task_id=lease.metadata.task_id,
                )
            except Exception as exc:
                return self._retained(lease, f"退出检查失败，已保留: {exc}")
            finally:
                if self._active.get(lease.metadata.task_id) == lease.root:
                    self._active.pop(lease.metadata.task_id, None)

    async def delete(
        self,
        lease: WorktreeLease,
        *,
        allow_pushed_commits: bool = False,
    ) -> WorktreeDisposition:
        lock = self._lock_for(lease.root)
        async with lock:
            state = await self.git.change_state(
                worktree_root=lease.root,
                base=lease.metadata.base_commit,
            )
            reason = self._retention_reason(state, allow_pushed_commits=allow_pushed_commits)
            if reason is not None:
                return self._retained(lease, reason, state)
            return await self._delete_locked(
                lease,
                state=state,
                allow_pushed_commits=allow_pushed_commits,
                owner_task_id=None,
            )

    async def cleanup_expired(self) -> CleanupReport:
        layout = self.layout
        if layout is None or not layout.storage_root.exists():
            return CleanupReport()
        results: list[CleanupItemResult] = []
        markers = sorted(layout.storage_root.rglob(METADATA_FILENAME))
        for marker in markers:
            root = marker.parent
            try:
                resolved_root = root.resolve()
                resolved_root.relative_to(layout.storage_root)
                metadata = self._read_metadata(resolved_root)
                expected_root = resolve_inside(
                    layout.storage_root,
                    Path(metadata.relative_name),
                    follow_leaf=True,
                )
                if expected_root != resolved_root:
                    raise WorktreeError("cleanup", "元数据名称与候选目录不匹配")
                self._validate_metadata(
                    metadata,
                    task_id=metadata.task_id,
                    role=metadata.role,
                    relative_name=metadata.relative_name,
                    branch=branch_name(metadata.relative_name),
                    retention=metadata.retention,
                )
                if metadata.retention == "persistent":
                    results.append(CleanupItemResult(resolved_root, "skipped", "长期 Worktree 不自动清理"))
                    continue
                created_at = self._parse_datetime(metadata.created_at)
                if self._now() < created_at + timedelta(days=self.config.retention_days):
                    results.append(CleanupItemResult(resolved_root, "skipped", "尚未过期"))
                    continue
                if metadata.task_id in self._active:
                    results.append(CleanupItemResult(resolved_root, "skipped", "任务仍在运行"))
                    continue
                lease = WorktreeLease(
                    metadata=metadata,
                    root=resolved_root,
                    cwd=self._lease_cwd(resolved_root, layout),
                    recovered=True,
                )
                disposition = await self.delete(lease, allow_pushed_commits=True)
                status = "cleaned" if disposition.status == "cleaned" else "skipped"
                results.append(CleanupItemResult(resolved_root, status, disposition.reason))
            except Exception as exc:
                results.append(CleanupItemResult(root, "failed", str(exc)))
        return CleanupReport(items=tuple(results))

    def active_task_ids(self) -> frozenset[str]:
        return frozenset(self._active)

    async def _delete_locked(
        self,
        lease: WorktreeLease,
        *,
        state: WorktreeChangeState,
        allow_pushed_commits: bool,
        owner_task_id: str | None,
    ) -> WorktreeDisposition:
        layout = self._require_layout()
        resolved_root = lease.root.resolve()
        try:
            resolved_root.relative_to(layout.storage_root)
        except ValueError as exc:
            raise WorktreeError("delete", f"目标不在 Worktree 根目录内: {resolved_root}") from exc
        metadata = self._read_metadata(resolved_root)
        if metadata != lease.metadata:
            raise WorktreeError("delete", "磁盘元数据与 lease 不一致")
        active_root = self._active.get(metadata.task_id)
        if active_root is not None and owner_task_id != metadata.task_id:
            return self._retained(lease, "任务仍在运行，拒绝删除", state)
        reason = self._retention_reason(state, allow_pushed_commits=allow_pushed_commits)
        if reason is not None:
            return self._retained(lease, reason, state)
        try:
            await self.git.remove_worktree(main_root=layout.repository_root, path=resolved_root)
            await self.git.delete_branch(main_root=layout.repository_root, branch=metadata.branch)
        except Exception as exc:
            return self._retained(lease, f"删除未完整完成: {exc}", state)
        return WorktreeDisposition(
            status="cleaned",
            root=resolved_root,
            cwd=lease.cwd,
            branch=metadata.branch,
            reason="工作树无受保护变更，已清理",
            state=state,
        )

    async def _cleanup_failed_create(self, lease: WorktreeLease) -> None:
        try:
            state = await self.git.change_state(
                worktree_root=lease.root,
                base=lease.metadata.base_commit,
            )
            if state.dirty or state.untracked or state.new_commit_count:
                return
            layout = self._require_layout()
            await self.git.remove_worktree(main_root=layout.repository_root, path=lease.root)
            await self.git.delete_branch(main_root=layout.repository_root, branch=lease.metadata.branch)
        except Exception:
            return

    def _retention_reason(
        self,
        state: WorktreeChangeState,
        *,
        allow_pushed_commits: bool,
    ) -> str | None:
        reasons: list[str] = []
        if state.dirty:
            reasons.append("存在未提交修改")
        if state.untracked:
            reasons.append(f"存在未跟踪文件: {', '.join(state.untracked[:5])}")
        if state.new_commit_count and not allow_pushed_commits:
            reasons.append(f"相对创建基线有 {state.new_commit_count} 个新增提交")
        elif state.unpushed_commit_count:
            reasons.append(f"存在 {state.unpushed_commit_count} 个未推送提交")
        return "；".join(reasons) if reasons else None

    def _retained(
        self,
        lease: WorktreeLease,
        reason: str,
        state: WorktreeChangeState | None = None,
    ) -> WorktreeDisposition:
        return WorktreeDisposition(
            status="retained",
            root=lease.root,
            cwd=lease.cwd,
            branch=lease.metadata.branch,
            reason=reason,
            state=state,
        )

    def _write_metadata(self, root: Path, metadata: WorktreeMetadata) -> None:
        path = root / METADATA_FILENAME
        temporary = root / f"{METADATA_FILENAME}.tmp"
        try:
            temporary.write_text(
                json.dumps(asdict(metadata), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            raise WorktreeError("metadata", f"无法写入恢复元数据: {exc}") from exc

    def _read_metadata(self, root: Path) -> WorktreeMetadata:
        path = root / METADATA_FILENAME
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorktreeError("recovery", f"无法读取恢复元数据 {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise WorktreeError("recovery", "恢复元数据顶层必须是对象")
        fields = (
            "version",
            "repository_id",
            "task_id",
            "role",
            "relative_name",
            "branch",
            "base_commit",
            "created_at",
        )
        allowed = {*fields, "retention"}
        if not set(raw).issubset(allowed) or not set(fields).issubset(raw):
            raise WorktreeError("recovery", "恢复元数据字段不完整或包含未知字段")
        if not isinstance(raw["version"], int) or any(not isinstance(raw[field], str) for field in fields[1:]):
            raise WorktreeError("recovery", "恢复元数据字段类型无效")
        retention = raw.get("retention", "ephemeral")
        if retention not in {"ephemeral", "persistent"}:
            raise WorktreeError("recovery", "恢复元数据 retention 无效")
        return WorktreeMetadata(**raw)

    def _validate_metadata(
        self,
        metadata: WorktreeMetadata,
        *,
        task_id: str,
        role: str,
        relative_name: str,
        branch: str,
        retention: str = "ephemeral",
    ) -> None:
        layout = self._require_layout()
        expected = {
            "version": METADATA_VERSION,
            "repository_id": layout.repository_id,
            "task_id": task_id,
            "role": role,
            "relative_name": relative_name,
            "branch": branch,
            "retention": retention,
        }
        for field, value in expected.items():
            if getattr(metadata, field) != value:
                raise WorktreeError("recovery", f"恢复元数据 {field} 不匹配")
        if _OBJECT_ID_RE.fullmatch(metadata.base_commit) is None:
            raise WorktreeError("recovery", "恢复元数据 base_commit 无效")
        self._parse_datetime(metadata.created_at)

    def _lease_cwd(self, root: Path, layout: RepositoryLayout) -> Path:
        cwd = resolve_inside(root, layout.relative_cwd, follow_leaf=True)
        if not cwd.is_dir():
            raise WorktreeError("enter", f"隔离工作目录不存在: {cwd}")
        return cwd

    def _parse_datetime(self, value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise WorktreeError("recovery", "恢复元数据 created_at 无效") from exc
        if parsed.tzinfo is None:
            raise WorktreeError("recovery", "恢复元数据 created_at 必须包含时区")
        return parsed.astimezone(timezone.utc)

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    def _require_layout(self) -> RepositoryLayout:
        if self.layout is None:
            raise self._layout_error or WorktreeError("repository_discovery", "未找到 Git 仓库")
        return self.layout

    def _lock_for(self, path: Path) -> asyncio.Lock:
        resolved = path.resolve()
        lock = self._locks.get(resolved)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[resolved] = lock
        return lock
