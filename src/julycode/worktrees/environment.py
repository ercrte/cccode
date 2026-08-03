from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from julycode.worktrees.git import GitClient
from julycode.worktrees.models import RepositoryLayout, WorktreeConfig, WorktreeError, WorktreeLease
from julycode.worktrees.paths import resolve_inside, validate_config_path


@dataclass(frozen=True)
class _InitOperation:
    kind: str
    relative: Path
    source: Path
    target: Path


class WorktreeEnvironmentInitializer:
    def __init__(self, git: GitClient | None = None) -> None:
        self.git = git or GitClient()

    async def initialize(
        self,
        *,
        layout: RepositoryLayout,
        lease: WorktreeLease,
        config: WorktreeConfig,
    ) -> None:
        operations = await self._preflight(layout=layout, lease=lease, config=config)
        created_targets: list[Path] = []
        created_parents: list[Path] = []
        try:
            for operation in operations:
                self._ensure_parents(operation.target.parent, lease.root, created_parents)
                if operation.kind in {"copy", "ignored_copy"}:
                    if operation.source.is_dir():
                        shutil.copytree(operation.source, operation.target)
                    else:
                        shutil.copy2(operation.source, operation.target)
                else:
                    operation.target.symlink_to(operation.source.resolve(), target_is_directory=True)
                created_targets.append(operation.target)
            await self.git.configure_hooks(
                main_root=layout.repository_root,
                worktree_root=lease.root,
            )
        except Exception as exc:
            rollback_errors = self._rollback(created_targets, created_parents)
            detail = str(exc)
            if rollback_errors:
                detail += "；回滚失败: " + "；".join(rollback_errors)
            if isinstance(exc, WorktreeError):
                raise WorktreeError("environment", detail) from exc
            raise WorktreeError("environment", detail) from exc

    async def _preflight(
        self,
        *,
        layout: RepositoryLayout,
        lease: WorktreeLease,
        config: WorktreeConfig,
    ) -> tuple[_InitOperation, ...]:
        groups = (
            ("copy", config.copy_paths),
            ("ignored_copy", config.ignored_copy_paths),
            ("symlink", config.symlink_paths),
        )
        seen: set[Path] = set()
        operations: list[_InitOperation] = []
        for kind, values in groups:
            for value in values:
                relative = validate_config_path(value)
                if relative in seen:
                    raise WorktreeError("environment", f"初始化路径重复: {value}")
                seen.add(relative)
                source = layout.repository_root / relative
                target = resolve_inside(lease.root, relative, follow_leaf=False)
                if source.is_symlink():
                    raise WorktreeError("environment", f"初始化源不能是软链: {value}")
                resolved_source = source.resolve()
                try:
                    resolved_source.relative_to(layout.repository_root)
                except ValueError as exc:
                    raise WorktreeError("environment", f"初始化源越过仓库边界: {value}") from exc
                if not source.exists():
                    raise WorktreeError("environment", f"初始化源不存在: {value}")
                if target.exists() or target.is_symlink():
                    raise WorktreeError("environment", f"初始化目标已存在: {value}")
                if kind == "symlink" and not source.is_dir():
                    raise WorktreeError("environment", f"软链初始化源必须是目录: {value}")
                if kind == "ignored_copy" and not await self.git.is_ignored(
                    cwd=layout.repository_root,
                    path=relative,
                ):
                    raise WorktreeError("environment", f"ignored_copy_paths 路径未被 Git 忽略: {value}")
                operations.append(
                    _InitOperation(
                        kind=kind,
                        relative=relative,
                        source=resolved_source,
                        target=target,
                    )
                )
        return tuple(operations)

    def _ensure_parents(self, parent: Path, root: Path, created: list[Path]) -> None:
        missing: list[Path] = []
        current = parent
        while current != root and not current.exists():
            missing.append(current)
            current = current.parent
        for directory in reversed(missing):
            directory.mkdir()
            created.append(directory)

    def _rollback(self, targets: list[Path], parents: list[Path]) -> list[str]:
        errors: list[str] = []
        for target in reversed(targets):
            try:
                if target.is_symlink() or target.is_file():
                    target.unlink()
                elif target.is_dir():
                    shutil.rmtree(target)
            except OSError as exc:
                errors.append(f"{target}: {exc}")
        for parent in reversed(parents):
            try:
                parent.rmdir()
            except OSError as exc:
                if parent.exists() and any(parent.iterdir()):
                    continue
                errors.append(f"{parent}: {exc}")
        return errors
