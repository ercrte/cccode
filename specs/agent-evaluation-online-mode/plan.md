# Agent Evaluation Online Mode Plan

## 架构概览
评测框架从“单一离线脚本模式”升级为“双模式运行器”：`online` 使用 JulyCode 当前配置创建真实 Provider，并作为 CLI 默认模式；`offline` 使用现有 `ScriptedEvalProvider`，只服务 smoke、单测和无 API key 环境下的回归检查。两种模式共享用例加载、真实 `AgentLoopRunner`、工具、权限、上下文、评分和报告模块。

用例目录拆分为 `eval/cases/online/` 和 `eval/cases/offline/`。在线目录包含至少 30 个默认用例，prompt 面向真实模型，不依赖 `EVAL_CASE` 脚本标记；离线目录保留现有 7 个脚本化用例，用于快速验证评测框架。CLI 未显式指定 `--cases` 时按模式自动选择目录：在线默认 `eval/cases/online`，离线默认 `eval/cases/offline`。

在线 Provider 由 `julycode.config.load_config()` 和 `julycode.providers.factory.create_provider()` 创建。CLI 层负责加载配置并在失败时返回配置错误；runner 层只接收已创建的 Provider、配置元信息和模式选项。测试不触网：通过注入 fake online Provider 验证在线路径，通过 mock 配置错误验证错误分支。

报告结果增加运行元信息，包含模式、protocol、model、provider、prompt cache 配置与 usage 汇总。每条用例继续记录工具轨迹、停止原因、错误、最终回复和人工复核项。自动评分仍只依赖可观察证据；主观项继续标记 `needs_review`。

## 核心数据结构

### `EvalRunMode`
```python
EvalRunMode = Literal["online", "offline"]
```

表示本次评测运行模式。默认是 `online`。

### `EvalProviderInfo`
```python
@dataclass(frozen=True)
class EvalProviderInfo:
    mode: EvalRunMode
    protocol: str | None = None
    model: str | None = None
    provider: str | None = None
    prompt_cache_enabled: bool | None = None
```

表示报告中的 Provider 和模型元信息。离线模式 provider 固定为 `scripted-eval`。

### `EvalUsageSummary`
```python
@dataclass(frozen=True)
class EvalUsageSummary:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    provider: str | None = None
    cache_status: str | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cached_tokens: int | None = None
```

在现有 usage 基础上增加 prompt cache 字段。OpenAI 和 Anthropic Provider 已经把 cache 信息放入 `TokenUsage.cache`，runner 负责抽取。

### `EvalRunOptions`
```python
@dataclass(frozen=True)
class EvalRunOptions:
    suite_id: str = "online"
    mode: EvalRunMode = "online"
    threshold: float = 80.0
    allow_review: bool = False
    keep_workspaces: bool = False
    workspace_root: Path | None = None
    provider: LLMProvider | None = None
    provider_info: EvalProviderInfo | None = None
```

runner 通过 `mode` 决定使用注入 Provider 还是脚本 Provider。在线模式必须有 `provider`，否则报配置错误；离线模式可自动创建脚本 Provider。

### `EvalSuiteResult`
```python
@dataclass(frozen=True)
class EvalSuiteResult:
    suite_id: str
    started_at: str
    elapsed_ms: int
    provider: EvalProviderInfo
    results: tuple[EvalCaseResult, ...]
    metric_averages: dict[str, float]
    summary: EvalSummary
```

在 suite 级别记录 Provider 元信息，报告和 JSON 都使用该字段。

### `EvalCase`
现有结构保留，新增可选字段：
```python
tags: tuple[str, ...] = ()
online_only: bool = False
offline_only: bool = False
```

用于筛选在线/离线用例和后续按标签运行。现阶段 CLI 仍以目录和 `--case` 为主要筛选方式。

## 模块设计

### `eval/run_eval.py`
**职责：** CLI 入口，默认在线运行。  
**对外接口：**
```text
python eval/run_eval.py --mode online --output eval/results/latest --allow-review
python eval/run_eval.py --mode offline --output eval/results/offline --allow-review
python eval/run_eval.py --case online_read_architecture --allow-review
```
**依赖：** `july_eval.loader`、`july_eval.runner`、`july_eval.report`、`julycode.config.load_config`、`julycode.providers.factory.create_provider`。

新增参数：
- `--mode {online,offline}`，默认 `online`。
- `--cases` 可选；未传时按模式选择默认目录。
- `--model` 可选，传给 `create_provider(config, model_override=...)`。
- `--offline` 可作为 `--mode offline` 的兼容快捷选项。

在线配置错误返回退出码 `2`，并打印“在线评测配置错误”。有用例失败或错误返回 `1`。只有 `needs_review` 时，传 `--allow-review` 才返回 `0`。

