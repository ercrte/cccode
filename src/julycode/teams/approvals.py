from __future__ import annotations

import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any

from julycode.teams.locking import AtomicJsonFile, FileLock
from julycode.teams.models import (
    ApprovalRecord,
    OutboxEvent,
    TeamActor,
    TeamConfig,
    TeamDataError,
    approval_from_dict,
    outbox_from_dict,
)
from julycode.teams.paths import TeamPaths
from julycode.teams.store import TeamStore
from julycode.teams.tasks import TaskService


class ApprovalService:
    def __init__(self, team_name: str, store: TeamStore, tasks: TaskService, config: TeamConfig | None = None) -> None:
        self.team_name = team_name
        self.store = store
        self.tasks = tasks
        self.config = config or store.config
        self.paths = TeamPaths.for_team(team_name, base=store.root)
        self.file = AtomicJsonFile(
            self.paths.approvals_file,
            FileLock(self.paths.approvals_lock, self.config),
        )

    async def request(self, member: TeamActor, task_id: str, plan: str) -> ApprovalRecord:
        if member.kind != "member":
            raise TeamDataError("只有团队成员可以提交审批计划")
        task = await self.tasks.get(task_id)
        if task.assignee != member.name or task.status != "awaiting_approval":
            raise TeamDataError("任务当前不属于该成员或不在等待审批")
        text = plan.strip()
        if not text:
            raise TeamDataError("审批计划不能为空")
        created: ApprovalRecord | None = None

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            nonlocal created
            approvals, outbox = self._parse(raw)
            pending = [item for item in approvals if item.task_id == task_id and item.member_name == member.name and item.status == "pending"]
            if pending:
                raise TeamDataError(f"已有待审批计划: {pending[-1].id}")
            versions = [item.plan_version for item in approvals if item.task_id == task_id and item.member_name == member.name]
            version = max(versions, default=0) + 1
            created = ApprovalRecord(
                id=f"approval-{uuid.uuid4().hex}",
                task_id=task_id,
                member_name=member.name,
                plan=text,
                plan_version=version,
                status="pending",
                requested_at=_now(),
            )
            event = OutboxEvent(
                id=f"event-{uuid.uuid4().hex}", source="approval", protocol="plan_request",
                sender=member.name, recipients=("lead",), body=text,
                summary=f"{member.name} 请求审批任务 {task_id} 计划 v{version}",
                task_id=task_id, approval_id=created.id, plan_version=version, created_at=_now(),
            )
            return self._document(raw, (*approvals, created), (*outbox, event))

        await self.file.mutate(mutate)
        assert created is not None
        await self.tasks.set_approval(task_id, created.id, approved=False)
        member_record = await self.store.get_member(self.team_name, member.name)
        await self.store.update_member(
            self.team_name,
            replace(member_record, pending_approval_id=created.id, status="awaiting_approval", updated_at=_now()),
        )
        return created

    async def approve(self, lead: TeamActor, approval_id: str, task_id: str, version: int) -> ApprovalRecord:
        return await self._decide(lead, approval_id, task_id, version, approved=True, reason=None)

    async def reject(
        self,
        lead: TeamActor,
        approval_id: str,
        task_id: str,
        version: int,
        reason: str,
    ) -> ApprovalRecord:
        text = reason.strip()
        if not text:
            raise TeamDataError("驳回审批必须提供理由")
        return await self._decide(lead, approval_id, task_id, version, approved=False, reason=text)

    async def current_for_member(self, member: TeamActor) -> ApprovalRecord | None:
        approvals, _ = self._parse(await self.file.read())
        relevant = [item for item in approvals if item.member_name == member.name]
        if not relevant:
            return None
        return sorted(relevant, key=lambda item: (item.requested_at, item.plan_version))[-1]

    async def can_mutate_project(self, member: TeamActor) -> bool:
        member_record = await self.store.get_member(self.team_name, member.name)
        if not member_record.require_approval:
            task = await self.tasks.get(member_record.current_task_id) if member_record.current_task_id else None
            return task is not None and task.status == "in_progress" and task.assignee == member.name
        current = await self.current_for_member(member)
        if current is None or current.status != "approved":
            return False
        task = await self.tasks.get(current.task_id)
        return task.status == "in_progress" and task.assignee == member.name and task.approval_id == current.id

    async def reconcile(self) -> tuple[str, ...]:
        approvals, _ = self._parse(await self.file.read())
        repaired: list[str] = []
        latest: dict[tuple[str, str], ApprovalRecord] = {}
        for approval in approvals:
            key = (approval.task_id, approval.member_name)
            previous = latest.get(key)
            if previous is None or approval.plan_version > previous.plan_version:
                latest[key] = approval
        for approval in latest.values():
            if approval.status not in {"pending", "rejected", "approved"}:
                continue
            task = await self.tasks.get(approval.task_id)
            expected_status = "in_progress" if approval.status == "approved" else "awaiting_approval"
            if task.status == "awaiting_approval" and (
                task.approval_id != approval.id or expected_status == "in_progress"
            ):
                await self.tasks.set_approval(
                    task.id,
                    approval.id,
                    approved=approval.status == "approved",
                )
                repaired.append(task.id)
            member = await self.store.get_member(self.team_name, approval.member_name)
            pending_id = None if approval.status == "approved" else approval.id
            member_status = "running" if approval.status == "approved" else "awaiting_approval"
            if member.status != member_status or member.pending_approval_id != pending_id:
                await self.store.update_member(
                    self.team_name,
                    replace(
                        member,
                        status=member_status,
                        pending_approval_id=pending_id,
                        updated_at=_now(),
                    ),
                )
        return tuple(repaired)

    async def pending_events(self, team_name: str) -> tuple[OutboxEvent, ...]:
        if team_name != self.team_name:
            return ()
        return self._parse(await self.file.read())[1]

    async def mark_delivered(self, team_name: str, event_id: str, recipient: str) -> None:
        if team_name != self.team_name:
            raise TeamDataError("outbox 团队不匹配")

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            approvals, events = self._parse(raw)
            updated = tuple(
                replace(event, delivered_to=(*event.delivered_to, recipient))
                if event.id == event_id and recipient not in event.delivered_to
                else event
                for event in events
            )
            return self._document(raw, approvals, updated)

        await self.file.mutate(mutate)

    async def _decide(
        self,
        lead: TeamActor,
        approval_id: str,
        task_id: str,
        version: int,
        *,
        approved: bool,
        reason: str | None,
    ) -> ApprovalRecord:
        if lead.kind != "lead" or lead.name != "lead":
            raise TeamDataError("只有 Team Lead 可以决定审批")
        decided: ApprovalRecord | None = None

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            nonlocal decided
            approvals, outbox = self._parse(raw)
            matches = [item for item in approvals if item.id == approval_id]
            if not matches:
                raise TeamDataError(f"未知审批: {approval_id}")
            current = matches[0]
            if current.task_id != task_id or current.plan_version != version:
                raise TeamDataError("审批任务或版本不匹配")
            if current.status != "pending":
                raise TeamDataError(f"审批已经结束: {current.status}")
            decided = replace(
                current,
                status=("approved" if approved else "rejected"),
                decided_at=_now(),
                decided_by=lead.name,
                reason=reason,
            )
            updated_approvals = tuple(decided if item.id == approval_id else item for item in approvals)
            protocol = "plan_approved" if approved else "plan_rejected"
            body = "计划已批准，可以开始执行。" if approved else f"计划已驳回：{reason}"
            event = OutboxEvent(
                id=f"event-{uuid.uuid4().hex}", source="approval", protocol=protocol,
                sender=lead.name, recipients=(current.member_name,), body=body, summary=body,
                task_id=task_id, approval_id=approval_id, plan_version=version, created_at=_now(),
            )
            return self._document(raw, updated_approvals, (*outbox, event))

        await self.file.mutate(mutate)
        assert decided is not None
        await self.tasks.set_approval(task_id, approval_id, approved=approved)
        member_record = await self.store.get_member(self.team_name, decided.member_name)
        await self.store.update_member(
            self.team_name,
            replace(
                member_record,
                status=("running" if approved else "awaiting_approval"),
                pending_approval_id=(None if approved else approval_id),
                updated_at=_now(),
            ),
        )
        return decided

    def _parse(self, raw: dict[str, Any]) -> tuple[tuple[ApprovalRecord, ...], tuple[OutboxEvent, ...]]:
        if raw.get("schema_version") != 1:
            raise TeamDataError("未知 approvals schema_version")
        approvals = raw.get("approvals", [])
        outbox = raw.get("outbox", [])
        if not isinstance(approvals, list) or not isinstance(outbox, list):
            raise TeamDataError("approvals/outbox 必须是数组")
        return (
            tuple(approval_from_dict(item) for item in approvals if isinstance(item, dict)),
            tuple(outbox_from_dict(item) for item in outbox if isinstance(item, dict)),
        )

    def _document(
        self,
        raw: dict[str, Any],
        approvals: tuple[ApprovalRecord, ...],
        outbox: tuple[OutboxEvent, ...],
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "revision": int(raw.get("revision", 0)) + 1,
            "approvals": [asdict(item) for item in approvals],
            "outbox": [asdict(item) for item in outbox],
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
