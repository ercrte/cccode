# 代码定位工具可靠性 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/julycode/tools/file_catalog.py` | 项目文件枚举、默认排除、显式范围和 glob 匹配 |
| 修改 | `src/julycode/tools/builtin.py` | 接入候选目录、搜索回退和文件局部读取 |
| 修改 | `src/julycode/prompting/modules.py` | 强化专用只读工具优先规则 |
| 视测试结果修改 | `src/julycode/permissions/sandbox.py` | 维持新参数与显式搜索目标的路径边界 |
| 新建 | `tests/test_tool_file_catalog.py` | 候选文件目录的 Git、非 Git、显式目标和 glob 测试 |
| 修改 | `tests/test_tools.py` | 三个只读工具的能力、回退和兼容测试 |
| 修改 | `tests/test_permissions.py` | 局部读取参数与显式目标的沙箱回归测试 |
| 修改 | `tests/test_prompting.py` | 专用工具选择规则测试 |
| 修改 | `eval/july_eval/provider.py` | 新离线用例的确定性工具序列 |
| 新建 | `eval/cases/offline/code_location_reliability.json` | 单文件搜索与局部读取离线评测 |
| 修改 | `eval/cases/offline/readonly_search.json` | 禁止只读场景使用命令工具 |
| 修改 | `eval/cases/offline/multi_tool_loop.json` | 禁止代码定位场景使用命令工具 |
| 修改 | `eval/cases/online/default_online_cases.json` | 在线只读定位用例禁止命令绕过 |
| 修改 | `tests/test_eval_framework.py` | 新用例加载、执行和禁用工具评分测试 |
| 修改 | `README.md` | 记录局部读取、忽略规则和命令限制 |
| 新建 | `specs/code-location-reliability/checklist.md` | 开发后的可执行验收清单 |

## T1：建立非 Git 默认候选文件遍历

**文件：** `src/julycode/tools/file_catalog.py`、`tests/test_tool_file_catalog.py`  
**依赖：** 无

**步骤：**

1. 定义 `DEFAULT_SEARCH_EXCLUDED_DIRS`，覆盖计划中的版本控制、JulyCode、缓存、环境、依赖和构建目录。
2. 创建 `FileCatalog`，保存解析后的项目根目录。
3. 使用 `os.scandir` 实现非 Git 默认文件遍历，在进入目录前剪枝默认排除项。
4. 跳过目录符号链接，并确保返回文件的真实路径仍位于项目根内。
5. 返回按项目相对 POSIX 路径稳定排序的文件集合。
6. 添加测试覆盖正常文件、嵌套文件、各类默认排除目录和目录符号链接。

**验证：** 运行 `python -m pytest tests/test_tool_file_catalog.py -q -k "non_git or excludes or symlink"`，期望非 Git 默认范围测试全部通过。

## T2：实现 Git Worktree 默认候选文件

**文件：** `src/julycode/tools/file_catalog.py`、`tests/test_tool_file_catalog.py`  
**依赖：** T1

**步骤：**

1. 使用无 Shell 参数调用识别当前 Worktree 根目录。
2. 运行 `git ls-files -co --exclude-standard -z` 获取 tracked 和未跟踪未忽略文件。
3. 当启动目录位于 Worktree 子目录时，只保留该目录下的文件并转换为相对启动目录路径。
4. 对 Git 返回结果继续应用固定默认排除目录和项目边界检查。
5. Git 不可用、不是仓库或命令失败时，退回 T1 的剪枝遍历。
6. 添加测试覆盖 tracked、未跟踪未忽略、被忽略文件、固定排除目录、子目录启动和 Git 失败回退。

**验证：** 运行 `python -m pytest tests/test_tool_file_catalog.py -q -k "git or worktree or fallback"`，期望 Git 文件集合和回退测试全部通过。

## T3：实现显式目标文件枚举

**文件：** `src/julycode/tools/file_catalog.py`、`tests/test_tool_file_catalog.py`  
**依赖：** T1

**步骤：**

1. 实现 `explicit_files(target)`。
2. 目标为文件时只返回该文件；目标为目录时递归返回目录内合法文件。
3. 显式非根目标不应用默认排除，因此允许访问项目内 `.julycode` 或 Git 忽略目录。
4. 目标等于项目根时复用默认候选范围。
5. 拒绝项目外路径，跳过越界文件符号链接和目录符号链接。
6. 添加测试覆盖显式文件、显式被忽略目录、根目录语义、越界和符号链接。

**验证：** 运行 `python -m pytest tests/test_tool_file_catalog.py -q -k "explicit or outside"`，期望显式目标和边界测试全部通过。

## T4：实现路径段感知的 glob 匹配

**文件：** `src/julycode/tools/file_catalog.py`、`tests/test_tool_file_catalog.py`  
**依赖：** T1

**步骤：**

