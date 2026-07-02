# MewCode Agent Loop Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/mewcode/config.py` | 增加 Agent Loop 配置与解析 |
| 修改 | `src/mewcode/providers/base.py` | 增加 Token 用量结构和 usage 流事件 |
| 修改 | `src/mewcode/providers/openai.py` | 解析 OpenAI 流式 usage |
| 修改 | `src/mewcode/providers/anthropic.py` | 解析 Anthropic 流式 usage |
| 修改 | `src/mewcode/tools/base.py` | 增加工具安全等级 |
| 修改 | `src/mewcode/tools/builtin.py` | 标注六个内置工具的安全等级 |
| 修改 | `src/mewcode/tools/registry.py` | 支持按安全等级筛选工具描述 |
| 新建 | `src/mewcode/tools/scheduler.py` | 实现工具策略、分批和多工具调度 |
| 修改 | `src/mewcode/session.py` | 增加待执行计划运行期状态 |
| 新建 | `src/mewcode/commands.py` | 解析普通输入、`/plan` 和 `/do` |
| 修改 | `src/mewcode/agent.py` | 实现流式收集器、AgentLoopRunner、停止条件和 Plan Mode |
| 修改 | `src/mewcode/tui/widgets.py` | 展示进度、Token 用量和多工具状态 |
| 修改 | `src/mewcode/tui/app.py` | 接入 AgentLoopRunner、命令解析和取消 |
| 修改 | `src/mewcode/cli.py` | 传入 Agent 配置 |
| 修改 | `tests/test_config.py` | 覆盖 Agent 配置解析 |
| 修改 | `tests/test_openai_provider.py` | 覆盖 OpenAI usage 解析 |
| 修改 | `tests/test_anthropic_provider.py` | 覆盖 Anthropic usage 解析 |
| 修改 | `tests/test_tools.py` | 覆盖工具安全等级 |
| 新建 | `tests/test_tool_scheduler.py` | 覆盖工具策略、分批、并发和串行 |
| 修改 | `tests/test_session.py` | 覆盖待执行计划保存与清理 |
| 新建 | `tests/test_commands.py` | 覆盖 `/plan`、`/do` 和普通输入解析 |
| 修改 | `tests/test_agent.py` | 覆盖 Agent Loop、停止条件、Plan Mode 和流式收集 |
| 修改 | `tests/test_tui_smoke.py` | 覆盖 TUI 进度、Token、多工具、取消和命令提示 |
| 修改 | `tests/e2e_mock_openai_server.py` | 支持多轮、多工具、Plan Mode 和停止条件 mock 场景 |
| 修改 | `README.md` | 更新 Agent Loop、Plan Mode 和边界说明 |

## T1: 增加 Agent 配置

**文件：** `src/mewcode/config.py`、`tests/test_config.py`  
**依赖：** 无  
**步骤：**
1. 增加 `AgentConfig`，默认 `max_iterations` 为 8。
2. 在 `AppConfig` 增加 `agent: AgentConfig` 字段。
3. 在配置解析中支持可选 `agent.max_iterations`，缺省时使用默认值。
4. 增加测试覆盖默认值、自定义正整数和非正整数报错。

**验证：** 运行 `python -m pytest tests/test_config.py -q`，期望通过。

## T2: 扩展 Provider 统一事件

**文件：** `src/mewcode/providers/base.py`、`tests/test_openai_provider.py`、`tests/test_anthropic_provider.py`  
**依赖：** T1  
**步骤：**
1. 增加 `TokenUsage` 数据结构。
2. 在 `StreamEventType` 增加 `usage`。
3. 在 `StreamEvent` 增加 `usage: TokenUsage | None` 字段。
4. 调整现有 Provider 测试的事件断言，确保新增事件类型不破坏旧路径。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py::test_openai_streams_text_and_done tests/test_anthropic_provider.py::test_anthropic_streams_text_thinking_signature_and_done -q`，期望通过。

## T3: 解析 OpenAI Token 用量

**文件：** `src/mewcode/providers/openai.py`、`tests/test_openai_provider.py`  
**依赖：** T2  
**步骤：**
1. 在 OpenAI 请求 payload 中加入流式 usage 请求选项。
2. 解析流式 chunk 中的 `usage` 字段并产出 `StreamEvent(type="usage")`。
3. 将 `prompt_tokens`、`completion_tokens`、`total_tokens` 映射到统一 `TokenUsage`。
4. 增加测试覆盖请求选项和 usage 事件。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py -q`，期望通过。

