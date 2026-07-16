from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from typing import Any

from mewcode.context.models import ContextConfig, RequestFootprint, TokenAnchor
from mewcode.prompting.base import GeneratedContextBlock, PromptBundle
from mewcode.providers.base import ChatMessage
from mewcode.tools.base import ToolSpec


class TokenEstimator:
    def __init__(self, config: ContextConfig | None = None) -> None:
        self.config = config or ContextConfig()

    def request_footprint(
        self,
        messages: list[ChatMessage] | tuple[ChatMessage, ...],
        tools: list[ToolSpec] | tuple[ToolSpec, ...],
        prompt: PromptBundle | None,
    ) -> RequestFootprint:
        payload = {
            "messages": [self._message_payload(message) for message in messages],
            "tools": [self._tool_payload(tool) for tool in tools],
            "prompt": self._prompt_payload(prompt),
        }
        chars = len(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        return RequestFootprint(chars=chars, estimated_tokens=self._chars_to_tokens(chars))

    def estimate_from_anchor(self, footprint: RequestFootprint, anchor: TokenAnchor | None) -> int:
        if anchor is None:
            return footprint.estimated_tokens
        delta_chars = footprint.chars - anchor.footprint_chars
        estimated = anchor.input_tokens + math.ceil(delta_chars / self.config.chars_per_token)
        return max(1, estimated)

    def estimate_message(self, message: ChatMessage) -> int:
        chars = len(json.dumps(self._message_payload(message), ensure_ascii=False, sort_keys=True, default=str))
        return self._chars_to_tokens(chars)

    def estimate_text(self, text: str) -> int:
        return self._chars_to_tokens(len(text))

    def estimate_generated_context(self, block: GeneratedContextBlock) -> int:
        chars = len(json.dumps(self._dataclass_payload(block), ensure_ascii=False, sort_keys=True, default=str))
        return self._chars_to_tokens(chars)

    def _chars_to_tokens(self, chars: int) -> int:
        return max(1, math.ceil(chars / self.config.chars_per_token))

    def _prompt_payload(self, prompt: PromptBundle | None) -> Any:
        if prompt is None:
            return None
        return {
            "stable_blocks": [self._dataclass_payload(block) for block in prompt.stable_blocks],
            "generated_context_blocks": [
                self._dataclass_payload(block) for block in prompt.generated_context_blocks
            ],
            "runtime_blocks": [self._dataclass_payload(block) for block in prompt.runtime_blocks],
        }

    def _tool_payload(self, tool: ToolSpec) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "parameters_schema": tool.parameters_schema,
            "timeout_seconds": tool.timeout_seconds,
            "safety": tool.safety,
        }

    def _message_payload(self, message: ChatMessage) -> dict[str, Any]:
        return {
            "role": message.role,
            "content": message.content,
            "thinking": message.thinking,
            "tool_calls": [self._dataclass_payload(call) for call in message.tool_calls],
            "tool_call_id": message.tool_call_id,
            "tool_result_is_error": message.tool_result_is_error,
            "provider_payload": message.provider_payload,
        }

    def _dataclass_payload(self, value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)
        return value
