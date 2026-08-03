# JulyCode 上下文管理 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] 上下文模型默认值符合设计，`ContextLimitError` 能携带压缩报告（验证：运行 `python -m pytest tests/test_context_estimator.py::test_context_config_defaults tests/test_context_estimator.py::test_context_limit_error_carries_report -q`，期望通过）
- [ ] `context:` 配置可解析，非法阈值、余量、换算比例和存储目录会报清晰配置错误（验证：运行 `python -m pytest tests/test_config.py::test_loads_context_config tests/test_config.py::test_rejects_invalid_context_config -q`，期望通过）
- [ ] 会话拥有独立上下文状态，支持替换历史消息和设置摘要，摘要不会自动写入普通消息历史（验证：运行 `python -m pytest tests/test_session.py::test_session_has_context_state tests/test_session.py::test_session_can_replace_messages tests/test_session.py::test_session_sets_context_summary -q`，期望通过）
- [ ] Token 估算能覆盖消息、工具描述、运行时提示和工具调用参数（验证：运行 `python -m pytest tests/test_context_estimator.py::test_estimates_request_footprint_from_messages_tools_and_prompt -q`，期望通过）
- [ ] Token 估算在有 usage 时使用锚点和字符增量，在无 usage 时使用全量近似估算（验证：运行 `python -m pytest tests/test_context_estimator.py::test_estimates_with_usage_anchor_delta tests/test_context_estimator.py::test_estimates_without_usage_anchor -q`，期望通过）
- [ ] 外置工具结果保存在项目内 `.julycode/context/<session_id>/tool-results/`，返回路径可被后续读取工具使用（验证：运行 `python -m pytest tests/test_context_compactor.py::test_context_store_writes_tool_result_under_project tests/test_context_compactor.py::test_context_store_returns_readable_relative_path -q`，期望通过）
- [ ] `.gitignore` 忽略 `.julycode/context/`，外置结果不会默认进入版本控制（验证：运行 `python -m pytest tests/test_context_compactor.py::test_context_store_writes_tool_result_under_project -q`，期望断言忽略规则存在）
- [ ] 单个超阈值工具结果会被外置，模型请求中只保留预览、规模信息和可读取路径（验证：运行 `python -m pytest tests/test_context_compactor.py::test_compacts_single_large_tool_result -q`，期望通过）
- [ ] 轻量预防不会改写普通用户消息，也不会重复外置已经压缩过的工具结果（验证：运行 `python -m pytest tests/test_context_compactor.py::test_light_compaction_keeps_user_messages_verbatim tests/test_context_compactor.py::test_compactor_does_not_reexternalize_existing_preview -q`，期望通过）
- [ ] 同一轮工具结果合计超阈值时，系统优先外置最大结果，保留仍在阈值内的小结果原文（验证：运行 `python -m pytest tests/test_context_compactor.py::test_compacts_largest_results_when_turn_total_exceeds_limit tests/test_context_compactor.py::test_turn_compaction_keeps_small_results_when_under_limit -q`，期望通过）
- [ ] 失败工具结果、命令输出、搜索结果和远端工具结果适用同一轻量压缩规则（验证：运行 `python -m pytest tests/test_context_compactor.py::test_compacts_failed_tool_results_with_same_rules -q`，期望通过）
- [ ] 历史切段不会拆开 assistant 工具调用和对应 tool 结果（验证：运行 `python -m pytest tests/test_context_manager.py::test_segmenter_keeps_tool_call_and_results_together -q`，期望通过）
- [ ] 重量兜底按近期 token 预算回选消息，并至少保留最近 5 条消息原文（验证：运行 `python -m pytest tests/test_context_manager.py::test_segmenter_selects_recent_by_token_budget tests/test_context_manager.py::test_segmenter_keeps_minimum_recent_messages -q`，期望通过）
- [ ] 摘要请求不携带任何工具，摘要 prompt 明确禁止工具调用并要求先草稿后正式摘要（验证：运行 `python -m pytest tests/test_context_summarizer.py::test_summarizer_requests_without_tools tests/test_context_summarizer.py::test_summarizer_prompt_requires_draft_and_final_summary -q`，期望通过）
- [ ] 摘要结果只保存 `<final_summary>`，不把 `<analysis_draft>` 写入压缩后的会话状态（验证：运行 `python -m pytest tests/test_context_summarizer.py::test_summarizer_keeps_only_final_summary -q`，期望通过）
- [ ] 摘要缺少正式摘要、正式摘要为空、Provider 报错或模型请求工具时会失败且不污染会话（验证：运行 `python -m pytest tests/test_context_summarizer.py::test_summarizer_fails_on_provider_error tests/test_context_summarizer.py::test_summarizer_fails_if_model_requests_tool tests/test_context_summarizer.py::test_summarizer_fails_without_final_summary -q`，期望通过）
- [ ] 上下文摘要和边界提示会进入运行时系统补充，且无摘要时不生成空摘要块（验证：运行 `python -m pytest tests/test_prompting.py::test_runtime_prompt_includes_context_summary tests/test_prompting.py::test_runtime_prompt_includes_context_boundary_notice tests/test_prompting.py::test_runtime_prompt_omits_summary_block_when_absent -q`，期望通过）
- [ ] `/compact` 会被解析成手动压缩命令，不携带 `model_text`，`/compact extra` 返回参数错误提示（验证：运行 `python -m pytest tests/test_commands.py::test_parse_compact_command tests/test_commands.py::test_parse_compact_command_does_not_become_agent_task tests/test_commands.py::test_parse_compact_with_extra_text_returns_prompt -q`，期望通过）

