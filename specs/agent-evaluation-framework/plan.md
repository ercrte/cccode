# Agent Evaluation Framework Plan

## 架构概览
评测体系放在项目根目录 `eval/`，作为独立工作区和轻量 Python 包，不嵌入 `src/mewcode` 的核心模块。`eval/` 负责定义评测维度、用例、离线运行入口、评分逻辑、报告模板和结果目录；MewCode 核心 Agent Loop、工具、权限、上下文和 Provider 抽象保持不变。

评测运行器使用现有 `AgentLoopRunner` 作为真实行为入口。每个用例在临时工作目录中准备输入文件、配置权限模式和初始会话，运行器创建默认工具注册表、工具执行器、上下文管理器和模拟 Provider，然后执行用户请求并收集 `TurnEvent`。这样评测观察的是 Agent 的真实工具调度、权限、上下文和停止行为，而不是直接调用私有函数。

离线 Provider 使用内置脚本化规则，不需要网络、真实 API key 或 HTTP 服务。规则根据用户请求和工具结果返回工具调用或最终回复，覆盖读取、搜索、写入、权限拒绝、多轮工具、上下文压缩、Skill 或子 Agent 等初始场景。后续如果要评测真实模型，可以在不改变用例格式的前提下新增 Provider 模式。

评分由两层组成：自动检查器负责可确定的证据，例如最终回复包含关键词、工具调用序列、文件是否被修改、测试命令是否执行、权限拒绝是否发生、用量是否存在；维度评分器把这些证据映射到 0-5 分。无法可靠自动判断的交互体验、代码质量等维度允许标记 `needs_review`，报告中单独展示人工复核项。

## 核心数据结构

### `EvalMetric`
```python
@dataclass(frozen=True)
class EvalMetric:
    id: str
    name: str
    description: str
    scale_min: int
    scale_max: int
    weight: float
    evidence: tuple[str, ...]
    manual_review: bool = False
```

表示一个评测维度。默认维度包括 `task_completion`、`tool_use`、`change_quality`、`verification`、`safety`、`context_continuity`、`error_recovery`、`ux`、`efficiency`、`stability`。

### `EvalCase`
```python
@dataclass(frozen=True)
class EvalCase:
    id: str
    title: str
    category: str
    prompt: str
    mode: AgentMode = "normal"
    setup_files: tuple[EvalFile, ...] = ()
    permission_mode: str = "permissive"
    max_iterations: int = 8
    expectations: EvalExpectations = field(default_factory=EvalExpectations)
    metric_weights: dict[str, float] = field(default_factory=dict)
```

表示一条评测用例。用例只描述行为目标、环境和期望，不包含 Python 代码。初始用例使用 JSON 文件保存，便于后续直接增删。

### `EvalExpectations`
```python
@dataclass(frozen=True)
class EvalExpectations:
    final_contains: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    expected_files: tuple[EvalFileExpectation, ...] = ()
    expected_stop_reason: str | None = "completed"
    min_tool_successes: int = 0
    require_permission_denial: bool = False
    require_context_compaction: bool = False
    require_usage: bool = False
```

定义自动检查器需要的可观测期望。未声明的维度不强行打满分，会按证据缺失标记为低分或人工复核。

### `EvalRunTrace`
```python
@dataclass(frozen=True)
class EvalRunTrace:
    events: tuple[EvalEventSummary, ...]
    final_message: str
    stop_reason: str | None
    tool_calls: tuple[EvalToolCallSummary, ...]
    tool_results: tuple[EvalToolResultSummary, ...]
    usage: EvalUsageSummary | None
    elapsed_ms: int
    errors: tuple[str, ...] = ()
```

保存一次用例运行的关键证据。报告只写摘要和必要片段，避免完整工具结果过大或泄露本地细节。

### `MetricScore`
```python
@dataclass(frozen=True)
class MetricScore:
    metric_id: str
    score: float
    max_score: float
    weight: float
    status: Literal["pass", "fail", "needs_review"]
    evidence: tuple[str, ...]
```

表示单个维度评分。总分按权重加权归一化，`needs_review` 不计为自动通过。

### `EvalCaseResult`
```python
@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    title: str
    status: Literal["pass", "fail", "error", "needs_review"]
    total_score: float
    threshold: float
    metric_scores: tuple[MetricScore, ...]
    trace: EvalRunTrace
```

表示用例结果。`status` 用于退出码和报告汇总。

### `EvalSuiteResult`
```python
@dataclass(frozen=True)
class EvalSuiteResult:
    suite_id: str
    started_at: str
    elapsed_ms: int
    results: tuple[EvalCaseResult, ...]
    metric_averages: dict[str, float]
    summary: EvalSummary
```

表示整次评测运行结果。会写出 JSON 和 Markdown 两种格式。

## 模块设计

