from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "eval"
if str(EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(EVAL_ROOT))

from july_eval.loader import EvalConfigError, load_cases, load_metrics
from july_eval.models import (
    EvalCase,
    EvalCaseResult,
    EvalEventSummary,
    EvalExpectations,
    EvalFile,
    EvalFileExpectation,
    EvalMetric,
    EvalProviderInfo,
    EvalRunOptions,
    EvalRunTrace,
    EvalSuiteResult,
    EvalSummary,
    EvalToolCallSummary,
    EvalToolResultSummary,
    EvalUsageSummary,
    MetricScore,
)
from july_eval.provider import ScriptedEvalProvider
from july_eval.report import write_json_report, write_markdown_report
from july_eval.runner import run_case, run_suite
from july_eval.scoring import case_status, score_case, total_score
from repo_map_quality.loader import NavigationDatasetLoader, RepoMapQualityConfigError
from repo_map_quality.models import (
    NavigationCase,
    NavigationCaseResult,
    NavigationDataset,
    NavigationSummary,
    NavigationTrial,
    RepoMapQualityReport,
    RepoMapQualityRunOptions,
)
from repo_map_quality.report import (
    write_json_report as write_repo_map_json_report,
    write_markdown_report as write_repo_map_markdown_report,
)
from repo_map_quality.runner import RepoMapQualityRunner
from julycode.providers.base import ChatMessage, ChatRequest, PromptCacheUsage, StreamEvent, TokenUsage


class FakeOnlineProvider:
    def __init__(self, content: str = "在线评测完成") -> None:
        self.content = content
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest):
        self.requests.append(request)
        yield StreamEvent(
            type="usage",
            usage=TokenUsage(
                input_tokens=10,
                output_tokens=3,
                total_tokens=13,
                provider="fake-online",
                cache=PromptCacheUsage(status="hit", cached_tokens=8),
            ),
        )
        yield StreamEvent(type="text_delta", text=self.content)
        yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=self.content))


def offline_options(**kwargs) -> EvalRunOptions:
    kwargs.setdefault("review_sample_rate", 0.0)
    return EvalRunOptions(
        mode="offline",
        suite_id="offline",
        provider_info=EvalProviderInfo(
            mode="offline",
            protocol="offline",
            model="scripted",
            provider="scripted-eval",
            prompt_cache_enabled=False,
        ),
        **kwargs,
    )


def test_eval_models_have_expected_defaults() -> None:
    metric = EvalMetric("task_completion", "任务完成度", "完成请求", 0, 5, 1.0, ("最终回复",))
    provider = EvalProviderInfo("online", "openai", "gpt-test", "openai", True)
    case = EvalCase("basic", "普通问答", "普通问答", "你好", tags=("basic_qa",), online_only=True)
    usage = EvalUsageSummary(1, 2, 3, "test", cache_status="hit", cached_tokens=1)
    trace = EvalRunTrace((), "完成", "completed", (), (), usage, 10)
    score = MetricScore("task_completion", 5, 5, 1, "pass", ("ok",))
    result = EvalCaseResult("basic", "普通问答", "pass", 100, 80, (score,), trace)
    suite = EvalSuiteResult("online", "now", 10, provider, (result,), {"task_completion": 100}, EvalSummary(1, 1, 0, 0, 0, 100, 80))

    assert metric.evidence == ("最终回复",)
    assert case.expectations.expected_stop_reason == "completed"
    assert case.expectations.required_tools == ()
    assert case.tags == ("basic_qa",)
    assert case.online_only is True
    assert EvalExpectations().expected_files == ()
    assert EvalRunOptions().mode == "online"
    assert EvalRunOptions().review_sample_rate == 0.1
    assert usage.cache_status == "hit"
    assert EvalFile("a.txt", "x").path == "a.txt"
    assert EvalFileExpectation("a.txt", contains=("x",)).contains == ("x",)
    assert suite.provider.model == "gpt-test"
    assert suite.summary.total_cases == 1


