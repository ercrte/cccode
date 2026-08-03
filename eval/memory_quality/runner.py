from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from memory_quality.matching import ExtractionMatcher, aggregate_extraction_metrics
from memory_quality.models import (
    ExtractionCase,
    ExtractionCaseResult,
    ExtractionMetrics,
    InheritanceCase,
    InheritanceCaseResult,
    InheritanceTrial,
    MemoryQualityDataset,
    MemoryQualityReport,
    MemoryQualityRunOptions,
)
from memory_quality.offline import ScriptedMemoryQualityProvider
from memory_quality.report import acceptance_failures
from julycode.agent import AgentLoopRunner
from julycode.commands import AgentCommand
from julycode.config import AgentConfig
from julycode.context.manager import ContextManager
from julycode.context.models import ContextConfig
from julycode.memory.index import MemoryIndexBuilder
from julycode.memory.manager import SessionMemoryManager
from julycode.memory.models import BootstrapOptions, KnowledgeContext, MemoryUpdateJob, SessionMemoryConfig
from julycode.memory.notes import MemoryNoteStore
from julycode.memory.updater import MemoryNoteUpdater
from julycode.providers.base import ChatMessage, ChatRequest, LLMProvider, StreamEvent
from julycode.session_id import new_session_id
from julycode.tools.base import ToolContext
from julycode.tools.executor import ToolExecutor
from julycode.tools.registry import create_default_registry


class RecordingProvider(LLMProvider):
    def __init__(self, wrapped: LLMProvider) -> None:
        self.wrapped = wrapped
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        async for event in self.wrapped.stream_chat(request):
            yield event


