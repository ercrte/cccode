from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from julycode.commands import AgentMode
    from julycode.context.models import ContextSummary
    from julycode.hooks.models import HookPromptInjection
    from julycode.memory.models import KnowledgeContext
    from julycode.mcp.search import McpPromptContext
    from julycode.session import PendingPlan
    from julycode.skills.models import SkillPromptContext
    from julycode.subagents.models import SubAgentPromptContext
    from julycode.teams.models import TeamPromptContext
    from julycode.tools.base import ToolSpec


RuntimeInstructionLevel = Literal["full", "refresh", "brief"]


@dataclass(frozen=True)
class PromptBlock:
    name: str
    title: str
    text: str
    stable: bool
    cacheable: bool = False


@dataclass(frozen=True)
class GeneratedContextBlock:
    name: str
    title: str
    text: str
    kind: str
    provenance: Literal["generated"] = "generated"
    trust: Literal["untrusted_repository_data"] = "untrusted_repository_data"
    persistence: Literal["request_ephemeral"] = "request_ephemeral"
    cache_scope: Literal["snapshot"] = "snapshot"
    snapshot_id: str = ""


@dataclass(frozen=True)
class PromptBundle:
    stable_blocks: Sequence[PromptBlock]
    runtime_blocks: Sequence[PromptBlock]
    generated_context_blocks: Sequence[GeneratedContextBlock] = field(default_factory=tuple)


@dataclass(frozen=True)
class RuntimePromptContext:
    cwd: Path
    mode: AgentMode
    iteration: int
    max_iterations: int
    allowed_tools: Sequence[ToolSpec]
    pending_plan: PendingPlan | None = None
    source_request: str = ""
    context_summary: ContextSummary | None = None
    knowledge_context: KnowledgeContext | None = None
    skill_context: SkillPromptContext | None = None
    sub_agent_context: SubAgentPromptContext | None = None
    team_context: TeamPromptContext | None = None
    mcp_context: McpPromptContext | None = None
    hook_injections: Sequence[HookPromptInjection] = ()
