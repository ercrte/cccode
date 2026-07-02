# MewCode 长期团队协作内核 Plan

## 架构概览

本阶段新增 `mewcode.teams` 子系统，不替换现有 `mewcode.subagents`。普通一次性子 Agent 继续使用原有 `delegate_agent`、短期运行状态和可选 Worktree；团队成员由独立的 `TeamManager` 管理，拥有长期花名册、固定 Worktree、持久会话、共享任务和邮箱。

团队数据分成四类：团队及成员元数据、共享任务、审批记录、参与者邮箱。每类共享文件都通过跨进程锁和同目录原子替换写入；会引发通知的状态变更在同一快照中写入持久 outbox，再幂等投递邮箱，避免跨文件崩溃造成状态已变但通知永久丢失。成员会话使用可容忍坏尾记录的 JSONL。团队目录固定放在用户级 MewCode 数据目录下，项目内只保留现有 Worktree 数据。

工具系统增加不可伪造的运行身份和可组合工具门禁。普通主 Agent 只看到团队生命周期工具；激活团队后，同一个主 Agent 以 Lead 身份获得成员管理、任务、消息和等待工具；团队成员只获得任务与消息工具；普通子 Agent 看不到团队工具。审批门禁会动态拒绝未获批成员的全部项目副作用工具，包括 shell。

协程成员由 `TeamRuntimeSupervisor` 管理。它为每个成员建立独立 Agent Loop、权限控制器、上下文管理器、缓存、Hook 状态、持久会话和固定 Worktree。成员自然完成一轮后协程结束并进入 `idle`；邮箱新消息通过事件唤醒成员，加载原会话后继续运行。运行租约保证同名成员最多有一个活跃实例。

Lead 通过任务清单、邮箱和 `team_wait` 形成一个完整编排回路。Lead 的 Agent Loop 在安全边界自动注入邮箱消息；当仍有活跃团队任务时，完成守卫阻止 Lead 提前给出成功结论，并引导其继续等待或处理失败。全部任务完成时，守卫附加权威任务与分支摘要，但不执行 Git 合并。

## 核心数据结构

### TeamConfig

```python
@dataclass(frozen=True)
class TeamConfig:
    enabled: bool = True
    lock_timeout_seconds: float = 2.0
    lock_retry_interval_seconds: float = 0.05
    stale_lock_seconds: float = 30.0
    wait_timeout_seconds: float = 30.0
```

团队根目录固定为 `~/.mewcode/teams`，不允许项目配置把它改到任意路径。配置只控制开关、锁等待和 Lead 等待行为。

### RuntimePrincipal

```python
RuntimePrincipalKind = Literal["main", "sub_agent", "team_member"]

@dataclass(frozen=True)
class RuntimePrincipal:
    kind: RuntimePrincipalKind
    team_name: str | None = None
    actor_name: str | None = None
```

`RuntimePrincipal` 由运行时创建并放入 `ToolContext`，模型不能通过工具参数覆盖。`main` 是否为 Lead 由 `TeamManager.active_team` 动态判断；`team_member` 必须同时携带团队名和成员名。

### TeamActor

```python
TeamActorKind = Literal["lead", "member"]

@dataclass(frozen=True)
class TeamActor:
    team_name: str
    name: str
    kind: TeamActorKind
    cwd: Path
```

`TeamActor` 只能由 `TeamManager` 根据 `RuntimePrincipal` 和花名册解析，不能从模型参数直接构造。领域服务用它确定发件人、任务操作者和成员 Worktree。

### TeamRecord

```python
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
    members: dict[str, TeamMemberRecord]
```

`repository_id` 复用 Worktree 仓库身份计算。`revision` 用于检测陈旧写入。Lead 使用保留名称 `lead`，并拥有独立邮箱，但不作为普通成员写入 `members`。

### TeamMemberRecord

```python
TeamMemberBackend = Literal["coroutine"]
TeamMemberStatus = Literal[
    "idle", "running", "awaiting_approval", "failed", "terminated"
]

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
```

