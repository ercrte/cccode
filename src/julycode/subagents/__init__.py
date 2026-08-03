from __future__ import annotations

from julycode.subagents.cache import FileReadCache, FileReadCacheEntry
from julycode.subagents.loader import SubAgentRoleLoader, default_sub_agent_roots
from julycode.subagents.models import (
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
from julycode.subagents.tools import DELEGATE_AGENT_TOOL_NAME, DelegateAgentTool

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
        from julycode.subagents.manager import SubAgentConfigurationError, SubAgentManager

        return {"SubAgentConfigurationError": SubAgentConfigurationError, "SubAgentManager": SubAgentManager}[name]
    raise AttributeError(name)
