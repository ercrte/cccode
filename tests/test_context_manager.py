from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mewcode.context.manager import ContextManager
from mewcode.context.models import ContextConfig, ContextLimitError, RequestFootprint, TokenAnchor
from mewcode.context.segmenter import ConversationSegmenter
from mewcode.context.estimator import TokenEstimator
from mewcode.prompting.base import PromptBlock, PromptBundle
from mewcode.providers.base import ChatMessage, ChatRequest, StreamEvent
from mewcode.session import ChatSession
from mewcode.tools.base import ToolCall


class FakeProvider:
    def __init__(self, text: str | None = None, *, fail: bool = False) -> None:
        self.text = text or "<analysis_draft>草稿</analysis_draft><final_summary>正式摘要</final_summary>"
        self.fail = fail
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        await asyncio.sleep(0)
        if self.fail:
            yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="no final"))
            return
        yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=self.text))


def prompt_factory(session: ChatSession) -> PromptBundle:
    text = "runtime"
    if session.context_state.summary is not None:
        text += f"\n<mewcode_context_summary>{session.context_state.summary.content}</mewcode_context_summary>"
    return PromptBundle(
        stable_blocks=(PromptBlock("identity", "身份", "stable", stable=True),),
        runtime_blocks=(PromptBlock("runtime_context", "运行时补充", text, stable=False),),
    )


def manager(tmp_path: Path, config: ContextConfig) -> ContextManager:
    return ContextManager(config, tmp_path, max_output_tokens=10)


def message(text: str, role: str = "user") -> ChatMessage:
    return ChatMessage(role=role, content=text)  # type: ignore[arg-type]


def test_segmenter_keeps_tool_call_and_results_together() -> None:
    estimator = TokenEstimator(ContextConfig())
    segmenter = ConversationSegmenter(estimator)
    messages = [
        message("u"),
        ChatMessage(role="assistant", tool_calls=(ToolCall("a", "read"), ToolCall("b", "search"))),
        ChatMessage(role="tool", content="ra", tool_call_id="a"),
        ChatMessage(role="tool", content="rb", tool_call_id="b"),
    ]

    segments = segmenter.split(messages)

    assert len(segments) == 2
    assert [item.role for item in segments[1].messages] == ["assistant", "tool", "tool"]


def test_segmenter_selects_recent_by_token_budget() -> None:
    segmenter = ConversationSegmenter(TokenEstimator(ContextConfig(chars_per_token=1.0)))
    segments = segmenter.split([message("old" * 100), message("new")])

    summarized, recent = segmenter.select_recent(segments, target_tokens=10, min_messages=1)

    assert tuple(item.content for segment in summarized for item in segment.messages) == ("old" * 100,)
    assert tuple(item.content for segment in recent for item in segment.messages) == ("new",)


def test_segmenter_keeps_minimum_recent_messages() -> None:
    segmenter = ConversationSegmenter(TokenEstimator(ContextConfig(chars_per_token=1.0)))
    segments = segmenter.split([message(str(index) * 50) for index in range(6)])

    _, recent = segmenter.select_recent(segments, target_tokens=1, min_messages=5)

    assert sum(len(segment.messages) for segment in recent) == 5


@pytest.mark.asyncio
async def test_prepare_request_runs_light_compaction_before_building_request(tmp_path: Path) -> None:
    config = ContextConfig(single_tool_result_tokens=5, window_tokens=100_000)
    session = ChatSession(messages=[ChatMessage(role="tool", content="x" * 200, tool_call_id="call-1")])

    prepared = await manager(tmp_path, config).prepare_request(
        session=session,
        provider=FakeProvider(),
        tools=(),
        prompt_factory=lambda: prompt_factory(session),
    )

    assert ".mewcode/context" in prepared.request.messages[0].content
    assert prepared.report is not None
    assert prepared.report.light_compacted is True


@pytest.mark.asyncio
async def test_prepare_request_can_skip_when_context_disabled(tmp_path: Path) -> None:
    config = ContextConfig(enabled=False, single_tool_result_tokens=1)
    session = ChatSession(messages=[ChatMessage(role="tool", content="x" * 200, tool_call_id="call-1")])

    prepared = await manager(tmp_path, config).prepare_request(
        session=session,
        provider=FakeProvider(),
        tools=(),
        prompt_factory=lambda: prompt_factory(session),
    )

    assert prepared.request.messages[0].content == "x" * 200


