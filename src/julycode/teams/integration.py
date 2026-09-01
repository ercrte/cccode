from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from julycode.errors import redact_secret
from julycode.teams.locking import AtomicJsonFile, FileLock, LockToken
from julycode.teams.models import (
    IntegratedTaskRecord,
    IntegrationFailure,
    IntegrationIntent,
    IntegrationRoundRecord,
    TaskAttemptRef,
    TaskResult,
    TeamActor,
    TeamDataError,
    TeamIntegrationFinalizeResult,
    TeamIntegrationState,
    TeamIntegrationSummary,
    TeamMemberRecord,
    TeamTask,
    integration_state_from_dict,
)
from julycode.teams.paths import TeamPaths
from julycode.teams.store import TeamStore
from julycode.worktrees.git import GitClient
from julycode.worktrees.manager import WorktreeManager
from julycode.worktrees.models import WorktreeError, WorktreeLease


class IntegrationTaskPort(Protocol):
    async def list(self, status: str | None = None) -> tuple[TeamTask, ...]: ...

    async def get(self, task_id: str) -> TeamTask: ...

    async def complete_recovered(
        self,
        task_id: str,
        attempt: int,
        result: str,
        commit: str,
    ) -> TeamTask: ...


class TeamIntegrationTransaction:
    def __init__(
        self,
        state: TeamIntegrationState,
        file: AtomicJsonFile,
    ) -> None:
        self._state = state
        self._file = file

    @property
    def state(self) -> TeamIntegrationState:
        return self._state

    def replace(self, state: TeamIntegrationState) -> None:
        """调用方已经持锁，直接做原子替换，避免锁重入。"""
        updated = replace(state, schema_version=1, revision=self._state.revision + 1)
        self._file._replace_unlocked(asdict(updated))
        self._state = updated


class TeamIntegrationStore:
    def __init__(self, team_name: str, team_store: TeamStore) -> None:
        self.team_name = team_name
        self.team_store = team_store
        self.paths = TeamPaths.for_team(team_name, base=team_store.root)
        self.lock = FileLock(self.paths.integration_lock, team_store.config)
        self.file = AtomicJsonFile(self.paths.integration_file, self.lock)

    async def load_or_create(self) -> TeamIntegrationState:
        token = await self.lock.acquire()
        try:
            if not self.paths.integration_file.exists():
                initial = TeamIntegrationState(1, 1, 1, None, ())
                self.file._replace_unlocked(asdict(initial))
                return initial
            return integration_state_from_dict(await self.file.read())
        finally:
            await self.lock.release(token)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[TeamIntegrationTransaction]:
        token: LockToken = await self.lock.acquire()
        try:
            if not self.paths.integration_file.exists():
                initial = TeamIntegrationState(1, 1, 1, None, ())
                self.file._replace_unlocked(asdict(initial))
            state = integration_state_from_dict(await self.file.read())
            yield TeamIntegrationTransaction(state, self.file)
        finally:
            await self.lock.release(token)


