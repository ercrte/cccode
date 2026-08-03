# JulyCode Skill 系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/julycode/skills/__init__.py` | 导出 Skill 子系统公共类型、管理器和工具 |
| 新建 | `src/julycode/skills/models.py` | 定义 Skill frontmatter、目录、激活状态、报告和提示上下文 |
| 新建 | `src/julycode/skills/loader.py` | 解析 Markdown frontmatter、发现三层 Skill、处理优先级覆盖 |
| 新建 | `src/julycode/skills/manager.py` | 管理热更新、白名单校验、激活状态、专属工具和动态命令 |
| 新建 | `src/julycode/skills/tools.py` | 实现 `load_skill` 系统工具和目录型 Skill 脚本工具 |
| 新建 | `src/julycode/skills/commands.py` | 把可用 Skill 注册成斜杠短命令 |
| 新建 | `src/julycode/skills/execution.py` | 实现共享模式和独立模式执行调度、摘要回流 |
| 新建 | `src/julycode/skills/builtin/__init__.py` | 内置 Skill 包资源标记 |
| 新建 | `src/julycode/skills/builtin/commit.md` | 内置 commit Skill |
| 新建 | `src/julycode/skills/builtin/review.md` | 内置 review Skill |
| 新建 | `src/julycode/skills/builtin/test.md` | 内置 test Skill |
| 修改 | `src/julycode/tools/base.py` | `ToolSpec` 增加 `visibility` |
| 修改 | `src/julycode/tools/registry.py` | 支持动态 origin 注册、注销和工具名集合 |
| 修改 | `src/julycode/tools/scheduler.py` | 支持 Skill 白名单、系统工具例外和权限跳过 |
| 修改 | `src/julycode/prompting/base.py` | `RuntimePromptContext` 增加 Skill 上下文 |
| 修改 | `src/julycode/prompting/builder.py` | 注入可用 Skill 摘要和已激活完整 SOP |
| 修改 | `src/julycode/commands/models.py` | `AgentCommand` 和 `CommandContext` 增加 Skill 相关字段/方法 |
| 修改 | `src/julycode/commands/registry.py` | 支持动态命令按 origin 注销或重建 |
| 修改 | `src/julycode/commands/builtin.py` | `/clear` 清理激活 Skill，移除静态 `/review` |
| 修改 | `src/julycode/commands/__init__.py` | 导出新增 Skill 命令上下文相关类型 |
| 修改 | `src/julycode/agent.py` | 接入 SkillManager、模型覆盖、白名单策略和独立执行 |
| 修改 | `src/julycode/providers/factory.py` | 支持按 model override 创建 Provider |
| 修改 | `src/julycode/cli.py` | 创建 SkillManager、注册 `load_skill`、注入 TUI |
| 修改 | `src/julycode/tui/app.py` | 持有 SkillManager，刷新 Skill，展示警告，执行 Skill 命令 |
| 修改 | `pyproject.toml` | 打包内置 Skill Markdown |
| 修改 | `README.md` | 说明 Skill 格式、目录、加载、模式、白名单和内置样板 |
| 新建 | `tests/test_skills_loader.py` | 覆盖 Skill 解析、优先级、警告和目录型工具 |
| 新建 | `tests/test_skills_manager.py` | 覆盖热更新、激活、白名单、动态工具和命令 |
| 新建 | `tests/test_skills_tools.py` | 覆盖 `load_skill` 和专属脚本工具 |
| 新建 | `tests/test_skills_execution.py` | 覆盖共享/独立执行、历史携带、摘要回流和模型覆盖 |
| 修改 | `tests/test_prompting.py` | 覆盖 Skill 摘要和完整 SOP 注入 |
| 修改 | `tests/test_tools.py` | 覆盖 `ToolSpec.visibility` 和工具注册表扩展 |
| 修改 | `tests/test_tool_scheduler.py` | 覆盖 Skill 白名单、Plan Mode 和系统工具例外 |
| 修改 | `tests/test_commands.py` | 覆盖 Skill 命令、帮助、补全、冲突和 `/clear` 清理 |
| 修改 | `tests/test_agent.py` | 覆盖 Agent Loop 加载 Skill 后继续执行 |
| 修改 | `tests/test_tui_smoke.py` | 覆盖启动刷新、警告展示和 Skill 命令基础交互 |

