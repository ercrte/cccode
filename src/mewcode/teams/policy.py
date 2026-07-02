from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mewcode.tools.base import RuntimePrincipal, ToolSpec


TEAM_TOOL_NAMES = frozenset({"manage_team", "manage_team_member", "team_task", "team_message", "team_wait"})
LEAD_TOOL_NAMES = TEAM_TOOL_NAMES
MEMBER_TOOL_NAMES = frozenset({"team_task", "team_message"})
LIFECYCLE_TOOL_NAMES = frozenset({"manage_team"})


@dataclass(frozen=True)
class TeamAudienceGate:
    principal: RuntimePrincipal
    active_team: Callable[[], str | None]
    enabled: Callable[[], bool] = lambda: True

    def allows(self, spec: ToolSpec) -> bool:
        if spec.name not in TEAM_TOOL_NAMES:
            return True
        if not self.enabled():
            return False
        if self.principal.kind == "sub_agent":
            return False
        if self.principal.kind == "team_member":
            return spec.name in MEMBER_TOOL_NAMES
        return spec.name in (LEAD_TOOL_NAMES if self.active_team() else LIFECYCLE_TOOL_NAMES)

    def denial(self, spec: ToolSpec) -> str:
        return f"当前运行身份 {self.principal.kind} 不允许使用团队工具: {spec.name}"


@dataclass(frozen=True)
class TeamMemberRoleGate:
    allowed_base_tools: frozenset[str] | None
    denied_base_tools: frozenset[str]

    def allows(self, spec: ToolSpec) -> bool:
        if spec.name in MEMBER_TOOL_NAMES:
            return True
        if spec.name in {"delegate_agent", "manage_team", "manage_team_member", "team_wait"}:
            return False
        if spec.visibility == "system":
            return True
        if self.allowed_base_tools is not None and spec.name not in self.allowed_base_tools:
            return False
        return spec.name not in self.denied_base_tools

    def denial(self, spec: ToolSpec) -> str:
        return f"团队成员角色不允许使用工具: {spec.name}"


@dataclass(frozen=True)
class ApprovalGate:
    can_mutate_project: Callable[[], bool]

    def allows(self, spec: ToolSpec) -> bool:
        if spec.name in MEMBER_TOOL_NAMES or spec.safety == "read_only" or spec.visibility == "system":
            return True
        return self.can_mutate_project()

    def denial(self, spec: ToolSpec) -> str:
        return f"当前任务尚未获得 Lead 审批，不允许执行副作用工具: {spec.name}"
