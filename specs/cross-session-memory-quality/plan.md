# JulyCode 跨会话记忆质量 Plan

## 架构概览
本阶段在现有 `julycode.memory` 子系统中把自动记忆更新拆成“模型提取候选 → 确定性校验 → 合并或落盘 → 重建索引”四步。模型继续负责理解自然语言和提出候选记忆，确定性校验器负责证据、长期性、关键偏好、敏感信息、重复和冲突门控。这样既保留现有模型提取能力，又把关键偏好高精度要求落实为可测试的程序规则。

长期记忆文件继续使用带 YAML frontmatter 的 Markdown。`MemoryNote` 增加用户原话证据、关键偏好标记和模型置信度，读取旧文件时提供安全默认值，保证已有记忆兼容。索引只注入经过校验的标题和正文，不注入置信度或原始证据；关键偏好在索引中优先排列并明确标记。运行时提示补充说明：长期记忆不是当前用户消息，关键偏好在未被当前用户明确覆盖时应遵循，已经存在的背景不得再次要求用户提供。

专项评测放在独立的 `eval/memory_quality` 包中，不把专用指标塞进现有通用 Agent 百分制评分。提取评测直接调用生产 `MemoryNoteUpdater.extract()`，使用人工标注数据逐项匹配；跨会话评测使用临时隔离目录运行真实 `AgentLoopRunner`、`SessionMemoryManager` 和 `BootstrapOptions(new_session=True)`，分别执行开启记忆和关闭记忆的成对试验。两类评测汇总到同一份 JSON 与 Markdown 报告。

普通测试和 `--mode offline` 使用确定性 Provider，只证明流程、匹配和统计正确，不声称模型质量。`--mode online` 复用 JulyCode 当前配置创建真实 Provider，完整数据集运行后才依据规格阈值给出发布验收结论。

## 核心数据结构

### SessionMemoryConfig 扩展
```python
@dataclass(frozen=True)
class SessionMemoryConfig:
    # 既有字段保持不变
    critical_preference_min_confidence: float = 0.95
```
`critical_preference_min_confidence` 只用于关键偏好门控，取值范围为 `[0, 1]`。它不是最终 Precision 指标，只是保守过滤候选的一项条件。

### MemoryNote 扩展
```python
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
    source_evidence: tuple[str, ...] = ()
    critical: bool = False
    confidence: float | None = None
```
旧笔记缺少新增字段时读取为 `source_evidence=()`、`critical=False`、`confidence=None`。新自动笔记必须有至少一段用户原话证据；旧笔记仅为兼容而允许证据为空。

### MemoryCandidate
```python
MemoryAction = Literal["create", "update", "skip"]
MemoryDurability = Literal["persistent", "temporary", "uncertain"]

@dataclass(frozen=True)
class MemoryCandidate:
    action: MemoryAction
    scope: MemoryScope | str = ""
    category: MemoryCategory | str = ""
    note_id: str = ""
    title: str = ""
    body: str = ""
    evidence: tuple[str, ...] = ()
    durability: MemoryDurability | str = ""
    critical: bool = False
    confidence: float = 0.0
    tags: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
```
该结构表示模型原始候选，不直接写入文件。`supersedes` 只能引用当前作用域内真实存在的笔记 ID。

### ValidatedMemoryOperation 与 MemoryRejection
```python
@dataclass(frozen=True)
class ValidatedMemoryOperation:
    action: Literal["create", "update"]
    note: MemoryNote
    supersedes: tuple[str, ...] = ()

@dataclass(frozen=True)
class MemoryRejection:
    candidate: MemoryCandidate
    code: str
    message: str
```
拒绝码至少覆盖 `invalid_schema`、`not_persistent`、`missing_user_evidence`、`critical_not_explicit`、`critical_low_confidence`、`sensitive_content`、`duplicate`、`invalid_update` 和 `invalid_supersedes`，用于测试和报告定位原因。

### MemoryExtractionResult
```python
@dataclass(frozen=True)
class MemoryExtractionResult:
    accepted: tuple[ValidatedMemoryOperation, ...] = ()
    rejected: tuple[MemoryRejection, ...] = ()
```
`skip` 不算接受或拒绝。评测中的“预测结果”只取最终可落盘的 `accepted`；被拒绝的应记忆候选会表现为 FN，被拒绝的负例候选不会形成 FP，但拒绝原因仍进入审计明细。

