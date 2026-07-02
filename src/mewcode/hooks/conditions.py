from __future__ import annotations

from mewcode.hooks.models import HookEvent, HookRule
from mewcode.matching import get_field_value, match_expression


def rule_matches(rule: HookRule, event: HookEvent) -> bool:
    if rule.event != event.name:
        return False
    if rule.condition is None:
        return True
    matches = [
        False
        if (value := get_field_value(event.data, condition.field)) is None
        else match_expression(condition.match, value)
        for condition in rule.condition.conditions
    ]
    return all(matches) if rule.condition.logic == "all" else any(matches)
