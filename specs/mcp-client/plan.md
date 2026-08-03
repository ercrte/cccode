# MCP 客户端 Plan

## 架构概览
本阶段在现有工具系统旁新增 `julycode.mcp` 子系统。配置层负责解析 `mcp_servers` map，并把用户级和项目级配置按 Server 名合并。MCP 子系统负责连接 Server、完成初始化、列出工具、维护会话和转发工具调用。工具中心仍是唯一对 Agent 暴露工具的入口，MCP 工具通过适配器实现现有 `Tool` 接口。

启动流程从“加载配置 → 创建 Provider → 创建内置工具注册中心”扩展为“加载配置 → 创建 Provider → 创建内置工具注册中心 → 创建 MCP Manager → TUI 启动事件循环中初始化 MCP Manager → 注册远端工具”。MCP Manager 会逐个连接配置中的 Server；某个 Server 失败只记录加载失败并跳过该 Server，内置工具和其他 Server 继续注册。

MCP 协议层按 JSON-RPC 2.0 实现请求、响应和通知。stdio 传输通过子进程 stdin/stdout 持久连接，并用后台 reader task 按 id 分发响应；Streamable HTTP 传输对每个 JSON-RPC 消息发起 POST，并同时支持 `application/json` 单响应和 `text/event-stream` 多消息响应。HTTP 初始化返回的 session id 会被缓存并用于后续请求。

远端工具名通过 `server__tool` 映射成全局工具名。`RemoteMcpTool` 的 `ToolSpec` 使用全局工具名、远端描述和远端 `inputSchema`；执行时把全局名还原为 Server 名和远端工具名，调用对应会话的 `tools/call`。远端返回的 `content`、`structuredContent` 和 `isError` 会转换为现有 `ToolResult` 可承载的数据或结构化失败。

MCP Manager 持有所有成功初始化的连接，并在 JulyCode 退出时关闭。初始化和关闭必须发生在 TUI 的同一个 asyncio 事件循环中，避免 stdio reader task 或 HTTP client 跨事件循环失效。stdio 关闭时先关闭子进程 stdin，再等待退出，必要时终止子进程；HTTP 关闭时关闭 `httpx.AsyncClient`，如果 Server 给过 session id，则尽力发送 DELETE 结束会话，失败不影响退出。

## 核心数据结构

### McpServerConfig
```python
@dataclass(frozen=True)
class McpServerConfig:
    name: str
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
```

表示一个 MCP Server 的解析后配置。`stdio` 必须有 `command`，可选 `args` 和 `env`；`http` 必须有 `url`，可选 `headers`。`${VAR}` 环境变量展开在进入该结构前完成，既支持整值引用，也支持出现在请求头这类字符串片段中。

### McpConfig
```python
@dataclass(frozen=True)
class McpConfig:
    servers: Mapping[str, McpServerConfig] = field(default_factory=dict)
```

挂在 `AppConfig.mcp` 上，保存所有启用的 MCP Server。

### JsonRpcError
```python
@dataclass(frozen=True)
class JsonRpcError:
    code: int
    message: str
    data: Any | None = None
```

表示 JSON-RPC error 对象。协议层收到 error response 时抛出 `McpProtocolError`，其中包含该对象。

### McpTransport
```python
class McpTransport(Protocol):
    async def start(self) -> None: ...
    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...
    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None: ...
    async def close(self) -> None: ...
```

MCP 会话使用的传输接口。`request()` 负责生成唯一 id、发送 JSON-RPC 请求、等待同 id 响应并返回 `result`；`notify()` 发送不带 id 的通知。

### StdioMcpTransport
```python
class StdioMcpTransport:
    def __init__(self, config: McpServerConfig, *, timeout_seconds: float = 10.0) -> None: ...
    async def start(self) -> None: ...
    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...
    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None: ...
    async def close(self) -> None: ...
```

启动本地子进程并按换行分隔读写 JSON-RPC 消息。内部维护 `_pending: dict[str, Future]`，reader task 收到 response 后按 id 唤醒对应请求；收到 `ping` 请求时返回空 result，收到通知时忽略，收到未知请求时返回 method not found。

### StreamableHttpMcpTransport
```python
class StreamableHttpMcpTransport:
    def __init__(self, config: McpServerConfig, *, timeout_seconds: float = 10.0) -> None: ...
    async def start(self) -> None: ...
    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...
    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None: ...
    async def close(self) -> None: ...
```

