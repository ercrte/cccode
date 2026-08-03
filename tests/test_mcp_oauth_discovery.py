from __future__ import annotations

import httpx
import pytest

from julycode.mcp.errors import McpOAuthDiscoveryError
from julycode.mcp.oauth.discovery import OAuthMetadataDiscovery, parse_www_authenticate


def test_oauth_challenge_parses_multiple_schemes_and_quoted_params() -> None:
    challenge = parse_www_authenticate(
        'Basic realm="legacy", Bearer resource_metadata="https://mcp.test/oauth/resource", '
        'scope="repo read:user", Digest realm="other"'
    )

    assert challenge.resource_metadata_url == "https://mcp.test/oauth/resource"
    assert challenge.scopes == ("repo", "read:user")


@pytest.mark.parametrize(
    "header",
    (
        None,
        'Basic realm="x"',
        'Bearer scope="repo"',
        'Bearer resource_metadata="http://mcp.test/meta"',
        'Bearer resource_metadata="https://mcp.test/a", resource_metadata="https://mcp.test/b"',
        'Bearer resource_metadata="https://mcp.test/a", Bearer resource_metadata="https://mcp.test/b"',
    ),
)
def test_oauth_challenge_rejects_missing_duplicate_or_unsafe_values(header: str | None) -> None:
    with pytest.raises(McpOAuthDiscoveryError):
        parse_www_authenticate(header)


@pytest.mark.parametrize(
    "url",
    (
        "https://user:password@mcp.test/meta",
        "https://mcp.test/meta#fragment",
    ),
)
def test_oauth_challenge_rejects_userinfo_and_fragment(url: str) -> None:
    with pytest.raises(McpOAuthDiscoveryError):
        parse_www_authenticate(f'Bearer resource_metadata="{url}"')


@pytest.mark.asyncio
async def test_oauth_metadata_discovery_validates_resource_issuer_and_pkce() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/resource":
            return httpx.Response(
                200,
                json={
                    "resource": "https://mcp.test/mcp",
                    "authorization_servers": ["https://auth.test/tenant"],
                    "scopes_supported": ["repo"],
                },
            )
        if request.url.path == "/.well-known/oauth-authorization-server/tenant":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://auth.test/tenant",
                    "authorization_endpoint": "https://auth.test/authorize",
                    "token_endpoint": "https://auth.test/token",
                    "registration_endpoint": "https://auth.test/register",
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    discovery = OAuthMetadataDiscovery(client=client)
    try:
        resource, authorization = await discovery.discover(
            parse_www_authenticate('Bearer resource_metadata="https://mcp.test/oauth/resource"'),
            "https://mcp.test/mcp",
        )
    finally:
        await client.aclose()

    assert resource.scopes_supported == ("repo",)
    assert authorization.issuer == "https://auth.test/tenant"
    assert authorization.registration_endpoint == "https://auth.test/register"


@pytest.mark.asyncio
async def test_oauth_metadata_discovery_rejects_redirect_and_oversized_response() -> None:
    async def redirect_handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(302, headers={"Location": "https://attacker.test/meta"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(redirect_handler))
    discovery = OAuthMetadataDiscovery(client=client, max_response_bytes=16)
    challenge = parse_www_authenticate('Bearer resource_metadata="https://mcp.test/meta"')
    try:
        with pytest.raises(McpOAuthDiscoveryError, match="重定向"):
            await discovery.fetch_protected_resource(challenge, "https://mcp.test/mcp")
    finally:
        await client.aclose()

    secret = "access-secret-must-not-leak"

    async def large_handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, content=(secret * 10).encode())

    client = httpx.AsyncClient(transport=httpx.MockTransport(large_handler))
    discovery = OAuthMetadataDiscovery(client=client, max_response_bytes=16)
    try:
        with pytest.raises(McpOAuthDiscoveryError) as exc_info:
            await discovery.fetch_protected_resource(challenge, "https://mcp.test/mcp")
    finally:
        await client.aclose()
    assert secret not in str(exc_info.value)


@pytest.mark.asyncio
async def test_oauth_metadata_rejects_resource_mismatch_and_missing_s256() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mcp.test":
            return httpx.Response(
                200,
                json={
                    "resource": "https://other.test/mcp",
                    "authorization_servers": ["https://auth.test"],
                },
            )
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    discovery = OAuthMetadataDiscovery(client=client)
    challenge = parse_www_authenticate('Bearer resource_metadata="https://mcp.test/meta"')
    try:
        with pytest.raises(McpOAuthDiscoveryError, match="不一致"):
            await discovery.fetch_protected_resource(challenge, "https://mcp.test/mcp")
    finally:
        await client.aclose()
