# 记忆索引注入优化 Checklist

> 每一项通过运行代码或观察行为来验证。

## 实现完整性
- [ ] `<julycode_memory_index>` 块从 `_dynamic_knowledge_context_lines` 移到 `_runtime_cache_prefix_lines`（验证：grep 确认 builder.py 中记忆索引生成在 cacheable 分支）
- [ ] `_dynamic_knowledge_context_lines` 不再输出 `<julycode_memory_index>`（验证：grep 确认）

## 测试
- [ ] 修改后的 prompting 测试通过（验证：`pytest tests/test_prompting.py -v`）
- [ ] 全量测试通过（验证：`pytest tests/`）

## 端到端
- [ ] 场景：含记忆索引的 PromptBlock 的 `cacheable=True`（验证：单元测试中构造含记忆的 context，检查生成的 block 属性）
- [ ] 场景：会话启动 → 第一条消息带记忆注入 → 第二条消息记忆不再重复注入（验证：运行 agent 测试 `test_runner_injects_memory_context_before_model_request`）