使用 `httpx.AsyncClient` 访问配置中的 MCP endpoint。每次请求都带 `Accept: application/json, text/event-stream`；初始化后保存 `Mcp-Session-Id`，后续请求带 `Mcp-Session-Id` 和 `MCP-Protocol-Version`。当响应是 SSE 时，复用现有 SSE parser 读取事件并按 JSON-RPC id 找到目标响应。

### McpToolDefinition
```python
@dataclass(frozen=True)
class McpToolDefinition:
    server_name: str
    remote_name: str
    global_name: str
    title: str | None
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None = None
```

保存远端工具定义和全局工具名映射。`global_name` 使用 `server_name + "__" + remote_name`。

### McpClientSession
```python
class McpClientSession:
    def __init__(self, server: McpServerConfig, transport: McpTransport) -> None: ...
    async def initialize(self) -> None: ...
    async def list_tools(self) -> tuple[McpToolDefinition, ...]: ...
    async def call_tool(self, remote_name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]: ...
    async def close(self) -> None: ...
```

封装单个 MCP Server 会话。`initialize()` 发送 `initialize`，校验协议版本和 `tools` capability，然后发送 `notifications/initialized`。`list_tools()` 处理 `nextCursor` 分页。`call_tool()` 发送 `tools/call`。

### RemoteMcpTool
```python
class RemoteMcpTool:
    def __init__(self, definition: McpToolDefinition, session: McpClientSession) -> None: ...
    spec: ToolSpec
    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]: ...
```

把远端 MCP 工具适配成现有 `Tool`。`spec.name` 为全局名，`spec.parameters_schema` 为远端 `inputSchema`。安全等级统一设为 `side_effect`，让现有权限系统处理人工确认和规则匹配。

### McpManager
```python
class McpManager:
    def __init__(self, config: McpConfig) -> None: ...
    async def initialize(self) -> None: ...
    def register_tools(self, registry: ToolRegistry) -> None: ...
    def load_report(self) -> McpLoadReport: ...
    async def close(self) -> None: ...
```

管理多个 Server 的生命周期。`initialize()` 创建传输、初始化会话并发现工具；失败的 Server 进入 report，不抛出导致整体启动失败。`register_tools()` 把发现到的 `RemoteMcpTool` 注册进现有 `ToolRegistry`。

### McpLoadReport
```python
@dataclass(frozen=True)
class McpLoadReport:
    loaded_servers: tuple[str, ...]
    failed_servers: Mapping[str, str]
    registered_tools: tuple[str, ...]
```

用于测试和启动时告警。错误信息必须经过脱敏。

## 模块设计

### `julycode.config`
**职责：** 解析 `mcp_servers` 配置，执行用户级/项目级 Server map 合并，展开环境变量。  
**对外接口：** `AppConfig.mcp: McpConfig`。  
**依赖：** `yaml`、`os.environ`、现有 `ConfigError`。

配置格式：
```yaml
mcp_servers:
  local_demo:
    type: stdio
    command: python
    args: ["tests/fixtures/mcp_stdio_server.py"]
    env:
      API_TOKEN: ${MCP_API_TOKEN}
  remote_demo:
    type: http
    url: http://127.0.0.1:8765/mcp
    headers:
      Authorization: Bearer ${MCP_API_TOKEN}
```

`mcp_servers` 缺省时为空。项目级和用户级均存在时，仅该 map 按 Server 名深合并；其他顶层字段保持现有“项目级覆盖用户级”语义。字符串中的 `${VAR}` 会展开，变量缺失或为空时抛出 `ConfigError`，错误中包含 Server 名和字段类别。

### `julycode.mcp.errors`
**职责：** 定义 MCP 子系统的可展示错误。  
**对外接口：** `McpError`、`McpConfigError`、`McpConnectionError`、`McpProtocolError`、`McpToolError`。  
**依赖：** `JulyCodeError`。

### `julycode.mcp.transport`
**职责：** 实现 JSON-RPC 请求/响应配对和两种传输。  
**对外接口：** `McpTransport`、`StdioMcpTransport`、`StreamableHttpMcpTransport`、`JsonRpcError`。  
**依赖：** `asyncio`、`json`、`httpx`、`julycode.providers.sse.iter_sse_lines`、`redact_secret`。

stdio 会捕获 stderr 的末尾日志供错误诊断，但不会把完整环境变量或请求头写入错误。HTTP 错误会脱敏用户配置的 headers 值。

### `julycode.mcp.client`
**职责：** 实现 MCP lifecycle 和工具协议。  
**对外接口：** `McpClientSession.initialize()`、`list_tools()`、`call_tool()`、`close()`。  
**依赖：** `McpTransport`、`McpToolDefinition`。

