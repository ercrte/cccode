from __future__ import annotations

from julycode.commands.models import CommandContext, CommandDefinition, CommandInvocation
from julycode.commands.registry import CommandRegistry
from julycode.skills.models import SkillDefinition


SKILL_COMMAND_ORIGIN = "skills"


def register_skill_commands(registry: CommandRegistry, definitions: tuple[SkillDefinition, ...]) -> None:
    registry.unregister_origin(SKILL_COMMAND_ORIGIN)
    for definition in sorted(definitions, key=lambda item: item.name):
        registry.register(_command_for_skill(definition))


def _command_for_skill(definition: SkillDefinition) -> CommandDefinition:
    async def handler(invocation: CommandInvocation, context: CommandContext) -> None:
        await context.invoke_skill(
            name=definition.name,
            arguments=invocation.argument,
            visible_text=invocation.raw_text,
        )

    return CommandDefinition(
        name=definition.name,
        aliases=(),
        description=definition.description,
        usage=f"/{definition.name} [参数]",
        kind="prompt",
        argument_hint="传给 Skill 的参数",
        handler=handler,
        origin=SKILL_COMMAND_ORIGIN,
    )
