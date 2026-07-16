#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parent
if str(EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(EVAL_ROOT))

from repo_map_quality.loader import NavigationDatasetLoader, RepoMapQualityConfigError  # noqa: E402
from repo_map_quality.models import RepoMapQualityRunOptions  # noqa: E402
from repo_map_quality.report import write_json_report, write_markdown_report  # noqa: E402
from repo_map_quality.runner import RepoMapQualityRunner  # noqa: E402
from mewcode.config import load_config  # noqa: E402
from mewcode.errors import ConfigError  # noqa: E402
from mewcode.providers.factory import create_provider  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 MewCode Repo Map 导航质量评测")
    parser.add_argument(
        "--mode",
        choices=("offline", "paired"),
        default="offline",
        help="offline 只测 Top-K；paired 使用真实模型成对统计探索调用数",
    )
    parser.add_argument(
        "--cases",
        default="eval/cases/repo_map_quality/navigation.json",
        help="导航评测数据集 JSON",
    )
    parser.add_argument("--root", default=".", help="被评测仓库根目录")
    parser.add_argument("--output", default="eval/results/repo-map/latest", help="报告输出目录")
    parser.add_argument("--map-budget", type=int, default=2000, help="Repo Map Token 预算")
    parser.add_argument("--top-k", type=int, default=None, help="覆盖所有用例的 Top-K")
    parser.add_argument("--model", default=None, help="paired 模式覆盖当前模型")
    args = parser.parse_args(argv)

    try:
        if args.map_budget <= 0:
            raise RepoMapQualityConfigError("--map-budget 必须大于 0")
        if args.top_k is not None and not 1 <= args.top_k <= 100:
            raise RepoMapQualityConfigError("--top-k 必须在 1 到 100 之间")
        dataset = NavigationDatasetLoader().load(Path(args.cases))
        if args.top_k is not None:
            dataset = replace(
                dataset,
                cases=tuple(replace(case, top_k=args.top_k) for case in dataset.cases),
            )
        provider = None
        model = None
        if args.mode == "paired":
            config = load_config(Path(args.root).resolve())
            provider = create_provider(config, args.model)
            model = args.model or config.model
        options = RepoMapQualityRunOptions(
            mode=args.mode,
            root=Path(args.root),
            map_budget=args.map_budget,
            provider=provider,
            model=model,
        )
        report = asyncio.run(RepoMapQualityRunner().run(dataset, options))
        output = Path(args.output)
        write_json_report(report, output / "results.json")
        write_markdown_report(report, output / "report.md")
    except (RepoMapQualityConfigError, ConfigError, ValueError) as exc:
        print(f"Repo Map 评测配置错误: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Repo Map 评测框架错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    summary = report.summary
    print(
        "Repo Map 评测完成: "
        f"模式={report.mode}, 用例={summary.case_count}, "
        f"关闭 Top-K={summary.disabled_top_k_hit_rate:.2%}, "
        f"开启 Top-K={summary.enabled_top_k_hit_rate:.2%}, "
        f"关闭探索={_number(summary.disabled_average_exploration_calls)}, "
        f"开启探索={_number(summary.enabled_average_exploration_calls)}"
    )
    print(f"报告: {Path(args.output) / 'report.md'}")
    return 0


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
