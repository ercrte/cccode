from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

import yaml

from julycode.errors import ConfigError
from julycode.matching import match_expression, parse_match_expression
from julycode.permissions.models import (
    PermissionEffect,
    PermissionRule,
    PermissionRuleSource,
    PermissionSubject,
    RuleMatch,
)


_RULE_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$")


class PermissionRuleParser:
    def parse_rule_key(self, key: str, source: PermissionRuleSource, effect: str) -> PermissionRule:
        raw_key = str(key).strip()
        match = _RULE_KEY_RE.match(raw_key)
        if not match:
            raise ConfigError(f"权限规则必须使用 工具名(模式) 格式: {key}")
        tool_name = _normalize_tool_name(match.group(1).strip())
        pattern = match.group(2).strip()
        if not tool_name:
            raise ConfigError(f"权限规则工具名不能为空: {key}")
        if not pattern:
            raise ConfigError(f"权限规则模式不能为空: {key}")
        normalized_effect = str(effect).strip().lower()
        if normalized_effect not in {"allow", "deny"}:
            raise ConfigError(f"权限规则结果必须是 allow 或 deny: {key}")
        expression = parse_match_expression(pattern)
        return PermissionRule(
            source=source,
            tool_name=tool_name,
            pattern=pattern,
            effect=normalized_effect,  # type: ignore[arg-type]
            match_kind=expression.kind,
            raw_key=raw_key,
            expression=expression,
        )


class PermissionRuleSet:
    def __init__(self, source: PermissionRuleSource, rules: list[PermissionRule] | tuple[PermissionRule, ...]) -> None:
        self.source = source
        self.rules = tuple(rules)

    def match(self, subject: PermissionSubject) -> RuleMatch | None:
        candidates: list[tuple[int, int, int, RuleMatch]] = []
        for index, rule in enumerate(self.rules):
            if rule.tool_name != subject.tool_name:
                continue
            target = _matching_target(rule, subject.targets)
            if target is None:
                continue
            match_kind_rank = 2 if rule.match_kind == "exact" else 1
            effect_rank = 1 if rule.effect == "deny" else 0
            candidates.append((match_kind_rank, effect_rank, -index, RuleMatch(rule=rule, target=target)))
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[0], item[1], item[2]))[3]


class SessionPermissionRules:
    def __init__(self) -> None:
        self._rules: list[PermissionRule] = []

    def add(self, rule: PermissionRule) -> None:
        self._rules.append(rule)

    def as_rule_set(self) -> PermissionRuleSet:
        return PermissionRuleSet("session", tuple(self._rules))


class PermissionRuleStore:
    def __init__(
        self,
        *,
        cwd: Path,
        user_rules: PermissionRuleSet,
        project_rules: PermissionRuleSet,
        local_rules: PermissionRuleSet,
    ) -> None:
        self.cwd = cwd.resolve(strict=False)
        self.user_rules = user_rules
        self.project_rules = project_rules
        self.local_rules = local_rules
        self.local_path = self.cwd / ".julycode.permissions.local.yaml"

    @classmethod
    def load(cls, cwd: Path) -> PermissionRuleStore:
        root = cwd.resolve(strict=False)
        parser = PermissionRuleParser()
        return cls(
            cwd=root,
            user_rules=_load_rule_set(user_permissions_path(), "user", parser),
            project_rules=_load_rule_set(root / ".julycode.permissions.yaml", "project", parser),
            local_rules=_load_rule_set(root / ".julycode.permissions.local.yaml", "local", parser),
        )

    def ordered_rule_sets(self, session_rules: SessionPermissionRules) -> tuple[PermissionRuleSet, ...]:
        return (
            session_rules.as_rule_set(),
            self.local_rules,
            self.project_rules,
            self.user_rules,
        )

    def add_local_rule(self, rule: PermissionRule) -> None:
        parser = PermissionRuleParser()
        existing = _read_yaml_rules(self.local_path, allow_missing=True)
        rules = dict(existing)
        rules[rule.raw_key] = rule.effect
        self.local_path.write_text(
            yaml.safe_dump({"rules": rules}, allow_unicode=True, sort_keys=True),
            encoding="utf-8",
        )
        self.local_rules = _load_rule_set(self.local_path, "local", parser)


def user_permissions_path() -> Path:
    return Path.home() / ".julycode" / "permissions.yaml"


def _load_rule_set(path: Path, source: PermissionRuleSource, parser: PermissionRuleParser) -> PermissionRuleSet:
    raw_rules = _read_yaml_rules(path, allow_missing=True)
    return PermissionRuleSet(
        source,
        tuple(parser.parse_rule_key(key, source, effect) for key, effect in raw_rules.items()),
    )


def _read_yaml_rules(path: Path, *, allow_missing: bool) -> dict[str, str]:
    if not path.exists():
        if allow_missing:
            return {}
        raise ConfigError(f"权限规则文件不存在: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"权限规则文件 {path} 不是合法 YAML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取权限规则文件 {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"权限规则文件 {path} 顶层必须是 YAML 对象")
    rules = raw.get("rules")
    if not isinstance(rules, dict):
        raise ConfigError(f"权限规则文件 {path} 必须包含 rules 对象")
    normalized: dict[str, str] = {}
    for key, value in rules.items():
        if not isinstance(key, str):
            raise ConfigError(f"权限规则文件 {path} 的规则名必须是字符串")
        if not isinstance(value, str):
            raise ConfigError(f"权限规则 {key} 的结果必须是 allow 或 deny")
        normalized[key] = value
    return normalized


def _matching_target(rule: PermissionRule, targets: tuple[str, ...]) -> str | None:
    expression = rule.expression or parse_match_expression(rule.pattern)
    for target in targets:
        if match_expression(expression, target):
            return target
    return None


def _normalize_tool_name(tool_name: str) -> str:
    if tool_name == "Bash":
        return "run_command"
    return tool_name