成员角色引用现有 Markdown 子 Agent 角色定义。角色的提示、模型、工具白黑名单、迭代上限和权限模式继续生效；角色中的 `isolation` 对团队成员无效，因为团队成员固定使用长期 Worktree。

### TeamTask

```python
TeamTaskStatus = Literal[
    "pending", "blocked", "in_progress", "awaiting_approval",
    "completed", "failed", "cancelled",
]
TeamTaskKind = Literal["code", "research"]

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
```

`blocked` 由依赖状态派生并持久化。领取 `code` 任务时记录成员分支当前 `start_commit`；完成时要求 Worktree 无未提交/未跟踪修改，且结果 commit 位于当前分支并严格晚于 `start_commit`。`research` 任务只要求非空结果。

### ApprovalRecord

```python
ApprovalStatus = Literal["pending", "approved", "rejected", "superseded"]

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
```

同一任务同一成员最多有一个 `pending` 审批。提交新计划会把旧驳回记录保留，并创建新版本；批准必须同时匹配审批 ID、任务、成员和版本。

### TeamMessage

```python
TeamProtocol = Literal[
    "message", "task_assignment", "plan_request", "plan_approved",
    "plan_rejected", "task_completed", "task_failed", "member_idle",
    "member_resumed", "member_terminated",
]

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
```

广播为每个收件人生成独立消息 ID，并共享 `broadcast_id`。摘要缺失时取正文第一条非空行并限制长度。协议校验由 `MailboxService` 在落盘前完成。

### OutboxEvent

```python
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
```

`team.json`、`tasks.json` 和 `approvals.json` 各自保存与本文件状态变更同事务产生的 outbox。投递消息 ID 由 event ID 和收件人确定，重复补投不会在邮箱产生重复记录。

### TeamPromptContext

```python
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
```

Lead 上下文包含全局任务与成员摘要；成员上下文只包含自己的任务、审批状态、团队参与者摘要和角色正文。邮箱正文通过会话消息投递，不重复塞入提示块。

### ToolGate 与 AgentLoopController

```python
class ToolGate(Protocol):
    def allows(self, spec: ToolSpec) -> bool: ...
    def denial(self, spec: ToolSpec) -> str: ...

@dataclass(frozen=True)
class CompletionDecision:
    accept: bool
    message: ChatMessage
    continuation: str | None = None

class AgentLoopController(Protocol):
    async def before_iteration(self, session: ChatSession) -> None: ...
    async def review_completion(self, message: ChatMessage) -> CompletionDecision: ...
```

`ToolPolicy` 按顺序组合 Plan Mode、Skill 白名单、原有子 Agent 门禁、团队身份门禁和审批门禁。任一门禁拒绝即不向模型暴露并在强行调用时返回结构化失败。

`AgentLoopController` 是可选扩展。普通 Agent 和一次性子 Agent 不配置时保持现状；Lead 与团队成员使用它在安全边界投递邮箱，Lead 额外检查完成条件。

## 核心接口

### FileLock 与 AtomicJsonFile

```python
class FileLock:
    async def acquire(self) -> LockToken: ...
    async def release(self, token: LockToken) -> None: ...

class AtomicJsonFile:
    async def read(self) -> dict[str, Any]: ...
    async def replace(self, value: Mapping[str, Any]) -> None: ...
    async def mutate(self, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]: ...
```

锁文件通过排他创建获得，内容记录随机 token、PID 和创建时间。普通操作锁超过 `stale_lock_seconds` 且所有者不可存活时才接管。原子写使用同目录临时文件、flush/fsync、`os.replace` 和目录 fsync；释放时只删除 token 匹配的锁。

### TeamStore

```python
class TeamStore:
    async def create(self, name: str, repository: RepositoryLayout) -> TeamRecord: ...
    async def list(self) -> tuple[TeamSummary, ...]: ...
    async def load(self, name: str, repository: RepositoryLayout) -> TeamRecord: ...
    async def update_member(self, team: str, member: TeamMemberRecord) -> TeamRecord: ...
    async def get_member(self, team: str, member: str) -> TeamMemberRecord: ...
    async def reconcile_interrupted(self, team: str) -> RecoveryReport: ...
```

