from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from mewcode.agent import AgentLoopRunner, CompletionDecision
from mewcode.commands import AgentCommand
from mewcode.config import AgentConfig, AppConfig
from mewcode.context.manager import ContextManager
from mewcode.hooks.manager import HookManager
from mewcode.memory.manager import SessionMemoryManager
from mewcode.permissions.controller import create_permission_controller
from mewcode.permissions.models import PermissionConfig
from mewcode.providers.base import LLMProvider
from mewcode.session import ChatSession
from mewcode.skills.execution import ProviderResolver
from mewcode.subagents.cache import FileReadCache
from mewcode.subagents.models import SubAgentRoleDefinition
from mewcode.teams.locking import AtomicJsonFile, FileLock, ProcessLease
from mewcode.teams.models import (
    MemberSpawnRequest,
    OutboxEvent,
    TeamActor,
    TeamDataError,
    TeamMemberRecord,
    WakeResult,
)
from mewcode.teams.paths import TeamPaths, validate_member_name
from mewcode.teams.sessions import TeamMemberSessionStore
from mewcode.tools.base import RuntimePrincipal, ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry
from mewcode.worktrees import WorktreeLease, WorktreeManager

if TYPE_CHECKING:
    from mewcode.mcp.manager import McpManager
    from mewcode.teams.manager import TeamManager


class TeamMemberRunnerFactory:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        executor: ToolExecutor,
        config: AppConfig,
        provider: LLMProvider,
        provider_resolver: ProviderResolver,
        hook_manager: HookManager | None = None,
        mcp_manager: McpManager | None = None,
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.config = config
        self.provider = provider
        self.provider_resolver = provider_resolver
        self.hook_manager = hook_manager
        self.mcp_manager = mcp_manager

    def create_runner(
        self,
        *,
        session: ChatSession,
        member: TeamMemberRecord,
        role: SubAgentRoleDefinition,
        manager: TeamManager,
        controller: TeamMemberLoopController,
        approval_state: dict[str, bool],
    ) -> AgentLoopRunner:
        principal = RuntimePrincipal("team_member", manager.active_team or controller.team_name, member.name)
        read_cache = FileReadCache()
        context = ToolContext(
            cwd=Path(member.worktree_cwd),
            max_output_chars=self.executor.context.max_output_chars,
            read_cache=read_cache,
            principal=principal,
        )
        mode = self.config.permissions.mode
        if role.frontmatter.permission_mode != "inherit":
            mode = role.frontmatter.permission_mode
        provider = self.provider_resolver(self._model(role))
        memory = SessionMemoryManager(
            Path(member.worktree_cwd),
            replace(self.config.memory, auto_notes_enabled=False),
        )
        memory.load_runtime_context()
        hook_manager = None
        if self.hook_manager is not None:
            hook_manager = HookManager(self.hook_manager.config, self.hook_manager.action_runner)
        max_iterations = role.frontmatter.max_iterations or self.config.sub_agents.default_max_iterations or self.config.agent.max_iterations
        return AgentLoopRunner(
            session,
            provider,
            self.registry,
            ToolExecutor(self.registry, context),
            AgentConfig(max_iterations=max_iterations),
            permission_controller=create_permission_controller(Path(member.worktree_cwd), PermissionConfig(mode=mode)),  # type: ignore[arg-type]
            context_manager=ContextManager(self.config.context, Path(member.worktree_cwd), self.config.max_tokens),
            memory_manager=memory,
            provider_resolver=lambda _override: provider,
            hook_manager=hook_manager,
            file_read_cache=read_cache,
            tool_gates=manager.member_tool_gates(principal, role, approval_state),
            loop_controller=controller,
            team_prompt_provider=lambda: manager.prompt_context(principal),
            mcp_manager=self.mcp_manager,
        )

    def _model(self, role: SubAgentRoleDefinition) -> str | None:
        model = role.frontmatter.model
        if not model or model == "inherit":
            return None
        return self.config.sub_agents.model_aliases.get(model, model)


class TeamMemberLoopController:
    def __init__(
        self,
        *,
        manager: TeamManager,
        team_name: str,
        member_name: str,
        session_store: TeamMemberSessionStore,
        approval_state: dict[str, bool],
    ) -> None:
        self.manager = manager
        self.team_name = team_name
        self.member_name = member_name
        self.session_store = session_store
        self.approval_state = approval_state

    async def before_iteration(self, session: ChatSession) -> None:
        services = self.manager._service(self.team_name)
        actor = await self.manager.actor_for(RuntimePrincipal("team_member", self.team_name, self.member_name))
        self.approval_state["allowed"] = await services.approvals.can_mutate_project(actor)
        unread = await services.mailbox.unread(actor)
        acknowledged: list[str] = []
        for message in unread:
            self.session_store.append_external_message(session, message.id, _render_message(message))
            acknowledged.append(message.id)
        if acknowledged:
            await services.mailbox.acknowledge(actor, acknowledged)
        await self.manager.refresh_prompt_context(self.team_name)

    async def review_completion(self, message) -> CompletionDecision:
        return CompletionDecision(True, message)


