# MewCode Skill 系统 Plan

## 架构概览
Skill 系统新增 `mewcode.skills` 子系统，作为本地能力包的发现、解析、激活和执行中心。它不替代现有工具、命令、提示和 Agent Loop，而是在这些模块之间提供一层明确的运行时状态：启动和热更新阶段生成 Skill 目录；模型请求阶段注入 Skill 摘要和已激活完整 SOP；工具策略阶段按 Skill 白名单收窄工具；命令阶段把 Skill 注册成斜杠短命令；执行阶段按共享或独立模式运行。

Skill 发现分三层：项目级 `.mewcode/skills/`、用户级 `~/.mewcode/skills/`、内置级包资源。解析器同时支持单文件 Skill 和目录型 Skill。单文件 Skill 是一个 Markdown 文件；目录型 Skill 以 `skill.md` 作为入口，并可包含 `tools/` 下的专属工具描述和实现脚本。解析失败只生成警告并跳过该 Skill；同名 Skill 按项目级、用户级、内置级覆盖。

两阶段加载由一个系统级内置工具 `load_skill` 完成。启动时 PromptBuilder 只注入 Skill 名字和一句说明；模型调用 `load_skill` 或用户输入 Skill 斜杠命令时，`SkillManager` 激活对应 Skill、渲染 SOP 参数、注册专属工具，并把完整 SOP 放入后续每轮运行时上下文。`load_skill` 是系统工具，始终对模型可见，不受 Skill 白名单和 Plan Mode 工具收窄影响。

执行模式由 `SkillExecutor` 统一处理。共享模式在当前 `ChatSession` 中继续执行，所有用户消息、助手消息、工具调用和工具结果进入主历史；独立模式创建临时 `ChatSession`，按配置携带最近 N 条主历史，执行完成后只把摘要回流到主对话，中间消息不写入主历史。指定模型通过同一供应商配置创建临时 Provider；共享模式下多个激活 Skill 的模型要求必须能解析为同一个模型，否则本轮请求失败并显示中文错误。

热更新不引入额外文件监听依赖。`SkillManager.refresh_if_changed()` 在 TUI 启动、每次用户提交前、每次 Agent 请求前执行轻量指纹检查；发现 Skill 文件变化后重建目录、更新斜杠命令、更新激活 Skill 的渲染指令和专属工具注册。删除已激活 Skill 会将其从激活列表移除并生成警告。白名单校验在 MCP 工具完成注册后执行，避免引用 MCP 工具的 Skill 被误判。

## 核心数据结构

### SkillSourceScope
```python
SkillSourceScope = Literal["project", "user", "builtin"]
```
表示 Skill 来源层级。覆盖优先级为 `project > user > builtin`。

### SkillExecutionMode
```python
SkillExecutionMode = Literal["shared", "isolated"]
```
表示 Skill 执行模式。`shared` 使用主对话；`isolated` 使用临时对话并回流摘要。

### SkillFrontmatter
```python
@dataclass(frozen=True)
class SkillFrontmatter:
    name: str
    description: str
    tools: tuple[str, ...]
    mode: SkillExecutionMode
    history: int
    model: str | None = None
```
对应 Markdown frontmatter。字段名固定为 `name`、`description`、`tools`、`mode`、`history`、`model`。`history` 是独立模式携带的最近主历史消息条数，必须大于等于 0；共享模式保留该字段但不使用。

### SkillRoots
```python
@dataclass(frozen=True)
class SkillRoots:
    project: Path
    user: Path
    builtin: Traversable
```
表示三层 Skill 根目录。`builtin` 使用 `importlib.resources.files()` 返回的包资源对象。

### SkillSummary
```python
@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    source_scope: SkillSourceScope
```
表示启动和运行时摘要中可暴露给模型的轻量信息，不包含完整正文。

