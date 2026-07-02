from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from mewcode.commands import AgentCommand
from mewcode.config import AppConfig
from mewcode.context.models import ContextConfig
from mewcode.permissions.models import PermissionConfig
from mewcode.providers.base import ChatMessage, ChatRequest, StreamEvent, TokenUsage
from mewcode.session import ChatSession
from mewcode.subagents.loader import SubAgentRoleLoader, default_sub_agent_roots
from mewcode.subagents.manager import SubAgentManager, _completion_notice
from mewcode.subagents.models import (
    BackgroundSubAgentRecord,
    ParentAgentContext,
    SubAgentConfig,
    SubAgentInvocation,
    SubAgentResult,
    SubAgentRoleDefinition,
    SubAgentRoleFrontmatter,
    SubAgentRoleRoots,
    SubAgentToolFilter,
    SubAgentWorkingContext,
    SubAgentWorktreeInfo,
)
from mewcode.subagents.runtime import SubAgentRunnerFactory
from mewcode.subagents.tools import DELEGATE_AGENT_TOOL_NAME, DelegateAgentTool
from mewcode.subagents.tools import _result_payload
from mewcode.tools.base import ToolCall, ToolContext, ToolExecutionError, ToolSpec
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry, create_default_registry
from mewcode.tools.scheduler import ToolPolicy
from mewcode.worktrees import WorktreeConfig, WorktreeDisposition, WorktreeLease, WorktreeMetadata


class FakeProvider:
    def __init__(self, content: str = "子任务已完成") -> None:
        self.content = content
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        await asyncio.sleep(0)
        yield StreamEvent(type="usage", usage=TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3))
        yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content=self.content))


class FakeTool:
    def __init__(self, name: str, *, safety: str = "read_only", visibility: str = "model") -> None:
        self.spec = ToolSpec(
            name=name,
            description=name,
            parameters_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            safety=safety,  # type: ignore[arg-type]
            visibility=visibility,  # type: ignore[arg-type]
        )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = arguments, context
        return {"name": self.spec.name}


class StubDelegateManager:
    def __init__(self) -> None:
        self.invocations: list[SubAgentInvocation] = []

    async def delegate(self, invocation: SubAgentInvocation):
        self.invocations.append(invocation)
        if invocation.background:
            return BackgroundSubAgentRecord(
                task_id="subagent-bg",
                invocation=invocation,
                status="background",
                created_at=0.0,
            )
        return SubAgentResult(
            task_id="subagent-fg",
            type=invocation.type,
            role=invocation.role,
            status="completed",
            task=invocation.task,
            summary="完成",
            final_text="完成",
            stop_reason="completed",
        )


def test_delegate_agent_tool_schema_is_stable() -> None:
    tool = DelegateAgentTool(StubDelegateManager())

    assert tool.spec.name == DELEGATE_AGENT_TOOL_NAME
    assert set(tool.spec.parameters_schema["properties"]) == {
        "type",
        "task",
        "role",
        "background",
        "max_iterations",
        "foreground_timeout_seconds",
    }
    assert tool.spec.parameters_schema["required"] == ["type", "task"]


@pytest.mark.asyncio
async def test_delegate_agent_tool_rejects_invalid_arguments(tmp_path: Path) -> None:
    manager = StubDelegateManager()
    tool = DelegateAgentTool(manager)

    with pytest.raises(ToolExecutionError, match="type 必须"):
        await tool.execute({"type": "bad", "task": "x"}, ToolContext(cwd=tmp_path))
    with pytest.raises(ToolExecutionError, match="defined 类型必须提供 role"):
        await tool.execute({"type": "defined", "task": "x"}, ToolContext(cwd=tmp_path))
    with pytest.raises(ToolExecutionError, match="task 不能为空"):
        await tool.execute({"type": "fork", "task": "   "}, ToolContext(cwd=tmp_path))

    assert manager.invocations == []


@pytest.mark.asyncio
async def test_delegate_agent_tool_routes_defined_and_forces_fork_background(tmp_path: Path) -> None:
    manager = StubDelegateManager()
    tool = DelegateAgentTool(manager)

    defined = await tool.execute(
        {"type": "defined", "role": "reviewer", "task": "审查", "max_iterations": 2},
        ToolContext(cwd=tmp_path),
    )
    fork = await tool.execute(
        {"type": "fork", "task": "继续调查", "background": False},
        ToolContext(cwd=tmp_path),
    )

    assert defined["background"] is False
    assert defined["role"] == "reviewer"
    assert fork["background"] is True
    assert manager.invocations[0].max_iterations == 2
    assert manager.invocations[1].type == "fork"
    assert manager.invocations[1].background is True


