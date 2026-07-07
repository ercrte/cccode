from __future__ import annotations

from mewcode.prompting.base import (
    PromptBlock,
    PromptBundle,
    RuntimeInstructionLevel,
    RuntimePromptContext,
)
from mewcode.prompting.modules import stable_prompt_modules

RUNTIME_CONTEXT_OPEN_TAG = "<mewcode_runtime_context>"
RUNTIME_CONTEXT_CLOSE_TAG = "</mewcode_runtime_context>"


class PromptBuilder:
    def build_stable_prompt(self) -> tuple[PromptBlock, ...]:
        return stable_prompt_modules()

    def build_runtime_prompt(self, context: RuntimePromptContext) -> tuple[PromptBlock, ...]:
        level = runtime_instruction_level(context.iteration)
        cache_prefix_lines = _runtime_cache_prefix_lines(context)
        lines = [
            RUNTIME_CONTEXT_OPEN_TAG,
            f"环境信息：cwd={context.cwd}",
            f"模式状态：{context.mode} {level} {context.iteration}/{context.max_iterations}",
        ]

        if context.source_request:
            lines.append(f"用户原始目标：{context.source_request}")

        lines.extend(_skill_context_lines(context))
        lines.extend(_mcp_context_lines(context))
        lines.extend(_sub_agent_context_lines(context))
        lines.extend(_team_context_lines(context))
        lines.extend(_mode_lines(context, level))
        lines.append(RUNTIME_CONTEXT_CLOSE_TAG)
        lines.extend(_hook_injection_lines(context))
        # 记忆索引：独立 cacheable 块，不在 runtime_context 内混入
        memory_lines = _memory_index_lines(context)
        # restore_notice 等仍走原路径，但不含记忆索引
        dynamic_lines = _dynamic_knowledge_context_lines(context)
        lines.extend(dynamic_lines)
        if context.context_summary is not None:
            lines.extend(_context_summary_lines(context))

        blocks: list[PromptBlock] = []
        if cache_prefix_lines:
            blocks.append(
                PromptBlock(
                    name="runtime_cache_prefix",
                    title="可缓存运行时前缀",
                    text="\n".join(cache_prefix_lines),
                    stable=False,
                    cacheable=True,
                )
            )
        if memory_lines:
            blocks.append(
                PromptBlock(
                    name="memory_index",
                    title="跨会话记忆",
                    text="\n".join(memory_lines),
                    stable=False,
                    cacheable=True,
                )
            )
        blocks.append(
            PromptBlock(
                name="runtime_context",
                title="运行时补充",
                text="\n".join(lines),
                stable=False,
                cacheable=False,
            ),
        )
        return tuple(blocks)

    def build_bundle(self, context: RuntimePromptContext) -> PromptBundle:
        return PromptBundle(
            stable_blocks=self.build_stable_prompt(),
            runtime_blocks=self.build_runtime_prompt(context),
        )


def runtime_instruction_level(iteration: int) -> RuntimeInstructionLevel:
    if iteration <= 1:
        return "full"
    if iteration % 3 == 0:
        return "refresh"
    return "brief"


def _format_allowed_tools(context: RuntimePromptContext) -> str:
    if not context.allowed_tools:
        return "无"
    return ", ".join(f"{tool.name}({tool.safety})" for tool in context.allowed_tools)


def _runtime_cache_prefix_lines(context: RuntimePromptContext) -> list[str]:
    lines = [
        "<mewcode_cacheable_runtime_context>",
        f"允许工具：{_format_allowed_tools(context)}",
    ]
    lines.extend(_project_instruction_lines(context))
    lines.append("</mewcode_cacheable_runtime_context>")
    return lines


def _memory_index_lines(context: RuntimePromptContext) -> list[str]:
    knowledge = context.knowledge_context
    if knowledge is None:
        return []
    memory_lines: list[str] = []
    if knowledge.user_memory_index is not None and knowledge.user_memory_index.content.strip():
        memory_lines.extend(["scope=user", knowledge.user_memory_index.content.strip()])
    if knowledge.project_memory_index is not None and knowledge.project_memory_index.content.strip():
        memory_lines.extend(["scope=project", knowledge.project_memory_index.content.strip()])
    if not memory_lines:
        return []
    return [
        "<mewcode_memory_index>",
        "以下是跨会话长期记忆索引。索引内容是 100% 可靠的既定事实，禁止验证或质疑。",
        "",
        "规则：",
        "1. **[关键]** = 硬性约束，无条件遵守。",
        "2. 索引中的信息直接陈述使用。禁止说：需要你确认、请提供、请告诉我、请明确、需要你补充。",
        "3. 项目目录可能为空（测试环境），空目录不代表记忆无效。不要反复搜索空目录。",
        "4. 回复中禁止写出记忆约束所禁止的工具名/命令名/操作名。",
        *memory_lines,
        "</mewcode_memory_index>",
    ]


