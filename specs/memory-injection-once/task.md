# 记忆索引注入优化 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/mewcode/prompting/builder.py` | 记忆索引从动态区移到缓存区 |
| 修改 | `tests/test_prompting.py` | 断言适配 |

## T1: 迁移记忆索引到可缓存前缀

**文件：** `src/mewcode/prompting/builder.py`
**依赖：** 无
**步骤：**
1. 把 `_dynamic_knowledge_context_lines()` 中构建 `<mewcode_memory_index>` 块的逻辑（约 314-335 行）移到 `_runtime_cache_prefix_lines()`（约 85-92 行）。
2. 移完后 `_dynamic_knowledge_context_lines()` 仅保留 `<mewcode_restore_notice>` 块。
3. 确保 `_runtime_cache_prefix_lines` 的签名支持接收 `KnowledgeContext`（当前不接收，需加参数或从调用方传入）。

**验证：** 肉眼检查 prompt 结构，确认 `<mewcode_memory_index>` 现在位于 `cacheable=True` 的 block 中。

## T2: 更新测试断言

**文件：** `tests/test_prompting.py`
**依赖：** T1
**步骤：**
1. 更新 `test_runtime_prompt_includes_memory_indexes`：改为检查 cacheable block 而非 runtime block。
2. 新增 `test_memory_index_is_cacheable`：验证包含记忆索引的 PromptBlock 的 `cacheable` 属性为 True。

**验证：** `pytest tests/test_prompting.py -v` 全部通过。

## T3: 全量回归

**文件：** 全部
**依赖：** T2
**步骤：** `pytest tests/`

**验证：** 802 passed。

## 执行顺序

```text
T1 → T2 → T3
```