def test_role_loader_uses_project_user_builtin_plugin_priority(tmp_path: Path) -> None:
    project = tmp_path / "project"
    user = tmp_path / "user"
    builtin = tmp_path / "builtin"
    plugin = tmp_path / "plugin"
    for root in (project, user, builtin, plugin):
        root.mkdir()
    write_role(plugin / "reviewer.md", "reviewer", "plugin 角色")
    write_role(builtin / "reviewer.md", "reviewer", "builtin 角色")
    write_role(user / "reviewer.md", "reviewer", "user 角色")
    write_role(project / "reviewer.md", "reviewer", "project 角色")
    write_role(project / "duplicate-a.md", "duplicated", "先出现")
    write_role(project / "duplicate-b.md", "duplicated", "后出现")

    catalog = SubAgentRoleLoader(
        SubAgentRoleRoots(project=project, user=user, builtin=builtin, plugins=(plugin,))
    ).discover()

    assert catalog.definitions["reviewer"].source_scope == "project"
    assert catalog.definitions["reviewer"].description == "project 角色"
    assert catalog.definitions["duplicated"].source_path.endswith("duplicate-a.md")
    assert any("同一层级重复定义" in warning.message for warning in catalog.warnings)


def test_builtin_roles_use_40_max_iterations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    catalog = SubAgentRoleLoader(default_sub_agent_roots(tmp_path)).discover()

    assert catalog.definitions["reviewer"].frontmatter.max_iterations == 40
    assert catalog.definitions["code-searcher"].frontmatter.max_iterations == 40