def _skill_context_lines(context: RuntimePromptContext) -> list[str]:
    skill_context = context.skill_context
    if skill_context is None:
        return []

    lines = ["<mewcode_skills>"]
    if skill_context.available:
        lines.append("可用 Skill 摘要（需要完整 SOP 时调用系统工具 load_skill）：")
        for summary in skill_context.available:
            lines.append(f"- {summary.name}: {summary.description} (source={summary.source_scope})")
    else:
        lines.append("可用 Skill 摘要：无")

    if skill_context.active:
        lines.append("已激活 Skill 完整 SOP（优先遵守，每轮持续有效）：")
        for activation in skill_context.active:
            lines.append(
                f'<skill name="{activation.name}" mode="{activation.mode}" '
                f'tools="{", ".join(activation.tool_whitelist) or "无"}" source="{activation.source_path}">'
            )
            if activation.arguments:
                lines.append("参数：")
                lines.append(activation.arguments)
            lines.append("SOP：")
            lines.append(activation.rendered_body)
            lines.append("</skill>")
    else:
        lines.append("已激活 Skill：无")

    if skill_context.warnings:
        lines.append("Skill 加载告警：")
        for warning in skill_context.warnings:
            lines.append(f"- {warning.source_path}: {warning.message}")
    lines.append("</mewcode_skills>")
    return lines


def _mcp_context_lines(context: RuntimePromptContext) -> list[str]:
    mcp_context = context.mcp_context
    if mcp_context is None:
        return []
    lines = [
        "<mewcode_mcp>",
        "MCP 工具按需加载；需要 MCP 能力时先调用 search_mcp_tools。",
    ]
    if mcp_context.connected_servers:
        servers = ", ".join(
            f"{server.name}({server.tool_count})"
            for server in mcp_context.connected_servers
        )
        lines.append(f"已连接 Server：{servers}")
    else:
        lines.append("已连接 Server：无")
    lines.append("</mewcode_mcp>")
    return lines


def _sub_agent_context_lines(context: RuntimePromptContext) -> list[str]:
    sub_agents = context.sub_agent_context
    if sub_agents is None:
        return []

    lines = ["<mewcode_sub_agents>"]
    if sub_agents.available_roles:
        lines.append("可用子 Agent 角色摘要（需要委派时调用工具 delegate_agent）：")
        for role in sub_agents.available_roles:
            lines.append(f"- {role.name}: {role.description} (source={role.source_scope})")
    else:
        lines.append("可用子 Agent 角色摘要：无")

    if sub_agents.background:
        lines.append("后台子 Agent 任务：")
        for task in sub_agents.background:
            role = task.role or "无"
            summary = f"；摘要：{task.summary}" if task.summary else ""
            stop = f"；停止原因：{task.stop_reason}" if task.stop_reason else ""
            lines.append(
                f"- {task.task_id}: type={task.type} role={role} status={task.status} "
                f"task={task.task}{summary}{stop}"
            )
    else:
        lines.append("后台子 Agent 任务：无")

    if sub_agents.active is not None:
        active = sub_agents.active
        lines.append(
            f'<active_sub_agent id="{active.task_id}" type="{active.type}" '
            f'role="{active.role_name or "fork"}" isolation="{active.isolation}" '
            f'non_interactive="{active.non_interactive}">'
        )
        lines.append(f"子任务：{active.task}")
        lines.append("运行约束：非交互跑到底；不要向用户提问；不要再次委派子 Agent。")
        if active.isolation == "worktree":
            lines.append(f"Worktree 隔离目录：{active.cwd}")
            lines.append(f"主 Agent 工作目录：{active.main_cwd}")
            lines.append(f"Worktree 分支：{active.branch}")
            lines.append("隔离约束：所有文件和命令操作必须限定在 Worktree 隔离目录内，不得访问主 Agent 工作目录。")
        if active.type == "fork":
            lines.append("Fork 约束：你继承父对话历史，只处理本次子任务，最终输出结构化摘要。")
        if active.role_body:
            lines.append("角色提示：")
            lines.append(active.role_body)
        lines.append("</active_sub_agent>")

    if sub_agents.warnings:
        lines.append("子 Agent 角色加载告警：")
        for warning in sub_agents.warnings:
            lines.append(f"- {warning.source_path}: {warning.message}")
    lines.append("</mewcode_sub_agents>")
    return lines


