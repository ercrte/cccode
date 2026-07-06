from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from mewcode.context.models import ContextSummary
from mewcode.session_id import SessionId

if TYPE_CHECKING:
    from mewcode.providers.base import ChatMessage
    from mewcode.session import ChatSession


InstructionScope = Literal["project_private", "project_root", "user"]
SessionRecordKind = Literal["message", "checkpoint"]
MemoryScope = Literal["user", "project"]
MemoryCategory = Literal["preference", "correction", "project_knowledge", "reference"]
MemoryAction = Literal["create", "update", "skip"]
MemoryDurability = Literal["persistent", "temporary", "uncertain"]


@dataclass(frozen=True)
class SessionMemoryConfig:
    enabled: bool = True
    project_dir: str = ".mewcode"
    sessions_dir: str = "sessions"
    memory_dir: str = "memory"
    user_dir: str = "~/.mewcode"
    instruction_filename: str = "AGENTS.md"
    include_max_depth: int = 5
    auto_restore: bool = True
    retention_days: int = 30
    time_gap_hours: int = 24
    index_max_lines: int = 400
    index_max_bytes: int = 50_000
    auto_notes_enabled: bool = True
    critical_preference_min_confidence: float = 0.95


@dataclass(frozen=True)
class InstructionBlock:
    scope: InstructionScope
    priority: int
    source_path: Path
    content: str


@dataclass(frozen=True)
class InstructionBundle:
    blocks: tuple[InstructionBlock, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionJsonlRecord:
    kind: SessionRecordKind
    session_id: SessionId
    created_at: str
    message: ChatMessage | None = None
    messages: tuple[ChatMessage, ...] = ()
    context_summary: ContextSummary | None = None


@dataclass(frozen=True)
class SessionInfo:
    session_id: SessionId
    path: Path
    title: str
    message_count: int
    updated_at: datetime
    expired: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RestoreReport:
    restored: bool
    session_id: SessionId
    source_path: Path | None = None
    skipped_bad_lines: int = 0
    truncated_messages: int = 0
    compacted: bool = False
    started_empty_reason: str = ""
    time_gap_notice: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryIndex:
    scope: MemoryScope
    path: Path
    content: str
    line_count: int
    byte_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgeContext:
    instructions: InstructionBundle = field(default_factory=InstructionBundle)
    user_memory_index: MemoryIndex | None = None
    project_memory_index: MemoryIndex | None = None
    restore_report: RestoreReport | None = None


@dataclass(frozen=True)
class MemoryNote:
    note_id: str
    scope: MemoryScope
    category: MemoryCategory
    title: str
    body: str
    source_session_id: SessionId
    created_at: str
    updated_at: str
    tags: tuple[str, ...] = ()
    source_evidence: tuple[str, ...] = ()
    critical: bool = False
    confidence: float | None = None


@dataclass(frozen=True)
class MemoryCandidate:
    action: MemoryAction
    scope: MemoryScope | str = ""
    category: MemoryCategory | str = ""
    note_id: str = ""
    title: str = ""
    body: str = ""
    evidence: tuple[str, ...] = ()
    durability: MemoryDurability | str = ""
    critical: bool = False
    confidence: float = 0.0
    tags: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    schema_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidatedMemoryOperation:
    action: Literal["create", "update"]
    note: MemoryNote
    supersedes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryRejection:
    candidate: MemoryCandidate
    code: str
    message: str


@dataclass(frozen=True)
class MemoryExtractionResult:
    accepted: tuple[ValidatedMemoryOperation, ...] = ()
    rejected: tuple[MemoryRejection, ...] = ()


@dataclass(frozen=True)
class MemoryUpdateJob:
    session_id: SessionId
    cwd: Path
    turn_messages: tuple[ChatMessage, ...]
    final_message: ChatMessage
    knowledge_context: KnowledgeContext


@dataclass(frozen=True)
class BootstrapOptions:
    new_session: bool = False


@dataclass(frozen=True)
class BootstrapResult:
    session: ChatSession
    knowledge_context: KnowledgeContext
    restore_report: RestoreReport


@dataclass(frozen=True)
class ProtocolValidationResult:
    messages: tuple[ChatMessage, ...]
    truncated_count: int = 0
    warning: str = ""
