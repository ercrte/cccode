# MewCode 工具系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] 六个核心工具均已注册，名称为 `read_file`、`write_file`、`edit_file`、`run_command`、`find_files`、`search_code`（验证：运行 `python -m pytest tests/test_tools.py::test_default_registry_contains_six_core_tools -q`，期望通过）
- [ ] 工具元信息包含名称、描述、参数 Schema 和超时设置，并可序列化给模型协议层使用（验证：运行 `python -m pytest tests/test_tools.py::test_default_registry_contains_six_core_tools tests/test_openai_provider.py::test_openai_request_includes_tools_when_available tests/test_anthropic_provider.py::test_anthropic_request_includes_tools_when_available -q`，期望通过）
- [ ] 参数 Schema 校验能拦截缺失必填字段、未知字段、类型错误和枚举错误（验证：运行 `python -m pytest tests/test_tools.py::test_validate_arguments_accepts_valid_object tests/test_tools.py::test_validate_arguments_reports_errors -q`，期望通过）
- [ ] 读取文件工具能返回存在文件的内容，且路径不存在或路径为目录时返回失败（验证：运行 `python -m pytest tests/test_tools.py::test_read_file_returns_content tests/test_tools.py::test_read_file_reports_missing_path tests/test_tools.py::test_read_file_rejects_directory -q`，期望通过）
- [ ] 写入文件工具能创建父目录、创建新文件并覆盖已有文件（验证：运行 `python -m pytest tests/test_tools.py::test_write_file_creates_file tests/test_tools.py::test_write_file_overwrites_file tests/test_tools.py::test_write_file_creates_parent_directories -q`，期望通过）
- [ ] 修改文件工具只在原文唯一匹配时写回，匹配不到或匹配多次时文件不变（验证：运行 `python -m pytest tests/test_tools.py::test_edit_file_replaces_unique_text tests/test_tools.py::test_edit_file_rejects_missing_text_without_writing tests/test_tools.py::test_edit_file_rejects_multiple_matches_without_writing -q`，期望通过）
- [ ] 执行命令工具返回退出码、标准输出和标准错误，并使用当前工具上下文工作目录（验证：运行 `python -m pytest tests/test_tools.py::test_run_command_returns_exit_code_and_output tests/test_tools.py::test_run_command_uses_context_cwd -q`，期望通过）
- [ ] 执行命令超时时会停止执行并返回结构化失败结果（验证：运行 `python -m pytest tests/test_tools.py::test_run_command_times_out tests/test_tool_executor.py::test_executor_wraps_timeout -q`，期望通过）
- [ ] 找文件工具返回匹配文件列表，空结果是成功响应且列表为空（验证：运行 `python -m pytest tests/test_tools.py::test_find_files_returns_matching_files tests/test_tools.py::test_find_files_returns_empty_matches tests/test_tools.py::test_find_files_respects_max_results -q`，期望通过）
- [ ] 搜索代码工具返回匹配位置和文本摘要，空结果是成功响应且列表为空（验证：运行 `python -m pytest tests/test_tools.py::test_search_code_returns_matches tests/test_tools.py::test_search_code_returns_empty_matches tests/test_tools.py::test_search_code_respects_path_and_max_results -q`，期望通过）
- [ ] 工具执行器把未知工具、无效 JSON、参数错误、工具业务错误、超时和未预期异常包装成结构化失败结果（验证：运行 `python -m pytest tests/test_tool_executor.py -q`，期望通过）

