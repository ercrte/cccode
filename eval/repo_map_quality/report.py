from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from repo_map_quality.models import RepoMapQualityReport
from julycode.errors import redact_secret


def write_json_report(report: RepoMapQualityReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_safe_value(asdict(report)), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_markdown_report(report: RepoMapQualityReport, path: Path) -> None:
    summary = report.summary
    lines = [
        "# JulyCode Repo Map 质量评测报告",
        "",
        "## 运行信息",
        "",
        f"- 模式：{report.mode}",
        f"- 数据集版本：{_safe(report.dataset_version)}",
        f"- 仓库根目录：{_safe(report.root)}",
        f"- 用例数：{summary.case_count}",
        "- 性质：质量观测，不是 CI 通过阈值。",
        "",
        "## 汇总",
        "",
        "| 指标 | Repo Map 关闭 | Repo Map 开启 |",
        "|---|---:|---:|",
        f"| 目标文件 Top-K 命中率 | {summary.disabled_top_k_hit_rate:.2%} | {summary.enabled_top_k_hit_rate:.2%} |",
        "| 命中前平均探索工具调用数 | "
        f"{_number(summary.disabled_average_exploration_calls)} | {_number(summary.enabled_average_exploration_calls)} |",
        "",
        "## 用例",
        "",
        "| Case | 目标文件 | K | 关闭 Top-K/读取/探索 | 开启 Top-K/读取/探索 | 开启 Top-K |",
        "|---|---|---:|---|---|---|",
    ]
    for result in report.results:
        lines.append(
            f"| {_safe(result.case_id)} | {_safe(result.target_file)} | {result.top_k} | "
            f"{_trial(result.disabled)} | {_trial(result.enabled)} | "
            f"{_safe(', '.join(result.enabled.top_files) or '-')} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _trial(trial) -> str:
    hit = "是" if trial.target_hit else "否"
    target_read = "-" if trial.target_read is None else "是" if trial.target_read else "否"
    calls = "-" if trial.exploration_calls is None else str(trial.exploration_calls)
    return f"{hit}/{target_read}/{calls}"


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _safe_value(value):
    if isinstance(value, str):
        return _safe(value)
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    return value


def _safe(text: str) -> str:
    return redact_secret(text).replace("|", "\\|").replace("\n", " ")
