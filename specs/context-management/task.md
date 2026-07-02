# MewCode 上下文管理 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/mewcode/context/__init__.py` | 导出上下文管理公开类型 |
| 新建 | `src/mewcode/context/models.py` | 定义配置、状态、摘要、报告和错误模型 |
| 新建 | `src/mewcode/context/estimator.py` | 近似 Token 估算和 usage 锚点逻辑 |
| 新建 | `src/mewcode/context/store.py` | 将完整工具结果保存到项目内可读取路径 |
| 新建 | `src/mewcode/context/compactor.py` | 执行轻量工具结果压缩 |
| 新建 | `src/mewcode/context/segmenter.py` | 按安全边界切分会话历史并选择近期原文 |
| 新建 | `src/mewcode/context/summarizer.py` | 发起无工具摘要请求并解析正式摘要 |
| 新建 | `src/mewcode/context/manager.py` | 编排轻量预防、重量兜底、熔断和报告 |
| 修改 | `src/mewcode/config.py` | 解析 `context:` 配置并挂到 `AppConfig` |
| 修改 | `src/mewcode/session.py` | 保存 `ContextState`，支持替换消息和设置摘要 |
| 修改 | `src/mewcode/prompting/base.py` | 让运行时提示上下文携带摘要状态 |
| 修改 | `src/mewcode/prompting/builder.py` | 注入上下文摘要和边界提示 |
| 修改 | `src/mewcode/commands.py` | 解析 `/compact` 手动压缩命令 |
| 修改 | `src/mewcode/agent.py` | 请求前调用上下文管理器，记录 usage 锚点并处理压缩事件 |
| 修改 | `src/mewcode/tui/app.py` | 处理 `/compact`，显示压缩报告，复用上下文管理器 |
| 修改 | `src/mewcode/cli.py` | 创建并传入共享 `ContextManager` |
| 修改 | `.gitignore` | 忽略 `.mewcode/context/` 外置结果目录 |
| 修改 | `README.md` | 说明上下文管理、配置、`/compact` 和外置路径 |
| 新建 | `tests/test_context_estimator.py` | 覆盖模型默认值、估算和 usage 锚点 |
| 新建 | `tests/test_context_compactor.py` | 覆盖外置保存、单条和轮次工具结果压缩 |
| 新建 | `tests/test_context_summarizer.py` | 覆盖摘要 prompt、禁工具、正式摘要解析和失败 |
| 新建 | `tests/test_context_manager.py` | 覆盖自动/手动压缩、近期保留、熔断和请求准备 |
| 修改 | `tests/test_config.py` | 覆盖 `context:` 配置解析和非法值 |
| 修改 | `tests/test_session.py` | 覆盖上下文状态、消息替换和摘要设置 |
| 修改 | `tests/test_prompting.py` | 覆盖摘要和边界提示注入 |
| 修改 | `tests/test_commands.py` | 覆盖 `/compact` 命令解析 |
| 修改 | `tests/test_agent.py` | 覆盖 Agent Loop 请求前压缩、usage 锚点和 context limit |
| 修改 | `tests/test_tui_smoke.py` | 覆盖 TUI 手动压缩报告和既有行为不回退 |
| 修改 | `tests/e2e_mock_openai_server.py` | 支持大工具结果和摘要响应场景 |

## T1: 建立上下文模型

**文件：** `src/mewcode/context/__init__.py`、`src/mewcode/context/models.py`、`tests/test_context_estimator.py`  
**依赖：** 无  
**步骤：**
1. 创建 `mewcode.context` 包。
2. 在 `models.py` 定义 `ContextConfig`、`ContextSummary`、`ContextExternalRef`、`TokenAnchor`、`RequestFootprint`、`ContextCompactionReport`、`ToolCompactionResult`、`PreparedChatRequest`、`ContextState` 和 `ContextLimitError`。
3. 在 `__init__.py` 导出后续模块需要使用的公开类型。
4. 在 `tests/test_context_estimator.py` 添加模型默认值和 `ContextLimitError.report` 行为测试。

