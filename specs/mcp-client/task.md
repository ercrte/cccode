# MCP 客户端 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/mewcode/config.py` | 增加 MCP 配置结构、解析、环境变量展开和 Server map 合并 |
| 修改 | `src/mewcode/cli.py` | 创建 MCP Manager 并传入 TUI 应用 |
| 修改 | `src/mewcode/tui/app.py` | 在 TUI 事件循环中初始化和关闭 MCP Manager |
| 新建 | `src/mewcode/mcp/__init__.py` | 导出 MCP 子系统公开入口 |
| 新建 | `src/mewcode/mcp/errors.py` | 定义 MCP 错误类型 |
| 新建 | `src/mewcode/mcp/transport.py` | 实现 JSON-RPC、stdio、Streamable HTTP 传输 |
| 新建 | `src/mewcode/mcp/client.py` | 实现 MCP 初始化、工具发现和工具调用会话 |
| 新建 | `src/mewcode/mcp/tools.py` | 实现远端工具到 MewCode Tool 的适配层 |
| 新建 | `src/mewcode/mcp/manager.py` | 实现多 Server 生命周期、注册和加载报告 |
| 修改 | `README.md` | 增加 MCP 配置示例、命名规则和不支持范围 |
| 修改 | `tests/test_config.py` | 覆盖 MCP 配置解析、合并和环境变量展开 |
| 新建 | `tests/test_mcp_transport.py` | 覆盖 JSON-RPC 配对、stdio、HTTP JSON/SSE 传输 |
| 新建 | `tests/test_mcp_client.py` | 覆盖初始化、initialized 通知、工具列表分页和调用 |
| 新建 | `tests/test_mcp_tools.py` | 覆盖远端工具适配、命名、结果和错误映射 |
| 新建 | `tests/test_mcp_manager.py` | 覆盖多 Server 加载、失败隔离、注册报告和 CLI 接入 |
| 修改 | `tests/test_agent.py` | 覆盖 Agent 通过 registry 调用 MCP 工具 |
| 新建 | `tests/fixtures/mcp_stdio_server.py` | 测试用 stdio MCP Server |
| 新建 | `tests/fixtures/mcp_http_server.py` | 测试用 Streamable HTTP MCP Server |

## T1: 增加 MCP 配置默认值

**文件：** `src/mewcode/config.py`、`tests/test_config.py`  
**依赖：** 无  
**步骤：**
1. 在 `tests/test_config.py` 增加 `test_mcp_config_defaults_to_empty`，断言未配置 MCP 时 `config.mcp.servers == {}`。
2. 在 `src/mewcode/config.py` 新增 `McpServerConfig` 和 `McpConfig` 数据结构。
3. 在 `AppConfig` 增加 `mcp: McpConfig` 默认值。
4. 保持现有配置测试中的 `AppConfig` 构造不需要额外传参。

**验证：** 运行 `python -m pytest tests/test_config.py::test_mcp_config_defaults_to_empty tests/test_config.py::test_loads_required_yaml_fields -q`，期望全部通过。

## T2: 解析 stdio 和 HTTP Server 配置

**文件：** `src/mewcode/config.py`、`tests/test_config.py`  
**依赖：** T1  
**步骤：**
1. 在 `tests/test_config.py` 增加 `test_loads_stdio_mcp_server_config`，覆盖 `type: stdio`、`command`、`args`、`env`。
2. 在 `tests/test_config.py` 增加 `test_loads_http_mcp_server_config`，覆盖 `type: http`、`url`、`headers`。
3. 在 `src/mewcode/config.py` 增加 `_parse_mcp_config()` 和单个 Server 解析逻辑。
4. 对缺失 `command`、缺失 `url`、未知 `type`、非 map 的 `mcp_servers` 抛出 `ConfigError`。

**验证：** 运行 `python -m pytest tests/test_config.py::test_loads_stdio_mcp_server_config tests/test_config.py::test_loads_http_mcp_server_config tests/test_config.py::test_rejects_invalid_mcp_server_config -q`，期望全部通过。

