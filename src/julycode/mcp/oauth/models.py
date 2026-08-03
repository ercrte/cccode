from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


OAuthState = Literal[
    "authorization_required",
    "authorizing",
    "authorized",
    "refreshing",
    "refresh_failed",
]


@dataclass(frozen=True)
class OAuthChallenge:
    resource_metadata_url: str
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProtectedResourceMetadata:
    resource: str
    authorization_servers: tuple[str, ...]
    scopes_supported: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    code_challenge_methods_supported: tuple[str, ...] = ()
    token_endpoint_auth_methods_supported: tuple[str, ...] = ("none",)


@dataclass(frozen=True)
class OAuthClientRegistration:
    client_id: str
    client_secret: str | None = field(default=None, repr=False)
    token_endpoint_auth_method: str = "none"


@dataclass(frozen=True)
class OAuthTokenSet:
    access_token: str = field(repr=False)
    token_type: str = "Bearer"
    expires_at: float | None = None
    refresh_token: str | None = field(default=None, repr=False)
    scope: tuple[str, ...] = ()


@dataclass(frozen=True)
class OAuthCredentialBundle:
    resource: str
    issuer: str
    client: OAuthClientRegistration
    token: OAuthTokenSet


@dataclass(frozen=True)
class OAuthCallbackResult:
    code: str = field(repr=False)
    state: str = field(repr=False)


@dataclass(frozen=True)
class McpOAuthStatus:
    server_name: str
    state: OAuthState = "authorization_required"
    message: str = "需要授权"
    warning: str | None = None
