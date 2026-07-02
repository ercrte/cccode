from __future__ import annotations

import json
from collections.abc import Sequence

from mewcode.context.estimator import TokenEstimator
from mewcode.context.models import ContextConfig, ContextExternalRef, ToolCompactionResult
from mewcode.context.store import ContextStore
from mewcode.providers.base import ChatMessage
from mewcode.session import ChatSession


class ToolResultCompactor:
    def __init__(
        self,
        config: ContextConfig,
        estimator: TokenEstimator,
        store: ContextStore,
    ) -> None:
        self.config = config
        self.estimator = estimator
        self.store = store

    def compact(self, session: ChatSession) -> ToolCompactionResult:
        external_refs: list[ContextExternalRef] = []
        changed = False

        for message in session.messages:
            if not self._can_externalize(message):
                continue
            estimated_tokens = self.estimator.estimate_message(message)
            if estimated_tokens <= self.config.single_tool_result_tokens:
                continue
            ref = self._externalize(session, message, estimated_tokens)
            external_refs.append(ref)
            changed = True

        for group in self._tool_result_groups(session.messages):
            candidates = [message for message in group if self._can_externalize(message)]
            total = sum(self.estimator.estimate_message(message) for message in candidates)
            if total <= self.config.turn_tool_result_tokens:
                continue
            ordered = sorted(candidates, key=self.estimator.estimate_message, reverse=True)
            for message in ordered:
                if total <= self.config.turn_tool_result_tokens:
                    break
                estimated_tokens = self.estimator.estimate_message(message)
                ref = self._externalize(session, message, estimated_tokens)
                external_refs.append(ref)
                changed = True
                total -= estimated_tokens

        if external_refs:
            session.context_state.compacted_tool_paths = tuple(
                [*session.context_state.compacted_tool_paths, *(ref.path for ref in external_refs)]
            )
        return ToolCompactionResult(changed=changed, external_refs=tuple(external_refs))

    def _externalize(self, session: ChatSession, message: ChatMessage, estimated_tokens: int) -> ContextExternalRef:
        ref = self.store.write_tool_result(
            session_id=session.context_state.session_id,
            message=message,
            estimated_tokens=estimated_tokens,
        )
        payload = {
            "mewcode_externalized": True,
            "notice": "完整工具结果已外置保存；如需完整细节，请使用 read_file 读取 external_path，不要凭预览猜测。",
            "external_path": ref.path,
            "tool_call_id": message.tool_call_id,
            "tool_result_is_error": message.tool_result_is_error,
            "original_chars": ref.original_chars,
            "estimated_tokens": ref.estimated_tokens,
            "preview": ref.preview,
        }
        message.content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return ref

    def _can_externalize(self, message: ChatMessage) -> bool:
        return message.role == "tool" and not self._already_externalized(message)

    def _already_externalized(self, message: ChatMessage) -> bool:
        if message.role != "tool":
            return False
        try:
            parsed = json.loads(message.content)
        except json.JSONDecodeError:
            return False
        return isinstance(parsed, dict) and parsed.get("mewcode_externalized") is True

    def _tool_result_groups(self, messages: Sequence[ChatMessage]) -> list[list[ChatMessage]]:
        groups: list[list[ChatMessage]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            if message.role != "assistant" or not message.tool_calls:
                index += 1
                continue
            call_ids = {call.id for call in message.tool_calls}
            group: list[ChatMessage] = []
            cursor = index + 1
            while cursor < len(messages):
                candidate = messages[cursor]
                if candidate.role != "tool" or candidate.tool_call_id not in call_ids:
                    break
                group.append(candidate)
                cursor += 1
            if group:
                groups.append(group)
            index = max(cursor, index + 1)
        return groups