## T1: 建立 Skill 包和数据模型

**文件：** `src/julycode/skills/__init__.py`, `src/julycode/skills/models.py`, `tests/test_skills_loader.py`  
**依赖：** 无  
**步骤：**
1. 新建 `src/julycode/skills/` 包和 `__init__.py`。
2. 在 `models.py` 中定义 `SkillSourceScope`、`SkillExecutionMode`、`SkillFrontmatter`、`SkillRoots`、`SkillSummary`、`SkillWarning`、`SkillError`、`SkillFingerprint`、`SkillDefinition`、`SkillToolDefinition`、`SkillCatalog`、`SkillActivation`、`SkillPromptContext`、`SkillRefreshReport`、`SkillExecutionSummary`。
3. 在 `__init__.py` 中导出这些公共模型。
4. 新建 `tests/test_skills_loader.py`，添加数据模型可导入和默认值断言。

**验证：** 运行 `python -m pytest tests/test_skills_loader.py -q -k "models or import"`，期望通过。

## T2: 实现 Skill frontmatter 解析

**文件：** `src/julycode/skills/loader.py`, `src/julycode/skills/__init__.py`, `tests/test_skills_loader.py`  
**依赖：** T1  
**步骤：**
1. 实现 Markdown frontmatter 分割，要求文件以 `---` 开头并存在结束分隔符。
2. 使用 `yaml.safe_load()` 解析 frontmatter，验证顶层为对象。
3. 校验 `name`、`description`、`tools`、`mode`、`history`、`model` 字段类型和取值。
4. 校验 Skill 名匹配 `[A-Za-z][A-Za-z0-9_-]*`，正文去除首尾空白后不能为空。
5. 实现 `{{input}}` 和 `{{args}}` 的参数渲染辅助函数。
6. 添加合法 Skill、缺字段、非法 YAML、非法名称、非法 mode、非法 history、空正文、占位符替换测试。

**验证：** 运行 `python -m pytest tests/test_skills_loader.py -q -k "frontmatter or placeholder or invalid"`，期望通过。

## T3: 实现三层发现和优先级覆盖

**文件：** `src/julycode/skills/loader.py`, `tests/test_skills_loader.py`  
**依赖：** T2  
**步骤：**
1. 实现 `SkillLoader.discover()`，扫描项目级、用户级和内置级 Skill 根目录。
2. 支持单文件 Skill：根目录下 `*.md`。
3. 支持目录型 Skill 入口：子目录中的 `skill.md`。
4. 对单个解析失败的 Skill 生成 `SkillWarning` 并继续加载其他 Skill。
5. 对同名 Skill 按项目级高于用户级高于内置级覆盖。
6. 生成 `SkillFingerprint`，包含参与扫描的 Markdown 和工具描述文件路径、mtime 和大小。
7. 添加项目覆盖用户、用户覆盖内置、非法文件跳过、fingerprint 变化测试。

**验证：** 运行 `python -m pytest tests/test_skills_loader.py -q -k "discover or priority or warning or fingerprint"`，期望通过。

## T4: 解析目录型 Skill 专属工具定义

**文件：** `src/julycode/skills/loader.py`, `tests/test_skills_loader.py`  
**依赖：** T3  
**步骤：**
1. 在目录型 Skill 中扫描 `tools/*.yaml`。
2. 校验工具 `name` 匹配 `[A-Za-z][A-Za-z0-9_]*`。
3. 校验 `description`、`safety`、`timeout_seconds`、`script`、`parameters` 字段。
4. 将脚本路径解析到 Skill 包目录内，拒绝跳出包目录的脚本路径。
5. 生成全局工具名 `skill_<skill_name>__<local_name>`。
6. 添加目录型 Skill 成功解析、非法工具描述跳过、脚本越界报 warning、全局名生成测试。

**验证：** 运行 `python -m pytest tests/test_skills_loader.py -q -k "directory or tool_definition or script"`，期望通过。

## T5: 扩展工具基础类型和注册表

