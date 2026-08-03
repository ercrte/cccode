# JulyCode 命令系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 删除 | `src/julycode/commands.py` | 移除旧的硬编码斜杠命令解析单文件 |
| 新建 | `src/julycode/commands/__init__.py` | 重导出命令系统公共类型和工厂函数，保持 `julycode.commands` 导入入口 |
| 新建 | `src/julycode/commands/models.py` | 定义命令元数据、解析结果、上下文协议、状态快照和 Agent 模式 |
| 新建 | `src/julycode/commands/registry.py` | 实现命令注册、冲突检测、大小写不敏感查找、解析和补全 |
| 新建 | `src/julycode/commands/dispatcher.py` | 实现输入分流和命令处理调度 |
| 新建 | `src/julycode/commands/builtin.py` | 实现十个内置命令的元数据和 handler |
| 修改 | `src/julycode/agent.py` | 使用新的命令模型，移除旧 `do` 命令入口语义和 pending plan 副作用 |
| 修改 | `src/julycode/tools/scheduler.py` | 将工具策略收敛为默认模式和计划模式 |
| 修改 | `src/julycode/prompting/modules.py` | 更新稳定提示中的模式说明 |
| 修改 | `src/julycode/prompting/builder.py` | 更新运行时提示，只处理默认模式和计划模式 |
| 修改 | `src/julycode/tui/app.py` | 接入命令注册中心、分发器、持久模式、状态快照和命令上下文 |
| 修改 | `src/julycode/tui/widgets.py` | 增加状态栏模式标记和命令补全菜单 |
| 修改 | `src/julycode/cli.py` | 启动阶段创建命令注册中心，命令冲突时中文报错退出 |
| 修改 | `tests/test_commands.py` | 覆盖命令注册、解析、补全、分发和内置命令 handler |
| 修改 | `tests/test_agent.py` | 更新 Agent 模式行为，删除旧 `/do` 执行待计划断言 |
| 修改 | `tests/test_tool_scheduler.py` | 更新工具策略测试，移除旧 `do` 模式覆盖 |
| 修改 | `tests/test_prompting.py` | 更新提示词模式测试，移除旧执行模式和待计划描述 |
| 修改 | `tests/test_tui_smoke.py` | 覆盖 TUI 命令分流、模式状态栏、补全和本地命令行为 |
| 修改 | `README.md` | 更新用户可见斜杠命令说明 |

## T1: 建立命令包骨架

**文件：** `src/julycode/commands.py`, `src/julycode/commands/__init__.py`, `src/julycode/commands/models.py`  
**依赖：** 无  
**步骤：**
1. 删除旧 `src/julycode/commands.py`。
2. 新建 `src/julycode/commands/` 包。
3. 在 `models.py` 中定义 `AgentMode = Literal["normal", "plan"]`、`AgentCommand`、命令元数据、解析结果、补全结果、状态快照和 `CommandContext` 协议。
4. 在 `__init__.py` 中重导出 `AgentCommand`、`AgentMode` 和命令模型公共类型。

**验证：** 运行 `python -m py_compile src/julycode/commands/__init__.py src/julycode/commands/models.py`，期望无错误。

## T2: 实现注册中心

**文件：** `src/julycode/commands/registry.py`, `src/julycode/commands/__init__.py`, `tests/test_commands.py`  
**依赖：** T1  
**步骤：**
1. 实现 `CommandRegistry`、`CommandRegistryError` 和命令入口规范化逻辑。
2. 实现注册时名称和别名冲突检测，冲突信息包含冲突入口和涉及命令。
3. 实现 `get()`、`parse()`、`visible_commands()` 和 `completion()`。
4. 在 `tests/test_commands.py` 中加入注册、冲突、大小写解析、未知命令、普通输入、隐藏命令补全过滤和单/多匹配补全测试。

**验证：** 运行 `python -m pytest tests/test_commands.py -q -k "registry or parse or completion"`，期望通过。

## T3: 实现命令分发器

**文件：** `src/julycode/commands/dispatcher.py`, `src/julycode/commands/__init__.py`, `tests/test_commands.py`  
**依赖：** T2  
**步骤：**
1. 实现 `CommandDispatcher.dispatch()`。
2. 对空输入返回已消费且不调用 context。
3. 对普通输入返回未消费。
4. 对未知斜杠命令调用 `show_assistant()` 展示 `/help` 引导。
5. 对已知命令调用对应 handler，并在异常时调用 `show_error()`。
6. 用 fake `CommandContext` 覆盖分发器消费结果、未知命令提示、handler 调用和失败恢复。

