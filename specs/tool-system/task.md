# JulyCode 工具系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/julycode/tools/__init__.py` | 工具包公共导出 |
| 新建 | `src/julycode/tools/base.py` | Tool 接口、ToolSpec、ToolCall、ToolResult、ToolContext、工具异常 |
| 新建 | `src/julycode/tools/validation.py` | 内置工具参数 Schema 子集校验 |
| 新建 | `src/julycode/tools/builtin.py` | 六个核心工具实现 |
| 新建 | `src/julycode/tools/registry.py` | 工具注册中心和默认工具注册 |
| 新建 | `src/julycode/tools/executor.py` | 工具执行、超时和结构化错误包装 |
| 新建 | `src/julycode/agent.py` | 一次工具调用链路编排和 TurnEvent |
| 修改 | `src/julycode/providers/base.py` | Provider 统一消息、请求和流事件支持工具调用 |
| 修改 | `src/julycode/providers/openai.py` | OpenAI 工具描述、工具消息和流式 tool_calls 适配 |
| 修改 | `src/julycode/providers/anthropic.py` | Anthropic tools、tool_use、tool_result 和流式参数适配 |
| 修改 | `src/julycode/session.py` | 会话历史支持工具调用消息和工具结果消息 |
| 修改 | `src/julycode/tui/widgets.py` | 工具调用状态视图 |
| 修改 | `src/julycode/tui/app.py` | 使用工具编排器并展示工具状态 |
| 修改 | `src/julycode/cli.py` | 创建默认工具注册中心、执行器和工具上下文 |
| 修改 | `README.md` | 更新工具系统能力和范围说明 |
| 新建 | `tests/test_tools.py` | 工具基础模型、参数校验、六个核心工具行为测试 |
| 新建 | `tests/test_tool_executor.py` | 工具执行器成功、失败、超时和参数错误测试 |
| 新建 | `tests/test_agent.py` | 一轮工具编排、结果回灌和连续工具拦截测试 |
| 修改 | `tests/test_openai_provider.py` | OpenAI 工具请求、工具消息和流式 tool_calls 测试 |
| 修改 | `tests/test_anthropic_provider.py` | Anthropic 工具请求、tool_use 和 tool_result 测试 |
| 修改 | `tests/test_session.py` | 工具消息进入会话上下文测试 |
| 修改 | `tests/test_tui_smoke.py` | 工具状态展示、错误恢复和纯聊天兼容测试 |
| 修改 | `tests/e2e_mock_openai_server.py` | 增加工具调用模拟响应用于 tmux 端到端 |

## T1: 工具基础模型

**文件：** `src/julycode/tools/base.py`, `src/julycode/tools/__init__.py`, `tests/test_tools.py`  
**依赖：** 无  
**步骤：**
1. 新建工具包并定义 `ToolSpec`、`ToolContext`、`ToolCall`、`ToolResult`、`ToolExecutionError` 和 `Tool` 协议。
2. 实现 `ToolResult.to_model_content()`，输出稳定 JSON 字符串，包含 `success`、`data`、`error_type`、`error`、`elapsed_ms`。
3. 在 `src/julycode/tools/__init__.py` 导出基础类型。
4. 添加测试覆盖成功结果、失败结果、JSON 序列化和包导入。

**验证：** 运行 `python -m pytest tests/test_tools.py::test_tool_result_serializes_success tests/test_tools.py::test_tool_result_serializes_error -q`，期望全部通过。

## T2: 参数 Schema 校验

**文件：** `src/julycode/tools/validation.py`, `tests/test_tools.py`  
**依赖：** T1  
**步骤：**
1. 实现 `validate_arguments(schema, arguments)`。
2. 支持 `object`、`properties`、`required`、`additionalProperties`、`string`、`number`、`integer`、`boolean`、`array` 和 `enum`。
3. 校验结果返回错误字符串列表，不直接抛异常。
4. 添加测试覆盖缺失必填字段、未知字段、类型错误、枚举错误和合法参数。

**验证：** 运行 `python -m pytest tests/test_tools.py::test_validate_arguments_accepts_valid_object tests/test_tools.py::test_validate_arguments_reports_errors -q`，期望全部通过。

## T3: 读取文件工具

**文件：** `src/julycode/tools/builtin.py`, `tests/test_tools.py`  
**依赖：** T1  
**步骤：**
1. 在 `builtin.py` 中实现 `ReadFileTool`，名称为 `read_file`。
2. 定义读取文件工具的参数 Schema 和详细描述。
3. 成功时按 UTF-8 返回 `path`、`content` 和 `truncated`。
4. 路径不存在、路径不是文件、读取失败或解码失败时抛出 `ToolExecutionError`。
5. 添加测试覆盖成功读取、不存在路径和目录路径。

