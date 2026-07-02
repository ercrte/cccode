from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


PROTOCOL_VERSION = "2025-06-18"
SESSION_ID = "fixture-session"


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "echo",
            "title": "Echo",
            "description": "Echo text back to the caller",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        {
            "name": "same_name",
            "description": "Tool used for name collision tests",
            "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
    ]


def success(message_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error(message_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self.send_error(404)
            return
        request = self._read_json()
        method = str(request.get("method", ""))
        if method != "initialize" and self.headers.get("Mcp-Session-Id") != SESSION_ID:
            self._send_json(error(request.get("id"), -32000, "missing session"), status=400)
            return
        response = self._handle(request)
        if method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if isinstance(request.get("params"), dict) and request["params"].get("response") == "sse":
            self._send_sse(response)
            return
        self._send_json(response, session_header=method == "initialize")

    def do_DELETE(self) -> None:
        if self.path != "/mcp":
            self.send_error(404)
            return
        self.server.deleted = True  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _handle(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method", ""))
        message_id = request.get("id")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        if method == "initialize":
            return success(
                message_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "mcp-http-fixture", "version": "1.0.0"},
                },
            )
        if method == "tools/list":
            return success(message_id, {"tools": tool_definitions()})
        if method == "tools/call":
            name = str(params.get("name", ""))
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            if name != "echo":
                return error(message_id, -32602, f"Unknown tool: {name}")
            text = str(arguments.get("text", ""))
            return success(
                message_id,
                {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": {"text": text},
                    "isError": False,
                },
            )
        if method == "fixture/error":
            return error(message_id, -32000, "fixture protocol error")
        return success(message_id, {})

    def _send_json(self, payload: dict[str, Any], *, status: int = 200, session_header: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if session_header:
            self.send_header("Mcp-Session-Id", SESSION_ID)
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _send_sse(self, payload: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        note = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"}, ensure_ascii=False)
        body = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"data: {note}\n\n".encode("utf-8"))
        self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
        self.wfile.flush()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18766
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.deleted = False  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
