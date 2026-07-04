# MCP 工具延迟加载 Plan

## 架构概览

本功能在现有 MCP Manager、工具注册表和 Agent Loop 之间增加三层能力：本地工具目录与检索、延迟可见性策略、轮次级激活状态。

MCP Server 的连接和 `tools/list` 流程保持不变。MCP Manager 在启动时仍保存所有远端定义，并把可执行的 `RemoteMcpTool` 注册到共享 `ToolRegistry`；区别是远端工具使用新的 `deferred` 可见性，默认不会进入 Provider 请求。Manager 另外注册一个固定的轻量检索工具，并维护预规范化的本地检索目录。

每个 `AgentLoopRunner` 从 MCP Manager 创建独立的 `McpTurnState`。检索工具本身无网络访问且不保存激活状态，只返回最多 5 个紧凑候选。Agent Loop 在工具执行结束后消费检索结果，用当前模式、Skill 白名单、子 Agent 过滤器和团队 Gate 再过滤候选，并用最终候选替换该 Runner 的上一批激活工具。下一次迭代重新计算 `ToolPolicy` 时，只有这些 `deferred` 工具会进入提示词、上下文估算和 Provider payload。

激活状态不写入共享 `ToolRegistry`，因此主 Agent、独立 Skill、子 Agent和团队成员可共享 MCP 会话与远端工具实例，同时拥有互不影响的候选集合。每个 Runner 在轮次开始时清空状态，并在覆盖所有退出路径的 `finally` 中再次清空；即使上次异步生成没有正常收尾，下一个轮次的入口清理也能阻止状态泄漏。

启动流程：

```text
McpManager.initialize()
  → MCP initialize + tools/list
  → McpToolCatalog.replace_server()
  → register_tools(ToolRegistry)
      → RemoteMcpTool(visibility=deferred)
      → SearchMcpToolsTool(轻量、system、read_only)
```

单轮调用流程：

```text
用户请求
  → McpTurnState.begin_turn()，激活集合为空
  → ToolPolicy 过滤所有 deferred 工具
  → 模型只看到 search_mcp_tools + 非 MCP 工具
  → search_mcp_tools(query, server?)
  → 本地目录确定性排序，返回最多 5 个紧凑候选
  → McpTurnState 用当前策略过滤并替换激活集合
  → 下一次迭代 ToolPolicy 放行激活的 deferred 工具
  → ContextManager 和 Provider 只接收实际放行集合
  → 模型调用 server__tool，沿用现有权限与 MCP 执行链
  → McpTurnState.end_turn() 清空
```

## 核心数据结构

### ToolVisibility

```python
ToolVisibility = Literal["model", "system", "deferred"]
```

- `model`：沿用现状，正常参与模式、白名单和 Gate 过滤。
- `system`：沿用现状，作为系统级入口保留。
- `deferred`：工具已注册且可以执行，但只有名称存在于当前 Runner 的激活集合时才允许向模型暴露或调用。

`RemoteMcpTool` 改用 `deferred`。其他现有工具不改变可见性。

### McpSearchDocument

```python
@dataclass(frozen=True)
class McpSearchDocument:
    definition: McpToolDefinition
    normalized_name: str
    normalized_title: str
    normalized_description: str
    name_tokens: frozenset[str]
    title_tokens: frozenset[str]
    description_tokens: frozenset[str]
```

表示一项预规范化的本地检索文档。完整参数 Schema 只保留在 `definition` 中，不进入检索工具结果。

### McpToolMatch

```python
@dataclass(frozen=True)
class McpToolMatch:
    global_name: str
    server_name: str
    remote_name: str
    title: str | None
    summary: str
    score: int
```

`summary` 折叠连续空白并限制为 160 个字符；没有说明时回退到标题或远端工具名。`score` 只用于本地排序，不发送给模型。

### McpToolSearchResult

