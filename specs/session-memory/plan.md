# JulyCode 会话恢复与长期记忆 Plan

## 架构概览
本阶段新增 `julycode.memory` 子系统，负责项目指令加载、JSONL 会话存档、启动恢复、自动笔记和记忆索引。它位于 CLI/TUI、Agent Loop、PromptBuilder 和现有上下文管理之间：CLI 启动时先创建记忆管理器并恢复会话；Agent Loop 每次请求前从记忆管理器读取最新知识上下文；Agent Loop 自然完成后把本轮对话交给记忆管理器后台更新笔记。

项目指令作为运行时知识块加载，不写入 `ChatSession.messages`。加载顺序固定为项目管理目录级、项目根级、用户级，对应 `<project>/.julycode/AGENTS.md`、`<project>/AGENTS.md`、`~/.julycode/AGENTS.md`；同一文件内的 `@include <relative-path>` 会在加载阶段展开，展开时限制深度、用 visited 集合防环路，并按作用域拦截越界路径。

会话存档使用项目内 `.julycode/sessions/<session_id>.jsonl`。每条用户、助手、工具消息追加为一行 JSON；上下文压缩后的摘要和近期消息检查点也用追加事件记录在同一个 JSONL 中，不额外维护 meta 文件。会话列表通过扫描 JSONL 计算标题、消息数、最近更新时间和过期状态。

会话启动恢复由 `SessionBootstrapper` 编排。默认扫描同一项目的会话目录，清理 30 天以上未活动会话，恢复最近未过期会话；如果用户传入 `--new-session`，则创建空会话。恢复时先跳过坏行，再应用检查点，再用协议校验器截断未配对工具调用或孤立工具结果；如果恢复后的历史超过预算，则复用现有 `ContextManager` 执行一次压缩，压缩仍超限时放弃恢复该会话并启动空会话，同时给出中文告警。

长期记忆分为用户级和项目级两个存储根。用户级在 `~/.julycode/memory/`，项目级在 `<project>/.julycode/memory/`；每条笔记是一个带 frontmatter 的 Markdown 文件，分类为用户偏好、纠正反馈、项目知识和参考资料。索引文件由笔记扫描生成，写入各自 `index.md`，并控制在 200 行和 25KB 内。索引在处理用户请求前注入运行时提示，因此模型表现为已经读过这些记忆。

自动笔记更新在 Agent Loop 自然完成后触发。`AgentLoopRunner` 只负责提交后台任务，不等待更新完成；更新任务用无工具 LLM 请求生成笔记操作，由 LLM 判断是否创建、更新、合并或跳过，写入后重建索引。更新失败只记录中文告警，不影响 TUI 继续对话。

## 核心数据结构

### SessionMemoryConfig
```python
@dataclass(frozen=True)
class SessionMemoryConfig:
    enabled: bool = True
    project_dir: str = ".julycode"
    sessions_dir: str = "sessions"
    memory_dir: str = "memory"
    user_dir: str = "~/.julycode"
    instruction_filename: str = "AGENTS.md"
    include_max_depth: int = 5
    auto_restore: bool = True
    retention_days: int = 30
    time_gap_hours: int = 24
    index_max_lines: int = 200
    index_max_bytes: int = 25_000
    auto_notes_enabled: bool = True
```
挂到 `AppConfig.memory`。`project_dir` 是项目内 JulyCode 管理目录；`sessions_dir` 和 `memory_dir` 都相对 `project_dir`。`user_dir` 默认展开到用户主目录。

### SessionId
```python
SessionId = NewType("SessionId", str)

def new_session_id(now: datetime | None = None) -> SessionId: ...
```
生成格式为 `YYYYMMDD-HHMMSS-xxxx`，其中 `xxxx` 是短随机后缀，避免同秒撞车。该 ID 同时用于 JSONL 文件名和现有上下文外置结果目录。