**验证：** 运行 `python -m pytest tests/test_context_estimator.py::test_context_config_defaults tests/test_context_estimator.py::test_context_limit_error_carries_report -q`，期望全部通过。

## T2: 解析上下文配置

**文件：** `src/mewcode/config.py`、`tests/test_config.py`  
**依赖：** T1  
**步骤：**
1. 将 `ContextConfig` 挂到 `AppConfig.context`，默认值来自 `ContextConfig()`。
2. 新增 `_parse_context(raw)`，解析 `enabled`、窗口、阈值、预览、近期保留、安全余量、失败上限、字符换算和存储目录字段。
3. 对整数、浮点数和目录字符串做合法性校验，非法值抛出 `ConfigError`。
4. 在配置测试中增加默认配置、显式配置和非法配置用例。

**验证：** 运行 `python -m pytest tests/test_config.py::test_loads_required_yaml_fields tests/test_config.py::test_loads_context_config tests/test_config.py::test_rejects_invalid_context_config -q`，期望全部通过。

## T3: 会话保存上下文状态

**文件：** `src/mewcode/session.py`、`tests/test_session.py`  
**依赖：** T1  
**步骤：**
1. 给 `ChatSession` 增加默认 `ContextState`，每个运行期生成稳定 `session_id`。
2. 新增 `replace_messages(messages)`，用于重量兜底后替换为近期原文。
3. 新增 `set_context_summary(summary)`，更新 `context_state.summary`。
4. 保持 `build_request()` 不把摘要自动写入普通消息历史。
5. 添加会话状态、消息替换、摘要设置和多轮上下文不回退测试。

**验证：** 运行 `python -m pytest tests/test_session.py::test_session_has_context_state tests/test_session.py::test_session_can_replace_messages tests/test_session.py::test_session_sets_context_summary tests/test_session.py::test_second_turn_receives_previous_context -q`，期望全部通过。

## T4: 实现 Token 估算器

**文件：** `src/mewcode/context/estimator.py`、`tests/test_context_estimator.py`  
**依赖：** T1  
**步骤：**
1. 实现 `TokenEstimator.estimate_message()`，对 `ChatMessage` 的角色、正文、thinking、工具调用、工具结果标记和 provider payload 做稳定字符估算。
2. 实现 `request_footprint(messages, tools, prompt)`，把消息、工具描述和提示块纳入字符足迹。
3. 实现 `estimate_from_anchor(footprint, anchor)`，有 usage 锚点时只估算字符增量，没有锚点时使用全量字符换算。
4. 增加无锚点、有锚点、工具描述、运行时提示和 tool call 参数估算测试。

**验证：** 运行 `python -m pytest tests/test_context_estimator.py::test_estimates_request_footprint_from_messages_tools_and_prompt tests/test_context_estimator.py::test_estimates_with_usage_anchor_delta tests/test_context_estimator.py::test_estimates_without_usage_anchor -q`，期望全部通过。

## T5: 保存外置工具结果

**文件：** `src/mewcode/context/store.py`、`.gitignore`、`tests/test_context_compactor.py`  
**依赖：** T1、T4  
**步骤：**
1. 实现 `ContextStore.write_tool_result()`，在项目目录下创建 `.mewcode/context/<session_id>/tool-results/`。
2. 将原始工具消息、调用标识、错误标记、字符数、估算 token 和创建时间写入 UTF-8 JSON 文件。
3. 返回 `ContextExternalRef`，其中 `path` 是项目相对路径。
4. 在 `.gitignore` 添加 `.mewcode/context/`。
5. 添加测试验证文件可读取、路径在项目内、JSON 包含原始工具内容且目录被忽略。

**验证：** 运行 `python -m pytest tests/test_context_compactor.py::test_context_store_writes_tool_result_under_project tests/test_context_compactor.py::test_context_store_returns_readable_relative_path -q`，期望全部通过。

## T6: 压缩单个大工具结果