class MemoryQualityRunner:
    def __init__(self, matcher: ExtractionMatcher | None = None) -> None:
        self.matcher = matcher or ExtractionMatcher()

    async def run(self, dataset: MemoryQualityDataset, options: MemoryQualityRunOptions) -> MemoryQualityReport:
        started_at = datetime.now(UTC).isoformat()
        extraction_results, metrics = await self.run_extraction(dataset.extraction_cases, options)
        inheritance_results = await self.run_inheritance(dataset.inheritance_cases, options)
        first_turn_accuracy, baseline_restatements, enabled_restatements, reduction = _inheritance_metrics(
            inheritance_results
        )
        failures = acceptance_failures(
            metrics,
            extraction_case_count=len(dataset.extraction_cases),
            inheritance_case_count=len(dataset.inheritance_cases),
            first_turn_accuracy=first_turn_accuracy,
            baseline_restatements=baseline_restatements,
            restatement_reduction=reduction,
        )
        return MemoryQualityReport(
            dataset_version=dataset.version,
            mode=options.mode,
            provider=options.provider_info,
            started_at=started_at,
            extraction_results=extraction_results,
            extraction_metrics=metrics,
            inheritance_results=inheritance_results,
            first_turn_accuracy=first_turn_accuracy,
            baseline_restatements=baseline_restatements,
            enabled_restatements=enabled_restatements,
            restatement_reduction=reduction,
            acceptance_passed=not failures,
            acceptance_failures=failures,
        )

    async def run_extraction(
        self,
        cases: Sequence[ExtractionCase],
        options: MemoryQualityRunOptions,
    ) -> tuple[tuple[ExtractionCaseResult, ...], ExtractionMetrics]:
        results: list[ExtractionCaseResult] = []
        for case in cases:
            with tempfile.TemporaryDirectory(prefix=f"mew-memory-extract-{case.case_id}-", dir=_tmp_root(options)) as raw:
                workspace = Path(raw)
                config = SessionMemoryConfig(user_dir=str(workspace / ".julycode-user"))
                store = MemoryNoteStore(workspace, config)
                updater = MemoryNoteUpdater(store, MemoryIndexBuilder(store, config))
                provider = _case_provider(options, extraction_case=case)
                job = MemoryUpdateJob(
                    session_id=new_session_id(),
                    cwd=workspace,
                    turn_messages=case.messages,
                    final_message=ChatMessage(role="assistant", content="当前轮已完成。"),
                    knowledge_context=KnowledgeContext(),
                )
                extracted = await updater.extract(job=job, provider=provider)
                results.append(self.matcher.match(case, extracted))
                if store.list_notes("user") or store.list_notes("project"):
                    raise RuntimeError(f"提取评测不应写入笔记: {case.case_id}")
        result_tuple = tuple(results)
        return result_tuple, aggregate_extraction_metrics(cases, result_tuple)

    async def run_inheritance(
        self,
        cases: Sequence[InheritanceCase],
        options: MemoryQualityRunOptions,
    ) -> tuple[InheritanceCaseResult, ...]:
        results = []
        for case in cases:
            enabled = await self._run_enabled_trial(case, options)
            baseline = await self._run_baseline_trial(case, options)
            results.append(InheritanceCaseResult(case_id=case.case_id, baseline=baseline, enabled=enabled))
        return tuple(results)

    async def _run_enabled_trial(
        self,
        case: InheritanceCase,
        options: MemoryQualityRunOptions,
    ) -> InheritanceTrial:
        with tempfile.TemporaryDirectory(prefix=f"mew-memory-enabled-{case.case_id}-", dir=_tmp_root(options)) as raw:
            workspace = Path(raw)
            config = SessionMemoryConfig(user_dir=str(workspace / ".julycode-user"))
            base_provider = _case_provider(options, inheritance_case=case)
            source_provider = RecordingProvider(base_provider)
            source_context = ContextManager(ContextConfig(), workspace, max_output_tokens=4096)
            source_manager = SessionMemoryManager(workspace, config)
            source_bootstrap = await source_manager.bootstrap(
                options=BootstrapOptions(new_session=True),
                provider=source_provider,
                context_manager=source_context,
            )
            source_runner = _agent_runner(
                source_bootstrap.session,
                source_provider,
                workspace,
                source_context,
                source_manager,
            )
            await _run_turn(source_runner, case.source_prompt)
            await source_manager.wait_for_updates()
            loaded = source_manager.load_runtime_context()
            if loaded.user_memory_index is None or loaded.project_memory_index is None:
                raise RuntimeError(f"来源会话没有生成两类记忆索引: {case.case_id}")

            target_config = replace(config, auto_notes_enabled=False)
            target_provider = RecordingProvider(base_provider)
            target_context = ContextManager(ContextConfig(), workspace, max_output_tokens=4096)
            target_manager = SessionMemoryManager(workspace, target_config)
            target_bootstrap = await target_manager.bootstrap(
                options=BootstrapOptions(new_session=True),
                provider=target_provider,
                context_manager=target_context,
            )
            started_empty = not target_bootstrap.session.messages
            target_runner = _agent_runner(
                target_bootstrap.session,
                target_provider,
                workspace,
                target_context,
                target_manager,
            )
            final_text = await _run_turn(target_runner, case.target_prompt)
            request = target_provider.requests[0]
            runtime = _runtime_text(request)
            old_history = request.messages[:-1]
            source_absent = all(case.source_prompt not in message.content for message in old_history)
            return _score_trial(
                case,
                final_text,
                memory_enabled=True,
                session_started_empty=started_empty and source_absent,
                injected_user_memory="scope=user" in runtime,
                injected_project_memory="scope=project" in runtime,
            )

    async def _run_baseline_trial(
        self,
        case: InheritanceCase,
        options: MemoryQualityRunOptions,
    ) -> InheritanceTrial:
        with tempfile.TemporaryDirectory(prefix=f"mew-memory-baseline-{case.case_id}-", dir=_tmp_root(options)) as raw:
            workspace = Path(raw)
            config = SessionMemoryConfig(enabled=False, user_dir=str(workspace / ".julycode-user"))
            provider = RecordingProvider(_case_provider(options, inheritance_case=case))
            context = ContextManager(ContextConfig(), workspace, max_output_tokens=4096)
            manager = SessionMemoryManager(workspace, config)
            bootstrapped = await manager.bootstrap(
                options=BootstrapOptions(new_session=True),
                provider=provider,
                context_manager=context,
            )
            started_empty = not bootstrapped.session.messages
            runner = _agent_runner(bootstrapped.session, provider, workspace, context, manager)
            final_text = await _run_turn(runner, case.target_prompt)
            runtime = _runtime_text(provider.requests[0])
            return _score_trial(
                case,
                final_text,
                memory_enabled=False,
                session_started_empty=started_empty,
                injected_user_memory="scope=user" in runtime,
                injected_project_memory="scope=project" in runtime,
            )


