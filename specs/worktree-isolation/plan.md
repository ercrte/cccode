# JulyCode Worktree 隔离 Plan

## 架构概览

本功能拆为五层，依赖方向保持从子 Agent 集成层指向 Worktree 领域层，Worktree 领域层不依赖 TUI、模型或 Agent Loop。

1. **配置与角色声明层**：扩展子 Agent 角色 frontmatter 和 `sub_agents.worktree` 项目配置。角色只声明是否需要隔离；目录名、分支名和生命周期策略不由模型输入控制。
2. **Worktree 领域层**：新增独立 `julycode.worktrees` 包，负责仓库布局发现、安全路径、Git 命令、环境初始化、元数据、创建/恢复、状态检查和保护删除。
3. **子 Agent 集成层**：`SubAgentManager` 在定义式隔离角色运行前取得 Worktree lease，在所有结束路径中释放 lease，并把处置结果写入子 Agent 结果。`SubAgentRunnerFactory` 只接收已经确定的绝对运行目录。
4. **目录隔离运行层**：工具执行器、权限控制器、Hook、上下文存储、项目指令和项目记忆均由子 Agent 的绝对 `cwd` 构造。进程不调用 `chdir`，也不共享按相对路径标识的状态。
5. **后台清理层**：独立 janitor 在应用挂载后立即调度一次清理，随后按间隔运行；应用卸载时取消。清理复用同一套路径、元数据、任务占用和 Git 保护检查。

需求归属如下：

| 需求 | 架构所有者 |
|------|------------|
| F1–F2 | 角色加载器、子 Agent 管理器 |
| F3–F8 | Worktree 生命周期管理器、路径与元数据模块 |
| F9–F11 | 子 Agent 运行工厂、提示构造、绝对路径边界 |
| F12–F14 | Worktree 环境初始化器、Git 配置适配 |
| F15–F16 | Worktree 状态检查与保护删除 |
| F17–F19 | Worktree janitor、生命周期管理器 |
| F20 | Worktree 错误模型、子 Agent 结果与 TUI 生命周期 |

## 核心数据结构

### `WorktreeConfig`

```python
@dataclass(frozen=True)
class WorktreeConfig:
    copy_paths: tuple[str, ...] = ()
    symlink_paths: tuple[str, ...] = ()
    ignored_copy_paths: tuple[str, ...] = ()
    cleanup_interval_seconds: float = 3600.0
    retention_days: float = 7.0
```

挂在现有 `SubAgentConfig.worktree` 下。三类路径都是仓库根目录相对路径，不支持 glob、独立目标或仓库外源。配置加载阶段完成语法、边界形式和跨类别重复校验；源是否存在、类型是否匹配以及 ignored 项是否确实被 Git 忽略，在创建时校验。

配置示例：

```yaml
sub_agents:
  worktree:
    copy_paths:
      - .julycode.permissions.local.yaml
    symlink_paths:
      - .venv
    ignored_copy_paths:
      - .env
    cleanup_interval_seconds: 3600
    retention_days: 7
```

### `SubAgentIsolation`

```python
SubAgentIsolation = Literal["shared", "worktree"]

@dataclass(frozen=True)
class SubAgentRoleFrontmatter:
    # 现有字段保持不变
    isolation: SubAgentIsolation = "shared"
```

frontmatter 未声明 `isolation` 时解析为 `shared`；只接受显式值 `worktree`，其他非空值使该角色解析失败。Fork 委派不读取该字段。

### `RepositoryLayout`

```python
@dataclass(frozen=True)
class RepositoryLayout:
    main_cwd: Path
    repository_root: Path
    relative_cwd: Path
    storage_root: Path
    repository_id: str
```

`main_cwd`、`repository_root` 和 `storage_root` 均为规范化绝对路径。`storage_root` 固定为仓库根目录下的 `.julycode/worktrees`；`relative_cwd` 用于 JulyCode 从仓库子目录启动时，让子 Agent 进入 Worktree 中对应的逻辑子目录。`repository_id` 由规范化仓库根路径稳定派生，恢复时不需要 Git。

### `WorktreeMetadata`

