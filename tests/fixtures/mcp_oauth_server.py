from __future__ import annotations

import argparse
import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit


PROTOCOL_VERSION = "2025-06-18"
SESSION_ID = "oauth-fixture-session"


class OAuthFixtureServer(ThreadingHTTPServer):
    base_url: str
    access_token: str
    refresh_token: str
    authorization_code: str
    rotate_refresh: bool
    request_log: list[dict[str, Any]]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def fixture(self) -> OAuthFixtureServer:
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/oauth/resource":
            self._json(
                {
                    "resource": f"{self.fixture.base_url}/mcp",
                    "authorization_servers": [self.fixture.base_url],
                    "scopes_supported": ["mcp:tools"],
                }
            )
            return
        if parsed.path == "/.well-known/oauth-authorization-server":
            self._json(
                {
                    "issuer": self.fixture.base_url + "/",
                    "authorization_endpoint": f"{self.fixture.base_url}/authorize",
                    "token_endpoint": f"{self.fixture.base_url}/token",
                    "registration_endpoint": f"{self.fixture.base_url}/register",
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                }
            )
            return
        if parsed.path == "/authorize":
            query = parse_qs(parsed.query)
            redirect_uri = _single(query, "redirect_uri")
            state = _single(query, "state")
            self.fixture.request_log.append(
                {
                    "endpoint": "authorize",
                    "resource": _single(query, "resource"),
                    "scope": _single(query, "scope"),
                    "code_challenge_method": _single(query, "code_challenge_method"),
                }
            )
            location = f"{redirect_uri}?{urlencode({'code': self.fixture.authorization_code, 'state': state})}"
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed.path == "/log":
            self._json({"requests": self.fixture.request_log})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/register":
            body = self._read_json()
            self.fixture.request_log.append(
                {"endpoint": "register", "redirect_uris": body.get("redirect_uris", [])}
            )
            self._json(
                {
                    "client_id": "fixture-dynamic-client",
                    "token_endpoint_auth_method": "none",
                },
                status=HTTPStatus.CREATED,
            )
            return
        if parsed.path == "/token":
            form = self._read_form()
            grant_type = _single(form, "grant_type")
            if _single(form, "resource") != f"{self.fixture.base_url}/mcp":
                self._json({"error": "invalid_target"}, status=HTTPStatus.BAD_REQUEST)
                return
            if grant_type == "authorization_code":
                if _single(form, "code") != self.fixture.authorization_code or not _single(form, "code_verifier"):
                    self._json({"error": "invalid_grant"}, status=HTTPStatus.BAD_REQUEST)
                    return
            elif grant_type == "refresh_token":
                if _single(form, "refresh_token") != self.fixture.refresh_token:
                    self._json({"error": "invalid_grant"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self.fixture.access_token = "fixture-access-token-refreshed"
                if self.fixture.rotate_refresh:
                    self.fixture.refresh_token = "fixture-refresh-token-rotated"
            else:
                self._json({"error": "unsupported_grant_type"}, status=HTTPStatus.BAD_REQUEST)
                return
            self.fixture.request_log.append({"endpoint": "token", "grant_type": grant_type})
            payload = {
                "access_token": self.fixture.access_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "mcp:tools",
            }
            if grant_type == "authorization_code" or self.fixture.rotate_refresh:
                payload["refresh_token"] = self.fixture.refresh_token
            self._json(payload)
            return
        if parsed.path == "/mcp":
            if self.headers.get("Authorization") != f"Bearer {self.fixture.access_token}":
                self.send_response(HTTPStatus.UNAUTHORIZED)
                self.send_header(
                    "WWW-Authenticate",
                    f'Bearer resource_metadata="{self.fixture.base_url}/oauth/resource", scope="mcp:tools"',
                )
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            request = self._read_json()
            self._handle_mcp(request)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        if urlsplit(self.path).path != "/mcp":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_mcp(self, request: dict[str, Any]) -> None:
        method = str(request.get("method", ""))
        message_id = request.get("id")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        if method == "notifications/initialized":
            self.send_response(HTTPStatus.ACCEPTED)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mcp-oauth-fixture", "version": "1.0"},
            }
            self._mcp_result(message_id, result, session=True)
            return
        if method == "tools/list":
            self._mcp_result(
                message_id,
                {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo OAuth MCP fixture text",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                                "additionalProperties": False,
                            },
                        }
                    ]
                },
            )
            return
        if method == "tools/call" and params.get("name") == "echo":
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            text = str(arguments.get("text", ""))
            self.fixture.request_log.append({"endpoint": "tools/call", "tool": "echo", "text": text})
            self._mcp_result(
                message_id,
                {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": {"text": text},
                    "isError": False,
                },
            )
            return
        self._json(
            {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": "not found"}}
        )

    def _mcp_result(self, message_id: object, result: dict[str, Any], *, session: bool = False) -> None:
        headers = {"Mcp-Session-Id": SESSION_ID} if session else None
        self._json({"jsonrpc": "2.0", "id": message_id, "result": result}, headers=headers)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8")) if raw else {}
        return data if isinstance(data, dict) else {}

    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return parse_qs(raw, keep_blank_values=True)

    def _json(
        self,
        payload: dict[str, Any],
        *,
        status: int = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _single(values: dict[str, list[str]], key: str) -> str:
    items = values.get(key, [])
    return items[0] if len(items) == 1 else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18767)
    parser.add_argument("--rotate-refresh", action="store_true")
    args = parser.parse_args()
    server = OAuthFixtureServer(("127.0.0.1", args.port), Handler)
    server.base_url = f"http://127.0.0.1:{server.server_port}"
    server.access_token = "fixture-access-token"
    server.refresh_token = "fixture-refresh-token"
    server.authorization_code = secrets.token_urlsafe(24)
    server.rotate_refresh = args.rotate_refresh
    server.request_log = []
    print(json.dumps({"base_url": server.base_url}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
