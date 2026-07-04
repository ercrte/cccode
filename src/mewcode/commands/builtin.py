from __future__ import annotations

from mewcode.commands.models import (
    CommandContext,
    CommandDefinition,
    CommandInvocation,
    CommandMemorySnapshot,
    CommandPermissionSnapshot,
    CommandSessionSnapshot,
    CommandSkillSnapshot,
    CommandStatusSnapshot,
    CommandSubAgentSnapshot,
    TokenUsage,
)
from mewcode.commands.registry import CommandRegistry


def create_builtin_command_registry() -> CommandRegistry:
    registry = CommandRegistry()

    async def help_handler(invocation: CommandInvocation, context: CommandContext) -> None:
        argument = invocation.argument.strip()
        if not argument:
            await context.show_assistant(_format_all_help(registry))
            return

        command = registry.get(argument)
        if command is None:
            await context.show_assistant(f"未找到命令 `/{argument.removeprefix('/')}`。输入 `/help` 查看可用命令。")
            return
        await context.show_assistant(_format_command_help(command))

    definitions = (
        CommandDefinition(
            name="help",
            aliases=("h", "?"),
            description="显示命令帮助。",
            usage="/help [命令]",
            kind="local",
            argument_hint="命令名或别名",
            handler=help_handler,
        ),
        CommandDefinition(
            name="compact",
            aliases=("comp",),
            description="手动触发上下文压缩检查。",
            usage="/compact",
            kind="local",
            handler=_compact_handler,
        ),
        CommandDefinition(
            name="clear",
            aliases=("cls",),
            description="清空当前界面消息显示区。",
            usage="/clear",
            kind="ui",
            handler=_clear_handler,
        ),
        CommandDefinition(
            name="plan",
            aliases=("p",),
            description="进入计划模式，后续普通输入按计划模式处理。",
            usage="/plan",
            kind="ui",
            handler=_plan_handler,
        ),
        CommandDefinition(
            name="do",
            aliases=("d",),
            description="回到默认执行模式。",
            usage="/do",
            kind="ui",
            handler=_do_handler,
        ),
        CommandDefinition(
            name="session",
            aliases=("sess",),
            description="显示当前会话状态。",
            usage="/session",
            kind="local",
            handler=_session_handler,
        ),
        CommandDefinition(
            name="memory",
            aliases=("mem",),
            description="显示长期记忆状态。",
            usage="/memory",
            kind="local",
            handler=_memory_handler,
        ),
        CommandDefinition(
            name="permission",
            aliases=("perm",),
            description="显示权限系统状态。",
            usage="/permission",
            kind="local",
            handler=_permission_handler,
        ),
        CommandDefinition(
            name="status",
            aliases=("st",),
            description="显示当前运行状态。",
            usage="/status",
            kind="local",
            handler=_status_handler,
        ),
        CommandDefinition(
            name="mcp",
            aliases=(),
            description="管理 MCP Server 的 OAuth 授权。",
            usage="/mcp auth|logout <server>",
            kind="local",
            argument_hint="auth 或 logout，以及 Server 名",
            handler=_mcp_handler,
        ),
        CommandDefinition(
            name="agents",
            aliases=("agent",),
            description="显示子 Agent 角色和后台任务。",
            usage="/agents",
            kind="local",
            handler=_agents_handler,
        ),
        CommandDefinition(
            name="background",
            aliases=("bg",),
            description="把当前前台子 Agent 任务切到后台。",
            usage="/background",
            kind="ui",
            handler=_background_handler,
        ),
    )
    for definition in definitions:
        registry.register(definition)
    return registry


async def _compact_handler(invocation: CommandInvocation, context: CommandContext) -> None:
    if invocation.argument:
        await context.show_assistant("`/compact` 不接受参数，请单独输入 `/compact`。")
        return
    await context.show_assistant(await context.compact_context())


async def _clear_handler(invocation: CommandInvocation, context: CommandContext) -> None:
    _ = invocation
    await context.clear_messages()
    clear_active = getattr(context, "clear_active_skills", None)
    if clear_active is not None:
        clear_active()
        await context.show_assistant("已清空当前界面消息显示区；已激活 Skill 已清理；会话上下文、持久记录和长期记忆仍保留。")
        return
    await context.show_assistant("已清空当前界面消息显示区；会话上下文、持久记录和长期记忆仍保留。")


async def _plan_handler(invocation: CommandInvocation, context: CommandContext) -> None:
    context.set_mode("plan")
    context.refresh_status()
    suffix = ""
    if invocation.argument:
        suffix = f"\n参数 `{invocation.argument}` 未发送给 AI；请直接输入要规划的需求。"
    await context.show_assistant(f"已进入计划模式 [PLAN]，后续普通输入会按计划模式处理。{suffix}")


async def _do_handler(invocation: CommandInvocation, context: CommandContext) -> None:
    context.set_mode("normal")
    context.refresh_status()
    suffix = ""
    if invocation.argument:
        suffix = f"\n参数 `{invocation.argument}` 已忽略；`/do` 只用于回到默认模式。"
    await context.show_assistant(f"已回到默认模式 [DEFAULT]。{suffix}")


