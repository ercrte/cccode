from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from mewcode.hooks import parse_hook_config
from mewcode.hooks.manager import create_hook_manager
from mewcode.hooks.models import HookRuntimeContext
from mewcode.permissions import PermissionConfig
from mewcode.permissions.controller import create_permission_controller
from mewcode.tools.base import ToolCall, ToolContext, ToolSpec
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry
from mewcode.tools.scheduler import ToolCallScheduler, ToolPolicy


class FakeTool:
    def __init__(
        self,
        name: str,
        *,
        safety: str,
        visibility: str = "model",
        delay: float = 0.0,
        log: list[str] | None = None,
    ) -> None:
        self.spec = ToolSpec(
            name=name,
            description=name,
            parameters_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            safety=safety,  # type: ignore[arg-type]
            visibility=visibility,  # type: ignore[arg-type]
        )
        self.delay = delay
        self.log = log

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = arguments, context
        if self.log is not None:
            self.log.append(f"start:{self.spec.name}")
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.log is not None:
            self.log.append(f"end:{self.spec.name}")
        return {"name": self.spec.name}


def make_registry(*tools: FakeTool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def make_scheduler(registry: ToolRegistry, tmp_path: Path, mode: str = "normal") -> ToolCallScheduler:
    return ToolCallScheduler(
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        ToolPolicy(mode=mode),  # type: ignore[arg-type]
    )


def make_hook_context(registry: ToolRegistry, tmp_path: Path, mode: str = "normal") -> HookRuntimeContext:
    return HookRuntimeContext(
        cwd=tmp_path,
        mode=mode,  # type: ignore[arg-type]
        allowed_tool_names=None,
        registry=registry,
        executor=ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        permission_controller=None,
    )


class ChoicePrompter:
    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.prompts = []

    async def request_permission(self, prompt):
        self.prompts.append(prompt)
        return self.choice


async def collect(scheduler: ToolCallScheduler, calls: list[ToolCall]) -> list[object]:
    return [event async for event in scheduler.run(calls)]


def test_tool_policy_allows_all_tools_in_normal_mode() -> None:
    registry = make_registry(FakeTool("read", safety="read_only"), FakeTool("write", safety="side_effect"))

    normal_names = {spec.name for spec in ToolPolicy("normal").allowed_specs(registry)}

    assert normal_names == {"read", "write"}


def test_tool_policy_allows_only_read_tools_in_plan_mode() -> None:
    registry = make_registry(FakeTool("read", safety="read_only"), FakeTool("write", safety="side_effect"))

    names = {spec.name for spec in ToolPolicy("plan").allowed_specs(registry)}

    assert names == {"read"}


def test_tool_policy_blocks_side_effect_tool_in_plan_mode() -> None:
    registry = make_registry(FakeTool("write", safety="side_effect"))
    call = ToolCall(id="c1", name="write")

    result = ToolPolicy("plan").validate_call(call, registry)

    assert result is not None
    assert result.success is False
    assert result.error_type == "tool_not_allowed"


def test_tool_policy_reports_unknown_tool() -> None:
    result = ToolPolicy("normal").validate_call(ToolCall(id="c1", name="missing"), ToolRegistry())

    assert result is not None
    assert result.error_type == "unknown_tool"


def test_tool_policy_hides_deferred_until_activated() -> None:
    registry = make_registry(
        FakeTool("read", safety="read_only"),
        FakeTool("github__search_code", safety="side_effect", visibility="deferred"),
        FakeTool("search_mcp_tools", safety="read_only", visibility="system"),
    )

    initial = {spec.name for spec in ToolPolicy("normal").allowed_specs(registry)}
    active = {
        spec.name
        for spec in ToolPolicy(
            "normal",
            activated_deferred_tools=frozenset({"github__search_code"}),
        ).allowed_specs(registry)
    }

    assert initial == {"read", "search_mcp_tools"}
    assert active == {"read", "search_mcp_tools", "github__search_code"}


def test_tool_policy_rejects_unloaded_deferred_call() -> None:
    registry = make_registry(FakeTool("github__search_code", safety="side_effect", visibility="deferred"))

    unloaded = ToolPolicy("normal").validate_call(
        ToolCall(id="c1", name="github__search_code"),
        registry,
    )
    loaded = ToolPolicy(
        "normal",
        activated_deferred_tools=frozenset({"github__search_code"}),
    ).validate_call(ToolCall(id="c2", name="github__search_code"), registry)

    assert unloaded is not None
    assert unloaded.error_type == "tool_not_loaded"
    assert loaded is None


def test_make_batches_groups_consecutive_read_tools_and_splits_side_effects(tmp_path: Path) -> None:
    registry = make_registry(
        FakeTool("read_a", safety="read_only"),
        FakeTool("read_b", safety="read_only"),
        FakeTool("write", safety="side_effect"),
        FakeTool("read_c", safety="read_only"),
    )
    scheduler = make_scheduler(registry, tmp_path)

    batches = scheduler.make_batches(
        [
            ToolCall(id="c1", name="read_a"),
            ToolCall(id="c2", name="read_b"),
            ToolCall(id="c3", name="write"),
            ToolCall(id="c4", name="read_c"),
        ]
    )

    assert [(tuple(call.name for call in batch.calls), batch.concurrent) for batch in batches] == [
        (("read_a", "read_b"), True),
        (("write",), False),
        (("read_c",), True),
    ]


@pytest.mark.asyncio
async def test_read_only_batch_runs_concurrently(tmp_path: Path) -> None:
    registry = make_registry(
        FakeTool("read_a", safety="read_only", delay=0.05),
        FakeTool("read_b", safety="read_only", delay=0.05),
    )
    scheduler = make_scheduler(registry, tmp_path)
    started = time.monotonic()

    events = await collect(scheduler, [ToolCall(id="c1", name="read_a"), ToolCall(id="c2", name="read_b")])

    assert time.monotonic() - started < 0.09
    assert [event.type for event in events] == ["tool_started", "tool_started", "tool_finished", "tool_finished"]
    assert [result.tool_name for result in scheduler.results()] == ["read_a", "read_b"]


@pytest.mark.asyncio
async def test_read_only_batch_emits_finished_events_as_each_tool_completes(tmp_path: Path) -> None:
    registry = make_registry(
        FakeTool("slow", safety="read_only", delay=0.08),
        FakeTool("fast", safety="read_only", delay=0.01),
    )
    scheduler = make_scheduler(registry, tmp_path)

    events = await collect(scheduler, [ToolCall(id="c1", name="slow"), ToolCall(id="c2", name="fast")])

    finished_names = [
        event.tool_result.tool_name
        for event in events
        if event.type == "tool_finished" and event.tool_result is not None
    ]
    assert finished_names == ["fast", "slow"]
    assert [result.tool_name for result in scheduler.results()] == ["slow", "fast"]


@pytest.mark.asyncio
async def test_side_effect_tools_run_serially(tmp_path: Path) -> None:
    log: list[str] = []
    registry = make_registry(
        FakeTool("write_a", safety="side_effect", delay=0.02, log=log),
        FakeTool("write_b", safety="side_effect", delay=0.02, log=log),
    )
    scheduler = make_scheduler(registry, tmp_path)

    await collect(scheduler, [ToolCall(id="c1", name="write_a"), ToolCall(id="c2", name="write_b")])

    assert log == ["start:write_a", "end:write_a", "start:write_b", "end:write_b"]
    assert [result.tool_name for result in scheduler.results()] == ["write_a", "write_b"]


@pytest.mark.asyncio
async def test_scheduler_keeps_results_in_original_call_order_for_mixed_tools(tmp_path: Path) -> None:
    registry = make_registry(
        FakeTool("read_a", safety="read_only"),
        FakeTool("write", safety="side_effect"),
        FakeTool("read_b", safety="read_only"),
    )
    scheduler = make_scheduler(registry, tmp_path)

    await collect(
        scheduler,
        [
            ToolCall(id="c1", name="read_a"),
            ToolCall(id="c2", name="write"),
            ToolCall(id="c3", name="read_b"),
        ],
    )

    assert [result.tool_name for result in scheduler.results()] == ["read_a", "write", "read_b"]


@pytest.mark.asyncio
async def test_scheduler_returns_policy_failure_without_executing_tool(tmp_path: Path) -> None:
    log: list[str] = []
    registry = make_registry(FakeTool("write", safety="side_effect", log=log))
    scheduler = make_scheduler(registry, tmp_path, mode="plan")

    events = await collect(scheduler, [ToolCall(id="c1", name="write")])

    assert log == []
    assert scheduler.results()[0].error_type == "tool_not_allowed"
    assert events[-1].tool_result is not None
    assert events[-1].tool_result.error_type == "tool_not_allowed"


@pytest.mark.asyncio
async def test_scheduler_permission_allow_executes_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    log: list[str] = []
    registry = make_registry(FakeTool("write", safety="side_effect", log=log))
    controller = create_permission_controller(tmp_path, PermissionConfig(mode="permissive"))
    scheduler = ToolCallScheduler(
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        ToolPolicy("normal"),
        controller,
    )

    await collect(scheduler, [ToolCall(id="c1", name="write")])

    assert log == ["start:write", "end:write"]
    assert scheduler.results()[0].success is True


@pytest.mark.asyncio
async def test_scheduler_permission_deny_skips_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / ".mewcode.permissions.local.yaml").write_text('rules:\n  "write(*)": deny\n', encoding="utf-8")
    log: list[str] = []
    registry = make_registry(FakeTool("write", safety="side_effect", log=log))
    controller = create_permission_controller(tmp_path, PermissionConfig(mode="permissive"))
    scheduler = ToolCallScheduler(
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        ToolPolicy("normal"),
        controller,
    )

    await collect(scheduler, [ToolCall(id="c1", name="write")])

    assert log == []
    assert scheduler.results()[0].error_type == "permission_rule_denied"