```python
@dataclass(frozen=True)
class WorktreeMetadata:
    version: int
    repository_id: str
    task_id: str
    role: str
    relative_name: str
    branch: str
    base_commit: str
    created_at: str
```

元数据写在 Worktree 根目录的固定 JulyCode 标记文件中。该标记路径通过仓库本地 exclude 忽略，不参与工作树 dirty 判定。恢复时要求版本受支持，所有身份字段与根据当前主目录、角色和任务 ID 计算的期望值一致，并要求提交值满足完整十六进制对象 ID 格式。

### `WorktreeLease`

```python
@dataclass(frozen=True)
class WorktreeLease:
    metadata: WorktreeMetadata
    root: Path
    cwd: Path
    recovered: bool
```

代表一个正在被子 Agent 使用的隔离目录。`root` 是 Worktree checkout 根，`cwd` 是与主 Agent 启动目录对应的 Worktree 内绝对目录。lease 存在期间任务 ID 被登记为 active，janitor 不得清理。

### `WorktreeChangeState`

```python
@dataclass(frozen=True)
class WorktreeChangeState:
    dirty: bool
    untracked: tuple[str, ...]
    new_commit_count: int
    upstream: str | None
    unpushed_commit_count: int
```

状态检查使用 porcelain Git 输出和创建基线计算。无新增提交时 `unpushed_commit_count=0`；有新增提交但没有 upstream 时，全部新增提交计为未推送；有 upstream 时，计算创建基线之后且 upstream 不可达的提交数。任何命令或解析失败都不构造“安全”状态，而是抛出保守失败错误。

### `WorktreeDisposition`

```python
WorktreeDispositionStatus = Literal["cleaned", "retained"]

@dataclass(frozen=True)
class WorktreeDisposition:
    status: WorktreeDispositionStatus
    root: Path
    cwd: Path
    branch: str
    reason: str
    state: WorktreeChangeState | None = None
```

子 Agent 退出和后台清理都返回结构化处置结果。前台自动退出只在无修改、无未跟踪文件、无新增提交时返回 `cleaned`；其他情况返回 `retained`。错误不会伪装成 cleaned。

### `SubAgentWorktreeInfo`

```python
@dataclass(frozen=True)
class SubAgentWorktreeInfo:
    root: str
    cwd: str
    branch: str
    base_commit: str
    disposition: WorktreeDispositionStatus
    reason: str
```

作为 `SubAgentResult.worktree` 的可选字段。定义式共享目录和 Fork 结果为 `None`。工具结果与后台完成通知展示该字段，但不改变 `delegate_agent` 的输入 schema。

### `ActiveSubAgentPrompt` 扩展

```python
@dataclass(frozen=True)
class ActiveSubAgentPrompt:
    # 现有字段保持不变
    isolation: SubAgentIsolation = "shared"
    cwd: Path | None = None
    main_cwd: Path | None = None
    branch: str | None = None
```

隔离角色填充全部 Worktree 字段；共享角色只设置 `isolation="shared"`。

### `CleanupReport`

```python
@dataclass(frozen=True)
class CleanupItemResult:
    path: Path
    status: Literal["cleaned", "skipped", "failed"]
    reason: str

@dataclass(frozen=True)
class CleanupReport:
    items: tuple[CleanupItemResult, ...] = ()
```

janitor 每轮输出可观察结果。常规安全跳过不打断主流程；损坏元数据、Git 检查失败和删除异常以失败项报告给日志回调。

## 核心接口

### 路径与仓库布局

```python
def validate_relative_name(value: str) -> tuple[str, ...]:
    """返回已校验段；失败时不执行任何文件系统或 Git 写操作。"""

def validate_config_path(value: str) -> Path:
    """校验环境规则的仓库相对路径，不允许绝对路径、空段或越界段。"""

def discover_repository_layout(main_cwd: Path) -> RepositoryLayout:
    """只通过文件系统向上寻找 .git 边界并构造绝对布局，不调用 Git。"""

def resolve_inside(root: Path, relative: Path, *, follow_leaf: bool = True) -> Path:
    """解析并用 relative_to 确认边界，禁止字符串前缀归属判断。"""
```

