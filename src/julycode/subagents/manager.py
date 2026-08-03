from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from julycode.config import AppConfig
from julycode.errors import JulyCodeError, redact_secret
from julycode.hooks.manager import HookManager
from julycode.providers.base import ChatMessage, LLMProvider
from julycode.session import ChatSession
from julycode.skills.execution import ProviderResolver
from julycode.subagents.loader import SubAgentRoleLoader
from julycode.subagents.models import (
    BackgroundSubAgentRecord,
    ParentAgentContext,
    SubAgentBackgroundSummary,
    SubAgentInvocation,
    SubAgentPromptContext,
    SubAgentRefreshReport,
    SubAgentResult,
    SubAgentRoleCatalog,
    SubAgentRoleDefinition,
    SubAgentRoleRoots,
    SubAgentWorkingContext,
    SubAgentWorktreeInfo,
)
from julycode.subagents.runtime import SubAgentRunnerFactory, run_sub_agent_to_result
from julycode.tools.executor import ToolExecutor
from julycode.tools.registry import ToolRegistry
from julycode.worktrees import (
    CleanupReport,
    WorktreeDisposition,
    WorktreeJanitor,
    WorktreeManager,
)

if TYPE_CHECKING:
    from julycode.mcp.manager import McpManager


class SubAgentConfigurationError(JulyCodeError):
    pass


