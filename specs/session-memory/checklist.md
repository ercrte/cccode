# JulyCode 会话恢复与长期记忆 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] 会话 ID 使用 `YYYYMMDD-HHMMSS-xxxx` 格式，且同一秒内不会冲突（验证：运行 `python -m pytest tests/test_session_id.py -q`，期望全部通过）
- [ ] `memory:` 配置可加载默认值、显式值，并拒绝非法值（验证：运行 `python -m pytest tests/test_config.py::test_loads_memory_config tests/test_config.py::test_rejects_invalid_memory_config -q`，期望全部通过）
- [ ] 三层项目指令按项目管理目录级、项目根级、用户级加载并按优先级注入上下文（验证：运行 `python -m pytest tests/test_memory_instructions.py::test_loads_three_instruction_layers_in_priority_order tests/test_prompting.py::test_runtime_prompt_includes_project_instructions_by_priority -q`，期望全部通过）
- [ ] 缺失指令文件不会阻止启动，也不会产生无意义告警（验证：运行 `python -m pytest tests/test_memory_instructions.py::test_missing_instruction_files_are_silent -q`，期望通过）
- [ ] 指令 `@include` 能展开合法相对路径，并拦截循环引用、嵌套过深和路径越界（验证：运行 `python -m pytest tests/test_memory_instructions.py::test_include_expands_relative_file tests/test_memory_instructions.py::test_include_blocks_cycle_depth_and_path_escape -q`，期望全部通过）
- [ ] 会话消息以 JSONL 追加写入，用户、助手和工具消息都能被序列化并恢复（验证：运行 `python -m pytest tests/test_session_store.py::test_chat_message_round_trip_json tests/test_session_store.py::test_store_appends_messages_as_jsonl -q`，期望全部通过）
- [ ] 会话 checkpoint 能记录压缩后的近期消息和上下文摘要，且不依赖 meta 文件（验证：运行 `python -m pytest tests/test_session_store.py::test_store_appends_checkpoint tests/test_session_store.py::test_checkpoint_restores_messages_and_summary -q`，期望全部通过）
- [ ] 会话列表的标题、消息数和最近更新时间来自 JSONL 扫描，删除额外 meta 文件不影响结果（验证：运行 `python -m pytest tests/test_session_store.py::test_list_sessions_scans_jsonl_for_title_count_and_time tests/test_session_store.py::test_list_sessions_does_not_require_meta_file -q`，期望全部通过）
- [ ] JSONL 中坏行会被跳过，其他有效消息仍可恢复（验证：运行 `python -m pytest tests/test_session_store.py::test_load_session_skips_bad_lines tests/test_session_store.py::test_load_session_keeps_valid_lines_after_bad_line -q`，期望全部通过）
- [ ] 超过 30 天未活动会话会被清理，未过期会话、项目指令和长期笔记不受影响（验证：运行 `python -m pytest tests/test_session_store.py::test_latest_unexpired_session tests/test_session_store.py::test_cleanup_expired_sessions_keeps_memory_files -q`，期望全部通过）
- [ ] 恢复历史会在协议安全边界截断未配对工具调用和孤立工具结果（验证：运行 `python -m pytest tests/test_session_recovery.py::test_validator_keeps_complete_tool_segments tests/test_session_recovery.py::test_validator_truncates_invalid_tool_history -q`，期望全部通过）
- [ ] 默认启动恢复最近未过期会话，显式空会话入口可启动全新会话（验证：运行 `python -m pytest tests/test_session_recovery.py::test_bootstrap_restores_latest_session_by_default tests/test_session_recovery.py::test_bootstrap_can_start_new_empty_session -q`，期望全部通过）
- [ ] 恢复距离上次活动过久的会话时，下一次模型上下文包含时间跨度提醒（验证：运行 `python -m pytest tests/test_session_recovery.py::test_bootstrap_adds_time_gap_notice_for_old_session tests/test_prompting.py::test_runtime_prompt_includes_restore_notice -q`，期望全部通过）
- [ ] 恢复后上下文超预算时先尝试一次压缩；压缩失败时启动空会话并说明原因（验证：运行 `python -m pytest tests/test_session_recovery.py::test_bootstrap_compacts_oversized_restored_session tests/test_session_recovery.py::test_bootstrap_starts_empty_when_restored_session_still_over_limit -q`，期望全部通过）
- [ ] 自动笔记以带 frontmatter 的 Markdown 独立存储，并按用户偏好、纠正反馈、项目知识、参考资料分类（验证：运行 `python -m pytest tests/test_memory_notes.py::test_write_and_read_memory_note tests/test_memory_notes.py::test_notes_are_grouped_by_scope_and_category -q`，期望全部通过）
- [ ] 自动笔记写入前会过滤常见密钥和敏感 token（验证：运行 `python -m pytest tests/test_memory_notes.py::test_note_store_redacts_sensitive_values -q`，期望通过）
- [ ] 用户级和项目级记忆索引能生成、读取，并按四类固定顺序组织（验证：运行 `python -m pytest tests/test_memory_index.py::test_builds_memory_index_by_category tests/test_memory_index.py::test_read_index_returns_existing_index -q`，期望全部通过）
- [ ] 记忆索引最终满足 200 行和 25KB 上限（验证：运行 `python -m pytest tests/test_memory_index.py::test_memory_index_is_limited_by_lines_and_bytes -q`，期望通过）
- [ ] 自动笔记更新请求不携带工具，能创建、更新、跳过笔记，并由模型操作避免重复索引条目（验证：运行 `python -m pytest tests/test_memory_updater.py::test_updater_requests_without_tools tests/test_memory_updater.py::test_updater_creates_and_updates_notes tests/test_memory_updater.py::test_updater_skip_does_not_write_note tests/test_memory_updater.py::test_updater_deduplicates_by_model_operations -q`，期望全部通过）
- [ ] 自动笔记更新失败不会产生部分写入（验证：运行 `python -m pytest tests/test_memory_updater.py::test_updater_fails_without_partial_writes -q`，期望通过）