### SkillWarning
```python
@dataclass(frozen=True)
class SkillWarning:
    message: str
    source_path: Path | str
```
表示可恢复问题，例如单个 Skill 解析失败或热更新时删除了已激活 Skill。

### SkillError
```python
@dataclass(frozen=True)
class SkillError:
    message: str
    source_path: Path | str
```
表示必须阻止继续运行的问题，例如白名单引用不存在工具或命令冲突。

### SkillFingerprint
```python
@dataclass(frozen=True)
class SkillFingerprint:
    entries: tuple[tuple[str, int, int], ...]
```
表示热更新指纹。每项包含路径、`mtime_ns` 和文件大小。

### SkillDefinition
```python
@dataclass(frozen=True)
class SkillDefinition:
    frontmatter: SkillFrontmatter
    body: str
    source_scope: SkillSourceScope
    source_path: Path
    package_dir: Path
    directory_skill: bool
    tool_definitions: tuple[SkillToolDefinition, ...] = ()
```
表示解析后的完整 Skill。`body` 是原始 SOP Markdown 正文；`package_dir` 是相对资源路径和专属工具脚本的根目录；`tool_definitions` 只在目录型 Skill 中存在。

### SkillToolDefinition
```python
@dataclass(frozen=True)
class SkillToolDefinition:
    local_name: str
    global_name: str
    description: str
    parameters_schema: dict[str, Any]
    script_path: Path
    safety: ToolSafety
    timeout_seconds: float
```
表示目录型 Skill 的专属工具。工具描述文件放在 `tools/<local_name>.yaml`，实现脚本放在同目录声明的 `script` 路径中。模型可见工具名使用 `skill_<skill_name>__<local_name>`，避免覆盖内置工具或 MCP 工具。

### SkillCatalog
```python
@dataclass(frozen=True)
class SkillCatalog:
    definitions: dict[str, SkillDefinition]
    warnings: tuple[SkillWarning, ...]
    fingerprint: SkillFingerprint
```
表示一次发现结果。`definitions` 已完成优先级覆盖，key 为大小写折叠后的 Skill 名。`warnings` 包含解析失败、被覆盖、已激活 Skill 被删除等非致命问题。

### SkillActivation
```python
@dataclass(frozen=True)
class SkillActivation:
    name: str
    arguments: str
    rendered_body: str
    mode: SkillExecutionMode
    tool_whitelist: frozenset[str]
    model: str | None
    source_path: Path
```
表示一个已激活 Skill。`rendered_body` 是把正文占位符替换为本次参数后的 SOP。占位符第一版支持 `{{input}}` 和 `{{args}}`，两者等价，替换为用户传入的原始参数文本。

### SkillPromptContext
```python
@dataclass(frozen=True)
class SkillPromptContext:
    available: tuple[SkillSummary, ...]
    active: tuple[SkillActivation, ...]
    warnings: tuple[str, ...] = ()
```
传给 PromptBuilder 的 Skill 上下文。`available` 只包含名字和一句说明；`active` 包含完整渲染 SOP。

### SkillRefreshReport
```python
@dataclass(frozen=True)
class SkillRefreshReport:
    changed: bool
    warnings: tuple[SkillWarning, ...]
    errors: tuple[SkillError, ...]
```
表示热更新结果。`errors` 包括白名单引用不存在工具、斜杠命令冲突、专属工具全局名冲突等必须阻止继续运行的问题。

### SkillExecutionSummary
```python
@dataclass(frozen=True)
class SkillExecutionSummary:
    skill_name: str
    input_goal: str
    result_text: str
    tool_statuses: tuple[str, ...]
    stop_reason: str
```
表示独立模式回流到主会话的确定性摘要来源。

### ToolSpec
```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    timeout_seconds: float = 10.0
    safety: ToolSafety = "side_effect"
    visibility: ToolVisibility = "normal"
```
在现有结构上增加 `visibility`。`ToolVisibility = Literal["normal", "system"]`。`load_skill` 使用 `visibility="system"`，因此始终暴露、跳过 Skill 白名单和 Plan Mode 收窄。

