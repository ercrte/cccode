# MewCode 命令系统 Plan

## 架构概览
命令系统拆成四层：命令模型、注册中心、命令分发器、界面适配层。命令模型描述元数据、解析结果和处理协议；注册中心负责登记内置命令、大小写不敏感查找、别名冲突检测和补全候选；命令分发器负责把用户输入分成空输入、斜杠命令、未知命令和普通对话；界面适配层由 TUI 实现，向命令处理函数提供展示消息、发送用户消息、切换模式、读取状态和刷新状态栏等能力。

内置命令以静态定义注册，不引入动态加载。`/help`、`/session`、`/memory`、`/permission`、`/status` 属于纯本地命令，只读取状态并显示中文结果；`/clear`、`/plan`、`/do` 属于影响界面状态命令；`/compact` 触发已有手动压缩流程，行为上仍绕过 Agent；`/review` 属于预设提示词命令，会构造确定的审查请求并交给现有 Agent Loop。

持久对话模式存在 TUI 应用状态中，默认是 `normal`，界面显示 `[DEFAULT]`；计划模式是 `plan`，界面显示 `[PLAN]`。普通非命令输入会按当前持久模式生成 `AgentCommand` 并进入 Agent Loop。`/plan` 只切换到 `plan`，`/do` 只切回 `normal`；旧的 `do` Agent 模式和待执行计划不再由命令入口触发。

Tab 补全由 TUI 层消费注册中心候选。用户输入斜杠前缀时，单匹配直接替换为规范命令名，多匹配显示轻量候选菜单；隐藏命令不会出现在候选中。补全只处理命令名部分，不解析参数。

## 核心数据结构

### CommandKind
```python
CommandKind = Literal["local", "ui", "prompt"]
```
表示命令执行模式。`local` 只读取状态和显示结果；`ui` 可以改变界面或运行状态；`prompt` 会构造请求并送入 Agent Loop。

### AgentMode
```python
AgentMode = Literal["normal", "plan"]
```
表示 Agent Loop 的工具策略和提示词模式。`normal` 对应状态栏 `[DEFAULT]`，允许完整工具能力；`plan` 对应状态栏 `[PLAN]`，沿用现有只读工具策略。旧的 `"do"` 不再作为命令入口可达模式。

### CommandDefinition
```python
@dataclass(frozen=True)
class CommandDefinition:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str
    kind: CommandKind
    argument_hint: str = ""
    hidden: bool = False
    handler: CommandHandler
```
描述一条可注册命令。`name` 和 `aliases` 内部不带前导 `/`，展示时统一补 `/`。`handler` 是异步处理函数。

### CommandInvocation
```python
@dataclass(frozen=True)
class CommandInvocation:
    definition: CommandDefinition
    raw_text: str
    command_text: str
    argument: str
    matched_name: str
```
表示一次命令调用。`raw_text` 是用户提交的完整文本；`command_text` 是第一个空格前的命令入口；`argument` 是首尾去空白后的参数；`matched_name` 是用户实际命中的主名称或别名。

### ParsedInput
```python
@dataclass(frozen=True)
class EmptyInput:
    pass

@dataclass(frozen=True)
class PlainInput:
    text: str

@dataclass(frozen=True)
class UnknownCommandInput:
    raw_text: str
    command_text: str

ParsedInput = EmptyInput | PlainInput | CommandInvocation | UnknownCommandInput
```
表示输入解析结果。空输入早返回；普通输入交给当前持久模式；未知斜杠命令显示 `/help` 引导。

### CommandCompletion
```python
@dataclass(frozen=True)
class CommandCompletion:
    replacement: str | None
    options: tuple[CommandDefinition, ...]
```
表示补全结果。`replacement` 非空时直接补全；`options` 多于一个时由 TUI 显示菜单。

### CommandHandler
```python
CommandHandler = Callable[[CommandInvocation, CommandContext], Awaitable[None]]
```
内置命令处理函数类型。处理函数只依赖 `CommandContext` 协议，不直接导入 Textual 组件。

### CommandContext
```python
class CommandContext(Protocol):
    @property
    def mode(self) -> AgentMode: ...

    def set_mode(self, mode: AgentMode) -> None: ...
    def status_snapshot(self) -> CommandStatusSnapshot: ...
    def session_snapshot(self) -> CommandSessionSnapshot: ...
    def memory_snapshot(self) -> CommandMemorySnapshot: ...
    def permission_snapshot(self) -> CommandPermissionSnapshot: ...
    def refresh_status(self) -> None: ...

    async def show_assistant(self, content: str) -> None: ...
    async def show_error(self, content: str) -> None: ...
    async def clear_messages(self) -> None: ...
    async def compact_context(self) -> str: ...
    async def send_prompt(self, *, visible_text: str, model_text: str, mode: AgentMode) -> None: ...
```
命令使用的界面控制协议。`send_prompt` 由 TUI 适配到现有 Agent Loop；本地命令和界面命令不得自行追加普通用户消息。