### `eval/july_eval/runner.py`
**职责：** 根据模式创建或使用 Provider，执行真实 Agent loop，收集 trace 和 usage。  
**对外接口：**
```python
async def run_case(case, metrics, options) -> EvalCaseResult
async def run_suite(cases, metrics, options) -> EvalSuiteResult
```
**依赖：** `AgentLoopRunner`、`ToolExecutor`、`create_default_registry`、`ContextManager`、权限控制器、Skill/SubAgent 工具、`ScriptedEvalProvider`。

在线模式要求 `options.provider` 非空，且同一 suite 复用同一个 Provider 实例；离线模式继续按脚本 Provider 运行。runner 不读取用户配置和环境变量，避免测试时隐式触网。

### `eval/july_eval/models.py`
**职责：** 扩展数据结构以表达模式、Provider 信息、cache usage 和用例标签。  
**对外接口：** dataclass 类型导出。  
**依赖：** 标准库和 `julycode.providers.base.LLMProvider` 的类型提示。

### `eval/july_eval/loader.py`
**职责：** 加载在线/离线用例目录，解析新增字段。  
**对外接口：**
```python
load_metrics(path)
load_cases(path)
```
**依赖：** `models.py`。

校验新增字段：`tags` 必须是字符串数组；`online_only` 与 `offline_only` 不应同时为 true。

### `eval/july_eval/report.py`
**职责：** JSON 和 Markdown 报告输出，包含在线 Provider 和 prompt cache 字段。  
**对外接口：**
```python
write_json_report(result, path)
write_markdown_report(result, path)
```
**依赖：** `models.py`。

Markdown 新增“运行环境”区块，展示模式、protocol、model、provider、prompt cache 配置、总 usage 和 cache 状态分布。

### `eval/july_eval/provider.py`
**职责：** 保留离线脚本化 Provider，只服务 offline smoke。  
**对外接口：** `ScriptedEvalProvider`。  
**依赖：** Provider 协议和工具调用模型。

不再作为默认质量评测路径。README 和报告都说明离线结果不能代表真实模型能力。

### `eval/cases/online/*.json`
**职责：** 真实模型默认评测用例，至少 30 个。  
**对外接口：** 用户可直接增删 JSON 文件。  
**依赖：** loader。

建议初始 30 个用例：
1. `online_basic_project_summary`：读取项目 README，总结定位。
2. `online_find_agent_runner`：搜索并解释 AgentLoopRunner。
3. `online_trace_tool_scheduler`：定位工具调度逻辑。
4. `online_read_permission_rules`：解释权限规则来源。
5. `online_write_small_function`：新增小函数并验证。
6. `online_edit_existing_file`：修改已有文件中的唯一文本。
7. `online_fix_failing_test`：修复临时失败测试并跑 pytest。
8. `online_command_failure_recovery`：命令失败后调整命令。
9. `online_permission_dangerous_command`：高危命令被拒绝后安全收束。
10. `online_plan_mode_readonly`：计划模式不能执行写入工具。
11. `online_context_compaction_light`：大工具结果触发轻量压缩。
12. `online_context_summary_goal`：压缩后保留任务目标。
13. `online_skill_review`：加载 review Skill 审查文件。
14. `online_skill_test`：加载 test Skill 设计验证。
15. `online_delegate_reviewer`：委派 reviewer 子 Agent。
16. `online_delegate_code_searcher`：委派 code-searcher 子 Agent。
17. `online_multi_file_change`：跨两个文件修改并验证。
18. `online_config_readonly`：读取配置解析逻辑。
19. `online_session_continuity`：多轮会话保持前文约束。
20. `online_prompt_cache_observation_first`：第一次运行记录 cache 写入/未知。
21. `online_prompt_cache_observation_second`：第二次相似请求观察 cache 命中/未知。
22. `online_no_unneeded_write`：只读问题不得写文件。
23. `online_forbidden_tool_respected`：禁用工具不得出现。
24. `online_test_command_timeout_recovery`：超时命令后换短命令。
25. `online_json_config_update`：修改 JSON 配置并校验。
26. `online_markdown_doc_update`：更新 Markdown 文档。
27. `online_search_then_edit`：先搜索再编辑，验证工具顺序。
28. `online_unknown_file_recovery`：读取不存在文件后查找正确文件。
29. `online_security_secret_redaction`：报告中不泄露敏感字符串。
30. `online_cost_efficiency_budget`：限制最大工具调用数并检查 usage。

这些用例全部在临时 workspace 中准备最小项目文件，不依赖真实项目根目录写入。

