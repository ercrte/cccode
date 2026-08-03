# 代码定位工具可靠性 Plan

## 架构概览

本次改造在现有工具系统内增加一层共享的“项目文件候选目录”。`find_files` 和 `search_code` 不再分别直接对工作目录执行无约束 glob 或递归扫描，而是先通过候选目录得到安全、可控、遵守忽略规则的文件集合，再完成 glob 匹配或内容搜索。

候选目录区分默认项目范围和显式目标范围：

- 默认项目范围：Git 项目使用 Git 的 tracked + untracked-not-ignored 文件集合，并额外排除固定的元数据与运行目录；非 Git 项目使用带目录剪枝的文件系统遍历。
- 显式目标范围：`search_code.path` 指向项目内非根文件或目录时，直接遍历该目标，不应用默认忽略规则，但继续执行项目边界、文件类型和符号链接安全检查。
- `search_code.path` 缺失或等于项目根时视为默认项目范围，避免模型习惯性传入 `"."` 后意外扫描 `.julycode`。

`search_code` 保留“优先 ripgrep、无条件可回退”的双后端结构。两个后端共同使用同一候选文件集合和 glob 过滤结果，从而保证搜索范围与忽略语义一致。ripgrep 始终输出文件名，并按批次接收候选路径；不可用、启动失败、超时或不支持当前正则时，丢弃该次后端的临时结果并由 Python 后端重新搜索相同候选集。

`read_file` 保留现有整文件读取行为，并增加按行读取参数。局部读取采用 `offset` 和 `limit`：`offset` 表示从第几行开始，按 1 起算；`limit` 表示最多读取多少行。只有传入任一范围参数时才进入局部读取模式。

提示词、工具描述和评测用例同步强调专用只读工具的职责；纯代码定位场景将 `run_command` 设为禁用工具，避免只检查“是否调用过 search_code”而遗漏命令绕过。

## 核心数据结构

### `FileCatalog`

```python
class FileCatalog:
    def __init__(self, cwd: Path) -> None: ...

    def default_files(self) -> tuple[Path, ...]: ...

    def explicit_files(self, target: Path) -> tuple[Path, ...]: ...

    def matching_files(
        self,
        pattern: str,
        *,
        files: Sequence[Path] | None = None,
        base: Path | None = None,
        max_results: int | None = None,
    ) -> tuple[Path, ...]: ...
```

职责：

- 识别当前目录是否位于 Git Worktree。
- 默认范围返回排序稳定的项目内文件集合。
- 显式范围返回目标文件，或以目标目录为根的全部合法文件。
- 对所有候选执行项目边界检查，排除越界目标和目录符号链接循环。
- 使用与 `Path.glob` 一致的路径段语义匹配 glob：`*` 和 `?` 不跨目录，`**` 可匹配零个或多个目录。
- `max_results` 达到后立即停止追加匹配结果。

### `DEFAULT_SEARCH_EXCLUDED_DIRS`

```python
DEFAULT_SEARCH_EXCLUDED_DIRS: frozenset[str]
```

默认排除目录：

- 版本控制：`.git`、`.hg`、`.svn`
- JulyCode 运行数据：`.julycode`
- Python 环境与缓存：`__pycache__`、`.venv`、`venv`、`.tox`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`
- 依赖与构建产物：`node_modules`、`build`、`dist`、`target`

这些固定排除同时应用于 Git 和非 Git 的默认范围。显式非根目标不应用该集合。

### `ReadWindow`

```python
@dataclass(frozen=True)
class ReadWindow:
    start_line: int
    limit: int | None
