from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mewcode.context.estimator import TokenEstimator
from mewcode.providers.base import ChatMessage


@dataclass(frozen=True)
class ConversationSegment:
    messages: tuple[ChatMessage, ...]
    estimated_tokens: int


class ConversationSegmenter:
    def __init__(self, estimator: TokenEstimator) -> None:
        self.estimator = estimator

    def split(self, messages: Sequence[ChatMessage]) -> tuple[ConversationSegment, ...]:
        segments: list[ConversationSegment] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            group = [message]
            if message.role == "assistant" and message.tool_calls:
                call_ids = {call.id for call in message.tool_calls}
                cursor = index + 1
                while cursor < len(messages):
                    candidate = messages[cursor]
                    if candidate.role != "tool" or candidate.tool_call_id not in call_ids:
                        break
                    group.append(candidate)
                    cursor += 1
                index = cursor
            else:
                index += 1
            segments.append(
                ConversationSegment(
                    messages=tuple(group),
                    estimated_tokens=sum(self.estimator.estimate_message(item) for item in group),
                )
            )
        return tuple(segments)

    def select_recent(
        self,
        segments: Sequence[ConversationSegment],
        *,
        target_tokens: int,
        min_messages: int,
    ) -> tuple[tuple[ConversationSegment, ...], tuple[ConversationSegment, ...]]:
        recent_reversed: list[ConversationSegment] = []
        recent_tokens = 0
        recent_messages = 0
        for segment in reversed(segments):
            should_keep = recent_tokens < target_tokens or recent_messages < min_messages
            if not should_keep:
                break
            recent_reversed.append(segment)
            recent_tokens += segment.estimated_tokens
            recent_messages += len(segment.messages)
        recent = tuple(reversed(recent_reversed))
        summarized_count = len(segments) - len(recent)
        return tuple(segments[:summarized_count]), recent
