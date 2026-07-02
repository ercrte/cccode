# MCP 客户端 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] 配置未声明 `mcp_servers` 时默认 MCP Server 列表为空，现有基础配置仍能加载（验证：运行 `python -m pytest tests/test_config.py::test_mcp_config_defaults_to_empty tests/test_config.py::test_loads_required_yaml_fields -q`，期望通过）
- [ ] stdio MCP Server 配置支持 `type`、`command`、`args`、`env`，非法 stdio 配置给出 `ConfigError`（验证：运行 `python -m pytest tests/test_config.py::test_loads_stdio_mcp_server_config tests/test_config.py::test_rejects_invalid_mcp_server_config -q`，期望通过）
- [ ] Streamable HTTP MCP Server 配置支持 `type`、`url`、`headers`，非法 HTTP 配置给出 `ConfigError`（验证：运行 `python -m pytest tests/test_config.py::test_loads_http_mcp_server_config tests/test_config.py::test_rejects_invalid_mcp_server_config -q`，期望通过）
- [ ] MCP 配置中的 `${VAR}` 能在 stdio `env`、HTTP `url` 和 HTTP `headers` 中展开，缺失变量错误包含 Server 名和字段类别且不泄露密钥（验证：运行 `python -m pytest tests/test_config.py::test_mcp_config_expands_environment_values tests/test_config.py::test_mcp_config_rejects_missing_environment_value -q`，期望通过）
- [ ] 用户级和项目级 MCP Server 按 Server 名合并，不同名保留，同名由项目级覆盖（验证：运行 `python -m pytest tests/test_config.py::test_project_mcp_servers_override_user_servers_by_name tests/test_config.py::test_project_config_overrides_user_config -q`，期望通过）
- [ ] stdio transport 能发送 JSON-RPC request 和 notification，并能关闭测试子进程（验证：运行 `python -m pytest tests/test_mcp_transport.py::test_stdio_transport_sends_request_and_notification tests/test_mcp_transport.py::test_stdio_transport_close_terminates_process -q`，期望通过）
- [ ] stdio transport 能按 id 匹配乱序响应，协议 error 和超时会转换为清晰错误（验证：运行 `python -m pytest tests/test_mcp_transport.py::test_stdio_transport_matches_out_of_order_responses_by_id tests/test_mcp_transport.py::test_stdio_transport_raises_protocol_error_response tests/test_mcp_transport.py::test_stdio_transport_times_out_pending_request -q`，期望通过）
- [ ] Streamable HTTP transport 能发送 JSON-RPC POST，携带 Accept header，缓存并复用 `Mcp-Session-Id` 和 `MCP-Protocol-Version`（验证：运行 `python -m pytest tests/test_mcp_transport.py::test_http_transport_sends_json_request_and_uses_session_headers -q`，期望通过）
- [ ] Streamable HTTP transport 能读取 SSE 响应直到匹配 id，并能在关闭时发送 DELETE 结束 session（验证：运行 `python -m pytest tests/test_mcp_transport.py::test_http_transport_reads_sse_response_until_matching_id tests/test_mcp_transport.py::test_http_transport_close_sends_delete_when_session_exists -q`，期望通过）
- [ ] MCP transport 错误会脱敏，不泄露配置中的 header 或环境变量密钥值（验证：运行 `python -m pytest tests/test_mcp_transport.py::test_transport_errors_are_redacted -q`，期望通过）
- [ ] MCP Client 初始化会先发送 `initialize`，成功后发送 `notifications/initialized`，且要求 Server 声明 tools capability（验证：运行 `python -m pytest tests/test_mcp_client.py::test_client_initialize_sends_initialize_and_initialized_notification tests/test_mcp_client.py::test_client_initialize_requires_tools_capability -q`，期望通过）
- [ ] MCP Client 能通过 `tools/list` 获取工具列表并处理分页（验证：运行 `python -m pytest tests/test_mcp_client.py::test_client_list_tools_handles_pagination -q`，期望通过）
- [ ] MCP Client 能通过 `tools/call` 调用远端工具，并拒绝无法理解的工具调用结果（验证：运行 `python -m pytest tests/test_mcp_client.py::test_client_call_tool_sends_tools_call tests/test_mcp_client.py::test_client_call_tool_rejects_invalid_result_shape -q`，期望通过）
- [ ] MCP 远端工具全局名采用 `server__tool`，且暴露为现有 `ToolSpec`，安全等级为 `side_effect`（验证：运行 `python -m pytest tests/test_mcp_tools.py::test_global_tool_name_uses_server_prefix tests/test_mcp_tools.py::test_remote_mcp_tool_exposes_tool_spec -q`，期望通过）
- [ ] MCP 远端工具成功结果会保留 `content`、`structured_content`、Server 名和远端工具名（验证：运行 `python -m pytest tests/test_mcp_tools.py::test_remote_mcp_tool_returns_success_payload -q`，期望通过）
- [ ] MCP 远端工具的 `isError`、协议错误、超时和非法响应都会映射为结构化工具失败（验证：运行 `python -m pytest tests/test_mcp_tools.py::test_remote_mcp_tool_maps_is_error_to_tool_execution_error tests/test_mcp_tools.py::test_remote_mcp_tool_maps_protocol_and_invalid_response_errors -q`，期望通过）