def test_role_loader_parses_worktree_isolation_and_defaults_shared(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_role(project / "shared.md", "shared", "共享角色")
    write_role(project / "isolated.md", "isolated", "隔离角色", isolation="worktree")

    catalog = SubAgentRoleLoader(
        SubAgentRoleRoots(project=project, user=tmp_path / "user", builtin=tmp_path / "builtin")
    ).discover()

    assert catalog.definitions["shared"].frontmatter.isolation == "shared"
    assert catalog.definitions["isolated"].frontmatter.isolation == "worktree"


@pytest.mark.parametrize("value", ("shared", "invalid", "", "true", "null"))
def test_role_loader_rejects_invalid_isolation(tmp_path: Path, value: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_role(project / "bad.md", "bad", "坏角色", isolation=value)

    catalog = SubAgentRoleLoader(
        SubAgentRoleRoots(project=project, user=tmp_path / "user", builtin=tmp_path / "builtin")
    ).discover()

    assert "bad" not in catalog.definitions
    assert any("frontmatter.isolation" in warning.message for warning in catalog.warnings)


def test_tool_policy_applies_sub_agent_filter_layers() -> None:
    registry = ToolRegistry()
    for tool in (
        FakeTool("read_file"),
        FakeTool("write_file", safety="side_effect"),
        FakeTool("delegate_agent", safety="side_effect"),
        FakeTool("system_tool", visibility="system"),
    ):
        registry.register(tool)
    tool_filter = SubAgentToolFilter(
        inherited_tools=frozenset({"read_file", "write_file", "system_tool"}),
        role_allow=frozenset({"read_file", "write_file", "system_tool"}),
        role_deny=frozenset({"write_file"}),
        global_blocked=frozenset({"delegate_agent"}),
        background_allowed=frozenset({"read_file"}),
    )

    policy = ToolPolicy("normal", filter=tool_filter)

    assert {spec.name for spec in policy.allowed_specs(registry)} == {"read_file", "system_tool"}
    write_denial = policy.validate_call(call("c1", "write_file"), registry)
    nested_denial = policy.validate_call(call("c2", "delegate_agent"), registry)
    assert write_denial is not None and write_denial.error_type == "tool_not_allowed"
    assert nested_denial is not None
    assert "不允许再次委派" in (nested_denial.error or "")


def test_runner_factory_defined_and_fork_modes_are_isolated(tmp_path: Path) -> None:
    registry = create_default_registry()
    provider = FakeProvider()
    requested_models: list[str | None] = []
    config = make_app_config(
        tmp_path,
        sub_agents=SubAgentConfig(
            default_max_iterations=5,
            background_allowed_tools=("read_file",),
            model_aliases={"haiku": "cheap-model"},
        ),
    )
    factory = SubAgentRunnerFactory(
        registry=registry,
        executor=ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        config=config,
        provider=provider,
        provider_resolver=lambda model: requested_models.append(model) or provider,
    )
    role = SubAgentRoleDefinition(
        frontmatter=SubAgentRoleFrontmatter(
            name="reviewer",
            description="审查代码",
            tools_allow=("read_file", "write_file"),
            tools_deny=("write_file",),
            model="haiku",
            max_iterations=2,
            permission_mode="strict",
        ),
        body="只做审查。",
        source_scope="project",
        source_path="reviewer.md",
    )
    parent_session = ChatSession()
    parent_session.append_user_message("父消息")
    parent_session.append_assistant_message(
        ChatMessage(role="assistant", content="", tool_calls=(call("tool-call", "read_file"),))
    )
    parent = ParentAgentContext(
        session=parent_session,
        mode="normal",
        command=AgentCommand(mode="normal", visible_text="父请求", model_text="父请求"),
        allowed_tools=registry.specs(),
        tool_whitelist=None,
    )

    defined_runner, defined_command, defined_session = factory.create_runner(
        task_id="defined-1",
        invocation=SubAgentInvocation(type="defined", role="reviewer", task="审查 src"),
        parent=parent,
        role=role,
        background=False,
        working_context=SubAgentWorkingContext(
            cwd=tmp_path,
            main_cwd=tmp_path,
            isolation="shared",
        ),
    )
    fork_runner, _fork_command, fork_session = factory.create_runner(
        task_id="fork-1",
        invocation=SubAgentInvocation(type="fork", task="继续调查"),
        parent=parent,
        role=None,
        background=True,
        working_context=SubAgentWorkingContext(
            cwd=tmp_path,
            main_cwd=tmp_path,
            isolation="shared",
        ),
    )

    assert defined_command.model_text == "审查 src"
    assert defined_session.messages == []
    assert defined_runner.config.max_iterations == 2
    assert requested_models[0] == "cheap-model"
    assert defined_runner.tool_filter is not None
    assert defined_runner.tool_filter.role_allow == frozenset({"read_file", "write_file"})
    assert defined_runner.tool_filter.role_deny == frozenset({"write_file"})
    assert [message.content for message in fork_session.messages] == ["父消息"]
    assert fork_runner.config.max_iterations == 5
    assert fork_runner.tool_filter is not None
    assert fork_runner.tool_filter.background_allowed == frozenset({"read_file"})
    assert defined_runner.executor.context.cwd == tmp_path.resolve()
    assert defined_runner.context_manager.cwd == tmp_path.resolve()
    assert defined_runner.memory_manager is not None
    assert defined_runner.memory_manager.cwd == tmp_path.resolve()


def test_runner_factory_max_iteration_priority_defaults_to_40(tmp_path: Path) -> None:
    registry = create_default_registry()
    provider = FakeProvider()
    default_factory = SubAgentRunnerFactory(
        registry=registry,
        executor=ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        config=make_app_config(tmp_path),
        provider=provider,
        provider_resolver=lambda _model: provider,
    )
    configured_factory = SubAgentRunnerFactory(
        registry=registry,
        executor=ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        config=make_app_config(tmp_path, sub_agents=SubAgentConfig(default_max_iterations=5)),
        provider=provider,
        provider_resolver=lambda _model: provider,
    )
    role = SubAgentRoleDefinition(
        frontmatter=SubAgentRoleFrontmatter(
            name="reviewer",
            description="审查代码",
            tools_allow=("read_file",),
            max_iterations=2,
        ),
        body="只做审查。",
        source_scope="project",
        source_path="reviewer.md",
    )

    assert default_factory._agent_config(
        SubAgentInvocation(type="fork", task="调查"),
        None,
    ).max_iterations == 40
    assert configured_factory._agent_config(
        SubAgentInvocation(type="defined", role="reviewer", task="审查"),
        role,
    ).max_iterations == 2
    assert configured_factory._agent_config(
        SubAgentInvocation(type="defined", role="reviewer", task="审查", max_iterations=3),
        role,
    ).max_iterations == 3


def test_working_context_and_worktree_result_defaults_are_compatible(tmp_path: Path) -> None:
    context = SubAgentWorkingContext(cwd=tmp_path, main_cwd=tmp_path, isolation="shared")
    info = SubAgentWorktreeInfo(
        root=str(tmp_path / "worktree"),
        cwd=str(tmp_path / "worktree"),
        branch="mewcode/reviewer/task-1",
        base_commit="a" * 40,
        disposition="retained",
        reason="存在修改",
    )
    result = SubAgentResult(
        task_id="task-1",
        type="defined",
        role="reviewer",
        status="completed",
        task="审查",
        summary="完成",
        worktree=info,
    )

    assert context.cwd == tmp_path
    assert context.lease is None
    assert result.worktree == info


@pytest.mark.asyncio
async def test_manager_forces_fork_to_background_and_notifies_main_session(tmp_path: Path) -> None:
    registry = create_default_registry()
    provider = FakeProvider("后台结论\n- 发现 A")
    main_session = ChatSession()
    main_session.append_user_message("父问题")
    manager = SubAgentManager(
        roots=SubAgentRoleRoots(
            project=tmp_path / "agents",
            user=tmp_path / "user-agents",
            builtin=tmp_path / "builtin-agents",
        ),
        tool_registry=registry,
        executor=ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        config=make_app_config(tmp_path, sub_agents=SubAgentConfig(foreground_timeout_seconds=0.1)),
        provider=provider,
        provider_resolver=lambda _model: provider,
        hook_manager=None,
        main_session=main_session,
    )
    manager.bind_parent_context(
        ParentAgentContext(
            session=main_session,
            mode="normal",
            command=AgentCommand(mode="normal", visible_text="父问题", model_text="父问题"),
            allowed_tools=registry.specs(),
            tool_whitelist=None,
        )
    )

    record = await manager.delegate(SubAgentInvocation(type="fork", task="继续调查", background=False))

    assert isinstance(record, BackgroundSubAgentRecord)
    assert record.invocation.background is True
    assert record.status == "background"
    assert record.task is not None
    await record.task
    await asyncio.sleep(0)
    assert record.status == "completed"
    assert record.result is not None
    assert record.result.summary == "后台结论"
    assert main_session.messages[-1].role == "assistant"
    assert "子 Agent 任务完成" in main_session.messages[-1].content
    assert [message.content for message in provider.requests[0].messages] == ["父问题", "继续调查"]


class StubWorktreeManager:
    def __init__(self, tmp_path: Path) -> None:
        root = tmp_path / "worktree"
        root.mkdir()
        self.lease = WorktreeLease(
            metadata=WorktreeMetadata(
                version=1,
                repository_id="repo",
                task_id="placeholder",
                role="writer",
                relative_name="writer/placeholder",
                branch="mewcode/writer/placeholder",
                base_commit="a" * 40,
                created_at="2026-01-01T00:00:00+00:00",
            ),
            root=root,
            cwd=root,
            recovered=False,
        )
        self.acquire_calls: list[tuple[str, str]] = []
        self.finish_calls: list[WorktreeLease] = []

    async def acquire(self, *, task_id: str, role: str) -> WorktreeLease:
        self.acquire_calls.append((task_id, role))
        metadata = WorktreeMetadata(
            version=1,
            repository_id="repo",
            task_id=task_id,
            role=role,
            relative_name=f"{role}/{task_id}",
            branch=f"mewcode/{role}/{task_id}",
            base_commit="a" * 40,
            created_at="2026-01-01T00:00:00+00:00",
        )
        self.lease = WorktreeLease(metadata, self.lease.root, self.lease.cwd, False)
        return self.lease

    async def finish(self, lease: WorktreeLease) -> WorktreeDisposition:
        self.finish_calls.append(lease)
        return WorktreeDisposition(
            status="retained",
            root=lease.root,
            cwd=lease.cwd,
            branch=lease.metadata.branch,
            reason="存在未提交修改",
        )


class StubJanitor:
    def __init__(self) -> None:
        self.started = 0
        self.closed = 0

    def start(self) -> None:
        self.started += 1

    async def close(self) -> None:
        self.closed += 1


def isolated_role() -> SubAgentRoleDefinition:
    return SubAgentRoleDefinition(
        frontmatter=SubAgentRoleFrontmatter(
            name="writer",
            description="写代码",
            tools_allow=("read_file", "write_file"),
            isolation="worktree",
        ),
        body="完成任务。",
        source_scope="project",
        source_path="writer.md",
    )


@pytest.mark.asyncio
async def test_manager_worktree_lifecycle_finishes_completed_task(tmp_path: Path) -> None:
    registry = create_default_registry()
    provider = FakeProvider("隔离任务完成")
    session = ChatSession()
    worktrees = StubWorktreeManager(tmp_path)
    janitor = StubJanitor()
    manager = SubAgentManager(
        roots=SubAgentRoleRoots(project=tmp_path / "agents", user=tmp_path / "user", builtin=tmp_path / "builtin"),
        tool_registry=registry,
        executor=ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        config=make_app_config(tmp_path),
        provider=provider,
        provider_resolver=lambda _model: provider,
        hook_manager=None,
        main_session=session,
        worktree_manager=worktrees,  # type: ignore[arg-type]
        worktree_janitor=janitor,  # type: ignore[arg-type]
    )
    manager.catalog.definitions["writer"] = isolated_role()
    manager.bind_parent_context(
        ParentAgentContext(
            session=session,
            mode="normal",
            command=AgentCommand(mode="normal", visible_text="父任务", model_text="父任务"),
            allowed_tools=registry.specs(),
            tool_whitelist=None,
        )
    )

    result = await manager.delegate(SubAgentInvocation(type="defined", role="writer", task="修改文件"))

    assert isinstance(result, SubAgentResult)
    assert result.status == "completed"
    assert result.worktree is not None
    assert result.worktree.disposition == "retained"
    assert result.worktree.root == str(worktrees.lease.root)
    assert len(worktrees.acquire_calls) == 1
    assert worktrees.finish_calls == [worktrees.lease]
    assert provider.requests[0].prompt is not None
    assert str(worktrees.lease.cwd) in provider.requests[0].prompt.runtime_blocks[-1].text


@pytest.mark.asyncio
async def test_manager_worktree_lifecycle_finishes_runner_factory_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = create_default_registry()
    provider = FakeProvider()
    session = ChatSession()
    worktrees = StubWorktreeManager(tmp_path)
    manager = SubAgentManager(
        roots=SubAgentRoleRoots(project=tmp_path / "agents", user=tmp_path / "user", builtin=tmp_path / "builtin"),
        tool_registry=registry,
        executor=ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        config=make_app_config(tmp_path),
        provider=provider,
        provider_resolver=lambda _model: provider,
        hook_manager=None,
        main_session=session,
        worktree_manager=worktrees,  # type: ignore[arg-type]
        worktree_janitor=StubJanitor(),  # type: ignore[arg-type]
    )
    manager.catalog.definitions["writer"] = isolated_role()
    manager.bind_parent_context(
        ParentAgentContext(session, "normal", AgentCommand("normal", "父", "父"), registry.specs(), None)
    )
    monkeypatch.setattr(SubAgentRunnerFactory, "create_runner", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("factory boom")))

    result = await manager.delegate(SubAgentInvocation(type="defined", role="writer", task="修改"))

    assert isinstance(result, SubAgentResult)
    assert result.status == "failed"
    assert "factory boom" in (result.error or "")
    assert len(worktrees.finish_calls) == 1
    assert result.worktree is not None


def test_worktree_payload_and_completion_notice_include_disposition(tmp_path: Path) -> None:
    info = SubAgentWorktreeInfo(
        root=str(tmp_path / "worktree"),
        cwd=str(tmp_path / "worktree"),
        branch="mewcode/writer/task-1",
        base_commit="a" * 40,
        disposition="retained",
        reason="存在未提交修改",
    )
    result = SubAgentResult(
        task_id="task-1",
        type="defined",
        role="writer",
        status="completed",
        task="修改",
        summary="完成",
        worktree=info,
    )

    payload = _result_payload(result)
    notice = _completion_notice(result)

    assert payload["worktree"]["disposition"] == "retained"
    assert payload["worktree"]["branch"] == info.branch
    assert "Worktree：retained" in notice
    assert info.root in notice
    assert info.branch in notice


class WritingProvider:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        if request.messages and request.messages[-1].role == "tool":
            yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="隔离写入完成"))
            return
        task_text = request.messages[-1].content if request.messages else "task"
        yield StreamEvent(
            type="message_done",
            message=ChatMessage(
                role="assistant",
                tool_calls=(
                    ToolCall(
                        id=f"write-{len(self.requests)}",
                        name="write_file",
                        arguments={"path": "isolated.txt", "content": task_text},
                    ),
                ),
            ),
        )


