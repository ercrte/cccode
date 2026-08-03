# MCP 工具延迟加载 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/julycode/tools/base.py` | 增加 `deferred` 工具可见性 |
| 修改 | `src/julycode/tools/scheduler.py` | 按轮次激活集合过滤和校验延迟工具 |
| 新建 | `src/julycode/mcp/search.py` | MCP 工具目录、规范化、确定性检索和搜索结果类型 |
| 新建 | `src/julycode/mcp/scope.py` | 单个 Agent Runner 的轮次激活状态 |
| 修改 | `src/julycode/mcp/tools.py` | 轻量检索工具和远端工具延迟可见性 |
| 修改 | `src/julycode/mcp/manager.py` | Catalog 同步、延迟注册、搜索状态、OAuth 更新和报告 |
| 修改 | `src/julycode/mcp/__init__.py` | 导出新增 MCP 公共类型 |
| 修改 | `src/julycode/prompting/base.py` | 运行时提示上下文增加 MCP 摘要 |
| 修改 | `src/julycode/prompting/builder.py` | 渲染紧凑的 MCP Server 名称和工具数量 |
| 修改 | `src/julycode/agent.py` | 接入轮次激活、检索结果消费和终止清理 |
| 修改 | `src/julycode/tui/app.py` | 向各 Runner 传递 Manager并显示当前暴露状态 |
| 修改 | `src/julycode/skills/execution.py` | 独立 Skill Runner 使用独立 MCP 轮次状态 |
| 修改 | `src/julycode/subagents/manager.py` | 向子 Agent Runner Factory 传递 Manager |
| 修改 | `src/julycode/subagents/runtime.py` | 子 Agent Runner 使用独立 MCP 轮次状态 |
| 修改 | `src/julycode/teams/runtime.py` | 团队成员 Runner 使用独立 MCP 轮次状态 |
| 修改 | `src/julycode/commands/models.py` | 状态快照记录当前暴露的 MCP 工具 |
| 修改 | `src/julycode/commands/builtin.py` | `/status` 区分发现数和暴露数 |
| 新建 | `tests/test_mcp_search.py` | 检索、Catalog、轮次状态、性能和占用测试 |
| 修改 | `tests/test_mcp_tools.py` | 检索 ToolSpec、紧凑结果和 deferred 属性测试 |
| 修改 | `tests/test_mcp_manager.py` | Manager、Catalog、注册、报告和 OAuth 测试 |
| 修改 | `tests/test_tool_scheduler.py` | deferred 过滤、激活和直接调用拒绝测试 |
| 修改 | `tests/test_agent.py` | 两阶段加载、替换、策略和退出清理测试 |
| 修改 | `tests/test_prompting.py` | MCP Server 紧凑摘要测试 |
| 修改 | `tests/test_context_estimator.py` | 实际暴露集合的 Token 估算测试 |
| 修改 | `tests/test_commands.py` | `/status` 发现数和暴露数测试 |
| 修改 | `tests/test_subagents.py` | 子 Agent MCP 激活状态隔离测试 |
| 修改 | `tests/test_team_runtime.py` | 团队成员 MCP 激活状态隔离测试 |
| 修改 | `tests/test_openai_provider.py` | 延迟候选工具序列化回归测试 |
| 修改 | `tests/test_anthropic_provider.py` | 延迟候选工具序列化回归测试 |
| 修改 | `tests/test_tui_smoke.py` | TUI Manager 传递、状态和 OAuth 更新测试 |
| 修改 | `tests/e2e_mock_openai_server.py` | mock 模型执行“检索后调用”两阶段流程 |
| 修改 | `_estimate_tokens.py` | 输出全量加载与延迟加载的占用对比 |
| 修改 | `README.md` | 说明 MCP 工具延迟加载行为、上限和生命周期 |

## T1: 增加 deferred 可见性

**文件：** `src/julycode/tools/base.py`、`src/julycode/mcp/tools.py`、`tests/test_mcp_tools.py`
**依赖：** 无

**步骤：**
1. 将 `deferred` 加入 `ToolVisibility`。
2. 将 `RemoteMcpTool` 的可见性设为 `deferred`，保持名称、说明、Schema、`side_effect` 和 origin 不变。
3. 更新远端工具规格测试，明确断言新的可见性和未改变的既有属性。

