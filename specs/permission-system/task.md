# MewCode 权限系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/mewcode/permissions/__init__.py` | 权限系统公共导出 |
| 新建 | `src/mewcode/permissions/models.py` | 权限模式、规则、决策、提示和事件模型 |
| 新建 | `src/mewcode/permissions/blacklist.py` | 高危命令硬拦截 |
| 新建 | `src/mewcode/permissions/sandbox.py` | 项目路径沙箱与工具调用目标提取 |
| 新建 | `src/mewcode/permissions/rules.py` | YAML 规则解析、匹配、加载和本地规则写入 |
| 新建 | `src/mewcode/permissions/engine.py` | 五层权限决策编排 |
| 新建 | `src/mewcode/permissions/controller.py` | 调度器使用的权限控制入口和人审结果处理 |
| 修改 | `src/mewcode/config.py` | 增加权限模式配置解析 |
| 修改 | `src/mewcode/tools/scheduler.py` | 工具执行前接入权限控制并发出权限事件 |
| 修改 | `src/mewcode/agent.py` | 扩展 TurnEvent 权限事件并传递权限控制器 |
| 修改 | `src/mewcode/tui/widgets.py` | 新增权限确认视图 |
| 修改 | `src/mewcode/tui/app.py` | 实现 PermissionPrompter 并处理权限事件 |
| 修改 | `src/mewcode/cli.py` | 创建权限控制器并注入 TUI |
| 修改 | `tests/test_config.py` | 覆盖权限配置解析 |
| 新建 | `tests/test_permissions.py` | 覆盖黑名单、沙箱、规则、引擎和控制器 |
| 修改 | `tests/test_tool_scheduler.py` | 覆盖权限 allow、deny、prompt 和 Plan Mode 优先级 |
| 修改 | `tests/test_agent.py` | 覆盖权限拒绝回灌后 Agent Loop 继续 |
| 修改 | `tests/test_tui_smoke.py` | 覆盖权限确认视图、用户选择和输入恢复 |
| 修改 | `tests/e2e_mock_openai_server.py` | 增加权限相关 mock 场景 |
| 修改 | `README.md` | 文档化权限模式、规则文件、黑名单和路径沙箱边界 |

## T1: 权限基础模型

**文件：** `src/mewcode/permissions/models.py`、`src/mewcode/permissions/__init__.py`、`tests/test_permissions.py`  
**依赖：** 无  
**步骤：**
1. 新建 `mewcode.permissions` 包。
2. 定义 `PermissionMode`、`PermissionEffect`、`PermissionRuleSource`、`MatchKind`、`PermissionDecisionKind` 和 `UserPermissionChoice`。
3. 定义 `PermissionConfig`、`PermissionRule`、`RuleMatch`、`PermissionSubject`、`PermissionDecision`、`PermissionPrompt`、`PermissionPromptResult` 和 `PermissionEventPayload`。
4. 定义 `PermissionPrompter` 协议。
5. 在 `__init__.py` 导出公共模型。
6. 添加测试覆盖默认权限模式、规则模型字段和包导入。

**验证：** 运行 `python -m pytest tests/test_permissions.py::test_permission_models_export_defaults -q`，期望通过。

## T2: 高危命令黑名单

**文件：** `src/mewcode/permissions/blacklist.py`、`tests/test_permissions.py`  
**依赖：** T1  
**步骤：**
1. 实现 `DangerousCommandGuard.check(command)`。
2. 增加不可配置的正则模式，覆盖根目录或家目录递归删除、`sudo rm`、磁盘格式化、裸设备写入、关机重启、fork bomb、全局权限破坏、`kill -9 -1` 和 `git clean -fdx`。
3. 命中时返回 `kind="deny"`、`error_type="permission_dangerous_command"` 的 `PermissionDecision`。
4. 未命中时返回 `None`。
5. 添加测试确认危险命令被拦截，普通命令不被拦截。

**验证：** 运行 `python -m pytest tests/test_permissions.py::test_dangerous_command_guard_blocks_high_risk_commands tests/test_permissions.py::test_dangerous_command_guard_allows_safe_commands -q`，期望通过。

