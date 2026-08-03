# JulyCode Hook System Plan

## 架构概览
本阶段新增 `julycode.hooks` 包，集中负责 Hook 配置模型、解析校验、条件匹配、动作执行、运行期状态和生命周期事件分发。Agent Loop、工具调度器和 TUI 只依赖 `HookManager` 的小接口，不直接理解 YAML 结构或动作细节。

配置层在现有主配置中新增 `hooks` 字段，沿用当前用户级配置和项目级配置的浅合并规则：项目级 `.julycode.yaml` 中的 `hooks` 整体覆盖用户级 `~/.julycode/config.yaml` 中的 `hooks`。未声明时生成空 Hook 配置。配置解析阶段完成集中校验，启动前暴露清晰 `ConfigError`。

匹配层新增通用匹配模块，权限规则和 Hook 条件都调用同一套解析与匹配函数。现有权限规则的精确和 glob 行为保持兼容，同时补齐反向匹配和正则匹配能力；Hook 条件使用字段路径读取事件上下文，字段不存在时按未匹配处理。

运行层由 `HookManager` 持有所有规则、只跑一次状态、提示词注入队列和后台任务集合。同步 Hook 在事件触发点内完成，后台 Hook 用 `asyncio.create_task()` 启动并由完成回调记录结果。所有动作异常都会转成 Hook 状态，不向上抛出影响 Agent 主流程。

Agent 接入点集中在 `AgentLoopRunner` 和 `ToolCallScheduler`。`AgentLoopRunner` 触发轮次、消息、上下文压缩、停止和错误事件，并在构造模型请求时消费 Hook 提示词注入。`ToolCallScheduler` 触发工具前后事件；工具执行前 Hook 先于策略、权限和真实工具执行，若返回拦截结果，则直接生成失败 `ToolResult` 回灌模型。

TUI 接入 `HookManager` 负责会话开始和结束事件，并消费新增 Hook 状态事件。Hook 状态主要用于观察和测试，默认不把 shell 或 HTTP 输出注入模型上下文。

## 核心数据结构

### MatchExpression
```python
MatchKind = Literal["exact", "glob", "regex"]

@dataclass(frozen=True)
class MatchExpression:
    raw: str
    pattern: str
    kind: MatchKind
    negated: bool = False
```

表示一段可复用匹配表达式。解析规则：

- `regex:<pattern>` 表示正则匹配。
- `glob:<pattern>` 表示显式 glob 匹配。
- 未带前缀但包含 glob 元字符时按 glob 匹配。
- 其他字符串按精确匹配。
- 表达式前缀 `!` 表示反向匹配，例如 `!run_command`、`!regex:^rm\b`。

### HookConfig
```python
@dataclass(frozen=True)
class HookConfig:
    rules: Sequence[HookRule] = ()
```

应用级 Hook 配置，挂在 `AppConfig.hooks` 下。

### HookRule
```python
HookEventName = Literal[
    "session.start",
    "session.end",
    "turn.start",
    "turn.end",
    "message.user",
    "message.assistant",
    "tool.before",
    "tool.after",
    "system.context_compacted",
    "system.stopped",
    "system.error",
]

@dataclass(frozen=True)
class HookRule:
    id: str
    index: int
    event: HookEventName
    condition: HookConditionGroup | None
    action: HookAction
    once: bool = False
    background: bool = False
```

一条已校验的 Hook 规则。`id` 来自配置里的 `name`，缺省时使用稳定的 `hook-<index>`。`index` 保留声明顺序，本阶段不支持显式优先级。

### HookConditionGroup
```python
ConditionLogic = Literal["all", "any"]

@dataclass(frozen=True)
class HookCondition:
    field: str
    match: MatchExpression

@dataclass(frozen=True)
class HookConditionGroup:
    logic: ConditionLogic
    conditions: Sequence[HookCondition]
```

条件组合。YAML 中 `if.all` 和 `if.any` 二选一；两者同时出现或都不是非空列表时配置报错。字段路径使用点号读取事件上下文，例如 `tool.name`、`tool.arguments.command`、`result.error_type`。