## T3: 支持 MCP 配置环境变量展开

**文件：** `src/mewcode/config.py`、`tests/test_config.py`  
**依赖：** T2  
**步骤：**
1. 在 `tests/test_config.py` 增加 `test_mcp_config_expands_environment_values`，覆盖 stdio `env`、HTTP `url` 和 `headers`。
2. 在 `tests/test_config.py` 增加 `test_mcp_config_rejects_missing_environment_value`，断言错误信息包含 Server 名和字段类别，不包含原始密钥值。
3. 在 `src/mewcode/config.py` 增加 MCP 专用字符串展开函数，支持整值 `${VAR}` 和字符串片段中的 `${VAR}`。
4. 只对 stdio `env`、HTTP `url` 和 HTTP `headers` 执行展开。

**验证：** 运行 `python -m pytest tests/test_config.py::test_mcp_config_expands_environment_values tests/test_config.py::test_mcp_config_rejects_missing_environment_value -q`，期望全部通过。

## T4: 合并用户级和项目级 MCP Server

**文件：** `src/mewcode/config.py`、`tests/test_config.py`  
**依赖：** T3  
**步骤：**
1. 在 `tests/test_config.py` 增加 `test_project_mcp_servers_override_user_servers_by_name`。
2. 断言用户级和项目级不同名 Server 同时保留。
3. 断言项目级同名 Server 完整覆盖用户级同名 Server。
4. 在 `load_config()` 中对 `mcp_servers` 做按 Server 名合并，其他顶层字段保持现有覆盖语义。

**验证：** 运行 `python -m pytest tests/test_config.py::test_project_mcp_servers_override_user_servers_by_name tests/test_config.py::test_project_config_overrides_user_config -q`，期望全部通过。

## T5: 定义 MCP 错误类型和包入口

**文件：** `src/mewcode/mcp/__init__.py`、`src/mewcode/mcp/errors.py`  
**依赖：** T1  
**步骤：**
1. 创建 `src/mewcode/mcp/errors.py`，定义 `McpError`、`McpConfigError`、`McpConnectionError`、`McpProtocolError`、`McpToolError`。
2. 让 `McpError` 继承现有 `MewCodeError`。
3. 让 `McpProtocolError` 可携带 JSON-RPC error code、message 和 data。
4. 创建 `src/mewcode/mcp/__init__.py`，导出后续开发需要的错误类型。

**验证：** 运行 `python -c "from mewcode.mcp.errors import McpError, McpProtocolError; assert issubclass(McpProtocolError, McpError)"`，期望退出码为 0。

## T6: 创建 stdio MCP 测试 Server

**文件：** `tests/fixtures/mcp_stdio_server.py`  
**依赖：** 无  
**步骤：**
1. 创建测试用 stdio Server，按行读取 JSON-RPC 消息并按行输出 JSON-RPC 响应。
2. 支持 `initialize`、`notifications/initialized`、`tools/list`、`tools/call`、`ping` 和测试专用延迟方法。
3. 提供两个工具：`echo` 和 `same_name`，用于命名和调用测试。
4. 支持通过参数触发乱序响应、协议错误和无响应超时场景。

**验证：** 运行 `python -m py_compile tests/fixtures/mcp_stdio_server.py`，期望退出码为 0。

## T7: 创建 Streamable HTTP MCP 测试 Server

**文件：** `tests/fixtures/mcp_http_server.py`  
**依赖：** 无  
**步骤：**
1. 创建测试用 HTTP Server，endpoint 为 `/mcp`。
2. 支持 `POST` 接收 JSON-RPC 请求，并根据请求返回 `application/json` 或 `text/event-stream`。
3. 在 `initialize` 响应中返回 `Mcp-Session-Id`，并校验后续请求携带该 header。
4. 支持 `DELETE /mcp` 记录会话关闭，供关闭流程测试使用。

**验证：** 运行 `python -m py_compile tests/fixtures/mcp_http_server.py`，期望退出码为 0。