`validate_relative_name` 实现 spec 中 200/64 字符和 ASCII 字符规则。`validate_config_path` 更严格地拒绝 `.`、`..`、反斜杠和空段；环境配置不接受目录名称语法以外的特殊路径表达式。

### Git 执行

```python
class GitClient:
    async def run(self, args: Sequence[str], *, cwd: Path) -> GitCommandResult: ...
    async def repository_root(self, *, cwd: Path) -> Path: ...
    async def head_commit(self, *, cwd: Path) -> str: ...
    async def create_worktree(self, *, cwd: Path, path: Path, branch: str, base: str) -> None: ...
    async def configure_hooks(self, *, main_root: Path, worktree_root: Path) -> None: ...
    async def change_state(self, *, worktree_root: Path, base: str) -> WorktreeChangeState: ...
    async def remove_worktree(self, *, main_root: Path, path: Path) -> None: ...
    async def delete_branch(self, *, main_root: Path, branch: str) -> None: ...
```

```python
@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: str
    stderr: str
```

所有命令使用参数数组和显式绝对 `cwd` 调用异步子进程，不经过 shell。错误包装为带阶段、命令动作和脱敏 stderr 摘要的 `WorktreeError`，不回显环境配置内容。

### 环境初始化

```python
class WorktreeEnvironmentInitializer:
    async def initialize(
        self,
        *,
        layout: RepositoryLayout,
        lease: WorktreeLease,
        config: WorktreeConfig,
    ) -> None: ...
```

初始化前先解析并验证全部源、目标和类型，再开始写入；顺序为独立复制、ignored 项复制、目录软链、Git hooks。三类路径不得重复。复制使用 `shutil.copy2`/`copytree` 保留基础元数据但生成独立内容；复制源自身若是软链或解析后越过主仓库则拒绝。软链源必须是主目录内真实目录，目标使用绝对链接并仍落在主仓库边界内。ignored 项必须由 Git 确认为主工作树中的忽略路径。

初始化器维护本次创建目标的操作记录。中途失败时仅回滚本次已创建的目标；无法确认归属的既有目标从不删除。回滚失败会随原始错误一起报告，由生命周期管理器保留 Worktree 供诊断。

### Worktree 生命周期

```python
class WorktreeManager:
    def __init__(
        self,
        main_cwd: Path,
        config: WorktreeConfig,
        *,
        git: GitClient | None = None,
        initializer: WorktreeEnvironmentInitializer | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None: ...

    async def acquire(self, *, task_id: str, role: str) -> WorktreeLease: ...
    async def finish(self, lease: WorktreeLease) -> WorktreeDisposition: ...
    async def delete(
        self,
        lease: WorktreeLease,
        *,
        allow_pushed_commits: bool = False,
    ) -> WorktreeDisposition: ...
    async def cleanup_expired(self) -> CleanupReport: ...
    def active_task_ids(self) -> frozenset[str]: ...
```

`acquire` 先生成并校验 `<role>/<task_id>` 相对名称，然后检查目标是否存在：

1. **目标存在**：只读元数据文件并完成仓库路径身份、任务、角色、名称、确定性分支和提交格式校验；不调用 `GitClient`、不初始化环境、不改写元数据。通过后登记 active 并返回 `recovered=True`。
2. **目标不存在**：在目标级异步锁内再次确认不存在，调用 Git 验证真实仓库根与文件系统发现结果一致，读取 `HEAD`，拒绝冲突分支，通过 `git worktree add -b` 创建，配置本地 exclude、写入元数据、初始化环境并登记 active。

创建失败时只删除能够证明属于本次调用且通过保护检查的内容。元数据尚未可靠写入、回滚失败或状态未知时保留现场，不使用强制递归删除。

`finish` 在目标级锁内保持 lease 的 active 登记，读取状态并通过内部 `_delete_locked()` 完成处置后，才在 `finally` 中取消登记，避免 janitor 在退出检查过程中抢占目录。无任何修改和新增提交时进入普通删除路径；否则返回 retained。即使子 Agent 失败或取消也执行同样逻辑。状态检查或删除无法确认安全时，`finish` 返回 `retained` 并携带失败原因，不覆盖已经得到的子 Agent 任务结果。

