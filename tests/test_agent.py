from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from mewcode.agent import AgentLoopRunner, CompletionDecision, StreamCollector, TurnEvent
from mewcode.commands import AgentCommand
from mewcode.config import AgentConfig
from mewcode.context.manager import ContextManager
from mewcode.context.models import ContextCompactionReport, ContextConfig, ContextLimitError, PreparedChatRequest, RequestFootprint
from mewcode.errors import ProviderError
from mewcode.hooks import parse_hook_config
from mewcode.hooks.manager import create_hook_manager
from mewcode.memory.models import KnowledgeContext, MemoryIndex, MemoryUpdateJob
from mewcode.mcp.scope import McpTurnState
from mewcode.mcp.search import McpPromptContext, McpToolMatch, McpToolSearchResult
from mewcode.mcp.tools import McpToolDefinition, RemoteMcpTool, SearchMcpToolsTool
from mewcode.permissions import PermissionConfig
from mewcode.permissions.controller import create_permission_controller
from mewcode.providers.base import ChatMessage, ChatRequest, StreamEvent, TokenUsage
from mewcode.session import ChatSession, PendingPlan
from mewcode.skills import LoadSkillTool, SkillManager
from mewcode.skills.models import SkillRoots
from mewcode.tools.base import ToolCall, ToolContext, ToolSpec
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry, create_default_registry


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
        await asyncio.sleep(10)
        yield StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="late"))


class FakeContextManager:
    def __init__(
        self,
        *,
        report: ContextCompactionReport | None = None,
        fail: bool = False,
        fail_after: int | None = None,
    ) -> None:
        self.report = report
        self.fail = fail
        self.fail_after = fail_after
        self.prepare_calls = 0
        self.prepare_called = False
        self.recorded_usage: TokenUsage | None = None

    async def prepare_request(self, *, session, provider, tools, prompt_factory, mode="auto"):
        _ = provider, mode
        self.prepare_called = True
        self.prepare_calls += 1
        if self.fail or (self.fail_after is not None and self.prepare_calls >= self.fail_after):
            raise ContextLimitError("上下文超出预算")
        prompt = prompt_factory()
        return PreparedChatRequest(
            request=session.build_request(tools=tools, prompt=prompt),
            footprint=RequestFootprint(chars=10, estimated_tokens=3),
            report=self.report,
        )

    def record_usage(self, usage: TokenUsage | None, footprint: RequestFootprint) -> None:
        _ = footprint
        self.recorded_usage = usage


class FakeMemoryManager:
    def __init__(self, contexts: list[KnowledgeContext] | None = None) -> None:
        self.contexts = contexts or [knowledge("user memory")]
        self.runtime_calls = 0
        self.jobs: list[MemoryUpdateJob] = []
        self.providers: list[object] = []

    def runtime_context(self) -> KnowledgeContext:
        index = min(self.runtime_calls, len(self.contexts) - 1)
        self.runtime_calls += 1
        return self.contexts[index]

    def schedule_update(self, *, job: MemoryUpdateJob, provider) -> None:
        self.jobs.append(job)
        self.providers.append(provider)


class FakeTool:
    def __init__(
        self,
        name: str,
        *,
        safety: str = "read_only",
        visibility: str = "model",
        log: list[str] | None = None,
    ) -> None:
        self.spec = ToolSpec(
            name=name,
            description=name,
            parameters_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}, "command": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            },
            safety=safety,  # type: ignore[arg-type]
            visibility=visibility,  # type: ignore[arg-type]
        )
        self.log = log

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = context
        if self.log is not None:
            self.log.append(self.spec.name)
        return {"tool": self.spec.name, "text": arguments.get("text", "")}


class FakeMcpSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    async def call_tool(self, remote_name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((remote_name, dict(arguments)))
        return {
            "content": [{"type": "text", "text": arguments.get("text", "")}],
            "structuredContent": {"text": arguments.get("text", "")},
            "isError": False,
        }


class FakeMcpManager:
    def __init__(self, matches_by_query: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self.matches_by_query = matches_by_query or {"echo": ("demo__echo",)}

    def search_tools(self, query: str, server_name: str | None = None) -> McpToolSearchResult:
        names = next(
            (names for key, names in self.matches_by_query.items() if key in query),
            (),
        )
        return McpToolSearchResult(
            status="ok" if names else "no_match",
            query=query,
            server_name=server_name,
            matches=tuple(
                McpToolMatch(
                    global_name=name,
                    server_name="demo",
                    remote_name=name.partition("__")[2],
                    title=name,
                    summary=name,
                    score=1000,
                )
                for name in names
            ),
        )

    def prompt_context(self) -> McpPromptContext:
        return McpPromptContext()

    def create_turn_state(self) -> McpTurnState:
        return McpTurnState(self.prompt_context)


def make_registry(*tools: FakeTool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def make_runner(
    provider,
    tmp_path: Path,
    *,
    registry: ToolRegistry | None = None,
    session: ChatSession | None = None,
    max_iterations: int = 8,
    permission_controller=None,
    context_manager=None,
    memory_manager=None,
    hook_manager=None,
    mcp_manager=None,
) -> AgentLoopRunner:
    tool_registry = registry or make_registry(FakeTool("read"))
    return AgentLoopRunner(
        session or ChatSession(),
        provider,
        tool_registry,
        ToolExecutor(tool_registry, ToolContext(cwd=tmp_path)),
        AgentConfig(max_iterations=max_iterations),
        permission_controller=permission_controller,
        context_manager=context_manager,
        memory_manager=memory_manager,
        hook_manager=hook_manager,
        mcp_manager=mcp_manager,
    )


def make_skill_manager(tmp_path: Path, registry: ToolRegistry) -> SkillManager:
    manager = SkillManager(
        SkillRoots(
            project=tmp_path / "project-skills",
            user=tmp_path / "user-skills",
            builtin=resources.files("mewcode.skills.builtin"),
        ),
        registry,
    )
    registry.register(LoadSkillTool(manager))
    manager.refresh_if_changed()
    return manager


def command(text: str = "任务", mode: str = "normal") -> AgentCommand:
    return AgentCommand(mode=mode, visible_text=text, model_text=text)  # type: ignore[arg-type]


def knowledge(content: str) -> KnowledgeContext:
    return KnowledgeContext(
        user_memory_index=MemoryIndex(
            scope="user",
            path=Path("user-index.md"),
            content=content,
            line_count=1,
            byte_count=len(content.encode("utf-8")),
        )
    )


async def collect(runner: AgentLoopRunner, agent_command: AgentCommand | None = None) -> list[TurnEvent]:
    return [event async for event in runner.run(agent_command or command())]


async def stream_events(events: list[StreamEvent]) -> AsyncIterator[StreamEvent]:
    for event in events:
        await asyncio.sleep(0)
        yield event


@pytest.mark.asyncio
async def test_stream_collector_streams_deltas_and_builds_complete_message() -> None:
    collector = StreamCollector()

    events = [
        event async for event in collector.collect(
            stream_events(
                [
                    StreamEvent(type="text_delta", text="你"),
                    StreamEvent(type="thinking_delta", text="想"),
                    StreamEvent(type="text_delta", text="好"),
                    StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="你好", thinking="想")),
                ]
            ),
            iteration=1,
            mode="normal",
        )
    ]

    assert [event.type for event in events] == ["text_delta", "thinking_delta", "text_delta"]
    result = collector.result()
    assert result.message.content == "你好"
    assert result.message.thinking == "想"


@pytest.mark.asyncio
async def test_stream_collector_emits_usage() -> None:
    usage = TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3, provider="openai")
    collector = StreamCollector()

    events = [
        event async for event in collector.collect(
            stream_events([StreamEvent(type="usage", usage=usage), StreamEvent(type="message_done")]),
            iteration=1,
            mode="normal",
        )
    ]

    assert events[0].type == "usage"
    assert events[0].usage == usage
    assert collector.result().usage == usage


@pytest.mark.asyncio
async def test_runner_streams_plain_chat_and_saves_message(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [
                StreamEvent(type="text_delta", text="你"),
                StreamEvent(type="text_delta", text="好"),
                StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="你好")),
            ]
        ]
    )
    runner = make_runner(provider, tmp_path)

    events = await collect(runner, command("hello"))

    assert [event.type for event in events] == ["progress", "text_delta", "text_delta", "progress", "message_done"]
    assert runner.session.messages[-1].content == "你好"
    assert provider.requests[0].tools[0].name == "read"
    assert provider.requests[0].prompt is not None


