from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx

from mewcode.mcp.errors import McpOAuthDiscoveryError
from mewcode.mcp.oauth.models import (
    AuthorizationServerMetadata,
    OAuthChallenge,
    ProtectedResourceMetadata,
)


MAX_METADATA_BYTES = 1024 * 1024
_PARAM_RE = re.compile(r"^([!#$%&'*+.^_`|~0-9A-Za-z-]+)\s*=\s*(.*)$")
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)\s+(.+)$")


def parse_www_authenticate(value: str | None) -> OAuthChallenge:
    """从可能包含多个认证方案的 Header 中提取唯一 Bearer challenge。"""
    if not value:
        raise McpOAuthDiscoveryError("MCP 401 响应缺少 WWW-Authenticate Bearer challenge")
    parts = _split_quoted(value, ",")
    bearer_groups: list[list[str]] = []
    current: list[str] | None = None
    for part in parts:
        text = part.strip()
        if not text:
            continue
        scheme_match = _SCHEME_RE.match(text)
        if scheme_match and not _PARAM_RE.match(text):
            scheme, remainder = scheme_match.groups()
            current = [remainder] if scheme.casefold() == "bearer" else None
            if current is not None:
                bearer_groups.append(current)
            continue
        if current is not None and _PARAM_RE.match(text):
            current.append(text)

    if len(bearer_groups) != 1:
        raise McpOAuthDiscoveryError("MCP 401 响应必须包含唯一 Bearer challenge")
    params: dict[str, str] = {}
    for part in bearer_groups[0]:
        match = _PARAM_RE.match(part.strip())
        if match is None:
            raise McpOAuthDiscoveryError("Bearer challenge 参数格式无效")
        key, raw_value = match.groups()
        normalized_key = key.casefold()
        if normalized_key in params:
            raise McpOAuthDiscoveryError("Bearer challenge 包含重复参数")
        params[normalized_key] = _unquote_auth_value(raw_value)

    metadata_url = params.get("resource_metadata")
    if not metadata_url:
        raise McpOAuthDiscoveryError("Bearer challenge 缺少 resource_metadata")
    validate_https_url(metadata_url, "resource_metadata")
    scopes = tuple(item for item in params.get("scope", "").split(" ") if item)
    return OAuthChallenge(resource_metadata_url=metadata_url, scopes=scopes)


def validate_https_url(value: str, field: str, *, allow_insecure_loopback: bool = False) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise McpOAuthDiscoveryError(f"{field} URL 无效") from exc
    is_loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme.casefold() != "https" and not (allow_insecure_loopback and parsed.scheme == "http" and is_loopback):
        raise McpOAuthDiscoveryError(f"{field} 必须使用 HTTPS")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise McpOAuthDiscoveryError(f"{field} URL 不允许 userinfo，且必须包含主机")
    if parsed.fragment:
        raise McpOAuthDiscoveryError(f"{field} URL 不允许 fragment")
    if port is not None and not 1 <= port <= 65535:
        raise McpOAuthDiscoveryError(f"{field} URL 端口无效")
    return normalize_url(value)


def normalize_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port is not None and not (
        parsed.scheme.casefold() == "https" and port == 443
        or parsed.scheme.casefold() == "http" and port == 80
    ):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, parsed.query, ""))


