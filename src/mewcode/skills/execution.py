from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Callable

from mewcode.config import AppConfig
from mewcode.context.manager import ContextManager
from mewcode.hooks.manager import HookManager
from mewcode.permissions.controller import create_permission_controller
from mewcode.permissions.models import PermissionConfig
from mewcode.providers.base import LLMProvider
from mewcode.providers.factory import create_provider
from mewcode.session import ChatSession
from mewcode.skills.manager import SkillManager
from mewcode.subagents.cache import FileReadCache
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from mewcode.mcp.manager import McpManager


ProviderResolver = Callable[[str | None], LLMProvider]


def create_provider_resolver(config: AppConfig, base_provider: LLMProvider) -> ProviderResolver:
    def resolve(model_override: str | None) -> LLMProvider:
        if not model_override or model_override == config.model:
            return base_provider
        return create_provider(replace(config, model=model_override))

    return resolve


def create_isolated_skill_runner(
    *,
    session: ChatSession,
    provider: LLMProvider,
    registry: ToolRegistry,
    executor: ToolExecutor,
    config: AppConfig,
    skill_manager: SkillManager,
    provider_resolver: ProviderResolver,
    hook_manager: object | None = None,
    mcp_manager: McpManager | None = None,
):
    from mewcode.agent import AgentLoopRunner

    read_cache = FileReadCache()
    child_executor = ToolExecutor(registry, replace(executor.context, read_cache=read_cache))
    child_hook_manager = None
    if hook_manager is not None:
        child_hook_manager = HookManager(hook_manager.config, hook_manager.action_runner)  # type: ignore[attr-defined]
    return AgentLoopRunner(
        session,
        provider,
        registry,
        child_executor,
        config.agent,
        permission_controller=create_permission_controller(
            executor.context.cwd,
            PermissionConfig(mode=config.permissions.mode),
        ),
        context_manager=ContextManager(config.context, executor.context.cwd, config.max_tokens),
        skill_manager=skill_manager,
        provider_resolver=provider_resolver,
        hook_manager=child_hook_manager,
        file_read_cache=read_cache,
        mcp_manager=mcp_manager,
    )
