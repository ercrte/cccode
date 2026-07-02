# MewCode 权限系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] 权限系统基础模型可导入，默认权限模式为 `default`，规则、决策、提示和事件载荷字段完整（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_models_export_defaults -q`，期望通过）
- [ ] 高危命令黑名单能拒绝根目录/家目录递归删除、`sudo rm`、格式化磁盘、裸设备写入、关机重启、fork bomb、全局权限破坏、`kill -9 -1` 和 `git clean -fdx`，普通命令不误拦（验证：运行 `python -m pytest tests/test_permissions.py::test_dangerous_command_guard_blocks_high_risk_commands tests/test_permissions.py::test_dangerous_command_guard_allows_safe_commands -q`，期望通过）
- [ ] 高危命令命中后不启动命令，并返回 `permission_dangerous_command` 失败结果；即使命中 allow 规则也仍被拒绝（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_engine_dangerous_command_overrides_allow_rule tests/test_agent.py::test_runner_does_not_execute_dangerous_command -q`，期望通过）
- [ ] 项目路径沙箱允许项目内普通路径，并拒绝 `..`、绝对路径和符号链接逃逸（验证：运行 `python -m pytest tests/test_permissions.py::test_project_sandbox_allows_inside_path tests/test_permissions.py::test_project_sandbox_rejects_parent_escape tests/test_permissions.py::test_project_sandbox_rejects_absolute_escape tests/test_permissions.py::test_project_sandbox_rejects_symlink_escape -q`，期望通过）
- [ ] 内置文件类工具的 `read_file`、`write_file`、`edit_file`、`search_code` 和 `find_files` 在执行前经过沙箱检查，项目外目标不会被读取或修改（验证：运行 `python -m pytest tests/test_permissions.py::test_project_sandbox_builds_subject_for_core_tools tests/test_permissions.py::test_project_sandbox_rejects_find_files_escape tests/test_permissions.py::test_project_sandbox_rejects_search_path_escape tests/test_permissions.py::test_permission_engine_sandbox_overrides_allow_rule -q`，期望通过）
- [ ] 规则解析支持 `工具名(模式)`、精确匹配、glob 匹配和 `Bash(...)` 到 `run_command(...)` 的别名归一化，非法规则会报错（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_rule_parser_parses_exact_and_glob tests/test_permissions.py::test_permission_rule_parser_normalizes_bash_alias tests/test_permissions.py::test_permission_rule_parser_rejects_invalid_rules -q`，期望通过）
- [ ] 同一来源内规则匹配遵守精确优先于 glob，匹配能力相同时 deny 优先于 allow（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_rule_set_prefers_exact_match tests/test_permissions.py::test_permission_rule_set_prefers_deny_on_equal_match tests/test_permissions.py::test_permission_rule_set_matches_any_subject_target -q`，期望通过）
- [ ] 用户级、项目级、本地级和会话级规则按 `session > local > project > user` 生效，低优先级规则不能覆盖高优先级规则（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_rule_store_orders_sources tests/test_permissions.py::test_permission_engine_uses_highest_priority_rule_source -q`，期望通过）
- [ ] 规则文件缺失时按空规则处理，格式错误时给出清晰 `ConfigError`，不会以半解析状态继续运行（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_rule_store_loads_missing_files_as_empty tests/test_permissions.py::test_permission_rule_store_rejects_invalid_yaml -q`，期望通过）
- [ ] 默认模式下明确 allow 自动执行，明确 deny 自动拒绝，未命中的读类工具允许，未命中的有副作用工具进入确认（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_engine_default_mode_decisions -q`，期望通过）
- [ ] 严格模式下有副作用工具即使命中 allow 规则也进入确认，读类工具仍按规则和默认读类策略处理（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_engine_strict_mode_prompts_side_effect_even_when_allowed -q`，期望通过）
- [ ] 放行模式下未命中的非硬拒绝工具自动允许，但显式 deny、高危命令和沙箱拒绝仍生效（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_engine_permissive_mode_allows_unmatched_but_respects_deny tests/test_permissions.py::test_permission_engine_dangerous_command_overrides_allow_rule tests/test_permissions.py::test_permission_engine_sandbox_overrides_allow_rule -q`，期望通过）
- [ ] 权限控制器能把权限拒绝转成失败 `ToolResult`，失败结果包含可读原因且不泄露敏感值（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_controller_turns_denial_into_tool_result tests/test_config.py::test_redact_secret_masks_exact_secret -q`，期望通过）
- [ ] 用户选择本次允许只影响当前调用，不写入规则；选择拒绝会返回 `permission_user_denied` 失败结果（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_controller_resolves_allow_once tests/test_tool_scheduler.py::test_scheduler_permission_prompt_deny_returns_failure -q`，期望通过）
- [ ] 用户选择本会话允许会创建会话级 allow 规则，当前运行期后续相同模式自动允许（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_controller_adds_session_rule tests/test_tui_smoke.py::test_tui_allow_session_reuses_permission -q`，期望通过）
- [ ] 用户选择永久允许会写入本地级规则文件，后续运行可按该规则自动允许（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_controller_persists_local_rule tests/test_tui_smoke.py::test_tui_allow_permanent_writes_local_rule -q`，期望通过）
- [ ] 没有交互 prompter 时，需要确认的调用会得到 `permission_confirmation_required` 拒绝，不会永久等待（验证：运行 `python -m pytest tests/test_permissions.py::test_permission_controller_denies_when_prompt_has_no_prompter -q`，期望通过）
- [ ] `permissions.mode` 支持 `strict`、`default`、`permissive` 三档，缺省为 `default`，非法模式会报配置错误（验证：运行 `python -m pytest tests/test_config.py::test_loads_permissions_mode tests/test_config.py::test_rejects_invalid_permissions_mode tests/test_config.py::test_loads_required_yaml_fields -q`，期望通过）