async def _session_handler(invocation: CommandInvocation, context: CommandContext) -> None:
    _ = invocation
    snapshot = context.session_snapshot()
    await context.show_assistant(_format_session(snapshot))


async def _memory_handler(invocation: CommandInvocation, context: CommandContext) -> None:
    _ = invocation
    snapshot = context.memory_snapshot()
    await context.show_assistant(_format_memory(snapshot))


async def _permission_handler(invocation: CommandInvocation, context: CommandContext) -> None:
    _ = invocation
    snapshot = context.permission_snapshot()
    await context.show_assistant(_format_permission(snapshot))


async def _status_handler(invocation: CommandInvocation, context: CommandContext) -> None:
    _ = invocation
    snapshot = context.status_snapshot()
    skill_snapshot = None
    snapshot_getter = getattr(context, "skill_snapshot", None)
    if snapshot_getter is not None:
        skill_snapshot = snapshot_getter()
    sub_agent_snapshot = None
    sub_agent_getter = getattr(context, "sub_agent_snapshot", None)
    if sub_agent_getter is not None:
        sub_agent_snapshot = sub_agent_getter()
    await context.show_assistant(_format_status(snapshot, skill_snapshot, sub_agent_snapshot))


async def _mcp_handler(invocation: CommandInvocation, context: CommandContext) -> None:
    parts = invocation.argument.split()
    if len(parts) != 2 or parts[0].casefold() not in {"auth", "logout"}:
        await context.show_assistant("用法：`/mcp auth <server>` 或 `/mcp logout <server>`。")
        return
    action, server_name = parts[0].casefold(), parts[1]
    if action == "auth":
        result = await context.authorize_mcp_server(server_name)
    else:
        result = await context.logout_mcp_server(server_name)
    context.refresh_status()
    await context.show_assistant(result)


async def _agents_handler(invocation: CommandInvocation, context: CommandContext) -> None:
    _ = invocation
    snapshot_getter = getattr(context, "sub_agent_snapshot", None)
    if snapshot_getter is None:
        await context.show_assistant("当前运行环境不支持子 Agent 状态查询。")
        return
    await context.show_assistant(_format_sub_agents(snapshot_getter()))


async def _background_handler(invocation: CommandInvocation, context: CommandContext) -> None:
    if invocation.argument:
        await context.show_assistant("`/background` 不接受参数，会作用于当前前台子 Agent 任务。")
        return
    switcher = getattr(context, "background_current_sub_agent", None)
    if switcher is None:
        await context.show_assistant("当前运行环境不支持子 Agent 后台切换。")
        return
    switched = await switcher()
    if switched:
        await context.show_assistant("当前前台子 Agent 任务已切到后台，完成后会异步通知主对话。")
        return
    await context.show_assistant("当前没有可切到后台的前台子 Agent 任务。")


def _format_all_help(registry: CommandRegistry) -> str:
    lines = ["可用命令："]
    for command in registry.visible_commands():
        lines.extend(("", _format_command_summary(command)))
    return "\n".join(lines).strip()


def _format_command_help(command: CommandDefinition) -> str:
    return "\n".join(
        line
        for line in (
            f"/{command.name}",
            f"类型：{_kind_label(command.kind)}",
            f"描述：{command.description}",
            f"别名：{_format_aliases(command)}",
            f"用法：{command.usage}",
            f"参数：{command.argument_hint or '无'}",
        )
        if line
    )


def _format_command_summary(command: CommandDefinition) -> str:
    return "\n".join(
        (
            f"/{command.name} — {command.description}",
            f"  别名：{_format_aliases(command)}",
            f"  用法：{command.usage}",
            f"  参数：{command.argument_hint or '无'}",
        )
    )


def _format_aliases(command: CommandDefinition) -> str:
    if not command.aliases:
        return "无"
    return ", ".join(f"/{alias}" for alias in command.aliases)


def _kind_label(kind: str) -> str:
    labels = {
        "local": "纯本地",
        "ui": "影响界面状态",
        "prompt": "预设提示词",
    }
    return labels.get(kind, kind)


def _format_session(snapshot: CommandSessionSnapshot) -> str:
    restored = "是" if snapshot.restored else "否"
    source = snapshot.source_path or "无"
    return "\n".join(
        (
            "会话状态：",
            f"- 会话标识：{snapshot.session_id}",
            f"- 恢复自历史会话：{restored}",
            f"- 来源：{source}",
            f"- 当前消息数量：{snapshot.message_count}",
            f"- 当前模式：{_mode_label(snapshot.mode)}",
        )
    )


def _format_memory(snapshot: CommandMemorySnapshot) -> str:
    return "\n".join(
        (
            "长期记忆状态：",
            f"- 记忆功能：{_enabled(snapshot.enabled)}",
            f"- 用户级记忆索引：{_available(snapshot.user_index_available)}",
            f"- 项目级记忆索引：{_available(snapshot.project_index_available)}",
            f"- 自动笔记：{_enabled(snapshot.auto_notes_enabled)}",
            f"- 告警数量：{snapshot.warning_count}",
        )
    )


