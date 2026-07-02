from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from mewcode.config import McpServerConfig
from mewcode.errors import redact_secret
from mewcode.mcp.errors import McpConnectionError, McpProtocolError, McpTimeoutError
from mewcode.providers.sse import iter_sse_lines


DEFAULT_PROTOCOL_VERSION = "2025-06-18"


@dataclass(frozen=True)
class JsonRpcError:
    code: int
    message: str
    data: Any | None = None


class McpTransport(Protocol):
    async def start(self) -> None:
        ...

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        ...

    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        ...

    async def close(self) -> None:
        ...


class StdioMcpTransport:
    def __init__(self, config: McpServerConfig, *, timeout_seconds: float = 10.0) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[str, asyncio.Future[Mapping[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail = ""

    async def start(self) -> None:
        if self.process is not None:
            return
        if not self.config.command:
            raise McpConnectionError(f"MCP Server {self.config.name} 缺少 command")
        try:
            self.process = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **self.config.env},
            )
        except OSError as exc:
            raise McpConnectionError(self._redact(f"启动 MCP Server {self.config.name} 失败: {exc}")) from exc
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        self._ensure_started()
        request_id = self._request_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Mapping[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        await self._send(payload)
        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise McpTimeoutError(f"MCP Server {self.config.name} 请求 {method} 超时") from exc

    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        self._ensure_started()
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = dict(params)
        await self._send(payload)

    async def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=1.0)
        except asyncio.TimeoutError:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(process.wait()), timeout=1.0)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        await self._cancel_task(self._reader_task)
        await self._cancel_task(self._stderr_task)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(McpConnectionError(f"MCP Server {self.config.name} 已关闭"))
        self._pending.clear()
        self.process = None

    def _ensure_started(self) -> None:
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise McpConnectionError(f"MCP Server {self.config.name} 尚未启动")

    def _request_id(self) -> str:
        value = str(self._next_id)
        self._next_id += 1
        return value

    async def _send(self, payload: Mapping[str, Any]) -> None:
        self._ensure_started()
        assert self.process is not None
        assert self.process.stdin is not None
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._write_lock:
            try:
                self.process.stdin.write(line.encode("utf-8"))
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise McpConnectionError(self._redact(f"MCP Server {self.config.name} stdin 已关闭")) from exc

    async def _read_stdout(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        while True:
            raw_line = await self.process.stdout.readline()
            if not raw_line:
                self._fail_pending(McpConnectionError(self._redact(f"MCP Server {self.config.name} stdout 已关闭")))
                return
            try:
                message = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(message, dict):
                await self._handle_message(message)

    async def _read_stderr(self) -> None:
        assert self.process is not None
        assert self.process.stderr is not None
        while True:
            raw = await self.process.stderr.readline()
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace")
            self._stderr_tail = (self._stderr_tail + text)[-4000:]

    async def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            request_id = str(message.get("id"))
            future = self._pending.pop(request_id, None)
            if future is None or future.done():
                return
            try:
                future.set_result(_result_from_response(message))
            except McpProtocolError as exc:
                future.set_exception(exc)
            return

        if "id" in message and "method" in message:
            method = str(message.get("method", ""))
            if method == "ping":
                await self._send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
            else:
                await self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                )

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    async def _cancel_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _redact(self, text: str) -> str:
        redacted = text
        for value in self.config.env.values():
            redacted = redact_secret(redacted, value)
        return redact_secret(redacted)


