# JulyCode 权限系统 Plan

## 架构概览
权限系统新增 `julycode.permissions` 包，作为工具调度与工具执行之间的统一决策层。所有内置工具调用先经过现有 `ToolPolicy` 处理未知工具和 Plan Mode 限制，再进入权限决策；权限允许后才调用 `ToolExecutor.execute()`。这样 Plan Mode 的只读约束仍是上游硬约束，权限规则无法把规划阶段升级成可写可执行。

权限决策分为五层：命令黑名单、路径沙箱、规则引擎、权限模式和人在回路。黑名单和沙箱由 `PermissionEngine` 在规则前执行，命中后直接返回拒绝。规则引擎加载用户级、项目级、本地级和会话级规则，按会话、本地、项目、用户的顺序查找。权限模式只处理未被硬拒绝、沙箱拒绝或显式 deny 拒绝的调用，并在严格模式下把有副作用工具转为用户确认。

人在回路通过异步 `PermissionPrompter` 接口接入。`ToolCallScheduler` 在需要确认时发出权限等待事件，调用 prompter 等待用户选择，再根据本次、本会话、永久允许或拒绝生成最终权限结果。本会话允许写入内存规则集，永久允许写入本地级规则文件。

TUI 新增权限确认视图，展示工具名、关键参数、触发原因和四个选择：本次允许、本会话允许、永久允许、拒绝。确认期间当前 Agent Loop 暂停该工具调用，但 Textual 事件循环仍可响应用户点击；用户决策后调度器继续执行或返回权限失败结果。

配置层新增权限模式解析。模型与供应商协议层不感知权限系统；OpenAI 和 Anthropic 仍只看到工具成功或失败结果。

## 核心数据结构

### PermissionMode
```python
PermissionMode = Literal["strict", "default", "permissive"]
```

- `strict`: 严格模式。有副作用工具即使命中 allow 规则也需要用户确认；读类工具按规则和默认读类策略执行。
- `default`: 默认模式。明确 allow 自动执行，明确 deny 自动拒绝；未命中的读类工具自动允许，未命中的有副作用工具需要确认。
- `permissive`: 放行模式。未命中的工具自动允许，但高危命令、路径沙箱和显式 deny 仍然生效。

### PermissionConfig
```python
@dataclass(frozen=True)
class PermissionConfig:
    mode: PermissionMode = "default"
```

挂在 `AppConfig.permissions` 下，从现有配置文件的 `permissions.mode` 读取。缺省为 `default`。

### PermissionEffect
```python
PermissionEffect = Literal["allow", "deny"]
```

规则结果，只允许 `allow` 或 `deny`。

### PermissionRuleSource
```python
PermissionRuleSource = Literal["session", "local", "project", "user"]
```

规则来源优先级为：

```text
session > local > project > user
```

### MatchKind
```python
MatchKind = Literal["exact", "glob"]
```

解析规则模式时自动判定：包含 `*`、`?` 或 `[]` 的模式按 glob 处理，否则按精确匹配处理。

### PermissionRule
```python
@dataclass(frozen=True)
class PermissionRule:
    source: PermissionRuleSource
    tool_name: str
    pattern: str
    effect: PermissionEffect
    match_kind: MatchKind
    raw_key: str
```

表示一条 `工具名(模式): allow|deny` 规则。`tool_name` 在解析时会经过别名归一化，`Bash(...)` 等价于 `run_command(...)`，以兼容用户给出的命令工具写法。

### RuleMatch
```python
@dataclass(frozen=True)
class RuleMatch:
    rule: PermissionRule
    target: str
```

表示某条规则命中了某个工具调用目标。规则冲突时先按来源优先级，再按 `exact > glob`，最后按 `deny > allow` 排序。

### PermissionSubject
```python
@dataclass(frozen=True)
class PermissionSubject:
    tool_name: str
    targets: tuple[str, ...]
    summary: str
```

从 `ToolCall` 中提取出来的匹配对象：

- `run_command`: targets 为规范化后的命令字符串。
- `read_file`、`write_file`、`edit_file`: targets 为解析后仍位于项目内的相对路径。
- `find_files`: targets 为 glob 模式字符串；权限沙箱会在执行前预检查匹配结果是否仍位于项目内。
- `search_code`: targets 为搜索起点相对路径和可选 glob 组合摘要。

### PermissionDecision
```python
PermissionDecisionKind = Literal["allow", "deny", "prompt"]

@dataclass(frozen=True)
class PermissionDecision:
    kind: PermissionDecisionKind
    reason: str
    error_type: str | None = None
    matched_rule: PermissionRule | None = None
    prompt: PermissionPrompt | None = None
```