def _format_permission(snapshot: CommandPermissionSnapshot) -> str:
    return "\n".join(
        (
            "权限状态：",
            f"- 权限模式：{snapshot.mode}",
            f"- 会话临时规则：{snapshot.session_rule_count} 条",
            f"- 本地规则：{snapshot.local_rule_count} 条",
            f"- 项目规则：{snapshot.project_rule_count} 条",
            f"- 用户规则：{snapshot.user_rule_count} 条",
            "- 权限确认：当有副作用工具需要确认时，可选择本次允许、本会话允许、永久允许或拒绝。",
        )
    )


def _format_status(
    snapshot: CommandStatusSnapshot,
    skill_snapshot: CommandSkillSnapshot | None = None,
    sub_agent_snapshot: CommandSubAgentSnapshot | None = None,
) -> str:
    mcp_report = snapshot.mcp_report
    if mcp_report is None:
        mcp = "未配置或未初始化"
    else:
        mcp = (
            f"已连接 Server {len(mcp_report.loaded_servers)} 个，发现工具 {len(mcp_report.discovered_tools)} 个，"
            f"当前轮次暴露 {len(snapshot.mcp_active_tools)} 个，"
            f"失败 Server {len(mcp_report.failed_servers)} 个，失败工具 {len(mcp_report.failed_tools)} 个"
        )
        if mcp_report.oauth_status:
            oauth_items = ", ".join(
                f"{name}={_oauth_state_label(status.state)}"
                for name, status in sorted(mcp_report.oauth_status.items())
            )
            mcp += f"；OAuth：{oauth_items}"
        if mcp_report.warnings:
            mcp += f"；告警：{'；'.join(mcp_report.warnings)}"
    skill_line = "未启用"
    if skill_snapshot is not None:
        active = ", ".join(skill_snapshot.active) if skill_snapshot.active else "无"
        skill_line = (
            f"可用 {len(skill_snapshot.available)} 个，已激活 {active}，"
            f"告警 {skill_snapshot.warning_count} 条"
        )
    sub_agent_line = "未启用"
    if sub_agent_snapshot is not None:
        active_background = sum(
            1 for task in sub_agent_snapshot.background if task.status in {"queued", "running", "background"}
        )
        sub_agent_line = (
            f"功能{_enabled(sub_agent_snapshot.enabled)}，角色 {len(sub_agent_snapshot.available)} 个，"
            f"后台/运行 {active_background} 个，告警 {sub_agent_snapshot.warning_count} 条"
        )
    return "\n".join(
        (
            "运行状态：",
            f"- 供应商：{snapshot.protocol}",
            f"- 模型：{snapshot.model}",
            f"- 当前模式：{_mode_label(snapshot.mode)}",
            f"- 任务运行中：{_enabled(snapshot.agent_running)}",
            f"- Token：{_format_usage(snapshot.last_usage)}",
            f"- MCP：{mcp}",
            f"- Skill：{skill_line}",
            f"- 子 Agent：{sub_agent_line}",
        )
    )


def _format_sub_agents(snapshot: CommandSubAgentSnapshot) -> str:
    lines = [
        "子 Agent 状态：",
        f"- 功能启用：{_enabled(snapshot.enabled)}",
        f"- 前台子任务运行中：{_enabled(snapshot.foreground_running)}",
        f"- 角色数量：{len(snapshot.available)}",
        f"- 告警数量：{snapshot.warning_count}",
    ]
    if snapshot.available:
        lines.append("- 可用角色：" + ", ".join(snapshot.available))
    else:
        lines.append("- 可用角色：无")
    if not snapshot.background:
        lines.append("- 后台任务：无")
        return "\n".join(lines)
    lines.append("- 后台任务：")
    for task in snapshot.background:
        role = task.role or "无"
        summary = task.summary or "暂无结果"
        lines.append(f"  - {task.task_id} [{task.status}] {task.type}/{role}: {task.task}")
        lines.append(f"    摘要：{summary}")
    return "\n".join(lines)


def _format_usage(usage: TokenUsage | None) -> str:
    if usage is None:
        return "未知"
    if usage.total_tokens is not None:
        return str(usage.total_tokens)
    return (
        f"in={usage.input_tokens if usage.input_tokens is not None else '?'} "
        f"out={usage.output_tokens if usage.output_tokens is not None else '?'}"
    )


def _oauth_state_label(state: str) -> str:
    return {
        "authorization_required": "需要授权",
        "authorizing": "授权中",
        "authorized": "已授权",
        "refreshing": "刷新中",
        "refresh_failed": "刷新失败",
    }.get(state, state)


def _mode_label(mode: str) -> str:
    return "[PLAN]" if mode == "plan" else "[DEFAULT]"


def _enabled(value: bool) -> str:
    return "是" if value else "否"


def _available(value: bool) -> str:
    return "可用" if value else "不可用"
