#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_quality.loader import MemoryQualityConfigError, MemoryQualityDatasetLoader  # noqa: E402
from memory_quality.models import MemoryQualityRunOptions  # noqa: E402
from memory_quality.report import write_json_report, write_markdown_report  # noqa: E402
from memory_quality.runner import MemoryQualityRunner  # noqa: E402
from mew_eval.models import EvalProviderInfo  # noqa: E402
from mewcode.config import load_config  # noqa: E402
from mewcode.errors import ConfigError  # noqa: E402
from mewcode.providers.factory import create_provider  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 MewCode 跨会话记忆质量评测")
    parser.add_argument("--mode", choices=("offline", "online"), default="offline", help="默认 offline，在线模式会消耗真实模型额度")
    parser.add_argument("--cases", default="eval/cases/memory_quality", help="专项数据集目录")
    parser.add_argument("--output", default="eval/results/memory-quality/latest", help="报告输出目录")
    parser.add_argument("--model", default=None, help="在线模式覆盖当前模型")
    args = parser.parse_args(argv)

    try:
        loader = MemoryQualityDatasetLoader()
        dataset = loader.load(Path(args.cases))
        loader.validate_acceptance_size(dataset)
        options = _options(args.mode, args.model)
        result = asyncio.run(MemoryQualityRunner().run(dataset, options))
        output = Path(args.output)
        write_json_report(result, output / "results.json")
        write_markdown_report(result, output / "report.md")
    except MemoryQualityConfigError as exc:
        print(f"记忆质量评测配置错误: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"在线记忆质量评测配置错误: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"记忆质量评测框架错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    metrics = result.extraction_metrics
    print(
        "记忆质量评测完成: "
        f"模式={result.mode}, 提取用例={len(result.extraction_results)}, "
        f"跨会话用例={len(result.inheritance_results)}, F1={metrics.f1:.2%}, "
        f"关键偏好Precision={metrics.critical_precision:.2%}, "
        f"首轮理解率={result.first_turn_accuracy:.2%}"
    )
    print(f"报告: {Path(args.output) / 'report.md'}")
    if not result.acceptance_passed:
        for failure in result.acceptance_failures:
            print(f"未通过: {failure}", file=sys.stderr)
        return 1
    return 0


def _options(mode: str, model_override: str | None) -> MemoryQualityRunOptions:
    if mode == "offline":
        return MemoryQualityRunOptions()
    config = load_config(Path.cwd())
    provider = create_provider(config, model_override)
    model = model_override or config.model
    return MemoryQualityRunOptions(
        mode="online",
        provider=provider,
        provider_info=EvalProviderInfo(
            mode="online",
            protocol=config.protocol,
            model=model,
            provider=config.protocol,
            prompt_cache_enabled=config.prompt_cache.enabled,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())

