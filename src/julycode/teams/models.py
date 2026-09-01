from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping


TeamMemberBackend = Literal["coroutine"]
TeamMemberStatus = Literal["idle", "running", "awaiting_approval", "failed", "terminated"]
TeamMemberSyncStatus = Literal["current", "pending", "blocked"]
TeamTaskStatus = Literal[
    "pending",
    "blocked",
    "in_progress",
    "awaiting_approval",
    "completed",
    "failed",
    "cancelled",
]
TeamTaskKind = Literal["code", "research"]
ApprovalStatus = Literal["pending", "approved", "rejected", "superseded"]
TeamActorKind = Literal["lead", "member"]
TeamProtocol = Literal[
    "message",
    "task_assignment",
    "plan_request",
    "plan_approved",
    "plan_rejected",
    "task_completed",
    "task_failed",
    "member_idle",
    "member_resumed",
    "member_terminated",
]

MEMBER_STATUSES = frozenset({"idle", "running", "awaiting_approval", "failed", "terminated"})
TASK_STATUSES = frozenset(
    {"pending", "blocked", "in_progress", "awaiting_approval", "completed", "failed", "cancelled"}
)
TASK_KINDS = frozenset({"code", "research"})
APPROVAL_STATUSES = frozenset({"pending", "approved", "rejected", "superseded"})
TEAM_PROTOCOLS = frozenset(
    {
        "message",
        "task_assignment",
        "plan_request",
        "plan_approved",
        "plan_rejected",
        "task_completed",
        "task_failed",
        "member_idle",
        "member_resumed",
        "member_terminated",
    }
)
MEMBER_SYNC_STATUSES = frozenset({"current", "pending", "blocked"})
INTEGRATION_PHASES = frozenset(
    {"active", "integrating", "blocked", "ready", "publishing", "published", "not_needed"}
)
INTEGRATION_INTENT_KINDS = frozenset({"merge_task", "publish"})
INTEGRATION_FAILURE_STAGES = frozenset({"prepare", "sync", "merge", "publish", "recovery", "cleanup"})
_OBJECT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class TeamDataError(ValueError):
    pass


@dataclass(frozen=True)
class TeamConfig:
    enabled: bool = True
    lock_timeout_seconds: float = 2.0
    lock_retry_interval_seconds: float = 0.05
    stale_lock_seconds: float = 30.0
    wait_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class TeamMemberRecord:
    name: str
    role: str
    backend: TeamMemberBackend
    require_approval: bool
    status: TeamMemberStatus
    worktree_root: str
    worktree_cwd: str
    branch: str
    worktree_owner_id: str
    session_path: str
    current_task_id: str | None
    pending_approval_id: str | None
    created_at: str
    updated_at: str
    last_active_at: str
    last_error: str | None = None
    sync_status: TeamMemberSyncStatus = "current"
    sync_head: str | None = None
    sync_error: str | None = None


@dataclass(frozen=True)
class TeamRecord:
    schema_version: int
    revision: int
    name: str
    repository_root: str
    repository_id: str
    lead_name: str
    created_at: str
    updated_at: str
    members: dict[str, TeamMemberRecord] = field(default_factory=dict)
    outbox: tuple[OutboxEvent, ...] = ()


@dataclass(frozen=True)
class TeamTask:
    id: str
    title: str
    description: str
    kind: TeamTaskKind
    status: TeamTaskStatus
    dependencies: tuple[str, ...]
    assignee: str | None
    created_by: str
    created_at: str
    updated_at: str
    result: str | None = None
    failure_reason: str | None = None
    start_commit: str | None = None
    commit: str | None = None
    approval_id: str | None = None
    blocked_reason: str | None = None
    attempt: int = 1
    integration_round: int = 1


@dataclass(frozen=True)
class TaskAttemptRef:
    task_id: str
    attempt: int


@dataclass(frozen=True)
class IntegratedTaskRecord:
    task: TaskAttemptRef
    member_name: str
    source_branch: str
    source_commit: str
    previous_head: str
    integration_head: str
    integrated_at: str


IntegrationIntentKind = Literal["merge_task", "publish"]


@dataclass(frozen=True)
class IntegrationIntent:
    kind: IntegrationIntentKind
    task: TaskAttemptRef | None
    member_name: str | None
    source_branch: str | None
    source_commit: str | None
    expected_head: str
    result_text: str | None
    started_at: str


IntegrationFailureStage = Literal["prepare", "sync", "merge", "publish", "recovery", "cleanup"]