**文件：** `src/julycode/tools/base.py`, `src/julycode/tools/registry.py`, `tests/test_tools.py`, `tests/test_skills_manager.py`  
**依赖：** T1  
**步骤：**
1. 在 `ToolSpec` 中增加 `visibility: ToolVisibility = "normal"`。
2. 定义 `ToolVisibility = Literal["normal", "system"]`。
3. 修改 `ToolRegistry.register()` 支持 `origin` 参数，默认 `static`。
4. 实现 `ToolRegistry.unregister_origin(origin)` 和 `ToolRegistry.names()`。
5. 确保现有内置工具默认 `visibility="normal"` 且现有测试仍兼容。
6. 添加按 origin 注销动态工具、重复工具名仍报错、工具名集合测试。

**验证：** 运行 `python -m pytest tests/test_tools.py tests/test_skills_manager.py -q -k "visibility or registry or origin"`，期望通过。

## T6: 实现 Skill 脚本工具

**文件：** `src/julycode/skills/tools.py`, `tests/test_skills_tools.py`  
**依赖：** T4, T5  
**步骤：**
1. 实现 `SkillScriptTool`，从 `SkillToolDefinition` 构造 `ToolSpec`。
2. 用 JSON stdin 调用本地 Python 脚本。
3. 要求 stdout 是 JSON 对象，并将其作为成功结果返回。
4. 将超时、非零退出码、stderr、非法 JSON 和非对象 JSON 转换为 `ToolExecutionError`。
5. 保持脚本路径必须在 Skill 包目录内。
6. 添加成功执行、参数传入、非法 JSON、非零退出码、超时和路径越界测试。

**验证：** 运行 `python -m pytest tests/test_skills_tools.py -q -k "script"`，期望通过。

## T7: 实现 load_skill 系统工具

**文件：** `src/julycode/skills/tools.py`, `tests/test_skills_tools.py`  
**依赖：** T6  
**步骤：**
1. 实现 `LoadSkillTool`，工具名为 `load_skill`。
2. 设置 `ToolSpec.visibility="system"` 和 `safety="side_effect"`。
3. 参数 schema 包含必填 `name` 和可选 `arguments`。
4. 执行时调用 `SkillManager.load()`，返回 Skill 名、执行模式、渲染 SOP、工具白名单、专属工具名、模型要求和来源路径。
5. 将未知 Skill 和加载失败转换为结构化工具失败。
6. 添加成功加载、参数替换、未知 Skill、系统工具 visibility 和描述文本测试。

**验证：** 运行 `python -m pytest tests/test_skills_tools.py -q -k "load_skill"`，期望通过。

## T8: 实现 SkillManager 激活和白名单校验

**文件：** `src/julycode/skills/manager.py`, `tests/test_skills_manager.py`  
**依赖：** T3, T4, T5, T6, T7  
**步骤：**
1. 实现 `SkillManager.refresh_if_changed()`，无变化时返回 `changed=False`。
2. 校验所有可用 Skill 白名单引用存在于当前工具、系统工具、MCP 工具或自身专属工具中。
3. 实现 `SkillManager.load()`，激活 Skill、渲染参数、注册对应专属工具。
4. 实现 `clear_active()`、`prompt_context()`、`active_tool_whitelist()`、`active_dedicated_tools()`。
5. 实现删除已激活 Skill 后刷新时清理激活状态并返回 warning。
6. 添加白名单成功、白名单缺失工具 fatal error、空白名单、多个 Skill 白名单并集、激活、清理、删除热更新测试。

**验证：** 运行 `python -m pytest tests/test_skills_manager.py -q -k "refresh or whitelist or active or clear"`，期望通过。

## T9: 实现模型覆盖解析

**文件：** `src/julycode/skills/manager.py`, `src/julycode/providers/factory.py`, `tests/test_skills_manager.py`, `tests/test_skills_execution.py`  
**依赖：** T8  
**步骤：**
1. 实现 `SkillManager.resolve_model_override(requested)`。
2. 当只有一个激活 Skill 指定模型时返回该模型。
3. 当命令级模型、激活 Skill 模型互相冲突时返回清晰 `SkillError`。
4. 修改 `create_provider(config, model_override=None)`，非空时用同一配置克隆 Provider 并替换模型名。
5. 添加无覆盖、单模型覆盖、命令覆盖、模型冲突、Provider model override 测试。

**验证：** 运行 `python -m pytest tests/test_skills_manager.py tests/test_skills_execution.py -q -k "model"`，期望通过。

## T10: 注册 Skill 斜杠命令

