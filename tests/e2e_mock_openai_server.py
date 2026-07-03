from __future__ import annotations

import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return

        body = self._read_json()
        self._write_request_log(body)
        if self._should_return_error(body):
            self.send_error(500, "mock provider error")
            return

        tool_calls = self._tool_calls(body)
        text = self._response_text(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        if tool_calls:
            self._write_tool_calls(tool_calls)
            self._write_usage()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        for chunk in _chunks(text, 3):
            payload = json.dumps({"choices": [{"delta": {"content": chunk}}]}, ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.05)
        self._write_usage()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _response_text(self, body: dict[str, Any]) -> str:
        messages = body.get("messages", [])
        if messages and messages[-1].get("role") == "tool":
            if "不允许再次委派" in str(messages[-1].get("content", "")):
                return "嵌套委派被拒绝，已按安全限制结束子任务。"
            if "嵌套委派被拒绝" in str(messages[-1].get("content", "")):
                return "嵌套委派被拒绝，已按安全限制结束子任务。"
            if "delegate_agent" in str(messages[-1].get("content", "")):
                if '"worktree"' in str(messages[-1].get("content", "")):
                    return f"Worktree 隔离任务已完成：{str(messages[-1].get('content', ''))[:500]}"
                return "子 Agent 结果已收到，主任务继续完成。"
            if "permission_" in str(messages[-1].get("content", "")):
                return "权限被拒绝，已改用安全方案说明。"
            if _contains_user_text(messages, "迭代上限"):
                return ""
            if _contains_user_text(messages, "连续未知工具"):
                return ""
            if "load_skill" in str(messages[-1].get("content", "")):
                return "review Skill 已加载并完成审查。"
            if _contains_model_text(messages, "当前待执行计划") or _contains_model_text(messages, "请执行下面这份已确认的计划"):
                return "计划已执行完成。"
            if _contains_user_text(messages, "计划") or _contains_model_text(messages, "模式状态：plan") or _contains_model_text(messages, "请先制定执行计划"):
                return "计划：先读取相关文件，再总结需要执行的步骤。"
            if _contains_user_text(messages, "连续工具"):
                return ""
            if _contains_user_text(messages, "多步"):
                tool_count = sum(1 for message in messages if message.get("role") == "tool")
                if tool_count >= 2:
                    return "多步任务已完成：已读取并搜索项目信息。"
            return f"工具结果已收到：{messages[-1].get('content', '')[:120]}"
        last = messages[-1].get("content", "") if messages else ""
        if "你正在为 MewCode 压缩较早的会话历史" in str(last):
            return (
                "<analysis_draft>梳理当前对话和工具结果。</analysis_draft>"
                "<final_summary>## 当前目标和约束\n继续完成用户请求。\n\n"
                "## 用户明确要求\n保留原始用户意图。\n\n"
                "## 已完成工作和关键决策\n已读取工具结果并保存必要索引。\n\n"
                "## 重要文件或工具结果索引\n查看外置路径获取完整细节。\n\n"
                "## 待办事项和阻塞\n无明确阻塞。\n\n"
                "## 验证状态与风险\n需要继续按工具结果验证。</final_summary>"
            )
        if "你正在为 MewCode 更新长期记忆" in str(last):
            return _memory_operations(str(last))
        if str(last).strip() == "慢速审查":
            time.sleep(8)
            return "慢速子 Agent 已完成。"
        if _contains_model_text(messages, '<active_sub_agent') and _contains_model_text(messages, 'type="fork"'):
            return "Fork 后台子 Agent 已完成检查。"
        if _contains_model_text(messages, '<active_sub_agent'):
            if _contains_model_text(messages, "慢速审查"):
                time.sleep(8)
                return "慢速子 Agent 已完成。"
            return "定义式子 Agent 已完成委派任务。"
        if "测试命名规则" in last and _contains_model_text(messages, "test_memory_"):
            return "本项目新增测试命名必须以 test_memory_ 开头。"
        if "什么语言" in last and _contains_model_text(messages, "默认用中文"):
            return "我应该默认用中文回答。"
        if "请先制定执行计划" in last:
            return "计划：先读取相关文件，再总结需要执行的步骤。"
        if "我的代号" in last or "what is my code" in last.lower():
            return "你的代号是 Mew-17。"
        if "记住" in last or "remember" in last.lower():
            return "已记住 Mew-17。"
        return "递归是把问题拆成与自身相似的更小问题。"

    def _tool_calls(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        messages = body.get("messages", [])
        if not body.get("tools") or not messages:
            return []
        team_calls = _team_e2e_tool_calls(body)
        if team_calls is not None:
            return team_calls
        if messages[-1].get("role") == "tool":
            if _contains_user_text(messages, "迭代上限"):
                return [{"name": "read_file", "arguments": {"path": "README.md"}}]
            if _contains_user_text(messages, "连续未知工具"):
                return [{"name": "missing_tool", "arguments": {}}]
            if _contains_model_text(messages, "当前待执行计划") or _contains_model_text(messages, "请执行下面这份已确认的计划"):
                return []
            if _contains_model_text(messages, "模式状态：plan") or _contains_model_text(messages, "请先制定执行计划"):
                return []
            if _contains_user_text(messages, "多步"):
                tool_count = sum(1 for message in messages if message.get("role") == "tool")
                if tool_count == 1:
                    return [{"name": "search_code", "arguments": {"pattern": "ChatSession"}}]
                return []
            if _contains_user_text(messages, "连续工具"):
                return [{"name": "read_file", "arguments": {"path": "README.md"}}]
            return []
        last = str(messages[-1].get("content", ""))
        lowered = last.lower()
        if _contains_model_text(messages, '<active_sub_agent') and ("再委派" in last or "嵌套" in last):
            return [{"name": "delegate_agent", "arguments": {"type": "defined", "role": "reviewer", "task": "嵌套委派"}}]
        if (
            _contains_model_text(messages, '<active_sub_agent')
            and _contains_model_text(messages, 'isolation="worktree"')
            and "isolated.txt" in last
        ):
            return [{"name": "write_file", "arguments": {"path": "isolated.txt", "content": "written in worktree"}}]
        if "Worktree 隔离子 Agent" in last and _has_tool(body, "delegate_agent"):
            return [
                {
                    "name": "delegate_agent",
                    "arguments": {
                        "type": "defined",
                        "role": "worktree-writer",
                        "task": "在隔离目录创建 isolated.txt，并返回结果",
                    },
                }
            ]
        if "Worktree 只读隔离子 Agent" in last and _has_tool(body, "delegate_agent"):
            return [
                {
                    "name": "delegate_agent",
                    "arguments": {
                        "type": "defined",
                        "role": "worktree-writer",
                        "task": "读取 README 并总结，不要修改文件",
                    },
                }
            ]
        if ("委派代码搜索子 Agent" in last or "代码搜索子 Agent" in last) and _has_tool(body, "delegate_agent"):
            return [
                {
                    "name": "delegate_agent",
                    "arguments": {
                        "type": "defined",
                        "role": "code-searcher",
                        "task": "查找 README 里 Skill 相关说明并总结",
                    },
                }
            ]
        if ("Fork 一个后台子 Agent" in last or "fork 一个后台" in lowered) and _has_tool(body, "delegate_agent"):
            return [
                {
                    "name": "delegate_agent",
                    "arguments": {
                        "type": "fork",
                        "task": "检查当前权限系统测试覆盖并完成后通知",
                        "background": False,
                    },
                }
            ]
        if ("子 Agent 再委派" in last or "让子 Agent 再委派" in last) and _has_tool(body, "delegate_agent"):
            return [
                {
                    "name": "delegate_agent",
                    "arguments": {
                        "type": "defined",
                        "role": "reviewer",
                        "task": "让子 Agent 再委派一个子 Agent",
                    },
                }
            ]
        if ("慢速前台子 Agent" in last or "触发一个前台定义式子 Agent" in last) and _has_tool(body, "delegate_agent"):
            return [
                {
                    "name": "delegate_agent",
                    "arguments": {
                        "type": "defined",
                        "role": "reviewer",
                        "task": "慢速审查",
                        "foreground_timeout_seconds": 30,
                    },
                }
            ]
        if "多个工具" in last or "多个读类" in last:
            return [
                {"name": "find_files", "arguments": {"pattern": "README.md"}},
                {"name": "read_file", "arguments": {"path": "README.md"}},
            ]
        if "危险命令" in last or "权限拒绝后调整" in last:
            return [{"name": "run_command", "arguments": {"command": "rm -rf /"}}]
        if "写入需要确认" in last:
            return [{"name": "write_file", "arguments": {"path": "tmp/permission-demo.txt", "content": "permission ok"}}]
        if "多步" in last:
            return [{"name": "read_file", "arguments": {"path": "README.md"}}]
        if "迭代上限" in last:
            return [{"name": "read_file", "arguments": {"path": "README.md"}}]
        if "连续未知工具" in last:
            return [{"name": "missing_tool", "arguments": {}}]
        if "local_demo" in last:
            return [{"name": "local_demo__echo", "arguments": {"text": _mcp_text(last, "hello-mcp")}}]
        if "remote_demo" in last:
            return [{"name": "remote_demo__echo", "arguments": {"text": _mcp_text(last, "http-mcp")}}]
        if "oauth_demo" in last and _has_tool(body, "oauth_demo__echo"):
            return [{"name": "oauth_demo__echo", "arguments": {"text": _mcp_text(last, "oauth-mcp")}}]
        if ("review skill" in lowered or "review Skill" in last or "用 review" in last) and _has_tool(body, "load_skill"):
            return [{"name": "load_skill", "arguments": {"name": "review", "input": _last_path(last) or "README.md"}}]
        if _contains_model_text(messages, "当前待执行计划") or "请执行下面这份已确认的计划" in last:
            return [{"name": "write_file", "arguments": {"path": "tmp/plan-do.txt", "content": "plan done"}}]
        if _contains_model_text(messages, "模式状态：plan") or "请先制定执行计划" in last:
            if "副作用" in last:
                return [{"name": "write_file", "arguments": {"path": "tmp/plan-blocked.txt", "content": "blocked"}}]
            return [{"name": "read_file", "arguments": {"path": "README.md"}}]
        if "不存在工具" in last:
            return [{"name": "missing_tool", "arguments": {}}]
        if "无效参数" in last:
            return [{"name": "read_file", "arguments": {}}]
        if "读取" in last or "read" in lowered:
            return [{"name": "read_file", "arguments": {"path": _last_path(last) or "README.md"}}]
        if "写入" in last or "创建" in last or "write" in lowered:
            return [{"name": "write_file", "arguments": {"path": "tmp/tool-demo.txt", "content": "MewCode tool ok"}}]
        if "修改" in last or "替换" in last or "edit" in lowered:
            return [
                {
                    "name": "edit_file",
                    "arguments": {
                        "path": _last_path(last) or "tmp/tool-edit.txt",
                        "old_text": "OLD",
                        "new_text": "NEW",
                    },
                }
            ]
        if "执行" in last or "command" in lowered:
            return [{"name": "run_command", "arguments": {"command": "python -c \"print('mew')\""}}]
        if "找" in last or "匹配" in last or "find" in lowered:
            return [{"name": "find_files", "arguments": {"pattern": "tests/test_*provider.py"}}]
        if "搜索" in last or "search" in lowered:
            return [{"name": "search_code", "arguments": {"pattern": "ChatSession"}}]
        return []

    def _write_tool_calls(self, tool_calls: list[dict[str, Any]]) -> None:
        parts = []
        for index, tool_call in enumerate(tool_calls):
            arguments = json.dumps(tool_call["arguments"], ensure_ascii=False)
            split_at = max(1, len(arguments) // 2)
            parts.append((index, tool_call, arguments[:split_at], arguments[split_at:]))
        first = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": f"call-mock-{index + 1}",
                                "type": "function",
                                "function": {"name": tool_call["name"], "arguments": first_args},
                            }
                            for index, tool_call, first_args, _ in parts
                        ]
                    }
                }
            ]
        }
        second = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": index,
                                "function": {"arguments": second_args},
                            }
                            for index, _, _, second_args in parts
                        ]
                    }
                }
            ]
        }
        for payload in (first, second):
            self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.05)

    def _write_usage(self) -> None:
        payload = {
            "choices": [],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 4,
                "total_tokens": 16,
                "prompt_tokens_details": {"cached_tokens": 4},
            },
        }
        self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _should_return_error(self, body: dict[str, Any]) -> bool:
        messages = body.get("messages", [])
        return bool(messages and "Provider 错误" in str(messages[-1].get("content", "")))

    def _write_request_log(self, body: dict[str, Any]) -> None:
        path = os.environ.get("MEWCODE_MOCK_REQUEST_LOG")
        if not path:
            return
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(body, ensure_ascii=False) + "\n")


