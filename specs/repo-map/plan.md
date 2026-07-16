# MewCode Repo Map 技术方案

## 1. 方案概览

Repo Map 作为主 Agent 的“请求期生成上下文”接入现有 Agent Loop，而不是写入系统身份、聊天历史或会话持久化。整体链路分为五层：

1. 仓库发现与内容指纹：确定项目身份、候选 Python 文件和工作区 revision。
2. Python 解析与关系图：提取安全符号信息，构建确定性的启发式文件关系图。
3. 请求相关排序与预算渲染：结合当前请求提示，在 ContextManager 授予的预算内生成完整原子条目。
4. Agent Loop 生命周期：在每个 workspace revision 下复用快照，源码副作用工具执行后刷新 revision。
5. Provider 序列化：长期稳定前缀在前，Repo Map 快照在后；Anthropic 使用快照级显式缓存边界，OpenAI MVP 保持自动缓存兼容。

首版只服务当前主 Agent。Skill 隔离执行、子 Agent、团队成员和独立 Worktree Agent 不注入 Repo Map。

## 2. 架构与依赖边界

### 2.1 新增核心包

新增 `src/mewcode/repo_map/`，包含以下模块：

| 模块 | 职责 |
| --- | --- |
| `models.py` | 仓库身份、文件指纹、解析结果、图节点、快照、状态等不可变数据模型 |
| `discovery.py` | Git/非 Git 根目录识别、ignore 规则、候选文件发现、内容读取与 SHA-256 指纹 |
| `parser.py` | 基于 `ast` 与 `tokenize` 的 Python 符号、导入和引用提取，以及不可信内容清理 |
| `graph.py` | 可确定关系解析、歧义降级、确定性的文件关系图与 PageRank-like 重要度 |
| `ranking.py` | 当前请求提示提取、请求相关性加权和稳定最终排序 |
| `renderer.py` | 原子条目组织、短签名降级、完整块 Token 预算与确定性文本输出 |
| `manager.py` | 后台索引、三层内存缓存、revision 生命周期、快照复用、失效和状态观测 |
| `__init__.py` | 仅导出稳定公共接口，不暴露内部缓存实现 |

依赖方向保持单向：

```text
discovery ─┐
parser ────┼─> graph ─> ranking ─> renderer
models ────┘                         │
          manager <──────────────────┘

AgentLoop / TUI / ContextManager ─> RepoMapManager
Provider adapters ─> PromptBundle.GeneratedContextBlock
ToolCallScheduler ─> 通用 ToolExecutionObserver 协议
```

`repo_map` 核心不依赖 Provider、ChatSession 或 TUI。渲染器接收 Token 计数回调，不反向依赖 ContextManager。`ToolCallScheduler` 只声明通用观察者协议，不导入 Repo Map，避免工具层与索引层形成循环依赖。

### 2.2 关键数据模型

在 `repo_map/models.py` 定义冻结 dataclass：

```python
RepositoryIdentity(
    root: Path,
    repo_id: str,
    worktree_id: str,
    head_id: str,
    is_git: bool,
)

FileFingerprint(relative_path: str, content_hash: str, size: int)
ScannedFile(fingerprint: FileFingerprint, source_bytes: bytes)

SymbolRecord(
    kind: SymbolKind,
    name: str,
    qualified_name: str,
    line_number: int,
    signature: str,
    short_signature: str,
    parent_qualified_name: str | None,
)

ImportRecord(module: str, symbol: str | None, alias: str | None, level: int, line_number: int)
ReferenceRecord(name: str, kind: ReferenceKind, line_number: int)
ParsedPythonFile(fingerprint, module_name, symbols, imports, references, diagnostics)

WorkspaceState(identity, ordered_fingerprints, revision)
RepoMapSnapshot(snapshot_id, revision, text, estimated_tokens, included_files, truncated)
RepoMapStatus(...)
```

`revision` 使用规范化 JSON 的 SHA-256 生成，输入至少包括项目/Worktree/HEAD 身份、按 POSIX 路径排序的文件路径与内容哈希、解析和建图规则版本。`snapshot_id` 额外覆盖规范化请求提示、有效预算与渲染规则版本。

## 3. 仓库发现与工作区身份