## T3: 项目路径沙箱基础

**文件：** `src/mewcode/permissions/sandbox.py`、`tests/test_permissions.py`  
**依赖：** T1  
**步骤：**
1. 实现 `ProjectSandbox.__init__()`，保存解析后的项目根目录。
2. 实现 `resolve_inside(raw_path)`，对相对路径、绝对路径和符号链接执行真实路径解析。
3. 使用 `Path.is_relative_to()` 判断解析后的路径是否仍位于项目根目录内。
4. 实现 `relative_display(path)`，返回项目内相对路径。
5. 添加测试覆盖项目内路径、`..` 逃逸、绝对路径逃逸和符号链接逃逸。

**验证：** 运行 `python -m pytest tests/test_permissions.py::test_project_sandbox_allows_inside_path tests/test_permissions.py::test_project_sandbox_rejects_parent_escape tests/test_permissions.py::test_project_sandbox_rejects_absolute_escape tests/test_permissions.py::test_project_sandbox_rejects_symlink_escape -q`，期望通过。

## T4: 工具调用沙箱检查和匹配目标

**文件：** `src/mewcode/permissions/sandbox.py`、`tests/test_permissions.py`  
**依赖：** T3  
**步骤：**
1. 实现 `check_tool_call(call)`，覆盖 `read_file`、`write_file`、`edit_file`、`search_code` 和 `find_files`。
2. 对 `read_file`、`write_file`、`edit_file` 检查 `path` 参数。
3. 对 `search_code` 检查可选 `path` 参数，缺省按 `.` 处理。
4. 对 `find_files` 拒绝绝对 glob、包含 `..` 的 glob，并预检查匹配文件的真实路径。
5. 实现 `subject_for(call)`，为命令、路径、glob 和搜索调用生成 `PermissionSubject`。
6. 添加测试覆盖各类工具的 subject 和沙箱拒绝。

**验证：** 运行 `python -m pytest tests/test_permissions.py::test_project_sandbox_builds_subject_for_core_tools tests/test_permissions.py::test_project_sandbox_rejects_find_files_escape tests/test_permissions.py::test_project_sandbox_rejects_search_path_escape -q`，期望通过。

## T5: 权限规则解析

**文件：** `src/mewcode/permissions/rules.py`、`tests/test_permissions.py`  
**依赖：** T1  
**步骤：**
1. 实现 `PermissionRuleParser.parse_rule_key(key, source, effect)`。
2. 支持 `工具名(模式)` 格式，校验缺失括号、空工具名、空模式和非法 effect。
3. 将 `Bash(...)` 归一化为 `run_command(...)`。
4. 根据模式中是否包含 glob 通配符自动设置 `match_kind`。
5. 添加测试覆盖精确规则、glob 规则、`Bash` 别名和非法规则。

**验证：** 运行 `python -m pytest tests/test_permissions.py::test_permission_rule_parser_parses_exact_and_glob tests/test_permissions.py::test_permission_rule_parser_normalizes_bash_alias tests/test_permissions.py::test_permission_rule_parser_rejects_invalid_rules -q`，期望通过。

## T6: 规则集匹配优先级

**文件：** `src/mewcode/permissions/rules.py`、`tests/test_permissions.py`  
**依赖：** T5  
**步骤：**
1. 实现 `PermissionRuleSet.match(subject)`。
2. 只匹配相同工具名的规则。
3. 支持对 `PermissionSubject.targets` 逐个执行精确或 glob 匹配。
4. 同一来源内精确匹配优先于 glob 匹配。
5. 同一来源内同等匹配能力发生 allow/deny 冲突时，deny 优先。
6. 添加测试覆盖精确优先、deny 优先、目标列表匹配和不匹配返回 `None`。

**验证：** 运行 `python -m pytest tests/test_permissions.py::test_permission_rule_set_prefers_exact_match tests/test_permissions.py::test_permission_rule_set_prefers_deny_on_equal_match tests/test_permissions.py::test_permission_rule_set_matches_any_subject_target -q`，期望通过。

