from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from mewcode.teams.locking import AtomicJsonFile, FileLock, LockToken
from mewcode.teams.models import (
    TeamConfig,
    TeamDataError,
    TeamMemberRecord,
    TeamTask,
    member_from_dict,
    task_from_dict,
)
from mewcode.teams.paths import TeamPaths, validate_member_name, validate_team_name
from mewcode.teams.store import TeamStore
from tests.test_worktrees import init_repository


def config(**changes) -> TeamConfig:
    values = asdict(TeamConfig())
    values.update(changes)
    return TeamConfig(**values)


def test_team_models_round_trip() -> None:
    member = TeamMemberRecord(
        name="worker", role="reviewer", backend="coroutine", require_approval=True, status="idle",
        worktree_root="/repo/w", worktree_cwd="/repo/w", branch="mewcode/teams/demo-worker",
        worktree_owner_id="demo-worker", session_path="/home/user/session.jsonl",
        current_task_id=None, pending_approval_id=None,
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
        last_active_at="2026-01-01T00:00:00+00:00",
    )
    task = TeamTask(
        id="task-1", title="实现功能", description="说明", kind="code", status="pending",
        dependencies=(), assignee=None, created_by="lead",
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
    )
    assert member_from_dict(asdict(member)) == member
    assert task_from_dict({**asdict(task), "dependencies": []}) == task
    with pytest.raises(TeamDataError, match="未知成员状态"):
        member_from_dict({**asdict(member), "status": "unknown"})


@pytest.mark.parametrize("value", ("team", "Team_1", "a-b", "9"))
def test_safe_name_accepts(value: str) -> None:
    assert validate_team_name(value) == value


@pytest.mark.parametrize("value", ("", "../x", "a/b", "a\\b", ".", "中文", "a" * 65))
def test_safe_name_rejects(value: str) -> None:
    with pytest.raises(TeamDataError):
        validate_team_name(value)


def test_team_paths_stay_inside_root(tmp_path: Path) -> None:
    paths = TeamPaths.for_team("demo", base=tmp_path)
    paths.ensure_directories()
    assert paths.root == tmp_path / "demo"
    assert paths.mailbox_file("lead").parent == paths.root / "mailboxes"
    assert paths.session_file("worker").parent == paths.root / "sessions"
    with pytest.raises(TeamDataError, match="保留"):
        validate_member_name("lead")


def test_team_paths_reject_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "teams"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "demo").symlink_to(outside, target_is_directory=True)

    with pytest.raises(TeamDataError, match="符号链接"):
        TeamPaths.for_team("demo", base=root)


def test_team_paths_reject_data_file_symlink_escape(tmp_path: Path) -> None:
    paths = TeamPaths.for_team("demo", base=tmp_path / "teams")
    paths.ensure_directories()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (paths.root / "tasks.json").symlink_to(outside)

    with pytest.raises(TeamDataError, match="符号链接"):
        _ = paths.tasks_file


@pytest.mark.asyncio
async def test_file_lock_retry_then_succeeds(tmp_path: Path) -> None:
    path = tmp_path / "data.lock"
    settings = config(lock_timeout_seconds=0.5, lock_retry_interval_seconds=0.01, stale_lock_seconds=1)
    first = FileLock(path, settings)
    first_token = await first.acquire()

    async def delayed_release() -> None:
        await asyncio.sleep(0.04)
        await first.release(first_token)

    release_task = asyncio.create_task(delayed_release())
    second = FileLock(path, settings)
    second_token = await second.acquire()
    await release_task
    await second.release(second_token)
    assert not path.exists()


@pytest.mark.asyncio
async def test_file_lock_timeout_and_wrong_token(tmp_path: Path) -> None:
    path = tmp_path / "data.lock"
    settings = config(lock_timeout_seconds=0.03, lock_retry_interval_seconds=0.005, stale_lock_seconds=1)
    lock = FileLock(path, settings)
    token = await lock.acquire()
    await lock.release(LockToken("wrong"))
    assert path.exists()
    with pytest.raises(TeamDataError, match="超时"):
        await FileLock(path, settings).acquire()
    await lock.release(token)


