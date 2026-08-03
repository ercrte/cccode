from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from julycode.mcp.errors import McpProtocolError, McpToolError
from julycode.mcp.search import McpToolMatch, McpToolSearchResult
from julycode.mcp.tools import (
    SEARCH_MCP_TOOLS_NAME,
    McpToolDefinition,
    RemoteMcpTool,
    SearchMcpToolsTool,
    make_global_tool_name,
    parse_global_tool_name,
)
from julycode.tools.base import ToolContext, ToolExecutionError


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
    assert tool.spec.visibility == "deferred"
    assert tool.spec.origin == "mcp:demo"


class FakeSearchProvider:
    def __init__(self, result: McpToolSearchResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str | None]] = []

    def search_tools(self, query: str, server_name: str | None = None) -> McpToolSearchResult:
        self.calls.append((query, server_name))
        return self.result


@pytest.mark.asyncio
async def test_search_mcp_tool_is_lightweight_system_read_tool(tmp_path: Path) -> None:
    provider = FakeSearchProvider(
        McpToolSearchResult(
            status="ok",
            query="pull request",
            server_name="github",
            matches=(
                McpToolMatch(
                    global_name="github__pull_request_read",
                    server_name="github",
                    remote_name="pull_request_read",
                    title="Read pull request",
                    summary="Read a pull request",
                    score=900,
                ),
            ),
        )
    )
    tool = SearchMcpToolsTool(provider)

    result = await tool.execute({"query": " pull request ", "server": "github"}, context(tmp_path))

    assert tool.spec.name == SEARCH_MCP_TOOLS_NAME
    assert tool.spec.safety == "read_only"
    assert tool.spec.visibility == "system"
    assert set(tool.spec.parameters_schema["properties"]) == {"query", "server"}
    assert provider.calls == [("pull request", "github")]
    assert result["matches"] == [
        {
            "name": "github__pull_request_read",
            "server": "github",
            "title": "Read pull request",
            "summary": "Read a pull request",
        }
    ]
    assert "score" not in str(result)
    assert "input_schema" not in str(result)


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