初始化请求使用协议版本 `2025-06-18`，client capabilities 为空对象，clientInfo 使用 `JulyCode` 和当前包版本。只要求 Server 声明 `tools` capability；不请求 resources、prompts、sampling、roots 或 elicitation。

### `julycode.mcp.tools`
**职责：** 把远端工具定义适配为 JulyCode `Tool`。  
**对外接口：** `RemoteMcpTool`、`make_global_tool_name(server_name, remote_name)`、`parse_global_tool_name(name)`。  
**依赖：** `ToolSpec`、`ToolContext`、`ToolExecutionError`、`McpClientSession`。

`RemoteMcpTool.execute()` 将 `tools/call` 返回结果转换为：
```python
{
    "server": "...",
    "remote_tool": "...",
    "content": [...],
    "structured_content": {...} | None,
    "is_error": bool,
}
```
当远端返回 `isError: true` 时抛出 `ToolExecutionError(error_type="mcp_tool_error")`；协议 error、超时和非法响应分别映射为 `mcp_protocol_error`、`timeout`、`mcp_invalid_response`。

### `julycode.mcp.manager`
**职责：** 管理多个 Server 的初始化、缓存、注册和关闭。  
**对外接口：** `McpManager.initialize()`、`register_tools()`、`load_report()`、`close()`、`create_mcp_manager(config)`。  
**依赖：** `McpClientSession`、两种 transport、`ToolRegistry`、`RemoteMcpTool`。

`register_tools()` 注册失败时仅影响当前工具或当前 Server，并把原因写入 report。全局工具名重复按失败处理；按已批准需求，正常情况下 `server__tool` 会避免内置工具和跨 Server 冲突。

### `julycode.cli`
**职责：** 创建 MCP Manager 并传入 TUI 应用。  
**对外接口：** 保持 `main(argv=None) -> int` 不变。  
**依赖：** `create_mcp_manager()`、`create_default_registry()`。

`main()` 创建 registry 和 MCP Manager 后，把 Manager 交给 `JulyCodeApp`。MCP 配置格式错误仍作为配置错误退出；单个 Server 连接或发现失败不会退出。

### `julycode.tui.app`
**职责：** 在 Textual 事件循环中初始化 MCP Manager、注册远端工具，并在退出时关闭连接。  
**对外接口：** `JulyCodeApp(..., mcp_manager: McpManager | None = None)`。  
**依赖：** `McpManager`、`ToolRegistry`。

`on_mount()` 中运行 MCP 初始化并注册工具，失败报告输出为脱敏 warning；`on_unmount()` 中关闭 MCP Manager。这样 stdio 子进程 reader task、HTTP client 和后续工具调用位于同一个事件循环。

### `README.md`
**职责：** 增加 MCP 配置和范围说明。  
**对外接口：** 用户文档。  
**依赖：** 无。

## 模块交互
1. `load_config()` 读取用户级和项目级 YAML，并把 `mcp_servers` 解析为 `AppConfig.mcp`。
2. `cli.main()` 创建内置 `ToolRegistry`。
3. `cli.main()` 创建 `McpManager(config.mcp)` 并传入 `JulyCodeApp`。
4. `JulyCodeApp.on_mount()` 在 TUI 事件循环中运行 `McpManager.initialize()`。
5. `McpManager` 对每个 Server 创建对应 transport。
6. `McpClientSession.initialize()` 发送 `initialize`，收到成功响应后发送 `notifications/initialized`。
7. `McpClientSession.list_tools()` 发送 `tools/list`，处理分页并生成 `McpToolDefinition`。
8. `McpManager.register_tools(registry)` 把每个 `McpToolDefinition` 包装为 `RemoteMcpTool` 注册到工具中心。
9. Agent Loop 构造模型请求时照常读取 `ToolRegistry.specs()`，Provider 无需感知 MCP。
10. 模型调用 `server__tool` 后，`ToolExecutor` 找到 `RemoteMcpTool` 并执行。
11. `RemoteMcpTool` 调用对应 session 的 `tools/call`，把返回值转换成现有工具结果。
12. Agent Loop 将工具结果照常写回会话并继续下一轮。
13. JulyCode 退出时，`JulyCodeApp.on_unmount()` 关闭 `McpManager`，Manager 逐个关闭成功创建的 session。

