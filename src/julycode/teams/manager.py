from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from julycode.agent import AgentLoopController, CompletionDecision
from julycode.providers.base import ChatMessage
from julycode.session import ChatSession
from julycode.teams.approvals import ApprovalService
from julycode.teams.events import TeamOutboxDispatcher
from julycode.teams.integration import TeamIntegrationService
from julycode.teams.mailbox import MailboxService
from julycode.teams.models import (
    BroadcastResult,
    DeliveryResult,
    MemberSpawnRequest,
    MemberSummary,
    MessageDraft,
    TaskSummary,
    TeamActor,
    TeamConfig,
    TeamDataError,
    TeamEventSnapshot,
    TeamPromptContext,
    TeamSnapshot,
    TeamSummary,
)
from julycode.teams.policy import ApprovalGate, TeamAudienceGate, TeamMemberRoleGate
from julycode.teams.store import TeamStore
from julycode.teams.tasks import TaskService
from julycode.tools.base import RuntimePrincipal
from julycode.worktrees.manager import WorktreeManager
from julycode.worktrees.models import WorktreeConfig

if TYPE_CHECKING:
    from julycode.subagents.models import SubAgentRoleDefinition
    from julycode.teams.runtime import TeamRuntimeSupervisor


@dataclass
class TeamServices:
    integration: TeamIntegrationService
    tasks: TaskService
    approvals: ApprovalService
    mailbox: MailboxService
    dispatcher: TeamOutboxDispatcher


