# 记忆索引注入优化 Plan

## 架构概览
不动整体架构。仅改 `builder.py` 中记忆索引块的归属：从非缓存 `runtime_context` 迁移到可缓存 `runtime_cache_prefix`。

## 核心改动

### 改动点一：记忆索引提到可缓存区

当前代码路径：

```
build_runtime_prompt()
  ├─ _runtime_cache_prefix_lines()    → PromptBlock(name="runtime_cache_prefix", cacheable=True)
  └─ _dynamic_knowledge_context_lines()  → 混在 runtime_context 中, cacheable=False
         └─ <mewcode_memory_index> ... </mewcode_memory_index>
```

改为：

```
build_runtime_prompt()
  ├─ _runtime_cache_prefix_lines()    → PromptBlock(name="runtime_cache_prefix", cacheable=True)
  │     └─ <mewcode_memory_index> ... </mewcode_memory_index>   ← 移到这里
  ├─ _dynamic_knowledge_context_lines() → 仅保留 restore notice
  └─ ...
```

### 改动点二：缓存自动刷新

`prompt_factory()` 每轮调用 `self.memory_manager.runtime_context()` 获取最新 `KnowledgeContext`，然后调用 `build_bundle()`。可缓存前缀的构建同样读取这个值。后台更新后 `KnowledgeContext` 变了 → 下轮可缓存前缀内容变了 → Anthropic 自动视为新前缀，缓存自然失效。

## 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/mewcode/prompting/builder.py` | 修改 | 记忆索引生成从 `_dynamic_knowledge_context_lines` 移到 `_runtime_cache_prefix_lines` |
| `tests/test_prompting.py` | 修改 | 断言适配新位置 |

## 技术决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 目标 block | `runtime_cache_prefix` | 已有可缓存块，直接复用 |
| 缓存策略 | 内容变化自动破缓存 | 无需手动管理 |
| restore notice | 留在原处 | 恢复通知是单纯警告，小到不值得缓存 |
