# 工具迭代上限调整为 40 Plan

## 架构概览
本次不新增模块，只调整现有配置默认值与内置角色元数据。主 Agent 继续由应用配置提供上限；子 Agent 和团队成员继续使用“单次委派 → 角色 → 子 Agent 默认值 → 主 Agent 默认值”的现有优先级。所有停止判断继续由现有 Agent Loop 负责。

## 核心数据结构

### `AgentConfig`
```python
@dataclass(frozen=True)
class AgentConfig:
    max_iterations: int = 40
```

主 Agent 缺省使用 40；配置解析仍接受任意合法正整数覆盖。

### `SubAgentConfig`
```python
@dataclass(frozen=True)
class SubAgentConfig:
    default_max_iterations: int | None = 40
```

子 Agent 缺省使用 40。类型保留 `None`，以兼容已有的显式空值和向主 Agent 回退的语义。

### 内置角色 Frontmatter
`reviewer` 与 `code-searcher` 的 `max_iterations` 均设为 40，继续通过现有角色加载器进入子 Agent 配置优先级链。

## 模块设计

### 配置模块
**职责：** 将主 Agent 和子 Agent 的缺省迭代上限解析为 40，并保持正整数校验和显式覆盖行为。  
**对外接口：** `AgentConfig`、`SubAgentConfig`、`load_config()`。  
**依赖：** 现有 YAML 配置解析逻辑。

### 子 Agent 角色资源
**职责：** 为两个内置角色声明 40 轮上限。  
**对外接口：** 现有 Markdown frontmatter。  
**依赖：** 现有子 Agent 角色加载器。

### 测试与文档
**职责：** 验证默认值、配置优先级、内置角色值及停止语义，并同步 README 示例。  
**依赖：** 现有配置、子 Agent 和 Agent Loop 测试设施。

## 模块交互
```text
配置文件或缺省值
  → AppConfig
  → 主 Agent 直接使用 AgentConfig.max_iterations
  → 子 Agent/团队成员按现有优先级选择 max_iterations
  → Agent Loop 按实际值执行并在达到上限时停止
```

## 文件组织
```text
src/julycode/config.py                              — 主 Agent 默认值及 YAML 解析默认值
src/julycode/subagents/models.py                    — 子 Agent 默认值
src/julycode/subagents/builtin/reviewer.md          — 内置审查角色上限
src/julycode/subagents/builtin/code-searcher.md     — 内置搜索角色上限
tests/test_config.py                               — 主/子 Agent 配置默认值与显式覆盖测试
tests/test_subagents.py                            — 内置角色及子 Agent 优先级测试
tests/test_agent.py                                — 复用现有迭代停止行为测试
README.md                                          — 配置与角色示例
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 默认值落点 | 修改现有配置数据结构和解析缺省值 | 保持现有配置入口，不引入新概念 |
| 子 Agent 类型 | 保留 `int | None` | 避免破坏显式空值向主 Agent 回退的兼容行为 |
| 内置角色 | 将两个角色的显式值改为 40 | 内置角色当前显式值会覆盖子 Agent 默认值，必须同步修改才能满足需求 |
| 停止逻辑 | 不修改 | 现有逻辑已按实际配置值停止，本次只调整输入值 |
| 测试策略 | 更新默认断言并增加内置角色/优先级覆盖 | 同时证明新默认生效和显式配置未回退 |

## 需求映射

| 需求 | 技术归属 |
|------|----------|
| F1 | `AgentConfig` 与主配置解析 |
| F2、F4 | `SubAgentConfig` 与现有优先级链 |
| F3 | 两个内置角色 frontmatter |
| F5 | 现有 Agent Loop 停止测试 |
| F6 | README 配置与角色示例 |