## 集成
- [ ] 所有工具调用在真实执行前都会先经过 `ToolPolicy` 和权限控制器；权限 allow 才执行真实工具，权限 deny 不执行真实工具（验证：运行 `python -m pytest tests/test_tool_scheduler.py::test_scheduler_permission_allow_executes_tool tests/test_tool_scheduler.py::test_scheduler_permission_deny_skips_tool -q`，期望通过）
- [ ] 权限确认会产出 `permission_requested` 和 `permission_resolved` 事件，事件顺序可被测试观察（验证：运行 `python -m pytest tests/test_tool_scheduler.py::test_scheduler_permission_prompt_allow_executes_after_events tests/test_tool_scheduler.py::test_scheduler_permission_prompt_deny_returns_failure -q`，期望通过）
- [ ] Plan Mode 的只读限制先于权限系统生效，即使放行模式或 allow 规则存在，有副作用工具仍被 `tool_not_allowed` 拒绝（验证：运行 `python -m pytest tests/test_tool_scheduler.py::test_scheduler_plan_mode_blocks_side_effect_before_permission tests/test_agent.py::test_plan_mode_blocks_side_effect_tools -q`，期望通过）
- [ ] 权限拒绝、用户拒绝、沙箱拒绝和高危命令拒绝都作为工具失败结果回灌给模型，未触发既有停止条件时 Agent Loop 继续下一轮（验证：运行 `python -m pytest tests/test_agent.py::test_runner_feeds_permission_denial_back_to_model tests/test_agent.py::test_runner_does_not_execute_dangerous_command -q`，期望通过）
- [ ] TUI 权限确认视图展示工具名、关键参数摘要、触发原因以及本次允许、本会话允许、永久允许和拒绝四个选择（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_permission_prompt_view_renders_choices tests/test_tui_smoke.py::test_permission_prompt_view_sets_choice -q`，期望通过）
- [ ] TUI 中用户选择本次允许后工具继续执行，选择拒绝后工具不执行、显示权限失败且输入区恢复（验证：运行 `python -m pytest tests/test_tui_smoke.py::test_tui_permission_allow_once_continues_tool tests/test_tui_smoke.py::test_tui_permission_deny_returns_failure_and_recovers_input -q`，期望通过）
- [ ] 权限等待、允许和拒绝状态能通过 TUI 行为或 `TurnEvent` 被观察到，且不会导致输入区永久不可用（验证：运行 `python -m pytest tests/test_tool_scheduler.py::test_scheduler_permission_prompt_allow_executes_after_events tests/test_tui_smoke.py::test_tui_permission_deny_returns_failure_and_recovers_input -q`，期望通过）
- [ ] CLI 启动时加载权限配置、创建权限控制器并注入 TUI，配置错误仍走脱敏错误路径（验证：运行 `python -m pytest tests/test_config.py::test_cli_reports_config_error_without_secret tests/test_tui_smoke.py::test_cli_entrypoint_is_importable -q`，期望通过）
- [ ] OpenAI 和 Anthropic Provider 不需要理解权限系统，权限拒绝以普通工具失败结果进入统一会话和协议转换路径（验证：运行 `python -m pytest tests/test_openai_provider.py tests/test_anthropic_provider.py tests/test_agent.py::test_runner_feeds_permission_denial_back_to_model -q`，期望通过）
- [ ] README 说明权限模式、规则文件位置、`Bash(git *)` 示例、硬拒绝和路径沙箱不可绕过，并保留网络限制、资源配额和审计日志为后续范围（验证：运行 `rg -n "权限系统|permissions.mode|Bash\\(git \\*\\)|\\.mewcode.permissions|网络请求限制|资源配额|审计日志" README.md`，期望命中相关说明）

## 编译与测试
- [ ] 权限核心测试全部通过（验证：运行 `python -m pytest tests/test_permissions.py -q`，期望通过）
- [ ] 权限、调度器、Agent、配置和 TUI 分层测试全部通过（验证：运行 `python -m pytest tests/test_permissions.py tests/test_tool_scheduler.py tests/test_agent.py tests/test_config.py tests/test_tui_smoke.py -q`，期望通过）
- [ ] 全部自动化测试通过，Provider、会话、工具和提示构建旧行为不回归（验证：运行 `python -m pytest -q`，期望通过）
- [ ] Python 文件无语法错误（验证：运行 `python -m compileall src tests`，期望无编译错误）
- [ ] 命令入口仍可导入（验证：运行 `python -c "from mewcode.cli import main; print(callable(main))"`，期望输出 `True`）
- [ ] 项目未配置 lint 命令时记录为不适用；如后续配置 lint，则 lint 检查通过（验证：查看 `pyproject.toml` 是否有 lint 配置；若有则运行对应命令，期望退出码为 0）

## 端到端场景
- [ ] 场景 1：危险命令硬拒绝且 Agent Loop 继续（验证：在 tmux 中启动 `python tests/e2e_mock_openai_server.py 18765`，配置 MewCode 指向该服务并启用 `permissions.mode: default`，启动 `mewcode`，输入触发危险命令的请求，观察 `run_command` 没有启动、工具结果为权限拒绝，模型随后给出安全替代回复）
- [ ] 场景 2：高危命令不可被规则放开（验证：写入本地规则 `Bash(rm -rf /): allow` 后在 tmux 中触发同一危险命令，观察仍返回高危命令拒绝，命令未执行）
- [ ] 场景 3：路径沙箱阻止项目外读取和写入（验证：在项目内创建指向项目外临时文件的符号链接，tmux 中请求读取或修改该链接，观察权限拒绝且项目外文件内容不变）
- [ ] 场景 4：默认模式下未命中的写入工具进入人审（验证：tmux 中输入让模型创建文件的请求，观察 TUI 出现权限确认视图，包含工具名、参数摘要、触发原因和四个选择）
- [ ] 场景 5：本次允许只放行当前调用（验证：在场景 4 选择本次允许，观察当前文件写入成功；再次触发相同写入请求，观察仍然再次弹出确认）
- [ ] 场景 6：本会话允许在当前运行期复用（验证：触发写入确认并选择本会话允许，随后再次触发相同模式写入，观察不再弹确认且工具直接执行；重启后再次触发会重新按持久规则以外的配置判断）
- [ ] 场景 7：永久允许写入本地规则并跨运行保留（验证：触发写入确认并选择永久允许，观察 `.mewcode.permissions.local.yaml` 出现 allow 规则；重启 MewCode 后再次触发相同模式，观察按规则自动允许）
- [ ] 场景 8：用户拒绝后工具不执行且模型能调整（验证：触发写入确认并选择拒绝，观察工具状态显示 `permission_user_denied`，目标文件不存在或未改变，模型收到失败结果后给出替代说明，输入区恢复可用）
- [ ] 场景 9：规则优先级符合 `session > local > project > user`（验证：分别设置用户级 allow、项目级 deny、本地级 allow，并在当前会话选择拒绝或会话 deny，触发同一工具调用，观察实际结果符合最高优先级来源）
- [ ] 场景 10：严格模式下 allow 规则仍需要确认（验证：配置 `permissions.mode: strict` 并写入某有副作用工具 allow 规则，tmux 中触发该工具，观察仍出现确认视图）
- [ ] 场景 11：放行模式仍尊重显式 deny、黑名单和沙箱（验证：配置 `permissions.mode: permissive`，分别触发未命中安全命令、显式 deny 命令、危险命令和项目外路径访问，观察只有未命中安全命令自动执行，其余均拒绝）
- [ ] 场景 12：Plan Mode 不被权限规则或放行模式绕过（验证：配置 `permissions.mode: permissive` 并写入 `Bash(*)` 或 `write_file(*)` allow 规则，tmux 中输入 `/plan <需求>` 并让 mock 返回写入或命令工具，观察工具未执行且模型收到 `tool_not_allowed` 失败结果）
- [ ] 场景 13：规则文件格式错误阻止启动并给出清晰错误（验证：写入非法 `.mewcode.permissions.local.yaml` 后启动 `mewcode`，观察启动失败并显示规则配置错误；修复文件后可正常启动）
- [ ] 场景 14：权限系统不影响普通读类工具和普通聊天（验证：tmux 中输入普通聊天和读取项目内 README 的请求，观察普通聊天仍流式输出，项目内读类工具可执行并返回最终回复）