**验证：** 运行 `python -m pytest tests/test_mcp_tools.py -k "remote_mcp_tool_exposes" -q`，期望 deferred、Schema、安全级别和 origin 断言全部通过。

## T2: 在 ToolPolicy 中过滤未激活工具

**文件：** `src/julycode/tools/scheduler.py`、`tests/test_tool_scheduler.py`
**依赖：** T1

**步骤：**
1. 给 `ToolPolicy` 增加不可变的 `activated_deferred_tools` 集合。
2. 让 `allowed_specs()` 默认排除 deferred 工具，仅放行激活集合中的 deferred 工具，再继续执行现有模式、白名单、子 Agent 和 Gate 过滤。
3. 让 `validate_call()` 对直接猜测未激活工具返回 `tool_not_loaded`。
4. 增加正常模式、Plan Mode、白名单、Gate、激活和未激活调用测试，确认 model/system 工具行为不变。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py -k "deferred or policy" -q`，期望未激活隐藏、激活放行、直接调用拒绝及既有策略测试通过。

## T3: 定义检索数据结构和文本规范化

**文件：** `src/julycode/mcp/search.py`、`tests/test_mcp_search.py`
**依赖：** T1

**步骤：**
1. 新增 `McpSearchDocument`、`McpToolMatch`、`McpToolSearchResult`、`McpToolSearchProvider` 和 `McpServerToolSummary`。
2. 实现 NFKC、`casefold()`、分隔符替换、Unicode token 提取、短 token 与通用停用词过滤。
3. 实现折叠空白、最长 160 字符的紧凑摘要，并保留标题和远端名回退。
4. 增加大小写、下划线、连字符、Unicode、重复词、停用词和摘要截断测试。

**验证：** 运行 `python -m pytest tests/test_mcp_search.py -k "normalize or tokenize or summary" -q`，期望所有规范化和摘要边界测试通过。

## T4: 实现 MCP 工具 Catalog 生命周期

**文件：** `src/julycode/mcp/search.py`、`tests/test_mcp_search.py`
**依赖：** T3

**步骤：**
1. 实现 `McpToolCatalog.replace_server()`，按 Server 原子替换定义和预规范化文档。
2. 实现 searchable 名称集合、`get()`、Server 摘要和 `remove_server()`。
3. 保证 Catalog 保存完整原始定义与 Schema，但 Server 摘要只统计可检索工具。
4. 增加替换、移除、跨 Server 同名工具、注册失败排除和 Schema 保留测试。

**验证：** 运行 `python -m pytest tests/test_mcp_search.py -k "catalog" -q`，期望 Catalog 生命周期和完整定义保留测试通过。

## T5: 实现确定性加权检索

**文件：** `src/julycode/mcp/search.py`、`tests/test_mcp_search.py`
**依赖：** T4

**步骤：**
1. 按 `plan.md` 的固定分值实现名称、标题、说明、前缀和全 token 覆盖评分。
2. 排除零分结果，按分数、Server 名、远端名稳定排序。
3. 支持可选 Server 过滤并将结果固定截断到 5 个。
4. 确保 Server 名不参与普通查询加分，结果对象不携带完整 Schema。
5. 增加精确名称、任务关键词、标题、说明、前缀、稳定 tie-break、无匹配、Server 过滤和 Top-5 测试。

**验证：** 运行 `python -m pytest tests/test_mcp_search.py -k "ranking or deterministic or server_filter or limit or no_match" -q`，期望排序和边界测试通过。

## T6: 验证检索性能

**文件：** `tests/test_mcp_search.py`
**依赖：** T5

**步骤：**
1. 构造 1,000 个带名称、标题、说明和 Schema 的工具定义并预建 Catalog。
2. 用 `time.perf_counter()` 只测量索引完成后的单次搜索路径。
3. 断言结果稳定、数量不超过 5 且耗时低于 100 毫秒。

**验证：** 运行 `python -m pytest tests/test_mcp_search.py -k "performance" -q`，期望 1,000 工具检索在 100 毫秒内完成。

## T7: 实现轻量 MCP 检索工具

**文件：** `src/julycode/mcp/tools.py`、`tests/test_mcp_tools.py`
**依赖：** T3、T5

**步骤：**
1. 新增 `SEARCH_MCP_TOOLS_NAME` 和只依赖 `McpToolSearchProvider` 的 `SearchMcpToolsTool`。
2. 按设计定义 `query` 必填、`server` 可选的固定 Schema。
3. 设置 `read_only`、`system` 和独立 origin，确保不触发权限确认且不与远端 Server origin 混淆。
4. 将搜索结果序列化为状态、查询、Server、最多 5 个紧凑候选和激活摘要，不输出 score 或 Schema。
5. 增加 ToolSpec、Provider 调用参数、空结果和紧凑输出测试。

**验证：** 运行 `python -m pytest tests/test_mcp_tools.py -k "search_mcp" -q`，期望 Schema、安全性、可见性和紧凑结果测试通过。

## T8: 实现 Runner 独立的 McpTurnState

**文件：** `src/julycode/mcp/scope.py`、`tests/test_mcp_search.py`
**依赖：** T2、T4、T7

**步骤：**
1. 实现 `begin_turn()`、`end_turn()`、`active_tools` 和 `prompt_context()`。
2. 实现 `apply_search_results()`，按原始调用顺序识别成功或失败的检索结果。
3. 用候选激活后的临时 `ToolPolicy` 计算实际可见集合，并把 `activated_tools`、过滤数量和 `policy_filtered` 状态写回模型可见结果。
4. 让每次搜索整体替换旧集合；空结果、搜索失败和最后一个搜索调用均按设计清理或覆盖。
5. 增加两个状态实例互不污染、重复搜索替换、Plan Mode/白名单过滤和 begin/end 清理测试。

**验证：** 运行 `python -m pytest tests/test_mcp_search.py -k "turn_state or policy_filtered or replacement or isolation" -q`，期望轮次状态和策略交集测试通过。

## T9: 将 Catalog 接入 MCP Manager

**文件：** `src/julycode/mcp/manager.py`、`src/julycode/mcp/__init__.py`、`tests/test_mcp_manager.py`
**依赖：** T4、T8

**步骤：**
1. 让 Manager 持有单一 `McpToolCatalog`，Server 初始化成功后写入定义，移除运行态时删除对应分片。
2. 实现 `search_tools()`，区分 `ok`、`no_match`、`server_not_found` 和 `server_unavailable`。
3. 实现 `create_turn_state()` 和只包含已连接 Server 名称/数量的 `prompt_context()`。
4. 扩展 `McpLoadReport.discovered_tools`，保留已注册与失败工具信息。
5. 对检索摘要和状态消息复用 Server 脱敏逻辑。
6. 导出新增公共类型并增加初始化、搜索状态、报告和脱敏测试。

**验证：** 运行 `python -m pytest tests/test_mcp_manager.py -k "catalog or search or report or redacted" -q`，期望目录同步、四类状态和报告测试通过。

## T10: 注册检索入口和 deferred 远端工具

**文件：** `src/julycode/mcp/manager.py`、`tests/test_mcp_manager.py`
**依赖：** T1、T7、T9

**步骤：**
1. 调整 `register_tools()`：远端工具仍注册到共享 Registry，但保持 deferred；有 MCP 配置时只额外注册一个检索工具。
2. 仅把成功注册的远端工具标记为 searchable，注册冲突继续进入 `failed_tools`。
3. 未配置 MCP 时不注册检索工具；全部 Server 不可用但存在配置时保留检索入口用于结构化诊断。
4. 增加成功注册、重复名隔离、空配置和全部不可用测试。

**验证：** 运行 `python -m pytest tests/test_mcp_manager.py -k "register or duplicate or empty or unavailable" -q`，期望远端工具本地存在但默认延迟、检索入口数量正确。

## T11: 同步 OAuth 与可检索目录

**文件：** `src/julycode/mcp/manager.py`、`tests/test_mcp_manager.py`
**依赖：** T9、T10

**步骤：**
1. OAuth 授权成功后重新初始化 Server、替换 Catalog 分片并注册 deferred 工具。
2. logout、`authorization_required` 和 `refresh_failed` 时撤销 searchable 标记并注销目标 Server origin。
3. 确认其他 Server、内置工具和检索入口不受影响。
4. 更新授权成功提示为“工具目录已加载”，保持命令行为兼容。
5. 增加授权前不可用、授权后可搜索、logout/失效后不可搜索和多 Server 隔离测试。

**验证：** 运行 `python -m pytest tests/test_mcp_manager.py -k "oauth and (search or catalog or logout or refresh)" -q`，期望 OAuth 状态与 Catalog/Registry 同步测试通过。

## T12: 注入紧凑 MCP 运行时提示

**文件：** `src/julycode/prompting/base.py`、`src/julycode/prompting/builder.py`、`tests/test_prompting.py`
**依赖：** T9

**步骤：**
1. 给 `RuntimePromptContext` 增加可选 `McpPromptContext`。
2. 在不可缓存运行时块渲染延迟加载说明和已连接 Server 的名称/工具数量。
3. 没有 MCP 上下文时不输出 MCP 块；有上下文时不列工具名、说明或 Schema。
4. 增加无 MCP、单 Server、多 Server、OAuth 动态摘要和目录不泄露测试。

**验证：** 运行 `python -m pytest tests/test_prompting.py -k "mcp" -q`，期望摘要紧凑、动态且不包含完整目录。

## T13: 接入 Agent Loop 的基础两阶段流程

**文件：** `src/julycode/agent.py`、`tests/test_agent.py`
**依赖：** T2、T8、T10、T12

**步骤：**
1. 给 `AgentLoopRunner` 和 `ToolAwareTurnRunner` 增加可选 `mcp_manager`，并为每个 Runner 创建独立状态。
2. 每次迭代把状态中的 active names 传入 `ToolPolicy`，把 MCP prompt context 传入 PromptBuilder。
3. Scheduler 完成后、写入 ChatSession 前消费搜索结果。
4. 增加请求序列测试：首次只有检索入口，检索后下一请求只有命中远端工具，随后可调用并回灌结果。
5. 更新原有直接调用 Remote MCP 工具的 Agent 测试为“检索→加载→调用”。

**验证：** 运行 `python -m pytest tests/test_agent.py -k "mcp and (lazy or remote)" -q`，期望两阶段请求和真实 RemoteMcpTool 执行测试通过。

## T14: 覆盖重复检索和既有策略交集

**文件：** `src/julycode/agent.py`、`tests/test_agent.py`
**依赖：** T13

**步骤：**
1. 验证同一轮第二次检索替换第一批工具，不做并集且数量始终不超过 5。
2. 验证模型直接猜测未加载 MCP 工具时收到 `tool_not_loaded`，下一迭代仍不暴露全目录。
3. 验证 Plan Mode、激活 Skill 白名单和 Tool Gate 会过滤候选，但检索入口仍可用。
4. 验证内置、system 和 Skill 专属工具在相同请求中的可见性保持不变。

**验证：** 运行 `python -m pytest tests/test_agent.py -k "mcp_lazy_replacement or mcp_lazy_policy or mcp_not_loaded" -q`，期望替换、上限和策略兼容测试通过。

## T15: 覆盖所有终止路径的轮次清理

**文件：** `src/julycode/agent.py`、`tests/test_agent.py`
**依赖：** T13

**步骤：**
1. 用外层 `try/finally` 覆盖 Hook、上下文准备、Provider 流式请求、Scheduler 和正常完成路径。
2. 在追加用户消息前执行入口清理，在 `finally` 执行出口清理。
3. 分别为正常完成、主动取消、Task cancellation、迭代上限、上下文限制、连续未知工具和 Provider 异常验证 active names 归零。
4. 在同一 Runner 开始下一用户轮次，断言首个请求不含上一轮远端工具定义。

**验证：** 运行 `python -m pytest tests/test_agent.py -k "mcp_lazy_cleanup" -q`，期望所有终止原因和下一轮入口测试通过。

## T16: 把 Manager 传入主 Agent和独立 Skill

**文件：** `src/julycode/tui/app.py`、`src/julycode/skills/execution.py`、`tests/test_tui_smoke.py`
**依赖：** T13、T15

**步骤：**
1. 主 Agent Runner 创建时传入应用持有的 MCP Manager。
2. `create_isolated_skill_runner()` 增加可选 Manager并创建独立状态。
3. 确认独立 Skill 和主 Agent共享 MCP 会话/Catalog，但 active names 不共享。
4. 更新 FakeManager 和 Runner 注入测试，覆盖未配置 Manager 的兼容路径。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -k "mcp or isolated" -q`，期望主 Agent、独立 Skill 和无 MCP 路径构造测试通过。