@dataclass(frozen=True)
class IntegrationFailure:
    stage: IntegrationFailureStage
    message: str
    task: TaskAttemptRef | None = None
    member_name: str | None = None
    commit: str | None = None
    conflict_paths: tuple[str, ...] = ()
    occurred_at: str = ""


IntegrationPhase = Literal[
    "active", "integrating", "blocked", "ready", "publishing", "published", "not_needed"
]


@dataclass(frozen=True)
class IntegrationRoundRecord:
    number: int
    phase: IntegrationPhase
    target_branch: str | None
    base_commit: str | None
    integration_owner_id: str | None
    integration_root: str | None
    integration_branch: str | None
    integration_head: str | None
    accepted: tuple[IntegratedTaskRecord, ...]
    intent: IntegrationIntent | None
    failure: IntegrationFailure | None
    started_at: str
    updated_at: str
    published_at: str | None = None


@dataclass(frozen=True)
class TeamIntegrationState:
    schema_version: int
    revision: int
    next_round: int
    current: IntegrationRoundRecord | None
    history: tuple[IntegrationRoundRecord, ...]


@dataclass(frozen=True)
class TeamIntegrationSummary:
    round_number: int | None
    phase: IntegrationPhase | Literal["idle"]
    target_branch: str | None
    base_commit: str | None
    integration_head: str | None
    accepted_tasks: tuple[TaskAttemptRef, ...]
    failure: IntegrationFailure | None
    member_sync_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TeamIntegrationFinalizeResult:
    status: Literal["waiting", "published", "not_needed", "blocked"]
    summary: TeamIntegrationSummary
    message: str


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    task_id: str
    member_name: str
    plan: str
    plan_version: int
    status: ApprovalStatus
    requested_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class TeamMessage:
    id: str
    sender: str
    recipient: str
    protocol: TeamProtocol
    body: str
    summary: str
    timestamp: str
    read: bool
    task_id: str | None = None
    approval_id: str | None = None
    plan_version: int | None = None
    broadcast_id: str | None = None


@dataclass(frozen=True)
class OutboxEvent:
    id: str
    source: Literal["team", "task", "approval"]
    protocol: TeamProtocol
    sender: str
    recipients: tuple[str, ...]
    body: str
    summary: str
    task_id: str | None
    approval_id: str | None
    plan_version: int | None
    created_at: str
    delivered_to: tuple[str, ...] = ()


@dataclass(frozen=True)
class TeamActor:
    team_name: str
    name: str
    kind: TeamActorKind
    cwd: Path


@dataclass(frozen=True)
class TaskDraft:
    title: str
    description: str
    kind: TeamTaskKind = "code"
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskPatch:
    title: str | None = None
    description: str | None = None
    dependencies: tuple[str, ...] | None = None
    status: TeamTaskStatus | None = None
    result: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class TaskResult:
    result: str
    commit: str | None = None


@dataclass(frozen=True)
class MessageDraft:
    recipient: str | None
    protocol: TeamProtocol
    body: str
    summary: str | None = None
    task_id: str | None = None
    approval_id: str | None = None
    plan_version: int | None = None
    reason: str | None = None
    message_id: str | None = None


@dataclass(frozen=True)
class DeliveryResult:
    recipient: str
    success: bool
    message_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class BroadcastResult:
    broadcast_id: str
    deliveries: tuple[DeliveryResult, ...]


@dataclass(frozen=True)
class MemberSpawnRequest:
    name: str
    role: str
    require_approval: bool = False
    backend: str = "coroutine"


@dataclass(frozen=True)
class TeamSummary:
    name: str
    repository_root: str
    lead_name: str
    member_count: int
    updated_at: str
    path: str


@dataclass(frozen=True)
class TeamSnapshot:
    team: TeamRecord
    tasks: tuple[TeamTask, ...] = ()
    unread_count: int = 0
    integration: TeamIntegrationSummary | None = None


@dataclass(frozen=True)
class TeamEventSnapshot:
    team_name: str
    tasks: tuple[TeamTask, ...]
    members: tuple[TeamMemberRecord, ...]
    unread: tuple[TeamMessage, ...]
    timed_out: bool = False
    integration: TeamIntegrationSummary | None = None


@dataclass(frozen=True)
class RecoveryReport:
    interrupted_members: tuple[str, ...] = ()
    released_task_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class WakeResult:
    member_name: str
    started: bool
    already_running: bool = False
    reason: str = ""


