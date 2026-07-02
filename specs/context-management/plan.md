# MewCode 上下文管理 Plan

## 架构概览
上下文管理作为独立子系统接入 Agent Loop 和 TUI 命令层。核心入口是 `ContextManager`：每次模型请求前，Agent Loop 把当前会话、候选工具、运行时提示构造函数和模型供应商交给它；它先执行轻量预防，再按预算决定是否执行重量兜底，最后用最新上下文状态重新构造运行时提示，返回可发送的 `ChatRequest` 和压缩报告。

轻量预防由 `ToolResultCompactor` 负责。它只处理 `role="tool"` 的消息，先按单条工具结果阈值外置保存，再按同一工具轮次的合计阈值从大到小外置保存。外置内容由 `ContextStore` 写入项目目录下 `.mewcode/context/<session_id>/...`，确保后续 `read_file` 可以重新读取。

重量兜底由 `HistorySummarizer` 和 `ConversationSegmenter` 配合完成。`ConversationSegmenter` 按协议安全边界切分历史，避免截断 assistant 工具调用和对应 tool 结果；`HistorySummarizer` 用无工具的 LLM 请求生成正式摘要，只保存 `<final_summary>` 内容，丢弃草稿。压缩后，早期消息从 `ChatSession.messages` 移除，正式摘要和边界提示存入 `ChatSession.context_state`，并通过运行时系统提示注入后续请求。

Token 估算由 `TokenEstimator` 负责。它用最近一次供应商返回的 input tokens 作为锚点，记录当时请求的字符足迹；之后估算当前请求时，只对字符足迹增量按近似比例换算。没有 usage 时退化为全量字符估算。自动压缩使用 13K 安全余量，`/compact` 使用 3K 安全余量。

`/compact` 在命令解析层成为独立命令，不进入普通 Agent Loop。TUI 收到该命令后直接调用同一个 `ContextManager.manual_compact()`，显示压缩报告；如果历史太短或不需要压缩，显示 no-op 原因。

## 核心数据结构

### ContextConfig
```python
@dataclass(frozen=True)
class ContextConfig:
    enabled: bool = True
    window_tokens: int = 128_000
    single_tool_result_tokens: int = 4_000
    turn_tool_result_tokens: int = 8_000
    tool_preview_chars: int = 2_000
    recent_tokens: int = 10_000
    min_recent_messages: int = 5
    auto_reserve_tokens: int = 13_000
    manual_reserve_tokens: int = 3_000
    summary_failure_limit: int = 3
    chars_per_token: float = 4.0
    store_dir: str = ".mewcode/context"
```
挂到 `AppConfig.context`。`window_tokens` 表示模型上下文窗口；可用输入预算按 `window_tokens - max_tokens - reserve_tokens` 计算。

### ContextState
```python
@dataclass
class ContextState:
    session_id: str
    summary: ContextSummary | None = None
    token_anchor: TokenAnchor | None = None
    consecutive_summary_failures: int = 0
    compacted_tool_paths: tuple[str, ...] = ()
```
挂到 `ChatSession.context_state`，保存当前运行期上下文压缩状态。

### ContextSummary
```python
@dataclass(frozen=True)
class ContextSummary:
    content: str
    boundary_notice: str
    created_at: str
    source_message_count: int
    kept_message_count: int
    external_paths: tuple[str, ...] = ()
```
`content` 是正式摘要；`boundary_notice` 固定提醒模型不要把摘要当完整事实来源，需要细节时重新读取文件或工具结果路径。

### ContextExternalRef
```python
@dataclass(frozen=True)
class ContextExternalRef:
    path: str
    original_chars: int
    estimated_tokens: int
    preview: str
```
表示一个被外置保存的工具结果。`path` 是项目相对路径，可直接交给 `read_file`。

### TokenAnchor
```python
@dataclass(frozen=True)
class TokenAnchor:
    input_tokens: int
    footprint_chars: int
```
记录最近一次真实 API usage 和对应请求字符足迹。

