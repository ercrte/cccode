# JulyCode 子 Agent 委派 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] `delegate_agent` 委派入口始终作为单一稳定工具暴露，定义式和 Fork 式通过参数分流，主 Agent 工具列表不因类型变化而增删工具（验证：运行 `python -m pytest tests/test_subagents_tools.py tests/test_agent.py -k "delegate or stable or parent_context" -q`，期望通过）
- [ ] `delegate_agent` 对缺少必填参数、未知类型、定义式缺少角色名等输入返回结构化失败，不启动错误子任务（验证：运行 `python -m pytest tests/test_subagents_tools.py -k invalid -q`，期望通过）
- [ ] 合法 Markdown + YAML frontmatter 角色能被加载，正文作为定义式子 Agent 生命周期内持续提示生效（验证：运行 `python -m pytest tests/test_subagents_loader.py tests/test_prompting.py -k "role or defined" -q`，期望通过）
- [ ] 角色模型偏好支持继承默认模型，也支持 `haiku`、`sonnet`、`opus` 或配置映射到实际模型名（验证：运行 `python -m pytest tests/test_config.py tests/test_subagents_manager.py -k model -q`，期望通过）
- [ ] 项目级、用户级、内置级、插件级同名角色按项目高于用户、高于内置、高于插件生效（验证：运行 `python -m pytest tests/test_subagents_loader.py -k priority -q`，期望通过）
- [ ] 角色 frontmatter 缺字段、YAML 非法、正文为空或引用不可用工具时给出可定位 warning/error，并避免启动错误角色（验证：运行 `python -m pytest tests/test_subagents_loader.py -k "bad or invalid or missing" -q`，期望通过）
- [ ] 定义式子 Agent 从空白会话启动，只看到角色提示、子任务和必要环境信息，看不到父对话无关历史（验证：运行 `python -m pytest tests/test_subagents_manager.py -k "defined and history" -q`，期望通过）
- [ ] Fork 式子 Agent 复制父对话安全快照，能引用父已有上下文，且不会复制未完成工具调用或破坏父循环状态（验证：运行 `python -m pytest tests/test_subagents_manager.py -k "fork and history" -q`，期望通过）
- [ ] Fork 式委派即使请求前台也会强制后台启动，并在工具结果中说明已强制后台（验证：运行 `python -m pytest tests/test_subagents_tools.py tests/test_subagents_manager.py -k "fork and background" -q`，期望通过）
- [ ] 子 Agent 运行到模型不再请求工具时正常完成，并生成包含任务目标、摘要、状态、停止原因、关键输出和用量的结构化结果（验证：运行 `python -m pytest tests/test_subagents_manager.py -k "completed or result" -q`，期望通过）
- [ ] 子 Agent 达到最大轮次、模型错误、工具错误、权限无法继续或取消时停止，并生成包含停止原因的结构化结果（验证：运行 `python -m pytest tests/test_subagents_manager.py -k "limit or error or cancel" -q`，期望通过）
- [ ] 内置 `code-searcher` 和 `reviewer` 角色随包安装可发现，且默认只允许读类核心工具（验证：运行 `python -m pytest tests/test_subagents_loader.py tests/test_subagents_policy.py -k builtin -q`，期望通过）

## 状态隔离
- [ ] 子 Agent 的中间模型消息、工具调用和工具结果不直接进入主对话历史，主对话只收到委派工具结果或后台完成通知（验证：运行 `python -m pytest tests/test_subagents_manager.py -k "main_history or no_pollution" -q`，期望通过）
- [ ] 子 Agent 的临时权限允许、拒绝和确认状态不会改变主 Agent 后续同类工具调用的权限判断（验证：运行 `python -m pytest tests/test_subagents_manager.py tests/test_permissions.py -k "permission and isolation" -q`，期望通过）
- [ ] 子 Agent 的文件读取缓存相互独立，也不影响主 Agent 缓存命中和失效判断（验证：运行 `python -m pytest tests/test_tools.py tests/test_subagents_manager.py -k "cache and isolation" -q`，期望通过）
- [ ] 子 Agent 的 Token 计数、上下文外置结果和摘要状态不影响主 Agent 或其他子 Agent 的上下文估算（验证：运行 `python -m pytest tests/test_subagents_manager.py tests/test_context_manager.py -k "context or token" -q`，期望通过）
- [ ] 子 Agent 拥有独立 Hook 运行时状态；子 Agent 内产生的 prompt injection 不污染主 Agent 后续请求（验证：运行 `python -m pytest tests/test_subagents_manager.py tests/test_hooks.py -k "hook and isolation" -q`，期望通过）
- [ ] 子 Agent 可以共享模型访问能力、Hook 配置与动作执行能力、全局工具注册表和项目文件系统视图完成任务（验证：运行 `python -m pytest tests/test_subagents_manager.py -k "shared or provider or hook" -q`，期望通过）

