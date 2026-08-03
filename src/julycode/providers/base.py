from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from julycode.prompting.base import PromptBundle
from julycode.tools.base import ToolCall, ToolSpec

ChatRole = Literal["user", "assistant", "tool"]
StreamEventType = Literal[
    "message_start",
    "text_delta",
    "thinking_delta",
    "tool_call_delta",
    "usage",
    "message_done",
    "error",
]


@dataclass
class ChatMessage:
    role: ChatRole
    content: str = ""
    thinking: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_result_is_error: bool = False
    provider_payload: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChatRequest:
    messages: Sequence[ChatMessage]
    tools: Sequence[ToolSpec] = ()
    prompt: PromptBundle | None = None


CacheStatus = Literal["hit", "miss", "write", "unknown", "unsupported"]


@dataclass(frozen=True)
class PromptCacheUsage:
    status: CacheStatus
    read_input_tokens: int | None = None
    creation_input_tokens: int | None = None
    cached_tokens: int | None = None
    supported: bool = True


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    provider: str | None = None
    cache: PromptCacheUsage | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class StreamEvent:
    type: StreamEventType
    text: str = ""
    message: ChatMessage | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str = ""
    usage: TokenUsage | None = None
    error: str | None = None


class LLMProvider(Protocol):
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        ...
