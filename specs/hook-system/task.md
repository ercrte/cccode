# JulyCode Hook System Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/julycode/matching.py` | 权限和 Hook 共用的匹配表达式解析、字段读取和匹配执行 |
| 修改 | `src/julycode/permissions/models.py` | 扩展权限规则匹配模型以复用通用匹配表达式 |
| 修改 | `src/julycode/permissions/rules.py` | 将权限规则匹配迁移到通用匹配模块，并保持现有优先级语义 |
| 新建 | `src/julycode/hooks/__init__.py` | Hook 包公共导出 |
| 新建 | `src/julycode/hooks/models.py` | Hook 配置、事件、动作、运行期状态和执行结果模型 |
| 新建 | `src/julycode/hooks/config.py` | Hook YAML 配置解析和集中校验 |
| 新建 | `src/julycode/hooks/conditions.py` | Hook 条件组合判断 |
| 新建 | `src/julycode/hooks/actions.py` | command、prompt、http、sub_agent 动作执行 |
| 新建 | `src/julycode/hooks/manager.py` | HookManager、once、后台任务、提示注入和工具拦截 |
| 修改 | `src/julycode/config.py` | AppConfig 增加 hooks 字段并解析主配置 |
| 修改 | `src/julycode/prompting/base.py` | RuntimePromptContext 增加 Hook 提示注入字段 |
| 修改 | `src/julycode/prompting/builder.py` | 运行时提示追加 Hook 注入块 |
| 修改 | `src/julycode/agent.py` | 接入轮次、消息、系统级 Hook 和 Hook TurnEvent |
| 修改 | `src/julycode/tools/scheduler.py` | 接入 tool.before 和 tool.after Hook |
| 修改 | `src/julycode/tui/app.py` | 创建和传递 HookManager，触发会话级 Hook，展示 Hook 状态 |
| 修改 | `src/julycode/cli.py` | 使用配置创建 HookManager |
| 修改 | `README.md` | 补充 Hook 配置示例和行为说明 |
| 临时创建 | `.julycode.yaml` | tmux 端到端验证时使用的本地 mock 配置 |
| 新建 | `tests/test_matching.py` | 通用匹配表达式单元测试 |
| 新建 | `tests/test_hooks_config.py` | Hook 配置解析和配置错误测试 |
| 新建 | `tests/test_hooks.py` | Hook 条件、动作、Manager、once、后台和拦截测试 |
| 修改 | `tests/test_permissions.py` | 权限规则兼容和新增匹配语法回归测试 |
| 修改 | `tests/test_config.py` | AppConfig hooks 字段加载和校验测试 |
| 修改 | `tests/test_prompting.py` | Hook 提示词注入测试 |
| 修改 | `tests/test_agent.py` | 生命周期 Hook、系统事件和提示注入集成测试 |
| 修改 | `tests/test_tool_scheduler.py` | 工具前拦截、工具后事件和执行顺序测试 |
| 修改 | `tests/test_tui_smoke.py` | TUI 会话级 Hook 和 Hook 状态回归测试 |

## T1: 实现通用匹配模块

**文件：** `src/julycode/matching.py`、`tests/test_matching.py`  
**依赖：** 无  
**步骤：**
1. 定义 `MatchExpression` 和 `MatchKind`。
2. 实现 `parse_match_expression()`，支持精确、显式 `glob:`、隐式 glob、`regex:` 和 `!` 反向匹配。
3. 实现 `match_expression()`，把待匹配值统一转成字符串后执行对应匹配。
4. 实现 `get_field_value()`，支持点号路径读取嵌套 mapping，字段不存在返回 `None`。
5. 添加精确、glob、regex、反向和缺失字段测试。

**验证：** 运行 `python -m pytest tests/test_matching.py -q`，期望全部通过。

## T2: 迁移权限规则到通用匹配模块

