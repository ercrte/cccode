# JulyCode Agent Loop Plan

## 架构概览
本阶段在现有“单轮工具回灌”基础上升级为 Agent Loop，整体分为五层：命令解析层、Agent 编排层、流式收集层、工具调度层、界面展示层。Provider 层继续隐藏 OpenAI / Anthropic 协议差异，只向 Agent 编排层输出统一流事件。

命令解析层负责把用户输入分成普通任务、`/plan <需求>` 和 `/do` 三类。普通任务直接进入全工具 Agent Loop；`/plan` 进入只读规划 Agent Loop，并在成功结束后保存待执行计划；`/do` 在存在待执行计划时，把保存的计划转成执行目标，并用全工具 Agent Loop 执行。

Agent 编排层由新的 `AgentLoopRunner` 负责。它替代当前每次只执行一个工具的编排逻辑，在每次任务内循环发起模型请求、收集完整模型回复、判断工具调用、调度工具执行、回灌工具结果，并根据停止条件结束任务。它只输出 `TurnEvent`，TUI 不直接读取 Provider 原始事件。

流式收集层负责双路处理 Provider 输出：文本和 thinking 增量立即转成界面事件；完整文本、thinking、工具调用和用量信息同步累计成完整响应，供 Agent 编排层追加会话、判断是否继续循环和产出最终回复。

工具调度层在现有 `ToolExecutor` 之上增加工具安全分级、Plan Mode 工具策略和多工具批执行。读类工具可以按连续批次并发执行；写入、修改、命令执行和安全性未知的工具按原始顺序串行执行。所有工具结果按模型原始调用顺序回灌。

界面展示层继续负责消息、工具状态、进度、错误和输入恢复。TUI 解析用户命令，展示用户原始输入，然后消费 Agent 事件更新界面。运行中 `Ctrl+C` 取消当前 Agent Loop；空闲时 `Ctrl+C` 和 `Esc` 保持退出行为。

## 核心数据结构

### AgentConfig
```python
@dataclass(frozen=True)
class AgentConfig:
    max_iterations: int = 8
```

Agent Loop 运行配置。`max_iterations` 表示单次用户任务允许的最大模型响应轮次，作为兜底安全网。配置文件中使用：

```yaml
agent:
  max_iterations: 8
```

`AppConfig` 增加 `agent: AgentConfig` 字段，并在缺省时使用默认值。

### ToolSafety
```python
ToolSafety = Literal["read_only", "side_effect"]
```

工具安全等级。`read_only` 表示工具只读取本地状态，可与相邻读类工具并发；`side_effect` 表示工具可能改变文件、执行命令或产生外部影响，必须串行执行。无法确认安全性的工具按 `side_effect` 处理。

内置工具分级：

| 工具 | 安全等级 | 理由 |
|------|----------|------|
| `read_file` | `read_only` | 只读取文件内容 |
| `find_files` | `read_only` | 只枚举文件路径 |
| `search_code` | `read_only` | 只搜索文本内容 |
| `write_file` | `side_effect` | 创建或覆盖文件 |
| `edit_file` | `side_effect` | 修改文件 |
| `run_command` | `side_effect` | 命令可能有副作用 |

### ToolSpec
```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    timeout_seconds: float = 10.0
    safety: ToolSafety = "side_effect"
```

在现有工具描述上增加 `safety`。默认值为 `side_effect`，避免第三方或测试工具在未标注时被误判为可并发或可用于 Plan Mode。

### TokenUsage
```python
@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    provider: str | None = None
    raw: dict[str, Any] | None = None
```

表示供应商返回的 Token 用量。OpenAI 有完整 usage 时填充 input/output/total；Anthropic 根据流式 usage 字段填充可用值；供应商未提供时 Agent 会发出未知用量状态，不中断任务。

### StreamEvent
```python
StreamEventType = Literal[
    "message_start",
    "text_delta",
    "thinking_delta",
    "tool_call_delta",
    "usage",
    "message_done",
    "error",
]

@dataclass(frozen=True)
class StreamEvent:
    type: StreamEventType
    text: str = ""
    message: ChatMessage | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str = ""
    usage: TokenUsage | None = None
    error: str | None = None
```

