from __future__ import annotations

import json

import httpx
import pytest

from mewcode.config import AppConfig, PromptCacheConfig, ThinkingConfig
from mewcode.errors import ProviderError
from mewcode.prompting.base import GeneratedContextBlock, PromptBlock, PromptBundle
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.providers.base import ChatMessage, ChatRequest
from mewcode.providers.factory import create_provider
from mewcode.tools.base import ToolCall, ToolSpec


def anthropic_config(
    api_key: str = "sk-ant-secret-1234567890",
    thinking: ThinkingConfig | None = None,
    *,
    prompt_cache: PromptCacheConfig | None = None,
) -> AppConfig:
    return AppConfig(
        protocol="anthropic",
        model="test-claude",
        base_url="https://anthropic.test/v1",
        api_key=api_key,
        thinking=thinking,
        prompt_cache=prompt_cache or PromptCacheConfig(),
    )


async def collect(provider: AnthropicProvider, request: ChatRequest):
    return [event async for event in provider.stream_chat(request)]


def read_file_spec() -> ToolSpec:
    return ToolSpec(
        name="read_file",
        description="读取文件",
        parameters_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )


def mcp_echo_spec() -> ToolSpec:
    return ToolSpec(
        name="demo__echo",
        description="Echo through MCP",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        visibility="deferred",
        origin="mcp:demo",
    )


def prompt_bundle() -> PromptBundle:
    return PromptBundle(
        stable_blocks=(
            PromptBlock("identity", "身份", "稳定规则", stable=True),
            PromptBlock("text_output", "文本输出", "输出规则", stable=True, cacheable=True),
        ),
        runtime_blocks=(
            PromptBlock(
                "runtime_cache_prefix",
                "可缓存运行时前缀",
                "<mewcode_cacheable_runtime_context>\n允许工具：read_file(read_only)\n</mewcode_cacheable_runtime_context>",
                stable=False,
                cacheable=True,
            ),
            PromptBlock(
                "runtime_context",
                "运行时补充",
                "<mewcode_runtime_context>\n环境信息：cwd=/repo\n模式状态：normal full 1/8\n</mewcode_runtime_context>",
                stable=False,
            ),
        ),
    )


def repo_map_block(text: str = "<mewcode_repo_map>def target(...)</mewcode_repo_map>") -> GeneratedContextBlock:
    return GeneratedContextBlock(
        name="repo_map",
        title="仓库地图",
        text=text,
        kind="repo_map",
        snapshot_id="snapshot-1",
    )


@pytest.mark.asyncio
async def test_anthropic_request_payload_and_headers() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers["x-api-key"]
        seen["version"] = request.headers["anthropic-version"]
        payload = json.loads(request.content.decode("utf-8"))
        seen["payload"] = payload
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(anthropic_config(), client=client)

    await collect(provider, ChatRequest(messages=[ChatMessage(role="user", content="hello")]))
    await client.aclose()

    payload = seen["payload"]
    assert seen["url"] == "https://anthropic.test/v1/messages"
    assert seen["api_key"] == "sk-ant-secret-1234567890"
    assert seen["version"] == "2023-06-01"
    assert payload["model"] == "test-claude"
    assert payload["stream"] is True
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert "tools" not in payload
    assert "tool_choice" not in payload
    assert "system" not in payload


@pytest.mark.asyncio
async def test_anthropic_request_includes_tools_when_available() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(anthropic_config(), client=client)

    await collect(provider, ChatRequest(messages=[], tools=[read_file_spec()]))
    await client.aclose()

    assert seen["payload"]["tools"] == [
        {
            "name": "read_file",
            "description": "读取文件",
            "input_schema": read_file_spec().parameters_schema,
        }
    ]


@pytest.mark.asyncio
async def test_anthropic_request_includes_mcp_prefixed_tool() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(anthropic_config(), client=client)

    await collect(provider, ChatRequest(messages=[], tools=[mcp_echo_spec()]))
    await client.aclose()

    assert seen["payload"]["tools"] == [
        {
            "name": "demo__echo",
            "description": "Echo through MCP",
            "input_schema": mcp_echo_spec().parameters_schema,
        }
    ]


