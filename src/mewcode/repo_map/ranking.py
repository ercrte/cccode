from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from mewcode.repo_map.models import ParsedPythonFile, RankedSymbol, RepoGraph


RANKING_VERSION = "ranking-v1:100-80-60-40:graph10"
_TOKEN_RE = re.compile(r"[\w./\\-]+", re.UNICODE)


@dataclass(frozen=True)
class RequestHints:
    normalized_text: str
    tokens: frozenset[str]


def extract_request_hints(source_request: str) -> RequestHints:
    normalized = source_request.replace("\\", "/").casefold()
    return RequestHints(
        normalized_text=normalized,
        tokens=frozenset(token.casefold() for token in _TOKEN_RE.findall(normalized)),
    )


def rank_symbols(
    files: tuple[ParsedPythonFile, ...],
    graph: RepoGraph,
    source_request: str,
) -> tuple[RankedSymbol, ...]:
    hints = extract_request_hints(source_request)
    graph_scores = dict(graph.scores)
    name_counts: dict[str, int] = {}
    for parsed in files:
        for symbol in parsed.symbols:
            key = symbol.name.casefold()
            name_counts[key] = name_counts.get(key, 0) + 1

    ranked: list[RankedSymbol] = []
    for parsed in sorted(files, key=lambda item: item.fingerprint.relative_path):
        path = parsed.fingerprint.relative_path
        path_key = path.casefold()
        filename = PurePosixPath(path).name.casefold()
        module_key = parsed.module_name.casefold()
        graph_score = graph_scores.get(path, 0.0)
        for symbol in parsed.symbols:
            request_score = 0.0
            if path_key in hints.normalized_text or (
                module_key and _contains_term(hints.normalized_text, module_key)
            ):
                request_score = 100.0
            elif filename in hints.tokens:
                request_score = 80.0
            elif _contains_term(hints.normalized_text, symbol.qualified_name.casefold()):
                request_score = 60.0
            elif symbol.name.casefold() in hints.tokens and name_counts[symbol.name.casefold()] == 1:
                request_score = 40.0
            score = round(request_score + graph_score * 10.0, 8)
            ranked.append(RankedSymbol(path, symbol, score, graph_score))
    ranked.sort(
        key=lambda item: (
            -round(item.score, 8),
            item.relative_path,
            item.symbol.qualified_name,
            item.symbol.line_number,
        )
    )
    return tuple(ranked)


def _contains_term(text: str, term: str) -> bool:
    if not term:
        return False
    pattern = rf"(?<![\w.]){re.escape(term)}(?![\w.])"
    return re.search(pattern, text) is not None