@dataclass(frozen=True)
class OutboxFlushReport:
    delivered: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemberSummary:
    name: str
    role: str
    status: str
    current_task_id: str | None
    sync_status: TeamMemberSyncStatus = "current"
    sync_head: str | None = None
    sync_error: str | None = None


@dataclass(frozen=True)
class TaskSummary:
    id: str
    title: str
    status: str
    assignee: str | None
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class TeamPromptContext:
    team_name: str
    actor_kind: TeamActorKind
    actor_name: str
    roster: tuple[MemberSummary, ...]
    tasks: tuple[TaskSummary, ...]
    unread_count: int
    current_task: TeamTask | None = None
    current_approval: ApprovalRecord | None = None
    role_body: str | None = None
    integration: TeamIntegrationSummary | None = None


def model_to_dict(value: Any) -> dict[str, Any]:
    return asdict(value)


def member_from_dict(raw: Mapping[str, Any]) -> TeamMemberRecord:
    status = str(raw.get("status", ""))
    backend = str(raw.get("backend", ""))
    if status not in MEMBER_STATUSES:
        raise TeamDataError(f"未知成员状态: {status}")
    if backend != "coroutine":
        raise TeamDataError(f"不支持的成员后端: {backend}")
    sync_status = str(raw.get("sync_status", "current"))
    if sync_status not in MEMBER_SYNC_STATUSES:
        raise TeamDataError(f"未知成员同步状态: {sync_status}")
    return TeamMemberRecord(
        name=_text(raw, "name"),
        role=_text(raw, "role"),
        backend="coroutine",
        require_approval=bool(raw.get("require_approval", False)),
        status=status,  # type: ignore[arg-type]
        worktree_root=_text(raw, "worktree_root"),
        worktree_cwd=_text(raw, "worktree_cwd"),
        branch=_text(raw, "branch"),
        worktree_owner_id=_text(raw, "worktree_owner_id"),
        session_path=_text(raw, "session_path"),
        current_task_id=_optional_text(raw.get("current_task_id")),
        pending_approval_id=_optional_text(raw.get("pending_approval_id")),
        created_at=_text(raw, "created_at"),
        updated_at=_text(raw, "updated_at"),
        last_active_at=_text(raw, "last_active_at"),
        last_error=_optional_text(raw.get("last_error")),
        sync_status=sync_status,  # type: ignore[arg-type]
        sync_head=_optional_object_id(raw.get("sync_head"), "sync_head"),
        sync_error=_optional_text(raw.get("sync_error")),
    )


def task_from_dict(raw: Mapping[str, Any]) -> TeamTask:
    status = str(raw.get("status", ""))
    kind = str(raw.get("kind", ""))
    if status not in TASK_STATUSES:
        raise TeamDataError(f"未知任务状态: {status}")
    if kind not in TASK_KINDS:
        raise TeamDataError(f"未知任务类型: {kind}")
    dependencies = raw.get("dependencies", [])
    if not isinstance(dependencies, (list, tuple)) or any(not isinstance(item, str) for item in dependencies):
        raise TeamDataError("任务 dependencies 必须是字符串数组")
    attempt = _positive_int(raw.get("attempt", 1), "attempt")
    integration_round = _positive_int(raw.get("integration_round", 1), "integration_round")
    return TeamTask(
        id=_text(raw, "id"),
        title=_text(raw, "title"),
        description=_text(raw, "description", allow_empty=True),
        kind=kind,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        dependencies=tuple(dependencies),
        assignee=_optional_text(raw.get("assignee")),
        created_by=_text(raw, "created_by"),
        created_at=_text(raw, "created_at"),
        updated_at=_text(raw, "updated_at"),
        result=_optional_text(raw.get("result")),
        failure_reason=_optional_text(raw.get("failure_reason")),
        start_commit=_optional_text(raw.get("start_commit")),
        commit=_optional_text(raw.get("commit")),
        approval_id=_optional_text(raw.get("approval_id")),
        blocked_reason=_optional_text(raw.get("blocked_reason")),
        attempt=attempt,
        integration_round=integration_round,
    )


