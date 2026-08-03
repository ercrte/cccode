# Prompt Cache Hit Rate Plan

## 架构概览
本次优化保持现有 Agent Loop、会话历史和工具执行流程不变，只调整提示块拆分、Provider 请求序列化和缓存配置。核心思路是把模型仍需看到、但跨连续请求通常不频繁变化的内容放进“可缓存运行时前缀”，让它位于真正动态的运行时补充之前；Provider 再根据协议把可缓存前缀映射成更容易命中的请求结构。

`julycode.prompting` 负责产出更细粒度的 `PromptBlock`。全局稳定提示仍放在 `PromptBundle.stable_blocks`；允许工具摘要和项目指令等内容放在 `runtime_blocks` 中但标记 `cacheable=True`；当前工作目录、模式轮次、当前用户目标、激活 Skill、Hook 注入、上下文摘要、恢复提示和团队动态状态仍放在 `cacheable=False` 的运行时块里。

`julycode.providers.openai` 负责把稳定提示和可缓存运行时块合并成请求前面的 system 消息，把动态运行时块放在后续 system 消息里，并在启用时发送稳定 hash 形式的 `prompt_cache_key`。如兼容 OpenAI 的网关拒绝缓存参数，Provider 只对缓存参数不兼容场景重试一次不带缓存参数的请求。

`julycode.providers.anthropic` 负责把稳定提示和可缓存运行时块按顺序放在 `system` 文本块前部，并把 `cache_control` 放在最后一个可缓存前缀块上；动态运行时块继续位于其后且不设置 `cache_control`。这样显式断点不会落在每轮变化内容之后。

`julycode.config` 增加缓存优化配置。默认开启安全的 OpenAI cache key 和 Anthropic cache_control；OpenAI retention 默认不发送，避免不支持该参数的模型或兼容接口被默认影响。用户可显式关闭优化或配置 retention。

## 核心数据结构

### `PromptBlock`
```python
@dataclass(frozen=True)
class PromptBlock:
    name: str
    title: str
    text: str
    stable: bool
    cacheable: bool = False
```

沿用现有结构。新的约定是：`stable=True` 表示全局固定提示；`stable=False, cacheable=True` 表示运行期生成但内容可作为当前请求系列的缓存前缀；`cacheable=False` 表示每轮动态内容，Provider 不应把缓存断点放在它之后。

### `PromptBundle`
```python
@dataclass(frozen=True)
class PromptBundle:
    stable_blocks: Sequence[PromptBlock]
    runtime_blocks: Sequence[PromptBlock]
```

沿用现有结构。Provider 通过 `block.cacheable` 区分运行时块中的缓存前缀和动态后缀，不新增字段，减少调用方迁移。

### `PromptCacheConfig`
```python
PromptCacheRetention = Literal["in_memory", "24h"]

@dataclass(frozen=True)
class PromptCacheConfig:
    enabled: bool = True
    key_namespace: str = "julycode"
    openai_cache_key: bool = True
    openai_retention: PromptCacheRetention | None = None
    anthropic_cache_control: bool = True
```

`enabled` 是总开关。`key_namespace` 只参与 hash 前缀命名，不包含用户目标、路径、密钥或原文内容。`openai_cache_key` 控制是否发送 `prompt_cache_key`。`openai_retention` 仅在用户显式配置时发送。`anthropic_cache_control` 控制是否设置显式断点。

### `OpenAIProvider`
```python
def _payload(self, request: ChatRequest, *, include_cache_options: bool = True) -> dict[str, Any]: ...
def _prompt_messages(self, request: ChatRequest) -> list[dict[str, Any]]: ...
def _prompt_cache_key(self, request: ChatRequest) -> str | None: ...
def _is_cache_option_unsupported(self, error: ProviderError) -> bool: ...
```

`_payload()` 在 `include_cache_options=True` 且配置启用时加入缓存参数。`_prompt_cache_key()` 对模型名、可缓存提示文本和工具 schema 的规范化 JSON 求 hash，只返回短 hash key。`_is_cache_option_unsupported()` 只识别明确指向 `prompt_cache_key` 或 `prompt_cache_retention` 的 400/422 错误。