### 3.1 Git 项目

`RepositoryDiscovery` 使用参数数组调用 Git，不使用 shell 拼接：

- `git -C <cwd> rev-parse --show-toplevel` 确定当前 Worktree 根目录。
- `git -C <root> ls-files -co --exclude-standard -z -- '*.py' '*.pyi'` 获取已跟踪文件及未跟踪但未忽略文件。
- 读取 Git dir、common dir、HEAD 和 symbolic ref，组成项目、分支与 Worktree 隔离身份；兼容 detached HEAD 与 unborn branch。

候选结果二次使用 `lstat` 校验：目录和文件 symlink 均排除，规范化后的真实路径必须仍位于根目录内。最终路径统一为相对根目录的 POSIX 表示，并在读取前排序。

### 3.2 非 Git 项目

以 MewCode 启动目录为根，通过 `os.scandir` 递归发现 `.py`、`.pyi`，跳过 Spec 指定的默认目录。遍历过程中不跟随 symlink，每层结果排序后再进入下一层。

### 3.3 内容指纹

每次扫描读取原始字节并计算 SHA-256，不依赖 mtime。`ScannedFile` 同时携带本次哈希对应的字节，解析器直接消费这份内容，避免“哈希后文件又变化”导致缓存身份和解析内容不一致。

单个文件读取失败只产生诊断并排除该文件；目录或 Git 查询失败时安全退化到非 Git 范围扫描，状态中记录原因。所有发现、读取和哈希工作均通过后台线程执行。

## 4. Python 解析与安全渲染

### 4.1 编码与 AST

`PythonSymbolParser` 使用标准库 `tokenize.detect_encoding` 识别 PEP 263 编码，再将文本交给 `ast.parse`。支持：

- 顶层类、函数、异步函数；
- 类中的方法与异步方法；
- 位置参数、仅位置参数、关键字参数、变长参数和返回注解的结构化签名；
- `import`、`from ... import ...`、相对导入与 `as` 别名；
- 同模块名称使用和调用目标名称。

语法错误、编码错误或单文件解析异常写入该文件诊断，不中止其他文件。

### 4.2 不可信源码清理

签名不使用源码切片直接复制，而由 AST 白名单节点重新渲染：

- 默认值统一为 `...`；
- 字符串形式注解和无法安全表达的注解统一为 `...`；
- 不输出 docstring、注释、decorator 和函数体；
- 清理 C0/C1 控制字符，仅保留正常换行和可打印文本；
- 单行硬限制 160 个字符；超限签名切换为保留种类和限定名的 `name(...)` 短签名。

模块名根据包链推导：识别 `__init__.py`、普通包和常见 `src/` 布局。无法唯一确定包根时退化为基于相对路径的稳定模块名，不猜测运行时 `sys.path`。

## 5. 关系图与确定性排序

### 5.1 支持的关系

`RepoGraphBuilder` 建立文件级加权有向边：

- `import package.module` 唯一解析到仓库模块：权重 3；
- `from package.module import Symbol` 唯一解析到符号：权重 5；
- 相对导入和别名在规范化后按上述规则处理；
- 同模块唯一直接名称引用：权重 2；
- 唯一可解析定义的直接调用：权重 2。

权重仅表达导航价值，不宣称精确调用关系。`obj.method()`、`getattr`、字符串导入、`import *`、动态 re-export 与重复定义无法消歧时不建立精确边。歧义导入只保留诊断，不随机选择目标。

### 5.2 图重要度

首版不引入 `networkx`。使用标准库实现固定参数的 PageRank-like 迭代：

- damping factor：`0.85`；
- 最大迭代：`50`；
- 收敛阈值：`1e-12`；
- 节点和出边始终按稳定路径排序；
- 空图和无出边节点使用均匀分布回退；
- 最终图分数量化到小数点后 8 位。

上述参数纳入 `GRAPH_VERSION`。并行或任意顺序得到的解析结果必须先排序再建图。

### 5.3 当前请求相关性

`RequestHintExtractor` 只从当前用户原始请求中提取规范化路径片段、模块片段、文件名和标识符，不读取模型生成内容。初始评分建议如下，并作为 `RANKING_VERSION` 的一部分固定：