## T4: 解析 Anthropic Token 用量

**文件：** `src/mewcode/providers/anthropic.py`、`tests/test_anthropic_provider.py`  
**依赖：** T2  
**步骤：**
1. 解析 Anthropic `message_start` 中的 usage。
2. 解析 Anthropic `message_delta` 中的 usage 增量。
3. 将可用字段映射到统一 `TokenUsage`，缺失字段保留为 `None`。
4. 增加测试覆盖 usage 事件和缺失 usage 不报错。

**验证：** 运行 `python -m pytest tests/test_anthropic_provider.py -q`，期望通过。

## T5: 标注工具安全等级

**文件：** `src/mewcode/tools/base.py`、`src/mewcode/tools/builtin.py`、`src/mewcode/tools/registry.py`、`tests/test_tools.py`  
**依赖：** 无  
**步骤：**
1. 增加 `ToolSafety = Literal["read_only", "side_effect"]`。
2. 在 `ToolSpec` 增加 `safety` 字段，默认 `side_effect`。
3. 为 `read_file`、`find_files`、`search_code` 标注 `read_only`。
4. 为 `write_file`、`edit_file`、`run_command` 标注 `side_effect`。
5. 在 `ToolRegistry` 增加按安全等级列出工具规格的方法。
6. 增加测试确认六个内置工具的安全等级。

**验证：** 运行 `python -m pytest tests/test_tools.py -q`，期望通过。

## T6: 实现工具策略

**文件：** `src/mewcode/tools/scheduler.py`、`tests/test_tool_scheduler.py`  
**依赖：** T5  
**步骤：**
1. 新建 `ToolPolicy`，支持 `normal`、`plan`、`do` 三种模式。
2. 实现 `allowed_specs()`：规划模式只返回读类工具，其他模式返回全部工具。
3. 实现 `validate_call()`：未知工具返回 `unknown_tool` 失败结果；规划模式请求有副作用工具返回 `tool_not_allowed` 失败结果。
4. 增加测试覆盖三种模式的工具暴露和受限工具拦截。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py::test_tool_policy_allows_all_tools_in_normal_and_do_modes tests/test_tool_scheduler.py::test_tool_policy_allows_only_read_tools_in_plan_mode tests/test_tool_scheduler.py::test_tool_policy_blocks_side_effect_tool_in_plan_mode -q`，期望通过。

## T7: 实现多工具分批调度

**文件：** `src/mewcode/tools/scheduler.py`、`tests/test_tool_scheduler.py`  
**依赖：** T6  
**步骤：**
1. 增加 `ToolBatch` 和 `ToolCallScheduler`。
2. 实现 `make_batches()`：连续读类工具合并为并发批次，有副作用工具形成单独串行批次。
3. 实现 `run()`：批次之间顺序等待，并发批次内同时执行。
4. 确保 `results()` 按模型原始工具调用顺序返回。
5. 增加测试覆盖读类并发、有副作用串行、混合工具顺序和结果顺序。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py -q`，期望通过。

## T8: 增加待执行计划状态

**文件：** `src/mewcode/session.py`、`tests/test_session.py`  
**依赖：** 无  
**步骤：**
1. 增加 `PendingPlan` 数据结构。
2. 在 `ChatSession` 增加 `pending_plan` 字段。
3. 增加 `save_pending_plan()` 和 `clear_pending_plan()`。
4. 增加测试覆盖保存、替换和清理待执行计划。

**验证：** 运行 `python -m pytest tests/test_session.py -q`，期望通过。

## T9: 实现命令解析

**文件：** `src/mewcode/commands.py`、`tests/test_commands.py`  
**依赖：** T8  
**步骤：**
1. 新建 `AgentCommand` 和 `AgentMode`。
2. 实现普通输入解析，生成 `normal` 命令。
3. 实现 `/plan <需求>` 解析，生成规划指令并保留原始需求。
4. 实现 `/plan` 缺少需求的可见提示。
5. 实现 `/do` 有待执行计划时生成执行指令，无计划时生成可见提示。

**验证：** 运行 `python -m pytest tests/test_commands.py -q`，期望通过。

