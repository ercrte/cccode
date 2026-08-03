from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from julycode.teams.manager import TeamManager
from julycode.teams.models import MemberSpawnRequest, MessageDraft, TaskDraft, TaskPatch, TaskResult, TeamDataError
from julycode.tools.base import ToolContext, ToolExecutionError, ToolSpec


MANAGE_TEAM_TOOL = "manage_team"
MANAGE_TEAM_MEMBER_TOOL = "manage_team_member"
TEAM_TASK_TOOL = "team_task"
TEAM_MESSAGE_TOOL = "team_message"
TEAM_WAIT_TOOL = "team_wait"


class _TeamTool:
    def __init__(self, manager: TeamManager, spec: ToolSpec) -> None:
        self.manager = manager
        self.spec = spec

    async def _actor(self, context: ToolContext):
        try:
            return await self.manager.actor_for(context.principal)
        except TeamDataError as exc:
            raise ToolExecutionError(str(exc), error_type="team_identity_error") from exc

    def _error(self, exc: Exception) -> ToolExecutionError:
        return ToolExecutionError(str(exc), error_type="team_error")


class ManageTeamTool(_TeamTool):
    def __init__(self, manager: TeamManager) -> None:
        super().__init__(manager, ToolSpec(
            name=MANAGE_TEAM_TOOL,
            description="创建、列出、打开、关闭或查看长期团队。",
            parameters_schema={
                "type": "object",
                "properties": {"action": {"type": "string", "enum": ["create", "list", "open", "close", "status"]}, "name": {"type": "string"}},
                "required": ["action"], "additionalProperties": False,
            }, safety="side_effect", origin="teams",
        ))

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext):
        if context.principal.kind != "main":
            raise ToolExecutionError("只有主 Agent 可以管理团队", error_type="team_identity_error")
        action = str(arguments.get("action", ""))
        name = str(arguments.get("name", "")).strip()
        try:
            if action == "create":
                return asdict(await self.manager.create_team(_required(name, "name")))
            if action == "list":
                return {"teams": [asdict(item) for item in await self.manager.list_teams()]}
            if action == "open":
                return asdict(await self.manager.open_team(_required(name, "name")))
            if action == "close":
                await self.manager.close_team()
                return {"closed": True}
            if action == "status":
                return asdict(await self.manager.status())
            raise TeamDataError(f"未知团队 action: {action}")
        except TeamDataError as exc:
            raise self._error(exc) from exc


class ManageTeamMemberTool(_TeamTool):
    def __init__(self, manager: TeamManager) -> None:
        super().__init__(manager, ToolSpec(
            name=MANAGE_TEAM_MEMBER_TOOL,
            description="派生、列出或终止长期团队成员。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["spawn", "list", "terminate"]},
                    "name": {"type": "string"}, "role": {"type": "string"},
                    "backend": {"type": "string"}, "require_approval": {"type": "boolean"},
                }, "required": ["action"], "additionalProperties": False,
            }, safety="side_effect", origin="teams",
        ))

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext):
        actor = await self._actor(context)
        if actor.kind != "lead":
            raise ToolExecutionError("只有 Team Lead 可以管理成员", error_type="team_identity_error")
        action = str(arguments.get("action", ""))
        try:
            if action == "spawn":
                request = MemberSpawnRequest(
                    _required(str(arguments.get("name", "")).strip(), "name"),
                    _required(str(arguments.get("role", "")).strip(), "role"),
                    bool(arguments.get("require_approval", False)),
                    str(arguments.get("backend", "coroutine")),
                )
                return asdict(await self.manager.spawn_member(request))
            if action == "list":
                return {"members": [asdict(item) for item in (await self.manager.status()).team.members.values()]}
            if action == "terminate":
                return asdict(await self.manager.terminate_member(_required(str(arguments.get("name", "")).strip(), "name")))
            raise TeamDataError(f"未知成员 action: {action}")
        except TeamDataError as exc:
            raise self._error(exc) from exc


