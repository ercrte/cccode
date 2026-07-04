from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from mewcode.config import McpConfig, McpServerConfig
from mewcode.errors import redact_secret
from mewcode.mcp.client import McpClientSession
from mewcode.mcp.errors import McpAuthorizationRequired, McpOAuthError
from mewcode.mcp.oauth.client import AuthorizationUrlCallback, McpOAuthSession
from mewcode.mcp.oauth.models import McpOAuthStatus
from mewcode.mcp.oauth.store import CredentialStore, OAuthCredentialStore
from mewcode.mcp.scope import McpTurnState
from mewcode.mcp.search import McpPromptContext, McpToolCatalog, McpToolMatch, McpToolSearchResult
from mewcode.mcp.tools import (
    SEARCH_MCP_TOOLS_NAME,
    McpToolDefinition,
    RemoteMcpTool,
    SearchMcpToolsTool,
)
from mewcode.mcp.transport import McpTransport, StdioMcpTransport, StreamableHttpMcpTransport
from mewcode.tools.registry import ToolRegistry


TransportFactory = Callable[[McpServerConfig], McpTransport]
OAuthSessionFactory = Callable[[McpServerConfig], McpOAuthSession]


@dataclass(frozen=True)
class McpLoadReport:
    loaded_servers: tuple[str, ...] = ()
    failed_servers: dict[str, str] = field(default_factory=dict)
    discovered_tools: tuple[str, ...] = ()
    registered_tools: tuple[str, ...] = ()
    failed_tools: dict[str, str] = field(default_factory=dict)
    oauth_status: dict[str, McpOAuthStatus] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


