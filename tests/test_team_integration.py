from __future__ import annotations

import asyncio
import subprocess
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from julycode.teams.integration import TeamIntegrationService, TeamIntegrationStore
from julycode.teams.models import (
    IntegrationRoundRecord,
    IntegrationIntent,
    TaskAttemptRef,
    TaskDraft,
    TaskPatch,
    TaskResult,
    TeamActor,
    TeamDataError,
    TeamIntegrationState,
    TeamMemberRecord,
    integration_state_from_dict,
)
from julycode.teams.store import TeamStore
from julycode.teams.tasks import TaskService
from julycode.worktrees import WorktreeConfig, WorktreeLease, WorktreeManager


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
    (path / "shared.txt").write_text("base\n", encoding="utf-8")
    git(path, "add", "shared.txt")
    git(path, "commit", "-qm", "initial")
    return path


class TeamEnvironment:
    def __init__(
        self,
        repository: Path,
        store: TeamStore,
        manager: WorktreeManager,
        integration: TeamIntegrationService,
        tasks: TaskService,
    ) -> None:
        self.repository = repository
        self.store = store
        self.manager = manager
        self.integration = integration
        self.tasks = tasks
        self.members: dict[str, tuple[TeamActor, WorktreeLease]] = {}

    async def add_member(self, name: str) -> TeamActor:
        lease = await self.manager.acquire(
            task_id=f"member-{name}", role="teams", retention="persistent"
        )
        now = datetime.now(timezone.utc).isoformat()
        paths = self.store.root / "demo" / "sessions" / f"{name}.jsonl"
        paths.parent.mkdir(parents=True, exist_ok=True)
        paths.touch()
        member = TeamMemberRecord(
            name=name,
            role="reviewer",
            backend="coroutine",
            require_approval=False,
            status="idle",
            worktree_root=str(lease.root),
            worktree_cwd=str(lease.cwd),
            branch=lease.metadata.branch,
            worktree_owner_id=lease.metadata.task_id,
            session_path=str(paths),
            current_task_id=None,
            pending_approval_id=None,
            created_at=now,
            updated_at=now,
            last_active_at=now,
        )
        await self.store.add_member("demo", member)
        actor = TeamActor("demo", name, "member", lease.cwd)
        self.members[name] = (actor, lease)
        return actor


async def make_environment(tmp_path: Path) -> TeamEnvironment:
    repository = init_repository(tmp_path / "repo")
    store = TeamStore(repository, root=tmp_path / "teams")
    await store.create("demo")
    manager = WorktreeManager(repository, WorktreeConfig())
    integration = TeamIntegrationService("demo", repository, store, manager)
    tasks = TaskService("demo", store, integration=integration)
    return TeamEnvironment(repository, store, manager, integration, tasks)


def commit_file(actor: TeamActor, relative: str, content: str, message: str) -> str:
    path = actor.cwd / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(actor.cwd, "add", relative)
    git(actor.cwd, "commit", "-qm", message)
    return git(actor.cwd, "rev-parse", "HEAD").stdout.strip()


def test_integration_model_round_trip_and_rejects_invalid() -> None:
    now = datetime.now(timezone.utc).isoformat()
    round_record = IntegrationRoundRecord(
        1, "active", None, None, None, None, None, None, (), None, None, now, now
    )
    state = TeamIntegrationState(1, 3, 2, round_record, ())
    assert integration_state_from_dict(asdict(state)) == state
    raw = asdict(state)
    raw["next_round"] = 1
    with pytest.raises(TeamDataError, match="next_round"):
        integration_state_from_dict(raw)


@pytest.mark.asyncio
async def test_integration_store_atomic_and_transaction_serializes(tmp_path: Path) -> None:
    env = await make_environment(tmp_path)
    store = TeamIntegrationStore("demo", env.store)

    async def assign() -> int:
        async with store.transaction() as transaction:
            before = transaction.state.revision
            await asyncio.sleep(0.01)
            transaction.replace(transaction.state)
            return before

    revisions = await asyncio.gather(assign(), assign())

    assert revisions[0] != revisions[1]
    assert (await store.load_or_create()).revision == 3