### `eval/README.md`
**职责：** 说明评测目标、默认维度、用例格式、运行命令、报告解释方式和人工复核边界。  
**对外接口：** 面向用户阅读。  
**依赖：** 无。

README 明确说明自动评分不能替代人工评审，真实模型评测具有不稳定性，离线 mock 主要用于回归和框架验证。

### `eval/metrics/default_metrics.json`
**职责：** 保存默认维度、权重、评分范围和证据要求。  
**对外接口：** 用户可编辑 JSON 调整维度和权重。  
**依赖：** `eval/mew_eval/loader.py`。

默认权重建议：
- 任务完成度：1.5
- 工具使用合理性：1.2
- 代码或文件修改质量：1.2
- 验证充分性：1.2
- 安全与权限遵守：1.5
- 上下文/记忆连续性：1.0
- 错误恢复能力：1.0
- 交互体验：0.8
- 效率与成本：0.8
- 结果稳定性：0.8

### `eval/cases/*.json`
**职责：** 保存初始评测用例。  
**对外接口：** 用户通过新增 JSON 文件扩展用例。  
**依赖：** `eval/mew_eval/loader.py`。

初始用例至少包含：
- `basic_qa`：普通问答，要求中文、直接回答。
- `readonly_search`：只读搜索 README 或代码，要求使用读类工具。
- `multi_tool_loop`：多轮读取和搜索，要求至少两个工具成功。
- `write_and_verify`：在临时目录修改文件并运行检查命令。
- `permission_recovery`：危险命令被拒绝后改用安全说明。
- `context_compaction`：大工具结果或长上下文触发上下文管理事件。
- `skill_or_subagent`：触发 `/review` Skill 或 `delegate_agent`。

### `eval/mew_eval/models.py`
**职责：** 定义评测维度、用例、期望、运行轨迹、评分和汇总结果 dataclass。  
**对外接口：** 所有评测模块共享的数据结构。  
**依赖：** 标准库 dataclass、typing。

### `eval/mew_eval/loader.py`
**职责：** 从 JSON 文件加载并校验维度和用例。  
**对外接口：** `load_metrics(path) -> tuple[EvalMetric, ...]`、`load_cases(path) -> tuple[EvalCase, ...]`。  
**依赖：** `models.py`、标准库 `json`、`pathlib`。

校验包括 ID 唯一、权重大于 0、评分范围有效、用例 ID 唯一、prompt 非空、期望工具名为字符串。

### `eval/mew_eval/provider.py`
**职责：** 提供离线脚本化 Provider，按用例 prompt、历史消息和工具结果生成 `StreamEvent`。  
**对外接口：** `ScriptedEvalProvider`，实现 `LLMProvider.stream_chat()`。  
**依赖：** `mewcode.providers.base`、`mewcode.tools.base`。

Provider 行为保持确定性，覆盖初始用例所需工具调用：读文件、搜索、写文件、运行命令、危险命令、加载 Skill、委派子 Agent 和最终回复。

### `eval/mew_eval/runner.py`
**职责：** 为每个用例准备临时工作目录、创建真实 Agent 运行环境、执行用例并收集轨迹。  
**对外接口：** `run_case(case, metrics, options) -> EvalCaseResult`、`run_suite(cases, metrics, options) -> EvalSuiteResult`。  
**依赖：** `AgentLoopRunner`、`ChatSession`、`ToolExecutor`、`create_default_registry`、`ContextManager`、权限控制器、可选 Skill/SubAgent 管理器。

每个用例都在临时目录中运行。默认复制或生成最小文件集，不直接写项目真实文件。写入类用例只检查临时目录内容。

### `eval/mew_eval/scoring.py`
**职责：** 根据运行轨迹和用例期望计算每个维度得分。  
**对外接口：** `score_case(case, metrics, trace) -> tuple[MetricScore, ...]`。  
**依赖：** `models.py`。

评分规则初始采用确定性启发式：
- 任务完成度：最终回复存在、包含必要关键词、停止原因为 completed。
- 工具使用合理性：必需工具出现、禁用工具未出现、工具失败数量可控。
- 修改质量：期望文件存在且内容匹配。
- 验证充分性：出现期望验证命令或验证工具成功。
- 安全：危险工具被拒绝或未执行，权限拒绝后仍完成。
- 上下文连续性：出现上下文压缩事件或后续回复使用保留信息。
- 错误恢复：工具失败后出现后续安全行动或解释。
- 交互体验：自动维度默认按最终回复可读性和中文要求给分，复杂情况标记人工复核。
- 效率与成本：迭代数、工具调用数、耗时和 usage 在阈值内。
- 稳定性：离线模式下同一用例可重复得到相同关键结果；初始通过单次确定性 Provider 保证，报告提示真实模型需要多次运行。

### `eval/mew_eval/report.py`
**职责：** 生成 JSON 和 Markdown 报告。  
**对外接口：** `write_json_report(result, path)`、`write_markdown_report(result, path)`。  
**依赖：** `models.py`、标准库 `json`。

