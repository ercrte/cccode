# JulyCode 多 Agent Worktree 自动合并 Plan

## 架构概览

本功能在现有 `julycode.teams` 与 `julycode.worktrees` 之间增加 `TeamIntegrationService`。它不向模型增加新的 Git 工具，而是由共享任务生命周期自动触发：任务创建或重置时登记集成轮次，代码任务领取前同步成员 Worktree，代码任务完成时先进入内部集成 Worktree，Lead 完成守卫最后执行一次安全发布。

每个集成轮次使用独立的长期内部 Worktree 和临时集成分支。内部成果通过普通 Git 三方合并串行累积，所有冲突只出现在该内部 Worktree。每个首次引入新成果的成功任务固定创建一个带任务元数据的 merge commit；若任务提交已经存在于基线或已经被接受，则只记录幂等命中，不重复创建提交。

最终发布不把成员分支逐个合并到 Lead 工作目录，而是在严格预检后，把已经包含全部成果的内部集成提交通过 `--ff-only` 一次性推进 Lead 目标分支。这样发布前 Lead 始终看不到部分成果；发布成功后，Lead 索引和工作目录由 Git 同步更新。发布状态持久化采用“操作意图 → Git 事实 → 完成记录”三阶段，重启时通过分支、提交父节点、工作区状态和持久意图进行对账。

当前开发环境为 Git 2.34.1，不依赖较新版本才完整支持的 `git merge-tree --write-tree`。内部 Worktree 方案还能继续执行仓库现有 Git hooks，并复用 JulyCode 已实现的 Worktree 路径、元数据、环境初始化与保护删除能力。

## 核心数据结构

### `TeamTask` 扩展

```python
@dataclass(frozen=True)
class TeamTask:
    # 现有字段保持不变
    attempt: int = 1
    integration_round: int = 1
```

`attempt` 在终态任务显式重置为 `pending` 时递增，同一个任务的幂等键为 `(task_id, attempt)`。`integration_round` 由集成服务在创建或重置任务时分配。旧版 `tasks.json` 缺少字段时按 `attempt=1`、`integration_round=1` 读取，因此无需破坏现有团队数据。

### `TeamMemberRecord` 扩展

```python
TeamMemberSyncStatus = Literal["current", "pending", "blocked"]

@dataclass(frozen=True)
class TeamMemberRecord:
    # 现有字段保持不变
    sync_status: TeamMemberSyncStatus = "current"
    sync_head: str | None = None
    sync_error: str | None = None
```

`sync_head` 表示成员 Worktree 最近确认一致的团队基线。发布后仍在运行、dirty、分支不匹配或无法快进的成员标为 `pending`/`blocked`。集成服务只通过 TeamStore 的字段级原子更新写入同步状态，避免覆盖运行时同时更新的成员生命周期字段。

### `TaskAttemptRef`

```python
@dataclass(frozen=True)
class TaskAttemptRef:
    task_id: str
    attempt: int
```

用于任务幂等、轮次归属和历史记录，不以单独的任务 ID 判断重复。

### `IntegratedTaskRecord`

```python
@dataclass(frozen=True)
class IntegratedTaskRecord:
    task: TaskAttemptRef
    member_name: str
    source_branch: str
    source_commit: str
    previous_head: str
    integration_head: str
    integrated_at: str
```

`previous_head` 与 `source_commit` 必须是新 merge commit 的两个父节点；来源已经是内部基线祖先时，`integration_head == previous_head`，表示幂等接受而没有重复合并。

### `IntegrationIntent`

```python
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
```

执行 Git 写操作前先持久化意图。任务意图保留完成任务所需的结果文本，使进程在 Git 合并成功但任务 JSON 尚未更新时仍可恢复。发布意图的 `expected_head` 是准备发布的内部集成提交。

### `IntegrationFailure`

```python
IntegrationFailureStage = Literal[
    "prepare", "sync", "merge", "publish", "recovery", "cleanup"
]

@dataclass(frozen=True)
class IntegrationFailure:
    stage: IntegrationFailureStage
    message: str
    task: TaskAttemptRef | None = None
    member_name: str | None = None
    commit: str | None = None
    conflict_paths: tuple[str, ...] = ()
    occurred_at: str = ""
```

失败信息经过脱敏后持久化。内容冲突单独保存规范化仓库相对路径；不能确认冲突路径时仍按失败处理，不回显任意命令输出。

### `IntegrationRoundRecord`

