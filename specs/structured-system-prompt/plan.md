# JulyCode Structured System Prompt Plan

## 架构概览
本阶段新增一个提示构造层，位于 `AgentLoopRunner` 和 Provider 之间。Agent Loop 不再只把会话消息和工具列表交给 Provider，而是在每次模型请求前构造 `PromptBundle`：稳定的全局系统提示作为固定前缀，动态环境和模式状态作为运行时补充块，会话历史仍沿用现有消息序列。

提示构造层由三部分组成。第一部分是固定模块库，按身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出的顺序生成稳定系统提示。第二部分是运行时补充构造器，按当前工作目录、Agent 模式、循环轮次、待执行计划和工具策略生成带标签的系统级补充消息。第三部分是缓存观测模型，把不同供应商返回的缓存字段统一到 `TokenUsage` 里，供 Agent 事件流、TUI 和测试读取。

Provider 层负责把统一提示结构映射到各自协议。OpenAI Chat Completions 使用前置 `system` 消息承载稳定提示和运行时补充，并把稳定内容放在消息数组最前面以获得自动前缀缓存；Anthropic Messages 使用 `system` 文本块承载稳定提示和运行时补充，在最后一个稳定块上设置显式缓存断点，动态块不标记缓存。两类 Provider 都继续保留现有流式文本、工具调用、工具结果和错误处理逻辑。

命令解析层需要收窄 `/plan` 和 `/do` 的用户消息内容。Plan Mode 的控制指令、只读约束和执行计划不再拼进普通用户消息，而是由提示构造层作为系统级运行时补充注入；用户消息只保留用户实际请求或“执行当前计划”这类意图文本。

工具描述继续由 `ToolSpec` 提供，但内置工具的描述会补充适用场景和关键约束。全局提示中的工具规则与每个工具描述互相呼应，形成双重约束，但不改变工具执行器、调度器和安全策略的现有职责。

## 核心数据结构

### PromptBlock
```python
@dataclass(frozen=True)
class PromptBlock:
    name: str
    title: str
    text: str
    stable: bool
    cacheable: bool = False
```

表示一个系统提示块。`stable=True` 的块内容必须确定且不含运行期环境、用户消息或密钥；`cacheable=True` 表示 Provider 可以在协议支持时把该块作为缓存断点或缓存前缀的一部分。

### PromptBundle
```python
@dataclass(frozen=True)
class PromptBundle:
    stable_blocks: Sequence[PromptBlock]
    runtime_blocks: Sequence[PromptBlock]
```

一次模型请求的系统提示集合。Provider 必须先序列化 `stable_blocks`，再序列化 `runtime_blocks`，最后追加正常会话消息。

### RuntimePromptContext
```python
@dataclass(frozen=True)
class RuntimePromptContext:
    cwd: Path
    mode: AgentMode
    iteration: int
    max_iterations: int
    allowed_tools: Sequence[ToolSpec]
    pending_plan: PendingPlan | None = None
    source_request: str = ""
```

运行时补充构造器的输入。它只保存当前轮次可观察状态，不保存长期记忆，也不从项目指令文件加载内容。

### RuntimeInstructionLevel
```python
RuntimeInstructionLevel = Literal["full", "refresh", "brief"]
```

描述当前轮次注入详细程度。第 1 轮使用 `full`，之后每 3 轮使用 `refresh`，其余轮次使用 `brief`。这个常量先放在提示构造层，不新增用户配置。

### PromptCacheUsage
```python
CacheStatus = Literal["hit", "miss", "write", "unknown", "unsupported"]

@dataclass(frozen=True)
class PromptCacheUsage:
    status: CacheStatus
    read_input_tokens: int | None = None
    creation_input_tokens: int | None = None
    cached_tokens: int | None = None
    supported: bool = True
```

统一缓存观测结果。Anthropic 可填 `read_input_tokens` 和 `creation_input_tokens`；OpenAI 可填 `cached_tokens`。当字段缺失或不可识别时使用 `unknown`，协议不支持时使用 `unsupported`。

### TokenUsage
```python
@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    provider: str | None = None
    cache: PromptCacheUsage | None = None
    raw: dict[str, Any] | None = None
```

在现有用量结构上增加 `cache` 字段。已有调用方如果只读取 `input_tokens`、`output_tokens`、`total_tokens` 不需要改变。

### ChatRequest
```python
@dataclass(frozen=True)
class ChatRequest:
    messages: Sequence[ChatMessage]
    tools: Sequence[ToolSpec] = ()
    prompt: PromptBundle | None = None
```