1. 实现将 glob 模式转换为路径段感知匹配规则。
2. 保证 `*`、`?` 和字符集合不跨 `/`，`**` 可以匹配零个或多个目录层级。
3. 保持现有常用语义：`*.py` 只匹配根级，`**/*.py` 同时匹配根级和嵌套文件，`src/**/*.py` 同时匹配 `src` 直属与深层文件。
4. 实现 `matching_files()` 的稳定排序和 `max_results` 提前停止。
5. 添加参数化测试覆盖根级、嵌套、字符集合、无匹配和最大结果数。

**验证：** 运行 `python -m pytest tests/test_tool_file_catalog.py -q -k "glob or max_results"`，期望 glob 语义和提前停止测试全部通过。

## T5：让 `find_files` 使用共享候选目录

**文件：** `src/julycode/tools/builtin.py`、`tests/test_tools.py`  
**依赖：** T2、T4

**步骤：**

1. 将 `FindFilesTool` 的直接 `Path.glob` 调用替换为 `FileCatalog.default_files()` 和 `matching_files()`。
2. 保持 `pattern`、`max_results`、`matches`、`count` 与相对路径格式兼容。
3. 更新工具描述，说明默认遵守项目忽略规则。
4. 添加测试覆盖 Git ignore、非 Git 默认排除、空结果、嵌套 glob 和最大结果数。
5. 保留现有 find_files 测试并调整与新 glob 语义冲突的断言。

**验证：** 运行 `python -m pytest tests/test_tools.py tests/test_tool_file_catalog.py -q -k "find_files"`，期望文件查找测试全部通过。

## T6：定义 `read_file` 局部读取参数和范围校验

**文件：** `src/julycode/tools/builtin.py`、`tests/test_tools.py`  
**依赖：** 无

**步骤：**

1. 为 `read_file` Schema 增加可选整数 `offset` 和 `limit`。
2. 定义内部 `ReadWindow`，将缺失的 `offset` 归一为第 1 行。
3. 校验 `offset > 0`、`limit > 0`，并拒绝非空文件中超过总行数的起始位置。
4. 对范围错误返回 `invalid_arguments`。
5. 添加测试覆盖只传 offset、只传 limit、二者同时传、零值、负值、错误类型和越过文件末尾。

**验证：** 运行 `python -m pytest tests/test_tools.py -q -k "read_file and (offset or limit or window or range)"`，期望范围解析与错误测试全部通过。

## T7：实现 `read_file` 局部内容与元数据返回

**文件：** `src/julycode/tools/builtin.py`、`tests/test_tools.py`  
**依赖：** T6

**步骤：**

1. 使用 `splitlines(keepends=True)` 按行切取局部内容。
2. 返回 `start_line`、`end_line`、`total_lines` 和 `has_more`。
3. 局部内容继续应用 `ToolContext.max_output_chars`，正确区分字符截断和行范围后仍有内容。
4. 对空文件定义稳定的范围元数据。
5. 添加测试覆盖换行保留、末尾窗口、空文件、字符截断和 `has_more`。

**验证：** 运行 `python -m pytest tests/test_tools.py -q -k "read_file and (partial or metadata or has_more or empty)"`，期望局部读取结果测试全部通过。

## T8：保持 `read_file` 整文件与缓存兼容

**文件：** `src/julycode/tools/builtin.py`、`tests/test_tools.py`  
**依赖：** T7

**步骤：**

1. 未传 `offset/limit` 时保留现有整文件返回路径。
2. 保持 `path`、`content`、`truncated` 字段及字符截断语义。
3. 局部与整文件读取都继续复用 `FileReadCache`，文件变化后缓存自动失效。
4. 添加或扩展测试验证整文件结果不新增强制字段、缓存命中和修改后失效。
5. 运行现有上下文外置相关测试，确认大工具结果处理不回归。

**验证：** 运行 `python -m pytest tests/test_tools.py tests/test_tui_smoke.py -q -k "read_file or large_tool_result"`，期望读取兼容和大结果场景通过。

## T9：建立 `search_code` 统一候选范围

**文件：** `src/julycode/tools/builtin.py`、`tests/test_tools.py`  
**依赖：** T2、T3、T4

**步骤：**

1. 在搜索开始前编译用户正则；非法模式返回 `invalid_arguments`。
2. `path` 缺失、为空或解析后等于项目根时使用默认候选范围。
3. `path` 指向项目内非根文件或目录时使用显式候选范围。
4. 可选 `glob` 通过 `FileCatalog.matching_files()` 过滤候选。
5. 没有候选文件时立即成功返回空结果。
6. 添加测试覆盖全项目、指定目录、单文件、显式被忽略目标、glob 和非法正则。

**验证：** 运行 `python -m pytest tests/test_tools.py -q -k "search_code and (scope or path or glob or regex)"`，期望搜索范围与参数测试全部通过。