```python
IntegrationPhase = Literal[
    "active",
    "integrating",
    "blocked",
    "ready",
    "publishing",
    "published",
    "not_needed",
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
```

研究任务可以形成没有目标分支和内部 Worktree 的 `not_needed` 轮次。代码轮次第一次领取任务时才捕获 Lead 分支和基线并创建 Worktree，避免只创建研究任务就产生 Git 副作用。

### `TeamIntegrationState`

```python
@dataclass(frozen=True)
class TeamIntegrationState:
    schema_version: int
    revision: int
    next_round: int
    current: IntegrationRoundRecord | None
    history: tuple[IntegrationRoundRecord, ...]
```

保存在团队目录的 `integration.json`。成功发布或无需发布后，当前轮次原样进入历史并清空 `current`；下一项新建或重置任务使用 `next_round`。未发布的 blocked 轮次一直保留为 current，后续修复与重试不能跳过它。

### 用户可见摘要

```python
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
```

`TeamSnapshot`、`TeamEventSnapshot` 和 `TeamPromptContext` 增加可选 `integration` 字段。工具仍使用现有输入 schema，只在结果中增加结构化摘要。

## 核心接口

### `TeamIntegrationStore`

```python
class TeamIntegrationTransaction:
    @property
    def state(self) -> TeamIntegrationState: ...
    def replace(self, state: TeamIntegrationState) -> None: ...

class TeamIntegrationStore:
    def __init__(self, team_name: str, team_store: TeamStore) -> None: ...
    async def load_or_create(self) -> TeamIntegrationState: ...
    def transaction(self) -> AsyncContextManager[TeamIntegrationTransaction]: ...
```

内部使用 `integration.json` 与 `integration.lock`，沿用 `AtomicJsonFile` 的同目录临时文件、`fsync` 和原子替换。`transaction()` 只获取一次 integration.lock，并允许持锁方在一次 Git 操作前后多次原子替换状态，不通过 `AtomicJsonFile.mutate()` 重入同一把锁。解析时严格校验 schema、轮次递增、对象 ID、分支/路径与 accepted 唯一性。缺失文件只为旧团队创建初始状态；损坏文件不会被静默覆盖。

### `IntegrationTaskPort`

```python
class IntegrationTaskPort(Protocol):
    async def list(self, status: str | None = None) -> tuple[TeamTask, ...]: ...
    async def get(self, task_id: str) -> TeamTask: ...
    async def complete_recovered(
        self,
        task_id: str,
        attempt: int,
        result: str,
        commit: str,
    ) -> TeamTask: ...
```

该协议定义在 integration 模块，恢复逻辑只依赖任务端口而不导入具体 TaskService。TaskService 实现协议并单向依赖 TeamIntegrationService，TeamManager 负责装配，从而避免 `integration.py ↔ tasks.py` 循环依赖。

### `TeamIntegrationService`

```python
class TeamIntegrationService:
    def __init__(
        self,
        team_name: str,
        main_cwd: Path,
        store: TeamStore,
        worktrees: WorktreeManager,
        *,
        git: GitClient | None = None,
    ) -> None: ...

    async def assign_round(self) -> int: ...
    async def prepare_code_claim(
        self,
        member: TeamMemberRecord,
        task: TeamTask,
    ) -> str: ...
    async def integrate_code_task(
        self,
        member: TeamActor,
        task: TeamTask,
        result: TaskResult,
        complete_task: Callable[[str], Awaitable[TeamTask]],
    ) -> TeamTask: ...
    async def validate_task_delete(self, task: TeamTask) -> None: ...
    async def recover(self, tasks: IntegrationTaskPort) -> TeamIntegrationSummary: ...
    async def finalize(
        self,
        tasks: tuple[TeamTask, ...],
    ) -> TeamIntegrationFinalizeResult: ...
    async def snapshot(self) -> TeamIntegrationSummary: ...
    async def close(self) -> None: ...
```

所有公开写操作通过 `TeamIntegrationStore.transaction()` 获取团队级 `integration.lock`。锁内可以短暂获取 tasks/team JSON 锁，但任何其他服务都不得在持有 tasks/team 锁时反向申请 integration 锁。固定锁序为：

```text
integration.lock → tasks.lock → team.lock / mailbox lock
```

tasks/team/mailbox 文件锁只覆盖读取、比较和原子替换，不跨越 Git 子进程等待；最外层 integration transaction 可以跨越 Git 子进程，负责在意图、Git 事实与完成记录之间阻止同团队的另一个进程交错操作。

