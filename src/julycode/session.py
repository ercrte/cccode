from __future__ import annotations

from dataclasses import dataclass, field

from collections.abc import Sequence
from typing import Protocol

from julycode.context.models import ContextState, ContextSummary
from julycode.prompting.base import PromptBundle
from julycode.providers.base import ChatMessage, ChatRequest
from julycode.tools.base import ToolResult, ToolSpec


@dataclass(frozen=True)
class PendingPlan:
    source_request: str
    plan_text: str


class PersistentSessionRecorder(Protocol):
    def append_message(self, message: ChatMessage) -> None:
        ...

    def append_checkpoint(self, messages: Sequence[ChatMessage], summary: ContextSummary | None) -> None:
        ...


@dataclass
class ChatSession:
    messages: list[ChatMessage] = field(default_factory=list)
    pending_plan: PendingPlan | None = None
    context_state: ContextState = field(default_factory=ContextState)
    recorder: PersistentSessionRecorder | None = None

    def append_user_message(self, text: str, *, metadata: dict[str, object] | None = None) -> ChatMessage:
        message = ChatMessage(role="user", content=text, metadata=dict(metadata) if metadata is not None else None)
        self.messages.append(message)
        self._record_message(message)
        return message

    def append_assistant_message(self, message: ChatMessage) -> None:
        if message.role != "assistant":
            raise ValueError("只能追加 assistant 消息")
        self.messages.append(message)
        self._record_message(message)

    def append_tool_result(self, result: ToolResult) -> ChatMessage:
        message = ChatMessage(
            role="tool",
            content=result.to_model_content(),
            tool_call_id=result.tool_call_id,
            tool_result_is_error=not result.success,
        )
        self.messages.append(message)
        self._record_message(message)
        return message

    def build_request(self, tools: Sequence[ToolSpec] = (), prompt: PromptBundle | None = None) -> ChatRequest:
        return ChatRequest(messages=tuple(self.messages), tools=tuple(tools), prompt=prompt)

    def replace_messages(self, messages: Sequence[ChatMessage]) -> None:
        self.messages = list(messages)

    def set_context_summary(self, summary: ContextSummary) -> None:
        self.context_state.summary = summary

    def set_recorder(self, recorder: PersistentSessionRecorder | None) -> None:
        self.recorder = recorder

    def append_checkpoint(self) -> None:
        if self.recorder is not None:
            self.recorder.append_checkpoint(tuple(self.messages), self.context_state.summary)

    def save_pending_plan(self, plan: PendingPlan) -> None:
        self.pending_plan = plan

    def clear_pending_plan(self) -> None:
        self.pending_plan = None

    def _record_message(self, message: ChatMessage) -> None:
        if self.recorder is not None:
            self.recorder.append_message(message)
