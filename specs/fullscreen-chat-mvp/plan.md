# JulyCode 全屏对话 MVP Plan

## 架构概览
JulyCode 采用分层结构：CLI 启动层负责解析命令并启动应用；配置层负责读取、合并和校验 YAML；会话层维护当前运行期的消息历史；Provider 层把不同供应商协议转换成统一的流事件；TUI 层消费统一事件并更新全屏界面。各层单向依赖，TUI 不直接理解 OpenAI 或 Anthropic 的原始流式协议。

全屏界面使用 Textual 构建。界面包含顶部状态栏、消息滚动区、可折叠思考区、底部输入区和退出提示。用户提交输入后，界面创建一次异步生成任务，禁用输入区并显示生成状态；收到文本或思考增量时立即更新对应区域；请求结束后把助手回复写入当前会话历史并恢复输入。

Provider 层使用 `httpx.AsyncClient.stream()` 直接访问供应商 HTTP API，并用内部 SSE 解析器处理事件流。这样可以统一处理 `base_url`、错误响应、超时、认证头和流式事件，也便于以后新增 Provider。

配置层读取用户级 `~/.julycode/config.yaml`，再从启动目录向上查找首个 `.julycode.yaml` 作为项目级配置。两份配置按字段浅合并，项目级同名字段覆盖用户级字段。`api_key` 支持明文和 `${ENV_VAR}` 形式，错误信息统一走脱敏处理。

## 核心数据结构

### AppConfig
```python
@dataclass(frozen=True)
class AppConfig:
    protocol: Literal["openai", "anthropic"]
    model: str
    base_url: str
    api_key: str
    max_tokens: int = 4096
    timeout_seconds: float = 60.0
    thinking: ThinkingConfig | None = None
```

表示一次运行使用的完整 LLM 配置。前四个字段来自核心 YAML 字段；其余字段是可选运行参数，用于让 MVP 能完成 Claude extended thinking 和稳定请求控制。

### ThinkingConfig
```python
@dataclass(frozen=True)
class ThinkingConfig:
    enabled: bool
    type: Literal["enabled", "adaptive"] = "enabled"
    budget_tokens: int | None = 1024
    effort: Literal["low", "medium", "high"] | None = None
    display: Literal["summarized", "omitted"] = "summarized"
```

表示 Claude extended thinking 的可选配置。`display` 默认使用 `summarized`，因为本阶段需要在界面中展示可折叠思考内容；当返回为空或配置为 `omitted` 时，界面仍保留折叠区但显示无可见思考内容。

### ChatMessage
```python
@dataclass
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str
    thinking: str | None = None
    provider_payload: dict[str, Any] | None = None
```

表示当前会话内的一条消息。`content` 是用户可见文本；`thinking` 是可见的 Claude thinking 摘要；`provider_payload` 保存供应商需要的原始补充信息，例如 Anthropic thinking block 的签名，便于后续轮次保留兼容性。

### ChatRequest
```python
@dataclass(frozen=True)
class ChatRequest:
    messages: Sequence[ChatMessage]
```

表示一次模型调用的输入。当前阶段只包含运行期上下文消息，不包含工具、文件或外部状态。

### StreamEvent
```python
@dataclass(frozen=True)
class StreamEvent:
    type: Literal[
        "message_start",
        "text_delta",
        "thinking_delta",
        "message_done",
        "error",
    ]
    text: str = ""
    message: ChatMessage | None = None
    error: str | None = None
```

Provider 对 TUI 暴露的统一流式事件。OpenAI 的 `delta.content` 和 Anthropic 的 `text_delta` 都转换为 `text_delta`；Anthropic 的 `thinking_delta` 转换为 `thinking_delta`。

### Provider 接口
```python
class LLMProvider(Protocol):
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        ...
```

所有供应商适配器必须实现同一接口。调用方只关心统一事件流，不关心 HTTP endpoint、认证头、SSE 事件名或供应商 JSON 结构。