@pytest.mark.asyncio
async def test_scheduler_permission_prompt_allow_executes_after_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    log: list[str] = []
    registry = make_registry(FakeTool("write", safety="side_effect", log=log))
    controller = create_permission_controller(tmp_path, PermissionConfig(), ChoicePrompter("allow_once"))
    scheduler = ToolCallScheduler(
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        ToolPolicy("normal"),
        controller,
    )

    events = await collect(scheduler, [ToolCall(id="c1", name="write")])

    assert [event.type for event in events] == [
        "tool_started",
        "permission_requested",
        "permission_resolved",
        "tool_finished",
    ]
    assert log == ["start:write", "end:write"]
    assert scheduler.results()[0].success is True


@pytest.mark.asyncio
async def test_scheduler_permission_prompt_deny_returns_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    log: list[str] = []
    registry = make_registry(FakeTool("write", safety="side_effect", log=log))
    controller = create_permission_controller(tmp_path, PermissionConfig(), ChoicePrompter("deny"))
    scheduler = ToolCallScheduler(
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        ToolPolicy("normal"),
        controller,
    )

    events = await collect(scheduler, [ToolCall(id="c1", name="write")])

    assert log == []
    assert [event.type for event in events] == [
        "tool_started",
        "permission_requested",
        "permission_resolved",
        "tool_finished",
    ]
    assert scheduler.results()[0].error_type == "permission_user_denied"