### CommandStatusSnapshot
```python
@dataclass(frozen=True)
class CommandStatusSnapshot:
    protocol: str
    model: str
    mode: AgentMode
    agent_running: bool
    last_usage: TokenUsage | None
    mcp_report: McpLoadReport | None
```
提供 `/status` 的只读数据。`last_usage` 来自最近一次 usage 事件；没有用量时展示未知。

### CommandSessionSnapshot
```python
@dataclass(frozen=True)
class CommandSessionSnapshot:
    session_id: str
    restored: bool
    source_path: str
    message_count: int
    mode: AgentMode
```
提供 `/session` 的只读数据。恢复状态从启动时的恢复报告读取；无恢复报告时按空会话展示。

### CommandMemorySnapshot
```python
@dataclass(frozen=True)
class CommandMemorySnapshot:
    enabled: bool
    user_index_available: bool
    project_index_available: bool
    auto_notes_enabled: bool
    warning_count: int
```
提供 `/memory` 的只读数据。可用状态来自运行时知识上下文中的记忆索引。

### CommandPermissionSnapshot
```python
@dataclass(frozen=True)
class CommandPermissionSnapshot:
    mode: str
    session_rule_count: int
    local_rule_count: int
    project_rule_count: int
    user_rule_count: int
```
提供 `/permission` 的只读数据。只展示规则数量和模式，不展示完整规则内容，避免泄露敏感路径或命令片段。

## 模块设计

### 命令模型模块
**职责：** 定义 `AgentMode`、命令元数据、输入解析结果、补全结果、状态快照和 `CommandContext` 协议。  
**对外接口：** `CommandDefinition`、`CommandInvocation`、`ParsedInput`、`CommandCompletion`、`CommandContext`、快照 dataclass。  
**依赖：** 只依赖标准库类型、`TokenUsage` 和 `McpLoadReport` 类型；不依赖 TUI。

### 命令注册中心
**职责：** 注册命令、检测名称和别名冲突、按大小写不敏感方式查找命令、列出可见命令、生成补全候选。  
**对外接口：**
```python
class CommandRegistry:
    def register(self, definition: CommandDefinition) -> None: ...
    def get(self, name: str) -> CommandDefinition | None: ...
    def parse(self, raw_text: str) -> ParsedInput: ...
    def visible_commands(self) -> tuple[CommandDefinition, ...]: ...
    def completion(self, raw_text: str) -> CommandCompletion: ...
```
**依赖：** 命令模型模块。冲突时抛出 `CommandRegistryError`，由 CLI 启动阶段捕获并退出。

### 内置命令模块
**职责：** 声明十个内置命令及处理函数，集中维护名称、别名、帮助文案、用法和类型。  
**对外接口：**
```python
def create_builtin_command_registry() -> CommandRegistry: ...
```
**依赖：** 命令模型、命令注册中心。处理函数通过 `CommandContext` 读取状态或执行界面动作。

内置命令定义：

| 命令 | 别名 | 类型 | 行为 |
|------|------|------|------|
| `/help` | `/h`, `/?` | local | 展示全部可见命令，或展示指定命令详情 |
| `/compact` | `/comp` | local | 调用手动上下文压缩并展示结果 |
| `/clear` | `/cls` | ui | 清空界面消息区，提示会话上下文仍保留 |
| `/plan` | `/p` | ui | 设置持久模式为 `plan` 并刷新状态 |
| `/do` | `/d` | ui | 设置持久模式为 `normal` 并刷新状态 |
| `/session` | `/sess` | local | 展示会话标识、恢复状态、消息数和模式 |
| `/memory` | `/mem` | local | 展示记忆启用、索引可用和自动笔记状态 |
| `/permission` | `/perm` | local | 展示权限模式和各层规则数量 |
| `/status` | `/st` | local | 展示模型、模式、任务、Token、MCP 告警 |
| `/review` | `/rev` | prompt | 构造代码审查请求并送入当前模式的 Agent Loop |

### 命令分发器
**职责：** 连接注册中心和 `CommandContext`，统一处理空输入、未知命令、本地命令、界面命令和预设提示词命令。  
**对外接口：**
```python
class CommandDispatcher:
    def __init__(self, registry: CommandRegistry) -> None: ...
    async def dispatch(self, raw_text: str, context: CommandContext) -> bool: ...
```
返回值表示输入是否已被命令系统消费。`False` 只会出现在普通非斜杠输入，调用方随后启动 Agent Loop。

