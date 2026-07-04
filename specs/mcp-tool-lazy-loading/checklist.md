# MCP 工具延迟加载 Checklist

> 每一项都必须通过运行命令或观察实际行为验证；不得仅凭代码阅读判定通过。

## Spec 验收标准

- [x] AC1 初始请求只暴露轻量检索能力：配置 45 个以上 MCP 工具时，Agent 首次请求包含 `search_mcp_tools`，不包含任何 `github__*` 完整定义。（验证：运行 `python -m pytest tests/test_agent.py -k "mcp_lazy_initial" -q`，检查捕获的首个 ChatRequest 工具名）
- [x] AC2 模型可完成“自然语言检索 → 最多 5 个完整候选 → 调用远端工具 → 回灌结果”的两阶段流程。（验证：运行 `python -m pytest tests/test_agent.py -k "mcp_lazy_call" -q`，检查相邻请求工具集合、Fake MCP 调用记录和最终工具消息）
- [x] AC3 同一轮第二次检索整体替换第一批候选，不做并集，任一请求中的 deferred MCP 工具不超过 5 个。（验证：运行 `python -m pytest tests/test_agent.py tests/test_mcp_search.py -k "mcp_lazy_replacement or replacement" -q`）
- [x] AC4 无匹配、未知 Server 和不可用 Server 分别返回结构化可恢复状态，模型可重试且不会获得完整目录。（验证：运行 `python -m pytest tests/test_mcp_manager.py tests/test_agent.py -k "no_match or server_not_found or server_unavailable" -q`）
- [x] AC5 正常完成、主动取消、Task cancellation、迭代上限、上下文限制、连续未知工具和 Provider 异常后，下一用户轮次均从零个远端候选开始。（验证：运行 `python -m pytest tests/test_agent.py -k "mcp_lazy_cleanup" -q`）
- [x] AC6 OAuth 授权成功后目录立即可检索和调用；logout、授权失效或 refresh 失败后目标 Server 工具不再可检索或暴露，其他 Server 不受影响。（验证：运行 `python -m pytest tests/test_mcp_manager.py tests/test_mcp_oauth_flow.py -k "oauth and (search or catalog or logout or refresh)" -q`）
- [x] AC7 检索工具不触发权限确认或 MCP 业务调用；实际远端工具仍经过 Plan Mode、权限规则、Scheduler 和 Hook。（验证：运行 `python -m pytest tests/test_tool_scheduler.py tests/test_agent.py -k "search_mcp or mcp_lazy_policy" -q`，检查 Fake Session 与 Hook/Permission 事件）
- [x] AC8 内置、system、Skill、子 Agent和团队工具的可见性与调用行为无回归，各 Runner 的 MCP 候选互不污染。（验证：运行 `python -m pytest tests/test_skills.py tests/test_subagents.py tests/test_team_runtime.py tests/test_team_e2e.py -q`）
- [x] AC9 相同目录和查询的排序稳定；预建 1,000 个工具索引后的单次搜索低于 100 毫秒。（验证：运行 `python -m pytest tests/test_mcp_search.py -k "deterministic or performance" -q`）
- [x] AC10 OpenAI 与 Anthropic 初始请求和激活后请求包含语义一致的工具名称、说明和 Schema，且不泄露内部 deferred/origin 字段。（验证：运行 `python -m pytest tests/test_openai_provider.py tests/test_anthropic_provider.py -k "mcp or deferred" -q`）
- [x] AC11 `/status` 同时显示已发现工具数和当前轮次暴露数；TokenEstimator 只统计实际 `allowed_tools`。（验证：运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py tests/test_context_estimator.py -k "mcp" -q`）
- [x] AC12 当前 GitHub 45 工具样本在未使用 MCP 时，MCP 工具定义估算占用较全量暴露至少下降 90%。（验证：运行 `python _estimate_tokens.py`，检查输出包含 full、idle lazy、active lazy 三组结果且 idle lazy 降幅 `>= 90%`）
- [x] AC13 未配置 MCP、全部 Server 不可用及普通非 MCP 对话均正常完成，全量测试无回归。（验证：运行 `python -m pytest -q`，并运行 `python -m pytest tests/test_mcp_manager.py tests/test_agent.py -k "no_mcp or unavailable or plain" -q`）
- [x] AC14 tmux 真实对话呈现完整链路：普通请求不加载 GitHub，GitHub 请求先检索再加载有限候选并成功调用，新请求恢复零候选。（验证：运行 `tmux capture-pane -p -S -400 -t mcp-lazy-github`，对照工具事件、请求日志、状态和最终回复）

## 实现完整性

- [x] `RemoteMcpTool` 已注册为 `deferred`，名称、原始说明、完整 Schema、`side_effect` 和 `mcp:<server>` origin 保持不变。（验证：运行 `python -m pytest tests/test_mcp_tools.py -k "remote_mcp_tool_exposes" -q`）
- [x] `ToolPolicy` 默认隐藏未激活 deferred 工具，允许激活工具，并对模型猜测的未加载工具返回 `tool_not_loaded`。（验证：运行 `python -m pytest tests/test_tool_scheduler.py -k "deferred" -q`）
- [x] Catalog 按 Server 原子替换和移除目录，只检索成功注册工具，同时完整保留名称、标题、说明和参数 Schema。（验证：运行 `python -m pytest tests/test_mcp_search.py -k "catalog" -q`）
- [x] 检索规范化和固定评分覆盖名称、标题、说明、前缀与稳定 tie-break，Server 名不会让同 Server 全部工具获得无意义加分。（验证：运行 `python -m pytest tests/test_mcp_search.py -k "normalize or ranking or deterministic" -q`）
- [x] 检索结果最多返回 5 个 160 字符以内的紧凑摘要，不包含 score、参数 Schema 或完整目录。（验证：运行 `python -m pytest tests/test_mcp_tools.py tests/test_mcp_search.py -k "compact or summary or limit" -q`）
- [x] `search_mcp_tools` 的输入仅为必填 `query` 和可选 `server`，规格为 `read_only + system`，未配置 MCP 时不注册该入口。（验证：运行 `python -m pytest tests/test_mcp_tools.py tests/test_mcp_manager.py -k "search_mcp or empty" -q`）
- [x] `McpTurnState` 在 begin/end 时清空、按最后一次检索替换候选，并与其他状态实例隔离。（验证：运行 `python -m pytest tests/test_mcp_search.py -k "turn_state or replacement or isolation" -q`）
- [x] Manager 报告能区分发现工具、成功注册工具和失败工具，错误、搜索摘要及 OAuth 状态继续脱敏。（验证：运行 `python -m pytest tests/test_mcp_manager.py -k "report or redacted" -q`）
- [x] 运行时 MCP 提示只包含延迟加载规则及已连接 Server 的名称/数量，不包含工具目录、说明或 Schema。（验证：运行 `python -m pytest tests/test_prompting.py -k "mcp" -q`）
- [x] README 已说明 `search_mcp_tools`、Top-5 上限、替换规则、轮次生命周期、权限行为和 `/status` 指标。（验证：运行 `rg -n "search_mcp_tools|按需加载|最多 5|当前用户轮次|发现工具|当前轮次暴露|server__tool" README.md`）

## 架构与集成

- [x] MCP 启动仍执行 initialize、`tools/list` 和完整本地注册；延迟的是模型可见性，不是 Server 连接或目录发现。（验证：运行 `python -m pytest tests/test_mcp_client.py tests/test_mcp_manager.py -k "initialize or register" -q`）
- [x] ContextManager、PromptBuilder 和 Provider 使用同一份 `ToolPolicy.allowed_specs()` 结果，不存在绕过策略的全目录估算或序列化路径。（验证：运行 `python -m pytest tests/test_agent.py tests/test_context_estimator.py -k "mcp" -q`，比较捕获请求与 footprint 工具集合）
- [x] 搜索路径只访问内存 Catalog，不调用 Provider、网络接口或 MCP `tools/call`。（验证：运行 `python -m pytest tests/test_mcp_tools.py tests/test_mcp_search.py -k "local_only or no_remote_call" -q`，检查 Fake Provider/Transport/MCP Session 调用计数为 0）
- [x] 主 Agent、独立 Skill、前后台子 Agent和团队成员共享 MCP 会话/Catalog，但每个 Runner 分别持有 active names。（验证：运行 `python -m pytest tests/test_tui_smoke.py tests/test_subagents.py tests/test_team_runtime.py -k "mcp and isolation" -q`）
- [x] Fork 继承、角色 allow/deny、background allow、团队 Role Gate 和 Approval Gate 在候选激活后仍有效。（验证：运行 `python -m pytest tests/test_subagents.py tests/test_team_runtime.py tests/test_tool_scheduler.py -k "mcp and (filter or gate or policy)" -q`）
- [x] OAuth 状态变化仅更新目标 Server 的 Catalog 分片和 origin，不移除检索入口、内置工具或其他 Server 工具。（验证：运行 `python -m pytest tests/test_mcp_manager.py -k "oauth and isolated" -q`）
- [x] OpenAI 与 Anthropic Provider 无新增 MCP 专有分支，仍通过统一 `ToolSpec` 发送实际允许集合。（验证：运行 `python -m pytest tests/test_openai_provider.py tests/test_anthropic_provider.py -k "mcp or tool" -q`）

## 性能、安全与质量

- [x] 1,000 工具规模检索满足 100 毫秒上限，结果数量和顺序稳定。（验证：运行 `python -m pytest tests/test_mcp_search.py -k "performance or deterministic" -q`）
- [x] GitHub 样本空闲请求工具定义占用降低至少 90%，激活状态只增加检索入口和最多 5 个候选的 Schema。（验证：运行 `python _estimate_tokens.py` 并运行 `python -m pytest tests/test_context_estimator.py -k "mcp" -q`）
- [x] 检索结果、状态、异常和测试日志不包含 Header、环境变量、OAuth token、client secret 或 PKCE verifier。（验证：运行 `python -m pytest tests/test_mcp_manager.py tests/test_mcp_oauth_flow.py tests/test_tui_smoke.py -k "secret or redacted or oauth" -q`，并确认输出无测试密钥原文）
- [x] 新增模块和 mock E2E 服务可编译导入。（验证：运行 `python -m compileall -q src tests && python -c "from mewcode.mcp.search import McpToolCatalog; from mewcode.mcp.scope import McpTurnState"`）
- [x] 项目没有配置 lint 工具时，至少保证全量测试和 diff 格式检查通过。（验证：运行 `python -m pytest -q && git diff --check`）
- [x] 工作区不存在无关改动、调试输出、临时配置、请求日志或凭据文件。（验证：运行 `git status --short` 并逐项对照 `task.md` 文件清单）

## 端到端场景

- [x] 场景 1：fixture 普通请求——在 `mcp-lazy-e2e` tmux session 启动 mock OpenAI、stdio/HTTP MCP fixture 和 MewCode，发送普通代码问题；首个请求只有非 MCP 工具和 `search_mcp_tools`，没有 `local_demo__*` 或 `remote_demo__*`。（验证：检查 mock 请求日志并运行 `tmux capture-pane -p -S -200 -t mcp-lazy-e2e`）
- [x] 场景 2：fixture 延迟调用——发送“调用 remote_demo 的 echo 工具返回 lazy-mcp”；界面依次显示 `search_mcp_tools`、下一迭代有限候选、`remote_demo__echo` 和成功回复。（验证：`tmux capture-pane -p -S -300 -t mcp-lazy-e2e` 与 mock 请求日志同时证明调用顺序和候选数 `<= 5`）
- [x] 场景 3：fixture 跨轮清理——完成场景 2 后发送普通问题；新请求不再包含 `remote_demo__echo`，`/status` 显示当前轮次暴露为 0。（验证：检查最后一次请求工具列表和 `tmux capture-pane -p -S -300 -t mcp-lazy-e2e`）
- [x] 场景 4：真实 GitHub MCP 空闲请求——在 `mcp-lazy-github` tmux session 启动 MewCode，发送与 GitHub 无关的代码问题；请求中无任何 `github__*` 完整定义。（验证：检查 Provider 请求日志/调试观测和 `tmux capture-pane -p -S -200 -t mcp-lazy-github`）
- [x] 场景 5：真实 GitHub MCP 只读调用——发送“查询当前认证的 GitHub 用户信息”；MewCode 先检索，下一迭代只加载最多 5 个相关工具，随后调用命中的只读 GitHub 能力并生成有效回复。（验证：`tmux capture-pane -p -S -400 -t mcp-lazy-github` 包含检索、GitHub 工具调用、成功结果和最终回复）
- [x] 场景 6：真实 GitHub 跨轮清理——场景 5 结束后再发送普通代码问题；新请求恢复为零个 `github__*` 候选。（验证：检查最后一次 Provider 请求工具列表，并在 `/status` 中观察当前轮次暴露为 0）
- [x] 场景 7：OAuth 动态更新——使用本地 `oauth_demo` fixture，未授权时检索得到 `server_unavailable`；授权后无需重启即可检索；logout 后再次不可检索且其他工具继续可用。（验证：在 fixture tmux 执行 `/mcp auth oauth_demo`、工具检索和 `/mcp logout oauth_demo`，捕获输出并对照 `/status`；不修改用户真实 GitHub 凭据）
- [x] 场景 8：无 MCP 配置——移除测试配置中的 `mcp_servers` 后启动 MewCode，发送读取 README 请求；不出现 `search_mcp_tools`，内置 `read_file` 正常调用并回复。（验证：检查 mock 请求日志和 tmux 输出）
- [x] 场景 9：验收环境清理——关闭 tmux session、fixture、mock Provider 和 MewCode，不保留临时配置、请求日志或凭据。（验证：运行 `tmux ls`、`ps -ef | rg "mcp_(stdio|http)_server|e2e_mock_openai_server|mewcode"` 和 `git status --short`）

## 验收记录（2026-07-04）

- [x] 自动化回归：`python -m pytest -q`，750 项全部通过。
- [x] 编译与导入：`python -m compileall -q src tests` 及新增 Catalog/TurnState 导入通过。
- [x] 格式检查：`git diff --check` 通过。
- [x] Token 指标：45 工具全量约 9368 tokens，空闲延迟加载约 96 tokens，下降 99.0%；激活 5 个候选约 1348 tokens。
- [x] fixture tmux：普通请求无远端定义；MCP 请求按 `search_mcp_tools → remote_demo__echo` 执行；下一轮远端候选清零。
- [x] 真实 GitHub tmux：发现 44 个工具；普通请求不加载 MCP；只读用户查询按“先检索、后命中工具”执行；结束后 `/status` 显示当前轮次暴露 0 个。
- [x] OAuth tmux：未授权返回 `server_unavailable`；授权后无需重启即可按需调用 `oauth_demo__echo`；logout 后立即恢复不可检索，其他 Server 保持可用。
- [x] 无 MCP tmux：Provider 两次请求仅含内置工具，不注册 `search_mcp_tools`，`read_file` 正常完成。
- [x] 环境清理：验收 tmux Server、fixture 进程、临时配置、请求日志、凭据和本次生成的会话/工具结果均已移除。

