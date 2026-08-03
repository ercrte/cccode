from __future__ import annotations

from julycode.context.estimator import TokenEstimator
from julycode.context.models import ContextCompactionReport, ContextConfig, ContextLimitError, RequestFootprint, TokenAnchor
from julycode.prompting.base import GeneratedContextBlock, PromptBlock, PromptBundle
from julycode.providers.base import ChatMessage
from julycode.tools.base import ToolCall, ToolSpec


def test_context_config_defaults() -> None:
    config = ContextConfig()

    assert config.enabled is True
    assert config.window_tokens == 128_000
    assert config.auto_reserve_tokens == 13_000
    assert config.manual_reserve_tokens == 3_000
    assert config.summary_failure_limit == 3
    assert config.store_dir == ".julycode/context"


def test_context_limit_error_carries_report() -> None:
    report = ContextCompactionReport(mode="auto", light_compacted=False, heavy_compacted=False, message="stop")
    error = ContextLimitError("too large", report=report)

    assert str(error) == "too large"
    assert error.report == report


def test_estimates_request_footprint_from_messages_tools_and_prompt() -> None:
    estimator = TokenEstimator(ContextConfig(chars_per_token=4.0))
    prompt = PromptBundle(
        stable_blocks=(PromptBlock("identity", "身份", "稳定", stable=True),),
        runtime_blocks=(PromptBlock("runtime", "运行时", "动态", stable=False),),
    )
    tool = ToolSpec(
        name="read_file",
        description="读取文件",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    message = ChatMessage(
        role="assistant",
        content="hello",
        tool_calls=(ToolCall("call-1", "read_file", {"path": "README.md"}, '{"path":"README.md"}'),),
    )

    footprint = estimator.request_footprint((message,), (tool,), prompt)

    assert footprint.chars > len("hello")
    assert footprint.estimated_tokens > 0


def test_estimator_counts_complete_generated_context_block() -> None:
    estimator = TokenEstimator(ContextConfig(chars_per_token=4.0))
    base = PromptBundle(stable_blocks=(), runtime_blocks=())
    block = GeneratedContextBlock(
        name="repo_map",
        title="仓库地图",
        text="<julycode_repo_map>路径与边界</julycode_repo_map>",
        kind="repo_map",
        snapshot_id="snapshot-1",
    )
    with_map = PromptBundle(stable_blocks=(), runtime_blocks=(), generated_context_blocks=(block,))

    assert estimator.estimate_text(block.text) > 0
    assert estimator.estimate_generated_context(block) > estimator.estimate_text("路径与边界")
    assert estimator.request_footprint((), (), with_map).estimated_tokens > estimator.request_footprint((), (), base).estimated_tokens


def test_estimates_with_usage_anchor_delta() -> None:
    estimator = TokenEstimator(ContextConfig(chars_per_token=4.0))
    footprint = RequestFootprint(chars=140, estimated_tokens=35)
    anchor = TokenAnchor(input_tokens=20, footprint_chars=100)

    assert estimator.estimate_from_anchor(footprint, anchor) == 30


def test_estimates_without_usage_anchor() -> None:
    estimator = TokenEstimator(ContextConfig(chars_per_token=4.0))
    footprint = RequestFootprint(chars=140, estimated_tokens=35)

    assert estimator.estimate_from_anchor(footprint, None) == 35


def test_mcp_lazy_tools_reduce_idle_definition_footprint_by_ninety_percent() -> None:
    estimator = TokenEstimator(ContextConfig(chars_per_token=4.0))
    full_tools = tuple(
        ToolSpec(
            name=f"github__tool_{index}",
            description="GitHub remote operation. " + "detailed behavior " * 12,
            parameters_schema={
                "type": "object",
                "properties": {
                    f"argument_{field}": {
                        "type": "string",
                        "description": "Detailed GitHub parameter description",
                    }
                    for field in range(6)
                },
                "required": ["argument_0"],
            },
            visibility="deferred",
        )
        for index in range(45)
    )
    search_tool = ToolSpec(
        name="search_mcp_tools",
        description="按自然语言意图检索 MCP 工具，候选将在下一次模型迭代按需加载。",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "server": {"type": "string"}},
            "required": ["query"],
        },
        safety="read_only",
        visibility="system",
    )

    full = estimator.request_footprint((), full_tools, None)
    idle = estimator.request_footprint((), (search_tool,), None)
    active = estimator.request_footprint((), (search_tool, *full_tools[:5]), None)

    assert idle.estimated_tokens <= full.estimated_tokens * 0.1
    assert idle.estimated_tokens < active.estimated_tokens < full.estimated_tokens
