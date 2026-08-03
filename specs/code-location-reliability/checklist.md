# 代码定位工具可靠性 Checklist

> 每一项必须通过运行命令或观察真实行为验证。开发完成后将 `[ ]` 更新为 `[x]`，并在最终验收报告中记录实际输出或 tmux 证据。

## 项目文件候选范围

- [x] C01 非 Git 项目的默认候选范围包含普通根级和嵌套文件，并排除 `.git`、`.hg`、`.svn`、`.julycode`、`__pycache__`、`.venv`、`venv`、`.tox`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`、`node_modules`、`build`、`dist` 和 `target`。（验证：运行 `python -m pytest tests/test_tool_file_catalog.py -q -k "non_git or excludes"`，期望全部通过；覆盖 AC3）
- [x] C02 默认候选遍历不跟随目录符号链接，也不返回真实路径位于项目外的文件。（验证：运行 `python -m pytest tests/test_tool_file_catalog.py -q -k "symlink or outside"`，期望越界与链接测试通过；覆盖 AC3、AC10）
- [x] C03 Git 项目的默认候选范围包含 tracked 文件和未跟踪未忽略文件，并排除 Git ignore 命中的文件。（验证：运行 `python -m pytest tests/test_tool_file_catalog.py -q -k "git and (tracked or untracked or ignored)"`，期望全部通过；覆盖 AC2）
- [x] C04 从 Git Worktree 子目录启动时，候选范围只包含启动目录下的文件，结果路径相对启动目录且使用 POSIX 格式。（验证：运行 `python -m pytest tests/test_tool_file_catalog.py -q -k "worktree and subdirectory"`，期望路径范围与格式测试通过；覆盖 AC2）
- [x] C05 Git 命令不可用、不是仓库或执行失败时，候选目录会退回文件系统剪枝遍历，固定排除目录仍然生效。（验证：运行 `python -m pytest tests/test_tool_file_catalog.py -q -k "git and fallback"`，期望回退测试通过；覆盖 AC1、AC3）
- [x] C06 显式指定项目内文件或非根目录时可以访问默认被忽略的目标；显式指定项目根或 `"."` 时仍使用安全的默认范围。（验证：运行 `python -m pytest tests/test_tool_file_catalog.py tests/test_tools.py -q -k "explicit and ignored or root_scope"`，期望显式目标与根范围测试通过；覆盖 AC2）

## Glob 与文件查找

- [x] C07 glob 匹配遵守路径段语义：`*.py` 只匹配根级，`**/*.py` 同时匹配根级和嵌套文件，`src/**/*.py` 同时匹配直属和深层文件，字符集合与 `?` 不跨目录。（验证：运行 `python -m pytest tests/test_tool_file_catalog.py -q -k "glob"`，期望参数化 glob 测试通过；覆盖 AC5）
- [x] C08 `find_files` 使用共享默认候选范围，Git ignore、固定排除、稳定排序、空结果和相对路径行为正确。（验证：运行 `python -m pytest tests/test_tools.py tests/test_tool_file_catalog.py -q -k "find_files"`，期望全部通过；覆盖 AC2、AC3）
- [x] C09 `find_files` 达到 `max_results` 后只返回规定数量并停止继续收集结果。（验证：运行 `python -m pytest tests/test_tool_file_catalog.py tests/test_tools.py -q -k "find_files and max_results"`，期望提前停止测试通过；覆盖 AC5）

## 代码搜索

- [x] C10 `search_code` 支持全项目、指定目录和指定单文件三种范围，命中包含正确项目相对路径、行号、列号和匹配文本。（验证：运行 `python -m pytest tests/test_tools.py -q -k "search_code and (scope or directory or single_file)"`，期望全部通过；覆盖 AC4）
- [x] C11 对指定单文件搜索时 ripgrep 始终返回文件名，含冒号的匹配文本不会破坏解析；无命中时成功返回空列表。（验证：运行 `python -m pytest tests/test_tools.py -q -k "search_code and (single_file or colon or empty)"`，期望全部通过；覆盖 AC4）
- [x] C12 `search_code.glob` 使用与 `find_files` 一致的路径段匹配，只搜索过滤后的候选文件。（验证：运行 `python -m pytest tests/test_tools.py tests/test_tool_file_catalog.py -q -k "search_code and glob"`，期望搜索过滤测试通过；覆盖 AC4、AC5）
- [x] C13 ripgrep 按有限候选批次执行，跨批次累计结果并在达到 `max_results` 后停止后续批次。（验证：运行 `python -m pytest tests/test_tools.py -q -k "search_code and (batch or max_results)"`，期望批次和提前停止测试通过；覆盖 AC5）
- [x] C14 ripgrep 不可用、启动失败、超时、返回异常状态或输出不可解析时，Python 后端会从头搜索同一候选集合，公开结果结构保持一致。（验证：运行 `python -m pytest tests/test_tools.py -q -k "search_code and (fallback or without_rg or timeout or parity)"`，期望回退与一致性测试通过；覆盖 AC8）
- [x] C15 无 ripgrep 环境下，包含大型 `.julycode` 伪运行数据的项目仍能在工具超时内找到源码命中，并且测试能证明运行数据文件未被读取。（验证：运行 `python -m pytest tests/test_tools.py -q -k "search_code and large_excluded_runtime"`，期望快速通过且无运行目录读取；覆盖 AC1）
- [x] C16 ripgrep 与 Python 后端对 Git ignore、固定排除和显式被忽略目标使用相同范围语义。（验证：运行 `python -m pytest tests/test_tools.py -q -k "search_code and (parity or ignored_scope)"`，期望后端一致性测试通过；覆盖 AC2、AC3、AC8）
- [x] C17 非法正则返回 `invalid_arguments`；候选为空、单文件无命中或排除目录中存在命中时均成功返回空结果，而不是工具异常。（验证：运行 `python -m pytest tests/test_tools.py -q -k "search_code and (invalid_regex or no_candidates or empty)"`，期望错误与空结果测试通过；覆盖 AC4、AC8）

## 文件局部读取

- [x] C18 `read_file` 接受 1-based `offset` 和正整数 `limit`，支持只传其中一个或同时传入；零值、负值和超出非空文件末尾的起始行返回 `invalid_arguments`。（验证：运行 `python -m pytest tests/test_tools.py -q -k "read_file and (offset or limit or range)"`，期望范围参数测试通过；覆盖 AC6）
- [x] C19 合法局部读取只返回指定窗口，并返回准确的 `start_line`、`end_line`、`total_lines` 和 `has_more`。（验证：运行 `python -m pytest tests/test_tools.py -q -k "read_file and (partial or metadata or has_more)"`，期望局部内容和元数据测试通过；覆盖 AC6）
- [x] C20 局部读取保留原始换行，空文件、文件末尾窗口和字符输出上限截断均有稳定结果，`truncated` 与 `has_more` 能反映未返回内容。（验证：运行 `python -m pytest tests/test_tools.py -q -k "read_file and (newline or empty or truncated)"`，期望边界测试通过；覆盖 AC6）
- [x] C21 未传 `offset/limit` 时，`read_file` 保持现有 `path/content/truncated` 结果、整文件字符截断和文件缓存失效语义。（验证：运行 `python -m pytest tests/test_tools.py -q -k "read_file and (returns_content or cache or full)"`，期望既有兼容测试通过；覆盖 AC7）
- [x] C22 大型文件的整文件工具结果仍能由上下文系统外置或压缩，Agent Loop 和 TUI 可继续完成请求。（验证：运行 `python -m pytest tests/test_tui_smoke.py tests/test_context_manager.py -q -k "large_tool_result or externalized"`，期望大结果集成测试通过；覆盖 AC7、AC10）

## 权限、安全与公开接口

- [x] C23 `read_file` 带范围参数时权限主体仍只由目标路径决定，不产生新的授权目标。（验证：运行 `python -m pytest tests/test_permissions.py -q -k "read_file and subject_for"`，期望权限主体测试通过；覆盖 AC10）
- [x] C24 `search_code` 显式项目内文件和目录可执行，项目外路径、父目录遍历和越界符号链接继续在执行前被拒绝。（验证：运行 `python -m pytest tests/test_permissions.py tests/test_tool_file_catalog.py -q -k "search_code and (sandbox or outside or symlink)"`，期望边界测试通过；覆盖 AC10）
- [x] C25 `find_files` 绝对 glob、包含 `..` 的 glob 和越界符号链接匹配继续被拒绝。（验证：运行 `python -m pytest tests/test_permissions.py -q -k "find_files and sandbox"`，期望越界测试通过；覆盖 AC10）
- [x] C26 默认注册表和模型可见工具仍是既有六个核心名称，不出现名为 `grep` 或 `glob` 的工具。（验证：运行 `python -m pytest tests/test_tools.py -q -k "default_registry"`，并运行 `python - <<'PY'\nfrom julycode.tools.registry import create_default_registry\nnames = {spec.name for spec in create_default_registry().specs()}\nassert names == {'read_file', 'write_file', 'edit_file', 'run_command', 'find_files', 'search_code'}\nprint(sorted(names))\nPY`；覆盖 AC12）

## 提示词、工具说明与文档

- [x] C27 稳定提示词明确：已知文件与局部行使用 `read_file`，未知文件名使用 `find_files`，代码或文本使用 `search_code`。（验证：运行 `python -m pytest tests/test_prompting.py -q -k "tool_rules"`，期望专用工具职责测试通过；覆盖 AC9）
- [x] C28 提示词和 `run_command` 描述明确禁止用命令包装 `grep/find/sed/cat` 替代代码定位，同时保留用户明确要求、构建、测试和验证例外。（验证：运行 `python -m pytest tests/test_prompting.py tests/test_tools.py -q -k "tool_rules or operational_rules"`，期望命令边界测试通过；覆盖 AC9）
- [x] C29 `read_file` Schema 和描述向模型暴露 `offset/limit`；`find_files/search_code` 描述说明默认忽略语义。（验证：运行 `python -m pytest tests/test_tools.py -q -k "descriptions or schema"`，期望工具规格测试通过；覆盖 AC6、AC9）
- [x] C30 README 说明局部读取参数、Git ignore 和固定排除规则、显式目标例外，以及 `run_command` 不支持 `cd`、管道、重定向和复合 Shell 语法。（验证：运行 `python - <<'PY'\nfrom pathlib import Path\ntext = Path('README.md').read_text(encoding='utf-8')\nfor expected in ('offset', 'limit', 'Git ignore', '显式', 'run_command', '不支持管道'):\n    assert expected in text, expected\nprint('README ok')\nPY`，期望输出 `README ok`）

## 自动评测

- [x] C31 新离线用例按 `search_code → read_file(offset, limit)` 的顺序完成单文件定位与局部读取，最终回复引用正确符号，且不调用 `run_command`。（验证：运行 `python -m pytest tests/test_eval_framework.py -q -k "code_location_reliability"`，期望用例加载、工具参数、结果和评分测试通过；覆盖 AC9）
- [x] C32 `readonly_search`、`multi_tool_loop` 和不需要命令的在线代码定位用例将 `run_command` 列为禁用工具；明确要求命令验证的用例仍允许使用它。（验证：运行 `python -m pytest tests/test_eval_framework.py -q -k "forbidden or online_cases or readonly"`，期望约束分类测试通过；覆盖 AC9）
- [x] C33 工具评分在调用 `forbidden_tools` 中的 `run_command` 时失败，即使专用工具也曾被调用也不能通过。（验证：运行 `python -m pytest tests/test_eval_framework.py -q -k "tool_use and forbidden"`，期望评分回归测试通过；覆盖 AC9）
- [x] C34 新增和收紧后的三个离线用例真实经过 Agent Loop 全部通过，报告中的工具序列不包含 `run_command`。（验证：运行 `python eval/run_eval.py --mode offline --case code_location_reliability --case readonly_search --case multi_tool_loop --output /tmp/julycode-code-location-eval --allow-review`，期望退出码为 0，报告显示三项通过；覆盖 AC9）

## 集成、编译与回归

- [x] C35 候选目录、工具、权限、提示词和评测专项测试全部通过。（验证：运行 `python -m pytest tests/test_tool_file_catalog.py tests/test_tools.py tests/test_permissions.py tests/test_prompting.py tests/test_eval_framework.py -q`，期望退出码为 0；覆盖 AC10）
- [x] C36 Agent、工具调度、OpenAI、Anthropic 和 TUI 相关测试全部通过，工具结果结构与供应商序列化无回归。（验证：运行 `python -m pytest tests/test_agent.py tests/test_tool_scheduler.py tests/test_openai_provider.py tests/test_anthropic_provider.py tests/test_tui_smoke.py -q`，期望退出码为 0；覆盖 AC10）
- [x] C37 全量 pytest 测试通过。（验证：运行 `python -m pytest -q`，期望退出码为 0；覆盖 AC10）
- [x] C38 `src`、`tests` 和 `eval` 编译无错误，补丁不存在尾随空白或格式错误。（验证：运行 `python -m compileall -q src tests eval` 和 `git diff --check`，期望退出码均为 0；覆盖 AC10）
- [x] C39 本功能未新增运行时依赖，也未修改仓库地图的候选文件与忽略规则。（验证：运行 `git diff -- requirements.txt pyproject.toml src/julycode/repo_map`，期望没有本功能相关依赖或仓库地图改动；覆盖 AC10）

## tmux 真实端到端场景

- [x] C40 在当前仓库用 tmux 启动真实 JulyCode，输入“请定位 `SearchCodeTool` 的实现，读取其核心搜索逻辑附近的代码并说明如何回退；只做只读分析。”，界面显示 `search_code` 成功定位源码。（验证：运行 `tmux new-session -d -s julycode-code-location 'julycode'`，发送请求后用 `tmux capture-pane -p -S -400 -t julycode-code-location` 捕获输出；覆盖 AC11）
- [x] C41 同一真实请求中模型使用带 `offset/limit` 的 `read_file` 查看局部实现，没有调用 `run_command` 执行 `grep`、`find`、`sed` 或 `cat`。（验证：检查 tmux 工具状态和当前会话 JSONL 中的工具调用参数；覆盖 AC11）
- [x] C42 真实请求最终回复正确说明候选文件范围、ripgrep 回退或 Python 搜索逻辑，并引用实际源码位置，输入区恢复可用。（验证：检查 tmux 最终回复、状态栏完成状态和 Composer 可输入状态；覆盖 AC11）
- [x] C43 tmux 验收完成后会话被清理，项目工作树只保留本功能计划内改动。（验证：运行 `tmux kill-session -t julycode-code-location`，再运行 `git status --short` 与验收前基线比较）

## 实际验收记录

- 聚焦回归：`333 passed in 10.68s`。
- 最终全量回归：`909 passed in 26.73s`。
- 离线 Agent 评测：3 个用例全部通过，平均分 `100.00`；工具序列分别为 `search_code → read_file`、`search_code → read_file`、`read_file`。
- 编译与补丁检查：`python -m compileall -q src tests eval`、`git diff --check` 均以退出码 0 完成。
- tmux 严格按原始提示执行的空会话：`.julycode/sessions/20260716-074743-f387.jsonl` 记录 `search_code(pattern="class SearchCodeTool")`、`find_files(pattern="**/*search*code*tool*")`，随后两次 `read_file` 分别使用 `offset=351, limit=120` 和 `offset=470, limit=50`；四个工具结果均成功，未出现 `run_command`。
- tmux 最终界面状态为“空闲”，输入区已恢复；回复说明 ripgrep 不可用、子进程异常、异常退出码或输出解析失败时回退到 Python 搜索，并补充引用实际路径 `src/julycode/tools/builtin.py:351-514`。
- 为消除 Repo Map 已知位置对工具选择的影响，验收启动前临时使用 `repo_map: false`，进程读取配置后立即删除该临时文件；tmux 会话已清理，最终工作树没有遗留 `.julycode.yaml`。

## 验收标准追踪

| Spec 验收标准 | Checklist 条目 |
| --- | --- |
| AC1 | C05、C14-C16 |
| AC2 | C03-C04、C06、C08、C16 |
| AC3 | C01-C02、C05、C08、C16 |
| AC4 | C10-C12、C17 |
| AC5 | C07、C09、C12-C13 |
| AC6 | C18-C20、C29 |
| AC7 | C21-C22 |
| AC8 | C14、C16-C17 |
| AC9 | C27-C29、C31-C34 |
| AC10 | C02、C22-C26、C35-C39 |
| AC11 | C40-C42 |
| AC12 | C26 |