Provider 到 Agent 的统一流事件。新增 `usage` 事件用于传递 Token 用量；其余事件沿用现有语义。

### AgentMode
```python
AgentMode = Literal["normal", "plan", "do"]
```

表示一次 Agent Loop 的运行模式。`normal` 使用全工具；`plan` 只暴露和允许读类工具；`do` 使用全工具执行已保存计划。

### AgentCommand
```python
@dataclass(frozen=True)
class AgentCommand:
    mode: AgentMode
    visible_text: str
    model_text: str
```

命令解析后的输入。`visible_text` 是界面显示的原始用户输入；`model_text` 是加入会话并发送给模型的任务文本。普通任务二者相同；`/plan` 会把用户需求包装成规划指令；`/do` 会把待执行计划包装成执行指令。

### PendingPlan
```python
@dataclass(frozen=True)
class PendingPlan:
    source_request: str
    plan_text: str
```

当前运行期待执行计划。仅保存在 `ChatSession` 内存中，不跨启动持久化。新的成功 `/plan` 会替换旧计划；成功 `/do` 会清除计划。

### AgentProgress
```python
@dataclass(frozen=True)
class AgentProgress:
    iteration: int
    max_iterations: int
    mode: AgentMode
    phase: Literal["model", "tools", "done"]
    detail: str = ""
```

描述 Agent Loop 当前进度。界面用它展示当前轮次、模式和阶段；测试用它验证循环进度。

### AgentStopReason
```python
AgentStopReason = Literal[
    "completed",
    "iteration_limit",
    "cancelled",
    "unknown_tool_limit",
    "stream_error",
]
```

Agent Loop 停止原因。`completed` 表示模型给出最终回复；其余原因都必须产出可观察的停止或错误事件。

### TurnEvent
```python
TurnEventType = Literal[
    "progress",
    "text_delta",
    "thinking_delta",
    "usage",
    "tool_started",
    "tool_finished",
    "message_done",
    "stopped",
    "error",
]

@dataclass(frozen=True)
class TurnEvent:
    type: TurnEventType
    text: str = ""
    message: ChatMessage | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    progress: AgentProgress | None = None
    usage: TokenUsage | None = None
    stop_reason: AgentStopReason | None = None
    error: str | None = None
```

Agent 到 TUI 的唯一事件流。TUI 根据这些事件更新消息正文、thinking 面板、工具状态、Token 用量、进度、取消和错误。

### StreamCollection
```python
@dataclass(frozen=True)
class StreamCollection:
    message: ChatMessage
    usage: TokenUsage | None
```

一次模型流式响应的完整聚合结果。`message` 包含完整文本、thinking 和工具调用；`usage` 是该响应内最后一次可用 Token 用量。

### StreamCollector
```python
class StreamCollector:
    async def collect(
        self,
        stream: AsyncIterator[StreamEvent],
        *,
        iteration: int,
        mode: AgentMode,
    ) -> AsyncIterator[TurnEvent]:
        ...

    def result(self) -> StreamCollection:
        ...
```

双路流式收集器。`collect()` 遍历 Provider 流事件并即时产出 `text_delta`、`thinking_delta`、`usage` 等 `TurnEvent`；`result()` 在流结束后返回完整聚合结果。

### ToolPolicy
```python
@dataclass(frozen=True)
class ToolPolicy:
    mode: AgentMode

    def allowed_specs(self, registry: ToolRegistry) -> tuple[ToolSpec, ...]: ...
    def validate_call(self, call: ToolCall, registry: ToolRegistry) -> ToolResult | None: ...
```

工具策略。普通和执行模式返回全部工具；规划模式只返回 `read_only` 工具。`validate_call()` 在模型请求未暴露或当前模式禁用的工具时生成结构化失败结果，避免实际执行。

### ToolBatch
```python
@dataclass(frozen=True)
class ToolBatch:
    calls: tuple[ToolCall, ...]
    concurrent: bool
```

一组可一起调度的工具调用。连续读类工具形成 `concurrent=True` 的批次；有副作用或安全性未知的工具形成单调用串行批次。