## T8: 实现 stdio transport 基础请求

**文件：** `src/mewcode/mcp/transport.py`、`tests/test_mcp_transport.py`  
**依赖：** T5、T6  
**步骤：**
1. 在 `tests/test_mcp_transport.py` 增加 `test_stdio_transport_sends_request_and_notification`。
2. 在 `src/mewcode/mcp/transport.py` 定义 `JsonRpcError`、`McpTransport` 和 `StdioMcpTransport`。
3. 用 `asyncio.create_subprocess_exec()` 启动 stdio Server，并写入 UTF-8 JSON-RPC 行。
4. 实现 `request()`、`notify()` 和 `close()` 的基础路径。

**验证：** 运行 `python -m pytest tests/test_mcp_transport.py::test_stdio_transport_sends_request_and_notification -q`，期望通过。

## T9: 实现 stdio JSON-RPC 异步配对和错误处理

**文件：** `src/mewcode/mcp/transport.py`、`tests/test_mcp_transport.py`  
**依赖：** T8  
**步骤：**
1. 在 `tests/test_mcp_transport.py` 增加 `test_stdio_transport_matches_out_of_order_responses_by_id`。
2. 在 `tests/test_mcp_transport.py` 增加 `test_stdio_transport_raises_protocol_error_response`。
3. 在 `tests/test_mcp_transport.py` 增加 `test_stdio_transport_times_out_pending_request`。
4. 在 `StdioMcpTransport` 中维护 pending future map，reader task 按响应 id 分发结果。
5. 收到 JSON-RPC error response 时抛出 `McpProtocolError`；超时时取消等待并返回清晰错误。

**验证：** 运行 `python -m pytest tests/test_mcp_transport.py::test_stdio_transport_matches_out_of_order_responses_by_id tests/test_mcp_transport.py::test_stdio_transport_raises_protocol_error_response tests/test_mcp_transport.py::test_stdio_transport_times_out_pending_request -q`，期望全部通过。

## T10: 实现 Streamable HTTP JSON 响应

**文件：** `src/mewcode/mcp/transport.py`、`tests/test_mcp_transport.py`  
**依赖：** T5、T7  
**步骤：**
1. 在 `tests/test_mcp_transport.py` 增加 `test_http_transport_sends_json_request_and_uses_session_headers`。
2. 在 `src/mewcode/mcp/transport.py` 实现 `StreamableHttpMcpTransport` 的 JSON 响应路径。
3. 每次请求设置 `Accept: application/json, text/event-stream`。
4. 初始化后保存 `Mcp-Session-Id`，后续请求带 `Mcp-Session-Id` 和 `MCP-Protocol-Version`。

**验证：** 运行 `python -m pytest tests/test_mcp_transport.py::test_http_transport_sends_json_request_and_uses_session_headers -q`，期望通过。

## T11: 实现 Streamable HTTP SSE 响应

**文件：** `src/mewcode/mcp/transport.py`、`tests/test_mcp_transport.py`  
**依赖：** T10  
**步骤：**
1. 在 `tests/test_mcp_transport.py` 增加 `test_http_transport_reads_sse_response_until_matching_id`。
2. 复用 `mewcode.providers.sse.iter_sse_lines()` 解析 SSE。
3. 忽略与当前请求无关的通知。
4. 当 SSE 中出现匹配 id 的 response 时返回对应 result 或抛出协议错误。

**验证：** 运行 `python -m pytest tests/test_mcp_transport.py::test_http_transport_reads_sse_response_until_matching_id -q`，期望通过。

## T12: 完成 transport 关闭和脱敏

**文件：** `src/mewcode/mcp/transport.py`、`tests/test_mcp_transport.py`  
**依赖：** T9、T11  
**步骤：**
1. 在 `tests/test_mcp_transport.py` 增加 `test_stdio_transport_close_terminates_process`。
2. 在 `tests/test_mcp_transport.py` 增加 `test_http_transport_close_sends_delete_when_session_exists`。
3. 在 `tests/test_mcp_transport.py` 增加 `test_transport_errors_are_redacted`。
4. stdio 关闭时先关闭 stdin，再等待退出，必要时 terminate。
5. HTTP 关闭时若存在 session id，尽力发送 DELETE，然后关闭 `httpx.AsyncClient`。

