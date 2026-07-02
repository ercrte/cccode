from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from mewcode.hooks import HookConfig, HookEvent, create_hook_manager, parse_hook_config
from mewcode.hooks.actions import HookActionRunner
from mewcode.hooks.conditions import rule_matches
from mewcode.hooks.manager import HookManager
from mewcode.hooks.models import HookRuntimeContext
from mewcode.permissions import PermissionConfig
from mewcode.permissions.controller import create_permission_controller
from mewcode.tools.base import ToolCall, ToolContext, ToolExecutionError, ToolResult, ToolSpec
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry, create_default_registry


class FakeTool:
    def __init__(self, name: str, *, safety: str = "read_only", log: list[str] | None = None) -> None:
        self.spec = ToolSpec(
            name=name,
            description=name,
            parameters_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            safety=safety,  # type: ignore[arg-type]
        )
        self.log = log

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = arguments, context
        if self.log is not None:
            self.log.append(self.spec.name)
        return {"ok": True}


class FailingRunner(HookActionRunner):
    async def run(self, rule, event, context):  # type: ignore[override]
        raise RuntimeError("boom")


def hook_context(tmp_path: Path, *, mode: str = "normal", whitelist: set[str] | None = None) -> HookRuntimeContext:
    registry = create_default_registry()
    return HookRuntimeContext(
        cwd=tmp_path,
        mode=mode,  # type: ignore[arg-type]
        allowed_tool_names=None if whitelist is None else frozenset(whitelist),
        registry=registry,
        executor=ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        permission_controller=None,
    )


def config(raw: list[dict[str, object]]) -> HookConfig:
    return parse_hook_config(raw)


def test_hook_models_are_importable() -> None:
    assert HookConfig().rules == ()
    assert create_hook_manager(HookConfig()).config.rules == ()


def test_hook_conditions_match_expected_events() -> None:
    rules = config(
        [
            {"event": "turn.start", "action": {"type": "prompt", "text": "always"}},
            {
                "event": "tool.before",
                "if": {"all": [{"field": "tool.name", "match": "run_command"}]},
                "action": {"type": "prompt", "text": "all"},
            },
            {
                "event": "tool.before",
                "if": {"any": [{"field": "tool.name", "match": "missing"}, {"field": "tool.arguments.command", "match": "git *"}]},
                "action": {"type": "prompt", "text": "any"},
            },
        ]
    ).rules
    turn = HookEvent("turn.start", {"turn": {"mode": "normal"}})
    tool = HookEvent("tool.before", {"tool": {"name": "run_command", "arguments": {"command": "git status"}}})

    assert rule_matches(rules[0], turn)
    assert rule_matches(rules[1], tool)
    assert rule_matches(rules[2], tool)
    assert not rule_matches(rules[1], turn)


def test_missing_condition_field_does_not_raise() -> None:
    [rule] = config(
        [
            {
                "event": "tool.before",
                "if": {"all": [{"field": "tool.arguments.command", "match": "git *"}]},
                "action": {"type": "prompt", "text": "x"},
            }
        ]
    ).rules

    assert not rule_matches(rule, HookEvent("tool.before", {"tool": {"name": "run_command"}}))


@pytest.mark.asyncio
async def test_prompt_action_returns_injection_result(tmp_path: Path) -> None:
    [rule] = config([{"event": "turn.start", "action": {"type": "prompt", "text": "注入内容"}}]).rules

    result = await HookActionRunner().run(rule, HookEvent("turn.start", {}), hook_context(tmp_path))

    assert result.status == "success"
    assert result.prompt_injection is not None
    assert result.prompt_injection.text == "注入内容"


@pytest.mark.asyncio
async def test_sub_agent_action_is_placeholder(tmp_path: Path) -> None:
    [rule] = config([{"event": "turn.start", "action": {"type": "sub_agent", "name": "worker"}}]).rules

    result = await HookActionRunner().run(rule, HookEvent("turn.start", {}), hook_context(tmp_path))

    assert result.status == "placeholder"
    assert "worker" in result.message


