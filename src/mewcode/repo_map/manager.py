from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Generic, TypeVar

from mewcode.config import RepoMapConfig
from mewcode.repo_map.discovery import DISCOVERY_VERSION, build_workspace_state, scan_repository
from mewcode.repo_map.graph import GRAPH_VERSION, RepoGraphBuilder
from mewcode.repo_map.models import (
    DiscoveryResult,
    ParsedPythonFile,
    RepoGraph,
    RepoMapCacheStatus,
    RepoMapDiagnostic,
    RepoMapSnapshot,
    RepoMapStatus,
    WorkspaceState,
)
from mewcode.repo_map.parser import PARSER_VERSION, PythonSymbolParser
from mewcode.repo_map.ranking import RANKING_VERSION, rank_symbols
from mewcode.repo_map.renderer import RENDERER_VERSION, RepoMapRenderer
from mewcode.tools.base import ToolCall, ToolResult, ToolSpec


K = TypeVar("K")
V = TypeVar("V")


class _BoundedLRU(Generic[K, V]):
    def __init__(
        self,
        max_entries: int,
        *,
        max_weight: int | None = None,
        weight: Callable[[V], int] | None = None,
    ) -> None:
        self.max_entries = max_entries
        self.max_weight = max_weight
        self.weight = weight or (lambda _: 1)
        self._values: OrderedDict[K, V] = OrderedDict()
        self._total_weight = 0

    def get(self, key: K) -> V | None:
        value = self._values.get(key)
        if value is not None:
            self._values.move_to_end(key)
        return value

    def put(self, key: K, value: V) -> None:
        previous = self._values.pop(key, None)
        if previous is not None:
            self._total_weight -= self.weight(previous)
        self._values[key] = value
        self._total_weight += self.weight(value)
        while len(self._values) > self.max_entries or (
            self.max_weight is not None and self._total_weight > self.max_weight
        ):
            _, removed = self._values.popitem(last=False)
            self._total_weight -= self.weight(removed)

    def clear(self) -> None:
        self._values.clear()
        self._total_weight = 0

    def __len__(self) -> int:
        return len(self._values)


@dataclass
class RepoMapTurn:
    turn_id: str
    source_request: str
    not_ready_locked: bool = False
    closed: bool = False


@dataclass(frozen=True)
class WorkspaceBaseline:
    revision: str | None


@dataclass(frozen=True)
class _IndexState:
    discovery: DiscoveryResult
    workspace: WorkspaceState
    parsed: tuple[ParsedPythonFile, ...]
    graph: RepoGraph
    cache: RepoMapCacheStatus
    elapsed_ms: float


def _index_health(index: _IndexState) -> tuple[str, str | None]:
    if not index.discovery.files:
        return "empty", "no-python-files"
    degraded = (
        bool(index.discovery.diagnostics)
        or any(item.diagnostics for item in index.parsed)
        or bool(index.graph.diagnostics)
    )
    return ("degraded", "partial-index") if degraded else ("ready", None)


class RepoMapToolObserver:
    def __init__(self, manager: RepoMapManager, turn: RepoMapTurn) -> None:
        self.manager = manager
        self.turn = turn

    async def before_execute(self, call: ToolCall, spec: ToolSpec) -> WorkspaceBaseline | None:
        _ = call
        if spec.safety == "read_only":
            return None
        return WorkspaceBaseline(self.manager.current_revision)

    async def after_execute(
        self,
        call: ToolCall,
        spec: ToolSpec,
        result: ToolResult,
        state: object | None,
    ) -> None:
        _ = call, result, state
        if spec.safety == "read_only":
            return
        changed = await self.manager.refresh_after_side_effect()
        if changed:
            self.turn.not_ready_locked = False


