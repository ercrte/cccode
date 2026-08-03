from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from julycode.teams.models import TaskDraft, TaskPatch, TaskResult, TeamActor, TeamDataError, TeamMemberRecord
from julycode.teams.store import TeamStore
from julycode.teams.tasks import TaskService
from tests.test_worktrees import git, init_repository


async def setup_team(tmp_path: Path, *, approval: bool = False):
    repo = init_repository(tmp_path / "repo")
    store = TeamStore(repo, root=tmp_path / "teams")
    await store.create("demo")
    now = "2026-01-01T00:00:00+00:00"
    for name in ("one", "two"):
        member = TeamMemberRecord(
            name, "reviewer", "coroutine", approval, "idle", str(repo), str(repo), "branch",
            f"owner-{name}", str(store.root / f"demo/sessions/{name}.jsonl"), None, None, now, now, now,
        )
        await store.add_member("demo", member)
    tasks = TaskService("demo", store)
    lead = TeamActor("demo", "lead", "lead", repo)
    return repo, store, tasks, lead


@pytest.mark.asyncio
async def test_task_crud_and_dependency_states(tmp_path: Path) -> None:
    repo, store, tasks, lead = await setup_team(tmp_path)
    first = await tasks.create(lead, TaskDraft("first", "", kind="research"))
    second = await tasks.create(lead, TaskDraft("second", "", kind="research", dependencies=(first.id,)))
    assert (await tasks.get(second.id)).status == "blocked"
    updated = await tasks.update(lead, first.id, TaskPatch(title="renamed"))
    assert updated.title == "renamed"
    with pytest.raises(TeamDataError, match="被依赖"):
        await tasks.delete(lead, first.id)


@pytest.mark.asyncio
async def test_dependency_validation_rejects_missing_and_cycle(tmp_path: Path) -> None:
    _repo, _store, tasks, lead = await setup_team(tmp_path)
    with pytest.raises(TeamDataError, match="不存在"):
        await tasks.create(lead, TaskDraft("bad", "", dependencies=("missing",)))
    first = await tasks.create(lead, TaskDraft("first", "", kind="research"))
    second = await tasks.create(lead, TaskDraft("second", "", kind="research", dependencies=(first.id,)))
    with pytest.raises(TeamDataError, match="循环"):
        await tasks.update(lead, first.id, TaskPatch(dependencies=(second.id,)))


@pytest.mark.asyncio
async def test_concurrent_claim_has_single_winner(tmp_path: Path) -> None:
    repo, _store, tasks, lead = await setup_team(tmp_path)
    task = await tasks.create(lead, TaskDraft("work", "", kind="research"))
    actors = (TeamActor("demo", "one", "member", repo), TeamActor("demo", "two", "member", repo))
    results = await asyncio.gather(*(tasks.claim(actor, task.id) for actor in actors), return_exceptions=True)
    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert (await tasks.get(task.id)).assignee in {"one", "two"}


@pytest.mark.asyncio
async def test_research_completion_creates_outbox(tmp_path: Path) -> None:
    repo, _store, tasks, lead = await setup_team(tmp_path)
    task = await tasks.create(lead, TaskDraft("research", "", kind="research"))
    member = TeamActor("demo", "one", "member", repo)
    await tasks.claim(member, task.id)
    completed = await tasks.complete(member, task.id, TaskResult("result"))
    assert completed.status == "completed"
    assert (await tasks.pending_events("demo"))[0].protocol == "task_completed"


@pytest.mark.asyncio
async def test_update_cannot_bypass_completion_validation(tmp_path: Path) -> None:
    _repo, _store, tasks, lead = await setup_team(tmp_path)
    task = await tasks.create(lead, TaskDraft("code", ""))

    with pytest.raises(TeamDataError, match="必须使用 complete"):
        await tasks.update(lead, task.id, TaskPatch(status="completed", result="绕过"))


@pytest.mark.asyncio
async def test_code_completion_requires_clean_reachable_new_head(tmp_path: Path) -> None:
    repo, _store, tasks, lead = await setup_team(tmp_path)
    member = TeamActor("demo", "one", "member", repo)
    task = await tasks.create(lead, TaskDraft("code", "修改代码"))
    claimed = await tasks.claim(member, task.id)
    assert claimed.start_commit == git(repo, "rev-parse", "HEAD").stdout.strip()

    (repo / "code.py").write_text("print('ok')\n", encoding="utf-8")
    with pytest.raises(TeamDataError, match="未提交或未跟踪"):
        await tasks.complete(member, task.id, TaskResult("已实现", claimed.start_commit))

    git(repo, "add", "code.py")
    git(repo, "commit", "-qm", "implement code task")
    commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(TeamDataError, match="当前新提交"):
        await tasks.complete(member, task.id, TaskResult("已实现", claimed.start_commit))

    completed = await tasks.complete(member, task.id, TaskResult("已实现", commit))
    assert completed.status == "completed"
    assert completed.commit == commit


@pytest.mark.asyncio
async def test_code_completion_records_head_when_commit_output_is_redacted(tmp_path: Path) -> None:
    repo, _store, tasks, lead = await setup_team(tmp_path)
    member = TeamActor("demo", "one", "member", repo)
    task = await tasks.create(lead, TaskDraft("code", "修改代码"))
    await tasks.claim(member, task.id)
    (repo / "auto.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "auto.py")
    git(repo, "commit", "-qm", "auto head")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()

    completed = await tasks.complete(member, task.id, TaskResult("已自动记录 HEAD"))

    assert completed.commit == head


@pytest.mark.asyncio
async def test_blocked_task_unlocks_after_dependency_completion(tmp_path: Path) -> None:
    repo, _store, tasks, lead = await setup_team(tmp_path)
    first = await tasks.create(lead, TaskDraft("first", "", kind="research"))
    second = await tasks.create(lead, TaskDraft("second", "", kind="research", dependencies=(first.id,)))
    member = TeamActor("demo", "one", "member", repo)

    with pytest.raises(TeamDataError, match="不可领取"):
        await tasks.claim(member, second.id)
    await tasks.claim(member, first.id)
    await tasks.complete(member, first.id, TaskResult("done"))

    assert (await tasks.get(second.id)).status == "pending"