@pytest.mark.asyncio
async def test_prepare_request_reports_externalized_paths(tmp_path: Path) -> None:
    session = ChatSession(messages=[ChatMessage(role="tool", content="x" * 200, tool_call_id="call-1")])

    prepared = await manager(tmp_path, ContextConfig(single_tool_result_tokens=5)).prepare_request(
        session=session,
        provider=FakeProvider(),
        tools=(),
        prompt_factory=lambda: prompt_factory(session),
    )

    assert prepared.report is not None
    assert prepared.report.externalized_paths


@pytest.mark.asyncio
async def test_auto_heavy_compaction_triggers_before_safety_margin(tmp_path: Path) -> None:
    config = ContextConfig(
        window_tokens=100,
        auto_reserve_tokens=20,
        recent_tokens=1,
        min_recent_messages=1,
        chars_per_token=8.0,
    )
    session = ChatSession(messages=[message("old" * 50), message("new")])

    prepared = await manager(tmp_path, config).prepare_request(
        session=session,
        provider=FakeProvider(),
        tools=(),
        prompt_factory=lambda: prompt_factory(session),
    )

    assert prepared.report is not None
    assert prepared.report.heavy_compacted is True


@pytest.mark.asyncio
async def test_heavy_compaction_replaces_old_messages_with_recent_messages(tmp_path: Path) -> None:
    config = ContextConfig(
        window_tokens=100,
        auto_reserve_tokens=20,
        recent_tokens=1,
        min_recent_messages=1,
        chars_per_token=8.0,
    )
    session = ChatSession(messages=[message("old" * 50), message("new")])

    await manager(tmp_path, config).prepare_request(
        session=session,
        provider=FakeProvider(),
        tools=(),
        prompt_factory=lambda: prompt_factory(session),
    )

    assert [item.content for item in session.messages] == ["new"]
    assert session.context_state.summary is not None


@pytest.mark.asyncio
async def test_prepare_request_rebuilds_prompt_after_summary(tmp_path: Path) -> None:
    config = ContextConfig(
        window_tokens=100,
        auto_reserve_tokens=20,
        recent_tokens=1,
        min_recent_messages=1,
        chars_per_token=8.0,
    )
    session = ChatSession(messages=[message("old" * 50), message("new")])

    prepared = await manager(tmp_path, config).prepare_request(
        session=session,
        provider=FakeProvider(),
        tools=(),
        prompt_factory=lambda: prompt_factory(session),
    )

    assert "正式摘要" in prepared.request.prompt.runtime_blocks[-1].text  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_manual_compact_uses_manual_safety_margin(tmp_path: Path) -> None:
    config = ContextConfig(window_tokens=120, manual_reserve_tokens=3_000, recent_tokens=1, min_recent_messages=1)
    session = ChatSession(messages=[message("old" * 50), message("new")])

    report = await manager(tmp_path, config).manual_compact(session=session, provider=FakeProvider())

    assert report.mode == "manual"
    assert report.heavy_compacted is True


@pytest.mark.asyncio
async def test_manual_compact_returns_noop_for_short_history(tmp_path: Path) -> None:
    session = ChatSession(messages=[message("only")])

    report = await manager(tmp_path, ContextConfig()).manual_compact(session=session, provider=FakeProvider())

    assert report.heavy_compacted is False
    assert "无需" in report.message


@pytest.mark.asyncio
async def test_manual_compact_report_contains_counts_and_paths(tmp_path: Path) -> None:
    config = ContextConfig(single_tool_result_tokens=5, recent_tokens=1, min_recent_messages=1)
    session = ChatSession(messages=[ChatMessage(role="tool", content="x" * 200, tool_call_id="call-1"), message("new")])

    report = await manager(tmp_path, config).manual_compact(session=session, provider=FakeProvider())

    assert report.kept_message_count >= 1
    assert report.estimated_tokens_before > 0
    assert report.externalized_paths