def _chunks(text: str, size: int):
    for index in range(0, len(text), size):
        yield text[index:index + size]


def _contains_user_text(messages: list[dict[str, Any]], text: str) -> bool:
    return any(message.get("role") == "user" and text in str(message.get("content", "")) for message in messages)


def _contains_model_text(messages: list[dict[str, Any]], text: str) -> bool:
    return any(
        message.get("role") in {"developer", "system", "assistant"}
        and text in str(message.get("content", ""))
        for message in messages
    )


def _has_tool(body: dict[str, Any], name: str) -> bool:
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name") == name:
            return True
        if tool.get("name") == name:
            return True
    return False


def _last_path(text: str) -> str | None:
    for token in reversed(text.replace("：", " ").replace(":", " ").split()):
        if "/" in token or "." in token:
            return token.strip("`'\"，。")
    return None


def _mcp_text(text: str, default: str) -> str:
    for token in reversed(text.replace("：", " ").replace(":", " ").split()):
        stripped = token.strip("`'\"，。")
        if stripped.endswith("-mcp") or stripped == "ok":
            return stripped
    return default


def _memory_operations(text: str) -> str:
    if "默认用中文" in text:
        return json.dumps(
            {
                "operations": [
                    {
                        "action": "create",
                        "scope": "user",
                        "category": "preference",
                        "note_id": "default-chinese-replies",
                        "title": "默认中文回复",
                        "body": "用户偏好：以后回答默认用中文。",
                        "tags": ["language"],
                    }
                ]
            },
            ensure_ascii=False,
        )
    if "test_memory_" in text:
        return json.dumps(
            {
                "operations": [
                    {
                        "action": "create",
                        "scope": "project",
                        "category": "project_knowledge",
                        "note_id": "test-memory-naming",
                        "title": "测试命名约定",
                        "body": "本项目新增测试命名必须以 test_memory_ 开头。",
                        "tags": ["test", "naming"],
                    }
                ]
            },
            ensure_ascii=False,
        )
    return json.dumps({"operations": [{"action": "skip"}]}, ensure_ascii=False)


