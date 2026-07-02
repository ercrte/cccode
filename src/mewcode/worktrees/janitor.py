from __future__ import annotations

import asyncio
from collections.abc import Callable

from mewcode.worktrees.models import CleanupItemResult, CleanupReport
from mewcode.worktrees.manager import WorktreeManager


class WorktreeJanitor:
    def __init__(
        self,
        manager: WorktreeManager,
        *,
        report: Callable[[CleanupReport], None] | None = None,
    ) -> None:
        self.manager = manager
        self.report = report
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_loop())

    async def run_once(self) -> CleanupReport:
        return await self.manager.cleanup_expired()

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _run_loop(self) -> None:
        while True:
            try:
                report = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                report = CleanupReport(
                    items=(
                        CleanupItemResult(
                            path=self.manager.main_cwd,
                            status="failed",
                            reason=f"janitor 执行失败: {exc}",
                        ),
                    )
                )
            if self.report is not None:
                try:
                    self.report(report)
                except Exception:
                    pass
            await asyncio.sleep(self.manager.config.cleanup_interval_seconds)