- 完整相对路径或模块精确命中：`+100`；
- 文件名精确命中：`+80`；
- 限定符号名命中：`+60`；
- 唯一裸符号名命中：`+40`；
- 图重要度：`normalized_rank * 10`。

因此明确请求提示必然优先于仅靠图重要度的候选。最终排序键固定为：

```python
(-round(score, 8), relative_posix_path, qualified_name, line_number)
```

同分、空图和重复符号均使用后面三个字段稳定回退。

## 6. Token 预算与地图格式

### 6.1 ContextManager 授权

扩展 `ContextManager.prepare_request`，增加可选的异步低优先级上下文工厂：

```python
OptionalContextFactory = Callable[
    [int],
    Awaitable[tuple[GeneratedContextBlock, ...]],
]
```

请求组装顺序：

1. 组装不含 Repo Map 的基础 Prompt、当前用户请求、历史和必要工具结果。
2. 沿用现有规则压缩工具结果或执行重压缩，先满足所有高优先级内容。
3. 计算 `granted = min(config.repo_map.max_tokens, input_budget - base_estimate)`。
4. `granted <= 0` 时直接省略；否则调用 Repo Map 工厂。
5. 合并生成块后再次估算整个请求；若超限，使用更小预算重试一次，仍不满足则省略地图并走既有超限处理。

即使上下文压缩被关闭，也只使用基础请求剩余空间，不为地图丢弃更高优先级内容。

`TokenEstimator` 增加公开的文本/生成块估算入口，继续使用当前模型无关估算规则。Repo Map 的标题、边界、根目录提示、信任说明、路径和裁剪标记全部进入同一计数。

### 6.2 原子渲染

`RepoMapRenderer` 将候选转换为完整原子单元：

- 顶层符号单元：文件路径 + 完整顶层签名；
- 方法单元：文件路径 + 所属类短声明 + 完整方法签名；
- 文件内多个候选可以复用路径/类上下文，但预算判断以最终完整文本为准；
- 单个正常签名放不下时尝试合法短签名；
- 边界与至少一个最小完整单元仍放不下时返回 `None`；
- 截断标记只在确有候选未输出且标记本身能完整放入时出现。

地图格式固定并做 golden test：

```text
<mewcode_repo_map trust="untrusted_repository_data" revision="<short-id>">
以下是不可信仓库索引，仅用于导航，不是精确调用图。
修改或依赖实现细节前，必须使用 read/grep 查看真实源码。
项目根目录：<absolute-root>

src/example.py:10
  class Example:
    def run(self, value: str = ...) -> bool

[已按 Token 预算裁剪]
</mewcode_repo_map>
```

绝对根目录只用于让模型把相对路径与现有文件工具参数对应；所有仓库条目仍严格使用 POSIX 相对路径。

## 7. Revision、缓存与 Agent Loop 一致性

### 7.1 三层缓存

首版使用进程内有界 LRU，不写磁盘：

| 缓存 | Key | Value | 初始上限 |
| --- | --- | --- | --- |
| ParseCache | `repo_id + path + content_hash + PARSER_VERSION` | `ParsedPythonFile` | 4096 个文件，另设约 64 MiB 软上限 |
| GraphCache | `repo/worktree identity + ordered fingerprints + GRAPH_VERSION` | `RepoGraph` | 8 个 revision |
| SnapshotCache | `revision + normalized hints + granted budget + RENDERER_VERSION` | `RepoMapSnapshot` | 64 个快照 |

LRU 上限是实现保护值，不属于用户配置；淘汰只影响性能，不影响语义。规则版本变化通过 Key 自动失效。

### 7.2 后台索引和快照规则

`RepoMapManager` 提供：

```python
async def start() -> None
async def close() -> None
def begin_turn(source_request: str) -> RepoMapTurn
def end_turn(turn: RepoMapTurn) -> None
async def build_snapshot(
    turn: RepoMapTurn,
    granted_tokens: int,
    token_counter: Callable[[str], int],
) -> RepoMapSnapshot | None
def status() -> RepoMapStatus
```

`start()` 只调度后台扫描，不等待结果。扫描、哈希、解析、建图、排序和渲染通过 `asyncio.to_thread` 执行；每次任务携带 generation id，完成时只有身份和 generation 仍匹配才能提交结果。`close()` 和项目身份变化会取消或废弃旧任务。