### AgentCommand
```python
@dataclass(frozen=True)
class AgentCommand:
    mode: AgentMode
    visible_text: str
    model_text: str
    model_override: str | None = None
    skill_name: str | None = None
```
在现有命令上增加可选模型和 Skill 来源。普通输入不设置；共享 Skill 命令可设置 `skill_name` 和 `model_override`。

## 核心接口

### SkillLoader
```python
class SkillLoader:
    def __init__(self, roots: SkillRoots) -> None:
        pass

    def discover(self) -> SkillCatalog:
        pass
```
负责扫描三层目录、解析 frontmatter、解析目录型专属工具、应用覆盖优先级并返回目录。

### SkillManager
```python
class SkillManager:
    def refresh_if_changed(
        self,
        tool_registry: ToolRegistry,
        command_registry: CommandRegistry | None = None,
    ) -> SkillRefreshReport:
        pass

    def load(self, name: str, arguments: str) -> SkillActivation:
        pass

    def clear_active(self) -> None:
        pass

    def prompt_context(self) -> SkillPromptContext:
        pass

    def active_tool_whitelist(self) -> frozenset[str] | None:
        pass

    def resolve_model_override(self, requested: str | None = None) -> str | None:
        pass

    def active_dedicated_tools(self) -> tuple[SkillScriptTool, ...]:
        pass
```
运行期核心对象。它保存当前目录、激活列表、警告和动态专属工具。`active_tool_whitelist()` 返回所有激活 Skill 白名单的并集；没有激活 Skill 时返回 `None`，表示不收窄普通工具。`command_registry` 为 `None` 时只刷新目录和工具状态，不重建斜杠命令。

### SkillExecutor
```python
class SkillExecutor:
    async def invoke_from_command(self, name: str, arguments: str, context: SkillCommandContext) -> None:
        pass

    async def run_isolated(self, activation: SkillActivation, main_session: ChatSession) -> SkillExecutionSummary:
        pass
```
负责按 Skill 执行模式调度。共享模式调用 `context.send_prompt()` 进入主 Agent Loop；独立模式创建临时会话和临时 Runner，执行后把摘要展示并追加到主会话。

### ProviderResolver
```python
class ProviderResolver(Protocol):
    def provider_for(self, model: str | None) -> LLMProvider:
        pass
```
按模型覆盖创建 Provider。`model is None` 返回当前默认 Provider；非空时使用同一 `AppConfig` 的协议、base_url、api_key 和其他参数，只替换 `model` 字段。

### SkillCommandContext
```python
class SkillCommandContext(CommandContext, Protocol):
    async def invoke_skill(self, *, name: str, arguments: str, visible_text: str) -> None:
        pass

    def clear_active_skills(self) -> None:
        pass

    def skill_snapshot(self) -> SkillPromptContext:
        pass
```
TUI 实现该协议。Skill 斜杠命令 handler 只调用 `invoke_skill`，不直接依赖 Textual 或 Agent Runner。

### LoadSkillTool
```python
class LoadSkillTool:
    spec: ToolSpec

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        pass
```
系统级工具。参数为 `name` 和可选 `arguments`。执行后调用 `SkillManager.load()`，返回 Skill 名、执行模式、渲染 SOP、工具白名单、专属工具名和模型要求。实际专属工具注册由 `SkillManager` 完成。

### SkillScriptTool
```python
class SkillScriptTool:
    spec: ToolSpec

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        pass
```
目录型 Skill 专属工具包装器。它以 JSON stdin 调用本地 Python 脚本，要求 stdout 输出 JSON 对象；超时、非零退出码、非法 JSON 都转成结构化工具失败。脚本路径必须解析在 Skill 包目录内。

## 模块设计

