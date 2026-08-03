# JulyCode Agent Loop Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] Agent 配置支持默认迭代上限和自定义正整数，非法值会报配置错误（验证：运行 `python -m pytest tests/test_config.py -q`，期望通过）
- [ ] Provider 统一事件包含 Token 用量事件，OpenAI 和 Anthropic 旧的纯文本流式路径不回归（验证：运行 `python -m pytest tests/test_openai_provider.py::test_openai_streams_text_and_done tests/test_anthropic_provider.py::test_anthropic_streams_text_thinking_signature_and_done -q`，期望通过）
- [ ] OpenAI Provider 能请求并解析流式 usage，产出统一 Token 用量事件（验证：运行 `python -m pytest tests/test_openai_provider.py -q`，期望通过）
- [ ] Anthropic Provider 能解析流式 usage，缺失 usage 时不影响回复生成（验证：运行 `python -m pytest tests/test_anthropic_provider.py -q`，期望通过）
- [ ] 六个内置工具均标注安全等级，读类工具为 `read_only`，写入、修改和命令工具为 `side_effect`（验证：运行 `python -m pytest tests/test_tools.py -q`，期望通过）
- [ ] Plan Mode 的工具策略只暴露读类工具，并阻止模型实际执行有副作用工具（验证：运行 `python -m pytest tests/test_tool_scheduler.py::test_tool_policy_allows_only_read_tools_in_plan_mode tests/test_tool_scheduler.py::test_tool_policy_blocks_side_effect_tool_in_plan_mode -q`，期望通过）
- [ ] 普通模式和 `/do` 执行模式会向模型暴露全部工具（验证：运行 `python -m pytest tests/test_tool_scheduler.py::test_tool_policy_allows_all_tools_in_normal_and_do_modes -q`，期望通过）
- [ ] 多个连续读类工具调用会被分为并发批次执行（验证：运行 `python -m pytest tests/test_tool_scheduler.py -q`，期望读类并发测试通过）
- [ ] 多个有副作用工具调用会按模型顺序串行执行（验证：运行 `python -m pytest tests/test_tool_scheduler.py -q`，期望副作用串行测试通过）
- [ ] 同一轮混合读类工具和有副作用工具时，调度顺序不会让有副作用工具越过前面的调用（验证：运行 `python -m pytest tests/test_tool_scheduler.py -q`，期望混合工具顺序测试通过）
- [ ] 工具执行结果按模型原始工具调用顺序回灌（验证：运行 `python -m pytest tests/test_tool_scheduler.py -q`，期望结果顺序测试通过）
- [ ] 当前运行期会话能保存、替换和清理待执行计划（验证：运行 `python -m pytest tests/test_session.py -q`，期望通过）
- [ ] 普通输入、`/plan <需求>`、缺少需求的 `/plan`、有计划的 `/do` 和无计划的 `/do` 都能被正确解析（验证：运行 `python -m pytest tests/test_commands.py -q`，期望通过）
- [ ] 流式收集器能实时转发文本增量，同时累计完整回复用于后续判断（验证：运行 `python -m pytest tests/test_agent.py::test_stream_collector_streams_deltas_and_builds_complete_message -q`，期望通过）
- [ ] 流式收集器能转发 Token 用量事件，并保留最后一次用量（验证：运行 `python -m pytest tests/test_agent.py::test_stream_collector_emits_usage -q`，期望通过）
- [ ] 普通聊天没有工具调用时，Agent Loop 直接结束并保存最终助手消息（验证：运行 `python -m pytest tests/test_agent.py::test_runner_streams_plain_chat_and_saves_message -q`，期望通过）
- [ ] 多步任务能在一次用户请求内连续多轮调用工具，直到模型给出最终回复（验证：运行 `python -m pytest tests/test_agent.py::test_runner_runs_multiple_tool_iterations_until_final_answer -q`，期望通过）
- [ ] 一次模型响应包含多个工具调用时，Agent Loop 会执行全部工具而不是只执行第一个（验证：运行 `python -m pytest tests/test_agent.py::test_runner_executes_all_tool_calls_in_one_model_response -q`，期望通过）
- [ ] 工具失败结果会回灌给模型，未触发停止条件时允许模型继续调整行动（验证：运行 `python -m pytest tests/test_agent.py -q`，期望工具失败回灌相关测试通过）
- [ ] 达到迭代上限时，Agent Loop 停止继续执行并产出迭代上限停止事件（验证：运行 `python -m pytest tests/test_agent.py::test_runner_stops_at_iteration_limit -q`，期望通过）
- [ ] 第一次未知工具会作为失败结果回灌，连续第二轮未知工具会停止本次任务（验证：运行 `python -m pytest tests/test_agent.py::test_runner_stops_after_consecutive_unknown_tools -q`，期望通过）
- [ ] Provider 或流式收集错误会停止本次任务并产出错误事件（验证：运行 `python -m pytest tests/test_agent.py::test_runner_reports_provider_error -q`，期望通过）
- [ ] Agent Loop 可以被取消，取消后产出 `cancelled` 停止事件（验证：运行 `python -m pytest tests/test_agent.py::test_runner_can_be_cancelled -q`，期望通过）
- [ ] `/plan <需求>` 成功结束后会保存待执行计划（验证：运行 `python -m pytest tests/test_agent.py::test_plan_mode_saves_pending_plan -q`，期望通过）
- [ ] `/plan <需求>` 中模型请求有副作用工具时，不执行真实工具，只回灌受限失败结果（验证：运行 `python -m pytest tests/test_agent.py::test_plan_mode_blocks_side_effect_tools -q`，期望通过）
- [ ] `/do` 会使用全工具能力执行待执行计划，并在成功完成后清理计划（验证：运行 `python -m pytest tests/test_agent.py::test_do_mode_executes_and_clears_pending_plan -q`，期望通过）
- [ ] `/do` 执行被取消、出错或达到上限停止时，待执行计划仍保留以便重试（验证：运行 `python -m pytest tests/test_agent.py::test_do_mode_keeps_plan_when_stopped -q`，期望通过）

