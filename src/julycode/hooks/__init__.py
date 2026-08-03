from __future__ import annotations

from julycode.hooks.config import parse_hook_config
from julycode.hooks.models import (
    HookAction,
    HookCommandAction,
    HookCondition,
    HookConditionGroup,
    HookConfig,
    HookEvent,
    HookExecutionResult,
    HookHttpAction,
    HookPromptAction,
    HookPromptInjection,
    HookRule,
    HookRuntimeContext,
    HookSubAgentAction,
    HookToolBlock,
    HookToolDecision,
)


def create_hook_manager(config: HookConfig):
    from julycode.hooks.manager import create_hook_manager as _create_hook_manager

    return _create_hook_manager(config)


def __getattr__(name: str):
    if name == "HookManager":
        from julycode.hooks.manager import HookManager

        return HookManager
    raise AttributeError(name)

__all__ = [
    "HookAction",
    "HookCommandAction",
    "HookCondition",
    "HookConditionGroup",
    "HookConfig",
    "HookEvent",
    "HookExecutionResult",
    "HookHttpAction",
    "HookPromptAction",
    "HookPromptInjection",
    "HookRule",
    "HookRuntimeContext",
    "HookSubAgentAction",
    "HookToolBlock",
    "HookToolDecision",
    "HookManager",
    "create_hook_manager",
    "parse_hook_config",
]
