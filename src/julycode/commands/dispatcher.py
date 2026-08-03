from __future__ import annotations

from julycode.commands.models import CommandContext, CommandInvocation, EmptyInput, PlainInput, UnknownCommandInput
from julycode.commands.registry import CommandRegistry
from julycode.errors import redact_secret


class CommandDispatcher:
    def __init__(self, registry: CommandRegistry) -> None:
        self.registry = registry

    async def dispatch(self, raw_text: str, context: CommandContext) -> bool:
        parsed = self.registry.parse(raw_text)
        if isinstance(parsed, EmptyInput):
            return True
        if isinstance(parsed, PlainInput):
            return False
        if isinstance(parsed, UnknownCommandInput):
            await context.show_assistant(f"未知命令 `{parsed.command_text}`。输入 `/help` 查看可用命令。")
            return True
        if isinstance(parsed, CommandInvocation):
            if parsed.definition.handler is None:
                await context.show_error(f"命令 `/{parsed.definition.name}` 未配置处理函数。")
                return True
            try:
                await parsed.definition.handler(parsed, context)
            except Exception as exc:
                await context.show_error(f"命令执行失败: {redact_secret(str(exc))}")
            return True
        return False