Provider 的统一请求输入。`prompt` 为 `None` 时保持现有行为，便于渐进迁移和单元测试。

### PromptBuilder
```python
class PromptBuilder:
    def build_stable_prompt(self) -> Sequence[PromptBlock]
    def build_runtime_prompt(self, context: RuntimePromptContext) -> Sequence[PromptBlock]
    def build_bundle(self, context: RuntimePromptContext) -> PromptBundle
```

提示构造入口。固定模块顺序在这里集中定义，运行时补充标签也在这里统一生成。

### AgentCommand
```python
@dataclass(frozen=True)
class AgentCommand:
    mode: AgentMode
    visible_text: str
    model_text: str
```

保持字段名兼容，但改变 `/plan` 和 `/do` 的语义：`model_text` 只表示用户请求或用户意图，不再包含系统控制指令或完整计划。

## 模块设计

### `julycode.prompting`
**职责：** 生成稳定全局提示、动态运行时补充、注入频率和标签格式。  
**对外接口：** `PromptBuilder.build_bundle(context)`。  
**依赖：** `julycode.commands.AgentMode`、`julycode.session.PendingPlan`、`julycode.tools.base.ToolSpec`。

固定提示模块用确定顺序返回：
1. 身份
2. 系统约束
3. 任务模式
4. 动作执行
5. 工具使用
6. 语气风格
7. 文本输出

运行时补充使用统一标签：
```text
<julycode_runtime_context>
环境信息：cwd=/home/cui/julycode
模式状态：plan full 1/8
本轮约束：只使用读取、查找和搜索类工具，不写文件、不改文件、不执行命令。
</julycode_runtime_context>
```

内部按 `环境信息`、`模式状态`、`待执行计划`、`本轮约束` 排列。`full` 包含完整模式说明和关键规则，`refresh` 只重复关键规则，`brief` 只说明当前模式、轮次和必要状态。

### `julycode.session`
**职责：** 在构建请求时接收并携带 `PromptBundle`。  
**对外接口：** `build_request(tools=(), prompt=None) -> ChatRequest`。  
**依赖：** Provider 基础类型。

会话历史仍只存储用户、助手和工具消息，不把稳定提示或运行时补充写入 `ChatSession.messages`，避免污染对话历史和后续显示。

### `julycode.agent`
**职责：** 在每次请求模型前收集运行时上下文，调用 `PromptBuilder`，再把 `PromptBundle` 放入 `ChatRequest`。  
**对外接口：** `AgentLoopRunner.run(command)` 保持不变。  
**依赖：** `PromptBuilder`、`RuntimePromptContext`、`ToolPolicy`、`ToolExecutor.context`。

Agent Loop 使用当前 `iteration` 计算注入级别。`plan` 模式使用只读工具策略构建上下文；`do` 模式从 `session.pending_plan` 注入待执行计划。完成 `plan` 时仍保存模型生成的计划，完成 `do` 时仍清除计划。

### `julycode.commands`
**职责：** 解析用户命令，但不再生成系统控制段落。  
**对外接口：** `parse_agent_command(raw_text, session)`。  
**依赖：** `ChatSession`。

`/plan <需求>` 返回 `mode="plan"`，`model_text` 为 `<需求>`。`/do` 返回 `mode="do"`，`model_text` 为“执行当前待执行计划。”；完整计划由运行时补充注入。

### `julycode.tools.builtin`
**职责：** 强化内置工具描述，让每个工具声明适用场景、约束和失败后的处理预期。  
**对外接口：** 现有 `ToolSpec` 不变。  
**依赖：** 无新增依赖。

示例规则：`edit_file` 描述强调编辑前应读取或搜索目标文件，`run_command` 描述强调命令有副作用且应用于构建、测试、检查或用户明确需要的本地命令，读类工具描述强调优先使用专用搜索和读取能力。

### `julycode.providers.openai`
**职责：** 把 `PromptBundle` 映射为 Chat Completions 请求，并解析 OpenAI 缓存用量。  
**对外接口：** `stream_chat(request)` 保持不变。  
**依赖：** `PromptBundle`、`PromptCacheUsage`。

请求构造顺序：
1. 首个 `system` 消息，内容为稳定模块拼接文本。
2. 第二个 `system` 消息，内容为带标签的运行时补充。
3. 现有会话消息。

OpenAI Prompt Caching 是自动前缀缓存；设计重点是让稳定提示、稳定工具列表和重复上下文位于请求前缀。用量解析读取 `usage.prompt_tokens_details.cached_tokens`，`cached_tokens > 0` 标记为 `hit`，等于 0 标记为 `miss`，字段缺失标记为 `unknown`。

