from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from memory_quality.models import ExtractionMetrics, MemoryQualityReport
from mewcode.errors import redact_secret


_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*\S{6,}")


def acceptance_failures(
    metrics: ExtractionMetrics,
    *,
    extraction_case_count: int,
    inheritance_case_count: int,
    first_turn_accuracy: float,
    baseline_restatements: int,
    restatement_reduction: float | None,
) -> tuple[str, ...]:
    failures: list[str] = []
    if extraction_case_count < 120:
        failures.append(f"提取用例不足 120：实际 {extraction_case_count}")
    if inheritance_case_count < 20:
        failures.append(f"跨会话用例不足 20：实际 {inheritance_case_count}")
    if metrics.f1 < 0.85:
        failures.append(f"整体 F1 未达到 85%：实际 {metrics.f1:.2%}")
    if metrics.critical_precision < 0.98:
        failures.append(f"关键偏好 Precision 未达到 98%：实际 {metrics.critical_precision:.2%}")
    if metrics.critical_tp < 45:
        failures.append(f"关键偏好命中不足 45：实际 {metrics.critical_tp}")
    if first_turn_accuracy < 0.90:
        failures.append(f"首轮理解正确率未达到 90%：实际 {first_turn_accuracy:.2%}")
    if baseline_restatements <= 0:
        failures.append("关闭记忆基线没有背景重述需求，减少率评测无效")
    if restatement_reduction is None or restatement_reduction < 0.80:
        actual = "无效" if restatement_reduction is None else f"{restatement_reduction:.2%}"
        failures.append(f"背景重复说明减少率未达到 80%：实际 {actual}")
    return tuple(failures)


def write_json_report(report: MemoryQualityReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _redact_value(asdict(report))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown_report(report: MemoryQualityReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = report.extraction_metrics
    lines = [
        "# MewCode 跨会话记忆质量报告",
        "",
        "## 运行信息",
        "",
        f"- 数据集版本：{_safe(report.dataset_version)}",
        f"- 模式：{report.mode}",
        f"- Provider：{_safe(report.provider.provider or 'unknown')}",
        f"- 协议：{_safe(report.provider.protocol or 'unknown')}",
        f"- 模型：{_safe(report.provider.model or 'unknown')}",
        f"- 开始时间：{_safe(report.started_at)}",
        "",
    ]
    if report.mode == "offline":
        lines.extend(
            [
                "> offline/scripted 结果只验证评测流程，不代表真实模型质量。",
                "",
            ]
        )
    lines.extend(
        [
            "## 自动记忆提取",
            "",
            "| 指标 | 数值 |",
            "|---|---:|",
            f"| TP / FP / FN | {metrics.tp} / {metrics.fp} / {metrics.fn} |",
            f"| Precision | {metrics.precision:.2%} |",
            f"| Recall | {metrics.recall:.2%} |",
            f"| F1 | {metrics.f1:.2%} |",
            f"| 关键偏好 TP / FP / FN | {metrics.critical_tp} / {metrics.critical_fp} / {metrics.critical_fn} |",
            f"| 关键偏好 Precision | {metrics.critical_precision:.2%} |",
            f"| 关键偏好 Recall | {metrics.critical_recall:.2%} |",
            "",
            "## 空白新会话继承",
            "",
            f"- 成对用例数：{len(report.inheritance_results)}",
            f"- 首轮任务理解正确率：{report.first_turn_accuracy:.2%}",
            f"- 关闭记忆背景重述次数：{report.baseline_restatements}",
            f"- 开启记忆背景重述次数：{report.enabled_restatements}",
            "- 背景重复说明减少率："
            + (f"{report.restatement_reduction:.2%}" if report.restatement_reduction is not None else "无效"),
            "",
            "## 验收结论",
            "",
            f"- 结果：{'通过' if report.acceptance_passed else '未通过'}",
        ]
    )
    if report.acceptance_failures:
        lines.extend(f"- {_safe(item)}" for item in report.acceptance_failures)
    else:
        lines.append("- 所有量化门槛均满足。")

    lines.extend(["", "## 提取失败明细", ""])
    extraction_failures = [
        result
        for result in report.extraction_results
        if result.false_positives or result.false_negatives or result.rejections
    ]
    if not extraction_failures:
        lines.append("无。")
    for result in extraction_failures:
        lines.append(f"### {_safe(result.case_id)}")
        for note in result.false_positives:
            lines.append(
                f"- FP：scope={note.scope} category={note.category} critical={note.critical} "
                f"预测={_safe(note.title)}：{_safe(note.body)} 证据={_safe(' | '.join(note.source_evidence))}"
            )
        for expected in result.false_negatives:
            lines.append(
                f"- FN：key={_safe(expected.key)} scope={expected.scope} category={expected.category} "
                f"critical={expected.critical} 期望证据={_safe(' | '.join(expected.evidence))}"
            )
        for rejection in result.rejections:
            lines.append(
                f"- 拒绝：code={_safe(rejection.code)} reason={_safe(rejection.message)} "
                f"候选={_safe(rejection.candidate.title)}：{_safe(rejection.candidate.body)}"
            )
        lines.append("")

    lines.extend(["## 跨会话失败明细", ""])
    inheritance_failures = [result for result in report.inheritance_results if not result.enabled.first_turn_correct]
    if not inheritance_failures:
        lines.append("无。")
    for result in inheritance_failures:
        lines.extend(
            [
                f"### {_safe(result.case_id)}",
                f"- baseline：{_safe(result.baseline.final_text)}",
                f"- enabled：{_safe(result.enabled.final_text)}",
                f"- 证据：{_safe(' | '.join(result.enabled.evidence))}",
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _safe(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    return value


def _safe(text: str) -> str:
    redacted = redact_secret(text)
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", redacted)
    redacted = _PRIVATE_KEY_RE.sub("-----BEGIN [REDACTED] PRIVATE KEY-----", redacted)
    return _PASSWORD_RE.sub("password=[REDACTED]", redacted)