### ToolCallScheduler
```python
class ToolCallScheduler:
    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        policy: ToolPolicy,
    ) -> None: ...

    def make_batches(self, calls: Sequence[ToolCall]) -> tuple[ToolBatch, ...]: ...

    async def run(
        self,
        calls: Sequence[ToolCall],
    ) -> AsyncIterator[TurnEvent]:
        ...

    def results(self) -> tuple[ToolResult, ...]: ...
```

多工具调度器。它按原始调用顺序分批，执行时发出 `tool_started` 和 `tool_finished` 事件，最终 `results()` 返回与原始调用顺序一致的结果列表。并发批次内部使用 `asyncio.gather()`，但回灌顺序仍按输入顺序。

### AgentLoopRunner
```python
class AgentLoopRunner:
    def __init__(
        self,
        session: ChatSession,
        provider: LLMProvider,
        registry: ToolRegistry,
        executor: ToolExecutor,
        config: AgentConfig,
    ) -> None: ...

    async def run(self, command: AgentCommand) -> AsyncIterator[TurnEvent]: ...
    def cancel(self) -> None: ...
```

Agent Loop 入口。`run()` 负责追加用户任务、循环调用模型、收集响应、执行工具、处理停止条件和维护待执行计划。`cancel()` 设置取消状态，并让当前任务尽快停止；TUI 也可以直接取消运行中的 asyncio task，Runner 必须捕获取消并产出 `stopped(cancelled)`。

## 模块设计

### 配置模块
**职责：** 解析 Agent Loop 配置，提供默认迭代上限。  
**对外接口：** `AgentConfig`、`AppConfig.agent`、配置解析逻辑。  
**依赖：** 无新增外部依赖。

配置示例：

```yaml
agent:
  max_iterations: 8
```

解析规则：未配置时使用 8；配置为非正整数时返回配置错误。

### Provider 基础协议
**职责：** 扩展统一流事件，使 Provider 可以传递 Token 用量。  
**对外接口：** `TokenUsage`、扩展后的 `StreamEvent`。  
**依赖：** 工具基础类型、现有 Provider 协议。

OpenAI Provider 请求流式接口时加入用量请求选项，并在流式 chunk 出现 `usage` 时发出 `StreamEvent(type="usage")`。Anthropic Provider 在 `message_start` 或 `message_delta` 中解析 usage 字段并发出同类事件。未收到 usage 时不报错。

### 命令解析模块
**职责：** 将用户输入解析为 Agent 命令，并处理 `/do` 无待执行计划、`/plan` 缺少需求等用户可见错误。  
**对外接口：**

```python
def parse_agent_command(raw_text: str, session: ChatSession) -> AgentCommand | ChatMessage:
    ...
```

返回 `AgentCommand` 表示可以启动 Agent Loop；返回 `ChatMessage` 表示无需调用模型，直接向用户展示提示，例如“当前没有待执行计划”。

### 会话模块
**职责：** 保存当前运行期消息上下文和待执行计划。  
**对外接口：**

```python
@dataclass
class ChatSession:
    messages: list[ChatMessage]
    pending_plan: PendingPlan | None = None

    def save_pending_plan(self, plan: PendingPlan) -> None: ...
    def clear_pending_plan(self) -> None: ...
```

会话历史仍只在当前运行期保留。待执行计划不写入磁盘。

### 工具基础模块
**职责：** 为工具增加安全分级。  
**对外接口：** `ToolSafety`、扩展后的 `ToolSpec.safety`。  
**依赖：** 无新增外部依赖。

内置工具在定义 `ToolSpec` 时显式标注安全等级；测试工具或未来工具若未标注，默认视为有副作用。

### 工具策略与调度模块
**职责：** 过滤当前模式可用工具、阻止 Plan Mode 执行有副作用工具、对多工具调用分批并发或串行执行。  
**对外接口：** `ToolPolicy`、`ToolBatch`、`ToolCallScheduler`。  
**依赖：** `ToolRegistry`、`ToolExecutor`、`ToolSpec.safety`。

批处理算法：

1. 按模型返回顺序扫描工具调用。
2. 连续 `read_only` 调用累计为一个并发批次。
3. 遇到 `side_effect` 或未知安全性的调用时，先提交前面的读类批次，再把该调用作为单独串行批次。
4. 调度执行所有批次，批次之间严格按顺序等待。
5. 所有结果按原始调用顺序追加到会话并回灌给模型。