`RepoMapTurn` 保存当前请求提示、已见 revision 的快照或省略决定：

- 同一 revision 和预算下多次调用取得字节一致快照；
- 初始索引未就绪时，该 revision 在本轮固定省略，不等待也不中途补入；
- 源码副作用导致 revision 改变后，可以为新 revision 获取新快照；
- 只读工具和无文件变化的副作用不改变 revision。

### 7.3 工具执行观察者

在 `tools/scheduler.py` 增加通用协议，并让所有真正执行的工具统一经过 `_execute` 包装：

```python
class ToolExecutionObserver(Protocol):
    async def before_execute(self, call: ToolCall, spec: ToolSpec) -> object | None: ...
    async def after_execute(
        self,
        call: ToolCall,
        spec: ToolSpec,
        result: ToolResult,
        state: object | None,
    ) -> None: ...
```

规则：

- `read/glob/grep` 等 `read_only` 工具不采集基线、不更新 revision；
- 已知编辑、写入、创建和删除工具，在执行前记录目标路径，执行后重新发现并只重新解析内容变化文件；
- 任意 command 或未知写入范围的副作用工具，在执行前后比较完整候选路径集合与内容哈希；
- 命令返回失败或超时也执行后置比较，因为可能已产生部分写入；
- 权限拒绝、hook 拦截等从未真正执行的工具不触发刷新；
- 无法可靠完成差异比较时，清空当前图/快照身份并调度全仓重建。

副作用工具原本已由 Scheduler 串行执行。`after_execute` 必须在下一次模型请求组装前完成 revision 提交，保证同一 Agent Loop 不看到旧地图。

### 7.4 Agent Loop 接入

`AgentLoopRunner` 增加可选 `RepoMapManager`：

1. `run()` 开始时创建 `RepoMapTurn`，在 `finally` 中结束。
2. 每次模型迭代向 ContextManager 传入绑定当前 turn 的可选上下文工厂。
3. 快照转换为 `GeneratedContextBlock` 后加入 PromptBundle。
4. ToolCallScheduler 使用该 turn 对应的 observer。
5. 工具修改仓库后 observer 更新 revision；下一次迭代自动请求新快照。

未注入 manager 时所有新参数均为空，现有 Agent Loop 行为不变。

## 8. Prompt、历史隔离与 Provider 缓存

### 8.1 独立生成上下文类型

在 `prompting/base.py` 新增：

```python
GeneratedContextBlock(
    name: str,
    title: str,
    text: str,
    kind: str,
    provenance: Literal["generated"],
    trust: Literal["untrusted_repository_data"],
    persistence: Literal["request_ephemeral"],
    cache_scope: Literal["snapshot"],
    snapshot_id: str,
)
```

`PromptBundle` 新增 `generated_context_blocks`，不复用 `ChatMessage`。`ChatSession.build_request()` 仍只持久化用户、助手和工具消息；摘要、恢复、长期记忆和日志路径都无法接触生成块。相关测试同时断言序列化会话和恢复文件中不存在地图边界文本。

统一逻辑顺序是：

```text
长期稳定工具/提示前缀
→ Repo Map 生成上下文
→ 其他动态运行时上下文
→ ChatSession 消息
```

Provider 可以选择合法角色，但必须保留块的 generated/trust/persistence/cache_scope 元数据语义。

### 8.2 OpenAI adapter

OpenAI MVP 将 Repo Map 序列化为独立 system/developer 生成上下文块，位于现有稳定 system 内容之后、动态内容之前。`_prompt_cache_key` 与稳定前缀摘要明确排除 Repo Map。

当前 MewCode OpenAI adapter 没有可靠的“模型是否支持显式 breakpoint”能力元数据。首版不发送 `prompt_cache_options` 或 breakpoint 字段，继续使用 OpenAI 的精确前缀自动缓存，避免旧模型或兼容网关拒绝未知参数。同一快照的请求内容仍字节一致，可由自动缓存复用。adapter 预留 `supports_snapshot_cache_breakpoint(model) -> bool` 能力钩子，只有未来建立受测试的模型能力表后才启用显式断点。