## 集成
- [ ] TUI 只消费 Agent Loop 的 `TurnEvent`，不直接依赖 OpenAI 或 Anthropic 原始流事件（验证：运行 `python -m pytest tests/test_agent.py tests/test_tui_smoke.py -q`，期望通过）
- [ ] 状态栏能展示 Agent 模式、轮次、阶段和 Token 用量或用量未知状态（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_status_bar_renders_agent_progress_and_usage -q`，期望通过）
- [ ] 工具状态视图能通过工具调用 id 精确更新同名工具的多个调用（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_tool_status_view_tracks_tool_call_id -q`，期望通过）
- [ ] TUI 普通聊天仍能实时显示流式文本，并在回复结束后恢复输入（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_submit_streams_text_into_message_view -q`，期望通过）
- [ ] TUI 能展示同一轮多个工具调用的开始、完成状态和最终回复（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_submit_shows_multiple_tool_statuses_and_final_answer -q`，期望通过）
- [ ] 用户输入 `/do` 但不存在待执行计划时，TUI 展示提示且不调用 Provider（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_do_without_plan_shows_prompt_without_provider_call -q`，期望通过）
- [ ] 运行中按 `Ctrl+C` 会取消当前 Agent Loop、展示取消状态并恢复输入；空闲时 `Ctrl+C` 仍退出（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_ctrl_c_cancels_running_agent_and_recovers_input -q`，期望通过）
- [ ] `Esc` 仍可退出应用，不受取消行为改动影响（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_escape_still_quits -q`，期望通过）
- [ ] mock OpenAI 服务覆盖多轮工具、多个工具调用、Plan Mode、迭代上限、连续未知工具和 Provider 错误场景（验证：运行 `python -m pytest tests/test_tui_smoke.py -q`，期望通过）
- [ ] README 描述 Agent Loop、`/plan`、`/do`、迭代上限和本阶段不做权限系统/上下文压缩，且不再把“最多一轮工具调用”描述为当前能力（验证：运行 `rg -n "Agent Loop|/plan|/do|迭代上限|权限系统|上下文压缩|最多一轮工具调用" README.md`，期望新能力和边界可见，旧边界只作为历史或不出现）

## 编译与测试
- [ ] 配置、会话、命令解析、工具、调度、Provider、Agent 和 TUI 分层测试全部通过（验证：运行 `python -m pytest tests/test_config.py tests/test_session.py tests/test_commands.py tests/test_tools.py tests/test_tool_scheduler.py tests/test_openai_provider.py tests/test_anthropic_provider.py tests/test_agent.py tests/test_tui_smoke.py -q`，期望通过）
- [ ] Python 文件无语法错误（验证：运行 `python -m compileall src tests`，期望无编译错误）
- [ ] 全部自动化测试通过（验证：运行 `python -m pytest -q`，期望通过）
- [ ] 命令入口仍可导入（验证：运行 `python -c "from julycode.cli import main; print(callable(main))"`，期望输出 `True`）
- [ ] 项目未配置 lint 命令时记录为不适用；如后续配置 lint，则 lint 检查通过（验证：查看 `pyproject.toml` 是否有 lint 配置；若有则运行对应命令，期望退出码为 0）

## 端到端场景
- [ ] 场景 1：普通聊天不触发工具调用时仍流式输出最终回复（验证：在 tmux 中启动 `python tests/e2e_mock_openai_server.py 18765`，配置 JulyCode 指向该服务，启动 `julycode`，输入“用一句话解释递归”，观察文本逐步出现且输入区恢复可用）
- [ ] 场景 2：多步 Agent Loop 能一次请求连续调用多个工具并最终回复（验证：tmux 中输入一个需要搜索代码、读取文件并总结的请求，观察界面依次显示多个工具状态，最终回复引用工具结果且输入区恢复可用）
- [ ] 场景 3：一次模型响应返回多个读类工具时，界面显示多个工具调用，并最终收到汇总回复（验证：使用 mock 场景触发多个 `read_only` 工具调用，观察多个工具状态均完成，最终回复包含所有结果）
- [ ] 场景 4：有副作用工具按顺序执行（验证：使用 mock 场景触发连续写入/修改工具，观察工具状态按顺序完成；随后检查目标文件内容符合预期）
- [ ] 场景 5：达到迭代上限时停止（验证：配置较小 `agent.max_iterations`，使用 mock 场景持续请求工具，观察界面显示迭代上限停止，且没有继续执行后续工具）
- [ ] 场景 6：运行中取消任务（验证：提交会持续运行的 mock 请求后按 `Ctrl+C`，观察界面显示已取消，输入区恢复可用；随后输入普通问题能继续得到回复）
- [ ] 场景 7：连续未知工具停止（验证：使用 mock 场景第一轮返回未知工具、第二轮仍返回未知工具，观察第一次失败结果回灌，第二次显示连续未知工具停止）
- [ ] 场景 8：Provider 错误脱敏并恢复输入（验证：使用 mock 场景或错误配置触发 Provider 错误，观察错误信息不包含完整密钥，输入区恢复可用）
- [ ] 场景 9：`/plan <需求>` 只读规划（验证：tmux 中输入 `/plan 给这个项目加一个简单文件总结功能`，观察只出现读类工具调用，最终展示计划并保存为待执行计划）
- [ ] 场景 10：`/plan <需求>` 阻止有副作用工具（验证：使用 mock 场景让规划阶段请求写入/修改/命令工具，观察该工具未实际执行，模型收到受限失败结果并继续输出计划）
- [ ] 场景 11：`/do` 执行已保存计划并清理计划（验证：完成场景 9 后输入 `/do`，观察执行阶段可调用全工具，最终完成后再次输入 `/do` 会提示没有待执行计划）
- [ ] 场景 12：无计划 `/do` 不启动空执行（验证：重启 JulyCode 或清空计划后输入 `/do`，观察界面直接提示没有待执行计划，mock Provider 没有收到请求）
- [ ] 场景 13：连续生成两个计划时以后一个为准（验证：依次输入两个不同 `/plan <需求>`，再输入 `/do`，观察执行内容对应第二个计划）
- [ ] 场景 14：Anthropic Provider 的 Agent Loop 事件行为与 OpenAI 一致（验证：运行 Anthropic Provider usage 和工具协议相关测试；如有可用 Anthropic 配置，再在 tmux 中输入读文件/Plan Mode 请求，观察工具状态、进度和最终回复行为与 OpenAI 场景一致）