**文件：** `src/mewcode/context/compactor.py`、`tests/test_context_compactor.py`  
**依赖：** T4、T5  
**步骤：**
1. 实现 `ToolResultCompactor.compact(session)` 的单结果阈值判断。
2. 当单个 `role="tool"` 消息超过阈值时，调用 `ContextStore` 保存完整内容。
3. 将工具消息正文替换为包含 `mewcode_externalized=true`、工具名、调用标识、成功或失败状态、原始规模、预览和路径的 JSON。
4. 确保普通用户消息、assistant 消息和未超阈值工具结果不被改写。
5. 添加单结果压缩、用户原文保留、已外置结果不重复压缩测试。

**验证：** 运行 `python -m pytest tests/test_context_compactor.py::test_compacts_single_large_tool_result tests/test_context_compactor.py::test_light_compaction_keeps_user_messages_verbatim tests/test_context_compactor.py::test_compactor_does_not_reexternalize_existing_preview -q`，期望全部通过。

## T7: 压缩同轮工具结果合计超限

**文件：** `src/mewcode/context/compactor.py`、`tests/test_context_compactor.py`  
**依赖：** T6  
**步骤：**
1. 在 `ToolResultCompactor` 中识别同一轮 assistant 工具调用后连续出现的 tool 结果集合。
2. 当该集合合计超过轮次阈值时，按估算体量从大到小外置保存工具结果。
3. 外置到该轮保留内容降到阈值内后停止，保留仍在阈值内的小结果原文。
4. 确保失败工具结果、命令输出、搜索结果和远端工具返回内容都走同一逻辑。
5. 添加轮次合计超限、保留小结果、失败结果外置测试。

**验证：** 运行 `python -m pytest tests/test_context_compactor.py::test_compacts_largest_results_when_turn_total_exceeds_limit tests/test_context_compactor.py::test_turn_compaction_keeps_small_results_when_under_limit tests/test_context_compactor.py::test_compacts_failed_tool_results_with_same_rules -q`，期望全部通过。

## T8: 实现历史安全切段

**文件：** `src/mewcode/context/segmenter.py`、`tests/test_context_manager.py`  
**依赖：** T4  
**步骤：**
1. 实现 `ConversationSegmenter.split()`，将普通用户/助手消息作为独立段。
2. 将包含工具调用的 assistant 消息及其后续对应 tool 结果合并为不可拆分段。
3. 实现 `select_recent()`，从尾部按 `recent_tokens` 回选近期段，并保证至少保留 `min_recent_messages` 条消息。
4. 添加工具调用段不可拆、按 token 回选、至少 5 条消息保留测试。

**验证：** 运行 `python -m pytest tests/test_context_manager.py::test_segmenter_keeps_tool_call_and_results_together tests/test_context_manager.py::test_segmenter_selects_recent_by_token_budget tests/test_context_manager.py::test_segmenter_keeps_minimum_recent_messages -q`，期望全部通过。

## T9: 实现摘要请求和正式摘要解析

**文件：** `src/mewcode/context/summarizer.py`、`tests/test_context_summarizer.py`  
**依赖：** T1  
**步骤：**
1. 实现 `HistorySummarizer.summarize()`，构造无工具 `ChatRequest(tools=())`。
2. 摘要 prompt 明确禁止工具调用，并要求输出 `<analysis_draft>` 和 `<final_summary>`。
3. 收集 Provider 流式文本，解析并只保存 `<final_summary>` 内容。
4. 生成 `ContextSummary`，包含固定边界提示、来源消息数、保留消息数和外置路径。
5. 添加测试断言请求不携带工具、prompt 包含禁工具和固定摘要结构、草稿不进入摘要内容。

**验证：** 运行 `python -m pytest tests/test_context_summarizer.py::test_summarizer_requests_without_tools tests/test_context_summarizer.py::test_summarizer_prompt_requires_draft_and_final_summary tests/test_context_summarizer.py::test_summarizer_keeps_only_final_summary -q`，期望全部通过。

## T10: 处理摘要失败

**文件：** `src/mewcode/context/summarizer.py`、`tests/test_context_summarizer.py`  
**依赖：** T9  
**步骤：**
1. 定义摘要失败异常，携带可读失败原因。
2. 当 Provider 报错、返回工具调用、没有 `<final_summary>` 或正式摘要为空时抛出摘要失败异常。
3. 确保失败时不会修改传入会话消息或摘要状态。
4. 添加 Provider 错误、工具调用、缺少正式摘要和空正式摘要测试。

