from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from mewcode.providers.base import LLMProvider


RepoMapQualityMode = Literal["offline", "paired"]


@dataclass(frozen=True)
class NavigationCase:
    case_id: str
    request: str
    target_file: str
    top_k: int = 5
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class NavigationDataset:
    version: str
    cases: tuple[NavigationCase, ...]


@dataclass(frozen=True)
class NavigationTrial:
    enabled: bool
    target_hit: bool
    top_files: tuple[str, ...] = ()
    target_read: bool | None = None
    exploration_calls: int | None = None
    final_text: str = ""
    error: str | None = None


@dataclass(frozen=True)
class NavigationCaseResult:
    case_id: str
    target_file: str
    top_k: int
    disabled: NavigationTrial
    enabled: NavigationTrial


@dataclass(frozen=True)
class NavigationSummary:
    case_count: int
    disabled_top_k_hit_rate: float
    enabled_top_k_hit_rate: float
    disabled_average_exploration_calls: float | None = None
    enabled_average_exploration_calls: float | None = None


@dataclass(frozen=True)
class RepoMapQualityReport:
    dataset_version: str
    mode: RepoMapQualityMode
    root: str
    started_at: str
    results: tuple[NavigationCaseResult, ...]
    summary: NavigationSummary


@dataclass(frozen=True)
class RepoMapQualityRunOptions:
    mode: RepoMapQualityMode = "offline"
    root: Path = field(default_factory=Path.cwd)
    map_budget: int = 2000
    provider: LLMProvider | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if self.map_budget <= 0:
            raise ValueError("map_budget 必须大于 0")
        if self.mode == "paired" and self.provider is None:
            raise ValueError("paired 模式必须提供 Provider")