`create/load` 同时校验安全名称、团队目录边界和仓库身份。`reconcile_interrupted` 只识别并持久化没有有效运行租约的 `running` 成员，返回待释放任务；`TeamManager` 再调用 `TaskService.release_interrupted`，避免 Store 反向依赖任务模块。`awaiting_approval` 状态保持不变。

### TaskService

```python
class TaskService:
    async def create(self, actor: TeamActor, draft: TaskDraft) -> TeamTask: ...
    async def get(self, task_id: str) -> TeamTask: ...
    async def list(self, status: TeamTaskStatus | None = None) -> tuple[TeamTask, ...]: ...
    async def update(self, actor: TeamActor, task_id: str, patch: TaskPatch) -> TeamTask: ...
    async def delete(self, actor: TeamActor, task_id: str) -> None: ...
    async def claim(self, member: TeamActor, task_id: str) -> TeamTask: ...
    async def release_interrupted(self, task_id: str, reason: str) -> TeamTask: ...
    async def complete(self, member: TeamActor, task_id: str, result: TaskResult) -> TeamTask: ...
```

`create/update` 在同一任务锁内检查依赖存在性和全图环路，再重算所有非终态任务的阻塞状态。`claim` 以 compare-and-set 方式要求任务当前可领取且无 assignee，并为代码任务记录成员分支当前 HEAD。需要审批的成员 claim 后进入 `awaiting_approval`，否则进入 `in_progress`。`complete` 校验结果 commit、分支祖先关系和 Worktree clean 状态后，才提交完成状态与通知 outbox。

### ApprovalService

```python
class ApprovalService:
    async def request(self, member: TeamActor, task_id: str, plan: str) -> ApprovalRecord: ...
    async def approve(self, lead: TeamActor, approval_id: str, task_id: str, version: int) -> ApprovalRecord: ...
    async def reject(
        self, lead: TeamActor, approval_id: str, task_id: str, version: int, reason: str
    ) -> ApprovalRecord: ...
    async def current_for_member(self, member: TeamActor) -> ApprovalRecord | None: ...
    async def can_mutate_project(self, member: TeamActor) -> bool: ...
```

审批记录是授权事实来源。批准先原子写入审批决定和 outbox，再把对应任务投影为 `in_progress`；驳回保留 `awaiting_approval`，直到成员提交新版本。若进程在审批与任务投影之间退出，团队打开、任务读取和成员唤醒时会按审批事实修复任务状态。审批通知失败不会撤销已提交决定，并会由补投器恢复。`can_mutate_project` 每次工具调用时查询持久状态，不能依赖进程内布尔值。

### MailboxService

```python
class MailboxService:
    async def send(self, actor: TeamActor, draft: MessageDraft) -> DeliveryResult: ...
    async def broadcast(self, actor: TeamActor, draft: MessageDraft) -> BroadcastResult: ...
    async def unread(self, actor: TeamActor) -> tuple[TeamMessage, ...]: ...
    async def acknowledge(self, actor: TeamActor, message_ids: Sequence[str]) -> None: ...
```

`send` 先通过团队注册表解析收件人，再校验协议。普通消息直接写邮箱；审批协议调用 `ApprovalService` 提交状态和 outbox，再触发 Dispatcher。广播按收件人排序逐个加锁和投递，不持有多个邮箱锁；返回每个收件人的成功或失败，调用方只唤醒成功目标。

### TeamOutboxDispatcher

```python
class OutboxSource(Protocol):
    async def pending_events(self, team_name: str) -> tuple[OutboxEvent, ...]: ...
    async def mark_delivered(self, team_name: str, event_id: str, recipient: str) -> None: ...

class TeamOutboxDispatcher:
    async def flush(self, team_name: str) -> OutboxFlushReport: ...
    async def flush_event(self, team_name: str, event_id: str) -> OutboxFlushReport: ...
```

Dispatcher 从三个状态文件读取未完成 outbox，按确定性消息 ID 调用 `MailboxService`。邮箱已经存在同 ID 时视为成功；成功后再把收件人写入 `delivered_to`。崩溃发生在邮箱落盘与 outbox 确认之间时，重试仍不会重复消息。团队打开、领域状态变化和 `team_wait` 前都会触发 flush。

