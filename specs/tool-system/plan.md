# JulyCode 工具系统 Plan

## 架构概览
JulyCode 工具系统采用四层结构：工具层负责定义、登记、校验和执行本地工具；Provider 层负责把统一工具描述和工具消息转换成 OpenAI / Anthropic 各自协议，并把流式工具调用解析回统一消息；编排层负责一次用户请求内的“模型请求 → 工具执行 → 工具结果回灌 → 最终模型回复”；TUI 层只消费统一 turn 事件并更新界面。

工具层不依赖任何供应商协议。每个工具以统一 `Tool` 接口暴露名称、描述、参数 JSON Schema 和异步执行方法。工具执行统一由 `ToolExecutor` 包裹，负责参数校验、超时、异常捕获和结构化结果生成。六个内置工具集中注册到 `ToolRegistry`，调用方按名称查找，不直接实例化具体工具。

Provider 层在现有 `stream_chat()` 基础上扩展工具能力。`ChatRequest` 携带可用工具列表，OpenAI Provider 将工具转为 Chat Completions 的 `tools` function 格式，并解析流式 `delta.tool_calls` 参数碎片；Anthropic Provider 将工具转为 Messages API 的顶层 `tools` 格式，并解析 `tool_use` 内容块和 `input_json_delta` 参数碎片。两者最终都产出带 `ToolCall` 的统一 `ChatMessage`。

编排层由 `ToolAwareTurnRunner` 负责，替代 TUI 直接遍历 Provider 的模式。它在第一轮模型回复中收集工具调用；若没有工具调用，则保持现有纯文本流式体验；若出现一个工具调用，则执行工具、把结果追加进会话历史，再发起第二次模型请求生成最终回复；若第二次模型请求再次要求工具调用，则不再执行，向 TUI 发出“不支持连续工具调用”的事件。

TUI 层继续负责输入、消息渲染、状态栏和错误恢复。它不理解 OpenAI / Anthropic 原始事件，也不直接调用工具，只展示工具开始、工具完成、工具失败和最终回复。这样工具系统可以在 CLI、测试和未来非 TUI 前端中复用。

## 核心数据结构

### ToolSpec
```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    timeout_seconds: float = 10.0
```

描述一个可暴露给模型的工具。`name` 必须满足 OpenAI 和 Anthropic 都接受的简单标识符约束；`parameters_schema` 使用 JSON Schema 子集描述参数。

### ToolContext
```python
@dataclass(frozen=True)
class ToolContext:
    cwd: Path
    max_output_chars: int = 20000
```

表示工具执行时的本地上下文。第一版只包含启动目录和结果最大字符数，不引入权限、沙箱或用户确认。

### ToolResult
```python
@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    tool_name: str
    success: bool
    data: dict[str, Any]
    error_type: str | None = None
    error: str | None = None
    elapsed_ms: int | None = None

    def to_model_content(self) -> str: ...
```

表示一次工具执行结果。成功时 `data` 存放高信号结果；失败时 `error_type` 和 `error` 描述原因。`to_model_content()` 负责生成返回给模型的 JSON 字符串。

### ToolCall
```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""
    parse_error: str | None = None
```

表示模型请求的一次工具调用。Provider 在流式解析期间拼接 `raw_arguments`，能解析为 JSON 对象时填充 `arguments`；解析失败时保留 `parse_error`，由执行层返回结构化失败结果。

### Tool
```python
class Tool(Protocol):
    spec: ToolSpec

    async def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolContext,
    ) -> Mapping[str, Any]:
        ...
```

所有内置工具实现同一接口。工具自身只处理业务行为，成功时返回可 JSON 序列化的数据；业务失败时抛出 `ToolExecutionError`。未知工具、参数校验、超时、异常包装和 Provider 消息格式都由执行器处理。

### ToolRegistry
```python
class ToolRegistry:
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def list(self) -> tuple[Tool, ...]: ...
    def specs(self) -> tuple[ToolSpec, ...]: ...

def create_default_registry() -> ToolRegistry: ...
```

