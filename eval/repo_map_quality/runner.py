from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from repo_map_quality.models import (
    NavigationCase,
    NavigationCaseResult,
    NavigationDataset,
    NavigationSummary,
    NavigationTrial,
    RepoMapQualityReport,
    RepoMapQualityRunOptions,
)
from julycode.agent import AgentLoopRunner
from julycode.commands import AgentCommand
from julycode.config import AgentConfig, RepoMapConfig
from julycode.context.estimator import TokenEstimator
from julycode.context.manager import ContextManager
from julycode.context.models import ContextConfig
from julycode.repo_map.manager import RepoMapManager
from julycode.session import ChatSession
from julycode.tools.base import ToolContext
from julycode.tools.builtin import FindFilesTool, ReadFileTool, SearchCodeTool
from julycode.tools.executor import ToolExecutor
from julycode.tools.registry import ToolRegistry


class RepoMapQualityRunner:
    async def run(
        self,
        dataset: NavigationDataset,
        options: RepoMapQualityRunOptions,
    ) -> RepoMapQualityReport:
        root = options.root.resolve()
        _validate_targets(root, dataset.cases)
        started_at = datetime.now(UTC).isoformat()
        manager = RepoMapManager(root, RepoMapConfig(enabled=True, max_tokens=options.map_budget))
        await manager.start()
        if not await manager.wait_until_ready():
            await manager.close()
            raise RuntimeError("Repo Map 初始索引失败")
        try:
            offline = [await self._offline_case(case, manager) for case in dataset.cases]
            if options.mode == "paired":
                results = [await self._paired_case(case, base, manager, options) for case, base in zip(dataset.cases, offline)]
            else:
                results = offline
        finally:
            await manager.close()
        result_tuple = tuple(results)
        return RepoMapQualityReport(
            dataset_version=dataset.version,
            mode=options.mode,
            root=root.as_posix(),
            started_at=started_at,
            results=result_tuple,
            summary=_summary(result_tuple),
        )

    async def _offline_case(
        self,
        case: NavigationCase,
        manager: RepoMapManager,
    ) -> NavigationCaseResult:
        estimator = TokenEstimator(ContextConfig())
        snapshot = await manager.build_snapshot(
            manager.begin_turn(case.request),
            manager.config.max_tokens,
            estimator.estimate_text,
        )
        top_files = () if snapshot is None else snapshot.included_files[: case.top_k]
        enabled = NavigationTrial(
            enabled=True,
            target_hit=case.target_file in top_files,
            top_files=top_files,
            error="map-omitted" if snapshot is None else None,
        )
        return NavigationCaseResult(
            case_id=case.case_id,
            target_file=case.target_file,
            top_k=case.top_k,
            disabled=NavigationTrial(enabled=False, target_hit=False),
            enabled=enabled,
        )

    async def _paired_case(
        self,
        case: NavigationCase,
        base: NavigationCaseResult,
        manager: RepoMapManager,
        options: RepoMapQualityRunOptions,
    ) -> NavigationCaseResult:
        disabled = await self._online_trial(case, options, repo_map_manager=None)
        enabled_online = await self._online_trial(case, options, repo_map_manager=manager)
        enabled = NavigationTrial(
            enabled=True,
            target_hit=base.enabled.target_hit,
            top_files=base.enabled.top_files,
            target_read=enabled_online.target_read,
            exploration_calls=enabled_online.exploration_calls,
            final_text=enabled_online.final_text,
            error=enabled_online.error or base.enabled.error,
        )
        return NavigationCaseResult(case.case_id, case.target_file, case.top_k, disabled, enabled)

    async def _online_trial(
        self,
        case: NavigationCase,
        options: RepoMapQualityRunOptions,
        *,
        repo_map_manager: RepoMapManager | None,
    ) -> NavigationTrial:
        registry = _read_only_registry()
        executor = ToolExecutor(registry, ToolContext(cwd=options.root))
        session = ChatSession()
        runner = AgentLoopRunner(
            session,
            options.provider,  # type: ignore[arg-type]
            registry,
            executor,
            AgentConfig(max_iterations=8),
            context_manager=ContextManager(ContextConfig(), options.root, max_output_tokens=4096),
            repo_map_manager=repo_map_manager,
        )
        error: str | None = None
        final_text = ""
        try:
            async for event in runner.run(AgentCommand("normal", case.request, case.request)):
                if event.type == "message_done" and event.message is not None:
                    final_text = event.message.content
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        hit, calls = _exploration_metrics(session, case.target_file)
        return NavigationTrial(
            enabled=repo_map_manager is not None,
            target_hit=False,
            target_read=hit,
            exploration_calls=calls,
            final_text=final_text,
            error=error,
        )


def _read_only_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(FindFilesTool())
    registry.register(SearchCodeTool())
    return registry


def _exploration_metrics(session: ChatSession, target_file: str) -> tuple[bool, int]:
    calls = 0
    target = PurePosixPath(target_file).as_posix()
    for message in session.messages:
        for call in message.tool_calls:
            if call.name not in {"find_files", "search_code", "read_file"}:
                continue
            path = str(call.arguments.get("path", "")).replace("\\", "/").removeprefix("./")
            if call.name == "read_file" and PurePosixPath(path).as_posix() == target:
                return True, calls
            calls += 1
    return False, calls


def _validate_targets(root: Path, cases: tuple[NavigationCase, ...]) -> None:
    for case in cases:
        target = (root / case.target_file).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"目标文件越出评测根目录: {case.target_file}") from exc
        if not target.is_file():
            raise ValueError(f"目标文件不存在: {case.target_file}")


def _summary(results: tuple[NavigationCaseResult, ...]) -> NavigationSummary:
    count = len(results)
    enabled_hits = sum(result.enabled.target_hit for result in results)
    disabled_hits = sum(result.disabled.target_hit for result in results)
    return NavigationSummary(
        case_count=count,
        disabled_top_k_hit_rate=disabled_hits / count if count else 0.0,
        enabled_top_k_hit_rate=enabled_hits / count if count else 0.0,
        disabled_average_exploration_calls=_average(
            result.disabled.exploration_calls for result in results
        ),
        enabled_average_exploration_calls=_average(
            result.enabled.exploration_calls for result in results
        ),
    )


def _average(values) -> float | None:
    available = [value for value in values if value is not None]
    return sum(available) / len(available) if available else None