### SessionState
```python
@dataclass
class SessionState:
    messages: list[ChatMessage]
    is_generating: bool = False
    last_error: str | None = None
```

表示当前 JulyCode 进程内的对话状态。退出进程后不写入磁盘。

## 模块设计

### `julycode.cli`
**职责：** 提供 `julycode` 命令入口，加载配置，创建 Provider 和 TUI 应用。  
**对外接口：** `main(argv: Sequence[str] | None = None) -> int`。  
**依赖：** `julycode.config`、`julycode.providers`、`julycode.tui.app`。

### `julycode.config`
**职责：** 发现配置文件、读取 YAML、合并用户级和项目级配置、解析环境变量、校验必填字段、脱敏错误。  
**对外接口：**
```python
def load_config(cwd: Path | None = None) -> AppConfig
def discover_project_config(cwd: Path) -> Path | None
def resolve_api_key(raw_value: str) -> str
```
**依赖：** `yaml`、`pathlib`、`os`、`julycode.errors`。

### `julycode.providers.base`
**职责：** 定义统一 Provider 接口、请求结构、流事件和 Provider 错误模型。  
**对外接口：** `LLMProvider`、`ChatRequest`、`StreamEvent`、`ChatMessage`。  
**依赖：** 标准库类型系统。

### `julycode.providers.factory`
**职责：** 根据 `AppConfig.protocol` 创建具体 Provider；对未知协议返回可理解错误。  
**对外接口：**
```python
def create_provider(config: AppConfig) -> LLMProvider
```
**依赖：** `julycode.providers.openai`、`julycode.providers.anthropic`、`julycode.errors`。

### `julycode.providers.sse`
**职责：** 解析标准 SSE 文本流，支持 `event:`、`data:`、空行分隔和多行 `data:`；忽略注释行。  
**对外接口：**
```python
@dataclass(frozen=True)
class SSEEvent:
    event: str | None
    data: str

async def iter_sse_lines(response: httpx.Response) -> AsyncIterator[SSEEvent]
```
**依赖：** `httpx`。

### `julycode.providers.openai`
**职责：** 调用 OpenAI Chat Completions 流式接口，把 data-only SSE chunk 转换为统一 `StreamEvent`。  
**对外接口：** `OpenAIProvider(config: AppConfig)`。  
**请求规则：** 请求 `POST {base_url}/chat/completions`，认证头使用 `Authorization: Bearer <api_key>`，请求体包含 `model`、`messages`、`stream: true`。  
**流式解析：** 读取 `choices[0].delta.content` 作为 `text_delta`；遇到 `[DONE]` 或结束 chunk 后发出 `message_done`。  
**依赖：** `httpx`、`julycode.providers.sse`、`julycode.providers.base`。

### `julycode.providers.anthropic`
**职责：** 调用 Claude Messages 流式接口，把命名 SSE 事件转换为统一 `StreamEvent`，并处理 Claude extended thinking。  
**对外接口：** `AnthropicProvider(config: AppConfig)`。  
**请求规则：** 请求 `POST {base_url}/messages`，认证头使用 `x-api-key`、`anthropic-version: 2023-06-01`、`content-type: application/json`，请求体包含 `model`、`messages`、`max_tokens`、`stream: true`，并在配置开启时加入 `thinking`。  
**流式解析：** `text_delta` 转换为 `text_delta`；`thinking_delta` 转换为 `thinking_delta`；`signature_delta` 保存到 `provider_payload`；`message_stop` 后发出 `message_done`。未知事件忽略但不中断。  
**依赖：** `httpx`、`julycode.providers.sse`、`julycode.providers.base`。

### `julycode.session`
**职责：** 维护当前进程内的消息历史，封装提交用户消息、追加助手回复和记录错误。  
**对外接口：**
```python
class ChatSession:
    def append_user_message(self, text: str) -> ChatMessage
    def append_assistant_message(self, message: ChatMessage) -> None
    def build_request(self) -> ChatRequest
```
**依赖：** `julycode.providers.base`。