### RequestFootprint
```python
@dataclass(frozen=True)
class RequestFootprint:
    chars: int
    estimated_tokens: int
```
表示一次候选请求的估算规模。字符足迹包含系统提示、运行时补充、会话消息、工具描述和 tool call 参数。

### ContextCompactionReport
```python
@dataclass(frozen=True)
class ContextCompactionReport:
    mode: Literal["auto", "manual"]
    light_compacted: bool
    heavy_compacted: bool
    externalized_paths: tuple[str, ...]
    kept_message_count: int
    summarized_message_count: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    message: str
```
供 TUI、测试和 Agent 事件流展示压缩结果。

### ToolCompactionResult
```python
@dataclass(frozen=True)
class ToolCompactionResult:
    changed: bool
    external_refs: tuple[ContextExternalRef, ...]
```
表示轻量预防是否改写了工具结果，以及生成了哪些外置引用。

### PreparedChatRequest
```python
@dataclass(frozen=True)
class PreparedChatRequest:
    request: ChatRequest
    footprint: RequestFootprint
    report: ContextCompactionReport | None = None
```
Agent Loop 使用 `request` 调模型；模型返回 usage 后，把 `footprint` 回传给 `ContextManager.record_usage()`。

### ContextLimitError
```python
class ContextLimitError(MewCodeError):
    report: ContextCompactionReport | None
```
当摘要熔断或压缩后仍明显超预算时抛出。Agent Loop 将它转换成 `stopped(context_limit)` 事件。

### CompactCommand
```python
@dataclass(frozen=True)
class CompactCommand:
    visible_text: str = "/compact"
```
`parse_agent_command()` 新增返回类型。它不是 `AgentCommand`，不会追加成普通用户任务。

## 核心接口

### ContextManager
```python
class ContextManager:
    def __init__(
        self,
        config: ContextConfig,
        cwd: Path,
        max_output_tokens: int,
        estimator: TokenEstimator | None = None,
        store: ContextStore | None = None,
    ) -> None: ...

    async def prepare_request(
        self,
        *,
        session: ChatSession,
        provider: LLMProvider,
        tools: Sequence[ToolSpec],
        prompt_factory: Callable[[], PromptBundle],
        mode: Literal["auto", "manual"] = "auto",
    ) -> PreparedChatRequest: ...

    async def manual_compact(
        self,
        *,
        session: ChatSession,
        provider: LLMProvider,
    ) -> ContextCompactionReport: ...

    def record_usage(self, usage: TokenUsage | None, footprint: RequestFootprint) -> None: ...
```
`prepare_request()` 是自动路径；`manual_compact()` 是 `/compact` 路径。二者共享轻量预防、历史选择、摘要生成、熔断和报告逻辑。`prompt_factory` 必须读取当前 `session.context_state.summary`，这样重量兜底成功后可重新生成包含新摘要和边界提示的最终 `PromptBundle`。

### TokenEstimator
```python
class TokenEstimator:
    def request_footprint(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        prompt: PromptBundle | None,
    ) -> RequestFootprint: ...

    def estimate_from_anchor(
        self,
        footprint: RequestFootprint,
        anchor: TokenAnchor | None,
    ) -> int: ...

    def estimate_message(self, message: ChatMessage) -> int: ...
```
`request_footprint()` 用稳定序列化计算字符足迹；`estimate_from_anchor()` 有锚点时计算 `anchor.input_tokens + (current_chars - anchor.footprint_chars) / chars_per_token`，无锚点时直接用全量字符估算。

### ToolResultCompactor
```python
class ToolResultCompactor:
    def compact(self, session: ChatSession) -> ToolCompactionResult: ...
```
扫描 `ChatSession.messages` 中的工具结果。已包含 `mewcode_externalized=true` 的工具结果不会重复外置。

### ContextStore
```python
class ContextStore:
    def write_tool_result(
        self,
        *,
        session_id: str,
        message: ChatMessage,
        estimated_tokens: int,
    ) -> ContextExternalRef: ...
```
写入 JSON 文件，返回项目相对路径。文件内容保存原始工具消息、工具调用标识、错误标记、字符数、估算 token 和创建时间。

