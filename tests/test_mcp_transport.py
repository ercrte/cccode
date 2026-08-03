from __future__ import annotations

import sys
import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from julycode.config import McpServerConfig
from julycode.mcp.errors import McpAuthorizationRequired, McpConnectionError, McpProtocolError, McpTimeoutError
from julycode.mcp.transport import StdioMcpTransport, StreamableHttpMcpTransport


def stdio_config(*args: str) -> McpServerConfig:
    return McpServerConfig(
        name="local_demo",
        transport="stdio",
        command=sys.executable,
        args=("tests/fixtures/mcp_stdio_server.py", *args),
    )


@pytest.mark.asyncio
async def test_stdio_transport_sends_request_and_notification() -> None:
    transport = StdioMcpTransport(stdio_config())
    await transport.start()
    try:
        result = await transport.request("initialize", {"protocolVersion": "2025-06-18"})
        await transport.notify("notifications/initialized")
    finally:
        await transport.close()

    assert result["protocolVersion"] == "2025-06-18"
    assert "tools" in result["capabilities"]


@pytest.mark.asyncio
async def test_stdio_transport_matches_out_of_order_responses_by_id() -> None:
    transport = StdioMcpTransport(stdio_config())
    await transport.start()
    try:
        first, second = await asyncio.gather(
            transport.request("fixture/out_of_order"),
            transport.request("fixture/out_of_order"),
        )
    finally:
        await transport.close()

    assert first["id"] == "1"
    assert second["id"] == "2"


@pytest.mark.asyncio
async def test_stdio_transport_raises_protocol_error_response() -> None:
    transport = StdioMcpTransport(stdio_config())
    await transport.start()
    try:
        with pytest.raises(McpProtocolError) as exc_info:
            await transport.request("fixture/error")
    finally:
        await transport.close()

    assert exc_info.value.code == -32000
    assert "fixture protocol error" in str(exc_info.value)


@pytest.mark.asyncio
async def test_stdio_transport_times_out_pending_request() -> None:
    transport = StdioMcpTransport(stdio_config(), timeout_seconds=0.05)
    await transport.start()
    try:
        with pytest.raises(McpTimeoutError):
            await transport.request("fixture/never")
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_stdio_transport_close_terminates_process() -> None:
    transport = StdioMcpTransport(stdio_config())
    await transport.start()
    process = transport.process

    await transport.close()

    assert process is not None
    assert process.returncode is not None


@pytest.mark.asyncio
async def test_http_transport_sends_json_request_and_uses_session_headers() -> None:
    seen: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        payload = _json(request)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json", "Mcp-Session-Id": "session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    },
                },
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": []}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = StreamableHttpMcpTransport(
        McpServerConfig(name="remote_demo", transport="http", url="https://mcp.test/mcp"),
        client=client,
    )
    try:
        init = await transport.request("initialize")
        tools = await transport.request("tools/list")
    finally:
        await transport.close()
        await client.aclose()

    assert init["protocolVersion"] == "2025-06-18"
    assert tools == {"tools": []}
    assert "application/json" in seen[0]["accept"]
    assert "text/event-stream" in seen[0]["accept"]
    assert seen[1]["mcp-session-id"] == "session-1"
    assert seen[1]["mcp-protocol-version"] == "2025-06-18"


