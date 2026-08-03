from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from julycode.commands import AgentCommand, AgentMode
from julycode.config import AgentConfig
from julycode.context.manager import ContextManager
from julycode.context.models import ContextCompactionReport, ContextConfig, ContextLimitError
from julycode.errors import JulyCodeError, ProviderError
from julycode.memory.models import KnowledgeContext, MemoryUpdateJob
from julycode.permissions.controller import PermissionController, create_permission_controller
from julycode.permissions.models import PermissionConfig, PermissionEventPayload
from julycode.prompting import GeneratedContextBlock, PromptBuilder, RuntimePromptContext
from julycode.providers.base import ChatMessage, LLMProvider, StreamEvent, TokenUsage
from julycode.session import ChatSession
from julycode.subagents.cache import FileReadCache
from julycode.subagents.models import ActiveSubAgentPrompt, ParentAgentContext, SubAgentPromptContext, SubAgentToolFilter
from julycode.session_id import SessionId
from julycode.tools.base import ToolCall, ToolResult
from julycode.tools.executor import ToolExecutor
from julycode.tools.registry import ToolRegistry
from julycode.tools.scheduler import ToolCallScheduler, ToolPolicy
from julycode.tools.scheduler import ToolGate
from julycode.hooks.models import HookEvent, HookExecutionResult, HookRuntimeContext

if TYPE_CHECKING:
    from julycode.memory.manager import SessionMemoryManager
    from julycode.mcp.manager import McpManager
    from julycode.repo_map.manager import RepoMapManager, RepoMapTurn
    from julycode.skills.manager import SkillManager
    from julycode.teams.models import TeamPromptContext


@dataclass(frozen=True)
class AgentProgress:
    iteration: int
    max_iterations: int
    mode: AgentMode
    phase: Literal["model", "tools", "done"]
    detail: str = ""


AgentStopReason = Literal[
    "completed",
    "iteration_limit",
    "cancelled",
    "unknown_tool_limit",
    "stream_error",
    "context_limit",
]

TurnEventType = Literal[
    "progress",
    "text_delta",
    "thinking_delta",
    "usage",
    "tool_started",
    "tool_finished",
    "permission_requested",
    "permission_resolved",
    "context_compacted",
    "hook_finished",
    "message_done",
    "stopped",
    "error",
]


@dataclass(frozen=True)
class TurnEvent:
    type: TurnEventType
    text: str = ""
    message: ChatMessage | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    permission: PermissionEventPayload | None = None
    context_report: ContextCompactionReport | None = None
    hook_result: HookExecutionResult | None = None
    progress: AgentProgress | None = None
    usage: TokenUsage | None = None
    stop_reason: AgentStopReason | None = None
    error: str | None = None


@dataclass(frozen=True)
class StreamCollection:
    message: ChatMessage
    usage: TokenUsage | None


@dataclass(frozen=True)
class CompletionDecision:
    accept: bool
    message: ChatMessage
    continuation: str | None = None


class AgentLoopController(Protocol):
    async def before_iteration(self, session: ChatSession) -> None:
        ...

    async def review_completion(self, message: ChatMessage) -> CompletionDecision:
        ...


class StreamCollector:
    def __init__(self) -> None:
        self._text_parts: list[str] = []
        self._thinking_parts: list[str] = []
        self._message: ChatMessage | None = None
        self._usage: TokenUsage | None = None

    async def collect(
        self,
        stream: AsyncIterator[StreamEvent],
        *,
        iteration: int,
        mode: AgentMode,
    ) -> AsyncIterator[TurnEvent]:
        _ = iteration, mode
        async for stream_event in stream:
            if stream_event.type == "text_delta":
                self._text_parts.append(stream_event.text)
                yield TurnEvent(type="text_delta", text=stream_event.text)
            elif stream_event.type == "thinking_delta":
                self._thinking_parts.append(stream_event.text)
                yield TurnEvent(type="thinking_delta", text=stream_event.text)
            elif stream_event.type == "usage":
                self._usage = stream_event.usage
                yield TurnEvent(type="usage", usage=stream_event.usage)
            elif stream_event.type == "message_done":
                self._message = stream_event.message
            elif stream_event.type == "error":
                raise ProviderError(stream_event.error or "Provider 返回未知错误")

    def result(self) -> StreamCollection:
        message = self._message or ChatMessage(
            role="assistant",
            content="".join(self._text_parts),
            thinking="".join(self._thinking_parts) or None,
        )
        return StreamCollection(message=message, usage=self._usage)