### 流式收集模块
**职责：** 同时满足实时展示和完整响应判断。  
**对外接口：** `StreamCollector`、`StreamCollection`。  
**依赖：** Provider `StreamEvent`、Agent `TurnEvent`。

处理规则：

- `text_delta`：立即产出 `TurnEvent(text_delta)`，并追加到完整文本。
- `thinking_delta`：立即产出 `TurnEvent(thinking_delta)`，并追加到完整 thinking。
- `usage`：立即产出 `TurnEvent(usage)`，并保存最后一次用量。
- `message_done`：保存完整 `ChatMessage`；如果 Provider 未给出 message，则用累计文本和 thinking 合成。
- `error` 或 Provider 异常：转为 Agent 错误停止。

### Agent 编排模块
**职责：** 运行 ReAct 风格循环，处理所有停止条件和 Plan Mode 状态。  
**对外接口：** `AgentLoopRunner`、`TurnEvent`、`AgentProgress`、`AgentStopReason`。  
**依赖：** `ChatSession`、`LLMProvider`、`ToolRegistry`、`ToolExecutor`、`ToolCallScheduler`、`StreamCollector`。

核心流程：

1. 根据 `AgentCommand` 追加模型可见的用户任务。
2. 对 `iteration` 从 1 到 `max_iterations` 循环。
3. 发出 `progress(model)`，使用当前模式对应的 `ToolPolicy.allowed_specs()` 构建模型请求。
4. 用 `StreamCollector` 收集 Provider 流，边收集边向 TUI 转发文本、thinking 和 usage。
5. 如果模型没有工具调用，追加 assistant 消息，发出 `message_done`，按模式保存或清除待执行计划，结束。
6. 如果当前已经是最后一轮且模型仍请求工具，发出 `stopped(iteration_limit)`，结束且不再执行工具。
7. 如果上一轮已经出现未知工具，且本轮再次出现未知工具，发出 `stopped(unknown_tool_limit)`，结束且不执行本轮工具。
8. 追加带工具调用的 assistant 消息。
9. 发出 `progress(tools)`，用 `ToolCallScheduler` 执行所有工具调用。
10. 将工具结果按原始顺序追加到会话。
11. 根据本轮是否出现未知工具更新连续未知工具计数；没有未知工具则清零。
12. 进入下一轮，让模型基于工具结果继续判断。

取消流程：TUI 在运行中触发取消时，取消当前 task 或调用 `cancel()`。Runner 捕获 `asyncio.CancelledError` 后发出 `stopped(cancelled)`；TUI 恢复输入。

### TUI 应用模块
**职责：** 展示 Agent Loop 事件、处理取消和 Plan Mode 命令。  
**对外接口：** `JulyCodeApp` 内部事件处理逻辑。  
**依赖：** `AgentLoopRunner`、`parse_agent_command`、现有 widgets。

界面行为：

- 提交输入后先展示用户原始输入。
- 如果命令解析直接返回提示消息，则展示提示并恢复输入。
- 运行中接收 `progress` 更新状态栏。
- 接收 `usage` 更新状态栏中的 Token 用量；没有 usage 时显示“用量未知”或保持空状态。
- 接收 `tool_started` 时新增工具状态视图。
- 接收 `tool_finished` 时更新对应工具状态。
- 接收 `message_done` 时更新助手消息并恢复输入。
- 接收 `stopped` 或 `error` 时展示原因并恢复输入。
- 运行中 `Ctrl+C` 取消当前任务；空闲时 `Ctrl+C` 退出；`Esc` 始终退出。

### TUI Widgets
**职责：** 承载进度、Token 用量和多工具状态展示。  
**对外接口：** 扩展 `StatusBar`；`ToolStatusView` 支持通过工具调用 id 定位更新。  
**依赖：** `TokenUsage`、`AgentProgress`、`ToolResult`。

`StatusBar` 增加当前模式、轮次和 Token 用量展示。`ToolStatusView` 保存 `tool_call_id`，避免同名工具并发时更新错视图。

### 测试模块
**职责：** 覆盖 Agent Loop、工具调度、Plan Mode、Provider usage 和 TUI 行为。  
**对外接口：** pytest 测试和 mock OpenAI 服务器。  
**依赖：** 现有测试工具、Textual test pilot、mock Provider。