@pytest.mark.asyncio
async def test_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    path = tmp_path / "data.lock"
    path.write_text(
        json.dumps({"token": "dead", "pid": 99999999, "host": os.uname().nodename, "created_at": time.time() - 60}),
        encoding="utf-8",
    )
    settings = config(lock_timeout_seconds=0.1, lock_retry_interval_seconds=0.005, stale_lock_seconds=0.01)
    lock = FileLock(path, settings)
    token = await lock.acquire()
    await lock.release(token)
    assert not path.exists()


@pytest.mark.asyncio
async def test_stale_lock_owned_by_live_process_is_not_reclaimed(tmp_path: Path) -> None:
    path = tmp_path / "data.lock"
    path.write_text(
        json.dumps({"token": "live", "pid": os.getpid(), "host": os.uname().nodename, "created_at": time.time() - 60}),
        encoding="utf-8",
    )
    settings = config(lock_timeout_seconds=0.03, lock_retry_interval_seconds=0.005, stale_lock_seconds=0.01)

    with pytest.raises(TeamDataError, match="超时"):
        await FileLock(path, settings).acquire()
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "live"


@pytest.mark.asyncio
async def test_atomic_json_mutate_and_failure_keep_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = AtomicJsonFile(path, FileLock(tmp_path / "state.lock"))
    await store.replace({"revision": 1, "value": "old"})
    updated = await store.mutate(lambda raw: {**raw, "revision": raw["revision"] + 1, "value": "new"})
    assert updated["revision"] == 2

    def fail(_raw):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await store.mutate(fail)
    assert await store.read() == updated


@pytest.mark.asyncio
async def test_atomic_json_concurrent_mutations_do_not_lose_revisions(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = AtomicJsonFile(path, FileLock(tmp_path / "state.lock"))
    await store.replace({"revision": 0})

    await asyncio.gather(*(
        store.mutate(lambda raw: {"revision": int(raw["revision"]) + 1})
        for _ in range(20)
    ))

    assert await store.read() == {"revision": 20}


@pytest.mark.asyncio
async def test_create_list_load_team_and_repository_binding(tmp_path: Path) -> None:
    first_repo = init_repository(tmp_path / "repo-one")
    second_repo = init_repository(tmp_path / "repo-two")
    root = tmp_path / "teams"
    store = TeamStore(first_repo, root=root)
    created = await store.create("demo")
    assert created.name == "demo"
    assert (root / "demo/team.json").exists()
    assert [item.name for item in await store.list()] == ["demo"]
    assert (await store.load("demo")).repository_root == str(first_repo)
    with pytest.raises(TeamDataError, match="其他项目"):
        await TeamStore(second_repo, root=root).load("demo")


@pytest.mark.asyncio
async def test_member_roster_rejects_duplicates(tmp_path: Path) -> None:
    repo = init_repository(tmp_path / "repo")
    store = TeamStore(repo, root=tmp_path / "teams")
    await store.create("demo")
    now = "2026-01-01T00:00:00+00:00"
    member = TeamMemberRecord(
        "worker", "reviewer", "coroutine", False, "idle", str(repo), str(repo), "branch",
        "owner", str(store.root / "demo/sessions/worker.jsonl"), None, None, now, now, now,
    )
    await store.add_member("demo", member)
    assert (await store.get_member("demo", "worker")) == member
    with pytest.raises(TeamDataError, match="已存在"):
        await store.add_member("demo", member)

    escaped = __import__("dataclasses").replace(member, name="escaped", session_path=str(tmp_path / "outside.jsonl"))
    with pytest.raises(TeamDataError, match="上下文路径"):
        await store.add_member("demo", escaped)


@pytest.mark.asyncio
async def test_reconcile_interrupted_marks_dead_running_member_failed(tmp_path: Path) -> None:
    repo = init_repository(tmp_path / "repo")
    store = TeamStore(repo, root=tmp_path / "teams")
    await store.create("demo")
    now = "2026-01-01T00:00:00+00:00"
    member = TeamMemberRecord(
        "worker", "reviewer", "coroutine", False, "running", str(repo), str(repo), "branch",
        "owner", str(store.root / "demo/sessions/worker.jsonl"), "task-1", None, now, now, now,
    )
    await store.add_member("demo", member)

    report = await store.reconcile_interrupted("demo")

    assert report.interrupted_members == ("worker",)
    assert report.released_task_ids == ("task-1",)
    recovered = await store.get_member("demo", "worker")
    assert recovered.status == "failed"
    assert recovered.last_error and "中断" in recovered.last_error
