from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from mewcode.config import McpConfig, McpServerConfig
from mewcode.errors import redact_secret
from mewcode.mcp.client import McpClientSession
from mewcode.mcp.tools import McpToolDefinition, RemoteMcpTool
from mewcode.mcp.transport import McpTransport, StdioMcpTransport, StreamableHttpMcpTransport
from mewcode.tools.registry import ToolRegistry


TransportFactory = Callable[[McpServerConfig], McpTransport]


@dataclass(frozen=True)
class McpLoadReport:
    loaded_servers: tuple[str, ...] = ()
    failed_servers: dict[str, str] = field(default_factory=dict)
    registered_tools: tuple[str, ...] = ()
    failed_tools: dict[str, str] = field(default_factory=dict)


class McpManager:
    def __init__(self, config: McpConfig, transport_factory: TransportFactory | None = None) -> None:
        self.config = config
        self._transport_factory = transport_factory or self._default_transport
        self._sessions: dict[str, McpClientSession] = {}
        self._definitions: dict[str, tuple[McpToolDefinition, ...]] = {}
        self._loaded_servers: list[str] = []
        self._failed_servers: dict[str, str] = {}
        self._registered_tools: list[str] = []
        self._failed_tools: dict[str, str] = {}

    async def initialize(self) -> None:
        for server in self.config.servers.values():
            transport = self._transport_factory(server)
            session = McpClientSession(server, transport)
            try:
                await session.initialize()
                definitions = await session.list_tools()
            except Exception as exc:
                self._failed_servers[server.name] = self._redact_for_server(server, str(exc))
                try:
                    await session.close()
                except Exception:
                    pass
                continue
            self._sessions[server.name] = session
            self._definitions[server.name] = definitions
            self._loaded_servers.append(server.name)

    def register_tools(self, registry: ToolRegistry) -> None:
        for server_name, definitions in self._definitions.items():
            session = self._sessions[server_name]
            for definition in definitions:
                try:
                    registry.register(RemoteMcpTool(definition, session))
                except Exception as exc:
                    self._failed_tools[definition.global_name] = self._redact_for_server(
                        self.config.servers[server_name],
                        str(exc),
                    )
                    continue
                self._registered_tools.append(definition.global_name)

    def load_report(self) -> McpLoadReport:
        return McpLoadReport(
            loaded_servers=tuple(self._loaded_servers),
            failed_servers=dict(self._failed_servers),
            registered_tools=tuple(self._registered_tools),
            failed_tools=dict(self._failed_tools),
        )

    async def close(self) -> None:
        for session in tuple(self._sessions.values()):
            try:
                await session.close()
            except Exception:
                pass

    def _default_transport(self, server: McpServerConfig) -> McpTransport:
        if server.transport == "stdio":
            return StdioMcpTransport(server)
        return StreamableHttpMcpTransport(server)

    def _redact_for_server(self, server: McpServerConfig, text: str) -> str:
        redacted = text
        for value in (*server.env.values(), *server.headers.values()):
            redacted = redact_secret(redacted, value)
        return redact_secret(redacted)


def create_mcp_manager(config: McpConfig) -> McpManager:
    return McpManager(config)