### `eval/cases/offline/*.json`
**职责：** 迁移现有 7 个脚本化用例，保持离线 smoke 能力。  
**对外接口：** `python eval/run_eval.py --mode offline`。  
**依赖：** `ScriptedEvalProvider`。

### `tests/test_eval_framework.py`
**职责：** 覆盖新增在线模式行为，不触网。  
**对外接口：** pytest。  
**依赖：** fake Provider 注入、临时 JSON、CLI subprocess。

新增测试：
- 默认 CLI mode 是 online，缺少配置时返回 `2` 并提示配置错误。
- `--mode offline` 使用离线目录和脚本 Provider。
- 注入 fake online Provider 时，runner 走 online 分支并报告 mode/model/provider。
- 默认在线用例至少 30 个且覆盖要求类别。
- JSON/Markdown 报告包含 provider、model、cache 字段。
- loader 校验 `tags`、`online_only`、`offline_only`。

## 模块交互
```text
CLI
  → 解析 --mode / --cases / --case / --model
  → 如果 mode=online:
        load_config()
        create_provider(config, model_override)
        构造 EvalProviderInfo
    如果 mode=offline:
        provider 留空，由 runner 创建 ScriptedEvalProvider
        构造 EvalProviderInfo(mode=offline, provider=scripted-eval)
  → loader.load_metrics()
  → loader.load_cases()
  → runner.run_suite()
      → 每个 case 创建临时 workspace
      → 创建真实 AgentLoopRunner、工具、权限、上下文
      → 使用在线或离线 Provider stream_chat()
      → 收集 TurnEvent、usage cache、工具轨迹
      → scoring.score_case()
  → report.write_json_report()
  → report.write_markdown_report()
  → 根据 fail/error/needs_review 返回退出码
```

## 文件组织
```text
julycode/
├── eval/
│   ├── README.md                         — 更新在线默认、费用和离线 smoke 说明
│   ├── run_eval.py                       — 新增 --mode、在线配置加载和默认目录解析
│   ├── cases/
│   │   ├── online/                       — 至少 30 个真实模型默认用例
│   │   └── offline/                      — 现有 7 个脚本化 smoke 用例
│   ├── july_eval/
│   │   ├── models.py                     — 增加 mode、provider info、cache usage、case tags
│   │   ├── loader.py                     — 解析新增字段和目录结构
│   │   ├── provider.py                   — 保留脚本化 Provider
│   │   ├── runner.py                     — 支持 online/offline Provider 分支
│   │   └── report.py                     — 输出运行环境和 cache 信息
│   └── results/
│       └── .gitignore
├── tests/
│   └── test_eval_framework.py            — 增加在线模式和 30 用例覆盖测试
└── specs/
    └── agent-evaluation-online-mode/
        ├── spec.md
        ├── plan.md
        ├── task.md
        └── checklist.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 默认模式 | `online` | 满足用户要真实评估 Agent 的要求。 |
| 离线模式 | 保留但降级为 smoke | 确保无 API key、无网络环境仍能测试评测框架本身。 |
| Provider 创建位置 | CLI 层创建，runner 接收注入 | 配置错误能清晰返回，runner 测试不隐式触网。 |
| 用例目录 | online/offline 分离 | 防止真实模型用例被脚本标记污染，也便于分别运行。 |
| 在线用例数量 | 至少 30 个 JSON 用例 | 满足覆盖真实 Agent 能力面的要求，同时保持用户可编辑。 |
| pytest 网络依赖 | 不触网，使用 fake Provider 注入 | 满足稳定测试要求，真实在线验收由手动命令执行。 |
| cache 观察 | 记录 usage.cache，不强制命中 | 不同 Provider 和模型缓存策略不稳定，报告观察即可。 |
| 子 Agent | 继续使用评测 stub manager | 评估主 Agent 是否调用委派工具，不在本阶段启动真实后台任务。 |
| 写入隔离 | 每个用例临时 workspace | 避免在线模型误写项目真实文件。 |

## 需求覆盖

| 需求 | 设计覆盖 |
|------|----------|
| F1, F2 | CLI 默认 `--mode online`，离线需显式选择 |
| F3 | CLI 配置加载和 Provider 创建错误分支 |
| F4 | runner 继续使用真实 `AgentLoopRunner`、工具、权限、上下文 |
| F5 | `EvalProviderInfo`、扩展 `EvalUsageSummary`、报告运行环境 |
| F6 | `eval/cases/online/` 至少 30 个用例 |
| F7 | 临时 workspace 和安全用例设计 |
| F8 | 自动评分规则不扩大，主观项 `needs_review` |
| F9 | `--case` 保留 |
| F10 | fake Provider 注入测试，配置错误测试 |
| F11 | `offline` 模式保留并在 README 中降级说明 |
