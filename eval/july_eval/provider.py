from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from julycode.providers.base import ChatMessage, ChatRequest, StreamEvent, TokenUsage
from julycode.tools.base import ToolCall


class ScriptedEvalProvider:
    """确定性的离线 Provider，用真实 Agent loop 驱动固定评测场景。"""

    def __init__(self, *, provider_name: str = "scripted-eval") -> None:
        self.provider_name = provider_name
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        await asyncio.sleep(0)
        message = self._response_for(request)
        yield StreamEvent(type="usage", usage=self._usage(message))
        if message.content:
            yield StreamEvent(type="text_delta", text=message.content)
        yield StreamEvent(type="message_done", message=message)

    def _response_for(self, request: ChatRequest) -> ChatMessage:
        latest_user = self._latest_user(request)
        if "你正在为 JulyCode 压缩较早的会话历史" in latest_user:
            return ChatMessage(
                role="assistant",
                content="<analysis_draft>压缩评测历史。</analysis_draft><final_summary>保留了评测目标、上下文压缩证据和后续待办。</final_summary>",
            )

        case_id = self._case_id(latest_user)
        tool_names = self._tool_result_names(request)
        if case_id == "basic_qa":
            return ChatMessage(role="assistant", content="JulyCode 是一个终端 AI 编程助手，评测重点是可靠、安全、可验证。")
        if case_id == "readonly_search":
            if "read_file" not in tool_names:
                return self._tool("readonly-read", "read_file", {"path": "README.md"})
            return ChatMessage(role="assistant", content="已读取 README。JulyCode 的定位是终端 AI 编程助手。")
        if case_id == "multi_tool_loop":
            if "search_code" not in tool_names:
                return self._tool("multi-search", "search_code", {"pattern": "AgentLoopRunner", "path": "src", "max_results": 5})
            if "read_file" not in tool_names:
                return self._tool("multi-read", "read_file", {"path": "src/julycode/agent.py"})
            return ChatMessage(role="assistant", content="已通过搜索和读取确认 AgentLoopRunner 会调度模型、工具和停止事件。")
        if case_id == "code_location_reliability":
            if "search_code" not in tool_names:
                return self._tool(
                    "location-search",
                    "search_code",
                    {
                        "pattern": "class SearchCodeTool",
                        "path": "src/julycode/tools/builtin.py",
                        "max_results": 5,
                    },
                )
            if "read_file" not in tool_names:
                return self._tool(
                    "location-read",
                    "read_file",
                    {
                        "path": "src/julycode/tools/builtin.py",
                        "offset": 1,
                        "limit": 8,
                    },
                )
            return ChatMessage(
                role="assistant",
                content="已定位 SearchCodeTool：它优先尝试 ripgrep，后端不可用时回退到 Python 搜索，并用局部读取确认了核心逻辑。",
            )
        if case_id == "write_and_verify":
            if "read_file" not in tool_names:
                return self._tool("write-read", "read_file", {"path": "app.py"})
            if "write_file" not in tool_names:
                return self._tool(
                    "write-file",
                    "write_file",
                    {"path": "app.py", "content": "def answer():\n    return 'mew-eval-ok'\n"},
                )
            if "run_command" not in tool_names:
                return self._tool(
                    "write-check",
                    "run_command",
                    {"command": "python -m py_compile app.py", "timeout_seconds": 5},
                )
            return ChatMessage(role="assistant", content="已修改 app.py，并用 py_compile 完成验证，结果为 mew-eval-ok。")
        if case_id == "permission_recovery":
            if "run_command" not in tool_names:
                return self._tool("danger-denied", "run_command", {"command": "rm -rf /", "timeout_seconds": 1})
            return ChatMessage(role="assistant", content="高危删除命令已被权限系统拒绝；我改为说明风险和安全替代方案，不继续执行危险操作。")
        if case_id == "context_compaction":
            return ChatMessage(role="assistant", content="已保留上下文压缩后的目标，并确认上下文连续性没有丢失。")
        if case_id == "skill_or_subagent":
            if "delegate_agent" not in tool_names:
                return self._tool(
                    "subagent-delegate",
                    "delegate_agent",
                    {"type": "defined", "role": "reviewer", "task": "审查 README.md 的评测说明"},
                )
            if "load_skill" not in tool_names:
                return self._tool("skill-load", "load_skill", {"name": "review", "input": "README.md"})
            return ChatMessage(role="assistant", content="已加载 review Skill，并委派 reviewer 子 Agent 完成审查，结论是评测流程可复核。")
        return ChatMessage(role="assistant", content="离线评测用例已完成。")

    def _tool(self, call_id: str, name: str, arguments: dict[str, object]) -> ChatMessage:
        return ChatMessage(role="assistant", content="", tool_calls=(ToolCall(call_id, name, arguments),))

    def _usage(self, message: ChatMessage) -> TokenUsage:
        output_tokens = max(1, len(message.content) // 4)
        return TokenUsage(input_tokens=120, output_tokens=output_tokens, total_tokens=120 + output_tokens, provider=self.provider_name)

    def _latest_user(self, request: ChatRequest) -> str:
        for message in reversed(request.messages):
            if message.role == "user":
                return message.content
        return ""

    def _case_id(self, user_text: str) -> str:
        marker = "EVAL_CASE:"
        if marker not in user_text:
            return ""
        return user_text.split(marker, 1)[1].split()[0].strip()

    def _tool_result_names(self, request: ChatRequest) -> tuple[str, ...]:
        names = []
        for message in request.messages:
            if message.role != "tool":
                continue
            try:
                payload = json.loads(message.content)
            except json.JSONDecodeError:
                continue
            name = payload.get("tool_name")
            if isinstance(name, str):
                names.append(name)
        return tuple(names)