Golden test 不只比较 `prompt_cache_key`，还对“tools + Repo Map 之前的消息内容”进行规范序列化和 SHA-256，证明地图启停或变化不影响长期稳定前缀。

### 8.3 Anthropic adapter

Anthropic 已有稳定 system block 的 `cache_control` 支持。开启 Prompt Cache 时：

1. 在长期稳定 system 前缀末尾保留第一个 `cache_control: {type: ephemeral}`；
2. Repo Map 作为后续独立 system content block；
3. 在 Repo Map 块设置第二个同类型断点，形成内容精确匹配的快照级缓存；
4. 动态运行时块和 messages 排在其后。

关闭缓存时不发任何 `cache_control`。Golden test 按 Anthropic 的 `tools → system → messages` 顺序计算 Repo Map 之前的规范前缀摘要，并验证地图改变只改变第二级内容。

## 9. TUI 生命周期、配置和状态

### 9.1 配置

在 `config.py` 增加：

```yaml
repo_map:
  enabled: true
  max_tokens: 2000
```

对应 `RepoMapConfig(enabled: bool = True, max_tokens: int = 2000)`。启用时 `max_tokens` 必须为正整数；项目配置继续覆盖用户配置。关闭时不启动后台索引。

### 9.2 TUI 生命周期

`MewCodeApp` 持有一个跨用户轮次复用的 `RepoMapManager`：

- 初始化根目录取工具执行上下文的启动 cwd；
- `on_mount` 调用非阻塞 `start()`；
- `on_unmount` 调用 `close()`，取消或废弃后台任务；
- 仅主 `_run_agent_command` 创建的 Runner 获取 manager；
- Skill、团队和隔离执行路径不传入 manager。

主 TUI 线程只做对象引用和状态快照读取，文件系统与 CPU 工作均在后台线程。增加事件循环 heartbeat 测试，验证 Repo Map 引入的同步工作不超过 16ms。

### 9.3 状态观测

`CommandStatusSnapshot` 增加 `RepoMapStatus`，`/status` 输出：

- enabled/state；
- root 和 revision 短 ID；
- configured/effective budget；
- 候选与入选文件数；
- truncated；
- parse/graph/snapshot cache 命中状态；
- 最近生成耗时；
- disabled、not-ready、no-python-files、budget-too-small、scan-error 等降级原因。

关闭或省略时生成新的明确状态，不沿用上次成功快照字段。

## 10. 异常与降级策略

| 场景 | 行为 |
| --- | --- |
| 功能关闭 | 不创建索引任务，不注入块，状态为 disabled |
| 无 Python 文件 | 空 revision 可观察，请求不注入块 |
| 首次索引未完成 | 当前 turn/revision 固定省略，不等待 |
| 单文件编码/语法/读取失败 | 排除该文件，保留其他文件和诊断 |
| Git 命令失败 | 记录原因并使用根目录安全扫描；仍不越界或跟随 symlink |
| 图关系构建失败 | 使用稳定路径与请求提示排序的无图降级 |
| 有效预算过小 | 整块省略，不产生半条目 |
| 缓存条目异常 | 丢弃对应层并重新计算，不传播到 Agent Loop |
| 副作用后无法确定差异 | 全仓图/快照失效并重建 |
| 后台任务属于旧项目或旧 generation | 丢弃结果，不覆盖当前状态 |
| Repo Map 内部异常 | 记录状态并继续不含地图的普通模型请求 |

Repo Map 的所有失败均不得绕过权限、修改仓库或阻断会话。

## 11. 文件变更规划

### 11.1 新增文件

- `src/mewcode/repo_map/__init__.py`
- `src/mewcode/repo_map/models.py`
- `src/mewcode/repo_map/discovery.py`
- `src/mewcode/repo_map/parser.py`
- `src/mewcode/repo_map/graph.py`
- `src/mewcode/repo_map/ranking.py`
- `src/mewcode/repo_map/renderer.py`
- `src/mewcode/repo_map/manager.py`
- `tests/test_repo_map_discovery.py`
- `tests/test_repo_map_parser.py`
- `tests/test_repo_map_graph.py`
- `tests/test_repo_map_renderer.py`
- `tests/test_repo_map_manager.py`
- `tests/test_repo_map_integration.py`
- `tests/fixtures/repo_map_golden.txt`
- `eval/repo_map_quality/__init__.py`
- `eval/repo_map_quality/models.py`
- `eval/repo_map_quality/loader.py`
- `eval/repo_map_quality/runner.py`
- `eval/repo_map_quality/report.py`
- `eval/run_repo_map_eval.py`
- `eval/cases/repo_map_quality/navigation.json`

