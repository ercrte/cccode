from __future__ import annotations

import math
from pathlib import Path

from mewcode.repo_map import RankedSymbol, SymbolRecord
from mewcode.repo_map.renderer import RepoMapRenderer


def _tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _ranked() -> tuple[RankedSymbol, ...]:
    return (
        RankedSymbol(
            "src/example.py",
            SymbolRecord("class", "Example", "Example", 10, "class Example", "class Example"),
            100.0,
            0.1,
        ),
        RankedSymbol(
            "src/example.py",
            SymbolRecord(
                "method",
                "run",
                "Example.run",
                12,
                "def run(self, value: str = ...) -> bool",
                "def run(...)",
                "Example",
            ),
            90.0,
            0.1,
        ),
        RankedSymbol(
            "src/helpers.py",
            SymbolRecord(
                "async_function",
                "helper",
                "helper",
                3,
                "async def helper(...)",
                "async def helper(...)",
            ),
            80.0,
            0.1,
        ),
    )


def test_renderer_matches_byte_exact_golden() -> None:
    rendered = RepoMapRenderer().render(
        _ranked(), root=Path("/repo"), revision="abcdef123456789", budget=2000, token_counter=_tokens
    )
    golden = (Path(__file__).parent / "fixtures" / "repo_map_golden.txt").read_text(encoding="utf-8")

    assert rendered is not None
    assert rendered.text + "\n" == golden
    assert rendered.included_files == ("src/example.py", "src/helpers.py")
    assert rendered.truncated is False


def test_renderer_keeps_method_parent_and_uses_short_signature() -> None:
    long_method = RankedSymbol(
        "module.py",
        SymbolRecord(
            "method",
            "run",
            "Service.run",
            5,
            "def run(self, " + ", ".join(f"arg{i}: str = ..." for i in range(30)) + ")",
            "def run(...)",
            "Service",
        ),
        1.0,
        0.0,
    )
    full = RepoMapRenderer().render(
        (long_method,), root=Path("/repo"), revision="rev", budget=200, token_counter=_tokens
    )

    assert full is not None
    assert "class Service:" in full.text
    assert "def run(...)" in full.text
    assert max(map(len, full.text.splitlines())) <= 160


def test_renderer_omits_entire_map_when_minimum_entry_does_not_fit() -> None:
    rendered = RepoMapRenderer().render(
        _ranked(), root=Path("/repo"), revision="rev", budget=10, token_counter=_tokens
    )

    assert rendered is None


def test_renderer_counts_truncation_marker_inside_budget() -> None:
    budget = 70
    rendered = RepoMapRenderer().render(
        _ranked(), root=Path("/repo"), revision="rev", budget=budget, token_counter=_tokens
    )

    assert rendered is not None
    assert rendered.estimated_tokens <= budget
    assert rendered.truncated is True
    assert "[已按 Token 预算裁剪]" in rendered.text
    assert not rendered.text.endswith("\n")
