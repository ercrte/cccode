from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from mewcode.context.models import SummaryError
from mewcode.context.summarizer import HistorySummarizer
from mewcode.errors import ProviderError
from mewcode.providers.base import ChatMessage, ChatRequest, StreamEvent
from mewcode.tools.base import ToolCall


class FakeProvider:
    def __init__(self, events: list[StreamEvent | BaseException]) -> None:
        self.events = events
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        for event in self.events:
            await asyncio.sleep(0)
            if isinstance(event, BaseException):
                raise event
            yield event


def final_message(text: str) -> StreamEvent:
    return StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=text))


@pytest.mark.asyncio
async def test_summarizer_requests_without_tools() -> None:
    provider = FakeProvider([final_message("<analysis_draft>草稿</analysis_draft><final_summary>正式</final_summary>")])

    await HistorySummarizer().summarize(provider=provider, previous_summary=None, messages=[], external_paths=[])

    assert provider.requests[0].tools == ()


@pytest.mark.asyncio
async def test_summarizer_prompt_requires_draft_and_final_summary() -> None:
    provider = FakeProvider([final_message("<analysis_draft>草稿</analysis_draft><final_summary>正式</final_summary>")])

    await HistorySummarizer().summarize(
        provider=provider,
        previous_summary=None,
        messages=[ChatMessage(role="user", content="需求")],
        external_paths=[".mewcode/context/a.json"],
    )

    prompt = provider.requests[0].messages[0].content
    assert "禁止调用任何工具" in prompt
    assert "<analysis_draft>" in prompt
    assert "<final_summary>" in prompt
    assert "当前目标和约束" in prompt


@pytest.mark.asyncio
async def test_summarizer_keeps_only_final_summary() -> None:
    provider = FakeProvider([final_message("<analysis_draft>草稿不要保存</analysis_draft><final_summary>正式摘要</final_summary>")])

    summary = await HistorySummarizer().summarize(provider=provider, previous_summary=None, messages=[], external_paths=[])

    assert summary.content == "正式摘要"
    assert "草稿不要保存" not in summary.content
    assert "不得凭摘要" in summary.boundary_notice


@pytest.mark.asyncio
async def test_summarizer_fails_on_provider_error() -> None:
    provider = FakeProvider([ProviderError("bad")])

    with pytest.raises(SummaryError):
        await HistorySummarizer().summarize(provider=provider, previous_summary=None, messages=[], external_paths=[])


@pytest.mark.asyncio
async def test_summarizer_fails_if_model_requests_tool() -> None:
    provider = FakeProvider(
        [
            StreamEvent(
                type="message_done",
                message=ChatMessage(role="assistant", tool_calls=(ToolCall("c1", "read_file"),)),
            )
        ]
    )

    with pytest.raises(SummaryError, match="工具"):
        await HistorySummarizer().summarize(provider=provider, previous_summary=None, messages=[], external_paths=[])


@pytest.mark.asyncio
async def test_summarizer_fails_without_final_summary() -> None:
    provider = FakeProvider([final_message("<analysis_draft>草稿</analysis_draft>")])

    with pytest.raises(SummaryError, match="正式摘要"):
        await HistorySummarizer().summarize(provider=provider, previous_summary=None, messages=[], external_paths=[])
