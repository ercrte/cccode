# 记忆提取与跨会话继承修复 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] T1: 提取 Prompt 包含每条 category 定义+触发词+正例+反例（验证：`grep -c "preference" src/julycode/memory/updater.py` 确认出现多次，且含"定义"、"正例"、"反例"关键词）
- [ ] T2: critical 门控允许 project_knowledge 类别（验证：`grep "project_knowledge" src/julycode/memory/extraction.py` 在 critical 检查行附近出现）
- [ ] T3: evidence 匹配使用归一化（验证：`grep "_normalize" src/julycode/memory/extraction.py` 在 evidence 检查附近出现）
- [ ] T4: 索引使用 `**[关键]**`（验证：`grep '\*\*\[关键\]\*\*' src/julycode/memory/index.py` 命中）
- [ ] T5: index_max_lines=400, index_max_bytes=50000（验证：`grep "index_max_lines\|index_max_bytes" src/julycode/memory/models.py`）
- [ ] T6: 引导语含"禁止"、"硬性行为约束"、"直接使用"（验证：`grep "禁止\|硬性行为约束\|直接使用" src/julycode/prompting/builder.py`）

## 单元测试

- [ ] 既有关键偏好测试通过（验证：`pytest tests/test_memory_extraction.py -v -k "critical"`）
- [ ] 新增 critical+project_knowledge 测试通过（验证：`pytest tests/test_memory_extraction.py::test_accepts_critical_project_knowledge -v`）
- [ ] 新增 evidence 归一化匹配测试通过（验证：`pytest tests/test_memory_extraction.py::test_evidence_normalized_matching -v`）
- [ ] 新增 evidence 仍拒内容不匹配测试通过（验证：`pytest tests/test_memory_extraction.py::test_evidence_still_rejects_mismatch -v`）
- [ ] 索引格式新断言通过（验证：`pytest tests/test_memory_index.py -v`）
- [ ] 引导语新断言通过（验证：`pytest tests/test_prompting.py -v`）
- [ ] 全量 pytest 通过（验证：`pytest tests/ -v --tb=short` — 不可有 FAILED）

## 离线评测

- [ ] 离线提取评测无回归（验证：`python eval/run_memory_eval.py --mode offline --output /tmp/memory-eval-fix-offline`，退出码 0）
- [ ] 离线报告包含正确的指标字段（验证：检查 `/tmp/memory-eval-fix-offline/report.md` 含 TP/FP/FN/Precision/Recall/F1）

## 端到端场景

- [ ] 场景 A — 关键项目约束提取：user_message="请长期记住：所有数据库迁移必须可逆"，预期提取 category=project_knowledge, scope=project, critical=True（验证：离线评测中 critical_042 对应的用例 TP）
- [ ] 场景 B — correction 与 preference 区分：user_message="纠正一下，不要再用 unittest，今后必须默认使用 pytest"，预期提取 category=correction, critical=True（验证：离线评测中 critical_049 对应的用例 TP）
- [ ] 场景 C — reference 与 project_knowledge 区分：user_message="架构说明入口是 docs/architecture.md"，预期提取 category=reference, scope=project（验证：离线评测中 memory_021 对应的用例 TP）
- [ ] 场景 D — 跨会话继承不重复询问：在 source 会话建立了"Web 框架 FastAPI"和"回答简洁"记忆后，target 会话直接使用 FastAPI 并简洁回答（验证：在线 inheritance 评测中 corresponding case 的 first_turn_correct=True）
