from __future__ import annotations

import sys
from pathlib import Path
from urllib.request import urlopen

from mewcode.config import AppConfig, McpConfig, McpOAuthConfig, McpServerConfig
from mewcode.context.manager import ContextManager
from mewcode.mcp.manager import McpManager
from mewcode.mcp.oauth.client import McpOAuthSession
from mewcode.mcp.oauth.discovery import OAuthMetadataDiscovery
from mewcode.mcp.oauth.models import OAuthChallenge
from mewcode.mcp.oauth.store import MemoryCredentialStore
from mewcode.permissions.controller import create_permission_controller
from mewcode.permissions.models import PermissionConfig
from mewcode.providers.factory import create_provider
from mewcode.session import ChatSession
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import create_default_registry
from mewcode.tui.app import MewCodeApp


def open_fixture_authorization(url: str) -> bool:
    # E2E fixture 用 HTTP 请求模拟浏览器，并实际走完 302 与 loopback callback。
    with urlopen(url, timeout=5) as response:  # noqa: S310 - 仅用于本机 E2E fixture
        response.read()
    return True


class InsecureFixtureOAuthSession(McpOAuthSession):
    """仅让本机 E2E fixture 使用 HTTP；生产 Session 仍严格要求 HTTPS。"""

    async def handle_unauthorized(self, www_authenticate: str | None) -> bool:
        _ = www_authenticate
        base_url = self.resource.rsplit("/mcp", 1)[0]
        self.challenge = OAuthChallenge(f"{base_url}/oauth/resource", ("mcp:tools",))
        if self.credentials is not None and self.credentials.token.refresh_token:
            return await self.refresh(force=True)
        self.credentials = None
        self._set_status("authorization_required", "需要授权；请运行 /mcp auth " + self.server.name)
        return False


def main() -> None:
    model_port = int(sys.argv[1]) if len(sys.argv) > 1 else 18768
    static_mcp_port = int(sys.argv[2]) if len(sys.argv) > 2 else 18766
    oauth_mcp_port = int(sys.argv[3]) if len(sys.argv) > 3 else 18767
    mcp = McpConfig(
        servers={
            "remote_demo": McpServerConfig(
                name="remote_demo",
                transport="http",
                url=f"http://127.0.0.1:{static_mcp_port}/mcp",
            ),
            "oauth_demo": McpServerConfig(
                name="oauth_demo",
                transport="http",
                url=f"http://127.0.0.1:{oauth_mcp_port}/mcp",
                oauth=McpOAuthConfig(scopes=("mcp:tools",)),
            ),
            "failed_demo": McpServerConfig(
                name="failed_demo",
                transport="http",
                url="http://127.0.0.1:18999/mcp",
            ),
        }
    )
    config = AppConfig(
        protocol="openai",
        model="e2e-model",
        base_url=f"http://127.0.0.1:{model_port}/v1",
        api_key="e2e-key",
        permissions=PermissionConfig(mode="permissive"),
        mcp=mcp,
    )
    store = MemoryCredentialStore("E2E 使用内存 OAuth 凭据存储")

    def oauth_factory(server: McpServerConfig) -> McpOAuthSession:
        return InsecureFixtureOAuthSession(
            server,
            store=store,
            discovery=OAuthMetadataDiscovery(allow_insecure_loopback=True),
            browser_opener=open_fixture_authorization,
            callback_timeout_seconds=10,
        )

    registry = create_default_registry()
    executor = ToolExecutor(registry, ToolContext(cwd=Path.cwd()))
    manager = McpManager(mcp, oauth_session_factory=oauth_factory)
    app = MewCodeApp(
        ChatSession(),
        create_provider(config),
        config,
        registry,
        executor,
        mcp_manager=manager,
        context_manager=ContextManager(config.context, Path.cwd(), config.max_tokens),
    )
    app.set_permission_controller(create_permission_controller(Path.cwd(), config.permissions, app))
    app.run()


if __name__ == "__main__":
    main()
