from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from mewcode.agent import AgentLoopRunner, TurnEvent
from mewcode.commands import AgentCommand
from mewcode.config import AgentConfig, AppConfig
from mewcode.context.manager import ContextManager
from mewcode.hooks.manager import HookManager
from mewcode.permissions.controller import create_permission_controller
from mewcode.permissions.models import PermissionConfig
from mewcode.memory.manager import SessionMemoryManager
from mewcode.providers.base import ChatMessage, LLMProvider, TokenUsage
from mewcode.session import ChatSession
from mewcode.skills.execution import ProviderResolver
from mewcode.subagents.cache import FileReadCache
from mewcode.subagents.models import (
    ActiveSubAgentPrompt,
    ParentAgentContext,
    SubAgentInvocation,
    SubAgentResult,
    SubAgentRoleDefinition,
    SubAgentToolFilter,
    SubAgentWorkingContext,
)
from mewcode.tools.base import ToolContext
from mewcode.tools.base import RuntimePrincipal
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from mewcode.mcp.manager import McpManager


class SubAgentRunnerFactory:
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
        task_id: str,
        invocation: SubAgentInvocation,
        parent: ParentAgentContext,
        role: SubAgentRoleDefinition | None,
        background: bool,
        working_context: SubAgentWorkingContext,
    ) -> tuple[AgentLoopRunner, AgentCommand, ChatSession]:
        if invocation.type == "defined":
            session = ChatSession()
        else:
            session = ChatSession(messages=_safe_fork_messages(parent.session.messages))

        active_prompt = ActiveSubAgentPrompt(
            task_id=task_id,
            type=invocation.type,
            role_name=role.name if role is not None else None,
            role_description=role.description if role is not None else None,
            role_body=role.body if role is not None else None,
            task=invocation.task,
            isolation=working_context.isolation,
            cwd=working_context.cwd,
            main_cwd=working_context.main_cwd,
            branch=(working_context.lease.metadata.branch if working_context.lease is not None else None),
        )
        tool_filter = self._tool_filter(invocation, parent, role, background)
        provider = self.provider_resolver(self._model_override(role))
        runner = AgentLoopRunner(
            session,
            provider,
            self.registry,
            self._child_executor(working_context.cwd),
            self._agent_config(invocation, role),
            permission_controller=self._permission_controller(role, working_context.cwd),
            context_manager=self._context_manager(session, working_context.cwd),
            memory_manager=self._memory_manager(working_context.cwd),
            provider_resolver=lambda _model_override: provider,
            hook_manager=self._child_hook_manager(),
            tool_filter=tool_filter,
            sub_agent_prompt=active_prompt,
            file_read_cache=FileReadCache(),
            mcp_manager=self.mcp_manager,
        )
        command = AgentCommand(mode=parent.mode, visible_text=invocation.task, model_text=invocation.task)
        return runner, command, session

    def _tool_filter(
        self,
        invocation: SubAgentInvocation,
        parent: ParentAgentContext,
        role: SubAgentRoleDefinition | None,
        background: bool,
    ) -> SubAgentToolFilter:
        if invocation.type == "fork":
            inherited = frozenset(spec.name for spec in parent.allowed_tools)
            role_allow = None
            role_deny = frozenset()
        else:
            inherited = None
            role_allow = frozenset(role.frontmatter.tools_allow) if role is not None else frozenset()
            role_deny = frozenset(role.frontmatter.tools_deny) if role is not None else frozenset()
        return SubAgentToolFilter(
            inherited_tools=inherited,
            role_allow=role_allow,
            role_deny=role_deny,
            global_blocked=frozenset(self.config.sub_agents.global_blocked_tools),
            background_allowed=frozenset(self.config.sub_agents.background_allowed_tools) if background else None,
            nested_blocked=frozenset(
                {"delegate_agent", "manage_team", "manage_team_member", "team_task", "team_message", "team_wait"}
            ),
        )

    def _model_override(self, role: SubAgentRoleDefinition | None) -> str | None:
        if role is None:
            return None
        model = role.frontmatter.model
        if not model or model == "inherit":
            return None
        return self.config.sub_agents.model_aliases.get(model, model)

    def _agent_config(self, invocation: SubAgentInvocation, role: SubAgentRoleDefinition | None) -> AgentConfig:
        max_iterations = (
            invocation.max_iterations
            or (role.frontmatter.max_iterations if role is not None else None)
            or self.config.sub_agents.default_max_iterations
            or self.config.agent.max_iterations
        )
        return replace(self.config.agent, max_iterations=max_iterations)

    def _permission_controller(self, role: SubAgentRoleDefinition | None, cwd: Path):
        mode = self.config.permissions.mode
        if role is not None and role.frontmatter.permission_mode != "inherit":
            mode = role.frontmatter.permission_mode
        return create_permission_controller(cwd, PermissionConfig(mode=mode))  # type: ignore[arg-type]

    def _context_manager(self, session: ChatSession, cwd: Path) -> ContextManager:
        _ = session
        return ContextManager(self.config.context, cwd, self.config.max_tokens)

    def _child_executor(self, cwd: Path) -> ToolExecutor:
        context = ToolContext(
            cwd=cwd,
            max_output_chars=self.executor.context.max_output_chars,
            read_cache=FileReadCache(),
            principal=RuntimePrincipal("sub_agent"),
        )
        return ToolExecutor(self.registry, context)

    def _memory_manager(self, cwd: Path) -> SessionMemoryManager:
        manager = SessionMemoryManager(
            cwd,
            replace(self.config.memory, auto_notes_enabled=False),
        )
        manager.load_runtime_context()
        return manager

    def _child_hook_manager(self) -> HookManager | None:
        if self.hook_manager is None:
            return None
        return HookManager(self.hook_manager.config, self.hook_manager.action_runner)