@pytest.mark.asyncio
async def test_runner_prepares_request_through_context_manager(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    context_manager = FakeContextManager()
    runner = make_runner(provider, tmp_path, context_manager=context_manager)

    await collect(runner, command("hello"))

    assert context_manager.prepare_called is True


@pytest.mark.asyncio
async def test_runner_records_usage_anchor_after_model_response(tmp_path: Path) -> None:
    usage = TokenUsage(input_tokens=10, output_tokens=2, total_tokens=12)
    provider = FakeProvider(
        [
            [
                StreamEvent(type="usage", usage=usage),
                StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成")),
            ]
        ]
    )
    context_manager = FakeContextManager()
    runner = make_runner(provider, tmp_path, context_manager=context_manager)

    await collect(runner, command("hello"))

    assert context_manager.recorded_usage == usage


@pytest.mark.asyncio
async def test_runner_emits_context_compacted_event(tmp_path: Path) -> None:
    report = ContextCompactionReport(
        mode="auto",
        light_compacted=True,
        heavy_compacted=False,
        message="已压缩工具结果。",
    )
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    runner = make_runner(provider, tmp_path, context_manager=FakeContextManager(report=report))

    events = await collect(runner, command("hello"))

    compacted = [event for event in events if event.type == "context_compacted"]
    assert compacted
    assert compacted[0].context_report == report


@pytest.mark.asyncio
async def test_runner_stops_on_context_limit(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="不会调用"))]])
    runner = make_runner(provider, tmp_path, context_manager=FakeContextManager(fail=True))

    events = await collect(runner, command("hello"))

    assert events[-2].type == "stopped"
    assert events[-2].stop_reason == "context_limit"
    assert provider.requests == []


@pytest.mark.asyncio
async def test_runner_runs_multiple_tool_iterations_until_final_answer(tmp_path: Path) -> None:
    first_call = ToolCall(id="c1", name="read", arguments={"text": "a"})
    second_call = ToolCall(id="c2", name="search", arguments={"text": "b"})
    registry = make_registry(FakeTool("read"), FakeTool("search"))
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(first_call,)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(second_call,)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))],
        ]
    )
    runner = make_runner(provider, tmp_path, registry=registry)

    events = await collect(runner, command("多步任务"))

    assert [event.type for event in events].count("tool_started") == 2
    assert [event.type for event in events].count("tool_finished") == 2
    assert provider.requests[2].messages[-1].role == "tool"
    assert runner.session.messages[-1].content == "完成"


@pytest.mark.asyncio
async def test_runner_executes_all_tool_calls_in_one_model_response(tmp_path: Path) -> None:
    calls = (
        ToolCall(id="c1", name="read", arguments={"text": "a"}),
        ToolCall(id="c2", name="search", arguments={"text": "b"}),
    )
    registry = make_registry(FakeTool("read"), FakeTool("search"))
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=calls))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="汇总"))],
        ]
    )
    runner = make_runner(provider, tmp_path, registry=registry)

    events = await collect(runner)

    assert [event.type for event in events].count("tool_started") == 2
    assert [message.role for message in provider.requests[1].messages] == ["user", "assistant", "tool", "tool"]


@pytest.mark.asyncio
async def test_runner_stops_at_iteration_limit(tmp_path: Path) -> None:
    provider = FakeProvider(
        [[StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(ToolCall("c1", "read"),)))]]
    )
    runner = make_runner(provider, tmp_path, max_iterations=1)

    events = await collect(runner)

    assert events[-2].type == "stopped"
    assert events[-2].stop_reason == "iteration_limit"
    assert "迭代上限" in events[-2].text
    assert "tool_started" not in [event.type for event in events]


@pytest.mark.asyncio
async def test_runner_stops_after_consecutive_unknown_tools(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(ToolCall("c1", "missing"),)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(ToolCall("c2", "missing"),)))],
        ]
    )
    runner = make_runner(provider, tmp_path)

    events = await collect(runner)

    assert [event.type for event in events].count("tool_finished") == 1
    assert events[-2].stop_reason == "unknown_tool_limit"
    assert len([message for message in runner.session.messages if message.role == "tool"]) == 1


@pytest.mark.asyncio
async def test_runner_reports_provider_error(tmp_path: Path) -> None:
    provider = FakeProvider([[ProviderError("bad")]])
    runner = make_runner(provider, tmp_path)

    events = await collect(runner)

    assert events[-1].type == "error"
    assert events[-1].error == "bad"
    assert events[-1].stop_reason == "stream_error"


@pytest.mark.asyncio
async def test_runner_can_be_cancelled(tmp_path: Path) -> None:
    runner = make_runner(SlowProvider(), tmp_path)

    task = asyncio.create_task(collect(runner))
    await asyncio.sleep(0.01)
    task.cancel()
    events = await task

    assert events[-1].type == "stopped"
    assert events[-1].stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_plan_mode_does_not_save_pending_plan(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="计划内容"))]])
    runner = make_runner(provider, tmp_path)

    await collect(runner, command("做事", mode="plan"))

    assert runner.session.pending_plan is None
    assert {spec.name for spec in provider.requests[0].tools} == {"read"}
    assert provider.requests[0].prompt is not None