**文件：** `src/julycode/skills/commands.py`, `src/julycode/commands/registry.py`, `src/julycode/commands/__init__.py`, `tests/test_commands.py`, `tests/test_skills_manager.py`  
**依赖：** T8  
**步骤：**
1. 实现 `register_skill_commands(registry, manager)`。
2. 为 `CommandRegistry` 增加 `unregister_origin(origin)`，用于热更新时移除旧 Skill 命令。
3. Skill 命令名使用 Skill `name`，描述使用 Skill `description`，参数提示说明替换 `{{input}} / {{args}}`。
4. Skill 命令 handler 调用 `context.invoke_skill(name=skill_name, arguments=invocation.argument, visible_text=invocation.raw_text)`。
5. 检测 Skill 命令与内置命令或其他 Skill 命令冲突，并产生清晰错误。
6. 添加帮助展示、Tab 补全候选、命令触发、命令冲突、热更新删除命令测试。

**验证：** 运行 `python -m pytest tests/test_commands.py tests/test_skills_manager.py -q -k "skill_command or help or completion or conflict"`，期望通过。

## T11: 迁移内置 review 命令并调整 clear

**文件：** `src/julycode/commands/models.py`, `src/julycode/commands/builtin.py`, `tests/test_commands.py`  
**依赖：** T10  
**步骤：**
1. 在 `CommandContext` 中增加 `invoke_skill()`、`clear_active_skills()`、`skill_snapshot()`。
2. 从静态内置命令列表中移除 `/review` 和 `/rev`。
3. 修改 `/clear` handler，在清空界面消息后调用 `clear_active_skills()`。
4. 调整内置命令数量测试，确认 `/review` 由 Skill 命令提供。
5. 添加 `/clear` 清理激活 Skill 测试。

**验证：** 运行 `python -m pytest tests/test_commands.py -q -k "clear or review or builtin"`，期望通过。

## T12: 在运行时提示中注入 Skill 上下文

**文件：** `src/julycode/prompting/base.py`, `src/julycode/prompting/builder.py`, `tests/test_prompting.py`  
**依赖：** T8  
**步骤：**
1. 在 `RuntimePromptContext` 中增加 `skill_context` 字段。
2. 在 `<julycode_runtime_context>` 内注入可用 Skill 摘要，只包含名字和一句说明。
3. 在同一运行时块内注入已激活 Skill 的名字、说明、执行模式、参数、工具白名单、来源和完整 SOP。
4. 确保 Skill 信息位于项目指令、长期记忆和上下文摘要之前。
5. 添加只有摘要不含正文、激活后包含完整 SOP、多个激活 Skill 同时出现、警告可见、排序稳定测试。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q -k "skill"`，期望通过。

## T13: 更新工具策略和调度器

**文件：** `src/julycode/tools/scheduler.py`, `tests/test_tool_scheduler.py`  
**依赖：** T5, T7, T8  
**步骤：**
1. 扩展 `ToolPolicy(mode, skill_tools=None)`。
2. `allowed_specs()` 始终保留 `visibility="system"` 工具。
3. Plan Mode 下普通工具仍先过滤为只读工具。
4. 有 Skill 白名单时，将普通工具与白名单取交集。
5. `validate_call()` 对系统工具跳过 Plan Mode 禁止和 Skill 白名单禁止。
6. 调度器对系统工具串行执行，但跳过权限确认。
7. 添加系统工具始终可见、Plan Mode 保留系统工具、白名单收窄、多个工具过滤、系统工具跳过权限、普通工具仍受权限测试。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py -q -k "skill or system or policy or permission"`，期望通过。

## T14: AgentCommand 和 Agent Loop 接入 Skill

**文件：** `src/julycode/commands/models.py`, `src/julycode/agent.py`, `tests/test_agent.py`  
**依赖：** T8, T9, T12, T13  
**步骤：**
1. 给 `AgentCommand` 增加 `model_override` 和 `skill_name` 可选字段。
2. `AgentLoopRunner` 构造函数增加可选 `skill_manager`、`provider_resolver`、`skill_executor`。
3. 每轮请求前调用 `skill_manager.refresh_if_changed()`。
4. 用 `skill_manager.active_tool_whitelist()` 构造 `ToolPolicy`。
5. 将 `skill_manager.prompt_context()` 放入 `RuntimePromptContext`。
6. 通过 `resolve_model_override()` 选择本轮 Provider。
7. 添加普通请求含 Skill 摘要、调用 `load_skill` 后下一轮包含完整 SOP、白名单影响请求工具、模型覆盖被使用测试。