集中登记和查询工具。`create_default_registry()` 注册六个内置工具。

### ToolExecutor
```python
class ToolExecutor:
    def __init__(self, registry: ToolRegistry, context: ToolContext) -> None: ...

    async def execute(self, call: ToolCall) -> ToolResult: ...
```

统一执行入口。它负责未知工具检查、参数 JSON 解析失败处理、参数 Schema 校验、`asyncio.wait_for()` 超时控制、捕获 `ToolExecutionError` 和未预期异常、耗时记录、结果脱敏，并包装成 `ToolResult`。

### ChatMessage
```python
ChatRole = Literal["user", "assistant", "tool"]

@dataclass
class ChatMessage:
    role: ChatRole
    content: str = ""
    thinking: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_result_is_error: bool = False
    provider_payload: dict[str, Any] | None = None
```

扩展当前会话消息。普通对话继续使用 `user` 和 `assistant`；工具结果使用内部 `tool` 角色。OpenAI Provider 将 `tool` 消息转成 `role: tool`；Anthropic Provider 将同一条内部消息转成 `role: user` 且内容为 `tool_result` 块。

### ChatRequest
```python
@dataclass(frozen=True)
class ChatRequest:
    messages: Sequence[ChatMessage]
    tools: Sequence[ToolSpec] = ()
```

一次模型请求。`tools` 为空时保持纯对话；非空时由具体 Provider 转为供应商工具定义。

### StreamEvent
```python
StreamEventType = Literal[
    "message_start",
    "text_delta",
    "thinking_delta",
    "tool_call_delta",
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
    error: str | None = None
```

Provider 对编排层输出的统一流事件。`tool_call_delta` 用于表示工具参数碎片到达；`message_done` 携带最终聚合后的 `ChatMessage`，其中可能包含 `tool_calls`。

### TurnEvent
```python
TurnEventType = Literal[
    "text_delta",
    "thinking_delta",
    "tool_started",
    "tool_finished",
    "message_done",
    "tool_limit_reached",
    "error",
]

@dataclass(frozen=True)
class TurnEvent:
    type: TurnEventType
    text: str = ""
    message: ChatMessage | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    error: str | None = None
```

编排层对 TUI 输出的事件。TUI 只根据这些事件更新消息正文、thinking 区、工具状态和错误提示。

### ToolAwareTurnRunner
```python
class ToolAwareTurnRunner:
    def __init__(
        self,
        session: ChatSession,
        provider: LLMProvider,
        registry: ToolRegistry,
        executor: ToolExecutor,
    ) -> None: ...

    async def run(self, user_text: str) -> AsyncIterator[TurnEvent]: ...
```

一次用户输入的编排器。它持有会话、Provider、注册中心和执行器，保证每次用户请求最多执行一轮工具。

## 内置工具设计

### read_file
**参数：**
```json
{
  "type": "object",
  "properties": {
    "path": {"type": "string", "description": "要读取的文件路径"}
  },
  "required": ["path"],
  "additionalProperties": false
}
```
**行为：** 按 UTF-8 读取文本文件，返回 `path` 和 `content`。路径不存在、不是文件或无法解码时返回失败。

### write_file
**参数：**
```json
{
  "type": "object",
  "properties": {
    "path": {"type": "string"},
    "content": {"type": "string"}
  },
  "required": ["path", "content"],
  "additionalProperties": false
}
```
**行为：** 创建父目录并按 UTF-8 写入内容。写入失败时返回失败。

### edit_file
**参数：**
```json
{
  "type": "object",
  "properties": {
    "path": {"type": "string"},
    "old_text": {"type": "string"},
    "new_text": {"type": "string"}
  },
  "required": ["path", "old_text", "new_text"],
  "additionalProperties": false
}
```
**行为：** 读取文件后统计 `old_text` 出现次数；恰好一次时替换并写回；零次或多次时不写文件并返回失败。

