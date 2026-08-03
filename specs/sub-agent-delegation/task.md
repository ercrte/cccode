# JulyCode 子 Agent 委派 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/julycode/subagents/__init__.py` | 导出子 Agent 公共类型和工具 |
| 新建 | `src/julycode/subagents/models.py` | 子 Agent 配置、角色、委派、后台任务、结果和提示上下文数据结构 |
| 新建 | `src/julycode/subagents/loader.py` | Markdown + YAML frontmatter 角色加载、覆盖和校验 |
| 新建 | `src/julycode/subagents/cache.py` | 独立文件读取缓存 |
| 新建 | `src/julycode/subagents/tools.py` | `delegate_agent` 稳定工具 |
| 新建 | `src/julycode/subagents/runtime.py` | 子 Agent Runner 创建、隔离上下文和结果聚合 |
| 新建 | `src/julycode/subagents/manager.py` | 子 Agent 委派、后台任务、通知和生命周期管理 |
| 新建 | `src/julycode/subagents/builtin/__init__.py` | 内置角色包 |
| 新建 | `src/julycode/subagents/builtin/code-searcher.md` | 内置代码搜索角色 |
| 新建 | `src/julycode/subagents/builtin/reviewer.md` | 内置审查角色 |
| 修改 | `src/julycode/config.py` | 解析 `sub_agents` 配置 |
| 修改 | `src/julycode/tools/base.py` | `ToolContext` 增加可选读取缓存 |
| 修改 | `src/julycode/tools/builtin.py` | `read_file` 接入读取缓存 |
| 修改 | `src/julycode/tools/scheduler.py` | `ToolPolicy` 支持子 Agent 多层工具过滤 |
| 修改 | `src/julycode/prompting/base.py` | 运行时提示上下文增加子 Agent 字段 |
| 修改 | `src/julycode/prompting/builder.py` | 注入角色摘要、后台摘要和子 Agent 运行提示 |
| 修改 | `src/julycode/agent.py` | 父上下文绑定、子 Agent 工具过滤和独立缓存传递 |
| 修改 | `src/julycode/tui/app.py` | 创建管理器、注册工具、刷新角色、显示通知、手动切后台 |
| 修改 | `src/julycode/commands/models.py` | 命令上下文增加子 Agent 快照和后台动作 |
| 修改 | `src/julycode/commands/builtin.py` | `/status`、`/agents`、`/background` 支持子 Agent |
| 修改 | `src/julycode/skills/execution.py` | 提供独立 Skill 复用子 Agent 隔离运行的适配 |
| 修改 | `src/julycode/cli.py` | CLI 启动路径注入子 Agent 管理器 |
| 修改 | `pyproject.toml` | 打包内置角色 Markdown |
| 修改 | `README.md` | 记录角色定义格式、配置和使用方式 |
| 新建 | `tests/test_subagents_loader.py` | 角色加载、覆盖和解析测试 |
| 新建 | `tests/test_subagents_tools.py` | 委派工具参数校验和工具结果测试 |
| 新建 | `tests/test_subagents_policy.py` | 子 Agent 工具过滤和防嵌套测试 |
| 新建 | `tests/test_subagents_manager.py` | 前台、后台、Fork、通知和取消测试 |
| 修改 | `tests/test_config.py` | `sub_agents` 配置解析测试 |
| 修改 | `tests/test_tools.py` | 文件读取缓存测试 |
| 修改 | `tests/test_tool_scheduler.py` | `ToolPolicy` 回归测试 |
| 修改 | `tests/test_prompting.py` | 子 Agent 提示注入测试 |
| 修改 | `tests/test_agent.py` | Runner 父上下文绑定、隔离和事件回归测试 |
| 修改 | `tests/test_commands.py` | `/status`、`/agents`、`/background` 命令测试 |
| 修改 | `tests/test_tui_smoke.py` | TUI 注册、通知、命令和手动后台测试 |
| 修改 | `tests/test_skills.py` | 独立 Skill 隔离执行回归测试 |
| 修改 | `tests/e2e_mock_openai_server.py` | 增加委派工具调用脚本化响应 |