@pytest.mark.asyncio
async def test_action_failure_returns_failed_result(tmp_path: Path) -> None:
    [rule] = config([{"event": "turn.start", "action": {"type": "command", "command": "python -V"}}]).rules
    empty_registry = ToolRegistry()
    context = HookRuntimeContext(
        cwd=tmp_path,
        mode="normal",
        allowed_tool_names=None,
        registry=empty_registry,
        executor=ToolExecutor(empty_registry, ToolContext(cwd=tmp_path)),
        permission_controller=None,
    )

    result = await HookActionRunner().run(rule, HookEvent("turn.start", {}), context)

    assert result.status == "failed"


@pytest.mark.asyncio
async def test_command_action_runs_through_tool_executor(tmp_path: Path) -> None:
    [rule] = config([{"event": "turn.start", "action": {"type": "command", "command": "python -V"}}]).rules

    result = await HookActionRunner().run(rule, HookEvent("turn.start", {}), hook_context(tmp_path))

    assert result.status == "success"
    assert "exit_code=0" in result.message


@pytest.mark.asyncio
async def test_command_action_timeout_is_recorded(tmp_path: Path) -> None:
    [rule] = config(
        [{"event": "turn.start", "action": {"type": "command", "command": "python -c 'import time; time.sleep(1)'", "timeout_seconds": 0.01}}]
    ).rules

    result = await HookActionRunner().run(rule, HookEvent("turn.start", {}), hook_context(tmp_path))

    assert result.status == "failed"
    assert "timeout" in result.message or "超时" in result.message


@pytest.mark.asyncio
async def test_command_action_respects_plan_mode_and_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    [rule] = config([{"event": "turn.start", "action": {"type": "command", "command": "python -V"}}]).rules

    plan_result = await HookActionRunner().run(rule, HookEvent("turn.start", {}), hook_context(tmp_path, mode="plan"))

    controller = create_permission_controller(tmp_path, PermissionConfig(mode="default"))
    context = hook_context(tmp_path)
    context = HookRuntimeContext(
        cwd=context.cwd,
        mode=context.mode,
        allowed_tool_names=context.allowed_tool_names,
        registry=context.registry,
        executor=context.executor,
        permission_controller=controller,
    )
    permission_result = await HookActionRunner().run(rule, HookEvent("turn.start", {}), context)

    assert plan_result.status == "failed"
    assert permission_result.status == "failed"
    assert "权限确认" in permission_result.message


