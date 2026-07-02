# Prompt Cache Hit Rate Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/mewcode/config.py` | 新增 `PromptCacheConfig` 并解析 `prompt_cache` 配置 |
| 修改 | `src/mewcode/prompting/builder.py` | 拆分可缓存运行时前缀和动态运行时补充 |
| 修改 | `src/mewcode/providers/openai.py` | 生成缓存友好的 system 消息、发送缓存参数、兼容降级 |
| 修改 | `src/mewcode/providers/anthropic.py` | 把 `cache_control` 放到最后一个可缓存前缀块 |
| 修改 | `README.md` | 说明缓存优化配置、观测方式和限制 |
| 修改 | `tests/test_config.py` | 覆盖配置默认值、显式配置和非法配置 |
| 修改 | `tests/test_prompting.py` | 覆盖运行时提示拆分和动态内容后置 |
| 修改 | `tests/test_openai_provider.py` | 覆盖 OpenAI payload、cache key、retention、降级和 usage |
| 修改 | `tests/test_anthropic_provider.py` | 覆盖 Anthropic 缓存断点位置和 usage |
| 修改 | `tests/test_agent.py` | 回归 Agent Loop 请求和工具流程 |

## T1: 增加 Prompt Cache 配置测试

**文件：** `tests/test_config.py`  
**依赖：** 无  
**步骤：**
1. 添加默认配置断言，确认 `AppConfig.prompt_cache` 缺省为开启、安全 namespace、OpenAI cache key 开启、retention 为空、Anthropic cache control 开启。
2. 添加显式 YAML 配置测试，覆盖 `enabled`、`key_namespace`、`openai_cache_key`、`openai_retention`、`anthropic_cache_control`。
3. 添加非法配置测试，覆盖空 `key_namespace` 和非法 `openai_retention`。

**验证：** 运行 `python -m pytest tests/test_config.py -q`，期望新增测试先失败，失败点指向缺少 `prompt_cache` 配置。

## T2: 实现 Prompt Cache 配置解析

**文件：** `src/mewcode/config.py`  
**依赖：** T1  
**步骤：**
1. 新增 `PromptCacheRetention` 类型和 `PromptCacheConfig` dataclass。
2. 给 `AppConfig` 增加 `prompt_cache: PromptCacheConfig = field(default_factory=PromptCacheConfig)`。
3. 在 `_parse_config()` 中调用 `_parse_prompt_cache(raw.get("prompt_cache"))`。
4. 实现 `_parse_prompt_cache()`，按 plan 的默认值、枚举和非空规则解析。

**验证：** 运行 `python -m pytest tests/test_config.py -q`，期望配置测试全部通过。

## T3: 增加提示拆分测试

**文件：** `tests/test_prompting.py`  
**依赖：** T2  
**步骤：**
1. 添加测试确认 `PromptBuilder.build_runtime_prompt()` 返回可缓存前缀块和动态块。
2. 断言可缓存前缀块 `stable=False` 且 `cacheable=True`，包含允许工具摘要。
3. 添加带项目指令的 `KnowledgeContext` 测试，断言项目指令进入可缓存前缀，记忆索引和恢复提示仍在动态块。
4. 断言 cwd、模式轮次、当前用户目标、Hook 注入、上下文摘要不出现在可缓存前缀块中。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q`，期望新增测试先失败，失败点指向运行时提示仍只有一个动态块。

## T4: 实现运行时提示拆分

**文件：** `src/mewcode/prompting/builder.py`  
**依赖：** T3  
**步骤：**
1. 将允许工具摘要从动态运行时块迁移到 `runtime_cache_prefix` 块。
2. 将项目指令从 `_knowledge_context_lines()` 拆入可缓存前缀块。
3. 保留 cwd、模式轮次、当前用户目标、Skill、子 Agent、团队状态、Hook 注入、记忆索引、恢复提示、上下文摘要和模式约束在动态块。
4. 确保无可缓存内容时不生成空前缀块。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q`，期望全部通过。

## T5: 增加 OpenAI Provider 缓存 payload 测试

**文件：** `tests/test_openai_provider.py`  
**依赖：** T4  
**步骤：**
1. 调整 `prompt_bundle()` fixture，让它包含一个 `cacheable=True` 的运行时块和一个动态运行时块。
2. 添加测试确认首个 system 消息包含稳定提示和可缓存运行时前缀，第二个 system 消息只包含动态运行时补充。
3. 添加测试确认默认 payload 包含稳定 hash 形式的 `prompt_cache_key`，且 key 不包含原始提示、路径或用户文本。
4. 添加测试确认配置 `openai_retention: 24h` 时 payload 包含 `prompt_cache_retention`。
5. 添加测试确认禁用 `prompt_cache.enabled` 或 `openai_cache_key` 后不会发送缓存参数。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py -q`，期望新增测试先失败，失败点指向缺少缓存参数和运行时块合并逻辑。

## T6: 实现 OpenAI 缓存参数和消息拆分

**文件：** `src/mewcode/providers/openai.py`  
**依赖：** T5  
**步骤：**
1. 调整 `_prompt_messages()`，把 `stable_blocks` 和 `runtime_blocks` 中 `cacheable=True` 的块合并为第一个 system 消息。
2. 将 `cacheable=False` 的运行时块合并为第二个 system 消息。
3. 实现 `_prompt_cache_key()`，对模型、缓存前缀文本和工具 schema 的规范化 JSON 计算短 hash，并加上 namespace。
4. 在 `_payload()` 中按配置添加 `prompt_cache_key` 和可选 `prompt_cache_retention`。
5. 保持工具 payload、消息 payload、stream usage 解析不变。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py -q`，期望除降级相关新增测试外全部通过。