## T1: 创建子 Agent 数据模型和包骨架

**文件：** `src/julycode/subagents/__init__.py`、`src/julycode/subagents/models.py`、`src/julycode/subagents/builtin/__init__.py`、`src/julycode/subagents/builtin/code-searcher.md`、`src/julycode/subagents/builtin/reviewer.md`、`pyproject.toml`
**依赖：** 无
**步骤：**
1. 新建 `subagents` 包和内置角色包。
2. 在 `models.py` 定义 `SubAgentConfig`、角色、委派、工具过滤、结果、后台记录、提示上下文和刷新报告 dataclass。
3. 编写两个内置角色 Markdown，frontmatter 只允许读类核心工具。
4. 更新 `pyproject.toml`，把 `julycode.subagents.builtin` 的 `*.md` 纳入包数据。
5. 在 `__init__.py` 导出稳定公共类型。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q`，期望现有提示测试仍通过；运行 `python -c "from julycode.subagents import SubAgentConfig; print(SubAgentConfig().foreground_timeout_seconds)"`，期望输出默认阈值。

## T2: 实现角色加载和覆盖

**文件：** `src/julycode/subagents/loader.py`、`tests/test_subagents_loader.py`
**依赖：** T1
**步骤：**
1. 实现 `default_sub_agent_roots()` 和 `SubAgentRoleLoader.discover()`。
2. 支持项目、用户、内置、插件来源，按项目高于用户高于内置高于插件覆盖。
3. 解析 Markdown frontmatter，校验必填字段、模型档位、权限模式、工具白黑名单和正文非空。
4. 对同层重复、非法 YAML、缺字段和不可读文件生成 warning。
5. 编写加载、覆盖、同层重复、非法 frontmatter 和内置角色发现测试。

**验证：** 运行 `python -m pytest tests/test_subagents_loader.py -q`，期望全部通过。

## T3: 接入 `sub_agents` 配置解析

**文件：** `src/julycode/config.py`、`tests/test_config.py`
**依赖：** T1
**步骤：**
1. 把 `SubAgentConfig` 挂到 `AppConfig`。
2. 实现 `_parse_sub_agents()`，解析启用开关、前台超时、后台任务上限、全局禁用工具、后台白名单、模型别名和插件角色根目录。
3. 校验数字字段为正数，工具列表和路径列表为字符串数组。
4. 在主配置解析中调用 `_parse_sub_agents()`。
5. 增加默认值、完整配置和非法配置测试。

**验证：** 运行 `python -m pytest tests/test_config.py -q`，期望全部通过。

## T4: 实现独立文件读取缓存

**文件：** `src/julycode/subagents/cache.py`、`src/julycode/tools/base.py`、`src/julycode/tools/builtin.py`、`tests/test_tools.py`
**依赖：** T1
**步骤：**
1. 实现 `FileReadCache`，用真实路径、mtime 和 size 判断缓存是否有效。
2. 给 `ToolContext` 增加 `read_cache` 可选字段，保持现有调用方无需改动。
3. 修改 `ReadFileTool`，缓存命中时复用内容，缓存失效时重新读取并写入缓存。
4. 增加缓存命中、文件变更失效、不同缓存实例互不影响测试。

**验证：** 运行 `python -m pytest tests/test_tools.py -q`，期望全部通过。

## T5: 扩展工具过滤策略

**文件：** `src/julycode/tools/scheduler.py`、`tests/test_subagents_policy.py`、`tests/test_tool_scheduler.py`
**依赖：** T1
**步骤：**
1. 扩展 `ToolPolicy`，增加 `filter` 参数并保持默认行为兼容。
2. 在 `allowed_specs()` 中按 Plan Mode、Skill 白名单、继承集合、角色白名单、角色黑名单、全局禁止、后台白名单和防嵌套顺序过滤工具。
3. 在 `validate_call()` 中为每类过滤失败返回结构化 `ToolResult`。
4. 编写定义式、Fork、后台白名单和 `delegate_agent` 防嵌套测试。
5. 保持现有调度、权限和 Plan Mode 测试通过。

**验证：** 运行 `python -m pytest tests/test_subagents_policy.py tests/test_tool_scheduler.py -q`，期望全部通过。

## T6: 渲染子 Agent 提示上下文

**文件：** `src/julycode/prompting/base.py`、`src/julycode/prompting/builder.py`、`tests/test_prompting.py`
**依赖：** T1
**步骤：**
1. 给 `RuntimePromptContext` 增加 `sub_agent_context` 字段。
2. 在主 Agent 提示中渲染可用角色摘要、角色加载 warning 和后台任务摘要。
3. 在子 Agent 提示中渲染当前角色正文、任务目标、非交互跑到底约束和 Fork 约束。
4. 确保子 Agent 提示位于普通会话历史之外，且不会替代 Skill 提示。
5. 增加主提示、定义式子提示和 Fork 子提示测试。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q`，期望全部通过。