@pytest.mark.asyncio
async def test_assign_round_and_integration_service_snapshot(tmp_path: Path) -> None:
    env = await make_environment(tmp_path)
    first = await env.integration.assign_round()
    second = await env.integration.assign_round()
    summary = await env.integration.snapshot()
    assert first == second == 1
    assert summary.round_number == 1
    assert summary.phase == "active"
    assert env.integration._owner_id(1) == env.integration._owner_id(1)


@pytest.mark.asyncio
async def test_two_members_dependency_publish_once(tmp_path: Path) -> None:
    env = await make_environment(tmp_path)
    first_member = await env.add_member("first")
    second_member = await env.add_member("second")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    lead_before = git(env.repository, "rev-parse", "HEAD").stdout.strip()
    first = await env.tasks.create(lead, TaskDraft("上游", "新增上游文件"))
    second = await env.tasks.create(
        lead, TaskDraft("下游", "读取上游文件", dependencies=(first.id,))
    )
    first = await env.tasks.claim(first_member, first.id)
    first_commit = commit_file(first_member, "upstream.txt", "upstream\n", "upstream")
    first = await env.tasks.complete(first_member, first.id, TaskResult("上游完成", first_commit))
    assert first.status == "completed"
    assert git(env.repository, "rev-parse", "HEAD").stdout.strip() == lead_before

    second = await env.tasks.claim(second_member, second.id)
    assert (second_member.cwd / "upstream.txt").read_text(encoding="utf-8") == "upstream\n"
    second_commit = commit_file(
        second_member, "downstream.txt", "uses upstream\n", "downstream"
    )
    second = await env.tasks.complete(
        second_member, second.id, TaskResult("下游完成", second_commit)
    )
    assert second.status == "completed"
    assert git(env.repository, "rev-parse", "HEAD").stdout.strip() == lead_before

    result = await env.integration.finalize(await env.tasks.list())

    assert result.status == "published"
    published = git(env.repository, "rev-parse", "HEAD").stdout.strip()
    assert published == result.summary.integration_head
    assert published != lead_before
    assert (env.repository / "upstream.txt").exists()
    assert (env.repository / "downstream.txt").exists()
    assert len(result.summary.accepted_tasks) == 2


@pytest.mark.asyncio
async def test_conflict_blocks_without_lead_change(tmp_path: Path) -> None:
    env = await make_environment(tmp_path)
    first_member = await env.add_member("first")
    second_member = await env.add_member("second")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    first = await env.tasks.create(lead, TaskDraft("第一处修改", "修改同一行"))
    second = await env.tasks.create(lead, TaskDraft("第二处修改", "修改同一行"))
    first = await env.tasks.claim(first_member, first.id)
    second = await env.tasks.claim(second_member, second.id)
    lead_before = git(env.repository, "rev-parse", "HEAD").stdout.strip()
    first_commit = commit_file(first_member, "shared.txt", "first\n", "first")
    second_commit = commit_file(second_member, "shared.txt", "second\n", "second")
    await env.tasks.complete(first_member, first.id, TaskResult("first", first_commit))

    with pytest.raises(TeamDataError, match="shared.txt"):
        await env.tasks.complete(second_member, second.id, TaskResult("second", second_commit))

    assert (await env.tasks.get(second.id)).status == "in_progress"
    summary = await env.integration.snapshot()
    assert summary.phase == "blocked"
    assert summary.failure is not None
    assert summary.failure.conflict_paths == ("shared.txt",)
    assert git(env.repository, "rev-parse", "HEAD").stdout.strip() == lead_before
    assert git(second_member.cwd, "rev-parse", "HEAD").stdout.strip() == second_commit


