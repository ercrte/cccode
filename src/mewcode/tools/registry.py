from __future__ import annotations

from mewcode.tools.base import Tool, ToolSafety, ToolSpec
from mewcode.tools.builtin import (
    EditFileTool,
    FindFilesTool,
    ReadFileTool,
    RunCommandTool,
    SearchCodeTool,
    WriteFileTool,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"工具已注册: {tool.spec.name}")
        self._tools[tool.spec.name] = tool

    def unregister_origin(self, origin: str) -> None:
        for name, tool in list(self._tools.items()):
            if tool.spec.origin == origin:
                del self._tools[name]

    def names(self) -> set[str]:
        return set(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> tuple[Tool, ...]:
        return tuple(self._tools.values())

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._tools.values())

    def specs_by_safety(self, safety: ToolSafety) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._tools.values() if tool.spec.safety == safety)


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(RunCommandTool())
    registry.register(FindFilesTool())
    registry.register(SearchCodeTool())
    return registry
