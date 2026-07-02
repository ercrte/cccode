# Tool Call Nonblocking Plan

## 架构概览

本次改动保持现有 Agent Loop、权限系统和工具协议不变，只把工具内部的阻塞执行点替换为不会占用主事件循环的实现。命令类子进程、纯文件系统扫描、读写和同步 fallback 放到 daemon 工作线程中执行，并通过事件循环轮询结果队列回收结果。

工具调度器继续按“只读工具并发、有副作用工具串行”的策略执行，但并发只读批次不再等待整批全部结束后才发完成事件，而是在单个任务完成后立即产出对应工具完成事件，同时内部仍按原始调用顺序保存结果，确保模型上下文顺序兼容。

Skill 专属脚本工具与内置命令工具采用一致的非阻塞工作线程模型，保留 stdout/stderr、退出码、超时和 JSON 解析行为。

## 核心数据结构

### ToolResult

沿用现有结构，不新增字段：

```python
@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    tool_name: str
    success: bool
    data: dict[str, Any]
    error_type: str | None = None
    error: str | None = None
    elapsed_ms: int | None = None
```

### ToolCallScheduler

新增内部辅助接口，负责并发只读批次的逐项完成事件：

```python
async def _run_concurrent_batch(
    self,
    calls: Sequence[ToolCall],
) -> AsyncIterator[tuple[ToolCall, ToolResult, tuple[object, ...]]]:
    ...
```

返回值包含原始调用、工具结果和对应 Hook 结果。调用方收到后立即发出 `hook_finished` 与 `tool_finished` 事件，并写入 `result_by_id`。

### 阻塞执行辅助函数

在工具模块内部提供小型 daemon 工作线程辅助函数，避免阻塞主事件循环：

```python
async def _run_blocking(function: Any, *args: Any, **kwargs: Any) -> Any:
    ...
```

调用方负责保持原有 stdout/stderr、退出码、超时和异常映射语义。

## 模块设计

### `mewcode.tools.builtin`

**职责：** 提供内置工具的非阻塞实现。  
**对外接口：** 保持现有 `execute(arguments, context)` 接口和返回结构不变。  
**依赖：** Python daemon 线程、队列轮询、现有 `ToolExecutionError`。

改动内容：

- `RunCommandTool.execute()` 在 daemon 工作线程中执行命令，超时后返回与当前一致的 `timeout` 错误。
- `SearchCodeTool._search_with_rg()` 在 daemon 工作线程中执行 `rg`；超时 fallback 到 Python 搜索。
- `ReadFileTool`、`WriteFileTool`、`EditFileTool`、`FindFilesTool` 的同步文件 IO 或 glob 扫描放入 daemon 工作线程。
- `SearchCodeTool._search_with_python()` 保持同步实现本体，但由异步入口放入线程执行。

### `mewcode.skills.tools`

**职责：** 提供 Skill 专属脚本工具执行。  
**对外接口：** 保持 `SkillScriptTool.execute()` 返回结构、错误类型和 JSON 解析行为不变。  
**依赖：** Python daemon 线程、队列轮询。

改动内容：

- 将脚本执行移入 daemon 工作线程，避免阻塞主事件循环。
- 保持 `MEWCODE_SKILL_NAME`、`MEWCODE_SKILL_TOOL`、`MEWCODE_SKILL_DIR` 环境变量。
- 超时后清理子进程，并返回当前一致的 `skill_tool_timeout` 错误。

### `mewcode.tools.scheduler`

**职责：** 调度工具调用并把工具生命周期事件交给 Agent/TUI。  
**对外接口：** 保持 `run()`、`results()`、`make_batches()` 行为兼容。  
**依赖：** Python `asyncio.create_task`、`asyncio.wait`。

改动内容：

- 并发只读批次创建独立任务。
- 任一任务完成后立即产出对应 Hook 完成事件和工具完成事件。
- `results()` 仍按模型原始 tool call 顺序返回。

### 测试

**职责：** 验证事件循环不阻塞、并发批次可逐项完成、兼容原有行为。  
**对外接口：** pytest 测试。  
**依赖：** 现有测试辅助类和临时目录。

新增或调整：

- 内置命令工具运行期间 ticker 协程仍能被调度。
- Skill 脚本工具运行期间 ticker 协程仍能被调度。
- Python 搜索或文件扫描通过 daemon 工作线程后 ticker 协程仍能被调度。
- 只读并发批次中快工具先产生 `tool_finished`。
- 保留现有超时、错误、返回格式测试。

## 模块交互

1. Agent Loop 收到模型工具调用。
2. ToolCallScheduler 按只读/副作用拆分批次。
3. 只读批次：
   1. Scheduler 为每个工具创建异步任务。
   2. 工具内部通过 daemon 工作线程执行慢操作。
   3. 某个工具完成后，Scheduler 立即产出该工具的 Hook 和完成事件。
   4. Scheduler 记录结果到 `result_by_id`。
4. 副作用批次：
   1. Scheduler 保持串行执行和权限确认流程。
   2. 用户确认后工具执行。
   3. 工具内部不阻塞事件循环。
5. Agent Loop 在批次结束后按原始顺序把结果写入 session，供下一轮模型调用。

## 文件组织

```text
mewcode/
├── src/mewcode/tools/builtin.py       — 内置工具非阻塞执行实现
├── src/mewcode/tools/scheduler.py     — 只读并发批次逐项完成事件
├── src/mewcode/skills/tools.py        — Skill 脚本工具非阻塞执行实现
├── tests/test_tools.py                — 内置工具非阻塞与兼容测试
├── tests/test_tool_scheduler.py       — 调度器逐项完成事件测试
└── tests/test_skills.py               — Skill 脚本工具非阻塞测试
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 本地命令执行 | 使用 daemon 工作线程承载 `subprocess.run` | 子进程运行期间不阻塞主事件循环，保留现有 stdout/stderr、超时和退出码语义 |
| 文件 IO 与 glob | 使用 daemon 工作线程包裹同步实现 | 改动小，保持路径、缓存、异常语义稳定，并避免默认线程池退出问题 |
| Python 搜索 fallback | 同步搜索本体保留，入口放入线程 | 避免重写搜索逻辑，同时解决大目录扫描阻塞 |
| 只读批次完成事件 | 任务完成即产出事件，最终结果仍按原始顺序 | 改善 TUI 反馈，不破坏模型工具结果顺序 |
| 权限确认 | 保持现状 | spec 明确不修改权限流程 |
| 流式命令输出 | 不做 | 超出本次范围，且会改变 TUI/工具协议 |
