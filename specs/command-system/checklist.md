# JulyCode 命令系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] 命令系统包已替换旧硬编码解析入口，公共导入 `julycode.commands` 可用（验证：运行 `python -m py_compile src/julycode/commands/__init__.py src/julycode/commands/models.py src/julycode/commands/registry.py src/julycode/commands/dispatcher.py src/julycode/commands/builtin.py`，期望无错误）
- [ ] 十个内置命令均已登记且元数据完整：`/help`、`/compact`、`/clear`、`/plan`、`/do`、`/session`、`/memory`、`/permission`、`/status`、`/review`（验证：运行 `python -m pytest tests/test_commands.py -q -k "builtin or help"`，期望通过）
- [ ] 命令注册中心能在启动前发现命令名和别名冲突，并报告冲突入口和涉及命令（验证：运行 `python -m pytest tests/test_commands.py -q -k "conflict"`，期望通过）
- [ ] 命令处理函数只通过抽象上下文完成显示、模式切换、压缩、状态查询和发送预设请求（验证：运行 `python -m pytest tests/test_commands.py -q -k "dispatcher or session or memory or permission or status or review"`，期望 fake context 测试通过）
- [ ] TUI 已实现当前模式、最近 Token 用量、会话、记忆、权限和 MCP 告警的命令快照（验证：运行 `python -m pytest tests/test_tui_smoke.py -q -k "snapshot or status"`，期望通过）
- [ ] 状态栏能展示持久模式标记 `[DEFAULT]` 和 `[PLAN]`（验证：运行 `python -m pytest tests/test_tui_smoke.py -q -k "status_bar or plan"`，期望通过）

## 命令解析与分发
- [ ] 空白输入不会新增消息、调用命令或发起模型请求（验证：运行 `python -m pytest tests/test_tui_smoke.py -q -k "empty"`，期望通过；或在 TUI 输入空格后按 Enter，观察消息区不变）
- [ ] 斜杠输入按第一个空格分割命令名和参数，命令名大小写不敏感，参数保留用户语义（验证：运行 `python -m pytest tests/test_commands.py -q -k "parse"`，期望 `/HELP`、`/help`、`/help   status` 用例通过）
- [ ] 未知斜杠命令显示包含 `/help` 的中文引导，且不会启动 Agent Loop（验证：运行 `python -m pytest tests/test_tui_smoke.py -q -k "unknown"`，期望 provider 未收到请求）
- [ ] 普通非斜杠输入绕过命令分发并按当前持久模式进入 Agent Loop（验证：运行 `python -m pytest tests/test_tui_smoke.py -q -k "plain or command"`，期望 provider 收到请求且模式正确）
- [ ] 命令执行异常时界面显示中文错误，输入区恢复可用，失败命令不追加为普通用户消息（验证：运行 `python -m pytest tests/test_commands.py -q -k "failure or dispatcher"`，期望通过）