@pytest.mark.asyncio
async def test_summary_failure_count_increments(tmp_path: Path) -> None:
    config = ContextConfig(
        window_tokens=100,
        auto_reserve_tokens=20,
        recent_tokens=1,
        min_recent_messages=1,
        chars_per_token=8.0,
    )
    session = ChatSession(messages=[message("old" * 50), message("new")])

    with pytest.raises(ContextLimitError):
        await manager(tmp_path, config).prepare_request(
            session=session,
            provider=FakeProvider(fail=True),
            tools=(),
            prompt_factory=lambda: prompt_factory(session),
        )

    assert session.context_state.consecutive_summary_failures == 1


@pytest.mark.asyncio
async def test_summary_failure_limit_raises_context_limit(tmp_path: Path) -> None:
    config = ContextConfig(
        window_tokens=100,
        auto_reserve_tokens=20,
        recent_tokens=1,
        min_recent_messages=1,
        chars_per_token=8.0,
    )
    session = ChatSession(messages=[message("old" * 50), message("new")])
    session.context_state.consecutive_summary_failures = 2

    with pytest.raises(ContextLimitError, match="连续失败"):
        await manager(tmp_path, config).prepare_request(
            session=session,
            provider=FakeProvider(fail=True),
            tools=(),
            prompt_factory=lambda: prompt_factory(session),
        )


@pytest.mark.asyncio
async def test_successful_summary_resets_failure_count(tmp_path: Path) -> None:
    config = ContextConfig(
        window_tokens=100,
        auto_reserve_tokens=20,
        recent_tokens=1,
        min_recent_messages=1,
        chars_per_token=8.0,
    )
    session = ChatSession(messages=[message("old" * 50), message("new")])
    session.context_state.consecutive_summary_failures = 2

    await manager(tmp_path, config).prepare_request(
        session=session,
        provider=FakeProvider(),
        tools=(),
        prompt_factory=lambda: prompt_factory(session),
    )

    assert session.context_state.consecutive_summary_failures == 0


@pytest.mark.asyncio
async def test_context_limit_raised_when_request_still_over_budget(tmp_path: Path) -> None:
    config = ContextConfig(window_tokens=1, auto_reserve_tokens=1, recent_tokens=1, min_recent_messages=1)
    session = ChatSession(messages=[message("old" * 50), message("new")])

    with pytest.raises(ContextLimitError):
        await manager(tmp_path, config).prepare_request(
            session=session,
            provider=FakeProvider(),
            tools=(),
            prompt_factory=lambda: prompt_factory(session),
        )


def test_usage_anchor_can_be_recorded_on_session(tmp_path: Path) -> None:
    mgr = manager(tmp_path, ContextConfig())
    session = ChatSession()
    mgr._last_session = session

    mgr.record_usage(type("Usage", (), {"input_tokens": 42})(), RequestFootprint(chars=100, estimated_tokens=25))

    assert session.context_state.token_anchor == TokenAnchor(input_tokens=42, footprint_chars=100)


@pytest.mark.asyncio
async def test_heavy_compaction_appends_session_checkpoint(tmp_path: Path) -> None:
    config = ContextConfig(
        window_tokens=100,
        auto_reserve_tokens=20,
        recent_tokens=1,
        min_recent_messages=1,
        chars_per_token=8.0,
    )
    session = ChatSession(messages=[message("old" * 50), message("new")])
    recorder = FakeRecorder()
    session.set_recorder(recorder)

    await manager(tmp_path, config).prepare_request(
        session=session,
        provider=FakeProvider(),
        tools=(),
        prompt_factory=lambda: prompt_factory(session),
    )

    assert len(recorder.checkpoints) == 1


@pytest.mark.asyncio
async def test_manual_compact_appends_session_checkpoint(tmp_path: Path) -> None:
    config = ContextConfig(recent_tokens=1, min_recent_messages=1)
    session = ChatSession(messages=[message("old" * 50), message("new")])
    recorder = FakeRecorder()
    session.set_recorder(recorder)

    await manager(tmp_path, config).manual_compact(session=session, provider=FakeProvider())

    assert len(recorder.checkpoints) == 1


class FakeRecorder:
    def __init__(self) -> None:
        self.checkpoints = []

    def append_message(self, message) -> None:
        _ = message

    def append_checkpoint(self, messages, summary) -> None:
        self.checkpoints.append((tuple(messages), summary))