## T10: 实现流式收集器

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`  
**依赖：** T2、T9  
**步骤：**
1. 增加 `AgentProgress`、`AgentStopReason` 和新版 `TurnEvent`。
2. 增加 `StreamCollection` 和 `StreamCollector`。
3. 收集 `text_delta` 和 `thinking_delta` 时同时产出实时事件并累计完整内容。
4. 收集 `usage` 时产出用量事件并保存最后一次用量。
5. 收到 `message_done` 时优先使用 Provider 给出的完整消息；没有完整消息时用累计内容合成。
6. 增加测试覆盖双路文本收集、thinking 收集和 usage 收集。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_stream_collector_streams_deltas_and_builds_complete_message tests/test_agent.py::test_stream_collector_emits_usage -q`，期望通过。

## T11: 实现基础 Agent Loop

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`  
**依赖：** T7、T10  
**步骤：**
1. 新建 `AgentLoopRunner`，接收会话、Provider、工具注册表、执行器和 Agent 配置。
2. 普通任务开始时追加用户消息。
3. 每轮发出 `progress(model)`，请求模型并收集完整响应。
4. 模型无工具调用时追加助手消息，发出 `message_done` 并结束。
5. 模型有工具调用时追加助手工具调用消息，调用调度器执行全部工具，按顺序追加工具结果。
6. 工具结果回灌后继续下一轮请求模型。
7. 更新现有单轮工具测试为多轮 Agent Loop 语义。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_streams_plain_chat_and_saves_message tests/test_agent.py::test_runner_runs_multiple_tool_iterations_until_final_answer tests/test_agent.py::test_runner_executes_all_tool_calls_in_one_model_response -q`，期望通过。

## T12: 实现停止条件

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`  
**依赖：** T11  
**步骤：**
1. 实现迭代上限停止，最后一轮若仍请求工具则不再执行工具。
2. 实现第一次未知工具回灌、连续第二轮未知工具停止。
3. 实现 Provider 流式错误停止并产出脱敏错误事件。
4. 实现 `cancel()` 和 `asyncio.CancelledError` 处理，产出 `stopped(cancelled)`。
5. 增加测试覆盖四类停止条件。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_stops_at_iteration_limit tests/test_agent.py::test_runner_stops_after_consecutive_unknown_tools tests/test_agent.py::test_runner_reports_provider_error tests/test_agent.py::test_runner_can_be_cancelled -q`，期望通过。

## T13: 接入 Plan Mode 到 Agent Loop

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`  
**依赖：** T9、T12  
**步骤：**
1. `plan` 模式使用只读工具策略构建模型请求。
2. `plan` 模式完成时保存 `PendingPlan`。
3. `plan` 模式遇到有副作用工具调用时回灌 `tool_not_allowed` 结果，不执行真实工具。
4. `do` 模式使用全工具能力执行待执行计划。
5. `do` 模式 completed 后清理待执行计划；取消、错误或上限停止时保留计划。
6. 增加测试覆盖保存计划、替换计划、执行计划和计划失败保留。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_plan_mode_saves_pending_plan tests/test_agent.py::test_plan_mode_blocks_side_effect_tools tests/test_agent.py::test_do_mode_executes_and_clears_pending_plan tests/test_agent.py::test_do_mode_keeps_plan_when_stopped -q`，期望通过。

## T14: 更新 TUI 状态组件

**文件：** `src/mewcode/tui/widgets.py`、`tests/test_tui_smoke.py`  
**依赖：** T2、T10  
**步骤：**
1. 扩展 `StatusBar`，支持显示 Agent 模式、轮次、阶段和 Token 用量。
2. 让 `StatusBar` 可以显示用量未知状态。
3. 扩展 `ToolStatusView`，保存 `tool_call_id`，支持同名工具多次调用时准确更新。
4. 增加测试覆盖状态栏进度、usage 展示和工具调用 id。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_status_bar_renders_agent_progress_and_usage tests/test_tui_smoke.py::test_tool_status_view_tracks_tool_call_id -q`，期望通过。

## T15: TUI 接入 AgentLoopRunner 和命令解析

**文件：** `src/mewcode/tui/app.py`、`src/mewcode/cli.py`、`tests/test_tui_smoke.py`  
**依赖：** T11、T14  
**步骤：**
1. 将 TUI 编排入口从单轮 Runner 切换为 `AgentLoopRunner`。
2. 提交输入时先调用 `parse_agent_command()`。
3. 命令解析返回提示消息时直接展示，不调用 Provider。
4. 消费新版 `TurnEvent`，更新文本、thinking、progress、usage、tool_started、tool_finished、message_done、stopped 和 error。
5. 在 `cli.py` 创建应用时传入 Agent 配置。
6. 更新 smoke 测试覆盖普通聊天、工具调用、`/do` 无计划提示和多工具状态展示。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_submit_streams_text_into_message_view tests/test_tui_smoke.py::test_submit_shows_multiple_tool_statuses_and_final_answer tests/test_tui_smoke.py::test_do_without_plan_shows_prompt_without_provider_call -q`，期望通过。