### HookAction
```python
HookActionType = Literal["command", "prompt", "http", "sub_agent"]
PromptScope = Literal["next_request"]

@dataclass(frozen=True)
class HookAction:
    type: HookActionType
    command: HookCommandAction | None = None
    prompt: HookPromptAction | None = None
    http: HookHttpAction | None = None
    sub_agent: HookSubAgentAction | None = None
    tool_block: HookToolBlock | None = None
```

动作配置。`tool_block` 是 `tool.before` 专用的通用动作结果策略，不新增第五种动作类型；当规则命中且声明 `tool_block` 时，HookManager 在动作执行后生成工具拒绝结果。若动作自身失败，默认只记录 Hook 失败，不拦截工具，避免 Hook 故障扩大影响。

### HookCommandAction
```python
@dataclass(frozen=True)
class HookCommandAction:
    command: str
    timeout_seconds: float = 10.0
```

shell 命令动作。运行在当前项目工作目录，使用现有 `run_command` 工具语义和权限检查路径执行。

### HookPromptAction
```python
@dataclass(frozen=True)
class HookPromptAction:
    text: str
    scope: PromptScope = "next_request"
```

提示词注入动作。注入内容进入下一次模型请求的运行时补充块，消费后移除，不写入会话历史。

### HookHttpAction
```python
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

@dataclass(frozen=True)
class HookHttpAction:
    method: HttpMethod
    url: str
    headers: Mapping[str, str]
    body: str | None = None
    json_body: object | None = None
    timeout_seconds: float = 10.0
```

HTTP 请求动作。使用 `httpx.AsyncClient(trust_env=False)`，请求头值不写入日志；响应体只记录脱敏后的短摘要。

### HookSubAgentAction
```python
@dataclass(frozen=True)
class HookSubAgentAction:
    name: str
    prompt: str = ""
```

子 Agent 动作占位。本阶段只校验和记录“已跳过占位动作”，不启动真实子 Agent。

### HookToolBlock
```python
@dataclass(frozen=True)
class HookToolBlock:
    reason: str
    error_type: str = "hook_blocked"
```

工具执行前拦截结果配置。只允许出现在 `tool.before` 事件，且所在规则不得配置 `background: true`。

### HookEvent
```python
@dataclass(frozen=True)
class HookEvent:
    name: HookEventName
    data: Mapping[str, object]
```

生命周期事件载体。`data` 使用稳定字段约定：

- `session.start` / `session.end`: `session.id`、`cwd`
- `turn.start` / `turn.end`: `turn.mode`、`turn.visible_text`、`turn.model_text`、`turn.stop_reason`
- `message.user`: `message.content`、`turn.mode`
- `message.assistant`: `message.content`、`message.has_tool_calls`、`turn.mode`
- `tool.before`: `tool.id`、`tool.name`、`tool.arguments`、`turn.mode`
- `tool.after`: `tool.id`、`tool.name`、`tool.arguments`、`result.success`、`result.error_type`
- `system.context_compacted`: `context.mode`、`context.light_compacted`、`context.heavy_compacted`
- `system.stopped`: `stop.reason`、`stop.text`
- `system.error`: `error.type`、`error.message`

### HookExecutionResult
```python
HookExecutionStatus = Literal["matched", "skipped_once", "success", "failed", "blocked", "placeholder"]

@dataclass(frozen=True)
class HookExecutionResult:
    rule_id: str
    event: HookEventName
    status: HookExecutionStatus
    message: str = ""
    elapsed_ms: int = 0
    tool_result: ToolResult | None = None
```

Hook 执行的可观测结果。`tool_result` 只在 `tool.before` 拦截时存在。

### HookRuntimeState
```python
@dataclass
class HookRuntimeState:
    executed_once: set[str] = field(default_factory=set)
    prompt_injections: list[HookPromptInjection] = field(default_factory=list)
    background_tasks: set[asyncio.Task[None]] = field(default_factory=set)
```

运行期状态。`executed_once` 不持久化，重启后清空。