### InstructionScope 与 InstructionBlock
```python
InstructionScope = Literal["project_private", "project_root", "user"]

@dataclass(frozen=True)
class InstructionBlock:
    scope: InstructionScope
    priority: int
    source_path: Path
    content: str
```
`priority` 越小优先级越高。最终注入时按 `project_private -> project_root -> user` 排列。

### InstructionBundle
```python
@dataclass(frozen=True)
class InstructionBundle:
    blocks: tuple[InstructionBlock, ...] = ()
    warnings: tuple[str, ...] = ()
```
包含所有成功加载的指令块和可展示告警。缺失文件不产生告警；非法 include、不可读文件和嵌套过深产生告警。

### SessionJsonlRecord
```python
SessionRecordKind = Literal["message", "checkpoint"]

@dataclass(frozen=True)
class SessionJsonlRecord:
    kind: SessionRecordKind
    session_id: SessionId
    created_at: str
    message: ChatMessage | None = None
    messages: tuple[ChatMessage, ...] = ()
    context_summary: ContextSummary | None = None
```
`message` 记录普通追加消息；`checkpoint` 记录上下文压缩后的近期消息和摘要。恢复时顺序应用记录，遇到 checkpoint 后以其 `messages` 和 `context_summary` 作为当前历史状态。

### SessionInfo
```python
@dataclass(frozen=True)
class SessionInfo:
    session_id: SessionId
    path: Path
    title: str
    message_count: int
    updated_at: datetime
    expired: bool
    warnings: tuple[str, ...] = ()
```
通过扫描 JSONL 计算，不写入单独 meta 文件。`title` 取恢复后第一条用户消息的首行摘要，没有用户消息时使用会话 ID。

### RestoreReport
```python
@dataclass(frozen=True)
class RestoreReport:
    restored: bool
    session_id: SessionId
    source_path: Path | None = None
    skipped_bad_lines: int = 0
    truncated_messages: int = 0
    compacted: bool = False
    started_empty_reason: str = ""
    time_gap_notice: str = ""
    warnings: tuple[str, ...] = ()
```
供 CLI/TUI 显示启动状态，也供 `PromptBuilder` 注入时间跨度提醒。

### KnowledgeContext
```python
@dataclass(frozen=True)
class KnowledgeContext:
    instructions: InstructionBundle
    user_memory_index: MemoryIndex | None = None
    project_memory_index: MemoryIndex | None = None
    restore_report: RestoreReport | None = None
```
运行时提示构造器的知识输入。它只作为系统级运行时补充出现，不进入普通会话消息。

### MemoryNote
```python
MemoryScope = Literal["user", "project"]
MemoryCategory = Literal["preference", "correction", "project_knowledge", "reference"]

@dataclass(frozen=True)
class MemoryNote:
    note_id: str
    scope: MemoryScope
    category: MemoryCategory
    title: str
    body: str
    source_session_id: SessionId
    created_at: str
    updated_at: str
    tags: tuple[str, ...] = ()
```
写入 Markdown frontmatter 和正文。`scope` 决定用户级或项目级目录；`category` 决定分类目录和索引分组。

### MemoryIndex
```python
@dataclass(frozen=True)
class MemoryIndex:
    scope: MemoryScope
    path: Path
    content: str
    line_count: int
    byte_count: int
    warnings: tuple[str, ...] = ()
```
`content` 是下一轮请求前注入的短索引。生成后必须满足行数和字节数上限；如果通过裁剪满足上限，`warnings` 记录原因。

### MemoryUpdateJob
```python
@dataclass(frozen=True)
class MemoryUpdateJob:
    session_id: SessionId
    cwd: Path
    turn_messages: tuple[ChatMessage, ...]
    final_message: ChatMessage
    knowledge_context: KnowledgeContext
```
Agent Loop 自然完成后提交给后台更新器。取消、错误、迭代上限和工具等待状态不会创建该任务。

## 核心接口