## T7: 增加并实现 OpenAI 兼容降级

**文件：** `tests/test_openai_provider.py`、`src/mewcode/providers/openai.py`  
**依赖：** T6  
**步骤：**
1. 添加测试模拟首个请求返回 400 且错误正文包含 `prompt_cache_key` 或 `prompt_cache_retention`，第二个请求成功。
2. 断言第一次 payload 带缓存参数，第二次 payload 不带缓存参数，最终仍产出正常 `message_done`。
3. 添加测试模拟非缓存参数错误，断言不重试且仍抛出 `ProviderError`。
4. 在 `stream_chat()` 中实现只针对缓存参数不兼容错误的一次降级重试。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py -q`，期望全部通过。

## T8: 增加 Anthropic Provider 缓存断点测试

**文件：** `tests/test_anthropic_provider.py`  
**依赖：** T4  
**步骤：**
1. 调整 `prompt_bundle()` fixture，让它包含稳定块、可缓存运行时块和动态运行时块。
2. 更新结构化 system block 测试，断言 `cache_control` 位于最后一个可缓存前缀块。
3. 添加测试确认动态运行时块不包含 `cache_control`。
4. 添加测试确认禁用 `prompt_cache.enabled` 或 `anthropic_cache_control` 时不发送 `cache_control`。

**验证：** 运行 `python -m pytest tests/test_anthropic_provider.py -q`，期望新增测试先失败，失败点指向断点仍只看稳定块或无法禁用。

## T9: 实现 Anthropic 缓存断点调整

**文件：** `src/mewcode/providers/anthropic.py`  
**依赖：** T8  
**步骤：**
1. 新增 `_cache_prefix_blocks()`，返回稳定块和 `cacheable=True` 的运行时块。
2. 新增 `_dynamic_runtime_blocks()`，返回 `cacheable=False` 的运行时块。
3. 调整 `_system_blocks()`，先输出缓存前缀块，再输出动态块。
4. 按 `prompt_cache.enabled` 和 `anthropic_cache_control` 在最后一个缓存前缀块添加 `cache_control`。
5. 保持 message、tool、thinking 和 usage 解析逻辑不变。

**验证：** 运行 `python -m pytest tests/test_anthropic_provider.py -q`，期望全部通过。

## T10: 增加 Agent Loop 回归测试

**文件：** `tests/test_agent.py`  
**依赖：** T4、T6、T9  
**步骤：**
1. 扩展现有普通聊天或多工具迭代测试，断言 `provider.requests[0].prompt.runtime_blocks` 至少包含可缓存前缀和动态块。
2. 断言工具调用后的第二次请求仍保留原会话消息顺序，最后一条为 tool 结果。
3. 确认工具列表仍由策略传入，没有因为缓存优化丢失。

**验证：** 运行 `python -m pytest tests/test_agent.py -q`，期望全部通过。

## T11: 更新 README 缓存说明

**文件：** `README.md`  
**依赖：** T2、T4、T6、T9  
**步骤：**
1. 在“结构化系统提示与缓存观测”章节补充可缓存运行时前缀、OpenAI `prompt_cache_key`、可选 retention 和 Anthropic 显式断点。
2. 增加 `prompt_cache` YAML 配置示例。
3. 明确说明实际命中受供应商、模型、请求长度、请求间隔、TTL 和完全一致前缀影响，MewCode 不保证每次命中。

**验证：** 运行 `python -m pytest tests/test_config.py::test_readme_mentions_session_memory -q`，期望 README 相关回归仍通过；人工查看 README 章节包含 `prompt_cache_key`、`prompt_cache_retention`、`cache_control`。

## T12: 跑相关单元测试回归

**文件：** `tests/test_config.py`、`tests/test_prompting.py`、`tests/test_openai_provider.py`、`tests/test_anthropic_provider.py`、`tests/test_agent.py`、`tests/test_tui_smoke.py`  
**依赖：** T1-T11  
**步骤：**
1. 运行配置、提示、Provider、Agent 和 TUI 相关测试。
2. 修复因缓存提示拆分引起的断言更新或真实回归。
3. 确认 usage 缓存显示测试仍通过。

**验证：** 运行 `python -m pytest tests/test_config.py tests/test_prompting.py tests/test_openai_provider.py tests/test_anthropic_provider.py tests/test_agent.py tests/test_tui_smoke.py -q`，期望全部通过。

## T13: tmux 端到端验收

**文件：** 无代码文件；使用本地运行环境  
**依赖：** T12、已批准的 `checklist.md`  
**步骤：**
1. 在 tmux 中启动 MewCode。
2. 输入真实对话请求，例如“请查看 README 里关于缓存的说明，并告诉我当前项目如何观测缓存命中”。
3. 观察 MewCode 是否调用读取或搜索工具，并生成中文回复。
4. 对照 `checklist.md` 逐项记录通过或失败证据。

**验证：** 在验收报告中记录 tmux 会话观察结果，期望工具调用正常、回复正常、状态栏或 usage 事件能展示缓存状态。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13
```
