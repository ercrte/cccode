from __future__ import annotations

from typing import Any

from mewcode.errors import MewCodeError


class McpError(MewCodeError):
    """MCP 子系统可展示给用户的基础错误。"""


class McpConfigError(McpError):
    """MCP 配置不可用。"""


class McpConnectionError(McpError):
    """MCP 连接或传输失败。"""


class McpTimeoutError(McpConnectionError):
    """MCP 请求超时。"""


class McpProtocolError(McpError):
    """MCP JSON-RPC 协议错误。"""

    def __init__(self, message: str, *, code: int | None = None, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class McpToolError(McpError):
    """MCP 工具结果不可用。"""