```python
McpToolSearchStatus = Literal[
    "ok",
    "no_match",
    "server_not_found",
    "server_unavailable",
    "policy_filtered",
]

@dataclass(frozen=True)
class McpToolSearchResult:
    status: McpToolSearchStatus
    query: str
    server_name: str | None
    matches: tuple[McpToolMatch, ...] = ()
    activated_tools: tuple[str, ...] = ()
    message: str = ""
```

搜索阶段先产生 `matches`。Agent Loop 根据当前 `ToolPolicy` 计算真正能暴露的工具后填充 `activated_tools`；若所有候选均被 Plan Mode、Skill、子 Agent 或团队策略过滤，状态改为 `policy_filtered`。

### McpToolSearchProvider

```python
class McpToolSearchProvider(Protocol):
    def search_tools(
        self,
        query: str,
        server_name: str | None = None,
    ) -> McpToolSearchResult: ...
```

轻量检索工具只依赖该协议，不直接导入 `McpManager`。Manager 实现协议并负责结合配置、连接和 OAuth 状态区分未知 Server 与不可用 Server，避免 `manager.py` 和 `tools.py` 形成循环依赖。

### McpToolCatalog

```python
class McpToolCatalog:
    def replace_server(
        self,
        server_name: str,
        definitions: tuple[McpToolDefinition, ...],
    ) -> None: ...

    def set_searchable(self, global_names: set[str]) -> None: ...
    def remove_server(self, server_name: str) -> None: ...
    def get(self, global_name: str) -> McpToolDefinition | None: ...
    def server_summaries(self) -> tuple[McpServerToolSummary, ...]: ...
    def search(
        self,
        query: str,
        *,
        server_name: str | None = None,
        limit: int = 5,
    ) -> tuple[McpToolMatch, ...]: ...
```

Catalog 是工具发现结果和检索索引的单一来源。`replace_server()` 原子替换目标 Server 的定义和预规范化文档；`set_searchable()` 只允许已成功注册到 `ToolRegistry` 的工具参与检索；`remove_server()` 同时移除定义、索引和可检索标记。

### McpServerToolSummary 与 McpPromptContext

```python
@dataclass(frozen=True)
class McpServerToolSummary:
    name: str
    tool_count: int


@dataclass(frozen=True)
class McpPromptContext:
    connected_servers: tuple[McpServerToolSummary, ...] = ()
```

运行时提示只展示已连接 Server 的名称和可检索工具数量，不列举工具名、说明或 Schema。

### McpTurnState

```python
class McpTurnState:
    def begin_turn(self) -> None: ...
    def apply_search_results(
        self,
        results: tuple[ToolResult, ...],
        *,
        policy: ToolPolicy,
        registry: ToolRegistry,
    ) -> tuple[ToolResult, ...]: ...
    def end_turn(self) -> None: ...

    @property
    def active_tools(self) -> frozenset[str]: ...

    def prompt_context(self) -> McpPromptContext: ...
```

状态实例属于单个 `AgentLoopRunner`，不属于 MCP Server 或共享注册表。`apply_search_results()` 按原始工具调用顺序处理检索结果；同一模型响应包含多个检索调用时，最后一个结果获胜。任何检索失败或空结果也会清空上一批激活工具，防止旧候选继续暴露。

### ToolPolicy

```python
@dataclass(frozen=True)
class ToolPolicy:
    mode: AgentMode
    whitelist: frozenset[str] | None = None
    filter: SubAgentToolFilter | None = None
    gates: tuple[ToolGate, ...] = ()
    activated_deferred_tools: frozenset[str] = frozenset()

    def allowed_specs(self, registry: ToolRegistry) -> tuple[ToolSpec, ...]: ...
    def validate_call(self, call: ToolCall, registry: ToolRegistry) -> ToolResult | None: ...
```

`allowed_specs()` 在现有 Plan Mode、白名单、子 Agent 和 Gate 过滤前先排除未激活的 `deferred` 工具。`validate_call()` 对模型猜测但未加载的远端工具返回 `tool_not_loaded`，不能因为它已存在于本地注册表而绕过延迟加载。

