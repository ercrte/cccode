from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from julycode.context.manager import ContextManager
from julycode.context.models import ContextLimitError
from julycode.memory.index import MemoryIndexBuilder
from julycode.memory.instructions import InstructionLoader
from julycode.memory.models import (
    BootstrapOptions,
    BootstrapResult,
    KnowledgeContext,
    ProtocolValidationResult,
    RestoreReport,
    SessionMemoryConfig,
)
from julycode.memory.notes import MemoryNoteStore
from julycode.memory.session_store import SessionJsonlStore
from julycode.prompting.base import PromptBundle
from julycode.providers.base import ChatMessage, LLMProvider
from julycode.session import ChatSession


class SessionHistoryValidator:
    def truncate_to_protocol_safe(self, messages: Sequence[ChatMessage]) -> ProtocolValidationResult:
        valid: list[ChatMessage] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            if message.role == "tool":
                return self._truncated(messages, valid, "恢复历史包含孤立工具结果，已截断。")
            if message.role == "assistant" and message.tool_calls:
                group = self._complete_tool_group(messages, index)
                if group is None:
                    return self._truncated(messages, valid, "恢复历史包含未配对工具调用，已截断。")
                valid.extend(group)
                index += len(group)
                continue
            valid.append(message)
            index += 1
        return ProtocolValidationResult(messages=tuple(valid))

    def _complete_tool_group(self, messages: Sequence[ChatMessage], index: int) -> list[ChatMessage] | None:
        assistant = messages[index]
        call_ids = [call.id for call in assistant.tool_calls]
        expected = set(call_ids)
        seen: set[str] = set()
        group = [assistant]
        cursor = index + 1
        while cursor < len(messages) and len(seen) < len(expected):
            candidate = messages[cursor]
            if candidate.role != "tool" or candidate.tool_call_id not in expected:
                return None
            if candidate.tool_call_id in seen:
                return None
            seen.add(candidate.tool_call_id)
            group.append(candidate)
            cursor += 1
        if seen != expected:
            return None
        return group

    def _truncated(
        self,
        messages: Sequence[ChatMessage],
        valid: Sequence[ChatMessage],
        warning: str,
    ) -> ProtocolValidationResult:
        return ProtocolValidationResult(
            messages=tuple(valid),
            truncated_count=len(messages) - len(valid),
            warning=warning,
        )


class SessionBootstrapper:
    def __init__(
        self,
        cwd: Path,
        config: SessionMemoryConfig | None = None,
        *,
        store: SessionJsonlStore | None = None,
        instruction_loader: InstructionLoader | None = None,
        note_store: MemoryNoteStore | None = None,
        index_builder: MemoryIndexBuilder | None = None,
        validator: SessionHistoryValidator | None = None,
    ) -> None:
        self.cwd = cwd.resolve()
        self.config = config or SessionMemoryConfig()
        self.store = store or SessionJsonlStore(self.cwd, self.config)
        self.instruction_loader = instruction_loader or InstructionLoader(self.cwd, self.config)
        self.note_store = note_store or MemoryNoteStore(self.cwd, self.config)
        self.index_builder = index_builder or MemoryIndexBuilder(self.note_store, self.config)
        self.validator = validator or SessionHistoryValidator()

    async def bootstrap(
        self,
        *,
        options: BootstrapOptions,
        provider: LLMProvider,
        context_manager: ContextManager,
    ) -> BootstrapResult:
        if not self.config.enabled:
            session = ChatSession()
            report = RestoreReport(
                restored=False,
                session_id=session.context_state.session_id,  # type: ignore[arg-type]
                started_empty_reason="记忆系统已关闭，启动普通空会话。",
            )
            return BootstrapResult(session=session, knowledge_context=KnowledgeContext(restore_report=report), restore_report=report)

        now = datetime.now(timezone.utc)
        self.store.cleanup_expired(now=now)
        knowledge_context = self.load_knowledge()

        if options.new_session or not self.config.auto_restore:
            session = self.store.create_session()
            reason = "已按请求启动空会话。" if options.new_session else "自动恢复已关闭，启动空会话。"
            report = RestoreReport(restored=False, session_id=session.context_state.session_id, started_empty_reason=reason)  # type: ignore[arg-type]
            return BootstrapResult(session=session, knowledge_context=replace(knowledge_context, restore_report=report), restore_report=report)

        latest = self.store.latest_unexpired(now=now)
        if latest is None:
            session = self.store.create_session()
            report = RestoreReport(restored=False, session_id=session.context_state.session_id, started_empty_reason="没有可恢复的未过期会话。")  # type: ignore[arg-type]
            return BootstrapResult(session=session, knowledge_context=replace(knowledge_context, restore_report=report), restore_report=report)

        session, loaded_report = self.store.load_session(latest.session_id)
        validation = self.validator.truncate_to_protocol_safe(session.messages)
        if validation.truncated_count:
            session.replace_messages(validation.messages)
        time_gap_notice = self._time_gap_notice(latest.updated_at, now)
        warnings = (
            *knowledge_context.instructions.warnings,
            *loaded_report.warnings,
            *(tuple([validation.warning]) if validation.warning else ()),
        )
        report = RestoreReport(
            restored=True,
            session_id=latest.session_id,
            source_path=latest.path,
            skipped_bad_lines=loaded_report.skipped_bad_lines,
            truncated_messages=validation.truncated_count,
            time_gap_notice=time_gap_notice,
            warnings=warnings,
        )
        session, report = await self._ensure_budget(session, report, provider, context_manager)
        knowledge_context = replace(knowledge_context, restore_report=report)
        return BootstrapResult(session=session, knowledge_context=knowledge_context, restore_report=report)

    def load_knowledge(self) -> KnowledgeContext:
        instructions = self.instruction_loader.load()
        user_index = self.index_builder.read_index("user") or self.index_builder.build("user")
        project_index = self.index_builder.read_index("project") or self.index_builder.build("project")
        return KnowledgeContext(
            instructions=instructions,
            user_memory_index=user_index,
            project_memory_index=project_index,
        )

    def _time_gap_notice(self, updated_at: datetime, now: datetime) -> str:
        hours = (now - updated_at).total_seconds() / 3600
        if hours < self.config.time_gap_hours:
            return ""
        return f"本会话距离上次活动约 {int(hours)} 小时，继续时请注意项目状态可能已变化。"

    async def _ensure_budget(
        self,
        session: ChatSession,
        report: RestoreReport,
        provider: LLMProvider,
        context_manager: ContextManager,
    ) -> tuple[ChatSession, RestoreReport]:
        if not session.messages:
            return session, report
        try:
            prepared = await context_manager.prepare_request(
                session=session,
                provider=provider,
                tools=(),
                prompt_factory=lambda: PromptBundle(stable_blocks=(), runtime_blocks=()),
                mode="manual",
            )
        except ContextLimitError as exc:
            empty = self.store.create_session()
            failed = RestoreReport(
                restored=False,
                session_id=empty.context_state.session_id,  # type: ignore[arg-type]
                started_empty_reason=f"恢复会话超过上下文预算，已启动空会话: {exc}",
                warnings=report.warnings,
            )
            return empty, failed
        compacted = bool(prepared.report and prepared.report.heavy_compacted)
        if compacted:
            return session, replace(report, compacted=True)
        return session, report
