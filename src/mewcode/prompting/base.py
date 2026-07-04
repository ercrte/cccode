from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from mewcode.commands import AgentMode
    from mewcode.context.models import ContextSummary
    from mewcode.hooks.models import HookPromptInjection
    from mewcode.memory.models import KnowledgeContext
    from mewcode.mcp.search import McpPromptContext
    from mewcode.session import PendingPlan
    from mewcode.skills.models import SkillPromptContext
    from mewcode.subagents.models import SubAgentPromptContext
    from mewcode.teams.models import TeamPromptContext
    from mewcode.tools.base import ToolSpec


RuntimeInstructionLevel = Literal["full", "refresh", "brief"]


@dataclass(frozen=True)
class PromptBlock:
    name: str
    title: str
    text: str
    stable: bool
    cacheable: bool = False


@dataclass(frozen=True)
class PromptBundle:
    stable_blocks: Sequence[PromptBlock]
    runtime_blocks: Sequence[PromptBlock]


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
