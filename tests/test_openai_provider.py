from __future__ import annotations

import json

import httpx
import pytest

from mewcode.config import AppConfig, PromptCacheConfig
from mewcode.errors import ProviderError
from mewcode.prompting.base import PromptBlock, PromptBundle
from mewcode.providers.base import ChatMessage, ChatRequest
from mewcode.providers.factory import create_provider
from mewcode.providers.openai import OpenAIProvider
from mewcode.tools.base import ToolCall, ToolSpec


def openai_config(
    api_key: str = "sk-openai-secret-1234567890",
    *,
    prompt_cache: PromptCacheConfig | None = None,
) -> AppConfig:
    return AppConfig(
        protocol="openai",
        model="test-openai",
        base_url="https://openai.test/v1",
        api_key=api_key,
        prompt_cache=prompt_cache or PromptCacheConfig(),
    )


async def collect(provider: OpenAIProvider, request: ChatRequest):
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
        stable_blocks=(PromptBlock("identity", "身份", "稳定规则", stable=True, cacheable=True),),
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


@pytest.mark.asyncio
async def test_openai_request_payload_and_headers() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        payload = json.loads(request.content.decode("utf-8"))
        seen["payload"] = payload
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(openai_config(), client=client)

    await collect(provider, ChatRequest(messages=[ChatMessage(role="user", content="hello")]))
    await client.aclose()

    payload = seen["payload"]
    assert seen["url"] == "https://openai.test/v1/chat/completions"
    assert seen["authorization"] == "Bearer sk-openai-secret-1234567890"
    assert payload["model"] == "test-openai"
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert "tools" not in payload
    assert "functions" not in payload


@pytest.mark.asyncio
async def test_openai_request_includes_tools_when_available() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(openai_config(), client=client)

    await collect(provider, ChatRequest(messages=[], tools=[read_file_spec()]))
    await client.aclose()

    payload = seen["payload"]
    tools = payload["tools"]
    assert tools == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取文件",
                "parameters": read_file_spec().parameters_schema,
            },
        }
    ]


@pytest.mark.asyncio
async def test_openai_request_includes_mcp_prefixed_tool() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(openai_config(), client=client)

    await collect(provider, ChatRequest(messages=[], tools=[mcp_echo_spec()]))
    await client.aclose()

    tool = seen["payload"]["tools"][0]["function"]
    assert tool == {
        "name": "demo__echo",
        "description": "Echo through MCP",
        "parameters": mcp_echo_spec().parameters_schema,
    }
    assert "mcp" not in seen["payload"]["tools"][0]


@pytest.mark.asyncio
async def test_openai_payload_includes_structured_prompt_messages() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(openai_config(), client=client)

    await collect(
        provider,
        ChatRequest(
            messages=[ChatMessage(role="user", content="hello")],
            tools=[read_file_spec()],
            prompt=prompt_bundle(),
        ),
    )
    await client.aclose()

    messages = seen["payload"]["messages"]
    assert messages[0]["role"] == "system"
    assert "## 身份\n稳定规则" in messages[0]["content"]
    assert "## 可缓存运行时前缀" in messages[0]["content"]
    assert "允许工具：read_file(read_only)" in messages[0]["content"]
    assert "<mewcode_runtime_context>" not in messages[0]["content"]
    assert "name" not in messages[0]
    assert messages[1]["role"] == "system"
    assert "<mewcode_runtime_context>" in messages[1]["content"]
    assert "环境信息：cwd=/repo" in messages[1]["content"]
    assert "允许工具：read_file(read_only)" not in messages[1]["content"]
    assert "name" not in messages[1]
    assert messages[2] == {"role": "user", "content": "hello"}
    assert seen["payload"]["tools"]
    assert "prompt_cache_key" in seen["payload"]


@pytest.mark.asyncio
async def test_openai_runtime_prompt_is_not_user_message() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(openai_config(), client=client)

    await collect(
        provider,
        ChatRequest(messages=[ChatMessage(role="user", content="hello")], prompt=prompt_bundle()),
    )
    await client.aclose()

    user_messages = [message for message in seen["payload"]["messages"] if message["role"] == "user"]
    assert user_messages == [{"role": "user", "content": "hello"}]
    assert all("<mewcode_runtime_context>" not in message["content"] for message in user_messages)


