from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import pytest

from mewcode.commands import (
    CommandDefinition,
    CommandDispatcher,
    CommandInvocation,
    CommandRegistry,
    CommandRegistryError,
    CommandSubAgentSnapshot,
    CommandSubAgentTaskSnapshot,
    CommandStatusSnapshot,
    CommandSessionSnapshot,
    CommandMemorySnapshot,
    CommandPermissionSnapshot,
    EmptyInput,
    PlainInput,
    UnknownCommandInput,
    create_builtin_command_registry,
)
from mewcode.mcp.manager import McpLoadReport
from mewcode.providers.base import TokenUsage
from mewcode.skills import LoadSkillTool, SkillManager
from mewcode.skills.models import SkillRoots
from mewcode.tools.registry import create_default_registry


async def noop_handler(invocation, context) -> None:
    _ = invocation, context


def command(name: str, *aliases: str, hidden: bool = False) -> CommandDefinition:
    return CommandDefinition(
        name=name,
        aliases=tuple(aliases),
        description=f"{name} 描述",
        usage=f"/{name}",
        kind="local",
        argument_hint="参数",
        hidden=hidden,
        handler=noop_handler,
    )


@dataclass
class FakeContext:
    current_mode: str = "normal"
    assistant_messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cleared: bool = False
    compact_called: bool = False
    refreshed: int = 0
    prompts: list[dict[str, str]] = field(default_factory=list)
    active_cleared: bool = False
    invoked_skills: list[dict[str, str]] = field(default_factory=list)

    @property
    def mode(self):
        return self.current_mode

    def set_mode(self, mode) -> None:
        self.current_mode = mode

    def status_snapshot(self) -> CommandStatusSnapshot:
        return CommandStatusSnapshot(
            protocol="openai",
            model="test-model",
            mode=self.current_mode,  # type: ignore[arg-type]
            agent_running=False,
            last_usage=TokenUsage(input_tokens=2, output_tokens=3, total_tokens=5),
            mcp_report=McpLoadReport(
                loaded_servers=("local",),
                registered_tools=("local__echo",),
                failed_servers={"bad": "失败"},
                failed_tools={},
            ),
        )

    def session_snapshot(self) -> CommandSessionSnapshot:
        return CommandSessionSnapshot(
            session_id="20260614-010203-abcd",
            restored=True,
            source_path=".mewcode/sessions/20260614-010203-abcd.jsonl",
            message_count=7,
            mode=self.current_mode,  # type: ignore[arg-type]
        )

    def memory_snapshot(self) -> CommandMemorySnapshot:
        return CommandMemorySnapshot(
            enabled=True,
            user_index_available=True,
            project_index_available=False,
            auto_notes_enabled=True,
            warning_count=1,
        )

    def permission_snapshot(self) -> CommandPermissionSnapshot:
        return CommandPermissionSnapshot(
            mode="default",
            session_rule_count=1,
            local_rule_count=2,
            project_rule_count=3,
            user_rule_count=4,
        )

    def skill_snapshot(self):
        from mewcode.commands import CommandSkillSnapshot

        return CommandSkillSnapshot(available=("commit", "review", "test"), active=(), warning_count=0)

    def sub_agent_snapshot(self) -> CommandSubAgentSnapshot:
        return CommandSubAgentSnapshot(
            enabled=True,
            available=("reviewer", "code-searcher"),
            background=(
                CommandSubAgentTaskSnapshot(
                    task_id="subagent-1",
                    type="defined",
                    role="reviewer",
                    status="background",
                    task="审查 README",
                    summary="正在执行",
                ),
            ),
            warning_count=0,
            foreground_running=False,
        )

    def refresh_status(self) -> None:
        self.refreshed += 1

    async def show_assistant(self, content: str) -> None:
        self.assistant_messages.append(content)

    async def show_error(self, content: str) -> None:
        self.errors.append(content)

    async def clear_messages(self) -> None:
        self.cleared = True

    async def compact_context(self) -> str:
        self.compact_called = True
        return "已完成手动压缩。"

    async def send_prompt(self, *, visible_text: str, model_text: str, mode) -> None:
        self.prompts.append({"visible_text": visible_text, "model_text": model_text, "mode": mode})

    async def invoke_skill(self, *, name: str, arguments: str, visible_text: str) -> None:
        self.invoked_skills.append({"name": name, "arguments": arguments, "visible_text": visible_text})

    async def background_current_sub_agent(self) -> bool:
        return False

    def clear_active_skills(self) -> None:
        self.active_cleared = True


