from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


WorktreeDispositionStatus = Literal["cleaned", "retained"]
CleanupItemStatus = Literal["cleaned", "skipped", "failed"]
GitOperation = Literal["none", "merge", "rebase", "cherry_pick", "revert"]
GitMergeStatus = Literal["merged", "already_integrated", "conflicted", "failed"]


@dataclass(frozen=True)
class GitMergeOutcome:
    status: GitMergeStatus
    head_before: str
    head_after: str
    conflict_paths: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class WorktreeConfig:
    copy_paths: tuple[str, ...] = ()
    symlink_paths: tuple[str, ...] = ()
    ignored_copy_paths: tuple[str, ...] = ()
    cleanup_interval_seconds: float = 3600.0
    retention_days: float = 7.0


@dataclass(frozen=True)
class RepositoryLayout:
    main_cwd: Path
    repository_root: Path
    relative_cwd: Path
    storage_root: Path
    repository_id: str


@dataclass(frozen=True)
class WorktreeMetadata:
    version: int
    repository_id: str
    task_id: str
    role: str
    relative_name: str
    branch: str
    base_commit: str
    created_at: str
    retention: Literal["ephemeral", "persistent"] = "ephemeral"


@dataclass(frozen=True)
class WorktreeLease:
    metadata: WorktreeMetadata
    root: Path
    cwd: Path
    recovered: bool


@dataclass(frozen=True)
class WorktreeChangeState:
    dirty: bool
    untracked: tuple[str, ...]
    new_commit_count: int
    upstream: str | None
    unpushed_commit_count: int


@dataclass(frozen=True)
class WorktreeDisposition:
    status: WorktreeDispositionStatus
    root: Path
    cwd: Path
    branch: str
    reason: str
    state: WorktreeChangeState | None = None


@dataclass(frozen=True)
class CleanupItemResult:
    path: Path
    status: CleanupItemStatus
    reason: str


@dataclass(frozen=True)
class CleanupReport:
    items: tuple[CleanupItemResult, ...] = ()

    @property
    def failures(self) -> tuple[CleanupItemResult, ...]:
        return tuple(item for item in self.items if item.status == "failed")


class WorktreeError(Exception):
    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        self.message = message
        super().__init__(f"Worktree {stage} 失败: {message}")
