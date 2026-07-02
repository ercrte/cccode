# Agent Evaluation Online Mode Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `eval/run_eval.py` | 默认在线 CLI、模式参数、真实 Provider 配置加载 |
| 修改 | `eval/README.md` | 在线默认、离线 smoke、费用和不稳定性说明 |
| 修改 | `eval/mew_eval/models.py` | 增加 mode、Provider 信息、cache usage、用例标签 |
| 修改 | `eval/mew_eval/loader.py` | 解析新增用例字段，校验 online/offline 标签 |
| 修改 | `eval/mew_eval/runner.py` | 支持在线 Provider 注入和离线脚本 Provider |
| 修改 | `eval/mew_eval/report.py` | 报告输出运行环境、模型和 prompt cache 信息 |
| 修改 | `eval/mew_eval/provider.py` | 保留离线脚本 Provider，适配 offline 用例目录 |
| 新建 | `eval/cases/online/*.json` | 至少 30 个真实模型评测用例 |
| 新建 | `eval/cases/offline/*.json` | 迁移现有 7 个离线 smoke 用例 |
| 删除或迁移 | `eval/cases/*.json` | 旧根目录用例迁移到 `offline/` |
| 修改 | `tests/test_eval_framework.py` | 在线模式、目录拆分、30 用例、报告字段和 CLI 测试 |

## T1: 增加在线模式模型测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** 无  
**步骤：**
1. 扩展模型默认值测试，断言 `EvalRunOptions.mode == "online"`。
2. 添加 `EvalProviderInfo` 构造断言，覆盖 `mode`、`protocol`、`model`、`provider`、`prompt_cache_enabled`。
3. 扩展 `EvalUsageSummary` 测试，断言 cache 字段默认存在且为 `None`。
4. 构造 `EvalSuiteResult` 时包含 provider 信息。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py::test_eval_models_have_expected_defaults -q`，期望先失败，失败点为缺少新增字段。

## T2: 实现在线模式数据结构

**文件：** `eval/mew_eval/models.py`、`eval/mew_eval/__init__.py`  
**依赖：** T1  
**步骤：**
1. 新增 `EvalRunMode` 和 `EvalProviderInfo`。
2. 扩展 `EvalUsageSummary` 的 prompt cache 字段。
3. 扩展 `EvalRunOptions` 的 `mode`、`provider`、`provider_info`。
4. 扩展 `EvalSuiteResult` 的 `provider` 字段。
5. 扩展 `EvalCase` 的 `tags`、`online_only`、`offline_only`。
6. 更新包导出。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py::test_eval_models_have_expected_defaults -q`，期望通过。

## T3: 增加 loader 新字段测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** T2  
**步骤：**
1. 修改 case loader 测试，加入 `tags`、`online_only` 和 `offline_only`。
2. 添加非法配置测试，覆盖 `tags` 非字符串数组。
3. 添加非法配置测试，覆盖 `online_only` 与 `offline_only` 同时为 true。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "load_cases or invalid_eval" -q`，期望先失败。

## T4: 实现 loader 新字段解析

**文件：** `eval/mew_eval/loader.py`  
**依赖：** T3  
**步骤：**
1. 解析 `tags` 为 tuple。
2. 解析 `online_only` 和 `offline_only`。
3. 校验两者不能同时为 true。
4. 保持旧用例 JSON 的兼容默认值。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "load_cases or invalid_eval" -q`，期望通过。

## T5: 迁移离线用例目录

