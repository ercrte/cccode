from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from julycode.errors import redact_secret
from julycode.hooks.models import (
    HookExecutionResult,
    HookPromptInjection,
    HookRule,
    HookRuntimeContext,
)
from julycode.permissions.models import PermissionDecision
from julycode.tools.base import ToolCall, ToolResult
from julycode.tools.scheduler import ToolPolicy


class HookActionRunner:
    def __init__(self, http_client_factory: Callable[[], httpx.AsyncClient] | None = None) -> None:
        self.http_client_factory = http_client_factory

    async def run(self, rule: HookRule, event, context: HookRuntimeContext) -> HookExecutionResult:
        started = time.monotonic()
        try:
            action = rule.action
            if action.type == "prompt" and action.prompt is not None:
                injection = HookPromptInjection(rule_id=rule.id, text=action.prompt.text)
                return self._result(rule, "success", "已注入 Hook 提示词", started, prompt_injection=injection)
            if action.type == "sub_agent" and action.sub_agent is not None:
                return self._result(rule, "placeholder", f"子 Agent 动作占位: {action.sub_agent.name}", started)
            if action.type == "command" and action.command is not None:
                return await self._run_command(rule, context, started)
            if action.type == "http" and action.http is not None:
                return await self._run_http(rule, context, started)
            return self._result(rule, "failed", "Hook 动作配置不完整", started)
        except Exception as exc:
            return self._result(rule, "failed", f"Hook 动作执行失败: {redact_secret(str(exc))}", started)

    async def _run_command(
        self,
        rule: HookRule,
        context: HookRuntimeContext,
        started: float,
    ) -> HookExecutionResult:
        action = rule.action.command
        if action is None:
            return self._result(rule, "failed", "command 动作配置缺失", started)
        call = ToolCall(
            id=f"hook-{rule.id}",
            name="run_command",
            arguments={"command": action.command, "timeout_seconds": action.timeout_seconds},
        )
        rejected = ToolPolicy(context.mode, context.allowed_tool_names).validate_call(call, context.registry)
        if rejected is not None:
            return self._result(rule, "failed", rejected.error or rejected.error_type or "Hook command 不允许执行", started)
        tool = context.registry.get("run_command")
        if tool is None:
            return self._result(rule, "failed", "run_command 工具不可用", started)
        if context.permission_controller is not None:
            decision = context.permission_controller.evaluate(call, tool.spec)
            if decision.kind != "allow":
                return self._result(rule, "failed", _permission_message(decision), started)
        result = await context.executor.execute(call)
        return self._tool_result(rule, result, started)

    async def _run_http(
        self,
        rule: HookRule,
        context: HookRuntimeContext,
        started: float,
    ) -> HookExecutionResult:
        action = rule.action.http
        if action is None:
            return self._result(rule, "failed", "HTTP 动作配置缺失", started)
        if context.mode == "plan":
            return self._result(rule, "failed", "Plan Mode 不允许执行 HTTP Hook", started)
        if context.allowed_tool_names is not None:
            return self._result(rule, "failed", "当前 Skill 工具白名单不允许执行 HTTP Hook", started)

        client: httpx.AsyncClient | None = None
        close_client = False
        try:
            if self.http_client_factory is None:
                client = httpx.AsyncClient(timeout=action.timeout_seconds, trust_env=False)
                close_client = True
            else:
                client = self.http_client_factory()
            response = await client.request(
                action.method,
                action.url,
                headers=dict(action.headers),
                content=action.body,
                json=action.json_body,
                timeout=action.timeout_seconds,
            )
            summary = redact_secret(response.text[:500])
            status = "success" if 200 <= response.status_code < 400 else "failed"
            return self._result(
                rule,
                status,  # type: ignore[arg-type]
                f"HTTP {response.status_code}: {summary}",
                started,
            )
        except httpx.TimeoutException:
            return self._result(rule, "failed", f"HTTP 请求超时，超过 {action.timeout_seconds:g} 秒", started)
        except httpx.HTTPError as exc:
            return self._result(rule, "failed", f"HTTP 请求失败: {redact_secret(str(exc))}", started)
        finally:
            if close_client and client is not None:
                await client.aclose()

    def _tool_result(self, rule: HookRule, result: ToolResult, started: float) -> HookExecutionResult:
        exit_code = result.data.get("exit_code")
        if result.success and exit_code in {None, 0}:
            status = "success"
        else:
            status = "failed"
        message_parts = []
        if exit_code is not None:
            message_parts.append(f"exit_code={exit_code}")
        if result.error_type:
            message_parts.append(result.error_type)
        if result.error:
            message_parts.append(result.error)
        stdout = result.data.get("stdout")
        stderr = result.data.get("stderr")
        if stdout:
            message_parts.append(str(stdout)[:500])
        if stderr:
            message_parts.append(str(stderr)[:500])
        return self._result(rule, status, redact_secret(" | ".join(message_parts) or "命令执行完成"), started)

    def _result(
        self,
        rule: HookRule,
        status: str,
        message: str,
        started: float,
        *,
        prompt_injection: HookPromptInjection | None = None,
    ) -> HookExecutionResult:
        return HookExecutionResult(
            rule_id=rule.id,
            event=rule.event,
            status=status,  # type: ignore[arg-type]
            message=redact_secret(message),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            prompt_injection=prompt_injection,
        )


def _permission_message(decision: PermissionDecision) -> str:
    if decision.kind == "prompt":
        return f"Hook command 需要权限确认，已跳过: {decision.reason}"
    return f"Hook command 权限拒绝: {decision.reason}"