**文件：** `src/julycode/permissions/models.py`、`src/julycode/permissions/rules.py`、`tests/test_permissions.py`  
**依赖：** T1  
**步骤：**
1. 调整权限规则模型，使规则保存通用匹配表达式或等价字段。
2. 修改 `PermissionRuleParser` 使用 `parse_match_expression()` 解析规则模式。
3. 修改 `PermissionRuleSet.match()` 使用 `match_expression()` 计算候选规则。
4. 保留既有来源优先级、精确优先于 glob、同等能力下 deny 优先的行为。
5. 增加权限规则 regex 和反向匹配测试，并保留现有权限测试兼容性。

**验证：** 运行 `python -m pytest tests/test_matching.py tests/test_permissions.py -q`，期望全部通过。

## T3: 定义 Hook 数据模型和公共导出

**文件：** `src/julycode/hooks/__init__.py`、`src/julycode/hooks/models.py`、`tests/test_hooks.py`  
**依赖：** 无  
**步骤：**
1. 新建 `julycode.hooks` 包。
2. 定义 Hook 事件名、动作类型、条件组合、动作配置、事件载体、执行结果、运行期状态、提示注入和工具决策模型。
3. 在 `__init__.py` 导出外部需要使用的模型和工厂函数占位引用。
4. 添加模型可导入和默认值测试。

**验证：** 运行 `python -m pytest tests/test_hooks.py::test_hook_models_are_importable -q`，期望通过。

## T4: 实现 Hook 配置解析和集中校验

**文件：** `src/julycode/hooks/config.py`、`tests/test_hooks_config.py`  
**依赖：** T1、T3  
**步骤：**
1. 实现 `parse_hook_config(raw)`，缺失或 `None` 返回空配置。
2. 解析 YAML 列表中的 `name`、`event`、`if`、`action`、`once`、`background`。
3. 校验事件名、动作类型、动作必填字段、timeout 正数和布尔字段。
4. 校验 `if.all` 和 `if.any` 二选一，条件字段和匹配表达式有效。
5. 校验 `tool_block` 只能用于 `tool.before`，且 `tool.before` 不允许 `background: true`。
6. 添加有效配置、缺字段、未知事件、未知动作、混合条件、无效 timeout 和拦截异步冲突测试。

**验证：** 运行 `python -m pytest tests/test_hooks_config.py -q`，期望全部通过。

## T5: 接入 AppConfig 的 hooks 字段

**文件：** `src/julycode/config.py`、`tests/test_config.py`  
**依赖：** T4  
**步骤：**
1. 给 `AppConfig` 增加 `hooks: HookConfig` 默认字段。
2. 在 `_parse_config()` 中调用 `parse_hook_config(raw.get("hooks"))`。
3. 调整现有配置加载测试的默认 `AppConfig` 期望值。
4. 添加未声明 hooks 默认为空、项目级 hooks 覆盖用户级 hooks、无效 hooks 配置报错测试。

**验证：** 运行 `python -m pytest tests/test_config.py -q`，期望全部通过。

## T6: 实现 Hook 条件判断

**文件：** `src/julycode/hooks/conditions.py`、`tests/test_hooks.py`  
**依赖：** T1、T3、T4  
**步骤：**
1. 实现 `rule_matches(rule, event)`。
2. 事件名不一致时返回不匹配。
3. 无条件规则在事件名一致时匹配。
4. `all` 条件要求全部匹配，`any` 条件要求至少一个匹配。
5. 字段不存在时按未匹配处理，不抛异常。
6. 添加无条件、all、any、字段缺失、工具参数匹配测试。

**验证：** 运行 `python -m pytest tests/test_hooks.py::test_hook_conditions_match_expected_events tests/test_hooks.py::test_missing_condition_field_does_not_raise -q`，期望通过。

## T7: 实现 prompt 和 sub_agent 动作

