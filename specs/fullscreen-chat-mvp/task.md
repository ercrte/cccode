# MewCode 全屏对话 MVP Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `pyproject.toml` | 包元数据、依赖、命令入口、pytest 配置 |
| 新建 | `README.md` | 安装、配置、启动和最小使用说明 |
| 新建 | `src/mewcode/__init__.py` | 包版本 |
| 新建 | `src/mewcode/cli.py` | `mewcode` 命令入口 |
| 新建 | `src/mewcode/config.py` | YAML 配置加载、合并、校验和环境变量解析 |
| 新建 | `src/mewcode/errors.py` | 统一错误类型和密钥脱敏 |
| 新建 | `src/mewcode/session.py` | 当前进程内对话状态 |
| 新建 | `src/mewcode/providers/__init__.py` | Provider 包导出 |
| 新建 | `src/mewcode/providers/base.py` | Provider 接口、消息模型和统一流事件 |
| 新建 | `src/mewcode/providers/factory.py` | 根据协议创建 Provider |
| 新建 | `src/mewcode/providers/sse.py` | SSE 事件解析 |
| 新建 | `src/mewcode/providers/openai.py` | OpenAI Chat Completions 协议适配 |
| 新建 | `src/mewcode/providers/anthropic.py` | Anthropic Messages 协议适配 |
| 新建 | `src/mewcode/tui/__init__.py` | TUI 包导出 |
| 新建 | `src/mewcode/tui/app.py` | Textual 应用主体 |
| 新建 | `src/mewcode/tui/widgets.py` | 消息、思考区、输入区和状态栏组件 |
| 新建 | `tests/test_config.py` | 配置、环境变量和脱敏测试 |
| 新建 | `tests/test_sse.py` | SSE 解析测试 |
| 新建 | `tests/test_openai_provider.py` | OpenAI Provider 请求和流事件测试 |
| 新建 | `tests/test_anthropic_provider.py` | Anthropic Provider 请求、thinking 和错误测试 |
| 新建 | `tests/test_session.py` | 会话上下文测试 |
| 新建 | `tests/test_tui_smoke.py` | TUI 启动、提交、退出和错误恢复冒烟测试 |

## T1: 项目骨架与依赖

**文件：** `pyproject.toml`, `README.md`, `src/mewcode/__init__.py`, `src/mewcode/providers/__init__.py`, `src/mewcode/tui/__init__.py`  
**依赖：** 无  
**步骤：**
1. 创建 `src/` 布局和包目录。
2. 在 `pyproject.toml` 中配置包名、Python 版本、`mewcode` 命令入口、运行依赖 `textual`、`httpx`、`PyYAML` 和开发测试依赖。
3. 在 `src/mewcode/__init__.py` 中定义包版本。
4. 在 `README.md` 中写入安装、配置、启动和范围说明章节标题，内容只包含当前已确定的项目目标。

**验证：** 运行 `python -m pip install -e ".[dev]"`，期望本地包安装成功；运行 `python -c "import mewcode; print(mewcode.__version__)"`，期望打印版本号。

## T2: 错误类型与基础模型

**文件：** `src/mewcode/errors.py`, `src/mewcode/providers/base.py`, `tests/test_config.py`  
**依赖：** T1  
**步骤：**
1. 定义 `MewCodeError`、`ConfigError`、`ProviderError`。
2. 实现 `redact_secret(text, secret)`，确保完整密钥不会出现在错误文本中。
3. 定义 `ChatMessage`、`ChatRequest`、`StreamEvent` 和 `LLMProvider`。
4. 添加脱敏测试，覆盖传入明确密钥和无密钥两种情况。

**验证：** 运行 `python -m pytest tests/test_config.py::test_redact_secret_masks_exact_secret -q`，期望测试通过且断言结果不包含原始密钥。

## T3: 配置基础加载与校验

**文件：** `src/mewcode/config.py`, `tests/test_config.py`  
**依赖：** T2  
**步骤：**
1. 定义 `AppConfig` 和 `ThinkingConfig`。
2. 实现 YAML 文件读取和空文件处理。
3. 校验 `protocol`、`model`、`base_url`、`api_key` 四个必填字段。
4. 对未知协议、缺失字段和非法 YAML 抛出 `ConfigError`。
5. 添加基础配置加载和错误路径测试。

**验证：** 运行 `python -m pytest tests/test_config.py::test_loads_required_yaml_fields tests/test_config.py::test_missing_required_field_raises_config_error -q`，期望全部通过。

## T4: 配置发现、覆盖与环境变量密钥

**文件：** `src/mewcode/config.py`, `tests/test_config.py`  
**依赖：** T3  
**步骤：**
1. 实现用户级配置路径 `~/.mewcode/config.yaml`。
2. 实现从当前目录向上查找首个 `.mewcode.yaml`。
3. 实现用户级配置和项目级配置按字段浅合并，项目级覆盖同名字段。
4. 实现 `${ENV_VAR}` 形式的 `api_key` 解析。
5. 添加项目覆盖、环境变量存在、环境变量缺失和错误脱敏测试。

