# Prompt Cache Hit Rate Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] `prompt_cache` 配置有安全默认值，且支持显式开启、关闭、namespace、OpenAI retention 和 Anthropic cache control（验证：运行 `python -m pytest tests/test_config.py -q`，期望通过）
- [ ] 运行时提示被拆成可缓存前缀和动态补充，允许工具摘要和项目指令位于可缓存前缀，cwd、轮次、当前目标、Hook 注入、记忆索引、恢复提示和上下文摘要位于动态补充（验证：运行 `python -m pytest tests/test_prompting.py -q`，期望通过）
- [ ] OpenAI 请求的第一个 system 消息包含稳定提示和可缓存运行时前缀，第二个 system 消息包含动态运行时补充，普通用户消息不承载运行时标签（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_payload_includes_structured_prompt_messages tests/test_openai_provider.py::test_openai_runtime_prompt_is_not_user_message -q`，期望通过）
- [ ] Anthropic 请求的 `cache_control` 位于最后一个可缓存前缀块，动态运行时块不设置 `cache_control`（验证：运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_payload_includes_structured_system_blocks tests/test_anthropic_provider.py::test_anthropic_runtime_prompt_is_not_cache_controlled -q`，期望通过）
- [ ] README 说明缓存优化策略、`prompt_cache` 配置、OpenAI `prompt_cache_key`、可选 `prompt_cache_retention`、Anthropic `cache_control` 和供应商命中限制（验证：人工查看 `README.md` 的“结构化系统提示与缓存观测”章节，期望包含这些关键词和限制说明）

## 集成
- [ ] OpenAI 默认请求携带安全 hash 形式的 `prompt_cache_key`，不泄露原始提示、用户文本、路径或密钥；配置禁用后不发送缓存参数（验证：运行 `python -m pytest tests/test_openai_provider.py -q`，期望通过）
- [ ] OpenAI 显式配置 retention 时携带 `prompt_cache_retention`；兼容接口拒绝缓存参数时只重试一次无缓存参数请求，非缓存参数错误不重试（验证：运行 `python -m pytest tests/test_openai_provider.py -q`，期望通过）
- [ ] Anthropic 可通过配置关闭显式缓存断点；关闭后请求仍保留系统块、工具和消息协议（验证：运行 `python -m pytest tests/test_anthropic_provider.py -q`，期望通过）
- [ ] Agent Loop 仍通过 ContextManager 构造请求，工具列表没有丢失，多轮工具调用后的会话消息顺序保持 assistant 工具调用后接 tool 结果（验证：运行 `python -m pytest tests/test_agent.py -q`，期望通过）
- [ ] TUI 状态栏仍能展示 Cache 的 hit、write、miss、unknown 或 unsupported 状态（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_status_bar_renders_cache_usage -q`，期望通过）

## 编译与测试
- [ ] 配置、提示、Provider、Agent 和 TUI 回归测试全部通过（验证：运行 `python -m pytest tests/test_config.py tests/test_prompting.py tests/test_openai_provider.py tests/test_anthropic_provider.py tests/test_agent.py tests/test_tui_smoke.py -q`，期望通过）
- [ ] README 相关回归测试仍通过（验证：运行 `python -m pytest tests/test_config.py::test_readme_mentions_session_memory -q`，期望通过）
- [ ] 缓存 usage 解析保持兼容：OpenAI `cached_tokens`、Anthropic `cache_read_input_tokens` 和 `cache_creation_input_tokens` 能映射为已有缓存状态（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_streams_cache_hit_usage tests/test_openai_provider.py::test_openai_streams_cache_miss_usage tests/test_openai_provider.py::test_openai_usage_without_cache_fields_is_unknown tests/test_anthropic_provider.py::test_anthropic_streams_cache_read_usage tests/test_anthropic_provider.py::test_anthropic_streams_cache_creation_usage tests/test_anthropic_provider.py::test_anthropic_usage_without_cache_fields_is_unknown -q`，期望通过）

## 端到端场景
- [ ] 场景 1：在 tmux 中启动 JulyCode，输入“请查看 README 里关于缓存的说明，并告诉我当前项目如何观测缓存命中” → JulyCode 调用读取或搜索工具并生成中文回复（验证：观察 tmux 输出，期望出现工具执行过程和最终回复）
- [ ] 场景 2：同一 tmux 会话内继续输入“再检查一下 Provider 里缓存字段是怎么解析的” → JulyCode 继续调用读取或搜索工具，状态栏或 usage 事件显示 Cache 状态为 hit、write、miss、unknown 或 unsupported 中的一个（验证：观察 tmux 输出和状态栏，真实供应商不要求必然 hit）
- [ ] 场景 3：对照本 checklist 逐项验收 → 所有单元测试项有命令证据，tmux 场景有观察记录；若真实供应商返回 miss 或 unknown，记录为缓存观测正常而非命中保证失败（验证：生成验收报告，期望每项有通过或失败证据）