def authorization_server_metadata_url(issuer: str) -> str:
    parsed = urlsplit(issuer)
    suffix = parsed.path.rstrip("/")
    path = "/.well-known/oauth-authorization-server" + suffix
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class OAuthMetadataDiscovery:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = MAX_METADATA_BYTES,
        allow_insecure_loopback: bool = False,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.allow_insecure_loopback = allow_insecure_loopback

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None

    async def discover(
        self,
        challenge: OAuthChallenge,
        expected_resource: str,
    ) -> tuple[ProtectedResourceMetadata, AuthorizationServerMetadata]:
        resource_metadata = await self.fetch_protected_resource(challenge, expected_resource)
        last_error: McpOAuthDiscoveryError | None = None
        for issuer in resource_metadata.authorization_servers:
            try:
                return resource_metadata, await self.fetch_authorization_server(issuer)
            except McpOAuthDiscoveryError as exc:
                last_error = exc
        if last_error is not None:
            raise McpOAuthDiscoveryError("未找到可用的 OAuth Authorization Server 元数据") from last_error
        raise McpOAuthDiscoveryError("Protected Resource Metadata 未声明 authorization_servers")

    async def fetch_protected_resource(
        self,
        challenge: OAuthChallenge,
        expected_resource: str,
    ) -> ProtectedResourceMetadata:
        data = await self._fetch_json(challenge.resource_metadata_url, "Protected Resource Metadata")
        resource = _required_string(data, "resource", "Protected Resource Metadata")
        expected = validate_https_url(
            expected_resource,
            "MCP resource",
            allow_insecure_loopback=self.allow_insecure_loopback,
        )
        actual = validate_https_url(resource, "resource", allow_insecure_loopback=self.allow_insecure_loopback)
        if actual != expected:
            raise McpOAuthDiscoveryError("Protected Resource Metadata 的 resource 与 MCP Server 不一致")
        issuers = _string_tuple(data.get("authorization_servers"), "authorization_servers", required=True)
        validated_issuers = tuple(
            validate_https_url(item, "authorization server", allow_insecure_loopback=self.allow_insecure_loopback)
            for item in issuers
        )
        scopes = _string_tuple(data.get("scopes_supported"), "scopes_supported")
        return ProtectedResourceMetadata(actual, validated_issuers, scopes)

    async def fetch_authorization_server(self, issuer: str) -> AuthorizationServerMetadata:
        normalized_issuer = validate_https_url(
            issuer,
            "issuer",
            allow_insecure_loopback=self.allow_insecure_loopback,
        )
        metadata_url = authorization_server_metadata_url(normalized_issuer)
        data = await self._fetch_json(metadata_url, "Authorization Server Metadata")
        actual_issuer = validate_https_url(
            _required_string(data, "issuer", "Authorization Server Metadata"),
            "issuer",
            allow_insecure_loopback=self.allow_insecure_loopback,
        )
        if actual_issuer != normalized_issuer:
            raise McpOAuthDiscoveryError("Authorization Server Metadata 的 issuer 不一致")
        authorization_endpoint = validate_https_url(
            _required_string(data, "authorization_endpoint", "Authorization Server Metadata"),
            "authorization_endpoint",
            allow_insecure_loopback=self.allow_insecure_loopback,
        )
        token_endpoint = validate_https_url(
            _required_string(data, "token_endpoint", "Authorization Server Metadata"),
            "token_endpoint",
            allow_insecure_loopback=self.allow_insecure_loopback,
        )
        registration_endpoint = data.get("registration_endpoint")
        if registration_endpoint is not None:
            if not isinstance(registration_endpoint, str) or not registration_endpoint:
                raise McpOAuthDiscoveryError("registration_endpoint 必须是非空字符串")
            registration_endpoint = validate_https_url(
                registration_endpoint,
                "registration_endpoint",
                allow_insecure_loopback=self.allow_insecure_loopback,
            )
        methods = _string_tuple(
            data.get("token_endpoint_auth_methods_supported", ["client_secret_basic"]),
            "token_endpoint_auth_methods_supported",
            required=True,
        )
        supported = tuple(method for method in methods if method in {"none", "client_secret_post", "client_secret_basic"})
        if not supported:
            raise McpOAuthDiscoveryError("Authorization Server 不支持可用的 token endpoint 认证方式")
        pkce_methods = _string_tuple(
            data.get("code_challenge_methods_supported"),
            "code_challenge_methods_supported",
            required=True,
        )
        if "S256" not in pkce_methods:
            raise McpOAuthDiscoveryError("Authorization Server 不支持 PKCE S256")
        return AuthorizationServerMetadata(
            issuer=actual_issuer,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            registration_endpoint=registration_endpoint,
            code_challenge_methods_supported=pkce_methods,
            token_endpoint_auth_methods_supported=supported,
        )

    async def _fetch_json(self, url: str, label: str) -> dict[str, Any]:
        validate_https_url(url, label, allow_insecure_loopback=self.allow_insecure_loopback)
        client = self._ensure_client()
        try:
            async with client.stream("GET", url, follow_redirects=False) as response:
                if 300 <= response.status_code < 400:
                    raise McpOAuthDiscoveryError(f"{label} 不允许 HTTP 重定向")
                if response.status_code != 200:
                    raise McpOAuthDiscoveryError(f"{label} 请求失败（HTTP {response.status_code}）")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > self.max_response_bytes:
                    raise McpOAuthDiscoveryError(f"{label} 响应超过大小限制")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_response_bytes:
                        raise McpOAuthDiscoveryError(f"{label} 响应超过大小限制")
                    chunks.append(chunk)
        except McpOAuthDiscoveryError:
            raise
        except httpx.HTTPError as exc:
            raise McpOAuthDiscoveryError(f"{label} 网络请求失败") from exc
        try:
            data = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise McpOAuthDiscoveryError(f"{label} 不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise McpOAuthDiscoveryError(f"{label} 必须是 JSON 对象")
        return data

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                trust_env=False,
                follow_redirects=False,
            )
            self._owns_client = True
        return self._client


def _split_quoted(value: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    start = 0
    quoted = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == delimiter and not quoted:
            parts.append(value[start:index])
            start = index + 1
    if quoted or escaped:
        raise McpOAuthDiscoveryError("WWW-Authenticate Header 引号格式无效")
    parts.append(value[start:])
    return parts


def _unquote_auth_value(value: str) -> str:
    text = value.strip()
    if not text:
        raise McpOAuthDiscoveryError("Bearer challenge 参数值为空")
    if not text.startswith('"'):
        if any(char.isspace() for char in text):
            raise McpOAuthDiscoveryError("Bearer challenge 参数值格式无效")
        return text
    if len(text) < 2 or not text.endswith('"'):
        raise McpOAuthDiscoveryError("Bearer challenge 参数引号格式无效")
    result: list[str] = []
    escaped = False
    for char in text[1:-1]:
        if escaped:
            result.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            result.append(char)
    if escaped:
        raise McpOAuthDiscoveryError("Bearer challenge 参数转义格式无效")
    return "".join(result)


def _required_string(data: dict[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise McpOAuthDiscoveryError(f"{label} 缺少 {key}")
    return value


def _string_tuple(value: Any, field: str, *, required: bool = False) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise McpOAuthDiscoveryError(f"{field} 必须是非空字符串数组")
    if required and not value:
        raise McpOAuthDiscoveryError(f"{field} 不能为空")
    return tuple(value)