**文件：** `src/julycode/hooks/actions.py`、`tests/test_hooks.py`  
**依赖：** T3  
**步骤：**
1. 新建 `HookActionRunner`。
2. 实现 prompt 动作，把提示词转成 `HookExecutionResult(success)`，供 Manager 入队。
3. 实现 sub_agent 动作占位，返回 `HookExecutionResult(placeholder)`，不启动真实子 Agent。
4. 捕获动作异常并返回 `HookExecutionResult(failed)`。
5. 添加 prompt 成功、sub_agent 占位和异常失败测试。

**验证：** 运行 `python -m pytest tests/test_hooks.py::test_prompt_action_returns_injection_result tests/test_hooks.py::test_sub_agent_action_is_placeholder -q`，期望通过。

## T8: 实现 command 动作

**文件：** `src/julycode/hooks/actions.py`、`tests/test_hooks.py`  
**依赖：** T2、T7  
**步骤：**
1. 在 `HookActionRunner` 中实现 command 动作。
2. 将 command 动作映射为 `run_command` 工具调用，复用当前 cwd、ToolPolicy、ToolExecutor 和 PermissionController。
3. 在 Plan Mode 或 Skill 白名单不允许 `run_command` 时返回 Hook 失败结果，不执行命令。
4. 当权限需要交互确认时返回 Hook 失败结果，不弹出确认。
5. 记录成功退出、非零退出、超时和权限拒绝摘要，并做脱敏。
6. 添加 command 成功、超时、Plan Mode 拒绝、权限拒绝测试。

**验证：** 运行 `python -m pytest tests/test_hooks.py::test_command_action_runs_through_tool_executor tests/test_hooks.py::test_command_action_respects_plan_mode_and_permissions -q`，期望通过。

## T9: 实现 HTTP 动作

**文件：** `src/julycode/hooks/actions.py`、`tests/test_hooks.py`  
**依赖：** T7  
**步骤：**
1. 在 `HookActionRunner` 中实现 HTTP 动作。
2. 使用 `httpx.AsyncClient(trust_env=False)` 发送声明的 method、url、headers、body 或 json。
3. 在 Plan Mode 或受限 Skill 运行上下文中跳过 HTTP 副作用并返回 Hook 失败结果。
4. 记录响应状态和脱敏后的短响应摘要。
5. 捕获 HTTP 错误和超时，返回 Hook 失败结果。
6. 使用 `httpx.MockTransport` 添加成功、超时或异常、受限模式拒绝测试。

**验证：** 运行 `python -m pytest tests/test_hooks.py::test_http_action_sends_request tests/test_hooks.py::test_http_action_failure_does_not_raise -q`，期望通过。

## T10: 实现 HookManager 核心流程

**文件：** `src/julycode/hooks/manager.py`、`tests/test_hooks.py`  
**依赖：** T6、T7、T8、T9  
**步骤：**
1. 实现 `create_hook_manager(config)` 和 `HookManager.emit()`。
2. 按规则声明顺序筛选和执行匹配规则。
3. 实现 `once` 运行期去重，当前进程内只执行第一次。
4. 实现后台任务创建、完成回调记录和 `close()` 清理。
5. 实现 prompt 注入入队、查看和消费。
6. 添加声明顺序、once、后台不阻塞、后台失败被记录、提示注入消费测试。

**验证：** 运行 `python -m pytest tests/test_hooks.py::test_hook_manager_runs_matching_rules_in_order tests/test_hooks.py::test_hook_manager_once_and_background_behavior tests/test_hooks.py::test_hook_manager_consumes_prompt_injections -q`，期望通过。

## T11: 实现工具前拦截决策

**文件：** `src/julycode/hooks/manager.py`、`tests/test_hooks.py`  
**依赖：** T10  
**步骤：**
1. 实现 `HookManager.before_tool()`，构造 `tool.before` 事件。
2. 当匹配规则声明 `tool_block` 且动作成功或无需副作用动作时，生成失败 `ToolResult`。
3. 拦截结果使用配置中的 reason 和 error_type，默认 error_type 为 `hook_blocked`。
4. 当动作失败时只记录 Hook 失败，不拦截工具。
5. 实现 `HookManager.after_tool()`，构造 `tool.after` 事件并返回执行结果。
6. 添加工具参数拦截、动作失败不拦截、tool.after 触发测试。