**验证：** 运行 `python -m pytest tests/test_config.py::test_project_config_overrides_user_config tests/test_config.py::test_api_key_can_reference_environment_variable tests/test_config.py::test_missing_environment_api_key_is_clear_and_redacted -q`，期望全部通过。

## T5: SSE 解析器

**文件：** `src/mewcode/providers/sse.py`, `tests/test_sse.py`  
**依赖：** T2  
**步骤：**
1. 定义 `SSEEvent`。
2. 实现对 `event:`、`data:`、空行分隔、多行 `data:` 和注释行的解析。
3. 保持 data-only SSE 的 `event` 为 `None`。
4. 添加基础事件、多行 data、注释忽略和尾部无空行测试。

**验证：** 运行 `python -m pytest tests/test_sse.py -q`，期望全部通过。

## T6: OpenAI Provider 请求构造

**文件：** `src/mewcode/providers/openai.py`, `tests/test_openai_provider.py`  
**依赖：** T3, T5  
**步骤：**
1. 实现 `OpenAIProvider` 初始化和 URL 拼接。
2. 将 `ChatMessage` 转换为 OpenAI `messages` 数组。
3. 构造 `POST {base_url}/chat/completions` 请求。
4. 设置 `Authorization: Bearer <api_key>` 请求头。
5. 请求体包含 `model`、`messages`、`stream: true`。
6. 使用 `httpx.MockTransport` 添加请求路径、请求头和请求体测试。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py::test_openai_request_payload_and_headers -q`，期望测试通过。

## T7: OpenAI Provider 流事件与错误处理

**文件：** `src/mewcode/providers/openai.py`, `tests/test_openai_provider.py`  
**依赖：** T6  
**步骤：**
1. 解析 OpenAI data-only SSE JSON chunk。
2. 将 `choices[0].delta.content` 转换为 `text_delta`。
3. 遇到 `[DONE]` 后发出 `message_done`。
4. 对非 2xx 响应、无效 JSON 和网络异常抛出脱敏后的 `ProviderError`。
5. 添加文本增量、完成事件、认证失败和无效 chunk 测试。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py -q`，期望全部通过。

## T8: Anthropic Provider 请求构造

**文件：** `src/mewcode/providers/anthropic.py`, `tests/test_anthropic_provider.py`  
**依赖：** T3, T5  
**步骤：**
1. 实现 `AnthropicProvider` 初始化和 URL 拼接。
2. 将 `ChatMessage` 转换为 Anthropic `messages` 数组。
3. 构造 `POST {base_url}/messages` 请求。
4. 设置 `x-api-key`、`anthropic-version` 和 `content-type` 请求头。
5. 请求体包含 `model`、`messages`、`max_tokens`、`stream: true`。
6. 配置开启 thinking 时，在请求体中加入 `thinking`。
7. 使用 `httpx.MockTransport` 添加请求路径、请求头、请求体和 thinking 配置测试。

**验证：** 运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_request_payload_and_headers tests/test_anthropic_provider.py::test_anthropic_request_includes_thinking_when_enabled -q`，期望全部通过。

## T9: Anthropic Provider 文本、思考与错误流

**文件：** `src/mewcode/providers/anthropic.py`, `tests/test_anthropic_provider.py`  
**依赖：** T8  
**步骤：**
1. 解析 Anthropic 命名 SSE 事件。
2. 将 `text_delta` 转换为统一 `text_delta`。
3. 将 `thinking_delta` 转换为统一 `thinking_delta`。
4. 保存 `signature_delta` 到完成消息的 `provider_payload`。
5. 遇到 `message_stop` 后发出 `message_done`。
6. 对 `event: error`、非 2xx 响应和网络异常抛出脱敏后的 `ProviderError`。
7. 添加文本增量、thinking 增量、签名保存、未知事件忽略和错误事件测试。

**验证：** 运行 `python -m pytest tests/test_anthropic_provider.py -q`，期望全部通过。

## T10: Provider 工厂

**文件：** `src/mewcode/providers/factory.py`, `src/mewcode/providers/__init__.py`, `tests/test_openai_provider.py`, `tests/test_anthropic_provider.py`  
**依赖：** T7, T9  
**步骤：**
1. 实现 `create_provider(config)`。
2. `protocol: openai` 返回 `OpenAIProvider`。
3. `protocol: anthropic` 返回 `AnthropicProvider`。
4. 未知协议抛出 `ConfigError`。
5. 在 Provider 测试中添加工厂选择和未知协议测试。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py::test_factory_returns_openai_provider tests/test_anthropic_provider.py::test_factory_returns_anthropic_provider tests/test_config.py::test_unknown_protocol_is_rejected -q`，期望全部通过。

## T11: 会话状态

**文件：** `src/mewcode/session.py`, `tests/test_session.py`  
**依赖：** T2  
**步骤：**
1. 实现 `ChatSession`。
2. 实现追加用户消息。
3. 实现追加助手消息。
4. 实现 `build_request()` 返回当前运行期完整上下文。
5. 添加多轮上下文、空会话和不写磁盘测试。

