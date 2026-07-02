from __future__ import annotations

import json
import tempfile
import time
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from mew_eval.models import (
    EvalCase,
    EvalCaseResult,
    EvalEventSummary,
    EvalMetric,
    EvalProviderInfo,
    EvalRunOptions,
    EvalRunTrace,
    EvalSummary,
    EvalSuiteResult,
    EvalToolCallSummary,
    EvalToolResultSummary,
    EvalUsageSummary,
)
from mew_eval.provider import ScriptedEvalProvider
from mew_eval.scoring import case_status, score_case, total_score
from mewcode.agent import AgentLoopRunner, TurnEvent
from mewcode.commands import AgentCommand
from mewcode.config import AgentConfig
from mewcode.context.manager import ContextManager
from mewcode.context.models import ContextConfig
from mewcode.permissions.controller import create_permission_controller
from mewcode.permissions.models import PermissionConfig
from mewcode.providers.base import ChatMessage, LLMProvider, TokenUsage
from mewcode.session import ChatSession
from mewcode.skills import LoadSkillTool, SkillManager, SkillRoots
from mewcode.subagents.models import SubAgentInvocation, SubAgentResult
from mewcode.subagents.tools import DelegateAgentTool
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import create_default_registry


async def run_case(
    case: EvalCase,
    metrics: tuple[EvalMetric, ...],
    options: EvalRunOptions | None = None,
) -> EvalCaseResult:
    run_options = options or EvalRunOptions()
    started = time.monotonic()
    workspace_manager = _workspace(run_options, case.id)
    with workspace_manager as workspace:
        workspace_path = Path(workspace)
        try:
            _prepare_workspace(workspace_path, case)
            trace = await _run_agent_case(case, workspace_path, run_options)
            scores = score_case(case, metrics, trace, workspace=workspace_path)
            total = total_score(scores)
            status = case_status(scores, trace, run_options.threshold)
            return EvalCaseResult(
                case_id=case.id,
                title=case.title,
                status=status,
                total_score=total,
                threshold=run_options.threshold,
                metric_scores=scores,
                trace=trace,
            )
        except Exception as exc:
            trace = EvalRunTrace(
                events=(),
                final_message="",
                stop_reason="stream_error",
                tool_calls=(),
                tool_results=(),
                usage=None,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                errors=(f"{type(exc).__name__}: {exc}",),
            )
            return EvalCaseResult(
                case_id=case.id,
                title=case.title,
                status="error",
                total_score=0.0,
                threshold=run_options.threshold,
                metric_scores=(),
                trace=trace,
            )


async def run_suite(
    cases: tuple[EvalCase, ...],
    metrics: tuple[EvalMetric, ...],
    options: EvalRunOptions | None = None,
) -> EvalSuiteResult:
    run_options = options or EvalRunOptions()
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat()
    results = []
    for case in cases:
        results.append(await run_case(case, metrics, run_options))
    result_tuple = tuple(results)
    return EvalSuiteResult(
        suite_id=run_options.suite_id,
        started_at=started_at,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        provider=_provider_info(run_options),
        results=result_tuple,
        metric_averages=_metric_averages(result_tuple, metrics),
        summary=_summary(result_tuple, run_options.threshold),
    )


def _workspace(options: EvalRunOptions, case_id: str) -> AbstractContextManager[Path]:
    if options.keep_workspaces:
        root = Path(tempfile.mkdtemp(prefix=f"mew-eval-{case_id}-", dir=_tmp_dir(options)))
        return nullcontext(root)
    return tempfile.TemporaryDirectory(prefix=f"mew-eval-{case_id}-", dir=_tmp_dir(options))  # type: ignore[return-value]


def _tmp_dir(options: EvalRunOptions) -> str | None:
    if options.workspace_root is None:
        return None
    options.workspace_root.mkdir(parents=True, exist_ok=True)
    return str(options.workspace_root)


def _prepare_workspace(workspace: Path, case: EvalCase) -> None:
    for file in case.setup_files:
        path = workspace / file.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file.content, encoding="utf-8")


async def _run_agent_case(case: EvalCase, workspace: Path, options: EvalRunOptions) -> EvalRunTrace:
    started = time.monotonic()
    session = ChatSession()
    if case.expectations.require_context_compaction:
        session.messages.append(ChatMessage(role="tool", content="x" * 200, tool_call_id="context-setup"))
    provider = _provider_for(options)
    registry = create_default_registry()
    skill_manager = _skill_manager(workspace, registry)
    sub_agent_manager = _EvalSubAgentManager()
    registry.register(LoadSkillTool(skill_manager))
    registry.register(DelegateAgentTool(sub_agent_manager))
    executor = ToolExecutor(registry, ToolContext(cwd=workspace))
    permission_controller = create_permission_controller(
        workspace,
        PermissionConfig(mode=case.permission_mode),  # type: ignore[arg-type]
    )
    context_config = (
        ContextConfig(single_tool_result_tokens=5, window_tokens=100_000)
        if case.expectations.require_context_compaction
        else ContextConfig()
    )
    context_manager = ContextManager(context_config, workspace, max_output_tokens=4096)
    runner = AgentLoopRunner(
        session,
        provider,
        registry,
        executor,
        AgentConfig(max_iterations=case.max_iterations),
        permission_controller=permission_controller,
        context_manager=context_manager,
        skill_manager=skill_manager,
        sub_agent_manager=sub_agent_manager,
    )
    events: list[EvalEventSummary] = []
    tool_calls: list[EvalToolCallSummary] = []
    tool_results: list[EvalToolResultSummary] = []
    usage: EvalUsageSummary | None = None
    final_message = ""
    stop_reason: str | None = None
    errors: list[str] = []
    command = AgentCommand(mode=case.mode, visible_text=case.prompt, model_text=case.prompt)
    async for event in runner.run(command):
        events.append(_event_summary(event))
        if event.tool_call is not None and event.type == "tool_started":
            tool_calls.append(
                EvalToolCallSummary(
                    id=event.tool_call.id,
                    name=event.tool_call.name,
                    arguments=dict(event.tool_call.arguments),
                )
            )
        if event.tool_result is not None and event.type == "tool_finished":
            tool_results.append(_tool_result_summary(event.tool_result))
        if event.usage is not None:
            usage = _usage_summary(event.usage)
        if event.type == "message_done" and event.message is not None:
            final_message = event.message.content
            stop_reason = event.stop_reason
        if event.type == "error" and event.error:
            errors.append(event.error)
            stop_reason = event.stop_reason
    return EvalRunTrace(
        events=tuple(events),
        final_message=final_message,
        stop_reason=stop_reason,
        tool_calls=tuple(tool_calls),
        tool_results=tuple(tool_results),
        usage=usage,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        errors=tuple(errors),
    )