## T7: 规则文件加载和本地持久化

**文件：** `src/mewcode/permissions/rules.py`、`tests/test_permissions.py`  
**依赖：** T5、T6  
**步骤：**
1. 实现 `SessionPermissionRules`，支持添加会话级规则并导出为 `PermissionRuleSet`。
2. 实现 `PermissionRuleStore.load(cwd)`，读取用户级、项目级和本地级 YAML。
3. 缺失文件按空规则处理。
4. 顶层非对象、缺少 `rules` 对象、非法 effect 或非法规则 key 时抛出 `ConfigError`。
5. 实现 `ordered_rule_sets(session_rules)`，顺序为会话、本地、项目、用户。
6. 实现 `add_local_rule(rule)`，写入或更新 `.mewcode.permissions.local.yaml`。
7. 添加测试覆盖缺失文件、三层加载顺序、格式错误和本地规则写入。

**验证：** 运行 `python -m pytest tests/test_permissions.py::test_permission_rule_store_loads_missing_files_as_empty tests/test_permissions.py::test_permission_rule_store_orders_sources tests/test_permissions.py::test_permission_rule_store_rejects_invalid_yaml tests/test_permissions.py::test_permission_rule_store_writes_local_rule -q`，期望通过。

## T8: 权限引擎硬拒绝和规则优先级

**文件：** `src/mewcode/permissions/engine.py`、`tests/test_permissions.py`  
**依赖：** T2、T4、T7  
**步骤：**
1. 实现 `PermissionEngine.evaluate(call, spec)` 的黑名单检查。
2. 实现文件类工具的沙箱检查，沙箱拒绝优先于规则。
3. 调用规则存储，按会话、本地、项目、用户顺序匹配规则。
4. 显式 deny 直接返回拒绝。
5. 添加测试确认危险命令和沙箱拒绝不能被 allow 规则绕过。
6. 添加测试确认用户级、项目级、本地级和会话级优先级正确。

**验证：** 运行 `python -m pytest tests/test_permissions.py::test_permission_engine_dangerous_command_overrides_allow_rule tests/test_permissions.py::test_permission_engine_sandbox_overrides_allow_rule tests/test_permissions.py::test_permission_engine_uses_highest_priority_rule_source -q`，期望通过。

## T9: 权限模式决策

**文件：** `src/mewcode/permissions/engine.py`、`tests/test_permissions.py`  
**依赖：** T8  
**步骤：**
1. 实现 `strict`、`default` 和 `permissive` 三档模式的 fallback 决策。
2. 在严格模式下，有副作用工具即使命中 allow 也返回 prompt。
3. 在默认模式下，明确 allow 执行，明确 deny 拒绝，未命中的读类工具允许，未命中的有副作用工具 prompt。
4. 在放行模式下，未命中工具允许，但显式 deny 仍拒绝。
5. 添加测试覆盖三种权限模式和显式规则组合。

**验证：** 运行 `python -m pytest tests/test_permissions.py::test_permission_engine_default_mode_decisions tests/test_permissions.py::test_permission_engine_strict_mode_prompts_side_effect_even_when_allowed tests/test_permissions.py::test_permission_engine_permissive_mode_allows_unmatched_but_respects_deny -q`，期望通过。

## T10: 权限控制器和人审结果

**文件：** `src/mewcode/permissions/controller.py`、`src/mewcode/permissions/__init__.py`、`tests/test_permissions.py`  
**依赖：** T7、T9  
**步骤：**
1. 实现 `PermissionController.evaluate()`，代理 `PermissionEngine.evaluate()`。
2. 实现 `denial_result()`，把权限拒绝转为失败 `ToolResult`，错误类型使用 `permission_*`。
3. 实现 `resolve_prompt()`，处理 `allow_once`、`allow_session`、`allow_permanent` 和 `deny`。
4. `allow_session` 写入 `SessionPermissionRules`。
5. `allow_permanent` 写入本地级规则文件；写入失败返回 `permission_persist_failed`。
6. 无 prompter 且需要确认时返回 `permission_confirmation_required` 拒绝。
7. 实现 `create_permission_controller(cwd, config, prompter=None)`。
8. 添加测试覆盖四种用户选择、无 prompter fallback 和拒绝结果格式。

