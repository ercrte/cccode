from __future__ import annotations

from mewcode.subagents.cache import FileReadCache, FileReadCacheEntry
from mewcode.subagents.loader import SubAgentRoleLoader, default_sub_agent_roots
from mewcode.subagents.models import (
    ActiveSubAgentPrompt,
    BackgroundSubAgentRecord,
    ParentAgentContext,
    SubAgentBackgroundSummary,
    SubAgentConfig,
    SubAgentInvocation,
    SubAgentPromptContext,
    SubAgentRefreshReport,
    SubAgentResult,
    SubAgentRoleCatalog,
    SubAgentRoleDefinition,
    SubAgentRoleFrontmatter,
    SubAgentRoleRoots,
    SubAgentRoleSummary,
    SubAgentRoleWarning,
    SubAgentToolFilter,
    SubAgentWorkingContext,
    SubAgentWorktreeInfo,
)
from mewcode.subagents.tools import DELEGATE_AGENT_TOOL_NAME, DelegateAgentTool

__all__ = [
    "ActiveSubAgentPrompt",
    "BackgroundSubAgentRecord",
    "DELEGATE_AGENT_TOOL_NAME",
    "DelegateAgentTool",
    "FileReadCache",
    "FileReadCacheEntry",
    "ParentAgentContext",
    "SubAgentBackgroundSummary",
    "SubAgentConfig",
    "SubAgentConfigurationError",
    "SubAgentInvocation",
    "SubAgentManager",
    "SubAgentPromptContext",
    "SubAgentRefreshReport",
    "SubAgentResult",
    "SubAgentRoleCatalog",
    "SubAgentRoleDefinition",
    "SubAgentRoleFrontmatter",
    "SubAgentRoleLoader",
    "SubAgentRoleRoots",
    "SubAgentRoleSummary",
    "SubAgentRoleWarning",
    "SubAgentToolFilter",
    "SubAgentWorkingContext",
    "SubAgentWorktreeInfo",
    "default_sub_agent_roots",
]


def __getattr__(name: str):
    if name in {"SubAgentConfigurationError", "SubAgentManager"}:
        from mewcode.subagents.manager import SubAgentConfigurationError, SubAgentManager

        return {"SubAgentConfigurationError": SubAgentConfigurationError, "SubAgentManager": SubAgentManager}[name]
    raise AttributeError(name)