### `julycode.tui.app`
**职责：** Textual 应用主体，组织界面布局、键盘绑定、异步生成任务和状态更新。  
**对外接口：** `JulyCodeApp(session: ChatSession, provider: LLMProvider)`。  
**依赖：** `textual`、`julycode.session`、`julycode.providers.base`。

### `julycode.tui.widgets`
**职责：** 提供消息视图、可折叠思考区、输入区和状态栏组件。  
**对外接口：** `MessageList`、`MessageView`、`ThinkingPanel`、`Composer`、`StatusBar`。  
**依赖：** `textual`。

### `julycode.errors`
**职责：** 定义配置错误、Provider 错误和脱敏工具，保证错误不会泄露完整密钥。  
**对外接口：**
```python
class JulyCodeError(Exception): ...
class ConfigError(JulyCodeError): ...
class ProviderError(JulyCodeError): ...
def redact_secret(text: str, secret: str | None = None) -> str
```
**依赖：** 标准库。

## 模块交互
1. 用户运行 `julycode`。
2. `julycode.cli` 调用 `load_config()`，读取 `~/.julycode/config.yaml` 和启动目录向上的 `.julycode.yaml`，合并并校验配置。
3. `julycode.cli` 调用 `create_provider(config)` 得到具体 Provider。
4. `julycode.cli` 创建 `ChatSession` 和 `JulyCodeApp`，进入全屏 TUI。
5. 用户在输入区提交文本。
6. TUI 调用 `ChatSession.append_user_message()`，再用 `build_request()` 取当前会话上下文。
7. TUI 创建异步任务并遍历 `provider.stream_chat(request)`。
8. Provider 将供应商 SSE 数据转换为统一 `StreamEvent`。
9. TUI 收到 `thinking_delta` 时更新对应助手消息的可折叠思考区；收到 `text_delta` 时更新助手回复正文；收到 `message_done` 时写入 `ChatSession`。
10. 如果任何层抛出 `JulyCodeError`，TUI 在状态栏和消息区显示脱敏后的错误，恢复输入并允许用户继续或退出。

## 文件组织
```text
julycode/
├── pyproject.toml                         — 包元数据、命令入口、依赖和测试配置
├── README.md                              — 最小安装、配置和启动说明
├── src/
│   └── julycode/
│       ├── __init__.py                    — 包版本
│       ├── cli.py                         — 命令入口
│       ├── config.py                      — YAML 配置加载、合并、校验
│       ├── errors.py                      — 错误类型和密钥脱敏
│       ├── session.py                     — 当前进程内对话状态
│       ├── providers/
│       │   ├── __init__.py                — Provider 导出
│       │   ├── base.py                    — Provider 接口和统一事件
│       │   ├── factory.py                 — Provider 创建
│       │   ├── sse.py                     — SSE 解析
│       │   ├── openai.py                  — OpenAI 协议适配
│       │   └── anthropic.py               — Anthropic 协议适配
│       └── tui/
│           ├── __init__.py                — TUI 导出
│           ├── app.py                     — Textual 应用主体
│           └── widgets.py                 — 消息、思考区、输入和状态组件
├── tests/
│   ├── test_config.py                     — 配置合并、环境变量、错误脱敏测试
│   ├── test_sse.py                        — SSE 解析测试
│   ├── test_openai_provider.py            — OpenAI 请求和流事件转换测试
│   ├── test_anthropic_provider.py         — Claude 请求、thinking 和错误转换测试
│   ├── test_session.py                    — 会话上下文测试
│   └── test_tui_smoke.py                  — TUI 启动和基础交互冒烟测试
└── specs/
    └── fullscreen-chat-mvp/
        ├── spec.md                        — 已批准需求
        ├── plan.md                        — 技术设计
        ├── task.md                        — 待生成任务拆解
        └── checklist.md                   — 待生成验收清单
```