**验证：** 运行 `python -m pytest tests/test_permissions.py::test_permission_controller_turns_denial_into_tool_result tests/test_permissions.py::test_permission_controller_resolves_allow_once tests/test_permissions.py::test_permission_controller_adds_session_rule tests/test_permissions.py::test_permission_controller_persists_local_rule tests/test_permissions.py::test_permission_controller_denies_when_prompt_has_no_prompter -q`，期望通过。

## T11: 权限配置解析

**文件：** `src/mewcode/config.py`、`tests/test_config.py`  
**依赖：** T1  
**步骤：**
1. 在 `AppConfig` 增加 `permissions: PermissionConfig`。
2. 实现 `_parse_permissions(raw)`。
3. 支持 `permissions.mode` 为 `strict`、`default` 或 `permissive`。
4. 缺省时使用 `PermissionConfig(mode="default")`。
5. 非对象配置或未知模式抛出 `ConfigError`。
6. 添加测试覆盖默认值、自定义模式和非法模式。

**验证：** 运行 `python -m pytest tests/test_config.py::test_loads_required_yaml_fields tests/test_config.py::test_loads_permissions_mode tests/test_config.py::test_rejects_invalid_permissions_mode -q`，期望通过。

## T12: 扩展 Agent 权限事件

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`  
**依赖：** T10  
**步骤：**
1. 在 `TurnEventType` 增加 `permission_requested` 和 `permission_resolved`。
2. 在 `TurnEvent` 增加 `permission: PermissionEventPayload | None`。
3. 在 `AgentLoopRunner.__init__()` 增加可选 `permission_controller` 参数。
4. 未传入时用 `executor.context.cwd` 创建放行模式的非交互权限控制器。
5. 保持现有纯聊天、多工具和 Plan Mode 测试不需要显式传权限控制器也能运行。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_streams_plain_chat_and_saves_message tests/test_agent.py::test_runner_executes_all_tool_calls_in_one_model_response -q`，期望通过。

## T13: 调度器接入权限 allow 和 deny

**文件：** `src/mewcode/tools/scheduler.py`、`tests/test_tool_scheduler.py`  
**依赖：** T10、T12  
**步骤：**
1. 在 `ToolCallScheduler.__init__()` 增加可选 `permission_controller`。
2. 在 `_execute_or_reject()` 中先保留 `ToolPolicy.validate_call()` 判断。
3. `ToolPolicy` 通过后调用 `PermissionController.evaluate()`。
4. 权限 allow 时调用 `ToolExecutor.execute()`。
5. 权限 deny 时返回 `PermissionController.denial_result()`，不调用真实工具。
6. 添加测试覆盖 allow 会执行工具、deny 不执行工具且返回权限失败。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py::test_scheduler_permission_allow_executes_tool tests/test_tool_scheduler.py::test_scheduler_permission_deny_skips_tool -q`，期望通过。

## T14: 调度器接入权限 prompt 事件

**文件：** `src/mewcode/tools/scheduler.py`、`tests/test_tool_scheduler.py`  
**依赖：** T13  
**步骤：**
1. 在 `ToolCallScheduler.run()` 中处理 `PermissionDecision(kind="prompt")`。
2. 真正等待用户选择前 yield `TurnEvent(type="permission_requested")`。
3. 调用 `PermissionController.resolve_prompt()` 得到最终决策。
4. 用户选择后 yield `TurnEvent(type="permission_resolved")`。
5. 允许时执行工具，拒绝时返回权限失败结果。
6. 添加测试覆盖 prompt 允许、prompt 拒绝和事件顺序。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py::test_scheduler_permission_prompt_allow_executes_after_events tests/test_tool_scheduler.py::test_scheduler_permission_prompt_deny_returns_failure -q`，期望通过。

## T15: Plan Mode 优先级保持不变