class TeamRuntimeSupervisor:
    def __init__(
        self,
        *,
        manager: TeamManager,
        worktrees: WorktreeManager,
        runner_factory: TeamMemberRunnerFactory,
        role_provider: Callable[[str], SubAgentRoleDefinition | None],
    ) -> None:
        self.manager = manager
        self.store = manager.store
        self.worktrees = worktrees
        self.runner_factory = runner_factory
        self.role_provider = role_provider
        self._tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._process_leases: dict[tuple[str, str], ProcessLease] = {}
        self._worktree_leases: dict[tuple[str, str], WorktreeLease] = {}
        self._runners: dict[tuple[str, str], AgentLoopRunner] = {}
        self._wake_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._terminating: set[tuple[str, str]] = set()
        self._closing = False

    async def create_member(self, team_name: str, request: MemberSpawnRequest) -> TeamMemberRecord:
        name = validate_member_name(request.name)
        if request.backend != "coroutine":
            raise TeamDataError(f"本阶段不支持成员后端: {request.backend}")
        role = self.role_provider(request.role)
        if role is None:
            raise TeamDataError(f"未知子 Agent 角色: {request.role}")
        self.manager.set_member_role_body(team_name, name, role.body)
        try:
            await self.store.get_member(team_name, name)
        except TeamDataError:
            pass
        else:
            raise TeamDataError(f"团队成员已存在: {name}")
        owner_id = _worktree_owner_id(team_name, name)
        lease = await self.worktrees.acquire(task_id=owner_id, role="teams", retention="persistent")
        paths = TeamPaths.for_team(team_name, base=self.store.root)
        session_store = TeamMemberSessionStore(paths.session_file(name))
        session_store.create()
        await AtomicJsonFile(
            paths.mailbox_file(name), FileLock(paths.mailbox_lock(name), self.manager.config)
        ).replace({"schema_version": 1, "revision": 1, "messages": []})
        now = _now()
        member = TeamMemberRecord(
            name=name,
            role=request.role,
            backend="coroutine",
            require_approval=request.require_approval,
            status="idle",
            worktree_root=str(lease.root),
            worktree_cwd=str(lease.cwd),
            branch=lease.metadata.branch,
            worktree_owner_id=owner_id,
            session_path=str(paths.session_file(name)),
            current_task_id=None,
            pending_approval_id=None,
            created_at=now,
            updated_at=now,
            last_active_at=now,
        )
        try:
            await self.store.add_member(team_name, member)
        except Exception:
            await self.worktrees.release(lease)
            raise
        self._worktree_leases[(team_name, name)] = lease
        await self.manager.refresh_prompt_context(team_name)
        return member

    async def wake(self, team_name: str, member_name: str) -> WakeResult:
        key = (team_name, member_name)
        lock = self._wake_locks.setdefault(key, asyncio.Lock())
        async with lock:
            existing = self._tasks.get(key)
            if existing is not None and not existing.done():
                return WakeResult(member_name, False, already_running=True, reason="成员已经运行")
            member = await self.store.get_member(team_name, member_name)
            if member.status == "terminated":
                raise TeamDataError(f"成员已终止: {member_name}")
            actor = TeamActor(team_name, member.name, "member", Path(member.worktree_cwd))
            unread = await self.manager._service(team_name).mailbox.unread(actor)
            if not unread and member.current_task_id is None:
                return WakeResult(member_name, False, reason="成员没有待处理消息或任务")
            role = self.role_provider(member.role)
            if role is None:
                raise TeamDataError(f"成员角色不可用: {member.role}")
            self.manager.set_member_role_body(team_name, member.name, role.body)
            lease = ProcessLease(TeamPaths.for_team(team_name, base=self.store.root).runtime_lease(member_name), self.manager.config)
            await lease.acquire()
            self._process_leases[key] = lease
            worktree = await self._worktree_for(team_name, member)
            _ = worktree
            task = asyncio.create_task(self._run_member(team_name, member, role))
            self._tasks[key] = task
            return WakeResult(member_name, True)

    async def terminate(self, team_name: str, member_name: str) -> TeamMemberRecord:
        key = (team_name, member_name)
        self._terminating.add(key)
        try:
            task = self._tasks.get(key)
            if task is not None and not task.done():
                runner = self._runners.get(key)
                if runner is not None:
                    runner.cancel()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            member = await self.store.get_member(team_name, member_name)
            if member.current_task_id:
                await self.manager._service(team_name).tasks.release_interrupted(
                    member.current_task_id, "成员被 Lead 终止，任务等待重新指派。"
                )
            now = _now()
            terminated = replace(
                member,
                status="terminated",
                current_task_id=None,
                pending_approval_id=None,
                updated_at=now,
                last_active_at=now,
            )
            await self.store.update_member(team_name, terminated)
            await self._emit_member_event(team_name, terminated, "member_terminated", f"成员 {member_name} 已终止。")
            await self._release_runtime(key)
            return terminated
        finally:
            self._terminating.discard(key)

    async def shutdown(self) -> None:
        self._closing = True
        tasks = tuple(self._tasks.values())
        for runner in self._runners.values():
            runner.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for key in tuple(self._process_leases):
            await self._release_runtime(key)
        for lease in tuple(self._worktree_leases.values()):
            await self.worktrees.release(lease)
        self._worktree_leases.clear()

    async def _run_member(self, team_name: str, member: TeamMemberRecord, role: SubAgentRoleDefinition) -> None:
        key = (team_name, member.name)
        session_store = TeamMemberSessionStore(Path(member.session_path))
        session, restore = session_store.load()
        approval_state = {"allowed": False}
        controller = TeamMemberLoopController(
            manager=self.manager,
            team_name=team_name,
            member_name=member.name,
            session_store=session_store,
            approval_state=approval_state,
        )
        runner = self.runner_factory.create_runner(
            session=session,
            member=member,
            role=role,
            manager=self.manager,
            controller=controller,
            approval_state=approval_state,
        )
        self._runners[key] = runner
        now = _now()
        await self.store.update_member(
            team_name,
            replace(member, status="running", updated_at=now, last_active_at=now, last_error=None),
        )
        if restore.restored:
            await self._emit_member_event(team_name, member, "member_resumed", f"成员 {member.name} 已恢复上下文。")
        session.append_user_message(
            "请处理刚收到的团队消息和当前共享任务。",
            metadata={"mewcode_generated": True, "source": "team_runtime"},
        )
        error: str | None = None
        try:
            async for event in runner.run(
                AgentCommand("normal", "处理团队消息", "处理团队消息"),
                append_user_message=False,
            ):
                if event.type == "error":
                    error = event.error or "成员运行失败"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = str(exc)
        finally:
            session.append_checkpoint()
            current = await self.store.get_member(team_name, member.name)
            if current.status != "terminated" and key not in self._terminating:
                if error and current.current_task_id:
                    try:
                        actor = TeamActor(team_name, member.name, "member", Path(member.worktree_cwd))
                        task = await self.manager._service(team_name).tasks.get(current.current_task_id)
                        if task.status not in {"completed", "failed", "cancelled"}:
                            await self.manager._service(team_name).tasks.fail(
                                actor, current.current_task_id, error
                            )
                            current = await self.store.get_member(team_name, member.name)
                    except TeamDataError:
                        pass
                status = "failed" if error else ("awaiting_approval" if current.status == "awaiting_approval" else "idle")
                idle = replace(
                    current,
                    status=status,
                    updated_at=_now(),
                    last_active_at=_now(),
                    last_error=error,
                )
                await self.store.update_member(team_name, idle)
                protocol = "message" if error else "member_idle"
                body = f"成员 {member.name} 运行失败：{error}" if error else f"成员 {member.name} 已空闲。"
                await self._emit_member_event(team_name, idle, protocol, body)
            self._runners.pop(key, None)
            self._tasks.pop(key, None)
            await self._release_runtime(key)
            self.manager.notify_event()
            await self.manager.refresh_prompt_context(team_name)
            if not self._closing:
                actor = TeamActor(team_name, member.name, "member", Path(member.worktree_cwd))
                if await self.manager._service(team_name).mailbox.unread(actor):
                    asyncio.create_task(self.wake(team_name, member.name))

    async def _emit_member_event(self, team_name: str, member: TeamMemberRecord, protocol: str, body: str) -> None:
        event = OutboxEvent(
            id=f"event-{uuid.uuid4().hex}", source="team", protocol=protocol,  # type: ignore[arg-type]
            sender=member.name, recipients=("lead",), body=body, summary=body,
            task_id=member.current_task_id, approval_id=member.pending_approval_id,
            plan_version=None, created_at=_now(),
        )
        await self.store.append_outbox(team_name, asdict(event))
        await self.manager._service(team_name).dispatcher.flush()

    async def _worktree_for(self, team_name: str, member: TeamMemberRecord) -> WorktreeLease:
        key = (team_name, member.name)
        existing = self._worktree_leases.get(key)
        if existing is not None:
            return existing
        lease = await self.worktrees.acquire(
            task_id=member.worktree_owner_id,
            role="teams",
            retention="persistent",
        )
        if lease.root != Path(member.worktree_root) or lease.cwd != Path(member.worktree_cwd):
            await self.worktrees.release(lease)
            raise TeamDataError("成员 Worktree 恢复路径与花名册不匹配")
        self._worktree_leases[key] = lease
        return lease

    async def _release_runtime(self, key: tuple[str, str]) -> None:
        lease = self._process_leases.pop(key, None)
        if lease is not None:
            await lease.release()


def _render_message(message) -> str:
    return (
        f"<team_message id=\"{message.id}\" protocol=\"{message.protocol}\" sender=\"{message.sender}\" "
        f"task=\"{message.task_id or ''}\" approval=\"{message.approval_id or ''}\" "
        f"version=\"{message.plan_version or ''}\">\n"
        f"{message.body}\n</team_message>"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _worktree_owner_id(team_name: str, member_name: str) -> str:
    digest = hashlib.sha256(f"{team_name}\0{member_name}".encode("utf-8")).hexdigest()[:24]
    return f"team-{digest}"