**验证：** 运行 `python -m pytest tests/test_context_summarizer.py::test_summarizer_fails_on_provider_error tests/test_context_summarizer.py::test_summarizer_fails_if_model_requests_tool tests/test_context_summarizer.py::test_summarizer_fails_without_final_summary -q`，期望全部通过。

## T11: 准备请求时执行轻量预防

**文件：** `src/mewcode/context/manager.py`、`tests/test_context_manager.py`  
**依赖：** T2、T3、T7  
**步骤：**
1. 实现 `ContextManager.prepare_request()` 的基础路径。
2. 每次准备请求时先调用 `ToolResultCompactor.compact()`。
3. 调用 `prompt_factory()` 构造 `PromptBundle`，并用当前会话消息、工具和提示生成 `ChatRequest`。
4. 返回 `PreparedChatRequest`，包含请求、footprint 和轻量压缩报告。
5. 支持 `context.enabled=false` 时跳过压缩但仍返回可用请求。
6. 添加轻量预防发生在请求前、禁用配置跳过、报告包含外置路径测试。

**验证：** 运行 `python -m pytest tests/test_context_manager.py::test_prepare_request_runs_light_compaction_before_building_request tests/test_context_manager.py::test_prepare_request_can_skip_when_context_disabled tests/test_context_manager.py::test_prepare_request_reports_externalized_paths -q`，期望全部通过。

## T12: 实现自动重量兜底

**文件：** `src/mewcode/context/manager.py`、`tests/test_context_manager.py`  
**依赖：** T8、T9、T11  
**步骤：**
1. 在 `prepare_request()` 中计算可用输入预算：`window_tokens - max_output_tokens - auto_reserve_tokens`。
2. 当估算超过预算时，使用 `ConversationSegmenter` 选择待摘要早期段和近期原文段。
3. 调用 `HistorySummarizer` 生成摘要。
4. 用近期原文替换 `session.messages`，把摘要写入 `session.context_state.summary`。
5. 重建包含新摘要的 `PromptBundle`，再生成最终 `PreparedChatRequest`。
6. 添加自动触发、13K 安全余量、近期原文保留、摘要注入后重建请求测试。

**验证：** 运行 `python -m pytest tests/test_context_manager.py::test_auto_heavy_compaction_triggers_before_safety_margin tests/test_context_manager.py::test_heavy_compaction_replaces_old_messages_with_recent_messages tests/test_context_manager.py::test_prepare_request_rebuilds_prompt_after_summary -q`，期望全部通过。

## T13: 实现手动 `/compact` 压缩路径

**文件：** `src/mewcode/context/manager.py`、`tests/test_context_manager.py`  
**依赖：** T12  
**步骤：**
1. 实现 `ContextManager.manual_compact(session, provider)`。
2. 手动路径先执行轻量预防，再用 `manual_reserve_tokens=3000` 判断或强制尝试重量兜底。
3. 当历史不足以摘要时返回 no-op 报告，不调用普通 Agent Loop。
4. 报告包含是否轻量压缩、是否重量压缩、保留消息数、摘要消息数、估算前后 token 和外置路径。
5. 添加手动压缩使用 3K 安全余量、短历史 no-op、报告字段完整测试。

**验证：** 运行 `python -m pytest tests/test_context_manager.py::test_manual_compact_uses_manual_safety_margin tests/test_context_manager.py::test_manual_compact_returns_noop_for_short_history tests/test_context_manager.py::test_manual_compact_report_contains_counts_and_paths -q`，期望全部通过。

## T14: 实现摘要失败熔断

**文件：** `src/mewcode/context/manager.py`、`tests/test_context_manager.py`  
**依赖：** T10、T12  
**步骤：**
1. 在摘要失败时递增 `session.context_state.consecutive_summary_failures`。
2. 连续失败少于上限且估算仍可安全发送时，返回带警告的报告并继续准备请求。
3. 连续失败达到 3 次或压缩后仍明显超预算时，抛出 `ContextLimitError`。
4. 成功重量兜底后清零连续失败计数。
5. 添加失败计数、三次熔断、成功后清零和超预算停止测试。

