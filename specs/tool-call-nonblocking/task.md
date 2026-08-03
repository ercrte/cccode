# Tool Call Nonblocking Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/julycode/tools/builtin.py` | 内置工具非阻塞执行 |
| 修改 | `src/julycode/skills/tools.py` | Skill 脚本工具非阻塞执行 |
| 修改 | `src/julycode/tools/scheduler.py` | 只读并发批次逐项完成事件 |
| 修改 | `tests/test_tools.py` | 内置工具非阻塞与兼容测试 |
| 修改 | `tests/test_skills.py` | Skill 脚本工具非阻塞测试 |
| 修改 | `tests/test_tool_scheduler.py` | 调度器逐项完成事件测试 |
| 修改 | `specs/tool-call-nonblocking/checklist.md` | 验收证据记录 |

## T1: 内置工具非阻塞执行

**文件：** `src/julycode/tools/builtin.py`  
**依赖：** 无  
**步骤：**
1. 移除内置工具在主事件循环线程内执行阻塞式 `subprocess.run` 的路径。
2. 增加模块内部 daemon 工作线程辅助函数，用于承载同步命令、IO 和扫描操作。
3. 将 `RunCommandTool.execute()` 改为工作线程承载的子进程实现，保持返回字段与超时错误一致。
4. 将 `SearchCodeTool._search_with_rg()` 改为工作线程承载的 `rg` 执行，保持返回码处理和超时 fallback。
5. 将文件读写、编辑、glob、Python 搜索 fallback 中可能较慢的同步文件系统操作放入 daemon 工作线程。

**验证：** 运行 `python -m pytest tests/test_tools.py -q`，期望全部通过。

## T2: Skill 脚本工具非阻塞执行

**文件：** `src/julycode/skills/tools.py`  
**依赖：** T1  
**步骤：**
1. 用 daemon 工作线程承载 `SkillScriptTool.execute()` 内的阻塞式脚本执行。
2. 保持 stdin payload、环境变量、cwd、stdout/stderr 解码逻辑不变。
3. 保持脚本失败、超时、JSON 非法和非对象结果的错误类型不变。

**验证：** 运行 `python -m pytest tests/test_skills.py -q`，期望全部通过。

## T3: 只读并发批次逐项完成反馈

**文件：** `src/julycode/tools/scheduler.py`  
**依赖：** T1  
**步骤：**
1. 为只读并发批次增加内部执行辅助逻辑，为每个工具创建独立任务。
2. 使用等待任一任务完成的方式逐项产出完成结果。
3. 单个工具完成后立即发出对应 Hook 完成事件和工具完成事件。
4. 继续按原始 tool call 顺序维护 `results()` 返回值。
5. 在取消或异常退出时清理未完成任务。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py -q`，期望全部通过。

## T4: 补充非阻塞行为测试

**文件：** `tests/test_tools.py`、`tests/test_skills.py`、`tests/test_tool_scheduler.py`  
**依赖：** T1、T2、T3  
**步骤：**
1. 在内置工具测试中增加命令工具运行期间 ticker 协程仍被调度的断言。
2. 在内置工具测试中增加 Python 搜索或文件扫描执行期间 ticker 协程仍被调度的断言。
3. 在 Skill 测试中增加脚本工具运行期间 ticker 协程仍被调度的断言。
4. 在调度器测试中增加快只读工具先于慢只读工具产生 `tool_finished` 的断言。
5. 保留原有返回结构、错误包装和超时测试。

**验证：** 运行 `python -m pytest tests/test_tools.py tests/test_skills.py tests/test_tool_scheduler.py -q`，期望全部通过。

## T5: 回归与验收

**文件：** `specs/tool-call-nonblocking/checklist.md`  
**依赖：** T4  
**步骤：**
1. 运行相关单元测试和全量测试。
2. 运行编译检查。
3. 按 AGENTS.md 要求用 tmux 启动 JulyCode，输入真实请求触发工具调用，观察状态与回复。
4. 对照 checklist 逐项记录证据。

**验证：** 运行 `python -m pytest -q`、`python -m compileall -q src tests`，并完成 tmux 端到端观察。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5
```