def integration_state_from_dict(raw: Mapping[str, Any]) -> TeamIntegrationState:
    """严格解析集成状态；损坏的恢复数据绝不静默重置。"""
    if raw.get("schema_version") != 1:
        raise TeamDataError(f"未知集成 schema_version: {raw.get('schema_version')}")
    revision = _non_negative_int(raw.get("revision", 0), "revision")
    next_round = _positive_int(raw.get("next_round", 1), "next_round")
    current_raw = raw.get("current")
    current = None if current_raw is None else integration_round_from_dict(_mapping(current_raw, "current"))
    history_raw = raw.get("history", [])
    if not isinstance(history_raw, (list, tuple)):
        raise TeamDataError("集成 history 必须是数组")
    history = tuple(integration_round_from_dict(_mapping(item, "history item")) for item in history_raw)
    numbers = [item.number for item in history]
    if current is not None:
        numbers.append(current.number)
    if len(numbers) != len(set(numbers)) or numbers != sorted(numbers):
        raise TeamDataError("集成轮次必须严格递增且唯一")
    if numbers and next_round <= max(numbers):
        raise TeamDataError("next_round 必须大于已有轮次")
    return TeamIntegrationState(1, revision, next_round, current, history)


def integration_round_from_dict(raw: Mapping[str, Any]) -> IntegrationRoundRecord:
    phase = str(raw.get("phase", ""))
    if phase not in INTEGRATION_PHASES:
        raise TeamDataError(f"未知集成阶段: {phase}")
    accepted_raw = raw.get("accepted", [])
    if not isinstance(accepted_raw, (list, tuple)):
        raise TeamDataError("accepted 必须是数组")
    accepted = tuple(integrated_task_from_dict(_mapping(item, "accepted item")) for item in accepted_raw)
    keys = [(item.task.task_id, item.task.attempt) for item in accepted]
    if len(keys) != len(set(keys)):
        raise TeamDataError("accepted 中存在重复任务尝试")
    intent_raw = raw.get("intent")
    failure_raw = raw.get("failure")
    return IntegrationRoundRecord(
        number=_positive_int(raw.get("number"), "number"),
        phase=phase,  # type: ignore[arg-type]
        target_branch=_optional_text(raw.get("target_branch")),
        base_commit=_optional_object_id(raw.get("base_commit"), "base_commit"),
        integration_owner_id=_optional_text(raw.get("integration_owner_id")),
        integration_root=_optional_text(raw.get("integration_root")),
        integration_branch=_optional_text(raw.get("integration_branch")),
        integration_head=_optional_object_id(raw.get("integration_head"), "integration_head"),
        accepted=accepted,
        intent=None if intent_raw is None else integration_intent_from_dict(_mapping(intent_raw, "intent")),
        failure=None if failure_raw is None else integration_failure_from_dict(_mapping(failure_raw, "failure")),
        started_at=_text(raw, "started_at"),
        updated_at=_text(raw, "updated_at"),
        published_at=_optional_text(raw.get("published_at")),
    )


def task_attempt_from_dict(raw: Mapping[str, Any]) -> TaskAttemptRef:
    return TaskAttemptRef(_text(raw, "task_id"), _positive_int(raw.get("attempt"), "attempt"))


def integrated_task_from_dict(raw: Mapping[str, Any]) -> IntegratedTaskRecord:
    return IntegratedTaskRecord(
        task=task_attempt_from_dict(_mapping(raw.get("task"), "task")),
        member_name=_text(raw, "member_name"),
        source_branch=_text(raw, "source_branch"),
        source_commit=_object_id(raw.get("source_commit"), "source_commit"),
        previous_head=_object_id(raw.get("previous_head"), "previous_head"),
        integration_head=_object_id(raw.get("integration_head"), "integration_head"),
        integrated_at=_text(raw, "integrated_at"),
    )


def integration_intent_from_dict(raw: Mapping[str, Any]) -> IntegrationIntent:
    kind = str(raw.get("kind", ""))
    if kind not in INTEGRATION_INTENT_KINDS:
        raise TeamDataError(f"未知集成意图: {kind}")
    task_raw = raw.get("task")
    task = None if task_raw is None else task_attempt_from_dict(_mapping(task_raw, "task"))
    if (kind == "merge_task") != (task is not None):
        raise TeamDataError("merge_task 意图必须包含任务，publish 意图不能包含任务")
    return IntegrationIntent(
        kind=kind,  # type: ignore[arg-type]
        task=task,
        member_name=_optional_text(raw.get("member_name")),
        source_branch=_optional_text(raw.get("source_branch")),
        source_commit=_optional_object_id(raw.get("source_commit"), "source_commit"),
        expected_head=_object_id(raw.get("expected_head"), "expected_head"),
        result_text=_optional_text(raw.get("result_text")),
        started_at=_text(raw, "started_at"),
    )