class TeamManager:
    def __init__(
        self,
        main_cwd: Path,
        config: TeamConfig | None = None,
        *,
        store: TeamStore | None = None,
        runtime: TeamRuntimeSupervisor | None = None,
        worktrees: WorktreeManager | None = None,
    ) -> None:
        self.main_cwd = main_cwd.resolve()
        self.config = config or TeamConfig()
        self.store = store or TeamStore(self.main_cwd, self.config)
        self.runtime = runtime
        self.worktrees = worktrees or WorktreeManager(self.main_cwd, WorktreeConfig())
        self.active_team: str | None = None
        self._services: dict[str, TeamServices] = {}
        self._event = asyncio.Event()
        self._prompt_cache: dict[tuple[str, str], TeamPromptContext] = {}
        self._role_bodies: dict[tuple[str, str], str] = {}

    def set_runtime(self, runtime: TeamRuntimeSupervisor) -> None:
        self.runtime = runtime

    def set_member_role_body(self, team_name: str, member_name: str, body: str) -> None:
        self._role_bodies[(team_name, member_name)] = body

    async def create_team(self, name: str) -> TeamSnapshot:
        if not self.config.enabled:
            raise TeamDataError("团队功能已关闭")
        if self.active_team is not None:
            raise TeamDataError(f"当前已激活团队: {self.active_team}")
        record = await self.store.create(name)
        self.active_team = record.name
        services = self._service(record.name)
        await services.integration.recover(services.tasks)
        await self._refresh_prompt(record.name)
        return TeamSnapshot(record, integration=await services.integration.snapshot())

    async def list_teams(self) -> tuple[TeamSummary, ...]:
        return await self.store.list()

    async def open_team(self, name: str) -> TeamSnapshot:
        if not self.config.enabled:
            raise TeamDataError("团队功能已关闭")
        if self.active_team is not None and self.active_team != name:
            raise TeamDataError(f"当前已激活团队: {self.active_team}")
        record = await self.store.load(name)
        self.active_team = record.name
        services = self._service(record.name)
        await services.integration.recover(services.tasks)
        recovery = await self.store.reconcile_interrupted(record.name)
        for task_id in recovery.released_task_ids:
            await services.tasks.release_interrupted(task_id, "成员进程中断，任务等待重新指派。")
        await services.approvals.reconcile()
        await services.dispatcher.flush()
        await self._refresh_prompt(record.name)
        return TeamSnapshot(
            await self.store.load(record.name),
            await services.tasks.list(),
            integration=await services.integration.snapshot(),
        )

    async def close_team(self) -> None:
        self.active_team = None

    async def status(self) -> TeamSnapshot:
        team_name = self._require_active()
        services = self._service(team_name)
        record = await self.store.load(team_name)
        lead = TeamActor(team_name, "lead", "lead", self.main_cwd)
        return TeamSnapshot(
            record,
            await services.tasks.list(),
            len(await services.mailbox.unread(lead)),
            await services.integration.snapshot(),
        )

    async def actor_for(self, principal: RuntimePrincipal) -> TeamActor:
        if principal.kind == "main":
            team_name = self._require_active()
            return TeamActor(team_name, "lead", "lead", self.main_cwd)
        if principal.kind != "team_member" or not principal.team_name or not principal.actor_name:
            raise TeamDataError("当前运行身份不是团队参与者")
        member = await self.store.get_member(principal.team_name, principal.actor_name)
        if member.status == "terminated":
            raise TeamDataError("当前成员已终止")
        return TeamActor(principal.team_name, member.name, "member", Path(member.worktree_cwd))

    async def spawn_member(self, request: MemberSpawnRequest):
        team_name = self._require_active()
        if self.runtime is None:
            raise TeamDataError("团队成员运行时未初始化")
        member = await self.runtime.create_member(team_name, request)
        await self._refresh_prompt(team_name)
        self.notify_event()
        return member

    async def terminate_member(self, name: str):
        team_name = self._require_active()
        if self.runtime is None:
            raise TeamDataError("团队成员运行时未初始化")
        member = await self.runtime.terminate(team_name, name)
        await self._refresh_prompt(team_name)
        self.notify_event()
        return member

    async def send_message(self, actor: TeamActor, draft: MessageDraft) -> DeliveryResult:
        services = self._service(actor.team_name)
        result = await services.mailbox.send(actor, draft)
        await services.dispatcher.flush()
        if self.runtime is not None and draft.recipient and draft.recipient != "lead":
            await self.runtime.wake(actor.team_name, draft.recipient)
        self.notify_event()
        await self._refresh_prompt(actor.team_name)
        return result

    async def broadcast(self, actor: TeamActor, draft: MessageDraft) -> BroadcastResult:
        services = self._service(actor.team_name)
        result = await services.mailbox.broadcast(actor, draft)
        if self.runtime is not None:
            for delivery in result.deliveries:
                if delivery.success and delivery.recipient != "lead":
                    await self.runtime.wake(actor.team_name, delivery.recipient)
        self.notify_event()
        return result

    async def wait_for_event(self, timeout_seconds: float | None = None) -> TeamEventSnapshot:
        team_name = self._require_active()
        services = self._service(team_name)
        await services.dispatcher.flush()
        timeout = timeout_seconds or self.config.wait_timeout_seconds
        timed_out = False
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
        self._event.clear()
        record = await self.store.load(team_name)
        lead = TeamActor(team_name, "lead", "lead", self.main_cwd)
        snapshot = TeamEventSnapshot(
            team_name,
            await services.tasks.list(),
            tuple(record.members.values()),
            await services.mailbox.unread(lead),
            timed_out,
            await services.integration.snapshot(),
        )
        await self._refresh_prompt(team_name)
        return snapshot

    def notify_event(self) -> None:
        self._event.set()

    def tool_gates(self, principal: RuntimePrincipal):
        return (TeamAudienceGate(principal, lambda: self.active_team, lambda: self.config.enabled),)

    def member_tool_gates(
        self,
        principal: RuntimePrincipal,
        role: SubAgentRoleDefinition,
        approval_state: dict[str, bool],
    ):
        return (
            TeamAudienceGate(principal, lambda: self.active_team, lambda: self.config.enabled),
            TeamMemberRoleGate(
                frozenset(role.frontmatter.tools_allow),
                frozenset(role.frontmatter.tools_deny),
            ),
            ApprovalGate(lambda: approval_state.get("allowed", False)),
        )

    def prompt_context(self, principal: RuntimePrincipal) -> TeamPromptContext | None:
        if principal.kind == "main":
            if self.active_team is None:
                return None
            return self._prompt_cache.get((self.active_team, "lead"))
        if principal.kind == "team_member" and principal.team_name and principal.actor_name:
            return self._prompt_cache.get((principal.team_name, principal.actor_name))
        return None

    def loop_controller(self, principal: RuntimePrincipal) -> AgentLoopController | None:
        if principal.kind == "main":
            return _MainLoopController(self)
        return None

    async def refresh_prompt_context(self, team_name: str) -> None:
        await self._refresh_prompt(team_name)

    async def shutdown(self) -> None:
        if self.runtime is not None:
            await self.runtime.shutdown()
        for services in self._services.values():
            await services.integration.close()

    def _service(self, team_name: str) -> TeamServices:
        existing = self._services.get(team_name)
        if existing is not None:
            return existing
        integration = TeamIntegrationService(
            team_name, self.main_cwd, self.store, self.worktrees
        )
        tasks = TaskService(team_name, self.store, integration=integration)
        approvals = ApprovalService(team_name, self.store, tasks)
        mailbox = MailboxService(team_name, self.store, approvals)
        dispatcher = TeamOutboxDispatcher(team_name, mailbox, (self.store, tasks, approvals))
        services = TeamServices(integration, tasks, approvals, mailbox, dispatcher)
        self._services[team_name] = services
        return services

    async def _refresh_prompt(self, team_name: str) -> None:
        record = await self.store.load(team_name)
        services = self._service(team_name)
        tasks = await services.tasks.list()
        integration = await services.integration.snapshot()
        roster = tuple(
            MemberSummary(
                member.name,
                member.role,
                member.status,
                member.current_task_id,
                member.sync_status,
                member.sync_head,
                member.sync_error,
            )
            for member in record.members.values()
        )
        summaries = tuple(TaskSummary(task.id, task.title, task.status, task.assignee, task.dependencies) for task in tasks)
        lead = TeamActor(team_name, "lead", "lead", self.main_cwd)
        self._prompt_cache[(team_name, "lead")] = TeamPromptContext(
            team_name,
            "lead",
            "lead",
            roster,
            summaries,
            len(await services.mailbox.unread(lead)),
            integration=integration,
        )
        for member in record.members.values():
            actor = TeamActor(team_name, member.name, "member", Path(member.worktree_cwd))
            current_task = await services.tasks.get(member.current_task_id) if member.current_task_id else None
            approval = await services.approvals.current_for_member(actor)
            unread_count = 0 if member.status == "terminated" else len(await services.mailbox.unread(actor))
            self._prompt_cache[(team_name, member.name)] = TeamPromptContext(
                team_name,
                "member",
                member.name,
                roster,
                summaries,
                unread_count,
                current_task,
                approval,
                self._role_bodies.get((team_name, member.name)),
                integration,
            )

    def _require_active(self) -> str:
        if self.active_team is None:
            raise TeamDataError("当前没有激活团队")
        return self.active_team