### McpLoadReport

```python
@dataclass(frozen=True)
class McpLoadReport:
    loaded_servers: tuple[str, ...] = ()
    failed_servers: dict[str, str] = field(default_factory=dict)
    discovered_tools: tuple[str, ...] = ()
    registered_tools: tuple[str, ...] = ()
    failed_tools: dict[str, str] = field(default_factory=dict)
    oauth_status: dict[str, McpOAuthStatus] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
```

`discovered_tools` 表示 `tools/list` 返回的完整目录，`registered_tools` 表示成功包装为本地延迟工具的子集。当前 Runner 的激活工具不放入共享报告，由命令状态快照从当前 Runner 读取，避免并发子 Agent 污染主 Agent 状态。

## 检索与排序设计

检索完全基于内存目录，不发起 MCP `tools/call`、额外 Provider 请求或任何网络请求。

文本规范化规则：

1. 使用 Unicode NFKC 规范化并执行 `casefold()`。
2. 将 `_`、`-`、`/`、`.`、`:` 等分隔符转为空格。
3. 折叠连续空白，提取 Unicode 字母和数字 token。
4. 去掉长度小于 2 的 token 和少量通用停用词；不维护 GitHub 专用同义词或分类表。
5. 检索工具说明提示模型优先使用能力关键词；当用户语言与远端说明语言不同时，可补充英文能力关键词。

每个唯一查询 token 只计分一次，避免重复词提高权重。评分规则固定如下：

| 命中位置 | 分值 |
|----------|------|
| 查询规范化后与远端名完全相同 | 1000 |
| 完整查询短语出现在远端名 | 300 |
| 完整查询短语出现在标题 | 180 |
| 完整查询短语出现在说明 | 80 |
| token 与远端名 token 完全匹配 | 每个 60 |
| token 与标题 token 完全匹配 | 每个 30 |
| token 与说明 token 完全匹配 | 每个 10 |
| 长度至少为 3 的 token 是远端名 token 前缀 | 每个 20 |
| 所有有效查询 token 都在名称、标题或说明中命中 | 100 |

只返回分数大于 0 的工具。排序键依次为分数降序、`server_name` 升序、`remote_name` 升序，确保相同目录和查询稳定复现。Server 名只用于显式过滤，不参与普通 token 加分，避免查询中出现 `github` 时所有 GitHub 工具获得相同高分。

首版将单次上限固定为 5，不在配置或工具参数中开放扩大上限。这样模型无法绕过上下文上限；需要其他能力时可再次检索，新结果替换上一批。

## 模块设计

### `mewcode.mcp.search`

**职责：** 定义检索数据结构、目录索引、文本规范化、稳定评分和紧凑摘要。
**对外接口：** `McpToolCatalog`、`McpToolMatch`、`McpToolSearchResult`、`McpToolSearchProvider`、`McpServerToolSummary`。
**依赖：** 标准库、`McpToolDefinition`。

索引只在 Server 工具列表变化时重建对应分片。搜索路径只遍历预规范化文档，在 1,000 项规模下避免重复处理完整 Schema。

### `mewcode.mcp.tools`

**职责：** 保留远端工具适配器，并新增轻量检索工具。
**对外接口：** `RemoteMcpTool`、`SearchMcpToolsTool`、`SEARCH_MCP_TOOLS_NAME`。
**依赖：** `McpToolSearchProvider`、`McpToolSearchResult`、现有工具基础类型。

`RemoteMcpTool.spec.visibility` 改为 `deferred`，其名称、说明、Schema、安全级别和执行逻辑保持不变。

