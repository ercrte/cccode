from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from julycode.mcp.search import McpPromptContext
from julycode.mcp.tools import SEARCH_MCP_TOOLS_NAME
from julycode.tools.base import ToolResult
from julycode.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from julycode.tools.scheduler import ToolPolicy


class McpTurnState:
    def __init__(self, prompt_context_provider: Callable[[], McpPromptContext]) -> None:
        self._prompt_context_provider = prompt_context_provider
        self._active_tools: frozenset[str] = frozenset()

    def begin_turn(self) -> None:
        self._active_tools = frozenset()

    def apply_search_results(
        self,
        results: tuple[ToolResult, ...],
        *,
        policy: ToolPolicy,
        registry: ToolRegistry,
    ) -> tuple[ToolResult, ...]:
        processed: list[ToolResult] = []
        for result in results:
            if result.tool_name != SEARCH_MCP_TOOLS_NAME:
                processed.append(result)
                continue

            self._active_tools = frozenset()
            if not result.success:
                processed.append(result)
                continue

            raw_matches = result.data.get("matches", [])
            candidate_names: list[str] = []
            if isinstance(raw_matches, list):
                for item in raw_matches:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name")
                    if isinstance(name, str) and name not in candidate_names:
                        candidate_names.append(name)
                    if len(candidate_names) >= 5:
                        break

            candidate_policy = replace(
                policy,
                activated_deferred_tools=frozenset(candidate_names),
            )
            allowed = {spec.name for spec in candidate_policy.allowed_specs(registry)}
            activated = tuple(name for name in candidate_names if name in allowed)
            self._active_tools = frozenset(activated)

            data = dict(result.data)
            data["activated_tools"] = list(activated)
            data["filtered_count"] = len(candidate_names) - len(activated)
            if candidate_names and not activated and data.get("status") == "ok":
                data["status"] = "policy_filtered"
                data["message"] = "候选工具均被当前模式或工具策略过滤"
            processed.append(replace(result, data=data))
        return tuple(processed)

    def end_turn(self) -> None:
        self._active_tools = frozenset()

    @property
    def active_tools(self) -> frozenset[str]:
        return self._active_tools

    def prompt_context(self) -> McpPromptContext:
        return self._prompt_context_provider()
