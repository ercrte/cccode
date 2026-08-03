# Agent Evaluation Framework Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `eval/README.md` | 评测体系说明、维度解释、运行方法和人工复核边界 |
| 新建 | `eval/run_eval.py` | 本地离线评测命令入口 |
| 新建 | `eval/metrics/default_metrics.json` | 默认评测维度、权重、评分范围和证据说明 |
| 新建 | `eval/cases/basic_qa.json` | 普通问答用例 |
| 新建 | `eval/cases/readonly_search.json` | 只读搜索用例 |
| 新建 | `eval/cases/multi_tool_loop.json` | 多轮工具调用用例 |
| 新建 | `eval/cases/write_and_verify.json` | 写入并验证用例 |
| 新建 | `eval/cases/permission_recovery.json` | 权限拒绝后恢复用例 |
| 新建 | `eval/cases/context_compaction.json` | 上下文或长任务用例 |
| 新建 | `eval/cases/skill_or_subagent.json` | Skill 或子 Agent 用例 |
| 新建 | `eval/results/.gitignore` | 忽略本地评测产物 |
| 新建 | `eval/july_eval/__init__.py` | 评测包导出 |
| 新建 | `eval/july_eval/models.py` | 评测数据结构 |
| 新建 | `eval/july_eval/loader.py` | JSON 加载与校验 |
| 新建 | `eval/july_eval/provider.py` | 离线脚本化 Provider |
| 新建 | `eval/july_eval/runner.py` | Agent 评测运行器 |
| 新建 | `eval/july_eval/scoring.py` | 自动评分逻辑 |
| 新建 | `eval/july_eval/report.py` | JSON/Markdown 报告生成 |
| 新建 | `tests/test_eval_framework.py` | 评测框架单元和集成测试 |

## T1: 增加评测模型测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** 无  
**步骤：**
1. 添加测试导入 `EvalMetric`、`EvalCase`、`EvalExpectations`、`EvalRunTrace`、`MetricScore`、`EvalCaseResult`、`EvalSuiteResult`。
2. 构造最小评测用例和评分结果，断言 dataclass 字段和默认值符合 plan。
3. 断言 `EvalExpectations.expected_stop_reason` 默认是 `completed`，空工具期望为 tuple。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py::test_eval_models_have_expected_defaults -q`，期望先失败，失败点为缺少 `eval.july_eval.models`。

## T2: 实现评测数据结构

**文件：** `eval/july_eval/__init__.py`、`eval/july_eval/models.py`  
**依赖：** T1  
**步骤：**
1. 创建 `eval/july_eval/` 包和 `__init__.py`。
2. 在 `models.py` 定义 plan 中所有 dataclass，并补充 `EvalFile`、`EvalFileExpectation`、`EvalEventSummary`、`EvalToolCallSummary`、`EvalToolResultSummary`、`EvalUsageSummary`、`EvalSummary`、`EvalRunOptions`。
3. 使用 tuple 默认值和不可变 dataclass，避免运行时共享可变状态。
4. 在 `__init__.py` 导出主要类型。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py::test_eval_models_have_expected_defaults -q`，期望通过。

## T3: 增加指标和用例加载测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** T2  
**步骤：**
1. 添加测试从临时 JSON 文件加载两个 metric，断言 ID、权重、评分范围和人工复核字段。
2. 添加测试从临时目录加载两个 case，断言按文件名稳定排序、setup_files、expectations 和 metric_weights 被解析。
3. 添加非法 JSON 结构测试，覆盖重复 ID、空 prompt、非法权重、非法评分范围和非字符串工具名。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "load_metrics or load_cases or invalid_eval" -q`，期望先失败，失败点为缺少 loader。

## T4: 实现 JSON 加载和校验

**文件：** `eval/july_eval/loader.py`  
**依赖：** T3  
**步骤：**
1. 实现 `load_metrics(path)`，支持文件路径，返回 `tuple[EvalMetric, ...]`。
2. 实现 `load_cases(path)`，支持文件或目录，目录按 `*.json` 文件名排序合并。
3. 实现字段解析、默认值、tuple 转换和错误提示。
4. 对 ID 唯一性、非空 prompt、权重大于 0、评分范围有效、工具名类型进行校验。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "load_metrics or load_cases or invalid_eval" -q`，期望通过。

## T5: 增加默认指标和初始用例文件

