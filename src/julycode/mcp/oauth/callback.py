from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlsplit

from julycode.mcp.errors import McpOAuthCallbackError
from julycode.mcp.oauth.models import OAuthCallbackResult


CALLBACK_PATH = "/oauth/callback"
MAX_REQUEST_LINE = 4096
MAX_HEADER_BYTES = 16384


class LoopbackOAuthCallback:
    def __init__(self, expected_state: str) -> None:
        self.expected_state = expected_state
        self._server: asyncio.AbstractServer | None = None
        self._result: asyncio.Future[OAuthCallbackResult] | None = None

    @property
    def redirect_uri(self) -> str:
        if self._server is None or not self._server.sockets:
            raise McpOAuthCallbackError("OAuth 回调服务尚未启动")
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}{CALLBACK_PATH}"

    async def start(self) -> str:
        if self._server is not None:
            return self.redirect_uri
        self._result = asyncio.get_running_loop().create_future()
        try:
            self._server = await asyncio.start_server(self._handle, host="127.0.0.1", port=0)
        except OSError as exc:
            raise McpOAuthCallbackError("无法启动本机 OAuth 回调服务") from exc
        return self.redirect_uri

    async def wait(self, timeout_seconds: float = 120.0) -> OAuthCallbackResult:
        if self._result is None:
            raise McpOAuthCallbackError("OAuth 回调服务尚未启动")
        try:
            return await asyncio.wait_for(asyncio.shield(self._result), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise McpOAuthCallbackError("等待 OAuth 浏览器回调超时") from exc
        finally:
            await self.close()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._result is not None and not self._result.done():
            self._result.set_exception(McpOAuthCallbackError("OAuth 授权已取消"))
            # close() 可能发生在无人等待的退出路径，主动取走异常。
            self._result.exception()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if self._result is None or self._result.done():
                await self._respond(writer, 409, "授权回调已处理。")
                return
            request_line = await reader.readline()
            if len(request_line) > MAX_REQUEST_LINE or not request_line.endswith(b"\n"):
                raise McpOAuthCallbackError("OAuth 回调请求行过长或不完整")
            try:
                method, target, version = request_line.decode("ascii").strip().split(" ", 2)
            except (UnicodeDecodeError, ValueError) as exc:
                raise McpOAuthCallbackError("OAuth 回调请求行无效") from exc
            if method != "GET" or version not in {"HTTP/1.0", "HTTP/1.1"}:
                raise McpOAuthCallbackError("OAuth 回调只接受 GET 请求")
            header_bytes = 0
            while True:
                line = await reader.readline()
                header_bytes += len(line)
                if header_bytes > MAX_HEADER_BYTES:
                    raise McpOAuthCallbackError("OAuth 回调 Header 过长")
                if line in {b"\r\n", b"\n", b""}:
                    break

            parsed = urlsplit(target)
            if parsed.path != CALLBACK_PATH or len(parsed.query) > MAX_REQUEST_LINE:
                raise McpOAuthCallbackError("OAuth 回调地址无效")
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            state = _single_query_value(query, "state")
            if state != self.expected_state:
                raise McpOAuthCallbackError("OAuth 回调 state 校验失败")
            error = _optional_single_query_value(query, "error")
            if error is not None:
                public_error = error if error in {
                    "access_denied",
                    "invalid_request",
                    "server_error",
                    "temporarily_unavailable",
                } else "unknown_error"
                raise McpOAuthCallbackError(f"OAuth 授权被拒绝（{public_error}）")
            code = _single_query_value(query, "code")
            self._result.set_result(OAuthCallbackResult(code=code, state=state))
            await self._respond(writer, 200, "JulyCode OAuth 授权已完成，可以关闭此页面。")
        except (McpOAuthCallbackError, ValueError) as exc:
            if self._result is not None and not self._result.done():
                message = str(exc) if isinstance(exc, McpOAuthCallbackError) else "OAuth 回调参数无效"
                self._result.set_exception(McpOAuthCallbackError(message))
            await self._respond(writer, 400, "JulyCode OAuth 授权失败，请返回终端查看。")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, asyncio.CancelledError):
                pass

    async def _respond(self, writer: asyncio.StreamWriter, status: int, message: str) -> None:
        reason = "OK" if status == 200 else "Bad Request" if status == 400 else "Conflict"
        body = (
            "<!doctype html><html><head><meta charset='utf-8'><title>JulyCode OAuth</title></head>"
            f"<body><p>{message}</p></body></html>"
        ).encode("utf-8")
        response = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + body
        writer.write(response)
        await writer.drain()


def _single_query_value(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key)
    if values is None or len(values) != 1 or not values[0]:
        raise McpOAuthCallbackError(f"OAuth 回调缺少唯一 {key} 参数")
    return values[0]


def _optional_single_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if values is None:
        return None
    if len(values) != 1 or not values[0]:
        raise McpOAuthCallbackError(f"OAuth 回调 {key} 参数无效")
    return values[0]