### TeamMemberSessionStore

```python
class TeamMemberSessionStore:
    def create(self) -> ChatSession: ...
    def load(self) -> tuple[ChatSession, MemberSessionRestoreReport]: ...
    def delivered_message_ids(self, session: ChatSession) -> frozenset[str]: ...
```

成员会话沿用主会话的 message/checkpoint JSONL 结构，但位置固定在团队成员目录。`ChatMessage` 新增仅供本地运行时使用的 `metadata` 字段，邮箱注入消息记录 `team_message_id`；Provider 序列化忽略该字段，持久化序列化保留它。恢复后用这些 ID 去重，先成功追加会话再确认邮箱已读。

### TeamRuntimeSupervisor

```python
class TeamRuntimeSupervisor:
    async def create_member(self, request: MemberSpawnRequest) -> TeamMemberRecord: ...
    async def wake(self, team_name: str, member_name: str) -> WakeResult: ...
    async def terminate(self, team_name: str, member_name: str) -> TeamMemberRecord: ...
    async def shutdown(self) -> None: ...
```

`create_member` 的顺序是：校验角色和后端 → 获取长期 Worktree → 初始化邮箱和会话 → 原子写入花名册。任一步失败都回滚尚未发布的元数据；已创建但无法安全删除的 Worktree 保留并报告路径。

`wake` 获取成员运行租约后创建 `asyncio.Task`。运行租约使用 PID、随机 token 和心跳，防止两个 MewCode 进程同时恢复同一成员。任务结束回调再次检查未读邮箱，避免消息到达与进入 idle 之间发生丢失唤醒。

### TeamManager

```python
class TeamManager:
    async def create_team(self, name: str) -> TeamSnapshot: ...
    async def list_teams(self) -> tuple[TeamSummary, ...]: ...
    async def open_team(self, name: str) -> TeamSnapshot: ...
    async def close_team(self) -> None: ...
    async def spawn_member(self, request: MemberSpawnRequest) -> TeamMemberRecord: ...
    async def terminate_member(self, name: str) -> TeamMemberRecord: ...
    async def send_message(self, actor: TeamActor, draft: MessageDraft) -> DeliveryResult: ...
    async def wait_for_event(self, timeout_seconds: float | None = None) -> TeamEventSnapshot: ...
    def prompt_context(self, principal: RuntimePrincipal) -> TeamPromptContext | None: ...
    def tool_gates(self, principal: RuntimePrincipal) -> tuple[ToolGate, ...]: ...
    def loop_controller(self, principal: RuntimePrincipal) -> AgentLoopController | None: ...
    async def shutdown(self) -> None: ...
```

`TeamManager` 是 TUI、工具和运行时的唯一组合入口。它不直接实现文件格式，而是协调 Store、Task、Approval、Mailbox、Worktree 和 Supervisor。`close_team` 只清除当前主会话的 Lead 激活态；成员运行仍由 Supervisor 管理。

## 模块设计

### `mewcode.teams.paths`

**职责：** 计算用户团队根目录、团队目录、邮箱、会话和锁路径；校验团队名和成员名；确保解析结果位于所属团队目录。

**对外接口：** `team_root()`、`validate_team_name()`、`validate_member_name()`、`TeamPaths`。

**依赖：** 标准库 `pathlib`；不依赖运行时或 TUI。

### `mewcode.teams.locking`

**职责：** 跨进程文件锁、旧锁接管、运行租约心跳和原子 JSON 替换。

**对外接口：** `FileLock`、`ProcessLease`、`AtomicJsonFile`。

**依赖：** `TeamConfig`、标准库文件 API。

### `mewcode.teams.store`

**职责：** 团队元数据和花名册持久化、schema/revision 校验、仓库绑定、启动恢复。

**对外接口：** `TeamStore`。

**依赖：** `models`、`paths`、`locking`、Worktree 仓库布局。

### `mewcode.teams.tasks`

