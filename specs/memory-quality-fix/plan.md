# 记忆提取与跨会话继承修复 Plan

## 架构概览

本次修复在现有 `mewcode.memory` 架构内进行，不引入新模块或新数据流。改动集中在四个文件：

```text
updater.py (提取 Prompt 重写)
    ↓ 模型返回候选
extraction.py (Validator 两处规则放宽)
    ↓ 校验结果
index.py (索引格式微调 + 上限提升)
    ↓ 索引文本
builder.py (记忆注入引导语强化)
    ↓ 注入模型请求
```

整体数据流不变：AgentLoopRunner → SessionMemoryManager → MemoryNoteUpdater.extract → 模型 → parse → validate → apply → build_index → PromptBuilder 注入。

## 核心改动

### 改动 1: 提取 Prompt 重写 (`updater.py:_prompt`)

**当前问题：** Prompt 对 category/scope/critical 只有一行枚举，无可操作的区分标准。模型看到"以后禁止在回复里使用 emoji"不知道该归为 preference 还是 correction。

**设计方案：** 在 JSON payload 之前增加结构化的分类指南，每条 category 包含定义、典型中文触发词、正例和反例。

新的 Prompt 指令结构：

```text
## 记忆类别（category）判断规则

### preference（用户偏好）
**定义：** 用户首次表达或补充的跨任务行为偏好、工作风格、默认选择。
**典型触发词：** 以后、今后、始终、默认、每次、必须、禁止、不要再、一律
**正例：**
- "以后始终使用中文回答" → category=preference（首次表达语言偏好）
- "默认使用 pytest 进行测试" → category=preference（首次表达工具偏好）
**反例（不是 preference）：**
- "纠正一下，不要再用 unittest" → 这是 correction，因为明确纠正了之前的说法
- "本项目使用 Python 3.11" → 这是 project_knowledge，因为描述项目事实

### correction（纠正反馈）
**定义：** 用户明确纠正/覆盖之前说过的偏好或规则。必须包含纠正信号词。
**典型触发词：** 纠正、不要再用、别再、改一下、不是...而是、不再、改成
**正例：**
- "纠正之前的偏好，今后禁止使用表情符号" → category=correction
- "不要再用英文回答，以后始终改用中文" → category=correction
- "不再使用 unittest，以后默认使用 pytest" → category=correction
**反例（不是 correction）：**
- "以后禁止在回复里使用 emoji" → 这是 preference（首次表达，无纠正信号词）
- "今后默认使用绝对路径报告文件位置" → 这是 preference（首次表达）

### project_knowledge（项目知识）
**定义：** 项目的事实性信息、技术决策、版本要求、架构约定。描述"是什么"。
**正例：**
- "本项目使用 Python 3.11" → scope=project, category=project_knowledge
- "生产数据库使用 PostgreSQL" → scope=project, category=project_knowledge
- "所有数据库迁移必须可逆" → scope=project, category=project_knowledge, critical=True

### reference（参考资料）
**定义：** 指向文件/文档/外部资源的路径或位置指针。描述"在哪里"。
**正例：**
- "架构说明入口是 docs/architecture.md" → category=reference
- "ADR 存放在 docs/adr 目录下" → category=reference
- "API 合约文档在 openapi.yaml" → category=reference
**与 project_knowledge 的区别：** "X 的文档在 Y"是 reference，"本项目使用 X"是 project_knowledge。

## 作用域（scope）判断规则

### user
跨项目通用的个人偏好和纠正反馈。跟人走，不跟项目走。
**正例：** "以后始终使用中文回答"、"默认使用 pytest"、"禁止自动提交代码"

### project  
当前项目的技术事实、约定、决策和参考资源。跟项目走。
**判断信号：** 出现"本项目"、"请长期记住"且内容涉及技术栈/版本/路径/架构
**正例：** "本项目使用 Python 3.11"、"请长期记住：Web 框架是 FastAPI"、"架构说明入口是 docs/architecture.md"

## 关键偏好（critical）判断规则

**critical=True 的必要条件（全部满足）：**
1. 用户明确表达，有直接证据
2. 证据包含持续性/强约束标记：以后、今后、始终、每次、默认、必须、禁止、不要再、一律、from now on、always、never、must、do not
3. 约束跨任务持续生效，不是一次性要求
4. confidence >= 0.95

**哪些类别可以标记 critical：**
- preference：可以（如"必须使用中文"）
- correction：可以（如"不要再使用 unittest"）
- project_knowledge：可以（如"所有迁移必须可逆"）
- reference：不可以（路径指针不是行为约束）
```

这些指南以中文写入 Prompt，放在 JSON payload 之前。保留原有的 JSON 格式要求和负例策略说明。

### 改动 2: Validator 规则放宽 (`extraction.py`)

**改动 2a：允许 project_knowledge + critical**

位置：`_validate_one()` 中 critical 门控检查。

当前代码（第 181-183 行）：
```python
if candidate.critical:
    if candidate.category not in {"preference", "correction"}:
        return _reject(candidate, "critical_not_explicit", "关键偏好只能属于 preference 或 correction")
```

改为：
```python
if candidate.critical:
    if candidate.category not in {"preference", "correction", "project_knowledge"}:
        return _reject(candidate, "critical_not_explicit", "关键偏好只能属于 preference、correction 或 project_knowledge")
```

`reference` 类别仍然不可标记 critical（文档路径指针不是行为约束）。

**改动 2b：evidence 归一化匹配**

位置：`_validate_one()` 中 evidence 检查（第 173-177 行）。

