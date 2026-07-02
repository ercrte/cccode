from __future__ import annotations

from mewcode.commands.models import (
    CommandCompletion,
    CommandDefinition,
    CommandInvocation,
    EmptyInput,
    ParsedInput,
    PlainInput,
    UnknownCommandInput,
)
from mewcode.errors import MewCodeError


class CommandRegistryError(MewCodeError):
    pass


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: list[CommandDefinition] = []
        self._entries: dict[str, CommandDefinition] = {}
        self._entry_labels: dict[str, str] = {}

    def register(self, definition: CommandDefinition) -> None:
        entries = (definition.name, *definition.aliases)
        normalized_entries = [_normalize_entry(entry) for entry in entries]
        for original, normalized in zip(entries, normalized_entries, strict=True):
            existing = self._entries.get(normalized)
            if existing is not None:
                label = self._entry_labels[normalized]
                raise CommandRegistryError(
                    "命令入口冲突: "
                    f"`/{label}` 同时指向 `/{existing.name}` 和 `/{definition.name}`"
                )
            if normalized_entries.count(normalized) > 1:
                raise CommandRegistryError(f"命令 `/{definition.name}` 内部入口冲突: `/{_strip_slash(original)}`")

        self._commands.append(definition)
        for original, normalized in zip(entries, normalized_entries, strict=True):
            self._entries[normalized] = definition
            self._entry_labels[normalized] = _strip_slash(original)

    def unregister_origin(self, origin: str) -> None:
        removed = [command for command in self._commands if command.origin == origin]
        if not removed:
            return
        self._commands = [command for command in self._commands if command.origin != origin]
        removed_ids = {id(command) for command in removed}
        for entry, definition in list(self._entries.items()):
            if id(definition) in removed_ids:
                del self._entries[entry]
                del self._entry_labels[entry]

    def get(self, name: str) -> CommandDefinition | None:
        return self._entries.get(_normalize_entry(name))

    def parse(self, raw_text: str) -> ParsedInput:
        text = raw_text.strip()
        if not text:
            return EmptyInput()
        if not text.startswith("/"):
            return PlainInput(text)

        command_text, _, argument = text.partition(" ")
        definition = self.get(command_text)
        if definition is None:
            return UnknownCommandInput(raw_text=text, command_text=command_text)
        return CommandInvocation(
            definition=definition,
            raw_text=text,
            command_text=command_text,
            argument=argument.strip(),
            matched_name=_strip_slash(command_text),
        )

    def visible_commands(self) -> tuple[CommandDefinition, ...]:
        return tuple(command for command in self._commands if not command.hidden)

    def completion(self, raw_text: str) -> CommandCompletion:
        text = raw_text.strip()
        if not text.startswith("/") or " " in text:
            return CommandCompletion(replacement=None, options=())

        prefix = _strip_slash(text).casefold()
        candidates: list[CommandDefinition] = []
        seen: set[str] = set()
        for command in self.visible_commands():
            entries = (command.name, *command.aliases)
            if any(_strip_slash(entry).casefold().startswith(prefix) for entry in entries):
                if command.name not in seen:
                    candidates.append(command)
                    seen.add(command.name)

        candidates.sort(key=lambda command: command.name)
        replacement = f"/{candidates[0].name}" if len(candidates) == 1 else None
        return CommandCompletion(replacement=replacement, options=tuple(candidates))


def _normalize_entry(entry: str) -> str:
    label = _strip_slash(entry)
    if not label:
        raise CommandRegistryError("命令入口不能为空")
    if any(char.isspace() for char in label):
        raise CommandRegistryError(f"命令入口不能包含空白: `/{label}`")
    return label.casefold()


def _strip_slash(entry: str) -> str:
    return str(entry).strip().removeprefix("/")
