from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from mewcode.tools.base import ToolCall, ToolContext, ToolExecutionError, ToolSpec
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry


class EchoTool:
    spec = ToolSpec(
        name="echo",
        description="echo",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        timeout_seconds=0.2,
    )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = context
        return {"text": arguments["text"]}


class FailingTool(EchoTool):
    spec = ToolSpec(
        name="fail",
        description="fail",
        parameters_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = arguments, context
        raise ToolExecutionError("业务失败", error_type="business_error", data={"detail": "x"})


class SlowTool(EchoTool):
    spec = ToolSpec(
        name="slow",
        description="slow",
        parameters_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        timeout_seconds=0.05,
    )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = arguments, context
        await asyncio.sleep(1)
        return {"ok": True}


class BrokenTool(EchoTool):
    spec = ToolSpec(
        name="broken",
        description="broken",
        parameters_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = arguments, context
        raise RuntimeError("boom")


def make_executor(tmp_path: Path) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(FailingTool())
    registry.register(SlowTool())
    registry.register(BrokenTool())
    return ToolExecutor(registry, ToolContext(cwd=tmp_path))


@pytest.mark.asyncio
async def test_executor_runs_registered_tool(tmp_path: Path) -> None:
    result = await make_executor(tmp_path).execute(ToolCall(id="c1", name="echo", arguments={"text": "hi"}))

    assert result.success is True
    assert result.data == {"text": "hi"}


@pytest.mark.asyncio
async def test_executor_reports_unknown_tool(tmp_path: Path) -> None:
    result = await make_executor(tmp_path).execute(ToolCall(id="c1", name="missing", arguments={}))

    assert result.success is False
    assert result.error_type == "unknown_tool"


@pytest.mark.asyncio
async def test_executor_reports_invalid_json(tmp_path: Path) -> None:
    result = await make_executor(tmp_path).execute(
        ToolCall(id="c1", name="echo", raw_arguments="{", parse_error="JSON 解析失败")
    )

    assert result.success is False
    assert result.error_type == "invalid_json"


@pytest.mark.asyncio
async def test_executor_reports_invalid_arguments(tmp_path: Path) -> None:
    result = await make_executor(tmp_path).execute(ToolCall(id="c1", name="echo", arguments={}))

    assert result.success is False
    assert result.error_type == "invalid_arguments"
    assert "text" in (result.error or "")


@pytest.mark.asyncio
async def test_executor_wraps_tool_execution_error(tmp_path: Path) -> None:
    result = await make_executor(tmp_path).execute(ToolCall(id="c1", name="fail", arguments={}))

    assert result.success is False
    assert result.error_type == "business_error"
    assert result.data == {"detail": "x"}


@pytest.mark.asyncio
async def test_executor_wraps_timeout(tmp_path: Path) -> None:
    result = await make_executor(tmp_path).execute(ToolCall(id="c1", name="slow", arguments={}))

    assert result.success is False
    assert result.error_type == "timeout"


@pytest.mark.asyncio
async def test_executor_wraps_unexpected_exception(tmp_path: Path) -> None:
    result = await make_executor(tmp_path).execute(ToolCall(id="c1", name="broken", arguments={}))

    assert result.success is False
    assert result.error_type == "unexpected_error"
