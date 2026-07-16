from __future__ import annotations

from pathlib import Path

from mewcode.context.models import ContextSummary
from mewcode.hooks.models import HookPromptInjection
from mewcode.memory.models import InstructionBlock, InstructionBundle, KnowledgeContext, MemoryIndex, RestoreReport
from mewcode.mcp.search import McpPromptContext, McpServerToolSummary
from mewcode.prompting.builder import PromptBuilder, runtime_instruction_level
from mewcode.prompting.base import GeneratedContextBlock, PromptBundle, RuntimePromptContext
from mewcode.prompting.modules import stable_prompt_modules
from mewcode.subagents.models import (
    ActiveSubAgentPrompt,
    SubAgentBackgroundSummary,
    SubAgentPromptContext,
    SubAgentRoleSummary,
    SubAgentRoleWarning,
)
from mewcode.tools.base import ToolSpec
from mewcode.teams.models import MemberSummary, TaskSummary, TeamPromptContext


def tool_spec(name: str, safety: str = "read_only") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        parameters_schema={"type": "object", "properties": {}, "additionalProperties": False},
        safety=safety,  # type: ignore[arg-type]
    )


def runtime_context(
    *,
    mode: str = "normal",
    iteration: int = 1,
    context_summary: ContextSummary | None = None,
    cwd: Path = Path("/home/cui/mewcode"),
    knowledge_context: KnowledgeContext | None = None,
    hook_injections: tuple[HookPromptInjection, ...] = (),
    sub_agent_context: SubAgentPromptContext | None = None,
    team_context: TeamPromptContext | None = None,
    mcp_context: McpPromptContext | None = None,
) -> RuntimePromptContext:
    return RuntimePromptContext(
        cwd=cwd,
        mode=mode,  # type: ignore[arg-type]
        iteration=iteration,
        max_iterations=8,
        allowed_tools=(tool_spec("read_file"), tool_spec("write_file", "side_effect")),
        source_request="整理 README",
        context_summary=context_summary,
        knowledge_context=knowledge_context,
        hook_injections=hook_injections,
        sub_agent_context=sub_agent_context,
        team_context=team_context,
        mcp_context=mcp_context,
    )


def runtime_blocks(context: RuntimePromptContext):
    return PromptBuilder().build_runtime_prompt(context)


def runtime_dynamic_text(context: RuntimePromptContext) -> str:
    return runtime_blocks(context)[-1].text


def runtime_cache_prefix_text(context: RuntimePromptContext) -> str:
    for block in runtime_blocks(context):
        if block.cacheable:
            return block.text
    return ""


def test_stable_modules_are_ordered_and_cacheable() -> None:
    modules = stable_prompt_modules()

    assert [module.name for module in modules] == [
        "identity",
        "system_constraints",
        "task_modes",
        "action_execution",
        "tool_usage",
        "tone_style",
        "text_output",
    ]
    assert [module.title for module in modules] == [
        "身份",
        "系统约束",
        "任务模式",
        "动作执行",
        "工具使用",
        "语气风格",
        "文本输出",
    ]
    assert all(module.stable for module in modules)
    assert [module.cacheable for module in modules] == [False, False, False, False, False, False, True]


def test_generated_context_block_preserves_ephemeral_untrusted_semantics() -> None:
    block = GeneratedContextBlock(
        name="repo_map",
        title="仓库地图",
        text="<mewcode_repo_map />",
        kind="repo_map",
        snapshot_id="snapshot-1",
    )
    bundle = PromptBundle(stable_blocks=(), runtime_blocks=(), generated_context_blocks=(block,))

    assert block.provenance == "generated"
    assert block.trust == "untrusted_repository_data"
    assert block.persistence == "request_ephemeral"
    assert block.cache_scope == "snapshot"
    assert bundle.generated_context_blocks == (block,)


def test_stable_modules_include_tool_rules() -> None:
    text = "\n".join(module.text for module in stable_prompt_modules())

    assert "优先使用专用工具" in text
    assert "编辑前先读取或搜索目标文件" in text
    assert "write_file 会创建或覆盖完整文件" in text
    assert "run_command 会执行本地命令" in text
    assert "工具失败结果是下一步决策依据" in text


def test_stable_prompt_is_deterministic_and_has_no_empty_optional_sections() -> None:
    builder = PromptBuilder()
    first = builder.build_stable_prompt()
    second = builder.build_stable_prompt()
    text = "\n\n".join(block.text for block in first)

    assert first == second
    assert "TBD" not in text
    assert "TODO" not in text
    assert "自定义指令：" not in text
    assert "长期记忆：" not in text
    assert "已激活的 Skill：" not in text