### InstructionLoader
```python
class InstructionLoader:
    def __init__(self, cwd: Path, config: SessionMemoryConfig) -> None: ...

    def load(self) -> InstructionBundle: ...
```
按三层路径加载指令，并展开 include。include 语法为独立一行 `@include <relative/path.md>`；相对路径基于当前文件所在目录解析。项目级 include 必须仍在项目根目录内，用户级 include 必须仍在 `~/.julycode` 内。

### SessionJsonlStore
```python
class SessionJsonlStore:
    def __init__(self, cwd: Path, config: SessionMemoryConfig) -> None: ...

    def create_session(self, session_id: SessionId | None = None) -> ChatSession: ...
    def attach_recorder(self, session: ChatSession) -> None: ...
    def append_message(self, session_id: SessionId, message: ChatMessage) -> None: ...
    def append_checkpoint(self, session: ChatSession) -> None: ...
    def list_sessions(self, *, now: datetime | None = None) -> tuple[SessionInfo, ...]: ...
    def load_session(self, session_id: SessionId) -> tuple[ChatSession, RestoreReport]: ...
    def latest_unexpired(self, *, now: datetime | None = None) -> SessionInfo | None: ...
    def cleanup_expired(self, *, now: datetime | None = None) -> tuple[SessionInfo, ...]: ...
```
所有写入都是 JSONL 追加。`load_session()` 对坏行跳过并累计告警；不会依赖任何 meta 文件。

### PersistentSessionRecorder
```python
class PersistentSessionRecorder:
    def append_message(self, message: ChatMessage) -> None: ...
    def append_checkpoint(self, messages: Sequence[ChatMessage], summary: ContextSummary | None) -> None: ...
```
挂到 `ChatSession`。`append_user_message()`、`append_assistant_message()`、`append_tool_result()` 成功修改内存状态后调用 recorder。`ContextManager` 完成重量压缩并替换消息后调用 checkpoint 写入。

### SessionHistoryValidator
```python
@dataclass(frozen=True)
class ProtocolValidationResult:
    messages: tuple[ChatMessage, ...]
    truncated_count: int
    warning: str

class SessionHistoryValidator:
    def truncate_to_protocol_safe(self, messages: Sequence[ChatMessage]) -> ProtocolValidationResult: ...
```
确保 assistant 工具调用和后续 tool 结果配对。发现未配对工具调用、孤立工具结果、重复工具结果或工具结果顺序中断时，在最近安全边界截断。

### SessionBootstrapper
```python
@dataclass(frozen=True)
class BootstrapOptions:
    new_session: bool = False

@dataclass(frozen=True)
class BootstrapResult:
    session: ChatSession
    knowledge_context: KnowledgeContext
    restore_report: RestoreReport

class SessionBootstrapper:
    async def bootstrap(
        self,
        *,
        options: BootstrapOptions,
        provider: LLMProvider,
        context_manager: ContextManager,
    ) -> BootstrapResult: ...
```
负责启动顺序：清理过期会话、加载指令和索引、按选项恢复或创建会话、执行协议截断和预算检查，并把 recorder 绑定到返回的 session。

### MemoryNoteStore
```python
class MemoryNoteStore:
    def list_notes(self, scope: MemoryScope) -> tuple[MemoryNote, ...]: ...
    def read_note(self, scope: MemoryScope, note_id: str) -> MemoryNote | None: ...
    def write_note(self, note: MemoryNote) -> Path: ...
```
负责 Markdown frontmatter 读写和分类目录管理。文件名使用安全化后的 `note_id`，目录按 `scope/category/` 分层。

### MemoryIndexBuilder
```python
class MemoryIndexBuilder:
    def build(self, scope: MemoryScope) -> MemoryIndex: ...
    def read_index(self, scope: MemoryScope) -> MemoryIndex | None: ...
```
扫描笔记生成 `index.md`。按分类固定顺序输出；超过 200 行或 25KB 时先按更新时间和类别优先级裁剪，再记录告警。