### `julycode.providers.anthropic`
**职责：** 把 `PromptBundle` 映射为 Messages 请求，并解析 Anthropic 缓存用量。  
**对外接口：** `stream_chat(request)` 保持不变。  
**依赖：** `PromptBundle`、`PromptCacheUsage`。

请求构造顺序：
1. `tools` 保持在请求顶层，工具定义稳定。
2. `system` 使用文本块数组：稳定提示块在前，最后一个稳定块加 `cache_control: {"type": "ephemeral"}`；运行时补充块在后，不加 `cache_control`。
3. 现有会话消息。

用量解析读取 `cache_read_input_tokens`、`cache_creation_input_tokens` 和 `input_tokens`。`cache_read_input_tokens > 0` 标记为 `hit`；无读取但有创建标记为 `write`；都为 0 且字段存在标记为 `miss`；字段缺失标记为 `unknown`。当缓存字段存在时，`TokenUsage.input_tokens` 使用缓存读取、缓存创建和未缓存输入的合计，`cache` 字段保留明细。

### `julycode.tui`
**职责：** 展示统一缓存观测结果。  
**对外接口：** `StatusBar.set_usage(usage)` 不变。  
**依赖：** 扩展后的 `TokenUsage`。

状态栏在生成中显示 Token 总量时追加简短缓存状态，例如 `Cache: hit 1920`、`Cache: write 1500` 或 `Cache: unknown`。不把原始供应商字段直接暴露给界面。

### 测试与人工场景
**职责：** 验证提示结构、Provider 映射、缓存字段解析、Plan Mode 注入和现有行为不回退。  
**对外接口：** 新增自动化测试和 `manual-scenarios.md`。  
**依赖：** 现有 pytest、TUI smoke、mock OpenAI server。

自动化测试覆盖请求 payload 和内部结构；人工场景覆盖真实交互中模型是否优先使用工具、编辑前是否读取、Plan Mode 是否保持只读、环境信息是否通过系统补充出现、缓存字段是否能被观察到。

## 模块交互
1. 用户输入进入 `parse_agent_command()`。
2. `AgentLoopRunner.run()` 把 `command.model_text` 作为普通用户消息追加到 `ChatSession.messages`。
3. 每一轮模型请求前，Agent Loop 用当前工具策略生成允许工具列表。
4. Agent Loop 构造 `RuntimePromptContext`，其中包含工作目录、模式、轮次、迭代上限、允许工具和待执行计划。
5. `PromptBuilder.build_bundle()` 返回稳定提示块和运行时补充块。
6. `ChatSession.build_request(tools, prompt)` 生成 `ChatRequest`。
7. Provider 按协议把 `PromptBundle`、`tools` 和 `messages` 序列化为请求 payload。
8. Provider 流式解析文本、thinking、工具调用和 usage；缓存字段进入 `TokenUsage.cache`。
9. Agent Loop 把 usage 作为 `TurnEvent(type="usage")` 发送给 TUI。
10. TUI 状态栏显示 Token 和缓存状态；工具调度、工具结果回灌和最终回复沿用现有流程。

