from __future__ import annotations

import json
import hashlib
from collections.abc import AsyncIterator
from typing import Any

import httpx

from julycode.config import AppConfig
from julycode.errors import ProviderError, redact_secret
from julycode.prompting.base import PromptBlock
from julycode.providers.base import ChatMessage, ChatRequest, PromptCacheUsage, StreamEvent, TokenUsage
from julycode.providers.sse import iter_sse_lines
from julycode.tools.base import ToolCall, ToolSpec


class OpenAIProvider:
    def __init__(self, config: AppConfig, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        text_parts: list[str] = []
        tool_call_parts: dict[int, dict[str, Any]] = {}
        yield StreamEvent(type="message_start")

        client = self._client or httpx.AsyncClient(timeout=self.config.timeout_seconds, trust_env=False)
        should_close = self._client is None
        try:
            include_cache_options = True
            while True:
                try:
                    async with client.stream(
                        "POST",
                        f"{self.config.base_url}/chat/completions",
                        headers=self._headers(),
                        json=self._payload(request, include_cache_options=include_cache_options),
                    ) as response:
                        await self._raise_for_status(response)
                        async for event in iter_sse_lines(response):
                            if event.data == "[DONE]":
                                break
                            chunk = self._parse_chunk(event.data)
                            usage = self._usage_from_chunk(chunk)
                            if usage is not None:
                                yield StreamEvent(type="usage", usage=usage)
                            for choice in chunk.get("choices", []):
                                delta = choice.get("delta") or {}
                                content = delta.get("content")
                                if content:
                                    text_parts.append(content)
                                    yield StreamEvent(type="text_delta", text=content)
                                for tool_delta in delta.get("tool_calls") or []:
                                    index = int(tool_delta.get("index", 0))
                                    part = tool_call_parts.setdefault(
                                        index,
                                        {"id": "", "name": "", "arguments_parts": []},
                                    )
                                    if tool_delta.get("id"):
                                        part["id"] = str(tool_delta["id"])
                                    function = tool_delta.get("function") or {}
                                    if function.get("name"):
                                        part["name"] = str(function["name"])
                                    arguments_delta = function.get("arguments")
                                    if arguments_delta:
                                        text = str(arguments_delta)
                                        part["arguments_parts"].append(text)
                                        yield StreamEvent(
                                            type="tool_call_delta",
                                            tool_call_id=part["id"] or None,
                                            tool_name=part["name"] or None,
                                            arguments_delta=text,
                                        )
                    break
                except ProviderError as exc:
                    if include_cache_options and self._is_cache_option_unsupported(exc):
                        include_cache_options = False
                        continue
                    raise

            message = ChatMessage(
                role="assistant",
                content="".join(text_parts),
                tool_calls=tuple(self._build_tool_calls(tool_call_parts)),
            )
            yield StreamEvent(type="message_done", message=message)
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError(redact_secret(f"OpenAI 请求失败: {exc}", self.config.api_key)) from exc
        finally:
            if should_close:
                await client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, request: ChatRequest, *, include_cache_options: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                *self._prompt_messages(request),
                *[self._message_payload(message) for message in request.messages],
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            payload["tools"] = [self._tool_payload(tool) for tool in request.tools]
        if include_cache_options and self.config.prompt_cache.enabled:
            if self.config.prompt_cache.openai_cache_key:
                cache_key = self._prompt_cache_key(request)
                if cache_key is not None:
                    payload["prompt_cache_key"] = cache_key
            if self.config.prompt_cache.openai_retention is not None:
                payload["prompt_cache_retention"] = self.config.prompt_cache.openai_retention
        return payload

    def _prompt_messages(self, request: ChatRequest) -> list[dict[str, Any]]:
        if request.prompt is None:
            return []

        messages: list[dict[str, Any]] = []
        cacheable_text = self._prompt_blocks_text(
            [*request.prompt.stable_blocks, *[block for block in request.prompt.runtime_blocks if block.cacheable]]
        )
        if cacheable_text:
            messages.append({"role": "system", "content": cacheable_text})

        for block in request.prompt.generated_context_blocks:
            text = self._prompt_blocks_text((block,))
            if text:
                messages.append({"role": "system", "content": text})

        runtime_text = self._prompt_blocks_text([block for block in request.prompt.runtime_blocks if not block.cacheable])
        if runtime_text:
            messages.append({"role": "system", "content": runtime_text})
        return messages

    def _prompt_blocks_text(self, blocks: list[PromptBlock] | tuple[PromptBlock, ...] | Any) -> str:
        parts = []
        for block in blocks:
            text = str(block.text).strip()
            if text:
                parts.append(f"## {block.title}\n{text}")
        return "\n\n".join(parts)

    def _prompt_cache_key(self, request: ChatRequest) -> str | None:
        prefix_text = self._prompt_blocks_text(self._cacheable_prompt_blocks(request))
        tools = [self._tool_payload(tool) for tool in request.tools]
        if not prefix_text and not tools:
            return None
        payload = {
            "model": self.config.model,
            "prompt_prefix": prefix_text,
            "tools": tools,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        namespace = self.config.prompt_cache.key_namespace
        return f"{namespace}:{digest}"

    def _cacheable_prompt_blocks(self, request: ChatRequest) -> list[PromptBlock]:
        if request.prompt is None:
            return []
        return [
            *request.prompt.stable_blocks,
            *[block for block in request.prompt.runtime_blocks if block.cacheable],
        ]

    def supports_snapshot_cache_breakpoint(self, model: str | None = None) -> bool:
        _ = model
        return False

    def _message_payload(self, message: ChatMessage) -> dict[str, Any]:
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.role == "assistant" and message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.raw_arguments or json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        return payload

    def _tool_payload(self, tool: ToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_schema,
            },
        }

    async def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        body = await response.aread()
        detail = body.decode("utf-8", errors="replace")
        raise ProviderError(
            redact_secret(
                f"OpenAI 返回错误 HTTP {response.status_code}: {detail}",
                self.config.api_key,
            )
        )

    def _is_cache_option_unsupported(self, error: ProviderError) -> bool:
        text = str(error)
        if "HTTP 400" not in text and "HTTP 422" not in text:
            return False
        return "prompt_cache_key" in text or "prompt_cache_retention" in text

    def _parse_chunk(self, raw_data: str) -> dict[str, Any]:
        try:
            chunk = json.loads(raw_data)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"OpenAI 流式数据不是合法 JSON: {raw_data[:120]}") from exc
        if not isinstance(chunk, dict):
            raise ProviderError("OpenAI 流式数据结构无效")
        return chunk

    def _build_tool_calls(self, parts: dict[int, dict[str, Any]]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for index in sorted(parts):
            part = parts[index]
            raw_arguments = "".join(part["arguments_parts"])
            arguments: dict[str, Any] = {}
            parse_error: str | None = None
            if raw_arguments:
                try:
                    parsed = json.loads(raw_arguments)
                    if isinstance(parsed, dict):
                        arguments = parsed
                    else:
                        parse_error = "工具参数不是 JSON 对象"
                except json.JSONDecodeError as exc:
                    parse_error = f"工具参数 JSON 解析失败: {exc.msg}"
            calls.append(
                ToolCall(
                    id=part["id"] or f"tool-call-{index}",
                    name=part["name"],
                    arguments=arguments,
                    raw_arguments=raw_arguments,
                    parse_error=parse_error,
                )
            )
        return calls

    def _usage_from_chunk(self, chunk: dict[str, Any]) -> TokenUsage | None:
        usage = chunk.get("usage")
        if not isinstance(usage, dict):
            return None
        input_tokens = self._optional_int(usage.get("prompt_tokens"))
        output_tokens = self._optional_int(usage.get("completion_tokens"))
        total_tokens = self._optional_int(usage.get("total_tokens"))
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            provider="openai",
            cache=self._cache_usage_from_usage(usage),
            raw=dict(usage),
        )

    def _cache_usage_from_usage(self, usage: dict[str, Any]) -> PromptCacheUsage:
        details = usage.get("prompt_tokens_details")
        if not isinstance(details, dict) or "cached_tokens" not in details:
            return PromptCacheUsage(status="unknown")
        cached_tokens = self._optional_int(details.get("cached_tokens"))
        if cached_tokens is None:
            return PromptCacheUsage(status="unknown")
        status = "hit" if cached_tokens > 0 else "miss"
        return PromptCacheUsage(status=status, cached_tokens=cached_tokens)

    def _optional_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