`delete` 取得目标级锁后执行路径层、元数据层和 active/Git 层检查，再调用同一 `_delete_locked()`，不会重入锁。由持有该 lease 的 `finish` 调用时允许当前任务完成自身删除，其他 active 任务和 janitor 均不得删除。普通退出要求 `new_commit_count == 0`；janitor 传 `allow_pushed_commits=True` 时，可删除有新增提交但 `unpushed_commit_count == 0` 的目录。删除顺序为 Git worktree remove，再删除确定性临时分支；分支删除失败会报告部分失败，不声称完整清理成功。

`cleanup_expired` 负责在固定 storage root 下查找元数据标记的父目录，不把普通中间目录视为候选；候选先比较 `created_at + retention_days`，再执行路径、元数据、active 和 Git 保护检查。单个候选失败被收集到报告中，不中断其余候选。

### 后台清理

```python
class WorktreeJanitor:
    def __init__(
        self,
        manager: WorktreeManager,
        *,
        report: Callable[[CleanupReport], None] | None = None,
    ) -> None: ...

    def start(self) -> None: ...
    async def run_once(self) -> CleanupReport: ...
    async def close(self) -> None: ...
```

`start()` 只创建后台任务并立即返回。后台任务先通过 `manager.cleanup_expired()` 运行一次，再按 `cleanup_interval_seconds` 等待。`run_once()` 是同一调用的显式测试入口。`close()` 取消定时任务并等待退出，不等待下一次间隔。

### 子 Agent 工作目录

```python
@dataclass(frozen=True)
class SubAgentWorkingContext:
    cwd: Path
    main_cwd: Path
    isolation: SubAgentIsolation
    lease: WorktreeLease | None = None
```

```python
class SubAgentRunnerFactory:
    def create_runner(
        self,
        *,
        task_id: str,
        invocation: SubAgentInvocation,
        parent: ParentAgentContext,
        role: SubAgentRoleDefinition | None,
        background: bool,
        working_context: SubAgentWorkingContext,
    ) -> tuple[AgentLoopRunner, AgentCommand, ChatSession]: ...
```

工厂不再从父 `executor.context.cwd` 隐式派生子 Agent 目录。它使用 `working_context.cwd` 分别创建子工具执行器、权限控制器、上下文管理器、项目知识/记忆管理器和活动提示。每个实例只属于当前子 Agent，不建立切换时清理缓存的机制。

### 项目知识加载

```python
class SessionBootstrapper:
    def load_knowledge(self) -> KnowledgeContext: ...

class SessionMemoryManager:
    def load_runtime_context(self) -> KnowledgeContext: ...
```

把现有私有项目知识读取流程提升为不恢复会话的公开入口。子 Agent 创建时以其绝对 `cwd` 构造独立 `SessionMemoryManager` 并调用该入口，再传给 `AgentLoopRunner`。因此项目指令和项目记忆从 Worktree 读取，用户级记忆仍按现有用户目录读取；不会把主 Agent 会话历史恢复进定义式子 Agent。

### 绝对路径边界

```python
@dataclass(frozen=True)
class ToolContext:
    cwd: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "cwd", self.cwd.resolve())
```

`ContextManager` 同样在构造时保存 `cwd.resolve()`，并将该绝对路径传给 `ContextStore`。`FileReadCache` 已以 `path.resolve()` 为 key，项目指令、记忆和会话存储现有实现也已保存绝对根路径。系统提示本身无跨 Agent 的路径缓存；每个 runner 独立构造，运行时提示直接使用绝对 `cwd`。该设计保持“按绝对路径隔离、无需切换时清缓存”。

## 模块设计

### `julycode.worktrees.models`

**职责：** 定义配置、仓库布局、元数据、lease、变更状态、处置结果、清理报告和错误类型。  
**对外接口：** 上述 dataclass、Literal、`WorktreeError(stage, message)`。  
**依赖：** 标准库 `dataclasses`、`datetime`、`pathlib`。

### `julycode.worktrees.paths`

