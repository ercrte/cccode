from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from mewcode.matching import MatchExpression
from mewcode.tools.base import ToolResult

if TYPE_CHECKING:
    from mewcode.commands import AgentMode
    from mewcode.permissions.controller import PermissionController
    from mewcode.tools.executor import ToolExecutor
    from mewcode.tools.registry import ToolRegistry


HookEventName = Literal[
    "session.start",
    "session.end",
    "turn.start",
    "turn.end",
    "message.user",
    "message.assistant",
    "tool.before",
    "tool.after",
    "system.context_compacted",
    "system.stopped",
    "system.error",
]
HookActionType = Literal["command", "prompt", "http", "sub_agent"]
ConditionLogic = Literal["all", "any"]
PromptScope = Literal["next_request"]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
HookExecutionStatus = Literal["success", "failed", "blocked", "placeholder", "skipped_once"]

HOOK_EVENTS: frozenset[str] = frozenset(
    {
        "session.start",
        "session.end",
        "turn.start",
        "turn.end",
        "message.user",
        "message.assistant",
        "tool.before",
        "tool.after",
        "system.context_compacted",
        "system.stopped",
        "system.error",
    }
)
HOOK_ACTION_TYPES: frozenset[str] = frozenset({"command", "prompt", "http", "sub_agent"})
HTTP_METHODS: frozenset[str] = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class HookCondition:
    field: str
    match: MatchExpression


@dataclass(frozen=True)
class HookConditionGroup:
    logic: ConditionLogic
    conditions: tuple[HookCondition, ...]


@dataclass(frozen=True)
class HookCommandAction:
    command: str
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class HookPromptAction:
    text: str
    scope: PromptScope = "next_request"


@dataclass(frozen=True)
class HookHttpAction:
    method: HttpMethod
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str | None = None
    json_body: object | None = None
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class HookSubAgentAction:
    name: str
    prompt: str = ""


@dataclass(frozen=True)
class HookToolBlock:
    reason: str
    error_type: str = "hook_blocked"


@dataclass(frozen=True)
class HookAction:
    type: HookActionType
    command: HookCommandAction | None = None
    prompt: HookPromptAction | None = None
    http: HookHttpAction | None = None
    sub_agent: HookSubAgentAction | None = None
    tool_block: HookToolBlock | None = None


@dataclass(frozen=True)
class HookRule:
    id: str
    index: int
    event: HookEventName
    condition: HookConditionGroup | None
    action: HookAction
    once: bool = False
    background: bool = False


@dataclass(frozen=True)
class HookConfig:
    rules: tuple[HookRule, ...] = ()


@dataclass(frozen=True)
class HookEvent:
    name: HookEventName
    data: Mapping[str, object]


@dataclass(frozen=True)
class HookPromptInjection:
    rule_id: str
    text: str


@dataclass(frozen=True)
class HookExecutionResult:
    rule_id: str
    event: HookEventName
    status: HookExecutionStatus
    message: str = ""
    elapsed_ms: int = 0
    tool_result: ToolResult | None = None
    prompt_injection: HookPromptInjection | None = None


@dataclass
class HookRuntimeState:
    executed_once: set[str] = field(default_factory=set)
    prompt_injections: list[HookPromptInjection] = field(default_factory=list)
    background_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    completed_background: list[HookExecutionResult] = field(default_factory=list)


@dataclass(frozen=True)
class HookRuntimeContext:
    cwd: Path
    mode: AgentMode
    allowed_tool_names: frozenset[str] | None
    registry: ToolRegistry
    executor: ToolExecutor
    permission_controller: PermissionController | None = None


@dataclass(frozen=True)
class HookToolDecision:
    blocked: bool
    results: tuple[HookExecutionResult, ...] = ()
    tool_result: ToolResult | None = None


def event_data(**kwargs: object) -> Mapping[str, object]:
    return kwargs
