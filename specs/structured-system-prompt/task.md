# JulyCode Structured System Prompt Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/julycode/prompting/__init__.py` | 导出提示构造公共类型和构造器 |
| 新建 | `src/julycode/prompting/base.py` | 定义 PromptBlock、PromptBundle、RuntimePromptContext 和注入级别类型 |
| 新建 | `src/julycode/prompting/modules.py` | 定义固定系统提示模块文本和顺序 |
| 新建 | `src/julycode/prompting/builder.py` | 实现 PromptBuilder、运行时补充标签和轮次注入策略 |
| 新建 | `tests/test_prompting.py` | 覆盖提示模块顺序、稳定性、动态补充和注入频率 |
| 修改 | `src/julycode/providers/base.py` | 扩展 ChatRequest、TokenUsage 和缓存观测结构 |
| 修改 | `src/julycode/session.py` | build_request 支持携带 PromptBundle |
| 修改 | `src/julycode/commands.py` | /plan 与 /do 不再拼接系统控制指令 |
| 修改 | `tests/test_commands.py` | 更新命令解析期望 |
| 修改 | `tests/test_session.py` | 验证 prompt 可携带且不污染历史 |
| 修改 | `src/julycode/agent.py` | 每轮构造 RuntimePromptContext 和 PromptBundle |
| 修改 | `tests/test_agent.py` | 验证 Agent 请求携带提示、Plan/Do 注入行为不回退 |
| 修改 | `src/julycode/providers/openai.py` | 映射 system 消息并解析 cached_tokens |
| 修改 | `tests/test_openai_provider.py` | 覆盖 OpenAI 系统提示 payload 和缓存用量解析 |
| 修改 | `src/julycode/providers/anthropic.py` | 映射 system 文本块、cache_control 和缓存用量 |
| 修改 | `tests/test_anthropic_provider.py` | 覆盖 Anthropic 系统提示 payload 和缓存用量解析 |
| 修改 | `src/julycode/tools/builtin.py` | 强化内置工具描述中的关键约束 |
| 修改 | `tests/test_tools.py` | 验证工具描述包含关键约束且工具行为不变 |
| 修改 | `src/julycode/tui/widgets.py` | 状态栏显示统一缓存观测状态 |
| 修改 | `tests/test_tui_smoke.py` | 验证缓存状态展示 |
| 修改 | `tests/e2e_mock_openai_server.py` | mock usage 增加缓存字段，支持端到端观察 |
| 修改 | `README.md` | 记录结构化系统提示、缓存观测和 Plan Mode 注入变化 |
| 新建 | `specs/structured-system-prompt/manual-scenarios.md` | 典型人工对比场景 |

## T1: 定义提示基础类型

**文件：** `src/julycode/prompting/base.py`、`src/julycode/prompting/__init__.py`、`src/julycode/providers/base.py`  
**依赖：** 无  
**步骤：**
1. 新建 `PromptBlock`，包含 `name`、`title`、`text`、`stable`、`cacheable`。
2. 新建 `PromptBundle`，包含 `stable_blocks` 和 `runtime_blocks`。
3. 新建 `RuntimeInstructionLevel` 和 `RuntimePromptContext`。
4. 在 Provider 基础类型中新增 `CacheStatus`、`PromptCacheUsage`。
5. 扩展 `TokenUsage` 增加 `cache` 字段，扩展 `ChatRequest` 增加可选 `prompt` 字段。
6. 在 `src/julycode/prompting/__init__.py` 导出新类型。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py::test_openai_streams_usage_event tests/test_anthropic_provider.py::test_anthropic_streams_usage_events tests/test_session.py::test_empty_session_builds_empty_request -q`，期望全部通过。

## T2: 实现固定系统提示模块

**文件：** `src/julycode/prompting/modules.py`、`tests/test_prompting.py`  
**依赖：** T1  
**步骤：**
1. 定义七个固定模块：身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出。
2. 按批准顺序返回固定模块，模块之间通过后续构造器拼装时保留空行分隔。
3. 每个固定模块设置稳定标记，最后一个固定模块设置可缓存标记。
4. 在工具使用模块中写入专用工具优先、编辑前读取、写入和命令边界、工具失败后继续调整等规则。
5. 添加测试断言模块名称顺序、模块数量、稳定标记、可缓存断点和关键中文规则。

**验证：** 运行 `python -m pytest tests/test_prompting.py::test_stable_modules_are_ordered_and_cacheable tests/test_prompting.py::test_stable_modules_include_tool_rules -q`，期望全部通过。

## T3: 实现运行时补充构造器

**文件：** `src/julycode/prompting/builder.py`、`src/julycode/prompting/__init__.py`、`tests/test_prompting.py`  
**依赖：** T2  
**步骤：**
1. 实现 `PromptBuilder.build_stable_prompt()`，返回固定模块。
2. 实现 `PromptBuilder.build_runtime_prompt(context)`，生成带 `<julycode_runtime_context>` 标签的动态补充块。
3. 实现 `PromptBuilder.build_bundle(context)`，按稳定块在前、运行时块在后的顺序返回 bundle。
4. 实现注入级别计算：第 1 轮 `full`，之后每 3 轮 `refresh`，其余为 `brief`。
5. 在 `plan` 模式完整补充中写入只读工具约束，在 `do` 模式补充中写入当前待执行计划和全工具状态。
6. 添加测试覆盖 full、refresh、brief 三种轮次，断言动态内容包含 cwd、模式、轮次、标签和必要计划内容。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q`，期望全部通过。