### 11.2 修改文件

- `src/mewcode/config.py`：Repo Map 配置及校验。
- `src/mewcode/prompting/base.py`：`GeneratedContextBlock` 与 PromptBundle 扩展。
- `src/mewcode/context/estimator.py`：文本/生成块公开估算接口。
- `src/mewcode/context/manager.py`：高优先级内容完成后的可选预算授权。
- `src/mewcode/tools/scheduler.py`：统一执行包装与观察者协议。
- `src/mewcode/agent.py`：turn 生命周期、可选上下文工厂和工具 observer 接入。
- `src/mewcode/providers/openai.py`：生成块序列化、稳定 key/前缀隔离。
- `src/mewcode/providers/anthropic.py`：生成块序列化和第二级 cache breakpoint。
- `src/mewcode/tui/app.py`：Manager 生命周期、主 Agent 注入和状态提供。
- `src/mewcode/commands/models.py`：状态数据模型。
- `src/mewcode/commands/builtin.py`：`/status` 展示。
- `README.md`：配置与行为说明。
- `eval/README.md`：Repo Map 质量评测运行说明。
- 现有配置、Prompt、ContextManager、Scheduler、Agent、Provider、命令和 TUI 测试文件：补充兼容与回归断言。

实现阶段若发现现有文件名与职责不完全对应，只允许在不改变本方案依赖边界和验收语义的前提下做小范围归位；产生新的架构选择时先回到 Spec/Plan 评审。

## 12. 测试方案

### 12.1 确定性单元测试

- Discovery：Git tracked/untracked/ignored、非 Git 排除、`.pyi`、symlink 越界、POSIX 路径、分支/Worktree 身份。
- Parser：PEP 263、类/函数/异步/方法、相对导入、别名、重复符号、re-export 边界、Unicode、错误文件隔离。
- Security：docstring、注释、decorator、长默认值、字符串注解、控制字符和 160 字符限制。
- Graph/Ranking：支持关系、歧义忽略、请求提示优先、空图、同分、随机输入顺序和固定分数量化。
- Renderer：完整预算计数、方法保留类、长签名降级、超小预算整块省略和精确 golden。
- Revision/Cache：只读稳定、编辑/新建/删除、command 批量修改、相同 mtime 不同内容、解析版本/渲染版本失效、项目隔离。
- Lifecycle：初始 not-ready 固定省略、旧 generation 不提交、取消安全、单文件只重解析一次。
- History：普通完成、摘要、保存/恢复与长期记忆均不出现地图文本。
- Provider：OpenAI/Anthropic 完整请求 golden、长期前缀摘要、Anthropic 次级断点、OpenAI 兼容模型不收到显式字段。
- Status：成功、裁剪、关闭、未就绪、空仓库和错误状态字段无残留。

### 12.2 集成与性能测试

- Stub model 驱动真实 Agent Loop：首个请求包含地图，模型发起只读工具后快照不变，编辑工具后下一次模型调用出现新 revision 和新签名，最终正常回复。
- 使用现有回归套件验证普通模式、Plan Mode、权限、Skill、MCP、压缩、恢复和流式输出不变。
- 当前 MewCode 仓库预热后重复生成快照，记录至少 100 次样本并断言 P95 `< 50ms`。
- 用事件循环 heartbeat 包围初次扫描与重建，断言主线程单次延迟 `< 16ms`。
- 随机化发现顺序、解析结果顺序和 `PYTHONHASHSEED`，精确比较地图字节。

### 12.3 质量评测

新增一组不在请求中直接给出目标路径的导航任务：

- 离线计算 Repo Map 的目标文件 Top-K 命中率；
- 可选在线成对运行 enabled/disabled，统计首次读取目标文件前的 `glob/grep/read` 探索调用数；
- 输出 `results.json` 和 `report.md`；
- 不把真实模型行为阈值作为 CI 门禁。