@pytest.mark.asyncio
async def test_publish_preflight_rejects_dirty_lead_and_retains_results(tmp_path: Path) -> None:
    env = await make_environment(tmp_path)
    member = await env.add_member("first")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    task = await env.tasks.create(lead, TaskDraft("代码", "增加文件"))
    task = await env.tasks.claim(member, task.id)
    commit = commit_file(member, "new.txt", "new\n", "new")
    await env.tasks.complete(member, task.id, TaskResult("done", commit))
    lead_before = git(env.repository, "rev-parse", "HEAD").stdout.strip()
    (env.repository / "dirty.txt").write_text("dirty", encoding="utf-8")

    result = await env.integration.finalize(await env.tasks.list())

    assert result.status == "blocked"
    assert "不干净" in result.message
    assert git(env.repository, "rev-parse", "HEAD").stdout.strip() == lead_before
    assert (await env.integration.snapshot()).integration_head is not None


@pytest.mark.asyncio
async def test_research_round_not_needed(tmp_path: Path) -> None:
    env = await make_environment(tmp_path)
    member = await env.add_member("first")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    before = git(env.repository, "rev-parse", "HEAD").stdout.strip()
    task = await env.tasks.create(lead, TaskDraft("调研", "只调研", kind="research"))
    task = await env.tasks.claim(member, task.id)
    await env.tasks.complete(member, task.id, TaskResult("调研完成"))

    result = await env.integration.finalize(await env.tasks.list())

    assert result.status == "not_needed"
    assert git(env.repository, "rev-parse", "HEAD").stdout.strip() == before
    assert list((env.repository / ".julycode/worktrees/integration").glob("*")) == []


@pytest.mark.asyncio
async def test_legacy_completed_tasks_import_without_publishing(tmp_path: Path) -> None:
    env = await make_environment(tmp_path)
    member = await env.add_member("first")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    legacy_tasks = TaskService("demo", env.store)
    task = await legacy_tasks.create(lead, TaskDraft("旧任务", "旧版本完成的代码任务"))
    task = await legacy_tasks.claim(member, task.id)
    commit = commit_file(member, "legacy.txt", "legacy\n", "legacy")
    await legacy_tasks.complete(member, task.id, TaskResult("legacy done", commit))
    lead_before = git(env.repository, "rev-parse", "HEAD").stdout.strip()

    summary = await env.integration.recover(legacy_tasks)

    assert summary.phase == "active"
    assert summary.accepted_tasks[0].task_id == task.id
    assert git(env.repository, "rev-parse", "HEAD").stdout.strip() == lead_before
    assert not (env.repository / "legacy.txt").exists()


@pytest.mark.asyncio
async def test_concurrent_completions_serialize_without_lost_results(tmp_path: Path) -> None:
    env = await make_environment(tmp_path)
    first_member = await env.add_member("first")
    second_member = await env.add_member("second")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    first = await env.tasks.create(lead, TaskDraft("first", "first"))
    second = await env.tasks.create(lead, TaskDraft("second", "second"))
    first = await env.tasks.claim(first_member, first.id)
    second = await env.tasks.claim(second_member, second.id)
    first_commit = commit_file(first_member, "first.txt", "first\n", "first")
    second_commit = commit_file(second_member, "second.txt", "second\n", "second")
    lead_before = git(env.repository, "rev-parse", "HEAD").stdout.strip()

    await asyncio.gather(
        env.tasks.complete(first_member, first.id, TaskResult("first", first_commit)),
        env.tasks.complete(second_member, second.id, TaskResult("second", second_commit)),
    )

    summary = await env.integration.snapshot()
    assert len(summary.accepted_tasks) == 2
    assert len(set(summary.accepted_tasks)) == 2
    assert git(env.repository, "rev-parse", "HEAD").stdout.strip() == lead_before
    result = await env.integration.finalize(await env.tasks.list())
    assert result.status == "published"
    assert (env.repository / "first.txt").exists()
    assert (env.repository / "second.txt").exists()