class SubAgentManager:
    def __init__(
        self,
        *,
        roots: SubAgentRoleRoots,
        tool_registry: ToolRegistry,
        executor: ToolExecutor,
        config: AppConfig,
        provider: LLMProvider,
        provider_resolver: ProviderResolver,
        hook_manager: HookManager | None,
        main_session: ChatSession,
        notify: Callable[[str], Awaitable[None]] | None = None,
        worktree_manager: WorktreeManager | None = None,
        worktree_janitor: WorktreeJanitor | None = None,
        cleanup_reporter: Callable[[CleanupReport], None] | None = None,
        mcp_manager: McpManager | None = None,
    ) -> None:
        self.roots = roots
        self.tool_registry = tool_registry
        self.executor = executor
        self.config = config
        self.provider = provider
        self.provider_resolver = provider_resolver
        self.hook_manager = hook_manager
        self.main_session = main_session
        self.notify = notify
        self.mcp_manager = mcp_manager
        self.loader = SubAgentRoleLoader(roots)
        self.catalog = SubAgentRoleCatalog(definitions={})
        self.parent_context: ParentAgentContext | None = None
        self.records: dict[str, BackgroundSubAgentRecord] = {}
        self._foreground_record: BackgroundSubAgentRecord | None = None
        self._counter = 0
        self.worktree_manager = worktree_manager or WorktreeManager(
            self.executor.context.cwd,
            self.config.sub_agents.worktree,
        )
        self.worktree_janitor = worktree_janitor or WorktreeJanitor(
            self.worktree_manager,
            report=cleanup_reporter,
        )

    def start(self) -> None:
        self.worktree_janitor.start()

    def refresh_if_changed(self) -> SubAgentRefreshReport:
        catalog = self.loader.discover()
        if catalog.fingerprint == self.catalog.fingerprint:
            return SubAgentRefreshReport(changed=False, warnings=catalog.warnings)
        errors = self._validation_errors(catalog)
        if errors:
            raise SubAgentConfigurationError("子 Agent 角色配置错误:\n" + "\n".join(f"- {error}" for error in errors))
        self.catalog = catalog
        return SubAgentRefreshReport(changed=True, warnings=catalog.warnings)

    def prompt_context(self) -> SubAgentPromptContext:
        return SubAgentPromptContext(
            available_roles=tuple(definition.summary() for definition in self.catalog.definitions.values()),
            warnings=self.catalog.warnings,
            background=tuple(self._background_summary(record) for record in self.records.values()),
        )

    def bind_parent_context(self, context: ParentAgentContext | None) -> None:
        self.parent_context = context

    async def delegate(self, invocation: SubAgentInvocation) -> SubAgentResult | BackgroundSubAgentRecord:
        if not self.config.sub_agents.enabled:
            raise SubAgentConfigurationError("子 Agent 功能已关闭")
        parent = self.parent_context
        if parent is None:
            raise SubAgentConfigurationError("当前没有可用于委派的父 Agent 上下文")
        if len([record for record in self.records.values() if record.status in {"queued", "running", "background"}]) >= self.config.sub_agents.max_background_tasks:
            raise SubAgentConfigurationError("后台子 Agent 任务数量已达到上限")

        role = self._role_for(invocation)
        if invocation.type == "fork" and not invocation.background:
            invocation = replace(invocation, background=True)
        record = self._create_record(invocation)
        background = invocation.background
        task = asyncio.create_task(self._run_record(record, parent, role, background))
        record.task = task
        task.add_done_callback(lambda done: asyncio.create_task(self._task_done(record, done)))
        if background:
            record.status = "background"
            return record
        return await self._wait_foreground(record)

    def background_snapshot(self) -> tuple[BackgroundSubAgentRecord, ...]:
        return tuple(self.records.values())

    def foreground_running(self) -> bool:
        record = self._foreground_record
        return record is not None and record.status == "running"

    def background_current_foreground(self) -> bool:
        record = self._foreground_record
        if record is None or record.force_background is None or record.status != "running":
            return False
        record.force_background.set()
        return True

    async def close(self) -> None:
        tasks = [record.task for record in self.records.values() if record.task is not None and not record.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.worktree_janitor.close()

    def _role_for(self, invocation: SubAgentInvocation) -> SubAgentRoleDefinition | None:
        if invocation.type == "fork":
            return None
        role_name = invocation.role or ""
        role = self.catalog.definitions.get(role_name)
        if role is None:
            available = ", ".join(sorted(self.catalog.definitions)) or "无"
            raise SubAgentConfigurationError(f"未知子 Agent 角色: {role_name}。可用角色: {available}")
        return role

    def _create_record(self, invocation: SubAgentInvocation) -> BackgroundSubAgentRecord:
        self._counter += 1
        task_id = f"subagent-{int(time.time() * 1000)}-{self._counter}"
        record = BackgroundSubAgentRecord(
            task_id=task_id,
            invocation=invocation,
            status="queued",
            created_at=time.time(),
            force_background=asyncio.Event(),
        )
        self.records[task_id] = record
        return record

    async def _run_record(
        self,
        record: BackgroundSubAgentRecord,
        parent: ParentAgentContext,
        role: SubAgentRoleDefinition | None,
        background: bool,
    ) -> SubAgentResult:
        record.status = "background" if background else "running"
        record.started_at = time.time()
        lease = None
        result: SubAgentResult | None = None
        disposition: WorktreeDisposition | None = None
        try:
            if role is not None and role.frontmatter.isolation == "worktree":
                lease = await self.worktree_manager.acquire(task_id=record.task_id, role=role.name)
                record.worktree_lease = lease
                working_context = SubAgentWorkingContext(
                    cwd=lease.cwd,
                    main_cwd=self.executor.context.cwd,
                    isolation="worktree",
                    lease=lease,
                )
            else:
                working_context = SubAgentWorkingContext(
                    cwd=self.executor.context.cwd,
                    main_cwd=self.executor.context.cwd,
                    isolation="shared",
                )
            factory = SubAgentRunnerFactory(
                registry=self.tool_registry,
                executor=self.executor,
                config=self.config,
                provider=self.provider,
                provider_resolver=self.provider_resolver,
                hook_manager=self.hook_manager,
                mcp_manager=self.mcp_manager,
            )
            runner, command, _session = factory.create_runner(
                task_id=record.task_id,
                invocation=record.invocation,
                parent=parent,
                role=role,
                background=background,
                working_context=working_context,
            )
            result = await run_sub_agent_to_result(
                task_id=record.task_id,
                invocation=record.invocation,
                runner=runner,
                command=command,
            )
        except asyncio.CancelledError:
            result = self._failed_result(record, "cancelled", cancelled=True)
        except Exception as exc:
            result = self._failed_result(record, redact_secret(str(exc)))
        finally:
            if lease is not None:
                try:
                    disposition = await self.worktree_manager.finish(lease)
                except Exception as exc:
                    disposition = WorktreeDisposition(
                        status="retained",
                        root=lease.root,
                        cwd=lease.cwd,
                        branch=lease.metadata.branch,
                        reason=f"退出处置失败，已保留: {redact_secret(str(exc))}",
                    )
        if result is None:
            result = self._failed_result(record, "子 Agent 未返回结果")
        if lease is not None and disposition is not None:
            result = replace(
                result,
                worktree=SubAgentWorktreeInfo(
                    root=str(disposition.root),
                    cwd=str(disposition.cwd),
                    branch=disposition.branch,
                    base_commit=lease.metadata.base_commit,
                    disposition=disposition.status,
                    reason=disposition.reason,
                ),
            )
        return result

    def _failed_result(
        self,
        record: BackgroundSubAgentRecord,
        error: str,
        *,
        cancelled: bool = False,
    ) -> SubAgentResult:
        return SubAgentResult(
            task_id=record.task_id,
            type=record.invocation.type,
            role=record.invocation.role,
            status="cancelled" if cancelled else "failed",
            task=record.invocation.task,
            summary="子 Agent 已取消。" if cancelled else f"子 Agent 失败：{error}",
            stop_reason="cancelled" if cancelled else "error",
            error=error,
        )

    async def _wait_foreground(self, record: BackgroundSubAgentRecord) -> SubAgentResult | BackgroundSubAgentRecord:
        timeout = record.invocation.foreground_timeout_seconds or self.config.sub_agents.foreground_timeout_seconds
        force_task = asyncio.create_task(record.force_background.wait()) if record.force_background is not None else None
        task = record.task
        if task is None:
            raise SubAgentConfigurationError("子 Agent 任务未正确启动")
        self._foreground_record = record
        try:
            wait_set = {task}
            if force_task is not None:
                wait_set.add(force_task)
            done, _pending = await asyncio.wait(wait_set, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                result = task.result()
                return result
            record.status = "background"
            record.invocation = replace(record.invocation, background=True)
            return record
        finally:
            if force_task is not None and not force_task.done():
                force_task.cancel()
            if self._foreground_record is record and record.status != "background":
                self._foreground_record = None

    async def _task_done(self, record: BackgroundSubAgentRecord, task: asyncio.Task[SubAgentResult]) -> None:
        try:
            result = task.result()
        except asyncio.CancelledError:
            result = SubAgentResult(
                task_id=record.task_id,
                type=record.invocation.type,
                role=record.invocation.role,
                status="cancelled",
                task=record.invocation.task,
                summary="子 Agent 已取消。",
                stop_reason="cancelled",
                error="cancelled",
            )
        except Exception as exc:
            error = redact_secret(str(exc))
            result = SubAgentResult(
                task_id=record.task_id,
                type=record.invocation.type,
                role=record.invocation.role,
                status="failed",
                task=record.invocation.task,
                summary=f"子 Agent 失败：{error}",
                stop_reason="error",
                error=error,
            )
        record.result = result
        record.status = result.status
        record.error = result.error
        record.usage = result.usage
        record.finished_at = time.time()
        if self._foreground_record is record:
            self._foreground_record = None
        if record.status in {"background", "running", "queued"}:
            return
        if record.invocation.background and not record.notified:
            await self._notify_completion(record, result)

    async def _notify_completion(self, record: BackgroundSubAgentRecord, result: SubAgentResult) -> None:
        text = _completion_notice(result)
        message = ChatMessage(role="assistant", content=text)
        self.main_session.append_assistant_message(message)
        record.notified = True
        if self.notify is None:
            return
        try:
            await self.notify(text)
        except Exception:
            return

    def _validation_errors(self, catalog: SubAgentRoleCatalog) -> tuple[str, ...]:
        registered = self.tool_registry.names()
        errors: list[str] = []
        for definition in catalog.definitions.values():
            for tool_name in (*definition.frontmatter.tools_allow, *definition.frontmatter.tools_deny):
                if tool_name not in registered:
                    errors.append(
                        f"角色 `{definition.name}` 引用不存在的工具 `{tool_name}` ({definition.source_path})"
                    )
        return tuple(errors)

    def _background_summary(self, record: BackgroundSubAgentRecord) -> SubAgentBackgroundSummary:
        result = record.result
        return SubAgentBackgroundSummary(
            task_id=record.task_id,
            type=record.invocation.type,
            role=record.invocation.role,
            status=record.status,
            task=record.invocation.task,
            summary=result.summary if result is not None else "",
            stop_reason=result.stop_reason if result is not None else None,
        )


def _completion_notice(result: SubAgentResult) -> str:
    lines = [
        f"子 Agent 任务完成：{result.task_id}",
        f"- 状态：{result.status}",
        f"- 类型：{result.type}",
        f"- 角色：{result.role or '无'}",
        f"- 停止原因：{result.stop_reason or 'completed'}",
        f"- 摘要：{result.summary or '无'}",
    ]
    if result.key_outputs:
        lines.append("- 关键结果：")
        lines.extend(f"  - {item}" for item in result.key_outputs)
    if result.error:
        lines.append(f"- 错误：{result.error}")
    if result.worktree is not None:
        lines.extend(
            (
                f"- Worktree：{result.worktree.disposition}",
                f"  - 目录：{result.worktree.root}",
                f"  - 分支：{result.worktree.branch}",
                f"  - 原因：{result.worktree.reason}",
            )
        )
    if result.usage is not None:
        total = result.usage.total_tokens
        if total is not None:
            lines.append(f"- Token：{total}")
        else:
            lines.append(
                "- Token："
                f"in={result.usage.input_tokens if result.usage.input_tokens is not None else '?'} "
                f"out={result.usage.output_tokens if result.usage.output_tokens is not None else '?'}"
            )
    return "\n".join(lines)