def _team_e2e_tool_calls(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    """为 tmux 验收提供确定性的长期团队工具脚本。"""
    messages = body.get("messages", [])
    if not _contains_user_text(messages, "团队端到端"):
        return None
    system_text = "\n".join(
        str(message.get("content", ""))
        for message in messages
        if message.get("role") in {"system", "developer"}
    )
    member_match = re.search(r'<mewcode_team name="([^"]+)" actor="([^"]+)" kind="member">', system_text)
    if member_match:
        return _team_member_calls(messages, member_match.group(2))
    return _team_lead_calls(messages)


def _team_lead_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls = _assistant_calls(messages)
    if not any(name == "manage_team" and args.get("action") == "create" for name, args in calls):
        return [{"name": "manage_team", "arguments": {"action": "create", "name": "e2e-team"}}]

    create_calls = [args for name, args in calls if name == "team_task" and args.get("action") == "create"]
    created = [
        data for data in _tool_result_data(messages, "team_task")
        if isinstance(data, dict) and data.get("id") and data.get("created_by") == "lead"
    ]
    if len(create_calls) == 0:
        return [{"name": "team_task", "arguments": {
            "action": "create", "title": "并行代码 A", "description": "创建 alice 成果文件",
            "kind": "code", "dependencies": [],
        }}]
    if len(create_calls) == 1:
        return [{"name": "team_task", "arguments": {
            "action": "create", "title": "并行代码 B", "description": "创建 bob 成果文件",
            "kind": "code", "dependencies": [],
        }}]
    if len(create_calls) == 2 and len(created) >= 2:
        return [{"name": "team_task", "arguments": {
            "action": "create", "title": "依赖汇总", "description": "汇总两个代码任务",
            "kind": "research", "dependencies": [created[0]["id"], created[1]["id"]],
        }}]

    spawn_calls = [args for name, args in calls if name == "manage_team_member" and args.get("action") == "spawn"]
    if len(spawn_calls) == 0:
        return [{"name": "manage_team_member", "arguments": {
            "action": "spawn", "name": "alice", "role": "team-writer",
            "backend": "coroutine", "require_approval": False,
        }}]
    if len(spawn_calls) == 1:
        return [{"name": "manage_team_member", "arguments": {
            "action": "spawn", "name": "bob", "role": "team-writer",
            "backend": "coroutine", "require_approval": True,
        }}]

    assignments = [
        args for name, args in calls
        if name == "team_message" and args.get("protocol") == "task_assignment"
    ]
    if len(assignments) == 0 and len(created) >= 3:
        return [{"name": "team_message", "arguments": {
            "action": "send", "recipient": "alice", "protocol": "task_assignment",
            "task_id": created[0]["id"], "body": "团队端到端：请领取并完成并行代码 A。",
        }}]
    if len(assignments) == 1 and len(created) >= 3:
        return [{"name": "team_message", "arguments": {
            "action": "send", "recipient": "bob", "protocol": "task_assignment",
            "task_id": created[1]["id"], "body": "团队端到端：请领取并完成并行代码 B，先提交计划。",
        }}]

    plan = _latest_team_message(messages, "plan_request")
    decisions = {
        str(args.get("approval_id"))
        for name, args in calls
        if name == "team_message" and args.get("protocol") in {"plan_approved", "plan_rejected"}
    }
    if plan and plan.get("approval") and plan["approval"] not in decisions:
        return [{"name": "team_message", "arguments": {
            "action": "send", "recipient": plan["sender"], "protocol": "plan_approved",
            "task_id": plan["task"], "approval_id": plan["approval"],
            "plan_version": int(plan["version"]), "body": "计划已核对，批准执行。",
        }}]

    tasks = _known_tasks(messages)
    if len(created) >= 3:
        dependency = tasks.get(str(created[2]["id"]), created[2])
        first_two_done = all(tasks.get(str(item["id"]), item).get("status") == "completed" for item in created[:2])
        dependency_assigned = any(args.get("task_id") == created[2]["id"] for args in assignments)
        if first_two_done and dependency.get("status") == "pending" and not dependency_assigned:
            return [{"name": "team_message", "arguments": {
                "action": "send", "recipient": "alice", "protocol": "task_assignment",
                "task_id": created[2]["id"], "body": "团队端到端：前置任务已完成，请领取依赖汇总任务。",
            }}]
        if tasks and all(tasks.get(str(item["id"]), item).get("status") == "completed" for item in created[:3]):
            return []
    return [{"name": "team_wait", "arguments": {"timeout_seconds": 0.2}}]


def _team_member_calls(messages: list[dict[str, Any]], actor: str) -> list[dict[str, Any]]:
    followup = _latest_team_message_body(messages, "message", "继续说明你之前改了什么")
    replied = any(
        name == "team_message" and args.get("recipient") == "lead"
        and "恢复原上下文" in str(args.get("body", ""))
        for name, args in _assistant_calls(messages)
    )
    if followup and not replied:
        return [{"name": "team_message", "arguments": {
            "action": "send", "recipient": "lead", "protocol": "message",
            "body": f"{actor} 已恢复原上下文：之前完成并提交了自己的团队任务。",
        }}]
    assignment = _latest_team_message(messages, "task_assignment")
    if not assignment or not assignment.get("task"):
        return []
    task_id = assignment["task"]
    calls = _assistant_calls(messages)
    tasks = _known_tasks(messages)
    task = tasks.get(task_id)
    if task is None or task.get("assignee") != actor:
        return [{"name": "team_task", "arguments": {"action": "claim", "task_id": task_id}}]
    if task.get("status") == "completed":
        return []
    if task.get("status") == "awaiting_approval":
        approved = _latest_team_message(messages, "plan_approved", task_id)
        requested = any(
            name == "team_message" and args.get("protocol") == "plan_request" and args.get("task_id") == task_id
            for name, args in calls
        )
        if not requested:
            return [{"name": "team_message", "arguments": {
                "action": "send", "recipient": "lead", "protocol": "plan_request",
                "task_id": task_id, "body": "计划 v1：写入独立成果文件，提交后报告 commit。",
            }}]
        if approved:
            return [{"name": "team_task", "arguments": {"action": "get", "task_id": task_id}}]
        return []
    if task.get("status") != "in_progress":
        return []

    peer = "bob" if actor == "alice" else "alice"
    collaborated = any(
        name == "team_message" and args.get("protocol", "message") == "message"
        and args.get("task_id") == task_id
        for name, args in calls
    )
    if not collaborated:
        return [{"name": "team_message", "arguments": {
            "action": "send", "recipient": peer, "protocol": "message", "task_id": task_id,
            "body": f"{actor} 已领取 {task_id}，完成后请直接查看共享任务。",
        }}]
    if task.get("kind") == "research":
        return [{"name": "team_task", "arguments": {
            "action": "complete", "task_id": task_id, "result": "两个并行代码任务均已提交并完成。",
        }}]

    path = f"team-{actor}.txt"
    actor_calls = [(name, args) for name, args in calls if args.get("task_id") == task_id or name in {"write_file", "run_command"}]
    if not any(name == "write_file" and args.get("path") == path for name, args in actor_calls):
        return [{"name": "write_file", "arguments": {"path": path, "content": f"{actor} team e2e\n"}}]
    commands = [str(args.get("command", "")) for name, args in actor_calls if name == "run_command"]
    if f"git add {path}" not in commands:
        return [{"name": "run_command", "arguments": {"command": f"git add {path}"}}]
    if f"git commit -m team-{actor}" not in commands:
        return [{"name": "run_command", "arguments": {"command": f"git commit -m team-{actor}"}}]
    if "git rev-parse HEAD" not in commands:
        return [{"name": "run_command", "arguments": {"command": "git rev-parse HEAD"}}]
    return [{"name": "team_task", "arguments": {
        "action": "complete", "task_id": task_id,
        "result": f"{actor} 已提交 {path}；commit 由系统从 clean Worktree HEAD 记录。",
    }}]


def _assistant_calls(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            try:
                arguments = json.loads(function.get("arguments", "{}"))
            except json.JSONDecodeError:
                arguments = {}
            if isinstance(arguments, dict):
                result.append((str(function.get("name", "")), arguments))
    return result


def _tool_result_data(messages: list[dict[str, Any]], tool_name: str) -> list[Any]:
    values: list[Any] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(str(message.get("content", "{}")))
        except json.JSONDecodeError:
            continue
        if payload.get("success") and payload.get("tool_name") == tool_name:
            values.append(payload.get("data"))
    return values


def _known_tasks(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    for data in _tool_result_data(messages, "team_task"):
        if isinstance(data, dict) and data.get("id"):
            tasks[str(data["id"])] = data
    for data in _tool_result_data(messages, "team_wait"):
        if not isinstance(data, dict):
            continue
        for task in data.get("tasks") or []:
            if isinstance(task, dict) and task.get("id"):
                tasks[str(task["id"])] = task
    return tasks


def _latest_team_message(
    messages: list[dict[str, Any]], protocol: str, task_id: str | None = None
) -> dict[str, str] | None:
    pattern = re.compile(r'<team_message\s+([^>]+)>')
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        for match in reversed(pattern.findall(str(message.get("content", "")))):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', match))
            if attrs.get("protocol") == protocol and (task_id is None or attrs.get("task") == task_id):
                return attrs
    return None


def _latest_team_message_body(
    messages: list[dict[str, Any]], protocol: str, body_text: str
) -> str | None:
    for message in reversed(messages):
        content = str(message.get("content", ""))
        if (
            message.get("role") == "user"
            and f'protocol="{protocol}"' in content
            and body_text in content
        ):
            return content
    return None


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18765
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"READY {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
