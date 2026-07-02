from __future__ import annotations

from pathlib import Path

import pytest

from mewcode.providers.base import ChatMessage
from mewcode.session import ChatSession
from mewcode.teams.manager import TeamManager
from mewcode.teams.models import MessageDraft, TaskDraft, TeamActor, TeamMemberRecord
from mewcode.teams.store import TeamStore
from tests.test_worktrees import init_repository


@pytest.mark.asyncio
async def test_manager_lifecycle_persists_across_instances(tmp_path: Path) -> None:
    repo = init_repository(tmp_path / "repo")
    root = tmp_path / "teams"
    first = TeamManager(repo, store=TeamStore(repo, root=root))
    await first.create_team("demo")
    await first.close_team()
    second = TeamManager(repo, store=TeamStore(repo, root=root))
    opened = await second.open_team("demo")
    assert opened.team.name == "demo" and second.active_team == "demo"


@pytest.mark.asyncio
async def test_lead_completion_guard_blocks_active_and_summarizes_complete(tmp_path: Path) -> None:
    repo = init_repository(tmp_path / "repo")
    manager = TeamManager(repo, store=TeamStore(repo, root=tmp_path / "teams"))
    await manager.create_team("demo")
    services = manager._service("demo")
    lead = TeamActor("demo", "lead", "lead", repo)
    task = await services.tasks.create(lead, TaskDraft("research", "", kind="research"))
    controller = manager.loop_controller(__import__("mewcode.tools.base", fromlist=["RuntimePrincipal"]).RuntimePrincipal())
    assert controller is not None
    blocked = await controller.review_completion(ChatMessage(role="assistant", content="完成"))
    assert not blocked.accept and "未完成" in (blocked.continuation or "")
    await services.tasks.update(lead, task.id, __import__("mewcode.teams.models", fromlist=["TaskPatch"]).TaskPatch(status="cancelled"))
    failed = await controller.review_completion(ChatMessage(role="assistant", content="完成"))
    assert failed.accept and "尚未达成" in failed.message.content


@pytest.mark.asyncio
async def test_lead_mailbox_is_injected_once(tmp_path: Path) -> None:
    repo = init_repository(tmp_path / "repo")
    manager = TeamManager(repo, store=TeamStore(repo, root=tmp_path / "teams"))
    await manager.create_team("demo")
    services = manager._service("demo")
    lead = TeamActor("demo", "lead", "lead", repo)
    from mewcode.teams.models import MessageDraft
    await services.mailbox.send(lead, MessageDraft("lead", "message", "event"))
    controller = manager.loop_controller(__import__("mewcode.tools.base", fromlist=["RuntimePrincipal"]).RuntimePrincipal())
    session = ChatSession()
    await controller.before_iteration(session)  # type: ignore[union-attr]
    await controller.before_iteration(session)  # type: ignore[union-attr]
    assert sum((message.metadata or {}).get("team_message_id") is not None for message in session.messages) == 1


@pytest.mark.asyncio
async def test_direct_collaboration_and_approval_cycle(tmp_path: Path) -> None:
    repo = init_repository(tmp_path / "repo")
    manager = TeamManager(repo, store=TeamStore(repo, root=tmp_path / "teams"))
    await manager.create_team("demo")
    now = "2026-01-01T00:00:00+00:00"
    for name, approval in (("alice", False), ("bob", True)):
        await manager.store.add_member(
            "demo",
            TeamMemberRecord(
                name, "reviewer", "coroutine", approval, "idle", str(repo), str(repo), "branch",
                f"owner-{name}", str(manager.store.root / f"demo/sessions/{name}.jsonl"),
                None, None, now, now, now,
            ),
        )
        paths = manager.store.root / "demo/mailboxes"
        (paths / f"{name}.json").write_text(
            '{"schema_version":1,"revision":1,"messages":[]}', encoding="utf-8"
        )
    services = manager._service("demo")
    lead = TeamActor("demo", "lead", "lead", repo)
    alice = TeamActor("demo", "alice", "member", repo)
    bob = TeamActor("demo", "bob", "member", repo)
    task = await services.tasks.create(lead, TaskDraft("review", "", kind="research"))
    await services.tasks.claim(bob, task.id)

    requested = await manager.send_message(
        bob,
        MessageDraft("lead", "plan_request", "v1：先检查", task_id=task.id),
    )
    first = await services.approvals.current_for_member(bob)
    assert first is not None and requested.message_id == first.id
    await manager.send_message(
        lead,
        MessageDraft(
            "bob", "plan_rejected", "补充验证", task_id=task.id,
            approval_id=first.id, plan_version=first.plan_version, reason="补充验证",
        ),
    )
    await manager.send_message(
        bob,
        MessageDraft("lead", "plan_request", "v2：检查并验证", task_id=task.id),
    )
    second = await services.approvals.current_for_member(bob)
    assert second is not None and second.plan_version == 2
    await manager.send_message(
        lead,
        MessageDraft(
            "bob", "plan_approved", "批准", task_id=task.id,
            approval_id=second.id, plan_version=second.plan_version,
        ),
    )
    assert await services.approvals.can_mutate_project(bob)

    await manager.send_message(bob, MessageDraft("alice", "message", "请直接复核任务结果"))
    unread = await services.mailbox.unread(alice)
    assert unread[-1].sender == "bob" and "复核" in unread[-1].body
