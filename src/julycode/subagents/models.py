from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from importlib.abc import Traversable
from pathlib import Path
from typing import Literal, TYPE_CHECKING

from julycode.worktrees.models import WorktreeConfig

if TYPE_CHECKING:
    from julycode.commands import AgentCommand, AgentMode
    from julycode.providers.base import TokenUsage
    from julycode.session import ChatSession
    from julycode.tools.base import ToolSpec
    from julycode.worktrees.models import WorktreeLease


SubAgentRoleSource = Literal["project", "user", "builtin", "plugin"]
SubAgentRoleModel = str
SubAgentPermissionMode = Literal["inherit", "strict", "default", "permissive"]
SubAgentType = Literal["defined", "fork"]
SubAgentStatus = Literal["queued", "running", "background", "completed", "failed", "cancelled"]
SubAgentIsolation = Literal["shared", "worktree"]


@dataclass(frozen=True)
class SubAgentConfig:
    enabled: bool = True
    foreground_timeout_seconds: float = 30.0
    default_max_iterations: int | None = 40
    max_background_tasks: int = 8
    global_blocked_tools: tuple[str, ...] = ("delegate_agent",)
    background_allowed_tools: tuple[str, ...] = ("read_file", "find_files", "search_code")
    model_aliases: dict[str, str] = field(default_factory=dict)
    plugin_role_roots: tuple[str, ...] = ()
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)


@dataclass(frozen=True)
class SubAgentRoleFrontmatter:
    name: str
    description: str
    tools_allow: tuple[str, ...]
    tools_deny: tuple[str, ...] = ()
    model: SubAgentRoleModel = "inherit"
    max_iterations: int | None = None
    permission_mode: SubAgentPermissionMode = "inherit"
    isolation: SubAgentIsolation = "shared"


@dataclass(frozen=True)
class SubAgentRoleSummary:
    name: str
    description: str
    source_scope: SubAgentRoleSource


@dataclass(frozen=True)
class SubAgentRoleWarning:
    message: str
    source_path: str


@dataclass(frozen=True)
class SubAgentRoleFingerprint:
    entries: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class SubAgentRoleDefinition:
    frontmatter: SubAgentRoleFrontmatter
    body: str
    source_scope: SubAgentRoleSource
    source_path: str

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def description(self) -> str:
        return self.frontmatter.description

    def summary(self) -> SubAgentRoleSummary:
        return SubAgentRoleSummary(
            name=self.frontmatter.name,
            description=self.frontmatter.description,
            source_scope=self.source_scope,
        )


@dataclass(frozen=True)
class SubAgentRoleRoots:
    project: Path
    user: Path
    builtin: Traversable
    plugins: tuple[Path | Traversable, ...] = ()


@dataclass(frozen=True)
class SubAgentRoleCatalog:
    definitions: dict[str, SubAgentRoleDefinition]
    warnings: tuple[SubAgentRoleWarning, ...] = ()
    fingerprint: SubAgentRoleFingerprint = field(default_factory=lambda: SubAgentRoleFingerprint(()))


@dataclass(frozen=True)
class SubAgentRefreshReport:
    changed: bool
    warnings: tuple[SubAgentRoleWarning, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubAgentInvocation:
    type: SubAgentType
    task: str
    role: str | None = None
    background: bool = False
    max_iterations: int | None = None
    foreground_timeout_seconds: float | None = None


@dataclass(frozen=True)
class ParentAgentContext:
    session: ChatSession
    mode: AgentMode
    command: AgentCommand
    allowed_tools: tuple[ToolSpec, ...]
    tool_whitelist: frozenset[str] | None


@dataclass(frozen=True)
class SubAgentToolFilter:
    inherited_tools: frozenset[str] | None = None
    role_allow: frozenset[str] | None = None
    role_deny: frozenset[str] = field(default_factory=frozenset)
    global_blocked: frozenset[str] = field(default_factory=frozenset)
    background_allowed: frozenset[str] | None = None
    nested_blocked: frozenset[str] = field(default_factory=lambda: frozenset({"delegate_agent"}))


@dataclass(frozen=True)
class SubAgentResult:
    task_id: str
    type: SubAgentType
    role: str | None
    status: SubAgentStatus
    task: str
    summary: str
    final_text: str = ""
    stop_reason: str | None = None
    key_outputs: tuple[str, ...] = ()
    error: str | None = None
    usage: TokenUsage | None = None
    worktree: SubAgentWorktreeInfo | None = None


@dataclass(frozen=True)
class SubAgentWorktreeInfo:
    root: str
    cwd: str
    branch: str
    base_commit: str
    disposition: Literal["cleaned", "retained"]
    reason: str


@dataclass(frozen=True)
class SubAgentWorkingContext:
    cwd: Path
    main_cwd: Path
    isolation: SubAgentIsolation
    lease: WorktreeLease | None = None


@dataclass
class BackgroundSubAgentRecord:
    task_id: str
    invocation: SubAgentInvocation
    status: SubAgentStatus
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    result: SubAgentResult | None = None
    error: str | None = None
    usage: TokenUsage | None = None
    task: asyncio.Task[SubAgentResult] | None = None
    force_background: asyncio.Event | None = None
    notified: bool = False
    worktree_lease: WorktreeLease | None = None


@dataclass(frozen=True)
class ActiveSubAgentPrompt:
    task_id: str
    type: SubAgentType
    role_name: str | None
    role_description: str | None
    role_body: str | None
    task: str
    non_interactive: bool = True
    isolation: SubAgentIsolation = "shared"
    cwd: Path | None = None
    main_cwd: Path | None = None
    branch: str | None = None


@dataclass(frozen=True)
class SubAgentBackgroundSummary:
    task_id: str
    type: SubAgentType
    role: str | None
    status: SubAgentStatus
    task: str
    summary: str = ""
    stop_reason: str | None = None


@dataclass(frozen=True)
class SubAgentPromptContext:
    available_roles: tuple[SubAgentRoleSummary, ...] = ()
    warnings: tuple[SubAgentRoleWarning, ...] = ()
    active: ActiveSubAgentPrompt | None = None
    background: tuple[SubAgentBackgroundSummary, ...] = ()