**职责：** 发现文件系统仓库边界，校验安全名称和配置路径，生成确定性目录/分支，执行规范化边界判断。  
**对外接口：** `validate_relative_name()`、`validate_config_path()`、`discover_repository_layout()`、`resolve_inside()`、`worktree_name(role, task_id)`、`branch_name(relative_name)`。  
**依赖：** `julycode.worktrees.models`、标准库路径与哈希模块。

### `julycode.worktrees.git`

**职责：** 以显式 cwd 执行 Git 原子命令，归一化 Git 错误，创建/移除 Worktree，配置 hooks，读取状态和提交保护信息。  
**对外接口：** `GitClient`、`GitCommandResult`。  
**依赖：** `asyncio.subprocess`、`julycode.errors.redact_secret`、Worktree models。

### `julycode.worktrees.environment`

**职责：** 执行配置驱动的复制、ignored 文件补齐、目录软链和失败回滚。  
**对外接口：** `WorktreeEnvironmentInitializer.initialize()`。  
**依赖：** `shutil`、`pathlib`、路径模块、GitClient。

### `julycode.worktrees.manager`

**职责：** 协调整个创建/恢复/进入/退出/删除流程，管理目标锁和 active lease，读写元数据，执行三层安全过滤。  
**对外接口：** `WorktreeManager`。  
**依赖：** Worktree models、paths、git、environment；不依赖子 Agent 或 TUI。

### `julycode.worktrees.janitor`

**职责：** 管理启动即运行和周期运行的清理任务，逐候选隔离失败并输出报告。  
**对外接口：** `WorktreeJanitor`。  
**依赖：** WorktreeManager、`asyncio`。

### `julycode.subagents.loader` / `julycode.subagents.models`

**职责：** 解析 `isolation`，扩展角色、运行提示、结果与后台记录的数据模型。  
**对外接口调整：** `SubAgentRoleFrontmatter.isolation`、`ActiveSubAgentPrompt` Worktree 字段、`SubAgentResult.worktree`。  
**依赖：** Worktree 结果只通过轻量 `SubAgentWorktreeInfo` 表达，不让通用角色加载器依赖 GitClient。

### `julycode.subagents.manager`

**职责：** 根据角色隔离声明建立 `SubAgentWorkingContext`，用 `try/finally` 覆盖完成、失败、取消和工厂异常的退出处置；管理并启动 janitor；补充前台结果和后台通知。  
**对外接口调整：** 构造函数可注入 `WorktreeManager`/`WorktreeJanitor`；新增 `start()`；`close()` 同时关闭 janitor。  
**依赖：** Worktree manager/janitor、现有 runner factory。

### `julycode.subagents.runtime`

**职责：** 从显式 `SubAgentWorkingContext` 构造所有 cwd 相关子运行组件和隔离提示。  
**对外接口调整：** `SubAgentRunnerFactory.create_runner(..., working_context=...)`。  
**依赖：** 现有 Agent Loop、工具、权限、上下文、Hook、记忆模块。

### `julycode.prompting.builder`

**职责：** 在 active 子 Agent 块中注入 isolation、隔离目录、主目录、分支和禁止越界说明。  
**对外接口：** 不新增公开接口，只扩展 `_sub_agent_context_lines()` 输出。  
**依赖：** 扩展后的 `ActiveSubAgentPrompt`。

### `julycode.config`

**职责：** 解析 `sub_agents.worktree`，校验正数时间、字符串数组、安全相对路径和三类规则不重复。  
**对外接口调整：** `SubAgentConfig.worktree: WorktreeConfig`。  
**依赖：** Worktree config 与路径校验。

### `julycode.tools.base` / `julycode.context.manager` / `julycode.memory`

**职责：** 在公共构造边界规范化 cwd；提供不恢复会话的项目知识加载入口。  
**对外接口调整：** `ToolContext.__post_init__()`、`SessionBootstrapper.load_knowledge()`、`SessionMemoryManager.load_runtime_context()`。  
**依赖：** 现有模块，不反向依赖 Worktree。

### `julycode.tui.app`

**职责：** `on_mount()` 在子 Agent 角色刷新后调用 `SubAgentManager.start()`；`on_unmount()` 通过现有 `close()` 取消子任务和 janitor；清理失败报告写入 stderr，不阻断界面。  
**对外接口：** 无。  
**依赖：** 扩展后的 SubAgentManager。