### ConversationSegmenter
```python
@dataclass(frozen=True)
class ConversationSegment:
    messages: tuple[ChatMessage, ...]
    estimated_tokens: int

class ConversationSegmenter:
    def split(self, messages: Sequence[ChatMessage]) -> tuple[ConversationSegment, ...]: ...

    def select_recent(
        self,
        segments: Sequence[ConversationSegment],
        *,
        target_tokens: int,
        min_messages: int,
    ) -> tuple[tuple[ConversationSegment, ...], tuple[ConversationSegment, ...]]: ...
```
`split()` 把 assistant 工具调用及其后续 tool 结果视为不可拆分段，避免 Provider 收到无效工具历史。

### HistorySummarizer
```python
class HistorySummarizer:
    async def summarize(
        self,
        *,
        provider: LLMProvider,
        previous_summary: ContextSummary | None,
        messages: Sequence[ChatMessage],
        external_paths: Sequence[str],
    ) -> ContextSummary: ...
```
构造无工具 `ChatRequest(tools=())`。摘要提示要求先写 `<analysis_draft>`，再写 `<final_summary>`；只解析并保存 `<final_summary>`。缺少正式摘要标签、供应商报错或返回工具调用都算摘要失败。

## 模块设计

### `mewcode.context.models`
**职责：** 定义 `ContextConfig`、`ContextState`、`ContextSummary`、`TokenAnchor`、`ContextCompactionReport` 等数据结构。  
**对外接口：** 上述 dataclass。  
**依赖：** 标准库 dataclasses、typing。

### `mewcode.context.estimator`
**职责：** 近似估算消息、提示、工具描述和完整请求的 token 规模，并维护锚点估算算法。  
**对外接口：** `TokenEstimator`。  
**依赖：** `ChatMessage`、`ToolSpec`、`PromptBundle`。

### `mewcode.context.store`
**职责：** 在项目目录内创建 `.mewcode/context/<session_id>/tool-results/`，保存完整工具结果，返回可读相对路径。  
**对外接口：** `ContextStore.write_tool_result()`。  
**依赖：** `Path`、`ChatMessage`。

### `mewcode.context.compactor`
**职责：** 执行轻量预防，识别超大工具结果并替换为预览 JSON。  
**对外接口：** `ToolResultCompactor.compact(session)`。  
**依赖：** `TokenEstimator`、`ContextStore`、`ChatSession`。

### `mewcode.context.segmenter`
**职责：** 按协议安全边界切分历史，并选择近期原文保留范围。  
**对外接口：** `ConversationSegmenter.split()`、`select_recent()`。  
**依赖：** `ChatMessage`、`TokenEstimator`。

### `mewcode.context.summarizer`
**职责：** 生成结构化摘要，解析正式摘要并丢弃草稿。  
**对外接口：** `HistorySummarizer.summarize()`。  
**依赖：** `LLMProvider`、`ChatRequest`、`ChatMessage`、`ContextSummary`。

### `mewcode.context.manager`
**职责：** 编排轻量预防、预算判断、重量兜底、熔断、报告和 usage 锚点更新。  
**对外接口：** `ContextManager.prepare_request()`、`manual_compact()`、`record_usage()`。  
**依赖：** context 子模块、`ChatSession`、`PromptBundle`、`LLMProvider`、`Callable`。

### `mewcode.session`
**职责：** 继续保存当前运行期消息和待执行计划，并新增上下文状态。  
**对外接口：** 新增 `context_state` 字段、`replace_messages(messages)`、`set_context_summary(summary)`。  
**依赖：** `ContextState`。为避免循环导入，运行期导入放在 `TYPE_CHECKING` 或模型模块保持轻量。

