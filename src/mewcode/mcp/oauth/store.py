from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from typing import Any, Protocol

from mewcode.mcp.errors import McpOAuthStorageError
from mewcode.mcp.oauth.discovery import normalize_url
from mewcode.mcp.oauth.models import OAuthClientRegistration, OAuthCredentialBundle, OAuthTokenSet


KEYRING_SERVICE = "mewcode.mcp.oauth"


class CredentialStore(Protocol):
    warning: str | None

    async def load(self, server_name: str, resource: str) -> OAuthCredentialBundle | None:
        ...

    async def save(self, server_name: str, resource: str, bundle: OAuthCredentialBundle) -> None:
        ...

    async def delete(self, server_name: str, resource: str) -> None:
        ...


class MemoryCredentialStore:
    def __init__(self, warning: str | None = None) -> None:
        self.warning = warning
        self._items: dict[str, OAuthCredentialBundle] = {}

    async def load(self, server_name: str, resource: str) -> OAuthCredentialBundle | None:
        return self._items.get(credential_account(server_name, resource))

    async def save(self, server_name: str, resource: str, bundle: OAuthCredentialBundle) -> None:
        self._items[credential_account(server_name, resource)] = bundle

    async def delete(self, server_name: str, resource: str) -> None:
        self._items.pop(credential_account(server_name, resource), None)


class OAuthCredentialStore:
    """优先使用系统 Keyring；不可用时仅在当前进程内保存。"""

    def __init__(self, *, timeout_seconds: float = 3.0, keyring_module: Any | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.warning: str | None = None
        self._memory = MemoryCredentialStore()
        self._keyring = keyring_module
        self._persistent = True
        if self._keyring is None:
            try:
                import keyring

                self._keyring = keyring
            except ImportError:
                self._fallback("系统 Keyring 不可用，OAuth 凭据仅在当前进程内保存")

    async def load(self, server_name: str, resource: str) -> OAuthCredentialBundle | None:
        account = credential_account(server_name, resource)
        if not self._persistent:
            return await self._memory.load(server_name, resource)
        try:
            raw = await self._call(self._keyring.get_password, KEYRING_SERVICE, account)
            if raw is None:
                return await self._memory.load(server_name, resource)
            bundle = deserialize_credentials(raw)
            await self._memory.save(server_name, resource, bundle)
            return bundle
        except (McpOAuthStorageError, Exception) as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            self._fallback("系统 Keyring 无法读取，OAuth 凭据仅在当前进程内保存")
            return await self._memory.load(server_name, resource)

    async def save(self, server_name: str, resource: str, bundle: OAuthCredentialBundle) -> None:
        await self._memory.save(server_name, resource, bundle)
        if not self._persistent:
            return
        try:
            await self._call(
                self._keyring.set_password,
                KEYRING_SERVICE,
                credential_account(server_name, resource),
                serialize_credentials(bundle),
            )
        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            self._fallback("系统 Keyring 无法写入，OAuth 凭据仅在当前进程内保存")

    async def delete(self, server_name: str, resource: str) -> None:
        await self._memory.delete(server_name, resource)
        if not self._persistent:
            return
        try:
            await self._call(
                self._keyring.delete_password,
                KEYRING_SERVICE,
                credential_account(server_name, resource),
            )
        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            # 某些后端对不存在的条目也会抛错；删除仍视为本地成功。
            self._fallback("系统 Keyring 无法删除，已清除当前进程内的 OAuth 凭据")

    async def _call(self, function: Any, *args: Any) -> Any:
        if function is None:
            raise McpOAuthStorageError("Keyring 接口不可用")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        def finish_result(value: Any) -> None:
            if not future.done():
                future.set_result(value)

        def finish_error(exc: BaseException) -> None:
            if not future.done():
                future.set_exception(exc)

        def run() -> None:
            try:
                result = function(*args)
            except BaseException as exc:
                loop.call_soon_threadsafe(finish_error, exc)
            else:
                loop.call_soon_threadsafe(finish_result, result)

        # Keyring 后端可能永久阻塞；daemon 线程保证有限等待后不阻塞 MewCode 退出。
        threading.Thread(target=run, name="mewcode-keyring", daemon=True).start()
        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise McpOAuthStorageError("Keyring 操作超时") from exc

    def _fallback(self, warning: str) -> None:
        self._persistent = False
        if self.warning is None:
            self.warning = warning
            self._memory.warning = warning


def credential_account(server_name: str, resource: str) -> str:
    digest = hashlib.sha256(normalize_url(resource).encode("utf-8")).hexdigest()[:24]
    return f"{server_name}:{digest}"


def serialize_credentials(bundle: OAuthCredentialBundle) -> str:
    payload = {
        "version": 1,
        "resource": bundle.resource,
        "issuer": bundle.issuer,
        "client": {
            "client_id": bundle.client.client_id,
            "client_secret": bundle.client.client_secret,
            "token_endpoint_auth_method": bundle.client.token_endpoint_auth_method,
        },
        "token": {
            "access_token": bundle.token.access_token,
            "token_type": bundle.token.token_type,
            "expires_at": bundle.token.expires_at,
            "refresh_token": bundle.token.refresh_token,
            "scope": list(bundle.token.scope),
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def deserialize_credentials(raw: str) -> OAuthCredentialBundle:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError
        client_data = data["client"]
        token_data = data["token"]
        if not isinstance(client_data, dict) or not isinstance(token_data, dict):
            raise ValueError
        resource = _required_text(data, "resource")
        issuer = _required_text(data, "issuer")
        client_id = _required_text(client_data, "client_id")
        auth_method = _required_text(client_data, "token_endpoint_auth_method")
        client_secret = _optional_text(client_data, "client_secret")
        access_token = _required_text(token_data, "access_token")
        token_type = _required_text(token_data, "token_type")
        refresh_token = _optional_text(token_data, "refresh_token")
        expires_at_raw = token_data.get("expires_at")
        expires_at = None if expires_at_raw is None else float(expires_at_raw)
        scope_raw = token_data.get("scope", [])
        if not isinstance(scope_raw, list) or any(not isinstance(item, str) for item in scope_raw):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise McpOAuthStorageError("Keyring 中的 OAuth 凭据格式无效") from exc
    return OAuthCredentialBundle(
        resource=resource,
        issuer=issuer,
        client=OAuthClientRegistration(client_id, client_secret, auth_method),
        token=OAuthTokenSet(access_token, token_type, expires_at, refresh_token, tuple(scope_raw)),
    )


def _required_text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError
    return value


def _optional_text(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError
    return value