## T7: 实现 `delegate_agent` 工具

**文件：** `src/julycode/subagents/tools.py`、`tests/test_subagents_tools.py`
**依赖：** T1、T3
**步骤：**
1. 定义 `DELEGATE_AGENT_TOOL_NAME = "delegate_agent"`。
2. 实现稳定 `ToolSpec`，参数包含 `type`、`task`、`role`、`background`、`max_iterations`、`foreground_timeout_seconds`。
3. 解析参数为 `SubAgentInvocation`，校验 `defined` 必须传 `role`、`fork` 强制后台。
4. 调用 `SubAgentManager.delegate()` 并把前台结果或后台启动记录转成工具返回数据。
5. 编写 schema 稳定、参数缺失、defined、fork 强制后台和 manager 错误测试。

**验证：** 运行 `python -m pytest tests/test_subagents_tools.py -q`，期望全部通过。

## T8: 实现子 Agent 运行工厂的定义式路径

**文件：** `src/julycode/subagents/runtime.py`、`tests/test_subagents_manager.py`
**依赖：** T2、T3、T4、T5、T6
**步骤：**
1. 实现 `SubAgentRunnerFactory.create_runner()` 的定义式分支。
2. 为定义式创建空白 `ChatSession`，只加入子任务用户消息和角色提示上下文。
3. 为子 Agent 创建独立 `ContextManager`、`PermissionController`、`FileReadCache` 和 Hook 运行时状态。
4. 根据角色解析模型覆盖、最大轮次和权限模式。
5. 增加定义式不携带父历史、持续注入角色正文、权限控制器隔离测试。

**验证：** 运行 `python -m pytest tests/test_subagents_manager.py -k defined -q`，期望相关测试通过。

## T9: 实现 Fork 路径和父历史快照

**文件：** `src/julycode/subagents/runtime.py`、`tests/test_subagents_manager.py`
**依赖：** T8
**步骤：**
1. 实现 Fork 分支，复制父 `ChatSession.messages` 的安全快照。
2. 避免复制未完成工具调用或破坏父 Agent 当前循环状态。
3. 继承父 Agent 当前可见工具集合，并叠加全局禁止、后台白名单和防嵌套过滤。
4. 强制 `SubAgentInvocation.background=True`，记录强制后台原因。
5. 增加 Fork 能看到父历史、不会写回主历史、工具集合不超过父集合、强制后台测试。

**验证：** 运行 `python -m pytest tests/test_subagents_manager.py -k fork -q`，期望相关测试通过。

## T10: 聚合子 Agent 结果和用量

**文件：** `src/julycode/subagents/runtime.py`、`tests/test_subagents_manager.py`
**依赖：** T8
**步骤：**
1. 实现 `run_sub_agent_to_result()`，消费子 Runner 事件直到完成、失败或取消。
2. 从 `message_done`、`stopped`、`error` 和 `usage` 事件聚合状态、停止原因、最终文本、错误和用量。
3. 生成 `SubAgentResult.summary`、`key_outputs` 和 `final_text`。
4. 保证工具失败先回灌给子 Agent，只有 Runner 停止时才结束子任务。
5. 增加正常完成、迭代上限、模型错误、工具限制失败和用量聚合测试。

**验证：** 运行 `python -m pytest tests/test_subagents_manager.py -k result -q`，期望相关测试通过。