class RepoMapManager:
    def __init__(
        self,
        cwd: Path,
        config: RepoMapConfig | None = None,
        *,
        discovery_factory: Callable[[Path], DiscoveryResult] = scan_repository,
        parser: PythonSymbolParser | None = None,
        graph_builder: RepoGraphBuilder | None = None,
        renderer: RepoMapRenderer | None = None,
    ) -> None:
        self.cwd = cwd.resolve()
        self.config = config or RepoMapConfig()
        self.discovery_factory = discovery_factory
        self.parser = parser or PythonSymbolParser()
        self.graph_builder = graph_builder or RepoGraphBuilder()
        self.renderer = renderer or RepoMapRenderer()
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mewcode-repo-map")
        self._parse_cache: _BoundedLRU[tuple[object, ...], ParsedPythonFile] = _BoundedLRU(
            4096,
            max_weight=64 * 1024 * 1024,
            weight=lambda parsed: parsed.fingerprint.size,
        )
        self._graph_cache: _BoundedLRU[tuple[object, ...], RepoGraph] = _BoundedLRU(8)
        self._snapshot_cache: _BoundedLRU[tuple[object, ...], RepoMapSnapshot] = _BoundedLRU(64)
        self._index: _IndexState | None = None
        self._generation = 0
        self._task: asyncio.Task[bool] | None = None
        self._closed = False
        self._parse_invocations = 0
        state = "disabled" if not self.config.enabled else "indexing"
        reason = "disabled" if not self.config.enabled else "not-ready"
        self._status = RepoMapStatus(
            enabled=self.config.enabled,
            state=state,  # type: ignore[arg-type]
            root=self.cwd.as_posix(),
            configured_budget=self.config.max_tokens,
            reason=reason,
        )

    @property
    def current_revision(self) -> str | None:
        return self._index.workspace.revision if self._index is not None else None

    @property
    def parse_invocations(self) -> int:
        return self._parse_invocations

    async def start(self) -> None:
        if not self.config.enabled or self._closed:
            return
        if self._task is None or self._task.done():
            self._schedule_refresh()

    async def close(self) -> None:
        self._closed = True
        self._generation += 1
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._status = replace(
            self._status,
            state="closed",
            revision=None,
            effective_budget=0,
            included_files=0,
            truncated=False,
            cache=RepoMapCacheStatus(),
            elapsed_ms=None,
            reason="closed",
        )

    async def wait_until_ready(self) -> bool:
        task = self._task
        if task is None:
            return self._index is not None
        try:
            return await task
        except asyncio.CancelledError:
            return False

    def begin_turn(self, source_request: str) -> RepoMapTurn:
        return RepoMapTurn(turn_id=uuid.uuid4().hex, source_request=source_request)

    def end_turn(self, turn: RepoMapTurn) -> None:
        turn.closed = True

    def observer_for(self, turn: RepoMapTurn) -> RepoMapToolObserver:
        return RepoMapToolObserver(self, turn)

    async def build_snapshot(
        self,
        turn: RepoMapTurn,
        granted_tokens: int,
        token_counter: Callable[[str], int],
    ) -> RepoMapSnapshot | None:
        if turn.closed or not self.config.enabled or self._closed:
            return None
        index = self._index
        if index is None:
            turn.not_ready_locked = True
            self._status = replace(
                self._status,
                revision=None,
                effective_budget=max(0, granted_tokens),
                included_files=0,
                truncated=False,
                cache=RepoMapCacheStatus(),
                elapsed_ms=None,
                reason="not-ready",
            )
            return None
        if turn.not_ready_locked:
            self._status = replace(
                self._status,
                revision=None,
                effective_budget=max(0, granted_tokens),
                included_files=0,
                truncated=False,
                cache=RepoMapCacheStatus(parse=index.cache.parse, graph=index.cache.graph),
                elapsed_ms=None,
                reason="not-ready",
            )
            return None
        effective_budget = min(self.config.max_tokens, max(0, granted_tokens))
        if effective_budget <= 0:
            self._status = replace(
                self._status,
                revision=None,
                effective_budget=0,
                included_files=0,
                truncated=False,
                cache=RepoMapCacheStatus(parse=index.cache.parse, graph=index.cache.graph),
                elapsed_ms=None,
                reason="budget-too-small",
            )
            return None

        hints_hash = hashlib.sha256(
            turn.source_request.replace("\\", "/").casefold().encode("utf-8")
        ).hexdigest()
        key = (index.workspace.revision, hints_hash, effective_budget, RANKING_VERSION, RENDERER_VERSION)
        with self._lock:
            cached = self._snapshot_cache.get(key)
        if cached is not None:
            state, reason = _index_health(index)
            self._status = replace(
                self._status,
                state=state,  # type: ignore[arg-type]
                revision=index.workspace.revision,
                effective_budget=effective_budget,
                included_files=len(cached.included_files),
                truncated=cached.truncated,
                cache=RepoMapCacheStatus(parse=index.cache.parse, graph=index.cache.graph, snapshot="hit"),
                elapsed_ms=0.0,
                reason=reason,
            )
            return cached

        started = time.perf_counter()
        try:
            snapshot = await _run_in_executor_poll(
                self._executor,
                self._render_snapshot,
                index,
                turn.source_request,
                effective_budget,
                token_counter,
                key,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._status = replace(
                self._status,
                state="degraded",
                revision=None,
                effective_budget=effective_budget,
                included_files=0,
                truncated=False,
                cache=RepoMapCacheStatus(parse=index.cache.parse, graph=index.cache.graph),
                elapsed_ms=None,
                reason=f"render-error:{type(exc).__name__}",
            )
            return None
        elapsed_ms = (time.perf_counter() - started) * 1000
        if snapshot is None:
            self._status = replace(
                self._status,
                revision=None,
                effective_budget=effective_budget,
                included_files=0,
                truncated=False,
                cache=RepoMapCacheStatus(parse=index.cache.parse, graph=index.cache.graph, snapshot="miss"),
                elapsed_ms=None,
                reason="budget-too-small",
            )
            return None
        with self._lock:
            self._snapshot_cache.put(key, snapshot)
        state, reason = _index_health(index)
        self._status = replace(
            self._status,
            state=state,  # type: ignore[arg-type]
            revision=index.workspace.revision,
            effective_budget=effective_budget,
            included_files=len(snapshot.included_files),
            truncated=snapshot.truncated,
            cache=RepoMapCacheStatus(parse=index.cache.parse, graph=index.cache.graph, snapshot="miss"),
            elapsed_ms=elapsed_ms,
            reason=reason,
        )
        return snapshot

    async def refresh_after_side_effect(self) -> bool:
        before = self.current_revision
        self._generation += 1
        generation = self._generation
        result = await self._refresh(generation)
        return result and before != self.current_revision

    def status(self) -> RepoMapStatus:
        return self._status

    def _schedule_refresh(self) -> None:
        self._generation += 1
        generation = self._generation
        self._status = replace(self._status, state="indexing", reason="not-ready")
        self._task = asyncio.create_task(self._refresh(generation))

    async def _refresh(self, generation: int) -> bool:
        try:
            index = await _run_in_executor_poll(self._executor, self._build_index)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if generation == self._generation and not self._closed:
                self._status = replace(
                    self._status,
                    state="degraded",
                    included_files=0,
                    truncated=False,
                    reason=f"scan-error:{type(exc).__name__}",
                )
            return False
        if generation != self._generation or self._closed:
            return False
        self._index = index
        state, reason = _index_health(index)
        self._status = RepoMapStatus(
            enabled=True,
            state=state,  # type: ignore[arg-type]
            root=index.discovery.identity.root.as_posix(),
            revision=index.workspace.revision,
            configured_budget=self.config.max_tokens,
            candidate_files=len(index.discovery.files),
            cache=index.cache,
            elapsed_ms=index.elapsed_ms,
            reason=reason,
        )
        return True

    def _build_index(self) -> _IndexState:
        started = time.perf_counter()
        discovery = self.discovery_factory(self.cwd)
        workspace = build_workspace_state(
            discovery,
            rules_version=f"{DISCOVERY_VERSION}:{PARSER_VERSION}:{GRAPH_VERSION}",
        )
        repository_paths = tuple(item.fingerprint.relative_path for item in discovery.files)
        parsed_files: list[ParsedPythonFile] = []
        parse_hits = 0
        parse_misses = 0
        for scanned in discovery.files:
            parse_key = (
                discovery.identity.repo_id,
                scanned.fingerprint.relative_path,
                scanned.fingerprint.content_hash,
                PARSER_VERSION,
            )
            with self._lock:
                parsed = self._parse_cache.get(parse_key)
            if parsed is None:
                parsed = self.parser.parse(scanned, repository_paths=repository_paths)
                self._parse_invocations += 1
                parse_misses += 1
                with self._lock:
                    self._parse_cache.put(parse_key, parsed)
            else:
                parse_hits += 1
            parsed_files.append(parsed)
        parsed_tuple = tuple(sorted(parsed_files, key=lambda item: item.fingerprint.relative_path))
        graph_key = (
            discovery.identity.repo_id,
            discovery.identity.worktree_id,
            discovery.identity.head_id,
            tuple((item.relative_path, item.content_hash) for item in workspace.ordered_fingerprints),
            GRAPH_VERSION,
        )
        with self._lock:
            graph = self._graph_cache.get(graph_key)
        graph_status = "hit" if graph is not None else "miss"
        if graph is None:
            try:
                graph = self.graph_builder.build(parsed_tuple)
            except Exception as exc:
                nodes = tuple(item.fingerprint.relative_path for item in parsed_tuple)
                uniform = round(1.0 / len(nodes), 8) if nodes else 0.0
                graph = RepoGraph(
                    nodes=nodes,
                    edges=(),
                    scores=tuple((node, uniform) for node in nodes),
                    diagnostics=(
                        RepoMapDiagnostic("graph-error", f"关系图降级：{exc}", level="error"),
                    ),
                )
            with self._lock:
                self._graph_cache.put(graph_key, graph)
        parse_status = "unused" if not discovery.files else "hit" if parse_misses == 0 else "miss" if parse_hits == 0 else "mixed"
        return _IndexState(
            discovery=discovery,
            workspace=workspace,
            parsed=parsed_tuple,
            graph=graph,
            cache=RepoMapCacheStatus(parse=parse_status, graph=graph_status),  # type: ignore[arg-type]
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def _render_snapshot(
        self,
        index: _IndexState,
        source_request: str,
        budget: int,
        token_counter: Callable[[str], int],
        key: tuple[object, ...],
    ) -> RepoMapSnapshot | None:
        ranked = rank_symbols(index.parsed, index.graph, source_request)
        rendered = self.renderer.render(
            ranked,
            root=index.discovery.identity.root,
            revision=index.workspace.revision,
            budget=budget,
            token_counter=token_counter,
        )
        if rendered is None:
            return None
        snapshot_id = hashlib.sha256(
            json.dumps(key, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return RepoMapSnapshot(
            snapshot_id=snapshot_id,
            revision=index.workspace.revision,
            text=rendered.text,
            estimated_tokens=rendered.estimated_tokens,
            included_files=rendered.included_files,
            truncated=rendered.truncated,
        )


async def _run_in_executor_poll(
    executor: ThreadPoolExecutor,
    function: Callable[..., V],
    *args: object,
) -> V:
    """在线程工作期间定期让出事件循环，并兼容无法可靠唤醒 selector 的终端环境。"""
    future = asyncio.get_running_loop().run_in_executor(executor, function, *args)
    try:
        while not future.done():
            await asyncio.sleep(0.005)
        return future.result()
    except asyncio.CancelledError:
        future.cancel()
        raise