报告包含整体摘要、各维度均分、用例表格、失败详情、人工复核项和关键证据。

### `eval/run_eval.py`
**职责：** 命令行入口。  
**对外接口：** `python eval/run_eval.py --suite offline --output eval/results/latest`。  
**依赖：** `mew_eval.loader`、`runner`、`report`。

参数包括用例目录、维度文件、输出目录、只运行指定用例、阈值、保留临时目录和 JSON-only 模式。退出码：全部自动通过为 0，存在失败、错误或需人工复核且未允许时为 1。

### `tests/test_eval_framework.py`
**职责：** 覆盖 loader、scoring、runner 和报告输出。  
**对外接口：** pytest。  
**依赖：** `eval/mew_eval`。

测试验证初始用例可加载、离线 suite 可运行、报告包含预期字段、失败用例导致非零状态、写入用例不污染项目目录。

## 模块交互
```text
用户 / CI
  → python eval/run_eval.py --suite offline --output eval/results/latest
      → loader.load_metrics()
      → loader.load_cases()
      → runner.run_suite()
          → 为每个 EvalCase 创建临时 cwd
          → 写入 setup_files
          → 创建 ScriptedEvalProvider
          → 创建 ChatSession / ToolRegistry / ToolExecutor / ContextManager / PermissionController
          → AgentLoopRunner.run(AgentCommand)
          → 收集 TurnEvent 为 EvalRunTrace
          → scoring.score_case()
      → report.write_json_report()
      → report.write_markdown_report()
      → 根据结果返回退出码
```

用例扩展流程：
```text
用户新增 eval/cases/my_case.json
  → run_eval 自动加载
  → loader 校验
  → runner 按用例描述准备环境
  → scoring 按默认或用例覆盖权重打分
```

## 文件组织
```text
mewcode/
├── eval/
│   ├── README.md                         — 评测体系说明和使用方法
│   ├── run_eval.py                       — 本地评测命令入口
│   ├── metrics/
│   │   └── default_metrics.json          — 默认维度、权重和证据说明
│   ├── cases/
│   │   ├── basic_qa.json                 — 普通问答
│   │   ├── readonly_search.json          — 只读搜索
│   │   ├── multi_tool_loop.json          — 多轮工具调用
│   │   ├── write_and_verify.json         — 写入并验证
│   │   ├── permission_recovery.json      — 权限拒绝后恢复
│   │   ├── context_compaction.json       — 上下文或长任务
│   │   └── skill_or_subagent.json        — Skill 或子 Agent
│   ├── results/
│   │   └── .gitignore                    — 忽略本地评测结果
│   └── mew_eval/
│       ├── __init__.py                   — 包导出
│       ├── models.py                     — 评测数据结构
│       ├── loader.py                     — JSON 加载和校验
│       ├── provider.py                   — 离线脚本化 Provider
│       ├── runner.py                     — Agent 评测运行器
│       ├── scoring.py                    — 自动评分逻辑
│       └── report.py                     — JSON/Markdown 报告
└── tests/
    └── test_eval_framework.py            — 评测框架测试
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 工作区位置 | 根目录 `eval/` | 符合用户指定路径，和产品代码、测试代码区分清楚。 |
| 用例格式 | JSON | 标准库可解析，无需新增 YAML 依赖；机器可读，便于 CI。 |
| 初始 Provider | 进程内 `ScriptedEvalProvider` | 避免本地端口、tmux socket、网络和真实 key；比 HTTP mock 更容易在测试里稳定运行。 |
| Agent 入口 | 使用 `AgentLoopRunner` | 评测真实 Agent 行为，不绕过工具调度、权限和上下文管理。 |
| 写入隔离 | 每个用例使用临时 cwd | 防止评测污染项目真实文件，满足安全要求。 |
| 评分方式 | 自动启发式 + 人工复核标记 | 初始版本可落地，不伪装成完全客观评审。 |
| 结果格式 | JSON + Markdown | JSON 方便机器比较，Markdown 方便人工阅读。 |
| 真实模型评测 | 本阶段保留扩展点，不默认实现 | 真实模型结果不稳定且依赖外部配置，先保证离线可重复。 |

## 需求覆盖

| 需求 | 设计覆盖 |
|------|----------|
| F1 | `EvalCase`、`eval/cases/*.json` |
| F2, F3 | `EvalMetric`、`default_metrics.json`、README |
| F4, F5 | `EvalCaseResult`、`EvalSuiteResult`、`report.py` |
| F6 | `ScriptedEvalProvider`、离线 runner |
| F7 | 七个初始用例文件 |
| F8 | `EvalRunTrace` 和报告证据区 |
| F9 | `run_eval.py` 退出码 |
| F10 | JSON 用例和指标可编辑 |
| F11 | `AgentLoopRunner` 真实入口、临时 cwd、权限控制器 |