**验证：** 运行 `python -m pytest tests/test_hooks.py::test_before_tool_can_block_call tests/test_hooks.py::test_failed_before_tool_hook_does_not_block tests/test_hooks.py::test_after_tool_emits_result_event -q`，期望通过。

## T12: 接入 PromptBuilder 的 Hook 提示词注入

**文件：** `src/julycode/prompting/base.py`、`src/julycode/prompting/builder.py`、`tests/test_prompting.py`  
**依赖：** T3、T10  
**步骤：**
1. 给 `RuntimePromptContext` 增加 `hook_injections` 字段，默认空序列。
2. 在 `PromptBuilder.build_runtime_prompt()` 中追加 `<julycode_hook_instructions>` 块。
3. 只在存在注入时输出 Hook 指令块。
4. 确保注入块位于运行时补充中，不改变稳定提示块。
5. 添加无注入不出现标签、有注入出现内容、稳定提示块不变测试。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q`，期望全部通过。

## T13: 在工具调度器中接入 tool.before 和 tool.after

**文件：** `src/julycode/tools/scheduler.py`、`tests/test_tool_scheduler.py`  
**依赖：** T11  
**步骤：**
1. 给 `ToolCallScheduler` 增加可选 `hook_manager` 和 `hook_context`。
2. 在每个工具 `tool_started` 后先调用 `hook_manager.before_tool()`。
3. 如果 Hook 返回拦截结果，跳过 ToolPolicy、权限和真实工具执行，直接作为工具结果。
4. 未拦截时保持原有 ToolPolicy、权限和执行顺序。
5. 工具结果确定后调用 `hook_manager.after_tool()`。
6. 为 Hook 执行结果产出 `TurnEvent(type="hook_finished")`。
7. 添加 tool.before 在权限前拦截、未拦截继续执行、tool.after 收到结果、混合批次顺序不回退测试。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py -q`，期望全部通过。

## T14: 扩展 Agent TurnEvent 和 Hook 运行上下文

**文件：** `src/julycode/agent.py`、`tests/test_agent.py`  
**依赖：** T10、T12、T13  
**步骤：**
1. 扩展 `TurnEventType` 增加 `hook_finished`，给 `TurnEvent` 增加 `hook_result`。
2. 给 `AgentLoopRunner` 增加可选 `hook_manager`。
3. 在每轮构造包含 cwd、mode、允许工具、注册表、执行器和权限控制器的 `HookRuntimeContext`。
4. 在 prompt_factory 中消费 Hook 提示注入并传给 `RuntimePromptContext`。
5. 创建 Scheduler 时传入 HookManager 和 HookRuntimeContext。
6. 添加 Hook TurnEvent 可被消费、提示注入进入下一次模型请求测试。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_emits_hook_finished_events tests/test_agent.py::test_runner_injects_hook_prompt_into_next_request -q`，期望通过。

## T15: 接入 Agent 生命周期事件

**文件：** `src/julycode/agent.py`、`tests/test_agent.py`  
**依赖：** T14  
**步骤：**
1. 在用户消息追加后触发 `turn.start` 和 `message.user`。
2. 在模型消息完成后触发 `message.assistant`。
3. 在上下文压缩完成时触发 `system.context_compacted`。
4. 在自然完成、迭代上限、取消、未知工具、上下文限制等停止路径触发 `system.stopped` 和 `turn.end`。
5. 在 Provider 或未预期错误路径触发 `system.error` 和 `turn.end`。
6. 确保 Hook 失败不会改变原有停止原因和会话消息。
7. 添加轮次、消息、上下文压缩、停止、错误事件测试。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_emits_turn_and_message_hooks tests/test_agent.py::test_runner_emits_system_hook_events tests/test_agent.py::test_hook_failure_does_not_stop_agent -q`，期望通过。

## T16: 接入 TUI 和 CLI

