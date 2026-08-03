from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import secrets
import time
import threading
import webbrowser
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

import httpx

from julycode.config import McpOAuthConfig, McpServerConfig
from julycode.errors import redact_secret
from julycode.mcp.errors import (
    McpOAuthConfigError,
    McpOAuthDiscoveryError,
    McpOAuthError,
)
from julycode.mcp.oauth.callback import LoopbackOAuthCallback
from julycode.mcp.oauth.discovery import OAuthMetadataDiscovery, normalize_url, parse_www_authenticate
from julycode.mcp.oauth.models import (
    AuthorizationServerMetadata,
    McpOAuthStatus,
    OAuthChallenge,
    OAuthClientRegistration,
    OAuthCredentialBundle,
    OAuthTokenSet,
    ProtectedResourceMetadata,
)
from julycode.mcp.oauth.store import CredentialStore, OAuthCredentialStore


MAX_OAUTH_RESPONSE_BYTES = 1024 * 1024
TOKEN_EXPIRY_SKEW_SECONDS = 60.0
AuthorizationUrlCallback = Callable[[str, bool], Awaitable[None] | None]
StatusCallback = Callable[[McpOAuthStatus], None]


class OAuthProtocolClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = MAX_OAUTH_RESPONSE_BYTES,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None

    async def register_client(
        self,
        metadata: AuthorizationServerMetadata,
        redirect_uri: str,
    ) -> OAuthClientRegistration:
        if metadata.registration_endpoint is None:
            raise McpOAuthConfigError("Authorization Server 不支持动态客户端注册")
        requested_method = "none" if "none" in metadata.token_endpoint_auth_methods_supported else metadata.token_endpoint_auth_methods_supported[0]
        data = await self._post_json(
            metadata.registration_endpoint,
            json_body={
                "client_name": "JulyCode",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": requested_method,
            },
            label="动态客户端注册",
        )
        client_id = _required_text(data, "client_id", "动态客户端注册")
        client_secret = _optional_text(data, "client_secret", "动态客户端注册")
        method = data.get("token_endpoint_auth_method", requested_method)
        if method not in metadata.token_endpoint_auth_methods_supported:
            raise McpOAuthConfigError("动态注册返回了不支持的 token endpoint 认证方式")
        if method != "none" and not client_secret:
            raise McpOAuthConfigError("动态注册未返回所需的 client_secret")
        return OAuthClientRegistration(client_id, client_secret, str(method))

    def static_client(
        self,
        config: McpOAuthConfig,
        metadata: AuthorizationServerMetadata,
    ) -> OAuthClientRegistration:
        if not config.client_id:
            raise McpOAuthConfigError("OAuth Server 不支持 DCR，且未配置 client_id")
        methods = metadata.token_endpoint_auth_methods_supported
        if config.client_secret:
            method = next((item for item in ("client_secret_basic", "client_secret_post") if item in methods), None)
            if method is None:
                raise McpOAuthConfigError("预注册客户端的 secret 与 token endpoint 认证方式不兼容")
            return OAuthClientRegistration(config.client_id, config.client_secret, method)
        if "none" not in methods:
            raise McpOAuthConfigError("Authorization Server 不接受无 secret 的预注册客户端")
        return OAuthClientRegistration(config.client_id, None, "none")

    def authorization_url(
        self,
        metadata: AuthorizationServerMetadata,
        client: OAuthClientRegistration,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scopes: tuple[str, ...],
        resource: str,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": client.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "resource": resource,
        }
        if scopes:
            params["scope"] = " ".join(scopes)
        separator = "&" if "?" in metadata.authorization_endpoint else "?"
        return f"{metadata.authorization_endpoint}{separator}{urlencode(params)}"

    async def exchange_code(
        self,
        metadata: AuthorizationServerMetadata,
        client: OAuthClientRegistration,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        resource: str,
    ) -> OAuthTokenSet:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "resource": resource,
        }
        return await self._token_request(metadata, client, form)

    async def refresh_token(
        self,
        metadata: AuthorizationServerMetadata,
        client: OAuthClientRegistration,
        *,
        refresh_token: str,
        resource: str,
    ) -> OAuthTokenSet:
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "resource": resource,
        }
        return await self._token_request(metadata, client, form)

    async def _token_request(
        self,
        metadata: AuthorizationServerMetadata,
        client: OAuthClientRegistration,
        form: dict[str, str],
    ) -> OAuthTokenSet:
        headers: dict[str, str] = {"Accept": "application/json"}
        auth: httpx.BasicAuth | None = None
        if client.token_endpoint_auth_method == "none":
            form["client_id"] = client.client_id
        elif client.token_endpoint_auth_method == "client_secret_post":
            if client.client_secret is None:
                raise McpOAuthConfigError("token endpoint 请求缺少 client_secret")
            form["client_id"] = client.client_id
            form["client_secret"] = client.client_secret
        elif client.token_endpoint_auth_method == "client_secret_basic":
            if client.client_secret is None:
                raise McpOAuthConfigError("token endpoint 请求缺少 client_secret")
            auth = httpx.BasicAuth(client.client_id, client.client_secret)
        else:
            raise McpOAuthConfigError("不支持的 token endpoint 认证方式")
        data = await self._post_json(
            metadata.token_endpoint,
            form=form,
            headers=headers,
            auth=auth,
            label="OAuth token endpoint",
        )
        access_token = _required_text(data, "access_token", "OAuth token endpoint")
        token_type = str(data.get("token_type", "Bearer"))
        if token_type.casefold() != "bearer":
            raise McpOAuthError("OAuth token endpoint 返回了不支持的 token_type")
        refresh_token = _optional_text(data, "refresh_token", "OAuth token endpoint")
        scope_value = data.get("scope", "")
        if not isinstance(scope_value, str):
            raise McpOAuthError("OAuth token endpoint 返回了无效 scope")
        expires_at: float | None = None
        if data.get("expires_in") is not None:
            try:
                expires_in = float(data["expires_in"])
            except (TypeError, ValueError) as exc:
                raise McpOAuthError("OAuth token endpoint 返回了无效 expires_in") from exc
            if expires_in < 0:
                raise McpOAuthError("OAuth token endpoint 返回了无效 expires_in")
            expires_at = time.time() + expires_in
        return OAuthTokenSet(
            access_token=access_token,
            token_type="Bearer",
            expires_at=expires_at,
            refresh_token=refresh_token,
            scope=tuple(item for item in scope_value.split(" ") if item),
        )

    async def _post_json(
        self,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        auth: httpx.BasicAuth | None = None,
        label: str,
    ) -> dict[str, Any]:
        client = self._ensure_client()
        try:
            async with client.stream(
                "POST",
                url,
                json=json_body,
                data=form,
                headers=headers,
                auth=auth,
                follow_redirects=False,
            ) as response:
                if 300 <= response.status_code < 400:
                    raise McpOAuthError(f"{label} 不允许 HTTP 重定向")
                if response.status_code < 200 or response.status_code >= 300:
                    raise McpOAuthError(f"{label} 请求失败（HTTP {response.status_code}）")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_response_bytes:
                        raise McpOAuthError(f"{label} 响应超过大小限制")
                    chunks.append(chunk)
        except McpOAuthError:
            raise
        except httpx.HTTPError as exc:
            raise McpOAuthError(f"{label} 网络请求失败") from exc
        try:
            data = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise McpOAuthError(f"{label} 不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise McpOAuthError(f"{label} 必须返回 JSON 对象")
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


class McpOAuthSession:
    def __init__(
        self,
        server: McpServerConfig,
        *,
        store: CredentialStore | None = None,
        discovery: OAuthMetadataDiscovery | None = None,
        protocol_client: OAuthProtocolClient | None = None,
        browser_opener: Callable[[str], bool] | None = None,
        callback_factory: Callable[[str], LoopbackOAuthCallback] = LoopbackOAuthCallback,
        status_callback: StatusCallback | None = None,
        callback_timeout_seconds: float = 120.0,
    ) -> None:
        if server.transport != "http" or not server.url or server.oauth is None or not server.oauth.enabled:
            raise McpOAuthConfigError("McpOAuthSession 需要启用 OAuth 的 HTTP Server")
        self.server = server
        self.config = server.oauth
        self.resource = normalize_url(server.url)
        self.store = store or OAuthCredentialStore()
        self.discovery = discovery or OAuthMetadataDiscovery()
        self.protocol_client = protocol_client or OAuthProtocolClient()
        self.browser_opener = browser_opener or webbrowser.open
        self.callback_factory = callback_factory
        self.status_callback = status_callback
        self.callback_timeout_seconds = callback_timeout_seconds
        self.challenge: OAuthChallenge | None = None
        self.resource_metadata: ProtectedResourceMetadata | None = None
        self.authorization_metadata: AuthorizationServerMetadata | None = None
        self.credentials: OAuthCredentialBundle | None = None
        self._status = McpOAuthStatus(server.name, warning=self.store.warning)
        self._authorize_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._active_callback: LoopbackOAuthCallback | None = None

    @property
    def status(self) -> McpOAuthStatus:
        warning = self.store.warning
        if warning != self._status.warning:
            self._status = replace(self._status, warning=warning)
        return self._status

    async def restore(self) -> None:
        bundle = await self.store.load(self.server.name, self.resource)
        if bundle is None or normalize_url(bundle.resource) != self.resource:
            self._set_status("authorization_required", "需要授权")
            return
        self.credentials = bundle
        if self._token_expiring(bundle.token) and bundle.token.refresh_token is None:
            self.credentials = None
            await self.store.delete(self.server.name, self.resource)
            self._set_status("authorization_required", "访问令牌已过期，需要重新授权")
            return
        self._set_status("authorized", "已恢复 OAuth 凭据")

    async def authorization_headers(self) -> Mapping[str, str]:
        credentials = self.credentials
        if credentials is None:
            return {}
        if self._token_expiring(credentials.token):
            if not credentials.token.refresh_token:
                self.credentials = None
                self._set_status("authorization_required", "访问令牌已过期，需要重新授权")
                return {}
            # 新进程尚未拿到 challenge/元数据时先发送现有 token，由 401 驱动安全发现和刷新。
            if self.authorization_metadata is not None or self.challenge is not None:
                await self.refresh(force=False)
            credentials = self.credentials
            if credentials is None:
                return {}
        return {"Authorization": f"Bearer {credentials.token.access_token}"}

    async def handle_unauthorized(self, www_authenticate: str | None) -> bool:
        try:
            self.challenge = parse_www_authenticate(www_authenticate)
        except McpOAuthDiscoveryError:
            self._set_status("authorization_required", "Server 返回了无效 OAuth challenge")
            return False
        credentials = self.credentials
        if credentials is not None and credentials.token.refresh_token:
            return await self.refresh(force=True)
        self.credentials = None
        self._set_status("authorization_required", "需要授权；请运行 /mcp auth " + self.server.name)
        return False

    async def authorization_failed(self) -> None:
        """刷新后仍收到 401 时停止继续重试，并移除失效凭据。"""
        await self._invalidate_after_refresh_failure()

    async def authorize(self, on_authorization_url: AuthorizationUrlCallback | None = None) -> None:
        async with self._authorize_lock:
            if self.credentials is not None and self.status.state == "authorized":
                raise McpOAuthError(f"MCP Server {self.server.name} 已授权；如需更换账号请先 logout")
            if self.challenge is None:
                raise McpOAuthError(
                    f"MCP Server {self.server.name} 尚未返回 OAuth challenge；请先检查 Server 连通性"
                )
            self._set_status("authorizing", "等待浏览器授权")
            callback: LoopbackOAuthCallback | None = None
            try:
                resource_metadata, authorization_metadata = await self.discovery.discover(
                    self.challenge,
                    self.resource,
                )
                self.resource_metadata = resource_metadata
                self.authorization_metadata = authorization_metadata
                state, verifier, challenge = generate_pkce()
                callback = self.callback_factory(state)
                self._active_callback = callback
                redirect_uri = await callback.start()
                client = await self._select_client(authorization_metadata, redirect_uri)
                scopes = self._select_scopes(resource_metadata)
                authorization_url = self.protocol_client.authorization_url(
                    authorization_metadata,
                    client,
                    redirect_uri=redirect_uri,
                    state=state,
                    code_challenge=challenge,
                    scopes=scopes,
                    resource=self.resource,
                )
                browser_opened = False
                if on_authorization_url is not None:
                    await _maybe_await(on_authorization_url(authorization_url, False))
                try:
                    browser_opened = await _run_browser_opener(self.browser_opener, authorization_url)
                except Exception:
                    browser_opened = False
                if not browser_opened and on_authorization_url is not None:
                    await _maybe_await(on_authorization_url(authorization_url, True))
                result = await callback.wait(self.callback_timeout_seconds)
                token = await self.protocol_client.exchange_code(
                    authorization_metadata,
                    client,
                    code=result.code,
                    code_verifier=verifier,
                    redirect_uri=redirect_uri,
                    resource=self.resource,
                )
                bundle = OAuthCredentialBundle(
                    resource=self.resource,
                    issuer=authorization_metadata.issuer,
                    client=client,
                    token=token,
                )
                await self.store.save(self.server.name, self.resource, bundle)
                self.credentials = bundle
                self._set_status("authorized", "授权成功")
            except asyncio.CancelledError:
                self._set_status("authorization_required", "授权已取消")
                raise
            except McpOAuthError:
                self._set_status("authorization_required", "授权失败，需要重试")
                raise
            except Exception as exc:
                self._set_status("authorization_required", "授权失败，需要重试")
                raise McpOAuthError("OAuth 授权流程失败") from exc
            finally:
                if callback is not None:
                    await callback.close()
                self._active_callback = None

    async def refresh(self, *, force: bool) -> bool:
        previous = self.credentials
        if previous is None or previous.token.refresh_token is None:
            self._set_status("authorization_required", "缺少 refresh token，需要重新授权")
            return False
        previous_access_token = previous.token.access_token
        async with self._refresh_lock:
            current = self.credentials
            if current is None or current.token.refresh_token is None:
                return False
            if current.token.access_token != previous_access_token:
                return True
            if not force and not self._token_expiring(current.token):
                return True
            metadata = self.authorization_metadata
            if metadata is None or metadata.issuer != current.issuer:
                if self.challenge is None:
                    self._set_status("refresh_failed", "无法发现刷新端点，需要重新授权")
                    return False
                try:
                    self.resource_metadata, metadata = await self.discovery.discover(self.challenge, self.resource)
                    if metadata.issuer != current.issuer:
                        raise McpOAuthDiscoveryError("凭据 issuer 与当前 Server 不一致")
                    self.authorization_metadata = metadata
                except McpOAuthError:
                    await self._invalidate_after_refresh_failure()
                    return False
            self._set_status("refreshing", "正在刷新访问令牌")
            try:
                token = await self.protocol_client.refresh_token(
                    metadata,
                    current.client,
                    refresh_token=current.token.refresh_token,
                    resource=self.resource,
                )
                if token.refresh_token is None:
                    token = replace(token, refresh_token=current.token.refresh_token)
                bundle = replace(current, token=token)
                await self.store.save(self.server.name, self.resource, bundle)
                self.credentials = bundle
                self._set_status("authorized", "访问令牌已刷新")
                return True
            except (McpOAuthError, Exception) as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                await self._invalidate_after_refresh_failure()
                return False

    async def logout(self) -> None:
        if self._active_callback is not None:
            await self._active_callback.close()
        self.credentials = None
        self.resource_metadata = None
        self.authorization_metadata = None
        await self.store.delete(self.server.name, self.resource)
        self._set_status("authorization_required", "已退出授权")

    async def close(self) -> None:
        if self._active_callback is not None:
            await self._active_callback.close()
            self._active_callback = None
        await self.discovery.close()
        await self.protocol_client.close()

    def redact(self, text: str) -> str:
        credentials = self.credentials
        if credentials is None:
            return redact_secret(text)
        values = (
            credentials.token.access_token,
            credentials.token.refresh_token,
            credentials.client.client_secret,
        )
        redacted = text
        for value in values:
            if value:
                redacted = redact_secret(redacted, value)
        return redact_secret(redacted)

    async def _select_client(
        self,
        metadata: AuthorizationServerMetadata,
        redirect_uri: str,
    ) -> OAuthClientRegistration:
        dcr_error: McpOAuthError | None = None
        if metadata.registration_endpoint is not None:
            try:
                return await self.protocol_client.register_client(metadata, redirect_uri)
            except McpOAuthError as exc:
                dcr_error = exc
        try:
            return self.protocol_client.static_client(self.config, metadata)
        except McpOAuthError as exc:
            if dcr_error is not None:
                raise McpOAuthConfigError("动态注册失败，且没有可用的预注册 OAuth 客户端") from dcr_error
            raise exc

    def _select_scopes(self, resource_metadata: ProtectedResourceMetadata) -> tuple[str, ...]:
        if self.challenge is not None and self.challenge.scopes:
            return self.challenge.scopes
        if self.config.scopes:
            return self.config.scopes
        return resource_metadata.scopes_supported

    def _token_expiring(self, token: OAuthTokenSet) -> bool:
        return token.expires_at is not None and token.expires_at <= time.time() + TOKEN_EXPIRY_SKEW_SECONDS

    async def _invalidate_after_refresh_failure(self) -> None:
        self.credentials = None
        await self.store.delete(self.server.name, self.resource)
        self._set_status("refresh_failed", "令牌刷新失败，需要重新授权")

    def _set_status(self, state: Any, message: str) -> None:
        self._status = McpOAuthStatus(
            server_name=self.server.name,
            state=state,
            message=message,
            warning=self.store.warning,
        )
        if self.status_callback is not None:
            self.status_callback(self._status)


def generate_pkce() -> tuple[str, str, str]:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return state, verifier, challenge


async def _maybe_await(value: Awaitable[None] | None) -> None:
    if inspect.isawaitable(value):
        await value


async def _run_browser_opener(opener: Callable[[str], bool], url: str) -> bool:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[bool] = loop.create_future()

    def finish(value: bool) -> None:
        if not future.done():
            future.set_result(value)

    def run() -> None:
        try:
            opened = bool(opener(url))
        except Exception:
            opened = False
        loop.call_soon_threadsafe(finish, opened)

    threading.Thread(target=run, name="julycode-oauth-browser", daemon=True).start()
    return await future


def _required_text(data: dict[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise McpOAuthError(f"{label} 缺少 {key}")
    return value


def _optional_text(data: dict[str, Any], key: str, label: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise McpOAuthError(f"{label} 的 {key} 无效")
    return value