**验证：** 运行 `python -m pytest tests/test_agent.py -q -k "skill or load_skill or model_override"`，期望通过。

## T15: 实现共享模式 Skill 执行

**文件：** `src/julycode/skills/execution.py`, `src/julycode/tui/app.py`, `tests/test_skills_execution.py`, `tests/test_tui_smoke.py`  
**依赖：** T10, T14  
**步骤：**
1. 实现 `SkillExecutor.invoke_from_command()` 的共享模式分支。
2. 共享模式加载 Skill 后构造 `AgentCommand`，`visible_text` 保留原斜杠命令，`model_text` 表达执行该 Skill 的目标。
3. 在 `JulyCodeApp` 中实现 `invoke_skill()`，调用 SkillExecutor。
4. 确认共享模式执行会走主 `send_prompt()`，用户可见触发信息进入主界面。
5. 添加 `/review src/julycode` 触发共享 Skill、参数替换、主历史保留用户消息和最终助手消息测试。

**验证：** 运行 `python -m pytest tests/test_skills_execution.py tests/test_tui_smoke.py -q -k "shared or invoke_skill"`，期望通过。

## T16: 实现独立模式 Skill 执行

**文件：** `src/julycode/skills/execution.py`, `src/julycode/agent.py`, `tests/test_skills_execution.py`, `tests/test_agent.py`  
**依赖：** T14, T15  
**步骤：**
1. 实现 `SkillExecutor.run_isolated()`，创建临时 `ChatSession`。
2. 按 `history` 复制主会话最近 N 条消息；`history=0` 时不复制。
3. 在临时会话中运行独立 Agent Loop，复用工具注册表、权限控制、上下文配置和 ProviderResolver。
4. 根据最终助手消息、工具结果数量、失败状态和停止原因生成 `SkillExecutionSummary`。
5. 把摘要作为 assistant 消息追加到主会话，中间消息不进入主会话。
6. 在 Agent Loop 中识别 `load_skill` 激活的独立模式 Skill，并调度独立执行后结束主轮次。
7. 添加历史携带数量、无历史、主历史不含中间工具消息、摘要回流、独立执行失败摘要测试。

**验证：** 运行 `python -m pytest tests/test_skills_execution.py tests/test_agent.py -q -k "isolated or summary or history"`，期望通过。

## T17: TUI 启动、热更新和错误展示接入