### ExtractionCase 与 ExpectedMemory
```python
@dataclass(frozen=True)
class ExpectedMemory:
    key: str
    scope: MemoryScope
    category: MemoryCategory
    critical: bool
    evidence: tuple[str, ...]
    content_term_groups: tuple[tuple[str, ...], ...]

@dataclass(frozen=True)
class ExtractionCase:
    case_id: str
    tags: tuple[str, ...]
    messages: tuple[ChatMessage, ...]
    expected: tuple[ExpectedMemory, ...]
```
`content_term_groups` 中每组至少命中一个词，用于允许中文或英文的等价表述；证据仍必须对应标注的用户原话。多数用例只含一个标注单元，重复和冲突场景可以包含多个消息或前置记忆。

### ExtractionCaseResult 与 ExtractionMetrics
```python
@dataclass(frozen=True)
class ExtractionMatch:
    expected_key: str
    predicted_note_id: str
    evidence: str

@dataclass(frozen=True)
class ExtractionCaseResult:
    case_id: str
    matches: tuple[ExtractionMatch, ...]
    false_positives: tuple[MemoryNote, ...]
    false_negatives: tuple[ExpectedMemory, ...]
    rejections: tuple[MemoryRejection, ...]

@dataclass(frozen=True)
class ExtractionMetrics:
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    critical_tp: int
    critical_fp: int
    critical_fn: int
    critical_precision: float
    critical_recall: float
```

### InheritanceCase 与 InheritanceCaseResult
```python
@dataclass(frozen=True)
class InheritanceExpectation:
    required_term_groups: tuple[tuple[str, ...], ...]
    forbidden_terms: tuple[str, ...] = ()
    restatement_terms: tuple[str, ...] = ()

@dataclass(frozen=True)
class InheritanceCase:
    case_id: str
    source_prompt: str
    target_prompt: str
    expectation: InheritanceExpectation

@dataclass(frozen=True)
class InheritanceTrial:
    memory_enabled: bool
    final_text: str
    first_turn_correct: bool
    requested_restatement: bool
    session_started_empty: bool
    injected_user_memory: bool
    injected_project_memory: bool
    evidence: tuple[str, ...]

@dataclass(frozen=True)
class InheritanceCaseResult:
    case_id: str
    baseline: InheritanceTrial
    enabled: InheritanceTrial
```
目标提示会明确要求“缺少既定背景时询问，不要猜测”，保证关闭记忆基线能形成有效的背景重述需求分母；这条提示不包含背景答案本身。

### MemoryQualityReport
```python
@dataclass(frozen=True)
class MemoryQualityReport:
    dataset_version: str
    mode: Literal["offline", "online"]
    provider: EvalProviderInfo
    started_at: str
    extraction_results: tuple[ExtractionCaseResult, ...]
    extraction_metrics: ExtractionMetrics
    inheritance_results: tuple[InheritanceCaseResult, ...]
    first_turn_accuracy: float
    baseline_restatements: int
    enabled_restatements: int
    restatement_reduction: float | None
    acceptance_passed: bool
    acceptance_failures: tuple[str, ...]
```
当关闭记忆的背景重述需求为 0 时，`restatement_reduction=None` 且验收失败。

## 核心接口

### MemoryCandidateValidator
```python
class MemoryExtractionError(ValueError): ...

class MemoryCandidateValidator:
    def __init__(
        self,
        note_store: MemoryNoteStore,
        config: SessionMemoryConfig,
        *,
        secrets: tuple[str, ...] = (),
    ) -> None: ...

    def validate(
        self,
        candidates: Sequence[MemoryCandidate],
        job: MemoryUpdateJob,
    ) -> MemoryExtractionResult: ...
```
`MemoryExtractionError` 定义在无 Provider 依赖的 `memory.extraction` 中；`memory.updater` 继续以 `MemoryUpdateError` 名称重导出该异常，保持既有调用方和测试兼容并避免模块循环导入。

校验顺序固定为：结构合法性 → `persistent` 长期性 → 用户证据精确定位 → 敏感信息检查 → 关键偏好门控 → create/update/supersedes 合法性 → 批内与已有笔记去重。所有候选先校验完成，再允许任何落盘。

用户证据只从 `job.turn_messages` 中 `role="user"` 的消息收集。每段 `evidence` 必须是某条用户消息的精确子串；助手最终回复和工具结果不能补足证据。关键偏好还要求：

