from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


SymbolKind = Literal["class", "function", "async_function", "method", "async_method"]
ReferenceKind = Literal["name", "call"]
DiagnosticLevel = Literal["warning", "error"]
RepoMapState = Literal["disabled", "indexing", "ready", "degraded", "empty", "closed"]


@dataclass(frozen=True)
class RepoMapDiagnostic:
    code: str
    message: str
    path: str | None = None
    level: DiagnosticLevel = "warning"


@dataclass(frozen=True)
class RepositoryIdentity:
    root: Path
    repo_id: str
    worktree_id: str
    head_id: str
    is_git: bool


@dataclass(frozen=True, order=True)
class FileFingerprint:
    relative_path: str
    content_hash: str
    size: int


@dataclass(frozen=True)
class ScannedFile:
    fingerprint: FileFingerprint
    source_bytes: bytes = field(repr=False)


@dataclass(frozen=True, order=True)
class SymbolRecord:
    kind: SymbolKind
    name: str
    qualified_name: str
    line_number: int
    signature: str
    short_signature: str
    parent_qualified_name: str | None = None


@dataclass(frozen=True, order=True)
class ImportRecord:
    module: str
    symbol: str | None
    alias: str | None
    level: int
    line_number: int
    is_star: bool = False


@dataclass(frozen=True, order=True)
class ReferenceRecord:
    name: str
    kind: ReferenceKind
    line_number: int


@dataclass(frozen=True)
class ParsedPythonFile:
    fingerprint: FileFingerprint
    module_name: str
    is_package: bool
    symbols: tuple[SymbolRecord, ...] = ()
    imports: tuple[ImportRecord, ...] = ()
    references: tuple[ReferenceRecord, ...] = ()
    diagnostics: tuple[RepoMapDiagnostic, ...] = ()


@dataclass(frozen=True, order=True)
class GraphEdge:
    source_path: str
    target_path: str
    relation: str
    weight: float


@dataclass(frozen=True)
class RepoGraph:
    nodes: tuple[str, ...]
    edges: tuple[GraphEdge, ...]
    scores: tuple[tuple[str, float], ...]
    diagnostics: tuple[RepoMapDiagnostic, ...] = ()

    def score_for(self, relative_path: str) -> float:
        return dict(self.scores).get(relative_path, 0.0)


@dataclass(frozen=True)
class RankedSymbol:
    relative_path: str
    symbol: SymbolRecord
    score: float
    graph_score: float


@dataclass(frozen=True)
class WorkspaceState:
    identity: RepositoryIdentity
    ordered_fingerprints: tuple[FileFingerprint, ...]
    revision: str


@dataclass(frozen=True)
class DiscoveryResult:
    identity: RepositoryIdentity
    files: tuple[ScannedFile, ...]
    diagnostics: tuple[RepoMapDiagnostic, ...] = ()


@dataclass(frozen=True)
class RepoMapSnapshot:
    snapshot_id: str
    revision: str
    text: str
    estimated_tokens: int
    included_files: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class RenderedRepoMap:
    text: str
    estimated_tokens: int
    included_files: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class RepoMapCacheStatus:
    parse: Literal["hit", "miss", "mixed", "unused"] = "unused"
    graph: Literal["hit", "miss", "unused"] = "unused"
    snapshot: Literal["hit", "miss", "unused"] = "unused"


@dataclass(frozen=True)
class RepoMapStatus:
    enabled: bool
    state: RepoMapState
    root: str | None = None
    revision: str | None = None
    configured_budget: int = 0
    effective_budget: int = 0
    candidate_files: int = 0
    included_files: int = 0
    truncated: bool = False
    cache: RepoMapCacheStatus = field(default_factory=RepoMapCacheStatus)
    elapsed_ms: float | None = None
    reason: str | None = None
