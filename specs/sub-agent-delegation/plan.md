# JulyCode 子 Agent 委派 Plan

## 架构概览
本阶段新增 `julycode.subagents` 子系统，承载角色定义、委派工具、子 Agent 运行、后台任务和结果通知。主 Agent 只看到一个稳定工具 `delegate_agent`；角色摘要和后台状态通过运行时提示注入，不通过增删工具实现。`delegate_agent` 根据 `type` 参数分流定义式和 Fork 式委派。

定义式子 Agent 使用角色定义启动。角色定义由 Markdown + YAML frontmatter 组成，加载优先级为项目级、用户级、内置级、插件级。定义式运行时创建空白 `ChatSession`，把角色正文作为子 Agent 运行时提示持续注入，只追加本次子任务用户消息。

Fork 式子 Agent 从父 `ChatSession` 的安全快照启动，复制当前可见历史和父 Agent 当前可见工具集合，并强制后台运行。Fork 使用与父 Agent 相同的稳定提示块和相同的历史前缀，尽量让供应商 prompt cache 命中；实际命中情况以供应商返回的 `TokenUsage.cache` 为准并记录到子任务结果。

状态隔离由新的子运行上下文保证：每个子 Agent 拥有独立 `ChatSession`、`ContextManager`、`PermissionController`、文件读取缓存、Hook 运行时状态和用量累积器；共享的是 `LLMProvider`/`provider_resolver`、Hook 配置与动作执行能力、全局 `ToolRegistry`、项目 cwd 和底层文件系统视图。子 Agent 中间消息只留在子会话中，不写入主会话。

后台任务由 `SubAgentManager` 追踪。显式后台、Fork 强制后台、前台超时自动后台和用户手动切后台都会变成同一种后台记录。后台完成后，管理器自动把一条中文完成通知追加到主 `ChatSession`，并通过 TUI 回调显示到主对话。

工具安全由 `ToolPolicy` 扩展和 `SubAgentToolFilter` 共同实现。全局禁止工具、角色白名单、角色黑名单、父工具继承集合、后台白名单和防嵌套限制都会在工具暴露前过滤，并在模型仍请求被禁工具时返回结构化失败结果。

## 核心数据结构

### SubAgentConfig
```python
@dataclass(frozen=True)
class SubAgentConfig:
    enabled: bool = True
    foreground_timeout_seconds: float = 30.0
    default_max_iterations: int | None = None
    max_background_tasks: int = 8
    global_blocked_tools: tuple[str, ...] = ("delegate_agent",)
    background_allowed_tools: tuple[str, ...] = ("read_file", "find_files", "search_code")
    model_aliases: dict[str, str] = field(default_factory=dict)
    plugin_role_roots: tuple[str, ...] = ()
```
说明：新增在 `AppConfig.sub_agents`。`model_aliases` 把 `haiku`、`sonnet`、`opus` 等档位映射到实际模型名；未配置时直接把档位名传给现有 `provider_resolver`。

### SubAgentRoleFrontmatter
```python
SubAgentRoleModel = Literal["inherit", "haiku", "sonnet", "opus"] | str
SubAgentPermissionMode = Literal["inherit", "strict", "default", "permissive"]

@dataclass(frozen=True)
class SubAgentRoleFrontmatter:
    name: str
    description: str
    tools_allow: tuple[str, ...]
    tools_deny: tuple[str, ...]
    model: SubAgentRoleModel = "inherit"
    max_iterations: int | None = None
    permission_mode: SubAgentPermissionMode = "inherit"
```
说明：解析 YAML frontmatter。字段名支持 `tools_allow`/`tools_deny`，并兼容 `allow_tools`/`deny_tools` 作为别名。

### SubAgentRoleDefinition
```python
SubAgentRoleSource = Literal["project", "user", "builtin", "plugin"]

@dataclass(frozen=True)
class SubAgentRoleDefinition:
    frontmatter: SubAgentRoleFrontmatter
    body: str
    source_scope: SubAgentRoleSource
    source_path: str

    @property
    def name(self) -> str: ...
    def summary(self) -> SubAgentRoleSummary: ...
```
说明：正文 `body` 是定义式子 Agent 生命周期内持续注入的角色提示。