1. 类别是 `preference` 或 `correction`；
2. 证据包含中英文明确持续性或强约束标记，例如“以后、始终、每次、默认、必须、禁止、不要再、请记住、from now on、always、never、by default、must”；
3. `confidence >= critical_preference_min_confidence`。

候选标题、正文或证据命中已知密钥、通用令牌、Bearer 凭据或私钥特征时直接拒绝；`MemoryNoteStore` 写入前仍保留现有脱敏作为第二道保护。

### MemoryNoteUpdater 扩展
```python
class MemoryNoteUpdater:
    async def extract(
        self,
        *,
        job: MemoryUpdateJob,
        provider: LLMProvider,
    ) -> MemoryExtractionResult: ...

    def apply(
        self,
        result: MemoryExtractionResult,
    ) -> tuple[MemoryIndex, ...]: ...

    async def update(
        self,
        *,
        job: MemoryUpdateJob,
        provider: LLMProvider,
    ) -> tuple[MemoryIndex, ...]: ...
```
`update()` 保持原有返回类型，内部依次调用 `extract()` 和 `apply()`，因此 `SessionMemoryManager` 无需改变调度契约。专项提取评测只调用 `extract()`，避免评测用例互相污染；重复和冲突集成测试显式调用 `apply()`。

提取 Prompt 改为发送带角色的当前轮消息、现有用户/项目索引，并声明完整 JSON schema。模型必须为非 `skip` 候选提供长期性、直接证据、关键标记、置信度和被替代笔记 ID。Prompt 明确列出负例策略：临时要求、助手猜测、工具输出、密钥和闲聊应返回 `skip`。

### MemoryNoteStore 扩展
```python
class MemoryNoteStore:
    def delete_note(self, scope: MemoryScope, note_id: str) -> bool: ...
    def contains_sensitive(self, text: str) -> bool: ...
```
`write_note()` 读写新增元信息并兼容旧格式。`apply()` 写入新笔记成功后才删除 `supersedes` 指向的旧笔记，避免先删后写导致记忆丢失。同 ID 更新保留原始 `created_at`，更新来源会话、证据和 `updated_at`。

### ExtractionMatcher
```python
class ExtractionMatcher:
    def match(
        self,
        case: ExtractionCase,
        result: MemoryExtractionResult,
    ) -> ExtractionCaseResult: ...
```
匹配边成立需同时满足作用域、类别和关键标记一致，预测证据与标注证据归一化后对应，并且每个 `content_term_groups` 至少有一个词出现在预测标题、正文或证据中。匹配器执行确定性一对一最大匹配；一个预测或标注最多使用一次，剩余预测为 FP，剩余标注为 FN。

### MemoryQualityDatasetLoader
```python
class MemoryQualityDatasetLoader:
    def load(self, root: Path) -> MemoryQualityDataset: ...
    def validate_acceptance_size(self, dataset: MemoryQualityDataset) -> None: ...
```
Loader 校验版本、唯一 case ID、消息角色、证据确实存在于用户消息、term group 非空、关键偏好/其他正例/负例数量，以及 20 个跨会话用例下限。完整验收运行必须调用 `validate_acceptance_size()`；单元测试可以直接构造小数据对象。

### MemoryQualityRunner
```python
class MemoryQualityRunner:
    async def run_extraction(
        self,
        cases: Sequence[ExtractionCase],
        provider: LLMProvider,
    ) -> tuple[tuple[ExtractionCaseResult, ...], ExtractionMetrics]: ...

    async def run_inheritance(
        self,
        cases: Sequence[InheritanceCase],
        provider: LLMProvider,
    ) -> tuple[InheritanceCaseResult, ...]: ...

    async def run(
        self,
        dataset: MemoryQualityDataset,
        options: MemoryQualityRunOptions,
    ) -> MemoryQualityReport: ...
```
每个提取用例构造独立临时 `MemoryUpdateJob`。每个跨会话用例创建两个完全隔离的临时 workspace 和 user memory 目录：

- 开启记忆：以空会话运行来源提示，等待后台记忆更新完成；重新创建 manager，以 `new_session=True` 启动空白会话；确认普通消息为空后运行目标提示。
- 关闭记忆：不加载来源历史和长期记忆，直接以相同目标提示运行空白会话。

