from __future__ import annotations

import asyncio
from dataclasses import replace
from dataclasses import FrozenInstanceError
import math
from pathlib import Path
import threading
import time

import pytest

from mewcode.repo_map import FileFingerprint, RepositoryIdentity, WorkspaceState
from mewcode.repo_map.discovery import scan_repository
from mewcode.repo_map.manager import RepoMapManager


def test_repo_map_models_are_immutable_and_comparable(tmp_path: Path) -> None:
    identity = RepositoryIdentity(
        root=tmp_path,
        repo_id="repo",
        worktree_id="worktree",
        head_id="head",
        is_git=True,
    )
    fingerprint = FileFingerprint("src/example.py", "abc", 3)
    state = WorkspaceState(identity, (fingerprint,), "revision")

    assert state == WorkspaceState(identity, (fingerprint,), "revision")
    with pytest.raises(FrozenInstanceError):
        fingerprint.size = 4  # type: ignore[misc]


def _tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


@pytest.mark.asyncio
async def test_manager_reuses_snapshot_for_same_revision_and_budget(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def run(value: str = 'secret'): pass\n", encoding="utf-8")
    manager = RepoMapManager(tmp_path)
    await manager.start()
    assert await manager.wait_until_ready() is True
    turn = manager.begin_turn("请查找 run")

    first = await manager.build_snapshot(turn, 2000, _tokens)
    second = await manager.build_snapshot(turn, 2000, _tokens)

    assert first is not None
    assert second == first
    assert manager.status().cache.snapshot == "hit"
    assert "secret" not in first.text
    await manager.close()


@pytest.mark.asyncio
async def test_manager_not_ready_decision_is_fixed_for_turn(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def run(): pass\n", encoding="utf-8")
    release = threading.Event()

    def delayed_discovery(path: Path):
        release.wait(timeout=2)
        return scan_repository(path)

    manager = RepoMapManager(tmp_path, discovery_factory=delayed_discovery)
    await manager.start()
    turn = manager.begin_turn("run")
    assert await manager.build_snapshot(turn, 2000, _tokens) is None
    release.set()
    assert await manager.wait_until_ready() is True

    assert await manager.build_snapshot(turn, 2000, _tokens) is None
    assert manager.status().reason == "not-ready"
    await manager.close()


@pytest.mark.asyncio
async def test_manager_reparses_only_changed_file_and_updates_revision(tmp_path: Path) -> None:
    first_file = tmp_path / "first.py"
    first_file.write_text("def first(): pass\n", encoding="utf-8")
    (tmp_path / "second.py").write_text("def second(): pass\n", encoding="utf-8")
    manager = RepoMapManager(tmp_path)
    await manager.start()
    assert await manager.wait_until_ready() is True
    before_revision = manager.current_revision
    assert manager.parse_invocations == 2

    first_file.write_text("def first_new(): pass\n", encoding="utf-8")
    assert await manager.refresh_after_side_effect() is True

    assert manager.current_revision != before_revision
    assert manager.parse_invocations == 3
    turn = manager.begin_turn("first_new")
    snapshot = await manager.build_snapshot(turn, 2000, _tokens)
    assert snapshot is not None
    assert "first_new" in snapshot.text
    assert "def first()" not in snapshot.text
    await manager.close()


@pytest.mark.asyncio
async def test_manager_no_source_change_keeps_revision_and_parse_cache(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def run(): pass\n", encoding="utf-8")
    manager = RepoMapManager(tmp_path)
    await manager.start()
    assert await manager.wait_until_ready() is True
    revision = manager.current_revision

    assert await manager.refresh_after_side_effect() is False

    assert manager.current_revision == revision
    assert manager.parse_invocations == 1
    assert manager.status().cache.parse == "hit"
    assert manager.status().cache.graph == "hit"
    await manager.close()


@pytest.mark.asyncio
async def test_manager_tracks_created_and_deleted_python_files(tmp_path: Path) -> None:
    original = tmp_path / "original.py"
    original.write_text("def original_symbol(): pass\n", encoding="utf-8")
    manager = RepoMapManager(tmp_path)
    await manager.start()
    assert await manager.wait_until_ready() is True
    first_revision = manager.current_revision

    created = tmp_path / "created.py"
    created.write_text("def created_symbol(): pass\n", encoding="utf-8")
    assert await manager.refresh_after_side_effect() is True
    second_revision = manager.current_revision
    created_snapshot = await manager.build_snapshot(
        manager.begin_turn("created_symbol"), 2000, _tokens
    )

    original.unlink()
    assert await manager.refresh_after_side_effect() is True
    deleted_snapshot = await manager.build_snapshot(
        manager.begin_turn("original_symbol created_symbol"), 2000, _tokens
    )

    assert first_revision != second_revision != manager.current_revision
    assert created_snapshot is not None and "created_symbol" in created_snapshot.text
    assert deleted_snapshot is not None
    assert "original_symbol" not in deleted_snapshot.text
    assert "created_symbol" in deleted_snapshot.text
    assert manager.parse_invocations == 2
    await manager.close()


@pytest.mark.asyncio
async def test_omitted_snapshot_clears_previous_snapshot_status(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def run(): pass\n", encoding="utf-8")
    manager = RepoMapManager(tmp_path)
    await manager.start()
    assert await manager.wait_until_ready() is True
    turn = manager.begin_turn("run")
    assert await manager.build_snapshot(turn, 2000, _tokens) is not None
    assert manager.status().revision is not None

    assert await manager.build_snapshot(turn, 0, _tokens) is None
    omitted = manager.status()

    assert omitted.revision is None
    assert omitted.effective_budget == 0
    assert omitted.included_files == 0
    assert omitted.truncated is False
    assert omitted.cache.snapshot == "unused"
    assert omitted.elapsed_ms is None
    assert omitted.reason == "budget-too-small"
    await manager.close()


@pytest.mark.asyncio
async def test_graph_and_renderer_failures_degrade_without_raising(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def run(): pass\n", encoding="utf-8")

    class BrokenGraphBuilder:
        def build(self, parsed):
            _ = parsed
            raise RuntimeError("graph failed")

    graph_manager = RepoMapManager(tmp_path, graph_builder=BrokenGraphBuilder())  # type: ignore[arg-type]
    await graph_manager.start()
    assert await graph_manager.wait_until_ready() is True
    assert graph_manager.status().state == "degraded"
    assert graph_manager.status().reason == "partial-index"
    assert await graph_manager.build_snapshot(
        graph_manager.begin_turn("run"), 2000, _tokens
    ) is not None
    await graph_manager.close()

    class BrokenRenderer:
        def render(self, *args, **kwargs):
            _ = args, kwargs
            raise RuntimeError("render failed")

    render_manager = RepoMapManager(tmp_path, renderer=BrokenRenderer())  # type: ignore[arg-type]
    await render_manager.start()
    assert await render_manager.wait_until_ready() is True
    assert await render_manager.build_snapshot(
        render_manager.begin_turn("run"), 2000, _tokens
    ) is None
    assert render_manager.status().state == "degraded"
    assert render_manager.status().reason == "render-error:RuntimeError"
    assert render_manager.status().revision is None
    await render_manager.close()


@pytest.mark.asyncio
async def test_manager_degrades_for_empty_repository(tmp_path: Path) -> None:
    manager = RepoMapManager(tmp_path)
    await manager.start()
    assert await manager.wait_until_ready() is True

    assert manager.status().state == "empty"
    assert manager.status().reason == "no-python-files"
    assert await manager.build_snapshot(manager.begin_turn("hello"), 2000, _tokens) is None
    await manager.close()


@pytest.mark.asyncio
async def test_manager_snapshot_is_byte_stable_for_shuffled_discovery(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def alpha(): pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from a import alpha\ndef beta(): alpha()\n", encoding="utf-8")

    def reversed_discovery(path: Path):
        result = scan_repository(path)
        return replace(result, files=tuple(reversed(result.files)))

    first_manager = RepoMapManager(tmp_path)
    second_manager = RepoMapManager(tmp_path, discovery_factory=reversed_discovery)
    await first_manager.start()
    await second_manager.start()
    assert await first_manager.wait_until_ready() is True
    assert await second_manager.wait_until_ready() is True

    first = await first_manager.build_snapshot(first_manager.begin_turn("alpha beta"), 2000, _tokens)
    second = await second_manager.build_snapshot(second_manager.begin_turn("alpha beta"), 2000, _tokens)

    assert first is not None
    assert second is not None
    assert first.revision == second.revision
    assert first.snapshot_id == second.snapshot_id
    assert first.text.encode("utf-8") == second.text.encode("utf-8")
    await first_manager.close()
    await second_manager.close()


@pytest.mark.asyncio
async def test_current_repository_cached_snapshot_p95_is_below_50ms() -> None:
    manager = RepoMapManager(Path.cwd())
    await manager.start()
    assert await manager.wait_until_ready() is True
    turn = manager.begin_turn("Repo Map manager context provider")
    assert await manager.build_snapshot(turn, 2000, _tokens) is not None

    samples: list[float] = []
    for _ in range(100):
        started = time.perf_counter()
        assert await manager.build_snapshot(turn, 2000, _tokens) is not None
        samples.append((time.perf_counter() - started) * 1000)

    p95 = sorted(samples)[94]
    assert len(samples) == 100
    assert p95 < 50
    await manager.close()


@pytest.mark.asyncio
async def test_background_index_and_refresh_do_not_block_event_loop_over_16ms(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("def before(): pass\n", encoding="utf-8")

    def slow_discovery(path: Path):
        time.sleep(0.05)
        return scan_repository(path)

    manager = RepoMapManager(tmp_path, discovery_factory=slow_discovery)
    stopped = asyncio.Event()
    gaps_ms: list[float] = []

    async def heartbeat() -> None:
        previous = time.perf_counter()
        while not stopped.is_set():
            await asyncio.sleep(0.001)
            current = time.perf_counter()
            gaps_ms.append((current - previous) * 1000)
            previous = current

    heartbeat_task = asyncio.create_task(heartbeat())
    await manager.start()
    assert await manager.wait_until_ready() is True
    source.write_text("def after(): pass\n", encoding="utf-8")
    assert await manager.refresh_after_side_effect() is True
    stopped.set()
    await heartbeat_task

    assert gaps_ms
    assert max(gaps_ms) < 16
    await manager.close()


@pytest.mark.asyncio
async def test_stale_generation_is_discarded_after_new_refresh(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("def old_name(): pass\n", encoding="utf-8")
    first_started = threading.Event()
    release_first = threading.Event()
    calls = 0

    def delayed_first_discovery(path: Path):
        nonlocal calls
        calls += 1
        if calls == 1:
            stale = scan_repository(path)
            first_started.set()
            release_first.wait(timeout=2)
            return stale
        return scan_repository(path)

    manager = RepoMapManager(tmp_path, discovery_factory=delayed_first_discovery)
    await manager.start()
    while not first_started.is_set():
        await asyncio.sleep(0.001)
    source.write_text("def fresh_name(): pass\n", encoding="utf-8")
    refresh_task = asyncio.create_task(manager.refresh_after_side_effect())
    release_first.set()

    assert await refresh_task is True
    snapshot = await manager.build_snapshot(manager.begin_turn("fresh_name"), 2000, _tokens)
    assert snapshot is not None
    assert "fresh_name" in snapshot.text
    assert "old_name" not in snapshot.text
    await manager.close()


@pytest.mark.asyncio
async def test_close_cancels_initial_index_without_committing_result(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def run(): pass\n", encoding="utf-8")
    started = threading.Event()
    release = threading.Event()

    def blocked_discovery(path: Path):
        started.set()
        release.wait(timeout=2)
        return scan_repository(path)

    manager = RepoMapManager(tmp_path, discovery_factory=blocked_discovery)
    await manager.start()
    while not started.is_set():
        await asyncio.sleep(0.001)
    await manager.close()
    release.set()
    await asyncio.sleep(0.01)

    assert manager.current_revision is None
    assert manager.status().state == "closed"
