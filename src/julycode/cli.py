from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from julycode.config import load_config
from julycode.context.manager import ContextManager
from julycode.commands import create_builtin_command_registry
from julycode.errors import JulyCodeError, redact_secret
from julycode.hooks.manager import create_hook_manager
from julycode.memory.manager import SessionMemoryManager
from julycode.memory.models import BootstrapOptions
from julycode.mcp.manager import create_mcp_manager
from julycode.permissions.controller import create_permission_controller
from julycode.providers.factory import create_provider
from julycode.session import ChatSession
from julycode.tools.base import ToolContext
from julycode.tools.executor import ToolExecutor
from julycode.tools.registry import create_default_registry
from julycode.tui.app import JulyCodeApp


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
    except (JulyCodeError, OSError, ValueError) as exc:
        print(f"JulyCode 配置错误: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    app = JulyCodeApp(
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
    except JulyCodeError as exc:
        print(f"JulyCode 配置错误: {redact_secret(str(exc))}", file=sys.stderr)
        return 1
    app.run()
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="julycode")
    parser.add_argument("--new-session", action="store_true", help="启动空会话，不自动恢复最近历史")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
