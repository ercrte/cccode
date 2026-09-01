from __future__ import annotations

import os
import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from julycode.worktrees import (
    GitMergeOutcome,
    WorktreeConfig,
    WorktreeDisposition,
    WorktreeError,
    WorktreeLease,
    GitClient,
    RepositoryLayout,
    WorktreeEnvironmentInitializer,
    WorktreeMetadata,
    WorktreeManager,
    WorktreeJanitor,
    METADATA_FILENAME,
    branch_name,
    discover_repository_layout,
    resolve_inside,
    validate_config_path,
    validate_relative_name,
    worktree_name,
)
from julycode.subagents.cache import FileReadCache


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def init_repository(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "config", "user.name", "JulyCode Tests")
    git(path, "config", "user.email", "julycode@example.test")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-qm", "initial")
    return path


async def create_environment_fixture(tmp_path: Path) -> tuple[RepositoryLayout, WorktreeLease, GitClient]:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    layout = discover_repository_layout(repository)
    root = layout.storage_root / "reviewer/task-1"
    branch = "julycode/reviewer/task-1"
    base = await client.head_commit(cwd=repository)
    await client.ensure_local_exclude(repository_root=repository)
    await client.create_worktree(cwd=repository, path=root, branch=branch, base=base)
    metadata = WorktreeMetadata(
        version=1,
        repository_id=layout.repository_id,
        task_id="task-1",
        role="reviewer",
        relative_name="reviewer/task-1",
        branch=branch,
        base_commit=base,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return layout, WorktreeLease(metadata=metadata, root=root, cwd=root, recovered=False), client


def test_worktree_models_have_expected_defaults() -> None:
    assert WorktreeConfig().retention_days == 7.0
    assert WorktreeConfig().cleanup_interval_seconds == 3600.0
    assert WorktreeLease
    assert WorktreeDisposition


def test_git_merge_outcome_model() -> None:
    outcome = GitMergeOutcome("merged", "a" * 40, "b" * 40)
    assert outcome.conflict_paths == ()
    assert outcome.detail == ""


@pytest.mark.asyncio
async def test_current_branch_and_git_is_clean(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    branch = git(repository, "branch", "--show-current").stdout.strip()
    assert await client.current_branch(cwd=repository) == branch
    assert await client.is_clean(cwd=repository)
    (repository / "untracked.txt").write_text("new", encoding="utf-8")
    assert not await client.is_clean(cwd=repository)
    (repository / "untracked.txt").unlink()
    git(repository, "checkout", "--detach", "-q")
    assert await client.current_branch(cwd=repository) is None


@pytest.mark.asyncio
async def test_commit_parents_and_git_fast_forward(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    root = await client.head_commit(cwd=repository)
    assert await client.commit_parents(cwd=repository, commit=root) == ()
    git(repository, "checkout", "-qb", "next")
    (repository / "next.txt").write_text("next", encoding="utf-8")
    git(repository, "add", "next.txt")
    git(repository, "commit", "-qm", "next")
    target = await client.head_commit(cwd=repository)
    git(repository, "checkout", "-q", "master")
    assert await client.fast_forward(cwd=repository, target=target) == target
    assert (repository / "next.txt").read_text(encoding="utf-8") == "next"


@pytest.mark.asyncio
async def test_merge_no_ff_success_and_already_integrated(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    base = await client.head_commit(cwd=repository)
    worktree = tmp_path / "source"
    await client.create_worktree(cwd=repository, path=worktree, branch="source", base=base)
    (worktree / "source.txt").write_text("source", encoding="utf-8")
    git(worktree, "add", "source.txt")
    git(worktree, "commit", "-qm", "source")
    source = await client.head_commit(cwd=worktree)

    outcome = await client.merge_no_ff(cwd=repository, source=source, message="merge source")
    assert outcome.status == "merged"
    assert await client.commit_parents(cwd=repository, commit=outcome.head_after) == (base, source)
    repeated = await client.merge_no_ff(cwd=repository, source=source, message="again")
    assert repeated.status == "already_integrated"
    assert repeated.head_after == outcome.head_after


@pytest.mark.asyncio
async def test_merge_conflict_aborts(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    base = await client.head_commit(cwd=repository)
    source_root = tmp_path / "source"
    await client.create_worktree(cwd=repository, path=source_root, branch="source", base=base)
    (source_root / "README.md").write_text("source\n", encoding="utf-8")
    git(source_root, "add", "README.md")
    git(source_root, "commit", "-qm", "source")
    source = await client.head_commit(cwd=source_root)
    (repository / "README.md").write_text("main\n", encoding="utf-8")
    git(repository, "add", "README.md")
    git(repository, "commit", "-qm", "main")
    before = await client.head_commit(cwd=repository)

    outcome = await client.merge_no_ff(cwd=repository, source=source, message="conflict")

    assert outcome.status == "conflicted"
    assert outcome.conflict_paths == ("README.md",)
    assert await client.head_commit(cwd=repository) == before
    assert await client.operation(cwd=repository) == "none"
    assert await client.is_clean(cwd=repository)


@pytest.mark.parametrize(
    "value",
    ("reviewer/task-1", "A0/safe_9", "x", "a" * 64),
)
def test_validate_relative_name_accepts_safe_nested_names(value: str) -> None:
    assert "/".join(validate_relative_name(value)) == value


@pytest.mark.parametrize(
    "value",
    (
        "",
        "/absolute",
        "a//b",
        "a/./b",
        "a/../b",
        "a\\b",
        "-leading",
        "a.b",
        "中文",
        "a" * 65,
        "a/" + "b" * 65,
        "/".join(("a" * 50,) * 5),
    ),
)
def test_validate_relative_name_rejects_unsafe_names(value: str) -> None:
    with pytest.raises(WorktreeError, match="path_validation"):
        validate_relative_name(value)


@pytest.mark.parametrize("value", (".env", ".venv/lib", "config/local.yaml", "node_modules"))
def test_validate_config_path_accepts_repository_relative_paths(value: str) -> None:
    assert validate_config_path(value) == Path(*value.split("/"))


@pytest.mark.parametrize("value", ("", "/tmp/a", "../a", "a/../b", "a//b", "a\\b"))
def test_validate_config_path_rejects_unsafe_paths(value: str) -> None:
    with pytest.raises(WorktreeError, match="config_path"):
        validate_config_path(value)


def test_repository_layout_supports_subdirectory_start(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    nested = repository / "src" / "pkg"
    nested.mkdir(parents=True)

    layout = discover_repository_layout(nested)

    assert layout.main_cwd == nested.resolve()
    assert layout.repository_root == repository.resolve()
    assert layout.relative_cwd == Path("src/pkg")
    assert layout.storage_root == (repository / ".julycode/worktrees").resolve()
    assert len(layout.repository_id) == 64


def test_repository_layout_rejects_non_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: False if path.name == ".git" else original_exists(path),
    )
    with pytest.raises(WorktreeError, match="repository_discovery"):
        discover_repository_layout(tmp_path)


def test_resolve_inside_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorktreeError, match="path_boundary"):
        resolve_inside(root, Path("link/file.txt"))


def test_resolve_inside_accepts_missing_leaf(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    resolved = resolve_inside(root, Path("new.txt"), follow_leaf=False)
    assert resolved == root / "new.txt"


def test_generated_names_ignore_task_text() -> None:
    relative = worktree_name("reviewer", "subagent-123-1")
    assert relative == "reviewer/subagent-123-1"
    assert branch_name(relative) == "julycode/reviewer/subagent-123-1"


def test_file_read_cache_isolated_by_absolute_worktree_path(tmp_path: Path) -> None:
    first = tmp_path / "one/same.txt"
    second = tmp_path / "two/same.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    cache = FileReadCache()
    cache.put(first, "one")
    cache.put(second, "two")

    assert cache.get(first) == "one"
    assert cache.get(second) == "two"
    assert len(cache._entries) == 2


@pytest.mark.asyncio
async def test_git_client_run_and_git_repository_identity(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    before = Path.cwd()

    result = await client.run(("status", "--short"), cwd=repository)

    assert result.returncode == 0
    assert await client.repository_root(cwd=repository / ".git" / "..") == repository.resolve()
    assert len(await client.head_commit(cwd=repository)) == 40
    assert Path.cwd() == before


@pytest.mark.asyncio
async def test_git_repository_identity_rejects_non_repository() -> None:
    with pytest.raises(WorktreeError, match="repository"):
        await GitClient().repository_root(cwd=Path("/"))


@pytest.mark.asyncio
async def test_local_exclude_hides_worktree_storage(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    await client.ensure_local_exclude(repository_root=repository)
    storage = repository / ".julycode/worktrees/demo"
    storage.mkdir(parents=True)
    (storage / "file.txt").write_text("ignored", encoding="utf-8")

    assert git(repository, "status", "--porcelain").stdout == ""


@pytest.mark.asyncio
async def test_create_git_worktree_excludes_main_uncommitted_changes(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    (repository / "README.md").write_text("dirty main\n", encoding="utf-8")
    worktree = repository / ".julycode/worktrees/reviewer/task-1"
    await client.ensure_local_exclude(repository_root=repository)

    await client.create_worktree(
        cwd=repository,
        path=worktree,
        branch="julycode/reviewer/task-1",
        base=await client.head_commit(cwd=repository),
    )

    assert (worktree / "README.md").read_text(encoding="utf-8") == "base\n"
    assert (repository / "README.md").read_text(encoding="utf-8") == "dirty main\n"
    assert (worktree / ".git").is_file()


@pytest.mark.asyncio
async def test_create_git_worktree_shares_repository_objects(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    worktree = repository / ".julycode/worktrees/reviewer/task-1"
    await client.ensure_local_exclude(repository_root=repository)
    await client.create_worktree(
        cwd=repository,
        path=worktree,
        branch="julycode/reviewer/task-1",
        base=await client.head_commit(cwd=repository),
    )

    git_file = (worktree / ".git").read_text(encoding="utf-8")
    assert "worktrees" in git_file
    assert not (worktree / ".git" / "objects").exists()


@pytest.mark.asyncio
async def test_branch_conflict_does_not_reset_existing_branch(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    branch = "julycode/reviewer/task-1"
    git(repository, "branch", branch)
    original = git(repository, "rev-parse", branch).stdout.strip()

    with pytest.raises(WorktreeError, match="分支已存在"):
        await client.create_worktree(
            cwd=repository,
            path=repository / ".julycode/worktrees/reviewer/task-1",
            branch=branch,
            base="HEAD",
        )

    assert git(repository, "rev-parse", branch).stdout.strip() == original


@pytest.mark.asyncio
async def test_worktree_inherits_custom_git_hooks(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    hooks = repository / ".githooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\necho hook-blocked >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    git(repository, "config", "core.hooksPath", ".githooks")
    client = GitClient()
    worktree = repository / ".julycode/worktrees/reviewer/task-1"
    await client.ensure_local_exclude(repository_root=repository)
    await client.create_worktree(cwd=repository, path=worktree, branch="julycode/reviewer/task-1", base="HEAD")
    before = git(repository, "config", "--get", "core.hooksPath").stdout.strip()

    await client.configure_hooks(main_root=repository, worktree_root=worktree)

    (worktree / "new.txt").write_text("new", encoding="utf-8")
    git(worktree, "add", "new.txt")
    commit = git(worktree, "commit", "-m", "blocked", check=False)
    assert commit.returncode != 0
    assert "hook-blocked" in commit.stderr
    assert git(repository, "config", "--get", "core.hooksPath").stdout.strip() == before


@pytest.mark.asyncio
async def test_shared_hooks_need_no_override(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    worktree = repository / ".julycode/worktrees/reviewer/task-1"
    await client.ensure_local_exclude(repository_root=repository)
    await client.create_worktree(cwd=repository, path=worktree, branch="julycode/reviewer/task-1", base="HEAD")

    await client.configure_hooks(main_root=repository, worktree_root=worktree)

    assert git(repository, "config", "--get", "core.hooksPath", check=False).returncode == 1


@pytest.mark.asyncio
async def test_change_state_tracks_dirty_untracked_and_unpushed(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    client = GitClient()
    base = await client.head_commit(cwd=repository)
    worktree = repository / ".julycode/worktrees/reviewer/task-1"
    await client.ensure_local_exclude(repository_root=repository)
    await client.create_worktree(cwd=repository, path=worktree, branch="julycode/reviewer/task-1", base=base)

    clean = await client.change_state(worktree_root=worktree, base=base)
    assert clean == clean.__class__(False, (), 0, None, 0)

    (worktree / "README.md").write_text("changed\n", encoding="utf-8")
    (worktree / "untracked.txt").write_text("new\n", encoding="utf-8")
    changed = await client.change_state(worktree_root=worktree, base=base)
    assert changed.dirty is True
    assert changed.untracked == ("untracked.txt",)

    git(worktree, "add", "README.md", "untracked.txt")
    git(worktree, "commit", "-qm", "work")
    committed = await client.change_state(worktree_root=worktree, base=base)
    assert committed.new_commit_count == 1
    assert committed.upstream is None
    assert committed.unpushed_commit_count == 1


@pytest.mark.asyncio
async def test_change_state_detects_pushed_and_later_unpushed_commits(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    git(repository, "remote", "add", "origin", str(remote))
    client = GitClient()
    base = await client.head_commit(cwd=repository)
    branch = "julycode/reviewer/task-1"
    worktree = repository / ".julycode/worktrees/reviewer/task-1"
    await client.ensure_local_exclude(repository_root=repository)
    await client.create_worktree(cwd=repository, path=worktree, branch=branch, base=base)
    (worktree / "first.txt").write_text("first", encoding="utf-8")
    git(worktree, "add", "first.txt")
    git(worktree, "commit", "-qm", "first")
    git(worktree, "push", "-qu", "origin", branch)

    pushed = await client.change_state(worktree_root=worktree, base=base)
    assert pushed.upstream == f"origin/{branch}"
    assert pushed.unpushed_commit_count == 0

    (worktree / "second.txt").write_text("second", encoding="utf-8")
    git(worktree, "add", "second.txt")
    git(worktree, "commit", "-qm", "second")
    ahead = await client.change_state(worktree_root=worktree, base=base)
    assert ahead.new_commit_count == 2
    assert ahead.unpushed_commit_count == 1


@pytest.mark.asyncio
async def test_environment_preflight_rejects_missing_source_without_writes(tmp_path: Path) -> None:
    layout, lease, client = await create_environment_fixture(tmp_path)
    before = sorted(path.relative_to(lease.root) for path in lease.root.rglob("*"))

    with pytest.raises(WorktreeError, match="environment.*不存在"):
        await WorktreeEnvironmentInitializer(client).initialize(
            layout=layout,
            lease=lease,
            config=WorktreeConfig(copy_paths=("missing.local",)),
        )

    assert sorted(path.relative_to(lease.root) for path in lease.root.rglob("*")) == before


@pytest.mark.asyncio
async def test_environment_preflight_rejects_target_conflict(tmp_path: Path) -> None:
    layout, lease, client = await create_environment_fixture(tmp_path)
    (layout.repository_root / "README.md").write_text("main local", encoding="utf-8")

    with pytest.raises(WorktreeError, match="目标已存在"):
        await WorktreeEnvironmentInitializer(client).initialize(
            layout=layout,
            lease=lease,
            config=WorktreeConfig(copy_paths=("README.md",)),
        )

    assert (lease.root / "README.md").read_text(encoding="utf-8") == "base\n"


@pytest.mark.asyncio
async def test_environment_preflight_rejects_source_symlink_and_wrong_type(tmp_path: Path) -> None:
    layout, lease, client = await create_environment_fixture(tmp_path)
    (layout.repository_root / "real.local").write_text("real", encoding="utf-8")
    (layout.repository_root / "linked.local").symlink_to(layout.repository_root / "real.local")
    initializer = WorktreeEnvironmentInitializer(client)

    with pytest.raises(WorktreeError, match="不能是软链"):
        await initializer.initialize(
            layout=layout,
            lease=lease,
            config=WorktreeConfig(copy_paths=("linked.local",)),
        )
    with pytest.raises(WorktreeError, match="必须是目录"):
        await initializer.initialize(
            layout=layout,
            lease=lease,
            config=WorktreeConfig(symlink_paths=("real.local",)),
        )


@pytest.mark.asyncio
async def test_environment_preflight_rejects_non_ignored_copy(tmp_path: Path) -> None:
    layout, lease, client = await create_environment_fixture(tmp_path)
    (layout.repository_root / "local.txt").write_text("local", encoding="utf-8")

    with pytest.raises(WorktreeError, match="未被 Git 忽略"):
        await WorktreeEnvironmentInitializer(client).initialize(
            layout=layout,
            lease=lease,
            config=WorktreeConfig(ignored_copy_paths=("local.txt",)),
        )


@pytest.mark.asyncio
async def test_copy_paths_and_ignored_copy_are_independent(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    (repository / ".gitignore").write_text(".env\n", encoding="utf-8")
    git(repository, "add", ".gitignore")
    git(repository, "commit", "-qm", "ignore env")
    (repository / "local-config").mkdir()
    config_file = repository / "local-config/settings.ini"
    config_file.write_text("mode=main\n", encoding="utf-8")
    config_file.chmod(0o640)
    (repository / ".env").write_text("TOKEN=local\n", encoding="utf-8")
    (repository / ".secret").write_text("not-declared\n", encoding="utf-8")
    client = GitClient()
    layout = discover_repository_layout(repository)
    root = layout.storage_root / "reviewer/task-1"
    base = await client.head_commit(cwd=repository)
    await client.ensure_local_exclude(repository_root=repository)
    await client.create_worktree(cwd=repository, path=root, branch="julycode/reviewer/task-1", base=base)
    lease = WorktreeLease(
        metadata=WorktreeMetadata(1, layout.repository_id, "task-1", "reviewer", "reviewer/task-1", "julycode/reviewer/task-1", base, datetime.now(timezone.utc).isoformat()),
        root=root,
        cwd=root,
        recovered=False,
    )

    await WorktreeEnvironmentInitializer(client).initialize(
        layout=layout,
        lease=lease,
        config=WorktreeConfig(
            copy_paths=("local-config",),
            ignored_copy_paths=(".env",),
        ),
    )

    copied = root / "local-config/settings.ini"
    copied.write_text("mode=child\n", encoding="utf-8")
    assert config_file.read_text(encoding="utf-8") == "mode=main\n"
    assert (root / ".env").read_text(encoding="utf-8") == "TOKEN=local\n"
    assert not (root / ".secret").exists()
    assert copied.stat().st_mode & 0o777 == 0o640


@pytest.mark.asyncio
async def test_symlink_paths_target_main_directory(tmp_path: Path) -> None:
    layout, lease, client = await create_environment_fixture(tmp_path)
    dependency = layout.repository_root / ".venv"
    dependency.mkdir()
    (dependency / "marker").write_text("shared", encoding="utf-8")

    await WorktreeEnvironmentInitializer(client).initialize(
        layout=layout,
        lease=lease,
        config=WorktreeConfig(symlink_paths=(".venv",)),
    )

    target = lease.root / ".venv"
    assert target.is_symlink()
    assert target.resolve() == dependency.resolve()
    assert (target / "marker").read_text(encoding="utf-8") == "shared"


class FailingHooksGitClient(GitClient):
    async def configure_hooks(self, *, main_root: Path, worktree_root: Path) -> None:
        _ = main_root, worktree_root
        raise WorktreeError("hooks", "injected failure")


@pytest.mark.asyncio
async def test_environment_rollback_removes_only_created_targets(tmp_path: Path) -> None:
    layout, lease, _client = await create_environment_fixture(tmp_path)
    source = layout.repository_root / "local/config.txt"
    source.parent.mkdir()
    source.write_text("config", encoding="utf-8")
    existing = lease.root / "existing.txt"
    existing.write_text("keep", encoding="utf-8")

    with pytest.raises(WorktreeError, match="environment.*injected failure"):
        await WorktreeEnvironmentInitializer(FailingHooksGitClient()).initialize(
            layout=layout,
            lease=lease,
            config=WorktreeConfig(copy_paths=("local/config.txt",)),
        )

    assert not (lease.root / "local/config.txt").exists()
    assert not (lease.root / "local").exists()
    assert existing.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_manager_acquire_new_writes_complete_metadata(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    manager = WorktreeManager(repository, WorktreeConfig())

    lease = await manager.acquire(task_id="task-1", role="reviewer")

    raw = json.loads((lease.root / METADATA_FILENAME).read_text(encoding="utf-8"))
    assert lease.recovered is False
    assert lease.root == (repository / ".julycode/worktrees/reviewer/task-1").resolve()
    assert lease.cwd == lease.root
    assert raw["repository_id"] == manager.layout.repository_id  # type: ignore[union-attr]
    assert raw["task_id"] == "task-1"
    assert raw["role"] == "reviewer"
    assert raw["branch"] == "julycode/reviewer/task-1"
    assert raw["base_commit"] == git(repository, "rev-parse", "HEAD").stdout.strip()
    assert datetime.fromisoformat(raw["created_at"]).tzinfo is not None


@pytest.mark.asyncio
async def test_explicit_base_acquire_and_recovery(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    base = git(repository, "rev-parse", "HEAD").stdout.strip()
    (repository / "later.txt").write_text("later", encoding="utf-8")
    git(repository, "add", "later.txt")
    git(repository, "commit", "-qm", "later")
    manager = WorktreeManager(repository, WorktreeConfig())

    lease = await manager.acquire(
        task_id="integration-1", role="integration", retention="persistent", base_commit=base
    )
    assert await manager.git.head_commit(cwd=lease.root) == base
    await manager.release(lease)
    recovered = await manager.acquire(
        task_id="integration-1", role="integration", retention="persistent", base_commit=base
    )
    assert recovered.recovered
    await manager.release(recovered)
    with pytest.raises(WorktreeError, match="base_commit"):
        await manager.acquire(
            task_id="integration-1",
            role="integration",
            retention="persistent",
            base_commit=git(repository, "rev-parse", "HEAD").stdout.strip(),
        )


@pytest.mark.asyncio
async def test_delete_merged_internal_worktree(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    manager = WorktreeManager(repository, WorktreeConfig())
    base = await manager.git.head_commit(cwd=repository)
    lease = await manager.acquire(
        task_id="integration-1", role="integration", retention="persistent", base_commit=base
    )
    (lease.root / "new.txt").write_text("new", encoding="utf-8")
    git(lease.root, "add", "new.txt")
    git(lease.root, "commit", "-qm", "integrated")
    integrated = await manager.git.head_commit(cwd=lease.root)
    await manager.git.fast_forward(cwd=repository, target=integrated)

    disposition = await manager.delete_merged(lease, merged_into=integrated)

    assert disposition.status == "cleaned"
    assert not lease.root.exists()


@pytest.mark.asyncio
async def test_manager_acquire_new_preserves_repository_subdirectory(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    subdirectory = repository / "src/pkg"
    subdirectory.mkdir(parents=True)
    (subdirectory / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(repository, "add", "src/pkg/module.py")
    git(repository, "commit", "-qm", "add package")
    manager = WorktreeManager(subdirectory, WorktreeConfig())

    lease = await manager.acquire(task_id="task-1", role="reviewer")

    assert lease.cwd == lease.root / "src/pkg"
    assert (lease.cwd / "module.py").exists()


class ExplodingDependency:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        async def explode(*args, **kwargs):
            _ = args, kwargs
            self.calls.append(name)
            raise AssertionError(f"不应调用 {name}")

        return explode


@pytest.mark.asyncio
async def test_fast_recovery_uses_no_git_or_initializer(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    original_manager = WorktreeManager(repository, WorktreeConfig())
    original = await original_manager.acquire(task_id="task-1", role="reviewer")
    before = (original.root / METADATA_FILENAME).read_bytes()
    fake_git = ExplodingDependency()
    fake_initializer = ExplodingDependency()
    recovering = WorktreeManager(
        repository,
        WorktreeConfig(),
        git=fake_git,  # type: ignore[arg-type]
        initializer=fake_initializer,  # type: ignore[arg-type]
    )

    recovered = await recovering.acquire(task_id="task-1", role="reviewer")

    assert recovered.recovered is True
    assert recovered.root == original.root
    assert fake_git.calls == []
    assert fake_initializer.calls == []
    assert (original.root / METADATA_FILENAME).read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", (("task_id", "other"), ("repository_id", "wrong"), ("branch", "wrong")))
async def test_fast_recovery_rejects_mismatched_metadata_without_writes(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    repository = init_repository(tmp_path / "repo")
    original_manager = WorktreeManager(repository, WorktreeConfig())
    lease = await original_manager.acquire(task_id="task-1", role="reviewer")
    marker = lease.root / METADATA_FILENAME
    raw = json.loads(marker.read_text(encoding="utf-8"))
    raw[field] = value
    marker.write_text(json.dumps(raw), encoding="utf-8")
    before = marker.read_bytes()
    fake_git = ExplodingDependency()
    manager = WorktreeManager(repository, WorktreeConfig(), git=fake_git)  # type: ignore[arg-type]

    with pytest.raises(WorktreeError, match="recovery"):
        await manager.acquire(task_id="task-1", role="reviewer")

    assert fake_git.calls == []
    assert marker.read_bytes() == before


@pytest.mark.asyncio
async def test_fast_recovery_rejects_bad_json_without_writes(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    manager = WorktreeManager(repository, WorktreeConfig())
    lease = await manager.acquire(task_id="task-1", role="reviewer")
    marker = lease.root / METADATA_FILENAME
    marker.write_text("{bad", encoding="utf-8")
    before = marker.read_bytes()
    recovering = WorktreeManager(repository, WorktreeConfig(), git=ExplodingDependency())  # type: ignore[arg-type]

    with pytest.raises(WorktreeError, match="recovery"):
        await recovering.acquire(task_id="task-1", role="reviewer")

    assert marker.read_bytes() == before


@pytest.mark.asyncio
async def test_finish_clean_removes_worktree_and_branch(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    manager = WorktreeManager(repository, WorktreeConfig())
    lease = await manager.acquire(task_id="task-1", role="reviewer")

    disposition = await manager.finish(lease)

    assert disposition.status == "cleaned"
    assert not lease.root.exists()
    assert git(repository, "show-ref", "--verify", "--quiet", f"refs/heads/{lease.metadata.branch}", check=False).returncode == 1
    assert manager.active_task_ids() == frozenset()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ("dirty", "untracked", "commit"))
async def test_finish_retains_protected_changes(tmp_path: Path, change: str) -> None:
    repository = init_repository(tmp_path / "repo")
    manager = WorktreeManager(repository, WorktreeConfig())
    lease = await manager.acquire(task_id="task-1", role="reviewer")
    if change == "dirty":
        (lease.root / "README.md").write_text("dirty", encoding="utf-8")
    else:
        (lease.root / "new.txt").write_text("new", encoding="utf-8")
        if change == "commit":
            git(lease.root, "add", "new.txt")
            git(lease.root, "commit", "-qm", "new")

    disposition = await manager.finish(lease)

    assert disposition.status == "retained"
    assert lease.root.exists()
    assert git(repository, "show-ref", "--verify", "--quiet", f"refs/heads/{lease.metadata.branch}").returncode == 0
    assert manager.active_task_ids() == frozenset()


@pytest.mark.asyncio
async def test_protected_delete_rejects_active_dirty_and_no_upstream(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    manager = WorktreeManager(repository, WorktreeConfig())
    lease = await manager.acquire(task_id="task-1", role="reviewer")

    active = await manager.delete(lease)
    assert active.status == "retained"
    assert "运行" in active.reason

    manager._active.clear()
    (lease.root / "new.txt").write_text("new", encoding="utf-8")
    dirty = await manager.delete(lease)
    assert dirty.status == "retained"
    assert "未跟踪" in dirty.reason
    git(lease.root, "add", "new.txt")
    git(lease.root, "commit", "-qm", "new")
    no_upstream = await manager.delete(lease, allow_pushed_commits=True)
    assert no_upstream.status == "retained"
    assert "未推送" in no_upstream.reason
    assert lease.root.exists()


@pytest.mark.asyncio
async def test_protected_delete_allows_pushed_commits_for_cleanup(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    git(repository, "remote", "add", "origin", str(remote))
    manager = WorktreeManager(repository, WorktreeConfig())
    lease = await manager.acquire(task_id="task-1", role="reviewer")
    (lease.root / "new.txt").write_text("new", encoding="utf-8")
    git(lease.root, "add", "new.txt")
    git(lease.root, "commit", "-qm", "new")
    git(lease.root, "push", "-qu", "origin", lease.metadata.branch)
    manager._active.clear()

    disposition = await manager.delete(lease, allow_pushed_commits=True)

    assert disposition.status == "cleaned"
    assert not lease.root.exists()


@pytest.mark.asyncio
async def test_cleanup_expired_pushed_worktree_and_retains_unsafe(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    creator = WorktreeManager(repository, WorktreeConfig(retention_days=1), clock=lambda: old)
    dirty_lease = await creator.acquire(task_id="dirty-1", role="reviewer")
    (dirty_lease.root / "dirty.txt").write_text("dirty", encoding="utf-8")
    creator._active.clear()

    cleaner = WorktreeManager(
        repository,
        WorktreeConfig(retention_days=1),
        clock=lambda: datetime(2020, 1, 3, tzinfo=timezone.utc),
    )
    report = await cleaner.cleanup_expired()

    assert any(item.path == dirty_lease.root and item.status == "skipped" for item in report.items)
    assert dirty_lease.root.exists()


@pytest.mark.asyncio
async def test_cleanup_expired_removes_clean_worktree(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    creator = WorktreeManager(repository, WorktreeConfig(retention_days=1), clock=lambda: old)
    lease = await creator.acquire(task_id="clean-1", role="reviewer")
    creator._active.clear()
    cleaner = WorktreeManager(
        repository,
        WorktreeConfig(retention_days=1),
        clock=lambda: datetime(2020, 1, 3, tzinfo=timezone.utc),
    )

    report = await cleaner.cleanup_expired()

    assert any(item.path == lease.root and item.status == "cleaned" for item in report.items)
    assert not lease.root.exists()


@pytest.mark.asyncio
async def test_cleanup_expired_pushed_worktree_is_removed(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    git(repository, "remote", "add", "origin", str(remote))
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    creator = WorktreeManager(repository, WorktreeConfig(retention_days=1), clock=lambda: old)
    lease = await creator.acquire(task_id="pushed-1", role="reviewer")
    (lease.root / "result.txt").write_text("pushed", encoding="utf-8")
    git(lease.root, "add", "result.txt")
    git(lease.root, "commit", "-qm", "result")
    git(lease.root, "push", "-qu", "origin", lease.metadata.branch)
    creator._active.clear()
    cleaner = WorktreeManager(
        repository,
        WorktreeConfig(retention_days=1),
        clock=lambda: datetime(2020, 1, 3, tzinfo=timezone.utc),
    )

    report = await cleaner.cleanup_expired()

    assert any(item.path == lease.root and item.status == "cleaned" for item in report.items)
    assert not lease.root.exists()


@pytest.mark.asyncio
async def test_cleanup_skips_unexpired_and_active_worktrees(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    now = datetime(2020, 1, 1, tzinfo=timezone.utc)
    manager = WorktreeManager(repository, WorktreeConfig(retention_days=7), clock=lambda: now)
    lease = await manager.acquire(task_id="active-1", role="reviewer")

    report = await manager.cleanup_expired()

    assert any(item.path == lease.root and item.status == "skipped" for item in report.items)
    assert lease.root.exists()


@pytest.mark.asyncio
async def test_cleanup_failure_for_bad_metadata_does_not_stop_other_candidates(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    creator = WorktreeManager(repository, WorktreeConfig(retention_days=1), clock=lambda: old)
    good = await creator.acquire(task_id="good-1", role="reviewer")
    creator._active.clear()
    bad = repository / ".julycode/worktrees/reviewer/bad-1"
    bad.mkdir(parents=True)
    (bad / METADATA_FILENAME).write_text("{bad", encoding="utf-8")
    cleaner = WorktreeManager(
        repository,
        WorktreeConfig(retention_days=1),
        clock=lambda: datetime(2020, 1, 3, tzinfo=timezone.utc),
    )

    report = await cleaner.cleanup_expired()

    assert any(item.path == bad and item.status == "failed" for item in report.items)
    assert any(item.path == good.root and item.status == "cleaned" for item in report.items)
    assert bad.exists()


@pytest.mark.asyncio
async def test_cleanup_failure_for_wrong_repository_metadata_is_conservative(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    creator = WorktreeManager(repository, WorktreeConfig(retention_days=1), clock=lambda: old)
    lease = await creator.acquire(task_id="wrong-repo-1", role="reviewer")
    creator._active.clear()
    marker = lease.root / METADATA_FILENAME
    raw = json.loads(marker.read_text(encoding="utf-8"))
    raw["repository_id"] = "wrong-repository"
    marker.write_text(json.dumps(raw), encoding="utf-8")
    cleaner = WorktreeManager(
        repository,
        WorktreeConfig(retention_days=1),
        clock=lambda: datetime(2020, 1, 3, tzinfo=timezone.utc),
    )

    report = await cleaner.cleanup_expired()

    assert any(item.path == lease.root and item.status == "failed" for item in report.items)
    assert lease.root.exists()


class FailingStatusGitClient(GitClient):
    async def change_state(self, *, worktree_root: Path, base: str):
        _ = worktree_root, base
        raise WorktreeError("status", "injected status failure")


@pytest.mark.asyncio
async def test_cleanup_failure_for_unknown_git_status_keeps_candidate(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    creator = WorktreeManager(repository, WorktreeConfig(retention_days=1), clock=lambda: old)
    lease = await creator.acquire(task_id="status-fail-1", role="reviewer")
    creator._active.clear()
    cleaner = WorktreeManager(
        repository,
        WorktreeConfig(retention_days=1),
        git=FailingStatusGitClient(),
        clock=lambda: datetime(2020, 1, 3, tzinfo=timezone.utc),
    )

    report = await cleaner.cleanup_expired()

    assert any(item.path == lease.root and item.status == "failed" for item in report.items)
    assert lease.root.exists()


class FakeCleanupManager:
    def __init__(self, tmp_path: Path, *, fail_first: bool = False) -> None:
        self.main_cwd = tmp_path
        self.config = WorktreeConfig(cleanup_interval_seconds=0.01)
        self.calls = 0
        self.fail_first = fail_first

    async def cleanup_expired(self):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("injected janitor failure")
        return type("Report", (), {"items": ()})()


@pytest.mark.asyncio
async def test_janitor_runs_immediately_periodically_and_closes(tmp_path: Path) -> None:
    manager = FakeCleanupManager(tmp_path)
    janitor = WorktreeJanitor(manager)  # type: ignore[arg-type]

    janitor.start()
    janitor.start()
    await asyncio.sleep(0.035)
    await asyncio.wait_for(janitor.close(), timeout=0.2)

    assert manager.calls >= 2


@pytest.mark.asyncio
async def test_janitor_failure_is_reported_and_next_cycle_runs(tmp_path: Path) -> None:
    manager = FakeCleanupManager(tmp_path, fail_first=True)
    reports = []
    janitor = WorktreeJanitor(manager, report=reports.append)  # type: ignore[arg-type]

    janitor.start()
    await asyncio.sleep(0.03)
    await janitor.close()

    assert manager.calls >= 2
    assert reports
    assert reports[0].failures
    assert "injected janitor failure" in reports[0].failures[0].reason


@pytest.mark.asyncio
async def test_concurrent_same_target_allows_only_one_active_acquire(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    manager = WorktreeManager(repository, WorktreeConfig())

    results = await asyncio.gather(
        manager.acquire(task_id="same-1", role="reviewer"),
        manager.acquire(task_id="same-1", role="reviewer"),
        return_exceptions=True,
    )

    leases = [result for result in results if isinstance(result, WorktreeLease)]
    errors = [result for result in results if isinstance(result, WorktreeError)]
    assert len(leases) == 1
    assert len(errors) == 1
    assert manager.active_task_ids() == frozenset({"same-1"})
    assert (leases[0].root / METADATA_FILENAME).exists()


class SlowStateGitClient(GitClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def change_state(self, *, worktree_root: Path, base: str):
        self.started.set()
        await self.release.wait()
        return await super().change_state(worktree_root=worktree_root, base=base)


@pytest.mark.asyncio
async def test_finish_cleanup_race_keeps_active_until_finish_completes(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    git_client = SlowStateGitClient()
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    manager = WorktreeManager(
        repository,
        WorktreeConfig(retention_days=1),
        git=git_client,
        clock=lambda: datetime(2020, 1, 3, tzinfo=timezone.utc),
    )
    lease = await manager.acquire(task_id="race-1", role="reviewer")
    marker = lease.root / METADATA_FILENAME
    raw = json.loads(marker.read_text(encoding="utf-8"))
    raw["created_at"] = old.isoformat()
    marker.write_text(json.dumps(raw), encoding="utf-8")
    lease = WorktreeLease(WorktreeMetadata(**raw), lease.root, lease.cwd, lease.recovered)

    finish_task = asyncio.create_task(manager.finish(lease))
    await git_client.started.wait()
    cleanup_report = await manager.cleanup_expired()
    git_client.release.set()
    disposition = await finish_task

    assert any(item.path == lease.root and item.reason == "任务仍在运行" for item in cleanup_report.items)
    assert disposition.status == "cleaned"
    assert manager.active_task_ids() == frozenset()
    assert not lease.root.exists()


@pytest.mark.asyncio
async def test_persistent_worktree_survives_release_and_recovers(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    first = WorktreeManager(repository, WorktreeConfig())
    lease = await first.acquire(task_id="member-one", role="teams", retention="persistent")

    await first.release(lease)

    assert lease.root.exists()
    assert git(
        repository,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{lease.metadata.branch}",
    ).returncode == 0
    recovered = await WorktreeManager(repository, WorktreeConfig()).acquire(
        task_id="member-one", role="teams", retention="persistent"
    )
    assert recovered.recovered
    assert recovered.root == lease.root
    assert recovered.metadata.retention == "persistent"


@pytest.mark.asyncio
async def test_cleanup_skips_persistent_worktree(tmp_path: Path) -> None:
    repository = init_repository(tmp_path / "repo")
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    creator = WorktreeManager(repository, WorktreeConfig(retention_days=1), clock=lambda: old)
    lease = await creator.acquire(task_id="member-old", role="teams", retention="persistent")
    await creator.release(lease)

    cleaner = WorktreeManager(
        repository,
        WorktreeConfig(retention_days=1),
        clock=lambda: datetime(2020, 1, 3, tzinfo=timezone.utc),
    )
    report = await cleaner.cleanup_expired()

    assert lease.root.exists()
    assert any(
        item.path == lease.root and item.status == "skipped" and "长期 Worktree" in item.reason
        for item in report.items
    )