## 集成
- [ ] OpenAI 请求在有工具可用时包含六个核心工具的 `tools` 描述，纯聊天兼容路径仍可不带工具字段（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_request_payload_and_headers tests/test_openai_provider.py::test_openai_request_includes_tools_when_available -q`，期望通过）
- [ ] Anthropic 请求在有工具可用时包含六个核心工具的 `tools` 描述，纯聊天兼容路径仍可不带工具字段（验证：运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_request_payload_and_headers tests/test_anthropic_provider.py::test_anthropic_request_includes_tools_when_available -q`，期望通过）
- [ ] OpenAI Provider 能把 assistant 工具调用消息和 tool 结果消息转换为协议可识别格式（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_payload_includes_tool_messages -q`，期望通过）
- [ ] Anthropic Provider 能把 assistant `tool_use` 消息和 `tool_result` 消息转换为协议可识别格式，并标记失败工具结果（验证：运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_payload_includes_tool_use_and_tool_result_messages -q`，期望通过）
- [ ] OpenAI 流式 `tool_calls` 参数碎片能拼接为完整工具调用，无效 JSON 不会导致 Provider 崩溃（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_streams_tool_call_deltas_and_done tests/test_openai_provider.py::test_openai_tool_call_invalid_json_becomes_parse_error -q`，期望通过）
- [ ] Anthropic 流式 `tool_use` 和 `input_json_delta` 参数碎片能拼接为完整工具调用，无效 JSON 不会导致 Provider 崩溃（验证：运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_streams_tool_call_deltas_and_done tests/test_anthropic_provider.py::test_anthropic_tool_call_invalid_json_becomes_parse_error -q`，期望通过）
- [ ] 编排器在无工具调用时保持纯聊天流式体验，并把多轮上下文写入会话（验证：运行 `python -m pytest tests/test_agent.py::test_runner_streams_plain_chat_and_saves_message tests/test_tui_smoke.py::test_second_turn_receives_previous_context -q`，期望通过）
- [ ] 编排器能执行一轮工具调用，把工具结果回灌到对话历史，并让模型生成最终回复（验证：运行 `python -m pytest tests/test_agent.py::test_runner_executes_one_tool_and_feeds_result_back -q`，期望通过）
- [ ] 编排器遇到第二轮再次请求工具时不执行第二次工具，并产生清晰限制提示（验证：运行 `python -m pytest tests/test_agent.py::test_runner_stops_when_second_response_requests_tool -q`，期望通过）
- [ ] 工具执行失败时，失败结果仍会回灌给模型，应用不会中断（验证：运行 `python -m pytest tests/test_agent.py::test_runner_feeds_tool_error_back_to_model tests/test_tui_smoke.py::test_tool_failure_recovers_input -q`，期望通过）
- [ ] TUI 能展示工具名称、运行状态、成功/失败状态和最终模型回复（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_tool_status_view_renders_running_and_finished_states tests/test_tui_smoke.py::test_submit_shows_tool_status_and_final_answer -q`，期望通过）
- [ ] Provider、编排器和 TUI 都继续使用统一事件模型，TUI 不依赖 OpenAI 或 Anthropic 原始流事件（验证：运行 `python -m pytest tests/test_tui_smoke.py tests/test_agent.py tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望通过）
- [ ] README 描述当前工具系统能力和“不做自动循环”的边界，不再保留纯对话旧结论（验证：运行 `rg -n "纯对话|tool use|read_file|write_file|edit_file|run_command|find_files|search_code|自动循环" README.md`，期望看到工具能力和边界说明，且不再出现“当前版本只做纯对话，不实现 tool use”的旧结论）

