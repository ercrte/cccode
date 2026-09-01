from typing import TYPE_CHECKING, Any

from julycode.teams.models import (
    ApprovalRecord,
    IntegrationFailure,
    IntegrationRoundRecord,
    IntegratedTaskRecord,
    MessageDraft,
    TaskAttemptRef,
    TeamActor,
    TeamConfig,
    TeamMemberRecord,
    TeamMessage,
    TeamRecord,
    TeamTask,
    TeamIntegrationFinalizeResult,
    TeamIntegrationState,
    TeamIntegrationSummary,
)

if TYPE_CHECKING:
    from julycode.teams.manager import TeamManager

__all__ = [
    "ApprovalRecord",
    "IntegrationFailure",
    "IntegrationRoundRecord",
    "IntegratedTaskRecord",
    "MessageDraft",
    "TeamActor",
    "TeamConfig",
    "TeamMemberRecord",
    "TeamMessage",
    "TeamManager",
    "TeamRecord",
    "TeamTask",
    "TaskAttemptRef",
    "TeamIntegrationFinalizeResult",
    "TeamIntegrationState",
    "TeamIntegrationSummary",
    "MANAGE_TEAM_TOOL",
    "MANAGE_TEAM_MEMBER_TOOL",
    "TEAM_TASK_TOOL",
    "TEAM_MESSAGE_TOOL",
    "TEAM_WAIT_TOOL",
    "create_team_tools",
]


def __getattr__(name: str) -> Any:
    """惰性导出运行时组件，避免配置加载阶段形成循环导入。"""
    if name == "TeamManager":
        from julycode.teams.manager import TeamManager

        globals()[name] = TeamManager
        return TeamManager
    if name in {
        "MANAGE_TEAM_TOOL",
        "MANAGE_TEAM_MEMBER_TOOL",
        "TEAM_TASK_TOOL",
        "TEAM_MESSAGE_TOOL",
        "TEAM_WAIT_TOOL",
        "create_team_tools",
    }:
        from julycode.teams import tools

        value = getattr(tools, name)
        globals()[name] = value
        return value
    raise AttributeError(name)