### `AnthropicProvider`
```python
def _system_blocks(self, request: ChatRequest) -> list[dict[str, Any]]: ...
def _cache_prefix_blocks(self, request: ChatRequest) -> tuple[PromptBlock, ...]: ...
def _dynamic_runtime_blocks(self, request: ChatRequest) -> tuple[PromptBlock, ...]: ...
```

`_cache_prefix_blocks()` 返回稳定提示和 `cacheable=True` 的运行时块。`_system_blocks()` 先序列化缓存前缀，再序列化动态块，并仅在缓存前缀最后一个块上设置 `cache_control`。

## 模块设计

### `julycode.prompting.builder`
**职责：** 拆分运行时提示，把可缓存前缀和动态后缀分成独立 `PromptBlock`。  
**对外接口：** 保持 `build_stable_prompt()`、`build_runtime_prompt(context)`、`build_bundle(context)` 不变。  
**依赖：** `RuntimePromptContext`、`KnowledgeContext`、`ToolSpec`。

`build_runtime_prompt()` 返回两个或更多块：
- `runtime_cache_prefix`：`stable=False, cacheable=True`，包含允许工具摘要和项目指令。
- `runtime_context`：`stable=False, cacheable=False`，包含 cwd、模式轮次、当前目标、Skill 激活内容、子 Agent/团队动态状态、Hook 注入、记忆索引、恢复提示、上下文摘要和模式约束。

项目指令从现有 `_knowledge_context_lines()` 中拆出；用户/项目记忆索引和恢复提示保留在动态块中，因为它们可能随会话恢复、后台记忆更新或警告变化。

### `julycode.providers.openai`
**职责：** 生成更稳定的前缀消息，发送可选缓存参数，解析 usage 缓存字段。  
**对外接口：** `stream_chat(request)` 不变。  
**依赖：** `AppConfig.prompt_cache`、`ChatRequest`、`PromptBlock`、`ToolSpec`。

请求消息顺序：
1. `system`：全局稳定提示 + 可缓存运行时前缀。
2. `system`：动态运行时补充。
3. 原会话消息。

缓存参数：
- 当 `prompt_cache.enabled` 和 `openai_cache_key` 均为真时，添加 `prompt_cache_key`。
- 当 `openai_retention` 非空时，添加 `prompt_cache_retention`。
- 如果首个请求因为这两个参数不被兼容接口识别而失败，重试一次不带缓存参数的同一请求。

usage 解析保持现有 `cached_tokens` 逻辑不变。

### `julycode.providers.anthropic`
**职责：** 设置不落在动态内容之后的显式缓存断点，保持 usage 解析。  
**对外接口：** `stream_chat(request)` 不变。  
**依赖：** `AppConfig.prompt_cache`、`ChatRequest`、`PromptBlock`。

系统块顺序：
1. 全局稳定提示块。
2. `cacheable=True` 的运行时前缀块。
3. `cacheable=False` 的动态运行时块。

当缓存优化开启且存在缓存前缀时，最后一个缓存前缀块添加 `cache_control: {"type": "ephemeral"}`。动态运行时块永远不添加 `cache_control`。工具定义和消息协议保持现有序列化方式。

### `julycode.config`
**职责：** 解析缓存优化配置并提供默认值。  
**对外接口：** `AppConfig` 增加 `prompt_cache: PromptCacheConfig`。  
**依赖：** YAML 配置读取和现有 `_parse_config()`。

配置示例：
```yaml
prompt_cache:
  enabled: true
  key_namespace: julycode
  openai_cache_key: true
  openai_retention: 24h
  anthropic_cache_control: true
```

解析规则：
- 缺省时使用安全默认值。
- `key_namespace` 不能为空。
- `openai_retention` 只允许 `in_memory`、`24h` 或空值。
- 布尔字段按 YAML 布尔值解析。