**文件：** `src/julycode/tui/app.py`、`src/julycode/cli.py`、`tests/test_tui_smoke.py`  
**依赖：** T5、T14、T15  
**步骤：**
1. 在 CLI 中根据 `config.hooks` 创建 HookManager 并传入 `JulyCodeApp`。
2. 给 `JulyCodeApp.__init__()` 增加可选 `hook_manager`，测试可注入。
3. 在 `on_mount()` 触发 `session.start`，在 `on_unmount()` 触发 `session.end` 并调用 `HookManager.close()`。
4. 创建 `AgentLoopRunner` 时传入 `hook_manager`。
5. 在 `_apply_turn_event()` 中处理 `hook_finished`，失败、拦截和占位状态显示简短消息。
6. 添加会话级 Hook、Hook 状态显示、无 Hook 时 TUI 回归测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -q`，期望全部通过。

## T17: 更新 README Hook 使用说明

**文件：** `README.md`  
**依赖：** T5、T10、T13、T16  
**步骤：**
1. 增加 `hooks:` 配置章节。
2. 给出无条件事件、条件匹配、工具执行前拦截、提示词注入、shell、HTTP 和 sub_agent 占位示例。
3. 说明 `once`、`background`、`timeout_seconds` 的行为。
4. 说明 Hook 失败不打断 Agent 主流程，且不能绕过权限系统、Plan Mode、Skill 白名单和路径沙箱。

**验证：** 运行 `rg -n "hooks:|tool.before|hook_blocked|background|sub_agent" README.md`，期望能匹配到 Hook 配置说明。

## T18: 跑 Hook 相关单元和集成测试

**文件：** `tests/test_matching.py`、`tests/test_hooks_config.py`、`tests/test_hooks.py`、`tests/test_config.py`、`tests/test_permissions.py`、`tests/test_prompting.py`、`tests/test_agent.py`、`tests/test_tool_scheduler.py`、`tests/test_tui_smoke.py`  
**依赖：** T1-T17  
**步骤：**
1. 运行 Hook 相关测试集合。
2. 如果失败，按失败模块修复后重新运行。
3. 确认匹配、配置、动作、Manager、Agent、工具调度、TUI 都通过。

**验证：** 运行 `python -m pytest tests/test_matching.py tests/test_hooks_config.py tests/test_hooks.py tests/test_config.py tests/test_permissions.py tests/test_prompting.py tests/test_agent.py tests/test_tool_scheduler.py tests/test_tui_smoke.py -q`，期望全部通过。

## T19: 跑全量回归测试

**文件：** `src/julycode`、`tests`  
**依赖：** T18  
**步骤：**
1. 运行完整测试套件。
2. 如果非 Hook 模块失败，判断是否为 Hook 接入导致的回归。
3. 修复回归并重新运行全量测试。

**验证：** 运行 `python -m pytest -q`，期望全部通过。

## T20: tmux 端到端验证准备

**文件：** `tests/e2e_mock_openai_server.py`、`.julycode.yaml`、`README.md`  
**依赖：** T19  
**步骤：**
1. 准备一份本地 `.julycode.yaml`，使用 mock OpenAI 配置和至少两条 Hook：一条 `tool.before` 拦截危险参数，一条 prompt 注入。
2. 在 tmux 中启动 `python tests/e2e_mock_openai_server.py 18765`。
3. 在另一个 tmux pane 启动 `julycode --new-session`。
4. 输入真实请求，触发工具调用和 Hook 拦截。
5. 观察工具被拦截后模型收到失败结果并调整回复，输入区恢复可用。

**验证：** 在 tmux 中观察到 `tool.before` 拦截结果、最终模型回复引用拒绝原因、JulyCode 未崩溃且可继续输入。

## 执行顺序

```text
T1 → T2
T3 → T4 → T5
T6 → T7 → T8 → T9 → T10 → T11
T12 → T13 → T14 → T15 → T16
T17 → T18 → T19 → T20
```
