from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from memory_quality.models import (
    ExpectedMemory,
    ExtractionCase,
    InheritanceCase,
    InheritanceExpectation,
    MemoryQualityDataset,
)
from mewcode.providers.base import ChatMessage


class MemoryQualityConfigError(ValueError):
    """专项评测数据不合法。"""


class MemoryQualityDatasetLoader:
    REQUIRED_COVERAGE_TAGS = frozenset(
        {
            "zh",
            "en",
            "negation",
            "correction",
            "conflict",
            "duplicate",
            "temporary",
            "uncertain",
            "assistant_only",
            "tool_only",
            "sensitive",
        }
    )

    def load(self, root: Path) -> MemoryQualityDataset:
        extraction_raw = _read_json(root / "extraction.json")
        inheritance_raw = _read_json(root / "inheritance.json")
        extraction_version = _required_string(extraction_raw, "version", "extraction.version")
        inheritance_version = _required_string(inheritance_raw, "version", "inheritance.version")
        if extraction_version != inheritance_version:
            raise MemoryQualityConfigError("extraction 与 inheritance 数据集版本不一致")
        extraction_cases = self._load_extraction_cases(extraction_raw)
        inheritance_cases = self._load_inheritance_cases(inheritance_raw)
        ids = [case.case_id for case in extraction_cases] + [case.case_id for case in inheritance_cases]
        if len(ids) != len(set(ids)):
            raise MemoryQualityConfigError("数据集存在重复 case ID")
        return MemoryQualityDataset(
            version=extraction_version,
            extraction_cases=extraction_cases,
            inheritance_cases=inheritance_cases,
        )

    def validate_acceptance_size(self, dataset: MemoryQualityDataset) -> None:
        critical = sum(1 for case in dataset.extraction_cases for item in case.expected if item.critical)
        other = sum(1 for case in dataset.extraction_cases for item in case.expected if not item.critical)
        negatives = sum(1 for case in dataset.extraction_cases if not case.expected)
        if len(dataset.extraction_cases) < 120:
            raise MemoryQualityConfigError("提取数据集至少需要 120 个用例")
        if critical < 50:
            raise MemoryQualityConfigError("关键偏好正例至少需要 50 个")
        if other < 30:
            raise MemoryQualityConfigError("其他长期记忆正例至少需要 30 个")
        if negatives < 40:
            raise MemoryQualityConfigError("不应记忆负例至少需要 40 个")
        if len(dataset.inheritance_cases) < 20:
            raise MemoryQualityConfigError("跨会话成对用例至少需要 20 个")
        tags = {tag for case in dataset.extraction_cases for tag in case.tags}
        missing = sorted(self.REQUIRED_COVERAGE_TAGS - tags)
        if missing:
            raise MemoryQualityConfigError(f"数据集缺少覆盖标签: {', '.join(missing)}")

    def _load_extraction_cases(self, raw: dict[str, Any]) -> tuple[ExtractionCase, ...]:
        cases = _required_list(raw, "cases", "extraction.cases")
        parsed: list[ExtractionCase] = []
        seen: set[str] = set()
        for index, item in enumerate(cases):
            path = f"extraction.cases[{index}]"
            obj = _object(item, path)
            case_id = _required_string(obj, "id", f"{path}.id")
            if case_id in seen:
                raise MemoryQualityConfigError(f"重复 case ID: {case_id}")
            seen.add(case_id)
            messages = tuple(self._message(value, f"{path}.messages") for value in _required_list(obj, "messages", path))
            expected = tuple(
                self._expected(value, f"{path}.expected", user_texts=tuple(m.content for m in messages if m.role == "user"))
                for value in _required_list(obj, "expected", path)
            )
            parsed.append(
                ExtractionCase(
                    case_id=case_id,
                    tags=_strings(obj.get("tags", []), f"{path}.tags"),
                    messages=messages,
                    expected=expected,
                )
            )
        return tuple(parsed)

    def _load_inheritance_cases(self, raw: dict[str, Any]) -> tuple[InheritanceCase, ...]:
        cases = _required_list(raw, "cases", "inheritance.cases")
        parsed: list[InheritanceCase] = []
        seen: set[str] = set()
        for index, item in enumerate(cases):
            path = f"inheritance.cases[{index}]"
            obj = _object(item, path)
            case_id = _required_string(obj, "id", f"{path}.id")
            if case_id in seen:
                raise MemoryQualityConfigError(f"重复 case ID: {case_id}")
            seen.add(case_id)
            source_prompt = _required_string(obj, "source_prompt", f"{path}.source_prompt")
            source_expected = tuple(
                self._expected(value, f"{path}.source_expected", user_texts=(source_prompt,))
                for value in _required_list(obj, "source_expected", path)
            )
            expectation_raw = _object(obj.get("expectation"), f"{path}.expectation")
            expectation = InheritanceExpectation(
                required_term_groups=_term_groups(expectation_raw.get("required_term_groups"), f"{path}.required_term_groups"),
                forbidden_terms=_strings(expectation_raw.get("forbidden_terms", []), f"{path}.forbidden_terms"),
                restatement_terms=_strings(expectation_raw.get("restatement_terms", []), f"{path}.restatement_terms"),
            )
            if not expectation.restatement_terms:
                raise MemoryQualityConfigError(f"{path}.restatement_terms 不能为空")
            parsed.append(
                InheritanceCase(
                    case_id=case_id,
                    tags=_strings(obj.get("tags", []), f"{path}.tags"),
                    source_prompt=source_prompt,
                    source_expected=source_expected,
                    target_prompt=_required_string(obj, "target_prompt", f"{path}.target_prompt"),
                    expectation=expectation,
                )
            )
        return tuple(parsed)

    def _message(self, raw: Any, path: str) -> ChatMessage:
        obj = _object(raw, path)
        role = _required_string(obj, "role", f"{path}.role")
        if role not in {"user", "assistant", "tool"}:
            raise MemoryQualityConfigError(f"{path}.role 非法: {role}")
        return ChatMessage(role=role, content=str(obj.get("content", "")))  # type: ignore[arg-type]

    def _expected(self, raw: Any, path: str, *, user_texts: tuple[str, ...]) -> ExpectedMemory:
        obj = _object(raw, path)
        scope = _required_string(obj, "scope", f"{path}.scope")
        category = _required_string(obj, "category", f"{path}.category")
        if scope not in {"user", "project"}:
            raise MemoryQualityConfigError(f"{path}.scope 非法: {scope}")
        if category not in {"preference", "correction", "project_knowledge", "reference"}:
            raise MemoryQualityConfigError(f"{path}.category 非法: {category}")
        evidence = _strings(obj.get("evidence"), f"{path}.evidence")
        if not evidence:
            raise MemoryQualityConfigError(f"{path}.evidence 不能为空")
        for quote in evidence:
            if not any(quote in text for text in user_texts):
                raise MemoryQualityConfigError(f"{path}.evidence 不存在于用户消息: {quote}")
        critical = obj.get("critical")
        if not isinstance(critical, bool):
            raise MemoryQualityConfigError(f"{path}.critical 必须是布尔值")
        return ExpectedMemory(
            key=_required_string(obj, "key", f"{path}.key"),
            scope=scope,  # type: ignore[arg-type]
            category=category,  # type: ignore[arg-type]
            critical=critical,
            evidence=evidence,
            content_term_groups=_term_groups(obj.get("content_term_groups"), f"{path}.content_term_groups"),
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MemoryQualityConfigError(f"无法读取评测数据 {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MemoryQualityConfigError(f"评测数据不是合法 JSON {path}: {exc.msg}") from exc
    return _object(value, str(path))


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MemoryQualityConfigError(f"{path} 必须是对象")
    return value


def _required_string(raw: dict[str, Any], key: str, path: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MemoryQualityConfigError(f"{path} 必须是非空字符串")
    return value.strip()


def _required_list(raw: dict[str, Any], key: str, path: str) -> list[Any]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise MemoryQualityConfigError(f"{path}.{key} 必须是数组")
    return value


def _strings(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise MemoryQualityConfigError(f"{path} 必须是非空字符串数组")
    return tuple(item.strip() for item in value)


def _term_groups(value: Any, path: str) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list) or not value:
        raise MemoryQualityConfigError(f"{path} 必须是非空二维字符串数组")
    groups = tuple(_strings(group, path) for group in value)
    if any(not group for group in groups):
        raise MemoryQualityConfigError(f"{path} 不能包含空词组")
    return groups