### SubAgentRoleRoots
```python
@dataclass(frozen=True)
class SubAgentRoleRoots:
    project: Path
    user: Path
    builtin: Traversable
    plugins: tuple[Path | Traversable, ...] = ()
```
默认路径：
```text
<项目>/.julycode/agents/
~/.julycode/agents/
julycode.subagents.builtin
config.sub_agents.plugin_role_roots
```

### SubAgentInvocation
```python
SubAgentType = Literal["defined", "fork"]

@dataclass(frozen=True)
class SubAgentInvocation:
    type: SubAgentType
    task: str
    role: str | None = None
    background: bool = False
    max_iterations: int | None = None
    foreground_timeout_seconds: float | None = None
```
说明：由 `delegate_agent` 工具参数解析得出。`defined` 必须提供 `role`；`fork` 忽略 `role` 并强制 `background=True`。

### ParentAgentContext
```python
@dataclass(frozen=True)
class ParentAgentContext:
    session: ChatSession
    mode: AgentMode
    command: AgentCommand
    allowed_tools: tuple[ToolSpec, ...]
    tool_whitelist: frozenset[str] | None
```
说明：主 Agent 每轮执行工具前设置当前父上下文，`DelegateAgentTool` 通过它获得父历史快照和当前工具可见范围。

### SubAgentToolFilter
```python
@dataclass(frozen=True)
class SubAgentToolFilter:
    inherited_tools: frozenset[str] | None = None
    role_allow: frozenset[str] | None = None
    role_deny: frozenset[str] = frozenset()
    global_blocked: frozenset[str] = frozenset()
    background_allowed: frozenset[str] | None = None
    nested_blocked: frozenset[str] = frozenset({"delegate_agent"})
```
说明：传给扩展后的 `ToolPolicy`。`None` 表示不按该维度收窄；空集合表示没有普通工具可见。

### SubAgentResult
```python
SubAgentStatus = Literal["queued", "running", "background", "completed", "failed", "cancelled"]

@dataclass(frozen=True)
class SubAgentResult:
    task_id: str
    type: SubAgentType
    role: str | None
    status: SubAgentStatus
    task: str
    summary: str
    final_text: str
    stop_reason: str | None
    key_outputs: tuple[str, ...]
    error: str | None
    usage: TokenUsage | None
```
说明：前台完成时作为工具结果返回；后台完成时用于生成主对话通知。

### BackgroundSubAgentRecord
```python
@dataclass
class BackgroundSubAgentRecord:
    task_id: str
    invocation: SubAgentInvocation
    status: SubAgentStatus
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    result: SubAgentResult | None = None
    error: str | None = None
    usage: TokenUsage | None = None
    task: asyncio.Task[SubAgentResult] | None = None
    force_background: asyncio.Event | None = None
    notified: bool = False
```

### FileReadCache
```python
@dataclass
class FileReadCacheEntry:
    path: Path
    mtime_ns: int
    size: int
    content: str

class FileReadCache:
    def get(self, path: Path) -> str | None: ...
    def put(self, path: Path, content: str) -> None: ...
```
说明：挂到 `ToolContext.read_cache`；主 Agent 和每个子 Agent 各自持有独立实例。`ReadFileTool` 在缓存命中且 mtime/size 未变时返回缓存内容。

### SubAgentPromptContext
```python
@dataclass(frozen=True)
class ActiveSubAgentPrompt:
    task_id: str
    type: SubAgentType
    role_name: str | None
    role_description: str | None
    role_body: str | None
    task: str
    non_interactive: bool = True

@dataclass(frozen=True)
class SubAgentPromptContext:
    available_roles: tuple[SubAgentRoleSummary, ...] = ()
    warnings: tuple[SubAgentRoleWarning, ...] = ()
    active: ActiveSubAgentPrompt | None = None
    background: tuple[SubAgentBackgroundSummary, ...] = ()
```
说明：主 Agent 看到可用角色摘要和后台任务摘要；子 Agent 看到自己的角色正文或 Fork 约束。

## 核心接口

### SubAgentRoleLoader
```python
class SubAgentRoleLoader:
    def __init__(self, roots: SubAgentRoleRoots) -> None: ...
    def discover(self) -> SubAgentRoleCatalog: ...
```
职责：按项目、用户、内置、插件顺序发现 `*.md` 角色文件；同名 first-wins；同层重复生成 warning；解析失败生成 warning。