`SearchMcpToolsTool` 接收 `McpToolSearchProvider`，使用以下固定 Schema：

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "要查找的 MCP 能力或任务意图；跨语言时可补充英文关键词"
    },
    "server": {
      "type": "string",
      "description": "可选的 MCP Server 名称"
    }
  },
  "required": ["query"],
  "additionalProperties": false
}
```

该工具为 `read_only`、`visibility="system"`，因此 Plan Mode 和 Skill 白名单下仍可检索，也不会触发权限确认。返回值仅包含状态、查询、Server、最多 5 个 `{name, server, title, summary}` 候选和激活摘要，不包含完整 Schema。实际远端工具仍为 `side_effect`，继续执行现有权限流程。

### `mewcode.mcp.scope`

**职责：** 管理单个 Agent Runner 的轮次激活集合，并把检索结果转换为下一迭代可用工具。
**对外接口：** `McpTurnState`、`McpPromptContext`。
**依赖：** `ToolPolicy`、`ToolRegistry`、`ToolResult`、`McpToolCatalog`。

状态更新时先取检索候选名称，再用“候选已激活”的临时 `ToolPolicy` 计算实际可见集合，从而保留 Plan Mode、Skill、子 Agent 和团队限制。处理后的工具结果会明确列出 `activated_tools` 和因策略被过滤的数量，模型不会误以为不可用候选已经加载。

### `mewcode.mcp.manager`

**职责：** 继续管理 Server 会话和 OAuth 生命周期，同时维护 Catalog、注册延迟工具并创建独立轮次状态。
**对外接口：**

```python
class McpManager:
    async def initialize(self) -> None: ...
    def register_tools(self, registry: ToolRegistry) -> None: ...
    def search_tools(self, query: str, server_name: str | None = None) -> McpToolSearchResult: ...
    def create_turn_state(self) -> McpTurnState: ...
    def prompt_context(self) -> McpPromptContext: ...
    def load_report(self) -> McpLoadReport: ...
