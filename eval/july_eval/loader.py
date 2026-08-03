from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from july_eval.models import EvalCase, EvalExpectations, EvalFile, EvalFileExpectation, EvalMetric


class EvalConfigError(ValueError):
    pass


def load_metrics(path: str | Path) -> tuple[EvalMetric, ...]:
    data = _read_json(Path(path))
    items = data.get("metrics") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise EvalConfigError("metrics 配置必须是数组或包含 metrics 数组的对象")
    metrics = tuple(_parse_metric(item, index) for index, item in enumerate(items))
    _validate_unique([metric.id for metric in metrics], "metric id")
    return metrics


def load_cases(path: str | Path) -> tuple[EvalCase, ...]:
    root = Path(path)
    files = sorted(root.glob("*.json")) if root.is_dir() else [root]
    cases: list[EvalCase] = []
    for file_path in files:
        data = _read_json(file_path)
        items = data.get("cases") if isinstance(data, dict) and "cases" in data else data
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            raise EvalConfigError(f"{file_path}: case 配置必须是对象、数组或包含 cases 数组的对象")
        cases.extend(_parse_case(item, file_path, index) for index, item in enumerate(items))
    _validate_unique([case.id for case in cases], "case id")
    return tuple(cases)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvalConfigError(f"配置文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvalConfigError(f"{path}: JSON 解析失败: {exc.msg}") from exc


def _parse_metric(item: Any, index: int) -> EvalMetric:
    data = _require_object(item, f"metrics[{index}]")
    metric_id = _non_empty_str(data, "id", f"metrics[{index}]")
    weight = _float(data.get("weight", 1.0), f"metrics[{index}].weight")
    scale_min = _int(data.get("scale_min", 0), f"metrics[{index}].scale_min")
    scale_max = _int(data.get("scale_max", 5), f"metrics[{index}].scale_max")
    if weight <= 0:
        raise EvalConfigError(f"metrics[{index}].weight 必须大于 0")
    if scale_min >= scale_max:
        raise EvalConfigError(f"metrics[{index}] 评分范围必须满足 scale_min < scale_max")
    evidence = _string_tuple(data.get("evidence", ()), f"metrics[{index}].evidence")
    if not evidence:
        raise EvalConfigError(f"metrics[{index}].evidence 不能为空")
    return EvalMetric(
        id=metric_id,
        name=_non_empty_str(data, "name", f"metrics[{index}]"),
        description=_non_empty_str(data, "description", f"metrics[{index}]"),
        scale_min=scale_min,
        scale_max=scale_max,
        weight=weight,
        evidence=evidence,
        manual_review=bool(data.get("manual_review", False)),
    )


def _parse_case(item: Any, path: Path, index: int) -> EvalCase:
    location = f"{path}: cases[{index}]"
    data = _require_object(item, location)
    prompt = _non_empty_str(data, "prompt", location)
    mode = str(data.get("mode", "normal"))
    if mode not in {"normal", "plan"}:
        raise EvalConfigError(f"{location}.mode 必须是 normal 或 plan")
    max_iterations = _int(data.get("max_iterations", 8), f"{location}.max_iterations")
    if max_iterations <= 0:
        raise EvalConfigError(f"{location}.max_iterations 必须大于 0")
    metric_weights = data.get("metric_weights", {})
    if not isinstance(metric_weights, dict):
        raise EvalConfigError(f"{location}.metric_weights 必须是对象")
    parsed_weights: dict[str, float] = {}
    for key, value in metric_weights.items():
        if not isinstance(key, str):
            raise EvalConfigError(f"{location}.metric_weights 的 key 必须是字符串")
        parsed = _float(value, f"{location}.metric_weights.{key}")
        if parsed <= 0:
            raise EvalConfigError(f"{location}.metric_weights.{key} 必须大于 0")
        parsed_weights[key] = parsed
    permission_mode = str(data.get("permission_mode", "permissive"))
    if permission_mode not in {"strict", "default", "permissive"}:
        raise EvalConfigError(f"{location}.permission_mode 必须是 strict、default 或 permissive")
    online_only = bool(data.get("online_only", False))
    offline_only = bool(data.get("offline_only", False))
    if online_only and offline_only:
        raise EvalConfigError(f"{location}.online_only 和 offline_only 不能同时为 true")
    return EvalCase(
        id=_non_empty_str(data, "id", location),
        title=_non_empty_str(data, "title", location),
        category=_non_empty_str(data, "category", location),
        prompt=prompt,
        mode=mode,  # type: ignore[arg-type]
        setup_files=_parse_files(data.get("setup_files", ()), f"{location}.setup_files"),
        permission_mode=permission_mode,
        max_iterations=max_iterations,
        expectations=_parse_expectations(data.get("expectations", {}), f"{location}.expectations"),
        metric_weights=parsed_weights,
        tags=_string_tuple(data.get("tags", ()), f"{location}.tags"),
        online_only=online_only,
        offline_only=offline_only,
    )


def _parse_files(raw: Any, location: str) -> tuple[EvalFile, ...]:
    if raw in (None, ()):
        return ()
    if not isinstance(raw, list):
        raise EvalConfigError(f"{location} 必须是数组")
    files = []
    for index, item in enumerate(raw):
        data = _require_object(item, f"{location}[{index}]")
        files.append(EvalFile(path=_non_empty_str(data, "path", f"{location}[{index}]"), content=str(data.get("content", ""))))
    return tuple(files)


def _parse_expectations(raw: Any, location: str) -> EvalExpectations:
    data = _require_object(raw or {}, location)
    return EvalExpectations(
        final_contains=_string_tuple(data.get("final_contains", ()), f"{location}.final_contains"),
        required_tools=_string_tuple(data.get("required_tools", ()), f"{location}.required_tools"),
        forbidden_tools=_string_tuple(data.get("forbidden_tools", ()), f"{location}.forbidden_tools"),
        expected_files=_parse_file_expectations(data.get("expected_files", ()), f"{location}.expected_files"),
        expected_stop_reason=data.get("expected_stop_reason", "completed"),
        min_tool_successes=_int(data.get("min_tool_successes", 0), f"{location}.min_tool_successes"),
        require_permission_denial=bool(data.get("require_permission_denial", False)),
        require_context_compaction=bool(data.get("require_context_compaction", False)),
        require_usage=bool(data.get("require_usage", False)),
        verification_commands=_string_tuple(data.get("verification_commands", ()), f"{location}.verification_commands"),
        max_tool_calls=(
            None
            if data.get("max_tool_calls") is None
            else _int(data.get("max_tool_calls"), f"{location}.max_tool_calls")
        ),
        require_chinese=bool(data.get("require_chinese", True)),
    )


def _parse_file_expectations(raw: Any, location: str) -> tuple[EvalFileExpectation, ...]:
    if raw in (None, ()):
        return ()
    if not isinstance(raw, list):
        raise EvalConfigError(f"{location} 必须是数组")
    expectations = []
    for index, item in enumerate(raw):
        data = _require_object(item, f"{location}[{index}]")
        expectations.append(
            EvalFileExpectation(
                path=_non_empty_str(data, "path", f"{location}[{index}]"),
                contains=_string_tuple(data.get("contains", ()), f"{location}[{index}].contains"),
                exact=(None if data.get("exact") is None else str(data.get("exact"))),
                must_exist=bool(data.get("must_exist", True)),
            )
        )
    return tuple(expectations)


def _require_object(item: Any, location: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise EvalConfigError(f"{location} 必须是对象")
    return item


def _non_empty_str(data: dict[str, Any], field: str, location: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise EvalConfigError(f"{location}.{field} 必须是非空字符串")
    return value.strip()


def _string_tuple(raw: Any, location: str) -> tuple[str, ...]:
    if raw in (None, ()):
        return ()
    if not isinstance(raw, list):
        raise EvalConfigError(f"{location} 必须是字符串数组")
    values = []
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            raise EvalConfigError(f"{location}[{index}] 必须是字符串")
        values.append(value)
    return tuple(values)


def _validate_unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise EvalConfigError(f"重复的 {label}: {value}")
        seen.add(value)


def _int(value: Any, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise EvalConfigError(f"{location} 必须是整数")
    return value


def _float(value: Any, location: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EvalConfigError(f"{location} 必须是数字")
    return float(value)