权限引擎的统一输出。`deny` 可直接转为失败 `ToolResult`；`prompt` 交给人在回路处理；`allow` 进入真实工具执行。

### PermissionPrompt
```python
@dataclass(frozen=True)
class PermissionPrompt:
    call: ToolCall
    tool_name: str
    title: str
    summary: str
    reason: str
    suggested_rule_key: str
```

展示给用户的确认请求。`suggested_rule_key` 用于用户选择本会话或永久允许时生成规则，例如 `run_command(git status)` 或 `write_file(src/example.py)`。

### UserPermissionChoice
```python
UserPermissionChoice = Literal["allow_once", "allow_session", "allow_permanent", "deny"]
```

TUI 返回给权限系统的用户决策。

### PermissionPromptResult
```python
@dataclass(frozen=True)
class PermissionPromptResult:
    choice: UserPermissionChoice
    rule: PermissionRule | None = None
```

用户确认后的结果。本会话和永久允许会携带生成的 allow 规则。

### PermissionEventPayload
```python
@dataclass(frozen=True)
class PermissionEventPayload:
    prompt: PermissionPrompt
    decision: PermissionDecision | None = None
    choice: UserPermissionChoice | None = None
```

挂到 `TurnEvent.permission` 上，用于 TUI 和测试观察权限等待、允许或拒绝状态。

### PermissionPrompter
```python
class PermissionPrompter(Protocol):
    async def request_permission(self, prompt: PermissionPrompt) -> UserPermissionChoice:
        ...
```

人在回路的异步接口。TUI 实现该接口；测试可以用固定返回值的 fake prompter。

### PermissionController
```python
class PermissionController:
    def __init__(
        self,
        config: PermissionConfig,
        engine: PermissionEngine,
        session_rules: SessionPermissionRules,
        rule_store: PermissionRuleStore,
        prompter: PermissionPrompter | None = None,
    ) -> None: ...

    def evaluate(self, call: ToolCall, spec: ToolSpec) -> PermissionDecision: ...

    async def resolve_prompt(self, prompt: PermissionPrompt) -> PermissionDecision: ...

    def denial_result(self, call: ToolCall, decision: PermissionDecision) -> ToolResult: ...
```

封装权限判断、用户确认结果应用和拒绝结果转换。无 prompter 且需要确认时，返回 `permission_confirmation_required` 拒绝，避免非交互路径挂起。

## 模块设计

### `julycode.permissions.models`
**职责：** 定义权限模式、规则、决策、提示和事件载荷等基础模型。

**对外接口：**
```python
PermissionMode
PermissionEffect
PermissionRuleSource
MatchKind
PermissionRule
RuleMatch
PermissionSubject
PermissionDecision
PermissionPrompt
PermissionPromptResult
PermissionEventPayload
PermissionPrompter
```

**依赖：** `dataclasses`、`typing`、`julycode.tools.base`。

### `julycode.permissions.blacklist`
**职责：** 对 `run_command` 的命令字符串执行不可配置的高危正则匹配。

**对外接口：**
```python
class DangerousCommandGuard:
    def check(self, command: str) -> PermissionDecision | None: ...
```

**规则范围：** 初始覆盖已知高危模式：根目录或家目录递归删除、`sudo rm`、磁盘格式化、裸设备写入、关机重启、fork bomb、全局权限破坏、`kill -9 -1`、`git clean -fdx` 等。命中后返回 `error_type="permission_dangerous_command"`。

**依赖：** `re`、`julycode.permissions.models`。

### `julycode.permissions.sandbox`
**职责：** 对内置文件类工具做项目路径沙箱检查，并为规则引擎提供规范化目标。

**对外接口：**
```python
class ProjectSandbox:
    def __init__(self, root: Path) -> None: ...

    @property
    def resolved_root(self) -> Path: ...

    def resolve_inside(self, raw_path: str) -> Path: ...

    def relative_display(self, path: Path) -> str: ...

    def check_tool_call(self, call: ToolCall) -> PermissionDecision | None: ...

    def subject_for(self, call: ToolCall) -> PermissionSubject: ...
```

**行为：**
- `resolve_inside()` 对相对路径、绝对路径和符号链接使用 `Path.resolve(strict=False)` 得到真实目标，再用 `is_relative_to(resolved_root)` 判断是否在项目内。
- `read_file`、`write_file`、`edit_file` 必须检查路径参数。
- `search_code` 必须检查可选 `path`，缺省按 `.` 处理。
- `find_files` 拒绝绝对 glob 和包含 `..` 的 glob，并在执行前展开候选匹配，确认所有已匹配文件的真实路径仍位于项目内；工具自身返回时继续只展示项目内相对路径。