## T17: 隔离子 Agent 的 MCP 激活状态

**文件：** `src/julycode/subagents/manager.py`、`src/julycode/subagents/runtime.py`、`tests/test_subagents.py`
**依赖：** T13、T16

**步骤：**
1. 让 `SubAgentManager` 保存可选 MCP Manager并传给 `SubAgentRunnerFactory`。
2. 每个定义式、Fork、前台和后台子 Agent Runner创建独立 `McpTurnState`。
3. 保持父 Agent 工具继承、角色 allow/deny、global blocked 和 background allow 过滤顺序。
4. 增加两个并发子 Agent 进行不同检索的测试，断言各自请求和主 Agent 请求互不包含对方候选。

**验证：** 运行 `python -m pytest tests/test_subagents.py -k "mcp" -q`，期望前台/后台/Fork 状态隔离和既有过滤测试通过。

## T18: 隔离团队成员的 MCP 激活状态

**文件：** `src/julycode/teams/runtime.py`、`src/julycode/tui/app.py`、`tests/test_team_runtime.py`
**依赖：** T13、T16

**步骤：**
1. 给 `TeamMemberRunnerFactory` 增加可选 MCP Manager并由 TUI 注入。
2. 每个团队成员 Runner创建独立状态，同时保留 Role Gate、Approval Gate 和成员权限控制。
3. 增加两个成员检索不同工具以及成员与主 Agent并行的隔离测试。
4. 验证未审批副作用 MCP 工具仍被拒绝，检索工具仍可用。