### `mewcode.skills.models`
**职责：** 定义 Skill frontmatter、来源、目录、激活状态、专属工具、刷新报告和提示上下文。  
**对外接口：** `SkillDefinition`、`SkillActivation`、`SkillPromptContext`、`SkillRefreshReport` 等 dataclass。  
**依赖：** 标准库、`Path`、`ToolSafety`。

### `mewcode.skills.loader`
**职责：** 扫描三层 Skill 根目录，解析单文件和目录型 Skill。  
**对外接口：** `SkillLoader.discover()`。  
**依赖：** `yaml.safe_load`、`importlib.resources`、`mewcode.skills.models`。

解析规则：
- 单文件 Skill：根目录下 `*.md`。
- 目录型 Skill：子目录中存在 `skill.md`。
- frontmatter 必须位于文件开头，以 `---` 分隔。
- 正文去除首尾空白后不能为空。
- Skill 名必须匹配 `[A-Za-z][A-Za-z0-9_-]*`，以便同时作为斜杠命令名和专属工具前缀。
- `tools` 必须是字符串数组，可以为空。
- `mode` 只能是 `shared` 或 `isolated`。
- `history` 必须是非负整数。
- `model` 为空或缺失时表示使用默认模型。

目录型专属工具描述文件格式：
```yaml
name: inspect_context
description: 读取 Skill 包内的辅助上下文
safety: read_only
timeout_seconds: 10
script: tools/inspect_context.py
parameters:
  type: object
  properties:
    query:
      type: string
  required: [query]
  additionalProperties: false
```
`name` 必须匹配 `[A-Za-z][A-Za-z0-9_]*`；`parameters` 直接作为 JSON Schema 传给模型和现有参数校验器。

### `mewcode.skills.manager`
**职责：** 保存当前目录和激活状态，做热更新、白名单校验、动态命令注册、专属工具注册和模型冲突校验。  
**对外接口：** `SkillManager`。  
**依赖：** `SkillLoader`、`CommandRegistry`、`ToolRegistry`、`SkillScriptTool`。

白名单校验规则：
- 候选工具名来自当前 `ToolRegistry` 中的普通工具、MCP 工具、系统工具，以及每个 Skill 自己的专属工具全局名。
- `load_skill` 是系统工具，可以不出现在 Skill 白名单中。
- 任一可用 Skill 的白名单引用不存在工具时，生成 fatal `SkillError`。
- 空白名单合法，表示激活后不暴露普通工具，只保留系统工具。

### `mewcode.skills.tools`
**职责：** 提供 `load_skill` 系统工具和目录型 Skill 专属脚本工具。  
**对外接口：** `LoadSkillTool`、`SkillScriptTool`。  
**依赖：** `ToolSpec`、`ToolExecutionError`、`SkillManager`、`asyncio.create_subprocess_exec`。

`load_skill` 的工具描述明确告诉模型：启动时只看到摘要，需要某个 Skill 时先调用该工具；调用后后续上下文会持续包含完整 SOP 和专属工具。`load_skill` 使用 `safety="side_effect"` 和 `visibility="system"`，由调度器串行执行并跳过用户权限确认。`SkillScriptTool` 默认按描述文件的 `safety` 参与权限和 Plan Mode 策略，系统不额外提供 OS 级沙箱。

### `mewcode.skills.commands`
**职责：** 把可用 Skill 注册成斜杠短命令。  
**对外接口：**
```python
def register_skill_commands(registry: CommandRegistry, manager: SkillManager) -> None:
    pass
```
**依赖：** `CommandDefinition`、`SkillManager`。

命令名为 `/{skill.name}`，别名第一版不从 Skill 定义读取。命令类型显示为 `prompt`；说明来自 `description`；参数提示为 `用户输入，会替换 {{input}} / {{args}}`。内置 `/review` 从静态内置命令迁移到内置 Skill，避免两套行为冲突。

