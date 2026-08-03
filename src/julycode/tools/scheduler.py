from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from julycode.commands import AgentMode
from julycode.permissions.controller import PermissionController
from julycode.permissions.models import PermissionEventPayload
from julycode.tools.base import ToolCall, ToolResult, ToolSpec
from julycode.tools.executor import ToolExecutor
from julycode.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from julycode.subagents.models import SubAgentToolFilter


class ToolGate(Protocol):
    def allows(self, spec: ToolSpec) -> bool:
        ...


class ToolExecutionObserver(Protocol):
    async def before_execute(self, call: ToolCall, spec: ToolSpec) -> object | None:
        ...

    async def after_execute(
        self,
        call: ToolCall,
        spec: ToolSpec,
        result: ToolResult,
        state: object | None,
    ) -> None:
        ...

    def denial(self, spec: ToolSpec) -> str:
        ...


@dataclass(frozen=True)
class ToolPolicy:
    mode: AgentMode
    whitelist: frozenset[str] | None = None
    filter: SubAgentToolFilter | None = None
    gates: tuple[ToolGate, ...] = ()
    activated_deferred_tools: frozenset[str] = frozenset()

    def allowed_specs(self, registry: ToolRegistry) -> tuple[ToolSpec, ...]:
        visible_specs = self._filter_deferred(registry.specs())
        if self.mode == "plan":
            specs = tuple(
                spec
                for spec in visible_specs
                if spec.safety == "read_only" or spec.visibility == "system"
            )
        else:
            specs = visible_specs
        return self._filter_gates(self._filter_sub_agent(self._filter_whitelist(specs)))

    def validate_call(self, call: ToolCall, registry: ToolRegistry) -> ToolResult | None:
        tool = registry.get(call.name)
        if tool is None:
            return ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=False,
                data={},
                error_type="unknown_tool",
                error=f"未知工具: {call.name}",
                elapsed_ms=0,
            )
        if tool.spec.visibility == "deferred" and call.name not in self.activated_deferred_tools:
            return ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=False,
                data={"visibility": "deferred"},
                error_type="tool_not_loaded",
                error=f"工具尚未按需加载: {call.name}",
                elapsed_ms=0,
            )
        if self.mode == "plan" and tool.spec.safety != "read_only" and tool.spec.visibility != "system":
            return ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=False,
                data={"safety": tool.spec.safety, "mode": self.mode},
                error_type="tool_not_allowed",
                error=f"规划阶段不允许执行有副作用工具: {call.name}",
                elapsed_ms=0,
            )
        if self.whitelist is not None and tool.spec.visibility != "system" and call.name not in self.whitelist:
            return ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=False,
                data={"mode": self.mode, "allowed_tools": sorted(self.whitelist)},
                error_type="tool_not_allowed",
                error=f"当前激活 Skill 不允许使用工具: {call.name}",
                elapsed_ms=0,
            )
        filter_result = self._validate_sub_agent_filter(call, tool.spec)
        if filter_result is not None:
            return filter_result
        for gate in self.gates:
            if gate.allows(tool.spec):
                continue
            reason = gate.denial(tool.spec)
            return ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=False,
                data={"reason": reason},
                error_type="tool_not_allowed",
                error=reason,
                elapsed_ms=0,
            )
        return None

    def _filter_whitelist(self, specs: tuple[ToolSpec, ...]) -> tuple[ToolSpec, ...]:
        if self.whitelist is None:
            return specs
        return tuple(spec for spec in specs if spec.visibility == "system" or spec.name in self.whitelist)

    def _filter_deferred(self, specs: tuple[ToolSpec, ...]) -> tuple[ToolSpec, ...]:
        return tuple(
            spec
            for spec in specs
            if spec.visibility != "deferred" or spec.name in self.activated_deferred_tools
        )

    def _filter_sub_agent(self, specs: tuple[ToolSpec, ...]) -> tuple[ToolSpec, ...]:
        if self.filter is None:
            return specs
        return tuple(spec for spec in specs if self._sub_agent_allowed(spec.name, spec.visibility == "system"))

    def _filter_gates(self, specs: tuple[ToolSpec, ...]) -> tuple[ToolSpec, ...]:
        return tuple(spec for spec in specs if all(gate.allows(spec) for gate in self.gates))

    def _validate_sub_agent_filter(self, call: ToolCall, spec: ToolSpec) -> ToolResult | None:
        if self.filter is None or self._sub_agent_allowed(call.name, spec.visibility == "system"):
            return None
        reason = self._sub_agent_denial_reason(call.name)
        return ToolResult(
            tool_call_id=call.id,
            tool_name=call.name,
            success=False,
            data={"reason": reason},
            error_type="tool_not_allowed",
            error=reason,
            elapsed_ms=0,
        )

    def _sub_agent_allowed(self, name: str, system_tool: bool = False) -> bool:
        tool_filter = self.filter
        if tool_filter is None:
            return True
        if name in tool_filter.global_blocked or name in tool_filter.nested_blocked:
            return False
        if system_tool:
            return True
        if tool_filter.inherited_tools is not None and name not in tool_filter.inherited_tools:
            return False
        if tool_filter.role_allow is not None and name not in tool_filter.role_allow:
            return False
        if name in tool_filter.role_deny:
            return False
        if tool_filter.background_allowed is not None and name not in tool_filter.background_allowed:
            return False
        return True

    def _sub_agent_denial_reason(self, name: str) -> str:
        tool_filter = self.filter
        if tool_filter is None:
            return f"子 Agent 不允许使用工具: {name}"
        if name in tool_filter.nested_blocked:
            return f"子 Agent 不允许再次委派子 Agent: {name}"
        if name in tool_filter.global_blocked:
            return f"全局禁止子 Agent 使用工具: {name}"
        if tool_filter.inherited_tools is not None and name not in tool_filter.inherited_tools:
            return f"Fork 子 Agent 不能使用父 Agent 未暴露的工具: {name}"
        if tool_filter.role_allow is not None and name not in tool_filter.role_allow:
            return f"角色白名单不允许使用工具: {name}"
        if name in tool_filter.role_deny:
            return f"角色黑名单禁止使用工具: {name}"
        if tool_filter.background_allowed is not None and name not in tool_filter.background_allowed:
            return f"后台子 Agent 不允许使用工具: {name}"
        return f"子 Agent 不允许使用工具: {name}"


