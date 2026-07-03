from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable
from pathlib import Path
from typing import TYPE_CHECKING
from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from mewcode.agent import AgentLoopRunner, TurnEvent
from mewcode.commands import (
    AgentCommand,
    AgentMode,
    CommandDispatcher,
    CommandMemorySnapshot,
    CommandPermissionSnapshot,
    CommandRegistry,
    CommandSessionSnapshot,
    CommandSkillSnapshot,
    CommandStatusSnapshot,
    CommandSubAgentSnapshot,
    CommandSubAgentTaskSnapshot,
    create_builtin_command_registry,
)
from mewcode.config import AppConfig
from mewcode.context.manager import ContextManager
from mewcode.context.models import ContextLimitError
from mewcode.errors import MewCodeError, ProviderError, redact_secret
from mewcode.hooks.manager import create_hook_manager
from mewcode.hooks.models import HookEvent, HookExecutionResult, HookRuntimeContext
from mewcode.memory.models import RestoreReport
from mewcode.mcp.manager import McpLoadReport, McpManager
from mewcode.permissions.controller import PermissionController
from mewcode.permissions.models import PermissionPrompt, UserPermissionChoice
from mewcode.providers.base import LLMProvider, TokenUsage
from mewcode.providers.base import ChatMessage
from mewcode.session import ChatSession
from mewcode.skills import LOAD_SKILL_TOOL_NAME, LoadSkillTool, SkillConfigurationError, SkillManager, default_skill_roots
from mewcode.skills.execution import ProviderResolver, create_isolated_skill_runner, create_provider_resolver
from mewcode.subagents import (
    DELEGATE_AGENT_TOOL_NAME,
    DelegateAgentTool,
    SubAgentConfigurationError,
    SubAgentManager,
    default_sub_agent_roots,
)
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry, create_default_registry
from mewcode.teams import TeamManager, create_team_tools
from mewcode.teams.runtime import TeamMemberRunnerFactory, TeamRuntimeSupervisor
from mewcode.teams.store import TeamStore
from mewcode.worktrees import CleanupReport
from mewcode.tui.widgets import (
    CommandCompletionMenu,
    Composer,
    MessageList,
    MessageView,
    PermissionPromptView,
    StatusBar,
    ToolStatusView,
)

if TYPE_CHECKING:
    from mewcode.memory.manager import SessionMemoryManager


class MewCodeApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }
    #status {
        dock: top;
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    #help {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    #composer {
        height: 3;
        border: round $secondary;
    }
    """

    BINDINGS = [
        ("ctrl+c", "cancel_or_quit", "取消/退出"),
        ("escape", "quit", "退出"),
        ("tab", "complete_command", "补全命令"),
    ]

    def __init__(
        self,
        session: ChatSession,
        provider: LLMProvider,
        config: AppConfig,
        registry: ToolRegistry | None = None,
        executor: ToolExecutor | None = None,
        permission_controller: PermissionController | None = None,
        mcp_manager: McpManager | None = None,
        context_manager: ContextManager | None = None,
        memory_manager: SessionMemoryManager | None = None,
        restore_report: RestoreReport | None = None,
        command_registry: CommandRegistry | None = None,
        skill_manager: SkillManager | None = None,
        sub_agent_manager: SubAgentManager | None = None,
        provider_resolver: ProviderResolver | None = None,
        hook_manager=None,
        team_manager: TeamManager | None = None,
    ) -> None:
        super().__init__()
        self.session = session
        self.provider = provider
        self.config = config
        self.registry = registry or create_default_registry()
        self.executor = executor or ToolExecutor(self.registry, ToolContext(cwd=Path.cwd()))
        self.permission_controller = permission_controller
        self.mcp_manager = mcp_manager
        self.context_manager = context_manager or ContextManager(
            config.context,
            self.executor.context.cwd,
            config.max_tokens,
        )
        self.memory_manager = memory_manager
        self.restore_report = restore_report
        self.command_registry = command_registry or create_builtin_command_registry()
        self.command_dispatcher = CommandDispatcher(self.command_registry)
        self.skill_manager = skill_manager or SkillManager(default_skill_roots(self.executor.context.cwd), self.registry)
        self.provider_resolver = provider_resolver or create_provider_resolver(config, provider)
        self.hook_manager = hook_manager or create_hook_manager(config.hooks)
        self.sub_agent_manager = sub_agent_manager or SubAgentManager(
            roots=default_sub_agent_roots(self.executor.context.cwd, config.sub_agents.plugin_role_roots),
            tool_registry=self.registry,
            executor=self.executor,
            config=config,
            provider=provider,
            provider_resolver=self.provider_resolver,
            hook_manager=self.hook_manager,
            main_session=session,
            notify=self.show_sub_agent_notification,
            cleanup_reporter=self._report_worktree_cleanup,
        )
        self.team_manager = team_manager or TeamManager(
            self.executor.context.cwd,
            config.teams,
            store=TeamStore(self.executor.context.cwd, config.teams),
        )
        if self.team_manager.runtime is None:
            team_runner_factory = TeamMemberRunnerFactory(
                registry=self.registry,
                executor=self.executor,
                config=config,
                provider=provider,
                provider_resolver=self.provider_resolver,
                hook_manager=self.hook_manager,
            )
            team_runtime = TeamRuntimeSupervisor(
                manager=self.team_manager,
                worktrees=self.sub_agent_manager.worktree_manager,
                runner_factory=team_runner_factory,
                role_provider=lambda name: self.sub_agent_manager.catalog.definitions.get(name),
            )
            self.team_manager.set_runtime(team_runtime)
        self._register_load_skill_tool()
        self._register_delegate_agent_tool()
        self._register_team_tools()
        self._agent_mode: AgentMode = "normal"
        self.last_usage: TokenUsage | None = None
        self._generation_task: asyncio.Task[None] | None = None
        self._runner: AgentLoopRunner | None = None

    def compose(self) -> ComposeResult:
        yield StatusBar(self.config.protocol, self.config.model, self._agent_mode, id="status")
        yield MessageList(id="messages")
        yield CommandCompletionMenu(id="completion")
        yield Static("Enter 发送 | Ctrl+C/Esc 退出", id="help")
        yield Composer(id="composer")

    async def on_mount(self) -> None:
        self.refresh_status()
        await self._show_restore_report()
        if self.mcp_manager is not None:
            try:
                await self.mcp_manager.initialize()
                self.mcp_manager.register_tools(self.registry)
                self._print_mcp_warnings(self.mcp_manager.load_report())
            except Exception as exc:
                await self._show_error(exc)
        await self._refresh_skills()
        if await self._refresh_sub_agents():
            self.sub_agent_manager.start()
        await self._emit_session_hook("session.start")
        self.query_one("#composer", Composer).focus()

    async def on_unmount(self) -> None:
        await self.team_manager.shutdown()
        await self.sub_agent_manager.close()
        await self._emit_session_hook("session.end")
        await self.hook_manager.close()
        if self.mcp_manager is not None:
            await self.mcp_manager.close()

    def set_permission_controller(self, controller: PermissionController) -> None:
        self.permission_controller = controller

    async def request_permission(self, prompt: PermissionPrompt) -> UserPermissionChoice:
        future: asyncio.Future[UserPermissionChoice] = asyncio.get_running_loop().create_future()
        messages = self.query_one("#messages", MessageList)
        await messages.append_message(PermissionPromptView(prompt, future))
        return await future

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if not isinstance(event.input, Composer):
            return
        event.stop()
        text = event.value.strip()
        if not text:
            return
        if self._generation_task is not None:
            if self._is_background_command(text):
                event.input.value = ""
                self.query_one("#completion", CommandCompletionMenu).clear_options()
                asyncio.create_task(self.command_dispatcher.dispatch(text, self))
            return
        event.input.value = ""
        self.query_one("#completion", CommandCompletionMenu).clear_options()
        self._generation_task = asyncio.create_task(self._handle_submitted_input(text))
        self._generation_task.add_done_callback(self._clear_generation_task)

    def action_complete_command(self) -> None:
        composer = self.query_one("#composer", Composer)
        menu = self.query_one("#completion", CommandCompletionMenu)
        completion = self.command_registry.completion(composer.value)
        if completion.replacement is not None:
            composer.value = completion.replacement
            menu.clear_options()
            return
        menu.set_options(completion.options)

    def action_cancel_or_quit(self) -> None:
        if self._generation_task is None:
            self.exit()
            return
        if self._runner is not None:
            self._runner.cancel()
        self._generation_task.cancel()

    def _clear_generation_task(self, task: asyncio.Task[None]) -> None:
        self._generation_task = None
        if not task.cancelled() and task.exception() is not None:
            self.call_later(self._show_unexpected_task_error, task.exception())

    @property
    def mode(self) -> AgentMode:
        return self._agent_mode

    def set_mode(self, mode: AgentMode) -> None:
        self._agent_mode = mode
        self.refresh_status()

    def refresh_status(self) -> None:
        self.query_one("#status", StatusBar).set_mode(self._agent_mode)

    def status_snapshot(self) -> CommandStatusSnapshot:
        mcp_report = self.mcp_manager.load_report() if self.mcp_manager is not None else None
        return CommandStatusSnapshot(
            protocol=self.config.protocol,
            model=self.config.model,
            mode=self._agent_mode,
            agent_running=self._runner is not None,
            last_usage=self.last_usage,
            mcp_report=mcp_report,
        )

    def session_snapshot(self) -> CommandSessionSnapshot:
        restored = self.restore_report.restored if self.restore_report is not None else False
        source_path = ""
        if self.restore_report is not None and self.restore_report.source_path is not None:
            source_path = str(self.restore_report.source_path)
        return CommandSessionSnapshot(
            session_id=self.session.context_state.session_id,
            restored=restored,
            source_path=source_path,
            message_count=len(self.session.messages),
            mode=self._agent_mode,
        )

    def memory_snapshot(self) -> CommandMemorySnapshot:
        context = self.memory_manager.runtime_context() if self.memory_manager is not None else None
        warning_count = 0
        if context is not None:
            warning_count += len(context.instructions.warnings)
        if self.memory_manager is not None:
            warning_count += len(getattr(self.memory_manager, "warnings", ()))
        return CommandMemorySnapshot(
            enabled=self.config.memory.enabled,
            user_index_available=context is not None and context.user_memory_index is not None,
            project_index_available=context is not None and context.project_memory_index is not None,
            auto_notes_enabled=self.config.memory.auto_notes_enabled,
            warning_count=warning_count,
        )

    def permission_snapshot(self) -> CommandPermissionSnapshot:
        controller = self.permission_controller
        if controller is None:
            return CommandPermissionSnapshot(
                mode=self.config.permissions.mode,
                session_rule_count=0,
                local_rule_count=0,
                project_rule_count=0,
                user_rule_count=0,
            )
        return CommandPermissionSnapshot(
            mode=controller.config.mode,
            session_rule_count=len(controller.session_rules.as_rule_set().rules),
            local_rule_count=len(controller.rule_store.local_rules.rules),
            project_rule_count=len(controller.rule_store.project_rules.rules),
            user_rule_count=len(controller.rule_store.user_rules.rules),
        )

    def skill_snapshot(self) -> CommandSkillSnapshot:
        context = self.skill_manager.prompt_context()
        return CommandSkillSnapshot(
            available=tuple(summary.name for summary in context.available),
            active=tuple(activation.name for activation in context.active),
            warning_count=len(context.warnings),
        )

    def sub_agent_snapshot(self) -> CommandSubAgentSnapshot:
        context = self.sub_agent_manager.prompt_context()
        return CommandSubAgentSnapshot(
            enabled=self.config.sub_agents.enabled,
            available=tuple(summary.name for summary in context.available_roles),
            background=tuple(
                CommandSubAgentTaskSnapshot(
                    task_id=task.task_id,
                    type=task.type,
                    role=task.role,
                    status=task.status,
                    task=task.task,
                    summary=task.summary,
                )
                for task in context.background
            ),
            warning_count=len(context.warnings),
            foreground_running=self.sub_agent_manager.foreground_running(),
        )

    async def show_assistant(self, content: str) -> None:
        messages = self.query_one("#messages", MessageList)
        await messages.append_message(MessageView("assistant", content))

    async def show_sub_agent_notification(self, content: str) -> None:
        await self.show_assistant(content)

    async def show_error(self, content: str) -> None:
        status = self.query_one("#status", StatusBar)
        messages = self.query_one("#messages", MessageList)
        status.set_error(content)
        await messages.append_message(MessageView("error", content))

    async def clear_messages(self) -> None:
        await self.query_one("#messages", MessageList).clear_messages()

    async def compact_context(self) -> str:
        try:
            report = await self.context_manager.manual_compact(session=self.session, provider=self.provider)
            return report.message or "已完成上下文压缩检查。"
        except ContextLimitError as exc:
            return str(exc)

    async def authorize_mcp_server(self, server_name: str) -> str:
        if self.mcp_manager is None:
            return "当前未配置 MCP Manager。"
        url_displayed = False

        async def show_authorization_url(url: str, browser_failed: bool) -> None:
            nonlocal url_displayed
            if not url_displayed:
                await self.show_assistant(f"请在浏览器完成 MCP OAuth 授权：\n{url}")
                url_displayed = True
            if browser_failed:
                await self.show_assistant("未能自动打开浏览器，请复制上面的地址手动访问。")

        result = await self.mcp_manager.authorize_server(server_name, show_authorization_url)
        self.refresh_status()
        return result

    async def logout_mcp_server(self, server_name: str) -> str:
        if self.mcp_manager is None:
            return "当前未配置 MCP Manager。"
        result = await self.mcp_manager.logout_server(server_name)
        self.refresh_status()
        return result

    async def send_prompt(self, *, visible_text: str, model_text: str, mode: AgentMode) -> None:
        await self._run_agent_command(AgentCommand(mode=mode, visible_text=visible_text, model_text=model_text))

    async def invoke_skill(self, *, name: str, arguments: str, visible_text: str) -> None:
        activation = self.skill_manager.load(name, arguments)
        command = AgentCommand(mode=self._agent_mode, visible_text=visible_text, model_text=visible_text)
        if activation.mode == "isolated":
            try:
                await self._run_isolated_skill(command, activation.history)
            finally:
                self.skill_manager.deactivate(activation.name)
            return
        await self._run_agent_command(command)

    def clear_active_skills(self) -> None:
        self.skill_manager.clear_active()

    async def background_current_sub_agent(self) -> bool:
        return self.sub_agent_manager.background_current_foreground()

    async def _handle_submitted_input(self, text: str) -> None:
        composer = self.query_one("#composer", Composer)
        try:
            if not await self._refresh_skills():
                return
            if not await self._refresh_sub_agents():
                return
            consumed = await self.command_dispatcher.dispatch(text, self)
            if not consumed:
                await self._run_agent_command(
                    AgentCommand(mode=self._agent_mode, visible_text=text, model_text=text)
                )
        finally:
            composer.disabled = False
            composer.focus()

    def _is_background_command(self, text: str) -> bool:
        command_text = text.strip().partition(" ")[0].casefold()
        return command_text in {"/background", "/bg"}

    async def _run_agent_command(self, command: AgentCommand) -> None:
        status = self.query_one("#status", StatusBar)
        messages = self.query_one("#messages", MessageList)
        status.set_generating(True)
        await messages.append_message(MessageView("user", command.visible_text))
        assistant_view = await messages.append_message(MessageView("assistant", ""))
        tool_views: dict[str, ToolStatusView] = {}
        runner = AgentLoopRunner(
            self.session,
            self.provider,
            self.registry,
            self.executor,
            self.config.agent,
            permission_controller=self.permission_controller,
            context_manager=self.context_manager,
            memory_manager=self.memory_manager,
            skill_manager=self.skill_manager,
            sub_agent_manager=self.sub_agent_manager,
            provider_resolver=self.provider_resolver,
            hook_manager=self.hook_manager,
            tool_gates=self.team_manager.tool_gates(self.executor.context.principal),
            loop_controller=self.team_manager.loop_controller(self.executor.context.principal),
            team_prompt_provider=lambda: self.team_manager.prompt_context(self.executor.context.principal),
        )
        self._runner = runner
        try:
            async for turn_event in runner.run(command):
                await self._apply_turn_event(turn_event, assistant_view, messages, tool_views, status)
            status.set_generating(False)
        except asyncio.CancelledError:
            assistant_view.set_content("已取消当前任务。")
            status.set_generating(False)
        except Exception as exc:
            await self._show_error(exc)
        finally:
            self._runner = None

    async def _run_isolated_skill(self, command: AgentCommand, history_count: int) -> None:
        status = self.query_one("#status", StatusBar)
        messages = self.query_one("#messages", MessageList)
        status.set_generating(True)
        await messages.append_message(MessageView("user", command.visible_text))
        assistant_view = await messages.append_message(MessageView("assistant", ""))
        tool_views: dict[str, ToolStatusView] = {}
        isolated_session = ChatSession(messages=list(self.session.messages[-history_count:]) if history_count else [])
        runner = create_isolated_skill_runner(
            session=isolated_session,
            provider=self.provider,
            registry=self.registry,
            executor=self.executor,
            config=self.config,
            skill_manager=self.skill_manager,
            provider_resolver=self.provider_resolver,
            hook_manager=self.hook_manager,
        )
        self._runner = runner
        final_message: ChatMessage | None = None
        try:
            async for turn_event in runner.run(command):
                if turn_event.type == "message_done":
                    final_message = turn_event.message
                await self._apply_turn_event(turn_event, assistant_view, messages, tool_views, status)
            if final_message is not None:
                summary = ChatMessage(
                    role="assistant",
                    content=f"独立 Skill 执行摘要：\n{final_message.content}",
                    thinking=final_message.thinking,
                )
                self.session.append_user_message(command.visible_text)
                self.session.append_assistant_message(summary)
                assistant_view.set_content(summary.content)
            status.set_generating(False)
        except asyncio.CancelledError:
            assistant_view.set_content("已取消当前任务。")
            status.set_generating(False)
        except Exception as exc:
            await self._show_error(exc)
        finally:
            self._runner = None

    async def _apply_turn_event(
        self,
        turn_event: TurnEvent,
        assistant_view: MessageView,
        messages: MessageList,
        tool_views: dict[str, ToolStatusView],
        status: StatusBar,
    ) -> None:
        if turn_event.type == "progress":
            status.set_progress(turn_event.progress)
            return
        if turn_event.type == "text_delta":
            assistant_view.append_content(turn_event.text)
            return
        if turn_event.type == "thinking_delta":
            assistant_view.append_thinking(turn_event.text)
            return
        if turn_event.type == "usage":
            self.last_usage = turn_event.usage
            status.set_usage(turn_event.usage)
            return
        if turn_event.type == "tool_started" and turn_event.tool_call is not None:
            view = await messages.append_message(
                ToolStatusView(turn_event.tool_call.name, tool_call_id=turn_event.tool_call.id)
            )
            if isinstance(view, ToolStatusView):
                tool_views[turn_event.tool_call.id] = view
            return
        if turn_event.type == "tool_finished" and turn_event.tool_result is not None:
            view = tool_views.get(turn_event.tool_result.tool_call_id)
            if view is not None:
                view.finish(turn_event.tool_result)
            return
        if turn_event.type == "permission_requested":
            status.set_permission_state("等待确认")
            return
        if turn_event.type == "permission_resolved":
            if turn_event.permission is not None and turn_event.permission.decision is not None:
                status.set_permission_state("已允许" if turn_event.permission.decision.kind == "allow" else "已拒绝")
            return
        if turn_event.type == "context_compacted":
            if turn_event.text:
                await messages.append_message(MessageView("assistant", turn_event.text))
            return
        if turn_event.type == "hook_finished" and turn_event.hook_result is not None:
            await self._show_hook_result(turn_event.hook_result)
            return
        if turn_event.type == "stopped":
            if turn_event.text:
                assistant_view.set_content(turn_event.text)
            if turn_event.progress is not None:
                status.set_progress(turn_event.progress)
            return
        if turn_event.type == "error":
            raise ProviderError(turn_event.error or "Provider 返回未知错误")
        if turn_event.type == "message_done" and turn_event.message is not None:
            assistant_view.set_content(turn_event.message.content)
            assistant_view.set_thinking(turn_event.message.thinking or "")
            return
        return

    async def _show_error(self, exc: BaseException) -> None:
        raw = str(exc) if isinstance(exc, MewCodeError) else f"未预期错误: {exc}"
        error_text = redact_secret(raw, self.config.api_key)
        await self.show_error(error_text)

    async def _emit_session_hook(self, event_name: str) -> None:
        if self.hook_manager is None:
            return
        event = HookEvent(
            event_name,  # type: ignore[arg-type]
            {
                "session": {"id": self.session.context_state.session_id},
                "cwd": str(self.executor.context.cwd),
            },
        )
        for result in await self.hook_manager.emit(event, self._hook_runtime_context()):
            await self._show_hook_result(result)

    def _hook_runtime_context(self) -> HookRuntimeContext:
        return HookRuntimeContext(
            cwd=self.executor.context.cwd,
            mode=self._agent_mode,
            allowed_tool_names=self.skill_manager.active_tool_whitelist(),
            registry=self.registry,
            executor=self.executor,
            permission_controller=self.permission_controller,
        )

    async def _show_hook_result(self, result: HookExecutionResult) -> None:
        if result.status not in {"failed", "blocked", "placeholder"}:
            return
        message = f"Hook {result.rule_id}: {result.message or result.status}"
        await self.query_one("#messages", MessageList).append_message(MessageView("assistant", message))

    def _show_unexpected_task_error(self, exc: BaseException) -> None:
        maybe_awaitable = self._show_error(exc)
        if isinstance(maybe_awaitable, Awaitable):
            asyncio.create_task(maybe_awaitable)

    def _print_mcp_warnings(self, report: McpLoadReport) -> None:
        for server_name, error in report.failed_servers.items():
            print(f"MewCode MCP 警告: Server {server_name} 加载失败: {redact_secret(error)}", file=sys.stderr)
        for tool_name, error in report.failed_tools.items():
            print(f"MewCode MCP 警告: 工具 {tool_name} 注册失败: {redact_secret(error)}", file=sys.stderr)
        for warning in report.warnings:
            print(f"MewCode MCP 警告: {redact_secret(warning)}", file=sys.stderr)

    def _report_worktree_cleanup(self, report: CleanupReport) -> None:
        for item in report.failures:
            print(
                f"MewCode Worktree 清理警告: {item.path}: {redact_secret(item.reason)}",
                file=sys.stderr,
            )

    def _register_load_skill_tool(self) -> None:
        if self.registry.get(LOAD_SKILL_TOOL_NAME) is None:
            self.registry.register(LoadSkillTool(self.skill_manager))

    def _register_delegate_agent_tool(self) -> None:
        if self.registry.get(DELEGATE_AGENT_TOOL_NAME) is None:
            self.registry.register(DelegateAgentTool(self.sub_agent_manager))

    def _register_team_tools(self) -> None:
        for tool in create_team_tools(self.team_manager):
            if self.registry.get(tool.spec.name) is None:
                self.registry.register(tool)

    async def _refresh_skills(self) -> bool:
        try:
            report = self.skill_manager.refresh_if_changed(self.command_registry)
        except SkillConfigurationError as exc:
            await self._show_error(exc)
            return False
        for warning in report.warnings:
            print(f"MewCode Skill 警告: {warning.source_path}: {warning.message}", file=sys.stderr)
        return True

    async def _refresh_sub_agents(self) -> bool:
        try:
            report = self.sub_agent_manager.refresh_if_changed()
        except SubAgentConfigurationError as exc:
            await self._show_error(exc)
            return False
        for warning in report.warnings:
            print(f"MewCode 子 Agent 警告: {warning.source_path}: {warning.message}", file=sys.stderr)
        return True

    async def _show_restore_report(self) -> None:
        lines = self._restore_report_lines()
        if not lines:
            return
        messages = self.query_one("#messages", MessageList)
        await messages.append_message(MessageView("assistant", "\n".join(lines)))

    def _restore_report_lines(self) -> list[str]:
        report = self.restore_report
        if report is None:
            return []

        lines: list[str] = []
        if report.restored:
            lines.append(f"已恢复会话 {report.session_id}。")
            if report.source_path is not None:
                lines.append(f"来源：{report.source_path}")
        else:
            lines.append(report.started_empty_reason or f"已启动空会话 {report.session_id}。")

        if report.skipped_bad_lines:
            lines.append(f"恢复时跳过 {report.skipped_bad_lines} 条坏记录。")
        if report.truncated_messages:
            lines.append(f"恢复时截断 {report.truncated_messages} 条协议不完整消息。")
        if report.compacted:
            lines.append("恢复历史已完成一次上下文压缩。")
        if report.time_gap_notice:
            lines.append(report.time_gap_notice)

        warnings = list(report.warnings)
        if self.memory_manager is not None:
            warnings.extend(self.memory_manager.runtime_context().instructions.warnings)
        seen: set[str] = set()
        for warning in warnings:
            if warning and warning not in seen:
                lines.append(warning)
                seen.add(warning)
        return lines