@pytest.mark.asyncio
async def test_plan_mode_blocks_side_effect_tools(tmp_path: Path) -> None:
    log: list[str] = []
    registry = make_registry(FakeTool("read", safety="read_only"), FakeTool("write", safety="side_effect", log=log))
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(ToolCall("c1", "write"),)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="计划"))],
        ]
    )
    runner = make_runner(provider, tmp_path, registry=registry)

    events = await collect(runner, command("做事", mode="plan"))

    assert log == []
    tool_finished = [event for event in events if event.type == "tool_finished"][0]
    assert tool_finished.tool_result is not None
    assert tool_finished.tool_result.error_type == "tool_not_allowed"
    assert runner.session.pending_plan is None


@pytest.mark.asyncio
async def test_normal_mode_executes_side_effect_tools_without_clearing_pending_plan(tmp_path: Path) -> None:
    log: list[str] = []
    session = ChatSession()
    session.save_pending_plan(PendingPlan(source_request="需求", plan_text="计划"))
    registry = make_registry(FakeTool("write", safety="side_effect", log=log))
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(ToolCall("c1", "write"),)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="执行完成"))],
        ]
    )
    runner = make_runner(provider, tmp_path, registry=registry, session=session)

    await collect(runner, command("执行任务", mode="normal"))

    assert log == ["write"]
    assert runner.session.pending_plan is not None
    assert provider.requests[0].prompt is not None


@pytest.mark.asyncio
async def test_normal_mode_keeps_pending_plan_when_stopped(tmp_path: Path) -> None:
    session = ChatSession()
    plan = PendingPlan(source_request="需求", plan_text="计划")
    session.save_pending_plan(plan)
    provider = FakeProvider(
        [[StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(ToolCall("c1", "read"),)))]]
    )
    runner = make_runner(provider, tmp_path, session=session, max_iterations=1)

    await collect(runner, command("执行任务", mode="normal"))

    assert runner.session.pending_plan == plan


@pytest.mark.asyncio
async def test_runner_attaches_prompt_bundle_to_model_request(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    runner = make_runner(provider, tmp_path)

    await collect(runner, command("查看 README"))

    request = provider.requests[0]
    assert request.prompt is not None
    assert [block.name for block in request.prompt.stable_blocks] == [
        "identity",
        "system_constraints",
        "task_modes",
        "action_execution",
        "tool_usage",
        "tone_style",
        "text_output",
    ]
    assert [block.name for block in request.prompt.runtime_blocks] == [
        "runtime_cache_prefix",
        "runtime_context",
    ]
    assert request.prompt.runtime_blocks[0].cacheable is True
    assert request.prompt.runtime_blocks[-1].cacheable is False
    assert "允许工具：read(read_only)" in request.prompt.runtime_blocks[0].text
    assert str(tmp_path) not in request.prompt.runtime_blocks[0].text
    assert str(tmp_path) in request.prompt.runtime_blocks[-1].text
    assert "模式状态：normal full 1/8" in request.prompt.runtime_blocks[-1].text


@pytest.mark.asyncio
async def test_runner_injects_memory_context_before_model_request(tmp_path: Path) -> None:
    first_call = ToolCall("call-1", "read", {"text": "x"})
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(first_call,)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))],
        ]
    )
    memory_manager = FakeMemoryManager([knowledge("第一版索引"), knowledge("第二版索引")])
    runner = make_runner(provider, tmp_path, context_manager=FakeContextManager(), memory_manager=memory_manager)

    await collect(runner, command("读取后回答"))

    assert memory_manager.runtime_calls == 2
    assert provider.requests[0].prompt is not None
    assert provider.requests[1].prompt is not None
    first_runtime = provider.requests[0].prompt.runtime_blocks[-1].text
    second_runtime = provider.requests[1].prompt.runtime_blocks[-1].text
    assert "<mewcode_memory_index>" in first_runtime
    assert "第一版索引" in first_runtime
    assert "第二版索引" in second_runtime