def bind_parent(manager: SubAgentManager, session: ChatSession, registry: ToolRegistry) -> None:
    manager.bind_parent_context(
        ParentAgentContext(
            session=session,
            mode="normal",
            command=AgentCommand(mode="normal", visible_text="父任务", model_text="父任务"),
            allowed_tools=registry.specs(),
            tool_whitelist=None,
        )
    )


def real_worktree_manager(
    repository: Path,
    provider,
) -> tuple[SubAgentManager, ToolRegistry, ChatSession]:
    registry = create_default_registry()
    session = ChatSession()
    manager = SubAgentManager(
        roots=SubAgentRoleRoots(
            project=repository / ".mewcode/agents",
            user=repository / "user-agents",
            builtin=repository / "builtin-agents",
        ),
        tool_registry=registry,
        executor=ToolExecutor(registry, ToolContext(cwd=repository)),
        config=make_app_config(repository),
        provider=provider,
        provider_resolver=lambda _model: provider,
        hook_manager=None,
        main_session=session,
    )
    role = isolated_role()
    manager.catalog.definitions[role.name] = role
    bind_parent(manager, session, registry)
    return manager, registry, session


@pytest.mark.asyncio
async def test_real_git_isolation_keeps_main_directory_unchanged(tmp_path: Path) -> None:
    from tests.test_worktrees import init_repository

    repository = init_repository(tmp_path / "repo")
    (repository / "README.md").write_text("主目录未提交修改\n", encoding="utf-8")
    before_cwd = Path.cwd()
    provider = WritingProvider()
    manager, _registry, _session = real_worktree_manager(repository, provider)

    result = await manager.delegate(SubAgentInvocation(type="defined", role="writer", task="写入 child-one"))

    assert isinstance(result, SubAgentResult)
    assert result.status == "completed"
    assert result.worktree is not None
    assert result.worktree.disposition == "retained"
    worktree = Path(result.worktree.root)
    assert (worktree / "isolated.txt").read_text(encoding="utf-8") == "写入 child-one"
    assert not (repository / "isolated.txt").exists()
    assert (repository / "README.md").read_text(encoding="utf-8") == "主目录未提交修改\n"
    assert (worktree / "README.md").read_text(encoding="utf-8") == "base\n"
    assert Path.cwd() == before_cwd
    assert provider.requests[0].prompt is not None
    assert str(worktree) in provider.requests[0].prompt.runtime_blocks[-1].text


