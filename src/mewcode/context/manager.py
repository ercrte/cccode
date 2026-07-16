from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path

from mewcode.context.compactor import ToolResultCompactor
from mewcode.context.estimator import TokenEstimator
from mewcode.context.models import (
    ContextCompactionReport,
    ContextConfig,
    ContextLimitError,
    PreparedChatRequest,
    RequestFootprint,
    SummaryError,
    TokenAnchor,
)
from mewcode.context.segmenter import ConversationSegment, ConversationSegmenter
from mewcode.context.store import ContextStore
from mewcode.context.summarizer import HistorySummarizer
from mewcode.prompting.base import GeneratedContextBlock, PromptBundle
from mewcode.providers.base import LLMProvider, TokenUsage
from mewcode.session import ChatSession
from mewcode.tools.base import ToolSpec


OptionalContextFactory = Callable[[int], Awaitable[tuple[GeneratedContextBlock, ...]]]


class ContextManager:
    def __init__(
        self,
        config: ContextConfig,
        cwd: Path,
        max_output_tokens: int,
        estimator: TokenEstimator | None = None,
        store: ContextStore | None = None,
        compactor: ToolResultCompactor | None = None,
        segmenter: ConversationSegmenter | None = None,
        summarizer: HistorySummarizer | None = None,
    ) -> None:
        self.config = config
        self.cwd = cwd.resolve()
        self.max_output_tokens = max_output_tokens
        self.estimator = estimator or TokenEstimator(config)
        self.store = store or ContextStore(self.cwd, config)
        self.compactor = compactor or ToolResultCompactor(config, self.estimator, self.store)
        self.segmenter = segmenter or ConversationSegmenter(self.estimator)
        self.summarizer = summarizer or HistorySummarizer()

    async def prepare_request(
        self,
        *,
        session: ChatSession,
        provider: LLMProvider,
        tools: Sequence[ToolSpec],
        prompt_factory: Callable[[], PromptBundle],
        mode: str = "auto",
        optional_context_factory: OptionalContextFactory | None = None,
        optional_context_max_tokens: int = 0,
    ) -> PreparedChatRequest:
        self._last_session = session
        reserve_tokens = self.config.manual_reserve_tokens if mode == "manual" else self.config.auto_reserve_tokens
        budget = self._input_budget(reserve_tokens)
        if not self.config.enabled:
            prompt = prompt_factory()
            footprint = self._anchored_footprint(session, tools, prompt)
            return await self._with_optional_context(
                session=session,
                tools=tools,
                prompt=prompt,
                report=None,
                footprint=footprint,
                budget=budget,
                factory=optional_context_factory,
                max_tokens=optional_context_max_tokens,
            )

        before_prompt = prompt_factory()
        before_footprint = self._anchored_footprint(session, tools, before_prompt)
        tool_result = self.compactor.compact(session)
        prompt = prompt_factory()
        footprint = self._anchored_footprint(session, tools, prompt)
        report = ContextCompactionReport(
            mode="manual" if mode == "manual" else "auto",
            light_compacted=tool_result.changed,
            heavy_compacted=False,
            externalized_paths=tuple(ref.path for ref in tool_result.external_refs),
            kept_message_count=len(session.messages),
            summarized_message_count=0,
            estimated_tokens_before=before_footprint.estimated_tokens,
            estimated_tokens_after=footprint.estimated_tokens,
            message="已执行轻量上下文检查。" if tool_result.changed else "",
        )

        if footprint.estimated_tokens <= budget:
            return await self._with_optional_context(
                session=session,
                tools=tools,
                prompt=prompt,
                report=report if tool_result.changed else None,
                footprint=footprint,
                budget=budget,
                factory=optional_context_factory,
                max_tokens=optional_context_max_tokens,
            )

        try:
            heavy_report = await self._heavy_compact(
                session=session,
                provider=provider,
                mode="manual" if mode == "manual" else "auto",
                estimated_tokens_before=footprint.estimated_tokens,
                externalized_paths=report.externalized_paths,
            )
        except SummaryError as exc:
            failure_report = self._record_summary_failure(session, str(exc), report, footprint)
            if session.context_state.consecutive_summary_failures >= self.config.summary_failure_limit:
                raise ContextLimitError("上下文摘要连续失败，已停止本次请求。", report=failure_report) from exc
            if footprint.estimated_tokens > budget:
                raise ContextLimitError("上下文已接近或超过预算，且摘要失败。", report=failure_report) from exc
            return await self._with_optional_context(
                session=session,
                tools=tools,
                prompt=prompt,
                report=failure_report,
                footprint=footprint,
                budget=budget,
                factory=optional_context_factory,
                max_tokens=optional_context_max_tokens,
            )

        prompt = prompt_factory()
        final_footprint = self._anchored_footprint(session, tools, prompt)
        if final_footprint.estimated_tokens > budget:
            raise ContextLimitError("上下文压缩后仍超过预算，已停止本次请求。", report=heavy_report)
        return await self._with_optional_context(
            session=session,
            tools=tools,
            prompt=prompt,
            report=heavy_report,
            footprint=final_footprint,
            budget=budget,
            factory=optional_context_factory,
            max_tokens=optional_context_max_tokens,
        )

    async def _with_optional_context(
        self,
        *,
        session: ChatSession,
        tools: Sequence[ToolSpec],
        prompt: PromptBundle,
        report: ContextCompactionReport | None,
        footprint: RequestFootprint,
        budget: int,
        factory: OptionalContextFactory | None,
        max_tokens: int,
    ) -> PreparedChatRequest:
        if factory is None or max_tokens <= 0 or footprint.estimated_tokens >= budget:
            return self._prepared(session, tools, prompt, report, footprint)
        granted = min(max_tokens, budget - footprint.estimated_tokens)
        if granted <= 0:
            return self._prepared(session, tools, prompt, report, footprint)

        base_blocks = tuple(prompt.generated_context_blocks)
        try:
            optional_blocks = await factory(granted)
        except Exception:
            optional_blocks = ()
        if not optional_blocks:
            return self._prepared(session, tools, prompt, report, footprint)
        combined = replace(
            prompt,
            generated_context_blocks=(*base_blocks, *optional_blocks),
        )
        combined_footprint = self._anchored_footprint(session, tools, combined)
        if combined_footprint.estimated_tokens <= budget:
            return self._prepared(session, tools, combined, report, combined_footprint)

        retry_budget = max(0, granted - (combined_footprint.estimated_tokens - budget))
        if retry_budget > 0 and retry_budget < granted:
            try:
                retry_blocks = await factory(retry_budget)
            except Exception:
                retry_blocks = ()
            if retry_blocks:
                retried = replace(
                    prompt,
                    generated_context_blocks=(*base_blocks, *retry_blocks),
                )
                retried_footprint = self._anchored_footprint(session, tools, retried)
                if retried_footprint.estimated_tokens <= budget:
                    return self._prepared(session, tools, retried, report, retried_footprint)
        return self._prepared(session, tools, prompt, report, footprint)

    async def manual_compact(
        self,
        *,
        session: ChatSession,
        provider: LLMProvider,
    ) -> ContextCompactionReport:
        if not self.config.enabled:
            return ContextCompactionReport(
                mode="manual",
                light_compacted=False,
                heavy_compacted=False,
                kept_message_count=len(session.messages),
                message="上下文管理已关闭，未执行压缩。",
            )
        before_tokens = sum(self.estimator.estimate_message(message) for message in session.messages)
        tool_result = self.compactor.compact(session)
        segments = self.segmenter.split(session.messages)
        summarized, recent = self.segmenter.select_recent(
            segments,
            target_tokens=self.config.recent_tokens,
            min_messages=self.config.min_recent_messages,
        )
        if not summarized:
            after_tokens = sum(self.estimator.estimate_message(message) for message in session.messages)
            return ContextCompactionReport(
                mode="manual",
                light_compacted=tool_result.changed,
                heavy_compacted=False,
                externalized_paths=tuple(ref.path for ref in tool_result.external_refs),
                kept_message_count=len(session.messages),
                summarized_message_count=0,
                estimated_tokens_before=before_tokens,
                estimated_tokens_after=after_tokens,
                message="当前历史较短，无需生成摘要。",
            )
        try:
            report = await self._summarize_segments(
                session=session,
                provider=provider,
                mode="manual",
                summarized=summarized,
                recent=recent,
                estimated_tokens_before=before_tokens,
                externalized_paths=tuple(ref.path for ref in tool_result.external_refs),
            )
        except SummaryError as exc:
            base_report = ContextCompactionReport(
                mode="manual",
                light_compacted=tool_result.changed,
                heavy_compacted=False,
                externalized_paths=tuple(ref.path for ref in tool_result.external_refs),
                kept_message_count=len(session.messages),
                estimated_tokens_before=before_tokens,
                estimated_tokens_after=before_tokens,
                message=f"手动压缩失败: {exc}",
            )
            failure_report = self._record_summary_failure(
                session,
                str(exc),
                base_report,
                RequestFootprint(chars=0, estimated_tokens=before_tokens),
            )
            if session.context_state.consecutive_summary_failures >= self.config.summary_failure_limit:
                raise ContextLimitError("上下文摘要连续失败，已停止手动压缩。", report=failure_report) from exc
            return failure_report
        return report

    def record_usage(self, usage: TokenUsage | None, footprint: RequestFootprint) -> None:
        if usage is None or usage.input_tokens is None:
            return
        anchor = TokenAnchor(input_tokens=usage.input_tokens, footprint_chars=footprint.chars)
        self._last_anchor = anchor
        session = getattr(self, "_last_session", None)
        if session is not None:
            session.context_state.token_anchor = anchor

    def record_session_usage(self, session: ChatSession, usage: TokenUsage | None, footprint: RequestFootprint) -> None:
        if usage is None or usage.input_tokens is None:
            return
        session.context_state.token_anchor = TokenAnchor(input_tokens=usage.input_tokens, footprint_chars=footprint.chars)

    def _prepared(
        self,
        session: ChatSession,
        tools: Sequence[ToolSpec],
        prompt: PromptBundle,
        report: ContextCompactionReport | None,
        footprint: RequestFootprint | None = None,
    ) -> PreparedChatRequest:
        raw = self.estimator.request_footprint(session.messages, tuple(tools), prompt)
        final_footprint = footprint or replace(
            raw,
            estimated_tokens=self.estimator.estimate_from_anchor(raw, session.context_state.token_anchor),
        )
        return PreparedChatRequest(
            request=session.build_request(tools=tools, prompt=prompt),
            footprint=final_footprint,
            report=report,
        )

    def _anchored_footprint(
        self,
        session: ChatSession,
        tools: Sequence[ToolSpec],
        prompt: PromptBundle,
    ) -> RequestFootprint:
        raw = self.estimator.request_footprint(session.messages, tuple(tools), prompt)
        return replace(raw, estimated_tokens=self.estimator.estimate_from_anchor(raw, session.context_state.token_anchor))

    async def _heavy_compact(
        self,
        *,
        session: ChatSession,
        provider: LLMProvider,
        mode: str,
        estimated_tokens_before: int,
        externalized_paths: Sequence[str],
    ) -> ContextCompactionReport:
        segments = self.segmenter.split(session.messages)
        summarized, recent = self.segmenter.select_recent(
            segments,
            target_tokens=self.config.recent_tokens,
            min_messages=self.config.min_recent_messages,
        )
        if not summarized:
            raise ContextLimitError(
                "上下文超过预算，但没有可安全摘要的早期消息。",
                report=ContextCompactionReport(
                    mode="manual" if mode == "manual" else "auto",
                    light_compacted=bool(externalized_paths),
                    heavy_compacted=False,
                    externalized_paths=tuple(externalized_paths),
                    kept_message_count=len(session.messages),
                    estimated_tokens_before=estimated_tokens_before,
                    estimated_tokens_after=estimated_tokens_before,
                    message="上下文超过预算，但没有可安全摘要的早期消息。",
                ),
            )
        return await self._summarize_segments(
            session=session,
            provider=provider,
            mode=mode,
            summarized=summarized,
            recent=recent,
            estimated_tokens_before=estimated_tokens_before,
            externalized_paths=externalized_paths,
        )

    async def _summarize_segments(
        self,
        *,
        session: ChatSession,
        provider: LLMProvider,
        mode: str,
        summarized: Sequence[ConversationSegment],
        recent: Sequence[ConversationSegment],
        estimated_tokens_before: int,
        externalized_paths: Sequence[str],
    ) -> ContextCompactionReport:
        summarized_messages = tuple(message for segment in summarized for message in segment.messages)
        recent_messages = tuple(message for segment in recent for message in segment.messages)
        all_paths = tuple([*session.context_state.compacted_tool_paths, *externalized_paths])
        summary = await self.summarizer.summarize(
            provider=provider,
            previous_summary=session.context_state.summary,
            messages=summarized_messages,
            external_paths=all_paths,
            kept_message_count=len(recent_messages),
        )
        session.replace_messages(recent_messages)
        session.set_context_summary(summary)
        session.context_state.consecutive_summary_failures = 0
        session.append_checkpoint()
        after_tokens = sum(self.estimator.estimate_message(message) for message in session.messages)
        return ContextCompactionReport(
            mode="manual" if mode == "manual" else "auto",
            light_compacted=bool(externalized_paths),
            heavy_compacted=True,
            externalized_paths=tuple(externalized_paths),
            kept_message_count=len(recent_messages),
            summarized_message_count=len(summarized_messages),
            estimated_tokens_before=estimated_tokens_before,
            estimated_tokens_after=after_tokens,
            message=(
                f"已压缩上下文：保留 {len(recent_messages)} 条近期消息，"
                f"摘要 {len(summarized_messages)} 条较早消息。"
            ),
        )

    def _record_summary_failure(
        self,
        session: ChatSession,
        reason: str,
        report: ContextCompactionReport,
        footprint: RequestFootprint,
    ) -> ContextCompactionReport:
        session.context_state.consecutive_summary_failures += 1
        return ContextCompactionReport(
            mode=report.mode,
            light_compacted=report.light_compacted,
            heavy_compacted=False,
            externalized_paths=report.externalized_paths,
            kept_message_count=len(session.messages),
            summarized_message_count=0,
            estimated_tokens_before=report.estimated_tokens_before or footprint.estimated_tokens,
            estimated_tokens_after=footprint.estimated_tokens,
            message=f"上下文摘要失败（第 {session.context_state.consecutive_summary_failures} 次）：{reason}",
        )

    def _input_budget(self, reserve_tokens: int) -> int:
        return max(1, self.config.window_tokens - self.max_output_tokens - reserve_tokens)
