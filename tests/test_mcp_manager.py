from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from mewcode.config import AppConfig, McpConfig, McpOAuthConfig, McpServerConfig
from mewcode.errors import ConfigError
from mewcode.mcp.errors import McpAuthorizationRequired, McpOAuthError
from mewcode.mcp.manager import McpLoadReport, McpManager
from mewcode.mcp.oauth.models import McpOAuthStatus
from mewcode.session import ChatSession
from mewcode.tools.base import ToolContext
from mewcode.tools.base import ToolSpec
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry, create_default_registry
from mewcode.tui.app import MewCodeApp


class FakeTransport:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[str] = []
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        _ = params
        self.requests.append(method)
        return self.responses.pop(0)

    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        _ = method, params

    async def close(self) -> None:
        self.closed = True


class ExistingTool:
    spec = ToolSpec(
        name="demo__echo",
        description="existing",
        parameters_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )

    async def execute(self, arguments, context):
        _ = arguments, context
        return {}


def config(*servers: McpServerConfig) -> McpConfig:
    return McpConfig(servers={server.name: server for server in servers})


def server(name: str, transport: str = "stdio") -> McpServerConfig:
    if transport == "http":
        return McpServerConfig(name=name, transport="http", url=f"https://{name}.test/mcp")
    return McpServerConfig(name=name, transport="stdio", command="python")


def initialize_result(*, tools: bool = True) -> dict[str, Any]:
    capabilities = {"tools": {"listChanged": False}} if tools else {"resources": {}}
    return {"protocolVersion": "2025-06-18", "capabilities": capabilities, "serverInfo": {"name": "fixture"}}


def tools_result(*names: str) -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": name,
                "description": f"{name} description",
                "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            }
            for name in names
        ]
    }


@pytest.mark.asyncio
async def test_manager_initializes_servers_and_registers_tools() -> None:
    transports = {
        "local": FakeTransport([initialize_result(), tools_result("echo")]),
        "remote": FakeTransport([initialize_result(), tools_result("echo")]),
    }
    manager = McpManager(config(server("local"), server("remote", "http")), lambda item: transports[item.name])
    registry = create_default_registry()

    await manager.initialize()
    manager.register_tools(registry)

    report = manager.load_report()
    assert report.loaded_servers == ("local", "remote")
    assert set(report.registered_tools) == {"local__echo", "remote__echo"}
    assert registry.get("local__echo") is not None
    assert registry.get("remote__echo") is not None


@pytest.mark.asyncio
async def test_manager_isolates_failed_server_and_keeps_successful_server() -> None:
    transports = {
        "ok": FakeTransport([initialize_result(), tools_result("echo")]),
        "bad": FakeTransport([initialize_result(tools=False)]),
    }
    manager = McpManager(config(server("ok"), server("bad")), lambda item: transports[item.name])
    registry = create_default_registry()

    await manager.initialize()
    manager.register_tools(registry)

    report = manager.load_report()
    assert report.loaded_servers == ("ok",)
    assert "bad" in report.failed_servers
    assert registry.get("read_file") is not None
    assert registry.get("ok__echo") is not None


@pytest.mark.asyncio
async def test_manager_records_duplicate_tool_registration_failure() -> None:
    transports = {"demo": FakeTransport([initialize_result(), tools_result("echo")])}
    manager = McpManager(config(server("demo")), lambda item: transports[item.name])
    registry = ToolRegistry()
    registry.register(ExistingTool())

    await manager.initialize()
    manager.register_tools(registry)

    report = manager.load_report()
    assert "demo__echo" in report.failed_tools
    assert report.registered_tools == ()


@pytest.mark.asyncio
async def test_manager_close_closes_initialized_sessions() -> None:
    transports = {"demo": FakeTransport([initialize_result(), tools_result("echo")])}
    manager = McpManager(config(server("demo")), lambda item: transports[item.name])

    await manager.initialize()
    await manager.close()

    assert transports["demo"].closed is True


def test_cli_initializes_mcp_manager_and_closes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from mewcode import cli

    events: list[str] = []
    fake_manager = object()
    restored_session = ChatSession()
    restore_report = object()

    class FakeMemoryManager:
        async def bootstrap(self, *, options, provider, context_manager):
            _ = provider, context_manager
            assert options.new_session is True
            events.append("bootstrap")
            return SimpleNamespace(session=restored_session, restore_report=restore_report)

    fake_memory_manager = FakeMemoryManager()

    class FakeApp:
        def __init__(self, *args, **kwargs) -> None:
            assert args[0] is restored_session
            assert kwargs["mcp_manager"] is fake_manager
            assert kwargs["memory_manager"] is fake_memory_manager
            assert kwargs["restore_report"] is restore_report
            events.append("app")

        def set_permission_controller(self, controller) -> None:
            _ = controller
            events.append("permission")

        def run(self) -> None:
            events.append("run")

    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: AppConfig(protocol="openai", model="test", base_url="https://example.test/v1", api_key="key"),
    )
    monkeypatch.setattr(cli, "create_provider", lambda config: object())
    monkeypatch.setattr(cli, "create_mcp_manager", lambda config: fake_manager)
    monkeypatch.setattr(cli, "create_permission_controller", lambda cwd, permissions, app: object())
    monkeypatch.setattr(cli, "SessionMemoryManager", lambda cwd, config: fake_memory_manager)
    monkeypatch.setattr(cli, "MewCodeApp", FakeApp)

    assert cli.main(["--new-session"]) == 0
    assert events == ["bootstrap", "app", "permission", "run"]