## 内置命令行为
- [ ] `/help` 展示全部十个非隐藏内置命令的名称、别名、描述、用法和参数提示（验证：运行 `python -m pytest tests/test_commands.py -q -k "help"`，期望帮助列表包含十个命令）
- [ ] `/help review` 只展示 `/review` 的详细帮助和用法（验证：运行 `python -m pytest tests/test_commands.py -q -k "help"`，期望单命令帮助不混入其他命令详情）
- [ ] `/compact` 触发手动上下文压缩，显示压缩结果或无需压缩原因，且不作为普通用户消息进入 AI 对话（验证：运行 `python -m pytest tests/test_tui_smoke.py -q -k "compact"`，期望 manual_compact 被调用且 provider 请求数为 0）
- [ ] `/clear` 清空当前界面消息显示区，并提示会话上下文仍保留（验证：运行 `python -m pytest tests/test_tui_smoke.py -q -k "clear"`，期望消息区被清空后出现保留上下文提示）
- [ ] `/session` 展示会话标识、恢复状态、消息数量和当前模式（验证：运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py -q -k "session"`，期望输出包含这些字段）
- [ ] `/memory` 展示记忆启用状态、用户级记忆索引、项目级记忆索引、自动笔记状态和告警概况（验证：运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py -q -k "memory"`，期望输出包含这些字段）
- [ ] `/permission` 展示权限模式、会话临时规则概况和权限确认说明，且不修改权限规则或泄露规则全文（验证：运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py -q -k "permission"`，期望规则数量可见、规则全文不可见）
- [ ] `/status` 展示供应商、模型、当前模式、任务运行状态、最近 Token 用量或未知状态、MCP 工具加载告警概况（验证：运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py -q -k "status"`，期望输出包含这些字段）
- [ ] `/review` 和 `/review <范围>` 构造确定的代码审查请求并送入当前对话交给 AI，界面可见触发的审查请求（验证：运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py -q -k "review"`，期望 model_text 包含审查要求和参数范围）

## 模式与补全
- [ ] `/plan` 进入计划模式，状态栏切换为 `[PLAN]`，界面显示已进入计划模式，且不会发起模型请求（验证：运行 `python -m pytest tests/test_tui_smoke.py -q -k "plan"`，期望 provider 请求数不增加）
- [ ] `[PLAN]` 模式下的普通输入以计划模式进入 AI 对话，只允许读类工具（验证：运行 `python -m pytest tests/test_tui_smoke.py tests/test_tool_scheduler.py -q -k "plan_mode or tool_policy"`，期望计划模式请求和只读工具策略通过）
- [ ] `/do` 回到默认模式，状态栏切换为 `[DEFAULT]`，界面显示已回到默认模式，且不会执行旧待执行计划（验证：运行 `python -m pytest tests/test_tui_smoke.py tests/test_agent.py -q -k "do or pending_plan"`，期望 `/do` 不触发 provider 且旧 pending plan 语义测试通过）
- [ ] `[DEFAULT]` 模式下的普通任务以默认执行模式进入 AI 对话，并保留完整工具能力（验证：运行 `python -m pytest tests/test_tui_smoke.py tests/test_tool_scheduler.py -q -k "default or normal or tool_policy"`，期望默认模式请求和工具策略通过）
- [ ] 主名称和别名调用同一命令时行为一致（验证：运行 `python -m pytest tests/test_commands.py -q -k "alias"`，期望 `/h`、`/?`、`/rev` 等别名用例通过）
- [ ] Tab 补全在单个非隐藏匹配时直接补全命令（验证：运行 `python -m pytest tests/test_tui_smoke.py -q -k "completion or tab"`，期望 `/sta` 补全为 `/status`）
- [ ] Tab 补全在多个非隐藏匹配时显示可选菜单，隐藏命令不出现在候选中（验证：运行 `python -m pytest tests/test_tui_smoke.py tests/test_commands.py -q -k "completion or hidden"`，期望菜单只含可见命令）

## 集成与回归
- [ ] Agent Loop 不再因计划模式完成而保存待执行计划，也不再通过旧 `do` 模式清除待执行计划（验证：运行 `python -m pytest tests/test_agent.py -q -k "pending_plan or plan_mode"`，期望新语义测试通过）
- [ ] 提示词只描述默认模式和计划模式，不再描述旧执行模式或待执行计划（验证：运行 `python -m pytest tests/test_prompting.py -q -k "mode or runtime_prompt or stable_modules"`，期望通过）
- [ ] 普通聊天、流式输出、工具调用、工具失败、权限确认、上下文压缩、MCP 初始化、会话恢复和长期记忆仍可正常工作（验证：运行 `python -m pytest tests/test_tui_smoke.py tests/test_agent.py tests/test_context_manager.py tests/test_permissions.py tests/test_mcp_manager.py tests/test_memory_updater.py -q`，期望通过）
- [ ] README 已说明十个内置命令、持久模式标记、Tab 补全和未知命令 `/help` 引导（验证：运行 `rg -n "/help|/compact|/clear|/plan|/do|/session|/memory|/permission|/status|/review|\\[DEFAULT\\]|\\[PLAN\\]" README.md`，期望都有匹配）

## 编译与测试
- [ ] 命令系统相关文件可编译（验证：运行 `python -m py_compile src/julycode/commands/__init__.py src/julycode/commands/models.py src/julycode/commands/registry.py src/julycode/commands/dispatcher.py src/julycode/commands/builtin.py`，期望无错误）
- [ ] 命令、Agent、工具策略、提示词和 TUI smoke 测试通过（验证：运行 `python -m pytest tests/test_commands.py tests/test_agent.py tests/test_tool_scheduler.py tests/test_prompting.py tests/test_tui_smoke.py -q`，期望全部通过）
- [ ] 项目全量测试通过（验证：运行 `python -m pytest -q`，期望全部通过；如存在外部环境失败，记录具体错误和影响范围）
- [ ] 当前项目没有配置独立 lint 工具时，不额外要求 lint；如后续新增 lint 配置，应纳入本项（验证：运行 `rg -n "\\[tool\\.(ruff|mypy|black|isort)|ruff|mypy|black|isort" pyproject.toml`，期望确认是否存在 lint 配置）

## 端到端场景
- [ ] 场景 1：在 tmux 中启动 JulyCode，输入 `/help`，看到十个内置命令及中文说明，输入区保持可用（验证：运行 `tmux new-session -d -s julycode-command-test 'julycode'` 后向 pane 发送 `/help` 和 Enter，观察 pane 输出）
- [ ] 场景 2：在 tmux 中输入 `/plan` 后看到状态栏 `[PLAN]`，再输入一个真实规划请求，观察 JulyCode 以计划模式回复且不会执行写入或命令工具（验证：同一 tmux 会话中发送 `/plan`、Enter、`请阅读 README 并规划如何改进命令帮助`、Enter，观察状态栏和工具行为）
- [ ] 场景 3：在 tmux 中输入 `/do` 后看到状态栏 `[DEFAULT]`，再输入一个普通对话请求，观察请求按默认模式交给 AI（验证：同一 tmux 会话中发送 `/do`、Enter、`查看当前项目状态并说明下一步`、Enter，观察状态栏和回复）
- [ ] 场景 4：在 tmux 中输入 `/status`、`/session`、`/memory`、`/permission`，每条都快速返回本地状态且不触发模型生成（验证：观察无工具调用和无流式模型生成，只显示本地状态）
- [ ] 场景 5：在 tmux 中输入 `/review README.md`，界面显示触发的审查请求，JulyCode 调用 AI 进行代码审查流程（验证：观察用户可见请求、AI 回复和必要工具调用）
- [ ] 场景 6：在 tmux 中输入未知命令 `/wat`，看到 `/help` 引导；输入 `/cl` 后按 Tab 能补全或显示候选菜单，输入仍可继续编辑（验证：观察提示和补全菜单，不遮挡输入）