### MemoryNoteUpdater
```python
class MemoryNoteUpdater:
    async def update(self, *, job: MemoryUpdateJob, provider: LLMProvider) -> tuple[MemoryIndex, ...]: ...
```
构造无工具 LLM 请求，让模型基于本轮对话和现有索引返回结构化笔记操作。去重、合并和跳过由 LLM 判断；写入前仍做基本结构校验和敏感信息过滤。

### SessionMemoryManager
```python
class SessionMemoryManager:
    async def bootstrap(
        self,
        *,
        options: BootstrapOptions,
        provider: LLMProvider,
        context_manager: ContextManager,
    ) -> BootstrapResult: ...

    def runtime_context(self) -> KnowledgeContext: ...
    def schedule_update(self, *, job: MemoryUpdateJob, provider: LLMProvider) -> None: ...
    async def wait_for_updates(self) -> None: ...
```
对 CLI、TUI 和 Agent 暴露统一入口。`runtime_context()` 每次请求前返回最新指令和索引；`schedule_update()` 创建后台任务并捕获错误；`wait_for_updates()` 只给测试和优雅退出使用。

### PromptBuilder 扩展
```python
@dataclass(frozen=True)
class RuntimePromptContext:
    cwd: Path
    mode: AgentMode
    iteration: int
    max_iterations: int
    allowed_tools: Sequence[ToolSpec]
    pending_plan: PendingPlan | None = None
    source_request: str = ""
    context_summary: ContextSummary | None = None
    knowledge_context: KnowledgeContext | None = None
```
`PromptBuilder.build_runtime_prompt()` 在现有运行时补充后追加：
- `<julycode_project_instructions>`：三层指令，按优先级排列并标明来源。
- `<julycode_memory_index>`：用户级和项目级索引，标明 scope。
- `<julycode_restore_notice>`：时间跨度提醒和恢复告警。
- 现有 `<julycode_context_summary>` 保持独立块。

## 模块设计

### `julycode.session_id`
**职责：** 生成和校验 `YYYYMMDD-HHMMSS-xxxx` 会话 ID。  
**对外接口：** `new_session_id()`、`is_valid_session_id(value)`。  
**依赖：** 标准库 `datetime`、`secrets`、`re`。

### `julycode.config`
**职责：** 解析 `memory:` 配置并挂到 `AppConfig.memory`。  
**对外接口：** 现有 `load_config()`，新增 `_parse_memory()`。  
**依赖：** `SessionMemoryConfig`。

### `julycode.session`
**职责：** 保持运行期消息历史，并在可选 recorder 存在时追加写 JSONL。  
**对外接口：** 现有 `ChatSession` 方法保持兼容，新增 `set_recorder()` 和 `append_checkpoint()`。  
**依赖：** `ContextState`、`PersistentSessionRecorder` 协议。

### `julycode.memory.models`
**职责：** 定义指令、会话存档、恢复报告、记忆笔记、索引和启动结果模型。  
**对外接口：** 上述 dataclass 和 Literal 类型。  
**依赖：** Provider 基础消息类型、上下文摘要类型。

### `julycode.memory.instructions`
**职责：** 加载三层指令文件和展开 include。  
**对外接口：** `InstructionLoader.load()`。  
**依赖：** `pathlib`、`SessionMemoryConfig`。

### `julycode.memory.session_store`
**职责：** JSONL 会话追加写、扫描、恢复、过期清理和会话列表计算。  
**对外接口：** `SessionJsonlStore`、`PersistentSessionRecorder`。  
**依赖：** `json`、`ChatSession`、`ChatMessage`、`ToolCall`、`ContextSummary`。

### `julycode.memory.recovery`
**职责：** 协议安全截断、时间跨度提醒和启动恢复编排。  
**对外接口：** `SessionHistoryValidator`、`SessionBootstrapper`。  
**依赖：** `SessionJsonlStore`、`ContextManager`、`PromptBuilder`、`LLMProvider`。

### `julycode.memory.notes`
**职责：** Markdown 笔记 frontmatter 读写、分类目录和敏感信息过滤。  
**对外接口：** `MemoryNoteStore`。  
**依赖：** 标准库；frontmatter 用简单 YAML 头部解析复用现有 `PyYAML`。