## T4: 会话请求携带 PromptBundle

**文件：** `src/julycode/session.py`、`tests/test_session.py`  
**依赖：** T3  
**步骤：**
1. 修改 `ChatSession.build_request()`，新增 `prompt` 参数并传给 `ChatRequest`。
2. 保持 `ChatSession.messages` 只保存用户、助手和工具消息。
3. 添加测试构造一个 `PromptBundle`，断言 request 可携带 prompt。
4. 添加测试断言携带 prompt 后会话历史没有新增系统消息。

**验证：** 运行 `python -m pytest tests/test_session.py -q`，期望全部通过。

## T5: 收窄命令解析中的模型文本

**文件：** `src/julycode/commands.py`、`tests/test_commands.py`  
**依赖：** T4  
**步骤：**
1. 修改 `/plan <需求>` 的 `model_text`，只保留用户真实需求。
2. 修改 `/do` 的 `model_text`，只表达“执行当前待执行计划”的用户意图，不包含完整计划或全工具控制指令。
3. 保留 `/plan` 缺参和 `/do` 无计划时的可见提示。
4. 更新测试断言控制指令不再出现在 `model_text`。

**验证：** 运行 `python -m pytest tests/test_commands.py -q`，期望全部通过。

## T6: Agent Loop 注入结构化提示

**文件：** `src/julycode/agent.py`、`tests/test_agent.py`  
**依赖：** T5  
**步骤：**
1. 在 `AgentLoopRunner` 初始化时创建或接收 `PromptBuilder`。
2. 每轮模型请求前，根据当前工具策略、cwd、模式、轮次、迭代上限和待执行计划构造 `RuntimePromptContext`。
3. 调用 `PromptBuilder.build_bundle()` 并传给 `ChatSession.build_request()`。
4. 保持现有工具策略、工具调度、停止条件和 Plan/Do 状态更新逻辑不变。
5. 更新 Agent 测试，断言普通、plan、do 请求均携带 prompt。
6. 更新 Plan/Do 测试，断言只读约束和完整计划位于 `request.prompt.runtime_blocks`，不在用户消息里。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_streams_plain_chat_and_saves_message tests/test_agent.py::test_plan_mode_saves_pending_plan tests/test_agent.py::test_do_mode_executes_and_clears_pending_plan -q`，期望全部通过。

## T7: OpenAI Provider 映射提示和缓存用量

**文件：** `src/julycode/providers/openai.py`、`tests/test_openai_provider.py`  
**依赖：** T6  
**步骤：**
1. 在 `_payload()` 中把 `request.prompt.stable_blocks` 拼成首个 `system` 消息。
2. 把 `request.prompt.runtime_blocks` 拼成第二个 `system` 消息，放在稳定消息之后、会话消息之前。
3. 当 `request.prompt` 为空时保持现有 payload 行为。
4. 解析 `usage.prompt_tokens_details.cached_tokens`，生成 `PromptCacheUsage`。
5. 字段存在且 `cached_tokens > 0` 时标记 `hit`；字段存在且为 0 时标记 `miss`；字段缺失时标记 `unknown`。
6. 添加测试断言 system 消息顺序、标签、稳定内容和用户消息不混淆。
7. 添加缓存命中、未命中和未知字段的用量解析测试。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py -q`，期望全部通过。

## T8: Anthropic Provider 映射提示和缓存用量

**文件：** `src/julycode/providers/anthropic.py`、`tests/test_anthropic_provider.py`  
**依赖：** T6  
**步骤：**
1. 在 `_payload()` 中把 `request.prompt.stable_blocks` 映射为 `system` 文本块数组前缀。
2. 在最后一个稳定块上添加 `cache_control: {"type": "ephemeral"}`。
3. 把 `request.prompt.runtime_blocks` 映射为稳定块之后的 `system` 文本块，不添加缓存控制。
4. 当 `request.prompt` 为空时保持现有 payload 行为。
5. 解析 `cache_read_input_tokens`、`cache_creation_input_tokens` 和 `input_tokens`，生成 `PromptCacheUsage`。
6. 字段显示读取时标记 `hit`，显示创建时标记 `write`，字段存在但均为 0 时标记 `miss`，字段缺失时标记 `unknown`。
7. 添加测试断言 `system` 块顺序、缓存断点、运行时标签和消息历史不混淆。
8. 添加缓存读取、缓存创建、未命中和未知字段的用量解析测试。

**验证：** 运行 `python -m pytest tests/test_anthropic_provider.py -q`，期望全部通过。

## T9: 强化工具描述