**依赖：** `pathlib`、`julycode.tools.base`、`julycode.permissions.models`。

### `julycode.permissions.rules`
**职责：** 解析 YAML 规则文件、匹配工具调用、处理规则优先级，并写入本地持久规则。

**YAML 格式：**
```yaml
rules:
  "Bash(git *)": allow
  "read_file(README.md)": allow
  "write_file(src/generated/**)": deny
```

**文件位置：**
- 用户级：`~/.julycode/permissions.yaml`
- 项目级：`<cwd>/.julycode.permissions.yaml`
- 本地级：`<cwd>/.julycode.permissions.local.yaml`
- 会话级：内存中的 `SessionPermissionRules`

**对外接口：**
```python
class PermissionRuleParser:
    def parse_rule_key(self, key: str, source: PermissionRuleSource, effect: str) -> PermissionRule: ...

class PermissionRuleSet:
    def __init__(self, source: PermissionRuleSource, rules: Sequence[PermissionRule]) -> None: ...
    def match(self, subject: PermissionSubject) -> RuleMatch | None: ...

class PermissionRuleStore:
    @classmethod
    def load(cls, cwd: Path) -> PermissionRuleStore: ...
    def ordered_rule_sets(self, session_rules: SessionPermissionRules) -> tuple[PermissionRuleSet, ...]: ...
    def add_local_rule(self, rule: PermissionRule) -> None: ...

class SessionPermissionRules:
    def add(self, rule: PermissionRule) -> None: ...
    def as_rule_set(self) -> PermissionRuleSet: ...
```

**依赖：** `yaml`、`fnmatch`、`pathlib`、`julycode.errors.ConfigError`。

### `julycode.permissions.engine`
**职责：** 编排黑名单、沙箱、规则引擎和权限模式，输出 `PermissionDecision`。

**对外接口：**
```python
class PermissionEngine:
    def __init__(
        self,
        config: PermissionConfig,
        sandbox: ProjectSandbox,
        command_guard: DangerousCommandGuard,
        rule_store: PermissionRuleStore,
        session_rules: SessionPermissionRules,
    ) -> None: ...

    def evaluate(self, call: ToolCall, spec: ToolSpec) -> PermissionDecision: ...
```

**决策顺序：**
1. `run_command` 先执行 `DangerousCommandGuard.check()`；命中直接 deny。
2. 文件类工具执行 `ProjectSandbox.check_tool_call()`；命中逃逸直接 deny。
3. 通过 `ProjectSandbox.subject_for()` 生成 `PermissionSubject`，按 `session > local > project > user` 匹配规则。
4. 显式 deny 直接 deny。
5. 严格模式下，`ToolSpec.safety == "side_effect"` 时返回 prompt，即使命中 allow。
6. 显式 allow 在默认和放行模式下 allow。
7. 未命中时，读类工具 allow；有副作用工具按模式处理：严格和默认 prompt，放行 allow。

**依赖：** `julycode.permissions.blacklist`、`sandbox`、`rules`、`models`。

### `julycode.permissions.controller`
**职责：** 提供调度器可调用的权限 API，处理用户确认选择，并生成工具失败结果。

**对外接口：**
```python
class PermissionController:
    def evaluate(self, call: ToolCall, spec: ToolSpec) -> PermissionDecision: ...
    async def resolve_prompt(self, prompt: PermissionPrompt) -> PermissionDecision: ...
    def denial_result(self, call: ToolCall, decision: PermissionDecision) -> ToolResult: ...

def create_permission_controller(
    cwd: Path,
    config: PermissionConfig,
    prompter: PermissionPrompter | None = None,
) -> PermissionController: ...
```

**行为：**
- `allow_once` 返回 allow，不写规则。
- `allow_session` 生成 `source="session"` 的 allow 规则并加入 `SessionPermissionRules`。
- `allow_permanent` 生成 `source="local"` 的 allow 规则，写入本地级 YAML 后返回 allow；写入失败返回 `permission_persist_failed`。
- `deny` 返回 `permission_user_denied`。

**依赖：** `julycode.tools.base`、`julycode.errors.redact_secret`、权限模型。

### `julycode.config`
**职责：** 加载权限模式配置，并保持原有模型供应商配置行为。

**改动接口：**
```python
@dataclass(frozen=True)
class AppConfig:
    ...
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
```

新增解析：
```python
def _parse_permissions(raw: Any) -> PermissionConfig: ...
```

支持配置：
```yaml
permissions:
  mode: default
```