@pytest.mark.asyncio
async def test_http_transport_reads_sse_response_until_matching_id() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = _json(request)
        body = (
            'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
            f'data: {{"jsonrpc":"2.0","id":"{payload["id"]}","result":{{"ok":true}}}}\n\n'
        )
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body.encode())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = StreamableHttpMcpTransport(
        McpServerConfig(name="remote_demo", transport="http", url="https://mcp.test/mcp"),
        client=client,
    )
    try:
        result = await transport.request("fixture/sse")
    finally:
        await transport.close()
        await client.aclose()

    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_http_transport_close_sends_delete_when_session_exists() -> None:
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "DELETE":
            assert request.headers["mcp-session-id"] == "session-1"
            return httpx.Response(200)
        payload = _json(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Mcp-Session-Id": "session-1"},
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = StreamableHttpMcpTransport(
        McpServerConfig(name="remote_demo", transport="http", url="https://mcp.test/mcp"),
        client=client,
    )
    await transport.request("initialize")
    await transport.close()
    await client.aclose()

    assert methods == ["POST", "DELETE"]


@pytest.mark.asyncio
async def test_transport_errors_are_redacted() -> None:
    secret = "Bearer top-secret-value"

    async def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(500, content=f"bad auth {secret}".encode())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = StreamableHttpMcpTransport(
        McpServerConfig(
            name="remote_demo",
            transport="http",
            url="https://mcp.test/mcp",
            headers={"Authorization": secret},
        ),
        client=client,
    )
    try:
        with pytest.raises(McpConnectionError) as exc_info:
            await transport.request("initialize")
    finally:
        await transport.close()
        await client.aclose()

    assert secret not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def _json(request: httpx.Request) -> dict[str, Any]:
    return __import__("json").loads(request.content.decode("utf-8"))


class FakeOAuthProvider:
    def __init__(self, *, retry: bool) -> None:
        self.token = "old-access-secret"
        self.retry = retry
        self.challenges: list[str | None] = []
        self.failed = 0

    async def authorization_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def handle_unauthorized(self, challenge: str | None) -> bool:
        self.challenges.append(challenge)
        if self.retry:
            self.token = "new-access-secret"
        return self.retry

    async def authorization_failed(self) -> None:
        self.failed += 1

    def redact(self, text: str) -> str:
        return text.replace(self.token, "[REDACTED]")


@pytest.mark.asyncio
async def test_http_oauth_401_marks_authorization_required_without_retry() -> None:
    challenge = 'Bearer resource_metadata="https://mcp.test/meta"'
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.headers["authorization"] == "Bearer old-access-secret"
        return httpx.Response(401, headers={"WWW-Authenticate": challenge})

    provider = FakeOAuthProvider(retry=False)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = StreamableHttpMcpTransport(
        McpServerConfig(name="demo", transport="http", url="https://mcp.test/mcp"),
        client=client,
        auth_provider=provider,
    )
    try:
        with pytest.raises(McpAuthorizationRequired) as exc_info:
            await transport.request("initialize")
    finally:
        await transport.close()
        await client.aclose()

    assert attempts == 1
    assert provider.challenges == [challenge]
    assert "old-access-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_http_oauth_refreshes_and_replays_request_only_once() -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        authorization = request.headers["authorization"]
        seen.append(authorization)
        if authorization == "Bearer old-access-secret":
            return httpx.Response(
                401,
                headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.test/meta"'},
            )
        payload = _json(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}},
        )

    provider = FakeOAuthProvider(retry=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = StreamableHttpMcpTransport(
        McpServerConfig(name="demo", transport="http", url="https://mcp.test/mcp"),
        client=client,
        auth_provider=provider,
    )
    try:
        result = await transport.request("tools/list")
    finally:
        await transport.close()
        await client.aclose()

    assert result == {"ok": True}
    assert seen == ["Bearer old-access-secret", "Bearer new-access-secret"]


@pytest.mark.asyncio
async def test_http_oauth_stops_after_second_401_and_hides_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.test/meta"'},
        )

    provider = FakeOAuthProvider(retry=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = StreamableHttpMcpTransport(
        McpServerConfig(name="demo", transport="http", url="https://mcp.test/mcp"),
        client=client,
        auth_provider=provider,
    )
    try:
        with pytest.raises(McpAuthorizationRequired) as exc_info:
            await transport.request("tools/list")
    finally:
        await transport.close()
        await client.aclose()

    assert len(provider.challenges) == 1
    assert provider.failed == 1
    assert "new-access-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_http_oauth_redacts_token_echoed_by_error_response() -> None:
    provider = FakeOAuthProvider(retry=False)

    async def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["authorization"].removeprefix("Bearer ")
        return httpx.Response(500, content=f"server echoed {token}".encode())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = StreamableHttpMcpTransport(
        McpServerConfig(name="demo", transport="http", url="https://mcp.test/mcp"),
        client=client,
        auth_provider=provider,
    )
    try:
        with pytest.raises(McpConnectionError) as exc_info:
            await transport.request("tools/list")
    finally:
        await transport.close()
        await client.aclose()

    assert "old-access-secret" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)
