from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from mewcode.config import McpOAuthConfig, McpServerConfig
from mewcode.mcp.errors import McpOAuthCallbackError
from mewcode.mcp.oauth.callback import LoopbackOAuthCallback
from mewcode.mcp.oauth.client import McpOAuthSession, OAuthProtocolClient, generate_pkce
from mewcode.mcp.oauth.discovery import OAuthMetadataDiscovery, parse_www_authenticate
from mewcode.mcp.oauth.models import AuthorizationServerMetadata, OAuthCallbackResult, OAuthClientRegistration
from mewcode.mcp.oauth.store import MemoryCredentialStore


def oauth_server(*, client_secret: str | None = None) -> McpServerConfig:
    return McpServerConfig(
        name="demo",
        transport="http",
        url="https://mcp.test/mcp",
        oauth=McpOAuthConfig(
            client_id="static-client",
            client_secret=client_secret,
            scopes=("configured",),
        ),
    )


def metadata() -> AuthorizationServerMetadata:
    return AuthorizationServerMetadata(
        issuer="https://auth.test/",
        authorization_endpoint="https://auth.test/authorize",
        token_endpoint="https://auth.test/token",
        registration_endpoint="https://auth.test/register",
        code_challenge_methods_supported=("S256",),
        token_endpoint_auth_methods_supported=("none", "client_secret_post", "client_secret_basic"),
    )


def test_pkce_and_authorization_url_include_resource_without_token() -> None:
    state, verifier, challenge = generate_pkce()
    url = OAuthProtocolClient().authorization_url(
        metadata(),
        OAuthClientRegistration("client"),
        redirect_uri="http://127.0.0.1:4567/oauth/callback",
        state=state,
        code_challenge=challenge,
        scopes=("repo", "read:user"),
        resource="https://mcp.test/mcp",
    )
    query = parse_qs(urlsplit(url).query)

    assert len(state) >= 32
    assert len(verifier) >= 43
    assert query["code_challenge_method"] == ["S256"]
    assert query["resource"] == ["https://mcp.test/mcp"]
    assert query["scope"] == ["repo read:user"]
    assert "access_token" not in query


class FakeSocket:
    def getsockname(self):
        return ("127.0.0.1", 4567)


class FakeServer:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.sockets = [FakeSocket()]
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeWriter:
    def __init__(self) -> None:
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


async def invoke_callback(server: FakeServer, target: str) -> FakeWriter:
    reader = asyncio.StreamReader()
    reader.feed_data(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
    reader.feed_eof()
    writer = FakeWriter()
    await server.handler(reader, writer)
    return writer


@pytest.mark.asyncio
async def test_loopback_callback_accepts_code_and_closes_port(monkeypatch: pytest.MonkeyPatch) -> None:
    holder: dict[str, FakeServer] = {}

    async def start_server(handler, host, port):
        assert (host, port) == ("127.0.0.1", 0)
        holder["server"] = FakeServer(handler)
        return holder["server"]

    monkeypatch.setattr(asyncio, "start_server", start_server)
    callback = LoopbackOAuthCallback("expected-state")
    redirect_uri = await callback.start()
    writer = await invoke_callback(
        holder["server"],
        "/oauth/callback?code=auth-code&state=expected-state",
    )
    result = await callback.wait(2)

    assert redirect_uri == "http://127.0.0.1:4567/oauth/callback"
    assert result.code == "auth-code"
    assert result.state == "expected-state"
    assert b"200 OK" in writer.data
    assert holder["server"].closed is True


@pytest.mark.asyncio
async def test_loopback_callback_rejects_wrong_state_without_echoing_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, FakeServer] = {}

    async def start_server(handler, host, port):
        _ = host, port
        holder["server"] = FakeServer(handler)
        return holder["server"]

    monkeypatch.setattr(asyncio, "start_server", start_server)
    callback = LoopbackOAuthCallback("expected-state")
    await callback.start()
    writer = await invoke_callback(
        holder["server"],
        "/oauth/callback?code=super-secret-code&state=wrong",
    )
    with pytest.raises(McpOAuthCallbackError) as exc_info:
        await callback.wait(2)
    assert b"400 Bad Request" in writer.data
    assert "super-secret-code" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_loopback_callback_timeout_closes_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    holder: dict[str, FakeServer] = {}

    async def start_server(handler, host, port):
        _ = host, port
        holder["server"] = FakeServer(handler)
        return holder["server"]

    monkeypatch.setattr(asyncio, "start_server", start_server)
    callback = LoopbackOAuthCallback("expected-state")
    await callback.start()

    with pytest.raises(McpOAuthCallbackError, match="超时"):
        await callback.wait(0.01)

    assert holder["server"].closed is True


