from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from textual.widgets import Button, Static

from mewcode.agent import AgentProgress, TurnEvent
from mewcode.config import AppConfig, RepoMapConfig
from mewcode.context.manager import ContextManager
from mewcode.context.models import ContextConfig
from mewcode.context.models import ContextCompactionReport, PreparedChatRequest, RequestFootprint
from mewcode.errors import ProviderError
from mewcode.hooks import parse_hook_config
from mewcode.hooks.manager import create_hook_manager
from mewcode.memory.models import InstructionBundle, KnowledgeContext, RestoreReport
from mewcode.mcp.manager import McpLoadReport
from mewcode.permissions import PermissionConfig
from mewcode.permissions.controller import create_permission_controller
from mewcode.permissions.models import PermissionPrompt
from mewcode.providers.base import ChatMessage, ChatRequest, PromptCacheUsage, StreamEvent, TokenUsage
from mewcode.repo_map.models import RepoMapStatus
from mewcode.session import ChatSession
from mewcode.skills import SkillManager, SkillRoots
from mewcode.tools.base import ToolCall, ToolContext, ToolResult
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry, create_default_registry
from mewcode.worktrees import CleanupItemResult, CleanupReport
from mewcode.tui.app import MewCodeApp
from mewcode.tui.widgets import (
    CommandCompletionMenu,
    Composer,
    MessageList,
    MessageView,
    PermissionPromptView,
    StatusBar,
    ThinkingPanel,
    ToolStatusView,
)


class FakeProvider:
    def __init__(self, responses: list[list[StreamEvent | BaseException]]) -> None:
        self.responses = responses
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        index = len(self.requests) - 1
        response = self.responses[index] if index < len(self.responses) else self.responses[-1]
        for item in response:
            await asyncio.sleep(0)
            if isinstance(item, BaseException):
                raise item
            yield item


class SlowProvider:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            await asyncio.sleep(10)
        yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="after cancel ok"))


class ManualBackgroundProvider:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []
        self.child_started = asyncio.Event()
        self.release_child = asyncio.Event()

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        index = len(self.requests)
        self.requests.append(request)
        if index == 0:
            yield StreamEvent(
                type="message_done",
                message=ChatMessage(
                    role="assistant",
                    tool_calls=(
                        ToolCall(
                            "call-delegate",
                            "delegate_agent",
                            {
                                "type": "defined",
                                "role": "reviewer",
                                "task": "慢速审查",
                                "foreground_timeout_seconds": 10,
                            },
                        ),
                    ),
                ),
            )
            return
        if index == 1:
            self.child_started.set()
            await self.release_child.wait()
            yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="后台审查完成"))
            return
        yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="主任务已恢复"))


class FakeContextManager:
    def __init__(self, report: ContextCompactionReport | None = None) -> None:
        self.report = report
        self.manual_called = False

    async def manual_compact(self, *, session: ChatSession, provider) -> ContextCompactionReport:
        _ = session, provider
        self.manual_called = True
        return self.report or ContextCompactionReport(
            mode="manual",
            light_compacted=False,
            heavy_compacted=False,
            kept_message_count=1,
            message="当前历史较短，无需生成摘要。",
        )

    async def prepare_request(self, *, session: ChatSession, provider, tools, prompt_factory, mode="auto") -> PreparedChatRequest:
        _ = provider, mode
        prompt = prompt_factory()
        return PreparedChatRequest(
            request=session.build_request(tools=tools, prompt=prompt),
            footprint=RequestFootprint(chars=10, estimated_tokens=3),
            report=self.report,
        )

    def record_usage(self, usage, footprint: RequestFootprint) -> None:
        _ = usage, footprint


class FakeMemoryManager:
    def __init__(self, context: KnowledgeContext | None = None) -> None:
        self.context = context or KnowledgeContext()

    def runtime_context(self) -> KnowledgeContext:
        return self.context

    def schedule_update(self, *, job, provider) -> None:
        _ = job, provider


class FakeRepoMapManager:
    def __init__(self) -> None:
        self.config = RepoMapConfig(enabled=True, max_tokens=321)
        self.started = 0
        self.closed = 0

    async def start(self) -> None:
        self.started += 1

    async def close(self) -> None:
        self.closed += 1

    def status(self) -> RepoMapStatus:
        return RepoMapStatus(
            enabled=True,
            state="ready",
            root="/repo",
            revision="abcdef0123456789",
            configured_budget=321,
            candidate_files=2,
        )