### run_command
**参数：**
```json
{
  "type": "object",
  "properties": {
    "command": {"type": "string"},
    "timeout_seconds": {"type": "number"}
  },
  "required": ["command"],
  "additionalProperties": false
}
```
**行为：** 在 `ToolContext.cwd` 下执行本地命令，返回 `exit_code`、`stdout`、`stderr` 和是否截断。超时会终止进程并返回失败。

### find_files
**参数：**
```json
{
  "type": "object",
  "properties": {
    "pattern": {"type": "string", "description": "glob 模式"},
    "max_results": {"type": "number"}
  },
  "required": ["pattern"],
  "additionalProperties": false
}
```
**行为：** 以启动目录为基准按 glob 模式查找文件，返回相对路径列表。没有匹配时返回成功且列表为空。

### search_code
**参数：**
```json
{
  "type": "object",
  "properties": {
    "pattern": {"type": "string", "description": "要搜索的文本或正则模式"},
    "path": {"type": "string", "description": "可选搜索起点"},
    "glob": {"type": "string", "description": "可选文件 glob 过滤"},
    "max_results": {"type": "number"}
  },
  "required": ["pattern"],
  "additionalProperties": false
}
```
**行为：** 优先使用 `rg` 搜索内容并返回 `path`、`line`、`column`、`text`；若环境没有 `rg`，使用 Python 递归文本扫描兜底。没有匹配时返回成功且列表为空。

## 模块设计

### `julycode.tools.base`
**职责：** 定义 `ToolSpec`、`ToolContext`、`ToolResult`、`ToolCall`、`Tool` 协议和工具异常类型。  
**对外接口：** 上述数据结构和 `ToolExecutionError`。  
**依赖：** 标准库类型系统、`pathlib`。

### `julycode.tools.validation`
**职责：** 对内置工具需要的 JSON Schema 子集做运行时校验。  
**对外接口：**
```python
def validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> list[str]
```
**依赖：** 标准库。  
**说明：** 第一版支持 `object`、`properties`、`required`、`additionalProperties`、基础类型和 `enum`，不引入完整 JSON Schema 依赖。

### `julycode.tools.registry`
**职责：** 注册、查询和列出工具。  
**对外接口：** `ToolRegistry`、`create_default_registry()`。  
**依赖：** `julycode.tools.base`、`julycode.tools.builtin`。

### `julycode.tools.executor`
**职责：** 执行工具调用并统一处理超时、未知工具、参数错误和异常，把工具返回数据或工具异常包装成 `ToolResult`。  
**对外接口：** `ToolExecutor.execute(call)`。  
**依赖：** `asyncio`、`time`、`julycode.tools.base`、`julycode.tools.registry`、`julycode.tools.validation`、`julycode.errors`。

### `julycode.tools.builtin`
**职责：** 实现六个核心工具。  
**对外接口：** `ReadFileTool`、`WriteFileTool`、`EditFileTool`、`RunCommandTool`、`FindFilesTool`、`SearchCodeTool`。  
**依赖：** `asyncio`、`pathlib`、`subprocess`、`shutil`、`re`、`julycode.tools.base`。

### `julycode.providers.base`
**职责：** 扩展统一 Provider 模型，支持工具请求和工具调用事件。  
**对外接口：** `ChatMessage`、`ChatRequest`、`StreamEvent`、`LLMProvider`、`ToolCall` 的导入或重导出。  
**依赖：** `julycode.tools.base`。

### `julycode.providers.openai`
**职责：** 将统一工具描述、消息历史和工具结果转换为 OpenAI Chat Completions 协议，并解析流式工具调用。  
**对外接口：** `OpenAIProvider.stream_chat(request)`。  
**关键行为：**
- 请求体在 `request.tools` 非空时加入 `tools`，每个工具使用 `{"type": "function", "function": {"name", "description", "parameters"}}`。
- 助手工具调用消息输出为 `role: assistant` 且包含 `tool_calls`。
- 工具结果消息输出为 `role: tool`、`tool_call_id` 和 JSON 字符串 `content`。
- 流式解析时按 `delta.tool_calls[*].index` 聚合 `id`、`function.name` 和 `function.arguments`。
**依赖：** `httpx`、`json`、`julycode.providers.sse`、`julycode.tools.base`。