class McpManager:
    def __init__(
        self,
        config: McpConfig,
        transport_factory: TransportFactory | None = None,
        *,
        credential_store: CredentialStore | None = None,
        oauth_session_factory: OAuthSessionFactory | None = None,
    ) -> None:
        self.config = config
        self._transport_factory = transport_factory
        self._credential_store = credential_store or OAuthCredentialStore()
        self._oauth_session_factory = oauth_session_factory
        self._sessions: dict[str, McpClientSession] = {}
        self._oauth_sessions: dict[str, McpOAuthSession] = {}
        self._oauth_restored: set[str] = set()
        self._definitions: dict[str, tuple[McpToolDefinition, ...]] = {}
        self._loaded_servers: list[str] = []
        self._failed_servers: dict[str, str] = {}
        self._registered_tools: list[str] = []
        self._failed_tools: dict[str, str] = {}
        self._registry: ToolRegistry | None = None
        self._catalog = McpToolCatalog()

    async def initialize(self) -> None:
        for server in self.config.servers.values():
            await self._initialize_server(server)

    def register_tools(self, registry: ToolRegistry) -> None:
        self._registry = registry
        registry.unregister_origin("mcp:discovery")
        if self.config.servers:
            try:
                registry.register(SearchMcpToolsTool(self))
            except Exception as exc:
                self._failed_tools[SEARCH_MCP_TOOLS_NAME] = redact_secret(str(exc))
            else:
                self._failed_tools.pop(SEARCH_MCP_TOOLS_NAME, None)
        for server_name in tuple(self._definitions):
            self._register_server_tools(server_name)
        self._refresh_searchable()

    def search_tools(self, query: str, server_name: str | None = None) -> McpToolSearchResult:
        normalized_query = query.strip()
        if server_name is not None and server_name not in self.config.servers:
            available = ", ".join(sorted(self.config.servers)) or "无"
            return McpToolSearchResult(
                status="server_not_found",
                query=normalized_query,
                server_name=server_name,
                message=f"未配置 MCP Server {server_name}；已配置 Server：{available}",
            )
        if server_name is not None and not self._server_search_available(server_name):
            return McpToolSearchResult(
                status="server_unavailable",
                query=normalized_query,
                server_name=server_name,
                message=f"MCP Server {server_name} 当前不可用",
            )

        matches = self._catalog.search(normalized_query, server_name=server_name)
        safe_matches = tuple(self._redact_match(match) for match in matches)
        if not safe_matches:
            return McpToolSearchResult(
                status="no_match",
                query=normalized_query,
                server_name=server_name,
                message="未找到相关 MCP 工具；请调整能力关键词后重试",
            )
        return McpToolSearchResult(
            status="ok",
            query=normalized_query,
            server_name=server_name,
            matches=safe_matches,
            message=f"找到 {len(safe_matches)} 个候选工具，将在下一次模型迭代按需加载",
        )

    def create_turn_state(self) -> McpTurnState:
        return McpTurnState(self.prompt_context)

    def prompt_context(self) -> McpPromptContext:
        return McpPromptContext(connected_servers=self._catalog.server_summaries())

    def load_report(self) -> McpLoadReport:
        oauth_status = {name: session.status for name, session in self._oauth_sessions.items()}
        warnings = tuple(
            dict.fromkeys(
                status.warning
                for status in oauth_status.values()
                if status.warning
            )
        )
        return McpLoadReport(
            loaded_servers=tuple(self._loaded_servers),
            failed_servers=dict(self._failed_servers),
            discovered_tools=tuple(definition.global_name for definition in self._catalog.definitions()),
            registered_tools=tuple(self._registered_tools),
            failed_tools=dict(self._failed_tools),
            oauth_status=oauth_status,
            warnings=warnings,
        )

    async def authorize_server(
        self,
        server_name: str,
        on_authorization_url: AuthorizationUrlCallback | None = None,
    ) -> str:
        server = self._oauth_server(server_name)
        oauth = await self._oauth_session(server)
        if oauth.status.state == "authorizing":
            raise McpOAuthError(f"MCP Server {server_name} 正在授权")
        if oauth.status.state == "authorized":
            raise McpOAuthError(f"MCP Server {server_name} 已授权；如需更换账号请先 logout")
        await oauth.authorize(on_authorization_url)
        await self._initialize_server(server, force=True)
        if server_name not in self._sessions:
            detail = self._failed_servers.get(server_name, "MCP 初始化失败")
            raise McpOAuthError(f"OAuth 授权成功，但 MCP Server 初始化失败: {detail}")
        if self._registry is not None:
            self._register_server_tools(server_name)
        return f"MCP Server {server_name} OAuth 授权成功，工具目录已加载"

    async def logout_server(self, server_name: str) -> str:
        server = self._oauth_server(server_name)
        oauth = await self._oauth_session(server)
        await self._remove_server_runtime(server_name)
        await oauth.logout()
        return f"MCP Server {server_name} 已退出 OAuth，相关工具已移除"

    async def close(self) -> None:
        for session in tuple(self._sessions.values()):
            try:
                await session.close()
            except Exception:
                pass
        self._sessions.clear()
        for oauth in tuple(self._oauth_sessions.values()):
            try:
                await oauth.close()
            except Exception:
                pass

    async def _initialize_server(self, server: McpServerConfig, *, force: bool = False) -> None:
        if force or server.name in self._sessions or server.name in self._definitions:
            await self._remove_server_runtime(server.name)
        oauth: McpOAuthSession | None = None
        if server.oauth is not None and server.oauth.enabled:
            oauth = await self._oauth_session(server)
        transport = self._make_transport(server, oauth)
        session = McpClientSession(server, transport)
        try:
            await session.initialize()
            definitions = await session.list_tools()
        except McpAuthorizationRequired:
            self._failed_servers.pop(server.name, None)
            try:
                await session.close()
            except Exception:
                pass
            return
        except Exception as exc:
            self._failed_servers[server.name] = self._redact_for_server(server, str(exc))
            try:
                await session.close()
            except Exception:
                pass
            return
        self._failed_servers.pop(server.name, None)
        self._sessions[server.name] = session
        self._definitions[server.name] = definitions
        self._catalog.replace_server(server.name, definitions)
        if server.name not in self._loaded_servers:
            self._loaded_servers.append(server.name)

    async def _oauth_session(self, server: McpServerConfig) -> McpOAuthSession:
        existing = self._oauth_sessions.get(server.name)
        if existing is not None:
            return existing
        if self._oauth_session_factory is not None:
            oauth = self._oauth_session_factory(server)
        else:
            oauth = McpOAuthSession(
                server,
                store=self._credential_store,
                status_callback=self._on_oauth_status,
            )
        # 测试注入的 Session 也可选择暴露可写回调。
        if getattr(oauth, "status_callback", None) is None:
            oauth.status_callback = self._on_oauth_status
        self._oauth_sessions[server.name] = oauth
        if server.name not in self._oauth_restored:
            await oauth.restore()
            self._oauth_restored.add(server.name)
        return oauth

    def _make_transport(
        self,
        server: McpServerConfig,
        oauth: McpOAuthSession | None,
    ) -> McpTransport:
        if self._transport_factory is not None:
            return self._transport_factory(server)
        if server.transport == "stdio":
            return StdioMcpTransport(server)
        return StreamableHttpMcpTransport(server, auth_provider=oauth)

    def _register_server_tools(self, server_name: str) -> None:
        registry = self._registry
        definitions = self._definitions.get(server_name)
        session = self._sessions.get(server_name)
        if registry is None or definitions is None or session is None:
            return
        registry.unregister_origin(f"mcp:{server_name}")
        self._registered_tools = [
            name for name in self._registered_tools if not name.startswith(f"{server_name}__")
        ]
        for definition in definitions:
            try:
                registry.register(RemoteMcpTool(definition, session))
            except Exception as exc:
                self._failed_tools[definition.global_name] = self._redact_for_server(
                    self.config.servers[server_name],
                    str(exc),
                )
                continue
            self._failed_tools.pop(definition.global_name, None)
            self._registered_tools.append(definition.global_name)
        self._refresh_searchable()

    async def _remove_server_runtime(self, server_name: str) -> None:
        if self._registry is not None:
            self._registry.unregister_origin(f"mcp:{server_name}")
        session = self._sessions.pop(server_name, None)
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass
        self._definitions.pop(server_name, None)
        self._catalog.remove_server(server_name)
        if server_name in self._loaded_servers:
            self._loaded_servers.remove(server_name)
        prefix = f"{server_name}__"
        self._registered_tools = [name for name in self._registered_tools if not name.startswith(prefix)]
        for name in tuple(self._failed_tools):
            if name.startswith(prefix):
                self._failed_tools.pop(name, None)
        self._refresh_searchable()

    def _oauth_server(self, server_name: str) -> McpServerConfig:
        server = self.config.servers.get(server_name)
        if server is None:
            raise McpOAuthError(f"未找到 MCP Server {server_name}")
        if server.transport != "http" or server.oauth is None or not server.oauth.enabled:
            raise McpOAuthError(f"MCP Server {server_name} 未启用 OAuth")
        return server

    def _on_oauth_status(self, status: McpOAuthStatus) -> None:
        if status.state in {"authorization_required", "refresh_failed"} and self._registry is not None:
            self._registry.unregister_origin(f"mcp:{status.server_name}")
            prefix = f"{status.server_name}__"
            self._registered_tools = [name for name in self._registered_tools if not name.startswith(prefix)]
            self._refresh_searchable()

    def _server_search_available(self, server_name: str) -> bool:
        if server_name not in self._sessions:
            return False
        oauth = self._oauth_sessions.get(server_name)
        if oauth is not None and oauth.status.state in {"authorization_required", "refresh_failed"}:
            return False
        return True

    def _refresh_searchable(self) -> None:
        self._catalog.set_searchable(set(self._registered_tools))

    def _redact_match(self, match: McpToolMatch) -> McpToolMatch:
        server = self.config.servers.get(match.server_name)
        if server is None:
            return match
        return replace(
            match,
            title=(self._redact_for_server(server, match.title) if match.title is not None else None),
            summary=self._redact_for_server(server, match.summary),
        )

    def _redact_for_server(self, server: McpServerConfig, text: str) -> str:
        redacted = text
        secrets = [*server.env.values(), *server.headers.values()]
        if server.oauth is not None and server.oauth.client_secret:
            secrets.append(server.oauth.client_secret)
        for value in secrets:
            redacted = redact_secret(redacted, value)
        return redact_secret(redacted)


def create_mcp_manager(config: McpConfig) -> McpManager:
    return McpManager(config)
