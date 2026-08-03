# 记忆索引注入优化 Spec

## 背景
当前每轮模型请求都注入完整的记忆索引文本（`<julycode_memory_index>`），放在 runtime prompt 的不可缓存区块中。每次注入相同内容，浪费 token 且无法享受 Anthropic prompt caching。记忆索引在会话启动到下一轮之间有变化的概率极低（后台异步更新需要时间），实际不需要每轮完整注入。

## 目标
- 记忆索引从"每轮注入"改为"会话级注入"，享受 prompt caching
- 后台记忆更新后，下一轮请求自动反映新索引

## 功能需求
- F1: 记忆索引块从 `cacheable=False` 的 runtime 区迁移到 `cacheable=True` 的可缓存区
- F2: 可缓存区每轮由 `prompt_factory()` 读取最新 `KnowledgeContext` 构建，因此后台更新后下一轮自动刷新

## 非功能需求
- N1: 不影响记忆提取、校验、落盘、索引构建的任何逻辑
- N2: 不新增配置项或 API
- N3: 全部既有测试通过

## 不做的事
- 不做主动缓存失效机制（内容变化自动破坏缓存，无需手动管理）
- 不改变记忆注入的内容和格式

## 验收标准
- AC1: 记忆索引内容出现在 `cacheable=True` 的 PromptBlock 中
- AC2: 全量 pytest 通过
- AC3: 手动验证：启动会话、发一条消息、确认记忆注入正常、发第二条消息、确认记忆不再重复注入