**验证：** 运行 `python -m pytest tests/test_mcp_transport.py::test_stdio_transport_close_terminates_process tests/test_mcp_transport.py::test_http_transport_close_sends_delete_when_session_exists tests/test_mcp_transport.py::test_transport_errors_are_redacted -q`，期望全部通过。

## T13: 实现 MCP 初始化会话

**文件：** `src/mewcode/mcp/client.py`、`tests/test_mcp_client.py`  
**依赖：** T5、T8、T10  
**步骤：**
1. 在 `tests/test_mcp_client.py` 增加 `test_client_initialize_sends_initialize_and_initialized_notification`。
2. 在 `tests/test_mcp_client.py` 增加 `test_client_initialize_requires_tools_capability`。
3. 创建 `McpClientSession`，实现 `initialize()`。
4. 初始化请求使用协议版本 `2025-06-18`、空 client capabilities 和 MewCode clientInfo。
5. 初始化成功后发送 `notifications/initialized`。

**验证：** 运行 `python -m pytest tests/test_mcp_client.py::test_client_initialize_sends_initialize_and_initialized_notification tests/test_mcp_client.py::test_client_initialize_requires_tools_capability -q`，期望全部通过。

## T14: 实现 MCP 工具列表分页

**文件：** `src/mewcode/mcp/client.py`、`tests/test_mcp_client.py`  
**依赖：** T13  
**步骤：**
1. 在 `tests/test_mcp_client.py` 增加 `test_client_list_tools_handles_pagination`。
2. 定义 `McpToolDefinition`。
3. 实现 `McpClientSession.list_tools()`，发送 `tools/list` 并处理 `nextCursor`。
4. 校验每个工具至少包含 `name` 和对象形式的 `inputSchema`。
5. 生成 `server__tool` 全局工具名。

**验证：** 运行 `python -m pytest tests/test_mcp_client.py::test_client_list_tools_handles_pagination -q`，期望通过。

## T15: 实现 MCP 工具调用会话

**文件：** `src/mewcode/mcp/client.py`、`tests/test_mcp_client.py`  
**依赖：** T13  
**步骤：**
1. 在 `tests/test_mcp_client.py` 增加 `test_client_call_tool_sends_tools_call`。
2. 在 `tests/test_mcp_client.py` 增加 `test_client_call_tool_rejects_invalid_result_shape`。
3. 实现 `McpClientSession.call_tool()`，发送 `tools/call`。
4. 校验返回值是对象，并保留 `content`、`structuredContent` 和 `isError`。

**验证：** 运行 `python -m pytest tests/test_mcp_client.py::test_client_call_tool_sends_tools_call tests/test_mcp_client.py::test_client_call_tool_rejects_invalid_result_shape -q`，期望全部通过。

## T16: 实现 MCP 工具命名和 ToolSpec 适配

**文件：** `src/mewcode/mcp/tools.py`、`tests/test_mcp_tools.py`  
**依赖：** T14  
**步骤：**
1. 在 `tests/test_mcp_tools.py` 增加 `test_global_tool_name_uses_server_prefix`。
2. 在 `tests/test_mcp_tools.py` 增加 `test_remote_mcp_tool_exposes_tool_spec`。
3. 实现 `make_global_tool_name()` 和 `parse_global_tool_name()`。
4. 实现 `RemoteMcpTool.__init__()`，把远端 `inputSchema`、description 和 title 转为 `ToolSpec`。
5. 将 `RemoteMcpTool.spec.safety` 设为 `side_effect`。

**验证：** 运行 `python -m pytest tests/test_mcp_tools.py::test_global_tool_name_uses_server_prefix tests/test_mcp_tools.py::test_remote_mcp_tool_exposes_tool_spec -q`，期望全部通过。