@pytest.mark.asyncio
async def test_http_action_sends_request(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="ok")

    [rule] = config([{"event": "turn.start", "action": {"type": "http", "url": "https://example.test/hook", "method": "POST", "json": {"ok": True}}}]).rules
    runner = HookActionRunner(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await runner.run(rule, HookEvent("turn.start", {}), hook_context(tmp_path))

    assert result.status == "success"
    assert requests[0].url == "https://example.test/hook"


@pytest.mark.asyncio
async def test_http_action_failure_does_not_raise(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("bad", request=request)

    [rule] = config([{"event": "turn.start", "action": {"type": "http", "url": "https://example.test/hook"}}]).rules
    runner = HookActionRunner(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await runner.run(rule, HookEvent("turn.start", {}), hook_context(tmp_path))
    plan_result = await HookActionRunner().run(rule, HookEvent("turn.start", {}), hook_context(tmp_path, mode="plan"))

    assert result.status == "failed"
    assert plan_result.status == "failed"


@pytest.mark.asyncio
async def test_hook_manager_runs_matching_rules_in_order(tmp_path: Path) -> None:
    manager = create_hook_manager(
        config(
            [
                {"name": "first", "event": "turn.start", "action": {"type": "prompt", "text": "1"}},
                {"name": "second", "event": "turn.start", "action": {"type": "prompt", "text": "2"}},
            ]
        )
    )

    results = await manager.emit(HookEvent("turn.start", {}), hook_context(tmp_path))

    assert [result.rule_id for result in results] == ["first", "second"]


@pytest.mark.asyncio
async def test_hook_manager_once_and_background_behavior(tmp_path: Path) -> None:
    manager = create_hook_manager(
        config(
            [
                {"name": "once", "event": "turn.start", "once": True, "action": {"type": "prompt", "text": "1"}},
                {"name": "bg", "event": "turn.start", "background": True, "action": {"type": "prompt", "text": "2"}},
            ]
        )
    )

    first = await manager.emit(HookEvent("turn.start", {}), hook_context(tmp_path))
    second = await manager.emit(HookEvent("turn.start", {}), hook_context(tmp_path))
    await asyncio.sleep(0.01)

    assert [result.status for result in first] == ["success", "success"]
    assert second[0].status == "skipped_once"
    assert any(result.rule_id == "bg" for result in manager.completed_background_results())


@pytest.mark.asyncio
async def test_background_hook_failure_is_recorded(tmp_path: Path) -> None:
    [rule] = config([{"name": "bg", "event": "turn.start", "background": True, "action": {"type": "prompt", "text": "x"}}]).rules
    manager = HookManager(HookConfig((rule,)), FailingRunner())

    await manager.emit(HookEvent("turn.start", {}), hook_context(tmp_path))
    await asyncio.sleep(0.01)

    assert manager.completed_background_results()[0].status == "failed"


@pytest.mark.asyncio
async def test_hook_manager_consumes_prompt_injections(tmp_path: Path) -> None:
    manager = create_hook_manager(config([{"event": "turn.start", "action": {"type": "prompt", "text": "ctx"}}]))

    await manager.emit(HookEvent("turn.start", {}), hook_context(tmp_path))

    assert manager.pending_prompt_injections()[0].text == "ctx"
    assert manager.consume_prompt_injections()[0].text == "ctx"
    assert manager.pending_prompt_injections() == ()


@pytest.mark.asyncio
async def test_before_tool_can_block_call(tmp_path: Path) -> None:
    manager = create_hook_manager(
        config(
            [
                {
                    "name": "block",
                    "event": "tool.before",
                    "if": {"all": [{"field": "tool.name", "match": "run_command"}]},
                    "action": {"type": "prompt", "text": "x", "tool_block": {"reason": "命令被 Hook 拦截"}},
                }
            ]
        )
    )

    decision = await manager.before_tool(ToolCall("c1", "run_command", {"command": "rm file"}), hook_context(tmp_path))

    assert decision.blocked is True
    assert decision.tool_result is not None
    assert decision.tool_result.error_type == "hook_blocked"
    assert "命令被 Hook 拦截" in (decision.tool_result.error or "")


@pytest.mark.asyncio
async def test_failed_before_tool_hook_does_not_block(tmp_path: Path) -> None:
    [rule] = config(
        [
            {
                "name": "block",
                "event": "tool.before",
                "action": {"type": "prompt", "text": "x", "tool_block": {"reason": "no"}},
            }
        ]
    ).rules
    manager = HookManager(HookConfig((rule,)), FailingRunner())

    decision = await manager.before_tool(ToolCall("c1", "run_command", {"command": "python -V"}), hook_context(tmp_path))

    assert decision.blocked is False
    assert decision.results[0].status == "failed"


@pytest.mark.asyncio
async def test_after_tool_emits_result_event(tmp_path: Path) -> None:
    manager = create_hook_manager(
        config(
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

    results = await manager.after_tool(
        ToolCall("c1", "read_file", {"path": "README.md"}),
        ToolResult("c1", "read_file", True, {"content": "x"}),
        hook_context(tmp_path),
    )

    assert results[0].rule_id == "after"
