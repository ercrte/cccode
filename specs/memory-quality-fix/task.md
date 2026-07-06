# 记忆提取与跨会话继承修复 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/mewcode/memory/updater.py` | 提取 Prompt 重写，增加分类指南 |
| 修改 | `src/mewcode/memory/extraction.py` | Validator：放宽 critical 类别 + evidence 归一化匹配 |
| 修改 | `src/mewcode/memory/index.py` | 索引格式微调（粗体关键标记 + 标签追加） |
| 修改 | `src/mewcode/memory/models.py` | index_max_lines/index_max_bytes 默认值翻倍 |
| 修改 | `src/mewcode/prompting/builder.py` | 记忆注入引导语重写 |
| 修改 | `tests/test_memory_extraction.py` | 新增 critical+project_knowledge 和归一化 evidence 测试 |
| 修改 | `tests/test_memory_index.py` | 新索引格式断言 |
| 修改 | `tests/test_prompting.py` | 新引导语内容断言 |

## T1: 提取 Prompt 重写

**文件：** `src/mewcode/memory/updater.py`
**依赖：** 无
**步骤：**
1. 在 `_prompt()` 方法中，将现有的单行分类描述替换为结构化的分类指南块。
2. 指南必须包含：每条 category（preference/correction/project_knowledge/reference）的定义、典型触发词、至少一个正例和一个反例。
3. 指南必须包含：scope（user/project）的判断规则和正例。
4. 指南必须包含：critical 的判断规则，明确哪些类别可以标记 critical。
5. 保留原有 JSON 格式要求、字段说明和负例策略（临时/猜测/敏感/工具输出 skip）。
6. 指南块放在 JSON payload 之前。

**验证：** 肉眼审查 Prompt 文本，确认每条 category 有定义+触发词+正例+反例。运行 `pytest tests/test_memory_updater.py -v` 确认无回归。

## T2: Validator 规则放宽 — critical 类别扩展

**文件：** `src/mewcode/memory/extraction.py`
**依赖：** 无
**步骤：**
1. 找到 `_validate_one()` 中 critical 门控检查（约第 181-183 行）。
2. 将 `candidate.category not in {"preference", "correction"}` 改为 `candidate.category not in {"preference", "correction", "project_knowledge"}`。
3. 更新拒绝消息为 "关键偏好只能属于 preference、correction 或 project_knowledge"。

**验证：** 运行 `pytest tests/test_memory_extraction.py -v -k "critical"` 确认既有关键偏好测试通过，且新增的 project_knowledge+critical 测试通过。

## T3: Validator 规则放宽 — evidence 归一化匹配

**文件：** `src/mewcode/memory/extraction.py`
**依赖：** 无
**步骤：**
1. 找到 `_validate_one()` 中 evidence 检查（约第 173-177 行）。
2. 在检查前对 `user_messages` 做归一化：`normalized_messages = tuple(_normalize(m) for m in user_messages)`。
3. 将 evidence 子串匹配从 `evidence in message` 改为 `_normalize(evidence) in norm_msg`。
4. 保持其他逻辑不变（空 evidence 仍被拒，非空但全部不匹配仍被拒）。

**验证：** 运行 `pytest tests/test_memory_extraction.py -v -k "evidence"` 确认 evidence 测试通过。手动验证：evidence "以后始终使用中文回答" 应能匹配消息 "以后始终使用中文回答。"（标点差异）。

## T4: 索引格式微调

**文件：** `src/mewcode/memory/index.py`
**依赖：** 无
**步骤：**
1. 找到 `MemoryIndexBuilder.build()` 中索引条目生成行（约第 35-38 行）。
2. 将关键偏好标记从 `[关键偏好]` 改为 `**[关键]**`。
3. 在条目 body 后追加标签（如有），形如 `` `tags: tag1, tag2` ``。
4. 保留类别排序和关键优先逻辑不变。