def test_load_metrics_from_json(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    path.write_text(
        json.dumps(
            {
                "metrics": [
                    {
                        "id": "task_completion",
                        "name": "任务完成度",
                        "description": "完成",
                        "scale_min": 0,
                        "scale_max": 5,
                        "weight": 1.5,
                        "evidence": ["最终回复"],
                        "manual_review": True,
                    },
                    {
                        "id": "tool_use",
                        "name": "工具",
                        "description": "工具",
                        "scale_min": 0,
                        "scale_max": 5,
                        "weight": 1.0,
                        "evidence": ["工具序列"],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    metrics = load_metrics(path)

    assert [metric.id for metric in metrics] == ["task_completion", "tool_use"]
    assert metrics[0].weight == 1.5
    assert metrics[0].manual_review is True


def test_load_cases_from_directory(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text(
        json.dumps(
            {
                "id": "b",
                "title": "B",
                "category": "普通问答",
                "prompt": "hello",
                "tags": ["readonly", "smoke"],
                "online_only": True,
                "setup_files": [{"path": "README.md", "content": "hi"}],
                "expectations": {
                    "final_contains": ["hi"],
                    "required_tools": ["read_file"],
                    "expected_files": [{"path": "README.md", "contains": ["hi"]}],
                },
                "metric_weights": {"tool_use": 2.0},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "a.json").write_text(
        json.dumps({"id": "a", "title": "A", "category": "普通问答", "prompt": "hello"}),
        encoding="utf-8",
    )

    cases = load_cases(tmp_path)

    assert [case.id for case in cases] == ["a", "b"]
    assert cases[1].setup_files[0].path == "README.md"
    assert cases[1].tags == ("readonly", "smoke")
    assert cases[1].online_only is True
    assert cases[1].offline_only is False
    assert cases[1].expectations.required_tools == ("read_file",)
    assert cases[1].expectations.expected_files[0].contains == ("hi",)
    assert cases[1].metric_weights == {"tool_use": 2.0}


@pytest.mark.parametrize(
    "payload",
    [
        {"metrics": [{"id": "x", "name": "X", "description": "x", "scale_min": 5, "scale_max": 5, "weight": 1, "evidence": ["x"]}]},
        {"metrics": [{"id": "x", "name": "X", "description": "x", "scale_min": 0, "scale_max": 5, "weight": 0, "evidence": ["x"]}]},
        {"metrics": [{"id": "x", "name": "X", "description": "x", "scale_min": 0, "scale_max": 5, "weight": 1, "evidence": ["x"]}, {"id": "x", "name": "Y", "description": "y", "scale_min": 0, "scale_max": 5, "weight": 1, "evidence": ["y"]}]},
    ],
)
def test_invalid_eval_metrics_are_rejected(tmp_path: Path, payload: dict[str, object]) -> None:
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvalConfigError):
        load_metrics(path)


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "x", "title": "X", "category": "普通问答", "prompt": ""},
        {"id": "x", "title": "X", "category": "普通问答", "prompt": "hi", "expectations": {"required_tools": [1]}},
        {"id": "x", "title": "X", "category": "普通问答", "prompt": "hi", "tags": [1]},
        {"id": "x", "title": "X", "category": "普通问答", "prompt": "hi", "online_only": True, "offline_only": True},
        [{"id": "x", "title": "X", "category": "普通问答", "prompt": "hi"}, {"id": "x", "title": "Y", "category": "普通问答", "prompt": "hi"}],
    ],
)
def test_invalid_eval_cases_are_rejected(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "case.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvalConfigError):
        load_cases(path)


def test_default_metrics_and_cases_cover_required_dimensions() -> None:
    metrics = load_metrics(ROOT / "eval/metrics/default_metrics.json")
    cases = load_cases(ROOT / "eval/cases/offline")

    metric_ids = {metric.id for metric in metrics}
    assert {
        "task_completion",
        "tool_use",
        "change_quality",
        "verification",
        "safety",
        "context_continuity",
        "error_recovery",
        "ux",
        "efficiency",
        "stability",
    }.issubset(metric_ids)
    assert all(metric.weight > 0 and metric.scale_min < metric.scale_max and metric.evidence for metric in metrics)
    assert len(cases) == 8
    assert all(case.offline_only for case in cases)
    categories = {case.category for case in cases}
    assert len(categories) >= 6
    assert {"普通问答", "只读代码搜索", "多轮工具调用", "文件修改与验证", "权限拒绝后调整", "上下文或长任务", "Skill 或子 Agent"}.issubset(categories)
    write_case = next(case for case in cases if case.id == "write_and_verify")
    assert write_case.setup_files
    assert write_case.expectations.expected_files
    permission_case = next(case for case in cases if case.id == "permission_recovery")
    assert permission_case.expectations.require_permission_denial is True


def test_online_cases_cover_required_scenarios() -> None:
    cases = load_cases(ROOT / "eval/cases/online")

    assert len(cases) >= 30
    assert all(case.online_only for case in cases)
    tags = {tag for case in cases for tag in case.tags}
    assert {
        "code_reading",
        "file_modification",
        "test_fix",
        "permission_denial",
        "context_compaction",
        "skill",
        "subagent",
        "command_failure_recovery",
        "plan_mode",
        "session_continuity",
        "prompt_cache",
        "multi_file_task",
    }.issubset(tags)
    assert any(case.expectations.expected_files for case in cases)
    assert any(case.expectations.verification_commands for case in cases)


def test_readonly_location_cases_forbid_run_command_but_verification_allows_it() -> None:
    offline_cases = {case.id: case for case in load_cases(ROOT / "eval/cases/offline")}
    online_cases = {case.id: case for case in load_cases(ROOT / "eval/cases/online")}

    for case_id in ("readonly_search", "multi_tool_loop", "code_location_reliability"):
        assert "run_command" in offline_cases[case_id].expectations.forbidden_tools
    for case_id in (
        "online_basic_project_summary",
        "online_find_agent_runner",
        "online_trace_tool_scheduler",
        "online_read_permission_rules",
        "online_search_then_edit",
        "online_unknown_file_recovery",
    ):
        assert "run_command" in online_cases[case_id].expectations.forbidden_tools

    assert "run_command" not in online_cases["online_write_small_function"].expectations.forbidden_tools
    assert "run_command" not in online_cases["online_fix_failing_test"].expectations.forbidden_tools


async def _messages_for(provider: ScriptedEvalProvider, messages: list[ChatMessage]):
    return [event async for event in provider.stream_chat(ChatRequest(messages=messages))]


@pytest.mark.asyncio
async def test_scripted_provider_basic_qa_returns_text_and_usage() -> None:
    provider = ScriptedEvalProvider()

    events = await _messages_for(provider, [ChatMessage(role="user", content="EVAL_CASE:basic_qa")])

    assert [event.type for event in events] == ["usage", "text_delta", "message_done"]
    assert events[0].usage is not None
    assert "JulyCode" in events[-1].message.content  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_scripted_provider_readonly_then_final() -> None:
    provider = ScriptedEvalProvider()

    first = await _messages_for(provider, [ChatMessage(role="user", content="EVAL_CASE:readonly_search")])
    assert first[-1].message.tool_calls[0].name == "read_file"  # type: ignore[union-attr]
    second = await _messages_for(
        provider,
        [
            ChatMessage(role="user", content="EVAL_CASE:readonly_search"),
            ChatMessage(role="tool", content=json.dumps({"tool_name": "read_file", "success": True})),
        ],
    )
    assert "README" in second[-1].message.content  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_scripted_provider_code_location_uses_search_then_partial_read() -> None:
    provider = ScriptedEvalProvider()
    user = ChatMessage(role="user", content="EVAL_CASE:code_location_reliability")

    first = await _messages_for(provider, [user])
    first_call = first[-1].message.tool_calls[0]  # type: ignore[union-attr]
    assert first_call.name == "search_code"
    assert first_call.arguments["path"] == "src/julycode/tools/builtin.py"

    second = await _messages_for(
        provider,
        [
            user,
            ChatMessage(
                role="tool",
                content=json.dumps({"tool_name": "search_code", "success": True}),
            ),
        ],
    )
    second_call = second[-1].message.tool_calls[0]  # type: ignore[union-attr]
    assert second_call.name == "read_file"
    assert second_call.arguments["offset"] == 1
    assert second_call.arguments["limit"] == 8

    third = await _messages_for(
        provider,
        [
            user,
            ChatMessage(
                role="tool",
                content=json.dumps({"tool_name": "search_code", "success": True}),
            ),
            ChatMessage(
                role="tool",
                content=json.dumps({"tool_name": "read_file", "success": True}),
            ),
        ],
    )
    assert "回退" in third[-1].message.content  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_scripted_provider_dangerous_command_and_determinism() -> None:
    provider = ScriptedEvalProvider()

    first = await _messages_for(provider, [ChatMessage(role="user", content="EVAL_CASE:permission_recovery")])
    second = await _messages_for(provider, [ChatMessage(role="user", content="EVAL_CASE:permission_recovery")])

    assert first[-1].message.tool_calls[0].name == "run_command"  # type: ignore[union-attr]
    assert first[-1].message.tool_calls[0].arguments == second[-1].message.tool_calls[0].arguments  # type: ignore[union-attr]


def _metrics_for_scoring() -> tuple[EvalMetric, ...]:
    return (
        EvalMetric("task_completion", "任务完成度", "完成", 0, 5, 1, ("最终回复",)),
        EvalMetric("tool_use", "工具使用合理性", "工具", 0, 5, 1, ("工具",)),
        EvalMetric("safety", "安全与权限遵守", "安全", 0, 5, 1, ("权限",)),
        EvalMetric("efficiency", "效率与成本", "效率", 0, 5, 1, ("usage",)),
        EvalMetric("ux", "交互体验", "体验", 0, 5, 1, ("人工",), manual_review=True),
    )


def test_score_case_and_metric_score_success() -> None:
    case = EvalCase(
        "readonly",
        "只读",
        "只读代码搜索",
        "hi",
        expectations=EvalExpectations(final_contains=("完成",), required_tools=("read_file",), min_tool_successes=1, require_usage=True),
    )
    trace = EvalRunTrace(
        events=(),
        final_message="已经完成",
        stop_reason="completed",
        tool_calls=(EvalToolCallSummary("c1", "read_file", {"path": "README.md"}),),
        tool_results=(EvalToolResultSummary("c1", "read_file", True),),
        usage=EvalUsageSummary(1, 2, 3, "scripted"),
        elapsed_ms=1,
    )

    scores = score_case(case, _metrics_for_scoring(), trace)

    assert any(score.metric_id == "task_completion" and score.status == "pass" for score in scores)
    assert any(score.metric_id == "ux" and score.status == "pass" for score in scores)
    assert total_score(scores) > 80
    assert case_status(scores, trace, 80) == "pass"


def test_ux_metric_only_reviews_anomaly_or_sample() -> None:
    metric = EvalMetric("ux", "交互体验", "体验", 0, 5, 1, ("人工",), manual_review=True)
    case = EvalCase("ux", "交互体验", "普通问答", "hi")
    normal_trace = EvalRunTrace((), "已经完成", "completed", (), (), None, 1)
    abnormal_trace = EvalRunTrace((), "done", "completed", (), (), None, 1)

    automatic = score_case(case, (metric,), normal_trace)[0]
    sampled = score_case(case, (metric,), normal_trace, review_sampled=True)[0]
    abnormal = score_case(case, (metric,), abnormal_trace)[0]

    assert automatic.status == "pass"
    assert "自动通过" in automatic.evidence[0]
    assert sampled.status == "needs_review"
    assert "抽样" in sampled.evidence[0]
    assert abnormal.status == "needs_review"
    assert "最终回复不含中文" in abnormal.evidence[0]


def test_score_case_missing_required_tool_fails() -> None:
    case = EvalCase("missing", "缺工具", "只读代码搜索", "hi", expectations=EvalExpectations(required_tools=("read_file",)))
    trace = EvalRunTrace((), "完成", "completed", (), (), EvalUsageSummary(1, 1, 2, "scripted"), 1)

    scores = score_case(case, _metrics_for_scoring(), trace)
    tool_score = next(score for score in scores if score.metric_id == "tool_use")

    assert tool_score.status == "fail"
    assert any("缺少必需工具" in item for item in tool_score.evidence)


def test_score_case_tool_use_forbidden_tool_fails() -> None:
    case = EvalCase(
        "forbidden",
        "禁用工具",
        "只读代码搜索",
        "hi",
        expectations=EvalExpectations(forbidden_tools=("run_command",)),
    )
    trace = EvalRunTrace(
        (),
        "完成",
        "completed",
        (EvalToolCallSummary("c1", "run_command", {"command": "grep x a.py"}),),
        (EvalToolResultSummary("c1", "run_command", True),),
        EvalUsageSummary(1, 1, 2, "scripted"),
        1,
    )

    scores = score_case(case, _metrics_for_scoring(), trace)
    tool_score = next(score for score in scores if score.metric_id == "tool_use")

    assert tool_score.status == "fail"
    assert any("调用了禁用工具: run_command" in item for item in tool_score.evidence)


def test_score_case_file_permission_and_context_branches(tmp_path: Path) -> None:
    case = EvalCase(
        "branches",
        "分支",
        "文件修改与验证",
        "hi",
        expectations=EvalExpectations(
            expected_files=(EvalFileExpectation("app.py", contains=("ok",)),),
            require_permission_denial=True,
            require_context_compaction=True,
        ),
    )
    (tmp_path / "app.py").write_text("missing", encoding="utf-8")
    metrics = (
        EvalMetric("change_quality", "代码或文件修改质量", "文件", 0, 5, 1, ("文件",)),
        EvalMetric("safety", "安全与权限遵守", "权限", 0, 5, 1, ("权限",)),
        EvalMetric("context_continuity", "上下文/记忆连续性", "上下文", 0, 5, 1, ("上下文",)),
    )
    trace = EvalRunTrace((), "完成", "completed", (), (), None, 1)

    scores = score_case(case, metrics, trace, workspace=tmp_path)

    assert [score.status for score in scores] == ["fail", "fail", "fail"]


@pytest.mark.asyncio
async def test_run_case_basic_qa_uses_real_runner() -> None:
    metrics = load_metrics(ROOT / "eval/metrics/default_metrics.json")
    case = next(case for case in load_cases(ROOT / "eval/cases/offline") if case.id == "basic_qa")

    result = await run_case(case, metrics, offline_options(allow_review=True))

    assert result.status == "pass"
    assert "JulyCode" in result.trace.final_message
    assert result.trace.usage is not None
    assert any(event.type == "message_done" for event in result.trace.events)


@pytest.mark.asyncio
async def test_run_case_code_location_reliability_passes_without_command() -> None:
    metrics = load_metrics(ROOT / "eval/metrics/default_metrics.json")
    case = next(
        case
        for case in load_cases(ROOT / "eval/cases/offline")
        if case.id == "code_location_reliability"
    )

    result = await run_case(case, metrics, offline_options(allow_review=True))

    assert result.status == "pass"
    assert [call.name for call in result.trace.tool_calls] == ["search_code", "read_file"]
    assert result.trace.tool_calls[1].arguments["offset"] == 1
    assert result.trace.tool_calls[1].arguments["limit"] == 8
    assert "run_command" not in [call.name for call in result.trace.tool_calls]


@pytest.mark.asyncio
async def test_run_case_write_and_verify_does_not_pollute_project_root() -> None:
    metrics = load_metrics(ROOT / "eval/metrics/default_metrics.json")
    case = next(case for case in load_cases(ROOT / "eval/cases/offline") if case.id == "write_and_verify")
    project_target = ROOT / "app.py"
    before_exists = project_target.exists()

    result = await run_case(case, metrics, offline_options(allow_review=True))

    assert result.status == "pass"
    assert [call.name for call in result.trace.tool_calls] == ["read_file", "write_file", "run_command"]
    assert project_target.exists() is before_exists


@pytest.mark.asyncio
async def test_run_case_permission_recovery_records_denial() -> None:
    metrics = load_metrics(ROOT / "eval/metrics/default_metrics.json")
    case = next(case for case in load_cases(ROOT / "eval/cases/offline") if case.id == "permission_recovery")

    result = await run_case(case, metrics, offline_options(allow_review=True))

    assert result.status == "pass"
    assert any(item.error_type == "permission_dangerous_command" for item in result.trace.tool_results)
    assert "安全替代" in result.trace.final_message


@pytest.mark.asyncio
async def test_run_suite_summary_and_metric_averages() -> None:
    metrics = load_metrics(ROOT / "eval/metrics/default_metrics.json")
    cases = tuple(case for case in load_cases(ROOT / "eval/cases/offline") if case.id in {"basic_qa", "readonly_search"})

    result = await run_suite(cases, metrics, offline_options(allow_review=True))

    assert result.summary.total_cases == 2
    assert result.provider.mode == "offline"
    assert result.summary.passed == 2
    assert result.summary.needs_review == 0
    assert result.metric_averages["task_completion"] == 100


@pytest.mark.asyncio
async def test_run_case_online_provider_records_cache_usage() -> None:
    metrics = load_metrics(ROOT / "eval/metrics/default_metrics.json")
    case = EvalCase(
        "online_fake",
        "在线 fake",
        "普通问答",
        "请回答在线评测完成",
        expectations=EvalExpectations(final_contains=("在线评测完成",), require_usage=True),
    )
    provider = FakeOnlineProvider()
    result = await run_case(
        case,
        metrics,
        EvalRunOptions(
            mode="online",
            suite_id="online",
            allow_review=True,
            review_sample_rate=0.0,
            provider=provider,
            provider_info=EvalProviderInfo("online", "openai", "fake-model", "fake-online", True),
        ),
    )

    assert result.status == "pass"
    assert provider.requests
    assert result.trace.usage is not None
    assert result.trace.usage.cache_status == "hit"
    assert result.trace.usage.cached_tokens == 8


@pytest.mark.asyncio
async def test_run_case_online_without_provider_returns_error() -> None:
    metrics = load_metrics(ROOT / "eval/metrics/default_metrics.json")
    case = EvalCase("online_missing_provider", "缺 Provider", "普通问答", "hi")

    result = await run_case(case, metrics, EvalRunOptions(mode="online"))

    assert result.status == "error"
    assert result.trace.errors


@pytest.mark.asyncio
async def test_report_writes_json_and_markdown_without_secrets(tmp_path: Path) -> None:
    metrics = load_metrics(ROOT / "eval/metrics/default_metrics.json")
    cases = tuple(case for case in load_cases(ROOT / "eval/cases/offline") if case.id in {"basic_qa", "permission_recovery"})
    result = await run_suite(cases, metrics, offline_options(allow_review=True))

    json_path = tmp_path / "results.json"
    md_path = tmp_path / "report.md"
    write_json_report(result, json_path)
    write_markdown_report(result, md_path)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = md_path.read_text(encoding="utf-8")
    assert data["provider"]["mode"] == "offline"
    assert data["provider"]["provider"] == "scripted-eval"
    assert data["summary"]["total_cases"] == 2
    assert data["results"][0]["metric_scores"]
    assert data["results"][0]["trace"]["events"]
    assert "运行环境" in markdown
    assert "Prompt cache" in markdown
    assert "总体摘要" in markdown
    assert "维度均分" in markdown
    assert "人工复核项" in markdown
    assert "关键证据" in markdown
    assert "api_key" not in json.dumps(data).lower()
    assert "api_key" not in markdown.lower()


def test_run_eval_cli_success_with_allow_review(tmp_path: Path) -> None:
    output = tmp_path / "eval-out"

    completed = subprocess.run(
        [
            sys.executable,
            "eval/run_eval.py",
            "--mode",
            "offline",
            "--metrics",
            "eval/metrics/default_metrics.json",
            "--output",
            str(output),
            "--allow-review",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "评测完成" in completed.stdout
    assert (output / "results.json").exists()
    assert (output / "report.md").exists()


def test_run_eval_cli_returns_failure_for_unallowed_review(tmp_path: Path) -> None:
    output = tmp_path / "eval-out"

    completed = subprocess.run(
        [
            sys.executable,
            "eval/run_eval.py",
            "--mode",
            "offline",
            "--case",
            "basic_qa",
            "--review-sample-rate",
            "1",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert (output / "results.json").exists()
    assert (output / "report.md").exists()


def test_run_eval_cli_rejects_invalid_review_sample_rate(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "eval/run_eval.py",
            "--offline",
            "--review-sample-rate",
            "1.1",
            "--output",
            str(tmp_path / "eval-out"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "必须在 0 到 1 之间" in completed.stderr


def test_run_eval_cli_default_online_reports_config_error(tmp_path: Path) -> None:
    output = tmp_path / "eval-out"
    home = tmp_path / "empty-home"
    home.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            "eval/run_eval.py",
            "--output",
            str(output),
            "--allow-review",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "HOME": str(home)},
    )

    assert completed.returncode == 2
    assert "在线评测配置错误" in completed.stderr


def test_run_eval_cli_offline_shortcut(tmp_path: Path) -> None:
    output = tmp_path / "eval-out"

    completed = subprocess.run(
        [
            sys.executable,
            "eval/run_eval.py",
            "--offline",
            "--output",
            str(output),
            "--allow-review",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    data = json.loads((output / "results.json").read_text(encoding="utf-8"))
    assert data["provider"]["mode"] == "offline"
    assert data["summary"]["total_cases"] == 8


def test_repo_map_navigation_loader_validates_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "navigation.json"
    path.write_text(
        json.dumps(
            {
                "version": "test-v1",
                "cases": [
                    {
                        "id": "service-entry",
                        "request": "start_service 是如何启动服务的？",
                        "target_file": "pkg/service.py",
                        "top_k": 3,
                        "tags": ["entrypoint"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    dataset = NavigationDatasetLoader().load(path)
    restored = json.loads(json.dumps(asdict(dataset), ensure_ascii=False))

    assert dataset.version == "test-v1"
    assert dataset.cases[0] == NavigationCase(
        "service-entry",
        "start_service 是如何启动服务的？",
        "pkg/service.py",
        3,
        ("entrypoint",),
    )
    assert restored["cases"][0]["target_file"] == "pkg/service.py"


@pytest.mark.parametrize(
    "prompt_text,target",
    [
        ("请读取 pkg/service.py", "pkg/service.py"),
        ("请读取 service.py", "pkg/service.py"),
    ],
)
def test_repo_map_navigation_loader_rejects_target_leak(
    tmp_path: Path,
    prompt_text: str,
    target: str,
) -> None:
    path = tmp_path / "navigation.json"
    path.write_text(
        json.dumps(
            {
                "version": "test-v1",
                "cases": [{"id": "bad", "request": prompt_text, "target_file": target}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RepoMapQualityConfigError, match="泄露"):
        NavigationDatasetLoader().load(path)


def test_default_repo_map_navigation_dataset_covers_core_modules() -> None:
    dataset = NavigationDatasetLoader().load(
        ROOT / "eval/cases/repo_map_quality/navigation.json"
    )

    assert len(dataset.cases) >= 8
    tags = {tag for case in dataset.cases for tag in case.tags}
    assert {"entrypoint", "context", "provider", "tools", "session", "repo-map"}.issubset(tags)
    assert all(case.target_file not in case.request for case in dataset.cases)


@pytest.mark.asyncio
async def test_repo_map_quality_offline_runner_and_reports(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "service.py").write_text("def start_service(): pass\n", encoding="utf-8")
    (package / "other.py").write_text("def unrelated(): pass\n", encoding="utf-8")
    dataset = NavigationDataset(
        version="test-v1",
        cases=(NavigationCase("service-entry", "start_service 如何启动服务？", "pkg/service.py", 1),),
    )

    report = await RepoMapQualityRunner().run(
        dataset,
        RepoMapQualityRunOptions(mode="offline", root=tmp_path, map_budget=2000),
    )
    json_path = tmp_path / "out/results.json"
    markdown_path = tmp_path / "out/report.md"
    write_repo_map_json_report(report, json_path)
    write_repo_map_markdown_report(report, markdown_path)

    assert report.summary.case_count == 1
    assert report.summary.disabled_top_k_hit_rate == 0.0
    assert report.summary.enabled_top_k_hit_rate == 1.0
    assert report.results[0].enabled.top_files == ("pkg/service.py",)
    assert json.loads(json_path.read_text(encoding="utf-8"))["mode"] == "offline"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Top-K 命中率" in markdown
    assert "不是 CI 通过阈值" in markdown


def test_repo_map_quality_report_contains_paired_metrics(tmp_path: Path) -> None:
    report = RepoMapQualityReport(
        dataset_version="test-v1",
        mode="paired",
        root="/repo",
        started_at="2026-07-16T00:00:00+00:00",
        results=(
            NavigationCaseResult(
                case_id="case-1",
                target_file="pkg/service.py",
                top_k=5,
                disabled=NavigationTrial(
                    enabled=False,
                    target_hit=False,
                    target_read=True,
                    exploration_calls=4,
                ),
                enabled=NavigationTrial(
                    enabled=True,
                    target_hit=True,
                    top_files=("pkg/service.py",),
                    target_read=True,
                    exploration_calls=1,
                ),
            ),
        ),
        summary=NavigationSummary(
            case_count=1,
            disabled_top_k_hit_rate=0.0,
            enabled_top_k_hit_rate=1.0,
            disabled_average_exploration_calls=4.0,
            enabled_average_exploration_calls=1.0,
        ),
    )
    json_path = tmp_path / "results.json"
    markdown_path = tmp_path / "report.md"

    write_repo_map_json_report(report, json_path)
    write_repo_map_markdown_report(report, markdown_path)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert data["summary"]["disabled_average_exploration_calls"] == 4.0
    assert data["summary"]["enabled_average_exploration_calls"] == 1.0
    assert "目标文件 Top-K 命中率" in markdown
    assert "4.00" in markdown and "1.00" in markdown
    assert "不是 CI 通过阈值" in markdown


def test_repo_map_eval_cli_exposes_offline_and_paired_modes() -> None:
    completed = subprocess.run(
        [sys.executable, "eval/run_repo_map_eval.py", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "offline" in completed.stdout
    assert "paired" in completed.stdout
    assert "--top-k" in completed.stdout
