from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from mewcode.commands import AgentMode

if TYPE_CHECKING:
    from mewcode.providers.base import LLMProvider


EvalStatus = Literal["pass", "fail", "error", "needs_review"]
EvalRunMode = Literal["online", "offline"]


@dataclass(frozen=True)
class EvalMetric:
    id: str
    name: str
    description: str
    scale_min: int
    scale_max: int
    weight: float
    evidence: tuple[str, ...]
    manual_review: bool = False


@dataclass(frozen=True)
class EvalProviderInfo:
    mode: EvalRunMode
    protocol: str | None = None
    model: str | None = None
    provider: str | None = None
    prompt_cache_enabled: bool | None = None


@dataclass(frozen=True)
class EvalFile:
    path: str
    content: str


@dataclass(frozen=True)
class EvalFileExpectation:
    path: str
    contains: tuple[str, ...] = ()
    exact: str | None = None
    must_exist: bool = True


@dataclass(frozen=True)
class EvalExpectations:
    final_contains: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    expected_files: tuple[EvalFileExpectation, ...] = ()
    expected_stop_reason: str | None = "completed"
    min_tool_successes: int = 0
    require_permission_denial: bool = False
    require_context_compaction: bool = False
    require_usage: bool = False
    verification_commands: tuple[str, ...] = ()
    max_tool_calls: int | None = None
    require_chinese: bool = True


@dataclass(frozen=True)
class EvalCase:
    id: str
    title: str
    category: str
    prompt: str
    mode: AgentMode = "normal"
    setup_files: tuple[EvalFile, ...] = ()
    permission_mode: str = "permissive"
    max_iterations: int = 8
    expectations: EvalExpectations = field(default_factory=EvalExpectations)
    metric_weights: dict[str, float] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    online_only: bool = False
    offline_only: bool = False


@dataclass(frozen=True)
class EvalEventSummary:
    type: str
    detail: str = ""


@dataclass(frozen=True)
class EvalToolCallSummary:
    id: str
    name: str
    arguments: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalToolResultSummary:
    call_id: str
    tool_name: str
    success: bool
    error_type: str | None = None
    error: str | None = None
    data_preview: str = ""


@dataclass(frozen=True)
class EvalUsageSummary:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    provider: str | None = None
    cache_status: str | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cached_tokens: int | None = None


@dataclass(frozen=True)
class EvalRunTrace:
    events: tuple[EvalEventSummary, ...]
    final_message: str
    stop_reason: str | None
    tool_calls: tuple[EvalToolCallSummary, ...]
    tool_results: tuple[EvalToolResultSummary, ...]
    usage: EvalUsageSummary | None
    elapsed_ms: int
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetricScore:
    metric_id: str
    score: float
    max_score: float
    weight: float
    status: EvalStatus
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    title: str
    status: EvalStatus
    total_score: float
    threshold: float
    metric_scores: tuple[MetricScore, ...]
    trace: EvalRunTrace


@dataclass(frozen=True)
class EvalSummary:
    total_cases: int
    passed: int
    failed: int
    errors: int
    needs_review: int
    average_score: float
    threshold: float


@dataclass(frozen=True)
class EvalSuiteResult:
    suite_id: str
    started_at: str
    elapsed_ms: int
    provider: EvalProviderInfo
    results: tuple[EvalCaseResult, ...]
    metric_averages: dict[str, float]
    summary: EvalSummary


@dataclass(frozen=True)
class EvalRunOptions:
    suite_id: str = "online"
    mode: EvalRunMode = "online"
    threshold: float = 80.0
    allow_review: bool = False
    keep_workspaces: bool = False
    workspace_root: Path | None = None
    provider: LLMProvider | None = None
    provider_info: EvalProviderInfo | None = None