def test_runtime_prompt_uses_tagged_context() -> None:
    blocks = runtime_blocks(runtime_context(mode="normal"))
    prefix = next(block for block in blocks if block.cacheable)
    block = blocks[-1]

    assert block.stable is False
    assert block.cacheable is False
    assert prefix.stable is False
    assert prefix.cacheable is True
    assert "<mewcode_runtime_context>" in block.text
    assert "</mewcode_runtime_context>" in block.text
    assert "环境信息：cwd=/home/cui/mewcode" in block.text
    assert "模式状态：normal full 1/8" in block.text
    assert "允许工具：read_file(read_only), write_file(side_effect)" in prefix.text
    assert "允许工具：" not in block.text


def test_runtime_prompt_includes_compact_mcp_server_summary() -> None:
    text = runtime_dynamic_text(
        runtime_context(
            mcp_context=McpPromptContext(
                connected_servers=(McpServerToolSummary("github", 45), McpServerToolSummary("demo", 2)),
            )
        )
    )

    assert "<mewcode_mcp>" in text
    assert "search_mcp_tools" in text
    assert "github(45)" in text
    assert "demo(2)" in text
    assert "github__get_me" not in text
    assert "inputSchema" not in text


def test_runtime_prompt_omits_mcp_block_without_context() -> None:
    assert "<mewcode_mcp>" not in runtime_dynamic_text(runtime_context())


def test_runtime_prompt_uses_full_refresh_and_brief_levels() -> None:
    assert runtime_instruction_level(1) == "full"
    assert runtime_instruction_level(2) == "brief"
    assert runtime_instruction_level(3) == "refresh"
    assert runtime_instruction_level(4) == "brief"
    assert runtime_instruction_level(6) == "refresh"

    full = runtime_dynamic_text(runtime_context(iteration=1))
    brief = runtime_dynamic_text(runtime_context(iteration=2))
    refresh = runtime_dynamic_text(runtime_context(iteration=3))

    assert "normal full 1/8" in full
    assert "先读取或搜索现状" in full
    assert "normal brief 2/8" in brief
    assert "保持工具规则" in brief
    assert "normal refresh 3/8" in refresh
    assert "先读后改" in refresh


def test_plan_mode_runtime_prompt_levels() -> None:
    full = runtime_dynamic_text(runtime_context(mode="plan", iteration=1))
    brief = runtime_dynamic_text(runtime_context(mode="plan", iteration=2))
    refresh = runtime_dynamic_text(runtime_context(mode="plan", iteration=3))

    assert "规划模式。只能使用读取、查找和搜索类工具" in full
    assert "禁止写入文件、修改文件、执行命令" in full
    assert "plan brief 2/8" in brief
    assert "只读，不执行计划" in brief
    assert "plan refresh 3/8" in refresh
    assert "规划模式仍只允许读类工具" in refresh


def test_stable_prompt_is_deterministic_and_runtime_changes_are_separate() -> None:
    builder = PromptBuilder()
    first_bundle = builder.build_bundle(runtime_context(cwd=Path("/tmp/one"), mode="normal", iteration=1))
    second_bundle = builder.build_bundle(runtime_context(cwd=Path("/tmp/two"), mode="plan", iteration=2))

    assert first_bundle.stable_blocks == second_bundle.stable_blocks
    assert "/tmp/one" not in "\n".join(block.text for block in first_bundle.stable_blocks)
    assert "/tmp/two" not in "\n".join(block.text for block in second_bundle.stable_blocks)
    assert "/tmp/one" not in "\n".join(block.text for block in first_bundle.runtime_blocks if block.cacheable)
    assert "/tmp/two" not in "\n".join(block.text for block in second_bundle.runtime_blocks if block.cacheable)
    assert "/tmp/one" in first_bundle.runtime_blocks[-1].text
    assert "/tmp/two" in second_bundle.runtime_blocks[-1].text


def test_runtime_prompt_includes_context_summary() -> None:
    summary = ContextSummary(
        content="当前目标：继续实现上下文管理",
        boundary_notice="需要细节时重新读取路径",
        created_at="now",
        source_message_count=3,
        kept_message_count=2,
    )

    block_text = runtime_dynamic_text(runtime_context(context_summary=summary))

    assert "<mewcode_context_summary>" in block_text
    assert "当前目标：继续实现上下文管理" in block_text


def test_runtime_prompt_includes_sub_agent_summaries() -> None:
    sub_agent_context = SubAgentPromptContext(
        available_roles=(SubAgentRoleSummary("reviewer", "审查代码", "builtin"),),
        background=(
            SubAgentBackgroundSummary(
                task_id="subagent-1",
                type="fork",
                role=None,
                status="completed",
                task="检查测试",
                summary="测试覆盖正常",
                stop_reason="completed",
            ),
        ),
        warnings=(SubAgentRoleWarning("坏角色已跳过", "bad.md"),),
    )

    block_text = runtime_dynamic_text(runtime_context(sub_agent_context=sub_agent_context))

    assert "<mewcode_sub_agents>" in block_text
    assert "delegate_agent" in block_text
    assert "- reviewer: 审查代码" in block_text
    assert "subagent-1" in block_text
    assert "测试覆盖正常" in block_text
    assert "bad.md: 坏角色已跳过" in block_text