**文件：** `eval/cases/offline/*.json`、`eval/cases/*.json`、`tests/test_eval_framework.py`  
**依赖：** T4  
**步骤：**
1. 创建 `eval/cases/offline/`。
2. 将现有 7 个脚本化用例迁移到 `eval/cases/offline/`。
3. 给离线用例补充 `offline_only: true` 和 `tags`。
4. 更新测试中离线用例路径为 `eval/cases/offline`。
5. 保持旧根目录不再作为默认用例目录。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -q`，期望通过。

## T6: 增加 30 个在线用例文件

**文件：** `eval/cases/online/*.json`、`tests/test_eval_framework.py`  
**依赖：** T4  
**步骤：**
1. 创建 `eval/cases/online/`。
2. 增加至少 30 个在线用例 JSON。
3. 每个在线用例设置 `online_only: true` 和场景 `tags`。
4. 给写入/命令类用例配置 `setup_files`、`expected_files` 或 `verification_commands`。
5. 添加测试断言在线用例数量至少 30，且覆盖 spec 要求的所有场景 tag。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py::test_online_cases_cover_required_scenarios -q`，期望通过。

## T7: 增加在线 runner 注入测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** T2、T4  
**步骤：**
1. 添加 fake online Provider，返回最终回复和带 cache 的 usage。
2. 构造 `EvalRunOptions(mode="online", provider=fake, provider_info=...)`。
3. 运行单个简单在线用例，断言 trace 中 usage、cache_status、provider 信息存在。
4. 添加测试断言在线模式没有 provider 时返回 error 结果。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "online_provider or run_case" -q`，期望先失败。

## T8: 实现 runner 在线/离线分支

**文件：** `eval/mew_eval/runner.py`  
**依赖：** T7  
**步骤：**
1. 根据 `EvalRunOptions.mode` 选择 Provider。
2. 在线模式要求 `options.provider` 非空，否则生成 `status="error"` 的结果。
3. 离线模式继续创建 `ScriptedEvalProvider`。
4. `run_suite()` 在结果中写入 `EvalProviderInfo`。
5. `_usage_summary()` 抽取 `TokenUsage.cache` 字段。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k "online_provider or run_case or run_suite" -q`，期望通过。

## T9: 增加报告 Provider/cache 字段测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** T8  
**步骤：**
1. 更新 JSON 报告测试，断言包含 `provider.mode`、`provider.model` 和 usage cache 字段。
2. 更新 Markdown 报告测试，断言包含“运行环境”、模式、模型和 prompt cache。
3. 保持敏感字段脱敏断言。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k report -q`，期望先失败。

## T10: 实现报告运行环境区块

**文件：** `eval/mew_eval/report.py`  
**依赖：** T9  
**步骤：**
1. JSON dataclass 转换自然包含 provider 字段。
2. Markdown 增加“运行环境”区块。
3. 展示 mode、protocol、model、provider 和 prompt cache enabled。
4. 在关键证据或 usage 区块展示 cache status。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k report -q`，期望通过。

## T11: 增加 CLI 默认在线测试

**文件：** `tests/test_eval_framework.py`  
**依赖：** T8  
**步骤：**
1. 添加 subprocess 测试：默认运行且无真实配置时返回退出码 `2`。
2. 断言 stderr 包含“在线评测配置错误”或等价清晰提示。
3. 添加 `--mode offline` 测试，断言无需真实配置即可生成报告并退出 `0`（传 `--allow-review`）。
4. 添加 `--offline` 快捷参数测试。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k run_eval_cli -q`，期望先失败。

## T12: 实现 CLI 在线默认和配置加载

**文件：** `eval/run_eval.py`  
**依赖：** T11  
**步骤：**
1. 修改描述为 Agent 评测，不再称为离线评测。
2. 增加 `--mode {online,offline}`，默认 online。
3. 增加 `--offline` 快捷参数。
4. 增加 `--model` 参数。
5. 未传 `--cases` 时，online 默认 `eval/cases/online`，offline 默认 `eval/cases/offline`。
6. 在线模式调用 `load_config()` 和 `create_provider()`。
7. 构造 `EvalProviderInfo` 并传给 runner。
8. 配置错误返回 `2`，错误文案清晰。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k run_eval_cli -q`，期望通过。

## T13: 更新离线 Provider 测试路径和语义

**文件：** `tests/test_eval_framework.py`、`eval/mew_eval/provider.py`  
**依赖：** T5、T12  
**步骤：**
1. 保留 `ScriptedEvalProvider` 单测。
2. 将测试命名或断言调整为 offline smoke。
3. 确认离线 Provider 仍能支持迁移后的 7 个 offline 用例。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -k scripted_provider -q`，期望通过。

## T14: 更新 README

**文件：** `eval/README.md`  
**依赖：** T12  
**步骤：**
1. 说明默认是在线真实模型评测。
2. 说明在线模式会产生费用、需要配置、可能较慢且不稳定。
3. 说明离线模式只用于 smoke/回归。
4. 更新运行命令示例：默认 online、显式 offline、单用例 online。
5. 说明报告中的 provider、model、usage、prompt cache 和 `needs_review`。

**验证：** 运行 `python - <<'PY'\nfrom pathlib import Path\ntext=Path('eval/README.md').read_text(encoding='utf-8')\nfor s in ['在线','离线','python eval/run_eval.py','--mode offline','真实模型','prompt cache','needs_review','费用']:\n    assert s in text, s\nprint('ok')\nPY`，期望输出 `ok`。

## T15: 更新 checklist 相关测试期望

**文件：** `tests/test_eval_framework.py`  
**依赖：** T6、T12、T14  
**步骤：**
1. 更新默认指标和用例覆盖测试，改为检查在线 30 用例和离线 7 smoke。
2. 更新 CLI 成功测试使用 `--mode offline`。
3. 更新 runner 集成测试按离线或 fake online 明确选择。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -q`，期望通过。

## T16: 跑评测框架和相关回归测试

**文件：** 所有相关文件  
**依赖：** T1-T15  
**步骤：**
1. 运行评测框架测试。
2. 运行 Agent、工具、权限、上下文相关回归测试。
3. 运行全量测试。
4. 修复失败并重跑。

**验证：** 运行 `python -m pytest -q`，期望全部通过。

## T17: 运行离线 smoke 命令

**文件：** `eval/run_eval.py`、`eval/results/offline/`  
**依赖：** T16  
**步骤：**
1. 运行 `python eval/run_eval.py --mode offline --output eval/results/offline --allow-review`。
2. 检查摘要显示 7 个离线 smoke 用例。
3. 检查 `results.json` 和 `report.md` 存在。

**验证：** 命令退出码为 0，报告存在且 `provider.mode == "offline"`。

## T18: 验证在线默认配置错误分支

**文件：** `eval/run_eval.py`  
**依赖：** T16  
**步骤：**
1. 在无可用配置或临时空 HOME 下运行默认在线命令。
2. 确认返回退出码 `2`。
3. 确认错误说明是在线配置问题，不是用例失败。

**验证：** 运行对应命令，期望 stderr 包含“在线评测配置错误”。

## T19: 可选真实在线单用例验收

**文件：** `eval/results/online/`  
**依赖：** T16  
**步骤：**
1. 如果当前环境存在有效 MewCode 配置和网络，运行 `python eval/run_eval.py --case online_basic_project_summary --output eval/results/online-single --allow-review`。
2. 如果缺少配置或网络，记录为环境阻塞，不视为实现失败。
3. 检查报告包含 online mode、model、provider 和 usage。

**验证：** 有配置时退出码为 0 或 1 且报告生成；无配置时退出码为 2 且错误清晰。

## T20: tmux 端到端验收

**文件：** 无代码文件；使用本地运行环境  
**依赖：** T17、T18、T19  
**步骤：**
1. 在 tmux 中运行 `python eval/run_eval.py --mode offline --output eval/results/tmux-offline --allow-review`。
2. 捕获 tmux pane，确认退出码为 0。
3. 在 tmux 中运行默认在线命令；如果配置不可用，确认退出码为 2 且错误清晰；如果配置可用，确认报告生成。
4. 检查报告 JSON 和 Markdown。

**验证：** tmux capture 输出和报告文件满足 checklist。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18 → T19 → T20
```