### SubAgentManager
```python
class SubAgentManager:
    def __init__(
        self,
        *,
        roots: SubAgentRoleRoots,
        tool_registry: ToolRegistry,
        executor: ToolExecutor,
        config: AppConfig,
        provider: LLMProvider,
        provider_resolver: ProviderResolver,
        hook_manager: HookManager | None,
        main_session: ChatSession,
        notify: Callable[[str], Awaitable[None]] | None = None,
    ) -> None: ...

    def refresh_if_changed(self) -> SubAgentRefreshReport: ...
    def prompt_context(self) -> SubAgentPromptContext: ...
    def bind_parent_context(self, context: ParentAgentContext | None) -> None: ...
    async def delegate(self, invocation: SubAgentInvocation) -> SubAgentResult | BackgroundSubAgentRecord: ...
    def background_snapshot(self) -> tuple[BackgroundSubAgentRecord, ...]: ...
    def background_current_foreground(self) -> bool: ...
    async def close(self) -> None: ...
```
职责：管理角色目录、创建子运行上下文、启动前台或后台子任务、自动通知主对话。传入的 `hook_manager` 只作为配置与动作执行能力来源，子运行上下文会创建独立 Hook 运行时状态。

### DelegateAgentTool
```python
DELEGATE_AGENT_TOOL_NAME = "delegate_agent"

class DelegateAgentTool:
    spec: ToolSpec
    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]: ...
```
工具参数：
```json
{
  "type": "object",
  "properties": {
    "type": {"type": "string", "enum": ["defined", "fork"]},
    "task": {"type": "string"},
    "role": {"type": "string"},
    "background": {"type": "boolean"},
    "max_iterations": {"type": "integer"},
    "foreground_timeout_seconds": {"type": "number"}
  },
  "required": ["type", "task"],
  "additionalProperties": false
}
```
说明：工具本身始终注册；缺少 `defined` 的 `role` 在工具内部返回 `invalid_arguments`。

### SubAgentRunnerFactory
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
    ) -> tuple[AgentLoopRunner, AgentCommand, ChatSession]: ...
```
职责：构建子 `ChatSession`、子 `ContextManager`、子 `PermissionController`、子 `ToolExecutor` 和子 `AgentLoopRunner`。

### ToolPolicy 扩展
```python
@dataclass(frozen=True)
class ToolPolicy:
    mode: AgentMode
    whitelist: frozenset[str] | None = None
    filter: SubAgentToolFilter | None = None

    def allowed_specs(self, registry: ToolRegistry) -> tuple[ToolSpec, ...]: ...
    def validate_call(self, call: ToolCall, registry: ToolRegistry) -> ToolResult | None: ...
```
说明：保留现有 Skill 白名单行为；新增过滤器后按顺序应用 Plan Mode、Skill 白名单、继承集合、角色白名单、角色黑名单、全局禁止、后台白名单和防嵌套限制。

### AgentLoopRunner 扩展
```python
class AgentLoopRunner:
    def __init__(
        ...,
        sub_agent_manager: SubAgentManager | None = None,
        tool_filter: SubAgentToolFilter | None = None,
        sub_agent_prompt: ActiveSubAgentPrompt | None = None,
        file_read_cache: FileReadCache | None = None,
    ) -> None: ...
```
说明：主 Runner 传入 `sub_agent_manager` 以暴露角色摘要和绑定父上下文；子 Runner 传入 `tool_filter`、`sub_agent_prompt` 和独立 `file_read_cache`。

### CommandContext 扩展
```python
@dataclass(frozen=True)
class CommandSubAgentSnapshot:
    available_roles: tuple[str, ...]
    background_running: int
    background_completed: int
    warning_count: int

class CommandContext(Protocol):
    def sub_agent_snapshot(self) -> CommandSubAgentSnapshot: ...
    def background_current_sub_agent(self) -> bool: ...
