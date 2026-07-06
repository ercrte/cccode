from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

from memory_quality.models import ExpectedMemory, ExtractionCase, InheritanceCase
from mewcode.providers.base import ChatMessage, ChatRequest, LLMProvider, StreamEvent


class ScriptedMemoryQualityProvider(LLMProvider):
    """只验证专项评测流程的确定性 Provider。"""

    def __init__(
        self,
        *,
        extraction_case: ExtractionCase | None = None,
        inheritance_case: InheritanceCase | None = None,
    ) -> None:
        self.extraction_case = extraction_case
        self.inheritance_case = inheritance_case
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        content = self._response(request)
        yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=content))

    def _response(self, request: ChatRequest) -> str:
        last = request.messages[-1].content if request.messages else ""
        if "你正在为 MewCode 提取可跨会话使用的长期记忆" in last:
            expected = self._expected_for_extraction()
            return _operations(expected)
        if self.inheritance_case is None:
            return "离线提取完成。"
        if last == self.inheritance_case.source_prompt:
            return "已确认并长期记住这些约定。"
        if last == self.inheritance_case.target_prompt:
            runtime = _runtime_text(request)
            has_user = "scope=user" in runtime
            has_project = "scope=project" in runtime
            has_terms = all(
                any(term.casefold() in runtime.casefold() for term in group)
                for group in self.inheritance_case.expectation.required_term_groups
            )
            if has_user and has_project and has_terms:
                return "；".join(group[0] for group in self.inheritance_case.expectation.required_term_groups)
            return f"{self.inheritance_case.expectation.restatement_terms[0]}既定项目背景。"
        return "离线场景完成。"

    def _expected_for_extraction(self) -> Sequence[ExpectedMemory]:
        if self.extraction_case is not None:
            return self.extraction_case.expected
        if self.inheritance_case is not None:
            return self.inheritance_case.source_expected
        return ()


def _operations(expected: Sequence[ExpectedMemory]) -> str:
    if not expected:
        return json.dumps({"operations": [{"action": "skip"}]}, ensure_ascii=False)
    operations = []
    for item in expected:
        body = "；".join(group[0] for group in item.content_term_groups)
        operations.append(
            {
                "action": "create",
                "scope": item.scope,
                "category": item.category,
                "note_id": item.key,
                "title": item.key,
                "body": body,
                "evidence": list(item.evidence),
                "durability": "persistent",
                "critical": item.critical,
                "confidence": 0.99,
                "tags": ["offline-fixture"],
                "supersedes": [],
            }
        )
    return json.dumps({"operations": operations}, ensure_ascii=False)


def _runtime_text(request: ChatRequest) -> str:
    if request.prompt is None:
        return ""
    return "\n".join(block.text for block in request.prompt.runtime_blocks)