## 模块交互

### 首次创建与运行

```text
delegate_agent(defined role)
  → SubAgentManager 读取 role.isolation
  → WorktreeManager.acquire(task_id, role)
      → 文件系统发现仓库布局 + 安全名称校验
      → 目标不存在，GitClient 校验仓库根与 HEAD
      → 写仓库本地 exclude
      → git worktree add 创建目录和分支
      → 写恢复元数据
      → EnvironmentInitializer 复制/软链/配置 hooks
      → 登记 active lease
  → SubAgentRunnerFactory(working_context.cwd)
      → 独立 ToolContext / Permission / Context / Memory / Prompt
  → AgentLoopRunner 执行任务
  → WorktreeManager.finish(lease)
      → 检查 dirty、untracked、新增提交
      → 无变更：保护删除 Worktree 与分支
      → 有变更：保留并返回路径、分支和原因
  → SubAgentResult / 后台通知回到主 Agent
```

### 快速恢复

```text
WorktreeManager.acquire
  → 生成确定性绝对目标路径
  → 发现目录已存在
  → 只读固定元数据文件
  → 校验 repository_id / task / role / name / branch / base 格式
  → 登记 active lease
  → 返回 recovered=True
```

该分支在检查目录存在之后不进入任何 GitClient 或环境初始化调用。测试使用会在调用时失败的 fake GitClient，证明零 Git 行为。

### 周期清理

```text
TUI on_mount
  → WorktreeJanitor.start（立即返回）
  → run_once 扫描 metadata marker
      → 过滤未过期
      → 路径层：必须位于 storage_root
      → 元数据层：必须属于当前 repository_id
      → 状态层：不得 active，Git 状态必须可确认
      → dirty / untracked / 无 upstream 的新增提交：保留
      → 有 upstream 且新增提交全部可达：允许删除
  → 记录 CleanupReport
  → sleep(cleanup_interval_seconds)
TUI on_unmount
  → janitor.close 取消 sleep/扫描任务并等待结束
```

## 文件组织