两次目标运行使用同一 Provider 配置，但不共享会话、文件或记忆目录。Runner 捕获首个请求的运行时提示，用于证明长期记忆已注入且上一会话消息未混入。

### MemoryQualityOfflineProvider
```python
class MemoryQualityOfflineProvider(LLMProvider):
    def for_extraction_case(self, case: ExtractionCase) -> LLMProvider: ...
    def for_inheritance_case(self, case: InheritanceCase) -> LLMProvider: ...
```
离线 Provider 依据当前测试用例返回确定性结构化候选、来源确认回复、基线询问或符合记忆的目标回复。它只用于验证生产提取链路和评测器，不在报告中标记为真实模型。

### 评测 CLI
```text
python eval/run_memory_eval.py --mode offline --output eval/results/memory-quality/offline
python eval/run_memory_eval.py --mode online --output eval/results/memory-quality/latest
```
CLI 默认 `offline`，避免意外发起约 200 次真实模型请求。`online` 模式复用 `load_config()`、`create_provider()` 和可选 `--model` 覆盖；完整执行 extraction 与 inheritance 两组评测，生成 `results.json` 和 `report.md`。在线模式任一门槛未达到时退出码为 1，配置或框架错误为 2，全部通过为 0。

## 模块设计

### 生产自动提取
**职责：** 生成候选、执行保守校验、处理重复与替代关系、落盘并重建索引。

**对外接口：** `MemoryNoteUpdater.extract()`、`MemoryNoteUpdater.apply()`、`MemoryNoteUpdater.update()`。

**依赖：** Provider 抽象、`MemoryCandidateValidator`、`MemoryNoteStore`、`MemoryIndexBuilder`。

**覆盖需求：** F1、F2、F3、F4、F5、F15。

### 长期笔记与索引
**职责：** 兼容读写来源元信息，执行写入前脱敏，安全删除被替代笔记，将关键偏好优先生成到短索引。

**对外接口：** `MemoryNoteStore`、`MemoryIndexBuilder`。

**依赖：** YAML、现有错误脱敏函数。

**覆盖需求：** F3、F4、F5、F10、F15。

### 运行时记忆注入
**职责：** 保持 `--new-session` 普通消息为空，在首次模型请求前注入用户和项目记忆，并明确长期记忆的使用边界。

**对外接口：** 既有 `SessionBootstrapper.bootstrap()`、PromptBuilder 动态知识块。

**依赖：** `SessionMemoryManager`、`MemoryIndexBuilder`。

**覆盖需求：** F9、F10、F15。

### 人工标注数据加载
**职责：** 读取 extraction 与 inheritance 数据，校验 schema、证据和验收规模。

**对外接口：** `MemoryQualityDatasetLoader`。

**依赖：** 标准库 JSON、专项评测模型。

**覆盖需求：** F6、F11、F13、F14。

### 提取匹配与指标
**职责：** 一对一匹配预测和标注，计算整体及关键偏好 Precision/Recall/F1，保留 FP/FN/拒绝证据。

**对外接口：** `ExtractionMatcher`、`aggregate_extraction_metrics()`。

**依赖：** 专项评测模型，无 Provider 依赖。

**覆盖需求：** F7、F8、F13。

### 跨会话成对评测
**职责：** 在隔离目录运行开启/关闭记忆试验，检查空会话、注入内容、首轮结果和背景重述需求。

**对外接口：** `MemoryQualityRunner.run_inheritance()`。

**依赖：** 生产 Agent、Provider、Memory Manager、Context Manager、临时目录。

**覆盖需求：** F9、F10、F11、F12、F13、F14、F15。

### 报告与 CLI
**职责：** 输出机器可读和人类可读结果，记录运行元数据，执行在线验收阈值和退出码策略。

**对外接口：** `write_memory_quality_json()`、`write_memory_quality_markdown()`、`run_memory_eval.py`。

**依赖：** 数据加载、Runner、Provider 工厂。

**覆盖需求：** F8、F12、F13、F14。

## 模块交互

### 生产记忆更新
```text
AgentLoopRunner 自然完成
  → SessionMemoryManager.schedule_update(job)
  → MemoryNoteUpdater.extract(job, provider)
  → 模型返回 MemoryCandidate JSON
  → MemoryCandidateValidator.validate(...)
  → MemoryExtractionResult
  → MemoryNoteUpdater.apply(...)
  → MemoryNoteStore 写入/替代
  → MemoryIndexBuilder 重建受影响索引
  → SessionMemoryManager 更新运行时 KnowledgeContext
  → 下一轮 PromptBuilder 注入新索引
```

