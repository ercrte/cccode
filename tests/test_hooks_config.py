from __future__ import annotations

import pytest

from julycode.errors import ConfigError
from julycode.hooks.config import parse_hook_config


def test_parse_hook_config_defaults_to_empty() -> None:
    assert parse_hook_config(None).rules == ()


def test_parse_hook_config_accepts_valid_rules() -> None:
    config = parse_hook_config(
        [
            {
                "name": "block-rm",
                "event": "tool.before",
                "if": {"all": [{"field": "tool.name", "match": "run_command"}]},
                "action": {
                    "type": "command",
                    "command": "python -V",
                    "timeout_seconds": 2,
                    "tool_block": {"reason": "blocked"},
                },
                "once": True,
            },
            {
                "event": "turn.start",
                "action": {"type": "prompt", "text": "只读上下文"},
                "background": False,
            },
        ]
    )

    assert len(config.rules) == 2
    assert config.rules[0].id == "block-rm"
    assert config.rules[0].action.command is not None
    assert config.rules[0].action.tool_block is not None
    assert config.rules[1].id == "hook-2"


@pytest.mark.parametrize(
    "raw",
    [
        {},
        [{"action": {"type": "prompt", "text": "x"}}],
        [{"event": "turn.start"}],
        [{"event": "unknown", "action": {"type": "prompt", "text": "x"}}],
        [{"event": "turn.start", "action": {"type": "bad"}}],
        [{"event": "turn.start", "if": {"all": [], "any": []}, "action": {"type": "prompt", "text": "x"}}],
        [{"event": "turn.start", "if": {"all": [{"field": "x", "match": "regex:["}]}, "action": {"type": "prompt", "text": "x"}}],
        [{"event": "tool.before", "background": True, "action": {"type": "prompt", "text": "x"}}],
        [{"event": "turn.start", "action": {"type": "command", "command": "true", "timeout_seconds": 0}}],
        [{"event": "turn.start", "action": {"type": "prompt", "text": "x", "tool_block": {"reason": "no"}}}],
    ],
)
def test_rejects_invalid_hook_config(raw: object) -> None:
    with pytest.raises(ConfigError):
        parse_hook_config(raw)


def test_rejects_mixed_condition_logic() -> None:
    with pytest.raises(ConfigError):
        parse_hook_config(
            [
                {
                    "event": "turn.start",
                    "if": {
                        "all": [{"field": "turn.mode", "match": "normal"}],
                        "any": [{"field": "turn.mode", "match": "plan"}],
                    },
                    "action": {"type": "prompt", "text": "x"},
                }
            ]
        )
