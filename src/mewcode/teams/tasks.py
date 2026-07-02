from __future__ import annotations

import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mewcode.teams.locking import AtomicJsonFile, FileLock
from mewcode.teams.models import (
    OutboxEvent,
    TaskDraft,
    TaskPatch,
    TaskResult,
    TeamActor,
    TeamConfig,
    TeamDataError,
    TeamTask,
    TASK_KINDS,
    TASK_STATUSES,
    outbox_from_dict,
    task_from_dict,
)
from mewcode.teams.paths import TeamPaths
from mewcode.teams.store import TeamStore
from mewcode.worktrees.git import GitClient


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class TaskService:
    def __init__(self, team_name: str, store: TeamStore, config: TeamConfig | None = None, *, git: GitClient | None = None) -> None:
        self.team_name = team_name
        self.store = store
        self.config = config or store.config
        self.paths = TeamPaths.for_team(team_name, base=store.root)
        self.file = AtomicJsonFile(self.paths.tasks_file, FileLock(self.paths.tasks_lock, self.config))
        self.git = git or GitClient()

    async def create(self, actor: TeamActor, draft: TaskDraft) -> TeamTask:
        await self._validate_actor(actor)
        if draft.kind not in TASK_KINDS:
            raise TeamDataError(f"未知任务类型: {draft.kind}")
        title = draft.title.strip()
        if not title:
            raise TeamDataError("任务标题不能为空")
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        now = _now()
        task = TeamTask(
            id=task_id,
            title=title,
            description=draft.description,
            kind=draft.kind,
            status="pending",
            dependencies=tuple(dict.fromkeys(draft.dependencies)),
            assignee=None,
            created_by=actor.name,
            created_at=now,
            updated_at=now,
        )

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            tasks, outbox = self._parse_document(raw)
            tasks[task.id] = task
            self._validate_graph(tasks)
            tasks = self._recompute(tasks)
            return self._document(raw, tasks, outbox)

        updated = await self.file.mutate(mutate)
        return self._parse_document(updated)[0][task_id]

    async def get(self, task_id: str) -> TeamTask:
        tasks, _ = self._parse_document(await self.file.read())
        try:
            return tasks[task_id]
        except KeyError as exc:
            raise TeamDataError(f"未知团队任务: {task_id}") from exc

    async def list(self, status: str | None = None) -> tuple[TeamTask, ...]:
        tasks, _ = self._parse_document(await self.file.read())
        values = sorted(tasks.values(), key=lambda item: (item.created_at, item.id))
        if status is not None:
            values = [task for task in values if task.status == status]
        return tuple(values)

    async def update(self, actor: TeamActor, task_id: str, patch: TaskPatch) -> TeamTask:
        await self._validate_actor(actor)
        if patch.status is not None and patch.status not in TASK_STATUSES:
            raise TeamDataError(f"未知任务状态: {patch.status}")
        if patch.status == "completed":
            raise TeamDataError("完成任务必须使用 complete，以校验结果和代码提交")
        if patch.status in {"blocked", "in_progress", "awaiting_approval", "failed"}:
            raise TeamDataError(f"任务状态 {patch.status} 必须由对应状态机操作产生")

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            tasks, outbox = self._parse_document(raw)
            task = self._require_task(tasks, task_id)
            if task.status in TERMINAL_STATUSES and patch.status not in {"pending", None}:
                raise TeamDataError("终态任务只能显式重置为 pending")
            updated = replace(
                task,
                title=(patch.title.strip() if patch.title is not None else task.title),
                description=(patch.description if patch.description is not None else task.description),
                dependencies=(
                    tuple(dict.fromkeys(patch.dependencies)) if patch.dependencies is not None else task.dependencies
                ),
                status=(patch.status if patch.status is not None else task.status),
                result=(patch.result if patch.result is not None else task.result),
                failure_reason=(patch.failure_reason if patch.failure_reason is not None else task.failure_reason),
                updated_at=_now(),
            )
            if not updated.title:
                raise TeamDataError("任务标题不能为空")
            if patch.status == "pending":
                updated = replace(
                    updated,
                    assignee=None,
                    approval_id=None,
                    start_commit=None,
                    commit=None,
                    result=None,
                    failure_reason=None,
                )
            tasks[task_id] = updated
            self._validate_graph(tasks)
            return self._document(raw, self._recompute(tasks), outbox)

        updated = await self.file.mutate(mutate)
        return self._parse_document(updated)[0][task_id]

    async def delete(self, actor: TeamActor, task_id: str) -> None:
        await self._validate_actor(actor)

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            tasks, outbox = self._parse_document(raw)
            self._require_task(tasks, task_id)
            dependents = [task.id for task in tasks.values() if task_id in task.dependencies]
            if dependents:
                raise TeamDataError(f"任务仍被依赖: {', '.join(dependents)}")
            del tasks[task_id]
            return self._document(raw, tasks, outbox)

        await self.file.mutate(mutate)

    async def claim(self, member: TeamActor, task_id: str) -> TeamTask:
        if member.kind != "member":
            raise TeamDataError("只有团队成员可以领取任务")
        await self._validate_actor(member)
        member_record = await self.store.get_member(self.team_name, member.name)
        if member_record.status == "terminated":
            raise TeamDataError("已终止成员不能领取任务")
        if member_record.current_task_id not in {None, task_id}:
            raise TeamDataError(f"成员已有当前任务: {member_record.current_task_id}")
        start_commit: str | None = None
        task_before = await self.get(task_id)
        if task_before.kind == "code":
            start_commit = await self.git.head_commit(cwd=member.cwd)

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            tasks, outbox = self._parse_document(raw)
            task = self._require_task(tasks, task_id)
            if task.status != "pending" or task.assignee is not None:
                raise TeamDataError(f"任务当前不可领取: {task.status}")
            status = "awaiting_approval" if member_record.require_approval else "in_progress"
            tasks[task_id] = replace(
                task,
                status=status,
                assignee=member.name,
                start_commit=start_commit,
                updated_at=_now(),
                blocked_reason=None,
            )
            return self._document(raw, tasks, outbox)

        updated = await self.file.mutate(mutate)
        task = self._parse_document(updated)[0][task_id]
        await self.store.update_member(
            self.team_name,
            replace(
                member_record,
                status=("awaiting_approval" if member_record.require_approval else "running"),
                current_task_id=task_id,
                updated_at=_now(),
                last_active_at=_now(),
            ),
        )
        return task

    async def complete(self, member: TeamActor, task_id: str, result: TaskResult) -> TeamTask:
        await self._validate_actor(member)
        task = await self.get(task_id)
        if task.assignee != member.name or task.status != "in_progress":
            raise TeamDataError("只有正在执行该任务的成员可以完成任务")
        text = result.result.strip()
        if not text:
            raise TeamDataError("任务完成结果不能为空")
        commit = result.commit
        if task.kind == "code":
            if task.start_commit is None:
                raise TeamDataError("代码任务缺少领取时的 Git 起点")
            state = await self.git.change_state(worktree_root=member.cwd, base=task.start_commit)
            if state.dirty or state.untracked:
                raise TeamDataError("代码任务 Worktree 存在未提交或未跟踪修改")
            head = await self.git.head_commit(cwd=member.cwd)
            commit = commit or head
            if commit != head or commit == task.start_commit:
                raise TeamDataError("结果 commit 必须是成员分支当前新提交")
            if not await self.git.is_ancestor(cwd=member.cwd, ancestor=task.start_commit, descendant=commit):
                raise TeamDataError("结果 commit 不在任务起点之后")

        return await self._finish(member, task, "completed", text, None, commit)

    async def fail(self, member: TeamActor, task_id: str, reason: str) -> TeamTask:
        await self._validate_actor(member)
        task = await self.get(task_id)
        if task.assignee != member.name:
            raise TeamDataError("只有任务负责人可以标记失败")
        if not reason.strip():
            raise TeamDataError("失败原因不能为空")
        return await self._finish(member, task, "failed", None, reason.strip(), None)

    async def release_interrupted(self, task_id: str, reason: str) -> TeamTask:
        task = await self.get(task_id)
        if task.status in TERMINAL_STATUSES:
            return task
        actor = TeamActor(self.team_name, task.assignee or "lead", "member" if task.assignee else "lead", Path("/"))
        return await self._finish(actor, task, "failed", None, reason, None, update_member=False)

    async def set_approval(self, task_id: str, approval_id: str, *, approved: bool) -> TeamTask:
        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            tasks, outbox = self._parse_document(raw)
            task = self._require_task(tasks, task_id)
            if task.status != "awaiting_approval":
                raise TeamDataError("任务当前不在等待审批状态")
            tasks[task_id] = replace(
                task,
                approval_id=approval_id,
                status=("in_progress" if approved else "awaiting_approval"),
                updated_at=_now(),
            )
            return self._document(raw, tasks, outbox)

        updated = await self.file.mutate(mutate)
        return self._parse_document(updated)[0][task_id]

    async def pending_events(self, team_name: str) -> tuple[OutboxEvent, ...]:
        if team_name != self.team_name:
            return ()
        return self._parse_document(await self.file.read())[1]

    async def mark_delivered(self, team_name: str, event_id: str, recipient: str) -> None:
        if team_name != self.team_name:
            raise TeamDataError("outbox 团队不匹配")

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            tasks, events = self._parse_document(raw)
            replaced_events = []
            for event in events:
                if event.id == event_id and recipient not in event.delivered_to:
                    event = replace(event, delivered_to=(*event.delivered_to, recipient))
                replaced_events.append(event)
            return self._document(raw, tasks, tuple(replaced_events))

        await self.file.mutate(mutate)

    async def _finish(
        self,
        member: TeamActor,
        task: TeamTask,
        status: str,
        result: str | None,
        reason: str | None,
        commit: str | None,
        *,
        update_member: bool = True,
    ) -> TeamTask:
        team = await self.store.load(self.team_name)
        recipients = [team.lead_name]
        current_tasks = await self.list()
        for candidate in current_tasks:
            if task.id in candidate.dependencies and candidate.assignee and candidate.assignee not in recipients:
                recipients.append(candidate.assignee)
        protocol = "task_completed" if status == "completed" else "task_failed"
        event = OutboxEvent(
            id=f"event-{uuid.uuid4().hex}",
            source="task",
            protocol=protocol,  # type: ignore[arg-type]
            sender=member.name,
            recipients=tuple(recipients),
            body=result or reason or "",
            summary=result or reason or "任务状态已更新",
            task_id=task.id,
            approval_id=task.approval_id,
            plan_version=None,
            created_at=_now(),
        )

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            tasks, outbox = self._parse_document(raw)
            current = self._require_task(tasks, task.id)
            if current.status in TERMINAL_STATUSES:
                raise TeamDataError(f"任务已经结束: {current.status}")
            if member.kind == "member" and current.assignee != member.name:
                raise TeamDataError("只有任务负责人可以结束任务")
            tasks[task.id] = replace(
                current,
                status=status,  # type: ignore[arg-type]
                result=result,
                failure_reason=reason,
                commit=commit,
                updated_at=_now(),
            )
            return self._document(raw, self._recompute(tasks), (*outbox, event))

        updated = await self.file.mutate(mutate)
        finished = self._parse_document(updated)[0][task.id]
        if update_member and member.kind == "member":
            member_record = await self.store.get_member(self.team_name, member.name)
            await self.store.update_member(
                self.team_name,
                replace(member_record, current_task_id=None, pending_approval_id=None, updated_at=_now()),
            )
        return finished

    def _parse_document(self, raw: dict[str, Any]) -> tuple[dict[str, TeamTask], tuple[OutboxEvent, ...]]:
        if raw.get("schema_version") != 1:
            raise TeamDataError("未知 tasks schema_version")
        tasks_raw = raw.get("tasks", [])
        outbox_raw = raw.get("outbox", [])
        if not isinstance(tasks_raw, list) or not isinstance(outbox_raw, list):
            raise TeamDataError("tasks/outbox 必须是数组")
        tasks = {task.id: task for task in (task_from_dict(value) for value in tasks_raw if isinstance(value, dict))}
        return tasks, tuple(outbox_from_dict(value) for value in outbox_raw if isinstance(value, dict))

    def _document(
        self,
        raw: dict[str, Any],
        tasks: dict[str, TeamTask],
        outbox: tuple[OutboxEvent, ...],
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "revision": int(raw.get("revision", 0)) + 1,
            "tasks": [asdict(task) for task in sorted(tasks.values(), key=lambda item: (item.created_at, item.id))],
            "outbox": [asdict(event) for event in outbox],
        }

    def _validate_graph(self, tasks: dict[str, TeamTask]) -> None:
        for task in tasks.values():
            for dependency in task.dependencies:
                if dependency not in tasks:
                    raise TeamDataError(f"依赖任务不存在: {dependency}")
                if dependency == task.id:
                    raise TeamDataError("任务不能依赖自身")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise TeamDataError("任务依赖存在循环")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in tasks[task_id].dependencies:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in tasks:
            visit(task_id)

    def _recompute(self, tasks: dict[str, TeamTask]) -> dict[str, TeamTask]:
        updated = dict(tasks)
        for task_id, task in tasks.items():
            if task.status not in {"pending", "blocked"}:
                continue
            incomplete = [dep for dep in task.dependencies if tasks[dep].status != "completed"]
            if incomplete:
                failed = [dep for dep in incomplete if tasks[dep].status in {"failed", "cancelled"}]
                reason = (
                    f"依赖失败或取消: {', '.join(failed)}"
                    if failed
                    else f"等待依赖完成: {', '.join(incomplete)}"
                )
                updated[task_id] = replace(task, status="blocked", blocked_reason=reason)
            else:
                updated[task_id] = replace(task, status="pending", blocked_reason=None)
        return updated

    def _require_task(self, tasks: dict[str, TeamTask], task_id: str) -> TeamTask:
        try:
            return tasks[task_id]
        except KeyError as exc:
            raise TeamDataError(f"未知团队任务: {task_id}") from exc

    async def _validate_actor(self, actor: TeamActor) -> None:
        if actor.team_name != self.team_name:
            raise TeamDataError("操作者不属于当前团队")
        if actor.kind == "lead":
            if actor.name != "lead":
                raise TeamDataError("Lead 身份无效")
            return
        member = await self.store.get_member(self.team_name, actor.name)
        if member.status == "terminated":
            raise TeamDataError(f"团队成员已终止: {actor.name}")
        if Path(member.worktree_cwd).resolve() != actor.cwd.resolve():
            raise TeamDataError("成员身份与花名册工作目录不匹配")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