@pytest.mark.asyncio
async def test_runner_load_skill_tool_activates_sop_and_whitelist(tmp_path: Path) -> None:
    registry = create_default_registry()
    manager = make_skill_manager(tmp_path, registry)
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    type="message_done",
                    message=ChatMessage(
                        role="assistant",
                        tool_calls=(ToolCall("call-load", "load_skill", {"name": "review", "input": "README.md"}),),
                    ),
                )
            ],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="审查完成"))],
        ]
    )
    runner = AgentLoopRunner(
        ChatSession(),
        provider,
        registry,
        ToolExecutor(registry, ToolContext(cwd=tmp_path)),
        AgentConfig(max_iterations=4),
        context_manager=FakeContextManager(),
        skill_manager=manager,
    )

    await collect(runner, command("帮我审查 README.md"))

    assert len(provider.requests) == 2
    second_runtime = provider.requests[1].prompt.runtime_blocks[-1].text  # type: ignore[union-attr]
    assert "你正在执行内置 review Skill" in second_runtime
    assert "README.md" in second_runtime
    second_tools = {tool.name for tool in provider.requests[1].tools}
    assert "load_skill" in second_tools
    assert "read_file" in second_tools
    assert "write_file" not in second_tools


@pytest.mark.asyncio
async def test_runner_works_without_memory_manager(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    runner = make_runner(provider, tmp_path)

    await collect(runner, command("hello"))

    assert provider.requests[0].prompt is not None
    runtime_text = provider.requests[0].prompt.runtime_blocks[-1].text
    assert "<mewcode_memory_index>" not in runtime_text
    assert runner.session.messages[-1].content == "完成"


@pytest.mark.asyncio
async def test_loop_controller_runs_at_safe_iteration_boundaries(tmp_path: Path) -> None:
    class Controller:
        def __init__(self) -> None:
            self.last_roles: list[str] = []

        async def before_iteration(self, session: ChatSession) -> None:
            self.last_roles.append(session.messages[-1].role)

        async def review_completion(self, message: ChatMessage) -> CompletionDecision:
            return CompletionDecision(True, message)

    registry = make_registry(FakeTool("read"))
    provider = FakeProvider([
        [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(ToolCall("c1", "read"),)))],
        [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))],
    ])
    controller = Controller()
    runner = AgentLoopRunner(
        ChatSession(), provider, registry, ToolExecutor(registry, ToolContext(tmp_path)), AgentConfig(),
        context_manager=FakeContextManager(), loop_controller=controller,
    )

    await collect(runner, command("读取"))

    assert controller.last_roles == ["user", "tool"]


@pytest.mark.asyncio
async def test_completion_controller_can_continue_and_replace_reply(tmp_path: Path) -> None:
    class Controller:
        def __init__(self) -> None:
            self.calls = 0

        async def before_iteration(self, session: ChatSession) -> None:
            _ = session

        async def review_completion(self, message: ChatMessage) -> CompletionDecision:
            self.calls += 1
            if self.calls == 1:
                return CompletionDecision(False, message, "继续检查团队状态")
            return CompletionDecision(True, ChatMessage(role="assistant", content="替换后的最终答复"))

    provider = FakeProvider([
        [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="过早完成"))],
        [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="原始答复"))],
    ])
    controller = Controller()
    runner = AgentLoopRunner(
        ChatSession(), provider, make_registry(FakeTool("read")),
        ToolExecutor(make_registry(FakeTool("unused")), ToolContext(tmp_path)), AgentConfig(),
        context_manager=FakeContextManager(), loop_controller=controller,
    )

    events = await collect(runner, command("团队任务"))

    assert len(provider.requests) == 2
    assert any(message.content == "继续检查团队状态" for message in runner.session.messages)
    assert runner.session.messages[-1].content == "替换后的最终答复"
    assert events[-1].message.content == "替换后的最终答复"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_runner_schedules_memory_update_on_natural_completion(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    memory_manager = FakeMemoryManager([knowledge("用户偏好索引")])
    runner = make_runner(provider, tmp_path, memory_manager=memory_manager)

    await collect(runner, command("请记住偏好"))

    assert len(memory_manager.jobs) == 1
    job = memory_manager.jobs[0]
    assert job.session_id == runner.session.context_state.session_id
    assert job.cwd == tmp_path
    assert [message.role for message in job.turn_messages] == ["user"]
    assert job.turn_messages[0].content == "请记住偏好"
    assert job.final_message.content == "完成"
    assert job.knowledge_context.user_memory_index is not None
    assert memory_manager.providers == [provider]


@pytest.mark.asyncio
async def test_runner_does_not_schedule_memory_update_on_non_natural_stop(tmp_path: Path) -> None:
    memory_manager = FakeMemoryManager([knowledge("用户偏好索引")])
    provider = FakeProvider(
        [[StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(ToolCall("c1", "read"),)))]]
    )
    runner = make_runner(provider, tmp_path, max_iterations=1, memory_manager=memory_manager)

    events = await collect(runner)

    assert events[-2].stop_reason == "iteration_limit"
    assert memory_manager.jobs == []