## T16: 实现 TUI 取消行为

**文件：** `src/mewcode/tui/app.py`、`tests/test_tui_smoke.py`  
**依赖：** T15  
**步骤：**
1. 调整 `Ctrl+C` 行为：运行中取消当前生成任务，空闲时退出。
2. 取消时调用 Runner 取消逻辑或取消当前 asyncio task。
3. 收到取消事件后展示已取消状态，恢复输入区。
4. 保持 `Esc` 始终退出。
5. 增加测试覆盖运行中取消和取消后继续输入。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_ctrl_c_cancels_running_agent_and_recovers_input tests/test_tui_smoke.py::test_escape_still_quits -q`，期望通过。

## T17: 更新 mock OpenAI 服务

**文件：** `tests/e2e_mock_openai_server.py`、`tests/test_tui_smoke.py`  
**依赖：** T15  
**步骤：**
1. 增加 mock 场景：多轮工具调用后最终回复。
2. 增加 mock 场景：一次返回多个工具调用。
3. 增加 mock 场景：`/plan` 只读规划和 `/do` 执行。
4. 增加 mock 场景：迭代上限、连续未知工具和 Provider 错误。
5. 在 TUI smoke 测试中复用关键 mock 行为，避免端到端前才暴露协议问题。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -q`，期望通过。

## T18: 更新 README 能力说明

**文件：** `README.md`  
**依赖：** T15  
**步骤：**
1. 更新简介，说明 MewCode 已支持 Agent Loop。
2. 更新工具章节，删除“最多一轮工具调用”的旧边界。
3. 增加 Plan Mode 使用说明：`/plan <需求>` 和 `/do`。
4. 增加停止条件和本阶段不做事项说明。

**验证：** 运行 `rg -n "Agent Loop|/plan|/do|迭代上限|权限系统|上下文压缩|最多一轮工具调用" README.md`，期望能看到新能力和边界，且旧的“最多一轮工具调用”不再作为当前能力描述出现。

## T19: 运行分层测试

**文件：** `tests/test_config.py`、`tests/test_session.py`、`tests/test_commands.py`、`tests/test_tools.py`、`tests/test_tool_scheduler.py`、`tests/test_agent.py`、`tests/test_openai_provider.py`、`tests/test_anthropic_provider.py`、`tests/test_tui_smoke.py`  
**依赖：** T1-T18  
**步骤：**
1. 运行配置、会话和命令解析测试。
2. 运行工具和调度测试。
3. 运行 Provider 测试。
4. 运行 Agent 和 TUI 测试。
5. 修复失败并重复执行，直到分层测试全部通过。

**验证：** 运行 `python -m pytest tests/test_config.py tests/test_session.py tests/test_commands.py tests/test_tools.py tests/test_tool_scheduler.py tests/test_openai_provider.py tests/test_anthropic_provider.py tests/test_agent.py tests/test_tui_smoke.py -q`，期望通过。

## T20: 运行全量编译和测试

**文件：** `src/mewcode/**/*.py`、`tests/**/*.py`  
**依赖：** T19  
**步骤：**
1. 运行 Python 编译检查。
2. 运行全量 pytest。
3. 修复所有语法错误、导入错误和回归失败。

**验证：** 运行 `python -m compileall src tests` 和 `python -m pytest -q`，期望都通过。

## T21: tmux 端到端冒烟

**文件：** `tests/e2e_mock_openai_server.py`、`README.md`  
**依赖：** T20  
**步骤：**
1. 在 tmux 中启动 mock OpenAI 服务。
2. 在 tmux 中配置并启动 `mewcode`。
3. 输入一个多步真实请求，观察 Agent Loop 是否连续调用多个工具并最终回复。
4. 输入 `/plan <需求>`，观察只读规划是否生成并保存计划。
5. 输入 `/do`，观察是否执行保存计划并在完成后清理计划。
6. 输入会触发停止条件的请求，观察界面是否显示停止原因并恢复输入。

**验证：** 在 tmux 中完成上述操作，期望工具调用状态、最终回复、Plan Mode 和停止条件均符合 `specs/agent-loop/spec.md`。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18 → T19 → T20 → T21
```
