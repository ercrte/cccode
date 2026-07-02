# Agent Evaluation Online Mode Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] CLI 默认运行模式是在线真实模型模式，而不是脚本化离线模式（验证：运行 `python eval/run_eval.py --help`，期望看到 `--mode {online,offline}` 且说明默认 online；运行默认命令在无配置环境下返回在线配置错误）
- [ ] CLI 显式支持离线 smoke 模式（验证：运行 `python eval/run_eval.py --mode offline --output eval/results/offline --allow-review`，期望退出码 0 并生成报告）
- [ ] 未传 `--cases` 时 online 默认加载 `eval/cases/online`，offline 默认加载 `eval/cases/offline`（验证：运行 `python -m pytest tests/test_eval_framework.py -k run_eval_cli -q`，期望通过）
- [ ] 在线 Provider 通过 MewCode 配置和 Provider 工厂创建，runner 只接收注入 Provider，不隐式加载配置（验证：运行 `python -m pytest tests/test_eval_framework.py -k "online_provider or run_case" -q`，期望通过）
- [ ] 数据结构能表达运行模式、Provider 信息、模型、prompt cache、用例标签、online/offline only（验证：运行 `python -m pytest tests/test_eval_framework.py::test_eval_models_have_expected_defaults -q`，期望通过）
- [ ] loader 能解析 `tags`、`online_only`、`offline_only`，非法组合给出明确错误（验证：运行 `python -m pytest tests/test_eval_framework.py -k "load_cases or invalid_eval" -q`，期望通过）
- [ ] 在线默认用例至少 30 个（验证：运行 `python -m pytest tests/test_eval_framework.py::test_online_cases_cover_required_scenarios -q`，期望通过）
- [ ] 在线默认用例覆盖代码阅读、文件修改、测试修复、权限拒绝、上下文压缩、Skill、子 Agent、命令失败恢复、计划模式、会话连续性、提示词缓存观察和多文件任务（验证：运行 `python -m pytest tests/test_eval_framework.py::test_online_cases_cover_required_scenarios -q`，期望通过）
- [ ] 离线 smoke 用例保留 7 个，并明确标记为 offline（验证：运行 `python -m pytest tests/test_eval_framework.py::test_default_metrics_and_cases_cover_required_dimensions -q`，期望通过）

## 集成
- [ ] 在线模式继续使用真实 `AgentLoopRunner`、工具注册表、工具执行器、权限控制器、上下文管理器和 Provider 抽象（验证：运行 `python -m pytest tests/test_eval_framework.py -k "online_provider or run_case" -q`，期望 trace 中存在真实 `message_done` 或真实工具事件）
- [ ] 在线模式缺少 Provider 注入时不会误报为 Agent 能力失败，而是产生 error 结果或 CLI 配置错误（验证：运行 `python -m pytest tests/test_eval_framework.py -k online_provider -q`，期望通过）
- [ ] 在线 CLI 缺少配置、API key 或 Provider 创建失败时返回退出码 2，并输出清晰配置错误（验证：运行 `python -m pytest tests/test_eval_framework.py -k run_eval_cli -q`，期望通过；手动命令可复验）
- [ ] 报告包含 mode、protocol、model、provider、prompt cache enabled、usage cache status、工具轨迹、停止原因和人工复核项（验证：运行 `python -m pytest tests/test_eval_framework.py -k report -q`，期望通过）
- [ ] JSON 和 Markdown 报告不包含 API key 或过长工具结果全文（验证：运行 `python -m pytest tests/test_eval_framework.py -k report -q`，期望通过）
- [ ] 写入和命令类用例在临时 workspace 中执行，不污染项目根目录（验证：运行 `python -m pytest tests/test_eval_framework.py -k "write_and_verify or run_case" -q`，期望通过）
- [ ] `--case` 可用于只运行指定在线或离线用例（验证：运行 `python -m pytest tests/test_eval_framework.py -k run_eval_cli -q`，期望通过）
- [ ] `--offline` 快捷参数等价于 `--mode offline`（验证：运行 `python -m pytest tests/test_eval_framework.py -k run_eval_cli -q`，期望通过）

## 编译与测试
- [ ] 评测框架测试全部通过（验证：运行 `python -m pytest tests/test_eval_framework.py -q`，期望通过）
- [ ] Agent、工具、权限、上下文相关回归测试仍通过（验证：运行 `python -m pytest tests/test_agent.py tests/test_tools.py tests/test_permissions.py tests/test_context_manager.py -q`，期望通过）
- [ ] 全量测试通过且不依赖真实 API key 或网络（验证：运行 `python -m pytest -q`，期望通过）
- [ ] README 说明在线默认、离线 smoke、真实模型费用/耗时/不稳定性、prompt cache、`needs_review` 和运行命令（验证：运行 `python - <<'PY'\nfrom pathlib import Path\ntext=Path('eval/README.md').read_text(encoding='utf-8')\nfor s in ['在线','离线','python eval/run_eval.py','--mode offline','真实模型','prompt cache','needs_review','费用']:\n    assert s in text, s\nprint('ok')\nPY`，期望输出 `ok`）
- [ ] 默认在线配置错误分支可观测（验证：在临时空 HOME 或无项目配置环境运行 `python eval/run_eval.py --output eval/results/online-config-check --allow-review`，期望退出码 2 且 stderr 包含“在线评测配置错误”）

## 端到端场景
- [ ] 场景 1：离线 smoke 端到端通过（验证：在 tmux 中运行 `python eval/run_eval.py --mode offline --output eval/results/tmux-offline --allow-review; printf "\\nEXIT:$?\\n"`，期望输出用例数 7、失败 0、错误 0、`EXIT:0`）
- [ ] 场景 2：检查离线 tmux 报告（验证：运行 `python - <<'PY'\nimport json\nfrom pathlib import Path\nreport=Path('eval/results/tmux-offline/report.md').read_text(encoding='utf-8')\ndata=json.loads(Path('eval/results/tmux-offline/results.json').read_text(encoding='utf-8'))\nassert data['provider']['mode'] == 'offline'\nassert data['summary']['total_cases'] == 7\nfor s in ['运行环境','总体摘要','维度均分','关键证据']:\n    assert s in report, s\nprint('ok')\nPY`，期望输出 `ok`）
- [ ] 场景 3：默认在线命令在无配置环境中清晰失败（验证：在 tmux 中使用空 HOME 运行 `python eval/run_eval.py --output eval/results/tmux-online-config --allow-review; printf "\\nEXIT:$?\\n"`，期望 `EXIT:2` 且输出包含“在线评测配置错误”）
- [ ] 场景 4：如果当前环境有有效 MewCode 配置和网络，真实在线单用例能生成报告（验证：运行 `python eval/run_eval.py --case online_basic_project_summary --output eval/results/online-single --allow-review`；有配置时报告包含 provider/model/usage，无配置时退出码 2 且错误清晰）
- [ ] 场景 5：对照本 checklist 逐项验收（验证：提交验收报告，期望所有非环境依赖项通过；真实在线单用例如因缺配置或网络不可用，应记录为环境阻塞而不是实现失败）
