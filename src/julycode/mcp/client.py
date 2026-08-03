from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from julycode import __version__
from julycode.config import McpServerConfig
from julycode.mcp.errors import McpProtocolError, McpToolError
from julycode.mcp.tools import McpToolDefinition, make_global_tool_name
from julycode.mcp.transport import DEFAULT_PROTOCOL_VERSION, McpTransport


class McpClientSession:
    def __init__(self, server: McpServerConfig, transport: McpTransport) -> None:
        self.server = server
        self.transport = transport
        self.initialized = False

    async def initialize(self) -> None:
        await self.transport.start()
        result = await self.transport.request(
            "initialize",
            {
                "protocolVersion": DEFAULT_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "JulyCode",
                    "title": "JulyCode",
                    "version": __version__,
                },
            },
        )
        protocol_version = str(result.get("protocolVersion", ""))
        if protocol_version != DEFAULT_PROTOCOL_VERSION:
            raise McpProtocolError(f"MCP Server {self.server.name} 不支持协议版本 {DEFAULT_PROTOCOL_VERSION}")
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, Mapping) or "tools" not in capabilities:
            raise McpProtocolError(f"MCP Server {self.server.name} 未声明 tools capability")
        await self.transport.notify("notifications/initialized")
        self.initialized = True

    async def list_tools(self) -> tuple[McpToolDefinition, ...]:
        definitions: list[McpToolDefinition] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else None
            result = await self.transport.request("tools/list", params)
            raw_tools = result.get("tools")
            if not isinstance(raw_tools, list):
                raise McpProtocolError(f"MCP Server {self.server.name} tools/list 未返回 tools 数组")
            for raw_tool in raw_tools:
                definitions.append(self._parse_tool_definition(raw_tool))
            next_cursor = result.get("nextCursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)
        return tuple(definitions)

    async def call_tool(self, remote_name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        result = await self.transport.request(
            "tools/call",
            {"name": remote_name, "arguments": dict(arguments)},
        )
        content = result.get("content", [])
        if "content" in result and not isinstance(content, list):
            raise McpToolError(f"MCP Server {self.server.name} tools/call 返回的 content 不是数组")
        if "structuredContent" in result and not isinstance(result.get("structuredContent"), Mapping):
            raise McpToolError(f"MCP Server {self.server.name} tools/call 返回的 structuredContent 不是对象")
        return result

    async def close(self) -> None:
        await self.transport.close()

    def _parse_tool_definition(self, raw_tool: Any) -> McpToolDefinition:
        if not isinstance(raw_tool, Mapping):
            raise McpProtocolError(f"MCP Server {self.server.name} tools/list 包含无效工具定义")
        remote_name = str(raw_tool.get("name", "")).strip()
        if not remote_name:
            raise McpProtocolError(f"MCP Server {self.server.name} tools/list 工具缺少 name")
        input_schema = raw_tool.get("inputSchema")
        if not isinstance(input_schema, Mapping):
            raise McpProtocolError(f"MCP Server {self.server.name} 工具 {remote_name} 缺少对象形式 inputSchema")
        output_schema = raw_tool.get("outputSchema")
        return McpToolDefinition(
            server_name=self.server.name,
            remote_name=remote_name,
            global_name=make_global_tool_name(self.server.name, remote_name),
            title=str(raw_tool["title"]) if raw_tool.get("title") is not None else None,
            description=str(raw_tool.get("description", "")),
            input_schema=dict(input_schema),
            output_schema=dict(output_schema) if isinstance(output_schema, Mapping) else None,
        )
