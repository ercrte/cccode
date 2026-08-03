from __future__ import annotations

import asyncio
import time

from julycode.errors import redact_secret
from julycode.hooks.actions import HookActionRunner
from julycode.hooks.conditions import rule_matches
from julycode.hooks.models import (
    HookConfig,
    HookEvent,
    HookExecutionResult,
    HookPromptInjection,
    HookRuntimeContext,
    HookRuntimeState,
    HookToolDecision,
)
from julycode.tools.base import ToolCall, ToolResult


class HookManager:
    def __init__(
        self,
        config: HookConfig,
        action_runner: HookActionRunner | None = None,
    ) -> None:
        self.config = config
        self.action_runner = action_runner or HookActionRunner()
        self.state = HookRuntimeState()

    async def emit(self, event: HookEvent, context: HookRuntimeContext) -> tuple[HookExecutionResult, ...]:
        results: list[HookExecutionResult] = []
        for rule in self.config.rules:
            if not rule_matches(rule, event):
                continue
            result = await self._run_or_schedule(rule, event, context)
            if result is not None:
                results.append(result)
        return tuple(results)

    async def before_tool(self, call: ToolCall, context: HookRuntimeContext) -> HookToolDecision:
        event = HookEvent(
            name="tool.before",
            data={
                "tool": {
                    "id": call.id,
                    "name": call.name,
                    "arguments": dict(call.arguments),
                },
                "turn": {"mode": context.mode},
            },
        )
        results: list[HookExecutionResult] = []
        blocked_result: ToolResult | None = None
        for rule in self.config.rules:
            if not rule_matches(rule, event):
                continue
            result = await self._run_or_schedule(rule, event, context)
            if result is not None:
                results.append(result)
            if result is None or result.status == "failed" or rule.action.tool_block is None:
                continue
            tool_result = _block_result(call, rule, result.elapsed_ms)
            blocked = HookExecutionResult(
                rule_id=rule.id,
                event=rule.event,
                status="blocked",
                message=tool_result.error or "",
                elapsed_ms=result.elapsed_ms,
                tool_result=tool_result,
            )
            results.append(blocked)
            blocked_result = blocked_result or tool_result
        return HookToolDecision(blocked=blocked_result is not None, results=tuple(results), tool_result=blocked_result)

    async def after_tool(
        self,
        call: ToolCall,
        result: ToolResult,
        context: HookRuntimeContext,
    ) -> tuple[HookExecutionResult, ...]:
        event = HookEvent(
            name="tool.after",
            data={
                "tool": {
                    "id": call.id,
                    "name": call.name,
                    "arguments": dict(call.arguments),
                },
                "result": {"success": result.success, "error_type": result.error_type},
                "turn": {"mode": context.mode},
            },
        )
        return await self.emit(event, context)

    def pending_prompt_injections(self) -> tuple[HookPromptInjection, ...]:
        return tuple(self.state.prompt_injections)

    def consume_prompt_injections(self) -> tuple[HookPromptInjection, ...]:
        injections = tuple(self.state.prompt_injections)
        self.state.prompt_injections.clear()
        return injections

    def completed_background_results(self) -> tuple[HookExecutionResult, ...]:
        return tuple(self.state.completed_background)

    async def close(self) -> None:
        tasks = tuple(self.state.background_tasks)
        if not tasks:
            return
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.state.background_tasks.difference_update(tasks)

    async def _run_or_schedule(
        self,
        rule,
        event: HookEvent,
        context: HookRuntimeContext,
    ) -> HookExecutionResult | None:
        if rule.once and rule.id in self.state.executed_once:
            return HookExecutionResult(rule_id=rule.id, event=rule.event, status="skipped_once", message="Hook once 已跳过")
        if rule.once:
            self.state.executed_once.add(rule.id)
        if rule.background:
            task = asyncio.create_task(self._run_rule(rule, event, context))
            self.state.background_tasks.add(task)
            task.add_done_callback(self._background_done)
            return HookExecutionResult(rule_id=rule.id, event=rule.event, status="success", message="Hook 后台任务已启动")
        return await self._run_rule(rule, event, context)

    async def _run_rule(self, rule, event: HookEvent, context: HookRuntimeContext) -> HookExecutionResult:
        started = time.monotonic()
        try:
            result = await self.action_runner.run(rule, event, context)
        except Exception as exc:
            return HookExecutionResult(
                rule_id=rule.id,
                event=rule.event,
                status="failed",
                message=f"Hook 动作执行失败: {redact_secret(str(exc))}",
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        if result.prompt_injection is not None:
            self.state.prompt_injections.append(result.prompt_injection)
        return result

    def _background_done(self, task: asyncio.Task[HookExecutionResult]) -> None:
        self.state.background_tasks.discard(task)
        try:
            result = task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            result = HookExecutionResult(
                rule_id="background",
                event="system.error",
                status="failed",
                message=f"后台 Hook 失败: {redact_secret(str(exc))}",
            )
        self.state.completed_background.append(result)


def create_hook_manager(config: HookConfig) -> HookManager:
    return HookManager(config)


def _block_result(call: ToolCall, rule, elapsed_ms: int) -> ToolResult:
    block = rule.action.tool_block
    reason = redact_secret(block.reason if block is not None else "Hook 拦截工具调用")
    error_type = block.error_type if block is not None else "hook_blocked"
    return ToolResult(
        tool_call_id=call.id,
        tool_name=call.name,
        success=False,
        data={"reason": reason, "hook_rule": rule.id},
        error_type=error_type,
        error=reason,
        elapsed_ms=elapsed_ms,
    )