**验证：** 运行 `python -m pytest tests/test_tools.py::test_read_file_returns_content tests/test_tools.py::test_read_file_reports_missing_path tests/test_tools.py::test_read_file_rejects_directory -q`，期望全部通过。

## T4: 写入文件工具

**文件：** `src/julycode/tools/builtin.py`, `tests/test_tools.py`  
**依赖：** T1  
**步骤：**
1. 实现 `WriteFileTool`，名称为 `write_file`。
2. 定义写入文件工具的参数 Schema 和详细描述。
3. 写入时自动创建父目录，并按 UTF-8 覆盖目标文件。
4. 成功时返回 `path`、`bytes_written` 和 `created`。
5. 添加测试覆盖创建新文件、覆盖已有文件和父目录自动创建。

**验证：** 运行 `python -m pytest tests/test_tools.py::test_write_file_creates_file tests/test_tools.py::test_write_file_overwrites_file tests/test_tools.py::test_write_file_creates_parent_directories -q`，期望全部通过。

## T5: 原文唯一替换工具

**文件：** `src/julycode/tools/builtin.py`, `tests/test_tools.py`  
**依赖：** T1  
**步骤：**
1. 实现 `EditFileTool`，名称为 `edit_file`。
2. 定义 `path`、`old_text`、`new_text` 参数 Schema 和详细描述。
3. 读取目标文件后统计 `old_text` 出现次数。
4. 恰好一次时写回替换结果，并返回 `path`、`replacements`。
5. 匹配不到或匹配多次时抛出 `ToolExecutionError`，且不得修改文件。
6. 添加测试覆盖唯一替换、零匹配不修改、多匹配不修改。

**验证：** 运行 `python -m pytest tests/test_tools.py::test_edit_file_replaces_unique_text tests/test_tools.py::test_edit_file_rejects_missing_text_without_writing tests/test_tools.py::test_edit_file_rejects_multiple_matches_without_writing -q`，期望全部通过。

## T6: 命令执行工具

**文件：** `src/julycode/tools/builtin.py`, `tests/test_tools.py`  
**依赖：** T1  
**步骤：**
1. 实现 `RunCommandTool`，名称为 `run_command`。
2. 定义 `command` 和可选 `timeout_seconds` 参数 Schema。
3. 在 `ToolContext.cwd` 下异步执行命令，返回 `exit_code`、`stdout`、`stderr`、`timed_out` 和 `truncated`。
4. 命令超时时终止进程并抛出 `ToolExecutionError`。
5. 添加测试覆盖成功命令、非零退出码、工作目录生效和超时。

**验证：** 运行 `python -m pytest tests/test_tools.py::test_run_command_returns_exit_code_and_output tests/test_tools.py::test_run_command_uses_context_cwd tests/test_tools.py::test_run_command_times_out -q`，期望全部通过。

## T7: 按模式找文件工具

**文件：** `src/julycode/tools/builtin.py`, `tests/test_tools.py`  
**依赖：** T1  
**步骤：**
1. 实现 `FindFilesTool`，名称为 `find_files`。
2. 定义 `pattern` 和可选 `max_results` 参数 Schema。
3. 以 `ToolContext.cwd` 为基准执行 glob 查找，只返回文件相对路径。
4. 没有匹配时返回成功且 `matches` 为空列表。
5. 添加测试覆盖匹配文件、忽略目录、空结果和 `max_results`。

**验证：** 运行 `python -m pytest tests/test_tools.py::test_find_files_returns_matching_files tests/test_tools.py::test_find_files_returns_empty_matches tests/test_tools.py::test_find_files_respects_max_results -q`，期望全部通过。

## T8: 搜索代码内容工具

**文件：** `src/julycode/tools/builtin.py`, `tests/test_tools.py`  
**依赖：** T1  
**步骤：**
1. 实现 `SearchCodeTool`，名称为 `search_code`。
2. 定义 `pattern`、可选 `path`、可选 `glob`、可选 `max_results` 参数 Schema。
3. 优先使用 `rg` 输出路径、行号、列号和匹配文本；没有 `rg` 时使用 Python 递归文本扫描兜底。
4. 没有匹配时返回成功且 `matches` 为空列表。
5. 添加测试覆盖命中结果、空结果、路径范围和 `max_results`。

**验证：** 运行 `python -m pytest tests/test_tools.py::test_search_code_returns_matches tests/test_tools.py::test_search_code_returns_empty_matches tests/test_tools.py::test_search_code_respects_path_and_max_results -q`，期望全部通过。