## 编译与测试
- [ ] 本地包可编辑安装成功（验证：运行 `python -m pip install -e ".[dev]"`，期望退出码为 0）
- [ ] 工具层测试全部通过（验证：运行 `python -m pytest tests/test_tools.py tests/test_tool_executor.py -q`，期望全部通过）
- [ ] Provider 工具协议测试全部通过（验证：运行 `python -m pytest tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望全部通过）
- [ ] 编排器、会话和 TUI 测试全部通过（验证：运行 `python -m pytest tests/test_agent.py tests/test_session.py tests/test_tui_smoke.py -q`，期望全部通过）
- [ ] 全部自动化测试通过（验证：运行 `python -m pytest -q`，期望全部通过）
- [ ] Python 文件无语法错误（验证：运行 `python -m compileall src tests`，期望无编译错误）
- [ ] 命令入口可导入（验证：运行 `python -c "from mewcode.cli import main; print(callable(main))"`，期望输出 `True`）
- [ ] 项目未配置 lint 命令时记录为不适用；如后续配置 lint，则 lint 检查通过（验证：查看 `pyproject.toml` 是否有 lint 配置；若有则运行对应命令，期望退出码为 0）

## 端到端场景
- [ ] 场景 1：OpenAI mock 纯聊天仍流式输出（验证：在 tmux 中启动 `python tests/e2e_mock_openai_server.py 18765`，配置 MewCode 指向该服务，启动 `mewcode`，输入“用一句话解释递归”，观察回复逐步出现且输入区恢复可用）
- [ ] 场景 2：读取文件工具链路（验证：在项目中准备一个文本文件；tmux 中输入“读取这个文件并总结内容：<路径>”，观察界面显示 `read_file` 工具执行，最终回复引用文件内容）
- [ ] 场景 3：写入文件工具链路（验证：tmux 中输入“创建 tmp/tool-demo.txt，内容是 MewCode tool ok”，观察界面显示 `write_file` 工具执行，最终回复说明写入成功；随后检查目标文件内容符合请求）
- [ ] 场景 4：修改文件唯一匹配链路（验证：准备只包含一次目标原文的文件；tmux 中输入“把 <路径> 里的旧字符串替换为新字符串”，观察界面显示 `edit_file` 工具执行，最终回复说明替换成功；随后检查文件只发生预期替换）
- [ ] 场景 5：修改文件匹配不到时不写入（验证：准备不包含目标原文的文件；tmux 中输入修改请求，观察 `edit_file` 返回未匹配失败，最终回复解释未修改；随后检查文件内容保持不变）
- [ ] 场景 6：修改文件匹配多次时不写入（验证：准备目标原文出现两次的文件；tmux 中输入修改请求，观察 `edit_file` 返回匹配不唯一失败，最终回复解释未修改；随后检查文件内容保持不变）
- [ ] 场景 7：执行命令工具链路（验证：tmux 中输入“执行 `python -c \"print('mew')\"` 并告诉我输出”，观察界面显示 `run_command` 工具执行，最终回复包含退出码 0 和输出 `mew`）
- [ ] 场景 8：执行命令超时恢复（验证：tmux 中输入一个会超时的命令请求，观察 `run_command` 返回超时失败，界面不崩溃，输入区恢复可用并能继续提交下一条消息）
- [ ] 场景 9：找文件工具链路（验证：tmux 中输入“找出 tests 目录下匹配 `test_*provider.py` 的文件”，观察界面显示 `find_files` 工具执行，最终回复列出匹配路径；输入不存在模式时最终回复说明结果为空）
- [ ] 场景 10：搜索代码工具链路（验证：tmux 中输入“搜索代码里 `ChatSession` 出现的位置”，观察界面显示 `search_code` 工具执行，最终回复引用匹配文件和行号；搜索不存在文本时最终回复说明结果为空）
- [ ] 场景 11：无效工具或无效参数恢复（验证：用 mock server 触发不存在工具或缺失必填参数，观察工具结果为结构化失败，最终回复说明失败原因，输入区恢复可用）
- [ ] 场景 12：连续工具调用被拦截（验证：用 mock server 在工具结果回灌后再次返回工具调用，观察界面显示本阶段不支持连续工具调用，且没有执行第二个工具）
- [ ] 场景 13：Anthropic 工具协议兼容（验证：运行 Anthropic Provider 工具相关测试；如有可用 Anthropic 配置，再在 tmux 中输入读取文件请求，观察工具调用状态和最终回复行为与 OpenAI 场景一致）
- [ ] 场景 14：错误脱敏与继续输入（验证：配置无效密钥或让 mock server 返回错误，tmux 中输入问题，观察错误信息不包含完整密钥，输入区恢复可用，退出操作仍有效）