**文件：** `src/mewcode/tools/scheduler.py`、`tests/test_tool_scheduler.py`、`tests/test_agent.py`  
**依赖：** T14  
**步骤：**
1. 确保 `ToolPolicy.validate_call()` 始终先于权限控制器执行。
2. 在 Plan Mode 下，有副作用工具直接返回 `tool_not_allowed`。
3. 确认即使权限配置为放行或存在 allow 规则，也不执行有副作用工具。
4. 添加调度器层测试覆盖 Plan Mode 不进入权限 prompt。
5. 保留 Agent 层 Plan Mode 失败回灌测试。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py::test_scheduler_plan_mode_blocks_side_effect_before_permission tests/test_agent.py::test_plan_mode_blocks_side_effect_tools -q`，期望通过。

## T16: Agent Loop 权限拒绝回灌

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`  
**依赖：** T14  
**步骤：**
1. 将 `permission_controller` 传给每轮创建的 `ToolCallScheduler`。
2. 确认权限失败结果按普通工具结果追加到会话。
3. 确认权限拒绝后没有触发既有停止条件时继续下一轮模型请求。
4. 添加测试：第一轮工具被权限拒绝，第二轮模型读取失败结果后给出最终回复。
5. 添加测试：危险命令权限失败不会启动命令。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_feeds_permission_denial_back_to_model tests/test_agent.py::test_runner_does_not_execute_dangerous_command -q`，期望通过。

## T17: 权限确认视图

**文件：** `src/mewcode/tui/widgets.py`、`tests/test_tui_smoke.py`  
**依赖：** T1  
**步骤：**
1. 新增 `PermissionPromptView`。
2. 展示工具名、关键参数摘要和触发原因。
3. 添加四个按钮：本次允许、本会话允许、永久允许、拒绝。
4. 按钮点击后设置传入的 `asyncio.Future[UserPermissionChoice]`。
5. 决策后移除确认视图或标记为已处理。
6. 添加测试覆盖视图渲染和四个按钮返回值。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_permission_prompt_view_renders_choices tests/test_tui_smoke.py::test_permission_prompt_view_sets_choice -q`，期望通过。

## T18: TUI 实现 PermissionPrompter

**文件：** `src/mewcode/tui/app.py`、`tests/test_tui_smoke.py`  
**依赖：** T16、T17  
**步骤：**
1. 让 `MewCodeApp` 实现 `PermissionPrompter.request_permission()`。
2. 请求权限时向消息区追加 `PermissionPromptView`，并等待 future 完成。
3. 在 `MewCodeApp` 增加 `set_permission_controller(controller)`。
4. 运行任务时把权限控制器传给 `AgentLoopRunner`。
5. 在 `_apply_turn_event()` 中处理 `permission_requested` 和 `permission_resolved`，更新状态栏或等待状态。
6. 添加测试覆盖用户选择本次允许后工具继续执行、用户拒绝后工具失败且输入恢复。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_tui_permission_allow_once_continues_tool tests/test_tui_smoke.py::test_tui_permission_deny_returns_failure_and_recovers_input -q`，期望通过。

## T19: TUI 会话级和永久允许

**文件：** `src/mewcode/tui/app.py`、`tests/test_tui_smoke.py`  
**依赖：** T18  
**步骤：**
1. 确认本会话允许后，相同调用模式在当前运行期不再弹确认。
2. 确认永久允许后，本地级规则文件被写入。
3. 添加测试模拟两次相同有副作用工具调用，第一次选择本会话允许，第二次直接执行。
4. 添加测试模拟永久允许并断言 `.mewcode.permissions.local.yaml` 包含生成的 allow 规则。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_tui_allow_session_reuses_permission tests/test_tui_smoke.py::test_tui_allow_permanent_writes_local_rule -q`，期望通过。

## T20: CLI 注入权限控制器

**文件：** `src/mewcode/cli.py`、`tests/test_config.py`、`tests/test_tui_smoke.py`  
**依赖：** T11、T18  
**步骤：**
1. 在 CLI 启动时读取 `config.permissions`。
2. 创建 `MewCodeApp` 后，用 app 作为 prompter 创建 `PermissionController`。
3. 调用 `app.set_permission_controller(controller)`。
4. 保持配置错误脱敏输出不变。
5. 添加或调整测试确认 CLI 入口仍可导入，权限配置错误走 `ConfigError` 路径。

