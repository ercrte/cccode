# Tool Call Nonblocking Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [x] 命令类工具执行期间主事件循环可继续调度（验证：运行 `python -m pytest tests/test_tools.py -q`，观察命令非阻塞测试通过；证据：`33 passed in 0.69s`）
- [x] 文件系统工具执行期间主事件循环可继续调度（验证：运行 `python -m pytest tests/test_tools.py -q`，观察文件/搜索非阻塞测试通过；证据：`33 passed in 0.69s`）
- [x] Skill 脚本工具执行期间主事件循环可继续调度（验证：运行 `python -m pytest tests/test_skills.py -q`，观察 Skill 非阻塞测试通过；证据：`9 passed in 0.43s`）
- [x] 只读并发批次中快工具能先于慢工具发出完成事件（验证：运行 `python -m pytest tests/test_tool_scheduler.py -q`，观察逐项完成测试通过；证据：`18 passed in 0.36s`）
- [x] 工具返回结构、错误包装和超时行为保持兼容（验证：运行 `python -m pytest tests/test_tools.py tests/test_skills.py tests/test_tool_scheduler.py -q`，观察现有兼容测试通过；证据：`60 passed in 1.28s`）

## 集成

- [x] Agent 工具调度仍按只读并发、有副作用串行执行（验证：运行 `python -m pytest tests/test_tool_scheduler.py -q`，观察调度策略测试通过；证据：`18 passed in 0.36s`）
- [x] 权限确认流程保持现状，副作用工具仍在需要时等待确认（验证：运行 `python -m pytest tests/test_permissions.py tests/test_tool_scheduler.py -q`，观察权限相关测试通过；证据：`51 passed in 0.47s`）
- [x] Hook 与工具执行集成保持兼容（验证：运行 `python -m pytest tests/test_hooks.py tests/test_tool_scheduler.py -q`，观察 Hook 工具测试通过；证据：`36 passed in 0.47s`）
- [x] Agent Loop 能继续接收工具结果并进入下一轮模型调用（验证：运行 `python -m pytest tests/test_agent.py tests/test_tui_smoke.py -q`，观察 Agent/TUI 测试通过；证据：`82 passed in 7.76s`）

## 编译与测试

- [x] 相关单元测试通过（验证：运行 `python -m pytest tests/test_tools.py tests/test_skills.py tests/test_tool_scheduler.py -q`，期望全部通过；证据：`60 passed in 1.28s`）
- [x] Agent、权限、Hook、TUI 相关回归测试通过（验证：运行 `python -m pytest tests/test_agent.py tests/test_permissions.py tests/test_hooks.py tests/test_tui_smoke.py -q`，期望全部通过；证据：`133 passed in 7.29s`）
- [x] 项目编译无错误（验证：运行 `python -m compileall -q src tests`，期望退出码为 0；证据：退出码 0，无输出）
- [x] 全量测试通过（验证：运行 `python -m pytest -q`，期望全部通过；证据：`651 passed in 20.31s`）

## 端到端场景

- [x] 场景 1：在 tmux 中启动 MewCode，输入需要读取项目文件的真实请求，观察工具状态出现并最终正常回复（验证：tmux 捕获界面显示工具调用完成和最终回答；证据：状态栏 `normal 2/40 done`，`工具: find_files 完成`、`工具: read_file 完成`，最终回复已显示）
- [x] 场景 2：在 tmux 中触发需要本地命令或搜索的真实请求，观察工具调用期间界面仍更新并最终正常回复（验证：tmux 捕获界面显示工具状态、进度和最终回答；证据：状态栏 `normal 2/40 done`，`工具: run_command 完成`，工具结果包含 `exit_code: 0` 和 `stdout: "mew\n"`）

## 验收记录

已完成。实现时发现当前环境中 `asyncio` 子进程/跨线程唤醒不可靠，因此采用 daemon 工作线程承载阻塞命令和 IO，并通过事件循环轮询队列取回结果；单元测试和 tmux 端到端均验证主事件循环可继续调度。
