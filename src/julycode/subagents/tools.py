from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from julycode.subagents.models import BackgroundSubAgentRecord, SubAgentInvocation, SubAgentResult
from julycode.tools.base import ToolContext, ToolExecutionError, ToolSpec

DELEGATE_AGENT_TOOL_NAME = "delegate_agent"


class DelegateAgentTool:
    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.spec = ToolSpec(
            name=DELEGATE_AGENT_TOOL_NAME,
            description=(
                "把独立子任务委派给子 Agent。type=defined 使用预定义角色；"
                "type=fork 继承当前对话历史并强制后台运行。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["defined", "fork"],
                        "description": "委派类型：defined 或 fork",
                    },
                    "task": {"type": "string", "description": "要交给子 Agent 的明确子任务"},
                    "role": {"type": "string", "description": "defined 类型使用的角色名"},
                    "background": {"type": "boolean", "description": "是否显式进入后台"},
                    "max_iterations": {"type": "integer", "description": "可选最大轮次"},
                    "foreground_timeout_seconds": {"type": "number", "description": "前台等待转后台阈值"},
                },
                "required": ["type", "task"],
                "additionalProperties": False,
            },
            timeout_seconds=3600.0,
            safety="side_effect",
            origin="system",
        )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        _ = context
        invocation = _parse_invocation(arguments)
        try:
            result = await self.manager.delegate(invocation)
        except Exception as exc:
            raise ToolExecutionError(
                f"子 Agent 委派失败: {exc}",
                error_type="sub_agent_delegate_failed",
            ) from exc
        if isinstance(result, BackgroundSubAgentRecord):
            return _record_payload(result)
        if isinstance(result, SubAgentResult):
            return _result_payload(result)
        raise ToolExecutionError("子 Agent 管理器返回了未知结果", error_type="sub_agent_invalid_result")


def _parse_invocation(arguments: Mapping[str, Any]) -> SubAgentInvocation:
    raw_type = str(arguments.get("type", "")).strip()
    if raw_type not in {"defined", "fork"}:
        raise ToolExecutionError("type 必须是 defined 或 fork", error_type="invalid_arguments")
    task = str(arguments.get("task", "")).strip()
    if not task:
        raise ToolExecutionError("task 不能为空", error_type="invalid_arguments")
    role = arguments.get("role")
    role_text = str(role).strip() if role is not None else None
    if raw_type == "defined" and not role_text:
        raise ToolExecutionError("defined 类型必须提供 role", error_type="invalid_arguments")

    background = bool(arguments.get("background", False))
    if raw_type == "fork":
        background = True

    max_iterations = _optional_positive_int(arguments.get("max_iterations"), "max_iterations")
    foreground_timeout = _optional_positive_float(
        arguments.get("foreground_timeout_seconds"),
        "foreground_timeout_seconds",
    )
    return SubAgentInvocation(
        type=raw_type,  # type: ignore[arg-type]
        task=task,
        role=role_text,
        background=background,
        max_iterations=max_iterations,
        foreground_timeout_seconds=foreground_timeout,
    )


def _optional_positive_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolExecutionError(f"{field} 必须是正整数", error_type="invalid_arguments") from exc
    if parsed <= 0:
        raise ToolExecutionError(f"{field} 必须是正整数", error_type="invalid_arguments")
    return parsed


def _optional_positive_float(value: Any, field: str) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ToolExecutionError(f"{field} 必须是正数", error_type="invalid_arguments") from exc
    if parsed <= 0:
        raise ToolExecutionError(f"{field} 必须是正数", error_type="invalid_arguments")
    return parsed


def _record_payload(record: BackgroundSubAgentRecord) -> dict[str, Any]:
    payload = {
        "background": True,
        "task_id": record.task_id,
        "type": record.invocation.type,
        "role": record.invocation.role,
        "status": record.status,
        "task": record.invocation.task,
        "message": f"子 Agent 任务已进入后台: {record.task_id}",
    }
    if record.worktree_lease is not None:
        payload["worktree"] = {
            "root": str(record.worktree_lease.root),
            "cwd": str(record.worktree_lease.cwd),
            "branch": record.worktree_lease.metadata.branch,
        }
    return payload


def _result_payload(result: SubAgentResult) -> dict[str, Any]:
    payload = {
        "background": False,
        "task_id": result.task_id,
        "type": result.type,
        "role": result.role,
        "status": result.status,
        "task": result.task,
        "summary": result.summary,
        "final_text": result.final_text,
        "stop_reason": result.stop_reason,
        "key_outputs": list(result.key_outputs),
        "error": result.error,
        "usage": _usage_payload(result),
    }
    if result.worktree is not None:
        payload["worktree"] = {
            "root": result.worktree.root,
            "cwd": result.worktree.cwd,
            "branch": result.worktree.branch,
            "base_commit": result.worktree.base_commit,
            "disposition": result.worktree.disposition,
            "reason": result.worktree.reason,
        }
    return payload


def _usage_payload(result: SubAgentResult) -> dict[str, Any] | None:
    usage = result.usage
    if usage is None:
        return None
    cache = None
    if usage.cache is not None:
        cache = {
            "status": usage.cache.status,
            "read_input_tokens": usage.cache.read_input_tokens,
            "creation_input_tokens": usage.cache.creation_input_tokens,
            "cached_tokens": usage.cache.cached_tokens,
            "supported": usage.cache.supported,
        }
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "provider": usage.provider,
        "cache": cache,
    }