class _LeadLoopController:
    def __init__(self, manager: TeamManager, team_name: str) -> None:
        self.manager = manager
        self.team_name = team_name

    async def before_iteration(self, session: ChatSession) -> None:
        services = self.manager._service(self.team_name)
        await services.dispatcher.flush()
        actor = TeamActor(self.team_name, "lead", "lead", self.manager.main_cwd)
        unread = await services.mailbox.unread(actor)
        delivered = {
            str((message.metadata or {}).get("team_message_id"))
            for message in session.messages
            if (message.metadata or {}).get("team_message_id")
        }
        appended: list[str] = []
        for message in unread:
            if message.id not in delivered:
                session.append_user_message(
                    _render_message(message),
                    metadata={"team_message_id": message.id, "source": "team_mailbox"},
                )
            appended.append(message.id)
        if appended:
            await services.mailbox.acknowledge(actor, appended)
        await self.manager._refresh_prompt(self.team_name)

    async def review_completion(self, message: ChatMessage) -> CompletionDecision:
        services = self.manager._service(self.team_name)
        tasks = await services.tasks.list()
        active = [task for task in tasks if task.status in {"pending", "blocked", "in_progress", "awaiting_approval"}]
        if active:
            detail = ", ".join(f"{task.id}:{task.status}" for task in active)
            return CompletionDecision(
                False,
                message,
                f"团队仍有未完成任务（{detail}）。请处理事件或调用 team_wait，不要给出最终成功结论。",
            )
        failed = [task for task in tasks if task.status in {"failed", "cancelled"}]
        if failed:
            detail = "\n".join(f"- {task.id} {task.title}: {task.failure_reason or task.status}" for task in failed)
            return CompletionDecision(True, replace(message, content=f"团队目标尚未达成：\n{detail}"))
        finalized = await services.integration.finalize(tasks)
        if finalized.status == "waiting":
            return CompletionDecision(False, message, finalized.message)
        if finalized.status == "blocked":
            return CompletionDecision(
                True,
                replace(message, content=f"团队任务已完成，但自动发布被阻止：{finalized.message}"),
            )
        suffix = "\n\n" + finalized.message
        return CompletionDecision(True, replace(message, content=message.content.rstrip() + suffix))


def _render_message(message) -> str:
    return (
        f"<team_message id=\"{message.id}\" protocol=\"{message.protocol}\" "
        f"sender=\"{message.sender}\" task=\"{message.task_id or ''}\" "
        f"approval=\"{message.approval_id or ''}\" version=\"{message.plan_version or ''}\">\n"
        f"{message.body}\n</team_message>"
    )


class _MainLoopController:
    def __init__(self, manager: TeamManager) -> None:
        self.manager = manager

    async def before_iteration(self, session: ChatSession) -> None:
        if self.manager.active_team is None:
            return
        await _LeadLoopController(self.manager, self.manager.active_team).before_iteration(session)

    async def review_completion(self, message: ChatMessage) -> CompletionDecision:
        if self.manager.active_team is None:
            return CompletionDecision(True, message)
        return await _LeadLoopController(self.manager, self.manager.active_team).review_completion(message)