## T11: 实现子 Agent 管理器前台委派

**文件：** `src/julycode/subagents/manager.py`、`tests/test_subagents_manager.py`
**依赖：** T7、T10
**步骤：**
1. 实现 `SubAgentManager.refresh_if_changed()`、`prompt_context()`、`bind_parent_context()`。
2. 实现前台定义式委派：创建任务记录、启动子任务、等待完成并返回 `SubAgentResult`。
3. 处理角色不存在、父上下文缺失、后台任务数量超限等失败。
4. 确保前台完成只返回工具结果，不自动追加后台通知。
5. 增加前台成功、角色缺失、父上下文缺失、任务上限测试。

**验证：** 运行 `python -m pytest tests/test_subagents_manager.py -k foreground -q`，期望相关测试通过。

## T12: 实现显式后台和完成通知

**文件：** `src/julycode/subagents/manager.py`、`tests/test_subagents_manager.py`
**依赖：** T11
**步骤：**
1. 实现显式后台委派，立即返回后台任务启动记录。
2. 后台任务完成时更新 `BackgroundSubAgentRecord` 的状态、结果、错误、结束时间和用量。
3. 生成中文完成通知，追加到主 `ChatSession`。
4. 调用可选 notify 回调，让 TUI 能立即显示通知。
5. 增加后台立即返回、完成记录、主会话通知、notify 回调和通知失败不崩溃测试。

**验证：** 运行 `python -m pytest tests/test_subagents_manager.py -k background -q`，期望相关测试通过。

## T13: 实现超时自动后台和手动切后台

**文件：** `src/julycode/subagents/manager.py`、`tests/test_subagents_manager.py`
**依赖：** T12
**步骤：**
1. 前台等待子任务时同时等待完成、超时和 `force_background` 事件。
2. 超过 `foreground_timeout_seconds` 后把任务切到后台，并返回已转后台结果。
3. 实现 `background_current_foreground()`，让用户命令触发当前前台子任务切后台。
4. 确保转后台后的任务继续运行并按后台通知规则回流。
5. 增加超时后台、手动后台、无前台任务时手动后台失败测试。

**验证：** 运行 `python -m pytest tests/test_subagents_manager.py -k \"timeout or manual\" -q`，期望相关测试通过。

## T14: 实现取消策略和生命周期关闭

**文件：** `src/julycode/subagents/manager.py`、`src/julycode/subagents/runtime.py`、`tests/test_subagents_manager.py`
**依赖：** T13
**步骤：**
1. 实现取消仍在前台等待的子任务。
2. 保留已后台化任务，避免主任务取消时误杀后台子任务。
3. 实现 `SubAgentManager.close()`，应用关闭时取消未完成后台任务。
4. 确保取消结果包含明确停止原因。
5. 增加前台取消、后台保留、close 取消后台任务测试。

**验证：** 运行 `python -m pytest tests/test_subagents_manager.py -k cancel -q`，期望相关测试通过。

## T15: 集成 AgentLoopRunner

**文件：** `src/julycode/agent.py`、`tests/test_agent.py`
**依赖：** T5、T6、T11
**步骤：**
1. 扩展 `AgentLoopRunner.__init__()`，接收 `sub_agent_manager`、`tool_filter`、`sub_agent_prompt`、`file_read_cache`。
2. 构建请求时把 `sub_agent_context` 传给 `RuntimePromptContext`。
3. 计算 `allowed_tools` 后绑定 `ParentAgentContext`，工具执行完成后清除。
4. 创建 `ToolPolicy` 时传入 `tool_filter`。
5. 创建 `ToolExecutor` 或 `ToolContext` 时传入当前 Runner 的独立读取缓存。
6. 增加主 Runner 角色摘要注入、父上下文绑定、子 Runner 防嵌套和现有 Agent Loop 回归测试。

**验证：** 运行 `python -m pytest tests/test_agent.py -q`，期望全部通过。

## T16: 接入 TUI 应用生命周期