```
说明：支持 `/status` 显示子 Agent 状态，并支持手动切后台动作。

## 模块设计

### `julycode.subagents.models`
**职责：** 定义角色、委派、后台任务、结果、提示上下文和配置报告等数据结构。  
**对外接口：** 上述 `SubAgentRoleDefinition`、`SubAgentInvocation`、`SubAgentResult`、`BackgroundSubAgentRecord` 等。  
**依赖：** `dataclasses`、`pathlib`、`typing`、`julycode.providers.base.TokenUsage`。

### `julycode.subagents.loader`
**职责：** 发现和解析角色 Markdown 文件。复用 Skill Loader 的 frontmatter 解析思路，但字段和来源优先级按子 Agent 需求实现。  
**对外接口：** `default_sub_agent_roots(cwd, plugin_roots=())`、`SubAgentRoleLoader.discover()`。  
**依赖：** `yaml`、`importlib.resources`、`julycode.subagents.models`。

### `julycode.subagents.manager`
**职责：** 子 Agent 总控。维护角色目录、当前父上下文、后台任务表、前台等待任务、通知回调和生命周期关闭。  
**对外接口：** `SubAgentManager.refresh_if_changed()`、`delegate()`、`background_current_foreground()`、`background_snapshot()`、`close()`。  
**依赖：** `AgentLoopRunner`、`SubAgentRunnerFactory`、`ToolRegistry`、`ToolExecutor`、`ChatSession`、`ProviderResolver`。

### `julycode.subagents.runtime`
**职责：** 创建并运行子 Agent。负责定义式空白会话、Fork 父历史快照、子上下文隔离、角色模型和权限模式解析、结果聚合和停止原因映射。  
**对外接口：** `SubAgentRunnerFactory.create_runner()`、`run_sub_agent_to_result()`。  
**依赖：** `ChatSession`、`AgentLoopRunner`、`ContextManager`、`PermissionController`、`PromptBuilder`、`ToolPolicy`、`HookManager`。

### `julycode.subagents.tools`
**职责：** 提供稳定的 `delegate_agent` 工具。  
**对外接口：** `DELEGATE_AGENT_TOOL_NAME`、`DelegateAgentTool`。  
**依赖：** `ToolSpec`、`ToolContext`、`SubAgentManager`。

### `julycode.subagents.cache`
**职责：** 提供独立文件读取缓存。  
**对外接口：** `FileReadCache`、`FileReadCacheEntry`。  
**依赖：** `pathlib`。

### `julycode.subagents.builtin`
**职责：** 提供内置定义式角色文件。初始内置 `code-searcher.md` 和 `reviewer.md`，默认只允许读类工具。  
**对外接口：** 包资源，由 `SubAgentRoleLoader` 读取。  
**依赖：** 无运行时逻辑。

### `julycode.tools.scheduler`
**职责：** 扩展工具过滤能力，并在被过滤工具被请求时返回结构化失败。  
**对外接口：** 扩展 `ToolPolicy` 构造参数和失败结果内容。  
**依赖：** `SubAgentToolFilter` 使用 `TYPE_CHECKING` 或轻量协议避免循环导入。

### `julycode.agent`
**职责：** 主/子 Agent Loop 的共同执行器。新增子 Agent 提示上下文、工具过滤、父上下文绑定和独立文件缓存传递。  
**对外接口：** `AgentLoopRunner` 构造参数扩展；`TurnEvent` 可选增加 `sub_agent_event` 事件。  
**依赖：** `SubAgentManager`、`SubAgentPromptContext`、`FileReadCache`。

### `julycode.prompting`
**职责：** 在运行时提示中注入可用子 Agent 角色摘要、后台任务摘要，以及子 Agent 自身的角色正文或 Fork 约束。  
**对外接口：** `RuntimePromptContext` 增加 `sub_agent_context` 字段。  
**依赖：** `julycode.subagents.models`。

### `julycode.config`
**职责：** 解析 `sub_agents` 配置，提供后台阈值、工具黑白名单、模型别名和插件角色根目录。  
**对外接口：** `SubAgentConfig` 挂到 `AppConfig.sub_agents`。  
**依赖：** `julycode.subagents.models` 或配置专属 dataclass。

### `julycode.tui.app`
**职责：** 创建 `SubAgentManager`，注册 `DelegateAgentTool`，刷新角色目录，显示后台通知，支持手动切后台，关闭时清理后台任务。  
**对外接口：** `sub_agent_snapshot()`、`background_current_sub_agent()`、`action_background_sub_agent()`。  
**依赖：** `SubAgentManager`、`DelegateAgentTool`。

### `julycode.commands`
**职责：** 在 `/status` 中显示子 Agent 摘要；新增 `/agents` 查看可用角色和后台任务详情；新增 `/background` 用于当前前台子 Agent 手动切后台。  
**对外接口：** `CommandSubAgentSnapshot`、`/agents` 命令、`/background` 命令。  
**依赖：** `CommandContext` 协议。

### `julycode.tools.base` 和 `julycode.tools.builtin`
**职责：** `ToolContext` 增加可选 `read_cache`；`ReadFileTool` 使用缓存但保持原返回字段兼容。  
**对外接口：** `ToolContext(read_cache: FileReadCache | None = None)`。  
**依赖：** `TYPE_CHECKING` 引用缓存类型。

### `julycode.skills`
**职责：** 独立 Skill 执行改为复用定义式子 Agent 的运行基础设施，继续保留原有用户可见语义。  
**对外接口：** `SkillManager` 不新增公开接口；`JulyCodeApp._run_isolated_skill()` 改为委派到子 Agent 隔离运行器。  
**依赖：** `SubAgentRunnerFactory` 或一层内部适配器。

## 模块交互
1. 应用启动时，TUI 创建默认工具注册表、SkillManager、SubAgentManager，并注册 `load_skill` 与 `delegate_agent`。`delegate_agent` 注册后保持常驻，主 Agent 工具列表稳定。
2. 每次用户输入前，TUI 调用 `skill_manager.refresh_if_changed()` 和 `sub_agent_manager.refresh_if_changed()`。角色定义错误作为中文警告或配置错误展示。
3. 主 Agent 构建请求时，`AgentLoopRunner` 从 `sub_agent_manager.prompt_context()` 获取角色摘要和后台任务摘要，交给 `PromptBuilder` 注入运行时提示。
4. 主 Agent 进入工具阶段前，`AgentLoopRunner` 计算当前 `allowed_tools`，用 `ParentAgentContext` 绑定到 `sub_agent_manager`。工具阶段结束后清除绑定。
5. 模型调用 `delegate_agent`。`DelegateAgentTool` 解析为 `SubAgentInvocation`，调用 `sub_agent_manager.delegate()`。
6. 定义式前台委派：Manager 创建后台任务记录但以“前台等待”状态运行子任务；若任务在阈值内完成，返回 `SubAgentResult`；若超时或用户切后台，返回“已进入后台”结果，子任务继续运行。
7. 显式后台或 Fork 委派：Manager 立即创建 `asyncio.Task`，状态设为 `background`，工具结果返回任务标识和启动信息。
8. 子 Agent Runner 每轮使用自己的 `ChatSession`、`ContextManager`、`PermissionController`、`FileReadCache` 和 Hook 运行时状态。工具集合由 `SubAgentToolFilter` 收窄，`delegate_agent` 在子 Agent 中不可见也不可执行。子 Agent 权限确认不进入交互式等待；会触发确认的决策会转为结构化拒绝结果。
9. 子 Agent 停止后，`run_sub_agent_to_result()` 聚合最终消息、停止原因、错误、关键输出和用量，写回 `BackgroundSubAgentRecord`。
10. 后台任务完成回调生成中文通知文本，追加到主 `ChatSession`，并通过 TUI notify 回调显示。通知只包含摘要和关键结果，不包含完整子 Agent 中间历史。
11. 用户取消主任务时，TUI 取消主 Runner。Manager 取消仍处于前台等待的子 Agent；已后台化任务继续运行并最终通知主对话。

## 文件组织
```text
src/julycode/
├── agent.py                         — 扩展 Runner 构造参数、父上下文绑定和子 Agent 提示上下文
├── cli.py                           — 创建并注入 SubAgentManager
├── config.py                        — 解析 sub_agents 配置
├── prompting/
│   ├── base.py                      — RuntimePromptContext 增加 sub_agent_context
│   └── builder.py                   — 渲染子 Agent 角色摘要、后台摘要和子 Agent 运行提示
├── tools/
│   ├── base.py                      — ToolContext 增加 read_cache
│   ├── builtin.py                   — ReadFileTool 接入可选缓存
│   └── scheduler.py                 — ToolPolicy 支持 SubAgentToolFilter
├── subagents/
│   ├── __init__.py                  — 导出子 Agent 公共类型
│   ├── cache.py                     — 文件读取缓存
│   ├── loader.py                    — 角色发现、frontmatter 解析和优先级覆盖
│   ├── manager.py                   — 委派、后台任务、通知和生命周期管理
│   ├── models.py                    — dataclass 和 Literal 定义
│   ├── runtime.py                   — 子 Agent Runner 创建与结果聚合
│   ├── tools.py                     — delegate_agent 工具
│   └── builtin/
│       ├── __init__.py              — 内置角色包
│       ├── code-searcher.md         — 内置代码搜索角色
│       └── reviewer.md              — 内置审查角色
├── skills/
│   └── execution.py                 — 独立执行适配到子 Agent 隔离运行器
├── commands/
│   ├── builtin.py                   — /status 显示子 Agent，新增 /background
│   └── models.py                    — CommandSubAgentSnapshot 与协议扩展
└── tui/
    └── app.py                       — 创建 SubAgentManager、注册工具、显示通知、手动切后台