```

由 `read_file` 参数解析得到：

- `offset` 缺失时 `start_line = 1`。
- `offset` 按 1 起算，必须大于 0。
- `limit` 必须大于 0。
- `offset` 或 `limit` 任一出现即为局部读取。
- 非空文件中，起始行超过总行数时返回 `invalid_arguments`。

### 局部读取结果

```json
{
  "path": "src/app.py",
  "content": "...",
  "truncated": false,
  "start_line": 120,
  "end_line": 159,
  "total_lines": 480,
  "has_more": true
}
```

- 保留既有 `path`、`content`、`truncated` 字段。
- `start_line` 和 `end_line` 表示实际返回内容覆盖的行范围。
- `total_lines` 表示文件总行数。
- `has_more` 表示请求范围或字符输出上限之后仍有未返回内容。
- 未提供 `offset/limit` 时仍走现有整文件读取与字符截断路径，保持既有结果语义。

### `CodeSearchMatch`

内部后端统一生成以下字典结构，不增加新的公开类型：

```python
{
    "path": str,
    "line": int,
    "column": int,
    "text": str,
}
```

ripgrep 和 Python 后端都必须返回该结构；公开响应继续使用 `{"matches": [...], "count": N}`。

## 模块设计

### `julycode.tools.file_catalog`

**职责：**

- 提供 `FileCatalog`、默认排除目录和 glob 匹配。
- 默认 Git 范围通过无 Shell 的参数数组调用：

```text
git -C <worktree-root> ls-files -co --exclude-standard -z
```

- 当 JulyCode 从 Worktree 子目录启动时，只保留当前 `cwd` 下的文件，并把路径转换为相对 `cwd` 的路径。
- Git 命令不可用或无法识别 Worktree 时，退回非 Git 的剪枝遍历；固定排除目录仍然生效。
- 非 Git 遍历使用 `os.scandir`，在进入目录前完成排除判断，不先构造完整递归列表。
- 显式目标遍历不应用默认排除，但不跟随目录符号链接；文件解析后的真实路径必须位于项目根内。

**对外接口：**

- `FileCatalog.default_files()`
- `FileCatalog.explicit_files(target)`
- `FileCatalog.matching_files(...)`
- `DEFAULT_SEARCH_EXCLUDED_DIRS`

**依赖：**

- Python 标准库 `pathlib`、`os`、`subprocess`、`re`
- 不依赖仓库地图私有方法，避免工具行为与仓库地图扫描格式耦合

**需求归属：** F1、F2、F3、F4、F6、F9，N1、N2、N3。

### `julycode.tools.builtin`

**职责：**

1. `FindFilesTool`
   - 使用 `FileCatalog.default_files()` 获取候选。
   - 使用 `matching_files()` 完成 glob 匹配。
   - 保留现有 `pattern`、`max_results` 参数和返回格式。

2. `SearchCodeTool`
   - 根据 `path` 决定默认范围或显式范围。
   - 在进入后端前使用 `FileCatalog.matching_files()` 应用可选 `glob`。
   - 在执行 ripgrep 前先用 Python 编译正则；非法正则直接返回 `invalid_arguments`。
   - ripgrep 命令固定包含 `--with-filename`，解决单文件输出缺失路径的问题。
   - 按有限路径批次调用 ripgrep，避免命令参数超过系统限制；每批解析后累计结果，达到 `max_results` 即停止。
   - ripgrep 不可用、启动失败、超时、返回不可用结果或不支持当前正则时，使用 Python 后端重新搜索同一候选集合。
   - Python 后端逐文件、逐行搜索并在达到 `max_results` 时立即返回，不使用 `rglob("*")`。

3. `ReadFileTool`
   - Schema 增加可选整数 `offset` 和 `limit`。
   - 继续通过现有 `FileReadCache` 获取完整文本，保证缓存失效语义不变。
   - 局部读取使用 `splitlines(keepends=True)` 保留原始换行。
   - 范围参数错误统一抛出 `ToolExecutionError(..., error_type="invalid_arguments")`。
   - 局部内容仍受 `ToolContext.max_output_chars` 限制，并据此设置 `truncated` 和 `has_more`。

4. 工具描述
   - `read_file` 明确说明 `offset/limit` 用于局部查看。
   - `find_files` 和 `search_code` 明确说明默认遵守项目忽略规则。
   - `run_command` 明确说明它不经过 Shell，不支持 `cd`、管道和重定向，不应用于替代文件查找、代码搜索和局部读取。

**对外接口：**

- 六个既有工具名称保持不变。
- 三个只读工具的既有必填参数与结果顶层结构保持兼容。

**依赖：**

- `julycode.tools.file_catalog`
- 现有 `ToolContext`、`ToolExecutionError`、`ToolSpec`

**需求归属：** F1、F5、F6、F7、F8、F9，N2、N4。

### `julycode.prompting.modules`

**职责：**

- 明确只读定位顺序：
  - 已知文件与局部行：`read_file`
  - 不知道文件名：`find_files`
  - 符号、配置、文本：`search_code`
- 明确不得为了代码定位用 `run_command` 包装 `grep`、`find`、`sed` 或 `cat`。
- 保留以下例外：用户明确要求运行命令、专用工具无法表达、构建、测试和验证。

**需求归属：** F10。

### `julycode.permissions.sandbox`

**职责：**

- 保持现有路径沙箱规则。
- 确认 `read_file` 新增的范围参数不改变权限主体，权限匹配仍只基于文件路径。
- 确认 `search_code` 显式项目内目标可用，项目外目标继续在执行前拒绝。

本模块原则上不新增公开接口；只有测试发现现有路径检查无法覆盖新调用方式时才做最小调整。

**需求归属：** N3、N4。

### `julycode` 自动评测

**职责：**

- 现有只读搜索用例增加 `run_command` 禁用要求。
- 新增确定性离线用例：
  1. 先对单文件执行 `search_code`。
  2. 再使用 `read_file(offset, limit)` 读取命中附近内容。
  3. 最终回复引用正确符号与局部内容。
  4. 全程禁止 `run_command`。
- Scripted Provider 增加该用例的固定工具调用序列。
- 在线代码定位用例将 `run_command` 标记为禁用工具，工具使用评分继续复用现有 `forbidden_tools` 机制，不新增评测配置字段。

无 ripgrep、忽略目录、Git tracked/untracked、最大结果数和回退一致性属于工具执行环境能力，由 pytest 回归测试覆盖；Agent 是否选择专用工具由离线和在线评测覆盖。

**需求归属：** F11、N5。

### `README.md`

**职责：**

- 更新六个核心工具说明。
- 记录 `read_file` 的 `offset/limit` 局部读取能力。
- 说明 `find_files/search_code` 默认应用忽略规则。
- 说明 `run_command` 为 argv 执行而非 Shell，不支持复合 Shell 语法。

**需求归属：** F7、F10。

## 模块交互

### 默认代码搜索

```text
SearchCodeTool.execute
  → 解析 pattern / path / glob / max_results
  → FileCatalog.default_files
      → Git Worktree: git ls-files -co --exclude-standard
      → 非 Git 或 Git 不可用: os.scandir 剪枝遍历
  → FileCatalog.matching_files(glob)
  → RipgrepBackend.search(candidate_files)
      → 成功: 解析并返回 matches
      → 不可用/失败/超时: PythonBackend.search(candidate_files)
  → 返回 {"matches": ..., "count": ...}