### HookRuntimeContext
```python
@dataclass(frozen=True)
class HookRuntimeContext:
    cwd: Path
    mode: AgentMode
    allowed_tool_names: frozenset[str] | None
    registry: ToolRegistry
    executor: ToolExecutor
    permission_controller: PermissionController | None
```

动作执行需要的运行时依赖。shell 动作映射成 `run_command` 调用，先经过 Plan Mode、Skill 白名单和权限判断；需要交互确认时视为 Hook 动作不可执行并记录失败，不弹出权限确认。

### HookManager
```python
class HookManager:
    async def emit(self, event: HookEvent, context: HookRuntimeContext) -> Sequence[HookExecutionResult]:
        raise NotImplementedError

    async def before_tool(self, call: ToolCall, context: HookRuntimeContext) -> HookToolDecision:
        raise NotImplementedError

    async def after_tool(
        self,
        call: ToolCall,
        result: ToolResult,
        context: HookRuntimeContext,
    ) -> Sequence[HookExecutionResult]:
        raise NotImplementedError

    def pending_prompt_injections(self) -> Sequence[HookPromptInjection]:
        raise NotImplementedError

    def consume_prompt_injections(self) -> Sequence[HookPromptInjection]:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError
```

Hook 系统主接口。

### HookToolDecision
```python
@dataclass(frozen=True)
class HookToolDecision:
    blocked: bool
    results: Sequence[HookExecutionResult]
    tool_result: ToolResult | None = None
```

工具执行前 Hook 的综合结果。`blocked=True` 时 `ToolCallScheduler` 不再进入策略、权限和工具执行。

### TurnEvent 扩展
```python
TurnEventType += Literal["hook_finished"]

@dataclass(frozen=True)
class TurnEvent:
    hook_result: HookExecutionResult | None = None
```

用于测试和 TUI 观察 Hook 状态。TUI 默认只在失败、拦截或占位动作时展示简短状态，其余成功状态可忽略。

### RuntimePromptContext 扩展
```python
@dataclass(frozen=True)
class RuntimePromptContext:
    hook_injections: Sequence[HookPromptInjection] = ()
```

提示构造层读取该字段，生成独立 `<julycode_hook_instructions>` 运行时补充块。

## 模块设计

### `julycode.matching`
**职责：** 提供权限规则和 Hook 条件共用的匹配表达式解析与执行。  
**对外接口：**
```python
def parse_match_expression(raw: str) -> MatchExpression
def match_expression(expression: MatchExpression, value: object) -> bool
def get_field_value(data: Mapping[str, object], field_path: str) -> object | None
```
**依赖：** `fnmatch`、`re`、`ConfigError`。

### `julycode.permissions.rules`
**职责：** 迁移到通用匹配模块，保持现有规则加载、优先级和冲突决策。  
**对外接口：** 保持 `PermissionRuleParser`、`PermissionRuleSet`、`PermissionRuleStore` 不变。  
**依赖：** `julycode.matching`。

### `julycode.hooks.models`
**职责：** 定义 Hook 配置、事件、动作、执行结果和运行期状态数据结构。  
**对外接口：** 暴露上述 dataclass 和 Literal 类型。  
**依赖：** `dataclasses`、`pathlib`、现有 `ToolCall`、`ToolResult`。

### `julycode.hooks.config`
**职责：** 从主配置的 `hooks` YAML 节点解析并集中校验 Hook 规则。  
**对外接口：**
```python
def parse_hook_config(raw: object) -> HookConfig
```
**依赖：** `julycode.matching`、`julycode.hooks.models`、`ConfigError`。  
**校验规则：**
- `hooks` 缺失或为 `null` 时返回空配置。
- `hooks` 必须是列表。
- 每条规则必须有 `event` 和 `action`。
- `if` 只能包含 `all` 或 `any` 之一。
- `action.type` 必须是 `command`、`prompt`、`http`、`sub_agent`。
- action 对应必填字段必须存在。
- `tool_block` 只允许在 `tool.before` 中使用。
- `tool.before` 不允许 `background: true`。
- `once`、`background` 必须是布尔值。
- timeout 必须大于 0。

