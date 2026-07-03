from __future__ import annotations

import pytest

from mewcode.mcp.oauth.models import OAuthClientRegistration, OAuthCredentialBundle, OAuthTokenSet
from mewcode.mcp.oauth.store import (
    MemoryCredentialStore,
    OAuthCredentialStore,
    credential_account,
    deserialize_credentials,
    serialize_credentials,
)


def bundle() -> OAuthCredentialBundle:
    return OAuthCredentialBundle(
        resource="https://mcp.test/mcp",
        issuer="https://auth.test/",
        client=OAuthClientRegistration("client", "client-secret", "client_secret_post"),
        token=OAuthTokenSet("access-secret", "Bearer", 1234.0, "refresh-secret", ("repo",)),
    )


class FakeKeyring:
    def __init__(self, *, fail: bool = False) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.fail = fail

    def get_password(self, service: str, account: str) -> str | None:
        if self.fail:
            raise RuntimeError("locked")
        return self.items.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        if self.fail:
            raise RuntimeError("locked")
        self.items[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        if self.fail:
            raise RuntimeError("locked")
        self.items.pop((service, account), None)


def test_oauth_credentials_roundtrip_and_repr_hide_secrets() -> None:
    credentials = bundle()
    restored = deserialize_credentials(serialize_credentials(credentials))

    assert restored == credentials
    rendered = repr(restored)
    assert "access-secret" not in rendered
    assert "refresh-secret" not in rendered
    assert "client-secret" not in rendered


@pytest.mark.asyncio
async def test_oauth_keyring_store_persists_deletes_and_isolates_url() -> None:
    keyring = FakeKeyring()
    store = OAuthCredentialStore(keyring_module=keyring)
    credentials = bundle()

    await store.save("demo", credentials.resource, credentials)

    assert await store.load("demo", credentials.resource) == credentials
    assert await store.load("demo", "https://mcp.test/other") is None
    assert credential_account("demo", credentials.resource) != credential_account("demo", "https://mcp.test/other")
    await store.delete("demo", credentials.resource)
    assert await store.load("demo", credentials.resource) is None


@pytest.mark.asyncio
async def test_oauth_keyring_failure_falls_back_to_memory_with_one_warning() -> None:
    store = OAuthCredentialStore(keyring_module=FakeKeyring(fail=True))
    credentials = bundle()

    await store.save("demo", credentials.resource, credentials)
    first_warning = store.warning
    restored = await store.load("demo", credentials.resource)

    assert restored == credentials
    assert first_warning is not None
    assert store.warning == first_warning
    assert "当前进程" in store.warning


@pytest.mark.asyncio
async def test_memory_store_never_writes_plaintext_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = MemoryCredentialStore()
    await store.save("demo", bundle().resource, bundle())
    assert list(tmp_path.iterdir()) == []