tests/
├── test_subagents_loader.py         — 角色加载、覆盖、解析错误
├── test_subagents_manager.py        — 前台、后台、Fork、自动通知、取消策略
├── test_subagents_tools.py          — delegate_agent 参数校验和工具结果
├── test_subagents_policy.py         — 工具多层过滤和防嵌套
├── test_agent.py                    — Runner 父上下文绑定、提示和事件回归
├── test_tui_smoke.py                — /background、后台完成通知和 /status 展示
└── e2e_mock_openai_server.py        — 增加委派工具调用脚本化响应

pyproject.toml                       — 包含 julycode.subagents.builtin 的 *.md 包数据
README.md                            — 记录角色定义格式和基础配置
```

## 技术决策
| 决策点 | 选择 | 理由 |
|--------|------|------|
| 委派入口 | 单一 `delegate_agent` 工具，参数 `type=defined/fork` 分流 | 满足工具列表稳定，避免每个角色或类型变成一个工具 |
| 角色格式 | Markdown + YAML frontmatter，正文作为持续提示 | 与 Skill 格式一致，便于人工维护，也满足角色生命周期提示要求 |
| 角色加载优先级 | 项目 > 用户 > 内置 > 插件，first-wins | 直接对应 spec；实现上沿用 Skill Loader 的覆盖思路 |
| Fork 历史继承 | 复制父会话当前消息快照，不引用同一个列表 | 保证 Fork 能看到父历史，同时不会把子消息写回主历史 |
| Fork prompt cache | 保留相同稳定提示块和历史前缀，记录供应商 cache 用量 | JulyCode 不能强制供应商命中缓存，只能最大化可缓存前缀并观测结果 |
| 状态隔离 | 每个子 Agent 新建 session、context manager、permission controller、read cache、hook runtime state | 隔离消息、权限、上下文外置路径、Token anchor、缓存和 Hook prompt injection 状态 |
| 基础设施共享 | 共享 provider/provider_resolver、Hook 配置与动作执行能力、registry、cwd | 避免重复连接和重复发现工具，同时不共享会污染上下文的运行时状态 |
| 前台转后台 | 子任务始终以 asyncio task 运行；前台只是等待它完成 | 同一机制覆盖显式后台、超时后台和手动切后台 |
| 后台通知 | 完成后追加一条 assistant 消息到主 ChatSession，并通过 TUI 回调显示 | 用户选择了自动可见通知，且主 Agent 后续请求能看到结果 |
| 后台工具策略 | 默认只允许配置白名单中的工具，初始为读类核心工具 | 非交互后台默认保守，防止权限确认和高风险工具卡死或误执行 |
| 防嵌套 | 子 Agent 过滤并拒绝 `delegate_agent` | 简单可靠地防止无限嵌套，符合本阶段不做嵌套编排 |
| 独立 Skill 迁移 | 复用子 Agent 隔离运行基础设施，保持 Skill 用户语义 | 避免两套隔离 Agent 逻辑，降低维护成本 |

## 需求覆盖
| 需求 | 架构归属 |
|------|----------|
| F1-F2 | `DelegateAgentTool` + `SubAgentInvocation` |
| F3-F7 | `SubAgentRoleLoader`、`SubAgentRoleDefinition`、角色校验 |
| F8-F10 | `SubAgentRunnerFactory` 的 defined/fork 会话构建 |
| F11-F14 | 子 `ChatSession`、`ContextManager`、`PermissionController`、`FileReadCache`、Hook 运行时状态与共享 provider/Hook 配置/registry |
| F15-F17 | `run_sub_agent_to_result()` 和 `SubAgentResult` |
| F18-F21 | `SubAgentManager` 后台记录、TUI notify、前台等待策略 |
| F22-F27 | `SubAgentToolFilter` + 扩展 `ToolPolicy` |
| F28-F29 | `SubAgentManager` 状态事件、TUI cancel/background 行为 |
