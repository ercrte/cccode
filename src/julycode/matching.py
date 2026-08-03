from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from julycode.errors import ConfigError


MatchKind = Literal["exact", "glob", "regex"]

_GLOB_CHARS = set("*?[")


@dataclass(frozen=True)
class MatchExpression:
    raw: str
    pattern: str
    kind: MatchKind
    negated: bool = False


def parse_match_expression(raw: str) -> MatchExpression:
    value = str(raw).strip()
    if not value:
        raise ConfigError("匹配表达式不能为空")

    negated = value.startswith("!")
    if negated:
        value = value[1:].strip()
        if not value:
            raise ConfigError("反向匹配表达式不能为空")

    kind: MatchKind
    pattern: str
    if value.startswith("regex:"):
        kind = "regex"
        pattern = value.removeprefix("regex:")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"正则匹配表达式无效: {exc}") from exc
    elif value.startswith("glob:"):
        kind = "glob"
        pattern = value.removeprefix("glob:")
    elif any(char in value for char in _GLOB_CHARS):
        kind = "glob"
        pattern = value
    else:
        kind = "exact"
        pattern = value

    if not pattern:
        raise ConfigError("匹配表达式模式不能为空")
    return MatchExpression(raw=str(raw).strip(), pattern=pattern, kind=kind, negated=negated)


def match_expression(expression: MatchExpression, value: object) -> bool:
    text = str(value)
    if expression.kind == "exact":
        matched = text == expression.pattern
    elif expression.kind == "glob":
        matched = fnmatch.fnmatchcase(text, expression.pattern)
    else:
        matched = re.search(expression.pattern, text) is not None
    return not matched if expression.negated else matched


def get_field_value(data: Mapping[str, object], field_path: str) -> object | None:
    current: object = data
    for part in str(field_path).split("."):
        if not part:
            return None
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current