**依赖：** 命令注册中心、命令模型。分发器不依赖 Textual，也不直接创建 Agent Runner。

### Agent Loop 适配
**职责：** 让普通输入和预设提示词命令都能用当前持久模式进入现有 Agent Loop。  
**对外接口：** 继续使用 `AgentCommand(mode, visible_text, model_text)`，但 `mode` 只使用 `"normal"` 或 `"plan"`。  
**依赖：** 现有 `AgentLoopRunner`、`ToolPolicy`、`PromptBuilder`。`ToolPolicy("plan")` 保持只允许读类工具；`ToolPolicy("normal")` 保持允许全部工具。

需要调整的行为：
- 删除命令入口对旧 `CompactCommand`、旧 `/plan <需求>`、旧 `/do` 执行待计划的依赖。
- `AgentLoopRunner` 不再因 `plan` 成功完成而保存待执行计划，也不再因 `do` 完成而清除待执行计划。
- `PromptBuilder` 的稳定提示和运行时提示改为描述默认模式与计划模式，不再描述旧执行模式和待执行计划。

### TUI 命令上下文适配器
**职责：** 在 `MewCodeApp` 内实现 `CommandContext`，把命令行为映射到现有 Textual 界面、上下文管理、权限控制、记忆管理和 Agent Loop。  
**对外接口：** `MewCodeApp` 提供内部方法实现 `CommandContext`，或使用轻量 adapter 包装 app。  
**依赖：** `MewCodeApp`、`StatusBar`、`MessageList`、`MessageView`、`ContextManager`、`PermissionController`、`SessionMemoryManager`、`McpManager`。

关键实现点：
- `MewCodeApp.__init__` 创建并持有 `command_registry`、`command_dispatcher`、`current_mode` 和 `last_usage`。
- `on_input_submitted` 先做空输入早返回，再清空输入框并启动统一输入任务。
- 统一输入任务先调用 `CommandDispatcher.dispatch`；返回 `False` 时用 `current_mode` 构造普通 `AgentCommand`。
- 本地命令运行期间禁用输入框，执行结束后恢复输入框。
- `send_prompt` 复用现有 `_run_generation` 的 Agent Loop 逻辑，但允许传入命令可见文本和模型文本。
- `_apply_turn_event` 收到 usage 事件时更新 `last_usage`，供 `/status` 查询。

### 状态栏
**职责：** 展示当前持久模式、模型信息、运行状态、进度、Token 和权限状态。  
**对外接口：**
```python
class StatusBar(Static):
    def set_mode(self, mode: AgentMode) -> None: ...
```
**依赖：** TUI widgets。`normal` 显示 `[DEFAULT]`，`plan` 显示 `[PLAN]`。模式切换命令和 app 初始化都调用 `set_mode`。

### 补全菜单
**职责：** 在用户按 Tab 且输入是斜杠命令前缀时展示补全结果。  
**对外接口：**
```python
class CommandCompletionMenu(Vertical):
    def set_options(self, options: Sequence[CommandDefinition]) -> None: ...
    def clear_options(self) -> None: ...
```
**依赖：** TUI widgets 和命令模型。`MewCodeApp.action_complete_command` 读取 `Composer.value`，调用 `CommandRegistry.completion`，单匹配时更新输入框，多匹配时更新菜单；输入提交或普通字符输入后隐藏菜单。

## 模块交互
1. CLI 启动时加载配置、Provider、工具注册表、MCP、上下文和记忆管理器。
2. CLI 调用 `create_builtin_command_registry()` 创建命令注册中心；注册阶段如有冲突，抛出 `CommandRegistryError`，CLI 打印中文错误并返回 1。
3. CLI 创建 `MewCodeApp` 时注入命令注册中心；`MewCodeApp` 创建 `CommandDispatcher`，初始化 `current_mode="normal"`，状态栏显示 `[DEFAULT]`。
4. 用户回车提交输入后，TUI 清空输入框并禁用输入。
5. 分发器解析输入：
   - 空输入：直接结束，不显示消息。
   - 普通输入：返回未消费，TUI 用当前持久模式启动 Agent Loop。
   - 未知斜杠命令：显示包含 `/help` 的提示，结束。
   - 已知斜杠命令：调用对应 handler。
6. 本地命令 handler 读取 `CommandContext` 的快照并调用 `show_assistant` 展示结果。
7. 界面状态命令 handler 调用 `set_mode`、`clear_messages`、`refresh_status` 等接口，必要时展示结果。
8. `/compact` handler 调用 `compact_context`，由 TUI 适配到 `ContextManager.manual_compact`，然后展示压缩结果。
9. `/review` handler 构造确定的审查提示，调用 `send_prompt(visible_text=原命令, model_text=审查提示, mode=当前模式)`，TUI 复用 Agent Loop 事件处理。
10. Agent Loop 运行期间，`ToolPolicy` 根据 `AgentCommand.mode` 控制工具范围；计划模式只允许读类工具，默认模式允许完整工具集。
11. 用户按 Tab 时，TUI 调用注册中心补全；单匹配直接补全，多匹配显示菜单，隐藏命令被过滤。