@pytest.mark.asyncio
async def test_anthropic_payload_includes_structured_system_blocks() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(anthropic_config(), client=client)

    await collect(
        provider,
        ChatRequest(
            messages=[ChatMessage(role="user", content="hello")],
            tools=[read_file_spec()],
            prompt=prompt_bundle(),
        ),
    )
    await client.aclose()

    system = seen["payload"]["system"]
    assert system[0] == {"type": "text", "text": "## 身份\n稳定规则"}
    assert system[1] == {
        "type": "text",
        "text": "## 文本输出\n输出规则",
    }
    assert system[2] == {
        "type": "text",
        "text": (
            "## 可缓存运行时前缀\n"
            "<mewcode_cacheable_runtime_context>\n允许工具：read_file(read_only)\n"
            "</mewcode_cacheable_runtime_context>"
        ),
        "cache_control": {"type": "ephemeral"},
    }
    assert system[3] == {
        "type": "text",
        "text": "## 运行时补充\n<mewcode_runtime_context>\n环境信息：cwd=/repo\n模式状态：normal full 1/8\n</mewcode_runtime_context>",
    }
    assert seen["payload"]["tools"]
    assert seen["payload"]["messages"] == [{"role": "user", "content": "hello"}]


def test_anthropic_repo_map_has_second_snapshot_cache_boundary() -> None:
    provider = AnthropicProvider(anthropic_config())
    base = prompt_bundle()
    prompt = PromptBundle(base.stable_blocks, base.runtime_blocks, (repo_map_block(),))

    system = provider._system_blocks(ChatRequest(messages=(), prompt=prompt))

    assert "可缓存运行时前缀" in system[2]["text"]
    assert system[2]["cache_control"] == {"type": "ephemeral"}
    assert "<mewcode_repo_map>" in system[3]["text"]
    assert system[3]["cache_control"] == {"type": "ephemeral"}
    assert "<mewcode_runtime_context>" in system[4]["text"]
    assert "cache_control" not in system[4]


def test_anthropic_repo_map_change_preserves_long_term_prefix() -> None:
    provider = AnthropicProvider(anthropic_config())
    base = prompt_bundle()
    first = PromptBundle(base.stable_blocks, base.runtime_blocks, (repo_map_block("map-one"),))
    second = PromptBundle(base.stable_blocks, base.runtime_blocks, (repo_map_block("map-two"),))

    first_system = provider._system_blocks(ChatRequest(messages=(), prompt=first))
    second_system = provider._system_blocks(ChatRequest(messages=(), prompt=second))

    assert first_system[:3] == second_system[:3]
    assert first_system[3] != second_system[3]
    assert first_system[3]["cache_control"] == second_system[3]["cache_control"]


@pytest.mark.asyncio
async def test_anthropic_runtime_prompt_is_not_cache_controlled() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(anthropic_config(), client=client)

    await collect(provider, ChatRequest(messages=[ChatMessage(role="user", content="hello")], prompt=prompt_bundle()))
    await client.aclose()

    runtime_block = seen["payload"]["system"][-1]
    assert "<mewcode_runtime_context>" in runtime_block["text"]
    assert "cache_control" not in runtime_block


@pytest.mark.asyncio
async def test_anthropic_cache_control_can_be_disabled() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(
        anthropic_config(prompt_cache=PromptCacheConfig(anthropic_cache_control=False)),
        client=client,
    )

    await collect(provider, ChatRequest(messages=[ChatMessage(role="user", content="hello")], prompt=prompt_bundle()))
    await client.aclose()

    assert all("cache_control" not in block for block in seen["payload"]["system"])


@pytest.mark.asyncio
async def test_anthropic_cache_options_can_be_disabled() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(
        anthropic_config(prompt_cache=PromptCacheConfig(enabled=False)),
        client=client,
    )

    await collect(provider, ChatRequest(messages=[ChatMessage(role="user", content="hello")], prompt=prompt_bundle()))
    await client.aclose()

    assert all("cache_control" not in block for block in seen["payload"]["system"])