## T9: 工具注册中心

**文件：** `src/julycode/tools/registry.py`, `src/julycode/tools/__init__.py`, `tests/test_tools.py`  
**依赖：** T3, T4, T5, T6, T7, T8  
**步骤：**
1. 实现 `ToolRegistry.register()`、`get()`、`list()` 和 `specs()`。
2. 注册重复工具名时抛出可理解错误。
3. 实现 `create_default_registry()`，注册六个核心工具。
4. 在工具包导出注册中心相关对象。
5. 添加测试断言默认注册中心包含且只包含六个核心工具名。

**验证：** 运行 `python -m pytest tests/test_tools.py::test_registry_returns_registered_tool tests/test_tools.py::test_registry_rejects_duplicate_names tests/test_tools.py::test_default_registry_contains_six_core_tools -q`，期望全部通过。

## T10: 工具执行器

**文件：** `src/julycode/tools/executor.py`, `src/julycode/tools/__init__.py`, `tests/test_tool_executor.py`  
**依赖：** T2, T9  
**步骤：**
1. 实现 `ToolExecutor`，接收 `ToolRegistry` 和 `ToolContext`。
2. 对未知工具返回 `success=false`、`error_type="unknown_tool"` 的 `ToolResult`。
3. 对 `ToolCall.parse_error` 返回 `error_type="invalid_json"`。
4. 对参数 Schema 校验失败返回 `error_type="invalid_arguments"`。
5. 使用工具级或调用参数中的超时时间执行工具。
6. 捕获 `ToolExecutionError`、超时和未预期异常，并包装为结构化失败结果。
7. 添加测试覆盖成功执行、未知工具、参数错误、工具业务错误、超时和异常包装。

**验证：** 运行 `python -m pytest tests/test_tool_executor.py -q`，期望全部通过。

## T11: Provider 基础结构与会话扩展

**文件：** `src/julycode/providers/base.py`, `src/julycode/session.py`, `tests/test_session.py`, `tests/test_tui_smoke.py`  
**依赖：** T1  
**步骤：**
1. 将 `ChatRole` 扩展为 `user`、`assistant`、`tool`。
2. 为 `ChatMessage` 增加 `tool_calls`、`tool_call_id`、`tool_result_is_error` 字段，并保持现有纯文本用法兼容。
3. 为 `ChatRequest` 增加 `tools` 字段，默认空元组。
4. 为 `StreamEvent` 增加 `tool_call_delta` 相关字段。
5. 在 `ChatSession` 中新增 `append_tool_result(result)`，并让 `build_request(tools=...)` 携带工具列表。
6. 更新现有测试中构造 `ChatRequest` 和 `ChatMessage` 的断言，保持纯聊天测试通过。

**验证：** 运行 `python -m pytest tests/test_session.py tests/test_tui_smoke.py::test_submit_streams_text_into_message_view -q`，期望全部通过。

## T12: OpenAI 工具请求和消息格式

**文件：** `src/julycode/providers/openai.py`, `tests/test_openai_provider.py`  
**依赖：** T1, T11  
**步骤：**
1. 将 `ToolSpec` 转换为 OpenAI Chat Completions `tools` function 格式。
2. 当 `ChatRequest.tools` 非空时，在请求体中加入 `tools`。
3. 将带 `tool_calls` 的 assistant 消息转换为 OpenAI assistant tool_calls 消息。
4. 将内部 `role="tool"` 消息转换为 OpenAI `role: tool`、`tool_call_id` 和 `content`。
5. 调整旧测试：纯聊天请求仍不带 `tools`。
6. 添加测试覆盖工具定义、assistant tool_calls 消息和 tool 结果消息。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py::test_openai_request_payload_and_headers tests/test_openai_provider.py::test_openai_request_includes_tools_when_available tests/test_openai_provider.py::test_openai_payload_includes_tool_messages -q`，期望全部通过。

## T13: OpenAI 流式工具调用解析

**文件：** `src/julycode/providers/openai.py`, `tests/test_openai_provider.py`  
**依赖：** T12  
**步骤：**
1. 在流式解析中识别 `choices[*].delta.tool_calls`。
2. 按 `index` 聚合 `id`、`type`、`function.name` 和分片 `function.arguments`。
3. 每个参数分片到达时发出 `tool_call_delta`。
4. `message_done` 中返回包含 `ToolCall` 的 assistant `ChatMessage`。
5. 参数 JSON 解析失败时保留 `raw_arguments` 和 `parse_error`，不抛 Provider 异常。
6. 添加测试覆盖分片参数拼接、多个 chunk 中 name 只出现一次和无效 JSON。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py::test_openai_streams_tool_call_deltas_and_done tests/test_openai_provider.py::test_openai_tool_call_invalid_json_becomes_parse_error -q`，期望全部通过。