def make_config() -> AppConfig:
    return AppConfig(
        protocol="openai",
        model="test-model",
        base_url="https://example.test/v1",
        api_key="sk-tui-secret-1234567890",
        repo_map=RepoMapConfig(enabled=False),
    )


@pytest.mark.asyncio
async def test_tui_manages_repo_map_lifecycle_and_reports_status() -> None:
    manager = FakeRepoMapManager()
    app = MewCodeApp(
        ChatSession(),
        FakeProvider([]),
        make_config(),
        repo_map_manager=manager,  # type: ignore[arg-type]
    )

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "/status")
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))
        assert manager.started == 1
        assert "Repo Map：ready" in rendered
        assert "根目录 /repo" in rendered
        assert "revision abcdef012345" in rendered

    assert manager.closed == 1


async def submit_and_wait(app: MewCodeApp, pilot, text: str) -> None:
    composer = app.query_one(Composer)
    composer.value = text
    await pilot.press("enter")
    for _ in range(100):
        if app._generation_task is None and not composer.disabled:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("generation task did not finish")


async def wait_for_generation_task(app: MewCodeApp, expected_done: bool) -> None:
    for _ in range(100):
        done = app._generation_task is None
        if done is expected_done:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("generation task state did not change")


async def wait_for_permission_prompt(app: MewCodeApp) -> PermissionPromptView:
    for _ in range(100):
        views = [view for view in app.query(PermissionPromptView) if view.display]
        if views:
            return views[-1]
        await asyncio.sleep(0.01)
    raise AssertionError("permission prompt did not appear")


async def wait_for_text(app: MewCodeApp, text: str) -> None:
    for _ in range(100):
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))
        if text in rendered:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"text not found: {text}")


def make_permission_prompt() -> PermissionPrompt:
    return PermissionPrompt(
        call=ToolCall("call-1", "write_file", {"path": "demo.txt", "content": "ok"}),
        tool_name="write_file",
        title="允许工具调用: write_file",
        summary="demo.txt",
        reason="有副作用工具需要用户确认",
        suggested_rule_key="write_file(demo.txt)",
    )


def make_permission_app(tmp_path: Path, provider: FakeProvider, mode: str = "default") -> MewCodeApp:
    registry = create_default_registry()
    executor = ToolExecutor(registry, ToolContext(cwd=tmp_path))
    app = MewCodeApp(ChatSession(), provider, make_config(), registry, executor)
    controller = create_permission_controller(tmp_path, PermissionConfig(mode=mode), app)  # type: ignore[arg-type]
    app.set_permission_controller(controller)
    return app


def make_hook_manager(raw: list[dict[str, object]]):
    return create_hook_manager(parse_hook_config(raw))


@pytest.mark.asyncio
async def test_widgets_can_render_and_toggle_thinking() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        messages = app.query_one(MessageList)
        view = await messages.append_message(MessageView("assistant", "answer", "thought"))
        await pilot.pause()
        panel = view.thinking_panel
        assert isinstance(panel, ThinkingPanel)
        assert panel.collapsed is True
        panel.toggle()
        assert panel.collapsed is False


@pytest.mark.asyncio
async def test_tool_status_view_renders_running_and_finished_states() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        messages = app.query_one(MessageList)
        view = await messages.append_message(ToolStatusView("read_file"))
        await pilot.pause()
        assert isinstance(view, ToolStatusView)
        assert "运行中" in str(view.body.content)
        view.finish(ToolResult("call-1", "read_file", True, {"content": "ok"}))
        assert "完成" in str(view.body.content)
        view.finish(ToolResult("call-1", "read_file", False, {}, error_type="not_found", error="missing"))
        assert "not_found" in str(view.body.content)


@pytest.mark.asyncio
async def test_permission_prompt_view_renders_choices() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        messages = app.query_one(MessageList)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        view = await messages.append_message(PermissionPromptView(make_permission_prompt(), future))
        await pilot.pause()

        assert isinstance(view, PermissionPromptView)
        assert "write_file" in str(view.title.content)
        assert "demo.txt" in str(view.body.content)
        labels = {str(button.label) for button in view.query(Button)}
        assert labels == {"本次允许", "本会话允许", "永久允许", "拒绝"}


@pytest.mark.asyncio
async def test_permission_prompt_view_sets_choice() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        messages = app.query_one(MessageList)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        view = await messages.append_message(PermissionPromptView(make_permission_prompt(), future))
        await pilot.pause()

        view.choose("allow_once")

    assert future.result() == "allow_once"