**验证：** 运行 `python -m pytest tests/test_team_runtime.py -k "mcp" -q`，期望团队成员隔离与 Gate 测试通过。

## T19: 更新 MCP 状态报告

**文件：** `src/julycode/commands/models.py`、`src/julycode/commands/builtin.py`、`src/julycode/tui/app.py`、`tests/test_commands.py`、`tests/test_tui_smoke.py`
**依赖：** T9、T13、T16

**步骤：**
1. 给 `CommandStatusSnapshot` 增加当前主 Runner 的 active MCP 工具名称。
2. 让 TUI 在 Runner 运行时读取其 active names，空闲时返回空集合。
3. 将 `/status` 文案改为已连接 Server、发现工具、当前轮次暴露工具、失败 Server 和失败工具。
4. 更新 OAuth 状态与现有 FakeContext/FakeManager 测试数据。
5. 增加运行中与轮次结束后暴露数量变化测试。

**验证：** 运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py -k "status and mcp" -q`，期望发现数、暴露数和 OAuth 状态同时正确。

## T20: 验证上下文估算和 Token 降幅

**文件：** `tests/test_context_estimator.py`、`tests/test_mcp_search.py`、`_estimate_tokens.py`
**依赖：** T2、T7、T13

**步骤：**
1. 构造 45 个代表性 GitHub MCP ToolSpec，对比全量、检索入口和检索入口 + 5 个候选三种 footprint。
2. 断言空闲请求不包含 deferred 目录，估算占用较全量下降至少 90%。
3. 断言激活候选后 ContextManager/Estimator 只增加候选集合的 Schema 开销。
4. 更新 `_estimate_tokens.py` 输出三种场景、降低比例和是否满足 90% 目标。

**验证：** 运行 `python -m pytest tests/test_context_estimator.py tests/test_mcp_search.py -k "mcp or token" -q && python _estimate_tokens.py`，期望测试通过且脚本显示空闲降幅至少 90%。

## T21: 验证 OpenAI 与 Anthropic 一致序列化

**文件：** `tests/test_openai_provider.py`、`tests/test_anthropic_provider.py`
**依赖：** T7、T13

**步骤：**
1. 为两个 Provider 构造相同的“检索入口 + 已激活候选”ChatRequest。
2. 断言初始请求都不含未激活 MCP 工具，激活后都包含相同名称、说明和 Schema。
3. 断言 payload 不包含 JulyCode 内部的 `deferred`、origin 或 MCP 状态字段。
4. 保留原有 MCP 前缀工具序列化测试。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py tests/test_anthropic_provider.py -k "mcp or deferred" -q`，期望两种 Provider 工具语义一致。

