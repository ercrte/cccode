from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from typing import Any


PROTOCOL_VERSION = "2025-06-18"


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


def write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def success(message_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error(message_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = str(message.get("method", ""))
    message_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if "id" not in message:
        return None
    if method == "initialize":
        return success(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mcp-stdio-fixture", "version": "1.0.0"},
            },
        )
    if method == "ping":
        return success(message_id, {})
    if method == "tools/list":
        if params.get("cursor") == "page-2":
            return success(message_id, {"tools": [tool_definitions()[1]]})
        if params.get("paginate"):
            return success(message_id, {"tools": [tool_definitions()[0]], "nextCursor": "page-2"})
        return success(message_id, {"tools": tool_definitions()})
    if method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if name == "error":
            return success(
                message_id,
                {"content": [{"type": "text", "text": "business failure"}], "isError": True},
            )
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
    if method == "fixture/never":
        return None
    if method == "fixture/delay":
        time.sleep(float(params.get("seconds", 0.05)))
        return success(message_id, {"delayed": True})
    if method == "fixture/out_of_order":
        return success(message_id, {"id": message_id})
    return error(message_id, -32601, f"Method not found: {method}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-secret", default="")
    _ = parser.parse_args()

    delayed: list[dict[str, Any]] = []
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        message = json.loads(raw_line)
        if message.get("method") == "notifications/initialized":
            continue
        response = handle_request(message)
        if response is None:
            continue
        if message.get("method") == "fixture/out_of_order":
            delayed.append(response)
            if len(delayed) == 2:
                write_message(delayed.pop())
                write_message(delayed.pop())
            continue
        write_message(response)

    def drain_delayed() -> None:
        for response in reversed(delayed):
            write_message(response)

    threading.Thread(target=drain_delayed, daemon=True).start()


if __name__ == "__main__":
    main()