@pytest.mark.asyncio
async def test_status_bar_renders_agent_progress_and_usage() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test():
        status = app.query_one(StatusBar)
        assert "[DEFAULT]" in str(status.content)
        status.set_mode("plan")
        assert "[PLAN]" in str(status.content)
        status.set_mode("normal")
        status.set_generating(True)
        status.set_progress(AgentProgress(iteration=2, max_iterations=8, mode="normal", phase="tools"))
        assert "normal 2/8 tools" in str(status.content)
        assert "Token: 未知" in str(status.content)
        status.set_usage(TokenUsage(input_tokens=2, output_tokens=3, total_tokens=5, provider="openai"))
        assert "Token: 5" in str(status.content)


@pytest.mark.asyncio
async def test_status_bar_renders_cache_usage() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test():
        status = app.query_one(StatusBar)
        status.set_generating(True)
        status.set_usage(
            TokenUsage(
                input_tokens=2,
                output_tokens=3,
                total_tokens=5,
                provider="openai",
                cache=PromptCacheUsage(status="hit", cached_tokens=2),
            )
        )
        assert "Cache: hit 2" in str(status.content)

        status.set_usage(
            TokenUsage(
                input_tokens=2,
                output_tokens=3,
                total_tokens=5,
                provider="anthropic",
                cache=PromptCacheUsage(status="write", creation_input_tokens=7),
            )
        )
        assert "Cache: write 7" in str(status.content)

        status.set_usage(
            TokenUsage(
                input_tokens=2,
                output_tokens=3,
                total_tokens=5,
                provider="openai",
                cache=PromptCacheUsage(status="unknown"),
            )
        )
        assert "Cache: unknown" in str(status.content)

        status.set_usage(
            TokenUsage(
                input_tokens=2,
                output_tokens=3,
                total_tokens=5,
                provider="openai",
                cache=PromptCacheUsage(status="unsupported", supported=False),
            )
        )
        assert "Cache: unsupported" in str(status.content)


@pytest.mark.asyncio
async def test_tool_status_view_tracks_tool_call_id() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        messages = app.query_one(MessageList)
        view = await messages.append_message(ToolStatusView("read_file", tool_call_id="call-1"))
        await pilot.pause()
        assert isinstance(view, ToolStatusView)
        assert view.tool_call_id == "call-1"
        assert "call-1" in str(view.title.content)


@pytest.mark.asyncio
async def test_app_starts_with_fullscreen_regions() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test():
        assert app.query_one("#status", StatusBar)
        assert app.query_one("#messages", MessageList)
        assert app.query_one("#composer", Composer)
        assert "Ctrl+C" in str(app.query_one("#help", Static).content)


@pytest.mark.asyncio
async def test_tui_displays_restore_report(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    report = RestoreReport(
        restored=True,
        session_id="20260612-080910-abcd",  # type: ignore[arg-type]
        source_path=tmp_path / ".mewcode" / "sessions" / "20260612-080910-abcd.jsonl",
        skipped_bad_lines=2,
        truncated_messages=1,
        compacted=True,
        time_gap_notice="本会话距离上次活动约 36 小时。",
        warnings=("恢复时跳过坏记录。",),
    )
    memory_manager = FakeMemoryManager(KnowledgeContext(instructions=InstructionBundle(warnings=("指令 include 已被拦截。",))))
    app = MewCodeApp(
        ChatSession(),
        provider,
        make_config(),
        memory_manager=memory_manager,  # type: ignore[arg-type]
        restore_report=report,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    assert "已恢复会话 20260612-080910-abcd" in rendered
    assert "恢复时跳过 2 条坏记录" in rendered
    assert "恢复时截断 1 条协议不完整消息" in rendered
    assert "恢复历史已完成一次上下文压缩" in rendered
    assert "本会话距离上次活动约 36 小时" in rendered
    assert "指令 include 已被拦截" in rendered


@pytest.mark.asyncio
async def test_tui_passes_memory_manager_to_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    from mewcode.tui import app as app_module

    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    memory_manager = FakeMemoryManager()
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, *args, **kwargs) -> None:
            _ = args
            captured["memory_manager"] = kwargs["memory_manager"]

        def cancel(self) -> None:
            captured["cancelled"] = True

        async def run(self, command):
            _ = command
            yield TurnEvent(
                type="message_done",
                message=ChatMessage(role="assistant", content="完成"),
                stop_reason="completed",
            )

    monkeypatch.setattr(app_module, "AgentLoopRunner", FakeRunner)
    app = MewCodeApp(ChatSession(), provider, make_config(), memory_manager=memory_manager)  # type: ignore[arg-type]

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "hello")

    assert captured["memory_manager"] is memory_manager


