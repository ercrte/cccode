from __future__ import annotations

import json
import re
from collections.abc import Sequence

from julycode.context.models import ContextSummary, SummaryError
from julycode.providers.base import ChatMessage, ChatRequest, LLMProvider, StreamEvent

SUMMARY_BOUNDARY_NOTICE = (
    "上下文已压缩：摘要和工具预览不是完整事实来源。需要文件、代码或工具结果细节时，"
    "必须重新读取对应路径，不得凭摘要或预览脑补代码细节。"
)


class HistorySummarizer:
    async def summarize(
        self,
        *,
        provider: LLMProvider,
        previous_summary: ContextSummary | None,
        messages: Sequence[ChatMessage],
        external_paths: Sequence[str],
        kept_message_count: int = 0,
    ) -> ContextSummary:
        request = ChatRequest(messages=[self._summary_message(previous_summary, messages, external_paths)], tools=())
        text_parts: list[str] = []
        final_message: ChatMessage | None = None
        try:
            async for event in provider.stream_chat(request):
                if event.type == "text_delta":
                    text_parts.append(event.text)
                elif event.type == "message_done":
                    final_message = event.message
                elif event.type == "error":
                    raise SummaryError(event.error or "摘要请求失败")
        except SummaryError:
            raise
        except Exception as exc:
            raise SummaryError(f"摘要请求失败: {exc}") from exc

        if final_message is not None and final_message.tool_calls:
            raise SummaryError("摘要请求中模型尝试调用工具")
        raw_text = (final_message.content if final_message is not None else "") or "".join(text_parts)
        final_summary = self._extract_final_summary(raw_text)
        if not final_summary:
            raise SummaryError("摘要结果缺少正式摘要")
        return ContextSummary(
            content=final_summary,
            boundary_notice=SUMMARY_BOUNDARY_NOTICE,
            created_at="runtime",
            source_message_count=len(messages),
            kept_message_count=kept_message_count,
            external_paths=tuple(external_paths),
        )

    def _summary_message(
        self,
        previous_summary: ContextSummary | None,
        messages: Sequence[ChatMessage],
        external_paths: Sequence[str],
    ) -> ChatMessage:
        payload = {
            "previous_summary": previous_summary.content if previous_summary is not None else "",
            "external_paths": list(external_paths),
            "messages": [self._message_payload(message) for message in messages],
        }
        prompt = (
            "你正在为 JulyCode 压缩较早的会话历史。禁止调用任何工具，只能根据本请求内容写摘要。\n"
            "先写 <analysis_draft>，用于梳理重点；再写 <final_summary>，作为唯一会保留的正式摘要。\n"
            "正式摘要必须包含固定部分：当前目标和约束、用户明确要求、已完成工作和关键决策、"
            "重要文件或工具结果索引、待办事项和阻塞、验证状态与风险。\n"
            "不要把草稿内容写进正式摘要之外的历史。\n\n"
            f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
        )
        return ChatMessage(role="user", content=prompt)

    def _message_payload(self, message: ChatMessage) -> dict[str, object]:
        return {
            "role": message.role,
            "content": message.content,
            "thinking": message.thinking,
            "tool_call_id": message.tool_call_id,
            "tool_result_is_error": message.tool_result_is_error,
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "raw_arguments": call.raw_arguments,
                    "parse_error": call.parse_error,
                }
                for call in message.tool_calls
            ],
        }

    def _extract_final_summary(self, text: str) -> str:
        match = re.search(r"<final_summary>(.*?)</final_summary>", text, flags=re.DOTALL)
        if match is None:
            return ""
        return match.group(1).strip()
