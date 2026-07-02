from __future__ import annotations

import json
from pathlib import Path

from mewcode.context.compactor import ToolResultCompactor
from mewcode.context.estimator import TokenEstimator
from mewcode.context.models import ContextConfig
from mewcode.context.store import ContextStore
from mewcode.providers.base import ChatMessage
from mewcode.session import ChatSession
from mewcode.tools.base import ToolCall


def make_compactor(tmp_path: Path, config: ContextConfig) -> ToolResultCompactor:
    estimator = TokenEstimator(config)
    return ToolResultCompactor(config, estimator, ContextStore(tmp_path, config))


def tool_message(content: str, call_id: str = "call-1", *, is_error: bool = False) -> ChatMessage:
    return ChatMessage(role="tool", content=content, tool_call_id=call_id, tool_result_is_error=is_error)


def test_context_store_writes_tool_result_under_project(tmp_path: Path) -> None:
    store = ContextStore(tmp_path, ContextConfig())
    message = tool_message('{"content":"hello"}')

    ref = store.write_tool_result(session_id="s1", message=message, estimated_tokens=12)

    path = tmp_path / ref.path
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["content"] == message.content
    assert payload["tool_call_id"] == "call-1"
    assert ".mewcode/context/" in Path(".gitignore").read_text(encoding="utf-8")


def test_context_store_returns_readable_relative_path(tmp_path: Path) -> None:
    ref = ContextStore(tmp_path).write_tool_result(
        session_id="s1",
        message=tool_message("hello"),
        estimated_tokens=1,
    )

    assert not Path(ref.path).is_absolute()
    assert (tmp_path / ref.path).read_text(encoding="utf-8")


def test_compacts_single_large_tool_result(tmp_path: Path) -> None:
    config = ContextConfig(single_tool_result_tokens=5, tool_preview_chars=10)
    session = ChatSession(messages=[tool_message("x" * 200)])

    result = make_compactor(tmp_path, config).compact(session)

    assert result.changed is True
    assert result.external_refs
    payload = json.loads(session.messages[0].content)
    assert payload["mewcode_externalized"] is True
    assert payload["external_path"] == result.external_refs[0].path
    assert payload["preview"] == "x" * 10


def test_light_compaction_keeps_user_messages_verbatim(tmp_path: Path) -> None:
    config = ContextConfig(single_tool_result_tokens=5)
    session = ChatSession(messages=[ChatMessage(role="user", content="原始用户消息"), tool_message("x" * 200)])

    make_compactor(tmp_path, config).compact(session)

    assert session.messages[0].content == "原始用户消息"


def test_compactor_does_not_reexternalize_existing_preview(tmp_path: Path) -> None:
    content = json.dumps({"mewcode_externalized": True, "external_path": ".mewcode/context/a.json"})
    session = ChatSession(messages=[tool_message(content)])

    result = make_compactor(tmp_path, ContextConfig(single_tool_result_tokens=1)).compact(session)

    assert result.changed is False
    assert result.external_refs == ()


def test_compacts_largest_results_when_turn_total_exceeds_limit(tmp_path: Path) -> None:
    config = ContextConfig(single_tool_result_tokens=10_000, turn_tool_result_tokens=50, tool_preview_chars=5)
    session = ChatSession(
        messages=[
            ChatMessage(
                role="assistant",
                tool_calls=(ToolCall("a", "read"), ToolCall("b", "search"), ToolCall("c", "read")),
            ),
            tool_message("x" * 300, "a"),
            tool_message("y" * 20, "b"),
            tool_message("z" * 250, "c"),
        ]
    )

    result = make_compactor(tmp_path, config).compact(session)

    assert result.changed is True
    assert len(result.external_refs) >= 1
    assert json.loads(session.messages[1].content)["mewcode_externalized"] is True
    assert session.messages[2].content == "y" * 20


def test_turn_compaction_keeps_small_results_when_under_limit(tmp_path: Path) -> None:
    config = ContextConfig(single_tool_result_tokens=10_000, turn_tool_result_tokens=10_000)
    session = ChatSession(
        messages=[
            ChatMessage(role="assistant", tool_calls=(ToolCall("a", "read"),)),
            tool_message("small", "a"),
        ]
    )

    result = make_compactor(tmp_path, config).compact(session)

    assert result.changed is False
    assert session.messages[1].content == "small"


def test_compacts_failed_tool_results_with_same_rules(tmp_path: Path) -> None:
    config = ContextConfig(single_tool_result_tokens=5)
    session = ChatSession(messages=[tool_message("error" * 100, is_error=True)])

    make_compactor(tmp_path, config).compact(session)

    payload = json.loads(session.messages[0].content)
    assert payload["tool_result_is_error"] is True
