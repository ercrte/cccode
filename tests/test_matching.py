from __future__ import annotations

import pytest

from mewcode.errors import ConfigError
from mewcode.matching import get_field_value, match_expression, parse_match_expression


def test_parse_match_expression_detects_exact_glob_regex_and_negation() -> None:
    exact = parse_match_expression("run_command")
    implicit_glob = parse_match_expression("git *")
    explicit_glob = parse_match_expression("glob:src/**/*.py")
    regex = parse_match_expression("regex:^git\\s+status$")
    negated = parse_match_expression("!regex:^rm\\b")

    assert exact.kind == "exact"
    assert implicit_glob.kind == "glob"
    assert explicit_glob.kind == "glob"
    assert explicit_glob.pattern == "src/**/*.py"
    assert regex.kind == "regex"
    assert negated.negated is True


def test_match_expression_matches_all_supported_kinds() -> None:
    assert match_expression(parse_match_expression("git status"), "git status")
    assert match_expression(parse_match_expression("git *"), "git status")
    assert match_expression(parse_match_expression("glob:src/*.py"), "src/app.py")
    assert match_expression(parse_match_expression("regex:^git\\s+status$"), "git status")
    assert match_expression(parse_match_expression("!rm *"), "git status")
    assert not match_expression(parse_match_expression("!rm *"), "rm file")


def test_parse_match_expression_rejects_invalid_values() -> None:
    with pytest.raises(ConfigError):
        parse_match_expression("")
    with pytest.raises(ConfigError):
        parse_match_expression("!")
    with pytest.raises(ConfigError):
        parse_match_expression("regex:[")


def test_get_field_value_reads_nested_mapping_and_missing_fields() -> None:
    data = {"tool": {"name": "run_command", "arguments": {"command": "git status"}}}

    assert get_field_value(data, "tool.name") == "run_command"
    assert get_field_value(data, "tool.arguments.command") == "git status"
    assert get_field_value(data, "tool.arguments.missing") is None
    assert get_field_value(data, "tool..name") is None
