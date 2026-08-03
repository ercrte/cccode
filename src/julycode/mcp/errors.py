from __future__ import annotations

from typing import Any

from julycode.errors import JulyCodeError


class McpError(JulyCodeError):
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


class McpOAuthError(McpError):
    """不携带令牌等秘密值的 OAuth 公开错误。"""


class McpOAuthConfigError(McpOAuthError):
    """OAuth 配置无效。"""


class McpOAuthDiscoveryError(McpOAuthError):
    """OAuth 元数据发现失败。"""


class McpOAuthCallbackError(McpOAuthError):
    """OAuth 回环回调失败。"""


class McpOAuthStorageError(McpOAuthError):
    """OAuth 凭据存储失败。"""


class McpAuthorizationRequired(McpOAuthError):
    """MCP Server 需要用户显式授权。"""

    def __init__(self, server_name: str, message: str | None = None) -> None:
        self.server_name = server_name
        super().__init__(message or f"MCP Server {server_name} 需要 OAuth 授权")