### `mewcode.skills.execution`
**职责：** 处理共享模式和独立模式执行。  
**对外接口：** `SkillExecutor`、`SkillExecutionSummary`。  
**依赖：** `AgentLoopRunner`、`ChatSession`、`ProviderResolver`、`ContextManager`、`ToolExecutor`。

共享模式流程：
1. `SkillManager.load()` 激活 Skill 并注册专属工具。
2. 构造 `AgentCommand(mode=current_mode, visible_text=原命令, model_text=执行 Skill 的目标说明, skill_name=name, model_override=model)`。
3. 调用主对话 `send_prompt()`，后续 PromptBuilder 注入完整 SOP。

独立模式流程：
1. `SkillManager.load()` 激活 Skill 并注册专属工具。
2. 创建临时 `ChatSession`，按 `history` 复制主会话最近 N 条消息。
3. 在临时会话中追加用户目标，使用同一个工具注册表、权限控制和上下文配置运行 Agent Loop。
4. 临时会话的中间消息不写入主会话。
5. 根据临时会话最终助手消息、工具结果和停止原因生成中文摘要。
6. 摘要展示给用户，并作为 assistant 消息追加到主会话。

### `mewcode.tools.registry`
**职责：** 支持动态工具注册和注销。  
**对外接口新增：**
```python
def register(self, tool: Tool, *, origin: str = "static") -> None:
    pass

def unregister_origin(self, origin: str) -> None:
    pass

def names(self) -> frozenset[str]:
    pass
```
**依赖：** 现有 Tool 协议。`origin` 用于热更新时移除旧的 Skill 专属工具。

### `mewcode.tools.scheduler`
**职责：** 在现有 Plan Mode 策略上叠加 Skill 白名单和系统工具例外。  
**对外接口调整：**
```python
@dataclass(frozen=True)
class ToolPolicy:
    mode: AgentMode
    skill_tools: frozenset[str] | None = None
```
**依赖：** `ToolSpec.visibility`。

策略顺序：
1. 系统工具始终可见且可调用。
2. Plan Mode 先过滤为只读工具。
3. 有激活 Skill 白名单时，再取白名单交集。
4. 权限系统继续在调度阶段生效。

调度时 `visibility="system"` 的工具按有副作用工具串行处理，但不进入权限确认流程；普通 Skill 专属工具仍按 `safety` 触发权限规则。

### `mewcode.prompting.base`
**职责：** 让运行时提示接收 Skill 上下文。  
**对外接口调整：**
```python
@dataclass(frozen=True)
class RuntimePromptContext:
    cwd: Path
    mode: AgentMode
    iteration: int
    max_iterations: int
    allowed_tools: Sequence[ToolSpec]
    skill_context: SkillPromptContext | None = None
```
**依赖：** `mewcode.skills.models` 仅在类型检查中导入，避免循环依赖。

### `mewcode.prompting.builder`
**职责：** 注入可用 Skill 摘要和已激活完整 SOP。  
**对外接口：** `PromptBuilder.build_runtime_prompt(context)` 保持不变。  
**依赖：** `RuntimePromptContext.skill_context`。

生成顺序放在 `<mewcode_runtime_context>` 内、项目指令和记忆之前：
```text
可用 Skill：
- commit：生成提交说明并辅助提交前检查
- review：以代码审查视角检查变更

已激活 Skill：
<skill name="review" mode="shared" source="/repo/.mewcode/skills/review.md">
参数：src/mewcode
可见工具：read_file, search_code
完整 SOP：
以代码审查视角检查指定范围，优先指出 bug、行为回归风险和缺失测试。
</skill>
```

### `mewcode.commands`
**职责：** 支持动态 Skill 命令，并让 `/clear` 清理激活 Skill。  
**对外接口调整：** `CommandContext` 增加 `invoke_skill`、`clear_active_skills`、`skill_snapshot`。  
**依赖：** `mewcode.skills` 只通过协议和 handler 闭包接入。

