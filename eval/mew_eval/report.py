from __future__ import annotations

import json
import re
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from mew_eval.models import EvalSuiteResult


def write_json_report(result: EvalSuiteResult, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(to_report_dict(result), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_markdown_report(result: EvalSuiteResult, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown_report(result), encoding="utf-8")


def to_report_dict(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: to_report_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [to_report_dict(item) for item in value]
    if isinstance(value, list):
        return [to_report_dict(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_report_dict(item) for key, item in value.items()}
    if isinstance(value, str):
        return _redact(_truncate(value, 2000))
    return value


def render_markdown_report(result: EvalSuiteResult) -> str:
    lines = [
        "# MewCode Agent Eval Report",
        "",
        "## 运行环境",
        "",
        f"- 模式: `{result.provider.mode}`",
        f"- Protocol: `{result.provider.protocol or 'unknown'}`",
        f"- Model: `{result.provider.model or 'unknown'}`",
        f"- Provider: `{result.provider.provider or 'unknown'}`",
        f"- Prompt cache: `{_bool_text(result.provider.prompt_cache_enabled)}`",
        "",
        "## 总体摘要",
        "",
        f"- Suite: `{result.suite_id}`",
        f"- 用例总数: {result.summary.total_cases}",
        f"- 自动通过: {result.summary.passed}",
        f"- 失败: {result.summary.failed}",
        f"- 错误: {result.summary.errors}",
        f"- 人工复核: {result.summary.needs_review}",
        f"- 平均分: {result.summary.average_score:.2f}",
        f"- 阈值: {result.summary.threshold:.2f}",
        "",
        "## 维度均分",
        "",
        "| 维度 | 均分 |",
        "|---|---:|",
    ]
    for metric_id, average in sorted(result.metric_averages.items()):
        lines.append(f"| `{metric_id}` | {average:.2f} |")
    lines.extend(
        [
            "",
            "## 用例结果",
            "",
            "| 用例 | 状态 | 总分 | 停止原因 | 工具调用 |",
            "|---|---|---:|---|---|",
        ]
    )
    for case_result in result.results:
        tools = ", ".join(call.name for call in case_result.trace.tool_calls) or "无"
        lines.append(
            f"| `{case_result.case_id}` | {case_result.status} | {case_result.total_score:.2f} | "
            f"{case_result.trace.stop_reason or ''} | {_md(_truncate(tools, 180))} |"
        )
    lines.extend(["", "## 失败用例", ""])
    failures = [item for item in result.results if item.status in {"fail", "error"}]
    if not failures:
        lines.append("- 无")
    for item in failures:
        lines.append(f"- `{item.case_id}`: {item.status}; errors={'; '.join(item.trace.errors) or '无'}")
    lines.extend(["", "## 人工复核项", ""])
    review_items = [
        (case_result, score)
        for case_result in result.results
        for score in case_result.metric_scores
        if score.status == "needs_review"
    ]
    if not review_items:
        lines.append("- 无")
    for case_result, score in review_items:
        evidence = "; ".join(score.evidence[:2])
        lines.append(f"- `{case_result.case_id}` / `{score.metric_id}`: {_md(_truncate(evidence, 240))}")
    lines.extend(["", "## 关键证据", ""])
    for case_result in result.results:
        lines.append(f"### {case_result.case_id}")
        lines.append("")
        lines.append(f"- 最终回复: {_md(_truncate(case_result.trace.final_message, 300))}")
        if case_result.trace.usage is not None:
            usage = case_result.trace.usage
            lines.append(
                "- Usage: "
                f"input={usage.input_tokens}, output={usage.output_tokens}, total={usage.total_tokens}, "
                f"provider={usage.provider}, cache={usage.cache_status or 'unknown'}"
            )
        if case_result.trace.events:
            lines.append(f"- 事件: {_md(', '.join(event.type for event in case_result.trace.events[:12]))}")
        for score in case_result.metric_scores[:6]:
            lines.append(f"- `{score.metric_id}`: {score.status}; {_md(_truncate('; '.join(score.evidence[:2]), 260))}")
        lines.append("")
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _redact(text: str) -> str:
    return re.sub(r"api_key", "redacted_key", text, flags=re.IGNORECASE)


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _bool_text(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "enabled" if value else "disabled"