@pytest.mark.asyncio
async def test_tui_lifecycle_initializes_and_closes_mcp_manager(tmp_path: Path) -> None:
    events: list[str] = []

    class FakeManager:
        async def initialize(self) -> None:
            events.append("initialize")

        def register_tools(self, registry: ToolRegistry) -> None:
            _ = registry
            events.append("register")

        def load_report(self) -> McpLoadReport:
            return McpLoadReport()

        async def close(self) -> None:
            events.append("close")

    registry = ToolRegistry()
    app = MewCodeApp(
        ChatSession(),
        object(),
        make_config(),
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        mcp_manager=FakeManager(),  # type: ignore[arg-type]
    )

    async with app.run_test():
        pass

    assert events == ["initialize", "register", "close"]


@pytest.mark.asyncio
async def test_tui_mcp_oauth_url_is_local_only_and_logout_updates_tools(tmp_path: Path) -> None:
    events: list[str] = []

    class FakeManager:
        async def initialize(self) -> None:
            events.append("initialize")

        def register_tools(self, registry: ToolRegistry) -> None:
            _ = registry

        def load_report(self) -> McpLoadReport:
            return McpLoadReport()

        async def authorize_server(self, server_name: str, callback) -> str:
            events.append(f"auth:{server_name}")
            await callback("https://auth.test/authorize?state=public-state", False)
            await callback("https://auth.test/authorize?state=public-state", True)
            return f"MCP Server {server_name} OAuth 授权成功，工具已加载"

        async def logout_server(self, server_name: str) -> str:
            events.append(f"logout:{server_name}")
            return f"MCP Server {server_name} 已退出 OAuth，相关工具已移除"

        async def close(self) -> None:
            events.append("close")

    session = ChatSession()
    registry = ToolRegistry()
    app = MewCodeApp(
        session,
        object(),
        make_config(),
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        mcp_manager=FakeManager(),  # type: ignore[arg-type]
    )

    async with app.run_test() as pilot:
        await app.command_dispatcher.dispatch("/mcp auth github", app)
        await app.command_dispatcher.dispatch("/mcp logout github", app)
        await pilot.pause()
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    assert events == ["initialize", "auth:github", "logout:github", "close"]
    assert "https://auth.test/authorize?state=public-state" in rendered
    assert "未能自动打开浏览器" in rendered
    assert "工具已移除" in rendered
    assert session.messages == []


@pytest.mark.asyncio
async def test_tui_emits_session_hook_events() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    hook_manager = make_hook_manager(
        [
            {"name": "start", "event": "session.start", "action": {"type": "prompt", "text": "session-start"}},
            {"name": "end", "event": "session.end", "action": {"type": "prompt", "text": "session-end"}},
        ]
    )
    app = MewCodeApp(ChatSession(), provider, make_config(), hook_manager=hook_manager)

    async with app.run_test():
        await asyncio.sleep(0)

    injections = hook_manager.pending_prompt_injections()
    assert [item.text for item in injections] == ["session-start", "session-end"]


@pytest.mark.asyncio
async def test_tui_displays_high_signal_hook_events() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    hook_manager = make_hook_manager(
        [{"name": "sub", "event": "session.start", "action": {"type": "sub_agent", "name": "worker"}}]
    )
    app = MewCodeApp(ChatSession(), provider, make_config(), hook_manager=hook_manager)

    async with app.run_test() as pilot:
        await pilot.pause()
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    assert "Hook sub" in rendered
    assert "worker" in rendered


@pytest.mark.asyncio
async def test_hook_failure_does_not_break_input() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    hook_manager = make_hook_manager(
        [
            {
                "name": "bad-command",
                "event": "turn.start",
                "action": {"type": "command", "command": "python -c 'import sys; sys.exit(7)'"},
            }
        ]
    )
    app = MewCodeApp(ChatSession(), provider, make_config(), hook_manager=hook_manager)

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "hello")
        assert app.query_one(Composer).disabled is False
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    assert "Hook bad-command" in rendered
    assert app.session.messages[-1].content == "完成"