## T22: 更新 mock 模型的两阶段 MCP 行为

**文件：** `tests/e2e_mock_openai_server.py`
**依赖：** T7、T13

**步骤：**
1. 当用户请求 MCP 工具但目标远端工具尚未出现时，让 mock 模型先调用 `search_mcp_tools`。
2. 收到检索结果且下一请求已包含目标工具后，再调用 `server__tool`。
3. 保留内置工具、Skill、子 Agent、权限和团队 E2E 分支。
4. 让请求日志可观察每轮工具名称和远端工具数量，且不记录密钥。

**验证：** 运行 `python -m py_compile tests/e2e_mock_openai_server.py`，再运行 `python -m pytest tests/test_tui_smoke.py -q`，期望 mock 可编译且 TUI smoke 无回归。

## T23: 更新用户文档

**文件：** `README.md`
**依赖：** T19、T20

**步骤：**
1. 将“启动时全部暴露 MCP 工具”改为“启动发现目录、模型按需检索”。
2. 说明固定检索入口、最多 5 个候选、再次检索替换和仅当前用户轮次有效。
3. 说明实际 MCP 工具仍采用 `server__tool`、仍经过权限系统和 Provider 统一序列化。
4. 更新 `/status` 示例并明确未配置 MCP 时不会增加检索工具开销。

