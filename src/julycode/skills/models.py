from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from importlib.abc import Traversable

from julycode.tools.base import ToolSafety


SkillSourceScope = Literal["project", "user", "builtin"]
SkillExecutionMode = Literal["shared", "isolated"]


@dataclass(frozen=True)
class SkillFrontmatter:
    name: str
    description: str
    tools: tuple[str, ...]
    mode: SkillExecutionMode
    history: int
    model: str | None = None


@dataclass(frozen=True)
class SkillRoots:
    project: Path
    user: Path
    builtin: Traversable


@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    source_scope: SkillSourceScope


@dataclass(frozen=True)
class SkillWarning:
    message: str
    source_path: str


@dataclass(frozen=True)
class SkillFingerprint:
    entries: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class SkillToolDefinition:
    local_name: str
    global_name: str
    description: str
    parameters_schema: dict[str, Any]
    script_path: Path
    safety: ToolSafety = "side_effect"
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class SkillDefinition:
    frontmatter: SkillFrontmatter
    body: str
    source_scope: SkillSourceScope
    source_path: str
    package_dir: Path | None = None
    directory_skill: bool = False
    tool_definitions: tuple[SkillToolDefinition, ...] = ()

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def description(self) -> str:
        return self.frontmatter.description

    def summary(self) -> SkillSummary:
        return SkillSummary(
            name=self.frontmatter.name,
            description=self.frontmatter.description,
            source_scope=self.source_scope,
        )


@dataclass(frozen=True)
class SkillCatalog:
    definitions: dict[str, SkillDefinition]
    warnings: tuple[SkillWarning, ...] = ()
    fingerprint: SkillFingerprint = field(default_factory=lambda: SkillFingerprint(()))


@dataclass(frozen=True)
class SkillActivation:
    name: str
    arguments: str
    rendered_body: str
    mode: SkillExecutionMode
    history: int
    tool_whitelist: tuple[str, ...]
    model: str | None
    source_path: str


@dataclass(frozen=True)
class SkillPromptContext:
    available: tuple[SkillSummary, ...] = ()
    active: tuple[SkillActivation, ...] = ()
    warnings: tuple[SkillWarning, ...] = ()


@dataclass(frozen=True)
class SkillRefreshReport:
    changed: bool
    warnings: tuple[SkillWarning, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillExecutionSummary:
    skill_name: str
    input_goal: str
    result_text: str
    tool_statuses: tuple[str, ...] = ()
    stop_reason: str | None = None