class StreamableHttpMcpTransport:
    def __init__(
        self,
        config: McpServerConfig,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self._next_id = 1
        self._client = client
        self._owns_client = client is None

    async def start(self) -> None:
        self._ensure_client()

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        request_id = self._request_id()
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        result = await self._post(payload, expected_id=request_id)
        if method == "initialize":
            self.protocol_version = str(result.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION)
        return result

    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = dict(params)
        await self._post_notification(payload)

    async def close(self) -> None:
        client = self._client
        if client is None:
            return
        if self.session_id and self.config.url:
            try:
                await client.delete(self.config.url, headers=self._headers())
            except Exception:
                pass
        if self._owns_client:
            await client.aclose()
        self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False)
            self._owns_client = True
        return self._client

    def _request_id(self) -> str:
        value = str(self._next_id)
        self._next_id += 1
        return value

    async def _post(self, payload: Mapping[str, Any], *, expected_id: str) -> Mapping[str, Any]:
        if not self.config.url:
            raise McpConnectionError(f"MCP Server {self.config.name} 缺少 url")
        client = self._ensure_client()
        try:
            async with client.stream(
                "POST",
                self.config.url,
                headers=self._headers(),
                json=dict(payload),
            ) as response:
                await self._raise_for_status(response)
                if response.headers.get("Mcp-Session-Id"):
                    self.session_id = response.headers["Mcp-Session-Id"]
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type == "text/event-stream":
                    return await self._read_sse_response(response, expected_id)
                body = await response.aread()
        except McpProtocolError:
            raise
        except httpx.HTTPError as exc:
            raise McpConnectionError(self._redact(f"MCP HTTP 请求失败: {exc}")) from exc

        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise McpConnectionError(self._redact(f"MCP HTTP 响应不是合法 JSON: {body[:120]!r}")) from exc
        return _result_from_response(_require_response(message, expected_id))

    async def _post_notification(self, payload: Mapping[str, Any]) -> None:
        if not self.config.url:
            raise McpConnectionError(f"MCP Server {self.config.name} 缺少 url")
        client = self._ensure_client()
        try:
            response = await client.post(self.config.url, headers=self._headers(), json=dict(payload))
            await self._raise_for_status(response)
            await response.aclose()
        except httpx.HTTPError as exc:
            raise McpConnectionError(self._redact(f"MCP HTTP 通知失败: {exc}")) from exc

    async def _read_sse_response(self, response: httpx.Response, expected_id: str) -> Mapping[str, Any]:
        async for event in iter_sse_lines(response):
            try:
                message = json.loads(event.data)
            except json.JSONDecodeError as exc:
                raise McpConnectionError(f"MCP SSE 数据不是合法 JSON: {event.data[:120]}") from exc
            if not isinstance(message, dict):
                continue
            if str(message.get("id")) != expected_id:
                continue
            return _result_from_response(_require_response(message, expected_id))
        raise McpConnectionError(f"MCP SSE 响应未包含请求 id {expected_id}")

    async def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        body = await response.aread()
        detail = body.decode("utf-8", errors="replace")
        raise McpConnectionError(self._redact(f"MCP HTTP {response.status_code}: {detail}"))

    def _headers(self) -> dict[str, str]:
        headers = dict(self.config.headers)
        headers["Accept"] = "application/json, text/event-stream"
        headers["Content-Type"] = "application/json"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _redact(self, text: str) -> str:
        redacted = text
        for value in self.config.headers.values():
            redacted = redact_secret(redacted, value)
        return redact_secret(redacted)


def _require_response(message: Any, expected_id: str) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise McpConnectionError("MCP 响应结构无效")
    if str(message.get("id")) != expected_id:
        raise McpConnectionError(f"MCP 响应 id 不匹配: 期望 {expected_id}，实际 {message.get('id')}")
    return message


def _result_from_response(message: dict[str, Any]) -> Mapping[str, Any]:
    if "error" in message:
        error = message["error"]
        if isinstance(error, Mapping):
            code = error.get("code")
            message_text = str(error.get("message", "MCP 协议错误"))
            raise McpProtocolError(
                message_text,
                code=int(code) if isinstance(code, int) else None,
                data=error.get("data"),
            )
        raise McpProtocolError("MCP 协议错误")
    result = message.get("result")
    if isinstance(result, Mapping):
        return result
    raise McpConnectionError("MCP 响应缺少对象形式的 result")