**职责：** 共享任务 CRUD、状态机、依赖图检查、并发 claim、阻塞状态重算和代码任务完成校验。

**对外接口：** `TaskService`。

**依赖：** `models`、`locking`、`GitClient`；不依赖 Agent Loop。

### `mewcode.teams.approvals`

**职责：** 审批版本、待审批唯一性、批准/驳回匹配和项目副作用授权判断。

**对外接口：** `ApprovalService`。

**依赖：** `models`、`locking`、`TaskService`。

### `mewcode.teams.mailbox`

**职责：** 名称注册表解析、邮箱读写、协议校验、广播、已读确认和投递结果。

**对外接口：** `MailboxService`。

**依赖：** `models`、`store`、`locking`、`ApprovalService`。

### `mewcode.teams.events`

**职责：** 扫描团队、任务和审批 outbox，幂等投递协议消息并记录逐收件人结果。

**对外接口：** `TeamOutboxDispatcher`。

**依赖：** `models`、`store`、`TaskService`、`ApprovalService`、`MailboxService`。

### `mewcode.teams.sessions`

**职责：** 成员 JSONL 会话记录、checkpoint、坏尾恢复、安全工具边界截断和消息 ID 去重。

**对外接口：** `TeamMemberSessionStore`。

**依赖：** `ChatSession`、通用消息序列化；不依赖 TeamManager。

### `mewcode.teams.policy`

**职责：** 团队工具 audience 过滤、成员角色工具过滤、防嵌套和审批前副作用门禁。

**对外接口：** `TeamAudienceGate`、`TeamMemberRoleGate`、`ApprovalGate`。

**依赖：** `RuntimePrincipal`、工具接口、`ApprovalService`。

成员有效工具集合为“角色允许的基础工具 + `team_task` + `team_message`”，再减去角色黑名单中的基础工具、全局禁止工具、成员管理工具、嵌套委派工具和审批门禁拒绝项；角色白名单不需要重复声明两个协作工具。

### `mewcode.teams.runtime`

**职责：** 成员 Runner 构造、运行租约、协程生命周期、邮箱边界注入、idle/resume/terminate 状态和通知。

**对外接口：** `TeamMemberRunnerFactory`、`TeamRuntimeSupervisor`、`TeamMemberLoopController`。

**依赖：** Agent Loop、现有角色目录、权限、上下文、Hook、Worktree 和团队领域服务。

### `mewcode.teams.manager`

**职责：** 团队生命周期、当前 Lead 激活态、成员管理、服务编排、事件通知和关闭恢复。

**对外接口：** `TeamManager`。

**依赖：** 所有 teams 领域模块、WorktreeManager、角色加载器；不依赖具体 TUI widget。

### `mewcode.teams.tools`

**职责：** 把领域接口暴露成稳定模型工具，并从 `ToolContext.principal` 获取身份。

**对外接口：**

- `manage_team`：`create | list | open | close | status`
- `manage_team_member`：`spawn | list | terminate`
- `team_task`：`create | get | list | update | delete | claim`
- `team_message`：`send | broadcast | read`
- `team_wait`：等待下一条团队事件并返回任务/邮箱摘要

**依赖：** `TeamManager`。工具只做参数解析和错误转换，不复制领域规则。

### Agent Loop 与提示集成

**职责：** Agent Loop 支持可组合 `ToolGate`、安全边界回调和候选完成审查；提示系统注入 Team Lead 或成员上下文。

**改动：**

- `ToolContext` 增加默认 `RuntimePrincipal(kind="main")`。
- `ToolPolicy` 从单一子 Agent filter 扩展为多个 gate，同时保留原有构造兼容层。
- `AgentLoopRunner.run()` 增加默认开启的用户消息追加参数，并在每次模型请求前调用 controller。
- `ChatMessage` 增加本地 `metadata`，Provider 不序列化，Session Store 持久化。
- `RuntimePromptContext` 增加 `team_context`；提示块展示身份、团队、当前任务、审批状态、邮箱规则和 Lead 编排约束。
- Lead 完成守卫在仍有活跃任务时返回 continuation；失败/取消时生成未达成摘要；全部完成时追加“分支待集成、未自动合并”的权威摘要。