`create_builtin_command_registry()` 保留非 Skill 内置命令；`review` 由内置 Skill 提供。`/help` 和 Tab 补全继续从同一个 `CommandRegistry` 读取，因此 Skill 命令天然参与帮助和补全。

### `mewcode.agent`
**职责：** 每轮请求前刷新 Skill、计算工具策略、注入 Skill prompt、解析模型覆盖，并在 `load_skill` 触发独立模式时调度独立执行。  
**对外接口调整：** `AgentLoopRunner` 构造函数增加 `skill_manager` 和 `provider_resolver` 可选参数。  
**依赖：** `SkillManager`、`SkillExecutor`。

关键行为：
- `allowed_tools = ToolPolicy(command.mode, skill_manager.active_tool_whitelist()).allowed_specs(registry)`。
- `RuntimePromptContext(skill_context=skill_manager.prompt_context())`。
- 每轮选择 Provider 时调用 `skill_manager.resolve_model_override(command.model_override)`。
- 如果 `load_skill` 激活的是独立模式 Skill，本轮工具结果后调用 `SkillExecutor.run_isolated()`，把摘要追加到主对话并结束主轮次。

### `mewcode.tui.app`
**职责：** 创建并持有 SkillManager、SkillExecutor、ProviderResolver，实现 SkillCommandContext，展示启动和热更新警告。  
**对外接口：** `MewCodeApp` 构造函数新增 `skill_manager`。  
**依赖：** `mewcode.skills`、现有 TUI widgets。

启动顺序：
1. CLI 创建默认工具注册表。
2. CLI 创建 SkillManager 并注册 `load_skill` 系统工具。
3. TUI mount 时初始化 MCP 并注册 MCP 工具。
4. TUI 调用 `skill_manager.refresh_if_changed()`，完成白名单校验、专属工具预校验和 Skill 命令注册。
5. 如果有 fatal 错误，显示错误并禁用输入；否则显示警告并聚焦输入框。

### `mewcode.providers.factory`
**职责：** 支持模型覆盖创建 Provider。  
**对外接口新增：**
```python
def create_provider(config: AppConfig, *, model_override: str | None = None) -> LLMProvider:
    pass
```
**依赖：** `dataclasses.replace`。非空 `model_override` 只替换 `AppConfig.model`。

### 内置 Skill 包资源
**职责：** 提供 commit、review、test 三个样板。  
**文件：** `src/mewcode/skills/builtin/commit.md`、`review.md`、`test.md`。  
**依赖：** `pyproject.toml` package data 配置。

三个样板都使用中文 SOP：
- `commit`：检查变更、运行必要检查、生成提交说明；默认共享模式，工具白名单包含读写搜索和命令。
- `review`：代码审查优先指出 bug、回归风险和缺失测试；默认共享模式，工具白名单以读取、搜索和必要命令为主。
- `test`：根据用户范围运行测试并分析失败；默认共享模式，工具白名单包含读取、搜索和命令。

## 模块交互

### 启动与热更新
1. CLI 加载配置、创建 Provider、默认工具注册表、SkillManager 和 `load_skill`。
2. TUI mount 初始化 MCP，并把 MCP 工具注册进同一个 ToolRegistry。
3. TUI 调用 SkillManager 刷新目录。
4. SkillLoader 扫描项目、用户、内置三层 Skill，解析合法 Skill，记录非法 Skill 警告。
5. SkillManager 校验白名单和专属工具名，向 CommandRegistry 注册 Skill 斜杠命令。
6. PromptBuilder 后续请求只能看到 Skill 摘要，直到某个 Skill 被加载。
7. 每次用户提交和每轮 Agent 请求前重复轻量刷新；无变化时直接返回。