class AgentLoopRunner:
    def __init__(
        self,
        session: ChatSession,
        provider: LLMProvider,
        registry: ToolRegistry,
        executor: ToolExecutor,
        config: AgentConfig,
        prompt_builder: PromptBuilder | None = None,
        permission_controller: PermissionController | None = None,
        context_manager: ContextManager | None = None,
        memory_manager: SessionMemoryManager | None = None,
        skill_manager: SkillManager | None = None,
        provider_resolver: Callable[[str | None], LLMProvider] | None = None,
        hook_manager: object | None = None,
        sub_agent_manager: object | None = None,
        tool_filter: SubAgentToolFilter | None = None,
        sub_agent_prompt: ActiveSubAgentPrompt | None = None,
        file_read_cache: FileReadCache | None = None,
        tool_gates: tuple[ToolGate, ...] = (),
        loop_controller: AgentLoopController | None = None,
        team_prompt_provider: Callable[[], TeamPromptContext | None] | None = None,
        mcp_manager: McpManager | None = None,
        repo_map_manager: RepoMapManager | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.registry = registry
        self.executor = (
            ToolExecutor(registry, replace(executor.context, read_cache=file_read_cache))
            if file_read_cache is not None
            else executor
        )
        self.config = config
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.permission_controller = permission_controller or create_permission_controller(
            executor.context.cwd,
            PermissionConfig(mode="permissive"),
        )
        self.context_manager = context_manager or ContextManager(
            ContextConfig(),
            executor.context.cwd,
            max_output_tokens=4096,
        )
        self.memory_manager = memory_manager
        self.skill_manager = skill_manager
        self.provider_resolver = provider_resolver or (lambda model_override: self.provider)
        self.hook_manager = hook_manager
        self.sub_agent_manager = sub_agent_manager
        self.tool_filter = tool_filter
        self.sub_agent_prompt = sub_agent_prompt
        self.tool_gates = tool_gates
        self.loop_controller = loop_controller
        self.team_prompt_provider = team_prompt_provider
        self.mcp_manager = mcp_manager
        self.repo_map_manager = repo_map_manager
        self.mcp_turn_state = mcp_manager.create_turn_state() if mcp_manager is not None else None
        self.repo_map_turn: RepoMapTurn | None = None
        self._cancel_requested = False

    async def run(self, command: AgentCommand, *, append_user_message: bool = True) -> AsyncIterator[TurnEvent]:
        if self.repo_map_manager is not None and getattr(self.repo_map_manager.config, "enabled", True):
            try:
                self.repo_map_turn = self.repo_map_manager.begin_turn(command.model_text)
            except Exception:
                self.repo_map_turn = None
        if self.mcp_turn_state is not None:
            self.mcp_turn_state.begin_turn()
        try:
            async for event in self._run_turn(command, append_user_message=append_user_message):
                yield event
        finally:
            if self.mcp_turn_state is not None:
                self.mcp_turn_state.end_turn()
            if self.repo_map_manager is not None and self.repo_map_turn is not None:
                try:
                    self.repo_map_manager.end_turn(self.repo_map_turn)
                except Exception:
                    pass
            self.repo_map_turn = None

    async def _run_turn(self, command: AgentCommand, *, append_user_message: bool = True) -> AsyncIterator[TurnEvent]:
        self._cancel_requested = False
        turn_start = len(self.session.messages)
        if append_user_message:
            self.session.append_user_message(command.model_text)
        previous_unknown_tool = False
        last_knowledge_context: KnowledgeContext | None = None
        initial_whitelist = self.skill_manager.active_tool_whitelist() if self.skill_manager is not None else None
        initial_hook_context = self._hook_context(command.mode, initial_whitelist)
        async for event in self._emit_hook(
            HookEvent(
                "turn.start",
                {
                    "turn": {
                        "mode": command.mode,
                        "visible_text": command.visible_text,
                        "model_text": command.model_text,
                    }
                },
            ),
            initial_hook_context,
        ):
            yield event
        async for event in self._emit_hook(
            HookEvent("message.user", {"message": {"content": command.model_text}, "turn": {"mode": command.mode}}),
            initial_hook_context,
        ):
            yield event

        try:
            for iteration in range(1, self.config.max_iterations + 1):
                if self._cancel_requested:
                    yield self._stopped("cancelled", "已取消当前任务。", command, iteration)
                    return

                if self.loop_controller is not None:
                    await self.loop_controller.before_iteration(self.session)

                tool_whitelist = self.skill_manager.active_tool_whitelist() if self.skill_manager is not None else None
                policy = ToolPolicy(
                    command.mode,
                    tool_whitelist,
                    self.tool_filter,
                    self.tool_gates,
                    self.mcp_turn_state.active_tools if self.mcp_turn_state is not None else frozenset(),
                )
                allowed_tools = policy.allowed_specs(self.registry)
                hook_context = self._hook_context(command.mode, tool_whitelist)
                provider = self.provider_resolver(
                    self.skill_manager.resolve_model_override() if self.skill_manager is not None else None
                )
                hook_injections = (
                    ()
                    if self.hook_manager is None
                    else self.hook_manager.consume_prompt_injections()  # type: ignore[attr-defined]
                )

                def prompt_factory() -> object:
                    nonlocal last_knowledge_context
                    last_knowledge_context = self.memory_manager.runtime_context() if self.memory_manager is not None else None
                    skill_context = self.skill_manager.prompt_context() if self.skill_manager is not None else None
                    sub_agent_context = self._sub_agent_prompt_context()
                    return self.prompt_builder.build_bundle(
                        RuntimePromptContext(
                            cwd=self.executor.context.cwd,
                            mode=command.mode,
                            iteration=iteration,
                            max_iterations=self.config.max_iterations,
                            allowed_tools=allowed_tools,
                            pending_plan=self.session.pending_plan,
                            source_request=self._runtime_source_request(command),
                            context_summary=self.session.context_state.summary,
                            knowledge_context=last_knowledge_context,
                            skill_context=skill_context,
                            sub_agent_context=sub_agent_context,
                            team_context=(self.team_prompt_provider() if self.team_prompt_provider is not None else None),
                            mcp_context=(
                                self.mcp_turn_state.prompt_context()
                                if self.mcp_turn_state is not None
                                else None
                            ),
                            hook_injections=hook_injections,
                        )
                    )

                try:
                    if self.repo_map_manager is not None and self.repo_map_turn is not None:
                        async def repo_map_factory(granted_tokens: int) -> tuple[GeneratedContextBlock, ...]:
                            snapshot = await self.repo_map_manager.build_snapshot(
                                self.repo_map_turn,  # type: ignore[arg-type]
                                granted_tokens,
                                self.context_manager.estimator.estimate_text,
                            )
                            if snapshot is None:
                                return ()
                            return (
                                GeneratedContextBlock(
                                    name="repo_map",
                                    title="仓库地图",
                                    text=snapshot.text,
                                    kind="repo_map",
                                    snapshot_id=snapshot.snapshot_id,
                                ),
                            )

                        prepared = await self.context_manager.prepare_request(
                            session=self.session,
                            provider=provider,
                            tools=allowed_tools,
                            prompt_factory=prompt_factory,  # type: ignore[arg-type]
                            optional_context_factory=repo_map_factory,
                            optional_context_max_tokens=self.repo_map_manager.config.max_tokens,
                        )
                    else:
                        prepared = await self.context_manager.prepare_request(
                            session=self.session,
                            provider=provider,
                            tools=allowed_tools,
                            prompt_factory=prompt_factory,  # type: ignore[arg-type]
                        )
                except ContextLimitError as exc:
                    text = str(exc)
                    stop_message = ChatMessage(role="assistant", content=text)
                    self.session.append_assistant_message(stop_message)
                    yield self._stopped("context_limit", text, command, iteration, stop_message)
                    yield TurnEvent(type="message_done", message=stop_message, stop_reason="context_limit")
                    return
                if prepared.report is not None and (prepared.report.light_compacted or prepared.report.heavy_compacted):
                    async for event in self._emit_hook(
                        HookEvent(
                            "system.context_compacted",
                            {
                                "context": {
                                    "mode": prepared.report.mode,
                                    "light_compacted": prepared.report.light_compacted,
                                    "heavy_compacted": prepared.report.heavy_compacted,
                                }
                            },
                        ),
                        hook_context,
                    ):
                        yield event
                    yield TurnEvent(
                        type="context_compacted",
                        text=prepared.report.message,
                        context_report=prepared.report,
                    )
                yield self._progress(command, iteration, "model")

                collector = StreamCollector()
                async for event in collector.collect(
                    provider.stream_chat(prepared.request),
                    iteration=iteration,
                    mode=command.mode,
                ):
                    yield event
                    if self._cancel_requested:
                        yield self._stopped("cancelled", "已取消当前任务。", command, iteration)
                        return
                collection = collector.result()
                self.context_manager.record_usage(collection.usage, prepared.footprint)
                message = collection.message
                async for event in self._emit_hook(
                    HookEvent(
                        "message.assistant",
                        {
                            "message": {
                                "content": message.content,
                                "has_tool_calls": bool(message.tool_calls),
                            },
                            "turn": {"mode": command.mode},
                        },
                    ),
                    hook_context,
                ):
                    yield event

                if self._cancel_requested:
                    async for event in self._stop_hook_events(command, "cancelled", "已取消当前任务。", hook_context):
                        yield event
                    yield self._stopped("cancelled", "已取消当前任务。", command, iteration)
                    return

                if not message.tool_calls:
                    if self.loop_controller is not None:
                        decision = await self.loop_controller.review_completion(message)
                        message = decision.message
                        if not decision.accept:
                            self.session.append_assistant_message(message)
                            continuation = decision.continuation or "继续处理尚未完成的工作。"
                            self.session.append_user_message(continuation, metadata={"julycode_generated": True})
                            continue
                    self.session.append_assistant_message(message)
                    self._schedule_memory_update(
                        turn_start=turn_start,
                        final_message=message,
                        context=last_knowledge_context,
                        provider=provider,
                    )
                    async for event in self._stop_hook_events(command, "completed", message.content, hook_context):
                        yield event
                    yield self._progress(command, iteration, "done")
                    yield TurnEvent(type="message_done", message=message, stop_reason="completed")
                    return

                if iteration >= self.config.max_iterations:
                    text = f"已达到迭代上限（{self.config.max_iterations} 轮），停止继续执行工具。"
                    stop_message = ChatMessage(role="assistant", content=text)
                    self.session.append_assistant_message(stop_message)
                    async for event in self._stop_hook_events(command, "iteration_limit", text, hook_context):
                        yield event
                    yield self._stopped("iteration_limit", text, command, iteration, stop_message)
                    yield TurnEvent(type="message_done", message=stop_message, stop_reason="iteration_limit")
                    return

                if previous_unknown_tool and self._has_unknown_tool(message.tool_calls):
                    text = "模型连续请求不存在的工具，已停止本次任务。"
                    stop_message = ChatMessage(role="assistant", content=text)
                    self.session.append_assistant_message(stop_message)
                    async for event in self._stop_hook_events(command, "unknown_tool_limit", text, hook_context):
                        yield event
                    yield self._stopped("unknown_tool_limit", text, command, iteration, stop_message)
                    yield TurnEvent(type="message_done", message=stop_message, stop_reason="unknown_tool_limit")
                    return

                self.session.append_assistant_message(message)
                yield self._progress(command, iteration, "tools")

                scheduler = ToolCallScheduler(
                    self.registry,
                    self.executor,
                    policy,
                    self.permission_controller,
                    hook_manager=self.hook_manager,
                    hook_context=hook_context,
                    execution_observer=(
                        self.repo_map_manager.observer_for(self.repo_map_turn)
                        if self.repo_map_manager is not None and self.repo_map_turn is not None
                        else None
                    ),
                )
                self._bind_parent_context(command, allowed_tools, tool_whitelist)
                try:
                    async for event in scheduler.run(message.tool_calls):
                        yield event
                        if self._cancel_requested:
                            yield self._stopped("cancelled", "已取消当前任务。", command, iteration)
                            return
                finally:
                    self._clear_parent_context()

                results = scheduler.results()
                if self.mcp_turn_state is not None:
                    results = self.mcp_turn_state.apply_search_results(
                        results,
                        policy=policy,
                        registry=self.registry,
                    )
                for result in results:
                    self.session.append_tool_result(result)
                previous_unknown_tool = any(result.error_type == "unknown_tool" for result in results)

            text = f"已达到迭代上限（{self.config.max_iterations} 轮），停止继续执行。"
            stop_message = ChatMessage(role="assistant", content=text)
            self.session.append_assistant_message(stop_message)
            final_hook_context = self._hook_context(command.mode, None)
            async for event in self._stop_hook_events(command, "iteration_limit", text, final_hook_context):
                yield event
            yield self._stopped(
                "iteration_limit",
                text,
                command,
                self.config.max_iterations,
                stop_message,
            )
            yield TurnEvent(type="message_done", message=stop_message, stop_reason="iteration_limit")
        except asyncio.CancelledError:
            async for event in self._stop_hook_events(
                command,
                "cancelled",
                "已取消当前任务。",
                self._hook_context(command.mode, None),
            ):
                yield event
            yield self._stopped("cancelled", "已取消当前任务。", command, 0)
            return
        except Exception as exc:
            error = str(exc) if isinstance(exc, JulyCodeError) else f"未预期错误: {exc}"
            hook_context = self._hook_context(command.mode, None)
            async for event in self._emit_hook(
                HookEvent("system.error", {"error": {"type": type(exc).__name__, "message": error}}),
                hook_context,
            ):
                yield event
            async for event in self._emit_hook(
                HookEvent("turn.end", {"turn": {"mode": command.mode, "stop_reason": "stream_error"}}),
                hook_context,
            ):
                yield event
            yield TurnEvent(type="error", error=error, stop_reason="stream_error")

    def cancel(self) -> None:
        self._cancel_requested = True

    @property
    def active_mcp_tools(self) -> frozenset[str]:
        if self.mcp_turn_state is None:
            return frozenset()
        return self.mcp_turn_state.active_tools

    def _schedule_memory_update(
        self,
        *,
        turn_start: int,
        final_message: ChatMessage,
        context: KnowledgeContext | None,
        provider: LLMProvider,
    ) -> None:
        if self.memory_manager is None or context is None:
            return
        memory_job = MemoryUpdateJob(
            session_id=SessionId(self.session.context_state.session_id),
            cwd=Path(self.executor.context.cwd),
            turn_messages=tuple(self.session.messages[turn_start:-1]),
            final_message=final_message,
            knowledge_context=context,
        )
        self.memory_manager.schedule_update(job=memory_job, provider=provider)

    def _runtime_source_request(self, command: AgentCommand) -> str:
        return command.visible_text

    def _has_unknown_tool(self, calls: tuple[ToolCall, ...]) -> bool:
        return any(self.registry.get(call.name) is None for call in calls)

    def _progress(
        self,
        command: AgentCommand,
        iteration: int,
        phase: Literal["model", "tools", "done"],
        detail: str = "",
    ) -> TurnEvent:
        return TurnEvent(
            type="progress",
            progress=AgentProgress(
                iteration=iteration,
                max_iterations=self.config.max_iterations,
                mode=command.mode,
                phase=phase,
                detail=detail,
            ),
        )

    def _stopped(
        self,
        reason: AgentStopReason,
        text: str,
        command: AgentCommand,
        iteration: int,
        message: ChatMessage | None = None,
    ) -> TurnEvent:
        safe_iteration = max(iteration, 1)
        return TurnEvent(
            type="stopped",
            text=text,
            message=message,
            stop_reason=reason,
            progress=AgentProgress(
                iteration=safe_iteration,
                max_iterations=self.config.max_iterations,
                mode=command.mode,
                phase="done",
                detail=text,
            ),
        )

    def _hook_context(self, mode: AgentMode, tool_whitelist: frozenset[str] | None) -> HookRuntimeContext:
        return HookRuntimeContext(
            cwd=self.executor.context.cwd,
            mode=mode,
            allowed_tool_names=tool_whitelist,
            registry=self.registry,
            executor=self.executor,
            permission_controller=self.permission_controller,
        )

    def _sub_agent_prompt_context(self) -> SubAgentPromptContext | None:
        if self.sub_agent_prompt is not None:
            return SubAgentPromptContext(active=self.sub_agent_prompt)
        if self.sub_agent_manager is None:
            return None
        prompt_context = getattr(self.sub_agent_manager, "prompt_context", None)
        if prompt_context is None:
            return None
        return prompt_context()

    def _bind_parent_context(
        self,
        command: AgentCommand,
        allowed_tools: tuple[object, ...],
        tool_whitelist: frozenset[str] | None,
    ) -> None:
        if self.sub_agent_manager is None:
            return
        binder = getattr(self.sub_agent_manager, "bind_parent_context", None)
        if binder is None:
            return
        binder(
            ParentAgentContext(
                session=self.session,
                mode=command.mode,
                command=command,
                allowed_tools=allowed_tools,  # type: ignore[arg-type]
                tool_whitelist=tool_whitelist,
            )
        )

    def _clear_parent_context(self) -> None:
        if self.sub_agent_manager is None:
            return
        binder = getattr(self.sub_agent_manager, "bind_parent_context", None)
        if binder is not None:
            binder(None)

    async def _emit_hook(
        self,
        event: HookEvent,
        context: HookRuntimeContext,
    ) -> AsyncIterator[TurnEvent]:
        if self.hook_manager is None:
            return
        results = await self.hook_manager.emit(event, context)  # type: ignore[attr-defined]
        for result in results:
            yield TurnEvent(type="hook_finished", hook_result=result)

    async def _stop_hook_events(
        self,
        command: AgentCommand,
        reason: AgentStopReason,
        text: str,
        context: HookRuntimeContext,
    ) -> AsyncIterator[TurnEvent]:
        async for event in self._emit_hook(
            HookEvent("system.stopped", {"stop": {"reason": reason, "text": text}}),
            context,
        ):
            yield event
        async for event in self._emit_hook(
            HookEvent("turn.end", {"turn": {"mode": command.mode, "stop_reason": reason}}),
            context,
        ):
            yield event


class ToolAwareTurnRunner:
    def __init__(
        self,
        session: ChatSession,
        provider: LLMProvider,
        registry: ToolRegistry,
        executor: ToolExecutor,
        mcp_manager: McpManager | None = None,
    ) -> None:
        self._runner = AgentLoopRunner(
            session,
            provider,
            registry,
            executor,
            AgentConfig(),
            mcp_manager=mcp_manager,
        )

    @property
    def session(self) -> ChatSession:
        return self._runner.session

    async def run(self, user_text: str) -> AsyncIterator[TurnEvent]:
        command = AgentCommand(mode="normal", visible_text=user_text, model_text=user_text)
        async for event in self._runner.run(command):
            yield event