class TeamTaskTool(_TeamTool):
    def __init__(self, manager: TeamManager) -> None:
        super().__init__(manager, ToolSpec(
            name=TEAM_TASK_TOOL, description="增删查改、领取或完成共享团队任务。",
            parameters_schema={
                "type": "object", "properties": {
                    "action": {"type": "string", "enum": ["create", "get", "list", "update", "delete", "claim", "complete", "fail"]},
                    "task_id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
                    "kind": {"type": "string", "enum": ["code", "research"]},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string"}, "result": {"type": "string"}, "reason": {"type": "string"},
                    "commit": {"type": "string"},
                }, "required": ["action"], "additionalProperties": False,
            }, safety="side_effect", origin="teams",
        ))

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext):
        actor = await self._actor(context)
        services = self.manager._service(actor.team_name)
        action = str(arguments.get("action", ""))
        task_id = str(arguments.get("task_id", "")).strip()
        try:
            if action == "create":
                draft = TaskDraft(
                    _required(str(arguments.get("title", "")).strip(), "title"),
                    str(arguments.get("description", "")),
                    str(arguments.get("kind", "code")),  # type: ignore[arg-type]
                    tuple(str(item) for item in arguments.get("dependencies", []) or []),
                )
                result = await services.tasks.create(actor, draft)
            elif action == "get": result = await services.tasks.get(_required(task_id, "task_id"))
            elif action == "list": return {"tasks": [asdict(item) for item in await services.tasks.list()]}
            elif action == "update":
                result = await services.tasks.update(actor, _required(task_id, "task_id"), TaskPatch(
                    title=arguments.get("title"), description=arguments.get("description"),
                    dependencies=(tuple(arguments["dependencies"]) if "dependencies" in arguments else None),
                    status=arguments.get("status"), result=arguments.get("result"), failure_reason=arguments.get("reason"),
                ))
            elif action == "delete":
                await services.tasks.delete(actor, _required(task_id, "task_id")); return {"deleted": True}
            elif action == "claim": result = await services.tasks.claim(actor, _required(task_id, "task_id"))
            elif action == "complete": result = await services.tasks.complete(actor, _required(task_id, "task_id"), TaskResult(str(arguments.get("result", "")), arguments.get("commit")))
            elif action == "fail": result = await services.tasks.fail(actor, _required(task_id, "task_id"), str(arguments.get("reason", "")))
            else: raise TeamDataError(f"未知任务 action: {action}")
            await self.manager.refresh_prompt_context(actor.team_name)
            self.manager.notify_event()
            return asdict(result)
        except TeamDataError as exc:
            raise self._error(exc) from exc


class TeamMessageTool(_TeamTool):
    def __init__(self, manager: TeamManager) -> None:
        super().__init__(manager, ToolSpec(
            name=TEAM_MESSAGE_TOOL, description="点对点发送、广播或读取团队消息。",
            parameters_schema={
                "type": "object", "properties": {
                    "action": {"type": "string", "enum": ["send", "broadcast", "read"]},
                    "recipient": {"type": "string"}, "protocol": {"type": "string"},
                    "body": {"type": "string"}, "summary": {"type": "string"}, "task_id": {"type": "string"},
                    "approval_id": {"type": "string"}, "plan_version": {"type": "integer"}, "reason": {"type": "string"},
                }, "required": ["action"], "additionalProperties": False,
            }, safety="side_effect", origin="teams",
        ))

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext):
        actor = await self._actor(context)
        action = str(arguments.get("action", ""))
        try:
            if action == "read":
                mailbox = self.manager._service(actor.team_name).mailbox
                messages = await mailbox.unread(actor)
                if messages:
                    await mailbox.acknowledge(actor, [message.id for message in messages])
                    await self.manager.refresh_prompt_context(actor.team_name)
                return {"messages": [asdict(item) for item in messages]}
            draft = MessageDraft(
                str(arguments.get("recipient", "")).strip() or None,
                str(arguments.get("protocol", "message")),  # type: ignore[arg-type]
                str(arguments.get("body", "")), arguments.get("summary"), arguments.get("task_id"),
                arguments.get("approval_id"), arguments.get("plan_version"), arguments.get("reason"),
            )
            if action == "send": return asdict(await self.manager.send_message(actor, draft))
            if action == "broadcast": return asdict(await self.manager.broadcast(actor, draft))
            raise TeamDataError(f"未知消息 action: {action}")
        except TeamDataError as exc:
            raise self._error(exc) from exc


class TeamWaitTool(_TeamTool):
    def __init__(self, manager: TeamManager) -> None:
        super().__init__(manager, ToolSpec(
            name=TEAM_WAIT_TOOL, description="等待下一条团队事件并返回任务、成员和邮箱摘要。",
            parameters_schema={"type": "object", "properties": {"timeout_seconds": {"type": "number"}}, "additionalProperties": False},
            timeout_seconds=3600.0, safety="read_only", origin="teams",
        ))

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext):
        actor = await self._actor(context)
        if actor.kind != "lead":
            raise ToolExecutionError("只有 Team Lead 可以等待团队事件", error_type="team_identity_error")
        timeout = arguments.get("timeout_seconds")
        if timeout is not None and float(timeout) <= 0:
            raise ToolExecutionError("timeout_seconds 必须大于 0", error_type="invalid_arguments")
        return asdict(await self.manager.wait_for_event(float(timeout) if timeout is not None else None))


def create_team_tools(manager: TeamManager):
    return (
        ManageTeamTool(manager), ManageTeamMemberTool(manager), TeamTaskTool(manager),
        TeamMessageTool(manager), TeamWaitTool(manager),
    )


def _required(value: str, field: str) -> str:
    if not value:
        raise TeamDataError(f"{field} 不能为空")
    return value