### `julycode.memory.index`
**职责：** 从笔记生成和读取用户级、项目级索引，并强制行数和字节上限。  
**对外接口：** `MemoryIndexBuilder`。  
**依赖：** `MemoryNoteStore`。

### `julycode.memory.updater`
**职责：** Agent 自然完成后用无工具 LLM 请求更新自动笔记。  
**对外接口：** `MemoryNoteUpdater.update()`。  
**依赖：** `LLMProvider`、`ChatRequest`、`MemoryNoteStore`、`MemoryIndexBuilder`。

### `julycode.memory.manager`
**职责：** 对外提供启动、运行时知识读取和后台更新任务管理。  
**对外接口：** `SessionMemoryManager`。  
**依赖：** memory 子模块、`asyncio`、`logging`。

### `julycode.prompting.base` 与 `julycode.prompting.builder`
**职责：** 扩展运行时提示输入并注入项目指令、记忆索引和恢复提醒。  
**对外接口：** `RuntimePromptContext`、`PromptBuilder.build_bundle()`。  
**依赖：** `KnowledgeContext`。

### `julycode.context.manager`
**职责：** 保持现有轻量/重量压缩职责；重量压缩成功后通知 session 追加 checkpoint。  
**对外接口：** `prepare_request()`、`manual_compact()` 保持不变。  
**依赖：** `ChatSession.append_checkpoint()`。

### `julycode.agent`
**职责：** 请求前注入最新知识上下文；自然完成后调度自动笔记更新。  
**对外接口：** `AgentLoopRunner` 构造参数新增 `memory_manager: SessionMemoryManager | None`。  
**依赖：** `SessionMemoryManager`、`MemoryUpdateJob`。

### `julycode.tui.app`
**职责：** 接收启动恢复结果并显示中文启动告警；创建 AgentLoopRunner 时传入 memory manager。  
**对外接口：** `JulyCodeApp(...)` 构造参数新增 `memory_manager` 和 `restore_report`。  
**依赖：** `SessionMemoryManager`。

### `julycode.cli`
**职责：** 解析 `--new-session`，创建记忆管理器并执行启动恢复。  
**对外接口：** `main(argv=None)`。  
**依赖：** `argparse`、`SessionMemoryManager`。

## 模块交互

### 启动流程
1. 用户运行 `julycode`，可选 `--new-session`。
2. `cli.main()` 加载配置、创建 Provider、工具注册表、权限控制器和 `ContextManager`。
3. `cli.main()` 创建 `SessionMemoryManager`，调用 `bootstrap()`。
4. `SessionMemoryManager` 清理超过 30 天未活动的 JSONL 会话。
5. `InstructionLoader` 加载三层 `AGENTS.md`，展开 include 并收集告警。
6. `MemoryIndexBuilder` 读取用户级和项目级 `index.md`，若缺失则用当前笔记生成。
7. 如果没有 `--new-session` 且存在最近未过期会话，`SessionJsonlStore` 恢复 JSONL。
8. `SessionHistoryValidator` 截断无效工具历史；如距离上次活动超过 `time_gap_hours`，生成时间跨度提醒。
9. 如果恢复历史估算超预算，`SessionBootstrapper` 复用 `ContextManager` 尝试压缩；压缩失败或仍超限则创建空会话并记录原因。
10. TUI 启动后在消息区或状态栏展示恢复结果和告警。

### 请求前知识注入
1. 用户输入进入 `parse_agent_command()`。
2. `AgentLoopRunner.run()` 追加用户消息，消息自动写入 JSONL。
3. 每次模型请求前，`prompt_factory()` 调用 `memory_manager.runtime_context()` 获取最新指令和索引。
4. `PromptBuilder` 把知识块作为运行时系统补充注入，保持与普通用户消息、上下文摘要分离。
5. `ContextManager.prepare_request()` 继续执行轻量预防和必要的重量兜底。
6. Provider 收到包含稳定提示、运行时知识、上下文摘要和会话消息的请求。