## 集成
- [ ] MCP Manager 能初始化多个 Server、发现工具并注册到现有 `ToolRegistry`（验证：运行 `python -m pytest tests/test_mcp_manager.py::test_manager_initializes_servers_and_registers_tools -q`，期望通过）
- [ ] 单个 MCP Server 初始化或工具发现失败时，成功 Server 的工具和内置工具仍可用，失败原因进入脱敏加载报告（验证：运行 `python -m pytest tests/test_mcp_manager.py::test_manager_isolates_failed_server_and_keeps_successful_server -q`，期望通过）
- [ ] MCP Manager 对重复全局工具名注册失败做局部隔离，并能关闭已初始化 session（验证：运行 `python -m pytest tests/test_mcp_manager.py::test_manager_records_duplicate_tool_registration_failure tests/test_mcp_manager.py::test_manager_close_closes_initialized_sessions -q`，期望通过）
- [ ] CLI 会创建并传入 MCP Manager，TUI 启动事件循环内会初始化 Manager 并注册工具，退出时关闭 Manager；MCP 配置错误沿用配置错误路径且脱敏（验证：运行 `python -m pytest tests/test_mcp_manager.py::test_cli_initializes_mcp_manager_and_closes_it tests/test_mcp_manager.py::test_tui_lifecycle_initializes_and_closes_mcp_manager tests/test_mcp_manager.py::test_cli_reports_mcp_config_error_without_secret tests/test_config.py::test_cli_reports_config_error_without_secret -q`，期望通过）
- [ ] Agent Loop 能把 `server__tool` 当作普通工具调用，工具结果会回灌到下一轮模型请求（验证：运行 `python -m pytest tests/test_agent.py::test_runner_can_execute_remote_mcp_tool_from_registry -q`，期望通过）
- [ ] OpenAI 和 Anthropic Provider 都能把 MCP 前缀工具作为普通 `ToolSpec` 暴露给模型，不携带 MCP 专用字段（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_request_includes_mcp_prefixed_tool tests/test_anthropic_provider.py::test_anthropic_request_includes_mcp_prefixed_tool -q`，期望通过）
- [ ] 默认无 MCP 配置时，默认 registry 仍只包含六个内置工具，普通聊天和内置工具行为不回退（验证：运行 `python -m pytest tests/test_tools.py::test_default_registry_contains_six_core_tools tests/test_tui_smoke.py::test_submit_streams_text_into_message_view tests/test_agent.py::test_runner_streams_plain_chat_and_saves_message tests/test_agent.py::test_runner_runs_multiple_tool_iterations_until_final_answer -q`，期望通过）
- [ ] README 包含 MCP 配置示例、两种连接类型、环境变量展开、`server__tool` 命名和不支持范围（验证：运行 `rg -n "mcp_servers|stdio|Streamable HTTP|server__tool|资源|提示词|采样|自动重连" README.md`，期望命中配置、命名和不支持范围）

## 编译与测试
- [ ] 测试 MCP stdio fixture 可被 Python 编译（验证：运行 `python -m py_compile tests/fixtures/mcp_stdio_server.py`，期望退出码为 0）
- [ ] 测试 MCP HTTP fixture 可被 Python 编译（验证：运行 `python -m py_compile tests/fixtures/mcp_http_server.py`，期望退出码为 0）
- [ ] MCP 新增测试和受影响回归测试全部通过（验证：运行 `python -m pytest tests/test_config.py tests/test_mcp_transport.py tests/test_mcp_client.py tests/test_mcp_tools.py tests/test_mcp_manager.py tests/test_agent.py tests/test_tools.py tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望通过）
- [ ] TUI 和权限相关回归测试通过，确认 MCP 工具接入未破坏用户确认和界面恢复（验证：运行 `python -m pytest tests/test_tool_scheduler.py tests/test_permissions.py tests/test_tui_smoke.py -q`，期望通过）
- [ ] 项目未配置 lint 命令时记录为不适用；如后续配置 lint，则 lint 检查通过（验证：查看 `pyproject.toml` 是否有 lint 配置；若有则运行对应命令，期望退出码为 0）

