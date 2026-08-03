# JulyCode Structured System Prompt Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] 固定系统提示包含身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出七个模块，且顺序固定（验证：运行 `python -m pytest tests/test_prompting.py::test_stable_modules_are_ordered_and_cacheable -q`，期望通过）
- [ ] 固定系统提示模块内容稳定、模块间有清晰分隔，未启用的可选模块不会输出空占位（验证：运行 `python -m pytest tests/test_prompting.py::test_stable_prompt_is_deterministic_and_has_no_empty_optional_sections -q`，期望通过）
- [ ] 全局系统提示包含专用工具优先、编辑前读取、写入边界、命令边界和工具失败后继续调整等关键规则（验证：运行 `python -m pytest tests/test_prompting.py::test_stable_modules_include_tool_rules -q`，期望通过）
- [ ] 运行时补充使用 `<julycode_runtime_context>` 边界标签，并包含 cwd、模式、轮次和本轮约束（验证：运行 `python -m pytest tests/test_prompting.py::test_runtime_prompt_uses_tagged_context -q`，期望通过）
- [ ] 运行时补充不会写入会话历史，用户、助手和工具消息历史保持原有语义（验证：运行 `python -m pytest tests/test_session.py::test_session_prompt_does_not_pollute_history -q`，期望通过）
- [ ] 相同稳定配置和工具集下连续构造的稳定提示一致，cwd、模式或轮次变化只影响运行时补充（验证：运行 `python -m pytest tests/test_prompting.py::test_stable_prompt_is_deterministic_and_runtime_changes_are_separate -q`，期望通过）
- [ ] 注入频率符合首轮完整、间隔轮次重复关键规则、其余轮次精简状态（验证：运行 `python -m pytest tests/test_prompting.py::test_runtime_prompt_uses_full_refresh_and_brief_levels -q`，期望通过）
- [ ] `/plan <需求>` 的用户消息只保留真实需求，规划约束通过运行时补充注入（验证：运行 `python -m pytest tests/test_commands.py::test_parse_plan_command tests/test_agent.py::test_plan_mode_prompt_is_runtime_instruction_not_user_text -q`，期望通过）
- [ ] `/do` 的用户消息不包含完整计划或全工具控制指令，待执行计划通过运行时补充注入（验证：运行 `python -m pytest tests/test_commands.py::test_parse_do_with_plan tests/test_agent.py::test_do_mode_injects_plan_as_runtime_instruction -q`，期望通过）
- [ ] Plan Mode 首轮运行时补充包含完整只读规划约束，后续轮次按频率降为 refresh 或 brief（验证：运行 `python -m pytest tests/test_prompting.py::test_plan_mode_runtime_prompt_levels -q`，期望通过）
- [ ] `/do` 执行阶段运行时补充包含当前待执行计划和全工具能力状态（验证：运行 `python -m pytest tests/test_prompting.py::test_do_mode_runtime_prompt_contains_pending_plan -q`，期望通过）
- [ ] 内置工具描述包含各自适用场景和关键约束，尤其是编辑前读取、完整写入风险、命令副作用和搜索定位优先（验证：运行 `python -m pytest tests/test_tools.py::test_builtin_tool_descriptions_include_operational_rules -q`，期望通过）
- [ ] `ChatRequest` 能携带 `PromptBundle`，且不破坏无 prompt 的旧请求路径（验证：运行 `python -m pytest tests/test_session.py::test_session_build_request_accepts_prompt tests/test_openai_provider.py::test_openai_request_payload_and_headers tests/test_anthropic_provider.py::test_anthropic_request_payload_and_headers -q`，期望通过）
- [ ] `TokenUsage` 能携带统一缓存观测结果，现有只读取 token 总量的调用方不需要改动（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_streams_usage_event tests/test_anthropic_provider.py::test_anthropic_streams_usage_events tests/test_tui_smoke.py::test_status_bar_renders_agent_progress_and_usage -q`，期望通过）

## Provider 与缓存
- [ ] OpenAI 请求把稳定提示放在首个 `system` 消息，运行时补充放在第二个 `system` 消息，二者位于会话消息之前（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_payload_includes_structured_prompt_messages -q`，期望通过）
- [ ] OpenAI 请求中的运行时补充带边界标签，不作为普通 user 消息发送（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_runtime_prompt_is_not_user_message -q`，期望通过）
- [ ] OpenAI 请求在有工具时仍发送稳定工具描述，并与结构化提示共同出现在请求中（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_request_includes_tools_when_available tests/test_openai_provider.py::test_openai_payload_includes_structured_prompt_messages -q`，期望通过）
- [ ] OpenAI usage 中的 `prompt_tokens_details.cached_tokens` 能被解析为缓存命中或未命中（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_streams_cache_hit_usage tests/test_openai_provider.py::test_openai_streams_cache_miss_usage -q`，期望通过）
- [ ] OpenAI usage 缺少缓存字段时不影响对话，缓存观测为 unknown（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_usage_without_cache_fields_is_unknown -q`，期望通过）
- [ ] Anthropic 请求把稳定提示映射为 `system` 文本块，并在最后一个稳定块设置 `cache_control`（验证：运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_payload_includes_structured_system_blocks -q`，期望通过）
- [ ] Anthropic 请求把运行时补充放在稳定块之后，且运行时补充不设置 `cache_control`（验证：运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_runtime_prompt_is_not_cache_controlled -q`，期望通过）
- [ ] Anthropic 请求在有工具时仍发送顶层工具定义，并与 system 缓存断点共同构成稳定前缀（验证：运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_request_includes_tools_when_available tests/test_anthropic_provider.py::test_anthropic_payload_includes_structured_system_blocks -q`，期望通过）
- [ ] Anthropic usage 中的 `cache_read_input_tokens` 能被解析为缓存命中（验证：运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_streams_cache_read_usage -q`，期望通过）
- [ ] Anthropic usage 中的 `cache_creation_input_tokens` 能被解析为缓存写入（验证：运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_streams_cache_creation_usage -q`，期望通过）
- [ ] Anthropic usage 缺少或不包含缓存字段时不影响对话，缓存观测为 unknown 或 miss（验证：运行 `python -m pytest tests/test_anthropic_provider.py::test_anthropic_usage_without_cache_fields_is_unknown tests/test_anthropic_provider.py::test_anthropic_zero_cache_fields_are_miss -q`，期望通过）
- [ ] OpenAI 和 Anthropic 的结构化提示流程都能与文本流式、thinking、工具调用和工具结果消息共存（验证：运行 `python -m pytest tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望通过）

