from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from julycode.config import McpServerConfig
from julycode.mcp.client import McpClientSession
from julycode.mcp.errors import McpProtocolError, McpToolError


class FakeTransport:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, Mapping[str, Any] | None]] = []
        self.notifications: list[tuple[str, Mapping[str, Any] | None]] = []
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        self.requests.append((method, params))
        return self.responses.pop(0)

    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        self.notifications.append((method, params))

    async def close(self) -> None:
        self.closed = True


def server() -> McpServerConfig:
    return McpServerConfig(name="demo", transport="stdio", command="python")


def initialize_result(*, capabilities: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "protocolVersion": "2025-06-18",
        "capabilities": capabilities if capabilities is not None else {"tools": {"listChanged": False}},
        "serverInfo": {"name": "fixture", "version": "1"},
    }


def tool(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "title": name.title(),
        "description": f"{name} description",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    }


@pytest.mark.asyncio
async def test_client_initialize_sends_initialize_and_initialized_notification() -> None:
    transport = FakeTransport([initialize_result()])
    session = McpClientSession(server(), transport)

    await session.initialize()

    assert transport.started is True
    assert transport.requests[0][0] == "initialize"
    assert transport.requests[0][1]["protocolVersion"] == "2025-06-18"
    assert transport.requests[0][1]["capabilities"] == {}
    assert transport.requests[0][1]["clientInfo"]["name"] == "JulyCode"
    assert transport.notifications == [("notifications/initialized", None)]
    assert session.initialized is True


@pytest.mark.asyncio
async def test_client_initialize_requires_tools_capability() -> None:
    transport = FakeTransport([initialize_result(capabilities={"resources": {}})])
    session = McpClientSession(server(), transport)

    with pytest.raises(McpProtocolError, match="tools"):
        await session.initialize()


@pytest.mark.asyncio
async def test_client_list_tools_handles_pagination() -> None:
    transport = FakeTransport(
        [
            {"tools": [tool("echo")], "nextCursor": "page-2"},
            {"tools": [tool("same_name")]},
        ]
    )
    session = McpClientSession(server(), transport)

    definitions = await session.list_tools()

    assert [definition.remote_name for definition in definitions] == ["echo", "same_name"]
    assert [definition.global_name for definition in definitions] == ["demo__echo", "demo__same_name"]
    assert transport.requests == [("tools/list", None), ("tools/list", {"cursor": "page-2"})]


@pytest.mark.asyncio
async def test_client_call_tool_sends_tools_call() -> None:
    transport = FakeTransport(
        [
            {
                "content": [{"type": "text", "text": "hello"}],
                "structuredContent": {"text": "hello"},
                "isError": False,
            }
        ]
    )
    session = McpClientSession(server(), transport)

    result = await session.call_tool("echo", {"text": "hello"})

    assert result["structuredContent"] == {"text": "hello"}
    assert transport.requests == [("tools/call", {"name": "echo", "arguments": {"text": "hello"}})]


@pytest.mark.asyncio
async def test_client_call_tool_rejects_invalid_result_shape() -> None:
    transport = FakeTransport([{"content": "not-list"}])
    session = McpClientSession(server(), transport)

    with pytest.raises(McpToolError, match="content"):
        await session.call_tool("echo", {})
