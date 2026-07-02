# MewCode Hook System Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [x] [AC1] 未配置 `hooks` 时 MewCode 行为不变，普通对话和工具调用正常（验证：运行 `python -m pytest tests/test_config.py::test_loads_required_yaml_fields tests/test_agent.py::test_runner_runs_multiple_tool_iterations_until_final_answer tests/test_tool_scheduler.py::test_side_effect_tools_run_serially -q`，期望全部通过）
- [x] [AC2, AC3] Hook 配置缺少事件、缺少动作、未知事件、未知动作、无效条件、无效匹配表达式、无效 timeout 和 `tool.before` 后台异步都会在配置加载阶段报错（验证：运行 `python -m pytest tests/test_hooks_config.py -q`，期望全部通过）
- [x] [AC4] 会话、轮次、消息、工具和系统级事件都能触发匹配 Hook（验证：运行 `python -m pytest tests/test_agent.py::test_runner_emits_turn_and_message_hooks tests/test_agent.py::test_runner_emits_system_hook_events tests/test_tool_scheduler.py::test_after_tool_emits_result_event tests/test_tui_smoke.py::test_tui_emits_session_hook_events -q`，期望全部通过）
- [x] [AC5] 省略条件的 Hook 在对应事件发生时无条件触发（验证：运行 `python -m pytest tests/test_hooks.py::test_hook_conditions_match_expected_events -q`，期望无条件规则用例通过）
- [x] [AC6, AC7, AC8] 条件组合只支持 `all` 或 `any`，匹配语义正确，混用时报配置错误（验证：运行 `python -m pytest tests/test_hooks.py::test_hook_conditions_match_expected_events tests/test_hooks_config.py::test_rejects_mixed_condition_logic -q`，期望全部通过）
- [x] [AC9] Hook 条件和权限规则都支持精确、反向、正则和 glob 匹配（验证：运行 `python -m pytest tests/test_matching.py tests/test_permissions.py::test_permission_rules_support_regex_and_negation -q`，期望全部通过）
- [x] [AC10] Hook 条件引用当前事件不存在的字段时按未匹配处理，Agent 主流程继续（验证：运行 `python -m pytest tests/test_hooks.py::test_missing_condition_field_does_not_raise -q`，期望通过）
- [x] [AC11, AC12] `tool.before` 可以按工具名和参数拦截工具，目标工具不执行，模型收到包含拒绝原因的工具失败结果（验证：运行 `python -m pytest tests/test_hooks.py::test_before_tool_can_block_call tests/test_tool_scheduler.py::test_hook_before_blocks_before_permission tests/test_agent.py::test_runner_feeds_hook_block_back_to_model -q`，期望全部通过）
- [x] [AC13] `tool.before` 未产出拒绝结果时，工具继续经过原有策略、权限和真实执行流程（验证：运行 `python -m pytest tests/test_tool_scheduler.py::test_hook_before_allows_existing_policy_permission_and_execution -q`，期望通过）
- [x] [AC14, AC15] command 动作在项目工作目录执行，并记录成功、失败和超时；超时不会中断 Agent 主流程（验证：运行 `python -m pytest tests/test_hooks.py::test_command_action_runs_through_tool_executor tests/test_hooks.py::test_command_action_timeout_is_recorded -q`，期望全部通过）
- [x] [AC16] prompt 动作注入内容进入后续模型请求的运行时上下文，不伪造用户消息，不写入会话历史（验证：运行 `python -m pytest tests/test_prompting.py::test_prompt_builder_includes_hook_injections tests/test_agent.py::test_runner_injects_hook_prompt_into_next_request -q`，期望全部通过）
- [x] [AC17] HTTP 动作会发送声明请求，并能观察响应成功、失败和超时状态（验证：运行 `python -m pytest tests/test_hooks.py::test_http_action_sends_request tests/test_hooks.py::test_http_action_failure_does_not_raise -q`，期望全部通过）
- [x] [AC18] sub_agent 动作只记录占位状态，不启动真实子 Agent，不阻塞 Agent 主流程（验证：运行 `python -m pytest tests/test_hooks.py::test_sub_agent_action_is_placeholder -q`，期望通过）
- [x] [AC19] `once` Hook 当前运行期只执行第一次，后续命中跳过，重建 HookManager 后可再次执行（验证：运行 `python -m pytest tests/test_hooks.py::test_hook_manager_once_and_background_behavior -q`，期望 once 用例通过）
- [x] [AC20] 非拦截后台 Hook 不阻塞 Agent 主流程，后台失败只记录 Hook 状态（验证：运行 `python -m pytest tests/test_hooks.py::test_hook_manager_once_and_background_behavior tests/test_hooks.py::test_background_hook_failure_is_recorded -q`，期望全部通过）
- [x] [AC21] Hook 动作抛异常、命令失败、HTTP 失败或占位不可运行时，TUI 不崩溃，输入区恢复可用，会话可继续（验证：运行 `python -m pytest tests/test_hooks.py::test_action_failure_returns_failed_result tests/test_agent.py::test_hook_failure_does_not_stop_agent tests/test_tui_smoke.py::test_hook_failure_does_not_break_input -q`，期望全部通过）
- [x] [AC22] 多条 Hook 规则匹配同一事件时按 YAML 声明顺序执行，无显式优先级也能得到稳定顺序（验证：运行 `python -m pytest tests/test_hooks.py::test_hook_manager_runs_matching_rules_in_order -q`，期望通过）
- [x] [AC23] Hook 不能绕过权限系统、Plan Mode、Skill 工具白名单或项目路径沙箱（验证：运行 `python -m pytest tests/test_hooks.py::test_command_action_respects_plan_mode_and_permissions tests/test_tool_scheduler.py::test_scheduler_plan_mode_blocks_side_effect_before_permission tests/test_permissions.py::test_file_tool_cannot_escape_project_sandbox -q`，期望全部通过）

