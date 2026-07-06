from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "eval"
if str(EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(EVAL_ROOT))

from memory_quality.matching import ExtractionMatcher, aggregate_extraction_metrics
from memory_quality.models import ExpectedMemory, ExtractionCase
from mewcode.memory.models import MemoryExtractionResult, ValidatedMemoryOperation
from mewcode.providers.base import ChatMessage
from tests.test_memory_notes import note


def expected(*, key: str = "language", scope: str = "user", category: str = "preference", critical: bool = True) -> ExpectedMemory:
    return ExpectedMemory(
        key=key,
        scope=scope,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        critical=critical,
        evidence=("以后始终用中文",),
        content_term_groups=(("中文", "Chinese"),),
    )


def case(*items: ExpectedMemory) -> ExtractionCase:
    return ExtractionCase(
        case_id="case",
        tags=(),
        messages=(ChatMessage(role="user", content="以后始终用中文"),),
        expected=items,
    )


def prediction(*, note_id: str = "language", scope: str = "user", category: str = "preference", critical: bool = True):
    item = note(note_id, scope=scope, category=category, body="默认使用中文", critical=critical, evidence=("以后始终用中文",))
    return ValidatedMemoryOperation(action="create", note=item)


def result(*operations: ValidatedMemoryOperation) -> MemoryExtractionResult:
    return MemoryExtractionResult(accepted=operations)


def test_full_match_metrics_are_one() -> None:
    item_case = case(expected())
    matched = ExtractionMatcher().match(item_case, result(prediction()))

    metrics = aggregate_extraction_metrics((item_case,), (matched,))

    assert metrics.precision == metrics.recall == metrics.f1 == 1.0
    assert metrics.critical_precision == metrics.critical_recall == 1.0


def test_all_missed_and_zero_prediction_are_zero() -> None:
    item_case = case(expected())
    matched = ExtractionMatcher().match(item_case, result())

    metrics = aggregate_extraction_metrics((item_case,), (matched,))

    assert metrics.tp == 0
    assert metrics.fn == 1
    assert metrics.precision == metrics.f1 == 0.0


def test_duplicate_prediction_counts_as_false_positive() -> None:
    item_case = case(expected())
    matched = ExtractionMatcher().match(item_case, result(prediction(note_id="one"), prediction(note_id="two")))

    metrics = aggregate_extraction_metrics((item_case,), (matched,))

    assert metrics.tp == 1
    assert metrics.fp == 1
    assert metrics.critical_fp == 1


def test_wrong_scope_category_or_critical_creates_fp_and_fn() -> None:
    item_case = case(expected())
    for operation in (
        prediction(scope="project"),
        prediction(category="correction"),
        prediction(critical=False),
    ):
        matched = ExtractionMatcher().match(item_case, result(operation))
        metrics = aggregate_extraction_metrics((item_case,), (matched,))
        assert metrics.tp == 0
        assert metrics.fp == 1
        assert metrics.fn == 1


def test_matching_is_stable_for_reordered_predictions() -> None:
    item_case = case(expected(key="one"), expected(key="two"))
    first = ExtractionMatcher().match(item_case, result(prediction(note_id="one"), prediction(note_id="two")))
    second = ExtractionMatcher().match(item_case, result(prediction(note_id="two"), prediction(note_id="one")))

    first_metrics = aggregate_extraction_metrics((item_case,), (first,))
    second_metrics = aggregate_extraction_metrics((item_case,), (second,))

    assert first_metrics == second_metrics
    assert first_metrics.tp == 2


def test_critical_metrics_are_independent() -> None:
    ordinary = ExpectedMemory(
        key="framework",
        scope="project",
        category="project_knowledge",
        critical=False,
        evidence=("项目使用 pytest",),
        content_term_groups=(("pytest",),),
    )
    item_case = ExtractionCase(
        case_id="mixed",
        tags=(),
        messages=(ChatMessage(role="user", content="以后始终用中文；项目使用 pytest"),),
        expected=(expected(), ordinary),
    )
    matched = ExtractionMatcher().match(item_case, result(prediction()))

    metrics = aggregate_extraction_metrics((item_case,), (matched,))

    assert metrics.tp == 1
    assert metrics.fn == 1
    assert metrics.critical_tp == 1
    assert metrics.critical_fn == 0
    assert metrics.critical_recall == 1.0