## 集成

- [ ] PromptBuilder 将项目指令、记忆索引、恢复提醒和运行期上下文摘要放在独立标签块中，来源和优先级清晰（验证：运行 `python -m pytest tests/test_prompting.py::test_runtime_prompt_includes_project_instructions_by_priority tests/test_prompting.py::test_runtime_prompt_includes_memory_indexes tests/test_prompting.py::test_runtime_prompt_keeps_memory_and_context_summary_separate -q`，期望全部通过）
- [ ] 稳定提示不被项目指令或长期记忆污染，仍保持可缓存的确定内容（验证：运行 `python -m pytest tests/test_prompting.py::test_stable_prompt_is_deterministic_and_has_no_empty_optional_sections -q`，期望通过）
- [ ] ContextManager 重量压缩成功后会写入 JSONL checkpoint，手动和自动压缩行为一致（验证：运行 `python -m pytest tests/test_context_manager.py::test_heavy_compaction_appends_session_checkpoint tests/test_context_manager.py::test_manual_compact_appends_session_checkpoint -q`，期望全部通过）
- [ ] Agent Loop 在每次模型请求前读取最新知识上下文，并在没有 memory manager 时保持旧行为（验证：运行 `python -m pytest tests/test_agent.py::test_runner_injects_memory_context_before_model_request tests/test_agent.py::test_runner_works_without_memory_manager -q`，期望全部通过）
- [ ] Agent Loop 只在最终回复无工具调用的自然完成分支调度自动笔记，取消、报错、迭代上限和工具中间态不调度（验证：运行 `python -m pytest tests/test_agent.py::test_runner_schedules_memory_update_on_natural_completion tests/test_agent.py::test_runner_does_not_schedule_memory_update_on_non_natural_stop -q`，期望全部通过）
- [ ] SessionMemoryManager 能返回最新运行时知识上下文，并捕获后台自动笔记失败（验证：运行 `python -m pytest tests/test_session_recovery.py::test_memory_manager_returns_runtime_context tests/test_memory_updater.py::test_memory_manager_background_update_failure_is_captured -q`，期望全部通过）
- [ ] TUI 能显示恢复结果、坏行告警、指令告警和时间跨度提醒，并把 memory manager 传给 Agent Runner（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_tui_displays_restore_report tests/test_tui_smoke.py::test_tui_passes_memory_manager_to_runner -q`，期望全部通过）
- [ ] CLI 支持 `--new-session`，默认启动恢复会话，且不破坏 MCP 初始化和权限控制器接入（验证：运行 `python -m pytest tests/test_mcp_manager.py::test_cli_initializes_mcp_manager_and_closes_it tests/test_tui_smoke.py::test_tui_lifecycle_initializes_and_closes_mcp_manager -q`，期望全部通过）
- [ ] `memory.enabled=false` 时不恢复、不落盘、不自动记忆，普通空会话行为保持可用（验证：运行 `python -m pytest tests/test_session_recovery.py::test_bootstrap_disabled_memory_starts_plain_session tests/test_agent.py::test_runner_works_without_memory_manager -q`，期望全部通过）
- [ ] README 和 `.gitignore` 已说明并忽略 `.julycode/sessions/`、`.julycode/memory/`、`.julycode/context/` 自动产物（验证：运行 `python -m pytest tests/test_config.py::test_readme_mentions_session_memory -q`，期望通过）

## 编译与测试

- [ ] 项目 Python 文件可编译（验证：运行 `python -m compileall src tests`，期望退出码为 0）
- [ ] 本阶段新增和修改的核心测试通过（验证：运行 `python -m pytest tests/test_session_id.py tests/test_memory_instructions.py tests/test_session_store.py tests/test_session_recovery.py tests/test_memory_notes.py tests/test_memory_index.py tests/test_memory_updater.py tests/test_prompting.py tests/test_agent.py tests/test_tui_smoke.py -q`，期望全部通过）
- [ ] 全量 pytest 通过，确认普通聊天、工具调用、Plan Mode、`/do`、权限确认、MCP 工具、上下文压缩和流式显示无回归（验证：运行 `python -m pytest -q`，期望全部通过）
- [ ] 项目未配置独立 lint 工具时不新增伪 lint 步骤；若后续加入 lint 配置，按配置运行对应命令（验证：检查 `pyproject.toml` 中没有 ruff、flake8、mypy 等 lint 配置；当前以 `compileall` 和 `pytest` 作为静态与行为检查）

## 端到端场景

- [ ] 场景 1：在 tmux 中启动 JulyCode，输入一段要求它记住项目约定并调用工具的真实请求，退出后重新启动同一项目；JulyCode 默认恢复最近会话，下一轮请求前上下文包含该项目约定（验证：使用 `tmux new-session -d -s julycode-memory 'julycode'` 启动，`tmux send-keys -t julycode-memory '请记住：本项目新增测试命名必须以 test_memory_ 开头，并查看 README' Enter` 输入，观察工具调用和最终回复；退出后再次 `tmux new-session -d -s julycode-memory-2 'julycode'`，输入 `继续刚才的约定，说明测试命名规则`，期望回复遵循约定并显示恢复信息）
- [ ] 场景 2：在 tmux 中使用 `julycode --new-session` 启动同一项目；旧会话不应自动污染当前对话（验证：运行 `tmux new-session -d -s julycode-new 'julycode --new-session'`，输入 `刚才的测试命名规则是什么？`，期望模型不能把旧会话当作当前用户刚刚说过的话，界面显示空会话启动状态）
- [ ] 场景 3：在项目内准备 `.julycode/AGENTS.md`、`AGENTS.md` 和用户级 `~/.julycode/AGENTS.md`，并在项目指令中加入合法 include 和越界 include；启动 JulyCode 后发送需要遵循规则的请求（验证：tmux 中观察模型请求后的行为遵循项目管理目录级优先规则；越界 include 产生中文告警但不阻止启动）
- [ ] 场景 4：手工损坏最近会话 JSONL 的最后一行后在 tmux 中启动 JulyCode；系统应跳过坏行并恢复其余历史（验证：向 `.julycode/sessions/<latest>.jsonl` 追加半行非法 JSON，再运行 `tmux new-session -d -s julycode-badline 'julycode'`，期望界面出现坏行告警，继续提问时模型仍能基于坏行前历史回复）
- [ ] 场景 5：构造超过 30 天未活动的会话文件和一个未过期会话文件后启动 JulyCode；过期会话被清理，未过期会话正常恢复（验证：修改测试项目中会话记录时间，运行 `tmux new-session -d -s julycode-cleanup 'julycode'`，观察 `.julycode/sessions/` 中过期文件被删除，未过期文件仍存在）
- [ ] 场景 6：让模型最终回复自然结束后，等待后台自动笔记完成，再发起下一轮请求；记忆索引应已经注入上下文（验证：tmux 中输入 `以后回答我默认用中文，记住这个偏好`，等待最终回复和后台更新，再输入 `用一句话回答你应该用什么语言`，期望回复基于偏好；同时检查用户级或项目级 `memory/index.md` 满足 200 行和 25KB 上限）
