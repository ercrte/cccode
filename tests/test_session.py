from __future__ import annotations

from pathlib import Path

from mewcode.context.models import ContextSummary
from mewcode.prompting.base import PromptBlock, PromptBundle
from mewcode.providers.base import ChatMessage
from mewcode.session import ChatSession, PendingPlan
from mewcode.tools.base import ToolResult, ToolSpec
from mewcode.session_id import is_valid_session_id


def test_empty_session_builds_empty_request() -> None:
    session = ChatSession()
    assert session.build_request().messages == ()


def test_second_turn_receives_previous_context() -> None:
    session = ChatSession()
    session.append_user_message("记住代号 Mew-17")
    session.append_assistant_message(ChatMessage(role="assistant", content="已记住"))
    session.append_user_message("我的代号是什么？")

    request = session.build_request()

    assert [message.role for message in request.messages] == ["user", "assistant", "user"]
    assert request.messages[0].content == "记住代号 Mew-17"


def test_session_appends_user_metadata() -> None:
    session = ChatSession()
    message = session.append_user_message("团队消息", metadata={"team_message_id": "msg-1"})
    assert message.metadata == {"team_message_id": "msg-1"}


def test_session_does_not_write_history_to_disk(tmp_path: Path) -> None:
    session = ChatSession()
    session.append_user_message("hello")
    session.append_assistant_message(ChatMessage(role="assistant", content="world"))

    assert list(tmp_path.iterdir()) == []


def test_session_can_append_tool_result_and_tools() -> None:
    session = ChatSession()
    result = ToolResult("call-1", "read_file", True, {"content": "hello"})
    tool = ToolSpec("read_file", "读取文件", {"type": "object"})

    session.append_tool_result(result)
    request = session.build_request(tools=[tool])

    assert request.messages[0].role == "tool"
    assert request.messages[0].tool_call_id == "call-1"
    assert request.messages[0].tool_result_is_error is False
    assert '"success": true' in request.messages[0].content
    assert request.tools == (tool,)


def test_session_build_request_accepts_prompt() -> None:
    session = ChatSession()
    prompt = PromptBundle(
        stable_blocks=(PromptBlock("identity", "身份", "稳定", stable=True, cacheable=True),),
        runtime_blocks=(PromptBlock("runtime", "运行时", "动态", stable=False),),
    )

    request = session.build_request(prompt=prompt)

    assert request.prompt == prompt


def test_session_prompt_does_not_pollute_history() -> None:
    session = ChatSession()
    session.append_user_message("hello")
    prompt = PromptBundle(
        stable_blocks=(PromptBlock("identity", "身份", "稳定", stable=True, cacheable=True),),
        runtime_blocks=(PromptBlock("runtime", "运行时", "动态", stable=False),),
    )

    request = session.build_request(prompt=prompt)

    assert [message.role for message in session.messages] == ["user"]
    assert [message.role for message in request.messages] == ["user"]
    assert request.prompt is prompt


def test_session_saves_replaces_and_clears_pending_plan() -> None:
    session = ChatSession()
    first = PendingPlan(source_request="需求一", plan_text="计划一")
    second = PendingPlan(source_request="需求二", plan_text="计划二")

    session.save_pending_plan(first)
    assert session.pending_plan == first

    session.save_pending_plan(second)
    assert session.pending_plan == second

    session.clear_pending_plan()
    assert session.pending_plan is None


def test_session_has_context_state() -> None:
    session = ChatSession()

    assert session.context_state.session_id
    assert is_valid_session_id(session.context_state.session_id)
    assert session.context_state.summary is None


def test_session_can_replace_messages() -> None:
    session = ChatSession()
    session.append_user_message("old")

    session.replace_messages([ChatMessage(role="user", content="new")])

    assert [message.content for message in session.messages] == ["new"]


def test_session_sets_context_summary() -> None:
    session = ChatSession()
    summary = ContextSummary(
        content="摘要",
        boundary_notice="边界",
        created_at="now",
        source_message_count=2,
        kept_message_count=1,
    )

    session.set_context_summary(summary)

    assert session.context_state.summary == summary


def test_session_recorder_appends_messages() -> None:
    recorder = FakeRecorder()
    session = ChatSession()
    session.set_recorder(recorder)

    user = session.append_user_message("hello")
    assistant = ChatMessage(role="assistant", content="world")
    session.append_assistant_message(assistant)
    tool = session.append_tool_result(ToolResult("call-1", "read_file", True, {"content": "ok"}))

    assert recorder.messages == [user, assistant, tool]


def test_session_appends_checkpoint_to_recorder() -> None:
    recorder = FakeRecorder()
    session = ChatSession()
    session.set_recorder(recorder)
    session.append_user_message("hello")
    summary = ContextSummary(
        content="摘要",
        boundary_notice="边界",
        created_at="now",
        source_message_count=1,
        kept_message_count=1,
    )
    session.set_context_summary(summary)

    session.append_checkpoint()

    assert recorder.checkpoints == [((session.messages[0],), summary)]


class FakeRecorder:
    def __init__(self) -> None:
        self.messages = []
        self.checkpoints = []

    def append_message(self, message) -> None:
        self.messages.append(message)

    def append_checkpoint(self, messages, summary) -> None:
        self.checkpoints.append((tuple(messages), summary))
