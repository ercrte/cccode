from __future__ import annotations

import asyncio
import time
from typing import Any

from julycode.errors import redact_secret
from julycode.tools.base import ToolCall, ToolContext, ToolExecutionError, ToolResult
from julycode.tools.registry import ToolRegistry
from julycode.tools.validation import validate_arguments


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, context: ToolContext) -> None:
        self.registry = registry
        self.context = context

    async def execute(self, call: ToolCall) -> ToolResult:
        started = time.monotonic()
        if call.parse_error:
            return self._failure(call, "invalid_json", call.parse_error, started)

        tool = self.registry.get(call.name)
        if tool is None:
            return self._failure(call, "unknown_tool", f"未知工具: {call.name}", started)

        validation_errors = validate_arguments(tool.spec.parameters_schema, call.arguments)
        if validation_errors:
            return self._failure(call, "invalid_arguments", "；".join(validation_errors), started)

        timeout = self._timeout_seconds(call, tool.spec.timeout_seconds)
        if timeout <= 0:
            return self._failure(call, "invalid_arguments", "timeout_seconds 必须大于 0", started)

        try:
            data = await asyncio.wait_for(tool.execute(call.arguments, self.context), timeout=timeout + 0.2)
            return ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=True,
                data=self._redact_value(dict(data)),
                elapsed_ms=self._elapsed_ms(started),
            )
        except asyncio.TimeoutError:
            return self._failure(call, "timeout", f"工具执行超时，超过 {timeout:g} 秒", started)
        except ToolExecutionError as exc:
            data = self._redact_value(dict(exc.data))
            return ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=False,
                data=data,
                error_type=exc.error_type,
                error=redact_secret(str(exc)),
                elapsed_ms=self._elapsed_ms(started),
            )
        except Exception as exc:
            return self._failure(call, "unexpected_error", f"工具执行出现未预期错误: {exc}", started)

    def _timeout_seconds(self, call: ToolCall, default: float) -> float:
        raw = call.arguments.get("timeout_seconds", default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return -1.0

    def _failure(self, call: ToolCall, error_type: str, error: str, started: float) -> ToolResult:
        return ToolResult(
            tool_call_id=call.id,
            tool_name=call.name,
            success=False,
            data={},
            error_type=error_type,
            error=redact_secret(error),
            elapsed_ms=self._elapsed_ms(started),
        )

    def _elapsed_ms(self, started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact_secret(value)
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact_value(item) for key, item in value.items()}
        return value