@pytest.mark.asyncio
async def test_openai_prompt_cache_key_is_safe_hash() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(openai_config(), client=client)

    await collect(
        provider,
        ChatRequest(
            messages=[ChatMessage(role="user", content="hello user text")],
            tools=[read_file_spec()],
            prompt=prompt_bundle(),
        ),
    )
    await client.aclose()

    key = seen["payload"]["prompt_cache_key"]
    assert isinstance(key, str)
    assert key.startswith("mewcode:")
    assert "稳定规则" not in key
    assert "hello" not in key
    assert "/repo" not in key
    assert "sk-openai" not in key


@pytest.mark.asyncio
async def test_openai_prompt_cache_retention_is_configurable() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(
        openai_config(prompt_cache=PromptCacheConfig(openai_retention="24h")),
        client=client,
    )

    await collect(provider, ChatRequest(messages=[], tools=[read_file_spec()], prompt=prompt_bundle()))
    await client.aclose()

    assert seen["payload"]["prompt_cache_retention"] == "24h"


@pytest.mark.asyncio
async def test_openai_prompt_cache_options_can_be_disabled() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(
        openai_config(prompt_cache=PromptCacheConfig(enabled=False, openai_retention="24h")),
        client=client,
    )

    await collect(provider, ChatRequest(messages=[], tools=[read_file_spec()], prompt=prompt_bundle()))
    await client.aclose()

    assert "prompt_cache_key" not in seen["payload"]
    assert "prompt_cache_retention" not in seen["payload"]


@pytest.mark.asyncio
async def test_openai_prompt_cache_key_can_be_disabled_independently() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(
        openai_config(prompt_cache=PromptCacheConfig(openai_cache_key=False)),
        client=client,
    )

    await collect(provider, ChatRequest(messages=[], tools=[read_file_spec()], prompt=prompt_bundle()))
    await client.aclose()

    assert "prompt_cache_key" not in seen["payload"]
    assert "prompt_cache_retention" not in seen["payload"]


@pytest.mark.asyncio
async def test_openai_payload_includes_tool_messages() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(openai_config(), client=client)
    tool_call = ToolCall(
        id="call-1",
        name="read_file",
        arguments={"path": "README.md"},
        raw_arguments='{"path":"README.md"}',
    )

    await collect(
        provider,
        ChatRequest(
            messages=[
                ChatMessage(role="assistant", content="", tool_calls=(tool_call,)),
                ChatMessage(role="tool", content='{"success":true}', tool_call_id="call-1"),
            ],
        ),
    )
    await client.aclose()

    messages = seen["payload"]["messages"]
    assert messages[0]["role"] == "assistant"
    assert messages[0]["tool_calls"][0]["id"] == "call-1"
    assert messages[0]["tool_calls"][0]["function"]["name"] == "read_file"
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == '{"path":"README.md"}'
    assert messages[1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": '{"success":true}',
    }