@pytest.mark.asyncio
async def test_publish_race_never_overwrites_external_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = await make_environment(tmp_path)
    member = await env.add_member("first")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    task = await env.tasks.create(lead, TaskDraft("code", "code"))
    task = await env.tasks.claim(member, task.id)
    commit = commit_file(member, "result.txt", "result\n", "result")
    await env.tasks.complete(member, task.id, TaskResult("done", commit))
    original = env.integration.git.fast_forward

    async def racing_fast_forward(*, cwd: Path, target: str) -> str:
        (env.repository / "external.txt").write_text("external\n", encoding="utf-8")
        git(env.repository, "add", "external.txt")
        git(env.repository, "commit", "-qm", "external update")
        return await original(cwd=cwd, target=target)

    monkeypatch.setattr(env.integration.git, "fast_forward", racing_fast_forward)
    result = await env.integration.finalize(await env.tasks.list())

    assert result.status == "blocked"
    assert (env.repository / "external.txt").exists()
    assert not (env.repository / "result.txt").exists()
    assert git(env.repository, "log", "-1", "--pretty=%s").stdout.strip() == "external update"
    state = await env.integration.state_store.load_or_create()
    assert state.current is not None and state.current.intent is not None
    assert Path(state.current.integration_root or "").exists()


@pytest.mark.asyncio
async def test_multi_round_reset_after_publish(tmp_path: Path) -> None:
    env = await make_environment(tmp_path)
    member = await env.add_member("first")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    task = await env.tasks.create(lead, TaskDraft("code", "round one"))
    task = await env.tasks.claim(member, task.id)
    first_commit = commit_file(member, "rounds.txt", "one\n", "round one")
    await env.tasks.complete(member, task.id, TaskResult("one", first_commit))
    first_publish = await env.integration.finalize(await env.tasks.list())
    assert first_publish.status == "published"

    task = await env.tasks.update(lead, task.id, TaskPatch(status="pending"))
    assert task.attempt == 2
    assert task.integration_round == 2
    task = await env.tasks.claim(member, task.id)
    second_commit = commit_file(member, "rounds.txt", "two\n", "round two")
    await env.tasks.complete(member, task.id, TaskResult("two", second_commit))
    second_publish = await env.integration.finalize(await env.tasks.list())

    assert second_publish.status == "published"
    state = await env.integration.state_store.load_or_create()
    assert [item.number for item in state.history] == [1, 2]
    assert [item.accepted[0].task.attempt for item in state.history] == [1, 2]
    assert (env.repository / "rounds.txt").read_text(encoding="utf-8") == "two\n"


