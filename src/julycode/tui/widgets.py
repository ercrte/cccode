from __future__ import annotations

import asyncio

from collections.abc import Sequence
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.widget import Widget
from textual.widgets import Button, Input, Static

from julycode.agent import AgentProgress
from julycode.commands import AgentMode, CommandDefinition
from julycode.permissions.models import PermissionPrompt, UserPermissionChoice
from julycode.providers.base import TokenUsage
from julycode.tools.base import ToolResult


class StatusBar(Static):
    def __init__(self, protocol: str, model: str, mode: AgentMode = "normal", **kwargs: object) -> None:
        super().__init__("", **kwargs)
        self.protocol = protocol
        self.model = model
        self.mode = mode
        self.generating = False
        self.error: str | None = None
        self.progress: AgentProgress | None = None
        self.usage: TokenUsage | None = None
        self.permission_state: str | None = None

    def on_mount(self) -> None:
        self.refresh_status()

    def set_generating(self, generating: bool) -> None:
        self.generating = generating
        if generating:
            self.error = None
            self.progress = None
            self.usage = None
            self.permission_state = None
        self.refresh_status()

    def set_error(self, error: str | None) -> None:
        self.error = error
        self.generating = False
        self.refresh_status()

    def set_progress(self, progress: AgentProgress | None) -> None:
        self.progress = progress
        self.refresh_status()

    def set_usage(self, usage: TokenUsage | None) -> None:
        self.usage = usage
        self.refresh_status()

    def set_permission_state(self, state: str | None) -> None:
        self.permission_state = state
        self.refresh_status()

    def set_mode(self, mode: AgentMode) -> None:
        self.mode = mode
        self.refresh_status()

    def refresh_status(self) -> None:
        state = "生成中" if self.generating else "空闲"
        mode = "[PLAN]" if self.mode == "plan" else "[DEFAULT]"
        progress = ""
        if self.progress is not None:
            progress = (
                f" | {self.progress.mode} "
                f"{self.progress.iteration}/{self.progress.max_iterations} "
                f"{self.progress.phase}"
            )
        usage = ""
        if self.generating:
            if self.usage is None:
                usage = " | Token: 未知"
            else:
                total = self.usage.total_tokens
                if total is not None:
                    usage = f" | Token: {total}"
                else:
                    usage = (
                        " | Token: "
                        f"in={self.usage.input_tokens if self.usage.input_tokens is not None else '?'} "
                        f"out={self.usage.output_tokens if self.usage.output_tokens is not None else '?'}"
                    )
                cache = self._cache_text()
                if cache:
                    usage += f" | Cache: {cache}"
        error = f" | 错误: {self.error}" if self.error else ""
        permission = f" | 权限: {self.permission_state}" if self.permission_state else ""
        self.update(Text(f"JulyCode | {mode} | {self.protocol} | {self.model} | {state}{progress}{usage}{permission}{error}"))

    def _cache_text(self) -> str:
        if self.usage is None or self.usage.cache is None:
            return ""
        cache = self.usage.cache
        if cache.status == "hit":
            value = cache.cached_tokens if cache.cached_tokens is not None else cache.read_input_tokens
            return f"hit {value}" if value is not None else "hit"
        if cache.status == "write":
            value = cache.creation_input_tokens
            return f"write {value}" if value is not None else "write"
        return cache.status