## 需求覆盖
| 需求 | 架构归属 |
|------|----------|
| F1 | `julycode.tui.app` 和 `julycode.tui.widgets` 提供全屏界面、消息区、输入区、状态提示和退出键绑定。 |
| F2 | Provider 的 `stream_chat()` 返回统一增量事件，TUI 逐事件更新界面。 |
| F3 | `ChatSession` 在当前进程内维护 `messages`，每次请求都传入完整当前会话上下文。 |
| F4 | `AppConfig.protocol` 和 `create_provider()` 在 OpenAI 与 Anthropic Provider 间切换。 |
| F5 | `julycode.config` 读取 YAML 并校验 `protocol`、`model`、`base_url`、`api_key`。 |
| F6 | `julycode.config` 先读用户级配置，再以项目级配置覆盖同名字段。 |
| F7 | `resolve_api_key()` 支持明文和 `${ENV_VAR}`，缺失时抛出脱敏 `ConfigError`。 |
| F8 | `AnthropicProvider` 把 `thinking_delta` 转换为统一事件，TUI 的 `ThinkingPanel` 可折叠展示。 |
| F9 | `errors.py` 和 TUI 错误处理路径展示可理解错误并恢复输入。 |
| F10 | TUI 退出键绑定结束进程，`ChatSession` 不持久化到磁盘。 |

## 技术决策
| 决策点 | 选择 | 理由 |
|--------|------|------|
| 开发语言 | Python 3.11+ | 符合项目约定；异步 HTTP、TUI 和测试生态成熟。 |
| TUI 框架 | Textual | 支持全屏布局、异步任务、滚动区域、键盘绑定和组件化，比直接使用 curses 更适合第一版全屏体验。 |
| HTTP 客户端 | `httpx.AsyncClient.stream()` | 能统一 OpenAI 与 Anthropic 的 SSE 处理，并完整支持自定义 `base_url`、超时和错误响应。 |
| OpenAI 协议 | Chat Completions `stream: true` | 满足纯对话和多轮上下文需求；官方文档说明 Chat Completions 流式返回 data-only SSE chunk，文本位于 `delta.content`。 |
| Anthropic 协议 | Messages API `stream: true` | 官方文档说明 Claude Messages 支持 SSE，并在 extended thinking 开启时返回 `thinking_delta`。 |
| SSE 解析 | 项目内实现轻量解析器 | 事件格式简单，避免额外依赖，同时可覆盖两家 data-only 和命名事件流。 |
| 配置合并 | 用户级默认 + 项目级覆盖 | 符合 CLI 工具习惯，也满足项目级配置覆盖同名字段的验收标准。 |
| 环境变量语法 | 仅支持完整 `${VAR_NAME}` 引用 | 行为明确，错误提示简单，避免混合字符串带来的歧义和密钥泄露风险。 |
| 会话持久化 | 仅内存保存 | 严格符合本阶段不跨启动恢复历史的范围。 |
| 错误处理 | 统一异常 + TUI 展示 + 密钥脱敏 | 满足错误可理解、可继续输入、不泄露密钥的要求。 |
| 自动测试 | 单元测试 + Provider mocked stream + TUI 冒烟 | 本地测试可稳定验证协议转换；真实 API 和 tmux 端到端放入验收阶段执行。 |

## 外部协议依据
- OpenAI 官方文档：Streaming API responses，说明 `stream=True` 使用 SSE，Chat Completions chunk 通过 `delta.content` 增量输出。<https://developers.openai.com/api/docs/guides/streaming-responses>
- OpenAI 官方 API Reference：Chat Completions chunk 对象包含 `choices[].delta.content`。<https://developers.openai.com/api/reference/resources/chat>
- Anthropic 官方文档：Streaming Messages，说明 `stream: true` 使用 SSE，事件包含 `text_delta`、`thinking_delta`、`message_stop` 等。<https://platform.claude.com/docs/en/build-with-claude/streaming>
- Anthropic 官方文档：Extended thinking，说明 thinking 配置、`display` 行为和流式 `thinking_delta`。<https://platform.claude.com/docs/en/build-with-claude/extended-thinking>
