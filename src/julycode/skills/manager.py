from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from julycode.commands.registry import CommandRegistry, CommandRegistryError
from julycode.errors import JulyCodeError
from julycode.skills.commands import register_skill_commands
from julycode.skills.loader import SkillLoader
from julycode.skills.models import (
    SkillActivation,
    SkillCatalog,
    SkillDefinition,
    SkillPromptContext,
    SkillRefreshReport,
    SkillRoots,
)
from julycode.skills.tools import LOAD_SKILL_TOOL_NAME, SkillScriptTool
from julycode.tools.registry import ToolRegistry


class SkillConfigurationError(JulyCodeError):
    pass


@dataclass(frozen=True)
class _ResolvedTools:
    names: tuple[str, ...]
    missing: tuple[str, ...]


class SkillManager:
    def __init__(self, roots: SkillRoots, tool_registry: ToolRegistry) -> None:
        self.roots = roots
        self.tool_registry = tool_registry
        self.loader = SkillLoader(roots)
        self.catalog = SkillCatalog(definitions={})
        self.active: dict[str, SkillActivation] = {}
        self._loaded_tool_origins: set[str] = set()

    def refresh_if_changed(self, command_registry: CommandRegistry | None = None) -> SkillRefreshReport:
        catalog = self.loader.discover()
        if catalog.fingerprint == self.catalog.fingerprint:
            if command_registry is not None:
                register_skill_commands(command_registry, tuple(self.catalog.definitions.values()))
            return SkillRefreshReport(changed=False, warnings=catalog.warnings)

        self._unregister_dynamic_tools()
        if command_registry is not None:
            command_registry.unregister_origin("skills")
        self.active.clear()

        errors = self._validation_errors(catalog)
        if errors:
            raise SkillConfigurationError("Skill 配置错误:\n" + "\n".join(f"- {error}" for error in errors))

        try:
            if command_registry is not None:
                register_skill_commands(command_registry, tuple(catalog.definitions.values()))
        except CommandRegistryError as exc:
            raise SkillConfigurationError(str(exc)) from exc

        self.catalog = catalog
        return SkillRefreshReport(changed=True, warnings=catalog.warnings)

    def load(self, name: str, arguments: Any = "") -> SkillActivation:
        definition = self.catalog.definitions.get(name)
        if definition is None:
            available = ", ".join(sorted(self.catalog.definitions)) or "无"
            raise SkillConfigurationError(f"未知 Skill: {name}。可用 Skill: {available}")

        self._register_dedicated_tools(definition)
        rendered_arguments = _argument_text(arguments)
        activation = SkillActivation(
            name=definition.name,
            arguments=rendered_arguments,
            rendered_body=_render_body(definition.body, arguments),
            mode=definition.frontmatter.mode,
            history=definition.frontmatter.history,
            tool_whitelist=self._resolved_tool_names(definition).names,
            model=definition.frontmatter.model,
            source_path=definition.source_path,
        )
        self.active[definition.name] = activation
        return activation

    def clear_active(self) -> None:
        self.active.clear()
        self._unregister_dynamic_tools()

    def deactivate(self, name: str) -> None:
        self.active.pop(name, None)
        origin = f"skill:{name}"
        if origin in self._loaded_tool_origins:
            self.tool_registry.unregister_origin(origin)
            self._loaded_tool_origins.remove(origin)

    def prompt_context(self) -> SkillPromptContext:
        return SkillPromptContext(
            available=tuple(definition.summary() for definition in self.catalog.definitions.values()),
            active=tuple(self.active.values()),
            warnings=self.catalog.warnings,
        )

    def active_tool_whitelist(self) -> frozenset[str] | None:
        if not self.active:
            return None
        names: set[str] = set()
        for activation in self.active.values():
            names.update(activation.tool_whitelist)
        return frozenset(names)

    def active_dedicated_tools(self) -> tuple[str, ...]:
        names: list[str] = []
        active_names = set(self.active)
        for definition in self.catalog.definitions.values():
            if definition.name not in active_names:
                continue
            names.extend(tool.global_name for tool in definition.tool_definitions)
        return tuple(names)

    def resolve_model_override(self) -> str | None:
        for activation in reversed(tuple(self.active.values())):
            if activation.model:
                return activation.model
        return None

    def summary_for_command(self, name: str) -> SkillActivation | None:
        return self.active.get(name)

    def _register_dedicated_tools(self, definition: SkillDefinition) -> None:
        if not definition.tool_definitions:
            return
        origin = f"skill:{definition.name}"
        if origin in self._loaded_tool_origins:
            return
        for tool_definition in definition.tool_definitions:
            if self.tool_registry.get(tool_definition.global_name) is None:
                self.tool_registry.register(SkillScriptTool(definition.name, tool_definition))
        self._loaded_tool_origins.add(origin)

    def _unregister_dynamic_tools(self) -> None:
        for origin in tuple(self._loaded_tool_origins):
            self.tool_registry.unregister_origin(origin)
        self._loaded_tool_origins.clear()

    def _validation_errors(self, catalog: SkillCatalog) -> tuple[str, ...]:
        errors: list[str] = []
        registered = self.tool_registry.names()
        registered.add(LOAD_SKILL_TOOL_NAME)
        for definition in catalog.definitions.values():
            resolved = self._resolved_tool_names(definition, registered=registered)
            for missing in resolved.missing:
                errors.append(
                    f"Skill `{definition.name}` 的 tools 白名单引用不存在的工具 `{missing}` "
                    f"({definition.source_path})"
                )
        return tuple(errors)

    def _resolved_tool_names(
        self,
        definition: SkillDefinition,
        *,
        registered: set[str] | None = None,
    ) -> _ResolvedTools:
        registered_names = set(registered or self.tool_registry.names())
        own_tools_by_local = {tool.local_name: tool.global_name for tool in definition.tool_definitions}
        own_global = {tool.global_name for tool in definition.tool_definitions}
        names: list[str] = []
        missing: list[str] = []
        for raw_name in definition.frontmatter.tools:
            name = own_tools_by_local.get(raw_name, raw_name)
            if name in own_global or name in registered_names or name == LOAD_SKILL_TOOL_NAME:
                names.append(name)
            else:
                missing.append(raw_name)
        return _ResolvedTools(names=tuple(dict.fromkeys(names)), missing=tuple(missing))


def _argument_text(arguments: Any) -> str:
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)


def _render_body(body: str, arguments: Any) -> str:
    text = body
    argument_text = _argument_text(arguments)
    text = text.replace("{{input}}", argument_text)
    text = text.replace("{{args}}", argument_text)
    if isinstance(arguments, dict):
        for key, value in arguments.items():
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                text = text.replace("{{" + str(key) + "}}", _argument_text(value))
    return text