**文件：** `src/julycode/tui/app.py`、`tests/test_tui_smoke.py`
**依赖：** T7、T11、T15
**步骤：**
1. 在 `JulyCodeApp` 初始化 `SubAgentManager`，允许测试注入。
2. 注册 `DelegateAgentTool`，避免重复注册。
3. `on_mount` 和用户输入前刷新子 Agent 角色。
4. 把 `sub_agent_manager` 传入主 `AgentLoopRunner`。
5. 实现后台通知回调，向主消息列表追加可见消息。
6. `on_unmount` 调用 `SubAgentManager.close()`。
7. 增加工具注册、刷新错误展示、后台完成通知和关闭清理测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -k sub_agent -q`，期望相关测试通过。

## T17: 增加 `/agents` 和 `/background` 命令

**文件：** `src/julycode/commands/models.py`、`src/julycode/commands/builtin.py`、`src/julycode/tui/app.py`、`tests/test_commands.py`、`tests/test_tui_smoke.py`
**依赖：** T13、T16
**步骤：**
1. 定义 `CommandSubAgentSnapshot` 和 `CommandContext` 协议方法。
2. `/status` 输出子 Agent 可用角色数、运行后台数、已完成数和告警数。
3. 新增 `/agents` 显示可用角色和后台任务详情。
4. 新增 `/background` 调用当前前台子 Agent 手动切后台。
5. 在 TUI 上下文实现快照和手动后台方法。
6. 增加命令格式、无任务切后台、有任务切后台和 status 展示测试。

**验证：** 运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py -k \"agents or background or status\" -q`，期望相关测试通过。

## T18: 迁移独立 Skill 执行隔离

**文件：** `src/julycode/skills/execution.py`、`src/julycode/tui/app.py`、`tests/test_skills.py`、`tests/test_tui_smoke.py`
**依赖：** T8、T10、T16
**步骤：**
1. 增加内部适配器，让 isolated Skill 使用子 Agent 隔离运行基础设施。
2. 保持用户可见行为为“独立 Skill 执行摘要”。
3. 确保 Skill 中间消息、工具调用和权限状态不污染主会话。
4. 保留 shared Skill 的现有执行路径。
5. 增加 isolated Skill 历史携带量、主历史不污染、shared Skill 回归测试。

**验证：** 运行 `python -m pytest tests/test_skills.py tests/test_tui_smoke.py -k skill -q`，期望相关测试通过。

## T19: 接入 CLI 启动路径和文档

**文件：** `src/julycode/cli.py`、`README.md`
**依赖：** T16、T17
**步骤：**
1. 在 CLI 创建和传递 `SubAgentManager` 所需依赖。
2. 确保非 TUI 直接构造路径仍能注册 `delegate_agent`。
3. 在 README 增加角色定义目录、frontmatter 字段、加载优先级、委派方式、后台行为和配置示例。
4. 记录本阶段不做 Worktree 隔离和跨会话后台持久化。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_app_can_mount_and_show_initial_state -q`，期望通过；运行 `python -m pytest tests/test_config.py -q`，期望通过。

## T20: 增加端到端 mock 场景

**文件：** `tests/e2e_mock_openai_server.py`、`tests/test_tui_smoke.py`
**依赖：** T16、T17
**步骤：**
1. 在 mock OpenAI server 中增加会调用 `delegate_agent` 的脚本化响应。
2. 覆盖定义式前台子任务、Fork 后台子任务和后台完成通知。
3. 在 TUI smoke 测试中通过假 provider 或 mock server 验证用户请求触发委派后，主对话只收到摘要和通知。
4. 确认子 Agent 中间工具结果不显示为主对话普通历史。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -k delegate -q`，期望相关测试通过。

## T21: 全量回归

**文件：** 全项目
**依赖：** T1-T20
**步骤：**
1. 运行全量 pytest。
2. 修复因新配置、工具过滤、提示上下文或 TUI 生命周期引起的回归。
3. 复查 README 和内置角色文案是否为中文且不含过期字段名。
4. 确认 `delegate_agent` 在主 Agent 工具列表中稳定出现，在子 Agent 工具列表中不可用。

**验证：** 运行 `python -m pytest -q`，期望全部通过。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18 → T19 → T20 → T21
```