**验证：** 运行 `python -m pytest tests/test_session.py -q`，期望全部通过。

## T12: TUI 基础组件

**文件：** `src/mewcode/tui/widgets.py`, `tests/test_tui_smoke.py`  
**依赖：** T2  
**步骤：**
1. 实现 `StatusBar`，显示当前协议、模型和生成状态。
2. 实现 `MessageView`，区分用户消息、助手消息和错误消息。
3. 实现 `ThinkingPanel`，支持折叠和展开。
4. 实现 `MessageList`，追加消息后滚动到底部。
5. 实现 `Composer`，支持多行输入和提交事件。
6. 添加组件可创建、思考区可折叠和输入提交事件测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_widgets_can_render_and_toggle_thinking -q`，期望测试通过。

## T13: TUI 应用启动、布局与退出

**文件：** `src/mewcode/tui/app.py`, `src/mewcode/tui/__init__.py`, `tests/test_tui_smoke.py`  
**依赖：** T11, T12  
**步骤：**
1. 实现 `MewCodeApp(session, provider, config)`。
2. 组合顶部状态栏、消息滚动区、思考区、输入区和底部退出提示。
3. 绑定明确退出操作。
4. 启动时让输入区获得焦点。
5. 使用 Textual 测试 pilot 添加启动和退出冒烟测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_app_starts_with_fullscreen_regions tests/test_tui_smoke.py::test_app_quits_with_key_binding -q`，期望全部通过。

## T14: TUI 提交输入与流式更新

**文件：** `src/mewcode/tui/app.py`, `src/mewcode/tui/widgets.py`, `tests/test_tui_smoke.py`  
**依赖：** T13  
**步骤：**
1. 提交输入后追加用户消息并清空输入区。
2. 生成期间禁用输入区并更新状态栏。
3. 遍历 `provider.stream_chat()`。
4. 收到 `text_delta` 时增量更新助手消息正文。
5. 收到 `thinking_delta` 时增量更新可折叠思考区。
6. 收到 `message_done` 后把助手消息写入 `ChatSession` 并恢复输入。
7. 使用假 Provider 添加流式文本、thinking 和多轮上下文测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_submit_streams_text_into_message_view tests/test_tui_smoke.py::test_submit_streams_thinking_into_collapsible_panel tests/test_tui_smoke.py::test_second_turn_receives_previous_context -q`，期望全部通过。

## T15: TUI 错误恢复

**文件：** `src/mewcode/tui/app.py`, `src/mewcode/tui/widgets.py`, `tests/test_tui_smoke.py`  
**依赖：** T14  
**步骤：**
1. 捕获 `MewCodeError` 和未预期异常。
2. 在消息区和状态栏展示可理解错误。
3. 确保错误文本经过脱敏。
4. 错误后恢复输入区，允许继续提交下一条消息。
5. 添加 Provider 抛错后的展示、脱敏和继续输入测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_provider_error_is_displayed_and_input_recovers tests/test_tui_smoke.py::test_error_message_does_not_leak_secret -q`，期望全部通过。

## T16: CLI 集成

**文件：** `src/mewcode/cli.py`, `tests/test_tui_smoke.py`, `tests/test_config.py`  
**依赖：** T10, T13  
**步骤：**
1. 实现 `main(argv=None) -> int`。
2. 调用 `load_config()` 加载配置。
3. 调用 `create_provider()` 创建 Provider。
4. 创建 `ChatSession` 和 `MewCodeApp`。
5. 配置错误时在终端输出脱敏错误并返回非零退出码。
6. 添加 CLI 配置错误测试和入口点可导入测试。

**验证：** 运行 `python -m pytest tests/test_config.py::test_cli_reports_config_error_without_secret tests/test_tui_smoke.py::test_cli_entrypoint_is_importable -q`，期望全部通过。

## T17: README 与示例配置

**文件：** `README.md`  
**依赖：** T16  
**步骤：**
1. 写入安装命令。
2. 写入用户级配置 `~/.mewcode/config.yaml` 示例。
3. 写入项目级 `.mewcode.yaml` 覆盖示例。
4. 写入 OpenAI 和 Anthropic 两种 `protocol` 示例。
5. 写入明文 `api_key` 和 `${ENV_VAR}` 示例。
6. 写入启动、退出和纯对话范围说明。

**验证：** 运行 `python -m pytest -q`，期望全部通过；运行 `python -m pip install -e ".[dev]"`，期望 README 中的安装命令仍可执行。

## T18: 全量回归检查

**文件：** `pyproject.toml`, `src/mewcode/**/*.py`, `tests/*.py`, `README.md`  
**依赖：** T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12, T13, T14, T15, T16, T17  
**步骤：**
1. 运行完整单元测试。
2. 运行 Python 编译检查。
3. 运行命令入口导入检查。
4. 修复发现的问题并重复执行检查。

**验证：** 运行 `python -m pytest -q`，期望全部通过；运行 `python -m compileall src tests`，期望无编译错误；运行 `python -c "from mewcode.cli import main; print(callable(main))"`，期望输出 `True`。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18
```
