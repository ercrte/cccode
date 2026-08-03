# Agent Evaluation Framework Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] `eval/` 独立工作区存在，包含 `README.md`、`run_eval.py`、`metrics/`、`cases/`、`results/` 和 `july_eval/`（验证：运行 `python - <<'PY'\nfrom pathlib import Path\nfor p in ['eval/README.md','eval/run_eval.py','eval/metrics/default_metrics.json','eval/cases','eval/results/.gitignore','eval/july_eval/models.py']:\n    assert Path(p).exists(), p\nprint('ok')\nPY`，期望输出 `ok`）
- [ ] 默认评测维度覆盖任务完成度、工具使用合理性、代码或文件修改质量、验证充分性、安全与权限遵守、上下文/记忆连续性、错误恢复能力、交互体验、效率与成本、结果稳定性，且每个维度都有评分范围、权重和证据说明（验证：运行 `python -m pytest tests/test_eval_framework.py::test_default_metrics_and_cases_cover_required_dimensions -q`，期望通过）
- [ ] 初始用例至少覆盖普通问答、只读代码搜索、多轮工具调用、文件修改与验证、权限拒绝后调整、上下文或长任务、Skill 或子 Agent 中的六类以上场景（验证：运行 `python -m pytest tests/test_eval_framework.py::test_default_metrics_and_cases_cover_required_dimensions -q`，期望通过）
- [ ] 评测数据结构能表达用例、期望、运行轨迹、维度评分、用例结果和 suite 汇总，默认值不会共享可变状态（验证：运行 `python -m pytest tests/test_eval_framework.py::test_eval_models_have_expected_defaults -q`，期望通过）
- [ ] JSON loader 能加载和校验指标与用例，非法配置给出明确错误而不是静默忽略（验证：运行 `python -m pytest tests/test_eval_framework.py -k "load_metrics or load_cases or invalid_eval" -q`，期望通过）
- [ ] 离线脚本化 Provider 不依赖真实 API key、网络或本地端口，并能确定性产生文本、usage 和工具调用（验证：运行 `python -m pytest tests/test_eval_framework.py -k scripted_provider -q`，期望通过）

## 集成
- [ ] 评测 Runner 使用真实 `AgentLoopRunner`、工具注册表、工具执行器、权限控制器、上下文管理器和 Provider 抽象执行用例，而不是绕过 Agent 核心路径（验证：运行 `python -m pytest tests/test_eval_framework.py -k "run_case or run_suite" -q`，期望通过；测试需断言 trace 中存在真实工具事件或真实最终消息）
- [ ] 写入类评测在临时 workspace 中执行，不污染项目根目录真实文件（验证：运行 `python -m pytest tests/test_eval_framework.py -k "write_and_verify or run_case" -q`，期望通过）
- [ ] 权限拒绝场景能记录拒绝证据，并在报告中定位到权限或工具环节（验证：运行 `python -m pytest tests/test_eval_framework.py -k "permission_recovery or run_case" -q`，期望通过）
- [ ] 自动评分能根据最终回复、工具调用序列、文件内容、权限拒绝、上下文事件、usage 和耗时计算维度分，并对主观项标记 `needs_review`（验证：运行 `python -m pytest tests/test_eval_framework.py -k "score_case or metric_score" -q`，期望通过）
- [ ] JSON 报告和 Markdown 报告都包含总体摘要、各维度均分、用例结果、失败详情、人工复核项和关键证据，且不包含 API key 或过长工具结果全文（验证：运行 `python -m pytest tests/test_eval_framework.py -k report -q`，期望通过）
- [ ] CLI 能按成功、失败、错误和人工复核状态返回清晰退出码，并始终写出可检查报告（验证：运行 `python -m pytest tests/test_eval_framework.py -k run_eval_cli -q`，期望通过）
- [ ] 用户可以通过编辑 `eval/cases/*.json` 和 `eval/metrics/default_metrics.json` 增加用例或调整权重，而不需要改动 `src/julycode` 核心代码（验证：运行 `python -m pytest tests/test_eval_framework.py -k "load_cases or load_metrics" -q`，期望通过；人工确认用例和指标是 JSON 文件）

## 编译与测试
- [ ] 评测框架测试全部通过（验证：运行 `python -m pytest tests/test_eval_framework.py -q`，期望通过）
- [ ] Agent、工具、权限、上下文相关回归测试仍通过（验证：运行 `python -m pytest tests/test_agent.py tests/test_tools.py tests/test_permissions.py tests/test_context_manager.py -q`，期望通过）
- [ ] 评测框架与相关回归测试合并运行通过（验证：运行 `python -m pytest tests/test_eval_framework.py tests/test_agent.py tests/test_tools.py tests/test_permissions.py tests/test_context_manager.py -q`，期望通过）
- [ ] 离线评测命令能生成机器可读和人类可读报告（验证：运行 `python eval/run_eval.py --cases eval/cases --metrics eval/metrics/default_metrics.json --output eval/results/latest --allow-review`，期望退出码为 0，且 `eval/results/latest/results.json` 和 `eval/results/latest/report.md` 存在）
- [ ] `eval/README.md` 说明默认维度、用例格式、运行命令、报告解释、`needs_review`、真实模型不稳定性和自动评分边界（验证：运行 `python - <<'PY'\nfrom pathlib import Path\ntext=Path('eval/README.md').read_text(encoding='utf-8')\nfor s in ['任务完成度','工具使用合理性','needs_review','python eval/run_eval.py','真实模型','自动评分']:\n    assert s in text, s\nprint('ok')\nPY`，期望输出 `ok`）

## 端到端场景
- [ ] 场景 1：在 tmux 中运行 `python eval/run_eval.py --cases eval/cases --metrics eval/metrics/default_metrics.json --output eval/results/tmux --allow-review` → 命令输出显示评测摘要，退出码为 0（验证：`tmux capture-pane -p` 中可见用例数、通过数、复核数或总分）
- [ ] 场景 2：打开 `eval/results/tmux/report.md` → 报告包含总体摘要、维度均分、至少 7 个用例结果、失败或复核项区块、关键证据（验证：运行 `python - <<'PY'\nfrom pathlib import Path\ntext=Path('eval/results/tmux/report.md').read_text(encoding='utf-8')\nfor s in ['总体摘要','维度均分','用例','关键证据']:\n    assert s in text, s\nassert text.count('|') > 20\nprint('ok')\nPY`，期望输出 `ok`）
- [ ] 场景 3：检查 `eval/results/tmux/results.json` → JSON 包含 suite summary、case results、metric scores、trace evidence，并且不包含真实 API key（验证：运行 `python - <<'PY'\nimport json\nfrom pathlib import Path\ndata=json.loads(Path('eval/results/tmux/results.json').read_text(encoding='utf-8'))\nassert data['summary']['total_cases'] >= 7\nassert data['results']\nassert all('metric_scores' in r and 'trace' in r for r in data['results'])\nassert 'api_key' not in json.dumps(data).lower()\nprint('ok')\nPY`，期望输出 `ok`）
- [ ] 场景 4：对照本 checklist 逐项验收 → 所有命令项有输出证据，tmux 场景有 capture 或报告文件证据；若存在 `needs_review`，报告明确标注为人工复核而不是框架错误（验证：提交验收报告，期望每项有通过或失败证据）
