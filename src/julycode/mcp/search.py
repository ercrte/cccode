from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from julycode.mcp.tools import McpToolDefinition


MCP_TOOL_SEARCH_LIMIT = 5
MCP_TOOL_SUMMARY_LIMIT = 160

_SEPARATORS = re.compile(r"[_\-/.:]+")
_TOKENS = re.compile(r"[^\W_]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "in",
        "of",
        "on",
        "or",
        "please",
        "the",
        "to",
        "tool",
        "use",
        "using",
        "with",
    }
)


McpToolSearchStatus = Literal[
    "ok",
    "no_match",
    "server_not_found",
    "server_unavailable",
    "policy_filtered",
]


@dataclass(frozen=True)
class McpSearchDocument:
    definition: McpToolDefinition
    normalized_name: str
    normalized_title: str
    normalized_description: str
    name_tokens: frozenset[str]
    title_tokens: frozenset[str]
    description_tokens: frozenset[str]


@dataclass(frozen=True)
class McpToolMatch:
    global_name: str
    server_name: str
    remote_name: str
    title: str | None
    summary: str
    score: int


@dataclass(frozen=True)
class McpToolSearchResult:
    status: McpToolSearchStatus
    query: str
    server_name: str | None
    matches: tuple[McpToolMatch, ...] = ()
    activated_tools: tuple[str, ...] = ()
    message: str = ""


class McpToolSearchProvider(Protocol):
    def search_tools(self, query: str, server_name: str | None = None) -> McpToolSearchResult:
        ...


@dataclass(frozen=True)
class McpServerToolSummary:
    name: str
    tool_count: int


@dataclass(frozen=True)
class McpPromptContext:
    connected_servers: tuple[McpServerToolSummary, ...] = ()


class McpToolCatalog:
    def __init__(self) -> None:
        self._definitions: dict[str, tuple[McpToolDefinition, ...]] = {}
        self._documents: dict[str, McpSearchDocument] = {}
        self._searchable: set[str] = set()

    def replace_server(
        self,
        server_name: str,
        definitions: tuple[McpToolDefinition, ...],
    ) -> None:
        self.remove_server(server_name)
        self._definitions[server_name] = definitions
        for definition in definitions:
            self._documents[definition.global_name] = _document(definition)

    def set_searchable(self, global_names: set[str]) -> None:
        self._searchable = set(global_names).intersection(self._documents)

    def remove_server(self, server_name: str) -> None:
        definitions = self._definitions.pop(server_name, ())
        for definition in definitions:
            self._documents.pop(definition.global_name, None)
            self._searchable.discard(definition.global_name)

    def get(self, global_name: str) -> McpToolDefinition | None:
        document = self._documents.get(global_name)
        return document.definition if document is not None else None

    def definitions(self) -> tuple[McpToolDefinition, ...]:
        return tuple(
            definition
            for server_name in sorted(self._definitions)
            for definition in self._definitions[server_name]
        )

    def server_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def server_summaries(self) -> tuple[McpServerToolSummary, ...]:
        counts: dict[str, int] = {}
        for name in self._searchable:
            document = self._documents.get(name)
            if document is None:
                continue
            server_name = document.definition.server_name
            counts[server_name] = counts.get(server_name, 0) + 1
        return tuple(McpServerToolSummary(name, counts[name]) for name in sorted(counts))

    def search(
        self,
        query: str,
        *,
        server_name: str | None = None,
        limit: int = MCP_TOOL_SEARCH_LIMIT,
    ) -> tuple[McpToolMatch, ...]:
        query_tokens = _tokenize(query)
        if not query_tokens or limit <= 0:
            return ()
        phrase = " ".join(query_tokens)
        matches: list[McpToolMatch] = []
        for global_name in self._searchable:
            document = self._documents.get(global_name)
            if document is None:
                continue
            definition = document.definition
            if server_name is not None and definition.server_name != server_name:
                continue
            score = _score(document, query_tokens, phrase)
            if score <= 0:
                continue
            matches.append(
                McpToolMatch(
                    global_name=definition.global_name,
                    server_name=definition.server_name,
                    remote_name=definition.remote_name,
                    title=definition.title,
                    summary=_summary(definition),
                    score=score,
                )
            )
        matches.sort(key=lambda item: (-item.score, item.server_name, item.remote_name))
        return tuple(matches[: min(limit, MCP_TOOL_SEARCH_LIMIT)])


def normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = _SEPARATORS.sub(" ", normalized)
    return _WHITESPACE.sub(" ", normalized).strip()


def _tokenize(value: str) -> tuple[str, ...]:
    normalized = normalize_search_text(value)
    tokens = []
    for token in _TOKENS.findall(normalized):
        if len(token) < 2 or token in _STOP_WORDS or token in tokens:
            continue
        tokens.append(token)
    return tuple(tokens)


def _document(definition: McpToolDefinition) -> McpSearchDocument:
    normalized_name = normalize_search_text(definition.remote_name)
    normalized_title = normalize_search_text(definition.title or "")
    normalized_description = normalize_search_text(definition.description)
    return McpSearchDocument(
        definition=definition,
        normalized_name=normalized_name,
        normalized_title=normalized_title,
        normalized_description=normalized_description,
        name_tokens=frozenset(_tokenize(normalized_name)),
        title_tokens=frozenset(_tokenize(normalized_title)),
        description_tokens=frozenset(_tokenize(normalized_description)),
    )


def _score(document: McpSearchDocument, query_tokens: tuple[str, ...], phrase: str) -> int:
    score = 0
    if phrase == document.normalized_name:
        score += 1000
    if phrase and phrase in document.normalized_name:
        score += 300
    if phrase and phrase in document.normalized_title:
        score += 180
    if phrase and phrase in document.normalized_description:
        score += 80

    covered: set[str] = set()
    for token in query_tokens:
        if token in document.name_tokens:
            score += 60
            covered.add(token)
        if token in document.title_tokens:
            score += 30
            covered.add(token)
        if token in document.description_tokens:
            score += 10
            covered.add(token)
        if len(token) >= 3 and any(name_token.startswith(token) for name_token in document.name_tokens):
            score += 20
            covered.add(token)
    if len(covered) == len(query_tokens):
        score += 100
    return score


def _summary(definition: McpToolDefinition) -> str:
    raw = definition.description or definition.title or definition.remote_name
    compact = _WHITESPACE.sub(" ", str(raw)).strip()
    return compact[:MCP_TOOL_SUMMARY_LIMIT]