## 端到端场景
- [ ] 场景 1：stdio MCP 工具可在真实 TUI 对话中被发现和调用（验证：在 tmux 中启动 `python tests/e2e_mock_openai_server.py 18765`；配置 `.mewcode.yaml` 使用 mock OpenAI，并配置 `mcp_servers.local_demo` 为 `type: stdio`、`command: python`、`args: ["tests/fixtures/mcp_stdio_server.py"]`；在另一个 tmux pane 启动 `mewcode`，输入“调用 local_demo 的 echo 工具返回 hello-mcp”；观察工具状态显示 `local_demo__echo`，最终回复引用 MCP 工具结果，输入区恢复可用）
- [ ] 场景 2：Streamable HTTP MCP 工具可在真实 TUI 对话中被发现和调用（验证：在 tmux 中启动 `python tests/fixtures/mcp_http_server.py 18766` 和 `python tests/e2e_mock_openai_server.py 18765`；配置 `.mewcode.yaml` 使用 mock OpenAI，并配置 `mcp_servers.remote_demo` 为 `type: http`、`url: http://127.0.0.1:18766/mcp`；启动 `mewcode`，输入“调用 remote_demo 的 echo 工具返回 http-mcp”；观察工具状态显示 `remote_demo__echo`，最终回复引用 MCP 工具结果）
- [ ] 场景 3：一个 MCP Server 失败不会影响内置工具和另一个成功 Server（验证：在 tmux 中只启动成功的 HTTP MCP Server 和 mock OpenAI；配置一个 `remote_demo` 指向成功 HTTP Server，另一个 `broken_demo` 指向未监听端口；启动 `mewcode`，观察 stderr 出现 `broken_demo` warning 但 TUI 正常进入；输入“调用 remote_demo 的 echo 工具返回 ok”；观察 `remote_demo__echo` 正常完成）
- [ ] 场景 4：未配置 MCP Server 时默认能力不回退（验证：移除 `.mewcode.yaml` 中的 `mcp_servers`，保留 mock OpenAI 配置；在 tmux 启动 `mewcode`，输入“读取 README.md 并总结一句”；观察调用内置 `read_file` 而不是 MCP 工具，最终回复正常且输入区恢复可用）