## 集成
- [ ] Agent Loop 每轮请求都会构造 `RuntimePromptContext`，并把 `PromptBundle` 传给当前 Provider（验证：运行 `python -m pytest tests/test_agent.py::test_runner_attaches_prompt_bundle_to_model_request -q`，期望通过）
- [ ] 普通多轮对话仍保留历史上下文，不因结构化提示丢失用户、助手或工具结果消息（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_second_turn_receives_previous_context tests/test_agent.py::test_runner_runs_multiple_tool_iterations_until_final_answer -q`，期望通过）
- [ ] Plan Mode 仍只暴露读类工具，并阻止有副作用工具真实执行（验证：运行 `python -m pytest tests/test_agent.py::test_plan_mode_saves_pending_plan tests/test_agent.py::test_plan_mode_blocks_side_effect_tools -q`，期望通过）
- [ ] `/do` 仍使用全工具能力执行当前待执行计划，完成后清理计划（验证：运行 `python -m pytest tests/test_agent.py::test_do_mode_executes_and_clears_pending_plan -q`，期望通过）
- [ ] 工具调用、工具结果回灌、迭代上限、连续未知工具、取消和 Provider 错误行为不回退（验证：运行 `python -m pytest tests/test_agent.py -q`，期望通过）
- [ ] 状态栏能展示 Token 用量和缓存状态 hit、write、miss、unknown、unsupported 中的可用状态（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_status_bar_renders_cache_usage -q`，期望通过）
- [ ] mock OpenAI server 能输出包含缓存字段的 usage，TUI 可观察到缓存状态（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_submit_streams_text_into_message_view tests/test_tui_smoke.py::test_status_bar_renders_cache_usage -q`，期望通过）
- [ ] README 描述结构化系统提示、运行时补充、缓存观测和本阶段不实现项目指令、记忆、MCP 的边界（验证：运行 `rg -n "结构化系统提示|运行时补充|缓存|unsupported|项目指令|记忆|MCP" README.md`，期望命中新增能力和未实现边界）
- [ ] 人工对比场景文档覆盖工具选择、编辑前读取、Plan Mode、动态环境注入和缓存观测，且每个场景包含通过标准（验证：运行 `rg -n "工具选择|编辑前读取|Plan Mode|动态环境|缓存观测|通过标准" specs/structured-system-prompt/manual-scenarios.md`，期望每类场景都能命中）

## 编译与测试
- [ ] 提示构造、会话、命令解析、Agent、OpenAI Provider 和 Anthropic Provider 分层测试全部通过（验证：运行 `python -m pytest tests/test_prompting.py tests/test_session.py tests/test_commands.py tests/test_agent.py tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望通过）
- [ ] 工具、调度和 TUI 回归测试全部通过（验证：运行 `python -m pytest tests/test_tools.py tests/test_tool_scheduler.py tests/test_tui_smoke.py -q`，期望通过）
- [ ] 全部自动化测试通过（验证：运行 `python -m pytest -q`，期望通过）
- [ ] Python 文件无语法错误（验证：运行 `python -m compileall src tests`，期望无编译错误）
- [ ] 命令入口仍可导入（验证：运行 `python -c "from julycode.cli import main; print(callable(main))"`，期望输出 `True`）
- [ ] 项目未配置 lint 命令时记录为不适用；如后续配置 lint，则 lint 检查通过（验证：查看 `pyproject.toml` 是否有 lint 配置；若有则运行对应命令，期望退出码为 0）