**验证：** 运行 `python -m pytest tests/test_context_manager.py::test_summary_failure_count_increments tests/test_context_manager.py::test_summary_failure_limit_raises_context_limit tests/test_context_manager.py::test_successful_summary_resets_failure_count tests/test_context_manager.py::test_context_limit_raised_when_request_still_over_budget -q`，期望全部通过。

## T15: 注入摘要和边界提示

**文件：** `src/mewcode/prompting/base.py`、`src/mewcode/prompting/builder.py`、`tests/test_prompting.py`  
**依赖：** T1、T3  
**步骤：**
1. 给 `RuntimePromptContext` 增加 `context_summary` 字段。
2. 在 `PromptBuilder.build_runtime_prompt()` 中，当存在摘要时追加 `<mewcode_context_summary>` 块。
3. 块内包含正式摘要、边界提示和外置路径列表。
4. 确保摘要块位于运行时系统补充中，不进入普通用户消息。
5. 添加摘要注入、边界提示、无摘要不产生空块测试。

**验证：** 运行 `python -m pytest tests/test_prompting.py::test_runtime_prompt_includes_context_summary tests/test_prompting.py::test_runtime_prompt_includes_context_boundary_notice tests/test_prompting.py::test_runtime_prompt_omits_summary_block_when_absent -q`，期望全部通过。

## T16: 解析 `/compact` 命令

**文件：** `src/mewcode/commands.py`、`tests/test_commands.py`  
**依赖：** T1  
**步骤：**
1. 定义 `CompactCommand`。
2. 在 `parse_agent_command()` 中识别精确 `/compact`。
3. 确保 `/compact` 不返回 `AgentCommand`，不会带 `model_text` 进入模型。
4. 对 `/compact extra` 返回 assistant 提示“`/compact` 不接受参数，请单独输入 `/compact`”，不调用模型。
5. 添加 `/compact`、普通输入、`/plan`、`/do` 既有行为不回退测试。

**验证：** 运行 `python -m pytest tests/test_commands.py::test_parse_compact_command tests/test_commands.py::test_parse_compact_command_does_not_become_agent_task tests/test_commands.py::test_parse_compact_with_extra_text_returns_prompt tests/test_commands.py::test_parse_plan_command tests/test_commands.py::test_parse_do_with_plan -q`，期望全部通过。

## T17: 接入 Agent Loop 自动上下文管理

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`  
**依赖：** T11、T12、T14、T15  
**步骤：**
1. 给 `AgentLoopRunner` 增加可选 `context_manager` 参数，未传入时使用基于当前 cwd 和默认配置的管理器。
2. 每轮 Provider 请求前构造 `prompt_factory`，由 `ContextManager.prepare_request()` 返回最终 `ChatRequest`。
3. 当 `PreparedChatRequest.report` 存在且发生压缩时，发出 `TurnEvent(type="context_compacted")`。
4. Provider 返回 usage 后调用 `context_manager.record_usage()`。
5. 捕获 `ContextLimitError`，发出 `stopped(context_limit)` 和最终用户可读消息。
6. 更新 Agent 测试，覆盖请求前压缩、usage 锚点、context_compacted 事件、context_limit 停止和 Plan Mode 不回退。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_prepares_request_through_context_manager tests/test_agent.py::test_runner_records_usage_anchor_after_model_response tests/test_agent.py::test_runner_emits_context_compacted_event tests/test_agent.py::test_runner_stops_on_context_limit tests/test_agent.py::test_plan_mode_saves_pending_plan -q`，期望全部通过。

## T18: 接入 TUI 手动压缩

