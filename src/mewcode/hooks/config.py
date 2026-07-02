from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mewcode.errors import ConfigError
from mewcode.matching import parse_match_expression
from mewcode.hooks.models import (
    HOOK_ACTION_TYPES,
    HOOK_EVENTS,
    HTTP_METHODS,
    HookAction,
    HookCommandAction,
    HookCondition,
    HookConditionGroup,
    HookConfig,
    HookHttpAction,
    HookPromptAction,
    HookRule,
    HookSubAgentAction,
    HookToolBlock,
)


def parse_hook_config(raw: object) -> HookConfig:
    if raw is None:
        return HookConfig()
    if not isinstance(raw, list):
        raise ConfigError("hooks 必须是 YAML 列表")
    rules = tuple(_parse_rule(item, index) for index, item in enumerate(raw))
    return HookConfig(rules=rules)


def _parse_rule(raw: object, index: int) -> HookRule:
    path = f"hooks[{index}]"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path} 必须是 YAML 对象")
    event = _required_str(raw, "event", path)
    if event not in HOOK_EVENTS:
        raise ConfigError(f"{path}.event 是未知 Hook 事件: {event}")
    action = _parse_action(raw.get("action"), path)
    condition = _parse_condition(raw.get("if"), path)
    once = _optional_bool(raw.get("once", False), f"{path}.once")
    background = _optional_bool(raw.get("background", False), f"{path}.background")
    if event == "tool.before" and background:
        raise ConfigError(f"{path}: tool.before 不允许 background: true")
    if action.tool_block is not None and event != "tool.before":
        raise ConfigError(f"{path}.action.tool_block 只能用于 tool.before")
    name = str(raw.get("name") or f"hook-{index + 1}").strip()
    if not name:
        raise ConfigError(f"{path}.name 不能为空")
    return HookRule(
        id=name,
        index=index,
        event=event,  # type: ignore[arg-type]
        condition=condition,
        action=action,
        once=once,
        background=background,
    )


def _parse_condition(raw: object, path: str) -> HookConditionGroup | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path}.if 必须是 YAML 对象")
    has_all = "all" in raw
    has_any = "any" in raw
    if has_all == has_any:
        raise ConfigError(f"{path}.if 必须且只能包含 all 或 any")
    logic = "all" if has_all else "any"
    items = raw[logic]
    if not isinstance(items, list) or not items:
        raise ConfigError(f"{path}.if.{logic} 必须是非空列表")
    conditions = tuple(_parse_condition_item(item, f"{path}.if.{logic}[{index}]") for index, item in enumerate(items))
    return HookConditionGroup(logic=logic, conditions=conditions)  # type: ignore[arg-type]


def _parse_condition_item(raw: object, path: str) -> HookCondition:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path} 必须是 YAML 对象")
    field = _required_str(raw, "field", path)
    if "match" not in raw:
        raise ConfigError(f"{path}.match 是必填字段")
    match = parse_match_expression(str(raw["match"]))
    return HookCondition(field=field, match=match)


def _parse_action(raw: object, path: str) -> HookAction:
    action_path = f"{path}.action"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{action_path} 必须是 YAML 对象")
    action_type = _required_str(raw, "type", action_path)
    if action_type not in HOOK_ACTION_TYPES:
        raise ConfigError(f"{action_path}.type 是未知 Hook 动作: {action_type}")
    tool_block = _parse_tool_block(raw.get("tool_block"), action_path)

    if action_type == "command":
        command = _required_str(raw, "command", action_path)
        return HookAction(
            type="command",
            command=HookCommandAction(command=command, timeout_seconds=_timeout(raw, action_path)),
            tool_block=tool_block,
        )
    if action_type == "prompt":
        text = _required_str(raw, "text", action_path)
        return HookAction(type="prompt", prompt=HookPromptAction(text=text), tool_block=tool_block)
    if action_type == "http":
        method = str(raw.get("method", "POST")).strip().upper()
        if method not in HTTP_METHODS:
            raise ConfigError(f"{action_path}.method 不支持: {method}")
        body = raw.get("body")
        if body is not None and not isinstance(body, str):
            raise ConfigError(f"{action_path}.body 必须是字符串")
        headers = _parse_headers(raw.get("headers"), action_path)
        return HookAction(
            type="http",
            http=HookHttpAction(
                method=method,  # type: ignore[arg-type]
                url=_required_str(raw, "url", action_path),
                headers=headers,
                body=body,
                json_body=raw.get("json"),
                timeout_seconds=_timeout(raw, action_path),
            ),
            tool_block=tool_block,
        )
    name = _required_str(raw, "name", action_path)
    prompt = str(raw.get("prompt", ""))
    return HookAction(type="sub_agent", sub_agent=HookSubAgentAction(name=name, prompt=prompt), tool_block=tool_block)


def _parse_tool_block(raw: object, path: str) -> HookToolBlock | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path}.tool_block 必须是 YAML 对象")
    reason = _required_str(raw, "reason", f"{path}.tool_block")
    error_type = str(raw.get("error_type", "hook_blocked")).strip() or "hook_blocked"
    return HookToolBlock(reason=reason, error_type=error_type)


def _parse_headers(raw: object, path: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path}.headers 必须是 YAML 对象")
    headers: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ConfigError(f"{path}.headers 的 key 必须是字符串")
        headers[key] = str(value)
    return headers


def _required_str(raw: Mapping[str, object], key: str, path: str) -> str:
    if key not in raw:
        raise ConfigError(f"{path}.{key} 是必填字段")
    value = str(raw[key]).strip()
    if not value:
        raise ConfigError(f"{path}.{key} 不能为空")
    return value


def _optional_bool(raw: object, path: str) -> bool:
    if not isinstance(raw, bool):
        raise ConfigError(f"{path} 必须是布尔值")
    return raw


def _timeout(raw: Mapping[str, object], path: str) -> float:
    value = raw.get("timeout_seconds", 10.0)
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path}.timeout_seconds 必须是数字") from exc
    if timeout <= 0:
        raise ConfigError(f"{path}.timeout_seconds 必须大于 0")
    return timeout