### `julycode.hooks.conditions`
**职责：** 对 HookEvent 执行条件判断。  
**对外接口：**
```python
def rule_matches(rule: HookRule, event: HookEvent) -> bool
```
**依赖：** `julycode.matching`。

### `julycode.hooks.actions`
**职责：** 执行四类动作，并把异常、超时和失败归一成 `HookExecutionResult`。  
**对外接口：**
```python
class HookActionRunner:
    async def run(
        self,
        rule: HookRule,
        event: HookEvent,
        context: HookRuntimeContext,
    ) -> HookExecutionResult:
        raise NotImplementedError
```
**依赖：** `asyncio`、`httpx`、`ToolPolicy`、`PermissionController`、`ToolExecutor`、`redact_secret`。

### `julycode.hooks.manager`
**职责：** 规则筛选、声明顺序执行、once 状态、后台任务、提示词注入队列和工具拦截决策。  
**对外接口：** `HookManager`、`create_hook_manager(config: HookConfig) -> HookManager`。  
**依赖：** `julycode.hooks.actions`、`julycode.hooks.conditions`、`julycode.hooks.models`。

### `julycode.config`
**职责：** 在 `AppConfig` 增加 `hooks: HookConfig`，并调用 `parse_hook_config()`。  
**对外接口调整：**
```python
@dataclass(frozen=True)
class AppConfig:
    # 现有字段保持不变
    hooks: HookConfig = field(default_factory=HookConfig)
```
**依赖：** `julycode.hooks.config`。

### `julycode.prompting`
**职责：** 在运行时补充中追加 Hook 提示词注入块。  
**对外接口调整：** `RuntimePromptContext` 增加 `hook_injections` 字段；`PromptBuilder.build_runtime_prompt()` 追加 `<julycode_hook_instructions>`。  
**依赖：** `julycode.hooks.models` 的轻量提示注入类型。

### `julycode.agent`
**职责：** 触发轮次、消息和系统级 Hook；把 Hook 提示注入传给 `PromptBuilder`；把 Hook 状态转成 `TurnEvent`。  
**对外接口调整：** `AgentLoopRunner` 构造函数新增可选参数 `hook_manager: HookManager | None = None`。  
**依赖：** `HookManager`、`HookRuntimeContext`、`HookEvent`。

### `julycode.tools.scheduler`
**职责：** 在工具调度中接入 `tool.before` 和 `tool.after`。  
**对外接口调整：** `ToolCallScheduler` 构造函数新增可选参数 `hook_manager: HookManager | None = None` 和 `hook_context: HookRuntimeContext | None = None`。  
**依赖：** `HookManager`。

### `julycode.tui.app`
**职责：** 创建和持有 HookManager；触发会话开始、会话结束事件；传入 AgentLoopRunner；消费 Hook 状态事件。  
**对外接口调整：** `JulyCodeApp.__init__()` 增加可选 `hook_manager`，便于测试注入。  
**依赖：** `create_hook_manager`。

### `julycode.cli`
**职责：** 用 `config.hooks` 创建 HookManager 并传给 TUI。  
**对外接口：** `main()` 无用户可见参数变化。  
**依赖：** `julycode.hooks.manager`。

## 模块交互
普通任务执行链路：

```text
JulyCodeApp
  → HookManager.emit(session.start)
  → 用户输入
  → AgentLoopRunner.run(command)
    → append_user_message()
    → HookManager.emit(turn.start)
    → HookManager.emit(message.user)
    → PromptBuilder.build_bundle(包含 hook_injections)
    → Provider.stream_chat()
    → 收到 assistant message
    → HookManager.emit(message.assistant)
    → ToolCallScheduler.run(tool_calls)
      → HookManager.before_tool(call)
        → 若 blocked：生成 ToolResult(error_type=hook_blocked)
        → 否则：ToolPolicy.validate_call()
        → PermissionController.evaluate()
        → ToolExecutor.execute()
      → HookManager.after_tool(call, result)
      → yield tool_finished
    → append_tool_result()
    → 下一轮模型请求或最终完成
    → HookManager.emit(system.stopped) 或 HookManager.emit(system.error)
    → HookManager.emit(turn.end)
  → JulyCodeApp.on_unmount()
  → HookManager.emit(session.end)
  → HookManager.close()
```