**验证：** 运行 `python -m pytest tests/test_config.py::test_cli_reports_config_error_without_secret tests/test_tui_smoke.py::test_cli_entrypoint_is_importable -q`，期望通过。

## T21: 权限相关 mock 端到端场景

**文件：** `tests/e2e_mock_openai_server.py`  
**依赖：** T16  
**步骤：**
1. 增加“危险命令”场景：模型请求 `run_command` 执行命中黑名单的命令。
2. 增加“权限拒绝后调整”场景：模型收到权限失败工具结果后改用安全回复。
3. 增加“写入需要确认”场景：模型请求 `write_file`，用于 tmux 中观察确认视图。
4. 确认已有 mock 场景不受新分支影响。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py::test_openai_streams_tool_call_deltas_and_done tests/test_agent.py::test_runner_feeds_permission_denial_back_to_model -q`，期望通过。

## T22: README 权限说明

**文件：** `README.md`  
**依赖：** T20  
**步骤：**
1. 新增权限系统章节，说明五层防御。
2. 文档化 `permissions.mode` 的 strict、default、permissive 三档行为。
3. 文档化用户级、项目级和本地级规则文件位置。
4. 给出 `rules` YAML 示例，包含 `Bash(git *)`、路径 allow 和路径 deny。
5. 说明高危命令和路径沙箱不可被配置或人审绕过。
6. 更新范围章节，移除“权限系统未实现”的旧结论，并保留网络限制、资源配额、审计日志不做。

**验证：** 运行 `rg -n "权限系统|permissions.mode|Bash\\(git \\*\\)|\\.mewcode.permissions|网络请求限制|审计日志" README.md`，期望看到权限说明和仍不做的后续范围。

## T23: 权限系统回归测试

**文件：** `tests/test_permissions.py`、`tests/test_tool_scheduler.py`、`tests/test_agent.py`、`tests/test_config.py`、`tests/test_tui_smoke.py`  
**依赖：** T1-T22  
**步骤：**
1. 运行权限核心、配置、调度器、Agent 和 TUI 相关测试。
2. 修复因权限默认行为引入的旧测试失败。
3. 确认工具失败结果仍脱敏。
4. 确认普通聊天、读类多工具并发和 Plan Mode 旧行为仍通过。

**验证：** 运行 `python -m pytest tests/test_permissions.py tests/test_tool_scheduler.py tests/test_agent.py tests/test_config.py tests/test_tui_smoke.py -q`，期望通过。

## T24: 全量自动化测试

**文件：** 全项目测试  
**依赖：** T23  
**步骤：**
1. 运行全量 pytest。
2. 修复权限系统引入的兼容性问题。
3. 确认 OpenAI 和 Anthropic Provider 测试不需要理解权限系统仍可通过。

**验证：** 运行 `python -m pytest -q`，期望通过。

## T25: tmux 端到端验收准备

**文件：** `tests/e2e_mock_openai_server.py`、`README.md`  
**依赖：** T24  
**步骤：**
1. 使用 mock OpenAI server 启动可复现的权限场景。
2. 在临时项目配置中启用 `permissions.mode: default`。
3. 在 tmux 中启动 `mewcode`。
4. 输入危险命令场景请求，观察命令被拒绝且 Agent Loop 继续。
5. 输入写文件场景请求，观察 TUI 弹出权限确认。
6. 对照 `checklist.md` 记录端到端观察结果。

**验证：** 在 tmux 中运行 MewCode 并完成危险命令拒绝、写入确认和权限拒绝后继续三类场景，期望行为符合 `checklist.md`。

## 执行顺序

```text
T1
→ T2 → T3
→ T4 → T5 → T6 → T7
→ T8 → T9 → T10
→ T11
→ T12 → T13 → T14 → T15 → T16
→ T17 → T18 → T19
→ T20
→ T21 → T22
→ T23 → T24 → T25
```