@pytest.mark.asyncio
async def test_crash_boundary_before_merge_recovers_for_retry(tmp_path: Path) -> None:
    env = await make_environment(tmp_path)
    member = await env.add_member("first")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    task = await env.tasks.create(lead, TaskDraft("code", "code"))
    task = await env.tasks.claim(member, task.id)
    commit = commit_file(member, "before.txt", "before\n", "before")
    async with env.integration.state_store.transaction() as transaction:
        current = transaction.state.current
        assert current is not None and current.integration_head is not None
        intent = IntegrationIntent(
            "merge_task",
            task=TaskAttemptRef(task.id, task.attempt),
            member_name=member.name,
            source_branch=git(member.cwd, "branch", "--show-current").stdout.strip(),
            source_commit=commit,
            expected_head=current.integration_head,
            result_text="done",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        transaction.replace(
            replace(
                transaction.state,
                current=replace(current, phase="integrating", intent=intent),
            )
        )

    summary = await env.integration.recover(env.tasks)
    assert summary.phase == "active"
    assert (await env.integration.state_store.load_or_create()).current.intent is None  # type: ignore[union-attr]
    completed = await env.tasks.complete(member, task.id, TaskResult("done", commit))
    assert completed.status == "completed"


@pytest.mark.asyncio
async def test_crash_boundary_after_merge_recovers_task_once(tmp_path: Path) -> None:
    env = await make_environment(tmp_path)
    member = await env.add_member("first")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    task = await env.tasks.create(lead, TaskDraft("code", "code"))
    task = await env.tasks.claim(member, task.id)
    commit = commit_file(member, "after.txt", "after\n", "after")

    async def crash_callback(_commit: str):
        raise RuntimeError("crash after merge")

    with pytest.raises(RuntimeError, match="crash after merge"):
        await env.integration.integrate_code_task(
            member, task, TaskResult("done", commit), crash_callback
        )
    internal_before = (await env.integration.snapshot()).integration_head
    summary = await env.integration.recover(env.tasks)

    assert (await env.tasks.get(task.id)).status == "completed"
    assert len(summary.accepted_tasks) == 1
    assert summary.integration_head != internal_before
    events = await env.tasks.pending_events("demo")
    assert len([event for event in events if event.task_id == task.id]) == 1


@pytest.mark.asyncio
async def test_crash_boundary_after_publish_recovers_without_duplicate_ff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = await make_environment(tmp_path)
    member = await env.add_member("first")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    task = await env.tasks.create(lead, TaskDraft("code", "code"))
    task = await env.tasks.claim(member, task.id)
    commit = commit_file(member, "publish.txt", "publish\n", "publish")
    await env.tasks.complete(member, task.id, TaskResult("done", commit))
    original = env.integration.git.fast_forward

    async def publish_then_crash(*, cwd: Path, target: str) -> str:
        await original(cwd=cwd, target=target)
        raise RuntimeError("crash after ff")

    monkeypatch.setattr(env.integration.git, "fast_forward", publish_then_crash)
    failed = await env.integration.finalize(await env.tasks.list())
    assert failed.status == "blocked"
    published_head = git(env.repository, "rev-parse", "HEAD").stdout.strip()
    await env.integration.close()
    recovered_service = TeamIntegrationService(
        "demo", env.repository, env.store, env.manager, git=env.manager.git
    )
    env.tasks.integration = recovered_service

    summary = await recovered_service.recover(env.tasks)

    assert summary.phase == "published"
    assert git(env.repository, "rev-parse", "HEAD").stdout.strip() == published_head
    state = await recovered_service.state_store.load_or_create()
    assert len(state.history) == 1 and state.history[0].phase == "published"


@pytest.mark.asyncio
async def test_crash_boundary_after_published_state_recovers_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = await make_environment(tmp_path)
    member = await env.add_member("first")
    lead = TeamActor("demo", "lead", "lead", env.repository)
    task = await env.tasks.create(lead, TaskDraft("code", "code"))
    task = await env.tasks.claim(member, task.id)
    commit = commit_file(member, "published.txt", "published\n", "published")
    await env.tasks.complete(member, task.id, TaskResult("done", commit))

    async def crash_during_sync(_head: str) -> list[str]:
        raise RuntimeError("crash after published state")

    monkeypatch.setattr(env.integration, "_sync_members", crash_during_sync)
    with pytest.raises(RuntimeError, match="crash after published state"):
        await env.integration.finalize(await env.tasks.list())
    state_before = await env.integration.state_store.load_or_create()
    assert state_before.current is not None and state_before.current.phase == "published"
    published_head = git(env.repository, "rev-parse", "HEAD").stdout.strip()
    await env.integration.close()
    recovered_service = TeamIntegrationService(
        "demo", env.repository, env.store, env.manager, git=env.manager.git
    )

    summary = await recovered_service.recover(env.tasks)

    assert summary.phase == "published"
    assert git(env.repository, "rev-parse", "HEAD").stdout.strip() == published_head
    state_after = await recovered_service.state_store.load_or_create()
    assert state_after.current is None
    assert state_after.history[-1].phase == "published"
