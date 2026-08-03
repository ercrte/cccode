from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from july_eval.models import EvalProviderInfo
from julycode.memory.models import MemoryCategory, MemoryNote, MemoryRejection, MemoryScope
from julycode.providers.base import ChatMessage

if TYPE_CHECKING:
    from julycode.providers.base import LLMProvider


MemoryQualityMode = Literal["offline", "online"]


@dataclass(frozen=True)
class ExpectedMemory:
    key: str
    scope: MemoryScope
    category: MemoryCategory
    critical: bool
    evidence: tuple[str, ...]
    content_term_groups: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ExtractionCase:
    case_id: str
    tags: tuple[str, ...]
    messages: tuple[ChatMessage, ...]
    expected: tuple[ExpectedMemory, ...]


@dataclass(frozen=True)
class InheritanceExpectation:
    required_term_groups: tuple[tuple[str, ...], ...]
    forbidden_terms: tuple[str, ...] = ()
    restatement_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class InheritanceCase:
    case_id: str
    tags: tuple[str, ...]
    source_prompt: str
    source_expected: tuple[ExpectedMemory, ...]
    target_prompt: str
    expectation: InheritanceExpectation


@dataclass(frozen=True)
class MemoryQualityDataset:
    version: str
    extraction_cases: tuple[ExtractionCase, ...]
    inheritance_cases: tuple[InheritanceCase, ...]


@dataclass(frozen=True)
class ExtractionMatch:
    expected_key: str
    predicted_note_id: str
    evidence: str


@dataclass(frozen=True)
class ExtractionCaseResult:
    case_id: str
    matches: tuple[ExtractionMatch, ...]
    false_positives: tuple[MemoryNote, ...]
    false_negatives: tuple[ExpectedMemory, ...]
    rejections: tuple[MemoryRejection, ...]


@dataclass(frozen=True)
class ExtractionMetrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    critical_tp: int = 0
    critical_fp: int = 0
    critical_fn: int = 0
    critical_precision: float = 0.0
    critical_recall: float = 0.0


@dataclass(frozen=True)
class InheritanceTrial:
    memory_enabled: bool
    final_text: str
    first_turn_correct: bool
    requested_restatement: bool
    session_started_empty: bool
    injected_user_memory: bool
    injected_project_memory: bool
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class InheritanceCaseResult:
    case_id: str
    baseline: InheritanceTrial
    enabled: InheritanceTrial


@dataclass(frozen=True)
class MemoryQualityReport:
    dataset_version: str
    mode: MemoryQualityMode
    provider: EvalProviderInfo
    started_at: str
    extraction_results: tuple[ExtractionCaseResult, ...]
    extraction_metrics: ExtractionMetrics
    inheritance_results: tuple[InheritanceCaseResult, ...]
    first_turn_accuracy: float
    baseline_restatements: int
    enabled_restatements: int
    restatement_reduction: float | None
    acceptance_passed: bool
    acceptance_failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryQualityRunOptions:
    mode: MemoryQualityMode = "offline"
    provider: LLMProvider | None = None
    provider_info: EvalProviderInfo = field(
        default_factory=lambda: EvalProviderInfo(
            mode="offline",
            protocol="offline",
            model="scripted",
            provider="scripted-memory-quality",
            prompt_cache_enabled=False,
        )
    )
    workspace_root: Path | None = None