### `mewcode.prompting`
**职责：** 在运行时补充中注入上下文摘要和边界提示。  
**对外接口：** `RuntimePromptContext` 新增 `context_summary` 字段；`PromptBuilder.build_runtime_prompt()` 在存在摘要时追加 `<mewcode_context_summary>` 块。  
**依赖：** `ContextSummary`。

### `mewcode.commands`
**职责：** 解析 `/compact`。  
**对外接口：** `parse_agent_command()` 返回 `CompactCommand`。  
**依赖：** `ChatSession`。

### `mewcode.agent`
**职责：** 在每次 Provider 请求前调用 `ContextManager.prepare_request()`；请求完成后用 usage 更新锚点。  
**对外接口：** `AgentLoopRunner` 新增可选 `context_manager` 参数；`TurnEventType` 增加 `context_compacted`；`AgentStopReason` 增加 `context_limit`。  
**依赖：** `ContextManager`、`ContextCompactionReport`。

### `mewcode.tui.app`
**职责：** 共享同一个 `ContextManager` 给 TUI 手动压缩和 Agent Loop 自动压缩；显示 `/compact` 报告和自动压缩状态。  
**对外接口：** `MewCodeApp` 新增可选 `context_manager` 参数。  
**依赖：** `CompactCommand`、`ContextManager`。

### `mewcode.config`
**职责：** 解析 `context:` 配置并提供默认值。  
**对外接口：** `AppConfig.context: ContextConfig`、`_parse_context()`。  
**依赖：** `ContextConfig`。

## 模块交互
普通 Agent Loop 请求：

```text
用户输入
  → parse_agent_command()
  → AgentLoopRunner.run()
  → session.append_user_message()
  → 构造 ToolPolicy、allowed_tools、prompt_factory
  → ContextManager.prepare_request(session, provider, tools, prompt_factory, mode="auto")
      → ToolResultCompactor.compact()
      → prompt_factory() 生成当前候选 PromptBundle
      → TokenEstimator 估算候选请求
      → 若超过 window - max_tokens - 13K：
          → ConversationSegmenter 选择早期历史和近期原文
          → HistorySummarizer 用 tools=() 生成正式摘要
          → session.messages 替换为近期原文
          → session.context_state.summary 保存摘要和边界提示
          → prompt_factory() 重新生成包含新摘要的 PromptBundle
      → 返回 PreparedChatRequest
  → provider.stream_chat(prepared.request)
  → StreamCollector 聚合 usage
  → ContextManager.record_usage(usage, prepared.footprint)
  → 工具调用、工具结果回灌和停止条件沿用现有流程
```

`/compact` 手动路径：

```text
用户输入 /compact
  → parse_agent_command() 返回 CompactCommand
  → TUI 调用 ContextManager.manual_compact(session, provider)
      → 先轻量预防
      → 用 3K 安全余量和 force 策略尝试重量兜底
      → 返回 ContextCompactionReport
  → TUI 显示报告，不启动普通 Agent Loop
```

摘要失败路径：

```text
HistorySummarizer 失败
  → session.context_state.consecutive_summary_failures += 1
  → 未到 3 次且估算仍可安全发送：返回警告报告并继续
  → 达到 3 次或估算可能溢出：抛出 ContextLimitError
  → AgentLoopRunner 发出 stopped(context_limit) 并恢复输入能力
```