**文件：** `eval/metrics/default_metrics.json`、`eval/cases/*.json`、`tests/test_eval_framework.py`  
**依赖：** T4  
**步骤：**
1. 创建默认指标 JSON，包含 10 个维度及权重、评分范围、证据说明。
2. 创建 7 个初始用例 JSON，覆盖 spec 要求的场景类别。
3. 添加测试加载仓库内默认指标和用例，断言指标 ID 集合完整、用例类别至少覆盖六类场景。
4. 断言写入类用例有 `setup_files` 或 `expected_files`，权限恢复用例要求权限拒绝证据。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py::test_default_metrics_and_cases_cover_required_dimensions -q`，期望通过。

## T6: 增加离线 Provider 测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** T2  
**步骤：**
1. 添加测试直接调用 `ScriptedEvalProvider.stream_chat()`，普通问答返回文本和 usage。
2. 添加测试读取场景返回 `read_file` 工具调用，多轮工具结果后返回最终回复。
3. 添加测试危险命令场景返回 `run_command` 工具调用，便于后续权限拒绝测试。
4. 添加测试 Provider 行为确定性：相同请求两次得到相同关键事件类型和工具名。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k scripted_provider -q`，期望先失败，失败点为缺少 provider。

## T7: 实现离线脚本化 Provider

**文件：** `eval/july_eval/provider.py`  
**依赖：** T6  
**步骤：**
1. 实现 `ScriptedEvalProvider`，符合 `LLMProvider.stream_chat()` 协议。
2. 根据最后一条用户消息和已有 tool 结果返回工具调用或最终回复。
3. 覆盖初始用例需要的 read/search/write/run/load_skill/delegate_agent 行为。
4. 每次模型响应返回稳定 usage，便于效率和成本评分。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k scripted_provider -q`，期望通过。

## T8: 增加评分逻辑测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** T2、T4、T5  
**步骤：**
1. 构造成功 trace，断言任务完成度、工具使用、安全、效率得分为通过。
2. 构造缺少必需工具的 trace，断言 `tool_use` 失败且 evidence 说明缺失工具。
3. 构造需要人工复核的 metric，断言状态为 `needs_review`。
4. 构造期望文件不匹配、权限拒绝缺失、上下文压缩缺失等失败分支。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "score_case or metric_score" -q`，期望先失败，失败点为缺少 scoring。

## T9: 实现自动评分器

**文件：** `eval/july_eval/scoring.py`  
**依赖：** T8  
**步骤：**
1. 实现 `score_case(case, metrics, trace, workspace=None)`。
2. 按 plan 的启发式规则计算每个 `MetricScore`。
3. 实现总分辅助函数，按权重归一化到 0-100。
4. 对人工复核 metric 返回 `needs_review`，并在 evidence 中说明原因。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "score_case or metric_score" -q`，期望通过。

## T10: 增加 Runner 集成测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** T5、T7、T9  
**步骤：**
1. 添加测试运行单个 `basic_qa` 用例，断言产生 `EvalCaseResult`、最终回复、usage 和 pass 状态。
2. 添加测试运行 `write_and_verify`，断言临时目录文件被修改，项目根目录未产生目标文件。
3. 添加测试运行 `permission_recovery`，断言 trace 中有权限拒绝证据且最终状态为 pass 或 needs_review。
4. 添加测试运行一个小 suite，断言 summary 统计和 metric_averages 正确。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "run_case or run_suite" -q`，期望先失败，失败点为缺少 runner。

## T11: 实现 Agent 评测运行器

**文件：** `eval/july_eval/runner.py`  
**依赖：** T10  
**步骤：**
1. 实现临时 workspace 准备，写入 `setup_files`。
2. 创建 `ChatSession`、`ScriptedEvalProvider`、`create_default_registry()`、`ToolExecutor`、`ContextManager`、`PermissionController`。
3. 用 `AgentLoopRunner.run()` 执行 `AgentCommand`，收集 progress、usage、tool_started、tool_finished、context_compacted、message_done、error 等事件摘要。
4. 调用 `score_case()` 生成 `EvalCaseResult`，并由 `run_suite()` 汇总。
5. 捕获框架错误，生成 `status="error"` 的结果而不是崩溃。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "run_case or run_suite" -q`，期望通过。

## T12: 增加报告生成测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** T11  
**步骤：**
1. 添加测试生成 JSON 报告，断言包含 suite summary、case results、metric scores 和 trace evidence。
2. 添加测试生成 Markdown 报告，断言包含总体摘要、维度均分、失败用例、人工复核项和关键证据。
3. 添加测试报告不会包含临时 HOME、API key 字样或过长工具结果全文。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "report" -q`，期望先失败，失败点为缺少 report。