## 文件组织
```text
mewcode/
├── src/mewcode/commands/                 — 命令系统包，替换现有单文件命令解析
│   ├── __init__.py                       — 对外重导出兼容入口
│   ├── models.py                         — 命令元数据、解析结果、上下文协议、状态快照
│   ├── registry.py                       — 注册中心、冲突检测、解析、补全
│   ├── builtin.py                        — 十个内置命令定义和 handler
│   └── dispatcher.py                     — 输入分发器
├── src/mewcode/agent.py                  — 调整 AgentMode 使用范围，移除旧 do/待计划命令副作用
├── src/mewcode/prompting/modules.py      — 更新模式说明，去掉旧执行模式描述
├── src/mewcode/prompting/builder.py      — 运行时模式只处理 normal/plan
├── src/mewcode/session.py                — 保留会话消息和上下文状态；待执行计划不再被命令系统使用
├── src/mewcode/tools/scheduler.py        — ToolPolicy 只区分 normal/plan
├── src/mewcode/tui/app.py                — 接入命令注册中心、分发器、持久模式、状态快照、命令上下文
├── src/mewcode/tui/widgets.py            — 状态栏模式标记、命令补全菜单
├── src/mewcode/cli.py                    — 启动阶段创建命令注册中心，冲突时报错退出
├── tests/test_commands.py                — 命令注册、解析、帮助、分发、内置命令单元测试
├── tests/test_agent.py                   — 更新 Agent 模式行为测试，删除旧 do 语义断言
├── tests/test_prompting.py               — 更新运行时提示模式文本测试
├── tests/test_tool_scheduler.py          — 更新 ToolPolicy 模式测试
└── tests/test_tui_smoke.py               — 覆盖 TUI 模式标记、补全和本地命令 smoke
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 命令系统组织 | 将 `mewcode.commands` 从单文件升级为包，并通过 `__init__.py` 重导出常用类型 | 注册、内置命令、分发和模型职责已经超过一个函数；包结构便于测试和后续扩展，同时保留导入入口 |
| 命令名存储 | 内部不带 `/`，展示和输入时带 `/` | 降低别名冲突检测和补全实现复杂度，帮助文本仍符合用户习惯 |
| 匹配规则 | 对命令名和别名做 `casefold()`，参数只做首尾空白裁剪 | 满足大小写不敏感，同时不破坏用户传给 `/review` 等命令的参数语义 |
| 启动冲突处理 | 注册时抛 `CommandRegistryError`，CLI 捕获后返回 1 | 等价于启动阶段失败，避免运行时才发现别名冲突 |
| 命令处理接口 | handler 依赖 `CommandContext` 协议 | 命令实现不绑定 Textual，单元测试可以用 fake context 验证 |
| 持久模式 | TUI 保存 `current_mode`，普通输入按该模式进入 Agent Loop | `/plan` 和 `/do` 是界面状态命令，模式应独立于单次命令解析 |
| 默认模式内部名 | 继续使用 `normal`，状态栏显示 `[DEFAULT]` | 复用现有 ToolPolicy 和 PromptBuilder 语义，减少无必要迁移；用户可见文本符合 spec |
| 旧 `do` 模式 | 从命令入口移除，不再由 `/do` 触发 Agent Loop | 满足已确认的 A 方案：`/do` 只退回默认模式 |
| `/compact` 实现 | 作为命令 handler 调用现有 `ContextManager.manual_compact` | 保留已有压缩能力和测试基础，只改变入口归属 |
| `/clear` 范围 | 只清空界面消息列表，并显示上下文仍保留 | 满足不删除会话、记忆和持久记录的边界，避免危险数据操作 |
| `/permission` 展示 | 展示权限模式和规则数量，不展示规则全文 | 能满足状态查询需求，同时降低敏感命令片段泄露风险 |
| `/review` 提示 | 构造固定中文审查请求，参数作为范围或补充要求拼入模型文本 | 行为确定且省 Token，不引入动态提示词生成 |
| Tab 补全 | TUI 绑定 Tab，注册中心只返回候选和替换文本 | 保持核心逻辑可测，菜单表现留在渲染层 |
| 隐藏命令 | 注册中心查找可调用，但 `visible_commands` 和 `completion` 默认排除 | 为后续内部命令留扩展点，同时符合隐藏命令不参与补全 |