@pytest.mark.asyncio
async def test_real_git_isolation_parallel_worktrees_do_not_overlap(tmp_path: Path) -> None:
    from tests.test_worktrees import init_repository

    repository = init_repository(tmp_path / "repo")
    provider = WritingProvider()
    manager, _registry, _session = real_worktree_manager(repository, provider)

    first, second = await asyncio.gather(
        manager.delegate(SubAgentInvocation(type="defined", role="writer", task="内容 one")),
        manager.delegate(SubAgentInvocation(type="defined", role="writer", task="内容 two")),
    )

    assert isinstance(first, SubAgentResult) and first.worktree is not None
    assert isinstance(second, SubAgentResult) and second.worktree is not None
    assert first.worktree.root != second.worktree.root
    assert Path(first.worktree.root, "isolated.txt").read_text(encoding="utf-8") == "内容 one"
    assert Path(second.worktree.root, "isolated.txt").read_text(encoding="utf-8") == "内容 two"
    assert not (repository / "isolated.txt").exists()


@pytest.mark.asyncio
async def test_real_git_isolation_clean_task_is_removed(tmp_path: Path) -> None:
    from tests.test_worktrees import init_repository

    repository = init_repository(tmp_path / "repo")
    provider = FakeProvider("只读任务完成")
    manager, _registry, _session = real_worktree_manager(repository, provider)

    result = await manager.delegate(SubAgentInvocation(type="defined", role="writer", task="只读检查"))

    assert isinstance(result, SubAgentResult)
    assert result.worktree is not None
    assert result.worktree.disposition == "cleaned"
    assert not Path(result.worktree.root).exists()
    assert git_branch_missing(repository, result.worktree.branch)