### 提取质量评测
```text
extraction.json
  → DatasetLoader 校验人工标签
  → 每个 case 构造 MemoryUpdateJob
  → 生产 MemoryNoteUpdater.extract（不落盘）
  → ExtractionMatcher 一对一匹配
  → 聚合 TP/FP/FN 与关键偏好子集
  → JSON/Markdown 报告
```

### 空白新会话成对评测
```text
inheritance.json
  ├→ enabled 临时目录
  │   → 来源会话自然完成并等待记忆落盘
  │   → 新 manager + new_session=True
  │   → 验证 messages == []，运行目标首轮
  └→ baseline 临时目录
      → memory.enabled=False + 空白会话
      → 运行同一目标首轮

两次 Trial
  → required/forbidden/restatement 断言
  → 首轮理解率 + 背景重述减少率
  → JSON/Markdown 报告
```

## 文件组织
```text
julycode/
├── src/julycode/
│   ├── config.py                         — 解析关键偏好置信阈值
│   ├── memory/
│   │   ├── __init__.py                   — 导出新增生产数据结构
│   │   ├── models.py                     — 扩展配置、MemoryNote 与提取结果模型
│   │   ├── extraction.py                 — 候选解析、证据/长期性/关键偏好/敏感校验
│   │   ├── updater.py                    — extract/apply/update 编排与提取 Prompt
│   │   ├── notes.py                      — 新元信息兼容读写、敏感检查和删除
│   │   └── index.py                      — 关键偏好优先索引
│   └── prompting/builder.py              — 长期记忆使用边界和免重复询问提示
├── eval/
│   ├── run_memory_eval.py                — 专项评测 CLI
│   ├── README.md                         — 专项评测命令、成本和报告说明
│   ├── cases/memory_quality/
│   │   ├── extraction.json               — 120+ 人工标注提取用例
│   │   └── inheritance.json              — 20+ 空白新会话成对用例
│   └── memory_quality/
│       ├── __init__.py
│       ├── models.py                     — 数据集、单例结果、聚合报告模型
│       ├── loader.py                     — JSON 加载与规模/证据校验
│       ├── matching.py                   — 一对一匹配与 Precision/Recall/F1
│       ├── offline.py                    — 确定性专项 Provider
│       ├── runner.py                     — 提取和跨会话执行器
│       └── report.py                     — JSON/Markdown 报告
├── tests/
│   ├── test_memory_extraction.py         — 候选解析及所有拒绝规则
│   ├── test_memory_updater.py            — extract/apply/update、重复和冲突
│   ├── test_memory_notes.py              — 新元信息、旧格式兼容和敏感保护
│   ├── test_memory_index.py              — 关键偏好优先索引
│   ├── test_session_recovery.py           — --new-session 空历史但加载记忆
│   ├── test_prompting.py                  — 长期记忆边界提示
│   ├── test_config.py                     — 新配置字段解析与范围检查
│   ├── test_memory_quality_loader.py      — 标签 schema、证据和数量校验
│   ├── test_memory_quality_matching.py    — TP/FP/FN、重复和关键偏好指标
│   ├── test_memory_quality_runner.py      — 离线提取与成对跨会话流程
│   └── test_memory_quality_report.py      — 报告字段、阈值和无效基线
├── tests/e2e_mock_openai_server.py        — 更新自动记忆 JSON schema 与 tmux 场景脚本
├── README.md                              — 自动记忆策略和专项验收入口
└── specs/cross-session-memory-quality/
    ├── spec.md
    ├── plan.md
    ├── task.md
    └── checklist.md
```

## 数据集设计

### extraction.json
顶层记录 `version` 和 `cases`。120 个用例按以下最低分布人工编写：

| 分组 | 最少数量 | 主要覆盖 |
|---|---:|---|
| 关键偏好正例 | 50 | 中英文、必须/禁止/默认/以后、纠正既有习惯 |
| 其他长期记忆正例 | 30 | 项目事实、长期约定、已确认决策、参考资料 |
| 不应记忆负例 | 40 | 临时格式、一次性任务、闲聊、猜测、工具输出、敏感值 |

