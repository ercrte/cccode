from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from repo_map_quality.models import NavigationCase, NavigationDataset


class RepoMapQualityConfigError(ValueError):
    """Repo Map 质量评测数据不合法。"""


class NavigationDatasetLoader:
    def load(self, path: Path) -> NavigationDataset:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RepoMapQualityConfigError(f"无法读取评测数据 {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RepoMapQualityConfigError(f"评测数据不是合法 JSON {path}: {exc.msg}") from exc
        obj = _object(raw, str(path))
        version = _required_string(obj, "version", "version")
        raw_cases = obj.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise RepoMapQualityConfigError("cases 必须是非空数组")

        cases: list[NavigationCase] = []
        seen: set[str] = set()
        for index, value in enumerate(raw_cases):
            case = self._case(_object(value, f"cases[{index}]"), index)
            if case.case_id in seen:
                raise RepoMapQualityConfigError(f"重复 case ID: {case.case_id}")
            seen.add(case.case_id)
            cases.append(case)
        return NavigationDataset(version=version, cases=tuple(cases))

    def _case(self, raw: dict[str, Any], index: int) -> NavigationCase:
        prefix = f"cases[{index}]"
        case_id = _required_string(raw, "id", f"{prefix}.id")
        request = _required_string(raw, "request", f"{prefix}.request")
        target = _normalized_target(_required_string(raw, "target_file", f"{prefix}.target_file"), prefix)
        top_k = raw.get("top_k", 5)
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise RepoMapQualityConfigError(f"{prefix}.top_k 必须是 1 到 100 的整数")
        tags_raw = raw.get("tags", [])
        if not isinstance(tags_raw, list) or not all(isinstance(item, str) and item.strip() for item in tags_raw):
            raise RepoMapQualityConfigError(f"{prefix}.tags 必须是字符串数组")

        folded_request = request.replace("\\", "/").casefold()
        filename = PurePosixPath(target).name.casefold()
        if target.casefold() in folded_request or filename in folded_request:
            raise RepoMapQualityConfigError(f"{prefix}.request 直接泄露了目标文件路径或文件名")
        return NavigationCase(case_id, request, target, top_k, tuple(item.strip() for item in tags_raw))


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RepoMapQualityConfigError(f"{path} 必须是对象")
    return value


def _required_string(raw: dict[str, Any], key: str, path: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RepoMapQualityConfigError(f"{path} 必须是非空字符串")
    return value.strip()


def _normalized_target(value: str, prefix: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        raise RepoMapQualityConfigError(f"{prefix}.target_file 必须是安全的相对路径")
    if path.suffix not in {".py", ".pyi"}:
        raise RepoMapQualityConfigError(f"{prefix}.target_file 必须是 Python 文件")
    return path.as_posix()