@pytest.mark.asyncio
async def test_escape_still_quits() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_submit_streams_text_into_message_view() -> None:
    provider = FakeProvider(
        [
            [
                StreamEvent(type="text_delta", text="你"),
                StreamEvent(type="text_delta", text="好"),
                StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="你好")),
            ]
        ]
    )
    session = ChatSession()
    app = MewCodeApp(session, provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "hello")

    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content == "你好"


@pytest.mark.asyncio
async def test_compact_command_shows_report_without_agent_task() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="unused"))]])
    context_manager = FakeContextManager(
        ContextCompactionReport(
            mode="manual",
            light_compacted=True,
            heavy_compacted=False,
            externalized_paths=(".mewcode/context/a.json",),
            kept_message_count=2,
            message="已完成手动压缩。",
        )
    )
    app = MewCodeApp(ChatSession(), provider, make_config(), context_manager=context_manager)  # type: ignore[arg-type]

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "/compact")
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    assert context_manager.manual_called is True
    assert provider.requests == []
    assert app.session.messages == []
    assert "已完成手动压缩。" in rendered


@pytest.mark.asyncio
async def test_tui_displays_context_compacted_event() -> None:
    report = ContextCompactionReport(
        mode="auto",
        light_compacted=True,
        heavy_compacted=False,
        message="已自动压缩上下文。",
    )
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    app = MewCodeApp(ChatSession(), provider, make_config(), context_manager=FakeContextManager(report))  # type: ignore[arg-type]

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "hello")
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    assert "已自动压缩上下文。" in rendered


@pytest.mark.asyncio
async def test_submit_streams_thinking_into_collapsible_panel() -> None:
    provider = FakeProvider(
        [
            [
                StreamEvent(type="thinking_delta", text="先想"),
                StreamEvent(type="text_delta", text="答案"),
                StreamEvent(
                    type="message_done",
                    message=ChatMessage(role="assistant", content="答案", thinking="先想"),
                ),
            ]
        ]
    )
    session = ChatSession()
    app = MewCodeApp(session, provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "question")

    assert session.messages[-1].content == "答案"
    assert session.messages[-1].thinking == "先想"


@pytest.mark.asyncio
async def test_second_turn_receives_previous_context() -> None:
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="已记住"))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="Mew-17"))],
        ]
    )
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "remember Mew-17")
        await submit_and_wait(app, pilot, "what is my code?")

    second_request = provider.requests[1]
    assert [message.role for message in second_request.messages] == ["user", "assistant", "user"]
    assert second_request.messages[0].content == "remember Mew-17"


@pytest.mark.asyncio
async def test_provider_error_is_displayed_and_input_recovers() -> None:
    provider = FakeProvider([[ProviderError("认证失败")]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "hello")
        assert app.query_one(Composer).disabled is False
        assert "认证失败" in str(app.query_one(StatusBar).content)


@pytest.mark.asyncio
async def test_worktree_janitor_starts_and_closes_with_tui() -> None:
    app = MewCodeApp(ChatSession(), FakeProvider([]), make_config())

    async with app.run_test():
        task = app.sub_agent_manager.worktree_janitor._task
        assert task is not None
        assert not task.done()

    assert app.sub_agent_manager.worktree_janitor._task is None


def test_worktree_janitor_failure_report_is_non_fatal(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    app = MewCodeApp(ChatSession(), FakeProvider([]), make_config())
    report = CleanupReport(
        items=(CleanupItemResult(tmp_path / "candidate", "failed", "secret=should-not-crash"),)
    )

    app._report_worktree_cleanup(report)

    captured = capsys.readouterr()
    assert "MewCode Worktree 清理警告" in captured.err
    assert "candidate" in captured.err


@pytest.mark.asyncio
async def test_submit_shows_multiple_tool_statuses_and_final_answer() -> None:
    first_call = ToolCall(
        id="call-1",
        name="find_files",
        arguments={"pattern": "README.md"},
        raw_arguments='{"pattern":"README.md"}',
    )
    second_call = ToolCall(
        id="call-2",
        name="read_file",
        arguments={"path": "README.md"},
        raw_arguments='{"path":"README.md"}',
    )
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    type="message_done",
                    message=ChatMessage(role="assistant", tool_calls=(first_call, second_call)),
                )
            ],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="找到 README.md"))],
        ]
    )
    session = ChatSession()
    app = MewCodeApp(session, provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "find readme")
        tool_views = list(app.query(ToolStatusView))
        assert len(tool_views) == 2
        assert all("完成" in str(view.body.content) for view in tool_views)
        assert app.query_one(Composer).disabled is False

    assert session.messages[-1].content == "找到 README.md"


