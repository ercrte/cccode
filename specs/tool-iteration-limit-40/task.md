# 工具迭代上限调整为 40 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/mewcode/config.py` | 主 Agent 配置默认值和子 Agent 配置解析默认值 |
| 修改 | `src/mewcode/subagents/models.py` | 子 Agent 数据结构默认值 |
| 修改 | `src/mewcode/subagents/builtin/reviewer.md` | 内置审查角色上限 |
| 修改 | `src/mewcode/subagents/builtin/code-searcher.md` | 内置搜索角色上限 |
| 修改 | `tests/test_config.py` | 主/子 Agent 默认值与显式覆盖测试 |
| 修改 | `tests/test_subagents.py` | 子 Agent 优先级与内置角色测试 |
| 修改 | `README.md` | 40 轮默认值和角色示例 |

## T1: 调整主 Agent 和子 Agent 默认配置

**文件：** `src/mewcode/config.py`、`src/mewcode/subagents/models.py`、`tests/test_config.py`  
**依赖：** 无  
**步骤：**
1. 将主 Agent 数据结构和 YAML 缺省解析值改为 40。
2. 将子 Agent 默认迭代上限改为 40；YAML 未提供该字段时解析为 40，显式空值继续保留回退语义。
3. 更新缺省配置断言，并保留主 Agent、子 Agent 显式正整数和非法值测试。

**验证：** 运行 `python -m pytest tests/test_config.py -q`，期望全部通过。

## T2: 调整内置角色并验证优先级

**文件：** `src/mewcode/subagents/builtin/reviewer.md`、`src/mewcode/subagents/builtin/code-searcher.md`、`tests/test_subagents.py`  
**依赖：** T1  
**步骤：**
1. 将两个内置角色 frontmatter 的迭代上限改为 40。
2. 增加加载真实内置角色的测试，断言两个角色均为 40。
3. 增加或补充子 Agent 运行配置测试，覆盖无显式值时使用 40，以及单次委派、角色和子 Agent 配置仍按现有优先级覆盖。

**验证：** 运行 `python -m pytest tests/test_subagents.py -q`，期望全部通过。

## T3: 同步用户文档

**文件：** `README.md`  
**依赖：** T1、T2  
**步骤：**
1. 将主 Agent 配置示例更新为 40。
2. 将子 Agent 默认配置和角色示例更新为 40。
3. 补充文字说明：40 是默认值，不是硬上限，合法显式配置仍可覆盖。

**验证：** 运行 `rg -n "max_iterations: 40|default_max_iterations: 40|默认.*40|40.*默认" README.md`，期望主 Agent、子 Agent 和角色示例均可见。

## T4: 回归与端到端验收

**文件：** 无新增修改  
**依赖：** T1、T2、T3  
**步骤：**
1. 运行配置、子 Agent 和 Agent Loop 回归测试。
2. 在 tmux 中启动 MewCode 新会话，输入要求读取项目文件并回答的真实请求。
3. 观察 MewCode 调用读取工具、进度显示 40 轮上限并最终生成回复。
4. 对照 `checklist.md` 逐项记录证据。

**验证：** 运行 `python -m pytest tests/test_config.py tests/test_subagents.py tests/test_agent.py -q`，并确认 tmux 捕获输出包含工具执行、`/40` 进度或等价上限信息及最终回复。

## 执行顺序

```text
T1 → T2 → T3 → T4
```