class TeamIntegrationService:
    def __init__(
        self,
        team_name: str,
        main_cwd: Path,
        store: TeamStore,
        worktrees: WorktreeManager,
        *,
        git: GitClient | None = None,
    ) -> None:
        self.team_name = team_name
        self.main_cwd = main_cwd.resolve()
        self.store = store
        self.worktrees = worktrees
        self.git = git or worktrees.git
        self.state_store = TeamIntegrationStore(team_name, store)
        self.owner_prefix = hashlib.sha256(
            f"{store.repository_layout().repository_id}\0{team_name}".encode("utf-8")
        ).hexdigest()[:20]
        self._lease: WorktreeLease | None = None

    async def assign_round(self) -> int:
        async with self.state_store.transaction() as transaction:
            state = transaction.state
            if state.current is not None:
                return state.current.number
            now = _now()
            round_record = IntegrationRoundRecord(
                number=state.next_round,
                phase="active",
                target_branch=None,
                base_commit=None,
                integration_owner_id=None,
                integration_root=None,
                integration_branch=None,
                integration_head=None,
                accepted=(),
                intent=None,
                failure=None,
                started_at=now,
                updated_at=now,
            )
            transaction.replace(
                replace(state, current=round_record, next_round=round_record.number + 1)
            )
            return round_record.number

    async def prepare_code_claim(self, member: TeamMemberRecord, task: TeamTask) -> str:
        async with self.state_store.transaction() as transaction:
            current = self._require_task_round(transaction.state, task)
            if current.target_branch is None:
                current = await self._capture_target(transaction, current)
            if current.failure is not None and current.phase == "blocked":
                raise TeamDataError(f"当前集成轮次已阻塞: {current.failure.message}")
            assert current.integration_head is not None
            try:
                await self._validate_member_worktree(member)
                member_head = await self.git.head_commit(cwd=Path(member.worktree_cwd))
                if not await self.git.is_ancestor(
                    cwd=Path(member.worktree_cwd),
                    ancestor=member_head,
                    descendant=current.integration_head,
                ):
                    raise TeamDataError("成员分支包含未登记的额外提交，不能同步到团队基线")
                if member_head != current.integration_head:
                    await self.git.fast_forward(
                        cwd=Path(member.worktree_cwd), target=current.integration_head
                    )
                await self.store.update_member_sync(
                    self.team_name,
                    member.name,
                    status="current",
                    head=current.integration_head,
                    error=None,
                )
                return current.integration_head
            except Exception as exc:
                message = _safe_error(exc)
                await self.store.update_member_sync(
                    self.team_name,
                    member.name,
                    status="blocked",
                    head=member.sync_head,
                    error=message,
                )
                raise TeamDataError(f"成员 Worktree 同步失败: {message}") from exc

    async def integrate_code_task(
        self,
        member: TeamActor,
        task: TeamTask,
        result: TaskResult,
        complete_task: Callable[[str], Awaitable[TeamTask]],
    ) -> TeamTask:
        if member.kind != "member":
            raise TeamDataError("只有成员代码任务可以进入自动集成")
        if not result.commit:
            raise TeamDataError("代码任务缺少结果 commit")
        async with self.state_store.transaction() as transaction:
            current = self._require_task_round(transaction.state, task)
            if current.target_branch is None or current.integration_head is None:
                raise TeamDataError("代码任务尚未建立内部集成基线")
            key = TaskAttemptRef(task.id, task.attempt)
            accepted = self._accepted(current, key)
            if accepted is not None:
                if accepted.source_commit != result.commit:
                    raise TeamDataError("相同任务尝试已接受其他提交")
                return await complete_task(result.commit)
            member_record = await self.store.get_member(self.team_name, member.name)
            source = await self._validate_task_source(member, member_record, task, result.commit, current)
            lease = await self._integration_lease(current)
            before = await self.git.head_commit(cwd=lease.root)
            if before != current.integration_head:
                self._block(transaction, current, "recovery", "内部集成 HEAD 与持久状态不一致")
                raise TeamDataError("内部集成 HEAD 与持久状态不一致，需人工检查")
            intent = IntegrationIntent(
                kind="merge_task",
                task=key,
                member_name=member.name,
                source_branch=member_record.branch,
                source_commit=source,
                expected_head=before,
                result_text=result.result.strip(),
                started_at=_now(),
            )
            current = replace(
                current,
                phase="integrating",
                intent=intent,
                failure=None,
                updated_at=_now(),
            )
            transaction.replace(replace(transaction.state, current=current))
            message = self._merge_message(current.number, task, member.name, source)
            outcome = await self.git.merge_no_ff(cwd=lease.root, source=source, message=message)
            if outcome.status == "conflicted":
                failure = IntegrationFailure(
                    stage="merge",
                    message="任务改动与当前内部基线冲突",
                    task=key,
                    member_name=member.name,
                    commit=source,
                    conflict_paths=outcome.conflict_paths,
                    occurred_at=_now(),
                )
                current = replace(
                    current,
                    phase="blocked",
                    intent=None,
                    failure=failure,
                    updated_at=_now(),
                )
                transaction.replace(replace(transaction.state, current=current))
                paths = ", ".join(outcome.conflict_paths) or "未知路径"
                raise TeamDataError(
                    f"内部集成冲突: {paths}；成员现场 {member.cwd}；内部现场 {lease.root}"
                )
            if outcome.status == "failed":
                failure = IntegrationFailure(
                    stage="merge",
                    message=outcome.detail or "内部合并失败且无法确认安全回滚",
                    task=key,
                    member_name=member.name,
                    commit=source,
                    conflict_paths=outcome.conflict_paths,
                    occurred_at=_now(),
                )
                current = replace(current, phase="blocked", failure=failure, updated_at=_now())
                transaction.replace(replace(transaction.state, current=current))
                raise TeamDataError(f"内部集成失败，现场已保留: {failure.message}")

            try:
                finished = await complete_task(source)
            except Exception:
                # intent 保留，恢复逻辑将依据实际 merge commit 补齐任务状态。
                raise
            record = IntegratedTaskRecord(
                task=key,
                member_name=member.name,
                source_branch=member_record.branch,
                source_commit=source,
                previous_head=before,
                integration_head=outcome.head_after,
                integrated_at=_now(),
            )
            current = replace(
                current,
                phase="active",
                integration_head=outcome.head_after,
                accepted=(*current.accepted, record),
                intent=None,
                failure=None,
                updated_at=_now(),
            )
            transaction.replace(replace(transaction.state, current=current))
            return finished

    async def validate_task_delete(self, task: TeamTask) -> None:
        state = await self.state_store.load_or_create()
        current = state.current
        if current is None or task.integration_round != current.number:
            return
        if self._accepted(current, TaskAttemptRef(task.id, task.attempt)) is not None:
            raise TeamDataError("当前未发布轮次中的已集成代码任务不能删除")

    async def recover(self, tasks: IntegrationTaskPort) -> TeamIntegrationSummary:
        async with self.state_store.transaction() as transaction:
            current = transaction.state.current
            if current is None:
                current = await self._import_legacy_completed(transaction, tasks)
            if current is None or current.target_branch is None:
                return await self._summary(transaction.state)
            try:
                lease = await self._integration_lease(current)
                if not await self.git.is_clean(cwd=lease.root) and await self.git.operation(cwd=lease.root) == "none":
                    raise TeamDataError("内部集成 Worktree 不干净")
                actual = await self.git.head_commit(cwd=lease.root)
                intent = current.intent
                if intent is None:
                    if actual != current.integration_head or await self.git.operation(cwd=lease.root) != "none":
                        raise TeamDataError("内部集成 Git 事实与持久状态不一致")
                    if current.phase == "published":
                        await self._archive_recovered_publish(transaction, current, lease)
                elif intent.kind == "merge_task":
                    current = await self._recover_merge(transaction, current, actual, tasks)
                else:
                    current = await self._recover_publish(transaction, current)
                    if current.phase == "published" and current.integration_head is not None:
                        await self._archive_recovered_publish(transaction, current, lease)
                return await self._summary(transaction.state)
            except Exception as exc:
                latest = transaction.state.current or current
                if latest is not None and not (
                    latest.phase == "blocked" and latest.failure is not None
                ):
                    self._block(transaction, latest, "recovery", _safe_error(exc))
                return await self._summary(transaction.state)

    async def finalize(self, tasks: tuple[TeamTask, ...]) -> TeamIntegrationFinalizeResult:
        async with self.state_store.transaction() as transaction:
            current = transaction.state.current
            if current is None:
                summary = await self._summary(transaction.state)
                if summary.phase == "published":
                    return TeamIntegrationFinalizeResult(
                        "published",
                        summary,
                        f"最近一轮已发布到 {summary.target_branch}，提交 {summary.integration_head}",
                    )
                return TeamIntegrationFinalizeResult("not_needed", summary, "当前没有待发布轮次")
            round_tasks = tuple(task for task in tasks if task.integration_round == current.number)
            failed = tuple(task for task in round_tasks if task.status in {"failed", "cancelled"})
            if failed:
                summary = await self._summary(transaction.state)
                detail = ", ".join(f"{task.id}:{task.status}" for task in failed)
                return TeamIntegrationFinalizeResult("blocked", summary, f"存在失败或取消任务: {detail}")
            waiting = tuple(task for task in round_tasks if task.status != "completed")
            if waiting:
                summary = await self._summary(transaction.state)
                detail = ", ".join(f"{task.id}:{task.status}" for task in waiting)
                return TeamIntegrationFinalizeResult("waiting", summary, f"仍有任务未完成: {detail}")
            code_tasks = tuple(task for task in round_tasks if task.kind == "code")
            if not code_tasks:
                archived = replace(current, phase="not_needed", updated_at=_now(), published_at=_now())
                transaction.replace(
                    replace(transaction.state, current=None, history=(*transaction.state.history, archived))
                )
                summary = await self._summary_for(archived)
                return TeamIntegrationFinalizeResult("not_needed", summary, "本轮只有研究任务，无需 Git 发布")
            missing = [
                task.id
                for task in code_tasks
                if self._accepted(current, TaskAttemptRef(task.id, task.attempt)) is None
            ]
            if missing:
                summary = await self._summary(transaction.state)
                return TeamIntegrationFinalizeResult(
                    "blocked", summary, f"完成任务尚未进入内部基线: {', '.join(missing)}"
                )
            if current.failure is not None:
                summary = await self._summary(transaction.state)
                return TeamIntegrationFinalizeResult("blocked", summary, current.failure.message)
            try:
                lease = await self._publish_preflight(current)
            except Exception as exc:
                failure = IntegrationFailure("publish", _safe_error(exc), occurred_at=_now())
                current = replace(current, phase="blocked", failure=failure, updated_at=_now())
                transaction.replace(replace(transaction.state, current=current))
                summary = await self._summary(transaction.state)
                return TeamIntegrationFinalizeResult("blocked", summary, failure.message)
            assert current.integration_head is not None
            intent = IntegrationIntent(
                kind="publish",
                task=None,
                member_name=None,
                source_branch=None,
                source_commit=None,
                expected_head=current.integration_head,
                result_text=None,
                started_at=_now(),
            )
            current = replace(current, phase="publishing", intent=intent, updated_at=_now())
            transaction.replace(replace(transaction.state, current=current))
            try:
                published_head = await self.git.fast_forward(
                    cwd=self.main_cwd, target=current.integration_head
                )
            except Exception as exc:
                # publish intent 保留，重启后根据 Lead HEAD 判定前后边界。
                failure = IntegrationFailure("publish", _safe_error(exc), occurred_at=_now())
                current = replace(current, phase="blocked", failure=failure, updated_at=_now())
                transaction.replace(replace(transaction.state, current=current))
                summary = await self._summary(transaction.state)
                return TeamIntegrationFinalizeResult("blocked", summary, failure.message)
            current = replace(
                current,
                phase="published",
                intent=None,
                failure=None,
                integration_head=published_head,
                updated_at=_now(),
                published_at=_now(),
            )
            transaction.replace(replace(transaction.state, current=current))
            warnings = await self._sync_members(published_head)
            disposition = await self.worktrees.delete_merged(lease, merged_into=published_head)
            if disposition.status != "cleaned":
                warnings.append(f"内部 Worktree 清理失败: {disposition.reason}")
                current = replace(
                    current,
                    failure=IntegrationFailure("cleanup", disposition.reason, occurred_at=_now()),
                    updated_at=_now(),
                )
            self._lease = None
            transaction.replace(
                replace(transaction.state, current=None, history=(*transaction.state.history, current))
            )
            summary = await self._summary_for(current, tuple(warnings))
            message = f"已一次性发布到 {current.target_branch}，提交 {published_head}"
            if warnings:
                message += "；警告: " + "；".join(warnings)
            return TeamIntegrationFinalizeResult("published", summary, message)

    async def snapshot(self) -> TeamIntegrationSummary:
        return await self._summary(await self.state_store.load_or_create())

    async def close(self) -> None:
        if self._lease is not None:
            await self.worktrees.release(self._lease)
            self._lease = None

    async def _capture_target(
        self,
        transaction: TeamIntegrationTransaction,
        current: IntegrationRoundRecord,
    ) -> IntegrationRoundRecord:
        branch = await self.git.current_branch(cwd=self.main_cwd)
        if branch is None:
            raise TeamDataError("Lead 处于游离 HEAD，不能启动代码集成轮次")
        if await self.git.operation(cwd=self.main_cwd) != "none":
            raise TeamDataError("Lead 存在进行中的 Git 操作")
        if not await self.git.is_clean(cwd=self.main_cwd):
            raise TeamDataError("Lead 工作树不干净")
        base = await self.git.head_commit(cwd=self.main_cwd)
        owner_id = self._owner_id(current.number)
        lease = await self.worktrees.acquire(
            task_id=owner_id,
            role="integration",
            retention="persistent",
            base_commit=base,
        )
        if (
            await self.git.current_branch(cwd=self.main_cwd) != branch
            or await self.git.head_commit(cwd=self.main_cwd) != base
            or not await self.git.is_clean(cwd=self.main_cwd)
        ):
            await self.worktrees.finish(lease)
            raise TeamDataError("创建内部 Worktree 期间 Lead 状态发生变化")
        self._lease = lease
        current = replace(
            current,
            target_branch=branch,
            base_commit=base,
            integration_owner_id=owner_id,
            integration_root=str(lease.root),
            integration_branch=lease.metadata.branch,
            integration_head=base,
            updated_at=_now(),
        )
        transaction.replace(replace(transaction.state, current=current))
        return current

    async def _import_legacy_completed(
        self,
        transaction: TeamIntegrationTransaction,
        tasks: IntegrationTaskPort,
    ) -> IntegrationRoundRecord | None:
        """旧团队没有集成记录时，只导入内部基线，不在 open 阶段发布 Lead。"""
        all_tasks = await tasks.list()
        historical = {
            (item.task.task_id, item.task.attempt)
            for round_record in transaction.state.history
            for item in round_record.accepted
        }
        candidates = [
            task
            for task in all_tasks
            if task.kind == "code"
            and task.status == "completed"
            and task.commit is not None
            and (task.id, task.attempt) not in historical
        ]
        if not candidates:
            return None
        round_number = min(task.integration_round for task in candidates)
        candidates = [task for task in candidates if task.integration_round == round_number]
        now = _now()
        current = IntegrationRoundRecord(
            number=round_number,
            phase="active",
            target_branch=None,
            base_commit=None,
            integration_owner_id=None,
            integration_root=None,
            integration_branch=None,
            integration_head=None,
            accepted=(),
            intent=None,
            failure=None,
            started_at=now,
            updated_at=now,
        )
        state = transaction.state
        transaction.replace(
            replace(state, current=current, next_round=max(state.next_round, round_number + 1))
        )
        current = await self._capture_target(transaction, current)
        lease = await self._integration_lease(current)
        team = await self.store.load(self.team_name)
        for task in self._topological_tasks(candidates):
            assert task.commit is not None and current.integration_head is not None
            if not await self.git.commit_exists(cwd=lease.root, commit=task.commit):
                self._block(
                    transaction,
                    current,
                    "recovery",
                    f"旧任务 {task.id} 的提交不存在: {task.commit}",
                )
                return transaction.state.current
            source = await self.git.head_object_id(cwd=lease.root, value=task.commit)
            before = await self.git.head_commit(cwd=lease.root)
            outcome = await self.git.merge_no_ff(
                cwd=lease.root,
                source=source,
                message=self._merge_message(
                    current.number, task, task.assignee or "legacy", source
                ),
            )
            if outcome.status not in {"merged", "already_integrated"}:
                failure = IntegrationFailure(
                    "recovery",
                    f"旧任务 {task.id} 导入失败: {outcome.detail or outcome.status}",
                    TaskAttemptRef(task.id, task.attempt),
                    task.assignee,
                    source,
                    outcome.conflict_paths,
                    _now(),
                )
                current = replace(
                    current, phase="blocked", failure=failure, updated_at=_now()
                )
                transaction.replace(replace(transaction.state, current=current))
                return current
            member = team.members.get(task.assignee or "")
            record = IntegratedTaskRecord(
                TaskAttemptRef(task.id, task.attempt),
                task.assignee or "legacy",
                member.branch if member is not None else f"legacy/{task.assignee or 'unknown'}",
                source,
                before,
                outcome.head_after,
                _now(),
            )
            current = replace(
                current,
                integration_head=outcome.head_after,
                accepted=(*current.accepted, record),
                updated_at=_now(),
            )
            transaction.replace(replace(transaction.state, current=current))
        return current

    def _topological_tasks(self, tasks: list[TeamTask]) -> tuple[TeamTask, ...]:
        by_id = {task.id: task for task in tasks}
        remaining = set(by_id)
        ordered: list[TeamTask] = []
        while remaining:
            ready = sorted(
                (
                    by_id[task_id]
                    for task_id in remaining
                    if all(dependency not in remaining for dependency in by_id[task_id].dependencies)
                ),
                key=lambda task: (task.created_at, task.id),
            )
            if not ready:
                raise TeamDataError("旧任务依赖图存在循环")
            ordered.extend(ready)
            remaining.difference_update(task.id for task in ready)
        return tuple(ordered)

    async def _validate_member_worktree(self, member: TeamMemberRecord) -> None:
        cwd = Path(member.worktree_cwd).resolve()
        root = Path(member.worktree_root).resolve()
        if not cwd.is_relative_to(root):
            raise TeamDataError("成员工作目录越过登记的 Worktree 根")
        if await self.git.current_branch(cwd=cwd) != member.branch:
            raise TeamDataError("成员当前分支与花名册不一致")
        if await self.git.operation(cwd=cwd) != "none":
            raise TeamDataError("成员 Worktree 存在进行中的 Git 操作")
        if not await self.git.is_clean(cwd=cwd):
            raise TeamDataError("成员 Worktree 存在未提交或未跟踪修改")

    async def _validate_task_source(
        self,
        actor: TeamActor,
        member: TeamMemberRecord,
        task: TeamTask,
        source: str,
        current: IntegrationRoundRecord,
    ) -> str:
        if task.kind != "code" or task.assignee != actor.name:
            raise TeamDataError("任务类型或负责人不匹配")
        if task.integration_round != current.number or task.status != "in_progress":
            raise TeamDataError("任务不属于当前轮次或不在执行中")
        if actor.cwd.resolve() != Path(member.worktree_cwd).resolve():
            raise TeamDataError("成员身份与登记 Worktree 不匹配")
        await self._validate_member_worktree(member)
        actual = await self.git.head_commit(cwd=actor.cwd)
        resolved = await self.git.head_object_id(cwd=actor.cwd, value=source)
        if actual != resolved:
            raise TeamDataError("结果 commit 必须等于成员分支当前 HEAD")
        if task.start_commit is None or resolved == task.start_commit:
            raise TeamDataError("结果 commit 必须位于任务领取基线之后")
        if not await self.git.is_ancestor(cwd=actor.cwd, ancestor=task.start_commit, descendant=resolved):
            raise TeamDataError("结果 commit 不属于当前任务的提交链")
        return resolved

    async def _integration_lease(self, current: IntegrationRoundRecord) -> WorktreeLease:
        if self._lease is not None:
            if str(self._lease.root) != current.integration_root:
                raise TeamDataError("活动内部 lease 与持久路径不一致")
            return self._lease
        if current.integration_owner_id is None or current.base_commit is None:
            raise TeamDataError("内部集成 Worktree 元数据不完整")
        lease = await self.worktrees.acquire(
            task_id=current.integration_owner_id,
            role="integration",
            retention="persistent",
            base_commit=current.base_commit,
        )
        if (
            str(lease.root) != current.integration_root
            or lease.metadata.branch != current.integration_branch
        ):
            await self.worktrees.release(lease)
            raise TeamDataError("内部集成 Worktree 恢复信息不匹配")
        self._lease = lease
        return lease

    async def _recover_merge(
        self,
        transaction: TeamIntegrationTransaction,
        current: IntegrationRoundRecord,
        actual: str,
        tasks: IntegrationTaskPort,
    ) -> IntegrationRoundRecord:
        intent = current.intent
        assert intent is not None and intent.kind == "merge_task" and intent.task is not None
        lease = await self._integration_lease(current)
        if actual == intent.expected_head:
            operation = await self.git.operation(cwd=lease.root)
            if operation == "merge":
                await self.git.abort_merge(cwd=lease.root)
                failure = IntegrationFailure(
                    "merge",
                    "恢复时发现未完成合并，已安全中止，请重新提交任务结果",
                    intent.task,
                    intent.member_name,
                    intent.source_commit,
                    occurred_at=_now(),
                )
                current = replace(current, phase="blocked", intent=None, failure=failure, updated_at=_now())
            elif operation == "none":
                current = replace(current, phase="active", intent=None, failure=None, updated_at=_now())
            else:
                raise TeamDataError(f"内部 Worktree 存在意外 Git 操作: {operation}")
            transaction.replace(replace(transaction.state, current=current))
            return current
        if intent.source_commit is None or intent.result_text is None:
            raise TeamDataError("merge intent 缺少恢复任务所需字段")
        parents = await self.git.commit_parents(cwd=lease.root, commit=actual)
        if parents != (intent.expected_head, intent.source_commit):
            raise TeamDataError("恢复时发现未知内部合并提交")
        await tasks.complete_recovered(
            intent.task.task_id,
            intent.task.attempt,
            intent.result_text,
            intent.source_commit,
        )
        record = IntegratedTaskRecord(
            intent.task,
            intent.member_name or "unknown",
            intent.source_branch or "unknown",
            intent.source_commit,
            intent.expected_head,
            actual,
            _now(),
        )
        if self._accepted(current, intent.task) is None:
            current = replace(current, accepted=(*current.accepted, record))
        current = replace(
            current,
            phase="active",
            integration_head=actual,
            intent=None,
            failure=None,
            updated_at=_now(),
        )
        transaction.replace(replace(transaction.state, current=current))
        return current

    async def _recover_publish(
        self,
        transaction: TeamIntegrationTransaction,
        current: IntegrationRoundRecord,
    ) -> IntegrationRoundRecord:
        intent = current.intent
        assert intent is not None and intent.kind == "publish"
        branch = await self.git.current_branch(cwd=self.main_cwd)
        head = await self.git.head_commit(cwd=self.main_cwd)
        if branch != current.target_branch or not await self.git.is_clean(cwd=self.main_cwd):
            raise TeamDataError("恢复发布时 Lead 分支或工作区不符合记录")
        if head == current.base_commit:
            current = replace(current, phase="ready", intent=None, failure=None, updated_at=_now())
        elif head == intent.expected_head:
            current = replace(
                current,
                phase="published",
                intent=None,
                failure=None,
                integration_head=head,
                published_at=_now(),
                updated_at=_now(),
            )
        else:
            raise TeamDataError("恢复发布时 Lead HEAD 既非基线也非预期结果")
        transaction.replace(replace(transaction.state, current=current))
        return current

    async def _archive_recovered_publish(
        self,
        transaction: TeamIntegrationTransaction,
        current: IntegrationRoundRecord,
        lease: WorktreeLease,
    ) -> None:
        assert current.integration_head is not None
        if (
            await self.git.current_branch(cwd=self.main_cwd) != current.target_branch
            or await self.git.head_commit(cwd=self.main_cwd) != current.integration_head
            or not await self.git.is_clean(cwd=self.main_cwd)
            or await self.git.operation(cwd=self.main_cwd) != "none"
        ):
            raise TeamDataError("恢复已发布轮次时 Lead Git 事实不一致")
        warnings = await self._sync_members(current.integration_head)
        disposition = await self.worktrees.delete_merged(
            lease, merged_into=current.integration_head
        )
        if disposition.status != "cleaned":
            warnings.append(f"内部 Worktree 清理失败: {disposition.reason}")
            current = replace(
                current,
                failure=IntegrationFailure("cleanup", disposition.reason, occurred_at=_now()),
            )
        self._lease = None
        transaction.replace(
            replace(
                transaction.state,
                current=None,
                history=(*transaction.state.history, current),
            )
        )

    async def _publish_preflight(self, current: IntegrationRoundRecord) -> WorktreeLease:
        if await self.git.current_branch(cwd=self.main_cwd) != current.target_branch:
            raise TeamDataError("Lead 目标分支已变化")
        if await self.git.head_commit(cwd=self.main_cwd) != current.base_commit:
            raise TeamDataError("Lead HEAD 已在轮次外变化")
        if await self.git.operation(cwd=self.main_cwd) != "none":
            raise TeamDataError("Lead 存在进行中的 Git 操作")
        if not await self.git.is_clean(cwd=self.main_cwd):
            raise TeamDataError("Lead 工作树不干净")
        lease = await self._integration_lease(current)
        if await self.git.operation(cwd=lease.root) != "none" or not await self.git.is_clean(cwd=lease.root):
            raise TeamDataError("内部集成 Worktree 状态不安全")
        if await self.git.head_commit(cwd=lease.root) != current.integration_head:
            raise TeamDataError("内部集成 HEAD 与记录不一致")
        return lease

    async def _sync_members(self, published_head: str) -> list[str]:
        warnings: list[str] = []
        team = await self.store.load(self.team_name)
        for member in team.members.values():
            if member.status != "idle":
                warning = f"{member.name} 正在运行，等待同步"
                await self.store.update_member_sync(
                    self.team_name, member.name, status="pending", head=member.sync_head, error=warning
                )
                warnings.append(warning)
                continue
            try:
                await self._validate_member_worktree(member)
                head = await self.git.head_commit(cwd=Path(member.worktree_cwd))
                if not await self.git.is_ancestor(
                    cwd=Path(member.worktree_cwd), ancestor=head, descendant=published_head
                ):
                    raise TeamDataError("成员分支不能快进到发布结果")
                if head != published_head:
                    await self.git.fast_forward(cwd=Path(member.worktree_cwd), target=published_head)
                await self.store.update_member_sync(
                    self.team_name, member.name, status="current", head=published_head, error=None
                )
            except Exception as exc:
                warning = f"{member.name} 同步失败: {_safe_error(exc)}"
                await self.store.update_member_sync(
                    self.team_name, member.name, status="blocked", head=member.sync_head, error=warning
                )
                warnings.append(warning)
        return warnings

    def _require_task_round(
        self, state: TeamIntegrationState, task: TeamTask
    ) -> IntegrationRoundRecord:
        current = state.current
        if current is None or task.integration_round != current.number:
            raise TeamDataError("任务不属于当前集成轮次")
        return current

    def _accepted(
        self, current: IntegrationRoundRecord, key: TaskAttemptRef
    ) -> IntegratedTaskRecord | None:
        return next((item for item in current.accepted if item.task == key), None)

    def _block(
        self,
        transaction: TeamIntegrationTransaction,
        current: IntegrationRoundRecord,
        stage: str,
        message: str,
    ) -> None:
        failure = IntegrationFailure(stage, message, occurred_at=_now())  # type: ignore[arg-type]
        blocked = replace(current, phase="blocked", failure=failure, updated_at=_now())
        transaction.replace(replace(transaction.state, current=blocked))

    async def _summary(self, state: TeamIntegrationState) -> TeamIntegrationSummary:
        if state.current is None:
            if state.history:
                return await self._summary_for(state.history[-1])
            return TeamIntegrationSummary(None, "idle", None, None, None, (), None)
        return await self._summary_for(state.current)

    async def _summary_for(
        self,
        current: IntegrationRoundRecord,
        extra_warnings: tuple[str, ...] = (),
    ) -> TeamIntegrationSummary:
        team = await self.store.load(self.team_name)
        warnings = tuple(
            f"{member.name}: {member.sync_error or member.sync_status}"
            for member in team.members.values()
            if member.sync_status != "current"
        )
        return TeamIntegrationSummary(
            current.number,
            current.phase,
            current.target_branch,
            current.base_commit,
            current.integration_head,
            tuple(item.task for item in current.accepted),
            current.failure,
            (*warnings, *extra_warnings),
        )

    def _owner_id(self, round_number: int) -> str:
        return f"team-integration-{self.owner_prefix}-{round_number}"

    def _merge_message(
        self, round_number: int, task: TeamTask, member_name: str, source: str
    ) -> str:
        return (
            f"JulyCode integrate {self.team_name} task {task.id}\n\n"
            f"JulyCode-Team: {self.team_name}\n"
            f"JulyCode-Round: {round_number}\n"
            f"JulyCode-Task: {task.id}\n"
            f"JulyCode-Attempt: {task.attempt}\n"
            f"JulyCode-Member: {member_name}\n"
            f"JulyCode-Source: {source}"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(exc: Exception) -> str:
    return redact_secret(str(exc))[:1000]