### Worktree 集成

**职责：** 让团队成员 Worktree 长期保留且可跨启动恢复。

**改动：**

- `WorktreeMetadata` 增加 `retention: "ephemeral" | "persistent"`，旧版本元数据缺省为 `ephemeral`。
- `WorktreeManager.acquire()` 增加 retention 参数，默认保持现有行为。
- 新增 `release()`，只释放进程内 active lease，不检查或删除目录。
- janitor 跳过 `persistent` Worktree。
- 团队成员使用稳定 owner ID 和 `persistent`；一次性子 Agent 继续使用 `ephemeral` 并在结束时 `finish()`。

### TUI 与配置集成

**职责：** 组装 TeamManager、注册工具、刷新角色、显示异步团队通知并在退出时安全关闭。

**改动：**

- `AppConfig` 增加 `teams: TeamConfig`，配置解析严格校验正数与超时关系。
- TUI 初始化 TeamManager 后注册五个团队工具；工具是否暴露由 gate 决定，不通过动态注册避免并发污染。
- 主 Agent Runner 每轮从 TeamManager 获取 gate、controller 和 prompt context。
- 成员事件通过与后台子 Agent 相同的非致命 UI 通知通道显示；邮箱仍是事实来源。
- TUI 卸载先关闭 TeamRuntimeSupervisor，再关闭 Worktree janitor；取消中的成员被标记中断，数据和 Worktree 保留。
- README 增加团队创建、角色、任务、消息、审批、恢复和本阶段边界说明。

## 模块交互

### 创建并激活团队

```text
主 Agent → manage_team(create)
          → TeamManager
          → 校验 Git RepositoryLayout 与安全名称
          → TeamStore 原子创建 team.json/tasks.json/approvals.json/邮箱
          → 设置当前 active_team
          → 下一轮 ToolGate 暴露 Lead 工具并注入 Lead 提示
```

### 拆任务并并行派发

```text
Lead → team_task(create 多次) → TaskService 检查 DAG 并持久化
Lead → manage_team_member(spawn) → Supervisor 创建固定 persistent Worktree 和 idle 成员
Lead → team_message(task_assignment) → 成员邮箱落盘 → Supervisor.wake
成员 → team_task(claim) → 原子领取；依赖未完成则拒绝
成员 → Agent Loop 在自己的 cwd 中执行
Lead → team_wait → 等待任务、邮箱或成员状态事件
```

任务完成、失败、成员空闲和审批状态变化先与领域状态一起进入对应 outbox；Dispatcher 随后写邮箱。直接普通消息没有跨文件状态变更，仍直接写目标邮箱并在锁超时时明确失败。

### 成员直接协作

```text
成员 A → team_message(send/broadcast)
       → ToolContext principal 确认 A 身份
       → 注册表解析目标 → MailboxService 加锁落盘
       → TeamManager 通知 Supervisor.wake(B)
       → B 的 loop controller 在安全边界追加消息并确认已读
```

若 B 正在模型流式响应或工具调用，消息保持未读到下一安全边界；若 B 正从 running 转 idle，结束回调会再次检查邮箱并恢复，避免丢失唤醒。

### 审批流程

```text
需审批成员领取任务
  → Task=awaiting_approval，ApprovalGate 只放行读类 + task/message
成员 → team_message(plan_request)
  → ApprovalService 创建 version=N → Lead 邮箱
Lead → team_message(plan_approved 或 plan_rejected)
  → 校验 sender/task/member/id/version
  → approved: Approval=approved，Task=in_progress，outbox 记录通知，写工具解锁
  → rejected: Approval=rejected，outbox 记录理由，成员修改计划后提交 version=N+1
```

审批门禁在每次工具调用时读取当前审批状态，因此旧 Runner、旧消息或伪造参数都不能复用历史批准。

### 空闲与跨重启恢复