@pytest.mark.asyncio
async def test_anthropic_payload_includes_tool_use_and_tool_result_messages() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(anthropic_config(), client=client)
    tool_call = ToolCall(
        id="toolu-1",
        name="read_file",
        arguments={"path": "README.md"},
        raw_arguments='{"path":"README.md"}',
    )

    await collect(
        provider,
        ChatRequest(
            messages=[
                ChatMessage(role="assistant", content="我先读取文件", tool_calls=(tool_call,)),
                ChatMessage(
                    role="tool",
                    content='{"success":false}',
                    tool_call_id="toolu-1",
                    tool_result_is_error=True,
                ),
            ],
        ),
    )
    await client.aclose()

    messages = seen["payload"]["messages"]
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"][0] == {"type": "text", "text": "我先读取文件"}
    assert messages[0]["content"][1] == {
        "type": "tool_use",
        "id": "toolu-1",
        "name": "read_file",
        "input": {"path": "README.md"},
    }
    assert messages[1] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu-1",
                "content": '{"success":false}',
                "is_error": True,
            }
        ],
    }


@pytest.mark.asyncio
async def test_anthropic_payload_groups_consecutive_tool_results_in_one_user_message() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(anthropic_config(), client=client)
    first_call = ToolCall(
        id="toolu-1",
        name="read_file",
        arguments={"path": "README.md"},
        raw_arguments='{"path":"README.md"}',
    )
    second_call = ToolCall(
        id="toolu-2",
        name="read_file",
        arguments={"path": "AGENTS.md"},
        raw_arguments='{"path":"AGENTS.md"}',
    )

    await collect(
        provider,
        ChatRequest(
            messages=[
                ChatMessage(role="user", content="读取两个文件"),
                ChatMessage(role="assistant", content="", tool_calls=(first_call, second_call)),
                ChatMessage(role="tool", content='{"success":true,"file":"README.md"}', tool_call_id="toolu-1"),
                ChatMessage(role="tool", content='{"success":true,"file":"AGENTS.md"}', tool_call_id="toolu-2"),
            ],
        ),
    )
    await client.aclose()

    messages = seen["payload"]["messages"]
    assert len(messages) == 3
    assert messages[1]["role"] == "assistant"
    assert [block["id"] for block in messages[1]["content"]] == ["toolu-1", "toolu-2"]
    assert messages[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu-1",
                "content": '{"success":true,"file":"README.md"}',
            },
            {
                "type": "tool_result",
                "tool_use_id": "toolu-2",
                "content": '{"success":true,"file":"AGENTS.md"}',
            },
        ],
    }


@pytest.mark.asyncio
async def test_anthropic_request_includes_thinking_when_enabled() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    config = anthropic_config(thinking=ThinkingConfig(enabled=True, budget_tokens=2048))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await collect(AnthropicProvider(config, client=client), ChatRequest(messages=[]))
    await client.aclose()

    assert seen["payload"]["thinking"] == {
        "type": "enabled",
        "display": "summarized",
        "budget_tokens": 2048,
    }


@pytest.mark.asyncio
async def test_anthropic_streams_text_thinking_signature_and_done() -> None:
    body = (
        'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"想"}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"答"}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"signature_delta","signature":"sig"}}\n\n'
        'event: ignored\ndata: {"type":"ignored"}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(AnthropicProvider(anthropic_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    assert [event.type for event in events] == [
        "message_start",
        "thinking_delta",
        "text_delta",
        "message_done",
    ]
    done = events[-1].message
    assert done is not None
    assert done.content == "答"
    assert done.thinking == "想"
    assert done.provider_payload == {"signature": "sig"}


@pytest.mark.asyncio
async def test_anthropic_streams_usage_events() -> None:
    body = (
        'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":4,"output_tokens":1}}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"答"}}\n\n'
        'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":3}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(AnthropicProvider(anthropic_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    assert [event.type for event in events] == [
        "message_start",
        "usage",
        "text_delta",
        "usage",
        "message_done",
    ]
    first_usage = events[1].usage
    second_usage = events[3].usage
    assert first_usage is not None
    assert first_usage.input_tokens == 4
    assert first_usage.output_tokens == 1
    assert first_usage.total_tokens == 5
    assert first_usage.provider == "anthropic"
    assert first_usage.cache is not None
    assert first_usage.cache.status == "unknown"
    assert second_usage is not None
    assert second_usage.input_tokens is None
    assert second_usage.output_tokens == 3
    assert second_usage.total_tokens is None
    assert second_usage.cache is not None
    assert second_usage.cache.status == "unknown"