### `TaskService` 接入

```python
class TaskService:
    def __init__(
        self,
        team_name: str,
        store: TeamStore,
        config: TeamConfig | None = None,
        *,
        git: GitClient | None = None,
        integration: TeamIntegrationService | None = None,
    ) -> None: ...

    async def complete_recovered(
        self,
        task_id: str,
        attempt: int,
        result: str,
        commit: str,
    ) -> TeamTask: ...
```

任务创建和终态重置先由 `assign_round()` 获取轮次，再写入任务。重置时递增 attempt 并清理本次任务结果；历史集成记录不变。删除当前未发布轮次中已有 accepted 记录的代码任务会被拒绝，因为移除它需要改写内部历史；发布后的任务仍可删除，审计信息由 integration history 保存。

代码任务 claim 在改变任务状态前调用 `prepare_code_claim()`；返回值作为新的 `start_commit`。代码任务 complete 把最终校验与 Git 集成交给 `integrate_code_task()`，只有集成成功后才通过回调执行原有 `_finish(..., completed)`。研究任务继续走原路径。

相同 `(task_id, attempt, commit)` 的重复 complete 直接返回已完成任务，不追加 outbox；已经 accepted/completed 的同一 attempt 又提供不同 commit 时拒绝。尚未 accepted 的冲突任务仍保持 in_progress，可以在成员产生新的修复提交后以同一 attempt 重试。`complete_recovered()` 只供持有 integration 锁的恢复流程使用，检查持久 intent 后完成任务状态和单次 outbox，不再次运行 Git 合并。

### `GitClient` 扩展

```python
GitOperation = Literal["none", "merge", "rebase", "cherry_pick", "revert"]
GitMergeStatus = Literal["merged", "already_integrated", "conflicted", "failed"]

@dataclass(frozen=True)
class GitMergeOutcome:
    status: GitMergeStatus
    head_before: str
    head_after: str
    conflict_paths: tuple[str, ...] = ()
    detail: str = ""

class GitClient:
    async def current_branch(self, *, cwd: Path) -> str | None: ...
    async def operation(self, *, cwd: Path) -> GitOperation: ...
    async def is_clean(self, *, cwd: Path) -> bool: ...
    async def commit_parents(self, *, cwd: Path, commit: str) -> tuple[str, ...]: ...
    async def fast_forward(self, *, cwd: Path, target: str) -> str: ...
    async def merge_no_ff(
        self,
        *,
        cwd: Path,
        source: str,
        message: str,
    ) -> GitMergeOutcome: ...
    async def abort_merge(self, *, cwd: Path) -> None: ...
```

`current_branch` 使用 symbolic ref，游离 HEAD 返回 `None`。`operation` 通过当前 Worktree 自己的 Git path 检查 `MERGE_HEAD`、rebase、`CHERRY_PICK_HEAD` 与 `REVERT_HEAD`。`is_clean` 同时检查 tracked、staged 和 untracked 状态。

`merge_no_ff` 使用 argv 执行 `git merge --no-ff --no-edit --no-gpg-sign -m <message> <source>`，不经过 shell。消息固定包含 team、round、task、attempt、member 和 source commit trailer。冲突时先读取未合并路径，再 `merge --abort` 并验证 HEAD 与工作区已回到原状态；无法验证回滚时返回保守失败并保留内部 Worktree。

`fast_forward` 使用 `git merge --ff-only <commit>`，前后都校验当前分支、HEAD、操作状态和干净状态。它不使用 `update-ref`，因为直接移动一个已检出分支的 ref 会让 Lead 索引与工作目录落后于分支。

### `WorktreeManager` 扩展

```python
class WorktreeManager:
    async def acquire(
        self,
        *,
        task_id: str,
        role: str,
        retention: str = "ephemeral",
        base_commit: str | None = None,
    ) -> WorktreeLease: ...

    async def delete_merged(
        self,
        lease: WorktreeLease,
        *,
        merged_into: str,
    ) -> WorktreeDisposition: ...
```

显式 `base_commit` 只接受当前仓库中存在的 commit。首次创建按该 commit 建 Worktree；恢复时磁盘元数据的 base 必须完全匹配。现有调用不传值时行为不变。

`delete_merged` 仅用于内部集成 Worktree：要求调用方持有 lease、工作区干净、没有进行中的 Git 操作、Worktree HEAD 是 `merged_into` 的祖先，并通过现有路径与元数据保护后才删除 Worktree 和内部集成分支。成员 Worktree 不调用此接口。清理失败只形成 warning，不回滚已发布结果。