## 集成
- [ ] 每次 Agent 请求前都会先执行轻量预防，再构建最终 `ChatRequest`（验证：运行 `python -m pytest tests/test_context_manager.py::test_prepare_request_runs_light_compaction_before_building_request tests/test_agent.py::test_runner_prepares_request_through_context_manager -q`，期望通过）
- [ ] `context.enabled=false` 时上下文管理可关闭，普通请求仍能构造成功（验证：运行 `python -m pytest tests/test_context_manager.py::test_prepare_request_can_skip_when_context_disabled -q`，期望通过）
- [ ] 自动重量兜底在保留 13K 安全余量前触发，较早历史被摘要，近期原文保留（验证：运行 `python -m pytest tests/test_context_manager.py::test_auto_heavy_compaction_triggers_before_safety_margin tests/test_context_manager.py::test_heavy_compaction_replaces_old_messages_with_recent_messages -q`，期望通过）
- [ ] 重量兜底成功后会重新构建运行时提示，使最终请求包含新摘要和边界提示（验证：运行 `python -m pytest tests/test_context_manager.py::test_prepare_request_rebuilds_prompt_after_summary -q`，期望通过）
- [ ] `/compact` 手动路径使用 3K 安全余量，短历史返回 no-op，报告包含压缩计数和外置路径（验证：运行 `python -m pytest tests/test_context_manager.py::test_manual_compact_uses_manual_safety_margin tests/test_context_manager.py::test_manual_compact_returns_noop_for_short_history tests/test_context_manager.py::test_manual_compact_report_contains_counts_and_paths -q`，期望通过）
- [ ] 摘要失败会累计次数，连续 3 次后熔断并抛出 context limit，成功摘要后失败计数清零（验证：运行 `python -m pytest tests/test_context_manager.py::test_summary_failure_count_increments tests/test_context_manager.py::test_summary_failure_limit_raises_context_limit tests/test_context_manager.py::test_successful_summary_resets_failure_count -q`，期望通过）
- [ ] 压缩后仍明显超预算时，系统停止本次请求并给出清晰 context limit 原因（验证：运行 `python -m pytest tests/test_context_manager.py::test_context_limit_raised_when_request_still_over_budget tests/test_agent.py::test_runner_stops_on_context_limit -q`，期望通过）
- [ ] Agent Loop 会在 Provider 返回 usage 后记录估算锚点，后续请求能使用锚点估算（验证：运行 `python -m pytest tests/test_agent.py::test_runner_records_usage_anchor_after_model_response -q`，期望通过）
- [ ] 自动压缩发生时 Agent 事件流包含 `context_compacted`，TUI 能展示简短状态（验证：运行 `python -m pytest tests/test_agent.py::test_runner_emits_context_compacted_event tests/test_tui_smoke.py::test_tui_displays_context_compacted_event -q`，期望通过）
- [ ] TUI 输入 `/compact` 会显示压缩报告、恢复输入区，且不会启动普通 Agent Loop 任务（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_compact_command_shows_report_without_agent_task -q`，期望通过）
- [ ] CLI 创建共享 `ContextManager` 并传给 TUI，README 记录配置、`/compact` 和外置路径（验证：运行 `python -m pytest tests/test_config.py::test_loads_context_config tests/test_config.py::test_readme_documents_context_management tests/test_tui_smoke.py::test_cli_entrypoint_is_importable -q`，期望通过）
- [ ] 现有普通聊天、工具调用、工具失败回灌、Plan Mode、`/do` 和权限确认行为不回退（验证：运行 `python -m pytest tests/test_agent.py::test_plan_mode_saves_pending_plan tests/test_agent.py::test_do_mode_executes_and_clears_pending_plan tests/test_tui_smoke.py::test_submit_shows_multiple_tool_statuses_and_final_answer tests/test_tui_smoke.py::test_tui_permission_deny_returns_failure_and_recovers_input -q`，期望通过）

## 编译与测试
- [ ] 新增上下文管理单元测试全部通过（验证：运行 `python -m pytest tests/test_context_estimator.py tests/test_context_compactor.py tests/test_context_summarizer.py tests/test_context_manager.py -q`，期望通过）
- [ ] 命令、配置、会话、提示、Agent 和 TUI 回归测试全部通过（验证：运行 `python -m pytest tests/test_config.py tests/test_session.py tests/test_prompting.py tests/test_commands.py tests/test_agent.py tests/test_tui_smoke.py -q`，期望通过）
- [ ] 项目完整测试套件通过（验证：运行 `python -m pytest -q`，期望通过）
- [ ] Python 文件语法和导入检查通过（验证：运行 `python -m compileall src tests`，期望无编译错误）

## 端到端场景
- [ ] 场景 1：大工具结果继续追问。用户在 tmux 中启动 JulyCode，输入“生成一个大工具输出后继续根据结果回答”，观察工具结果被外置到 `.julycode/context/...`，随后模型通过路径读取细节并继续回复（验证：先运行 `python tests/e2e_mock_openai_server.py 18765`，再在另一个 tmux pane 启动指向 mock server 的 `julycode`，按场景输入并观察报告、工具调用和最终回复）
- [ ] 场景 2：手动压缩。用户在已有多轮对话后输入 `/compact`，观察界面显示压缩报告、保留消息数、摘要状态或 no-op 原因，且没有把 `/compact` 作为普通模型任务回复（验证：tmux 中输入 `/compact`，观察状态栏恢复空闲、消息区出现中文压缩报告，mock server 不收到普通用户任务）
- [ ] 场景 3：自动重量兜底。用户连续触发多轮大输出直到接近窗口上限，观察系统自动生成结构化摘要并补充边界提示，后续追问仍能引用近期原文（验证：tmux 中输入连续大输出场景，观察 `context_compacted` 状态、摘要结构和最终回复）
- [ ] 场景 4：摘要失败熔断。mock server 连续返回无 `<final_summary>` 摘要响应，系统连续失败 3 次后停止本次请求并显示上下文压缩暂时不可用（验证：tmux 中使用摘要失败 mock 场景，观察第三次后不再重复摘要请求，输入区恢复可用）
- [ ] 场景 5：Plan Mode 不回退。用户输入 `/plan 给项目加一个小改动`，再输入 `/do`，观察规划仍只用读类工具、执行阶段仍可用全工具，期间上下文管理不改变 Plan Mode 语义（验证：tmux 中执行 `/plan` 和 `/do`，观察工具限制、计划保存/清除和最终回复）