### 12.4 tmux 端到端验收

实现和自动测试完成后，严格按项目 `AGENTS.md`：

1. 在 tmux 中启动 MewCode。
2. 输入一段未给出准确路径的真实仓库问题。
3. 观察 `/status`、Provider 请求、Repo Map 注入、工具调用、工具结果回灌和最终回复。
4. 再发送会触发真实源码修改的请求，确认同一 Agent Loop 后续调用使用新 revision。
5. 对照 `checklist.md` 逐项记录证据；真实模型选择某个固定工具不作为硬性标准。

## 13. 技术决策记录

| 决策 | 选择 | 原因与取舍 |
| --- | --- | --- |
| Python 解析 | 标准库 `ast` + `tokenize` | 满足 Python MVP、编码声明和安全结构化提取；不提前引入 Tree-sitter |
| 文件集合 | Git `ls-files -co --exclude-standard`；非 Git 安全遍历 | 同时覆盖 tracked 与未忽略 untracked，行为与仓库 ignore 一致 |
| 变化检测 | 原始内容 SHA-256 | 解决相同 mtime、批量命令和跨缓存复用边界 |
| 图算法 | 固定参数的 stdlib PageRank-like | 避免 `networkx` 依赖，并可完全控制排序与浮点量化 |
| 缓存 | 进程内三层有界 LRU | 首版满足增量解析和隔离；不引入磁盘格式迁移、锁和缓存损坏面 |
| Prompt 类型 | 独立 `GeneratedContextBlock` | 保留 provenance/trust/lifecycle/cache scope，结构上隔离 ChatSession |
| Token 分配 | ContextManager 先完成高优先级内容，再授权地图 | 直接保证 Repo Map 是最低优先级且进入整体估算 |
| 修改观察点 | ToolCallScheduler 的实际执行边界 | 可覆盖编辑、删除、command、失败后部分写入，且在下一轮模型调用前刷新 |
| OpenAI 缓存 | MVP 使用自动缓存，排除稳定 key；预留能力钩子 | 当前 adapter 无可靠显式断点能力表，避免向旧模型/兼容网关发送不支持字段 |
| Anthropic 缓存 | 稳定前缀 + Repo Map 两级 `cache_control` | adapter 已支持内容块断点，可实现同快照短期复用 |
| 后台执行 | `asyncio.to_thread` + generation id + 协作式废弃 | 不阻塞 TUI，旧项目结果不能提交；避免首版引入进程池生命周期复杂度 |
| 服务范围 | 仅主 Agent | 与 Spec 范围一致，不为子 Agent/团队/隔离 Worktree 提前设计专属生命周期 |

## 14. 需求覆盖检查

| 需求组 | 设计归属 |
| --- | --- |
| F1、F2、F3 | 配置、RepositoryDiscovery、TUI 生命周期 |
| F4、F5、F6 | PythonSymbolParser、RepoGraphBuilder、安全渲染 |
| F7 | RequestHintExtractor、确定性排序和图分数量化 |
| F8、F9、F10、F19 | ContextManager 授权、TokenEstimator、RepoMapRenderer |
| F11、F12、F13、F14 | WorkspaceState、三层缓存、RepoMapTurn、ToolExecutionObserver |
| F15、F16、F17 | GeneratedContextBlock、OpenAI/Anthropic adapter 和 Provider golden |
| F18 | PromptBundle 与 ChatSession 结构隔离、持久化回归测试 |
| F20、F21、F22 | 后台 manager、降级表、RepoMapStatus 与 `/status` |
| F23 | 全部新接口可选、既有回归套件和主 Agent 限定 |
| N1、N2 | 标准库只读索引实现 |
| N3、N4、N5、N6 | 后台线程、generation id、缓存和性能测试 |
| N7、N8、N9、N10 | 固定排序/参数、异常边界、golden 与 Provider 摘要测试 |
| N11、N12 | stub 集成、tmux 人工验收和成对质量评测 |

本方案没有新增外部运行时依赖，不修改权限模型，不让 Repo Map 进入会话持久化，也不存在核心包反向依赖 Agent/TUI/Provider 的循环。