**验证：** 运行 `rg -n "search_mcp_tools|按需加载|最多 5|当前用户轮次|发现工具|当前轮次暴露|server__tool" README.md`，期望每项行为均有明确说明。

## T24: 运行 MCP 与策略专项回归

**文件：** `src/julycode/mcp/search.py`、`src/julycode/mcp/scope.py`、`src/julycode/mcp/tools.py`、`src/julycode/mcp/manager.py`、`src/julycode/tools/scheduler.py`、`src/julycode/agent.py`、`tests/test_mcp_search.py`、`tests/test_mcp_tools.py`、`tests/test_mcp_manager.py`、`tests/test_mcp_oauth_flow.py`、`tests/test_tool_scheduler.py`、`tests/test_agent.py`、`tests/test_prompting.py`、`tests/test_context_estimator.py`、`tests/test_commands.py`、`tests/test_tui_smoke.py`
**依赖：** T1-T23

**步骤：**
1. 运行检索、工具、Manager、OAuth、策略、Agent、提示词、上下文、命令和 TUI 专项测试。
2. 修复失败并重新运行，禁止用跳过或放宽断言掩盖回归。
3. 检查测试输出不包含配置密钥、OAuth token 或完整远端目录泄漏。

**验证：** 运行 `python -m pytest tests/test_mcp_search.py tests/test_mcp_tools.py tests/test_mcp_manager.py tests/test_mcp_oauth_flow.py tests/test_tool_scheduler.py tests/test_agent.py tests/test_prompting.py tests/test_context_estimator.py tests/test_commands.py tests/test_tui_smoke.py -q`，期望全部通过。

## T25: 运行多 Agent 与 Provider 回归

**文件：** `src/julycode/skills/execution.py`、`src/julycode/subagents/manager.py`、`src/julycode/subagents/runtime.py`、`src/julycode/teams/runtime.py`、`tests/test_skills.py`、`tests/test_subagents.py`、`tests/test_team_runtime.py`、`tests/test_team_e2e.py`、`tests/test_openai_provider.py`、`tests/test_anthropic_provider.py`
**依赖：** T17、T18、T21、T24