### `julycode.tools.scheduler`
**职责：** 在工具执行前接入权限控制，并继续负责多工具分批、并发读类和串行有副作用工具。

**改动接口：**
```python
class ToolCallScheduler:
    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        policy: ToolPolicy,
        permission_controller: PermissionController | None = None,
    ) -> None: ...
```

`run()` 的执行流程改为：
1. 调用 `ToolPolicy.validate_call()`，处理未知工具和 Plan Mode 限制。
2. 调用权限控制器；deny 返回权限失败 `ToolResult`。
3. prompt 时先 yield `permission_requested` 事件，再调用 `PermissionPrompter.request_permission()` 等待用户选择，然后 yield `permission_resolved` 事件。
4. allow 后调用 `ToolExecutor.execute()`。

并发读类批次继续使用 `asyncio.gather()`；读类工具要么 allow，要么 deny，不进入 prompt。有副作用工具本来就是单独串行批次，调度器在每个有副作用调用真正执行前同步完成权限确认。无法确认安全性的工具必须在 `ToolSpec.safety` 上标为 `side_effect`，从而进入串行批次。

### `julycode.agent`
**职责：** 扩展事件模型，把权限等待和权限决策传递给 TUI 与测试。

**改动接口：**
```python
TurnEventType = Literal[
    ...,
    "permission_requested",
    "permission_resolved",
]

@dataclass(frozen=True)
class TurnEvent:
    ...
    permission: PermissionEventPayload | None = None
```

`AgentLoopRunner` 新增可选 `permission_controller` 参数，并传给 `ToolCallScheduler`。未传入时用 `executor.context.cwd` 创建一个加载现有规则、模式为 `permissive`、无 prompter 的非交互控制器，保证非 TUI 测试不会挂起；生产 CLI 和 TUI 必须传入由配置创建的控制器。

### `julycode.tui.widgets`
**职责：** 增加权限确认视图和权限状态展示。

**新增接口：**
```python
class PermissionPromptView(Vertical):
    def __init__(self, prompt: PermissionPrompt, future: asyncio.Future[UserPermissionChoice], **kwargs: object) -> None: ...
```

视图包含工具名、关键参数、触发原因和四个按钮：本次允许、本会话允许、永久允许、拒绝。按钮点击后设置 future 并移除视图。

`ToolStatusView` 继续用于显示工具运行结果；权限拒绝表现为失败工具结果，body 展示 `permission_*` 错误类型。

### `julycode.tui.app`
**职责：** 实现 `PermissionPrompter`，把权限请求转为 TUI 上的确认视图，并处理权限事件。

**改动接口：**
```python
class JulyCodeApp(App[None], PermissionPrompter):
    async def request_permission(self, prompt: PermissionPrompt) -> UserPermissionChoice: ...
```

`JulyCodeApp.__init__()` 接收 `permission_controller`。运行任务时创建 `AgentLoopRunner(..., permission_controller=...)`。收到 `permission_requested` 事件时更新状态栏或追加提示信息；收到 `permission_resolved` 事件时清理等待状态。

### `julycode.cli`
**职责：** 在启动时创建权限控制器，并把 TUI prompter 接入。

**改动流程：**
1. `load_config()` 读取 `permissions.mode`。
2. 创建 registry、executor。
3. 创建 `JulyCodeApp`。
4. 用 app 作为 prompter 创建 `PermissionController`。
5. 通过 `app.set_permission_controller(controller)` 注入权限控制器后启动 TUI。

### `README.md`
**职责：** 文档化权限模式、规则格式、规则文件位置、硬拒绝和沙箱边界。

## 模块交互
普通工具调用链：

```text
AgentLoopRunner
  → ToolPolicy.allowed_specs()
  → provider.stream_chat()
  → ToolCallScheduler.run(tool_calls)
      → ToolPolicy.validate_call()
      → PermissionController.evaluate()
          → DangerousCommandGuard.check()
          → ProjectSandbox.check_tool_call()
          → PermissionRuleStore ordered match
          → PermissionMode fallback
      → allow: ToolExecutor.execute()
      → deny: PermissionController.denial_result()
      → prompt:
          → TurnEvent(permission_requested)
          → PermissionPrompter.request_permission()
          → PermissionController.resolve_prompt()
          → TurnEvent(permission_resolved)
          → allow 或 deny
  → ChatSession.append_tool_result()
  → 下一轮模型请求
```

Plan Mode 调用链：

```text
ToolPolicy.validate_call()
  → side_effect 工具直接返回 tool_not_allowed
  → 不进入 PermissionController
```

规则加载链：