## 端到端场景
- [ ] 场景 1：普通聊天仍能流式回复并显示缓存状态（验证：在 tmux 中启动 `python tests/e2e_mock_openai_server.py 18765`，配置 JulyCode 指向该服务，启动 `julycode`，输入“用一句话解释递归”，观察文本逐步出现、状态栏显示 Token/Cache 信息且输入区恢复可用）
- [ ] 场景 2：工具选择场景优先使用专用读类工具（验证：tmux 中输入“查找 README 里关于 Plan Mode 的说明并总结”，观察模型调用查找或读取工具，最终回复引用工具结果）
- [ ] 场景 3：编辑前读取场景会先读取或搜索目标文件再编辑（验证：tmux 中输入“把 README 里的 Plan Mode 小节补一句缓存观测说明”，观察先出现读取或搜索工具，再出现编辑或写入工具；检查文件变化符合请求）
- [ ] 场景 4：`/plan <需求>` 只读规划约束通过运行时补充生效（验证：tmux 中输入 `/plan 给这个项目加一个简单文件总结功能`，观察只出现读类工具调用，最终展示计划并保存为待执行计划）
- [ ] 场景 5：规划阶段请求有副作用工具时会被阻止（验证：使用 mock 场景让规划阶段请求写入/修改/命令工具，观察该工具未实际执行，界面显示工具失败或模型继续输出计划）
- [ ] 场景 6：`/do` 执行阶段通过运行时补充注入当前计划并允许全工具（验证：完成场景 4 后输入 `/do`，观察执行阶段可调用全工具，最终完成后再次输入 `/do` 会提示没有待执行计划）
- [ ] 场景 7：动态环境注入可在请求记录中观察（验证：检查 mock server 捕获的请求 payload，期望看到 `<julycode_runtime_context>`、cwd、模式和轮次信息，且用户消息不包含这些控制字段）
- [ ] 场景 8：缓存观测可在连续请求中观察（验证：连续输入两个相似请求，观察状态栏或 usage 事件中出现 Cache hit、write、miss 或 unknown 状态，且对话不中断）
- [ ] 场景 9：多轮工具调用和工具结果回灌不回退（验证：tmux 中输入一个需要搜索代码、读取文件并总结的请求，观察多个工具状态、最终回复和输入恢复均正常）
- [ ] 场景 10：Provider 错误或缓存字段缺失不会导致 TUI 崩溃（验证：使用 mock 错误场景或缺失缓存字段的 mock usage，观察错误脱敏或 Cache unknown，输入区恢复可用）