## T17: 实现 MCP 工具结果和错误映射

**文件：** `src/mewcode/mcp/tools.py`、`tests/test_mcp_tools.py`  
**依赖：** T15、T16  
**步骤：**
1. 在 `tests/test_mcp_tools.py` 增加 `test_remote_mcp_tool_returns_success_payload`。
2. 在 `tests/test_mcp_tools.py` 增加 `test_remote_mcp_tool_maps_is_error_to_tool_execution_error`。
3. 在 `tests/test_mcp_tools.py` 增加 `test_remote_mcp_tool_maps_protocol_and_invalid_response_errors`。
4. 实现 `RemoteMcpTool.execute()`，调用 session 的 `call_tool()`。
5. 将成功结果转换为包含 `server`、`remote_tool`、`content`、`structured_content`、`is_error` 的 dict。
6. 将远端 `isError: true`、协议错误、超时和非法响应映射为 `ToolExecutionError`。

**验证：** 运行 `python -m pytest tests/test_mcp_tools.py::test_remote_mcp_tool_returns_success_payload tests/test_mcp_tools.py::test_remote_mcp_tool_maps_is_error_to_tool_execution_error tests/test_mcp_tools.py::test_remote_mcp_tool_maps_protocol_and_invalid_response_errors -q`，期望全部通过。

## T18: 实现 MCP Manager 成功加载和注册

**文件：** `src/mewcode/mcp/manager.py`、`tests/test_mcp_manager.py`  
**依赖：** T12、T17  
**步骤：**
1. 在 `tests/test_mcp_manager.py` 增加 `test_manager_initializes_servers_and_registers_tools`。
2. 定义 `McpLoadReport` 和 `McpManager`。
3. `McpManager.initialize()` 按配置创建 transport 和 `McpClientSession`，完成初始化和工具发现。
4. `McpManager.register_tools()` 把 `RemoteMcpTool` 注册到 `ToolRegistry`。
5. `load_report()` 返回成功 Server 和注册工具名。

**验证：** 运行 `python -m pytest tests/test_mcp_manager.py::test_manager_initializes_servers_and_registers_tools -q`，期望通过。

## T19: 实现 Manager 失败隔离和关闭

**文件：** `src/mewcode/mcp/manager.py`、`tests/test_mcp_manager.py`  
**依赖：** T18  
**步骤：**
1. 在 `tests/test_mcp_manager.py` 增加 `test_manager_isolates_failed_server_and_keeps_successful_server`。
2. 在 `tests/test_mcp_manager.py` 增加 `test_manager_records_duplicate_tool_registration_failure`。
3. 在 `tests/test_mcp_manager.py` 增加 `test_manager_close_closes_initialized_sessions`。
4. 捕获单个 Server 初始化、工具列表和注册失败，并写入脱敏 report。
5. `close()` 逐个关闭已创建的 session，单个关闭失败不影响其他 session。

**验证：** 运行 `python -m pytest tests/test_mcp_manager.py::test_manager_isolates_failed_server_and_keeps_successful_server tests/test_mcp_manager.py::test_manager_records_duplicate_tool_registration_failure tests/test_mcp_manager.py::test_manager_close_closes_initialized_sessions -q`，期望全部通过。

## T20: 接入 CLI/TUI 启动和退出流程

**文件：** `src/mewcode/cli.py`、`src/mewcode/tui/app.py`、`tests/test_mcp_manager.py`  
**依赖：** T19  
**步骤：**
1. 在 `tests/test_mcp_manager.py` 增加 `test_cli_initializes_mcp_manager_and_closes_it`，验证 CLI 创建并传入 Manager。
2. 在 `tests/test_mcp_manager.py` 增加 `test_cli_reports_mcp_config_error_without_secret`。
3. 在 `tests/test_mcp_manager.py` 增加 `test_tui_lifecycle_initializes_and_closes_mcp_manager`，验证 TUI 事件循环内初始化、注册和关闭。
4. 修改 `cli.main()`，创建默认 registry 和 `McpManager`，并把 Manager 传给 `MewCodeApp`。
5. 修改 `MewCodeApp.on_mount()`，在 TUI 事件循环内初始化 MCP Manager 并注册工具。
6. 对单 Server 加载失败输出 warning 到 stderr，不退出。
7. 修改 `MewCodeApp.on_unmount()`，在同一事件循环内关闭 MCP Manager。
8. 对 MCP 配置错误按现有配置错误路径退出。

