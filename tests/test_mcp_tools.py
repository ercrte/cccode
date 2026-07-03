from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from mewcode.mcp.errors import McpProtocolError, McpToolError
from mewcode.mcp.tools import McpToolDefinition, RemoteMcpTool, make_global_tool_name, parse_global_tool_name
from mewcode.tools.base import ToolContext, ToolExecutionError


class FakeSession:
    def __init__(self, result: Mapping[str, Any] | BaseException) -> None:
        self.result = result
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    async def call_tool(self, remote_name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((remote_name, dict(arguments)))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def definition() -> McpToolDefinition:
    return McpToolDefinition(
        server_name="demo",
        remote_name="echo",
        global_name="demo__echo",
        title="Echo",
        description="Echo text",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )


def context(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


def test_global_tool_name_uses_server_prefix() -> None:
    assert make_global_tool_name("server", "tool") == "server__tool"
    assert parse_global_tool_name("server__tool") == ("server", "tool")
    with pytest.raises(ValueError):
        parse_global_tool_name("tool")


def test_remote_mcp_tool_exposes_tool_spec() -> None:
    tool = RemoteMcpTool(definition(), FakeSession({}))

    assert tool.spec.name == "demo__echo"
    assert "demo" in tool.spec.description
    assert "Echo text" in tool.spec.description
    assert tool.spec.parameters_schema["properties"]["text"]["type"] == "string"
    assert tool.spec.safety == "side_effect"
    assert tool.spec.origin == "mcp:demo"


@pytest.mark.asyncio
async def test_remote_mcp_tool_returns_success_payload(tmp_path: Path) -> None:
    session = FakeSession(
        {
            "content": [{"type": "text", "text": "hello"}],
            "structuredContent": {"text": "hello"},
            "isError": False,
        }
    )
    tool = RemoteMcpTool(definition(), session)

    result = await tool.execute({"text": "hello"}, context(tmp_path))

    assert session.calls == [("echo", {"text": "hello"})]
    assert result == {
        "server": "demo",
        "remote_tool": "echo",
        "content": [{"type": "text", "text": "hello"}],
        "structured_content": {"text": "hello"},
        "is_error": False,
    }


@pytest.mark.asyncio
async def test_remote_mcp_tool_maps_is_error_to_tool_execution_error(tmp_path: Path) -> None:
    tool = RemoteMcpTool(
        definition(),
        FakeSession({"content": [{"type": "text", "text": "business failure"}], "isError": True}),
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute({"text": "hello"}, context(tmp_path))

    assert exc_info.value.error_type == "mcp_tool_error"
    assert exc_info.value.data["is_error"] is True
    assert "business failure" in str(exc_info.value)


@pytest.mark.asyncio
async def test_remote_mcp_tool_maps_protocol_and_invalid_response_errors(tmp_path: Path) -> None:
    protocol_tool = RemoteMcpTool(definition(), FakeSession(McpProtocolError("bad protocol", code=-32000)))
    with pytest.raises(ToolExecutionError) as protocol_exc:
        await protocol_tool.execute({}, context(tmp_path))
    assert protocol_exc.value.error_type == "mcp_protocol_error"
    assert protocol_exc.value.data["code"] == -32000

    invalid_tool = RemoteMcpTool(definition(), FakeSession(McpToolError("bad shape")))
    with pytest.raises(ToolExecutionError) as invalid_exc:
        await invalid_tool.execute({}, context(tmp_path))
    assert invalid_exc.value.error_type == "mcp_invalid_response"
