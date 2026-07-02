from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from mewcode.matching import MatchExpression, parse_match_expression
from mewcode.tools.base import ToolCall


PermissionMode = Literal["strict", "default", "permissive"]
PermissionEffect = Literal["allow", "deny"]
PermissionRuleSource = Literal["session", "local", "project", "user"]
MatchKind = Literal["exact", "glob", "regex"]
PermissionDecisionKind = Literal["allow", "deny", "prompt"]
UserPermissionChoice = Literal["allow_once", "allow_session", "allow_permanent", "deny"]


@dataclass(frozen=True)
class PermissionConfig:
    mode: PermissionMode = "default"


@dataclass(frozen=True)
class PermissionRule:
    source: PermissionRuleSource
    tool_name: str
    pattern: str
    effect: PermissionEffect
    match_kind: MatchKind
    raw_key: str
    expression: MatchExpression | None = None

    def __post_init__(self) -> None:
        if self.expression is None:
            object.__setattr__(self, "expression", parse_match_expression(self.pattern))


@dataclass(frozen=True)
class RuleMatch:
    rule: PermissionRule
    target: str


@dataclass(frozen=True)
class PermissionSubject:
    tool_name: str
    targets: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class PermissionPrompt:
    call: ToolCall
    tool_name: str
    title: str
    summary: str
    reason: str
    suggested_rule_key: str


@dataclass(frozen=True)
class PermissionDecision:
    kind: PermissionDecisionKind
    reason: str
    error_type: str | None = None
    matched_rule: PermissionRule | None = None
    prompt: PermissionPrompt | None = None


@dataclass(frozen=True)
class PermissionPromptResult:
    choice: UserPermissionChoice
    rule: PermissionRule | None = None


@dataclass(frozen=True)
class PermissionEventPayload:
    prompt: PermissionPrompt
    decision: PermissionDecision | None = None
    choice: UserPermissionChoice | None = None


class PermissionPrompter(Protocol):
    async def request_permission(self, prompt: PermissionPrompt) -> UserPermissionChoice:
        ...