@pytest.mark.asyncio
async def test_delegate_agent_defined_runs_in_isolated_child_context() -> None:
    tool_call = ToolCall(
        "call-delegate",
        "delegate_agent",
        {"type": "defined", "role": "reviewer", "task": "审查 README"},
    )
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(tool_call,)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="子审查完成"))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="主回复完成"))],
        ]
    )
    session = ChatSession()
    app = MewCodeApp(session, provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "委派 reviewer 审查 README")
        tool_names = [view.tool_name for view in app.query(ToolStatusView)]

    assert len(provider.requests) == 3
    assert provider.requests[1].messages[-1].content == "审查 README"
    assert provider.requests[1].prompt is not None
    assert provider.requests[1].prompt.runtime_blocks[0].cacheable is True
    assert '<active_sub_agent' in provider.requests[1].prompt.runtime_blocks[-1].text
    assert "优先指出 bug、回归风险" in provider.requests[1].prompt.runtime_blocks[-1].text
    assert any(message.role == "tool" and "子审查完成" in message.content for message in session.messages)
    assert not any(message.role == "assistant" and message.content == "子审查完成" for message in session.messages)
    assert session.messages[-1].content == "主回复完成"
    assert "delegate_agent" in tool_names


@pytest.mark.asyncio
async def test_background_command_switches_foreground_sub_agent_to_background() -> None:
    provider = ManualBackgroundProvider()
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        composer = app.query_one(Composer)
        composer.value = "启动慢速子 Agent"
        await pilot.press("enter")
        for _ in range(100):
            if provider.child_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert provider.child_started.is_set()

        composer.value = "/background"
        await pilot.press("enter")
        await wait_for_text(app, "已切到后台")
        await wait_for_generation_task(app, expected_done=True)
        provider.release_child.set()
        await wait_for_text(app, "子 Agent 任务完成")
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    assert "后台审查完成" in rendered
    assert app.session.messages[-1].role == "assistant"
    assert "子 Agent 任务完成" in app.session.messages[-1].content


@pytest.mark.asyncio
async def test_do_switches_to_default_without_provider_call() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "/plan")
        assert "[PLAN]" in str(app.query_one(StatusBar).content)
        await submit_and_wait(app, pilot, "/do")
        assert app.query_one(Composer).disabled is False
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))
        status_content = str(app.query_one(StatusBar).content)

    assert provider.requests == []
    assert app.session.messages == []
    assert "已回到默认模式" in rendered
    assert "[DEFAULT]" in status_content


@pytest.mark.asyncio
async def test_unknown_and_help_commands_do_not_call_provider() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "/wat")
        await submit_and_wait(app, pilot, "/HELP")
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    assert "未知命令 `/wat`" in rendered
    assert "/help" in rendered
    assert "/review" in rendered
    assert provider.requests == []


@pytest.mark.asyncio
async def test_empty_input_does_not_add_messages_or_call_provider() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "   ")
        message_views = list(app.query(MessageView))

    assert provider.requests == []
    assert message_views == []


@pytest.mark.asyncio
async def test_plan_mode_routes_plain_input_to_plan_agent_mode() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="计划"))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "/plan")
        await submit_and_wait(app, pilot, "规划 README")

    assert len(provider.requests) == 1
    assert provider.requests[0].prompt is not None
    assert "模式状态：plan full 1/40" in provider.requests[0].prompt.runtime_blocks[-1].text
    assert [message.content for message in provider.requests[0].messages if message.role == "user"] == ["规划 README"]


@pytest.mark.asyncio
async def test_default_mode_routes_plain_input_to_normal_agent_mode() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "/plan")
        await submit_and_wait(app, pilot, "/do")
        await submit_and_wait(app, pilot, "执行任务")

    assert len(provider.requests) == 1
    assert provider.requests[0].prompt is not None
    assert "模式状态：normal full 1/40" in provider.requests[0].prompt.runtime_blocks[-1].text