**文件：** `src/julycode/cli.py`, `src/julycode/tui/app.py`, `tests/test_tui_smoke.py`, `tests/test_skills_manager.py`  
**依赖：** T8, T10, T13, T15  
**步骤：**
1. CLI 创建 `SkillManager` 并注册 `LoadSkillTool` 到默认工具注册表。
2. `JulyCodeApp` 构造函数接收并保存 `skill_manager`。
3. TUI mount 初始化 MCP 后调用 `skill_manager.refresh_if_changed(registry, command_registry)`。
4. 启动刷新出现 warning 时在界面展示中文警告。
5. 启动刷新出现 fatal error 时展示错误并禁用输入。
6. 每次用户提交前和每轮 Agent 请求前做轻量热更新。
7. 添加 MCP 后白名单校验、启动 warning 展示、fatal 禁用输入、提交前热更新测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py tests/test_skills_manager.py -q -k "startup or refresh or warning or fatal or mcp"`，期望通过。

## T18: 添加内置 commit、review、test Skill

**文件：** `src/julycode/skills/builtin/__init__.py`, `src/julycode/skills/builtin/commit.md`, `src/julycode/skills/builtin/review.md`, `src/julycode/skills/builtin/test.md`, `pyproject.toml`, `tests/test_skills_loader.py`, `tests/test_commands.py`  
**依赖：** T3, T10, T11, T13  
**步骤：**
1. 新建内置 Skill 包资源目录。
2. 编写 `commit.md`，包含合法 frontmatter 和中文 SOP。
3. 编写 `review.md`，迁移原 `/review` 审查行为到 Skill SOP。
4. 编写 `test.md`，包含测试执行和失败分析 SOP。
5. 更新 `pyproject.toml`，确保内置 Markdown 被打包。
6. 添加默认发现三个内置 Skill、`/review` 由 Skill 命令注册、`/help` 和补全能看到三个 Skill 的测试。

**验证：** 运行 `python -m pytest tests/test_skills_loader.py tests/test_commands.py -q -k "builtin or commit or review or test"`，期望通过。

## T19: 更新 README Skill 文档

**文件：** `README.md`  
**依赖：** T18  
**步骤：**
1. 增加 Skill 系统说明，解释两阶段加载。
2. 记录项目级、用户级、内置级目录和覆盖优先级。
3. 写出单文件 Skill frontmatter 示例。
4. 写出目录型 Skill 结构和专属工具 YAML 示例。
5. 说明共享模式、独立模式、历史携带、模型覆盖、工具白名单和 `load_skill` 系统工具。
6. 说明 Skill 会注册成斜杠命令，`/clear` 会清除已激活 Skill。
7. 列出内置 commit、review、test 样板。
8. 明确本阶段不做市场分发和版本管理。

**验证：** 运行 `rg -n "Skill|load_skill|frontmatter|commit|review|test|市场|版本" README.md`，期望能看到新增说明和范围边界。

## T20: Skill 子系统专项回归

**文件：** `tests/test_skills_loader.py`, `tests/test_skills_manager.py`, `tests/test_skills_tools.py`, `tests/test_skills_execution.py`  
**依赖：** T1-T19  
**步骤：**
1. 运行 Skill loader、manager、tools、execution 测试。
2. 修复解析、热更新、白名单、专属工具、共享执行或独立执行失败。
3. 确认每个测试文件都覆盖至少一个失败路径。

**验证：** 运行 `python -m pytest tests/test_skills_loader.py tests/test_skills_manager.py tests/test_skills_tools.py tests/test_skills_execution.py -q`，期望全部通过。

## T21: 集成回归

**文件：** `tests/test_prompting.py`, `tests/test_tool_scheduler.py`, `tests/test_commands.py`, `tests/test_agent.py`, `tests/test_tui_smoke.py`, `tests/test_tools.py`  
**依赖：** T20  
**步骤：**
1. 运行提示、工具策略、命令、Agent、TUI 和工具注册相关测试。
2. 修复 Skill 接入导致的普通聊天、Plan Mode、权限、命令补全或状态栏回归。
3. 确认 `load_skill` 系统工具不会破坏现有内置工具和 MCP 工具行为。

**验证：** 运行 `python -m pytest tests/test_prompting.py tests/test_tool_scheduler.py tests/test_commands.py tests/test_agent.py tests/test_tui_smoke.py tests/test_tools.py -q`，期望全部通过。

## T22: 项目全量自动化测试

**文件：** `src/julycode/`, `tests/`  
**依赖：** T21  
**步骤：**
1. 运行项目全量测试。
2. 修复命令、上下文管理、权限、记忆、MCP 或 Provider 的回归。
3. 如果失败来自不可控外部环境，记录具体命令、错误信息和影响范围。

**验证：** 运行 `python -m pytest -q`，期望全部通过，或记录不可控环境失败的具体错误。

## T23: tmux 端到端验证

**文件：** `src/julycode/`, `tests/e2e_mock_openai_server.py`, `README.md`  
**依赖：** T22  
**步骤：**
1. 在 tmux 中启动 JulyCode，使用测试配置或 mock OpenAI server。
2. 输入 `/help`，确认 commit、review、test 三个 Skill 命令可见。
3. 输入 `/review README.md`，确认触发 Skill，界面显示用户命令，模型请求带完整 SOP。
4. 输入一个需要按需加载 Skill 的普通请求，确认模型能调用 `load_skill`，后续请求包含完整 SOP。
5. 输入 `/clear` 后再发普通请求，确认此前激活 Skill 不再出现在运行时上下文。
6. 对照后续 `checklist.md` 逐项记录通过或失败证据。

**验证：** 运行 `tmux capture-pane -pt julycode-skill-e2e`，期望能看到 `/help`、`/review README.md`、`load_skill` 调用或对应工具状态、`/clear` 后的可继续对话记录。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18 → T19 → T20 → T21 → T22 → T23
```