## T13: 实现 JSON 和 Markdown 报告

**文件：** `eval/july_eval/report.py`  
**依赖：** T12  
**步骤：**
1. 实现 dataclass 到 JSON 安全字典转换，截断长 evidence。
2. 实现 `write_json_report(result, path)`。
3. 实现 `write_markdown_report(result, path)`。
4. 报告中列出总体通过率、总分、维度均分、失败用例、人工复核项和关键证据。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "report" -q`，期望通过。

## T14: 增加 CLI 入口测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** T13  
**步骤：**
1. 添加测试通过 subprocess 运行 `python eval/run_eval.py --cases eval/cases --metrics eval/metrics/default_metrics.json --output <tmp>`。
2. 断言退出码为 0，输出目录包含 `results.json` 和 `report.md`。
3. 添加失败阈值测试，使用过高阈值或失败用例，断言退出码为 1 且报告仍生成。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "run_eval_cli" -q`，期望先失败，失败点为缺少 CLI。

## T15: 实现 `eval/run_eval.py`

**文件：** `eval/run_eval.py`  
**依赖：** T14  
**步骤：**
1. 实现 argparse 参数：`--cases`、`--metrics`、`--output`、`--case`、`--threshold`、`--allow-review`、`--keep-workspaces`。
2. 调用 loader、runner、report 完整运行离线 suite。
3. 输出简短命令行摘要，写入 `results.json` 和 `report.md`。
4. 按失败、错误、人工复核和阈值返回退出码。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "run_eval_cli" -q`，期望通过。

## T16: 编写 eval README 和结果忽略规则

**文件：** `eval/README.md`、`eval/results/.gitignore`  
**依赖：** T15  
**步骤：**
1. 说明评测目标、默认维度、用例格式、运行命令和报告解释。
2. 明确自动评分与人工复核边界，说明真实模型评测不稳定且不是初始默认能力。
3. 给出新增用例和调整权重的示例。
4. 在 `eval/results/.gitignore` 忽略本地评测结果但保留目录。

**验证：** 运行 `python - <<'PY'\nfrom pathlib import Path\ntext=Path('eval/README.md').read_text(encoding='utf-8')\nfor s in ['任务完成度','工具使用合理性','needs_review','python eval/run_eval.py','真实模型']:\n    assert s in text\nprint('ok')\nPY`，期望输出 `ok`。

## T17: 跑评测框架和相关回归测试

**文件：** `tests/test_eval_framework.py`、相关现有测试  
**依赖：** T1-T16  
**步骤：**
1. 运行评测框架测试。
2. 运行 Agent、工具、权限、上下文相关回归测试。
3. 修复因评测框架导入路径、权限模式或临时目录处理造成的问题。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py tests/test_agent.py tests/test_tools.py tests/test_permissions.py tests/test_context_manager.py -q`，期望全部通过。

## T18: 运行离线评测命令

**文件：** `eval/run_eval.py`、`eval/results/latest/`  
**依赖：** T17  
**步骤：**
1. 运行 `python eval/run_eval.py --cases eval/cases --metrics eval/metrics/default_metrics.json --output eval/results/latest --allow-review`。
2. 查看命令行摘要，确认用例数、通过数、需要复核数和总分。
3. 查看 `eval/results/latest/results.json` 和 `eval/results/latest/report.md`。

**验证：** 运行上述命令，期望退出码为 0，且两个报告文件存在。

## T19: tmux 端到端验收

**文件：** 无代码文件；使用本地运行环境  
**依赖：** T18、已批准的 `checklist.md`  
**步骤：**
1. 在 tmux 中启动命令 `python eval/run_eval.py --cases eval/cases --metrics eval/metrics/default_metrics.json --output eval/results/tmux --allow-review`。
2. 观察命令输出是否显示评测摘要。
3. 打开 `eval/results/tmux/report.md`，确认包含总体摘要、维度均分、用例表和复核项。
4. 对照 `checklist.md` 记录证据。

**验证：** tmux capture 输出包含评测完成摘要；报告文件存在且包含至少 7 个用例结果。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18 → T19
```