## T10：修复并加固 ripgrep 搜索后端

**文件：** `src/julycode/tools/builtin.py`、`tests/test_tools.py`  
**依赖：** T9

**步骤：**

1. ripgrep 命令加入 `--with-filename`。
2. 将候选文件按安全数量分批作为独立 argv 参数传入，不经过 Shell。
3. 正确解析含冒号文本的 `path:line:column:text` 输出。
4. 跨批次累计结果，达到 `max_results` 后停止执行后续批次。
5. 捕获程序不存在、启动失败、超时、非 0/1 返回码和解析异常，标记为需要 Python 回退。
6. 添加测试覆盖单文件路径、目录搜索、多批次、文本含冒号、最大结果数和后端异常。

**验证：** 运行 `python -m pytest tests/test_tools.py -q -k "search_code and (rg or ripgrep or single_file or batch)"`，期望 ripgrep 后端测试全部通过。

## T11：重构 Python 搜索回退

**文件：** `src/julycode/tools/builtin.py`、`tests/test_tools.py`  
**依赖：** T9、T10

**步骤：**

1. Python 后端只遍历 T9 生成的候选文件，不再调用 `rglob("*")`。
2. 逐文件、逐行搜索，跳过不可解码或读取失败的文件。
3. 达到 `max_results` 后立即返回。
4. ripgrep 不可用、启动失败、超时或返回错误时，从头用 Python 搜索同一候选集合。
5. 添加测试比较 ripgrep 与 Python 的命中结构、忽略语义和显式目标语义。
6. 添加包含大型 `.julycode` 伪运行文件的无 ripgrep 回归测试，验证不会读取该文件且能快速找到源码。

**验证：** 运行 `python -m pytest tests/test_tools.py -q -k "search_code and (fallback or without_rg or excluded or parity)"`，期望回退、一致性和大型排除目录测试全部通过。

## T12：验证项目沙箱与权限主体兼容

**文件：** `tests/test_permissions.py`、`src/julycode/permissions/sandbox.py`  
**依赖：** T7、T9

**步骤：**

1. 添加测试确认 `read_file` 带 `offset/limit` 时权限主体仍只有文件路径。
2. 添加测试确认 `search_code` 显式项目内文件和目录通过沙箱。
3. 保留并扩展项目外、父目录和符号链接越界拒绝测试。
4. 仅在测试暴露现有检查缺口时，对 `ProjectSandbox` 做最小修复。
5. 确认 `find_files` 的 glob 越界预检查仍生效。

**验证：** 运行 `python -m pytest tests/test_permissions.py -q -k "sandbox or subject_for or read_file or search_code or find_files"`，期望全部路径与权限测试通过。

## T13：强化模型工具选择规则

**文件：** `src/julycode/prompting/modules.py`、`tests/test_prompting.py`、`tests/test_tools.py`  
**依赖：** T5、T8、T11

**步骤：**

1. 更新稳定提示词，说明已知文件和局部行使用 `read_file`、未知文件名使用 `find_files`、文本或符号使用 `search_code`。
2. 明确代码定位和局部读取不得用 `run_command` 包装 `grep/find/sed/cat`。
3. 保留用户明确要求命令、专用工具无法表达、构建、测试和验证例外。
4. 更新四个工具的模型可见描述，与提示规则保持一致。
5. 添加测试断言专用工具规则、命令限制和局部读取参数会暴露给模型。

**验证：** 运行 `python -m pytest tests/test_prompting.py tests/test_tools.py -q -k "tool_rules or descriptions or operational_rules"`，期望提示词和工具描述测试通过。

## T14：新增专用代码定位离线评测

**文件：** `eval/cases/offline/code_location_reliability.json`、`eval/july_eval/provider.py`、`tests/test_eval_framework.py`  
**依赖：** T8、T11

**步骤：**

1. 创建包含多行源码的离线评测 workspace。
2. 用例要求先对指定单文件调用 `search_code`，再调用带 `offset/limit` 的 `read_file`。
3. 将 `run_command` 加入 `forbidden_tools`，并设置所需工具、成功数和最大调用数。
4. 在 `ScriptedEvalProvider` 中实现固定的两步工具调用与最终回复。
5. 添加测试断言工具序列、局部读取参数、最终回复和用例通过状态。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -q -k "code_location_reliability"`，期望新离线用例加载、执行和评分全部通过。

## T15：收紧现有只读定位评测

**文件：** `eval/cases/offline/readonly_search.json`、`eval/cases/offline/multi_tool_loop.json`、`eval/cases/online/default_online_cases.json`、`tests/test_eval_framework.py`  
**依赖：** T14

**步骤：**

1. 为不需要命令的离线只读用例添加 `run_command` 禁用要求。
2. 为在线代码阅读、符号定位、未知文件恢复和搜索后编辑前的定位阶段添加合适的 `run_command` 禁用要求。
3. 不修改用户明确要求命令验证的用例。
4. 添加测试确认关键定位用例都禁止 `run_command`，命令验证用例仍允许它。
5. 添加评分回归，确认调用禁用工具会导致 tool_use 维度失败。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -q -k "forbidden or online_cases or tool_use or readonly"`，期望评测约束测试全部通过。