### `julycode.providers.anthropic`
**职责：** 将统一工具描述、消息历史和工具结果转换为 Anthropic Messages 协议，并解析流式工具调用。  
**对外接口：** `AnthropicProvider.stream_chat(request)`。  
**关键行为：**
- 请求体在 `request.tools` 非空时加入顶层 `tools`，每个工具使用 `{"name", "description", "input_schema"}`。
- 助手工具调用消息输出为 `role: assistant` 且 content 包含 `text` 和 `tool_use` 块。
- 内部 `tool` 消息输出为 `role: user` 且 content 首项为 `tool_result` 块；失败结果设置 `is_error: true`。
- 流式解析时在 `content_block_start` 捕获 `tool_use` 的 `id` 和 `name`，在 `input_json_delta` 中拼接 `partial_json`，在 `content_block_stop` 后生成统一 `ToolCall`。
**依赖：** `httpx`、`json`、`julycode.providers.sse`、`julycode.tools.base`。

### `julycode.agent`
**职责：** 编排一次用户请求内的模型调用、工具执行和结果回灌。  
**对外接口：** `ToolAwareTurnRunner`、`TurnEvent`。  
**依赖：** `julycode.session`、`julycode.providers.base`、`julycode.tools.registry`、`julycode.tools.executor`。

### `julycode.session`
**职责：** 支持工具消息进入当前运行期历史。  
**对外接口：**
```python
class ChatSession:
    def append_user_message(self, text: str) -> ChatMessage: ...
    def append_assistant_message(self, message: ChatMessage) -> None: ...
    def append_tool_result(self, result: ToolResult) -> ChatMessage: ...
    def build_request(self, tools: Sequence[ToolSpec] = ()) -> ChatRequest: ...
```
**依赖：** `julycode.providers.base`、`julycode.tools.base`。

### `julycode.tui.widgets`
**职责：** 展示工具调用状态。  
**对外接口：** 在现有组件基础上新增或扩展 `ToolStatusView`，用于展示工具名称、成功/失败和简短结果。  
**依赖：** `textual`、`julycode.tools.base`。

### `julycode.tui.app`
**职责：** 用 `ToolAwareTurnRunner` 替代直接调用 Provider，并把 turn 事件映射到界面。  
**对外接口：**
```python
class JulyCodeApp(App[None]):
    def __init__(
        self,
        session: ChatSession,
        provider: LLMProvider,
        config: AppConfig,
        registry: ToolRegistry,
        executor: ToolExecutor,
    ) -> None: ...
```
**依赖：** `textual`、`julycode.agent`、`julycode.tools`。

### `julycode.cli`
**职责：** 启动时创建默认工具注册中心和执行器，并传入 TUI。  
**对外接口：** `main(argv=None) -> int`。  
**依赖：** `julycode.tools.registry`、`julycode.tools.executor`。

## 模块交互
1. 用户运行 `julycode`。
2. `julycode.cli` 加载配置，创建 Provider、`ChatSession`、默认 `ToolRegistry`、`ToolExecutor` 和 `JulyCodeApp`。
3. 用户在 TUI 提交文本。
4. TUI 展示用户消息，并调用 `ToolAwareTurnRunner.run(user_text)`。
5. Runner 将用户消息写入 `ChatSession`，调用 `build_request(tools=registry.specs())`。
6. Provider 发起第一轮流式模型请求，并把文本、thinking 和工具调用碎片转成统一 `StreamEvent`。
7. Runner 将文本和 thinking 事件透传给 TUI；收到第一轮 `message_done` 后检查 `message.tool_calls`。
8. 如果没有工具调用，Runner 将助手消息写入会话，发出 `message_done`，本轮结束。
9. 如果有一个工具调用，Runner 将助手工具调用消息写入会话，发出 `tool_started`，调用 `ToolExecutor.execute()`。
10. Executor 找到工具、校验参数、设置超时、执行工具并返回 `ToolResult`；失败也返回结构化结果。
11. Runner 将工具结果写入会话，发出 `tool_finished`。
12. Runner 再次调用 Provider，让模型基于包含工具结果的历史生成回复。
13. 第二轮模型回复如果是纯文本，Runner 将助手最终消息写入会话并结束。
14. 第二轮模型回复如果再次包含工具调用，Runner 不执行工具，发出 `tool_limit_reached`，并在会话中追加一条说明本阶段不支持连续工具调用的助手文本。
15. 任意层出现错误时，Runner 或 TUI 输出脱敏后的错误事件，TUI 恢复输入区。