```text
src/julycode/
├── config.py                         — 解析 sub_agents.worktree 配置
├── tools/base.py                     — ToolContext 规范化绝对 cwd
├── context/manager.py                — 上下文边界保存绝对 cwd
├── memory/manager.py                 — 加载项目知识但不恢复子 Agent 历史
├── memory/recovery.py                — 暴露项目知识读取入口
├── prompting/builder.py              — 注入 Worktree 隔离提示
├── subagents/
│   ├── loader.py                     — 解析 isolation frontmatter
│   ├── manager.py                    — 接入 lease、结果处置和 janitor
│   ├── models.py                     — 扩展角色、提示和结果结构
│   ├── runtime.py                    — 用显式工作上下文创建子运行时
│   └── tools.py                      — 输出 Worktree 处置信息，输入 schema 不变
├── tui/app.py                        — 启停后台 janitor
└── worktrees/
    ├── __init__.py                   — 导出稳定接口
    ├── models.py                     — 生命周期数据结构和错误
    ├── paths.py                      — 安全名称、路径边界和仓库布局
    ├── git.py                        — Git 子进程适配与状态判定
    ├── environment.py                — 复制、软链、ignored 文件与回滚
    ├── manager.py                    — 创建、恢复、退出和保护删除
    └── janitor.py                    — 启动及周期过期清理
tests/
├── test_config.py                    — Worktree 配置合法/非法场景
├── test_tools.py                     — ToolContext 绝对 cwd
├── test_prompting.py                 — 隔离路径提示
├── test_subagents.py                 — 角色解析、runner cwd、结果与取消集成
└── test_worktrees.py                 — 临时 Git 仓库生命周期、安全与清理测试
README.md                             — 角色 isolation 与项目初始化配置说明
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 生命周期归属 | 独立 `julycode.worktrees` 包 | 避免 Git、文件初始化和清理调度挤入 SubAgentManager，便于无模型测试 |
| 存储根 | 固定 `<repo>/.julycode/worktrees` | 目录可预测且不接受模型配置；通过仓库本地 exclude 保证不追踪 |
| 目录与分支命名 | `<role>/<task_id>` 与 `julycode/<role>/<task_id>` | 只使用已验证角色和内部 ID，支持嵌套且不读取任务文本 |
| 仓库子目录启动 | Worktree 内进入与主 cwd 相同的相对子目录 | 保持现有 JulyCode 项目边界语义，不强制切到仓库顶层 |
| Git 调用 | 异步子进程、argv、显式 cwd、无 shell | 避免命令注入和全局 chdir，错误与超时可控 |
| 快速恢复身份 | 文件系统布局 + 固定元数据 + 确定性字段 | 目录存在分支可以完全不调用 Git，异常元数据保守拒绝 |
| 元数据位置 | Worktree 根固定隐藏标记，本地 exclude 忽略 | 恢复只需读目标目录，且标记不制造 dirty 状态 |
| 初始化配置 | 精确相对路径数组，无 glob、无脚本、无自定义目标 | 满足常见复制/软链需求，同时限制输入面和越界风险 |
| 初始化失败 | 仅回滚本次已创建目标；不强删未知内容 | 防止初始化半成功后继续运行，也避免误删既有文件 |
| hooks | 无自定义时使用共享 hooks；自定义时启用 `extensions.worktreeConfig` 并通过 worktree-local Git config 指向主目录当前有效绝对路径 | 子目录执行同一套校验，不修改主工作目录有效 hooks 配置 |
| 前台自动清理 | 只清理无修改且无新增提交的 Worktree | 与已确认的保守保留策略一致，即使提交已推送也先保留给主 Agent |
| 未推送判定 | 新增提交 + upstream 可达性；无 upstream 全部视为未推送 | 不依赖网络，符合 Git 分支跟踪语义，未知状态一律保留 |
| janitor 删除 | 过期且 clean，新增提交全部被 upstream 包含时允许 | 能回收已推送成果，同时不绕过变更保护 |
| 并发控制 | 目标路径级 `asyncio.Lock` + active task 集合 | 覆盖单进程内委派与 janitor 竞争，依赖关系简单且可测试 |
| cwd 隔离 | 每个 runner 新建绝对路径绑定的执行、权限、上下文和记忆实例 | 无需全局缓存清理，不会让主 Agent 或其他子 Agent切目录 |
| 清理调度 | TUI 生命周期持有 janitor，启动即异步运行，退出取消 | 不阻塞启动/退出，后台任务不会泄漏 |
| 测试仓库 | 每个用例创建临时 Git 仓库与可选 bare remote | 不依赖当前源码目录的 Git 元数据，也不访问网络 |

## 测试策略

- 路径单元测试覆盖总长度、段长度、ASCII 字符、嵌套、空段、`.`、`..`、绝对路径、反斜杠、符号链接越界和解析后越界。
- Git 生命周期测试在临时仓库中验证主目录 dirty 时创建、分支/目录冲突、两个 Worktree 相同文件隔离、hooks 继承、无变更清理和有变更保留。
- 快速恢复测试预置合法/损坏/错仓库/错任务元数据，并注入“任何调用都失败”的 GitClient，证明合法恢复零 Git 且只读。
- 初始化测试覆盖文件与目录复制、独立副本、ignored 文件、目录软链、重复规则、源缺失、类型错误、目标冲突、源软链越界和失败回滚。
- 删除保护测试覆盖未提交、未跟踪、新增无 upstream、新增落后 upstream、新增已推送和 Git 状态未知。
- janitor 测试注入时钟，覆盖未过期、active、根外、坏元数据、dirty、未推送、已推送、单候选失败不影响其他候选，以及 start/close 不阻塞。
- 子 Agent 集成测试覆盖共享角色不创建 Worktree、隔离角色传入绝对 cwd、完成/失败/取消均 finish、结果 payload 和后台通知包含处置信息、Fork 行为不变。
- 全量回归运行 `python -m pytest`；最终按项目要求在 tmux 中启动 JulyCode，用真实对话触发隔离定义式子 Agent，并逐项对照 `checklist.md` 验收。