@pytest.mark.asyncio
async def test_status_session_memory_permission_commands_render_local_snapshots() -> None:
    provider = FakeProvider([[StreamEvent(type="usage", usage=TokenUsage(total_tokens=9)), StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "hello")
        await submit_and_wait(app, pilot, "/status")
        await submit_and_wait(app, pilot, "/session")
        await submit_and_wait(app, pilot, "/memory")
        await submit_and_wait(app, pilot, "/permission")
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    assert "Token：9" in rendered
    assert "会话标识" in rendered
    assert "长期记忆状态" in rendered
    assert "权限状态" in rendered
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_review_command_sends_visible_command_and_model_prompt() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="审查完成"))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "/review README.md")
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    assert len(provider.requests) == 1
    assert provider.requests[0].messages[0].content == "/review README.md"
    assert provider.requests[0].prompt is not None
    runtime_text = provider.requests[0].prompt.runtime_blocks[-1].text
    assert "你正在执行内置 review Skill" in runtime_text
    assert "README.md" in runtime_text
    assert "/review README.md" in rendered


@pytest.mark.asyncio
async def test_isolated_skill_uses_isolated_runtime_and_deactivates(tmp_path: Path) -> None:
    registry = create_default_registry()
    skill_root = tmp_path / "project-skills"
    skill_root.mkdir()
    (skill_root / "solo.md").write_text(
        "\n".join(
            (
                "---",
                "name: solo",
                "description: 独立执行",
                "tools:",
                "  - read_file",
                "mode: isolated",
                "history: 2",
                "---",
                "你正在执行 isolated Skill：{{input}}",
            )
        ),
        encoding="utf-8",
    )
    skill_manager = SkillManager(
        SkillRoots(
            project=skill_root,
            user=tmp_path / "user-skills",
            builtin=tmp_path / "builtin-skills",
        ),
        registry,
    )
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="隔离完成"))]])
    session = ChatSession()
    session.append_user_message("历史请求")
    session.append_assistant_message(ChatMessage(role="assistant", content="历史答复"))
    app = MewCodeApp(
        session,
        provider,
        make_config(),
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        skill_manager=skill_manager,
    )

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "/solo 目标")
        rendered = "\n".join(str(view.body.content) for view in app.query(MessageView))

    assert [message.content for message in provider.requests[0].messages] == ["历史请求", "历史答复", "/solo 目标"]
    assert provider.requests[0].prompt is not None
    assert "你正在执行 isolated Skill：目标" in provider.requests[0].prompt.runtime_blocks[-1].text
    assert "独立 Skill 执行摘要" in rendered
    assert "solo" not in app.skill_manager.active
    assert [message.content for message in session.messages] == [
        "历史请求",
        "历史答复",
        "/solo 目标",
        "独立 Skill 执行摘要：\n隔离完成",
    ]


@pytest.mark.asyncio
async def test_command_completion_single_match_updates_composer() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        composer = app.query_one(Composer)
        composer.value = "/sta"
        await pilot.press("tab")
        await pilot.pause()
        value = app.query_one(Composer).value
        menu_display = app.query_one(CommandCompletionMenu).display

    assert value == "/status"
    assert menu_display is False


@pytest.mark.asyncio
async def test_command_completion_multi_match_shows_menu() -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=""))]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        composer = app.query_one(Composer)
        composer.value = "/s"
        await pilot.press("tab")
        await pilot.pause()
        menu = app.query_one(CommandCompletionMenu)
        menu_display = menu.display
        menu_content = str(menu.body.content)

    assert menu_display is True
    assert "/session" in menu_content
    assert "/status" in menu_content


@pytest.mark.asyncio
async def test_tool_failure_recovers_input() -> None:
    tool_call = ToolCall(id="call-1", name="missing_tool", arguments={})
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(tool_call,)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="工具失败"))],
        ]
    )
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "bad tool")
        tool_views = list(app.query(ToolStatusView))
        assert tool_views
        assert "unknown_tool" in str(tool_views[0].body.content)
        assert app.query_one(Composer).disabled is False


@pytest.mark.asyncio
async def test_tui_permission_allow_once_continues_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    tool_call = ToolCall(
        id="call-1",
        name="write_file",
        arguments={"path": "allowed.txt", "content": "ok"},
    )
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(tool_call,)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="写入完成"))],
        ]
    )
    app = make_permission_app(tmp_path, provider)

    async with app.run_test() as pilot:
        composer = app.query_one(Composer)
        composer.value = "write"
        await pilot.press("enter")
        prompt = await wait_for_permission_prompt(app)
        prompt.choose("allow_once")
        await wait_for_generation_task(app, expected_done=True)

    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "ok"
    assert app.session.messages[-1].content == "写入完成"