```text
成员 Agent Loop 无工具调用结束
  → 会话 checkpoint → 成员状态 idle → member_idle 投递 Lead → 协程释放
MewCode 退出
  → 取消活跃成员 → 标记 interrupted/failed → release persistent Worktree
再次启动并打开团队
  → TeamStore 校验项目 → reconcile_interrupted → 恢复邮箱/任务/成员元数据
Lead/成员发送新消息
  → Supervisor 获取运行租约 → 加载 session.jsonl → 去重未读消息
  → 原 Worktree + 原上下文运行 → member_resumed 通知 Lead
```

### Lead 完成判断

```text
Lead 候选最终回复
  → TeamLeadLoopController.review_completion
  ├─ 有 pending/blocked/running/awaiting_approval → 拒绝结束，要求 wait/处理
  ├─ 有 failed/cancelled 且无法重派 → 替换为未达成摘要
  └─ 全部 completed → 接受并附加任务、成员、commit/branch、未合并说明
```

## 文件组织

```text
mewcode/
├── src/mewcode/
│   ├── teams/
│   │   ├── __init__.py       — 团队公共导出
│   │   ├── models.py         — 团队、成员、任务、消息、审批与快照模型
│   │   ├── paths.py          — 用户团队目录和名称/路径安全
│   │   ├── locking.py        — 文件锁、运行租约、原子 JSON 写入
│   │   ├── store.py          — 团队元数据、花名册和恢复
│   │   ├── tasks.py          — 任务 CRUD、DAG、状态机和完成校验
│   │   ├── approvals.py      — 计划审批状态机
│   │   ├── mailbox.py        — 注册表、邮箱、协议与广播
│   │   ├── events.py         — 持久 outbox 幂等补投
│   │   ├── sessions.py       — 成员会话持久化和消息去重
│   │   ├── policy.py         — audience、角色和审批工具门禁
│   │   ├── runtime.py        — 成员 Runner 与协程 Supervisor
│   │   ├── manager.py        — 团队生命周期和服务编排
│   │   └── tools.py          — 五个团队模型工具
│   ├── providers/base.py     — ChatMessage 本地 metadata
│   ├── session.py            — 带 metadata 的用户消息追加
│   ├── memory/session_store.py — 持久化 ChatMessage metadata
│   ├── tools/base.py         — RuntimePrincipal 与 ToolContext
│   ├── tools/scheduler.py    — 可组合 ToolGate
│   ├── agent.py              — AgentLoopController 和完成守卫挂点
│   ├── prompting/base.py     — TeamPromptContext 引用
│   ├── prompting/builder.py  — Lead/成员运行提示
│   ├── config.py             — TeamConfig 解析
│   ├── worktrees/models.py   — persistent retention 元数据
│   ├── worktrees/manager.py  — acquire/release 与 janitor 跳过
│   ├── subagents/runtime.py  — 普通子 Agent principal/gate 适配
│   └── tui/app.py            — TeamManager、工具、通知与 shutdown 组装
├── tests/
│   ├── test_team_store.py    — 路径、原子存储、仓库绑定和恢复
│   ├── test_team_tasks.py    — CRUD、状态机、DAG 和并发 claim
│   ├── test_team_mailbox.py  — 锁、协议、广播、已读和去重
│   ├── test_team_approvals.py — 审批版本与副作用门禁
│   ├── test_team_runtime.py  — 协程、隔离、idle/resume/terminate
│   ├── test_team_tools.py    — schema、principal 和工具可见性
│   ├── test_teams_integration.py — Lead 编排、完成守卫和跨重启集成
│   └── e2e_mock_openai_server.py — 团队端到端模型脚本
├── specs/team-collaboration-core/
│   ├── spec.md
│   ├── plan.md
│   ├── task.md
│   └── checklist.md
└── README.md                 — 用户配置和工作流说明
```

运行期用户数据：

```text
~/.mewcode/teams/<team-name>/
├── team.json
├── team.lock
├── tasks.json
├── tasks.lock
├── approvals.json
├── approvals.lock
├── mailboxes/
│   ├── lead.json
│   ├── lead.lock
│   ├── <member>.json
│   └── <member>.lock
├── sessions/
│   ├── <member>.jsonl
│   └── <member>.lock
└── runtime/
    └── <member>.lease
```