@pytest.mark.asyncio
async def test_plan_mode_prompt_is_runtime_instruction_not_user_text(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="计划"))]])
    runner = make_runner(provider, tmp_path)
    plan_command = AgentCommand(mode="plan", visible_text="做事", model_text="做事")

    await collect(runner, plan_command)

    request = provider.requests[0]
    assert [message.content for message in request.messages if message.role == "user"] == ["做事"]
    assert "禁止写入文件" not in request.messages[0].content
    assert request.prompt is not None
    runtime_text = request.prompt.runtime_blocks[-1].text
    assert "规划模式。只能使用读取、查找和搜索类工具" in runtime_text
    assert "禁止写入文件、修改文件、执行命令" in runtime_text


@pytest.mark.asyncio
async def test_normal_mode_does_not_inject_pending_plan(tmp_path: Path) -> None:
    session = ChatSession()
    session.save_pending_plan(PendingPlan(source_request="需求", plan_text="1. 读取 README\n2. 修改文件"))
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    runner = make_runner(provider, tmp_path, session=session)
    normal_command = AgentCommand(mode="normal", visible_text="执行当前任务", model_text="执行当前任务")

    await collect(runner, normal_command)

    request = provider.requests[0]
    assert [message.content for message in request.messages if message.role == "user"] == ["执行当前任务"]
    assert "1. 读取 README" not in request.messages[0].content
    assert request.prompt is not None
    runtime_text = request.prompt.runtime_blocks[-1].text
    assert "当前待执行计划：" not in runtime_text
    assert "1. 读取 README\n2. 修改文件" not in runtime_text


@pytest.mark.asyncio
async def test_runner_feeds_permission_denial_back_to_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / ".mewcode.permissions.local.yaml").write_text('rules:\n  "write(*)": deny\n', encoding="utf-8")
    log: list[str] = []
    registry = make_registry(FakeTool("write", safety="side_effect", log=log))
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(ToolCall("c1", "write"),)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="已改用安全方案"))],
        ]
    )
    controller = create_permission_controller(tmp_path, PermissionConfig(mode="permissive"))
    runner = make_runner(provider, tmp_path, registry=registry, permission_controller=controller)

    events = await collect(runner)

    assert log == []
    assert [event.type for event in events].count("tool_finished") == 1
    assert provider.requests[1].messages[-1].role == "tool"
    assert "permission_rule_denied" in provider.requests[1].messages[-1].content
    assert runner.session.messages[-1].content == "已改用安全方案"


@pytest.mark.asyncio
async def test_runner_does_not_execute_dangerous_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    log: list[str] = []
    registry = make_registry(FakeTool("run_command", safety="side_effect", log=log))
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    type="message_done",
                    message=ChatMessage(
                        role="assistant",
                        tool_calls=(ToolCall("c1", "run_command", {"command": "rm -rf /"}),),
                    ),
                )
            ],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="已拒绝危险命令"))],
        ]
    )
    controller = create_permission_controller(tmp_path, PermissionConfig(mode="permissive"))
    runner = make_runner(provider, tmp_path, registry=registry, permission_controller=controller)

    await collect(runner)

    assert log == []
    assert "permission_dangerous_command" in provider.requests[1].messages[-1].content
    assert runner.session.messages[-1].content == "已拒绝危险命令"


@pytest.mark.asyncio
async def test_runner_can_execute_remote_mcp_tool_from_registry(tmp_path: Path) -> None:
    mcp_session = FakeMcpSession()
    mcp_manager = FakeMcpManager()
    mcp_tool = RemoteMcpTool(
        McpToolDefinition(
            server_name="demo",
            remote_name="echo",
            global_name="demo__echo",
            title="Echo",
            description="Echo text",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        ),
        mcp_session,
    )
    registry = ToolRegistry()
    registry.register(mcp_tool)
    registry.register(SearchMcpToolsTool(mcp_manager))
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    type="message_done",
                    message=ChatMessage(
                        role="assistant",
                        tool_calls=(ToolCall("search-1", "search_mcp_tools", {"query": "echo", "server": "demo"}),),
                    ),
                )
            ],
            [
                StreamEvent(
                    type="message_done",
                    message=ChatMessage(
                        role="assistant",
                        tool_calls=(ToolCall("call-1", "demo__echo", {"text": "hello-mcp"}),),
                    ),
                )
            ],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="done"))],
        ]
    )

    runner = make_runner(provider, tmp_path, registry=registry, mcp_manager=mcp_manager)
    events = await collect(runner)

    assert [event.type for event in events].count("tool_started") == 2
    assert [event.type for event in events].count("tool_finished") == 2
    assert mcp_session.calls == [("echo", {"text": "hello-mcp"})]
    assert len(provider.requests) == 3
    assert {tool.name for tool in provider.requests[0].tools} == {"search_mcp_tools"}
    assert {tool.name for tool in provider.requests[1].tools} == {"search_mcp_tools", "demo__echo"}
    assert provider.requests[2].messages[-1].role == "tool"
    assert "hello-mcp" in provider.requests[2].messages[-1].content
    assert runner.active_mcp_tools == frozenset()