@pytest.mark.asyncio
async def test_full_oauth_authorize_refresh_and_logout_with_static_fallback() -> None:
    seen_forms: list[dict[str, list[str]]] = []
    token_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_count
        if request.method == "GET" and request.url.host == "mcp.test":
            return httpx.Response(
                200,
                json={
                    "resource": "https://mcp.test/mcp",
                    "authorization_servers": ["https://auth.test/"],
                    "scopes_supported": ["metadata-scope"],
                },
            )
        if request.method == "GET" and request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://auth.test/",
                    "authorization_endpoint": "https://auth.test/authorize",
                    "token_endpoint": "https://auth.test/token",
                    "registration_endpoint": "https://auth.test/register",
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                },
            )
        if request.method == "POST" and request.url.path == "/register":
            return httpx.Response(404, json={"error": "unsupported"})
        if request.method == "POST" and request.url.path == "/token":
            form = parse_qs(request.content.decode())
            seen_forms.append(form)
            token_count += 1
            if form["grant_type"] == ["authorization_code"]:
                assert form["code"] == ["fixture-code"]
                assert "code_verifier" in form
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-secret-1",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "refresh_token": "refresh-secret-1",
                        "scope": "challenge-scope",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "access-secret-2",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(404)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = MemoryCredentialStore()
    class ImmediateCallback:
        def __init__(self, state: str) -> None:
            self.state = state

        async def start(self) -> str:
            return "http://127.0.0.1:4567/oauth/callback"

        async def wait(self, timeout_seconds: float) -> OAuthCallbackResult:
            assert timeout_seconds == 2
            return OAuthCallbackResult("fixture-code", self.state)

        async def close(self) -> None:
            return None

    session = McpOAuthSession(
        oauth_server(),
        store=store,
        discovery=OAuthMetadataDiscovery(client=http_client),
        protocol_client=OAuthProtocolClient(client=http_client),
        browser_opener=lambda url: False,
        callback_factory=ImmediateCallback,  # type: ignore[arg-type]
        callback_timeout_seconds=2,
    )
    session.challenge = parse_www_authenticate(
        'Bearer resource_metadata="https://mcp.test/resource-meta", scope="challenge-scope"'
    )
    displayed: list[tuple[str, bool]] = []

    async def on_url(url: str, browser_failed: bool) -> None:
        displayed.append((url, browser_failed))

    try:
        await session.authorize(on_url)

        assert session.status.state == "authorized"
        assert await session.authorization_headers() == {"Authorization": "Bearer access-secret-1"}
        assert displayed[0][1] is False
        assert displayed[-1][1] is True
        assert seen_forms[0]["resource"] == ["https://mcp.test/mcp"]
        assert seen_forms[0]["client_id"] == ["static-client"]

        assert await session.refresh(force=True) is True
        assert await session.authorization_headers() == {"Authorization": "Bearer access-secret-2"}
        assert session.credentials is not None
        assert session.credentials.token.refresh_token == "refresh-secret-1"
        assert seen_forms[1]["resource"] == ["https://mcp.test/mcp"]

        await session.logout()
        assert session.status.state == "authorization_required"
        assert await store.load("demo", "https://mcp.test/mcp") is None
    finally:
        await session.close()
        await http_client.aclose()


@pytest.mark.asyncio
async def test_session_restores_expiring_token_and_waits_for_challenge_before_refresh() -> None:
    store = MemoryCredentialStore()
    server = oauth_server()
    from mewcode.mcp.oauth.models import OAuthCredentialBundle, OAuthTokenSet

    credentials = OAuthCredentialBundle(
        resource=server.url or "",
        issuer="https://auth.test/",
        client=OAuthClientRegistration("static-client"),
        token=OAuthTokenSet("old-access", expires_at=time.time() - 1, refresh_token="refresh-token"),
    )
    await store.save(server.name, server.url or "", credentials)
    session = McpOAuthSession(server, store=store)
    try:
        await session.restore()
        assert await session.authorization_headers() == {"Authorization": "Bearer old-access"}
        assert session.status.state == "authorized"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_concurrent_refresh_is_singleflight_and_rotates_refresh_token() -> None:
    from mewcode.mcp.oauth.models import OAuthCredentialBundle, OAuthTokenSet

    class SlowProtocolClient:
        def __init__(self) -> None:
            self.calls = 0

        async def refresh_token(self, metadata, client, *, refresh_token, resource):
            _ = metadata, client, resource
            assert refresh_token == "old-refresh"
            self.calls += 1
            await asyncio.sleep(0.02)
            return OAuthTokenSet(
                "new-access",
                expires_at=time.time() + 3600,
                refresh_token="rotated-refresh",
            )

        async def close(self) -> None:
            return None

    server = oauth_server()
    store = MemoryCredentialStore()
    protocol = SlowProtocolClient()
    session = McpOAuthSession(server, store=store, protocol_client=protocol)  # type: ignore[arg-type]
    session.authorization_metadata = metadata()
    session.credentials = OAuthCredentialBundle(
        resource=server.url or "",
        issuer=metadata().issuer,
        client=OAuthClientRegistration("static-client"),
        token=OAuthTokenSet("old-access", expires_at=time.time() - 1, refresh_token="old-refresh"),
    )

    try:
        results = await asyncio.gather(
            session.refresh(force=True),
            session.refresh(force=True),
            session.refresh(force=True),
        )
    finally:
        await session.close()

    assert results == [True, True, True]
    assert protocol.calls == 1
    assert session.credentials is not None
    assert session.credentials.token.refresh_token == "rotated-refresh"


@pytest.mark.asyncio
async def test_dynamic_registration_parses_public_client_and_static_auth_methods() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(__import__("json").loads(request.content.decode()))
        return httpx.Response(
            201,
            json={"client_id": "dynamic-client", "token_endpoint_auth_method": "none"},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    protocol = OAuthProtocolClient(client=http_client)
    try:
        client = await protocol.register_client(
            metadata(),
            "http://127.0.0.1:4567/oauth/callback",
        )
        secret_client = protocol.static_client(
            McpOAuthConfig(client_id="static", client_secret="secret"),
            metadata(),
        )
    finally:
        await http_client.aclose()

    assert client == OAuthClientRegistration("dynamic-client", None, "none")
    assert seen["redirect_uris"] == ["http://127.0.0.1:4567/oauth/callback"]
    assert secret_client.token_endpoint_auth_method == "client_secret_basic"
