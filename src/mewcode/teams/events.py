from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol

from mewcode.teams.mailbox import MailboxService
from mewcode.teams.models import OutboxEvent, OutboxFlushReport, TeamDataError, TeamMessage


class OutboxSource(Protocol):
    async def pending_events(self, team_name: str) -> tuple[OutboxEvent, ...]:
        ...

    async def mark_delivered(self, team_name: str, event_id: str, recipient: str) -> None:
        ...


class TeamOutboxDispatcher:
    def __init__(self, team_name: str, mailbox: MailboxService, sources: Sequence[OutboxSource]) -> None:
        self.team_name = team_name
        self.mailbox = mailbox
        self.sources = tuple(sources)

    async def flush(self, team_name: str | None = None) -> OutboxFlushReport:
        actual = team_name or self.team_name
        if actual != self.team_name:
            raise TeamDataError("outbox 团队不匹配")
        delivered: list[str] = []
        failed: list[str] = []
        for source in self.sources:
            for event in await source.pending_events(actual):
                for recipient in event.recipients:
                    if recipient in event.delivered_to:
                        continue
                    message_id = _delivery_id(event.id, recipient)
                    message = TeamMessage(
                        id=message_id,
                        sender=event.sender,
                        recipient=recipient,
                        protocol=event.protocol,
                        body=event.body,
                        summary=event.summary,
                        timestamp=event.created_at,
                        read=False,
                        task_id=event.task_id,
                        approval_id=event.approval_id,
                        plan_version=event.plan_version,
                    )
                    try:
                        await self.mailbox.deliver_message(message)
                        await source.mark_delivered(actual, event.id, recipient)
                        delivered.append(message_id)
                    except Exception:
                        failed.append(message_id)
        return OutboxFlushReport(tuple(delivered), tuple(failed))

    async def flush_event(self, team_name: str, event_id: str) -> OutboxFlushReport:
        if team_name != self.team_name:
            raise TeamDataError("outbox 团队不匹配")
        delivered: list[str] = []
        failed: list[str] = []
        for source in self.sources:
            events = tuple(event for event in await source.pending_events(team_name) if event.id == event_id)
            if not events:
                continue
            temporary = TeamOutboxDispatcher(team_name, self.mailbox, (_FilteredSource(source, event_id),))
            report = await temporary.flush()
            delivered.extend(report.delivered)
            failed.extend(report.failed)
        return OutboxFlushReport(tuple(delivered), tuple(failed))


class _FilteredSource:
    def __init__(self, source: OutboxSource, event_id: str) -> None:
        self.source = source
        self.event_id = event_id

    async def pending_events(self, team_name: str) -> tuple[OutboxEvent, ...]:
        return tuple(event for event in await self.source.pending_events(team_name) if event.id == self.event_id)

    async def mark_delivered(self, team_name: str, event_id: str, recipient: str) -> None:
        await self.source.mark_delivered(team_name, event_id, recipient)


def _delivery_id(event_id: str, recipient: str) -> str:
    digest = hashlib.sha256(f"{event_id}\0{recipient}".encode("utf-8")).hexdigest()[:24]
    return f"message-{digest}"
