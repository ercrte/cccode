#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from july_eval.loader import EvalConfigError, load_cases, load_metrics  # noqa: E402
from july_eval.models import EvalProviderInfo, EvalRunOptions  # noqa: E402
from july_eval.report import write_json_report, write_markdown_report  # noqa: E402
from july_eval.runner import run_suite  # noqa: E402
from julycode.config import load_config  # noqa: E402
from julycode.errors import ConfigError  # noqa: E402
from julycode.providers.factory import create_provider  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 JulyCode Agent 评测")
    parser.add_argument("--mode", choices=("online", "offline"), default="online", help="评测模式，默认 online")
    parser.add_argument("--offline", action="store_true", help="快捷启用离线 smoke 模式，等价于 --mode offline")
    parser.add_argument("--cases", default=None, help="用例 JSON 文件或目录；未传时按 mode 选择默认目录")
    parser.add_argument("--metrics", default="eval/metrics/default_metrics.json", help="指标 JSON 文件")
    parser.add_argument("--output", default="eval/results/latest", help="报告输出目录")
    parser.add_argument("--case", action="append", dest="case_ids", default=[], help="只运行指定 case id，可重复传入")
    parser.add_argument("--model", default=None, help="在线模式下覆盖配置中的模型名")
    parser.add_argument("--threshold", type=float, default=80.0, help="单用例通过分数阈值")
    parser.add_argument("--allow-review", action="store_true", help="允许存在 needs_review 时仍以 0 退出")
    parser.add_argument(
        "--review-sample-rate",
        type=_review_sample_rate,
        default=0.1,
        help="基础检查通过后进入人工复核的稳定抽样比例，范围 0 到 1，默认 0.1",
    )
    parser.add_argument("--keep-workspaces", action="store_true", help="保留临时 workspace 便于排查")
    args = parser.parse_args(argv)
    mode = "offline" if args.offline else args.mode
    cases_path = args.cases or f"eval/cases/{mode}"

    try:
        metrics = load_metrics(args.metrics)
        cases = load_cases(cases_path)
        if args.case_ids:
            selected = set(args.case_ids)
            cases = tuple(case for case in cases if case.id in selected)
            missing = selected - {case.id for case in cases}
            if missing:
                raise EvalConfigError(f"指定 case 不存在: {', '.join(sorted(missing))}")
        if not cases:
            raise EvalConfigError("没有可运行的评测用例")
        options = _run_options(
            mode=mode,
            threshold=args.threshold,
            allow_review=args.allow_review,
            review_sample_rate=args.review_sample_rate,
            keep_workspaces=args.keep_workspaces,
            model_override=args.model,
        )
        result = asyncio.run(run_suite(cases, metrics, options))
        output = Path(args.output)
        write_json_report(result, output / "results.json")
        write_markdown_report(result, output / "report.md")
    except EvalConfigError as exc:
        print(f"评测配置错误: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"在线评测配置错误: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"评测框架错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    summary = result.summary
    print(
        "评测完成: "
        f"用例数={summary.total_cases}, 自动通过={summary.passed}, "
        f"失败={summary.failed}, 错误={summary.errors}, 复核={summary.needs_review}, "
        f"平均分={summary.average_score:.2f}"
    )
    print(f"报告: {Path(args.output) / 'report.md'}")
    if summary.failed or summary.errors:
        return 1
    if summary.needs_review and not args.allow_review:
        return 1
    return 0


def _run_options(
    *,
    mode: str,
    threshold: float,
    allow_review: bool,
    review_sample_rate: float,
    keep_workspaces: bool,
    model_override: str | None,
) -> EvalRunOptions:
    if mode == "offline":
        return EvalRunOptions(
            suite_id="offline",
            mode="offline",
            threshold=threshold,
            allow_review=allow_review,
            review_sample_rate=review_sample_rate,
            keep_workspaces=keep_workspaces,
            provider_info=EvalProviderInfo(
                mode="offline",
                protocol="offline",
                model="scripted",
                provider="scripted-eval",
                prompt_cache_enabled=False,
            ),
        )
    config = load_config(Path.cwd())
    provider = create_provider(config, model_override)
    model = model_override or config.model
    return EvalRunOptions(
        suite_id="online",
        mode="online",
        threshold=threshold,
        allow_review=allow_review,
        review_sample_rate=review_sample_rate,
        keep_workspaces=keep_workspaces,
        provider=provider,
        provider_info=EvalProviderInfo(
            mode="online",
            protocol=config.protocol,
            model=model,
            provider=config.protocol,
            prompt_cache_enabled=config.prompt_cache.enabled,
        ),
    )


def _review_sample_rate(value: str) -> float:
    try:
        rate = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("人工复核抽样比例必须是数字") from exc
    if not 0.0 <= rate <= 1.0:
        raise argparse.ArgumentTypeError("人工复核抽样比例必须在 0 到 1 之间")
    return rate


if __name__ == "__main__":
    raise SystemExit(main())