def register_builtin_skills(registry: CommandRegistry, tmp_path: Path) -> SkillManager:
    tool_registry = create_default_registry()
    roots = SkillRoots(
        project=tmp_path / "project-skills",
        user=tmp_path / "user-skills",
        builtin=resources.files("mewcode.skills.builtin"),
    )
    manager = SkillManager(roots, tool_registry)
    tool_registry.register(LoadSkillTool(manager))
    manager.refresh_if_changed(registry)
    return manager


def test_registry_detects_conflict_between_name_and_alias() -> None:
    registry = CommandRegistry()
    registry.register(command("help", "h"))

    with pytest.raises(CommandRegistryError, match="/h.*help.*history"):
        registry.register(command("history", "H"))


def test_registry_detects_internal_conflict() -> None:
    registry = CommandRegistry()

    with pytest.raises(CommandRegistryError, match="内部入口冲突"):
        registry.register(command("help", "HELP"))


def test_cli_reports_command_registry_conflict(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from mewcode import cli as cli_module

    def fail_registry():
        raise CommandRegistryError("命令入口冲突: `/x`")

    monkeypatch.setattr(cli_module, "create_builtin_command_registry", fail_registry)

    assert cli_module.main([]) == 1
    assert "命令入口冲突" in capsys.readouterr().err


def test_parse_case_insensitive_command_and_argument() -> None:
    registry = CommandRegistry()
    registry.register(command("help"))

    parsed = registry.parse("  /HELP   status  ")

    assert isinstance(parsed, CommandInvocation)
    assert parsed.definition.name == "help"
    assert parsed.command_text == "/HELP"
    assert parsed.argument == "status"


def test_parse_empty_plain_and_unknown_inputs() -> None:
    registry = CommandRegistry()

    assert isinstance(registry.parse("   "), EmptyInput)
    plain = registry.parse("  帮我看 README  ")
    assert isinstance(plain, PlainInput)
    assert plain.text == "帮我看 README"
    unknown = registry.parse("/missing arg")
    assert isinstance(unknown, UnknownCommandInput)
    assert unknown.command_text == "/missing"


def test_completion_single_multi_and_hidden_commands() -> None:
    registry = CommandRegistry()
    registry.register(command("status", "st"))
    registry.register(command("session", "sess"))
    registry.register(command("secret", hidden=True))

    single = registry.completion("/sta")
    assert single.replacement == "/status"
    assert [option.name for option in single.options] == ["status"]

    multi = registry.completion("/s")
    assert multi.replacement is None
    assert [option.name for option in multi.options] == ["session", "status"]
    assert "secret" not in [option.name for option in multi.options]


@pytest.mark.asyncio
async def test_dispatcher_unknown_command_is_consumed_with_help_hint() -> None:
    dispatcher = CommandDispatcher(CommandRegistry())
    context = FakeContext()

    consumed = await dispatcher.dispatch("/wat", context)

    assert consumed is True
    assert "/help" in context.assistant_messages[-1]


@pytest.mark.asyncio
async def test_dispatcher_plain_input_is_not_consumed() -> None:
    dispatcher = CommandDispatcher(CommandRegistry())

    consumed = await dispatcher.dispatch("hello", FakeContext())

    assert consumed is False


@pytest.mark.asyncio
async def test_dispatcher_calls_handler_and_recovers_failure() -> None:
    async def ok_handler(invocation, context) -> None:
        await context.show_assistant(invocation.argument)

    async def failing_handler(invocation, context) -> None:
        _ = invocation, context
        raise RuntimeError("boom")

    registry = CommandRegistry()
    registry.register(command("ok"))
    registry.register(
        CommandDefinition("bad", (), "bad", "/bad", "local", handler=failing_handler)
    )
    registry.register(
        CommandDefinition("run", (), "run", "/run", "local", handler=ok_handler)
    )
    dispatcher = CommandDispatcher(registry)
    context = FakeContext()

    assert await dispatcher.dispatch("/run value", context) is True
    assert context.assistant_messages[-1] == "value"
    assert await dispatcher.dispatch("/bad", context) is True
    assert "命令执行失败" in context.errors[-1]


def test_builtin_registry_contains_builtin_visible_commands() -> None:
    registry = create_builtin_command_registry()

    names = [command.name for command in registry.visible_commands()]

    assert names == [
        "help",
        "compact",
        "clear",
        "plan",
        "do",
        "session",
        "memory",
        "permission",
        "status",
        "agents",
        "background",
    ]
    assert all(command.description and command.usage and command.kind for command in registry.visible_commands())


@pytest.mark.asyncio
async def test_help_lists_builtin_commands_and_single_command_detail(tmp_path: Path) -> None:
    registry = create_builtin_command_registry()
    register_builtin_skills(registry, tmp_path)
    dispatcher = CommandDispatcher(registry)
    context = FakeContext()

    await dispatcher.dispatch("/help", context)
    all_help = context.assistant_messages[-1]
    assert all(
        f"/{name}" in all_help
        for name in [
            "help",
            "compact",
            "clear",
            "plan",
            "do",
            "session",
            "memory",
            "permission",
            "status",
            "agents",
            "background",
            "review",
        ]
    )
    assert "别名" in all_help
    assert "用法" in all_help
    assert "参数" in all_help

    await dispatcher.dispatch("/help review", context)
    review_help = context.assistant_messages[-1]
    assert "/review" in review_help
    assert "审查" in review_help
    assert "/status —" not in review_help


@pytest.mark.asyncio
async def test_alias_invokes_same_builtin_command_and_skill_command(tmp_path: Path) -> None:
    registry = create_builtin_command_registry()
    register_builtin_skills(registry, tmp_path)
    dispatcher = CommandDispatcher(registry)
    context = FakeContext()

    await dispatcher.dispatch("/h review", context)
    await dispatcher.dispatch("/review README.md", context)

    assert "/review" in context.assistant_messages[-1]
    assert context.invoked_skills[-1] == {
        "name": "review",
        "arguments": "README.md",
        "visible_text": "/review README.md",
    }


@pytest.mark.asyncio
async def test_session_memory_permission_and_status_commands_render_snapshots() -> None:
    registry = create_builtin_command_registry()
    dispatcher = CommandDispatcher(registry)
    context = FakeContext()

    for raw in ("/session", "/memory", "/permission", "/status", "/agents", "/background"):
        await dispatcher.dispatch(raw, context)

    rendered = "\n".join(context.assistant_messages)
    assert "20260614-010203-abcd" in rendered
    assert "当前消息数量：7" in rendered
    assert "用户级记忆索引：可用" in rendered
    assert "项目级记忆索引：不可用" in rendered
    assert "权限模式：default" in rendered
    assert "会话临时规则：1 条" in rendered
    assert "read_file(secret.txt)" not in rendered
    assert "供应商：openai" in rendered
    assert "Token：5" in rendered
    assert "失败 Server 1 个" in rendered
    assert "子 Agent：功能是，角色 2 个" in rendered
    assert "子 Agent 状态" in rendered
    assert "当前没有可切到后台" in rendered


@pytest.mark.asyncio
async def test_plan_do_clear_compact_and_review_handlers() -> None:
    registry = create_builtin_command_registry()
    dispatcher = CommandDispatcher(registry)
    context = FakeContext()

    await dispatcher.dispatch("/plan", context)
    assert context.current_mode == "plan"
    assert context.refreshed == 1
    assert "[PLAN]" in context.assistant_messages[-1]

    await dispatcher.dispatch("/do", context)
    assert context.current_mode == "normal"
    assert context.refreshed == 2
    assert "[DEFAULT]" in context.assistant_messages[-1]

    await dispatcher.dispatch("/clear", context)
    assert context.cleared is True
    assert context.active_cleared is True
    assert "会话上下文" in context.assistant_messages[-1]

    await dispatcher.dispatch("/compact", context)
    assert context.compact_called is True
    assert context.assistant_messages[-1] == "已完成手动压缩。"

    assert context.prompts == []