## 文件组织
```text
julycode/
├── specs/structured-system-prompt/
│   ├── spec.md                         — 已批准需求
│   ├── plan.md                         — 本技术设计
│   ├── task.md                         — 后续任务拆解
│   ├── checklist.md                    — 后续验收清单
│   └── manual-scenarios.md             — 人工对比场景
├── src/julycode/
│   ├── prompting/
│   │   ├── __init__.py                 — 提示构造公共导出
│   │   ├── base.py                     — PromptBlock、PromptBundle、RuntimePromptContext
│   │   ├── modules.py                  — 固定提示模块文本和顺序
│   │   └── builder.py                  — PromptBuilder 和轮次注入策略
│   ├── providers/
│   │   ├── base.py                     — ChatRequest、TokenUsage、PromptCacheUsage 扩展
│   │   ├── openai.py                   — OpenAI system 消息映射和 cached_tokens 解析
│   │   └── anthropic.py                — Anthropic system 块缓存断点和 cache usage 解析
│   ├── session.py                      — build_request 携带 PromptBundle
│   ├── agent.py                        — 每轮构造 RuntimePromptContext 和 PromptBundle
│   ├── commands.py                     — /plan 与 /do 不再拼接系统控制文本
│   ├── tools/builtin.py                — 强化内置工具描述
│   └── tui/widgets.py                  — 状态栏显示缓存观测
├── tests/
│   ├── test_prompting.py               — 提示模块顺序、稳定性和运行时注入频率
│   ├── test_openai_provider.py         — OpenAI payload 和缓存用量解析
│   ├── test_anthropic_provider.py      — Anthropic payload 和缓存用量解析
│   ├── test_agent.py                   — Agent Loop 注入 PromptBundle，Plan/Do 行为不回退
│   ├── test_commands.py                — /plan 与 /do 用户消息语义
│   └── test_tui_smoke.py               — 状态栏缓存状态展示
└── README.md                           — 记录结构化系统提示、缓存观测和 Plan Mode 注入变化
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 提示结构承载位置 | 新增 `PromptBundle`，不把系统提示写入 `ChatSession.messages` | 会话历史保持用户、助手、工具消息语义，避免系统补充污染历史和界面展示。 |
| 固定模块顺序 | 身份 → 系统约束 → 任务模式 → 动作执行 → 工具使用 → 语气风格 → 文本输出 | 与需求指定优先级一致，稳定内容可确定生成。 |
| 动态补充形式 | 使用带 `<julycode_runtime_context>` 标签的系统级补充块 | 与普通用户输入区分，便于模型识别运行时控制信息，也便于测试断言。 |
| 注入频率 | 第 1 轮 full，每 3 轮 refresh，其余 brief | 满足“首轮完整、间隔轮次重复、其余精简”，同时避免每轮重复长指令。 |
| OpenAI 映射 | 使用前置 `system` 消息放在消息数组最前面 | 兼容 OpenAI Chat Completions 和更多 OpenAI 兼容接口；前缀稳定有利于自动 Prompt Caching。 |
| OpenAI 缓存观测 | 解析 `usage.prompt_tokens_details.cached_tokens` | 官方文档把该字段作为缓存命中 Token 数；OpenAI 不提供同等 cache write 明细，因此写入状态不推断。 |
| Anthropic 映射 | `system` 文本块数组，最后一个稳定块加 `cache_control`，动态块不加 | 官方文档支持 system 文本块缓存断点；把动态内容放在断点之后可避免每轮变化破坏稳定前缀缓存。 |
| Anthropic 缓存观测 | 解析 `cache_read_input_tokens` 和 `cache_creation_input_tokens` | 官方文档直接用这两个字段区分缓存读取和创建，能满足命中与写入观测。 |
| 工具规则强化 | 同时修改全局工具模块和每个内置工具描述 | 模型在读取全局规则和单个工具 schema 时都会看到关键约束，提高遵守率。 |
| Plan Mode 改造 | 命令解析只保留用户意图，模式约束由运行时补充注入 | 解决当前控制指令混入普通用户消息的问题，并让 `/do` 的计划注入不污染用户历史。 |
| 可选模块 | 构造器预留可选块参数，但本阶段不实现加载器 | 满足未来插入自定义指令、Skill、长期记忆的结构需求，同时遵守本阶段不做加载和记忆。 |
| 供应商能力差异 | Provider 内部封装，统一向上暴露 `PromptCacheUsage` | 用户交互层和 Agent Loop 不需要理解 OpenAI/Anthropic 字段差异。 |

## 协议依据
- OpenAI Prompt Caching 会自动作用于较长提示，并建议把静态内容放在前缀；用量中通过 `cached_tokens` 观测命中。参考：https://developers.openai.com/api/docs/guides/prompt-caching
- OpenAI Chat Completions 支持 `system` 消息；部分兼容接口不接受 `developer`，因此 OpenAI Provider 使用前置 `system` 消息承载结构化提示。参考：https://developers.openai.com/api/reference/resources/chat
- Anthropic Prompt Caching 支持在 `system` 文本块上设置 `cache_control`，缓存前缀顺序为 `tools`、`system`、`messages`，并通过 `cache_creation_input_tokens`、`cache_read_input_tokens` 观测缓存。参考：https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching

## 需求覆盖
| 需求 | 架构 owner |
|------|------------|
| F1, F2 | `julycode.prompting.modules` 和 `PromptBuilder.build_stable_prompt()` |
| F3, F5, F6, F7 | `RuntimePromptContext`、`PromptBuilder.build_runtime_prompt()` |
| F4, F12, F13, F15 | `providers.openai`、`providers.anthropic`、`PromptCacheUsage` |
| F8 | `commands.py`、`agent.py`、运行时模式补充 |
| F9, F10 | 固定工具模块和 `tools/builtin.py` 工具描述 |
| F11 | `ChatSession.build_request()` 和 Provider payload 构造顺序 |
| F14 | `manual-scenarios.md`、README 和端到端验收 |