## 模块设计

### 集成状态与恢复

**职责：** 管理轮次、意图、accepted 顺序、失败和发布历史；根据 Git 事实修复中断边界。  
**对外接口：** `TeamIntegrationStore`、`TeamIntegrationService.recover()`、`snapshot()`。  
**依赖：** TeamStore、AtomicJsonFile、GitClient、WorktreeManager、IntegrationTaskPort；不依赖具体 TaskService。

恢复按以下状态机处理：

1. `intent=None`：验证 integration 分支 HEAD 等于记录 head；不一致时进入 blocked，不猜测。
2. `merge_task` 且 HEAD 未变化：若没有 merge in progress，可安全重试；若存在 merge，则收集冲突、abort 并记录失败。
3. `merge_task` 且 HEAD 已变化：验证新 HEAD 是两父 merge commit，父节点严格等于 intent 的 expected/source；随后补写任务 completed 与 accepted 记录。
4. `publish` 且 Lead HEAD 仍为 base：发布尚未发生，可重试。
5. `publish` 且 Lead HEAD 等于 expected integration head、工作区干净：发布已完成，补写 published、同步成员并清理内部 Worktree。
6. 其他 HEAD、分支或工作区组合：进入 blocked，保留全部现场并报告人工检查信息。

旧团队没有 `integration.json` 时创建初始状态。旧任务按 attempt/round 1 载入；已完成代码任务按依赖拓扑、创建时间和任务 ID 的稳定顺序导入内部集成。旧数据只在内部 Worktree 处理，不在 open 阶段直接发布 Lead 分支。

### 代码任务领取同步

**职责：** 捕获轮次目标、创建内部 Worktree、把成员安全快进到最新基线。  
**对外接口：** `assign_round()`、`prepare_code_claim()`。  
**依赖：** TeamIntegrationStore、TeamStore、GitClient、WorktreeManager。

第一次代码 claim 捕获 Lead `branch + HEAD`，确认 Lead clean/无进行中 Git 操作，再用显式 base 创建本轮内部 Worktree。创建结束后再次比较 Lead branch/HEAD；发生竞态则清理尚无成果的内部 Worktree并拒绝 claim。

成员同步依次校验花名册路径、实际当前分支、clean、无进行中 Git 操作、成员 HEAD 是 integration HEAD 的祖先，然后执行 ff-only。同步成功后原子记录 member sync head；任一失败都不改变任务 assignee/status/start_commit。

### 任务内部集成

**职责：** 校验任务来源、持久化 intent、合并、冲突回滚、任务完成与幂等。  
**对外接口：** `integrate_code_task()`。  
**依赖：** IntegrationTaskPort、TeamIntegrationStore、GitClient；任务完成通过注入回调，不导入具体 TaskService。

完整调用链：

```text
成员 complete
  → integration.lock
  → 恢复上次中断并读取当前 task/state
  → 校验成员 Worktree、start commit、source HEAD、attempt/round
  → 写 merge_task intent
  → 在内部 Worktree merge --no-ff
      ├─ 冲突：记录路径 → abort → 保留 task=in_progress → blocked
      └─ 成功：验证父节点 → TaskService 写 completed/outbox
                  → 写 accepted/清 intent → 解锁依赖
```

如果 source commit 已是 integration HEAD 祖先，则不运行 merge，只在确认当前 attempt 尚未记录后写 accepted 并完成任务。内部 merge commit 信息由系统生成，成员提交本身不改写。

### 最终发布与成员回同步

**职责：** 判断发布条件、安全快进 Lead、恢复发布边界、同步成员并清理内部 Worktree。  
**对外接口：** `finalize()`。  
**依赖：** TeamStore、IntegrationTaskPort、GitClient、WorktreeManager。

Lead 完成守卫发现当前轮次全部任务 completed 后调用 finalize。没有代码任务时直接归档 `not_needed`；有代码任务时先确认每个 `(task_id, attempt)` 都存在 accepted 记录，再写 publish intent。

发布前后均校验 Lead 当前分支等于 target、HEAD 等于 base、工作区 clean、无进行中 Git 操作。随后在 Lead cwd 执行一次 ff-only 到 integration head，并验证新 HEAD、索引和工作区状态。只有验证通过才持久化 published。

