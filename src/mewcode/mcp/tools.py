from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mewcode.mcp.errors import McpConnectionError, McpProtocolError, McpTimeoutError, McpToolError
from mewcode.tools.base import ToolContext, ToolExecutionError, ToolSpec


@dataclass(frozen=True)
class McpToolDefinition:
    server_name: str
    remote_name: str
    global_name: str
    title: str | None
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None = None


def make_global_tool_name(server_name: str, remote_name: str) -> str:
    return f"{server_name}__{remote_name}"


def parse_global_tool_name(name: str) -> tuple[str, str]:
    if "__" not in name:
        raise ValueError(f"不是 MCP 全局工具名: {name}")
    server_name, remote_name = name.split("__", 1)
    if not server_name or not remote_name:
        raise ValueError(f"不是 MCP 全局工具名: {name}")
    return server_name, remote_name


class RemoteMcpTool:
    def __init__(self, definition: McpToolDefinition, session: Any) -> None:
        self.definition = definition
        self.session = session
        self.spec = ToolSpec(
            name=definition.global_name,
            description=self._description(definition),
            parameters_schema=dict(definition.input_schema),
            safety="side_effect",
            origin=f"mcp:{definition.server_name}",
        )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = context
        try:
            result = await self.session.call_tool(self.definition.remote_name, arguments)
        except McpTimeoutError as exc:
            raise ToolExecutionError(str(exc), error_type="timeout") from exc
        except asyncio.TimeoutError as exc:
            raise ToolExecutionError("MCP 工具调用超时", error_type="timeout") from exc
        except McpProtocolError as exc:
            raise ToolExecutionError(str(exc), error_type="mcp_protocol_error", data=self._error_data(exc)) from exc
        except McpToolError as exc:
            raise ToolExecutionError(str(exc), error_type="mcp_invalid_response") from exc
        except McpConnectionError as exc:
            raise ToolExecutionError(str(exc), error_type="mcp_connection_error") from exc

        payload = self._payload(result)
        if payload["is_error"]:
            raise ToolExecutionError(
                self._content_summary(payload["content"]) or "MCP 工具返回错误",
                error_type="mcp_tool_error",
                data=payload,
            )
        return payload

    def _payload(self, result: Mapping[str, Any]) -> dict[str, Any]:
        content = result.get("content", [])
        if not isinstance(content, list):
            raise ToolExecutionError("MCP 工具结果 content 必须是数组", error_type="mcp_invalid_response")
        is_error = bool(result.get("isError", False))
        structured = result.get("structuredContent")
        return {
            "server": self.definition.server_name,
            "remote_tool": self.definition.remote_name,
            "content": content,
            "structured_content": structured if isinstance(structured, Mapping) else None,
            "is_error": is_error,
        }

    def _description(self, definition: McpToolDefinition) -> str:
        parts = [f"MCP Server `{definition.server_name}` 的远端工具 `{definition.remote_name}`。"]
        if definition.title:
            parts.append(str(definition.title))
        if definition.description:
            parts.append(str(definition.description))
        return "\n".join(parts)

    def _content_summary(self, content: object) -> str:
        if not isinstance(content, list):
            return ""
        texts = []
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "text" and item.get("text"):
                texts.append(str(item["text"]))
        return "\n".join(texts)

    def _error_data(self, exc: McpProtocolError) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if exc.code is not None:
            data["code"] = exc.code
        if exc.data is not None:
            data["data"] = exc.data
        return data
