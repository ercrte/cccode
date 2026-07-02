from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from mewcode.errors import MewCodeError
from mewcode.session_id import new_session_id

if TYPE_CHECKING:
    from mewcode.providers.base import ChatRequest


@dataclass(frozen=True)
class ContextConfig:
    enabled: bool = True
    window_tokens: int = 128_000
    single_tool_result_tokens: int = 4_000
    turn_tool_result_tokens: int = 8_000
    tool_preview_chars: int = 2_000
    recent_tokens: int = 10_000
    min_recent_messages: int = 5
    auto_reserve_tokens: int = 13_000
    manual_reserve_tokens: int = 3_000
    summary_failure_limit: int = 3
    chars_per_token: float = 4.0
    store_dir: str = ".mewcode/context"


@dataclass(frozen=True)
class ContextSummary:
    content: str
    boundary_notice: str
    created_at: str
    source_message_count: int
    kept_message_count: int
    external_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextExternalRef:
    path: str
    original_chars: int
    estimated_tokens: int
    preview: str


@dataclass(frozen=True)
class TokenAnchor:
    input_tokens: int
    footprint_chars: int


@dataclass(frozen=True)
class RequestFootprint:
    chars: int
    estimated_tokens: int


@dataclass(frozen=True)
class ContextCompactionReport:
    mode: Literal["auto", "manual"]
    light_compacted: bool
    heavy_compacted: bool
    externalized_paths: tuple[str, ...] = ()
    kept_message_count: int = 0
    summarized_message_count: int = 0
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0
    message: str = ""


@dataclass(frozen=True)
class ToolCompactionResult:
    changed: bool
    external_refs: tuple[ContextExternalRef, ...] = ()


@dataclass(frozen=True)
class PreparedChatRequest:
    request: ChatRequest
    footprint: RequestFootprint
    report: ContextCompactionReport | None = None


@dataclass
class ContextState:
    session_id: str = field(default_factory=lambda: str(new_session_id()))
    summary: ContextSummary | None = None
    token_anchor: TokenAnchor | None = None
    consecutive_summary_failures: int = 0
    compacted_tool_paths: tuple[str, ...] = ()


class ContextLimitError(MewCodeError):
    def __init__(
        self,
        message: str,
        *,
        report: ContextCompactionReport | None = None,
    ) -> None:
        super().__init__(message)
        self.report = report


class SummaryError(MewCodeError):
    pass