### 会话追加与恢复
1. `ChatSession.append_user_message()`、`append_assistant_message()`、`append_tool_result()` 修改内存历史。
2. 如果 session 已绑定 recorder，对应消息立即追加为一行 JSONL。
3. 如果上下文重量压缩替换了历史并设置摘要，`ChatSession.append_checkpoint()` 追加 checkpoint。
4. 崩溃后恢复时，JSON 解码失败的行被跳过；后续有效行继续应用。
5. 恢复完成后再做协议安全截断，确保不会把无效工具历史发送给模型。

### 自动笔记更新
1. Agent Loop 收到最终 assistant 消息且 `tool_calls` 为空。
2. Agent Loop 追加 assistant 消息、应用 Plan/Do 状态变更，然后构造 `MemoryUpdateJob`。
3. `SessionMemoryManager.schedule_update()` 用 `asyncio.create_task()` 后台执行。
4. `MemoryNoteUpdater` 发起无工具 LLM 请求，要求返回结构化笔记操作。
5. `MemoryNoteStore` 创建或更新 Markdown 笔记；`MemoryIndexBuilder` 重建用户级和项目级索引。
6. 失败时记录 warning，不回滚当前会话，不阻塞下一轮对话。

## 文件组织
```text
julycode/
├── specs/session-memory/
│   ├── spec.md                         — 已批准需求
│   ├── plan.md                         — 本技术设计
│   ├── task.md                         — 后续任务拆解
│   └── checklist.md                    — 后续验收清单
├── src/julycode/
│   ├── session_id.py                   — 会话 ID 生成与校验
│   ├── config.py                       — 解析 memory 配置
│   ├── session.py                      — ChatSession 绑定 recorder 和 checkpoint
│   ├── cli.py                          — 启动参数、恢复会话、创建 memory manager
│   ├── agent.py                        — 知识注入和自然完成后调度笔记更新
│   ├── tui/app.py                      — 展示恢复告警并传递 memory manager
│   ├── prompting/
│   │   ├── base.py                     — RuntimePromptContext 增加 KnowledgeContext
│   │   └── builder.py                  — 注入指令、记忆索引、恢复提醒
│   ├── context/
│   │   ├── models.py                   — ContextState 使用新会话 ID
│   │   └── manager.py                  — 压缩成功后追加 checkpoint
│   └── memory/
│       ├── __init__.py                 — 导出记忆子系统公开类型
│       ├── models.py                   — 指令、恢复、笔记、索引模型
│       ├── instructions.py             — 三层指令加载和 @include 展开
│       ├── session_store.py            — JSONL 追加写、扫描、恢复、清理
│       ├── recovery.py                 — 协议安全截断和启动恢复编排
│       ├── notes.py                    — Markdown 笔记读写与敏感信息过滤
│       ├── index.py                    — 记忆索引生成、裁剪和读取
│       ├── updater.py                  — 无工具 LLM 自动笔记更新
│       └── manager.py                  — 启动、运行时上下文、后台任务管理
├── tests/
│   ├── test_session_id.py              — 会话 ID 格式和同秒防撞
│   ├── test_memory_instructions.py     — 三层指令、include、越界和环路
│   ├── test_session_store.py           — JSONL 追加、坏行、列表扫描、清理
│   ├── test_session_recovery.py        — 默认恢复、空会话、协议截断、预算压缩
│   ├── test_memory_notes.py            — Markdown 笔记、分类、scope 和脱敏
│   ├── test_memory_index.py            — 索引生成和 200 行 / 25KB 上限
│   ├── test_memory_updater.py          — 自然完成后无工具 LLM 更新和去重操作
│   ├── test_prompting.py               — 知识上下文注入和来源边界
│   ├── test_agent.py                   — 自然完成触发笔记，非自然停止不触发
│   ├── test_tui_smoke.py               — 启动恢复提示和既有 TUI 行为
│   └── test_config.py                  — memory 配置解析
├── README.md                           — 说明会话恢复、项目指令和记忆目录
└── .gitignore                          — 忽略 .julycode/sessions/、.julycode/memory/ 自动产物
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 指令文件名 | 三层都使用 `AGENTS.md` | 仓库已有 `AGENTS.md`，用户和项目都能直接手写 Markdown，避免新增命名体系 |
| 指令优先级 | `.julycode/AGENTS.md` > `AGENTS.md` > `~/.julycode/AGENTS.md` | 项目专用细节优先，其次项目根通用说明，最后用户通用偏好 |
| include 语法 | 独立行 `@include <relative-path>` | 易读、易解析、可避免在普通正文中误触发复杂语法 |
| 会话存储 | `.julycode/sessions/<id>.jsonl` 追加事件 | 满足追加快、坏行可跳过、无 meta 同步负担，也能记录压缩检查点 |
| 会话 ID | `YYYYMMDD-HHMMSS-xxxx` | 可按文件名粗略排序，随机后缀避免同秒冲突 |
| 恢复默认 | 自动恢复最近未过期会话，`--new-session` 创建空会话 | 贴合“中断后接着用”，同时给用户明确逃生口 |
| 超限恢复 | 启动时先尝试一次现有上下文压缩 | 复用已验证的摘要和安全边界机制，不新增第二套压缩逻辑 |
| 知识注入位置 | 运行时系统补充，不写入普通消息 | 不污染用户消息历史，也不破坏稳定提示缓存 |
| 自动笔记触发点 | Agent Loop 自然完成后后台任务 | 只在最终回复无工具调用时沉淀，避免工具中间态或失败态误记 |
| 自动笔记去重 | 由 LLM 根据现有索引和候选事实判断 | 符合需求，不引入向量数据库或复杂相似度检索 |
| 索引上限 | 生成后强制 200 行和 25KB 双限制 | 保证请求前可注入，避免长期记忆反向挤压工作上下文 |
| 失败处理 | 记录中文 warning，TUI 不退出 | 满足“记忆失败不阻断对话”，保留可观察问题 |

## 需求覆盖

| 需求 | 架构负责人 |
|------|------------|
| F1 | `InstructionLoader` 三层加载与 `PromptBuilder` 优先级注入 |
| F2 | `InstructionLoader` include 深度、visited 和路径边界检查 |
| F3 | `InstructionBundle.warnings` 与 TUI/日志告警 |
| F4 | `SessionBootstrapper` 默认恢复和 CLI `--new-session` |
| F5 | `session_id.new_session_id()` |
| F6 | `SessionJsonlStore.append_message()` JSONL 追加写 |
| F7 | `SessionJsonlStore.list_sessions()` 扫描计算会话信息 |
| F8 | `SessionJsonlStore.load_session()` 坏行跳过 |
| F9 | `SessionHistoryValidator.truncate_to_protocol_safe()` |
| F10 | `SessionBootstrapper` 复用 `ContextManager` 执行恢复预算压缩 |
| F11 | `RestoreReport.time_gap_notice` 与 `PromptBuilder` 恢复提醒注入 |
| F12 | `SessionJsonlStore.cleanup_expired()` |
| F13 | `AgentLoopRunner` 自然完成分支调度 `MemoryUpdateJob` |
| F14 | `MemoryNote` 分类和 `MemoryNoteUpdater` 操作协议 |
| F15 | `MemoryNote.scope`、用户级和项目级存储根 |
| F16 | `MemoryNoteStore` Markdown frontmatter 读写 |
| F17 | `SessionMemoryManager.runtime_context()` 与 `PromptBuilder` 索引注入 |
| F18 | `MemoryIndexBuilder` 行数和字节上限 |
| F19 | `SessionMemoryManager` 后台任务错误捕获和 warning |
| F20 | `KnowledgeContext` 分块注入，和现有上下文摘要保持独立标签 |
| F21 | 现有 Agent、TUI、MCP、权限、上下文测试作为回归验证 |