```

初始化成功后把目标 Server 定义写入 Catalog。`register_tools()` 注册延迟远端工具，并只在至少配置一个 MCP Server 时注册轻量检索工具；未配置 MCP 时不增加任何工具定义开销。

Catalog 能区分以下情况：

- Server 不在配置中：`server_not_found`。
- Server 已配置但连接、授权或注册不可用：`server_unavailable`。
- Server 可用但没有相关候选：`no_match`。
- 找到候选：`ok`。

OAuth 授权成功后重新写入目录并注册延迟工具；logout、refresh 失败或授权失效时同步撤销该 Server 的可检索标记并注销对应 origin。已有 Runner 即使仍保存旧名称，下一次策略计算也会因注册表中不存在该工具而停止暴露。

### `mewcode.tools.base`

**职责：** 扩展工具可见性语义。
**对外接口：** `ToolVisibility` 增加 `deferred`。
**依赖：** 无新增依赖。

### `mewcode.tools.scheduler`

**职责：** 在现有模式、白名单和 Gate 之外执行延迟可见性过滤。
**对外接口：** `ToolPolicy.activated_deferred_tools`。
**依赖：** 现有工具类型。

检索工具是只读系统工具，沿用并发调度；若一个模型响应包含多个检索调用，Scheduler 最终结果仍按原始调用顺序排列，`McpTurnState` 依此保证最后一次替换前一次。实际 MCP 工具的调度与权限行为不变。

### `mewcode.agent`

**职责：** 把轮次激活状态接入 Agent Loop。
**对外接口：** `AgentLoopRunner` 增加可选 `mcp_manager` 参数和只读 `active_mcp_tools` 属性；`ToolAwareTurnRunner` 同步支持传入 Manager。
**依赖：** `McpManager`、`McpTurnState`。

每轮执行顺序：

1. 在追加用户消息和触发 Hook 前调用 `begin_turn()`。
2. 每次迭代用 `active_tools` 构造 `ToolPolicy`。
3. 将 `prompt_context()` 传给 `RuntimePromptContext`。
4. Scheduler 完成后、工具结果写入 `ChatSession` 前调用 `apply_search_results()`。
5. 用处理后的结果回灌模型。
6. 用覆盖正常完成、取消、上下文限制、迭代上限、未知工具和异常的外层 `finally` 调用 `end_turn()`。

Provider 请求和 ContextManager 都继续使用同一个 `allowed_tools`，因此无须增加 Provider 特例，Token 估算自然只计算实际暴露集合。

### `mewcode.prompting.base` 与 `mewcode.prompting.builder`

**职责：** 向模型提供紧凑的 MCP Server 摘要和延迟加载规则。
**对外接口：** `RuntimePromptContext.mcp_context: McpPromptContext | None`。
**依赖：** MCP prompt 数据结构仅用于类型检查。

动态运行时块增加紧凑内容：

```text
<mewcode_mcp>
MCP 工具按需加载；需要时先调用 search_mcp_tools。
已连接 Server：github(45)
</mewcode_mcp>
```

该块不列工具名和说明，并放在不可缓存运行时部分，保证 OAuth 状态变化能立即反映。当前激活工具已出现在允许工具摘要中，不重复列举。

### `mewcode.tui.app`

**职责：** 把同一个 MCP Manager 传入主 Agent、独立 Skill、子 Agent 和团队 Runner，并在状态快照中读取主 Runner 的当前激活集合。
**对外接口：** 保持现有构造参数，扩展内部 Runner 创建参数。
**依赖：** `McpManager`。

### `mewcode.skills.execution`、`mewcode.subagents.manager`、`mewcode.subagents.runtime`、`mewcode.teams.runtime`

**职责：** 为每个非主 Agent Runner 传入共享 MCP Manager，由 Runner 创建独立 `McpTurnState`。
**对外接口：** 相应 Factory 增加可选 `mcp_manager` 参数。
**依赖：** `McpManager`。

并发隔离规则：

- 主 Agent、每个子 Agent、每个团队成员和独立 Skill 各有独立激活集合。
- 共享的只有 MCP 会话、Catalog 和只读/延迟工具实例。
- Fork 子 Agent 仍受父 Agent 当时的工具白名单限制；即使检索到其他工具，策略也不会暴露。
- 后台子 Agent 的激活工具只进入它自己的 Provider 请求，不会进入后续主用户轮次。

### `mewcode.commands.models` 与 `mewcode.commands.builtin`

**职责：** 区分 MCP 已发现工具数和主 Runner 当前暴露工具数。
**对外接口：** `CommandStatusSnapshot` 增加 `mcp_active_tools: tuple[str, ...] = ()`。
**依赖：** `McpLoadReport`。

`/status` 输出示例：

```text
MCP：已连接 Server 1 个，发现工具 45 个，当前轮次暴露 3 个，失败 Server 0 个，失败工具 0 个
```

### Provider 与上下文管理

OpenAI Provider、Anthropic Provider、`ChatRequest`、`ContextManager` 和 `TokenEstimator` 不新增 MCP 分支。它们接收 `ToolPolicy.allowed_specs()` 的结果：初始为轻量检索工具，检索后为轻量检索工具加最多 5 个远端工具。Provider 仍把这些工具按统一 `ToolSpec` 序列化，TokenEstimator 对同一集合估算。

### `README.md`

**职责：** 更新 MCP 工具暴露说明，解释检索→加载→调用流程、单次 5 个上限、轮次结束清理、状态观察和 Provider 无关性。
**对外接口：** 用户文档。
**依赖：** 无。

## 模块交互

### 正常检索与调用

1. TUI mount 初始化 MCP Manager，Manager 发现 GitHub 工具并注册为 `deferred`。
2. TUI 创建主 `AgentLoopRunner` 并传入 Manager；Runner 创建独立状态并清空激活集合。
3. `ToolPolicy.allowed_specs()` 排除所有未激活的 GitHub 工具，只保留 `search_mcp_tools` 和现有非 MCP 工具。
4. 模型调用 `search_mcp_tools(query="authenticated GitHub user profile", server="github")`。
5. Search Tool 在 Catalog 中评分，返回 `github__get_me` 等最多 5 个紧凑候选。
6. Scheduler 完成后，Runner 让 `McpTurnState` 以当前策略过滤候选并替换激活集合。
7. 下一次迭代的 `allowed_specs()` 包含 `github__get_me` 的完整说明和 Schema。
8. 模型调用该工具；Scheduler、权限系统、Hook、Executor 和 MCP Session 沿用现有链路。
9. Runner 完成或终止后清空状态；下一主用户轮次再次从零个远端工具开始。

### 重复检索

1. 当前激活集合为 `{github__get_me}`。
2. 模型调用检索工具查找 Pull Request 评论能力。
3. 搜索返回新的最多 5 个候选。
4. 状态以新集合整体替换 `{github__get_me}`，不做并集。
5. 下一请求不再包含 `github__get_me`，除非它再次命中。

### OAuth 失效

1. OAuth 状态进入 `authorization_required` 或 `refresh_failed`。
2. Manager 立即撤销目标 Server 的 searchable 标记，并按 origin 注销其 deferred 工具。
3. 后续搜索返回 `server_unavailable`；现有状态中的旧名称因注册表缺失而无法暴露或执行。
4. 授权成功后 Manager 重新初始化、替换 Catalog 分片并注册 deferred 工具，无需重启。

## 文件组织

```text
mewcode/
├── src/mewcode/tools/base.py              — 增加 deferred 可见性
├── src/mewcode/tools/scheduler.py         — 激活集合过滤与未加载调用拒绝
├── src/mewcode/mcp/search.py              — 新建：Catalog、规范化、评分、结果结构
├── src/mewcode/mcp/scope.py               — 新建：Runner 轮次激活状态
├── src/mewcode/mcp/tools.py               — 检索工具、远端工具 deferred 标记
├── src/mewcode/mcp/manager.py             — Catalog 同步、延迟注册、状态工厂和报告
├── src/mewcode/mcp/__init__.py            — 导出新增公共类型
├── src/mewcode/prompting/base.py           — RuntimePromptContext 增加 MCP 摘要
├── src/mewcode/prompting/builder.py        — 生成紧凑 MCP 运行时提示
├── src/mewcode/agent.py                    — 轮次状态接入、结果消费和 finally 清理
├── src/mewcode/tui/app.py                  — 传递 Manager、状态读取
├── src/mewcode/skills/execution.py         — 独立 Skill Runner 使用独立 MCP 状态
├── src/mewcode/subagents/manager.py        — 向子 Agent Factory 传递 Manager
├── src/mewcode/subagents/runtime.py        — 子 Agent Runner 使用独立 MCP 状态
├── src/mewcode/teams/runtime.py            — 团队成员 Runner 使用独立 MCP 状态
├── src/mewcode/commands/models.py          — 状态快照增加当前激活工具
├── src/mewcode/commands/builtin.py         — /status 区分发现数和暴露数
├── tests/test_mcp_search.py                — 新建：检索、排序、边界、性能和占用
├── tests/test_mcp_tools.py                 — 检索 ToolSpec、紧凑结果、远端可见性
├── tests/test_mcp_manager.py               — 目录同步、注册、OAuth 和报告
├── tests/test_tool_scheduler.py            — deferred 隐藏、激活和直接调用拒绝
├── tests/test_agent.py                     — 两阶段调用、替换和全部终止路径清理
├── tests/test_prompting.py                 — Server 名称/数量摘要且不泄露目录
├── tests/test_context_estimator.py         — 只估算实际暴露工具集合
├── tests/test_commands.py                  — /status 发现数和暴露数
├── tests/test_subagents.py                 — 并发 Runner 激活集合隔离
├── tests/test_team_runtime.py              — 团队成员激活集合隔离与 Gate 保留
├── tests/test_openai_provider.py           — 候选工具序列化回归
├── tests/test_anthropic_provider.py        — 候选工具序列化回归
├── tests/test_tui_smoke.py                 — Manager 传递、状态和 OAuth 动态更新
├── tests/e2e_mock_openai_server.py         — mock 模型先检索再调用 MCP 工具
├── _estimate_tokens.py                     — 对比全量与延迟加载占用
└── README.md                               — 延迟加载行为与限制
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 首次发现方式 | 模型显式调用轻量检索工具 | 不需要宿主猜测用户意图；错误时模型可以调整查询；只增加一次模型迭代。 |
| 远端工具存放 | 完整工具仍注册在本地，但标记为 `deferred` | 复用现有 Executor、权限、Hook 和 MCP Session；只改变模型可见集合。 |
| 激活状态位置 | 每个 `AgentLoopRunner` 独立持有 | 共享注册表不发生动态增删，避免主 Agent、后台任务和团队成员并发污染。 |
| 激活生命周期 | 当前 Runner 的当前用户轮次 | 严格限制长期上下文占用，符合已确认需求；后续追问重新检索。 |
| 重复检索 | 新候选替换旧候选 | 提供固定上界，避免同一轮多次检索重新堆积全部工具。 |
| 候选上限 | 首版固定为 5 | 当前 GitHub 工作流通常可由少量相关工具覆盖；固定上限防止模型或配置绕过上下文目标。 |
| 检索算法 | 本地确定性加权词法检索 | 无外部依赖、无隐私外发、延迟可控；模型可使用英文能力关键词弥补跨语言描述。 |
| 索引字段 | 远端名、标题、说明 | 覆盖 Spec 要求且避免完整 Schema 文本使通用参数词主导排序。 |
| 检索工具安全性 | `read_only + system` | 检索只读本地目录，应在 Plan Mode、Skill 和角色白名单下可用且不触发权限确认。 |
| 实际 MCP 工具安全性 | 保持 `side_effect` | 不扩大现有权限边界；即使远端操作看似只读也继续沿用当前保守策略。 |
| Provider 接入 | Provider 无感知 | 使用统一 `ToolSpec` 和现有序列化，保证 OpenAI/Anthropic 语义一致。 |
| 未配置 MCP | 不注册检索工具 | 空配置保持现有请求完全不变，不为不存在的能力增加上下文。 |
| 配置项 | 首版不新增搜索配置 | 已批准范围只有固定默认行为；YAGNI，避免引入可破坏上限的配置。 |

