from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from mewcode.mcp.manager import McpLoadReport
from mewcode.providers.base import TokenUsage


AgentMode = Literal["normal", "plan"]
CommandKind = Literal["local", "ui", "prompt"]


@dataclass(frozen=True)
class AgentCommand:
    mode: AgentMode
    visible_text: str
    model_text: str


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str
    kind: CommandKind
    argument_hint: str = ""
    hidden: bool = False
    handler: CommandHandler | None = None
    origin: str = "builtin"


@dataclass(frozen=True)
class CommandInvocation:
    definition: CommandDefinition
    raw_text: str
    command_text: str
    argument: str
    matched_name: str


@dataclass(frozen=True)
class EmptyInput:
    pass


@dataclass(frozen=True)
class PlainInput:
    text: str


@dataclass(frozen=True)
class UnknownCommandInput:
    raw_text: str
    command_text: str


ParsedInput = EmptyInput | PlainInput | CommandInvocation | UnknownCommandInput


@dataclass(frozen=True)
class CommandCompletion:
    replacement: str | None
    options: tuple[CommandDefinition, ...]


@dataclass(frozen=True)
class CommandStatusSnapshot:
    protocol: str
    model: str
    mode: AgentMode
    agent_running: bool
    last_usage: TokenUsage | None
    mcp_report: McpLoadReport | None


@dataclass(frozen=True)
class CommandSessionSnapshot:
    session_id: str
    restored: bool
    source_path: str
    message_count: int
    mode: AgentMode


@dataclass(frozen=True)
class CommandMemorySnapshot:
    enabled: bool
    user_index_available: bool
    project_index_available: bool
    auto_notes_enabled: bool
    warning_count: int


@dataclass(frozen=True)
class CommandPermissionSnapshot:
    mode: str
    session_rule_count: int
    local_rule_count: int
    project_rule_count: int
    user_rule_count: int


@dataclass(frozen=True)
class CommandSkillSnapshot:
    available: tuple[str, ...]
    active: tuple[str, ...]
    warning_count: int


@dataclass(frozen=True)
class CommandSubAgentTaskSnapshot:
    task_id: str
    type: str
    role: str | None
    status: str
    task: str
    summary: str


@dataclass(frozen=True)
class CommandSubAgentSnapshot:
    enabled: bool
    available: tuple[str, ...]
    background: tuple[CommandSubAgentTaskSnapshot, ...]
    warning_count: int
    foreground_running: bool


class CommandContext(Protocol):
    @property
    def mode(self) -> AgentMode:
        ...

    def set_mode(self, mode: AgentMode) -> None:
        ...

    def status_snapshot(self) -> CommandStatusSnapshot:
        ...

    def session_snapshot(self) -> CommandSessionSnapshot:
        ...

    def memory_snapshot(self) -> CommandMemorySnapshot:
        ...

    def permission_snapshot(self) -> CommandPermissionSnapshot:
        ...

    def skill_snapshot(self) -> CommandSkillSnapshot:
        ...

    def sub_agent_snapshot(self) -> CommandSubAgentSnapshot:
        ...

    def refresh_status(self) -> None:
        ...

    async def show_assistant(self, content: str) -> None:
        ...

    async def show_error(self, content: str) -> None:
        ...

    async def clear_messages(self) -> None:
        ...

    async def compact_context(self) -> str:
        ...

    async def authorize_mcp_server(self, server_name: str) -> str:
        ...

    async def logout_mcp_server(self, server_name: str) -> str:
        ...

    async def send_prompt(self, *, visible_text: str, model_text: str, mode: AgentMode) -> None:
        ...

    async def invoke_skill(self, *, name: str, arguments: str, visible_text: str) -> None:
        ...

    async def background_current_sub_agent(self) -> bool:
        ...

    def clear_active_skills(self) -> None:
        ...


CommandHandler = Callable[[CommandInvocation, CommandContext], Awaitable[None]]