## 文件组织
```text
julycode/
├── README.md                              — 更新范围说明，描述工具系统基本能力
├── src/
│   └── julycode/
│       ├── agent.py                       — 一次工具调用链路编排与 TurnEvent
│       ├── cli.py                         — 创建工具注册中心和执行器
│       ├── session.py                     — 会话历史支持工具调用和工具结果
│       ├── providers/
│       │   ├── base.py                    — 扩展消息、请求、事件结构
│       │   ├── openai.py                  — OpenAI 工具描述、流式工具调用和工具结果适配
│       │   └── anthropic.py               — Anthropic 工具描述、tool_use 流式解析和 tool_result 适配
│       ├── tools/
│       │   ├── __init__.py                — 工具包导出
│       │   ├── base.py                    — Tool 接口、ToolSpec、ToolCall、ToolResult
│       │   ├── validation.py              — 参数 Schema 子集校验
│       │   ├── registry.py                — 工具注册中心和默认注册
│       │   ├── executor.py                — 超时、错误处理和统一执行入口
│       │   └── builtin.py                 — 六个核心工具实现
│       └── tui/
│           ├── app.py                     — 消费 TurnEvent，展示工具状态和最终回复
│           └── widgets.py                 — 工具状态视图
├── tests/
│   ├── test_tools.py                      — 工具注册、参数校验、六个工具行为和错误路径
│   ├── test_tool_executor.py              — 超时、未知工具、参数错误和异常包装
│   ├── test_agent.py                      — 一轮工具编排、结果回灌和连续工具拦截
│   ├── test_openai_provider.py            — OpenAI 工具请求、流式 tool_calls 聚合和 tool 消息
│   ├── test_anthropic_provider.py         — Anthropic tools、tool_use 聚合和 tool_result 消息
│   ├── test_session.py                    — 工具消息进入会话上下文
│   ├── test_tui_smoke.py                  — 工具状态展示、错误恢复、纯聊天兼容
│   └── e2e_mock_openai_server.py          — 增加工具调用模拟响应，用于 tmux 端到端
└── specs/
    └── tool-system/
        ├── spec.md                        — 已批准需求
        ├── plan.md                        — 本技术设计
        ├── task.md                        — 待生成任务拆解
        └── checklist.md                   — 待生成验收清单
```