## 需求覆盖

| 需求 | 架构责任方 |
|------|------------|
| F1、F2、F3、F4 | `TeamManager`、`TeamStore`、`paths` |
| F5、F6、F7 | `TeamMemberRecord`、`TeamRuntimeSupervisor`、persistent Worktree |
| F8 | Lead 提示、`team_task`、成员副作用门禁、完成守卫 |
| F9、F10、F11、F12 | `TaskService`、`tasks.json` 原子存储 |
| F13 | `RuntimePrincipal`、`ToolGate`、`teams.policy` |
| F14、F15、F16、F17、F18、F19 | `MailboxService`、名称注册表、邮箱锁、持久 outbox 和消息 metadata 去重 |
| F20 | `TeamMemberLoopController`、Supervisor 事件与结束后复查 |
| F21、F22、F23 | 独立 Runner、`TeamMemberSessionStore`、运行租约和 wake |
| F24、F25、F26 | `ApprovalService`、`ApprovalGate`、审批协议 |
| F27 | `TaskService.complete`、`GitClient`、完成/失败协议消息 |
| F28 | `TeamStore` 状态更新、启动 reconcile、Supervisor 回调 |
| F29、F30 | `team_wait`、Lead controller、完成守卫和权威摘要 |
| F31 | `TeamRuntimeSupervisor.terminate`、任务释放和终止协议 |

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 与现有子 Agent 的关系 | 新增 teams 层并复用底层，不把子 Agent 全量重构成成员 | 保持一次性委派兼容，降低回归面；长期生命周期语义由新层负责 |
| 第一阶段后端 | 只实现 `asyncio.Task` 协程，其他 backend 值直接报错 | 满足已批准范围，并为第二阶段保留显式 backend 字段，不发生静默降级 |
| 角色来源 | 复用现有 Markdown 角色目录和优先级 | 避免重复角色配置；团队固定 Worktree，仅忽略角色 isolation |
| 共享状态格式 | 锁保护的原子 JSON 快照 | 当前规模不需要数据库；比并发追加 JSONL 更容易保证任务、审批和已读状态的一致快照 |
| 会话格式 | JSONL message/checkpoint | 复用现有恢复语义，允许跳过坏尾记录并保留长会话追加性能 |
| 邮箱投递 | 持久 outbox + at-least-once + 确定性消息 ID + 会话 metadata 去重 | 文件系统无法经济地实现跨文件 exactly-once；该组合能补偿跨文件崩溃并避免邮箱或上下文重复 |
| 广播一致性 | 每收件人独立投递并返回逐项结果 | 避免同时持有多个邮箱锁和分布式事务；失败可定位、成功不回滚 |
| 工具隔离 | RuntimePrincipal + 可组合 ToolGate + 工具内二次鉴权 | 模型不能伪造身份；同时控制模型可见 schema 和强行调用，未来 coordinator 可复用 |
| 审批限制 | 动态 ApprovalGate 拦截全部项目副作用工具 | `run_command` 也能写文件，仅隐藏 write/edit 不足以落实审批要求 |
| 成员工作目录 | 每成员一个 persistent Git Worktree | 并发修改互不覆盖，并为第三阶段按分支集成保留稳定输入 |
| Lead 等待 | `team_wait` 事件工具 + completion guard | 避免忙轮询和 Lead 提前结束，同时不要求后台自动创建第二个 Lead Agent Loop |
| 消息时机 | Agent Loop 模型请求前的安全边界 | 不插入未配对工具调用之间，也不尝试中断模型流或正在执行的工具 |
| 跨重启运行冲突 | 带 PID/token/心跳的成员运行租约 | 文件锁只适合短临界区；运行租约才能保证长期协程单实例且可从崩溃恢复 |
| Git 集成边界 | 校验并报告 commit/branch，不 merge/rebase/cherry-pick | 符合第一阶段范围，把冲突处理留给第三阶段 |
| 外部依赖 | 不新增第三方锁或数据库依赖 | Python 标准库足以实现本地锁和原子替换，减少安装和跨平台变量 |