@pytest.mark.asyncio
async def test_acquire_environment_error_keeps_main_running(tmp_path: Path) -> None:
    from tests.test_worktrees import init_repository

    repository = init_repository(tmp_path / "repo")
    provider = FakeProvider("后续共享任务完成")
    registry = create_default_registry()
    session = ChatSession()
    config = make_app_config(
        repository,
        sub_agents=SubAgentConfig(worktree=WorktreeConfig(copy_paths=("missing.local",))),
    )
    manager = SubAgentManager(
        roots=SubAgentRoleRoots(project=repository / "agents", user=repository / "user", builtin=repository / "builtin"),
        tool_registry=registry,
        executor=ToolExecutor(registry, ToolContext(cwd=repository)),
        config=config,
        provider=provider,
        provider_resolver=lambda _model: provider,
        hook_manager=None,
        main_session=session,
    )
    manager.catalog.definitions["writer"] = isolated_role()
    bind_parent(manager, session, registry)
    before = Path.cwd()

    failed = await manager.delegate(SubAgentInvocation(type="defined", role="writer", task="隔离失败"))

    assert isinstance(failed, SubAgentResult)
    assert failed.status == "failed"
    assert "environment" in (failed.error or "")
    assert provider.requests == []
    assert Path.cwd() == before

    manager.catalog.definitions["shared"] = SubAgentRoleDefinition(
        frontmatter=SubAgentRoleFrontmatter(
            name="shared",
            description="共享角色",
            tools_allow=("read_file",),
        ),
        body="完成共享任务。",
        source_scope="project",
        source_path="shared.md",
    )
    succeeded = await manager.delegate(SubAgentInvocation(type="defined", role="shared", task="继续"))
    assert isinstance(succeeded, SubAgentResult)
    assert succeeded.status == "completed"


class BlockingProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        _ = request
        self.started.set()
        await self.release.wait()
        yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="late"))


@pytest.mark.asyncio
async def test_manager_worktree_lifecycle_finishes_cancelled_task(tmp_path: Path) -> None:
    registry = create_default_registry()
    provider = BlockingProvider()
    session = ChatSession()
    worktrees = StubWorktreeManager(tmp_path)
    janitor = StubJanitor()
    manager = SubAgentManager(
        roots=SubAgentRoleRoots(project=tmp_path / "agents", user=tmp_path / "user", builtin=tmp_path / "builtin"),
        tool_registry=registry,
        executor=ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        config=make_app_config(tmp_path),
        provider=provider,  # type: ignore[arg-type]
        provider_resolver=lambda _model: provider,  # type: ignore[return-value]
        hook_manager=None,
        main_session=session,
        worktree_manager=worktrees,  # type: ignore[arg-type]
        worktree_janitor=janitor,  # type: ignore[arg-type]
    )
    manager.catalog.definitions["writer"] = isolated_role()
    bind_parent(manager, session, registry)

    record = await manager.delegate(
        SubAgentInvocation(type="defined", role="writer", task="等待取消", background=True)
    )
    assert isinstance(record, BackgroundSubAgentRecord)
    await provider.started.wait()
    await manager.close()

    assert record.task is not None
    result = record.task.result()
    assert result.status == "cancelled", result
    assert result.worktree is not None
    assert len(worktrees.finish_calls) == 1
    assert janitor.closed == 1


def git_branch_missing(repository: Path, branch: str) -> bool:
    completed = __import__("subprocess").run(
        ("git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"),
        cwd=repository,
        check=False,
    )
    return completed.returncode == 1


def write_role(path: Path, name: str, description: str, *, isolation: str | None = None) -> None:
    isolation_lines = () if isolation is None else (f"isolation: {isolation}",)
    path.write_text(
        "\n".join(
            (
                "---",
                f"name: {name}",
                f"description: {description}",
                "tools_allow:",
                "  - read_file",
                *isolation_lines,
                "---",
                "执行指定职责。",
            )
        ),
        encoding="utf-8",
    )


def call(call_id: str, name: str):
    from mewcode.tools.base import ToolCall

    return ToolCall(id=call_id, name=name)


def make_app_config(tmp_path: Path, *, sub_agents: SubAgentConfig | None = None) -> AppConfig:
    _ = tmp_path
    return AppConfig(
        protocol="openai",
        model="base-model",
        base_url="http://localhost",
        api_key="test-key",
        context=ContextConfig(enabled=False),
        permissions=PermissionConfig(mode="permissive"),
        sub_agents=sub_agents or SubAgentConfig(),
    )