**文件：** `src/mewcode/tui/app.py`、`tests/test_tui_smoke.py`  
**依赖：** T13、T16、T17  
**步骤：**
1. 给 `MewCodeApp` 增加可选 `context_manager` 参数，并传给 `AgentLoopRunner`。
2. 在 `_run_generation()` 中识别 `CompactCommand`，直接调用 `manual_compact()`。
3. 将压缩报告作为 assistant 消息展示，恢复输入区，不调用 Provider 普通对话。
4. 在 `_apply_turn_event()` 中展示自动 `context_compacted` 事件的简短状态。
5. 添加 `/compact` 不发起普通模型任务、显示报告、自动压缩事件可展示、既有工具和权限流程不回退测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_compact_command_shows_report_without_agent_task tests/test_tui_smoke.py::test_tui_displays_context_compacted_event tests/test_tui_smoke.py::test_submit_shows_multiple_tool_statuses_and_final_answer tests/test_tui_smoke.py::test_tui_permission_deny_returns_failure_and_recovers_input -q`，期望全部通过。

## T19: 接入 CLI、文档和忽略规则

**文件：** `src/mewcode/cli.py`、`README.md`、`.gitignore`、`tests/test_config.py`、`tests/test_tui_smoke.py`  
**依赖：** T2、T18  
**步骤：**
1. 在 CLI 启动时用 `config.context`、当前 cwd 和 `config.max_tokens` 创建共享 `ContextManager`。
2. 将共享 `ContextManager` 传给 `MewCodeApp`。
3. README 增加 `context:` 配置示例、两层压缩说明、`/compact` 说明和 `.mewcode/context/` 外置路径说明。
4. 确认 `.gitignore` 忽略 `.mewcode/context/`。
5. 在测试中读取 README，断言文档包含 `context:`、`/compact` 和 `.mewcode/context/`。

**验证：** 运行 `python -m pytest tests/test_config.py::test_loads_context_config tests/test_config.py::test_readme_documents_context_management tests/test_tui_smoke.py::test_cli_entrypoint_is_importable -q`，期望全部通过。

## T20: 更新端到端 mock 场景

**文件：** `tests/e2e_mock_openai_server.py`、`tests/test_agent.py`、`tests/test_tui_smoke.py`  
**依赖：** T17、T18  
**步骤：**
1. 在 mock OpenAI server 中增加大工具输出触发逻辑。
2. 增加摘要请求响应，返回包含 `<analysis_draft>` 和 `<final_summary>` 的内容。
3. 增加读取外置工具结果路径后的可观察回复。
4. 在 Agent/TUI 测试中增加大工具结果外置后继续追问的场景。
5. 确保 mock 仍兼容现有 Plan Mode、权限和 MCP 场景。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_externalizes_large_tool_result_and_continues tests/test_tui_smoke.py::test_tui_can_continue_after_large_tool_result_compaction -q`，期望全部通过。

## T21: 回归上下文管理测试集

**文件：** `tests/test_context_estimator.py`、`tests/test_context_compactor.py`、`tests/test_context_summarizer.py`、`tests/test_context_manager.py`、`tests/test_commands.py`、`tests/test_agent.py`、`tests/test_tui_smoke.py`  
**依赖：** T1-T20  
**步骤：**
1. 运行新增上下文测试集，修正导入、类型和异步测试问题。
2. 运行命令、Agent 和 TUI 相关回归测试，修复上下文接入造成的行为差异。
3. 确认新增默认配置不会破坏现有配置测试。

**验证：** 运行 `python -m pytest tests/test_context_estimator.py tests/test_context_compactor.py tests/test_context_summarizer.py tests/test_context_manager.py tests/test_config.py tests/test_session.py tests/test_prompting.py tests/test_commands.py tests/test_agent.py tests/test_tui_smoke.py -q`，期望全部通过。

## T22: 全量测试和静态导入检查

**文件：** `src/mewcode/context/*.py`、`src/mewcode/*.py`、`tests/*.py`  
**依赖：** T21  
**步骤：**
1. 运行完整 pytest 套件。
2. 运行 `python -m compileall src tests` 检查语法和导入。
3. 修复任何由上下文管理引入的全局回归。

**验证：** 运行 `python -m pytest -q` 和 `python -m compileall src tests`，期望全部通过。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18 → T19 → T20 → T21 → T22
```