### 模型通过工具加载共享 Skill
1. 模型在普通对话中看到 Skill 摘要和 `load_skill`。
2. 模型调用 `load_skill(name="review", arguments="src/mewcode")`。
3. ToolExecutor 执行 LoadSkillTool，SkillManager 激活 `review`，渲染 SOP，注册专属工具。
4. ToolPolicy 下一轮按激活 Skill 白名单收窄工具，并保留 `load_skill`。
5. PromptBuilder 在 `<mewcode_runtime_context>` 内注入 `review` 完整 SOP。
6. Agent Loop 继续当前主会话，工具调用和最终回复都进入主历史。

### 用户通过斜杠命令触发共享 Skill
1. 用户输入 `/review src/mewcode`。
2. CommandDispatcher 命中动态 Skill 命令。
3. TUI 的 `invoke_skill()` 调用 SkillExecutor。
4. SkillExecutor 激活 Skill 并调用主对话 `send_prompt()`。
5. 主 Agent Loop 的后续请求带完整 SOP、参数和收窄后的工具。

### 独立模式执行
1. 用户或模型加载 `mode="isolated"` 的 Skill。
2. SkillExecutor 创建临时 ChatSession，并复制最近 `history` 条主历史。
3. 临时 Agent Loop 使用相同工具注册表、权限控制、上下文配置和模型覆盖执行。
4. 临时执行产生的用户、助手、工具消息保留在临时会话内。
5. SkillExecutor 生成摘要并追加到主会话。
6. 主会话后续请求仍能看到该 Skill 已激活完整 SOP，直到 `/clear` 或热更新删除该 Skill。

### 清空对话
1. 用户输入 `/clear`。
2. 现有界面消息区清空。
3. Command handler 调用 `context.clear_active_skills()`。
4. SkillManager 移除所有激活 Skill 并注销对应专属工具。
5. 后续 PromptBuilder 只注入可用 Skill 摘要，不再注入完整 SOP。

