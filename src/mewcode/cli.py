from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from mewcode.config import load_config
from mewcode.context.manager import ContextManager
from mewcode.commands import create_builtin_command_registry
from mewcode.errors import MewCodeError, redact_secret
from mewcode.hooks.manager import create_hook_manager
from mewcode.memory.manager import SessionMemoryManager
from mewcode.memory.models import BootstrapOptions
from mewcode.mcp.manager import create_mcp_manager
from mewcode.permissions.controller import create_permission_controller
from mewcode.providers.factory import create_provider
from mewcode.session import ChatSession
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import create_default_registry
from mewcode.tui.app import MewCodeApp


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        command_registry = create_builtin_command_registry()
        config = load_config()
        hook_manager = create_hook_manager(config.hooks)
        provider = create_provider(config)
        registry = create_default_registry()
        mcp_manager = create_mcp_manager(config.mcp)
        cwd = Path.cwd()
        executor = ToolExecutor(registry, ToolContext(cwd=cwd))
        context_manager = ContextManager(config.context, cwd, config.max_tokens)
        memory_manager = None
        restore_report = None
        if config.memory.enabled:
            memory_manager = SessionMemoryManager(cwd, config.memory)
            bootstrap = asyncio.run(
                memory_manager.bootstrap(
                    options=BootstrapOptions(new_session=args.new_session),
                    provider=provider,
                    context_manager=context_manager,
                )
            )
            session = bootstrap.session
            restore_report = bootstrap.restore_report
        else:
            session = ChatSession()
    except (MewCodeError, OSError, ValueError) as exc:
        print(f"MewCode 配置错误: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    app = MewCodeApp(
        session,
        provider,
        config,
        registry,
        executor,
        mcp_manager=mcp_manager,
        context_manager=context_manager,
        memory_manager=memory_manager,
        restore_report=restore_report,
        command_registry=command_registry,
        hook_manager=hook_manager,
    )
    try:
        app.set_permission_controller(create_permission_controller(cwd, config.permissions, app))
    except MewCodeError as exc:
        print(f"MewCode 配置错误: {redact_secret(str(exc))}", file=sys.stderr)
        return 1
    app.run()
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mewcode")
    parser.add_argument("--new-session", action="store_true", help="启动空会话，不自动恢复最近历史")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