@pytest.mark.asyncio
async def test_tui_lifecycle_initializes_and_closes_mcp_manager(tmp_path) -> None:
    events: list[str] = []

    class FakeManager:
        async def initialize(self) -> None:
            events.append("initialize")

        def register_tools(self, registry: ToolRegistry) -> None:
            _ = registry
            events.append("register")

        def load_report(self) -> McpLoadReport:
            return McpLoadReport()

        async def close(self) -> None:
            events.append("close")

    registry = ToolRegistry()
    app = MewCodeApp(
        ChatSession(),
        object(),
        AppConfig(protocol="openai", model="test", base_url="https://example.test/v1", api_key="key"),
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        mcp_manager=FakeManager(),  # type: ignore[arg-type]
    )

    async with app.run_test():
        pass

    assert events == ["initialize", "register", "close"]


def test_cli_reports_mcp_config_error_without_secret(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from mewcode import cli

    secret = "sk-mcp-secret-1234567890"

    def fail_load_config() -> None:
        raise ConfigError(f"mcp bad secret {secret}")

    monkeypatch.setattr(cli, "load_config", fail_load_config)

    assert cli.main([]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.err
    assert "[REDACTED]" in captured.err


class FakeOAuthSession:
    def __init__(self, server: McpServerConfig) -> None:
        self.server = server
        self.status = McpOAuthStatus(server.name)
        self.status_callback = None
        self.authorized = False
        self.closed = False

    async def restore(self) -> None:
        self.status = McpOAuthStatus(self.server.name, "authorization_required", "需要授权")

    async def authorize(self, callback=None) -> None:
        if callback is not None:
            await callback("https://auth.test/authorize?public=value", False)
        self.authorized = True
        self.status = McpOAuthStatus(self.server.name, "authorized", "授权成功")
        if self.status_callback is not None:
            self.status_callback(self.status)

    async def logout(self) -> None:
        self.authorized = False
        self.status = McpOAuthStatus(self.server.name, "authorization_required", "已退出")
        if self.status_callback is not None:
            self.status_callback(self.status)

    async def close(self) -> None:
        self.closed = True


class OAuthGateTransport(FakeTransport):
    def __init__(self, oauth: FakeOAuthSession) -> None:
        super().__init__([initialize_result(), tools_result("echo")])
        self.oauth = oauth

    async def request(self, method: str, params=None):
        if not self.oauth.authorized:
            raise McpAuthorizationRequired(self.oauth.server.name)
        return await super().request(method, params)


@pytest.mark.asyncio
async def test_manager_oauth_authorize_reinitializes_registers_and_logout_isolates_tool() -> None:
    oauth_holder: dict[str, FakeOAuthSession] = {}
    remote = McpServerConfig(
        name="oauth_demo",
        transport="http",
        url="https://mcp.test/mcp",
        oauth=McpOAuthConfig(client_id="client"),
    )

    def oauth_factory(server: McpServerConfig) -> FakeOAuthSession:
        oauth = FakeOAuthSession(server)
        oauth_holder[server.name] = oauth
        return oauth

    def transport_factory(server: McpServerConfig) -> OAuthGateTransport:
        return OAuthGateTransport(oauth_holder[server.name])

    manager = McpManager(
        config(remote),
        transport_factory,
        oauth_session_factory=oauth_factory,  # type: ignore[arg-type]
    )
    registry = create_default_registry()
    await manager.initialize()
    manager.register_tools(registry)

    assert registry.get("oauth_demo__echo") is None
    assert manager.load_report().failed_servers == {}
    assert manager.load_report().oauth_status["oauth_demo"].state == "authorization_required"

    urls: list[str] = []

    async def show_url(url: str, failed: bool) -> None:
        assert failed is False
        urls.append(url)

    result = await manager.authorize_server("oauth_demo", show_url)

    assert "授权成功" in result
    assert urls == ["https://auth.test/authorize?public=value"]
    assert registry.get("oauth_demo__echo") is not None
    assert registry.get("oauth_demo__echo").spec.origin == "mcp:oauth_demo"  # type: ignore[union-attr]

    result = await manager.logout_server("oauth_demo")
    assert "工具已移除" in result
    assert registry.get("oauth_demo__echo") is None
    assert registry.get("read_file") is not None
    assert manager.load_report().oauth_status["oauth_demo"].state == "authorization_required"
    await manager.close()


@pytest.mark.asyncio
async def test_manager_oauth_rejects_unknown_non_oauth_and_duplicate_authorization() -> None:
    oauth_holder: dict[str, FakeOAuthSession] = {}
    oauth_server = McpServerConfig(
        name="oauth_demo",
        transport="http",
        url="https://mcp.test/mcp",
        oauth=McpOAuthConfig(client_id="client"),
    )
    plain_server = McpServerConfig(name="plain", transport="http", url="https://plain.test/mcp")

    def oauth_factory(server: McpServerConfig) -> FakeOAuthSession:
        session = FakeOAuthSession(server)
        oauth_holder[server.name] = session
        return session

    def transport_factory(server: McpServerConfig):
        if server.name == "oauth_demo":
            return OAuthGateTransport(oauth_holder[server.name])
        return FakeTransport([initialize_result(), tools_result("echo")])

    manager = McpManager(
        config(oauth_server, plain_server),
        transport_factory,
        oauth_session_factory=oauth_factory,  # type: ignore[arg-type]
    )
    await manager.initialize()

    with pytest.raises(McpOAuthError, match="未找到"):
        await manager.authorize_server("missing")
    with pytest.raises(McpOAuthError, match="未启用 OAuth"):
        await manager.authorize_server("plain")
    await manager.authorize_server("oauth_demo")
    with pytest.raises(McpOAuthError, match="已授权"):
        await manager.authorize_server("oauth_demo")
    await manager.close()