def _skill_manager(workspace: Path, registry: Any) -> SkillManager:
    manager = SkillManager(
        SkillRoots(
            project=workspace / ".mewcode" / "skills",
            user=workspace / ".mewcode-user" / "skills",
            builtin=resources.files("mewcode.skills.builtin"),
        ),
        registry,
    )
    manager.refresh_if_changed()
    return manager


def _provider_for(options: EvalRunOptions) -> LLMProvider:
    if options.mode == "offline":
        return ScriptedEvalProvider()
    if options.provider is None:
        raise RuntimeError("在线评测模式缺少 Provider，请通过 CLI 配置或测试注入 provider")
    return options.provider


def _provider_info(options: EvalRunOptions) -> EvalProviderInfo:
    if options.provider_info is not None:
        return options.provider_info
    if options.mode == "offline":
        return EvalProviderInfo(mode="offline", provider="scripted-eval", protocol="offline", model="scripted")
    return EvalProviderInfo(mode="online")


class _EvalSubAgentManager:
    def __init__(self) -> None:
        self.parent_context = None
        self.invocations: list[SubAgentInvocation] = []

    def bind_parent_context(self, context: object | None) -> None:
        self.parent_context = context

    def prompt_context(self) -> None:
        return None

    async def delegate(self, invocation: SubAgentInvocation) -> SubAgentResult:
        self.invocations.append(invocation)
        return SubAgentResult(
            task_id=f"eval-subagent-{len(self.invocations)}",
            type=invocation.type,
            role=invocation.role,
            status="completed",
            task=invocation.task,
            summary="子 Agent 已完成离线审查。",
            final_text="评测说明可复核。",
            stop_reason="completed",
            usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15, provider="scripted-eval-subagent"),
        )


def _event_summary(event: TurnEvent) -> EvalEventSummary:
    detail = event.text or event.error or ""
    if event.progress is not None:
        detail = f"{event.progress.phase}:{event.progress.iteration}/{event.progress.max_iterations}"
    if event.tool_call is not None:
        detail = event.tool_call.name
    if event.tool_result is not None:
        detail = f"{event.tool_result.tool_name}:{'ok' if event.tool_result.success else event.tool_result.error_type}"
    if event.permission is not None and event.permission.decision is not None:
        detail = f"{event.permission.decision.kind}:{event.permission.decision.reason}"
    if event.context_report is not None:
        detail = event.context_report.message
    return EvalEventSummary(type=event.type, detail=_truncate(detail))


def _tool_result_summary(result: Any) -> EvalToolResultSummary:
    return EvalToolResultSummary(
        call_id=result.tool_call_id,
        tool_name=result.tool_name,
        success=result.success,
        error_type=result.error_type,
        error=result.error,
        data_preview=_truncate(json.dumps(result.data, ensure_ascii=False, sort_keys=True)),
    )


def _usage_summary(usage: TokenUsage) -> EvalUsageSummary:
    cache = usage.cache
    return EvalUsageSummary(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        provider=usage.provider,
        cache_status=cache.status if cache is not None else None,
        cache_read_input_tokens=cache.read_input_tokens if cache is not None else None,
        cache_creation_input_tokens=cache.creation_input_tokens if cache is not None else None,
        cached_tokens=cache.cached_tokens if cache is not None else None,
    )


def _summary(results: tuple[EvalCaseResult, ...], threshold: float) -> EvalSummary:
    total_cases = len(results)
    average = round(sum(result.total_score for result in results) / total_cases, 2) if total_cases else 0.0
    return EvalSummary(
        total_cases=total_cases,
        passed=sum(1 for result in results if result.status == "pass"),
        failed=sum(1 for result in results if result.status == "fail"),
        errors=sum(1 for result in results if result.status == "error"),
        needs_review=sum(1 for result in results if result.status == "needs_review"),
        average_score=average,
        threshold=threshold,
    )


def _metric_averages(results: tuple[EvalCaseResult, ...], metrics: tuple[EvalMetric, ...]) -> dict[str, float]:
    averages: dict[str, float] = {}
    for metric in metrics:
        scores = [
            score.score / score.max_score * 100
            for result in results
            for score in result.metric_scores
            if score.metric_id == metric.id and score.max_score > 0
        ]
        averages[metric.id] = round(sum(scores) / len(scores), 2) if scores else 0.0
    return averages


def _truncate(text: str, limit: int = 500) -> str:
    cleaned = text.replace("\n", "\\n")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "...[truncated]"