published 后逐个处理成员。idle 且 clean、分支正确、HEAD 为 published head 祖先的成员执行 ff-only；其他成员记录 pending/blocked warning。最后在确认 published head 包含 integration head 后调用 `delete_merged()`。成员同步或内部清理失败不会回滚 Lead，但会进入摘要并在新代码 claim 前再次处理。

### TeamManager 与完成守卫

**职责：** 构造共享服务、恢复团队、向提示与工具结果暴露状态、形成权威最终回复。  
**对外接口：** 现有 `create_team/open_team/status/wait_for_event/prompt_context/shutdown`。  
**依赖：** TeamIntegrationService、TaskService、TeamRuntimeSupervisor。

`TeamServices` 增加 integration。TeamManager 接收并复用 TUI 已有的 WorktreeManager，确保成员与内部集成 Worktree 使用同一仓库布局和保护器。create/open 后调用 recover；status、wait 和 prompt context 加入 snapshot；shutdown 释放内部 integration lease。

Lead 完成守卫的结果规则：

- 仍有 active/failed/cancelled 任务：维持现有未达成报告，不发布。
- 任务 completed 但内部状态 blocked：返回“内部集成失败”，不宣称完成。
- 全部 accepted 但 Lead 预检失败：返回“任务已完成但发布被拒绝”及重试条件。
- `not_needed`：权威追加“无需发布”。
- `published`：权威追加目标分支、最终提交、任务/成员摘要，替换原“待集成分支/未自动合并”文案。

### 提示、工具与文档

**职责：** 让 Lead 和成员理解当前集成轮次、阻塞原因和同步状态。  
**对外接口：** `TeamPromptContext` 渲染、现有团队工具结果、README。  
**依赖：** TeamManager snapshot。

Lead 提示显示 phase、target、accepted、failure；成员提示额外显示自己的 sync status/head/error。`manage_team status` 与 `team_wait` 的输入 schema 不变，输出增加 integration。`team_task complete` 的输入 schema 不变，成功结果增加 attempt/round，冲突通过现有结构化 tool error 返回并持久显示。

## 模块交互

```text
TeamTask create/reset
  └─ TeamIntegrationService.assign_round()
       └─ integration.json

TeamTask claim(code)
  └─ prepare_code_claim()
       ├─ 首个 code task：捕获 Lead branch/HEAD → 创建内部 Worktree
       └─ member Worktree clean/ancestor → ff-only 到 integration HEAD

TeamTask complete(code)
  └─ integrate_code_task()
       ├─ integration.json intent
       ├─ internal Worktree merge commit
       ├─ tasks.json completed + outbox
       └─ integration.json accepted

Lead completion guard
  └─ finalize()
       ├─ Lead 安全预检
       ├─ Lead ff-only 到 integration HEAD
       ├─ integration.json published
       ├─ 成员 Worktree 回同步
       └─ 安全清理内部 Worktree/分支
```

同一团队全部箭头都受 integration.lock 串行保护；不同团队使用不同锁、状态文件、内部分支和 Worktree。Git 自身的 index/ref lock 作为最终跨进程保护，JulyCode 不绕过或强制删除 Git 锁。

## 需求归属

| 需求 | 架构负责人 |
|------|------------|
| F1 | TaskService 类型分流、TeamManager 接入边界 |
| F2 | TeamIntegrationService 轮次初始化、GitClient 状态查询 |
| F3 | `prepare_code_claim`、成员同步状态 |
| F4 | TaskService + `integrate_code_task` 双重校验 |
| F5 | 内部集成状态机、任务完成回调 |
| F6 | integration.lock、TaskAttemptRef、accepted 记录 |
| F7 | Git merge outcome、冲突路径与 abort 校验 |
| F8 | 独立内部 Worktree、保守失败 |
| F9 | Lead 完成守卫、`finalize` |
| F10 | 发布前 Git 状态预检 |
| F11 | 内部 merge commits + Lead ff-only |
| F12 | publish intent、Git 事实恢复 |
| F13 | 成员 sync 字段与发布后回同步 |
| F14 | integration.json、recover 状态机 |
| F15 | IntegrationSummary、TeamSnapshot/Event/Prompt |
| F16 | Lead CompletionDecision 权威结果 |
| F17 | 系统派生输入、argv GitClient、安全 Worktree 接口 |
| F18 | TeamTask attempt/round、重置路径 |

## 文件组织