## 后台任务
- [ ] 显式后台委派立即返回后台任务标识和启动信息，不阻塞主 Agent 等待完整结果（验证：运行 `python -m pytest tests/test_subagents_manager.py -k "explicit and background" -q`，期望通过）
- [ ] 前台子 Agent 未转后台时，主 Agent 等待完成并收到结构化工具结果（验证：运行 `python -m pytest tests/test_subagents_manager.py -k foreground -q`，期望通过）
- [ ] 前台子 Agent 超过阈值后自动切入后台，主 Agent 收到已转后台信息，任务继续运行（验证：运行 `python -m pytest tests/test_subagents_manager.py -k timeout -q`，期望通过）
- [ ] 用户在子 Agent 前台等待期间执行 `/background` 后，主 Agent 不再阻塞等待该任务，任务完成后继续按后台规则通知（验证：运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py -k background -q`，期望通过）
- [ ] 后台任务管理能展示或返回任务标识、类型、角色、状态、启动时间、结束时间、结果、错误和用量（验证：运行 `python -m pytest tests/test_subagents_manager.py tests/test_commands.py -k "snapshot or agents" -q`，期望通过）
- [ ] 后台子 Agent 完成后自动向主会话追加一条中文可见完成通知，通知包含任务标识、状态、摘要、停止原因和关键结果（验证：运行 `python -m pytest tests/test_subagents_manager.py tests/test_tui_smoke.py -k notification -q`，期望通过）
- [ ] 后台完成通知失败不会导致 TUI 退出、主会话损坏或后续输入不可用（验证：运行 `python -m pytest tests/test_subagents_manager.py -k notify_failure -q`，期望通过）
- [ ] 用户取消主任务时，前台等待的子 Agent 被停止；已后台化的子 Agent 保留并在完成后通知主对话（验证：运行 `python -m pytest tests/test_subagents_manager.py tests/test_tui_smoke.py -k cancel -q`，期望通过）
- [ ] 应用关闭时未完成后台任务会被清理，不留下未处理 asyncio 任务告警（验证：运行 `python -m pytest tests/test_tui_smoke.py -k unmount -q`，期望通过）

## 工具与安全
- [ ] 全局禁止工具不会出现在任何子 Agent 可用工具集合中，也不能通过角色白名单或 Fork 继承启用（验证：运行 `python -m pytest tests/test_subagents_policy.py -k global_block -q`，期望通过）
- [ ] 定义式子 Agent 工具集合同时符合角色白名单、角色黑名单、当前运行模式、权限策略和后台安全策略（验证：运行 `python -m pytest tests/test_subagents_policy.py -k defined -q`，期望通过）
- [ ] Fork 式子 Agent 工具集合不超过父 Agent 当前可见工具能力，并继续受到全局禁止、后台白名单和防嵌套限制（验证：运行 `python -m pytest tests/test_subagents_policy.py tests/test_subagents_manager.py -k fork -q`，期望通过）
- [ ] 后台子 Agent 请求不适合后台执行的工具时，系统不执行该工具，并把结构化失败结果回灌给子 Agent（验证：运行 `python -m pytest tests/test_subagents_policy.py tests/test_subagents_manager.py -k "background and denied" -q`，期望通过）
- [ ] 子 Agent 请求不可用、被限制或被权限拒绝的工具时，失败结果先回灌给子 Agent；只有达到停止条件时才结束（验证：运行 `python -m pytest tests/test_subagents_manager.py -k "tool_failure or denied" -q`，期望通过）
- [ ] 子 Agent 尝试再次调用 `delegate_agent` 时被拒绝，并返回防嵌套失败结果（验证：运行 `python -m pytest tests/test_subagents_policy.py -k nested -q`，期望通过）
- [ ] Plan Mode、Skill 工具白名单、权限系统、上下文压缩和 Hook 的既有行为不因子 Agent 工具过滤而回归（验证：运行 `python -m pytest tests/test_agent.py tests/test_skills.py tests/test_permissions.py tests/test_context_manager.py tests/test_hooks.py -q`，期望全部通过）

## 集成
- [ ] 主 Agent 运行时提示包含可用子 Agent 角色摘要和后台任务摘要，子 Agent 运行时提示包含自己的角色正文或 Fork 约束（验证：运行 `python -m pytest tests/test_prompting.py -k sub_agent -q`，期望通过）
- [ ] `AgentLoopRunner` 在工具阶段前绑定父上下文，工具阶段后清除，`delegate_agent` 能读取父历史和父可见工具集合（验证：运行 `python -m pytest tests/test_agent.py -k parent_context -q`，期望通过）
- [ ] TUI 启动时注册 `delegate_agent`，用户输入前刷新角色目录，角色加载错误以中文展示且不阻断后续普通输入（验证：运行 `python -m pytest tests/test_tui_smoke.py -k sub_agent -q`，期望通过）
- [ ] `/status` 显示子 Agent 摘要，`/agents` 显示可用角色和后台任务详情，`/background` 支持手动切后台（验证：运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py -k "status or agents or background" -q`，期望通过）
- [ ] `sub_agents` 配置可设置启用状态、前台超时、后台任务上限、全局禁用工具、后台白名单、模型别名和插件角色根目录（验证：运行 `python -m pytest tests/test_config.py -k sub_agents -q`，期望通过）
- [ ] 独立 Skill 复用子 Agent 隔离运行基础设施，保留“独立 Skill 执行摘要”的用户可见语义，shared Skill 行为不变（验证：运行 `python -m pytest tests/test_skills.py tests/test_tui_smoke.py -k skill -q`，期望通过）
- [ ] README 记录角色定义格式、加载优先级、委派方式、后台行为、配置示例和本阶段不做的范围（验证：运行 `rg -n "delegate_agent|sub_agents|\\.julycode/agents|Worktree|跨会话" README.md`，期望能看到对应说明）