@pytest.mark.asyncio
async def test_anthropic_streams_cache_read_usage() -> None:
    body = (
        'event: message_start\ndata: {"type":"message_start","message":{"usage":'
        '{"input_tokens":4,"cache_read_input_tokens":12,"cache_creation_input_tokens":0,"output_tokens":1}}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(AnthropicProvider(anthropic_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    usage = [event.usage for event in events if event.type == "usage"][0]
    assert usage is not None
    assert usage.input_tokens == 16
    assert usage.total_tokens == 17
    assert usage.cache is not None
    assert usage.cache.status == "hit"
    assert usage.cache.read_input_tokens == 12


@pytest.mark.asyncio
async def test_anthropic_streams_cache_creation_usage() -> None:
    body = (
        'event: message_start\ndata: {"type":"message_start","message":{"usage":'
        '{"input_tokens":4,"cache_read_input_tokens":0,"cache_creation_input_tokens":9,"output_tokens":1}}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(AnthropicProvider(anthropic_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    usage = [event.usage for event in events if event.type == "usage"][0]
    assert usage is not None
    assert usage.input_tokens == 13
    assert usage.total_tokens == 14
    assert usage.cache is not None
    assert usage.cache.status == "write"
    assert usage.cache.creation_input_tokens == 9


@pytest.mark.asyncio
async def test_anthropic_zero_cache_fields_are_miss() -> None:
    body = (
        'event: message_start\ndata: {"type":"message_start","message":{"usage":'
        '{"input_tokens":4,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"output_tokens":1}}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(AnthropicProvider(anthropic_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    usage = [event.usage for event in events if event.type == "usage"][0]
    assert usage is not None
    assert usage.cache is not None
    assert usage.cache.status == "miss"


@pytest.mark.asyncio
async def test_anthropic_usage_without_cache_fields_is_unknown() -> None:
    body = (
        'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":4,"output_tokens":1}}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(AnthropicProvider(anthropic_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    usage = [event.usage for event in events if event.type == "usage"][0]
    assert usage is not None
    assert usage.cache is not None
    assert usage.cache.status == "unknown"


@pytest.mark.asyncio
async def test_anthropic_error_event_is_redacted() -> None:
    secret = "sk-ant-secret-1234567890"
    body = f'event: error\ndata: {{"type":"error","error":{{"message":"bad {secret}"}}}}\n\n'
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    with pytest.raises(ProviderError) as exc_info:
        await collect(AnthropicProvider(anthropic_config(secret), client=client), ChatRequest(messages=[]))
    await client.aclose()

    assert secret not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


@pytest.mark.asyncio
async def test_anthropic_http_error_is_redacted() -> None:
    secret = "sk-ant-secret-1234567890"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(401, content=f"bad {secret}".encode()))
    )

    with pytest.raises(ProviderError) as exc_info:
        await collect(AnthropicProvider(anthropic_config(secret), client=client), ChatRequest(messages=[]))
    await client.aclose()

    assert secret not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_factory_returns_anthropic_provider() -> None:
    assert isinstance(create_provider(anthropic_config()), AnthropicProvider)


@pytest.mark.asyncio
async def test_anthropic_streams_tool_call_deltas_and_done() -> None:
    body = (
        'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"tool_use","id":"toolu-1","name":"read_file","input":{}}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"{\\"path\\""}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":":\\"README.md\\"}"}}\n\n'
        'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(AnthropicProvider(anthropic_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    assert [event.type for event in events] == [
        "message_start",
        "tool_call_delta",
        "tool_call_delta",
        "message_done",
    ]
    done = events[-1].message
    assert done is not None
    assert done.tool_calls[0].id == "toolu-1"
    assert done.tool_calls[0].name == "read_file"
    assert done.tool_calls[0].arguments == {"path": "README.md"}
    assert done.tool_calls[0].parse_error is None


@pytest.mark.asyncio
async def test_anthropic_tool_call_invalid_json_becomes_parse_error() -> None:
    body = (
        'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"tool_use","id":"toolu-1","name":"read_file","input":{}}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"{"}}\n\n'
        'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(AnthropicProvider(anthropic_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    done = events[-1].message
    assert done is not None
    assert done.tool_calls[0].raw_arguments == "{"
    assert done.tool_calls[0].parse_error is not None