**验证：** 运行 `python -m pytest tests/test_mcp_manager.py::test_cli_initializes_mcp_manager_and_closes_it tests/test_mcp_manager.py::test_tui_lifecycle_initializes_and_closes_mcp_manager tests/test_mcp_manager.py::test_cli_reports_mcp_config_error_without_secret tests/test_config.py::test_cli_reports_config_error_without_secret -q`，期望全部通过。

## T21: 覆盖 Agent 调用 MCP 工具

**文件：** `tests/test_agent.py`  
**依赖：** T17  
**步骤：**
1. 在 `tests/test_agent.py` 增加 `test_runner_can_execute_remote_mcp_tool_from_registry`。
2. 使用 fake MCP session 构造 `RemoteMcpTool` 并注册到 `ToolRegistry`。
3. 使用 fake provider 让模型请求 `demo__echo`。
4. 断言 Agent 发出 `tool_started`、`tool_finished`，并把成功工具结果回灌到下一轮模型请求。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_can_execute_remote_mcp_tool_from_registry -q`，期望通过。

## T22: 验证 Provider 对 MCP 工具无特殊分支

**文件：** `tests/test_openai_provider.py`、`tests/test_anthropic_provider.py`  
**依赖：** T16  
**步骤：**
1. 在 `tests/test_openai_provider.py` 增加或扩展工具 payload 测试，使用 `demo__echo` 这类 MCP 全局工具名。
2. 在 `tests/test_anthropic_provider.py` 增加或扩展工具 payload 测试，使用同一个 MCP 全局工具名。
3. 断言 Provider 仍按普通 `ToolSpec` 输出工具描述，不引入 MCP 专用字段。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py::test_openai_request_includes_mcp_prefixed_tool tests/test_anthropic_provider.py::test_anthropic_request_includes_mcp_prefixed_tool -q`，期望全部通过。

## T23: 更新 README MCP 文档

**文件：** `README.md`  
**依赖：** T4、T20  
**步骤：**
1. 增加 `mcp_servers` 配置示例，包含 stdio 和 Streamable HTTP。
2. 说明用户级和项目级 Server map 合并规则。
3. 说明 `${VAR}` 展开规则和缺失变量错误。
4. 说明工具命名规则为 `server__tool`。
5. 更新范围说明，移除“真实 MCP 接入未实现”的旧边界，保留资源、提示词、采样、健康检查和自动重连不支持。

**验证：** 运行 `rg -n "mcp_servers|stdio|Streamable HTTP|server__tool|资源|提示词|采样|自动重连" README.md`，期望命中配置、命名和不支持范围。

## T24: 运行 MCP 相关回归

**文件：** `src/mewcode/config.py`、`src/mewcode/cli.py`、`src/mewcode/mcp/*.py`、`tests/test_*.py`、`README.md`  
**依赖：** T1 至 T23  
**步骤：**
1. 运行 MCP 新增测试和受影响的配置、工具、Agent、Provider 测试。
2. 修复失败测试对应的实现或测试预期。
3. 确认没有破坏默认无 MCP 配置路径。

**验证：** 运行 `python -m pytest tests/test_config.py tests/test_mcp_transport.py tests/test_mcp_client.py tests/test_mcp_tools.py tests/test_mcp_manager.py tests/test_agent.py tests/test_tools.py tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望全部通过。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5
T6 → T7
T8 → T9 → T10 → T11 → T12
T13 → T14 → T15
T16 → T17
T18 → T19 → T20
T21 → T22 → T23 → T24
```