def integration_failure_from_dict(raw: Mapping[str, Any]) -> IntegrationFailure:
    stage = str(raw.get("stage", ""))
    if stage not in INTEGRATION_FAILURE_STAGES:
        raise TeamDataError(f"未知集成失败阶段: {stage}")
    task_raw = raw.get("task")
    return IntegrationFailure(
        stage=stage,  # type: ignore[arg-type]
        message=_text(raw, "message"),
        task=None if task_raw is None else task_attempt_from_dict(_mapping(task_raw, "task")),
        member_name=_optional_text(raw.get("member_name")),
        commit=_optional_object_id(raw.get("commit"), "commit"),
        conflict_paths=_string_tuple(raw.get("conflict_paths", []), "conflict_paths"),
        occurred_at=_text(raw, "occurred_at", allow_empty=True),
    )


def message_from_dict(raw: Mapping[str, Any]) -> TeamMessage:
    protocol = str(raw.get("protocol", ""))
    if protocol not in TEAM_PROTOCOLS:
        raise TeamDataError(f"未知消息协议: {protocol}")
    return TeamMessage(
        id=_text(raw, "id"),
        sender=_text(raw, "sender"),
        recipient=_text(raw, "recipient"),
        protocol=protocol,  # type: ignore[arg-type]
        body=_text(raw, "body", allow_empty=True),
        summary=_text(raw, "summary"),
        timestamp=_text(raw, "timestamp"),
        read=bool(raw.get("read", False)),
        task_id=_optional_text(raw.get("task_id")),
        approval_id=_optional_text(raw.get("approval_id")),
        plan_version=_optional_int(raw.get("plan_version")),
        broadcast_id=_optional_text(raw.get("broadcast_id")),
    )


def approval_from_dict(raw: Mapping[str, Any]) -> ApprovalRecord:
    status = str(raw.get("status", ""))
    if status not in APPROVAL_STATUSES:
        raise TeamDataError(f"未知审批状态: {status}")
    return ApprovalRecord(
        id=_text(raw, "id"),
        task_id=_text(raw, "task_id"),
        member_name=_text(raw, "member_name"),
        plan=_text(raw, "plan"),
        plan_version=int(raw.get("plan_version", 0)),
        status=status,  # type: ignore[arg-type]
        requested_at=_text(raw, "requested_at"),
        decided_at=_optional_text(raw.get("decided_at")),
        decided_by=_optional_text(raw.get("decided_by")),
        reason=_optional_text(raw.get("reason")),
    )


def outbox_from_dict(raw: Mapping[str, Any]) -> OutboxEvent:
    protocol = str(raw.get("protocol", ""))
    source = str(raw.get("source", ""))
    if protocol not in TEAM_PROTOCOLS or source not in {"team", "task", "approval"}:
        raise TeamDataError("outbox source 或 protocol 无效")
    recipients = _string_tuple(raw.get("recipients"), "recipients")
    delivered = _string_tuple(raw.get("delivered_to", []), "delivered_to")
    return OutboxEvent(
        id=_text(raw, "id"),
        source=source,  # type: ignore[arg-type]
        protocol=protocol,  # type: ignore[arg-type]
        sender=_text(raw, "sender"),
        recipients=recipients,
        body=_text(raw, "body", allow_empty=True),
        summary=_text(raw, "summary"),
        task_id=_optional_text(raw.get("task_id")),
        approval_id=_optional_text(raw.get("approval_id")),
        plan_version=_optional_int(raw.get("plan_version")),
        created_at=_text(raw, "created_at"),
        delivered_to=delivered,
    )


def _text(raw: Mapping[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise TeamDataError(f"{key} 必须是{'字符串' if allow_empty else '非空字符串'}")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TeamDataError("可选字段必须是字符串或 null")
    return value


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TeamDataError("可选字段必须是整数或 null")
    return value


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TeamDataError(f"{field_name} 必须是对象")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TeamDataError(f"{field_name} 必须是正整数")
    return value


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TeamDataError(f"{field_name} 必须是非负整数")
    return value


def _object_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _OBJECT_ID_RE.fullmatch(value) is None:
        raise TeamDataError(f"{field_name} 必须是完整 Git 对象 ID")
    return value


def _optional_object_id(value: Any, field_name: str) -> str | None:
    return None if value is None else _object_id(value, field_name)


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise TeamDataError(f"{field_name} 必须是字符串数组")
    return tuple(value)
