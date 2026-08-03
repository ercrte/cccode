from __future__ import annotations

import asyncio
from pathlib import Path

from julycode.context.manager import ContextManager
from julycode.memory.index import MemoryIndexBuilder
from julycode.memory.models import BootstrapOptions, BootstrapResult, KnowledgeContext, MemoryUpdateJob, SessionMemoryConfig
from julycode.memory.notes import MemoryNoteStore
from julycode.memory.recovery import SessionBootstrapper
from julycode.memory.updater import MemoryNoteUpdater
from julycode.providers.base import LLMProvider


class SessionMemoryManager:
    def __init__(
        self,
        cwd: Path,
        config: SessionMemoryConfig | None = None,
        *,
        bootstrapper: SessionBootstrapper | None = None,
        updater: MemoryNoteUpdater | None = None,
    ) -> None:
        self.cwd = cwd.resolve()
        self.config = config or SessionMemoryConfig()
        note_store = MemoryNoteStore(self.cwd, self.config)
        index_builder = MemoryIndexBuilder(note_store, self.config)
        self.bootstrapper = bootstrapper or SessionBootstrapper(self.cwd, self.config, note_store=note_store, index_builder=index_builder)
        self.updater = updater or MemoryNoteUpdater(note_store, index_builder)
        self._context = KnowledgeContext()
        self._tasks: set[asyncio.Task[None]] = set()
        self.warnings: list[str] = []

    async def bootstrap(
        self,
        *,
        options: BootstrapOptions,
        provider: LLMProvider,
        context_manager: ContextManager,
    ) -> BootstrapResult:
        result = await self.bootstrapper.bootstrap(options=options, provider=provider, context_manager=context_manager)
        self._context = result.knowledge_context
        return result

    def runtime_context(self) -> KnowledgeContext:
        return self._context

    def load_runtime_context(self) -> KnowledgeContext:
        if not self.config.enabled:
            self._context = KnowledgeContext()
            return self._context
        self._context = self.bootstrapper.load_knowledge()
        return self._context

    def schedule_update(self, *, job: MemoryUpdateJob, provider: LLMProvider) -> None:
        if not self.config.enabled or not self.config.auto_notes_enabled:
            return

        async def runner() -> None:
            try:
                indexes = await self.updater.update(job=job, provider=provider)
                user_index = self._context.user_memory_index
                project_index = self._context.project_memory_index
                for index in indexes:
                    if index.scope == "user":
                        user_index = index
                    else:
                        project_index = index
                self._context = KnowledgeContext(
                    instructions=self._context.instructions,
                    user_memory_index=user_index,
                    project_memory_index=project_index,
                    restore_report=self._context.restore_report,
                )
            except Exception as exc:
                self.warnings.append(f"自动记忆更新失败: {exc}")

        task = asyncio.create_task(runner())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_for_updates(self) -> None:
        if not self._tasks:
            return
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