新增或修改测试重点：

- 多轮工具循环直到最终回复。
- 迭代上限停止。
- 取消运行中任务。
- 连续未知工具停止。
- Provider 流式错误停止。
- 文本双路收集：实时 delta 与完整 message 一致。
- 多个读类工具并发执行。
- 多个有副作用工具串行执行。
- 混合工具按保守批次执行。
- `/plan` 只暴露读类工具，阻止有副作用工具。
- `/do` 执行保存计划、无计划提示、执行成功后清理计划。
- TUI 进度、Token 用量、取消和多工具状态展示。

## 模块交互
普通 Agent Loop：

```text
用户输入
  → TUI 展示用户消息
  → parse_agent_command 得到 normal 命令
  → AgentLoopRunner.run()
  → ChatSession 追加用户任务
  → Provider.stream_chat(ChatRequest(messages, all_tools))
  → StreamCollector 实时转发文本/usage 并聚合完整回复
  → 无工具调用：保存 assistant，message_done，结束
  → 有工具调用：保存 assistant(tool_calls)
  → ToolCallScheduler 分批执行工具
  → ChatSession 追加所有 tool result
  → 下一轮 Provider.stream_chat(...)
  → 直到最终回复或停止条件
```

Plan Mode：

```text
/plan <需求>
  → parse_agent_command 得到 plan 命令
  → AgentLoopRunner 使用 ToolPolicy(plan)
  → Provider 只收到 read_only 工具描述
  → 模型如请求有副作用工具，ToolPolicy 返回 tool_not_allowed 失败结果
  → 模型最终输出计划
  → ChatSession.save_pending_plan(PendingPlan)
  → TUI 展示计划

/do
  → 若无 pending_plan：直接展示提示，不调用模型
  → 若有 pending_plan：parse_agent_command 得到 do 命令
  → AgentLoopRunner 使用全工具执行计划
  → completed 后 ChatSession.clear_pending_plan()
```

停止条件：

```text
模型无工具调用
  → completed

当前为 max_iterations 且仍请求工具
  → iteration_limit，停止且不执行本轮工具

第一次未知工具
  → 执行器返回 unknown_tool 失败结果并回灌

下一轮再次出现未知工具
  → unknown_tool_limit，停止且不执行本轮工具

用户取消
  → cancelled，停止后续请求和工具调度

Provider 或流式收集错误
  → stream_error / error，展示脱敏错误
```