def _case_provider(
    options: MemoryQualityRunOptions,
    *,
    extraction_case: ExtractionCase | None = None,
    inheritance_case: InheritanceCase | None = None,
) -> LLMProvider:
    if options.mode == "offline":
        return ScriptedMemoryQualityProvider(
            extraction_case=extraction_case,
            inheritance_case=inheritance_case,
        )
    if options.provider is None:
        raise RuntimeError("在线记忆质量评测缺少 Provider")
    return options.provider


def _agent_runner(
    session,
    provider: LLMProvider,
    workspace: Path,
    context_manager: ContextManager,
    memory_manager: SessionMemoryManager,
) -> AgentLoopRunner:
    registry = create_default_registry()
    executor = ToolExecutor(registry, ToolContext(cwd=workspace))
    return AgentLoopRunner(
        session,
        provider,
        registry,
        executor,
        AgentConfig(max_iterations=4),
        context_manager=context_manager,
        memory_manager=memory_manager,
    )


async def _run_turn(runner: AgentLoopRunner, prompt: str) -> str:
    final_text = ""
    command = AgentCommand(mode="normal", visible_text=prompt, model_text=prompt)
    async for event in runner.run(command):
        if event.type == "message_done" and event.message is not None:
            final_text = event.message.content
        if event.type == "error":
            raise RuntimeError(event.error or "跨会话评测 Agent 失败")
    return final_text


def _score_trial(
    case: InheritanceCase,
    final_text: str,
    *,
    memory_enabled: bool,
    session_started_empty: bool,
    injected_user_memory: bool,
    injected_project_memory: bool,
) -> InheritanceTrial:
    lowered = final_text.casefold()
    missing = [
        "/".join(group)
        for group in case.expectation.required_term_groups
        if not any(term.casefold() in lowered for term in group)
    ]
    forbidden = [term for term in case.expectation.forbidden_terms if term.casefold() in lowered]
    restatements = [term for term in case.expectation.restatement_terms if term.casefold() in lowered]
    evidence = [*(f"缺少必需项: {item}" for item in missing), *(f"命中禁止项: {item}" for item in forbidden)]
    if restatements:
        evidence.append(f"请求背景重述: {', '.join(restatements)}")
    if not session_started_empty:
        evidence.append("目标会话不是空白新会话")
    if memory_enabled and not injected_user_memory:
        evidence.append("首个请求缺少用户长期记忆")
    if memory_enabled and not injected_project_memory:
        evidence.append("首个请求缺少项目长期记忆")
    correct = (
        not missing
        and not forbidden
        and not restatements
        and session_started_empty
        and (not memory_enabled or (injected_user_memory and injected_project_memory))
    )
    return InheritanceTrial(
        memory_enabled=memory_enabled,
        final_text=final_text,
        first_turn_correct=correct,
        requested_restatement=bool(restatements),
        session_started_empty=session_started_empty,
        injected_user_memory=injected_user_memory,
        injected_project_memory=injected_project_memory,
        evidence=tuple(evidence),
    )


def _inheritance_metrics(
    results: Sequence[InheritanceCaseResult],
) -> tuple[float, int, int, float | None]:
    total = len(results)
    accuracy = sum(1 for result in results if result.enabled.first_turn_correct) / total if total else 0.0
    baseline = sum(1 for result in results if result.baseline.requested_restatement)
    enabled = sum(1 for result in results if result.enabled.requested_restatement)
    reduction = (baseline - enabled) / baseline if baseline else None
    return accuracy, baseline, enabled, reduction


def _runtime_text(request: ChatRequest) -> str:
    if request.prompt is None:
        return ""
    return "\n".join(block.text for block in request.prompt.runtime_blocks)


def _tmp_root(options: MemoryQualityRunOptions) -> str | None:
    if options.workspace_root is None:
        return None
    options.workspace_root.mkdir(parents=True, exist_ok=True)
    return str(options.workspace_root)