## 文件组织
```text
mewcode/
├── specs/skill-system/
│   ├── spec.md                         — 已批准需求
│   ├── plan.md                         — 本技术设计
│   ├── task.md                         — 后续任务拆解
│   └── checklist.md                    — 后续验收清单
├── src/mewcode/
│   ├── skills/
│   │   ├── __init__.py                 — Skill 子系统公共导出
│   │   ├── models.py                   — Skill 数据结构、报告和上下文
│   │   ├── loader.py                   — frontmatter 解析、目录发现和优先级覆盖
│   │   ├── manager.py                  — 热更新、激活状态、白名单校验、动态注册
│   │   ├── tools.py                    — load_skill 系统工具和 SkillScriptTool
│   │   ├── commands.py                 — Skill 斜杠命令注册
│   │   ├── execution.py                — 共享/独立执行调度和摘要回流
│   │   └── builtin/
│   │       ├── __init__.py             — 内置 Skill 包资源
│   │       ├── commit.md               — 内置 commit Skill
│   │       ├── review.md               — 内置 review Skill
│   │       └── test.md                 — 内置 test Skill
│   ├── tools/
│   │   ├── base.py                     — ToolSpec 增加 visibility
│   │   ├── registry.py                 — 动态 origin 注册和注销
│   │   └── scheduler.py                — Skill 白名单和系统工具例外
│   ├── prompting/
│   │   ├── base.py                     — RuntimePromptContext 增加 skill_context
│   │   └── builder.py                  — 注入 Skill 摘要和已激活完整 SOP
│   ├── commands/
│   │   ├── models.py                   — CommandContext 增加 Skill 方法
│   │   ├── builtin.py                  — /clear 清理 Skill，/review 迁移为 Skill
│   │   └── registry.py                 — 支持动态命令重建或 origin 注销
│   ├── agent.py                        — 传递 Skill 上下文、模型覆盖、独立执行调度
│   ├── cli.py                          — 创建 SkillManager 和 load_skill 工具
│   ├── providers/factory.py            — 支持 model_override
│   └── tui/app.py                      — 持有 SkillManager，刷新、警告、命令调用
├── tests/
│   ├── test_skills_loader.py           — frontmatter、优先级、解析警告、目录型 Skill
│   ├── test_skills_manager.py          — 激活、热更新、白名单校验、命令/工具动态注册
│   ├── test_skills_tools.py            — load_skill 和专属脚本工具
│   ├── test_skills_execution.py        — 共享/独立执行、历史携带、摘要回流、模型覆盖
│   ├── test_prompting.py               — Skill 摘要和完整 SOP 注入
│   ├── test_tool_scheduler.py          — Skill 白名单、Plan Mode 和系统工具例外
│   ├── test_commands.py                — Skill 命令帮助、补全、冲突和 /clear 清理
│   ├── test_agent.py                   — Agent Loop 加载 Skill 后继续执行
│   └── test_tui_smoke.py               — 启动警告和 Skill 命令基础交互
├── README.md                           — Skill 格式、目录、加载、执行模式、内置样板说明
└── pyproject.toml                      — 包含内置 Skill Markdown package data
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Skill 根目录 | 项目 `.mewcode/skills/`、用户 `~/.mewcode/skills/`、内置包资源 | 与现有项目/用户配置和本地自动产物目录保持一致，且不引入新配置项 |
| frontmatter 字段名 | `name`、`description`、`tools`、`mode`、`history`、`model` | 字段短且直观，能完整覆盖需求 |
| 占位符 | 第一版只支持 `{{input}}` 和 `{{args}}` | 满足参数替换需求，避免提前设计复杂模板语言 |
| 热更新 | 每次提交和每轮请求前做文件指纹检查 | 不增加 watchdog 依赖，行为确定且易测试 |
| 系统级加载工具 | `load_skill` 使用 `ToolSpec.visibility="system"` | 保持模型可调用工具形态，同时明确绕过 Skill 白名单和 Plan Mode 收窄 |
| 专属工具命名 | `skill_<skill_name>__<local_name>` | 避免覆盖内置工具、MCP 工具和其他 Skill 工具 |
| 专属工具执行 | Python 脚本 JSON stdin/stdout 协议 | 易测试、跨平台、与现有 Python 项目一致；权限仍由 ToolSpec safety 控制 |
| 白名单组合 | 多个激活 Skill 白名单取并集，再与当前基础工具集合取交集 | 满足多个 Skill 同时激活，避免某个 Skill 意外扩大工具 |
| 模型覆盖 | 使用同一 AppConfig 克隆 Provider，只替换 model | 不引入多供应商配置矩阵，符合本阶段 YAGNI |
| 独立执行摘要 | 根据临时会话最终消息和工具状态确定性生成 | 避免额外模型请求，确保主历史只收到摘要 |
| `/review` 迁移 | 从静态内置命令迁移为内置 Skill | 避免命令冲突，同时用同一机制覆盖 commit/review/test 样板 |
| 清空对话 | `/clear` 同时清 UI 消息和激活 Skill，但不删会话历史 | 保持既有 `/clear` 不删除历史的语义，同时满足 Skill 清理需求 |

## 需求覆盖

| 需求 | 架构归属 |
|------|----------|
| F1-F4 | `SkillLoader`、`SkillDefinition`、`SkillToolDefinition` |
| F5-F7 | `SkillLoader` 覆盖策略、`SkillManager` 冲突处理 |
| F8-F12 | `SkillPromptContext`、`PromptBuilder`、`load_skill` |
| F13-F15 | `SkillExecutor` 共享/独立执行 |
| F16 | `ProviderResolver`、`SkillManager.resolve_model_override()` |
| F17-F20 | `ToolPolicy`、`ToolRegistry`、`SkillScriptTool` |
| F21-F24 | `mewcode.skills.commands`、`CommandRegistry` 动态注册、热更新 |
| F25 | `/clear` handler、`SkillManager.clear_active()` |
| F26 | `src/mewcode/skills/builtin/*.md` |