**步骤：**
1. 运行 Skill、子 Agent、团队和两个 Provider 测试。
2. 确认并发 Runner 不共享 active names，非 MCP 工具集合无变化。
3. 修复失败后重复执行同一组命令。

**验证：** 运行 `python -m pytest tests/test_skills.py tests/test_subagents.py tests/test_team_runtime.py tests/test_team_e2e.py tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望全部通过。

## T26: 运行全量自动化测试

**文件：** `src/julycode/`、`tests/`、`README.md`、`_estimate_tokens.py`
**依赖：** T24、T25

**步骤：**
1. 运行全量 pytest。
2. 对失败按根因修复，重新运行受影响测试和全量测试。
3. 运行 `git diff --check` 并确认没有临时配置、日志或测试产物进入工作区。

**验证：** 运行 `python -m pytest -q && git diff --check`，期望全部测试通过且无格式错误。

## T27: 用 tmux 验证 fixture MCP 两阶段流程

**文件：** `tests/e2e_mock_openai_server.py`、`tests/fixtures/mcp_stdio_server.py`、`tests/fixtures/mcp_http_server.py`
**依赖：** T22、T26

**步骤：**
1. 在独立 tmux session 中启动 mock OpenAI、stdio/HTTP MCP fixture 和 JulyCode。
2. 发送普通代码请求，检查首轮请求日志只含检索入口和非 MCP 工具，不含远端完整定义。
3. 发送真实对话请求“调用 remote_demo 的 echo 工具返回 lazy-mcp”，观察先调用检索工具，下一迭代只加载命中候选，再调用 `remote_demo__echo`。
4. 再发送普通请求，确认新轮次远端候选恢复为 0。

**验证：** 运行 `tmux capture-pane -p -S -300 -t mcp-lazy-e2e`，期望依次看到普通回复、`search_mcp_tools`、`remote_demo__echo`、成功结果以及下一轮不携带旧候选的请求日志。

## T28: 用 tmux 验证真实 GitHub MCP

**文件：** 无代码改动
**依赖：** T27

**步骤：**
1. 使用当前已配置并授权的 GitHub MCP，在 tmux 中启动 JulyCode。
2. 先发送与 GitHub 无关的真实请求，记录输入 token 和工具列表，确认没有 `github__*` 完整定义。
3. 发送只读真实请求“查询当前认证的 GitHub 用户信息”，观察模型先检索并只加载相关候选，再调用命中的 GitHub 工具。
4. 请求完成后发送另一段普通代码问题，确认 GitHub 候选没有跨轮保留。
5. 对照 `checklist.md` 记录 tmux 输出、请求工具数量、调用顺序和最终回复证据。

**验证：** 运行 `tmux capture-pane -p -S -400 -t mcp-lazy-github`，期望看到检索→有限候选→GitHub 只读调用→轮次清理的完整链路。

## T29: 清理 E2E 环境并检查工作区

**文件：** 无代码改动
**依赖：** T27、T28

**步骤：**
1. 关闭两个 E2E tmux session及其 fixture/mock 子进程。
2. 删除测试期间生成的临时配置、请求日志和输出文件，不删除用户原有配置或凭据。
3. 检查没有遗留 JulyCode、fixture、mock Provider 或 OAuth callback 进程。
4. 检查 Git 工作区只包含本功能预期文件。

**验证：** 运行 `tmux ls`、`ps -ef | rg "mcp_(stdio|http)_server|e2e_mock_openai_server|julycode"` 和 `git status --short`，期望没有 E2E 残留进程/会话，工作区只显示预期改动。

## 执行顺序

```text
T1 → T2
T1 → T3 → T4 → T5 → T6
T3 + T5 → T7
T2 + T4 + T7 → T8
T4 + T8 → T9 → T10 → T11
T9 → T12
T2 + T8 + T10 + T12 → T13 → T14
T13 → T15 → T16 → T17
T16 → T18
T9 + T13 + T16 → T19
T2 + T7 + T13 → T20
T7 + T13 → T21
T7 + T13 → T22
T19 + T20 → T23
T1-T23 → T24
T17 + T18 + T21 + T24 → T25
T24 + T25 → T26
T22 + T26 → T27 → T28
T27 + T28 → T29
```
