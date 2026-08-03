from __future__ import annotations

from pathlib import Path

import pytest

from julycode.teams.approvals import ApprovalService
from julycode.teams.models import TaskDraft, TeamActor, TeamDataError, TeamMemberRecord
from julycode.teams.store import TeamStore
from julycode.teams.tasks import TaskService
from tests.test_worktrees import init_repository


async def setup_approval(tmp_path: Path):
    repo = init_repository(tmp_path / "repo")
    store = TeamStore(repo, root=tmp_path / "teams")
    await store.create("demo")
    now = "2026-01-01T00:00:00+00:00"
    member_record = TeamMemberRecord(
        "worker", "reviewer", "coroutine", True, "idle", str(repo), str(repo), "branch", "owner",
        str(store.root / "demo/sessions/worker.jsonl"), None, None, now, now, now,
    )
    await store.add_member("demo", member_record)
    tasks = TaskService("demo", store)
    lead = TeamActor("demo", "lead", "lead", repo)
    member = TeamActor("demo", "worker", "member", repo)
    task = await tasks.create(lead, TaskDraft("review", "", kind="research"))
    await tasks.claim(member, task.id)
    return store, tasks, ApprovalService("demo", store, tasks), lead, member, task


@pytest.mark.asyncio
async def test_approval_request_approve_and_gate(tmp_path: Path) -> None:
    _store, tasks, approvals, lead, member, task = await setup_approval(tmp_path)
    record = await approvals.request(member, task.id, "先读取再修改")
    assert record.plan_version == 1
    assert not await approvals.can_mutate_project(member)
    decided = await approvals.approve(lead, record.id, task.id, 1)
    assert decided.status == "approved"
    assert await approvals.can_mutate_project(member)
    assert (await tasks.get(task.id)).status == "in_progress"


@pytest.mark.asyncio
async def test_stale_duplicate_and_reject_version(tmp_path: Path) -> None:
    _store, _tasks, approvals, lead, member, task = await setup_approval(tmp_path)
    first = await approvals.request(member, task.id, "v1")
    await approvals.reject(lead, first.id, task.id, 1, "补充测试")
    second = await approvals.request(member, task.id, "v2")
    assert second.plan_version == 2
    with pytest.raises(TeamDataError, match="版本"):
        await approvals.approve(lead, second.id, task.id, 1)
    await approvals.approve(lead, second.id, task.id, 2)
    with pytest.raises(TeamDataError, match="已经结束"):
        await approvals.approve(lead, second.id, task.id, 2)


@pytest.mark.asyncio
async def test_approval_projection_recovery_repairs_pending_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, tasks, approvals, _lead, member, task = await setup_approval(tmp_path)
    original = tasks.set_approval

    async def fail_projection(*args, **kwargs):
        raise RuntimeError("injected projection failure")

    monkeypatch.setattr(tasks, "set_approval", fail_projection)
    with pytest.raises(RuntimeError, match="projection"):
        await approvals.request(member, task.id, "先分析再修改")
    recorded = await approvals.current_for_member(member)
    assert recorded is not None and recorded.status == "pending"
    assert (await tasks.get(task.id)).approval_id is None

    monkeypatch.setattr(tasks, "set_approval", original)
    repaired = await approvals.reconcile()

    assert repaired == (task.id,)
    assert (await tasks.get(task.id)).approval_id == recorded.id
    roster = await store.get_member("demo", "worker")
    assert roster.pending_approval_id == recorded.id


@pytest.mark.asyncio
async def test_approval_projection_recovery_repairs_approved_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, tasks, approvals, lead, member, task = await setup_approval(tmp_path)
    request = await approvals.request(member, task.id, "执行计划")
    original = tasks.set_approval

    async def fail_projection(*args, **kwargs):
        raise RuntimeError("injected projection failure")

    monkeypatch.setattr(tasks, "set_approval", fail_projection)
    with pytest.raises(RuntimeError, match="projection"):
        await approvals.approve(lead, request.id, task.id, request.plan_version)
    assert (await approvals.current_for_member(member)).status == "approved"  # type: ignore[union-attr]
    assert (await tasks.get(task.id)).status == "awaiting_approval"

    monkeypatch.setattr(tasks, "set_approval", original)
    repaired = await approvals.reconcile()

    assert repaired == (task.id,)
    assert (await tasks.get(task.id)).status == "in_progress"
    roster = await store.get_member("demo", "worker")
    assert roster.status == "running" and roster.pending_approval_id is None