### `README.md`
**职责：** 说明缓存优化策略、配置项和限制。  
**对外接口：** 文档章节“结构化系统提示与缓存观测”。  
**依赖：** 无。

文档强调 JulyCode 会提高命中概率，但实际命中仍取决于供应商、模型、请求长度、请求间隔、缓存 TTL 和完全一致的前缀。

## 模块交互
```text
AgentLoopRunner
  → PromptBuilder.build_bundle(RuntimePromptContext)
      → stable_blocks
      → runtime_blocks(cacheable prefix + dynamic suffix)
  → ChatSession.build_request(tools, prompt)
  → Provider.stream_chat(ChatRequest)
      → OpenAIProvider/AnthropicProvider 序列化缓存前缀和动态后缀
      → 发送请求
      → 解析 usage.cache
  → ContextManager.record_usage()
  → TUI 状态栏显示 Cache 状态
```

OpenAI 降级路径：
```text
OpenAIProvider.stream_chat()
  → 首次请求 include_cache_options=True
  → 如果 HTTP 400/422 明确表示缓存参数未知
  → 第二次请求 include_cache_options=False
  → 流式处理逻辑复用原路径
```

## 文件组织
```text
julycode/
├── src/julycode/config.py              — PromptCacheConfig、配置解析和默认值
├── src/julycode/prompting/builder.py   — 运行时提示拆分为可缓存前缀和动态后缀
├── src/julycode/providers/openai.py    — OpenAI 缓存参数、cache key、兼容降级
├── src/julycode/providers/anthropic.py — Anthropic cache_control 断点位置
├── README.md                          — 缓存优化配置和限制说明
├── tests/test_config.py               — prompt_cache 配置默认值和解析
├── tests/test_prompting.py            — 可缓存运行时块拆分和动态内容后置
├── tests/test_openai_provider.py      — payload、cache key、retention、降级和 usage
├── tests/test_anthropic_provider.py   — cache_control 位于缓存前缀末尾且动态块无断点
└── tests/test_agent.py                — Agent Loop 请求仍携带 prompt 且工具流程不变
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 运行时缓存前缀承载 | 复用 `PromptBlock.cacheable`，不扩展 `PromptBundle` 字段 | 现有类型已表达“可缓存”语义，调用方迁移最少。 |
| 动态运行时位置 | 仍作为系统级补充，但放在可缓存前缀之后 | 不把系统约束降级成用户消息，同时避免动态内容过早破坏缓存前缀。 |
| OpenAI cache key | 默认发送短 hash 形式 `prompt_cache_key`，必要时兼容降级 | 官方建议一致使用 cache key；hash 不泄露原文；降级保护兼容接口。 |
| OpenAI retention | 默认不发送，用户显式配置才发送 | retention 支持范围与模型相关，默认发送可能破坏兼容性。 |
| Anthropic 断点 | 设置在最后一个可缓存前缀块，不设置在动态块之后 | 符合官方“断点放在相同前缀末尾”的建议，避免持续写入但无读取。 |
| 历史消息断点 | 本阶段不在消息历史上新增断点 | 当前动态系统补充位于消息之前，消息断点会把动态内容纳入前缀，反而不稳定；为保持语义不重排到用户消息。 |
| 命中保证 | 只提高命中概率，不承诺命中 | 实际命中由供应商路由、TTL、模型和请求间隔决定。 |

## 需求覆盖

| 需求 | 设计覆盖 |
|------|----------|
| F1 | 可缓存运行时前缀 + OpenAI cache key + Anthropic cache_control |
| F2 | 动态运行时块后置，Provider 不在动态块之后设置断点 |
| F3 | Anthropic 断点设置在最后一个可缓存前缀块 |
| F4 | OpenAI `prompt_cache_key` 默认开启，retention 可配置 |
| F5 | Agent Loop、会话消息、工具序列化和工具结果关联保持原接口 |
| F6 | usage 解析保持现有逻辑，并新增回归测试 |
| F7 | README 说明能力边界和观测方式 |