用例 ID 使用稳定前缀 `critical_`、`memory_`、`negative_`。标注证据逐字取自用户消息；term groups 只用于正文语义核验，不替代证据核验。数据集校验按标注单元统计正例，并按 `expected == []` 统计纯负例。

### inheritance.json
至少 20 个用例，覆盖语言、测试框架、命名规则、依赖选择、兼容版本、目录约定、禁止操作和已确认架构决策。来源提示同时包含一条项目知识和一条关键用户偏好；目标提示不重复这些答案，只要求按既定约定完成一个可由关键词检查的首轮任务，并要求未知时明确询问而非猜测。

所有用例只使用临时目录，不读取当前仓库 `.julycode/memory`，也不写入真实 `~/.julycode`。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 提取方式 | LLM 候选 + 确定性保守校验 | 兼顾自然语言覆盖面和关键偏好高精度 |
| 关键偏好判断 | 用户原话、显式持续性标记、0.95 置信阈值三重门控 | 限制隐式推断和临时要求误入关键偏好 |
| 证据来源 | 只接受当前轮用户消息精确子串 | 可审计，并阻止助手猜测或工具输出变成用户偏好 |
| 敏感信息 | 校验阶段拒绝，写入阶段继续脱敏 | 满足“不产生对应记忆”，同时保留纵深保护 |
| 重复处理 | 归一化精确去重；模型通过既有 ID 表达更新/替代 | 不引入向量服务，符合本轮范围 |
| 冲突处理 | 新候选显式 `supersedes` 既有 ID，先写新再删旧 | 让最近明确纠正可审计，避免先删后写丢数据 |
| 旧笔记兼容 | 新 frontmatter 字段全部有默认值 | 升级后无需迁移即可继续读取既有记忆 |
| 索引排序 | 关键偏好优先，其余按类别和更新时间 | 提高首轮遵循概率且不改变现有体量上限 |
| 提取匹配 | 证据 + 元数据 + term group 的确定性一对一最大匹配 | 无额外 judge 成本，FP/FN 可复核，重复预测不能重复得分 |
| 跨会话定义 | 强制 `new_session=True`，不恢复消息 | 严格证明长期记忆继承而非历史恢复 |
| 背景复述基线 | 同用例关闭记忆成对运行 | 直接计算用户批准的减少率口径 |
| 评测隔离 | 每个 trial 独立项目目录和 user_dir | 防止用例间、用户真实记忆和项目真实文件互相污染 |
| 离线评测 | 根据人工标签生成确定性脚本响应 | 只验证框架，不冒充真实模型质量 |
| 在线评测 | 复用当前 Provider 配置并记录元数据 | 结果与实际 JulyCode 模型行为一致且可追溯 |
| CLI 默认模式 | offline | 完整在线验收调用量较大，避免误触成本 |
| 与通用 eval 的关系 | 独立专项包和报告，复用 Provider 元信息 | 专项集合级指标不适合现有单用例百分制，但保持报告风格一致 |

## 需求覆盖检查

| 需求 | 架构归属 | 主要验证 |
|---|---|---|
| F1 | 提取 Prompt、Validator | 临时/猜测/闲聊负例 |
| F2 | Validator 关键偏好三重门控 | 关键正例与隐式偏好负例 |
| F3 | MemoryNote、NoteStore | frontmatter 读写与报告证据 |
| F4 | Validator、NoteStore | 敏感/助手/工具负例 |
| F5 | Validator、Updater.apply、NoteStore | 重复和冲突顺序测试 |
| F6 | DatasetLoader、extraction.json | 数量与标签校验 |
| F7 | ExtractionMatcher、指标聚合 | 指标边界单元测试 |
| F8 | Report | FP/FN/分类错误报告测试 |
| F9 | Bootstrapper、跨会话 Runner | 空消息与首次请求捕获 |
| F10 | IndexBuilder、PromptBuilder | 继承类别和临时内容排除 |
| F11 | DatasetLoader、inheritance.json、Runner | 20 个成对用例完整运行 |
| F12 | Runner、Report | 理解率与减少率统计 |
| F13 | OfflineProvider、专项测试 | 无网络一次命令通过 |
| F14 | CLI、在线 Runner、Report | Provider 元数据和真实阈值 |
| F15 | Manager 错误边界、隔离目录、回归测试 | 全量 pytest 与 tmux 验收 |