def test_runtime_prompt_includes_active_defined_sub_agent_role_body() -> None:
    sub_agent_context = SubAgentPromptContext(
        active=ActiveSubAgentPrompt(
            task_id="subagent-2",
            type="defined",
            role_name="reviewer",
            role_description="审查代码",
            role_body="只审查，不修改。",
            task="审查 src",
        )
    )

    block_text = runtime_dynamic_text(runtime_context(sub_agent_context=sub_agent_context))

    assert '<active_sub_agent id="subagent-2" type="defined" role="reviewer"' in block_text
    assert "非交互跑到底" in block_text
    assert "不要再次委派子 Agent" in block_text
    assert "只审查，不修改。" in block_text


def test_runtime_prompt_includes_active_fork_constraints() -> None:
    sub_agent_context = SubAgentPromptContext(
        active=ActiveSubAgentPrompt(
            task_id="subagent-3",
            type="fork",
            role_name=None,
            role_description=None,
            role_body=None,
            task="继续调查",
        )
    )

    block_text = runtime_dynamic_text(runtime_context(sub_agent_context=sub_agent_context))

    assert 'type="fork" role="fork"' in block_text
    assert "Fork 约束" in block_text


def test_runtime_prompt_includes_sub_agent_worktree_paths_and_boundary() -> None:
    sub_agent_context = SubAgentPromptContext(
        active=ActiveSubAgentPrompt(
            task_id="subagent-4",
            type="defined",
            role_name="writer",
            role_description="修改代码",
            role_body="完成指定修改。",
            task="修改 src",
            isolation="worktree",
            cwd=Path("/repo/.mewcode/worktrees/writer/subagent-4"),
            main_cwd=Path("/repo"),
            branch="mewcode/writer/subagent-4",
        )
    )

    block_text = runtime_dynamic_text(runtime_context(sub_agent_context=sub_agent_context))

    assert 'isolation="worktree"' in block_text
    assert "Worktree 隔离目录：/repo/.mewcode/worktrees/writer/subagent-4" in block_text
    assert "主 Agent 工作目录：/repo" in block_text
    assert "Worktree 分支：mewcode/writer/subagent-4" in block_text
    assert "不得访问主 Agent 工作目录" in block_text


def test_runtime_prompt_includes_context_boundary_notice() -> None:
    summary = ContextSummary(
        content="摘要",
        boundary_notice="不能按摘要脑补代码",
        created_at="now",
        source_message_count=3,
        kept_message_count=2,
        external_paths=(".mewcode/context/a.json",),
    )

    block_text = runtime_dynamic_text(runtime_context(context_summary=summary))

    assert "不能按摘要脑补代码" in block_text
    assert ".mewcode/context/a.json" in block_text


def test_team_prompt_renders_lead_roster_tasks_and_constraints() -> None:
    team = TeamPromptContext(
        "demo",
        "lead",
        "lead",
        (MemberSummary("worker", "reviewer", "running", "task-1"),),
        (TaskSummary("task-1", "实现功能", "in_progress", "worker", ()),),
        2,
    )

    block_text = runtime_dynamic_text(runtime_context(team_context=team))

    assert '<mewcode_team name="demo" actor="lead"' in block_text
    assert "worker: role=reviewer status=running task=task-1" in block_text
    assert "task-1: 实现功能 status=in_progress assignee=worker" in block_text
    assert "先把用户目标拆成带依赖任务写入共享清单" in block_text
    assert "不得声称已经自动合并" in block_text


def test_team_prompt_renders_member_role_body_and_constraints() -> None:
    team = TeamPromptContext(
        "demo",
        "member",
        "worker",
        (MemberSummary("worker", "reviewer", "idle", None),),
        (),
        0,
        role_body="只审查，不修改无关文件。",
    )

    block_text = runtime_dynamic_text(runtime_context(team_context=team))

    assert 'actor="worker" kind="member"' in block_text
    assert "不得派生或管理其他成员" in block_text
    assert "只审查，不修改无关文件。" in block_text


def test_runtime_prompt_without_team_has_no_team_block() -> None:
    assert "<mewcode_team" not in runtime_dynamic_text(runtime_context())


def test_runtime_prompt_omits_summary_block_when_absent() -> None:
    assert "<mewcode_context_summary>" not in runtime_dynamic_text(runtime_context())