```

### 显式目标搜索

```text
ProjectSandbox 检查 search_code.path
  → SearchCodeTool 判断 target != cwd
  → FileCatalog.explicit_files(target)
  → 可选 glob 过滤
  → ripgrep / Python 搜索
  → 返回项目相对路径
```

### 文件查找

```text
FindFilesTool.execute
  → ProjectSandbox 预检查 glob
  → FileCatalog.default_files
  → segment-aware glob 匹配
  → 达到 max_results 后停止
  → 返回 {"matches": ..., "count": ...}
```

### 局部文件读取

```text
ProjectSandbox 检查 read_file.path
  → ReadFileTool 从缓存或磁盘取得完整文本
  → 解析 offset / limit
  → 未传范围: 现有整文件截断路径
  → 传入范围: 行切片 + 字符上限
  → 返回内容、实际行范围、总行数和 has_more
```

### 工具选择评测

```text
评测用例声明 required_tools + forbidden_tools=["run_command"]
  → Agent Loop 执行真实工具
  → scoring 检查工具调用名称和成功结果
  → 使用命令绕过专用工具时用例失败
```

## 文件组织

```text
src/julycode/tools/
├── builtin.py                       — 接入候选目录、搜索回退和局部读取
└── file_catalog.py                  — 新增项目文件枚举、忽略与 glob 匹配