## 需求覆盖

| 需求 | 设计覆盖 |
|------|----------|
| F1 | 启动仍执行完整初始化和工具发现；Catalog 保存原始定义与 Schema |
| F2 | deferred 默认隐藏；Search Tool 固定暴露；McpPromptContext 只列 Server 名和数量 |
| F3 | Catalog 对名称、标题、说明执行自然语言关键词检索并支持 Server 过滤 |
| F4 | 固定 Top 5、紧凑摘要、Agent Loop 自动激活后在下一迭代暴露完整定义 |
| F5 | McpTurnState 对每个检索结果执行整体替换，不做累加 |
| F6 | 搜索状态区分无匹配、未知 Server、不可用 Server 和策略过滤，不回退全量暴露 |
| F7 | RemoteMcpTool 执行链保持不变，仅改变 visibility |
| F8 | begin_turn 入口清理 + 覆盖所有退出路径的 finally 清理 |
| F9 | Manager 在授权、logout 和授权失效时同步 Catalog searchable 状态与注册表 origin |
| F10 | Search Tool 为本地 read_only/system；Remote Tool 继续经过完整 ToolPolicy、权限、调度和 Hook |
| F11 | 现有工具 visibility 不变；各类 Runner 只新增独立 MCP 状态 |
| F12 | Provider 接收同一 allowed_tools 集合，无专有协议分支 |
| F13 | McpLoadReport 记录发现目录；CommandStatusSnapshot 记录当前主 Runner 激活集合 |
| F14 | ContextManager 与 Provider 共用过滤后的 allowed_tools，TokenEstimator 无全目录旁路 |