## 编译与测试
- [ ] 项目 Python 文件可编译（验证：运行 `python -m compileall src tests`，期望退出码为 0）
- [ ] 子 Agent 专项测试全部通过（验证：运行 `python -m pytest tests/test_subagents_loader.py tests/test_subagents_tools.py tests/test_subagents_policy.py tests/test_subagents_manager.py -q`，期望全部通过）
- [ ] 相关回归测试全部通过（验证：运行 `python -m pytest tests/test_agent.py tests/test_tools.py tests/test_tool_scheduler.py tests/test_prompting.py tests/test_config.py tests/test_commands.py tests/test_tui_smoke.py tests/test_skills.py -q`，期望全部通过）
- [ ] 全量单元测试通过（验证：运行 `python -m pytest -q`，期望全部通过）
- [ ] 项目未配置 lint 工具，本阶段不新增 lint 门禁（验证：运行 `python -c "import tomllib; cfg=tomllib.load(open('pyproject.toml','rb')); tool=cfg.get('tool', {}); print(any(name in tool for name in ('ruff','mypy','flake8','black')))"`，期望输出 `False`）

## 端到端场景
- [ ] 场景 1：在 tmux 中启动 JulyCode，输入“请委派代码搜索子 Agent 查找 README 里 Skill 相关说明并总结”后，主 Agent 调用 `delegate_agent` 的定义式路径，子 Agent 使用读类工具完成，主对话只显示委派工具结果摘要（验证：使用 mock provider 或 `tests/e2e_mock_openai_server.py` 启动 `julycode`，运行 `tmux capture-pane -p`，期望看到 `delegate_agent`、子任务摘要和无子 Agent 中间历史污染）
- [ ] 场景 2：在 tmux 中启动 JulyCode，先进行一轮普通对话形成父历史，再输入“Fork 一个后台子 Agent 检查当前权限系统测试覆盖并完成后通知我”，系统立即返回后台任务标识，稍后自动显示完成通知（验证：使用 mock provider 或 `tests/e2e_mock_openai_server.py`，运行 `tmux capture-pane -p`，期望看到后台任务 ID、强制后台说明和完成通知）
- [ ] 场景 3：在 tmux 中启动 JulyCode，触发一个前台定义式子 Agent 后执行 `/background`，主输入恢复，子任务继续在后台完成并通知主对话（验证：运行 `tmux send-keys -t <session> "/background" Enter` 后再 `tmux capture-pane -p`，期望看到已切入后台和完成通知）
- [ ] 场景 4：在 tmux 中启动 JulyCode，输入“让子 Agent 再委派一个子 Agent”之类的请求，子 Agent 尝试嵌套委派时被拒绝并返回结构化失败，主应用不中断（验证：使用 mock provider 构造嵌套调用，运行 `tmux capture-pane -p`，期望看到防嵌套拒绝和后续可继续输入）
- [ ] 场景 5：完成上述端到端场景后，对照本 checklist 逐项验收并记录证据（验证：保存命令输出、tmux 截屏文本或测试输出，期望所有非跳过项都有证据）