## T14: Anthropic 工具请求和消息格式

**文件：** `src/julycode/providers/anthropic.py`, `tests/test_anthropic_provider.py`  
**依赖：** T1, T11  
**步骤：**
1. 将 `ToolSpec` 转换为 Anthropic 顶层 `tools` 格式。
2. 当 `ChatRequest.tools` 非空时，在请求体中加入 `tools`。
3. 将带 `tool_calls` 的 assistant 消息转换为包含 `text` 和 `tool_use` 块的 content 数组。
4. 将内部 `role="tool"` 消息转换为紧随其后的 `role: user` 且首个 content 块为 `tool_result`。
5. 失败工具结果设置 `is_error: true`。
6. 调整旧测试：纯聊天请求仍不带 `tools`。
7. 添加测试覆盖工具定义、assistant tool_use 消息、成功 tool_result 和失败 tool_result。

**验证：** 运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_request_payload_and_headers tests/test_anthropic_provider.py::test_anthropic_request_includes_tools_when_available tests/test_anthropic_provider.py::test_anthropic_payload_includes_tool_use_and_tool_result_messages -q`，期望全部通过。

## T15: Anthropic 流式工具调用解析

**文件：** `src/julycode/providers/anthropic.py`, `tests/test_anthropic_provider.py`  
**依赖：** T14  
**步骤：**
1. 在 `content_block_start` 中识别 `tool_use` 块，记录 block index、tool id 和工具名。
2. 在 `content_block_delta` 中识别 `input_json_delta`，拼接 `partial_json`。
3. 在 `content_block_stop` 后将完整输入解析为 `ToolCall`。
4. 每个参数分片到达时发出 `tool_call_delta`。
5. `message_done` 中返回同时包含文本、thinking 和 `tool_calls` 的 assistant `ChatMessage`。
6. 参数 JSON 解析失败时保留 `raw_arguments` 和 `parse_error`，不抛 Provider 异常。
7. 添加测试覆盖 tool_use 分片解析、文本加 tool_use 混合响应、无效 JSON。

**验证：** 运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_streams_tool_call_deltas_and_done tests/test_anthropic_provider.py::test_anthropic_tool_call_invalid_json_becomes_parse_error -q`，期望全部通过。

## T16: 编排器纯聊天路径

**文件：** `src/julycode/agent.py`, `tests/test_agent.py`  
**依赖：** T10, T11  
**步骤：**
1. 定义 `TurnEvent` 和 `ToolAwareTurnRunner`。
2. 实现 `run(user_text)` 的纯聊天路径：追加用户消息、传入工具列表、透传文本和 thinking、保存 assistant 完成消息。
3. 编排器使用 `registry.specs()` 构造请求。
4. 添加假 Provider 测试：无工具调用时只发出文本和完成事件，会话包含用户消息和 assistant 消息。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_streams_plain_chat_and_saves_message -q`，期望通过。

## T17: 编排器一轮工具执行与结果回灌

**文件：** `src/julycode/agent.py`, `tests/test_agent.py`  
**依赖：** T16  
**步骤：**
1. 第一轮 `message_done` 包含一个 `ToolCall` 时，保存 assistant 工具调用消息。
2. 发出 `tool_started`，调用 `ToolExecutor.execute()`。
3. 保存工具结果消息并发出 `tool_finished`。
4. 发起第二轮 Provider 请求，让模型基于工具结果生成最终回复。
5. 保存最终 assistant 消息并发出 `message_done`。
6. 添加测试断言第二轮请求历史顺序为 user、assistant tool_calls、tool result，并包含工具列表。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_executes_one_tool_and_feeds_result_back -q`，期望通过。

## T18: 编排器工具边界和失败路径

**文件：** `src/julycode/agent.py`, `tests/test_agent.py`  
**依赖：** T17  
**步骤：**
1. 第一轮响应包含多个工具调用时，只执行第一个，并在工具结果中说明本阶段只支持一个工具调用。
2. 第二轮响应再次包含工具调用时，发出 `tool_limit_reached`，不执行第二次工具。
3. 工具执行失败时仍把失败 `ToolResult` 回灌给模型。
4. Provider 抛出错误时发出 `error` 事件并保持输入调用方可恢复。
5. 添加测试覆盖连续工具调用拦截、工具失败结果回灌和 Provider 错误事件。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_stops_when_second_response_requests_tool tests/test_agent.py::test_runner_feeds_tool_error_back_to_model tests/test_agent.py::test_runner_reports_provider_error -q`，期望全部通过。

## T19: 工具状态视图

**文件：** `src/julycode/tui/widgets.py`, `tests/test_tui_smoke.py`  
**依赖：** T1  
**步骤：**
1. 新增 `ToolStatusView`，显示工具名称、运行状态、成功/失败和简短摘要。
2. 为工具状态视图添加稳定样式，避免改变现有消息区布局。
3. 在成功结果中展示工具名和完成状态，在失败结果中展示工具名和错误类型。
4. 添加组件测试覆盖初始运行状态、成功完成状态和失败状态。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_tool_status_view_renders_running_and_finished_states -q`，期望通过。

