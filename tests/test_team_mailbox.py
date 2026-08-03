from __future__ import annotations

from pathlib import Path

import pytest

from julycode.teams.events import TeamOutboxDispatcher
from julycode.teams.mailbox import MailboxService
from julycode.teams.models import MessageDraft, TeamActor, TeamDataError, TeamMemberRecord
from julycode.teams.store import TeamStore
from tests.test_worktrees import init_repository


async def setup_mailbox(tmp_path: Path):
    repo = init_repository(tmp_path / "repo")
    store = TeamStore(repo, root=tmp_path / "teams")
    await store.create("demo")
    now = "2026-01-01T00:00:00+00:00"
    for name in ("one", "two"):
        member = TeamMemberRecord(
            name, "reviewer", "coroutine", False, "idle", str(repo), str(repo), "branch", name,
            str(store.root / f"demo/sessions/{name}.jsonl"), None, None, now, now, now,
        )
        await store.add_member("demo", member)
        paths = store.root / "demo/mailboxes"
        (paths / f"{name}.json").write_text('{"schema_version":1,"revision":1,"messages":[]}', encoding="utf-8")
    mailbox = MailboxService("demo", store)
    return repo, store, mailbox


@pytest.mark.asyncio
async def test_direct_message_defaults_and_acknowledge(tmp_path: Path) -> None:
    repo, _store, mailbox = await setup_mailbox(tmp_path)
    sender = TeamActor("demo", "one", "member", repo)
    recipient = TeamActor("demo", "two", "member", repo)
    result = await mailbox.send(sender, MessageDraft("two", "message", "hello"))
    unread = await mailbox.unread(recipient)
    assert result.success and len(unread) == 1
    assert unread[0].sender == "one" and unread[0].summary == "hello" and not unread[0].read
    await mailbox.acknowledge(recipient, [unread[0].id])
    assert await mailbox.unread(recipient) == ()


@pytest.mark.asyncio
async def test_protocol_validation_rejects_missing_task(tmp_path: Path) -> None:
    repo, _store, mailbox = await setup_mailbox(tmp_path)
    with pytest.raises(TeamDataError, match="task_id"):
        await mailbox.send(TeamActor("demo", "one", "member", repo), MessageDraft("two", "task_assignment", "do"))


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ("task_assignment", "task_completed", "task_failed"))
async def test_protocol_validation_requires_task_id(tmp_path: Path, protocol: str) -> None:
    repo, _store, mailbox = await setup_mailbox(tmp_path)
    actor = TeamActor("demo", "lead", "lead", repo) if protocol == "task_assignment" else TeamActor("demo", "one", "member", repo)
    recipient = "two" if protocol == "task_assignment" else "lead"

    with pytest.raises(TeamDataError, match="task_id"):
        await mailbox.send(actor, MessageDraft(recipient, protocol, "body"))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_protocol_validation_rejects_unknown_and_wrong_sender(tmp_path: Path) -> None:
    repo, _store, mailbox = await setup_mailbox(tmp_path)
    lead = TeamActor("demo", "lead", "lead", repo)
    member = TeamActor("demo", "one", "member", repo)

    with pytest.raises(TeamDataError, match="未知团队消息协议"):
        await mailbox.send(member, MessageDraft("two", "unknown", "body"))  # type: ignore[arg-type]
    with pytest.raises(TeamDataError, match="任务指派只能"):
        await mailbox.send(member, MessageDraft("two", "task_assignment", "body", task_id="task-1"))
    with pytest.raises(TeamDataError, match="只能由成员"):
        await mailbox.send(lead, MessageDraft("two", "task_completed", "body", task_id="task-1"))
    with pytest.raises(TeamDataError, match="发送给 Lead"):
        await mailbox.send(member, MessageDraft("two", "member_idle", "idle"))


@pytest.mark.asyncio
async def test_registry_rejects_forged_cross_team_and_unknown_sender(tmp_path: Path) -> None:
    repo, _store, mailbox = await setup_mailbox(tmp_path)

    with pytest.raises(TeamDataError, match="不属于当前团队"):
        await mailbox.send(TeamActor("other", "one", "member", repo), MessageDraft("two", "message", "x"))
    with pytest.raises(TeamDataError, match="未知团队成员"):
        await mailbox.send(TeamActor("demo", "ghost", "member", repo), MessageDraft("two", "message", "x"))
    with pytest.raises(TeamDataError, match="工作目录不匹配"):
        await mailbox.send(
            TeamActor("demo", "one", "member", tmp_path),
            MessageDraft("two", "message", "x"),
        )


@pytest.mark.asyncio
async def test_broadcast_delivers_to_other_participants(tmp_path: Path) -> None:
    repo, _store, mailbox = await setup_mailbox(tmp_path)
    result = await mailbox.broadcast(TeamActor("demo", "one", "member", repo), MessageDraft(None, "message", "all"))
    assert {item.recipient for item in result.deliveries} == {"lead", "two"}


@pytest.mark.asyncio
async def test_outbox_dispatch_is_idempotent(tmp_path: Path) -> None:
    repo, store, mailbox = await setup_mailbox(tmp_path)
    from julycode.teams.models import OutboxEvent
    event = OutboxEvent(
        "event-1", "team", "member_idle", "one", ("lead",), "idle", "idle", None, None, None,
        "2026-01-01T00:00:00+00:00",
    )
    await store.append_outbox("demo", __import__("dataclasses").asdict(event))
    dispatcher = TeamOutboxDispatcher("demo", mailbox, (store,))
    await dispatcher.flush()
    await dispatcher.flush()
    unread = await mailbox.unread(TeamActor("demo", "lead", "lead", repo))
    assert len(unread) == 1