@pytest.mark.asyncio
async def test_mcp_lazy_replacement_keeps_only_latest_candidates(tmp_path: Path) -> None:
    manager = FakeMcpManager({"echo": ("demo__echo",), "issue": ("demo__issue_read",)})
    registry = make_registry(
        FakeTool("demo__echo", safety="side_effect", visibility="deferred"),
        FakeTool("demo__issue_read", safety="side_effect", visibility="deferred"),
    )
    registry.register(SearchMcpToolsTool(manager))
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    type="message_done",
                    message=ChatMessage(
                        role="assistant",
                        tool_calls=(ToolCall("search-1", "search_mcp_tools", {"query": "echo"}),),
                    ),
                )
            ],
            [
                StreamEvent(
                    type="message_done",
                    message=ChatMessage(
                        role="assistant",
                        tool_calls=(ToolCall("search-2", "search_mcp_tools", {"query": "issue"}),),
                    ),
                )
            ],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="done"))],
        ]
    )
    runner = make_runner(provider, tmp_path, registry=registry, mcp_manager=manager)

    await collect(runner)

    assert {tool.name for tool in provider.requests[0].tools} == {"search_mcp_tools"}
    assert {tool.name for tool in provider.requests[1].tools} == {"search_mcp_tools", "demo__echo"}
    assert {tool.name for tool in provider.requests[2].tools} == {"search_mcp_tools", "demo__issue_read"}


@pytest.mark.asyncio
async def test_mcp_lazy_cleanup_after_provider_error_and_context_limit(tmp_path: Path) -> None:
    manager = FakeMcpManager()
    registry = make_registry(FakeTool("demo__echo", safety="side_effect", visibility="deferred"))
    registry.register(SearchMcpToolsTool(manager))

    error_provider = FakeProvider(
        [
            [
                StreamEvent(
                    type="message_done",
                    message=ChatMessage(
                        role="assistant",
                        tool_calls=(ToolCall("search-1", "search_mcp_tools", {"query": "echo"}),),
                    ),
                )
            ],
            [ProviderError("bad")],
        ]
    )
    error_runner = make_runner(error_provider, tmp_path, registry=registry, mcp_manager=manager)
    await collect(error_runner)
    assert error_runner.active_mcp_tools == frozenset()

    limit_provider = FakeProvider(
        [
            [
                StreamEvent(
                    type="message_done",
                    message=ChatMessage(
                        role="assistant",
                        tool_calls=(ToolCall("search-2", "search_mcp_tools", {"query": "echo"}),),
                    ),
                )
            ]
        ]
    )
    limit_runner = make_runner(
        limit_provider,
        tmp_path,
        registry=registry,
        mcp_manager=manager,
        context_manager=FakeContextManager(fail_after=2),
    )
    await collect(limit_runner)
    assert limit_runner.active_mcp_tools == frozenset()


@pytest.mark.asyncio
async def test_runner_externalizes_large_tool_result_and_continues(tmp_path: Path) -> None:
    big_text = "x" * 500
    registry = make_registry(FakeTool("read"))
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    type="message_done",
                    message=ChatMessage(
                        role="assistant",
                        tool_calls=(ToolCall("call-1", "read", {"text": big_text}),),
                    ),
                )
            ],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))],
        ]
    )
    context_manager = ContextManager(
        ContextConfig(single_tool_result_tokens=5, window_tokens=100_000),
        tmp_path,
        max_output_tokens=10,
    )
    runner = make_runner(provider, tmp_path, registry=registry, context_manager=context_manager)

    events = await collect(runner)

    assert [event.type for event in events].count("context_compacted") == 1
    assert len(provider.requests) == 2
    tool_message = provider.requests[1].messages[-1]
    assert tool_message.role == "tool"
    assert "mewcode_externalized" in tool_message.content
    assert ".mewcode/context" in tool_message.content
    assert "完成" == runner.session.messages[-1].content