## 需求覆盖
| 需求 | 架构归属 |
|------|----------|
| F1 | `julycode.tools.base` 定义统一 `Tool`、`ToolSpec` 和参数 Schema。 |
| F2 | `julycode.tools.builtin` 实现六个核心工具，`create_default_registry()` 默认注册。 |
| F3 | `ToolRegistry` 集中登记；OpenAI / Anthropic Provider 分别把 `ToolSpec` 转成供应商工具描述。 |
| F4 | Provider 流式解析 `delta.tool_calls` 和 `input_json_delta`，聚合成 `ToolCall`。 |
| F5 | `ToolAwareTurnRunner` 执行工具、追加工具结果，并发起第二次模型请求。 |
| F6 | `ToolAwareTurnRunner` 对第二次模型响应中的工具调用发出 `tool_limit_reached`，不再执行。 |
| F7 | `ToolExecutor` 统一处理超时、未知工具、参数错误、系统调用失败和文件错误。 |
| F8 | `ToolResult` 统一表达成功、失败、错误类型和可读错误。 |
| F9 | `ReadFileTool` 负责文件存在性、文件类型、读取和解码错误。 |
| F10 | `WriteFileTool` 负责创建父目录、覆盖写入和写入错误。 |
| F11 | `EditFileTool` 负责唯一匹配替换，零次或多次匹配不写文件。 |
| F12 | `RunCommandTool` 返回退出码、stdout、stderr，并处理超时和启动失败。 |
| F13 | `FindFilesTool` 返回匹配路径列表，空结果仍为成功。 |
| F14 | `SearchCodeTool` 返回路径、行列和匹配摘要，空结果仍为成功。 |
| F15 | Provider 工具解析兼容 OpenAI 和 Anthropic；无工具调用时继续输出文本增量。 |
| F16 | `ChatSession` 支持助手工具调用消息和工具结果消息进入运行期历史。 |
| F17 | `TurnEvent` 和 TUI 的工具状态视图展示工具名称、执行状态和最终回复。 |

## 技术决策
| 决策点 | 选择 | 理由 |
|--------|------|------|
| Provider API | 继续使用当前 OpenAI Chat Completions 和 Anthropic Messages API | 保持现有配置和测试结构稳定，不在本阶段迁移到新 API 面。 |
| 工具协议适配 | Provider 内部做协议转换，上层只看 `ToolSpec` / `ToolCall` / `ToolResult` | 隔离 OpenAI `tool_calls` 和 Anthropic `tool_use` / `tool_result` 差异。 |
| 工具结果角色 | 内部使用 `role="tool"`，Anthropic Provider 转成 `role="user"` + `tool_result` | 会话层保持统一结构，同时满足 Anthropic 对消息块顺序的要求。 |
| 工具调用数量 | 第一版每次用户请求最多执行一个工具调用 | 符合 spec 的一次工具调用链路边界，避免提前实现并行工具和复杂结果聚合。 |
| 第二轮工具调用 | 第二次模型响应仍允许暴露工具，但检测到工具调用后立即停止执行 | 可验证“模型再次请求工具时不继续执行”的需求，而不是简单禁用工具让情况不可观测。 |
| 参数校验 | 实现内置工具所需 JSON Schema 子集校验 | 避免新增依赖，同时覆盖六个核心工具的参数约束。 |
| 文件内容 | 第一版按 UTF-8 文本处理 | JulyCode 当前目标是代码助手，二进制文件处理不在 spec 范围内。 |
| 文件修改 | `old_text` 精确计数为 1 时才写回 | 满足唯一匹配要求，避免模型提供模糊上下文时误改多处。 |
| 命令执行 | 使用异步 subprocess，捕获 stdout/stderr，执行目录为启动目录 | 与 TUI 异步模型一致，避免阻塞界面。 |
| 搜索实现 | `search_code` 优先调用 `rg`，缺失时 Python 兜底 | 常见代码库搜索速度更好，同时保证没有 `rg` 时功能仍可用。 |
| 输出截断 | 工具结果超过 `max_output_chars` 时截断并标记 | 避免大文件或命令输出撑爆模型上下文。 |
| 安全策略 | 不加入权限确认、危险命令拦截或沙箱 | spec 明确本阶段不做这些能力，后续章节再设计。 |

## 官方协议依据
- OpenAI Function Calling 文档说明 Chat Completions 工具定义使用 `tools`，工具调用结果通过 `tool_calls`、`tool_call_id` 和 `role: tool` 回灌；流式工具调用需要按 `delta.tool_calls[*].index` 聚合参数碎片。
- Anthropic Tool Use 文档说明工具定义使用顶层 `tools`，模型返回 `tool_use` 内容块，工具结果以 `tool_result` 内容块放在紧随其后的 `user` 消息中；流式 `tool_use.input` 通过 `input_json_delta` 发送部分 JSON 字符串。