**文件：** `src/julycode/tools/builtin.py`、`tests/test_tools.py`、`tests/test_openai_provider.py`、`tests/test_anthropic_provider.py`  
**依赖：** T7、T8  
**步骤：**
1. 更新 `read_file` 描述，强调用于读取已知文件内容和编辑前确认现状。
2. 更新 `write_file` 描述，强调会创建或覆盖文件，应只在需要完整写入时使用。
3. 更新 `edit_file` 描述，强调修改前应先读取或搜索，且原文必须唯一匹配。
4. 更新 `run_command` 描述，强调用于本地构建、测试、检查或用户明确需要的命令，可能有副作用。
5. 更新 `find_files` 和 `search_code` 描述，强调优先用于定位文件和查找代码。
6. 添加测试断言关键描述文本存在。
7. 更新 provider 工具 payload 测试中的预期描述。

**验证：** 运行 `python -m pytest tests/test_tools.py tests/test_openai_provider.py::test_openai_request_includes_tools_when_available tests/test_anthropic_provider.py::test_anthropic_request_includes_tools_when_available -q`，期望全部通过。

## T10: TUI 展示缓存状态

**文件：** `src/julycode/tui/widgets.py`、`tests/test_tui_smoke.py`  
**依赖：** T7、T8  
**步骤：**
1. 在 `StatusBar.refresh_status()` 中读取 `TokenUsage.cache`。
2. 生成简短缓存展示文本：hit、write、miss、unknown、unsupported。
3. 有 token 总量时继续展示总量，没有总量时保留输入输出 token 展示。
4. 添加 smoke 测试，构造带缓存命中和未知状态的 `TokenUsage`，断言状态栏文本包含缓存状态。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_status_bar_renders_agent_progress_and_usage tests/test_tui_smoke.py::test_status_bar_renders_cache_usage -q`，期望全部通过。

## T11: 更新端到端 mock usage

**文件：** `tests/e2e_mock_openai_server.py`  
**依赖：** T7、T10  
**步骤：**
1. 在 mock OpenAI server 的 usage 事件中加入 `prompt_tokens_details.cached_tokens`。
2. 保持现有工具调用、普通回复和错误场景逻辑不变。
3. 用现有端到端相关测试确认 mock 输出仍兼容。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_submit_streams_text_into_message_view tests/test_tui_smoke.py::test_status_bar_renders_cache_usage -q`，期望全部通过。

## T12: 编写人工对比场景文档

**文件：** `specs/structured-system-prompt/manual-scenarios.md`  
**依赖：** T10、T11  
**步骤：**
1. 写入工具选择场景：要求模型查找文件并总结，观察是否优先使用查找和读取工具。
2. 写入编辑前读取场景：要求修改一个已存在文件，观察是否先读取或搜索再编辑。
3. 写入 Plan Mode 场景：输入 `/plan <需求>`，观察是否只使用读类工具。
4. 写入动态环境注入场景：观察请求 payload 或 mock 记录中是否有运行时标签和 cwd。
5. 写入缓存观测场景：连续两轮相似请求，观察状态栏或 usage 事件中的 cache 状态。
6. 每个场景包含操作、预期可观察结果和通过标准。

**验证：** 运行 `rg -n "工具选择|编辑前读取|Plan Mode|动态环境|缓存观测|通过标准" specs/structured-system-prompt/manual-scenarios.md`，期望每类场景都能命中。

## T13: 更新 README

**文件：** `README.md`  
**依赖：** T12  
**步骤：**
1. 增加结构化系统提示说明，解释稳定提示和运行时补充的区别。
2. 更新 Plan Mode 说明，明确控制指令通过系统级补充注入，用户消息保留原始需求。
3. 增加缓存观测说明，说明状态栏可能显示 hit、write、miss、unknown 或 unsupported。
4. 保持“不做的范围”与 spec 一致，不宣称项目指令加载、记忆或 MCP 已实现。

**验证：** 运行 `rg -n "结构化系统提示|运行时补充|缓存|unsupported|项目指令|MCP" README.md`，期望能看到新增能力和未实现边界。

## T14: 运行 Provider 与提示集成回归

**文件：** `tests/test_prompting.py`、`tests/test_session.py`、`tests/test_commands.py`、`tests/test_agent.py`、`tests/test_openai_provider.py`、`tests/test_anthropic_provider.py`  
**依赖：** T13  
**步骤：**
1. 运行提示构造、会话、命令、Agent 和两类 Provider 测试。
2. 如果失败，根据失败点回到对应任务修复。
3. 确认 OpenAI 和 Anthropic 的无 prompt 旧路径仍有测试覆盖。

**验证：** 运行 `python -m pytest tests/test_prompting.py tests/test_session.py tests/test_commands.py tests/test_agent.py tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望全部通过。

## T15: 运行全量自动化测试

**文件：** `tests/`、`README.md`、`specs/structured-system-prompt/manual-scenarios.md`  
**依赖：** T14  
**步骤：**
1. 运行全量 pytest。
2. 如果有失败，定位到相关任务修复并重新运行。
3. 确认 README 和人工场景文档能被 grep 检查到关键内容。

**验证：** 运行 `python -m pytest -q`，期望全部通过。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15
```