@pytest.mark.asyncio
async def test_runner_emits_hook_finished_events(tmp_path: Path) -> None:
    hook_manager = create_hook_manager(
        parse_hook_config([{"name": "turn-hook", "event": "turn.start", "action": {"type": "prompt", "text": "ctx"}}])
    )
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    runner = make_runner(provider, tmp_path, hook_manager=hook_manager)

    events = await collect(runner, command("hello"))

    hook_events = [event for event in events if event.type == "hook_finished"]
    assert hook_events
    assert hook_events[0].hook_result is not None
    assert hook_events[0].hook_result.rule_id == "turn-hook"


@pytest.mark.asyncio
async def test_runner_injects_hook_prompt_into_next_request(tmp_path: Path) -> None:
    hook_manager = create_hook_manager(
        parse_hook_config([{"name": "inject", "event": "turn.start", "action": {"type": "prompt", "text": "Hook 注入内容"}}])
    )
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    runner = make_runner(provider, tmp_path, hook_manager=hook_manager)

    await collect(runner, command("hello"))

    assert provider.requests[0].prompt is not None
    runtime_text = provider.requests[0].prompt.runtime_blocks[-1].text
    assert "<mewcode_hook_instructions>" in runtime_text
    assert "Hook 注入内容" in runtime_text


@pytest.mark.asyncio
async def test_runner_feeds_hook_block_back_to_model(tmp_path: Path) -> None:
    log: list[str] = []
    registry = make_registry(FakeTool("write", safety="side_effect", log=log))
    hook_manager = create_hook_manager(
        parse_hook_config(
            [
                {
                    "name": "block-write",
                    "event": "tool.before",
                    "if": {"all": [{"field": "tool.name", "match": "write"}]},
                    "action": {"type": "prompt", "text": "x", "tool_block": {"reason": "Hook 拒绝写入"}},
                }
            ]
        )
    )
    provider = FakeProvider(
        [
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", tool_calls=(ToolCall("c1", "write"),)))],
            [StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="已调整"))],
        ]
    )
    runner = make_runner(provider, tmp_path, registry=registry, hook_manager=hook_manager)

    await collect(runner)

    assert log == []
    assert "hook_blocked" in provider.requests[1].messages[-1].content
    assert "Hook 拒绝写入" in provider.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_runner_emits_turn_and_message_hooks(tmp_path: Path) -> None:
    hook_manager = create_hook_manager(
        parse_hook_config(
            [
                {"name": "turn-start", "event": "turn.start", "action": {"type": "prompt", "text": "a"}},
                {"name": "user-message", "event": "message.user", "action": {"type": "prompt", "text": "b"}},
                {"name": "assistant-message", "event": "message.assistant", "action": {"type": "prompt", "text": "c"}},
                {"name": "turn-end", "event": "turn.end", "action": {"type": "prompt", "text": "d"}},
            ]
        )
    )
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])

    events = await collect(make_runner(provider, tmp_path, hook_manager=hook_manager))

    rule_ids = [event.hook_result.rule_id for event in events if event.type == "hook_finished" and event.hook_result]
    assert {"turn-start", "user-message", "assistant-message", "turn-end"} <= set(rule_ids)


@pytest.mark.asyncio
async def test_runner_emits_system_hook_events(tmp_path: Path) -> None:
    report = ContextCompactionReport(mode="auto", light_compacted=True, heavy_compacted=False, message="压缩")
    hook_manager = create_hook_manager(
        parse_hook_config(
            [
                {"name": "compacted", "event": "system.context_compacted", "action": {"type": "prompt", "text": "a"}},
                {"name": "stopped", "event": "system.stopped", "action": {"type": "prompt", "text": "b"}},
            ]
        )
    )
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])

    events = await collect(
        make_runner(
            provider,
            tmp_path,
            context_manager=FakeContextManager(report=report),
            hook_manager=hook_manager,
        )
    )

    rule_ids = [event.hook_result.rule_id for event in events if event.type == "hook_finished" and event.hook_result]
    assert "compacted" in rule_ids
    assert "stopped" in rule_ids


@pytest.mark.asyncio
async def test_hook_failure_does_not_stop_agent(tmp_path: Path) -> None:
    hook_manager = create_hook_manager(
        parse_hook_config(
            [{"name": "bad", "event": "turn.start", "action": {"type": "command", "command": "python -V"}}]
        )
    )
    hook_manager.action_runner = object()  # type: ignore[assignment]
    provider = FakeProvider([[StreamEvent(type="message_done", message=ChatMessage(role="assistant", content="完成"))]])
    runner = make_runner(provider, tmp_path, hook_manager=hook_manager)

    events = await collect(runner)

    assert events[-1].type == "message_done"
    assert runner.session.messages[-1].content == "完成"