## 文件组织
```text
src/mewcode/
├── context/
│   ├── __init__.py                 — 导出上下文管理公开类型
│   ├── models.py                   — ContextConfig、ContextState、报告和摘要模型
│   ├── estimator.py                — Token 近似估算和 usage 锚点逻辑
│   ├── store.py                    — 外置工具结果落盘
│   ├── compactor.py                — 轻量工具结果压缩
│   ├── segmenter.py                — 历史消息安全切段和近期保留选择
│   ├── summarizer.py               — 无工具摘要请求和正式摘要解析
│   └── manager.py                  — 请求前上下文管理编排
├── config.py                       — 解析 context 配置
├── session.py                      — 挂载 ContextState 并支持替换消息
├── prompting/
│   ├── base.py                     — RuntimePromptContext 增加 context_summary
│   └── builder.py                  — 注入上下文摘要和边界提示
├── commands.py                     — 解析 /compact
├── agent.py                        — 请求前调用 ContextManager，记录 usage 锚点
├── tui/app.py                      — 手动压缩入口和报告展示
└── cli.py                          — 创建共享 ContextManager
tests/
├── test_context_estimator.py       — 估算和 usage 锚点
├── test_context_compactor.py       — 工具结果外置保存和预览
├── test_context_summarizer.py      — 摘要 prompt、禁工具和正式摘要解析
├── test_context_manager.py         — 自动/手动压缩、熔断、消息保留
├── test_commands.py                — /compact 解析
├── test_agent.py                   — Agent Loop 请求前压缩和 context_limit 停止
├── test_tui_smoke.py               — /compact TUI 报告和既有行为不回退
└── e2e_mock_openai_server.py       — 支持摘要响应和大工具输出场景
.gitignore                         — 忽略 .mewcode/context/
README.md                          — 说明 context 配置、/compact 和外置路径
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 压缩入口 | Agent Loop 请求前统一调用 `ContextManager` | 满足每次请求前执行，且不污染 Provider 协议层。 |
| 外置目录 | 项目内 `.mewcode/context/<session_id>/` | 符合读取工具的项目目录边界，后续模型可用 `read_file` 取回。 |
| 外置格式 | JSON 保存原始工具消息和元数据 | 对模型和测试都可读，且能保留成功/失败、调用标识和错误信息。 |
| 轻量压缩对象 | 只处理 tool 消息 | 符合 Token 大头优先处理工具结果，并避免改写用户原始消息。 |
| 历史切分 | 按工具调用段不可拆分 | 防止 Provider 收到 assistant tool call 缺少对应 tool result 的无效历史。 |
| 摘要承载 | 摘要存入 `ContextState`，通过运行时系统提示注入 | 不伪造用户消息，也兼容 Anthropic 对 system 的限制。 |
| 边界提示 | 与摘要一起作为运行时系统补充注入 | 提示优先级高，且不会污染用户原文。 |
| 摘要请求 | 使用同一 `LLMProvider`，`tools=()` | 保持供应商无关，并硬性禁止工具调用。 |
| 草稿处理 | 要求 `<analysis_draft>` 和 `<final_summary>`，只保存 final | 满足“先写草稿再写正式摘要，草稿用完就丢”。 |
| Token 估算 | usage 锚点 + 字符增量估算 | 符合本阶段不做精确 tokenizer 的范围，成本低且可测试。 |
| 手动压缩 | `/compact` 独立命令，不进普通 Agent Loop | 避免模型把命令当任务执行，用户能直接看到压缩结果。 |
| 熔断 | 连续摘要失败计数存入 `ContextState` | 同一会话内避免自动摘要死循环，成功摘要后清零。 |
| 配置 | 新增 `context:` 配置块并提供保守默认值 | 用户可按模型窗口调节，不要求本阶段自动发现模型上下文。 |

## 需求覆盖

| 需求 | 设计覆盖 |
|------|----------|
| F1 | `ContextManager.prepare_request()` 在 Agent Loop 每次请求前执行轻量和重量判断 |
| F2-F6 | `ToolResultCompactor`、`ContextStore`、外置预览 JSON |
| F7 | `TokenEstimator`、`TokenAnchor`、`record_usage()` |
| F8-F10 | 自动/手动预算、`ConversationSegmenter.select_recent()` |
| F11-F14 | `HistorySummarizer`、`ContextSummary`、运行时摘要块和边界提示 |
| F15 | `CompactCommand`、`manual_compact()`、TUI 报告 |
| F16-F18 | `consecutive_summary_failures`、`ContextLimitError`、成功摘要清零 |
| F19 | Agent/TUI 只在请求前接入，工具、权限、Plan Mode 流程保留 |
| F20 | `.mewcode/context/...` 项目相对路径，可被 `read_file` 读取 |
