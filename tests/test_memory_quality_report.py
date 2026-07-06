from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "eval"
if str(EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(EVAL_ROOT))

from memory_quality.models import ExtractionMetrics, MemoryQualityReport
from memory_quality.report import acceptance_failures, write_json_report, write_markdown_report
from mew_eval.models import EvalProviderInfo


def passing_metrics() -> ExtractionMetrics:
    return ExtractionMetrics(
        tp=80,
        fp=1,
        fn=5,
        precision=80 / 81,
        recall=80 / 85,
        f1=0.96,
        critical_tp=49,
        critical_fp=1,
        critical_fn=1,
        critical_precision=0.98,
        critical_recall=0.98,
    )


def report(*, mode: str = "online", failures: tuple[str, ...] = ()) -> MemoryQualityReport:
    return MemoryQualityReport(
        dataset_version="1.0",
        mode=mode,  # type: ignore[arg-type]
        provider=EvalProviderInfo(mode=mode, protocol="openai", model="test", provider="openai"),  # type: ignore[arg-type]
        started_at="2026-07-06T00:00:00Z",
        extraction_results=(),
        extraction_metrics=passing_metrics(),
        inheritance_results=(),
        first_turn_accuracy=0.95,
        baseline_restatements=20,
        enabled_restatements=2,
        restatement_reduction=0.9,
        acceptance_passed=not failures,
        acceptance_failures=failures,
    )


def test_json_report_is_machine_readable(tmp_path: Path) -> None:
    path = tmp_path / "results.json"

    write_json_report(report(), path)
    data = json.loads(path.read_text())

    assert data["provider"]["model"] == "test"
    assert data["extraction_metrics"]["f1"] == 0.96


def test_markdown_report_contains_all_metrics_and_offline_notice(tmp_path: Path) -> None:
    path = tmp_path / "report.md"

    write_markdown_report(report(mode="offline"), path)
    text = path.read_text()

    for expected in ("Precision", "Recall", "F1", "首轮任务理解正确率", "背景重复说明减少率"):
        assert expected in text
    assert "不代表真实模型质量" in text


def test_acceptance_thresholds_and_invalid_baseline() -> None:
    assert acceptance_failures(
        passing_metrics(),
        extraction_case_count=120,
        inheritance_case_count=20,
        first_turn_accuracy=0.9,
        baseline_restatements=10,
        restatement_reduction=0.8,
    ) == ()

    failures = acceptance_failures(
        ExtractionMetrics(),
        extraction_case_count=1,
        inheritance_case_count=1,
        first_turn_accuracy=0.0,
        baseline_restatements=0,
        restatement_reduction=None,
    )

    assert any("F1" in item for item in failures)
    assert any("Precision" in item for item in failures)
    assert any("命中" in item for item in failures)
    assert any("无效" in item for item in failures)


def test_report_redacts_sensitive_values(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    unsafe = report(failures=("Bearer abcdefghijklmnop password=hunter22 sk-test-secret-1234567890",))

    write_markdown_report(unsafe, path)
    text = path.read_text()

    assert "abcdefghijklmnop" not in text
    assert "hunter22" not in text
    assert "sk-test-secret" not in text
    assert "[REDACTED]" in text