class ThinkingPanel(Vertical):
    DEFAULT_CSS = """
    ThinkingPanel {
        height: auto;
        border: round $accent;
        padding: 0 1;
        margin-top: 1;
    }
    ThinkingPanel > .thinking-body {
        color: $text-muted;
    }
    """

    def __init__(self, text: str = "", collapsed: bool = True, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.text = text
        self.collapsed = collapsed
        self.header = Static()
        self.body = Static(classes="thinking-body")

    def compose(self) -> ComposeResult:
        yield self.header
        yield self.body

    def on_mount(self) -> None:
        self.refresh_panel()

    def toggle(self) -> None:
        self.collapsed = not self.collapsed
        self.refresh_panel()

    def append_text(self, text: str) -> None:
        self.text += text
        self.refresh_panel()

    def set_text(self, text: str) -> None:
        self.text = text
        self.refresh_panel()

    def refresh_panel(self) -> None:
        marker = "+" if self.collapsed else "-"
        self.header.update(f"{marker} thinking")
        self.body.update(self.text or "无可见 thinking 内容")
        self.body.display = not self.collapsed


class MessageView(Vertical):
    DEFAULT_CSS = """
    MessageView {
        height: auto;
        margin: 0 1 1 1;
        padding: 0 1;
        border-left: solid $primary;
    }
    MessageView.error {
        border-left: solid $error;
    }
    .message-role {
        text-style: bold;
    }
    .message-body {
        height: auto;
    }
    """

    def __init__(
        self,
        role: str,
        content: str = "",
        thinking: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(classes="error" if role == "error" else "", **kwargs)
        self.role = role
        self.content = content
        self.thinking = thinking or ""
        self.role_label = Static(classes="message-role")
        self.body = Static(classes="message-body")
        self.thinking_panel = ThinkingPanel(self.thinking, collapsed=True)

    def compose(self) -> ComposeResult:
        yield self.role_label
        yield self.body
        yield self.thinking_panel

    def on_mount(self) -> None:
        self.role_label.update(_role_title(self.role))
        self.body.update(Text(self.content))
        self.thinking_panel.display = bool(self.thinking)

    def append_content(self, text: str) -> None:
        self.content += text
        self.body.update(Text(self.content))

    def set_content(self, text: str) -> None:
        self.content = text
        self.body.update(Text(self.content))

    def append_thinking(self, text: str) -> None:
        self.thinking += text
        self.thinking_panel.display = True
        self.thinking_panel.append_text(text)

    def set_thinking(self, text: str) -> None:
        self.thinking = text
        self.thinking_panel.display = bool(text)
        self.thinking_panel.set_text(text)


class ToolStatusView(Vertical):
    DEFAULT_CSS = """
    ToolStatusView {
        height: auto;
        margin: 0 1 1 1;
        padding: 0 1;
        border-left: solid $secondary;
    }
    ToolStatusView.failed {
        border-left: solid $error;
    }
    .tool-title {
        text-style: bold;
    }
    .tool-body {
        height: auto;
        color: $text-muted;
    }
    """

    def __init__(self, tool_name: str, tool_call_id: str | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.state = "running"
        self.result: ToolResult | None = None
        self.title = Static(classes="tool-title")
        self.body = Static(classes="tool-body")

    def compose(self) -> ComposeResult:
        yield self.title
        yield self.body

    def on_mount(self) -> None:
        self.refresh_status()

    def finish(self, result: ToolResult) -> None:
        self.result = result
        self.state = "success" if result.success else "failed"
        self.set_class(not result.success, "failed")
        self.refresh_status()

    def refresh_status(self) -> None:
        suffix = f" [{self.tool_call_id}]" if self.tool_call_id else ""
        self.title.update(f"工具: {self.tool_name}{suffix}")
        if self.result is None:
            self.body.update("运行中")
            return
        if self.result.success:
            self.body.update("完成")
            return
        self.body.update(f"失败: {self.result.error_type or 'tool_error'}")


class PermissionPromptView(Vertical):
    DEFAULT_CSS = """
    PermissionPromptView {
        height: auto;
        margin: 0 1 1 1;
        padding: 0 1;
        border-left: solid $warning;
    }
    .permission-title {
        text-style: bold;
    }
    .permission-body {
        height: auto;
        color: $text-muted;
    }
    .permission-actions {
        height: auto;
    }
    """

    def __init__(
        self,
        prompt: PermissionPrompt,
        future: asyncio.Future[UserPermissionChoice],
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.prompt = prompt
        self.future = future
        self.title = Static(classes="permission-title")
        self.body = Static(classes="permission-body")

    def compose(self) -> ComposeResult:
        yield self.title
        yield self.body
        yield Button("本次允许", classes="permission-allow-once")
        yield Button("本会话允许", classes="permission-allow-session")
        yield Button("永久允许", classes="permission-allow-permanent")
        yield Button("拒绝", classes="permission-deny")

    def on_mount(self) -> None:
        self.title.update(f"权限确认: {self.prompt.tool_name}")
        self.body.update(f"{self.prompt.summary}\n{self.prompt.reason}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choice: UserPermissionChoice | None = None
        if event.button.has_class("permission-allow-once"):
            choice = "allow_once"
        elif event.button.has_class("permission-allow-session"):
            choice = "allow_session"
        elif event.button.has_class("permission-allow-permanent"):
            choice = "allow_permanent"
        elif event.button.has_class("permission-deny"):
            choice = "deny"
        if choice is None:
            return
        event.stop()
        self.choose(choice)

    def choose(self, choice: UserPermissionChoice) -> None:
        if not self.future.done():
            self.future.set_result(choice)
        self.display = False


class MessageList(VerticalScroll):
    DEFAULT_CSS = """
    MessageList {
        height: 1fr;
        border: round $primary;
    }
    """

    async def append_message(self, message: Widget) -> Widget:
        await self.mount(message)
        self.scroll_end(animate=False)
        return message

    async def clear_messages(self) -> None:
        await self.remove_children()


class CommandCompletionMenu(Vertical):
    DEFAULT_CSS = """
    CommandCompletionMenu {
        height: auto;
        max-height: 6;
        margin: 0 1;
        padding: 0 1;
        border-left: solid $secondary;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.body = Static()

    def compose(self) -> ComposeResult:
        yield self.body

    def on_mount(self) -> None:
        self.clear_options()

    def set_options(self, options: Sequence[CommandDefinition]) -> None:
        if not options:
            self.clear_options()
            return
        lines = [f"/{option.name}  {option.description}" for option in options]
        self.body.update("\n".join(lines))
        self.display = True

    def clear_options(self) -> None:
        self.body.update("")
        self.display = False


class Composer(Input):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(placeholder="输入问题后按 Enter 发送", **kwargs)

    def on_key(self, event: Key) -> None:
        if event.key != "tab":
            return
        event.prevent_default()
        event.stop()
        self.app.action_complete_command()


def _role_title(role: str) -> str:
    if role == "user":
        return "你"
    if role == "assistant":
        return "JulyCode"
    if role == "error":
        return "错误"
    return role