@pytest.mark.asyncio
async def test_scheduler_plan_mode_blocks_side_effect_before_permission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    prompter = ChoicePrompter("allow_once")
    log: list[str] = []
    registry = make_registry(FakeTool("write", safety="side_effect", log=log))
    controller = create_permission_controller(tmp_path, PermissionConfig(mode="permissive"), prompter)
    scheduler = ToolCallScheduler(
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        ToolPolicy("plan"),
        controller,
    )

    events = await collect(scheduler, [ToolCall(id="c1", name="write")])

    assert log == []
    assert prompter.prompts == []
    assert scheduler.results()[0].error_type == "tool_not_allowed"
    assert [event.type for event in events] == ["tool_started", "tool_finished"]


@pytest.mark.asyncio
async def test_hook_before_blocks_before_permission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    log: list[str] = []
    registry = make_registry(FakeTool("write", safety="side_effect", log=log))
    controller = create_permission_controller(tmp_path, PermissionConfig(mode="permissive"))
    hook_manager = create_hook_manager(
        parse_hook_config(
            [
                {
                    "name": "block-write",
                    "event": "tool.before",
                    "if": {"all": [{"field": "tool.name", "match": "write"}]},
                    "action": {"type": "prompt", "text": "x", "tool_block": {"reason": "blocked by hook"}},
                }
            ]
        )
    )
    scheduler = ToolCallScheduler(
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        ToolPolicy("normal"),
        controller,
        hook_manager=hook_manager,
        hook_context=make_hook_context(registry, tmp_path),
    )

    events = await collect(scheduler, [ToolCall(id="c1", name="write")])

    assert log == []
    assert scheduler.results()[0].error_type == "hook_blocked"
    assert [event.type for event in events] == ["tool_started", "hook_finished", "hook_finished", "tool_finished"]


@pytest.mark.asyncio
async def test_hook_before_allows_existing_policy_permission_and_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    log: list[str] = []
    registry = make_registry(FakeTool("write", safety="side_effect", log=log))
    hook_manager = create_hook_manager(
        parse_hook_config([{"name": "observe", "event": "tool.before", "action": {"type": "prompt", "text": "x"}}])
    )
    scheduler = ToolCallScheduler(
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        ToolPolicy("normal"),
        create_permission_controller(tmp_path, PermissionConfig(mode="permissive")),
        hook_manager=hook_manager,
        hook_context=make_hook_context(registry, tmp_path),
    )

    events = await collect(scheduler, [ToolCall(id="c1", name="write")])

    assert log == ["start:write", "end:write"]
    assert scheduler.results()[0].success is True
    assert "hook_finished" in [event.type for event in events]


@pytest.mark.asyncio
async def test_after_tool_emits_result_event(tmp_path: Path) -> None:
    registry = make_registry(FakeTool("read", safety="read_only"))
    hook_manager = create_hook_manager(
        parse_hook_config(
            [
                {
                    "name": "after",
                    "event": "tool.after",
                    "if": {"all": [{"field": "result.success", "match": "True"}]},
                    "action": {"type": "prompt", "text": "done"},
                }
            ]
        )
    )
    scheduler = ToolCallScheduler(
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        ToolPolicy("normal"),
        hook_manager=hook_manager,
        hook_context=make_hook_context(registry, tmp_path),
    )

    events = await collect(scheduler, [ToolCall(id="c1", name="read")])

    hook_events = [event for event in events if event.type == "hook_finished"]
    assert hook_events
    assert hook_events[-1].hook_result is not None
    assert hook_events[-1].hook_result.rule_id == "after"
