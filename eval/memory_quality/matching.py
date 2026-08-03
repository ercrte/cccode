from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from memory_quality.models import (
    ExpectedMemory,
    ExtractionCase,
    ExtractionCaseResult,
    ExtractionMatch,
    ExtractionMetrics,
)
from julycode.memory.models import MemoryExtractionResult, MemoryNote


_SPACE_RE = re.compile(r"\s+")


class ExtractionMatcher:
    def match(self, case: ExtractionCase, result: MemoryExtractionResult) -> ExtractionCaseResult:
        predictions = tuple(operation.note for operation in result.accepted)
        edges = {
            prediction_index: tuple(
                expected_index
                for expected_index, expected in enumerate(case.expected)
                if _matches(prediction, expected)
            )
            for prediction_index, prediction in enumerate(predictions)
        }
        expected_owner: dict[int, int] = {}
        for prediction_index in range(len(predictions)):
            _augment(prediction_index, edges, expected_owner, set())
        prediction_to_expected = {prediction: expected for expected, prediction in expected_owner.items()}
        matches = tuple(
            ExtractionMatch(
                expected_key=case.expected[expected_index].key,
                predicted_note_id=predictions[prediction_index].note_id,
                evidence=" | ".join(predictions[prediction_index].source_evidence),
            )
            for prediction_index, expected_index in sorted(prediction_to_expected.items())
        )
        false_positives = tuple(
            prediction
            for index, prediction in enumerate(predictions)
            if index not in prediction_to_expected
        )
        matched_expected = set(expected_owner)
        false_negatives = tuple(
            expected
            for index, expected in enumerate(case.expected)
            if index not in matched_expected
        )
        return ExtractionCaseResult(
            case_id=case.case_id,
            matches=matches,
            false_positives=false_positives,
            false_negatives=false_negatives,
            rejections=result.rejected,
        )


def aggregate_extraction_metrics(
    cases: Sequence[ExtractionCase],
    results: Sequence[ExtractionCaseResult],
) -> ExtractionMetrics:
    case_by_id = {case.case_id: case for case in cases}
    tp = sum(len(result.matches) for result in results)
    fp = sum(len(result.false_positives) for result in results)
    fn = sum(len(result.false_negatives) for result in results)
    critical_tp = 0
    for result in results:
        expected_by_key = {item.key: item for item in case_by_id[result.case_id].expected}
        critical_tp += sum(1 for match in result.matches if expected_by_key[match.expected_key].critical)
    critical_fp = sum(
        1
        for result in results
        for note in result.false_positives
        if note.critical
    )
    critical_fn = sum(
        1
        for result in results
        for expected in result.false_negatives
        if expected.critical
    )
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    critical_precision = _ratio(critical_tp, critical_tp + critical_fp)
    critical_recall = _ratio(critical_tp, critical_tp + critical_fn)
    return ExtractionMetrics(
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        critical_tp=critical_tp,
        critical_fp=critical_fp,
        critical_fn=critical_fn,
        critical_precision=critical_precision,
        critical_recall=critical_recall,
    )


def _matches(note: MemoryNote, expected: ExpectedMemory) -> bool:
    if note.scope != expected.scope or note.category != expected.category or note.critical != expected.critical:
        return False
    predicted_evidence = tuple(_normalize(item) for item in note.source_evidence)
    if not all(
        any(_contains_either(_normalize(quote), predicted) for predicted in predicted_evidence)
        for quote in expected.evidence
    ):
        return False
    content = _normalize(" ".join((note.title, note.body, *note.source_evidence)))
    return all(any(_normalize(term) in content for term in group) for group in expected.content_term_groups)


def _augment(
    prediction_index: int,
    edges: dict[int, tuple[int, ...]],
    expected_owner: dict[int, int],
    visited: set[int],
) -> bool:
    for expected_index in edges[prediction_index]:
        if expected_index in visited:
            continue
        visited.add(expected_index)
        owner = expected_owner.get(expected_index)
        if owner is None or _augment(owner, edges, expected_owner, visited):
            expected_owner[expected_index] = prediction_index
            return True
    return False


def _contains_either(left: str, right: str) -> bool:
    return left in right or right in left


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.category(char).startswith("P"))
    return _SPACE_RE.sub(" ", normalized).strip()


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0

