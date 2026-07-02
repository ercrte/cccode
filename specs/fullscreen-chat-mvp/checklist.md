# MewCode 全屏对话 MVP Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] 全屏 TUI 启动后包含消息区、输入区、状态提示和退出方式（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_app_starts_with_fullscreen_regions -q`，期望通过）
- [ ] 用户提交消息后，助手回复以增量方式进入消息区（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_submit_streams_text_into_message_view -q`，期望通过）
- [ ] 同一运行期内第二轮请求包含前一轮上下文（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_second_turn_receives_previous_context tests/test_session.py -q`，期望通过）
- [ ] 配置支持 `protocol`、`model`、`base_url`、`api_key` 四个核心 YAML 字段（验证：运行 `python -m pytest tests/test_config.py::test_loads_required_yaml_fields -q`，期望通过）
- [ ] 项目级 `.mewcode.yaml` 覆盖用户级 `~/.mewcode/config.yaml` 的同名字段（验证：运行 `python -m pytest tests/test_config.py::test_project_config_overrides_user_config -q`，期望通过）
- [ ] `api_key` 支持明文和 `${ENV_VAR}`，环境变量缺失时错误清晰且不泄露密钥（验证：运行 `python -m pytest tests/test_config.py::test_api_key_can_reference_environment_variable tests/test_config.py::test_missing_environment_api_key_is_clear_and_redacted -q`，期望通过）
- [ ] Claude extended thinking 内容进入独立可折叠区域，最终回复仍进入正常消息区域（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_submit_streams_thinking_into_collapsible_panel tests/test_anthropic_provider.py -q`，期望通过）
- [ ] 配置错误、认证失败、后端错误和流式错误会展示可理解错误，并恢复输入（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_provider_error_is_displayed_and_input_recovers tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望通过）
- [ ] 错误信息不会输出完整 API key（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_error_message_does_not_leak_secret tests/test_config.py::test_cli_reports_config_error_without_secret -q`，期望通过）
- [ ] 退出后会话历史不写入磁盘，重新启动不会自动恢复上一轮消息（验证：运行 `python -m pytest tests/test_session.py -q`，期望通过；再执行 tmux 场景 5 观察无历史恢复）

## 集成
- [ ] CLI 能加载配置、创建 Provider、启动 TUI 应用（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_cli_entrypoint_is_importable tests/test_config.py::test_cli_reports_config_error_without_secret -q`，期望通过）
- [ ] OpenAI Provider 使用统一 `StreamEvent` 输出文本增量和完成事件（验证：运行 `python -m pytest tests/test_openai_provider.py -q`，期望通过）
- [ ] Anthropic Provider 使用统一 `StreamEvent` 输出文本增量、thinking 增量和完成事件（验证：运行 `python -m pytest tests/test_anthropic_provider.py -q`，期望通过）
- [ ] TUI 只消费统一 Provider 接口，不依赖供应商原始 SSE 事件名（验证：运行 `python -m pytest tests/test_tui_smoke.py tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望通过）
- [ ] SSE 解析器正确处理命名事件、data-only 事件、多行 data 和注释行（验证：运行 `python -m pytest tests/test_sse.py -q`，期望通过）
- [ ] 第一版不会发送 tool use、函数调用、文件操作或代码编辑相关请求字段（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_request_payload_and_headers tests/test_anthropic_provider.py::test_anthropic_request_payload_and_headers -q`，期望通过，断言请求体不包含工具字段）
- [ ] README 中的 OpenAI、Anthropic、用户级配置、项目级覆盖和环境变量示例与实际配置加载行为一致（验证：运行 `python -m pytest tests/test_config.py -q`，期望通过；人工对照 README 示例字段）

## 编译与测试
- [ ] 本地包可编辑安装成功（验证：运行 `python -m pip install -e ".[dev]"`，期望退出码为 0）
- [ ] 全部自动化测试通过（验证：运行 `python -m pytest -q`，期望全部通过）
- [ ] Python 文件无语法错误（验证：运行 `python -m compileall src tests`，期望无编译错误）
- [ ] 命令入口可导入（验证：运行 `python -c "from mewcode.cli import main; print(callable(main))"`，期望输出 `True`）
- [ ] 如项目配置了 lint 或格式化命令，该命令通过（验证：运行 pyproject 中配置的 lint/format check，期望退出码为 0；若未配置则记录为不适用）

## 端到端场景
- [ ] 场景 1：OpenAI 纯对话流式输出（验证：准备有效 OpenAI 配置后，在 tmux 中启动 `mewcode`，输入“用一句话解释递归”，观察全屏界面逐步出现回复、状态从生成中恢复为空闲、可继续输入）
- [ ] 场景 2：OpenAI 多轮上下文（验证：同一 tmux 会话中先输入“记住我的代号是 Mew-17”，再输入“我的代号是什么？”，观察回复包含 `Mew-17`）
- [ ] 场景 3：Anthropic Claude 流式输出（验证：准备有效 Anthropic 配置后，在 tmux 中启动 `mewcode`，输入“用一句话解释 Python 协程”，观察回复逐步出现）
- [ ] 场景 4：Claude extended thinking 可折叠显示（验证：准备开启 thinking 的 Anthropic 配置，在 tmux 中输入一个需要推理的问题，观察 thinking 出现在独立可折叠区域，最终回复出现在普通消息区域）
- [ ] 场景 5：退出不恢复历史（验证：在 tmux 中完成一轮对话后退出，再重新启动 `mewcode`，观察消息区不包含上一轮对话）
- [ ] 场景 6：项目级配置覆盖用户级配置（验证：用户级配置写 OpenAI，项目级 `.mewcode.yaml` 写 Anthropic；在项目目录 tmux 启动后，观察状态栏显示项目级协议和模型）
- [ ] 场景 7：环境变量密钥缺失错误（验证：配置 `api_key: ${MEWCODE_MISSING_KEY}` 且不设置该变量，在 tmux 中启动 `mewcode`，观察错误说明变量缺失且不包含密钥内容）
- [ ] 场景 8：认证失败后可继续或退出（验证：配置无效密钥，在 tmux 中启动并输入问题，观察界面显示认证/请求错误，输入区恢复可用，退出操作仍有效）
- [ ] 场景 9：常见终端尺寸下布局不遮挡（验证：分别用约 `120x40` 和 `80x24` 的 tmux 窗口启动 `mewcode`，输入长文本，观察消息区、输入区、状态栏和退出提示互不遮挡）