## T20: TUI 接入工具编排器

**文件：** `src/julycode/tui/app.py`, `src/julycode/tui/widgets.py`, `tests/test_tui_smoke.py`  
**依赖：** T16, T17, T18, T19  
**步骤：**
1. 调整 `JulyCodeApp` 构造参数，接收 `ToolRegistry` 和 `ToolExecutor`。
2. 提交输入后使用 `ToolAwareTurnRunner.run()`，不再直接遍历 Provider。
3. 收到 `text_delta` 和 `thinking_delta` 时保持现有增量渲染。
4. 收到 `tool_started` 时在消息区追加工具状态视图。
5. 收到 `tool_finished` 时更新工具状态视图。
6. 收到 `tool_limit_reached` 时显示清晰提示，并恢复输入。
7. 更新现有 TUI 测试用例，使纯聊天、thinking、多轮上下文和错误恢复继续通过。
8. 添加测试覆盖工具调用状态展示和工具失败后输入恢复。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -q`，期望全部通过。

## T21: CLI 集成默认工具

**文件：** `src/julycode/cli.py`, `tests/test_tui_smoke.py`, `tests/test_config.py`  
**依赖：** T9, T10, T20  
**步骤：**
1. 在 CLI 启动时创建 `create_default_registry()`。
2. 用当前工作目录创建 `ToolContext`。
3. 创建 `ToolExecutor` 并传入 `JulyCodeApp`。
4. 保持配置错误脱敏和非零退出码行为不变。
5. 更新 CLI 可导入和配置错误测试，确认构造路径不破坏旧行为。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_cli_entrypoint_is_importable tests/test_config.py::test_cli_reports_config_error_without_secret -q`，期望全部通过。

## T22: README 范围更新

**文件：** `README.md`  
**依赖：** T20, T21  
**步骤：**
1. 更新项目描述，说明当前版本支持一次工具调用链路。
2. 列出六个核心工具和“本阶段不做自动循环”的边界。
3. 保留现有安装、配置和启动说明。
4. 移除“只做纯对话、不实现 tool use”的过期表述。

**验证：** 运行 `rg -n "纯对话|tool use|read_file|write_file|edit_file|run_command|find_files|search_code|自动循环" README.md`，期望看到工具能力和边界说明，且不再出现“当前版本只做纯对话，不实现 tool use”的旧结论。

## T23: Mock OpenAI 工具端到端服务

**文件：** `tests/e2e_mock_openai_server.py`  
**依赖：** T12, T13, T17  
**步骤：**
1. 扩展 mock server，识别请求是否包含 `tools`。
2. 当用户消息要求读取、写入、修改、执行命令、找文件或搜代码时，先流式返回对应 OpenAI `tool_calls`。
3. 当请求历史包含 `role: tool` 结果时，返回基于工具结果的最终文本回复。
4. 保持原有纯聊天和多轮记忆场景可用。
5. 手动运行 mock server 并用简单 HTTP 请求验证能返回工具调用流。

**验证：** 运行 `python tests/e2e_mock_openai_server.py 18765` 后，用测试请求触发工具调用，期望响应中出现 `delta.tool_calls`；结束服务后运行 `python -m pytest tests/test_openai_provider.py::test_openai_streams_tool_call_deltas_and_done -q`，期望通过。

## T24: 全量回归和编译检查

**文件：** `src/julycode/**/*.py`, `tests/**/*.py`  
**依赖：** T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12, T13, T14, T15, T16, T17, T18, T19, T20, T21, T22, T23  
**步骤：**
1. 运行全部单元测试并修复失败。
2. 运行 Python 编译检查并修复语法错误。
3. 确认旧的纯聊天测试仍通过。
4. 确认新增工具、Provider、编排器和 TUI 测试都通过。

**验证：** 运行 `python -m pytest -q` 和 `python -m compileall src tests`，期望全部通过。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18 → T19 → T20 → T21 → T22 → T23 → T24
```
