# 工具迭代上限调整为 40 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统实际生效的迭代上限。

## 实现完整性

- [x] [AC1] 未配置主 Agent 上限时，加载结果为 40（证据：`tests/test_config.py` 72 项通过）
- [x] [AC2] 未配置单次委派、角色和子 Agent 默认上限时，子 Agent 实际上限为 40（证据：`tests/test_subagents.py` 24 项通过）
- [x] [AC3] 内置 `reviewer` 和 `code-searcher` 加载后的上限均为 40（证据：真实内置角色加载测试通过）
- [x] [AC4] 主 Agent、子 Agent 默认值、角色和单次委派的显式正整数仍按原优先级生效（证据：配置及子 Agent 优先级测试通过）
- [x] [AC5] 较小显式上限仍能触发原有停止事件和用户提示（证据：`test_runner_stops_at_iteration_limit` 通过）
- [x] [AC6] README 展示主 Agent、子 Agent 和内置角色的 40 轮配置，并说明可显式覆盖（证据：`rg` 命中角色、子 Agent、主 Agent 示例和覆盖说明）

## 集成

- [x] 子 Agent 与团队成员继续通过现有优先级链获得有效正整数上限（证据：目标回归测试共 140 项通过）
- [x] OpenAI/Anthropic 共用的 Agent Loop 停止逻辑没有变化（证据：Agent 与两类 Provider 回归测试 72 项通过）

## 编译与测试

- [x] Python 源码和测试文件可编译（证据：`python -m compileall -q src tests` 退出码为 0）
- [x] 目标回归测试全部通过（证据：140 项通过）
- [x] 全量测试无回归（证据：647 项通过）

## 端到端场景

- [x] 默认主 Agent 场景：在 tmux 中运行 `mewcode --new-session` 并提交真实读取请求（证据：界面显示 `normal 2/40 done`、`工具: read_file 完成`，最终回答最低 Python 版本为 3.11）
- [x] 显式小上限边界：自动化场景持续请求工具且配置上限为 1 时停止并显示原因（证据：`test_runner_stops_at_iteration_limit` 通过）