@dataclass(frozen=True)
class ToolBatch:
    calls: tuple[ToolCall, ...]
    concurrent: bool


class ToolCallScheduler:
    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        policy: ToolPolicy,
        permission_controller: PermissionController | None = None,
        hook_manager: object | None = None,
        hook_context: object | None = None,
        execution_observer: ToolExecutionObserver | None = None,
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.policy = policy
        self.permission_controller = permission_controller
        self.hook_manager = hook_manager
        self.hook_context = hook_context
        self.execution_observer = execution_observer
        self._results: list[ToolResult] = []

    def make_batches(self, calls: Sequence[ToolCall]) -> tuple[ToolBatch, ...]:
        batches: list[ToolBatch] = []
        read_batch: list[ToolCall] = []
        for call in calls:
            tool = self.registry.get(call.name)
            safety = tool.spec.safety if tool is not None else "side_effect"
            if safety == "read_only":
                read_batch.append(call)
                continue
            if read_batch:
                batches.append(ToolBatch(tuple(read_batch), concurrent=True))
                read_batch = []
            batches.append(ToolBatch((call,), concurrent=False))
        if read_batch:
            batches.append(ToolBatch(tuple(read_batch), concurrent=True))
        return tuple(batches)

    async def run(self, calls: Sequence[ToolCall]) -> AsyncIterator[object]:
        from julycode.agent import TurnEvent

        self._results = []
        result_by_id: dict[str, ToolResult] = {}
        for batch in self.make_batches(calls):
            for call in batch.calls:
                yield TurnEvent(type="tool_started", tool_call=call)

            if batch.concurrent:
                async for call, result, hook_results in self._run_concurrent_batch(batch.calls):
                    result_by_id[call.id] = result
                    for hook_result in hook_results:
                        yield TurnEvent(type="hook_finished", tool_call=call, hook_result=hook_result)
                    yield TurnEvent(type="tool_finished", tool_call=call, tool_result=result)
                continue
            else:
                results = []
                hook_results_by_call = {}
                for call in batch.calls:
                    hook_decision = await self._before_tool(call)
                    hook_results_by_call[call.id] = hook_decision.results
                    if hook_decision.blocked and hook_decision.tool_result is not None:
                        result = hook_decision.tool_result
                        after_results = await self._after_tool(call, result)
                        hook_results_by_call[call.id] = (*hook_results_by_call[call.id], *after_results)
                        results.append(result)
                        continue
                    rejected = self.policy.validate_call(call, self.registry)
                    if rejected is not None:
                        result = rejected
                        after_results = await self._after_tool(call, result)
                        hook_results_by_call[call.id] = (*hook_results_by_call[call.id], *after_results)
                        results.append(result)
                        continue
                    if self.permission_controller is None:
                        result = await self._execute(call)
                        after_results = await self._after_tool(call, result)
                        hook_results_by_call[call.id] = (*hook_results_by_call[call.id], *after_results)
                        results.append(result)
                        continue
                    tool = self.registry.get(call.name)
                    if tool is None:
                        result = await self._execute(call)
                        after_results = await self._after_tool(call, result)
                        hook_results_by_call[call.id] = (*hook_results_by_call[call.id], *after_results)
                        results.append(result)
                        continue
                    decision = self.permission_controller.evaluate(call, tool.spec)
                    if decision.kind == "allow":
                        result = await self._execute(call)
                        after_results = await self._after_tool(call, result)
                        hook_results_by_call[call.id] = (*hook_results_by_call[call.id], *after_results)
                        results.append(result)
                        continue
                    if decision.kind == "deny" or decision.prompt is None:
                        result = self.permission_controller.denial_result(call, decision)
                        after_results = await self._after_tool(call, result)
                        hook_results_by_call[call.id] = (*hook_results_by_call[call.id], *after_results)
                        results.append(result)
                        continue
                    yield TurnEvent(
                        type="permission_requested",
                        tool_call=call,
                        permission=PermissionEventPayload(prompt=decision.prompt, decision=decision),
                    )
                    resolved = await self.permission_controller.resolve_prompt(decision.prompt)
                    yield TurnEvent(
                        type="permission_resolved",
                        tool_call=call,
                        permission=PermissionEventPayload(prompt=decision.prompt, decision=resolved),
                    )
                    if resolved.kind == "allow":
                        result = await self._execute(call)
                    else:
                        result = self.permission_controller.denial_result(call, resolved)
                    after_results = await self._after_tool(call, result)
                    hook_results_by_call[call.id] = (*hook_results_by_call[call.id], *after_results)
                    results.append(result)

            for call, result in zip(batch.calls, results, strict=True):
                result_by_id[call.id] = result
                for hook_result in hook_results_by_call.get(call.id, ()):
                    yield TurnEvent(type="hook_finished", tool_call=call, hook_result=hook_result)
                yield TurnEvent(type="tool_finished", tool_call=call, tool_result=result)

        self._results = [result_by_id[call.id] for call in calls]

    def results(self) -> tuple[ToolResult, ...]:
        return tuple(self._results)

    async def _execute_or_reject(self, call: ToolCall) -> ToolResult:
        rejected = self.policy.validate_call(call, self.registry)
        if rejected is not None:
            return rejected
        permission_denied = self._permission_denial(call)
        if permission_denied is not None:
            return permission_denied
        return await self._execute(call)

    async def _execute(self, call: ToolCall) -> ToolResult:
        tool = self.registry.get(call.name)
        if tool is None or self.execution_observer is None:
            return await self.executor.execute(call)
        state: object | None = None
        try:
            state = await self.execution_observer.before_execute(call, tool.spec)
        except Exception:
            state = None
        result = await self.executor.execute(call)
        try:
            await self.execution_observer.after_execute(call, tool.spec, result, state)
        except Exception:
            pass
        return result

    async def _execute_or_reject_with_hooks(self, call: ToolCall) -> tuple[ToolResult, tuple[object, ...]]:
        hook_results: tuple[object, ...] = ()
        hook_decision = await self._before_tool(call)
        hook_results = hook_decision.results
        if hook_decision.blocked and hook_decision.tool_result is not None:
            result = hook_decision.tool_result
            return result, (*hook_results, *(await self._after_tool(call, result)))
        result = await self._execute_or_reject(call)
        return result, (*hook_results, *(await self._after_tool(call, result)))

    async def _run_concurrent_batch(
        self,
        calls: Sequence[ToolCall],
    ) -> AsyncIterator[tuple[ToolCall, ToolResult, tuple[object, ...]]]:
        tasks = {
            asyncio.create_task(self._execute_or_reject_with_hooks(call)): call
            for call in calls
        }
        pending = set(tasks)
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    call = tasks[task]
                    result, hook_results = task.result()
                    yield call, result, hook_results
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _before_tool(self, call: ToolCall):
        if self.hook_manager is None or self.hook_context is None:
            from julycode.hooks.models import HookToolDecision

            return HookToolDecision(blocked=False)
        return await self.hook_manager.before_tool(call, self.hook_context)  # type: ignore[attr-defined]

    async def _after_tool(self, call: ToolCall, result: ToolResult) -> tuple[object, ...]:
        if self.hook_manager is None or self.hook_context is None:
            return ()
        return tuple(await self.hook_manager.after_tool(call, result, self.hook_context))  # type: ignore[attr-defined]

    def _permission_denial(self, call: ToolCall) -> ToolResult | None:
        if self.permission_controller is None:
            return None
        tool = self.registry.get(call.name)
        if tool is None:
            return None
        decision = self.permission_controller.evaluate(call, tool.spec)
        if decision.kind == "deny":
            return self.permission_controller.denial_result(call, decision)
        if decision.kind == "prompt":
            return self.permission_controller.denial_result(call, decision)
        return None