## 文件组织
```text
julycode/
├── src/julycode/
│   ├── agent.py                         — AgentLoopRunner、TurnEvent、AgentProgress、StreamCollector
│   ├── commands.py                      — /plan、/do、普通输入的命令解析
│   ├── config.py                        — AgentConfig 与 agent.max_iterations 配置解析
│   ├── session.py                       — PendingPlan 与待执行计划运行期状态
│   ├── providers/
│   │   ├── base.py                      — TokenUsage、扩展 StreamEvent
│   │   ├── openai.py                    — OpenAI 流式 usage 解析
│   │   └── anthropic.py                 — Anthropic 流式 usage 解析
│   ├── tools/
│   │   ├── base.py                      — ToolSafety、ToolSpec.safety
│   │   ├── builtin.py                   — 内置工具安全等级标注
│   │   ├── registry.py                  — 按安全等级筛选工具描述
│   │   └── scheduler.py                 — ToolPolicy、ToolBatch、ToolCallScheduler
│   └── tui/
│       ├── app.py                       — AgentLoopRunner 接入、取消、命令模式处理
│       └── widgets.py                   — 状态栏和工具状态视图扩展
├── tests/
│   ├── test_agent.py                    — Agent Loop 循环、停止条件、Plan Mode
│   ├── test_tool_scheduler.py           — 多工具分批、并发和串行顺序
│   ├── test_openai_provider.py          — OpenAI usage 解析与请求选项
│   ├── test_anthropic_provider.py       — Anthropic usage 解析
│   ├── test_config.py                   — agent.max_iterations 配置解析
│   ├── test_session.py                  — PendingPlan 保存和清理
│   └── test_tui_smoke.py                — TUI 进度、Token、取消和命令提示
└── specs/agent-loop/
    ├── spec.md                          — 已批准需求
    ├── plan.md                          — 本技术设计
    ├── task.md                          — 后续任务拆解
    └── checklist.md                     — 后续验收清单
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 循环入口 | 新建 `AgentLoopRunner` 替代单轮 `ToolAwareTurnRunner` | 当前单轮编排已成为瓶颈，新 Runner 可以集中处理循环、停止条件和 Plan Mode |
| 事件模型 | 保持 Provider 事件与 TUI 事件分层 | Provider 差异继续封装在协议层，界面只依赖 Agent 事件 |
| 流式收集 | 独立 `StreamCollector` 双路处理 | 同时满足实时展示和完整回复判断，避免在 Runner 中散落拼接逻辑 |
| 工具安全等级 | 在 `ToolSpec` 增加 `safety`，默认 `side_effect` | 工具自身最清楚安全属性，默认保守可避免误并发 |
| 多工具调度 | 连续读类并发，有副作用单调用串行 | 满足并发能力，同时不让有副作用工具越序或并行 |
| Plan Mode 工具限制 | 同时过滤暴露工具和执行前校验 | 即使模型请求未暴露的有副作用工具，也不会实际执行 |
| 迭代上限语义 | 限制模型响应轮次；最后一轮若仍请求工具则停止且不执行 | 上限作为安全网，避免在无法继续观察的情况下执行新动作 |
| 连续未知工具语义 | 第一次 unknown_tool 回灌，下一轮仍出现未知工具则停止 | 给模型一次修正机会，同时防止无限请求不存在工具 |
| 待执行计划存储 | 保存于 `ChatSession.pending_plan`，不持久化 | 符合本阶段“不做跨启动恢复”的边界 |
| `/do` 成功后清理计划 | 仅在 completed 时清理，取消/错误/上限停止时保留 | 失败或取消后用户可以再次 `/do` 重试当前计划 |
| 取消方式 | 运行中 `Ctrl+C` 取消，空闲 `Ctrl+C` 退出，`Esc` 退出 | 保留现有退出习惯，同时提供任务取消能力 |
| Token 用量 | Provider 有则发 `usage`，无则 Agent/TUI 显示未知 | 不把用量信息作为硬依赖，避免供应商缺字段导致任务失败 |
| 权限与确认 | 本阶段不引入权限系统，只做 Plan Mode 工具限制 | 与 spec 边界一致，避免提前实现后续章节能力 |

## 需求覆盖

| 需求 | 架构归属 |
|------|----------|
| F1 | `AgentLoopRunner` 循环发起模型请求、执行工具并回灌 |
| F2 | `AgentLoopRunner` 在无工具调用时发出 `message_done(completed)` |
| F3 | `AgentConfig.max_iterations` 与 Runner 迭代上限判断 |
| F4 | TUI 取消动作与 Runner 取消处理 |
| F5 | Runner 连续未知工具计数与 `unknown_tool_limit` 停止 |
| F6 | `StreamCollector` / Runner 错误转换与 TUI 输入恢复 |
| F7 | `TurnEvent` 覆盖文本、工具、usage、progress、完成、取消、错误 |
| F8 | TUI 只消费 `TurnEvent` |
| F9 | `StreamCollector` 双路收集 |
| F10 | `ToolCallScheduler` 接收完整工具调用列表 |
| F11 | `ToolSpec.safety`、`ToolBatch` 与调度算法 |
| F12 | `ToolCallScheduler.results()` 按原始顺序返回，Session 按序追加 |
| F13 | 批处理算法在副作用工具前后切分读类批次 |
| F14 | `ToolPolicy(plan).allowed_specs()` 只返回读类工具 |
| F15 | Runner 在 plan completed 后保存 `PendingPlan` |
| F16 | `/do` 命令转执行目标，Runner 使用全工具模式 |
| F17 | 命令解析层在无 pending plan 时直接返回提示 |
| F18 | `ChatSession.save_pending_plan()` 替换旧计划，completed do 清理 |
| F19 | 工具失败结果由调度器返回并追加到会话，未触发停止条件则继续 |
| F20 | `ChatSession` 保存同一任务内的 assistant/tool 消息供后续轮次引用 |
