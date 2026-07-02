from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from mewcode.subagents.cache import FileReadCache


ToolSafety = Literal["read_only", "side_effect"]
ToolVisibility = Literal["model", "system"]
RuntimePrincipalKind = Literal["main", "sub_agent", "team_member"]


@dataclass(frozen=True)
class RuntimePrincipal:
    kind: RuntimePrincipalKind = "main"
    team_name: str | None = None
    actor_name: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    timeout_seconds: float = 10.0
    safety: ToolSafety = "side_effect"
    visibility: ToolVisibility = "model"
    origin: str = "builtin"


@dataclass(frozen=True)
class ToolContext:
    cwd: Path
    max_output_chars: int = 20000
    read_cache: FileReadCache | None = None
    principal: RuntimePrincipal = field(default_factory=RuntimePrincipal)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cwd", self.cwd.resolve())


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""
    parse_error: str | None = None


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    tool_name: str
    success: bool
    data: dict[str, Any]
    error_type: str | None = None
    error: str | None = None
    elapsed_ms: int | None = None

    def to_model_content(self) -> str:
        payload = {
            "success": self.success,
            "tool_name": self.tool_name,
            "data": self.data,
            "error_type": self.error_type,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class ToolExecutionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_type: str = "tool_error",
        data: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.data = dict(data or {})


class Tool(Protocol):
    spec: ToolSpec

    async def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolContext,
    ) -> Mapping[str, Any]:
        ...