## T16：更新用户文档

**文件：** `README.md`  
**依赖：** T13

**步骤：**

1. 更新 `read_file` 说明，记录 `offset` 为 1-based 起始行、`limit` 为最大行数。
2. 更新 `find_files/search_code` 说明，记录默认遵守 Git ignore 和固定排除目录。
3. 说明显式指定项目内非根目标时可以搜索被忽略目录。
4. 更新 `run_command` 说明，明确 argv 执行、不支持 `cd`、管道、重定向和复合 Shell 语法。
5. 确认文档未新增 `grep` 或 `glob` 工具别名。

**验证：** 运行 `python - <<'PY'\nfrom pathlib import Path\ntext = Path('README.md').read_text(encoding='utf-8')\nfor expected in ('offset', 'limit', 'Git ignore', 'run_command', '不支持管道'):\n    assert expected in text, expected\nprint('README ok')\nPY`，期望输出 `README ok`。

## T17：运行聚焦回归测试

**文件：** `src/julycode/tools/`、`src/julycode/prompting/`、`src/julycode/permissions/`、`tests/`、`eval/`  
**依赖：** T5、T8、T11、T12、T13、T15、T16

**步骤：**

1. 运行候选目录和工具单元测试。
2. 运行权限、提示词和评测框架测试。
3. 运行 Agent、工具调度、供应商适配和 TUI 相关测试。
4. 修复发现的兼容问题并重复运行，直到全部通过。

**验证：** 运行 `python -m pytest tests/test_tool_file_catalog.py tests/test_tools.py tests/test_permissions.py tests/test_prompting.py tests/test_eval_framework.py tests/test_agent.py tests/test_tool_scheduler.py tests/test_openai_provider.py tests/test_anthropic_provider.py tests/test_tui_smoke.py -q`，期望退出码为 0。

## T18：运行离线 Agent 评测

**文件：** `eval/run_eval.py`、`eval/cases/offline/`  
**依赖：** T17

**步骤：**

1. 运行新增 `code_location_reliability` 离线用例。
2. 运行 `readonly_search` 和 `multi_tool_loop` 用例。
3. 检查报告中的工具序列不包含 `run_command`。
4. 确认三个用例全部通过且没有工具失败。

**验证：** 运行 `python eval/run_eval.py --mode offline --case code_location_reliability --case readonly_search --case multi_tool_loop --output /tmp/julycode-code-location-eval --allow-review`，期望退出码为 0，报告显示三个用例通过。

## T19：运行全量测试和编译检查

**文件：** 全仓库  
**依赖：** T18

**步骤：**

1. 运行全部 pytest 测试。
2. 编译检查 `src`、`tests` 和 `eval`。
3. 运行 `git diff --check`。
4. 确认默认工具注册表仍只有既有六个核心工具，不包含 `grep/glob` 别名。

**验证：** 依次运行 `python -m pytest -q`、`python -m compileall -q src tests eval` 和 `git diff --check`，期望退出码均为 0。

## T20：执行 tmux 真实端到端验收

**文件：** `specs/code-location-reliability/checklist.md`  
**依赖：** T19

**步骤：**

1. 使用 tmux 启动真实 JulyCode。
2. 输入真实请求：“请定位 `SearchCodeTool` 的实现，读取其核心搜索逻辑附近的代码并说明如何回退；只做只读分析。”
3. 捕获 pane，观察模型是否使用 `search_code` 定位文件并使用带 `offset/limit` 的 `read_file` 查看局部内容。
4. 确认没有使用 `run_command` 执行 `grep/find/sed/cat`。
5. 确认最终回复引用正确文件与逻辑，输入区恢复可用。
6. 对照 `checklist.md` 逐项记录证据并清理 tmux 会话。

**验证：** 运行 `tmux capture-pane -p -t julycode-code-location`，期望输出中可观察到专用工具调用、正确最终回复和可用输入区；最后运行 `tmux kill-session -t julycode-code-location`。

## 执行顺序

```text
T1 → T2 ─┐
 │       ├→ T5 ───────────────┐
 ├→ T3 ─┤                     │
 └→ T4 ─┴→ T9 → T10 → T11 ───┼→ T13 → T16 ─┐
                              │              │
T6 → T7 → T8 ─────────────────┼→ T14 → T15 ─┼→ T17 → T18 → T19 → T20
                              │              │
                              └→ T12 ────────┘
```
