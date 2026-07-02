# MewCode Skill 系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] Skill 文件格式可用：合法 YAML frontmatter + Markdown 正文能被发现，启动摘要只包含 Skill 名字和一句说明，不包含完整正文（验证：运行 `python -m pytest tests/test_skills_loader.py tests/test_prompting.py -q -k "frontmatter or summary"`，期望通过；覆盖 AC1）
- [ ] 非法 Skill 不阻断整体：缺字段、非法 YAML、不可读正文或非法目录型工具会被跳过并产生中文 warning，其他合法 Skill 仍可用（验证：运行 `python -m pytest tests/test_skills_loader.py -q -k "invalid or warning"`，期望通过；覆盖 AC2）
- [ ] 三层优先级正确：项目级同名 Skill 覆盖用户级和内置级，删除项目级后热更新能回退到低优先级版本（验证：运行 `python -m pytest tests/test_skills_loader.py tests/test_skills_manager.py -q -k "priority or fallback"`，期望通过；覆盖 AC3）
- [ ] 系统级加载能力可用：`load_skill` 能按名称和参数加载 Skill，并在后续请求中持续注入完整 SOP 和参数（验证：运行 `python -m pytest tests/test_skills_tools.py tests/test_agent.py tests/test_prompting.py -q -k "load_skill or active"`，期望通过；覆盖 AC4）
- [ ] 多 Skill 可同时激活：后续运行时上下文能同时呈现多个 Skill 的名字、参数和完整 SOP，且边界清晰（验证：运行 `python -m pytest tests/test_prompting.py -q -k "multiple or active"`，期望通过；覆盖 AC5）
- [ ] 共享模式保留主历史：共享 Skill 执行后，用户触发信息、助手回复、工具调用和工具结果进入主会话历史（验证：运行 `python -m pytest tests/test_skills_execution.py tests/test_agent.py -q -k "shared"`，期望通过；覆盖 AC6）
- [ ] 独立模式隔离中间过程：独立 Skill 只向主历史回流触发信息和摘要，中间工具消息不进入主历史（验证：运行 `python -m pytest tests/test_skills_execution.py -q -k "isolated or summary"`，期望通过；覆盖 AC7）
- [ ] 独立模式历史携带量正确：`history=N` 只携带最近 N 条主历史，`history=0` 不携带主历史（验证：运行 `python -m pytest tests/test_skills_execution.py -q -k "history"`，期望通过；覆盖 AC8）
- [ ] 模型覆盖行为正确：Skill 指定可用模型时使用该模型，指定模型不可用或模型要求冲突时显示中文错误且不静默降级（验证：运行 `python -m pytest tests/test_skills_manager.py tests/test_skills_execution.py -q -k "model"`，期望通过；覆盖 AC9）
- [ ] 工具白名单能收窄工具：只声明读取和搜索工具的 Skill 激活后，写入、编辑和命令工具不会暴露给模型，但 `load_skill` 仍可见（验证：运行 `python -m pytest tests/test_tool_scheduler.py tests/test_agent.py -q -k "whitelist or system"`，期望通过；覆盖 AC10）
- [ ] 多 Skill 白名单组合正确：多个激活 Skill 的白名单取并集后再与基础工具集合、Plan Mode 策略相交（验证：运行 `python -m pytest tests/test_skills_manager.py tests/test_tool_scheduler.py -q -k "whitelist or plan"`，期望通过；覆盖 AC11）
- [ ] 白名单引用不存在工具会失败：启动或热更新时能指出具体 Skill 名和工具名，并阻止进入可误用状态（验证：运行 `python -m pytest tests/test_skills_manager.py tests/test_tui_smoke.py -q -k "missing_tool or fatal"`，期望通过；覆盖 AC12）
- [ ] 目录型专属工具按需暴露：加载目录型 Skill 前专属工具不在模型请求中，加载后可见并按现有权限规则执行（验证：运行 `python -m pytest tests/test_skills_loader.py tests/test_skills_tools.py tests/test_agent.py -q -k "directory or script or dedicated"`，期望通过；覆盖 AC13）
- [ ] Skill 斜杠命令可触发执行：输入 Skill 对应命令和参数后触发该 Skill，并把参数替换到 `{{input}}` 或 `{{args}}`（验证：运行 `python -m pytest tests/test_commands.py tests/test_skills_execution.py -q -k "skill_command or placeholder"`，期望通过；覆盖 AC14）
- [ ] 帮助和补全包含 Skill 命令：`/help` 与 Tab 补全能展示 Skill 命令，说明来自 Skill 的一句说明（验证：运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py -q -k "help or completion"`，期望通过；覆盖 AC15）
- [ ] 命令冲突可诊断：Skill 命令与已有命令冲突时显示冲突来源；迁移后的 `/review` 只保留 Skill 行为（验证：运行 `python -m pytest tests/test_commands.py tests/test_skills_manager.py -q -k "conflict or review"`，期望通过；覆盖 AC16）
- [ ] Skill 热更新生效：修改说明、正文、白名单或执行模式后，摘要、加载结果、工具可见性和命令行为反映新内容（验证：运行 `python -m pytest tests/test_skills_manager.py tests/test_tui_smoke.py -q -k "refresh or changed"`，期望通过；覆盖 AC17）
- [ ] 删除 Skill 后不可继续使用：删除已发现 Skill 并热更新后，该 Skill 从摘要、帮助和补全中消失，再加载时返回清晰错误（验证：运行 `python -m pytest tests/test_skills_manager.py tests/test_commands.py -q -k "delete or missing"`，期望通过；覆盖 AC18）
- [ ] 清空对话会清理激活 Skill：执行 `/clear` 后已激活 Skill 为空，后续普通请求不再携带旧 SOP（验证：运行 `python -m pytest tests/test_commands.py tests/test_prompting.py tests/test_tui_smoke.py -q -k "clear or active"`，期望通过；覆盖 AC19）
- [ ] 内置样板可用：默认安装下 commit、review、test 三个内置 Skill 能被发现、加载并通过斜杠命令触发（验证：运行 `python -m pytest tests/test_skills_loader.py tests/test_commands.py tests/test_skills_tools.py -q -k "builtin or commit or review or test"`，期望通过；覆盖 AC20）
- [ ] 失败可恢复：Skill 加载失败、独立执行失败、专属工具失败或权限拒绝时，界面显示可理解错误，输入能力恢复，普通对话可继续（验证：运行 `python -m pytest tests/test_skills_tools.py tests/test_skills_execution.py tests/test_tui_smoke.py tests/test_tool_scheduler.py -q -k "failure or denied or recover"`，期望通过；覆盖 AC21）

## 集成
- [ ] Skill 目录发现与 MCP 工具注册顺序正确：引用 MCP 工具的 Skill 在 MCP 注册后校验，不被提前误判为不存在（验证：运行 `python -m pytest tests/test_skills_manager.py tests/test_tui_smoke.py -q -k "mcp"`，期望通过）
- [ ] PromptBuilder 集成正确：可用 Skill 摘要和已激活完整 SOP 位于运行时上下文内，并位于项目指令、长期记忆和上下文摘要之前（验证：运行 `python -m pytest tests/test_prompting.py -q -k "skill"`，期望通过）
- [ ] ToolPolicy 集成正确：`load_skill` 作为系统工具始终可见且不受 Skill 白名单或 Plan Mode 隐藏，普通工具仍受 Plan Mode、白名单和权限约束（验证：运行 `python -m pytest tests/test_tool_scheduler.py -q -k "system or policy or permission"`，期望通过）
- [ ] CommandRegistry 集成正确：动态 Skill 命令能注册、注销、补全、帮助展示，并与内置命令共享冲突检测（验证：运行 `python -m pytest tests/test_commands.py tests/test_skills_manager.py -q -k "skill_command or unregister or conflict"`，期望通过）
- [ ] Agent Loop 集成正确：模型通过 `load_skill` 激活 Skill 后，下一轮请求使用收窄后的工具集合，并携带完整 SOP（验证：运行 `python -m pytest tests/test_agent.py -q -k "load_skill or whitelist or prompt"`，期望通过）
- [ ] TUI 集成正确：启动 warning 可见，fatal error 禁用输入，用户提交前热更新生效，Skill 命令能复用现有 Agent Loop 展示路径（验证：运行 `python -m pytest tests/test_tui_smoke.py -q -k "startup or refresh or invoke_skill"`，期望通过）
- [ ] Provider 模型覆盖集成正确：共享和独立 Skill 的模型覆盖都通过 ProviderResolver 使用同一配置克隆 Provider（验证：运行 `python -m pytest tests/test_skills_execution.py -q -k "model"`，期望通过）
- [ ] 既有行为不回退：普通聊天、Plan Mode、MCP 工具、上下文压缩、会话恢复、长期记忆和权限系统仍通过现有测试（验证：运行 `python -m pytest tests/test_agent.py tests/test_mcp_manager.py tests/test_context_manager.py tests/test_session_recovery.py tests/test_memory_instructions.py tests/test_permissions.py -q`，期望通过）

## 编译与测试
- [ ] Skill 子系统专项测试通过（验证：运行 `python -m pytest tests/test_skills_loader.py tests/test_skills_manager.py tests/test_skills_tools.py tests/test_skills_execution.py -q`，期望全部通过）
- [ ] Skill 集成相关测试通过（验证：运行 `python -m pytest tests/test_prompting.py tests/test_tool_scheduler.py tests/test_commands.py tests/test_agent.py tests/test_tui_smoke.py tests/test_tools.py -q`，期望全部通过）
- [ ] 项目全量测试通过（验证：运行 `python -m pytest -q`，期望全部通过；若失败来自不可控外部环境，记录具体命令、错误和影响范围）
- [ ] 项目源码语法编译通过（验证：运行 `python -m compileall -q src`，期望无输出且退出码为 0）
- [ ] README 包含用户可见说明和范围边界（验证：运行 `rg -n "Skill|load_skill|frontmatter|commit|review|test|市场|版本" README.md`，期望命中新能力说明和不做范围）
- [ ] 内置 Markdown 被打包配置覆盖（验证：运行 `python -m pytest tests/test_skills_loader.py -q -k "builtin"`，期望通过，并检查默认发现 commit、review、test）

## 端到端场景
- [ ] 场景 1：启动后查看帮助，用户输入 `/help` → 界面展示 commit、review、test 三个 Skill 命令，且说明为中文（验证：在 tmux 会话 `mewcode-skill-e2e` 中启动 MewCode，输入 `/help`，运行 `tmux capture-pane -pt mewcode-skill-e2e`，期望看到 `/commit`、`/review`、`/test`）
- [ ] 场景 2：通过斜杠命令触发共享 Skill，用户输入 `/review README.md` → 界面展示用户命令，模型请求携带 review 完整 SOP，最终回复保留在主对话（验证：使用 mock OpenAI server 或请求记录，运行 `tmux capture-pane -pt mewcode-skill-e2e` 并检查请求日志，期望看到 `/review README.md` 和 review SOP 标记）
- [ ] 场景 3：模型按需加载 Skill，用户输入普通请求“用 review Skill 检查 README.md” → 模型调用 `load_skill`，后续请求包含完整 SOP 且工具集合按白名单收窄（验证：查看 tmux pane 和 mock 请求日志，期望看到 `load_skill` 工具状态、review 激活上下文和收窄后的工具列表）
- [ ] 场景 4：清空对话清理激活 Skill，用户输入 `/clear` 后再输入普通问题 → 后续请求只包含可用 Skill 摘要，不再包含之前 review 的完整 SOP（验证：查看 mock 请求日志和 `tmux capture-pane -pt mewcode-skill-e2e`，期望 `/clear` 后仍可继续对话且运行时上下文不含旧完整 SOP）
- [ ] 场景 5：非法 Skill 可恢复，创建一个 YAML 非法的项目级 Skill 后热更新 → 界面展示 warning，合法内置 Skill 仍可用（验证：在项目 `.mewcode/skills/` 放入非法 Markdown，触发一次输入或重启，运行 `tmux capture-pane -pt mewcode-skill-e2e`，期望看到 warning 且 `/help` 仍展示内置 Skill）
- [ ] 场景 6：白名单 fatal 可诊断，创建一个引用不存在工具的项目级 Skill 后热更新 → 界面显示具体 Skill 名和工具名并禁用误用路径（验证：在项目 `.mewcode/skills/` 放入白名单错误的 Skill，触发启动或热更新，运行 `tmux capture-pane -pt mewcode-skill-e2e`，期望看到缺失工具名和对应 Skill 名）