**验证：** 运行 `pytest tests/test_memory_index.py -v` 确认索引构建测试通过。检查生成的索引文本包含 `**[关键]**` 和标签。

## T5: 索引上限提升

**文件：** `src/mewcode/memory/models.py`
**依赖：** 无
**步骤：**
1. 找到 `SessionMemoryConfig` 的 `index_max_lines` 和 `index_max_bytes` 默认值。
2. `index_max_lines` 从 200 改为 400。
3. `index_max_bytes` 从 25_000 改为 50_000。

**验证：** 运行 `pytest tests/test_memory_index.py -v` 确认无回归。检查 `SessionMemoryConfig()` 的默认值。

## T6: 记忆注入引导语强化

**文件：** `src/mewcode/prompting/builder.py`
**依赖：** 无
**步骤：**
1. 找到 `_dynamic_knowledge_context_lines()` 中注入记忆索引的引导语（约第 317-323 行）。
2. 将三行中文引导语替换为编号规则格式：
   - 规则 1：`[关键]` 记忆是硬性行为约束，必须遵守，不得以任何理由违反
   - 规则 2：索引中已有的背景信息禁止要求用户重复提供或确认，直接使用
   - 规则 3：当前用户明确指令可覆盖旧记忆
   - 末尾：违反以上规则将导致任务失败
3. 保留 `以下内容是跨会话长期记忆索引...` 的开头声明。

**验证：** 运行 `pytest tests/test_prompting.py -v` 确认引导语测试通过。肉眼检查生成的引导语包含"禁止"、"硬性行为约束"、"不得以任何理由违反"、"直接使用"。

## T7: 更新单元测试

**文件：** `tests/test_memory_extraction.py`、`tests/test_memory_index.py`、`tests/test_prompting.py`
**依赖：** T1-T6
**步骤：**

### T7a: extraction 测试
1. 新增 `test_accepts_critical_project_knowledge`：构造 category=project_knowledge, critical=True, confidence=0.99, evidence 含"必须"的候选，断言 accepted 非空。
2. 新增 `test_evidence_normalized_matching`：构造 user_message="以后始终使用中文回答。"，evidence="以后始终使用中文回答"（无句号），断言 accepted 非空。
3. 新增 `test_evidence_still_rejects_mismatch`：构造 user_message="我喜欢简洁"，evidence="以后必须简洁"（内容不同），断言 rejected code=missing_user_evidence。

### T7b: index 测试
1. 更新既有索引格式断言：检查 `**[关键]**` 替代 `[关键偏好]`。
2. 新增 `test_index_includes_tags`：构造有 tags 的笔记，断言索引包含 `` `tags: ...` ``。

### T7c: prompting 测试
1. 更新引导语断言：检查"禁止"、"硬性行为约束"、"直接使用"等新关键词出现。

**验证：** 运行 `pytest tests/test_memory_extraction.py tests/test_memory_index.py tests/test_prompting.py -v`，全部通过。

## T8: 全量回归测试

**文件：** 全部
**依赖：** T7
**步骤：**
1. 运行全量测试套件。
2. 确认与记忆相关的所有测试通过。
3. 确认与 prompting 相关的测试通过。

**验证：** `pytest tests/ -v --tb=short` 全部通过。

## T9: 离线评测回归

**文件：** eval/
**依赖：** T8
**步骤：**
1. 运行离线提取评测，确认无回归。

**验证：** `python eval/run_memory_eval.py --mode offline --output /tmp/memory-eval-offline` 返回 0。

## 执行顺序

```text
T1 ──┬── T2 ──┬── T7a ──┬── T8 ── T9
     │         │          │
     ├── T3 ──┤          │
     │         │          │
     ├── T4 ──┤── T7b ──┤
     │         │          │
     ├── T5 ──┤          │
     │         │          │
     └── T6 ──┴── T7c ──┘
```

T1-T6 可并行执行（改动不同文件且无依赖）。T7 依赖全部实现完成。T8 依赖 T7。T9 依赖 T8。