## 文件组织
```text
src/julycode/
├── config.py                 — 增加 MCP 配置结构、解析、环境变量展开和 Server map 合并
├── cli.py                    — 创建 MCP Manager 并传入 TUI 应用
├── tui/app.py                — 在 TUI 事件循环中初始化和关闭 MCP Manager
├── mcp/
│   ├── __init__.py           — 导出 MCP 子系统公开入口
│   ├── errors.py             — MCP 错误类型
│   ├── transport.py          — JSON-RPC、stdio、Streamable HTTP 传输实现
│   ├── client.py             — MCP 初始化、工具发现和工具调用会话
│   ├── tools.py              — 远端工具到 JulyCode Tool 的适配层
│   └── manager.py            — 多 Server 生命周期、工具注册和加载报告
tests/
├── test_config.py            — MCP 配置解析、合并和环境变量展开测试
├── test_mcp_transport.py     — JSON-RPC 配对、stdio、HTTP JSON/SSE 响应测试
├── test_mcp_client.py        — 初始化、initialized 通知、tools/list 分页、tools/call 测试
├── test_mcp_tools.py         — 远端工具适配、命名、结果和错误映射测试
├── test_mcp_manager.py       — 多 Server 加载、失败隔离、注册报告测试
├── test_tools.py             — 默认 registry 包含内置工具的回归测试
├── test_agent.py             — Agent 通过 registry 调用 MCP 工具的集成测试
└── fixtures/
    ├── mcp_stdio_server.py   — 测试用 stdio MCP Server
    └── mcp_http_server.py    — 测试用 Streamable HTTP MCP Server
README.md                    — MCP 配置示例和范围说明
specs/mcp-client/
├── spec.md
├── plan.md
├── task.md
└── checklist.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 配置 key | `mcp_servers` | 现有配置字段使用 snake_case，保持一致。 |
| HTTP 类型值 | `type: http` 表示 Streamable HTTP | 配置简洁；文档中明确它是 Streamable HTTP，不支持旧 HTTP+SSE 兼容模式。 |
| 工具命名 | `server__tool` | 已确认的方案，避免覆盖内置工具和跨 Server 冲突。 |
| MCP 工具安全等级 | 默认 `side_effect` | 远端工具 annotations 不可信，先接入现有权限系统，避免无确认执行外部能力。 |
| Server 加载失败 | 单 Server 失败写入 report 并跳过 | 满足失败隔离，避免一个外部服务阻塞整个应用。 |
| 配置格式错误 | 直接 `ConfigError` 退出 | 配置本身不可理解时无法可靠判断用户意图，应尽早失败。 |
| JSON-RPC id | 每个 session 内递增字符串 id | 满足同会话唯一，便于测试乱序响应配对。 |
| stdio stderr | 捕获末尾日志用于诊断 | MCP 允许 Server 用 stderr 打日志；只保留有限长度并脱敏。 |
| HTTP SSE GET | 本阶段不主动建立 GET 监听流 | 需求只覆盖工具初始化、列表和调用；动态通知、健康检查和重连不在范围。 |
| HTTP session id | 初始化响应有 `Mcp-Session-Id` 时缓存并带到后续请求 | 符合 Streamable HTTP session 管理要求。 |
| 工具结果转换 | 保留 MCP 原始 `content`，同时暴露 `structuredContent` | 不丢失远端信息，并让模型可读可解析。 |
| 生命周期关闭 | TUI `on_mount` 初始化、`on_unmount` 关闭 Manager | stdio reader task 和后续工具调用必须处于同一 asyncio 事件循环，避免跨 loop 持久连接失效。 |
| Provider 改动 | 不改 OpenAI/Anthropic Provider | MCP 工具在注册中心已变成普通 `ToolSpec`，供应商协议层无需分支。 |
| 旧 HTTP+SSE | 不支持兼容旧传输 | 用户明确要求 Streamable HTTP，本阶段避免扩大范围。 |

## 需求覆盖

| 需求 | 架构 owner |
|------|------------|
| F1, F6 | `julycode.config` 的 `mcp_servers` 解析和 Server map 合并 |
| F2, F3, F4, F5 | `McpServerConfig`、配置解析、环境变量展开 |
| F7, F13 | `McpClientSession.initialize()` |
| F8 | `McpClientSession.list_tools()` |
| F9, F10 | `McpToolDefinition`、`RemoteMcpTool`、`McpManager.register_tools()` |
| F11, F16, F17 | `RemoteMcpTool.execute()`、`McpClientSession.call_tool()` |
| F12 | `StdioMcpTransport`、`StreamableHttpMcpTransport` 的 request/response id 配对 |
| F14, F15 | `McpManager` 会话缓存、加载报告和关闭流程 |
| F18 | `mcp_servers` 缺省为空，默认 registry 创建路径保持原状 |
| F19 | `README.md` MCP 章节 |