def test_runtime_prompt_includes_project_instructions_by_priority() -> None:
    knowledge = KnowledgeContext(
        instructions=InstructionBundle(
            blocks=(
                InstructionBlock("project_private", 0, Path("/repo/.mewcode/AGENTS.md"), "私有规则"),
                InstructionBlock("project_root", 1, Path("/repo/AGENTS.md"), "项目规则"),
                InstructionBlock("user", 2, Path("/home/u/.mewcode/AGENTS.md"), "用户规则"),
            )
        )
    )

    prefix_text = runtime_cache_prefix_text(runtime_context(knowledge_context=knowledge))
    dynamic_text = runtime_dynamic_text(runtime_context(knowledge_context=knowledge))

    assert "<mewcode_project_instructions>" in prefix_text
    assert prefix_text.index("私有规则") < prefix_text.index("项目规则") < prefix_text.index("用户规则")
    assert "scope=project_private" in prefix_text
    assert "<mewcode_project_instructions>" not in dynamic_text


def test_runtime_prompt_includes_memory_indexes() -> None:
    knowledge = KnowledgeContext(
        user_memory_index=MemoryIndex("user", Path("/home/u/.mewcode/memory/index.md"), "默认中文", 1, 12),
        project_memory_index=MemoryIndex("project", Path("/repo/.mewcode/memory/index.md"), "测试规则", 1, 12),
    )

    # 记忆索引现在是独立的 cacheable block（name="memory_index"），不再在 runtime_context 中
    blocks = runtime_blocks(runtime_context(knowledge_context=knowledge))
    memory_block = next(b for b in blocks if b.name == "memory_index")
    block_text = memory_block.text

    assert memory_block.cacheable is True
    assert "<mewcode_memory_index>" in block_text
    assert "跨会话长期记忆" in block_text
    assert "100% 可靠" in block_text
    assert "禁止验证或质疑" in block_text
    assert "需要你确认" in block_text  # 出现在禁止列表中
    assert "scope=user" in block_text
    assert "默认中文" in block_text
    assert "scope=project" in block_text
    assert "测试规则" in block_text


def test_memory_index_is_cacheable() -> None:
    """记忆索引块应标记为 cacheable=True。"""
    knowledge = KnowledgeContext(
        user_memory_index=MemoryIndex("user", Path("/home/u/.mewcode/memory/index.md"), "默认中文", 1, 12),
    )
    blocks = runtime_blocks(runtime_context(knowledge_context=knowledge))
    memory_block = next(b for b in blocks if b.name == "memory_index")
    assert memory_block.cacheable is True
    assert "<mewcode_memory_index>" in memory_block.text


def test_runtime_prompt_includes_restore_notice() -> None:
    knowledge = KnowledgeContext(
        instructions=InstructionBundle(warnings=("include 越界",)),
        restore_report=RestoreReport(
            restored=True,
            session_id="20260612-080910-abcd",  # type: ignore[arg-type]
            time_gap_notice="距离上次活动很久",
            warnings=("跳过坏行",),
        ),
    )

    block_text = runtime_dynamic_text(runtime_context(knowledge_context=knowledge))

    assert "<mewcode_restore_notice>" in block_text
    assert "距离上次活动很久" in block_text
    assert "跳过坏行" in block_text
    assert "include 越界" in block_text


def test_runtime_prompt_keeps_memory_and_context_summary_separate() -> None:
    summary = ContextSummary(
        content="上下文摘要",
        boundary_notice="不能脑补",
        created_at="now",
        source_message_count=2,
        kept_message_count=1,
    )
    knowledge = KnowledgeContext(
        project_memory_index=MemoryIndex("project", Path("/repo/.mewcode/memory/index.md"), "长期记忆", 1, 12),
    )

    ctx = runtime_context(context_summary=summary, knowledge_context=knowledge)
    memory_block = next(b for b in runtime_blocks(ctx) if b.name == "memory_index")
    dynamic_text = runtime_dynamic_text(ctx)

    assert "<mewcode_memory_index>" in memory_block.text  # 记忆在独立 cacheable 块
    assert "<mewcode_context_summary>" in dynamic_text  # 上下文摘要在动态区


def test_prompt_builder_includes_hook_injections() -> None:
    block_text = runtime_dynamic_text(
        runtime_context(hook_injections=(HookPromptInjection("hook-1", "必须先说明 Hook 上下文"),))
    )

    assert "<mewcode_hook_instructions>" in block_text
    assert "来源：hook-1" in block_text
    assert "必须先说明 Hook 上下文" in block_text


def test_prompt_builder_omits_hook_injections_when_absent() -> None:
    assert "<mewcode_hook_instructions>" not in runtime_dynamic_text(runtime_context())
