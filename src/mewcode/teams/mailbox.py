from __future__ import annotations

import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mewcode.teams.approvals import ApprovalService
from mewcode.teams.locking import AtomicJsonFile, FileLock
from mewcode.teams.models import (
    BroadcastResult,
    DeliveryResult,
    MessageDraft,
    TeamActor,
    TeamConfig,
    TeamDataError,
    TeamMessage,
    message_from_dict,
)
from mewcode.teams.paths import TeamPaths
from mewcode.teams.store import TeamStore


_TASK_PROTOCOLS = frozenset({"task_assignment", "task_completed", "task_failed"})
_APPROVAL_PROTOCOLS = frozenset({"plan_request", "plan_approved", "plan_rejected"})
_MEMBER_PROTOCOLS = frozenset({"member_idle", "member_resumed", "member_terminated"})


class MailboxService:
    def __init__(
        self,
        team_name: str,
        store: TeamStore,
        approvals: ApprovalService | None = None,
        config: TeamConfig | None = None,
    ) -> None:
        self.team_name = team_name
        self.store = store
        self.approvals = approvals
        self.config = config or store.config
        self.paths = TeamPaths.for_team(team_name, base=store.root)

    async def send(self, actor: TeamActor, draft: MessageDraft) -> DeliveryResult:
        await self._validate_actor(actor)
        recipient = draft.recipient or ""
        await self._validate_recipient(recipient)
        self._validate_protocol(actor, draft, recipient)
        if draft.protocol == "plan_request":
            if self.approvals is None or draft.task_id is None:
                raise TeamDataError("审批服务不可用")
            approval = await self.approvals.request(actor, draft.task_id, draft.body)
            return DeliveryResult(recipient="lead", success=True, message_id=approval.id)
        if draft.protocol in {"plan_approved", "plan_rejected"}:
            if self.approvals is None or draft.approval_id is None or draft.task_id is None or draft.plan_version is None:
                raise TeamDataError("审批消息缺少关联字段")
            if draft.protocol == "plan_approved":
                await self.approvals.approve(actor, draft.approval_id, draft.task_id, draft.plan_version)
            else:
                await self.approvals.reject(
                    actor,
                    draft.approval_id,
                    draft.task_id,
                    draft.plan_version,
                    draft.reason or draft.body,
                )
            return DeliveryResult(recipient=recipient, success=True, message_id=draft.approval_id)
        message = self._build_message(actor, draft, recipient)
        await self.deliver_message(message)
        return DeliveryResult(recipient=recipient, success=True, message_id=message.id)

    async def broadcast(self, actor: TeamActor, draft: MessageDraft) -> BroadcastResult:
        await self._validate_actor(actor)
        if draft.protocol in _APPROVAL_PROTOCOLS:
            raise TeamDataError("审批协议不支持广播")
        team = await self.store.load(self.team_name)
        recipients = ["lead", *sorted(team.members)]
        recipients = [name for name in recipients if name != actor.name and (name == "lead" or team.members[name].status != "terminated")]
        broadcast_id = f"broadcast-{uuid.uuid4().hex}"
        deliveries: list[DeliveryResult] = []
        for recipient in recipients:
            try:
                self._validate_protocol(actor, draft, recipient)
                message = self._build_message(
                    actor,
                    replace(draft, recipient=recipient),
                    recipient,
                    broadcast_id=broadcast_id,
                )
                await self.deliver_message(message)
                deliveries.append(DeliveryResult(recipient, True, message.id))
            except Exception as exc:
                deliveries.append(DeliveryResult(recipient, False, error=str(exc)))
        return BroadcastResult(broadcast_id=broadcast_id, deliveries=tuple(deliveries))

    async def unread(self, actor: TeamActor) -> tuple[TeamMessage, ...]:
        await self._validate_actor(actor)
        messages = await self._read_mailbox(actor.name)
        return tuple(message for message in messages if not message.read)

    async def acknowledge(self, actor: TeamActor, message_ids: tuple[str, ...] | list[str]) -> None:
        await self._validate_actor(actor)
        identifiers = frozenset(message_ids)
        file = self._mailbox_store(actor.name)

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            messages = self._parse_document(raw)
            raw["messages"] = [
                asdict(replace(message, read=True)) if message.id in identifiers else asdict(message)
                for message in messages
            ]
            raw["revision"] = int(raw.get("revision", 0)) + 1
            return raw

        await file.mutate(mutate)

    async def deliver_message(self, message: TeamMessage) -> None:
        await self._validate_recipient(message.recipient)
        file = self._mailbox_store(message.recipient)

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            messages = self._parse_document(raw)
            if any(existing.id == message.id for existing in messages):
                return raw
            raw["messages"] = [asdict(existing) for existing in messages] + [asdict(message)]
            raw["revision"] = int(raw.get("revision", 0)) + 1
            return raw

        await file.mutate(mutate)

    async def _read_mailbox(self, actor_name: str) -> tuple[TeamMessage, ...]:
        return self._parse_document(await self._mailbox_store(actor_name).read())

    def _mailbox_store(self, actor_name: str) -> AtomicJsonFile:
        return AtomicJsonFile(
            self.paths.mailbox_file(actor_name),
            FileLock(self.paths.mailbox_lock(actor_name), self.config),
        )

    def _parse_document(self, raw: dict[str, Any]) -> tuple[TeamMessage, ...]:
        if raw.get("schema_version") != 1:
            raise TeamDataError("未知 mailbox schema_version")
        messages = raw.get("messages", [])
        if not isinstance(messages, list):
            raise TeamDataError("mailbox messages 必须是数组")
        return tuple(message_from_dict(item) for item in messages if isinstance(item, dict))

    def _build_message(
        self,
        actor: TeamActor,
        draft: MessageDraft,
        recipient: str,
        *,
        broadcast_id: str | None = None,
    ) -> TeamMessage:
        body = draft.body.strip()
        if not body:
            raise TeamDataError("消息正文不能为空")
        summary = (draft.summary or _summary(body)).strip()
        if not summary:
            raise TeamDataError("消息摘要不能为空")
        return TeamMessage(
            id=draft.message_id or f"message-{uuid.uuid4().hex}",
            sender=actor.name,
            recipient=recipient,
            protocol=draft.protocol,
            body=body,
            summary=summary[:160],
            timestamp=_now(),
            read=False,
            task_id=draft.task_id,
            approval_id=draft.approval_id,
            plan_version=draft.plan_version,
            broadcast_id=broadcast_id,
        )

    async def _validate_actor(self, actor: TeamActor) -> None:
        if actor.team_name != self.team_name:
            raise TeamDataError("发件人不属于当前团队")
        if actor.kind == "lead" and actor.name != "lead":
            raise TeamDataError("Lead 身份无效")
        if actor.kind == "member":
            member = await self.store.get_member(self.team_name, actor.name)
            if member.status == "terminated":
                raise TeamDataError(f"团队成员已终止: {actor.name}")
            if Path(member.worktree_cwd).resolve() != actor.cwd.resolve():
                raise TeamDataError("成员身份与花名册工作目录不匹配")

    async def _validate_recipient(self, recipient: str) -> None:
        if recipient == "lead":
            return
        member = await self.store.get_member(self.team_name, recipient)
        if member.status == "terminated":
            raise TeamDataError(f"收件人已终止: {recipient}")

    def _validate_protocol(self, actor: TeamActor, draft: MessageDraft, recipient: str) -> None:
        from mewcode.teams.models import TEAM_PROTOCOLS

        if draft.protocol not in TEAM_PROTOCOLS:
            raise TeamDataError(f"未知团队消息协议: {draft.protocol}")
        if draft.protocol in _TASK_PROTOCOLS and not draft.task_id:
            raise TeamDataError(f"{draft.protocol} 必须提供 task_id")
        if draft.protocol == "task_assignment" and (actor.kind != "lead" or recipient == "lead"):
            raise TeamDataError("任务指派只能由 Lead 发送给成员")
        if draft.protocol in {"task_completed", "task_failed"} and actor.kind != "member":
            raise TeamDataError(f"{draft.protocol} 只能由成员发送")
        if draft.protocol in _APPROVAL_PROTOCOLS:
            if not draft.task_id:
                raise TeamDataError(f"{draft.protocol} 必须提供 task_id")
            if draft.protocol == "plan_request":
                if actor.kind != "member" or recipient != "lead":
                    raise TeamDataError("计划请求只能由成员发送给 Lead")
            else:
                if actor.kind != "lead" or recipient == "lead":
                    raise TeamDataError("审批决定只能由 Lead 发送给成员")
                if not draft.approval_id or draft.plan_version is None:
                    raise TeamDataError("审批决定必须提供 approval_id 和 plan_version")
        if draft.protocol in _MEMBER_PROTOCOLS:
            if actor.kind != "member" or recipient != "lead":
                raise TeamDataError(f"{draft.protocol} 只能由成员发送给 Lead")
            if not draft.body.strip():
                raise TeamDataError(f"{draft.protocol} 正文不能为空")


def _summary(body: str) -> str:
    for line in body.splitlines():
        if line.strip():
            return line.strip()[:160]
    return "团队消息"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
