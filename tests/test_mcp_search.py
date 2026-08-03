from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from julycode.mcp.scope import McpTurnState
from julycode.mcp.search import McpPromptContext, McpToolCatalog, normalize_search_text
from julycode.mcp.tools import McpToolDefinition
from julycode.tools.base import ToolContext, ToolResult, ToolSpec
from julycode.tools.registry import ToolRegistry
from julycode.tools.scheduler import ToolPolicy


def definition(
    remote_name: str,
    description: str,
    *,
    server: str = "github",
    title: str | None = None,
) -> McpToolDefinition:
    return McpToolDefinition(
        server_name=server,
        remote_name=remote_name,
        global_name=f"{server}__{remote_name}",
        title=title,
        description=description,
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


class FakeTool:
    def __init__(self, name: str, *, visibility: str = "deferred", safety: str = "side_effect") -> None:
        self.spec = ToolSpec(
            name=name,
            description=name,
            parameters_schema={"type": "object", "properties": {}},
            visibility=visibility,  # type: ignore[arg-type]
            safety=safety,  # type: ignore[arg-type]
        )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = arguments, context
        return {}


def test_normalize_search_text_handles_unicode_case_and_separators() -> None:
    assert normalize_search_text("ＰＵＬＬ_Request-Read") == "pull request read"


def test_catalog_replaces_server_preserves_schema_and_searchable_subset() -> None:
    catalog = McpToolCatalog()
    first = definition("search_code", "Search source code")
    second = definition("get_me", "Authenticated user profile")
    catalog.replace_server("github", (first, second))
    catalog.set_searchable({first.global_name})

    assert catalog.get(first.global_name) is first
    assert catalog.get(first.global_name).input_schema["required"] == ["query"]  # type: ignore[union-attr]
    assert catalog.server_summaries()[0].tool_count == 1
    assert [item.global_name for item in catalog.search("search code")] == [first.global_name]

    replacement = definition("list_issues", "List repository issues")
    catalog.replace_server("github", (replacement,))
    catalog.set_searchable({replacement.global_name})
    assert catalog.get(first.global_name) is None
    assert catalog.get(replacement.global_name) is replacement


def test_search_ranking_server_filter_limit_and_stable_tie_break() -> None:
    catalog = McpToolCatalog()
    definitions = (
        definition("pull_request_read", "Read pull request details", title="Pull request reader"),
        definition("search_pull_requests", "Search pull requests"),
        definition("issue_read", "Read issue details"),
        definition("pull_request_read", "Read pull request details", server="gitlab"),
        *(definition(f"pull_request_extra_{index}", "Pull request helper") for index in range(8)),
    )
    github = tuple(item for item in definitions if item.server_name == "github")
    gitlab = tuple(item for item in definitions if item.server_name == "gitlab")
    catalog.replace_server("github", github)
    catalog.replace_server("gitlab", gitlab)
    catalog.set_searchable({item.global_name for item in definitions})

    first = catalog.search("pull request", server_name="github")
    second = catalog.search("pull request", server_name="github")

    assert first == second
    assert len(first) == 5
    assert all(item.server_name == "github" for item in first)
    assert first[0].global_name == "github__pull_request_read"
    assert catalog.search("totally-unmatched-capability") == ()


def test_search_summary_is_compact_and_does_not_include_schema() -> None:
    catalog = McpToolCatalog()
    item = definition("search_code", "Long   description\n" + "x" * 300)
    catalog.replace_server("github", (item,))
    catalog.set_searchable({item.global_name})

    match = catalog.search("search code")[0]

    assert len(match.summary) <= 160
    assert "  " not in match.summary
    assert "properties" not in match.summary


def test_search_performance_for_one_thousand_tools() -> None:
    catalog = McpToolCatalog()
    definitions = tuple(
        definition(f"tool_{index}_search", f"Search repository object number {index}")
        for index in range(1000)
    )
    catalog.replace_server("github", definitions)
    catalog.set_searchable({item.global_name for item in definitions})

    started = time.perf_counter()
    matches = catalog.search("search repository")
    elapsed = time.perf_counter() - started

    assert len(matches) == 5
    assert elapsed < 0.1


def search_result(*names: str, status: str = "ok") -> ToolResult:
    return ToolResult(
        tool_call_id="search-1",
        tool_name="search_mcp_tools",
        success=True,
        data={
            "status": status,
            "matches": [
                {"name": name, "server": name.partition("__")[0], "title": None, "summary": name}
                for name in names
            ],
            "activated_tools": [],
        },
    )


def test_turn_state_replaces_candidates_filters_policy_and_is_isolated() -> None:
    registry = ToolRegistry()
    for name in ("github__get_me", "github__search_code", "github__issue_write"):
        registry.register(FakeTool(name))
    first_state = McpTurnState(lambda: McpPromptContext())
    second_state = McpTurnState(lambda: McpPromptContext())
    first_state.begin_turn()
    second_state.begin_turn()

    first = first_state.apply_search_results(
        (search_result("github__get_me", "github__search_code"),),
        policy=ToolPolicy("normal"),
        registry=registry,
    )
    second_state.apply_search_results(
        (search_result("github__issue_write"),),
        policy=ToolPolicy("normal"),
        registry=registry,
    )

    assert first_state.active_tools == frozenset({"github__get_me", "github__search_code"})
    assert second_state.active_tools == frozenset({"github__issue_write"})
    assert first[0].data["activated_tools"] == ["github__get_me", "github__search_code"]

    first_state.apply_search_results(
        (search_result("github__issue_write"),),
        policy=ToolPolicy("plan"),
        registry=registry,
    )
    assert first_state.active_tools == frozenset()
    first_state.end_turn()
    second_state.end_turn()
    assert first_state.active_tools == second_state.active_tools == frozenset()
