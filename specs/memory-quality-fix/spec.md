# 记忆提取与跨会话继承修复 Spec

## 背景

跨会话记忆质量评测（`eval/results/memory-quality/latest/report.md`）暴露了两类关键问题：

**提取侧**：整体 F1 仅 55.26%（目标 85%），关键偏好 Precision 仅 70.21%（目标 98%）。根因分析表明：
- 模型无法可靠区分 `correction`（纠正旧行为）与 `preference`（新行为约束），导致 16 对 FP+FN
- `user` vs `project` scope 混淆，"本项目使用 Python 3.11" 被错分为 user
- `project_knowledge` vs `reference` 混淆，路径/指针类信息被错分为 project_knowledge
- `critical` 标记在 project_knowledge 类别上被 Validator 拒绝（`critical_not_explicit`），导致 "migrations must be reversible" 等约束漏提
- 证据逐字匹配过严，模型轻微改写原文即被拒绝

**继承侧**：首轮任务理解正确率仅 30%（目标 90%），背景重复说明减少率仅 57%（目标 80%）。根因分析表明：
- 索引格式仅有 `title: body`，缺少关键标记、证据等上下文
- 行为偏好（如"回答简洁"、"禁止 emoji"）作为知识注入而非系统指令，模型不严格遵守
- 索引大小上限（200 行/25KB）不足以容纳全部关键记忆
- "不要要求用户重复提供" 的提示语言不够强，模型仍频繁要求重述背景

本阶段聚焦于修复上述已确认的根因，不改动评测框架和整体架构。

## 目标

- 提取 Prompt 重写，提供清晰的 category、scope 定义和正反例，消除 correction/preference 和 project_knowledge/reference 混淆
- Validator 规则调整：允许 project_knowledge 类别标记 critical，放宽 evidence 匹配为归一化子串匹配
- 索引格式增强：在条目中添加 `[关键]` 中文标记、关联标签和证据摘要，提升跨会话模型遵循率
- 记忆注入引导语强化：将关键行为偏好提升为系统级约束，明确禁止要求用户重述已知背景
- 索引大小上限提升，确保全部关键记忆可见
- 整体 F1 回升至 85%+，关键偏好 Precision 回升至 98%+，首轮理解正确率回升至 90%+，背景重复说明减少率回升至 80%+

## 功能需求

- F1: 提取 Prompt 必须包含每条 category（preference / correction / project_knowledge / reference）的明确定义、典型中文触发词和正反例；区分 correction（明确纠正/覆盖先前说法）和 preference（首次表达或补充新偏好）。
- F2: 提取 Prompt 必须包含每条 scope（user / project）的明确判断规则；出现"本项目"、"请长期记住"且内容为项目特定事实/约定时，scope=project。
- F3: 提取 Prompt 必须包含 critical 标记指南：明确表达跨任务持续生效的行为约束（含"必须/禁止/始终/默认/以后/今后/from now on/always/never"等标记）且 confidence>=0.95 时才标记 critical=True。
- F4: Validator 必须允许 `category=project_knowledge` 且 `critical=True` 的候选通过关键偏好门控（当前规则仅允许 preference 和 correction）。
- F5: Validator 的 evidence 匹配必须从"原始子串匹配"改为"归一化后子串匹配"（NFKC + casefold + 去标点 + 空白压缩），容忍模型对原文的轻微改写。
- F6: 记忆索引条目格式从 `- [关键偏好] title: body` 扩展为包含标签和证据摘要的多行格式，确保跨会话模型能理解约束的完整语义。
- F7: 记忆注入引导语必须强化：(a) 将 critical=True 的用户偏好提升为"必须遵守的行为规则"，(b) 明确声明"索引中已有的背景信息不得要求用户重复提供，违反此规则将导致任务失败"。
- F8: `index_max_lines` 默认值从 200 提升到 400，`index_max_bytes` 从 25,000 提升到 50,000。
- F9: 现有全部测试必须继续通过；既有记忆文件格式保持兼容。

## 非功能需求

- N1: 提取 Prompt 改动不影响现有 JSON schema 结构（operations 数组、字段名不变），只优化指令文本。
- N2: Validator 规则调整必须保持所有现有拒绝码（invalid_schema、not_persistent、missing_user_evidence、sensitive_content 等）的语义不变，仅放宽两个条件（critical 类别范围、evidence 匹配方式）。
- N3: 索引格式调整必须向后兼容：旧索引文件仍可被新代码读取。
- N4: 改动不涉及新依赖、新服务、新配置文件。

## 不做的事

- 不引入新的 memory category 或 scope 值
- 不修改 MemoryNote 数据结构和磁盘格式
- 不修改评测数据集（extraction.json / inheritance.json）
- 不修改评测匹配逻辑（ExtractionMatcher）
- 不修改 SessionMemoryConfig 除 index_max_lines/index_max_bytes 外的默认值
- 不引入 embedding、向量检索或 LLM judge 做二次校验
- 不在提取阶段引入多轮 LLM 调用

## 验收标准

- AC1: 所有既有单元测试通过（`pytest tests/test_memory_extraction.py tests/test_memory_updater.py tests/test_memory_notes.py tests/test_memory_index.py tests/test_prompting.py`）。
- AC2: 新增/修改的 Validator 单元测试覆盖：(a) project_knowledge + critical=True 候选通过，(b) evidence 归一化匹配通过，(c) 归一化后仍不匹配的证据被拒绝。
- AC3: 新增/修改的索引构建测试覆盖：(a) 关键偏好正确标记，(b) 新格式包含标签和证据摘要，(c) 索引不超过新的上限。
- AC4: 离线提取评测（`python eval/run_memory_eval.py --mode offline`）全部通过，无回归。
- AC5: 在线提取评测整体 F1 >= 85%、关键偏好 Precision >= 98%、关键偏好命中 >= 45。
- AC6: 在线跨会话评测首轮理解正确率 >= 90%、背景重复说明减少率 >= 80%。
- AC7: 提取 Prompt 文本审查：每条 category 有明确定义和至少一个正例和一个负例（skip 的情况）。