工具执行前顺序：

```text
tool_started event
  → Hook tool.before
  → Plan Mode / Skill whitelist policy
  → permission engine
  → permission prompt（仅模型工具调用）
  → real tool execution
  → Hook tool.after
  → tool_finished event
```

提示词注入链路：

```text
Hook prompt action
  → HookManager.enqueue_prompt_injection()
  → AgentLoopRunner.prompt_factory()
  → HookManager.consume_prompt_injections()
  → RuntimePromptContext(包含 hook_injections)
  → PromptBuilder 生成 <julycode_hook_instructions>
  → Provider 请求
```

后台动作链路：

```text
HookManager.emit(event)
  → 匹配 background 规则
  → asyncio.create_task(执行匹配动作)
  → 主流程立即继续
  → task done callback 记录成功或失败
  → HookManager.close() 尽力取消或等待未完成后台任务
```

## 文件组织
```text
src/julycode/
├── matching.py                       — 权限和 Hook 共用匹配表达式
├── config.py                         — AppConfig 增加 hooks 字段并解析主配置
├── permissions/
│   ├── models.py                     — MatchKind 扩展为 exact/glob/regex，PermissionRule 增加 negated 或 expression
│   └── rules.py                      — 权限规则匹配迁移到 julycode.matching
├── hooks/
│   ├── __init__.py                   — Hook 公共导出
│   ├── models.py                     — Hook dataclass、Literal、事件与结果模型
│   ├── config.py                     — YAML 解析和集中校验
│   ├── conditions.py                 — 条件字段读取与匹配
│   ├── actions.py                    — command/prompt/http/sub_agent 动作执行
│   └── manager.py                    — HookManager、once、后台任务、拦截和注入队列
├── prompting/
│   ├── base.py                       — RuntimePromptContext 增加 hook_injections
│   └── builder.py                    — 运行时提示追加 Hook 注入块
├── agent.py                          — 生命周期 Hook 触发和 TurnEvent 扩展
├── tools/
│   └── scheduler.py                  — tool.before/tool.after 接入
├── tui/
│   └── app.py                        — 会话级 Hook 和 Hook 状态展示
└── cli.py                            — 创建 HookManager

tests/
├── test_matching.py                  — 精确、反向、正则、glob 共用匹配测试
├── test_hooks_config.py              — Hook YAML 解析和配置错误测试
├── test_hooks.py                     — HookManager、动作执行、once、后台、拦截测试
├── test_config.py                    — AppConfig hooks 字段加载测试
├── test_agent.py                     — 生命周期事件、提示词注入、系统事件集成测试
├── test_tool_scheduler.py            — tool.before 拦截顺序和 tool.after 测试
└── test_tui_smoke.py                 — TUI 会话级事件和 Hook 状态回归测试
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 配置入口 | `hooks` 放在主配置 | 符合已确认范围，复用现有配置发现、加载和项目级覆盖行为。 |
| 多来源合并 | 沿用主配置浅合并，项目级 `hooks` 整体覆盖用户级 | 不引入独立 Hook 来源优先级，避免和“不做显式优先级”冲突。 |
| 规则顺序 | 按 YAML 列表声明顺序执行 | 满足稳定顺序要求，避免本阶段引入 priority。 |
| 匹配语法 | 新增 `julycode.matching`，权限与 Hook 共用 | 真正复用权限匹配语义，并补齐反向和正则能力。 |
| 反向匹配表示 | `!` 前缀 | 表达简洁，可组合 `!regex:` 和 `!glob:`，也不破坏现有精确/glob 规则。 |
| Hook 拦截 | `tool.before` 动作内声明 `tool_block`，不是新 action type | 保持四种动作类型不变，同时给工具前事件提供拒绝结果。 |
| Hook 失败策略 | 失败只产出 `HookExecutionResult(failed)` 并记录 | 满足 Hook 自身失败不影响主流程。 |
| shell 动作执行 | 映射到现有 `run_command` 工具语义，并先做策略/权限检查 | 避免 shell Hook 成为绕过 Plan Mode、Skill 白名单、危险命令和权限系统的新入口。 |
| HTTP 动作执行 | 直接使用 `httpx.AsyncClient`，Plan Mode 或受限 Skill 下跳过 | 当前没有模型 HTTP 工具，直接实现可控；受限模式下避免产生额外副作用。 |
| 提示词注入 | 下一次模型请求一次性消费 | 避免注入内容长期滞留或写入历史，Hook 需要重复注入时由事件重复触发。 |
| 后台任务 | `asyncio.create_task()` + done callback + close 清理 | 不阻塞 Agent 主流程，同时避免退出时遗留未观察异常。 |
| 子 Agent 动作 | 配置可声明，运行时只返回 placeholder | 为后续 SubAgent 章节留接口，同时严格遵守本阶段不真实运行。 |
| TUI 展示 | 只展示失败、拦截、占位等高信号 Hook 状态 | 避免正常 Hook 成功噪音污染对话界面，测试仍可消费 TurnEvent。 |

## 需求覆盖

| 需求 | 设计覆盖 |
|------|----------|
| F1 | `HookRule` 固定 `event`、`condition`、`action`，条件可为 `None`。 |
| F2 | `AppConfig.hooks` 和 `parse_hook_config()` 从主配置加载，缺省为空。 |
| F3 | `julycode.hooks.config` 集中校验所有结构、事件、动作、条件和控制冲突。 |
| F4 | `JulyCodeApp` 触发 `session.start`、`session.end`。 |
| F5 | `AgentLoopRunner` 触发 `turn.start`、`turn.end`。 |
| F6 | `AgentLoopRunner` 触发 `message.user`、`message.assistant`。 |
| F7 | `ToolCallScheduler` 在策略、权限和真实执行前调用 `HookManager.before_tool()`，执行后调用 `after_tool()`。 |
| F8 | `AgentLoopRunner` 在上下文压缩、停止和错误路径触发系统事件。 |
| F9 | `HookEvent.data` 定义各事件稳定字段，条件按字段路径读取。 |
| F10 | `julycode.matching` 提供精确、反向、正则和 glob，并迁移权限规则共用。 |
| F11 | `HookConditionGroup.logic` 只允许 `all` 或 `any`。 |
| F12 | `get_field_value()` 字段不存在返回未匹配。 |
| F13 | `tool.before` 事件包含 `tool.name` 和完整 `tool.arguments`。 |
| F14 | `HookToolDecision.blocked` 让 Scheduler 直接生成失败 `ToolResult`。 |
| F15 | 拦截结果走普通工具结果回灌，不触发 Agent 停止。 |
| F16 | 未 blocked 时 Scheduler 继续原有策略、权限和执行链路。 |
| F17 | `HookCommandAction` 通过当前 cwd 执行并记录输出、退出码、超时和错误。 |
| F18 | `HookPromptAction` 进入运行时提示块，不伪造用户消息，不写历史。 |
| F19 | `HookHttpAction` 发送请求并记录响应状态、摘要、超时和失败。 |
| F20 | `HookSubAgentAction` 只返回 placeholder 状态。 |
| F21 | `HookRuntimeState.executed_once` 实现当前运行期 once。 |
| F22 | `background` 规则走后台 task，不阻塞主流程。 |
| F23 | command/http action 都支持 `timeout_seconds`，shell 命令超时归一为失败。 |
| F24 | 配置校验禁止 `tool.before + background`。 |
| F25 | `HookActionRunner` 捕获异常并返回失败结果，Agent/TUI 不中断。 |
| F26 | `HookManager` 按 `HookRule.index` 顺序执行匹配规则。 |
| F27 | 只有 prompt action 和 tool_block 结果进入模型可见上下文；其他动作只记录状态。 |