@pytest.mark.asyncio
async def test_openai_streams_text_and_done() -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))
    events = await collect(OpenAIProvider(openai_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    assert [event.type for event in events] == ["message_start", "text_delta", "text_delta", "message_done"]
    assert events[1].text == "你"
    assert events[-1].message is not None
    assert events[-1].message.content == "你好"


@pytest.mark.asyncio
async def test_openai_streams_usage_event() -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
        "data: [DONE]\n\n"
    )

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))
    events = await collect(OpenAIProvider(openai_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    assert [event.type for event in events] == ["message_start", "text_delta", "usage", "message_done"]
    usage = events[2].usage
    assert usage is not None
    assert usage.input_tokens == 3
    assert usage.output_tokens == 2
    assert usage.total_tokens == 5
    assert usage.provider == "openai"
    assert usage.cache is not None
    assert usage.cache.status == "unknown"


@pytest.mark.asyncio
async def test_openai_streams_cache_hit_usage() -> None:
    body = (
        'data: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":2,"total_tokens":10,'
        '"prompt_tokens_details":{"cached_tokens":6}}}\n\n'
        "data: [DONE]\n\n"
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(OpenAIProvider(openai_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    usage = [event.usage for event in events if event.type == "usage"][0]
    assert usage is not None
    assert usage.cache is not None
    assert usage.cache.status == "hit"
    assert usage.cache.cached_tokens == 6


@pytest.mark.asyncio
async def test_openai_streams_cache_miss_usage() -> None:
    body = (
        'data: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":2,"total_tokens":10,'
        '"prompt_tokens_details":{"cached_tokens":0}}}\n\n'
        "data: [DONE]\n\n"
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(OpenAIProvider(openai_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    usage = [event.usage for event in events if event.type == "usage"][0]
    assert usage is not None
    assert usage.cache is not None
    assert usage.cache.status == "miss"
    assert usage.cache.cached_tokens == 0


@pytest.mark.asyncio
async def test_openai_usage_without_cache_fields_is_unknown() -> None:
    body = (
        'data: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":2,"total_tokens":10}}\n\n'
        "data: [DONE]\n\n"
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(OpenAIProvider(openai_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    usage = [event.usage for event in events if event.type == "usage"][0]
    assert usage is not None
    assert usage.cache is not None
    assert usage.cache.status == "unknown"


@pytest.mark.asyncio
async def test_openai_retries_without_cache_options_when_unsupported() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        payloads.append(payload)
        if len(payloads) == 1:
            return httpx.Response(400, content=b'{"error":{"message":"unknown parameter prompt_cache_key"}}')
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(openai_config(), client=client)

    events = await collect(provider, ChatRequest(messages=[], tools=[read_file_spec()], prompt=prompt_bundle()))
    await client.aclose()

    assert [event.type for event in events] == ["message_start", "message_done"]
    assert len(payloads) == 2
    assert "prompt_cache_key" in payloads[0]
    assert "prompt_cache_key" not in payloads[1]
    assert "prompt_cache_retention" not in payloads[1]


@pytest.mark.asyncio
async def test_openai_does_not_retry_non_cache_option_errors() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(400, content=b'{"error":{"message":"bad request"}}')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(openai_config(), client=client)

    with pytest.raises(ProviderError, match="bad request"):
        await collect(provider, ChatRequest(messages=[], tools=[read_file_spec()], prompt=prompt_bundle()))
    await client.aclose()

    assert len(payloads) == 1


@pytest.mark.asyncio
async def test_openai_http_error_is_redacted() -> None:
    secret = "sk-openai-secret-1234567890"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(401, content=f"bad {secret}".encode()))
    )

    with pytest.raises(ProviderError) as exc_info:
        await collect(OpenAIProvider(openai_config(secret), client=client), ChatRequest(messages=[]))
    await client.aclose()

    assert secret not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


@pytest.mark.asyncio
async def test_openai_invalid_chunk_raises_provider_error() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"data: nope\n\n")))

    with pytest.raises(ProviderError, match="JSON"):
        await collect(OpenAIProvider(openai_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()


def test_factory_returns_openai_provider() -> None:
    assert isinstance(create_provider(openai_config()), OpenAIProvider)


@pytest.mark.asyncio
async def test_openai_streams_tool_call_deltas_and_done() -> None:
    body = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function",'
        '"function":{"name":"read_file","arguments":"{\\"path\\""}}]}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":":\\"README.md\\"}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(OpenAIProvider(openai_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    assert [event.type for event in events] == [
        "message_start",
        "tool_call_delta",
        "tool_call_delta",
        "message_done",
    ]
    done = events[-1].message
    assert done is not None
    assert done.tool_calls[0].id == "call-1"
    assert done.tool_calls[0].name == "read_file"
    assert done.tool_calls[0].arguments == {"path": "README.md"}
    assert done.tool_calls[0].parse_error is None


@pytest.mark.asyncio
async def test_openai_tool_call_invalid_json_becomes_parse_error() -> None:
    body = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function",'
        '"function":{"name":"read_file","arguments":"{"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode())))

    events = await collect(OpenAIProvider(openai_config(), client=client), ChatRequest(messages=[]))
    await client.aclose()

    done = events[-1].message
    assert done is not None
    assert done.tool_calls[0].raw_arguments == "{"
    assert done.tool_calls[0].parse_error is not None