src/julycode/prompting/
└── modules.py                       — 强化专用工具优先规则

src/julycode/permissions/
└── sandbox.py                       — 保持并验证路径边界语义

tests/
├── test_tool_file_catalog.py        — 新增 Git/非 Git 候选与 glob 单元测试
├── test_tools.py                    — 搜索后端、单文件、回退、局部读取测试
├── test_permissions.py              — 新参数和显式路径沙箱回归测试
├── test_prompting.py                — 工具选择提示回归测试
└── test_eval_framework.py           — 新离线用例与禁用命令评分测试

eval/
├── cases/offline/
│   ├── code_location_reliability.json — 新增专用工具定位用例
│   ├── readonly_search.json           — 禁止 run_command
│   └── multi_tool_loop.json           — 禁止 run_command
├── cases/online/
│   └── default_online_cases.json      — 只读定位用例禁止 run_command
└── july_eval/
    └── provider.py                    — 新用例的确定性工具调用序列

README.md                            — 更新工具能力与命令限制
specs/code-location-reliability/
├── spec.md
├── plan.md
├── task.md
└── checklist.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 工具命名 | 保留 `read_file/find_files/search_code/run_command` | 避免重复能力和权限规则迁移，先解决可靠性根因 |
| 默认 Git 文件集合 | `git ls-files -co --exclude-standard -z` | 直接复用 Git 有效忽略规则，同时包含 tracked 和未跟踪未忽略文件 |
| 默认固定排除 | Git 和非 Git 都排除 `.julycode`、VCS、缓存、依赖和构建目录 | 防止运行数据或生成物体积增长拖垮搜索 |
| 显式目标语义 | 非根显式目标绕过默认忽略，项目根 `"."` 仍走默认范围 | 兼顾访问被忽略目标的能力与常用根搜索的安全性能 |
| ripgrep 使用方式 | 对候选路径分批搜索，并强制 `--with-filename` | 保证与 Python 回退使用相同候选集，修复单文件解析并避免参数过长 |
| 回退策略 | ripgrep 任一不可用条件下重新用 Python 搜索同一候选集 | 不把外部程序可用性暴露给模型，保持范围和结果结构稳定 |
| Python 搜索 | 不再 `rglob("*")`，仅扫描候选文件并尽早停止 | 从根本上避免 `.julycode` 全量扫描和超时 |
| glob 实现 | 路径段感知的内部 matcher | `PurePath.match` 对根级 `*.py` 语义与现有 `Path.glob` 不一致，不能直接替换 |
| 局部读取参数 | `offset`（1-based）+ `limit`（行数） | 参数数量少，符合模型常见读取工具习惯，并可表达任意连续行窗口 |
| 整文件兼容 | 无范围参数时保留现有完整读取与字符截断 | 避免影响既有 Agent、缓存和上下文外置行为 |
| 评测约束 | 复用 `forbidden_tools=["run_command"]` | 现有评分器已经支持，无需扩展评测数据模型 |
| 仓库地图关系 | 不复用其私有扫描函数，也不修改其规则 | 工具需要所有文本文件和显式目标语义，仓库地图只面向 Python 导航，职责不同 |