## 集成
- [x] Hook 配置从主配置 `hooks` 字段加载，项目级配置整体覆盖用户级配置（验证：运行 `python -m pytest tests/test_config.py::test_loads_hook_config tests/test_config.py::test_project_hooks_override_user_hooks -q`，期望全部通过）
- [x] HookManager、AgentLoopRunner、ToolCallScheduler 和 PromptBuilder 的公开接口至少被一个真实调用方使用（验证：运行 `python -m pytest tests/test_agent.py tests/test_tool_scheduler.py tests/test_prompting.py -q`，期望全部通过）
- [x] 工具调度的并发读工具、串行副作用工具和混合批次顺序在接入 Hook 后不回退（验证：运行 `python -m pytest tests/test_tool_scheduler.py::test_read_only_batch_runs_concurrently tests/test_tool_scheduler.py::test_side_effect_tools_run_serially tests/test_tool_scheduler.py::test_scheduler_keeps_results_in_original_call_order_for_mixed_tools -q`，期望全部通过）
- [x] 现有权限拒绝、高危命令拒绝和工具失败仍作为工具结果回灌给模型（验证：运行 `python -m pytest tests/test_agent.py::test_runner_feeds_permission_denial_back_to_model tests/test_agent.py::test_runner_does_not_execute_dangerous_command tests/test_agent.py::test_runner_runs_multiple_tool_iterations_until_final_answer -q`，期望全部通过）
- [x] TUI 能消费 `hook_finished` 事件，并只对失败、拦截或占位状态显示简短状态（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_tui_displays_high_signal_hook_events -q`，期望通过）
- [x] README 包含 Hook 配置入口、条件、拦截、prompt、command、HTTP、sub_agent、once、background 和权限边界说明（验证：运行 `rg -n "hooks:|tool.before|hook_blocked|background|sub_agent|权限系统|Plan Mode" README.md`，期望全部关键词命中）

## 编译与测试
- [x] 项目 Python 文件语法正确（验证：运行 `python -m compileall src tests -q`，期望退出码为 0）
- [x] Hook 相关单元与集成测试全部通过（验证：运行 `python -m pytest tests/test_matching.py tests/test_hooks_config.py tests/test_hooks.py tests/test_config.py tests/test_permissions.py tests/test_prompting.py tests/test_agent.py tests/test_tool_scheduler.py tests/test_tui_smoke.py -q`，期望全部通过）
- [x] 全量测试通过，确认 Hook 接入未破坏 MCP、Skill、上下文、记忆、Provider 和 TUI 既有行为（验证：运行 `python -m pytest -q`，期望全部通过）
- [x] 本项目当前未配置独立 lint 工具，不能跳过语法和测试检查（验证：查看 `pyproject.toml` 无 lint 配置，并已运行 `python -m compileall src tests -q` 与 `python -m pytest -q`）

## 端到端场景
- [x] 场景 1：无 Hook 配置启动后执行普通工具任务，行为与现有版本一致（验证：在 tmux 中启动 `python tests/e2e_mock_openai_server.py 18765`；配置 `.mewcode.yaml` 使用 mock OpenAI 且不声明 `hooks`；另一个 tmux pane 启动 `mewcode --new-session`；输入“读取 README.md 并总结一句”；观察工具调用正常、最终回复正常、输入区恢复可用）
- [x] 场景 2：`tool.before` Hook 拦截危险工具参数，拒绝原因回灌给模型后模型调整回复（验证：在 tmux 中使用 mock OpenAI 配置和 `tool.before` 拦截规则启动 `mewcode --new-session`；输入会触发被拦截工具的真实请求；观察工具未执行、工具结果包含 `hook_blocked` 和拒绝原因、最终回复引用拒绝原因、输入区恢复可用）
- [x] 场景 3：prompt Hook 注入上下文影响下一次模型请求但不写入用户消息历史（验证：在 tmux 中配置 `turn.start` prompt Hook；启动 `mewcode --new-session` 并输入普通请求；观察 mock server 收到的请求运行时提示含 `<mewcode_hook_instructions>`，会话显示区没有伪造用户消息，最终回复正常）
- [x] 场景 4：后台 Hook 失败不影响主流程（验证：在 tmux 中配置一个后台 HTTP Hook 指向不可达地址；启动 `mewcode --new-session` 并输入普通请求；观察 Agent 仍完成回复，TUI 未崩溃，输入区恢复可用）