@pytest.mark.asyncio
async def test_tui_permission_deny_returns_failure_and_recovers_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    tool_call = ToolCall(
        id="call-1",
        name="write_file",
        arguments={"path": "denied.txt", "content": "no"},
    )
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(tool_call,)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="已拒绝"))],
        ]
    )
    app = make_permission_app(tmp_path, provider)

    async with app.run_test() as pilot:
        composer = app.query_one(Composer)
        composer.value = "write"
        await pilot.press("enter")
        prompt = await wait_for_permission_prompt(app)
        prompt.choose("deny")
        await wait_for_generation_task(app, expected_done=True)
        assert app.query_one(Composer).disabled is False
        tool_views = list(app.query(ToolStatusView))
        assert tool_views
        assert "permission_user_denied" in str(tool_views[0].body.content)

    assert not (tmp_path / "denied.txt").exists()


@pytest.mark.asyncio
async def test_tui_can_continue_after_large_tool_result_compaction(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 1000, encoding="utf-8")
    tool_call = ToolCall("call-1", "read_file", {"path": "big.txt"})
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(tool_call,)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="已读取大文件"))],
        ]
    )
    registry = create_default_registry()
    executor = ToolExecutor(registry, ToolContext(cwd=tmp_path))
    context_manager = ContextManager(
        ContextConfig(single_tool_result_tokens=5, window_tokens=100_000),
        tmp_path,
        max_output_tokens=10,
    )
    app = MewCodeApp(ChatSession(), provider, make_config(), registry, executor, context_manager=context_manager)

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "读取 big.txt")

    assert len(provider.requests) == 2
    assert "mewcode_externalized" in provider.requests[1].messages[-1].content
    assert app.session.messages[-1].content == "已读取大文件"


@pytest.mark.asyncio
async def test_tui_allow_session_reuses_permission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    tool_call = ToolCall("call-1", "write_file", {"path": "session.txt", "content": "one"})
    second_call = ToolCall("call-2", "write_file", {"path": "session.txt", "content": "two"})
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(tool_call,)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="one done"))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(second_call,)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="two done"))],
        ]
    )
    app = make_permission_app(tmp_path, provider)

    async with app.run_test() as pilot:
        composer = app.query_one(Composer)
        composer.value = "write once"
        await pilot.press("enter")
        prompt = await wait_for_permission_prompt(app)
        prompt.choose("allow_session")
        await wait_for_generation_task(app, expected_done=True)
        await submit_and_wait(app, pilot, "write twice")

    assert (tmp_path / "session.txt").read_text(encoding="utf-8") == "two"
    assert app.session.messages[-1].content == "two done"


@pytest.mark.asyncio
async def test_tui_allow_permanent_writes_local_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    tool_call = ToolCall("call-1", "write_file", {"path": "permanent.txt", "content": "ok"})
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(tool_call,)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="done"))],
        ]
    )
    app = make_permission_app(tmp_path, provider)

    async with app.run_test() as pilot:
        composer = app.query_one(Composer)
        composer.value = "write"
        await pilot.press("enter")
        prompt = await wait_for_permission_prompt(app)
        prompt.choose("allow_permanent")
        await wait_for_generation_task(app, expected_done=True)

    assert "write_file(permanent.txt): allow" in (tmp_path / ".mewcode.permissions.local.yaml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_error_message_does_not_leak_secret() -> None:
    secret = "sk-tui-secret-1234567890"
    provider = FakeProvider([[ProviderError(f"bad {secret}")]])
    app = MewCodeApp(ChatSession(), provider, make_config())

    async with app.run_test() as pilot:
        await submit_and_wait(app, pilot, "hello")
        rendered = str(app.query_one(StatusBar).content)
        assert secret not in rendered
    assert "[REDACTED]" in rendered


@pytest.mark.asyncio
async def test_ctrl_c_cancels_running_agent_and_recovers_input() -> None:
    app = MewCodeApp(ChatSession(), SlowProvider(), make_config())

    async with app.run_test() as pilot:
        composer = app.query_one(Composer)
        composer.value = "slow"
        await pilot.press("enter")
        await wait_for_generation_task(app, expected_done=False)
        await pilot.press("ctrl+c")
        await wait_for_generation_task(app, expected_done=True)
        await submit_and_wait(app, pilot, "after cancel")
        assert app.query_one(Composer).disabled is False


def test_cli_entrypoint_is_importable() -> None:
    from mewcode.cli import main

    assert callable(main)