**验证：** 运行 `python -m pytest tests/test_commands.py -q -k "dispatcher or unknown"`，期望通过。

## T4: 登记十个内置命令和帮助命令

**文件：** `src/julycode/commands/builtin.py`, `src/julycode/commands/__init__.py`, `tests/test_commands.py`  
**依赖：** T3  
**步骤：**
1. 实现 `create_builtin_command_registry()` 并登记 `/help`、`/compact`、`/clear`、`/plan`、`/do`、`/session`、`/memory`、`/permission`、`/status`、`/review`。
2. 为每条命令填写名称、别名、描述、用法、类型和参数提示。
3. 实现 `/help` 无参数时展示全部非隐藏命令。
4. 实现 `/help <命令>` 按主名称或别名展示单个命令详情。
5. 补充内置命令数量、元数据完整性、帮助列表和单命令帮助测试。

**验证：** 运行 `python -m pytest tests/test_commands.py -q -k "builtin or help"`，期望通过。

## T5: 实现本地状态查询命令

**文件：** `src/julycode/commands/builtin.py`, `tests/test_commands.py`  
**依赖：** T4  
**步骤：**
1. 实现 `/session`，展示会话标识、恢复状态、消息数量和当前模式。
2. 实现 `/memory`，展示记忆启用、用户级索引、项目级索引、自动笔记和告警数量。
3. 实现 `/permission`，展示权限模式和各层规则数量，不展示规则全文。
4. 实现 `/status`，展示供应商、模型、当前模式、任务运行状态、最近 Token 用量或未知状态、MCP 告警概况。
5. 用 fake context 覆盖四个命令的中文输出和敏感规则不泄露行为。

**验证：** 运行 `python -m pytest tests/test_commands.py -q -k "session or memory or permission or status"`，期望通过。

## T6: 实现界面状态命令和预设提示词命令

**文件：** `src/julycode/commands/builtin.py`, `tests/test_commands.py`  
**依赖：** T5  
**步骤：**
1. 实现 `/plan`，调用 `set_mode("plan")`、`refresh_status()` 并展示进入计划模式提示。
2. 实现 `/do`，调用 `set_mode("normal")`、`refresh_status()` 并展示回到默认模式提示。
3. 实现 `/clear`，调用 `clear_messages()` 并展示会话上下文仍保留的提示。
4. 实现 `/compact`，调用 `compact_context()` 并展示压缩结果。
5. 实现 `/review`，构造固定中文代码审查请求，参数作为审查范围或补充要求传入 `send_prompt()`。
6. 用 fake context 覆盖这些命令不触发普通用户消息、模式切换、压缩调用和 review prompt 内容。

**验证：** 运行 `python -m pytest tests/test_commands.py -q -k "plan or do or clear or compact or review"`，期望通过。

## T7: 更新 Agent 模式语义

**文件：** `src/julycode/agent.py`, `src/julycode/tools/scheduler.py`, `tests/test_agent.py`, `tests/test_tool_scheduler.py`  
**依赖：** T1  
**步骤：**
1. 将 Agent 相关类型和测试中的模式范围收敛为 `normal` 和 `plan`。
2. 移除 `AgentLoopRunner` 完成 `plan` 后保存 `PendingPlan` 的行为。
3. 移除 `AgentLoopRunner` 完成 `do` 后清除 `PendingPlan` 的行为。
4. 更新 `ToolPolicy`，默认模式允许全部工具，计划模式只允许读类工具。
5. 更新旧 `plan` 保存计划、旧 `do` 执行计划相关测试为新语义测试。

**验证：** 运行 `python -m pytest tests/test_agent.py tests/test_tool_scheduler.py -q -k "plan_mode or tool_policy or pending_plan"`，期望通过。

## T8: 更新提示词模式说明

**文件：** `src/julycode/prompting/modules.py`, `src/julycode/prompting/builder.py`, `tests/test_prompting.py`  
**依赖：** T7  
**步骤：**
1. 将稳定提示中的模式说明改为默认模式和计划模式。
2. 删除运行时提示里的旧执行模式和待执行计划段落。
3. 保持计划模式只读约束说明。
4. 更新提示词测试，确认 `normal` 和 `plan` 运行时上下文仍正确，旧 `do` 断言被移除。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q -k "mode or runtime_prompt or stable_modules"`，期望通过。

## T9: 接入 TUI 状态栏和命令上下文快照

**文件：** `src/julycode/tui/widgets.py`, `src/julycode/tui/app.py`, `tests/test_tui_smoke.py`  
**依赖：** T5  
**步骤：**
1. 在 `StatusBar` 增加持久模式字段和 `set_mode()`。
2. 在状态栏文本中显示 `[DEFAULT]` 或 `[PLAN]`。
3. 在 `JulyCodeApp` 中保存 `current_mode` 和 `last_usage`。
4. 实现 `/session`、`/memory`、`/permission`、`/status` 所需的 snapshot 方法。
5. 在 usage 事件处理时更新 `last_usage`。
6. 补充 TUI 状态栏模式标记和状态快照 smoke 测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -q -k "status_bar or snapshot or status"`，期望通过。

