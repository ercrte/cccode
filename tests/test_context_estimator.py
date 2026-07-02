from __future__ import annotations

from mewcode.context.estimator import TokenEstimator
from mewcode.context.models import ContextCompactionReport, ContextConfig, ContextLimitError, RequestFootprint, TokenAnchor
from mewcode.prompting.base import PromptBlock, PromptBundle
from mewcode.providers.base import ChatMessage
from mewcode.tools.base import ToolCall, ToolSpec


def test_context_config_defaults() -> None:
    config = ContextConfig()

    assert config.enabled is True
    assert config.window_tokens == 128_000
    assert config.auto_reserve_tokens == 13_000
    assert config.manual_reserve_tokens == 3_000
    assert config.summary_failure_limit == 3
    assert config.store_dir == ".mewcode/context"


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


def test_estimates_with_usage_anchor_delta() -> None:
    estimator = TokenEstimator(ContextConfig(chars_per_token=4.0))
    footprint = RequestFootprint(chars=140, estimated_tokens=35)
    anchor = TokenAnchor(input_tokens=20, footprint_chars=100)

    assert estimator.estimate_from_anchor(footprint, anchor) == 30


def test_estimates_without_usage_anchor() -> None:
    estimator = TokenEstimator(ContextConfig(chars_per_token=4.0))
    footprint = RequestFootprint(chars=140, estimated_tokens=35)

    assert estimator.estimate_from_anchor(footprint, None) == 35