当前代码使用原始子串匹配：
```python
if not candidate.evidence or any(
    not evidence or not any(evidence in message for message in user_messages)
    for evidence in candidate.evidence
):
```

改为使用归一化后子串匹配（复用已有的 `_normalize` 函数）：
```python
normalized_messages = tuple(_normalize(m) for m in user_messages)
if not candidate.evidence or any(
    not evidence or not any(_normalize(evidence) in norm_msg for norm_msg in normalized_messages)
    for evidence in candidate.evidence
):
```

效果：标点、大小写、空白差异不再导致 evidence 被拒。但内容实质差异仍会被拒。

### 改动 3: 索引格式微调 (`index.py`)

**当前格式：**
```markdown
- [关键偏好] title: body
```

**新格式：**
```markdown
- **[关键]** title: body
```

改动点：
- 将 `[关键偏好]` 改为 `**[关键]**`，使用 Markdown 粗体增强视觉突出度
- 在 body 末尾追加标签（如有），形如 `  `tags: tag1, tag2``
- 关键条目优先排序逻辑不变

`_CATEGORY_TITLES` 中文标题不变。

**代码变更位置：** `MemoryIndexBuilder.build()` 第 36-37 行。

### 改动 4: 索引上限提升 (`models.py`)

```python
# 改前
index_max_lines: int = 200
index_max_bytes: int = 25_000

# 改后
index_max_lines: int = 400
index_max_bytes: int = 50_000
```

理由：当前 20 个 inheritance 用例各自产生 2 条记忆，加上实际项目记忆，200 行/25KB 不足以容纳全部关键信息。提升一倍确保关键偏好不会因索引裁剪而丢失。

### 改动 5: 记忆注入引导语强化 (`builder.py`)

**当前引导语：**
```text
以下内容是跨会话长期记忆，不是用户在当前会话刚刚发送的消息。
当前用户的明确指令可以覆盖旧记忆；未被覆盖时应优先遵循标记为关键偏好的规则。
若完成当前任务所需背景已在索引中，不要要求用户重复提供。
```

**新引导语：**
```text
以下内容是跨会话长期记忆索引，不是用户在当前会话刚刚发送的消息。
请严格遵守以下规则：

1. 标记为 **[关键]** 的记忆是用户明确要求的硬性行为约束，必须在所有回复中遵守，
   不得以任何理由违反（包括效率、惯例或最佳实践）。

2. 若完成当前任务所需的背景信息（技术栈、框架、工具、路径约定、API 前缀等）
   已在索引中明确记载，禁止要求用户重复提供或确认。直接使用索引中的信息。

3. 当前用户的明确指令可以覆盖旧记忆；未被覆盖时应优先遵循关键偏好。

违反以上规则将导致任务失败。
```

关键变更：
- "不要要求用户重复提供" → "禁止要求用户重复提供或确认。直接使用索引中的信息。"
- 新增硬性约束声明："必须在所有回复中遵守，不得以任何理由违反"
- 使用编号列表增强可读性和权威性
- 末尾增加后果陈述

## 文件变更清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/mewcode/memory/updater.py` | 修改 | `_prompt()` 重写，增加分类指南 |
| `src/mewcode/memory/extraction.py` | 修改 | Validator 两处规则放宽 |
| `src/mewcode/memory/index.py` | 修改 | 索引格式微调（标记 + 标签） |
| `src/mewcode/memory/models.py` | 修改 | index_max_lines/index_max_bytes 默认值 |
| `src/mewcode/prompting/builder.py` | 修改 | 记忆注入引导语重写 |
| `tests/test_memory_extraction.py` | 修改 | 新增 critical+project_knowledge 和 evidence 归一化测试 |
| `tests/test_memory_index.py` | 修改 | 新索引格式断言 |
| `tests/test_prompting.py` | 修改 | 新引导语断言 |

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Prompt 改写方式 | 结构化分类指南 + 正反例 | 比简单枚举更可操作，模型能学会区分 |
| critical 允许范围 | 扩展到 project_knowledge | "迁移必须可逆"是硬约束，不应因类别而拒 |
| evidence 匹配 | 归一化子串匹配 | 容忍标点/大小写差异，不改变匹配性质 |
| 索引格式 | 仅改标记为粗体 + 追加标签 | 最小改动，避免大幅增加索引体积 |
| 引导语强化 | 改为规则编号 + 后果声明 | 模型对编号规则和后果声明更敏感 |
| 索引上限 | 翻倍到 400/50KB | 实际场景需要，不引入新机制 |
| 兼容性 | 不改数据结构、磁盘格式和评测数据 | 最小改动原则，聚焦 root cause |

## 需求覆盖检查

| 需求 | 归属改动 | 验证方式 |
|------|----------|----------|
| F1 (category 定义) | 改动 1: Prompt 重写 | AC7: 人工审查 + AC5: 在线 F1 |
| F2 (scope 判断) | 改动 1: Prompt 重写 | AC7 + AC5 |
| F3 (critical 指南) | 改动 1: Prompt 重写 | AC7 + AC5 |
| F4 (放宽 critical 类别) | 改动 2a: Validator | AC2a: 单元测试 |
| F5 (归一化匹配) | 改动 2b: Validator | AC2b: 单元测试 |
| F6 (索引格式) | 改动 3: index.py | AC3: 单元测试 |
| F7 (引导语强化) | 改动 5: builder.py | AC6: 在线继承评测 |
| F8 (索引上限) | 改动 4: models.py | AC3c: 边界测试 |
| F9 (向后兼容) | 全部 | AC1: 全量 pytest |