## T10: 接入 TUI 命令分流

**文件：** `src/julycode/tui/app.py`, `tests/test_tui_smoke.py`  
**依赖：** T6, T9  
**步骤：**
1. 在 `JulyCodeApp` 初始化命令注册中心和 `CommandDispatcher`。
2. 将回车入口改为先调用分发器；普通输入才启动 Agent Loop。
3. 实现 `show_assistant()`、`show_error()`、`clear_messages()`、`compact_context()` 和 `send_prompt()`。
4. 重构现有 `_run_generation()`，让普通输入和 `/review` 都能复用同一 Agent Loop 执行路径。
5. 更新 `/compact` smoke 测试，确认仍触发手动压缩且不请求模型。
6. 增加未知命令、`/help`、`/plan`、`/do`、普通输入模式分流和 `/review` 可见请求测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -q -k "command or compact or plan or review or unknown"`，期望通过。

## T11: 实现 TUI 命令补全菜单

**文件：** `src/julycode/tui/widgets.py`, `src/julycode/tui/app.py`, `tests/test_tui_smoke.py`  
**依赖：** T10  
**步骤：**
1. 新增 `CommandCompletionMenu`，用于展示多匹配候选。
2. 在 TUI 中绑定 Tab 到命令补全 action。
3. 当前输入是斜杠命令前缀且只有一个非隐藏匹配时，直接替换输入为规范命令。
4. 当前输入存在多个非隐藏匹配时，显示候选菜单。
5. 输入提交或输入不再是命令前缀时隐藏候选菜单。
6. 增加单匹配补全、多匹配菜单和隐藏命令不出现的 smoke 测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -q -k "completion or tab"`，期望通过。

## T12: 接入 CLI 启动冲突错误

**文件：** `src/julycode/cli.py`, `tests/test_commands.py`  
**依赖：** T10  
**步骤：**
1. 在 CLI 启动阶段调用 `create_builtin_command_registry()`。
2. 将 registry 注入 `JulyCodeApp`。
3. 捕获 `CommandRegistryError`，打印中文配置错误并返回 1。
4. 增加 CLI 或 registry 工厂冲突错误测试，确认错误信息包含冲突入口。

**验证：** 运行 `python -m pytest tests/test_commands.py -q -k "cli or conflict"`，期望通过。

## T13: 更新用户文档

**文件：** `README.md`  
**依赖：** T10  
**步骤：**
1. 新增或更新斜杠命令说明，列出十个内置命令。
2. 将 `/plan` 和 `/do` 文档改为持久模式切换。
3. 保留 `/compact` 手动上下文压缩说明，并说明该命令不会进入普通 Agent 任务。
4. 简要说明 Tab 补全和未知命令 `/help` 引导。

**验证：** 运行 `rg -n "/help|/plan|/do|/review|\\[DEFAULT\\]|\\[PLAN\\]" README.md`，期望能看到对应说明。

## T14: 全量单元回归

**文件：** `tests/test_commands.py`, `tests/test_agent.py`, `tests/test_tool_scheduler.py`, `tests/test_prompting.py`, `tests/test_tui_smoke.py`  
**依赖：** T1-T13  
**步骤：**
1. 运行命令系统、Agent、工具策略、提示词和 TUI smoke 相关测试。
2. 修复因新命令语义导致的失败。
3. 确认旧带参数计划命令和旧 `/do` 执行计划语义不再通过命令入口触发。

**验证：** 运行 `python -m pytest tests/test_commands.py tests/test_agent.py tests/test_tool_scheduler.py tests/test_prompting.py tests/test_tui_smoke.py -q`，期望全部通过。

## T15: 项目全量测试

**文件：** `src/julycode/`, `tests/`  
**依赖：** T14  
**步骤：**
1. 运行项目全量测试。
2. 修复命令包重构引起的导入、上下文压缩、权限、记忆、MCP 或 Provider 回归。
3. 确认失败只来自环境外部依赖时记录原因，不伪造通过。

**验证：** 运行 `python -m pytest -q`，期望全部通过，或记录不可控环境失败的具体错误。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15
```