```text
create_permission_controller(cwd, config, prompter)
  → PermissionRuleStore.load(cwd)
      → ~/.julycode/permissions.yaml
      → <cwd>/.julycode.permissions.yaml
      → <cwd>/.julycode.permissions.local.yaml
  → SessionPermissionRules()
  → PermissionEngine(...)
```

永久允许链：

```text
PermissionPromptView: 永久允许
  → JulyCodeApp.request_permission() 返回 allow_permanent
  → PermissionController.resolve_prompt()
  → PermissionRuleStore.add_local_rule()
  → <cwd>/.julycode.permissions.local.yaml 写入 allow 规则
  → 当前调用继续执行
```

## 文件组织
```text
src/julycode/
├── permissions/
│   ├── __init__.py              — 权限系统公共导出
│   ├── models.py                — 权限模式、规则、决策、提示和事件模型
│   ├── blacklist.py             — 高危命令硬拦截
│   ├── sandbox.py               — 项目路径沙箱与调用目标提取
│   ├── rules.py                 — YAML 规则加载、匹配、排序和本地规则写入
│   ├── engine.py                — 五层权限决策编排
│   └── controller.py            — 调度器使用的权限控制入口和人审结果处理
├── config.py                    — 增加 PermissionConfig 与 permissions.mode 解析
├── tools/
│   └── scheduler.py             — 工具执行前调用 PermissionController，发出权限事件
├── agent.py                     — TurnEvent 增加权限事件载荷，Runner 传递权限控制器
├── tui/
│   ├── app.py                   — 实现 PermissionPrompter，接入权限控制器和事件处理
│   └── widgets.py               — 新增 PermissionPromptView
└── cli.py                       — 创建权限控制器并传入 TUI

tests/
├── test_permissions.py          — 黑名单、沙箱、规则优先级、模式决策和永久规则写入测试
├── test_tool_scheduler.py       — 权限 allow/deny/prompt 与 Plan Mode 共存测试
├── test_agent.py                — 权限拒绝回灌后 Agent Loop 继续测试
├── test_config.py               — permissions.mode 配置解析测试
└── test_tui_smoke.py            — 权限确认视图、用户选择和输入恢复测试

specs/permission-system/
├── spec.md
├── plan.md
├── task.md
└── checklist.md

README.md                       — 权限系统用户说明
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 权限接入位置 | 接在 `ToolCallScheduler` 与 `ToolExecutor` 之间 | 调度器已经集中处理工具调用顺序和 Plan Mode，接入后能覆盖所有真实执行路径，并保持 Provider 层无感知 |
| Plan Mode 优先级 | `ToolPolicy.validate_call()` 先于权限控制器执行 | 确保权限规则和放行模式不能绕过规划阶段只读约束 |
| 规则文件位置 | 用户级 `~/.julycode/permissions.yaml`，项目级 `<cwd>/.julycode.permissions.yaml`，本地级 `<cwd>/.julycode.permissions.local.yaml` | 保持规则与主模型配置分离；本地级文件适合作为当前机器上的项目覆盖 |
| 永久允许写入位置 | 写入本地级规则文件 | 避免一次项目内确认扩大成所有项目的全局允许，同时满足跨运行保留 |
| 规则格式 | `rules` 映射中使用 `"工具名(模式)": allow|deny` | 贴合用户给出的格式，便于手写和 diff |
| 规则匹配 | 自动区分精确和 glob，精确优先，deny 冲突优先 | 满足可预测的优先级要求，避免宽泛 allow 意外覆盖精确 deny |
| 命令工具别名 | 支持 `Bash(...)` 作为 `run_command(...)` 别名 | 兼容用户示例，同时不改变内部工具名 |
| 路径沙箱根 | 使用 CLI 启动时的 `Path.cwd().resolve()` | 与当前工具执行目录一致，行为简单可观测 |
| 符号链接解析 | 使用 `Path.resolve(strict=False)` 后做 `is_relative_to()` | 可处理不存在的写入目标，同时解析已有父目录中的符号链接，阻止逃逸 |
| 确认交互 | 用异步 `PermissionPrompter` 协议 | 核心权限逻辑可测试，TUI 只负责收集用户选择 |
| 无 prompter 行为 | 需要确认时返回 `permission_confirmation_required` | 避免非交互测试或未来 CLI 批处理路径永久等待 |
| 权限拒绝表现 | 转为失败 `ToolResult` 回灌模型 | 满足 Agent Loop 不因权限拒绝终止，并复用现有工具失败展示 |
| 配置错误处理 | 规则 YAML 格式错误抛 `ConfigError` | 与现有配置错误路径一致，避免半解析状态继续运行 |
