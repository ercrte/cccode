from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from mewcode.config import AppConfig
from mewcode.errors import ProviderError, redact_secret
from mewcode.prompting.base import PromptBlock
from mewcode.providers.base import ChatMessage, ChatRequest, PromptCacheUsage, StreamEvent, TokenUsage
from mewcode.providers.sse import iter_sse_lines
from mewcode.tools.base import ToolCall, ToolSpec


class AnthropicProvider:
    def __init__(self, config: AppConfig, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_blocks: dict[int, dict[str, Any]] = {}
        tool_calls: list[ToolCall] = []
        signature: str | None = None
        yield StreamEvent(type="message_start")

        client = self._client or httpx.AsyncClient(timeout=self.config.timeout_seconds, trust_env=False)
        should_close = self._client is None
        try:
            async with client.stream(
                "POST",
                f"{self.config.base_url}/messages",
                headers=self._headers(),
                json=self._payload(request),
            ) as response:
                await self._raise_for_status(response)
                async for event in iter_sse_lines(response):
                    payload = self._parse_event(event.data)
                    payload_type = payload.get("type")
                    usage = self._usage_from_payload(payload, payload_type)
                    if usage is not None:
                        yield StreamEvent(type="usage", usage=usage)
                    if event.event == "error" or payload_type == "error":
                        error = payload.get("error", {})
                        message = error.get("message") if isinstance(error, dict) else str(error)
                        raise ProviderError(redact_secret(f"Anthropic 流式错误: {message}", self.config.api_key))

                    if payload_type == "content_block_delta":
                        index = int(payload.get("index", 0))
                        delta = payload.get("delta") or {}
                        delta_type = delta.get("type")
                        if delta_type == "text_delta":
                            text = str(delta.get("text", ""))
                            if text:
                                text_parts.append(text)
                                yield StreamEvent(type="text_delta", text=text)
                        elif delta_type == "thinking_delta":
                            thinking = str(delta.get("thinking", ""))
                            if thinking:
                                thinking_parts.append(thinking)
                                yield StreamEvent(type="thinking_delta", text=thinking)
                        elif delta_type == "signature_delta":
                            signature = str(delta.get("signature", ""))
                        elif delta_type == "input_json_delta":
                            partial_json = str(delta.get("partial_json", ""))
                            if partial_json:
                                block = tool_blocks.setdefault(
                                    index,
                                    {"id": "", "name": "", "input_parts": [], "initial_input": {}},
                                )
                                block["input_parts"].append(partial_json)
                                yield StreamEvent(
                                    type="tool_call_delta",
                                    tool_call_id=block["id"] or None,
                                    tool_name=block["name"] or None,
                                    arguments_delta=partial_json,
                                )
                    elif payload_type == "content_block_start":
                        index = int(payload.get("index", 0))
                        content_block = payload.get("content_block") or {}
                        if content_block.get("type") == "tool_use":
                            tool_blocks[index] = {
                                "id": str(content_block.get("id", "")),
                                "name": str(content_block.get("name", "")),
                                "input_parts": [],
                                "initial_input": content_block.get("input") or {},
                            }
                    elif payload_type == "content_block_stop":
                        index = int(payload.get("index", 0))
                        block = tool_blocks.pop(index, None)
                        if block is not None:
                            tool_calls.append(self._build_tool_call(index, block))
                    elif payload_type == "message_stop":
                        break

            provider_payload = {"signature": signature} if signature else None
            message = ChatMessage(
                role="assistant",
                content="".join(text_parts),
                thinking="".join(thinking_parts) or None,
                tool_calls=tuple(tool_calls),
                provider_payload=provider_payload,
            )
            yield StreamEvent(type="message_done", message=message)
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError(redact_secret(f"Anthropic 请求失败: {exc}", self.config.api_key)) from exc
        finally:
            if should_close:
                await client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _payload(self, request: ChatRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": self._message_payloads(request.messages),
            "max_tokens": self.config.max_tokens,
            "stream": True,
        }
        if self.config.thinking is not None and self.config.thinking.enabled:
            thinking: dict[str, Any] = {
                "type": self.config.thinking.type,
                "display": self.config.thinking.display,
            }
            if self.config.thinking.budget_tokens is not None:
                thinking["budget_tokens"] = self.config.thinking.budget_tokens
            if self.config.thinking.effort is not None:
                thinking["effort"] = self.config.thinking.effort
            payload["thinking"] = thinking
        system = self._system_blocks(request)
        if system:
            payload["system"] = system
        if request.tools:
            payload["tools"] = [self._tool_payload(tool) for tool in request.tools]
        return payload

    def _system_blocks(self, request: ChatRequest) -> list[dict[str, Any]]:
        if request.prompt is None:
            return []

        cache_prefix_blocks = [
            {"type": "text", "text": self._prompt_block_text(block)}
            for block in self._cache_prefix_blocks(request)
            if block.text.strip()
        ]
        if (
            cache_prefix_blocks
            and self.config.prompt_cache.enabled
            and self.config.prompt_cache.anthropic_cache_control
        ):
            cache_prefix_blocks[-1]["cache_control"] = {"type": "ephemeral"}

        runtime_blocks = [
            {"type": "text", "text": self._prompt_block_text(block)}
            for block in self._dynamic_runtime_blocks(request)
            if block.text.strip()
        ]
        return [*cache_prefix_blocks, *runtime_blocks]

    def _cache_prefix_blocks(self, request: ChatRequest) -> tuple[PromptBlock, ...]:
        if request.prompt is None:
            return ()
        return (
            *request.prompt.stable_blocks,
            *tuple(block for block in request.prompt.runtime_blocks if block.cacheable),
        )

    def _dynamic_runtime_blocks(self, request: ChatRequest) -> tuple[PromptBlock, ...]:
        if request.prompt is None:
            return ()
        return tuple(block for block in request.prompt.runtime_blocks if not block.cacheable)

    def _prompt_block_text(self, block: PromptBlock) -> str:
        return f"## {block.title}\n{block.text.strip()}"

    def _message_payloads(self, messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "tool":
                tool_results.append(self._tool_result_block(message))
                continue
            if tool_results:
                payloads.append({"role": "user", "content": tool_results})
                tool_results = []
            payloads.append(self._message_payload(message))
        if tool_results:
            payloads.append({"role": "user", "content": tool_results})
        return payloads

    def _tool_result_block(self, message: ChatMessage) -> dict[str, Any]:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": message.tool_call_id or "",
            "content": message.content,
        }
        if message.tool_result_is_error:
            block["is_error"] = True
        return block

    def _message_payload(self, message: ChatMessage) -> dict[str, Any]:
        if message.role == "tool":
            return {"role": "user", "content": [self._tool_result_block(message)]}

        if message.role == "assistant" and (message.thinking or message.provider_payload):
            content: list[dict[str, Any]] = []
            signature = (message.provider_payload or {}).get("signature")
            thinking_block: dict[str, Any] = {
                "type": "thinking",
                "thinking": message.thinking or "",
            }
            if signature:
                thinking_block["signature"] = signature
            content.append(thinking_block)
            if message.content:
                content.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            return {"role": message.role, "content": content}

        if message.role == "assistant" and message.tool_calls:
            content = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            return {"role": message.role, "content": content}

        return {"role": message.role, "content": message.content}

    def _tool_payload(self, tool: ToolSpec) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters_schema,
        }

    async def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        body = await response.aread()
        detail = body.decode("utf-8", errors="replace")
        raise ProviderError(
            redact_secret(
                f"Anthropic 返回错误 HTTP {response.status_code}: {detail}",
                self.config.api_key,
            )
        )

    def _parse_event(self, raw_data: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Anthropic 流式数据不是合法 JSON: {raw_data[:120]}") from exc
        if not isinstance(payload, dict):
            raise ProviderError("Anthropic 流式数据结构无效")
        return payload

    def _build_tool_call(self, index: int, block: dict[str, Any]) -> ToolCall:
        raw_arguments = "".join(block["input_parts"])
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
        elif isinstance(block.get("initial_input"), dict):
            arguments = dict(block["initial_input"])
            raw_arguments = json.dumps(arguments, ensure_ascii=False)
        return ToolCall(
            id=block["id"] or f"tool-use-{index}",
            name=block["name"],
            arguments=arguments,
            raw_arguments=raw_arguments,
            parse_error=parse_error,
        )

    def _usage_from_payload(self, payload: dict[str, Any], payload_type: object) -> TokenUsage | None:
        usage: Any = None
        if payload_type == "message_start":
            message = payload.get("message")
            if isinstance(message, dict):
                usage = message.get("usage")
        elif payload_type == "message_delta":
            usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None

        input_tokens = self._anthropic_input_tokens(usage)
        output_tokens = self._optional_int(usage.get("output_tokens"))
        total_tokens = None
        if input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            provider="anthropic",
            cache=self._cache_usage_from_usage(usage),
            raw=dict(usage),
        )

    def _anthropic_input_tokens(self, usage: dict[str, Any]) -> int | None:
        values = [
            self._optional_int(usage.get("input_tokens")),
            self._optional_int(usage.get("cache_read_input_tokens")),
            self._optional_int(usage.get("cache_creation_input_tokens")),
        ]
        parts = [value for value in values if value is not None]
        if not parts:
            return None
        return sum(parts)

    def _cache_usage_from_usage(self, usage: dict[str, Any]) -> PromptCacheUsage:
        has_read = "cache_read_input_tokens" in usage
        has_creation = "cache_creation_input_tokens" in usage
        if not has_read and not has_creation:
            return PromptCacheUsage(status="unknown")

        read_tokens = self._optional_int(usage.get("cache_read_input_tokens")) if has_read else None
        creation_tokens = self._optional_int(usage.get("cache_creation_input_tokens")) if has_creation else None
        if (has_read and read_tokens is None) or (has_creation and creation_tokens is None):
            return PromptCacheUsage(status="unknown")

        safe_read = read_tokens or 0
        safe_creation = creation_tokens or 0
        if safe_read > 0:
            status = "hit"
        elif safe_creation > 0:
            status = "write"
        else:
            status = "miss"
        return PromptCacheUsage(
            status=status,
            read_input_tokens=read_tokens,
            creation_input_tokens=creation_tokens,
        )

    def _optional_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