```text
src/julycode/
├── worktrees/
│   ├── git.py                 — Git 分支/操作状态、merge、abort、ff-only 与父节点校验
│   ├── manager.py             — 显式 base 创建与已发布内部 Worktree 安全清理
│   ├── models.py              — 必要的 Git/处置状态扩展
│   └── __init__.py            — 导出新增稳定类型
├── teams/
│   ├── integration.py         — 集成状态存储、轮次状态机、恢复、发布与成员同步
│   ├── models.py              — task attempt/round、成员同步、集成记录与摘要模型
│   ├── paths.py               — integration.json / integration.lock 安全路径
│   ├── store.py               — 初始化集成文件、成员同步字段级原子更新与旧数据兼容
│   ├── tasks.py               — create/reset/claim/complete/delete 接入集成生命周期
│   ├── manager.py             — 服务装配、恢复、状态快照和 Lead 完成守卫
│   ├── runtime.py             — 成员状态更新保留 sync 字段、共享 WorktreeManager
│   ├── tools.py               — 团队工具输出包含集成摘要，输入 schema 不变
│   └── __init__.py            — 导出用户可见集成类型
├── prompting/
│   └── builder.py             — Lead/成员系统提示渲染集成与同步状态
└── tui/
    └── app.py                 — 向 TeamManager 注入现有 WorktreeManager

tests/
├── test_team_integration.py   — 真实 Git 的轮次、合并、冲突、发布、恢复和竞态测试
├── test_team_tasks.py         — attempt/round、同步领取、完成幂等、重置和删除规则
├── test_team_store.py         — integration 文件与旧 JSON 兼容、成员字段级更新
├── test_team_runtime.py       — 成员跨轮次同步、运行状态不覆盖 sync 字段
├── test_teams_integration.py  — TeamManager snapshot、prompt 与完成守卫集成
├── test_team_e2e.py           — TUI 内多成员依赖任务自动发布链路
├── test_worktrees.py          — 新 GitClient 与 delete_merged 保护测试
├── test_prompting.py          — 团队集成提示渲染
├── test_config.py             — README 自动合并行为文档断言
└── e2e_mock_openai_server.py  — 自动合并与冲突 tmux 对话脚本

README.md                      — 长期团队自动集成、拒绝条件、恢复与不做事项
specs/team-worktree-auto-merge/
├── spec.md
├── plan.md
├── task.md
└── checklist.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 集成载体 | 每轮独立内部 Git Worktree/分支 | 冲突不触碰 Lead；兼容 Git 2.34.1；执行现有 Git hooks；复用已有安全路径和元数据。 |
| 集成粒度 | 每个成功代码任务一个 `--no-ff` merge commit | 保留任务提交原貌和双亲关系，任务/成员可追溯；依赖任务立即获得统一基线。 |
| 发布方式 | Lead 工作目录严格预检后执行一次 `--ff-only` | 内部分支已经包含全部成果，发布只前进一次；Git 同时更新 branch、index 和 worktree。 |
| 目标捕获 | 每轮首次代码 claim 时记录 Lead 命名分支与 HEAD | 研究轮次无 Git 副作用；防止发布到用户后来切换的分支。 |
| 并发控制 | 团队级跨进程 integration.lock + 固定 JSON 锁序 | 串行化完成、发布和任务新增，避免进程内 asyncio 锁无法覆盖重启或多进程。 |
| 崩溃一致性 | 持久 intent + Git 事实 + 完成记录三阶段对账 | 覆盖 Git 已成功但 JSON 未完成、JSON 有意图但 Git 未开始等边界，支持幂等恢复。 |
| 重复标识 | `(task_id, attempt)` | 同一任务重置后可以产生新成果，同时事件重放不会重复集成旧尝试。 |
| 冲突策略 | 收集路径后 abort，任务保持未完成 | 不让系统或模型猜测内容；此前 accepted 与成员成果均保留。 |
| 成员同步 | claim 前强制安全快进，发布后尽力快进 | 保证依赖可见；发布后单个成员异常不回滚已经成功的 Lead 结果。 |
| 内部清理 | 仅在发布提交包含 integration HEAD 后安全删除内部 Worktree | 避免长期轮次泄漏目录，同时不删除任何成员 Worktree/分支。 |
| 配置与工具 | 不增加开关和新模型工具 | 用户已选择长期团队默认闭环；合并目标与提交必须来自系统状态，减少自由输入攻击面。 |
| 旧数据 | 宽进严出地读取缺省 attempt/round，首次恢复构造 integration 状态 | 保留现有团队，无需手工迁移；损坏或不一致数据仍保守失败。 |