async def run_sub_agent_to_result(
    *,
    task_id: str,
    invocation: SubAgentInvocation,
    runner: AgentLoopRunner,
    command: AgentCommand,
) -> SubAgentResult:
    final_message: ChatMessage | None = None
    final_stop_reason: str | None = None
    usage: TokenUsage | None = None
    error: str | None = None
    try:
        async for event in runner.run(command):
            final_message, final_stop_reason, usage, error = _collect_event(
                event,
                final_message,
                final_stop_reason,
                usage,
                error,
            )
    except asyncio.CancelledError:
        return SubAgentResult(
            task_id=task_id,
            type=invocation.type,
            role=invocation.role,
            status="cancelled",
            task=invocation.task,
            summary="子 Agent 已取消。",
            stop_reason="cancelled",
            error="cancelled",
            usage=usage,
        )

    final_text = final_message.content if final_message is not None else ""
    if error is not None:
        status = "failed"
        summary = f"子 Agent 失败：{error}"
    elif final_stop_reason == "cancelled":
        status = "cancelled"
        summary = "子 Agent 已取消。"
    elif final_stop_reason in {None, "completed"}:
        status = "completed"
        summary = _summary_text(final_text)
    else:
        status = "failed"
        summary = _summary_text(final_text) or f"子 Agent 停止：{final_stop_reason}"
    return SubAgentResult(
        task_id=task_id,
        type=invocation.type,
        role=invocation.role,
        status=status,  # type: ignore[arg-type]
        task=invocation.task,
        summary=summary,
        final_text=final_text,
        stop_reason=final_stop_reason,
        key_outputs=_key_outputs(final_text),
        error=error,
        usage=usage,
    )


def _safe_fork_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    copied = copy.deepcopy(messages)
    while copied and copied[-1].role == "assistant" and copied[-1].tool_calls:
        copied.pop()
    return copied


def _collect_event(
    event: TurnEvent,
    final_message: ChatMessage | None,
    stop_reason: str | None,
    usage: TokenUsage | None,
    error: str | None,
) -> tuple[ChatMessage | None, str | None, TokenUsage | None, str | None]:
    if event.type == "usage":
        usage = event.usage or usage
    elif event.type == "message_done":
        final_message = event.message or final_message
        stop_reason = event.stop_reason or stop_reason
    elif event.type == "stopped":
        final_message = event.message or final_message
        stop_reason = event.stop_reason or stop_reason
    elif event.type == "error":
        error = event.error or "子 Agent 未知错误"
        stop_reason = event.stop_reason or stop_reason
    return final_message, stop_reason, usage, error


def _summary_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    first_line = stripped.splitlines()[0].strip()
    if len(first_line) > 240:
        return first_line[:240] + "..."
    return first_line


def _key_outputs(text: str) -> tuple[str, ...]:
    outputs: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        outputs.append(stripped[:240])
        if len(outputs) >= 5:
            break
    return tuple(outputs)