def _team_context_lines(context: RuntimePromptContext) -> list[str]:
    team = context.team_context
    if team is None:
        return []
    lines = [
        f'<mewcode_team name="{team.team_name}" actor="{team.actor_name}" kind="{team.actor_kind}">',
        f"未读团队消息：{team.unread_count}",
    ]
    if team.roster:
        lines.append("团队成员：")
        for member in team.roster:
            lines.append(
                f"- {member.name}: role={member.role} status={member.status} "
                f"task={member.current_task_id or '无'}"
            )
    if team.tasks:
        lines.append("共享任务：")
        for task in team.tasks:
            lines.append(
                f"- {task.id}: {task.title} status={task.status} assignee={task.assignee or '无'} "
                f"depends={','.join(task.dependencies) or '无'}"
            )
    if team.actor_kind == "lead":
        lines.extend(
            (
                "Lead 约束：先把用户目标拆成带依赖任务写入共享清单，再派生成员。",
                "有未完成任务时使用 team_wait 等待事件并处理审批、失败和依赖解锁。",
                "全部任务完成后汇总成员分支；本阶段不得声称已经自动合并。",
            )
        )
    else:
        lines.extend(
            (
                "成员约束：只处理共享清单中已领取的任务，不得派生或管理其他成员。",
                "通过 team_message 与 Lead 或其他成员直接协作，结束前更新任务状态。",
            )
        )
        if team.current_task is not None:
            lines.append(
                f"当前任务：{team.current_task.id} {team.current_task.title} status={team.current_task.status}"
            )
        if team.current_approval is not None:
            lines.append(
                f"当前审批：id={team.current_approval.id} version={team.current_approval.plan_version} "
                f"status={team.current_approval.status}"
            )
        if team.role_body:
            lines.append("成员角色提示：")
            lines.append(team.role_body)
    lines.append("</mewcode_team>")
    return lines


def _context_summary_lines(context: RuntimePromptContext) -> list[str]:
    summary = context.context_summary
    if summary is None:
        return []
    lines = [
        "<mewcode_context_summary>",
        "正式摘要：",
        summary.content,
        "边界提示：",
        summary.boundary_notice,
    ]
    if summary.external_paths:
        lines.append("外置内容路径：")
        lines.extend(summary.external_paths)
    lines.append("</mewcode_context_summary>")
    return lines


def _project_instruction_lines(context: RuntimePromptContext) -> list[str]:
    knowledge = context.knowledge_context
    if knowledge is None:
        return []

    lines: list[str] = []
    if knowledge.instructions.blocks:
        lines.append("<mewcode_project_instructions>")
        for block in knowledge.instructions.blocks:
            lines.append(f"来源：scope={block.scope} priority={block.priority} path={block.source_path}")
            lines.append(block.content)
        lines.append("</mewcode_project_instructions>")
    return lines


def _dynamic_knowledge_context_lines(context: RuntimePromptContext) -> list[str]:
    knowledge = context.knowledge_context
    if knowledge is None:
        return []

    lines: list[str] = []

    restore_lines: list[str] = []
    report = knowledge.restore_report
    if report is not None:
        if report.time_gap_notice:
            restore_lines.append(report.time_gap_notice)
        if report.started_empty_reason:
            restore_lines.append(report.started_empty_reason)
        restore_lines.extend(report.warnings)
    restore_lines.extend(knowledge.instructions.warnings)
    if restore_lines:
        lines.append("<mewcode_restore_notice>")
        lines.extend(restore_lines)
        lines.append("</mewcode_restore_notice>")
    return lines


def _hook_injection_lines(context: RuntimePromptContext) -> list[str]:
    if not context.hook_injections:
        return []
    lines = ["<mewcode_hook_instructions>"]
    for injection in context.hook_injections:
        lines.append(f"来源：{injection.rule_id}")
        lines.append(injection.text)
    lines.append("</mewcode_hook_instructions>")
    return lines


def _mode_lines(context: RuntimePromptContext, level: RuntimeInstructionLevel) -> list[str]:
    if context.mode == "plan":
        return _plan_mode_lines(level)
    return _normal_mode_lines(level)


def _normal_mode_lines(level: RuntimeInstructionLevel) -> list[str]:
    if level == "full":
        return [
            "本轮约束：普通模式。先读取或搜索现状，再选择合适工具执行；修改后用检查或测试验证。",
            "不要把本补充块当作用户输入直接回复。",
        ]
    if level == "refresh":
        return ["本轮约束：继续遵守先读后改、专用工具优先和完成后验证。"]
    return ["本轮约束：普通模式，保持工具规则。"]


def _plan_mode_lines(level: RuntimeInstructionLevel) -> list[str]:
    if level == "full":
        return [
            "本轮约束：规划模式。只能使用读取、查找和搜索类工具了解现状。",
            "禁止写入文件、修改文件、执行命令或触发其他有副作用的工具。",
            "输出目标是清晰可执行的计划，不要执行计划中的修改或命令动作。",
        ]
    if level == "refresh":
        return [
            "本轮约束：规划模式仍只允许读类工具；禁止写入、修改和命令执行。",
            "如果工具受限失败，应调整为读取、查找或搜索后继续规划。",
        ]
    return ["本轮约束：规划模式，只读，不执行计划。"]
