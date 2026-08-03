# JulyCode Repo Map Tasks

## 执行约束

- 四份 Spec 文档全部批准前不得开始执行本文件中的实现任务。
- 每个任务按“先补测试或可观测断言，再实现，再运行验证”的顺序完成。
- 每个任务验证通过后才能进入依赖它的任务；失败时在当前任务修复，不把失败留给后续任务。
- 只实现 `spec.md` 已批准范围；不引入 Tree-sitter、networkx、Embedding、磁盘缓存或子 Agent 专属地图。
- Python 注释、用户可见状态和文档使用中文。

## 文件清单

### 新建

| 操作 | 文件 | 职责 |
| --- | --- | --- |
| 新建 | `src/julycode/repo_map/__init__.py` | 导出 Repo Map 稳定公共接口 |
| 新建 | `src/julycode/repo_map/models.py` | 仓库、解析、关系图、快照和状态数据模型 |
| 新建 | `src/julycode/repo_map/discovery.py` | 根目录、文件发现、越界防护、内容指纹和 revision |
| 新建 | `src/julycode/repo_map/parser.py` | Python 编码、AST 符号、导入、引用和安全签名解析 |
| 新建 | `src/julycode/repo_map/graph.py` | 关系解析和确定性 PageRank-like 图重要度 |
| 新建 | `src/julycode/repo_map/ranking.py` | 请求提示提取、评分和稳定排序 |
| 新建 | `src/julycode/repo_map/renderer.py` | 原子条目、预算裁剪、边界与地图文本 |
| 新建 | `src/julycode/repo_map/manager.py` | 后台索引、LRU 缓存、turn、revision 刷新和状态 |
| 新建 | `tests/test_repo_map_discovery.py` | 仓库发现、路径安全、指纹与身份测试 |
| 新建 | `tests/test_repo_map_parser.py` | Python 解析与不可信内容清理测试 |
| 新建 | `tests/test_repo_map_graph.py` | 关系、图分数、请求排序与确定性测试 |
| 新建 | `tests/test_repo_map_renderer.py` | 原子渲染、预算与 golden 测试 |
| 新建 | `tests/test_repo_map_manager.py` | 缓存、后台生命周期、revision、性能与降级测试 |
| 新建 | `tests/test_repo_map_integration.py` | Stub Agent Loop、历史隔离和主 Agent 集成测试 |
| 新建 | `tests/fixtures/repo_map_golden.txt` | 关键地图的字节精确预期输出 |
| 新建 | `eval/repo_map_quality/__init__.py` | 导出 Repo Map 质量评测接口 |
| 新建 | `eval/repo_map_quality/models.py` | 评测 case/result 数据模型 |
| 新建 | `eval/repo_map_quality/loader.py` | 数据集读取与校验 |
| 新建 | `eval/repo_map_quality/runner.py` | Top-K 与 enabled/disabled 成对评测 |
| 新建 | `eval/repo_map_quality/report.py` | JSON 和 Markdown 报告生成 |
| 新建 | `eval/run_repo_map_eval.py` | Repo Map 质量评测 CLI |
| 新建 | `eval/cases/repo_map_quality/navigation.json` | 不直接暴露目标路径的导航任务集 |

### 修改

| 操作 | 文件 | 职责 |
| --- | --- | --- |
| 修改 | `src/julycode/config.py` | `RepoMapConfig`、默认值、合并与校验 |
| 修改 | `src/julycode/prompting/base.py` | `GeneratedContextBlock` 和 PromptBundle 扩展 |
| 修改 | `src/julycode/prompting/__init__.py` | 导出新增 Prompt 类型 |
| 修改 | `src/julycode/context/estimator.py` | 文本和生成上下文估算入口 |
| 修改 | `src/julycode/context/manager.py` | 低优先级上下文预算授权和最终复核 |
| 修改 | `src/julycode/tools/scheduler.py` | 通用执行观察者和统一工具执行包装 |
| 修改 | `src/julycode/agent.py` | RepoMapTurn、请求期生成块和工具刷新接入 |
| 修改 | `src/julycode/providers/openai.py` | Repo Map 序列化与长期缓存身份隔离 |
| 修改 | `src/julycode/providers/anthropic.py` | Repo Map system block 与次级缓存断点 |
| 修改 | `src/julycode/tui/app.py` | RepoMapManager 生命周期、主 Agent 注入和状态快照 |
| 修改 | `src/julycode/commands/models.py` | Repo Map 运行状态字段 |
| 修改 | `src/julycode/commands/builtin.py` | `/status` 的 Repo Map 状态输出 |
| 修改 | `README.md` | Repo Map 配置、行为和安全边界说明 |
| 修改 | `eval/README.md` | 质量评测运行与指标说明 |
| 修改 | `tests/test_config.py` | 默认启用、关闭、预算和配置覆盖测试 |
| 修改 | `tests/test_prompting.py` | 生成上下文元数据和 PromptBundle 测试 |
| 修改 | `tests/test_context_estimator.py` | 生成块 Token 估算测试 |
| 修改 | `tests/test_context_manager.py` | 低优先级授权、缩小、省略和超限回归测试 |
| 修改 | `tests/test_tool_scheduler.py` | observer 调用边界、失败和拒绝测试 |
| 修改 | `tests/test_agent.py` | turn 生命周期与兼容路径测试 |
| 修改 | `tests/test_openai_provider.py` | OpenAI 序列化、key 和稳定前缀 golden |
| 修改 | `tests/test_anthropic_provider.py` | Anthropic 两级断点和稳定前缀 golden |
| 修改 | `tests/test_session.py` | Repo Map 不进入消息历史测试 |
| 修改 | `tests/test_session_recovery.py` | Repo Map 不进入持久化和恢复测试 |
| 修改 | `tests/test_memory_updater.py` | Repo Map 不进入长期记忆测试 |
| 修改 | `tests/test_commands.py` | `/status` Repo Map 输出测试 |
| 修改 | `tests/test_tui_smoke.py` | 后台生命周期、主 Agent 限定和 TUI 回归测试 |
| 修改 | `tests/test_eval_framework.py` | Repo Map 质量评测 CLI/报告冒烟测试 |

## T1：建立 Repo Map 核心数据模型

**文件：** `src/julycode/repo_map/models.py`、`src/julycode/repo_map/__init__.py`、`tests/test_repo_map_manager.py`
**依赖：** 无

**步骤：**
1. 定义 Plan 中的冻结 dataclass、枚举和诊断类型。
2. 为路径、tuple 集合和可选状态字段设置不可变且可比较的表示。
3. 只从包入口导出后续模块需要的稳定类型。

**验证：** 运行 `python -m pytest tests/test_repo_map_manager.py -q`，期望数据模型构造、相等性和冻结行为测试通过。

## T2：增加 Repo Map 配置

**文件：** `src/julycode/config.py`、`tests/test_config.py`
**依赖：** T1

**步骤：**
1. 增加 `RepoMapConfig(enabled=True, max_tokens=2000)` 并接入 AppConfig。
2. 接入用户配置与项目配置的现有合并流程。
3. 校验启用时预算必须为正整数，关闭时允许保留预算值。

**验证：** 运行 `python -m pytest tests/test_config.py -q`，期望默认启用、显式关闭、2000 默认预算、覆盖和非法值测试通过。

## T3：实现 Git Worktree 根目录与身份识别

**文件：** `src/julycode/repo_map/discovery.py`、`tests/test_repo_map_discovery.py`
**依赖：** T1

**步骤：**
1. 用无 shell 的 Git 参数调用识别 Worktree 根、Git dir、common dir、HEAD 和 symbolic ref。
2. 处理 detached HEAD、unborn branch 和位于仓库子目录启动的情况。
3. 生成隔离项目、分支与 Worktree 的 `RepositoryIdentity`。

**验证：** 运行 `python -m pytest tests/test_repo_map_discovery.py -q`，期望 Git 根目录和身份场景测试通过。

## T4：实现 Git 候选文件集合

**文件：** `src/julycode/repo_map/discovery.py`、`tests/test_repo_map_discovery.py`
**依赖：** T3

**步骤：**
1. 使用 `git ls-files -co --exclude-standard -z` 获取 `.py`、`.pyi`。
2. 包含 tracked 与未忽略 untracked，排除有效 ignore 命中的文件。
3. 对 NUL 分隔结果去重并按 POSIX 相对路径排序。

**验证：** 运行 `python -m pytest tests/test_repo_map_discovery.py -q`，期望 tracked、untracked、ignored 和 `.pyi` 场景测试通过。

## T5：实现非 Git 安全文件发现

**文件：** `src/julycode/repo_map/discovery.py`、`tests/test_repo_map_discovery.py`
**依赖：** T1

**步骤：**
1. 以启动 cwd 为根使用排序后的 `os.scandir` 递归。
2. 应用 Spec 指定的非 Git 默认排除目录。
3. 只返回 `.py`、`.pyi`，Git 探测失败时记录降级诊断。

**验证：** 运行 `python -m pytest tests/test_repo_map_discovery.py -q`，期望非 Git 范围、默认排除和 Git 失败降级测试通过。

## T6：加入 symlink、越界与路径规范化防护

**文件：** `src/julycode/repo_map/discovery.py`、`tests/test_repo_map_discovery.py`
**依赖：** T4、T5

**步骤：**
1. 对目录和文件使用 `lstat`，不跟随任何 symlink。
2. 验证规范化路径仍位于项目根内，排除越界目标。
3. 将输出路径统一为根目录相对 POSIX 路径。

**验证：** 运行 `python -m pytest tests/test_repo_map_discovery.py -q`，期望文件 symlink、目录 symlink、根外目标和跨平台路径测试通过。

## T7：实现内容指纹与 workspace revision

**文件：** `src/julycode/repo_map/discovery.py`、`tests/test_repo_map_discovery.py`
**依赖：** T6

**步骤：**
1. 一次读取原始字节并生成 SHA-256、大小和 `ScannedFile`。
2. 用规范化项目身份、有序文件指纹和规则版本计算 revision。
3. 隔离读取失败文件并证明 revision 不依赖 mtime。

**验证：** 运行 `python -m pytest tests/test_repo_map_discovery.py -q`，期望相同 mtime 不同内容、文件新建/删除和读取失败测试通过。

## T8：解析编码和基础 Python 符号

**文件：** `src/julycode/repo_map/parser.py`、`tests/test_repo_map_parser.py`
**依赖：** T1

**步骤：**
1. 使用 `tokenize.detect_encoding` 解码 `ScannedFile.source_bytes`。
2. 使用 `ast.parse` 提取类、函数、异步函数、方法和异步方法。
3. 记录相对路径、限定名、父类作用域和行号。

**验证：** 运行 `python -m pytest tests/test_repo_map_parser.py -q`，期望 UTF-8、PEP 263、`.pyi` 和基础符号测试通过。

## T9：实现结构化安全签名

**文件：** `src/julycode/repo_map/parser.py`、`tests/test_repo_map_parser.py`
**依赖：** T8

**步骤：**
1. 从 AST 参数节点重建位置、仅位置、关键字、变长参数和返回注解。
2. 将所有默认值统一渲染为 `...`。
3. 为每个符号同时生成正常签名与 `name(...)` 短签名。

**验证：** 运行 `python -m pytest tests/test_repo_map_parser.py -q`，期望各参数类型、默认值隐藏、返回注解和短签名测试通过。

## T10：完成不可信源码清理

**文件：** `src/julycode/repo_map/parser.py`、`tests/test_repo_map_parser.py`
**依赖：** T9

**步骤：**
1. 仅允许结构化标识符注解，字符串和未知注解降级为 `...`。
2. 确保 docstring、注释、decorator 和函数体不进入记录。
3. 清理控制字符并保证任何签名单行不超过 160 字符。

**验证：** 运行 `python -m pytest tests/test_repo_map_parser.py -q`，期望恶意默认值、字符串注解、控制字符和超长签名测试通过。

## T11：提取模块名和导入记录

**文件：** `src/julycode/repo_map/parser.py`、`tests/test_repo_map_parser.py`
**依赖：** T8

**步骤：**
1. 根据相对路径、`__init__.py` 包链和常见 `src/` 布局推导稳定模块名。
2. 提取绝对导入、`from` 导入、相对导入、别名和 `import *` 标记。
3. 无法唯一判断包根时使用稳定的路径模块名回退。

**验证：** 运行 `python -m pytest tests/test_repo_map_parser.py -q`，期望包、`src/`、相对层级、别名和 `import *` 测试通过。

## T12：提取引用并隔离解析错误

**文件：** `src/julycode/repo_map/parser.py`、`tests/test_repo_map_parser.py`
**依赖：** T10、T11

**步骤：**
1. 提取同模块直接名称使用和直接 `Name(...)` 调用。
2. 将 Attribute 调用、`getattr` 和字符串导入标为不建立精确关系的诊断。
3. 将编码、语法和单文件解析错误转换为局部诊断。

**验证：** 运行 `python -m pytest tests/test_repo_map_parser.py -q`，期望直接引用、动态边界和错误文件隔离测试通过。

## T13：解析模块和符号导入关系

**文件：** `src/julycode/repo_map/graph.py`、`tests/test_repo_map_graph.py`
**依赖：** T12

**步骤：**
1. 建立模块名、限定符号名和别名的确定性索引。
2. 唯一解析 `import module` 与 `from module import Symbol`，分别应用固定权重。
3. 正确解析相对导入，无法解析时只记录诊断。

**验证：** 运行 `python -m pytest tests/test_repo_map_graph.py -q`，期望绝对/相对导入、别名和符号导入边测试通过。

## T14：解析唯一引用并处理歧义

**文件：** `src/julycode/repo_map/graph.py`、`tests/test_repo_map_graph.py`
**依赖：** T13

**步骤：**
1. 为同模块唯一直接名称与唯一直接调用建立固定权重边。
2. 重复符号、动态 re-export、`import *` 和 Attribute 调用不建立精确边。
3. 对边按源路径、目标路径和关系种类排序并合并固定权重。

**验证：** 运行 `python -m pytest tests/test_repo_map_graph.py -q`，期望唯一引用和全部歧义边界测试通过。

## T15：实现确定性 PageRank-like 图分数

**文件：** `src/julycode/repo_map/graph.py`、`tests/test_repo_map_graph.py`
**依赖：** T14

**步骤：**
1. 实现 damping `0.85`、最多 50 次、阈值 `1e-12` 的迭代。
2. 稳定处理 dangling node、空图和无边图。
3. 将最终分数量化到 8 位，并把参数纳入 `GRAPH_VERSION`。

**验证：** 运行 `python -m pytest tests/test_repo_map_graph.py -q`，期望已知小图、空图、输入乱序和分数量化测试通过。

## T16：提取当前请求提示

**文件：** `src/julycode/repo_map/ranking.py`、`tests/test_repo_map_graph.py`
**依赖：** T12

**步骤：**
1. 从当前用户原始请求提取路径、文件名、模块和标识符提示。
2. 规范化路径分隔符、Python 模块分隔符和大小写比较规则。
3. 不从历史、工具输出或模型生成内容提取提示。

**验证：** 运行 `python -m pytest tests/test_repo_map_graph.py -q`，期望路径、模块、符号、Unicode 和无提示场景测试通过。

## T17：实现请求优先评分和稳定最终排序

**文件：** `src/julycode/repo_map/ranking.py`、`tests/test_repo_map_graph.py`
**依赖：** T15、T16

**步骤：**
1. 实现 Plan 固定的 `100/80/60/40` 请求加权与 `rank * 10` 图分数。
2. 以 `(-round(score, 8), path, qualified_name, line)` 排序。
3. 覆盖同分、重复符号、空图和任意输入顺序。

**验证：** 运行 `python -m pytest tests/test_repo_map_graph.py -q`，期望请求命中优先于纯图重要度且随机输入顺序输出相同。

## T18：实现地图边界和原子条目

**文件：** `src/julycode/repo_map/renderer.py`、`tests/test_repo_map_renderer.py`
**依赖：** T17

**步骤：**
1. 输出固定 XML-like 边界、revision、根目录和中文不可信导航提示。
2. 顶层符号按“路径 + 签名”组成原子单元。
3. 方法按“路径 + 所属类 + 方法签名”组成原子单元，禁止孤立方法。

**验证：** 运行 `python -m pytest tests/test_repo_map_renderer.py -q`，期望边界、提示、相对路径和类方法原子性测试通过。

## T19：实现完整 Token 预算与短签名降级

**文件：** `src/julycode/repo_map/renderer.py`、`tests/test_repo_map_renderer.py`
**依赖：** T18

**步骤：**
1. 使用注入的 token counter 对每次完整候选块估算。
2. 正常签名放不下时尝试完整短签名，仍放不下则跳过该单元。
3. 预算无法容纳边界与一个最小条目时省略整块；裁剪标记也计入预算。

**验证：** 运行 `python -m pytest tests/test_repo_map_renderer.py -q`，期望精确预算、短签名、超小预算和不输出残缺条目测试通过。

## T20：建立地图 golden 与字节确定性测试

**文件：** `tests/fixtures/repo_map_golden.txt`、`tests/test_repo_map_renderer.py`
**依赖：** T19

**步骤：**
1. 为包含顶层函数、类方法和裁剪标记的固定仓库生成 golden。
2. 精确比较换行、空格、路径、排序和边界内容。
3. 随机化候选输入顺序并重复比较同一 golden。

**验证：** 运行 `python -m pytest tests/test_repo_map_renderer.py -q`，期望所有渲染测试与字节 golden 通过。

## T21：实现三层有界 LRU 缓存

**文件：** `src/julycode/repo_map/manager.py`、`tests/test_repo_map_manager.py`
**依赖：** T7、T12、T15、T17、T20

**步骤：**
1. 实现 ParseCache、GraphCache 和 SnapshotCache 的 Plan 指定 key。
2. 实现条目上限、ParseCache 软内存上限和 LRU 淘汰。
3. 缓存异常时仅丢弃对应层并重新计算。

**验证：** 运行 `python -m pytest tests/test_repo_map_manager.py -q`，期望三层命中、版本隔离、淘汰和损坏恢复测试通过。

## T22：实现后台初始索引与取消

**文件：** `src/julycode/repo_map/manager.py`、`tests/test_repo_map_manager.py`
**依赖：** T21

**步骤：**
1. `start()` 使用 `asyncio.to_thread` 调度发现、哈希、解析和建图，不等待完成。
2. 为任务分配 generation id，只有当前项目和 generation 匹配才提交。
3. `close()` 取消或废弃任务，旧结果不能覆盖新状态。

**验证：** 运行 `python -m pytest tests/test_repo_map_manager.py -q`，期望非阻塞启动、取消和旧 generation 丢弃测试通过。

## T23：实现 RepoMapTurn 与快照复用

**文件：** `src/julycode/repo_map/manager.py`、`tests/test_repo_map_manager.py`
**依赖：** T22

**步骤：**
1. 实现 `begin_turn`、`end_turn` 和当前请求提示绑定。
2. 同 revision、提示和预算复用同一字节快照。
3. 索引未就绪时对当前 turn/revision 固定省略，不等待或中途补入。

**验证：** 运行 `python -m pytest tests/test_repo_map_manager.py -q`，期望快照复用、预算隔离和 not-ready 固定省略测试通过。

## T24：实现精确源码工具失效

**文件：** `src/julycode/repo_map/manager.py`、`tests/test_repo_map_manager.py`
**依赖：** T23

**步骤：**
1. 为已知编辑、创建和删除工具记录目标路径基线。
2. 工具执行后重新发现目标变化并更新有序指纹与 revision。
3. 仅重新解析内容哈希变化文件，复用未变化 ParseCache。

**验证：** 运行 `python -m pytest tests/test_repo_map_manager.py -q`，期望编辑、新建、删除和单文件只重解析测试通过。

## T25：实现 command 与未知副作用的全仓比较

**文件：** `src/julycode/repo_map/manager.py`、`tests/test_repo_map_manager.py`
**依赖：** T24

**步骤：**
1. 未知写入范围工具执行前后比较完整候选集合和内容哈希。
2. 工具失败或超时后仍执行比较，捕获部分写入。
3. 无法可靠比较时使图和快照全局失效，并调度安全重建。

**验证：** 运行 `python -m pytest tests/test_repo_map_manager.py -q`，期望批量修改、失败后写入、无变化 command 和比较失败测试通过。

## T26：完成状态与安全降级

**文件：** `src/julycode/repo_map/manager.py`、`tests/test_repo_map_manager.py`
**依赖：** T25

**步骤：**
1. 填充 enabled、root、revision、预算、文件数、裁剪、缓存和耗时字段。
2. 实现 disabled、not-ready、no-python-files、budget-too-small、scan-error 等原因。
3. 每次省略或失败生成新状态，清除旧快照残留值。

**验证：** 运行 `python -m pytest tests/test_repo_map_manager.py -q`，期望成功与全部降级状态测试通过，异常不向调用方传播。

## T27：增加请求期 GeneratedContextBlock

**文件：** `src/julycode/prompting/base.py`、`src/julycode/prompting/__init__.py`、`tests/test_prompting.py`
**依赖：** T1

**步骤：**
1. 定义 generated、untrusted、request_ephemeral、snapshot 元数据字段。
2. 给 PromptBundle 增加默认空的 `generated_context_blocks`。
3. 保持所有旧 PromptBundle 构造调用兼容。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q`，期望元数据、默认空值和旧构造方式测试通过。

## T28：扩展 TokenEstimator 估算生成块

**文件：** `src/julycode/context/estimator.py`、`tests/test_context_estimator.py`
**依赖：** T27

**步骤：**
1. 增加公开文本估算和生成块估算入口。
2. 确保 PromptBundle 总估算包含生成块完整序列化内容。
3. 保持既有 `chars_per_token` 和安全余量语义不变。

**验证：** 运行 `python -m pytest tests/test_context_estimator.py -q`，期望地图标题、边界和正文均计入总估算，既有断言不回归。

## T29：在 ContextManager 授予最低优先级预算

**文件：** `src/julycode/context/manager.py`、`tests/test_context_manager.py`
**依赖：** T27、T28

**步骤：**
1. 增加可选异步 `OptionalContextFactory` 参数。
2. 先完成基础 Prompt、工具结果压缩和必要重压缩，再计算剩余预算。
3. 按配置上限与剩余空间的最小值调用工厂，零预算时不调用。

**验证：** 运行 `python -m pytest tests/test_context_manager.py -q`，期望高优先级内容不因地图丢失，工厂只收到有效剩余预算。

## T30：实现合并后的最终预算复核

**文件：** `src/julycode/context/manager.py`、`tests/test_context_manager.py`
**依赖：** T29

**步骤：**
1. 合并生成块后重新估算完整请求。
2. 超限时以更小预算至多重试一次，仍超限则省略地图。
3. 地图省略后继续走既有超限和压缩错误处理。

**验证：** 运行 `python -m pytest tests/test_context_manager.py -q`，期望缩小、整块省略、禁用压缩和既有超限行为测试通过。

## T31：定义工具执行观察者协议

**文件：** `src/julycode/tools/scheduler.py`、`tests/test_tool_scheduler.py`
**依赖：** 无

**步骤：**
1. 定义异步 `ToolExecutionObserver.before_execute/after_execute` 协议。
2. 给 Scheduler 增加默认 `None` 的可选 observer。
3. 保持没有 observer 时现有调度顺序和返回值不变。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py -q`，期望旧 Scheduler 测试和无 observer 兼容测试通过。

## T32：统一真实工具执行包装

**文件：** `src/julycode/tools/scheduler.py`、`tests/test_tool_scheduler.py`
**依赖：** T31

**步骤：**
1. 将所有实际 `executor.execute` 路径收敛到 `_execute` 包装。
2. 真正执行前调用 before，完成、失败或超时后调用 after。
3. 权限拒绝和 hook 拦截等未执行调用不通知 observer；读并发与写串行语义不变。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py -q`，期望只读并发、副作用串行、成功、失败、超时和拒绝边界测试通过。

## T33：接入 Agent Loop 的 turn 生命周期

**文件：** `src/julycode/agent.py`、`tests/test_agent.py`
**依赖：** T23、T30、T32

**步骤：**
1. 给 AgentLoopRunner 增加默认空的 RepoMapManager 依赖。
2. 在 `run()` 开始创建 turn，在所有结束和异常路径的 `finally` 中结束。
3. 无 manager 时不改变既有 MCP、权限、Plan Mode 和流式生命周期。

**验证：** 运行 `python -m pytest tests/test_agent.py -q`，期望 turn 始终成对结束且全部旧 Agent 测试通过。

## T34：接入每次模型调用的地图与 revision 刷新

**文件：** `src/julycode/agent.py`、`tests/test_agent.py`
**依赖：** T26、T27、T30、T33

**步骤：**
1. 将 manager 快照转换为带完整元数据的 GeneratedContextBlock。
2. 每次模型迭代把绑定 turn 的工厂交给 ContextManager。
3. 将 turn observer 交给 Scheduler，确保刷新完成后才组装下一次模型请求。

**验证：** 运行 `python -m pytest tests/test_agent.py -q`，期望只读迭代复用快照、源码修改后下一迭代使用新 revision。

## T35：序列化 OpenAI Repo Map 生成块

**文件：** `src/julycode/providers/openai.py`、`tests/test_openai_provider.py`
**依赖：** T27

**步骤：**
1. 把 Repo Map 序列化为稳定 system 前缀之后、动态运行时之前的独立合法上下文。
2. 明确排除 `_prompt_cache_key` 中的地图内容和 snapshot id。
3. 不发送显式 breakpoint 参数，并加入默认 false 的能力钩子。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py -q`，期望角色顺序、地图语义、稳定 key 和无不支持字段测试通过。

## T36：增加 OpenAI 稳定前缀 golden

**文件：** `tests/test_openai_provider.py`
**依赖：** T35

**步骤：**
1. 规范序列化 tools 与 Repo Map 之前的消息前缀并计算 SHA-256。
2. 比较地图关闭、两个不同快照和相同快照重用场景。
3. 同时断言完整请求中的 Repo Map 会随快照变化。

**验证：** 运行 `python -m pytest tests/test_openai_provider.py -q`，期望长期前缀摘要恒定且完整请求快照内容正确变化。

## T37：序列化 Anthropic Repo Map 与两级断点

**文件：** `src/julycode/providers/anthropic.py`、`tests/test_anthropic_provider.py`
**依赖：** T27

**步骤：**
1. 将生成块放在稳定 system blocks 之后、动态 blocks 之前。
2. 缓存开启时保留稳定前缀断点，并在 Repo Map 块增加第二个 ephemeral 断点。
3. 缓存关闭时完全移除 `cache_control`，messages 和 tools 顺序不变。

**验证：** 运行 `python -m pytest tests/test_anthropic_provider.py -q`，期望开启/关闭缓存、两级断点和原有请求测试通过。

## T38：增加 Anthropic 稳定前缀 golden

**文件：** `tests/test_anthropic_provider.py`
**依赖：** T37

**步骤：**
1. 按 `tools → system → messages` 规范序列化 Repo Map 前缀。
2. 比较地图关闭、不同地图和相同快照场景的长期前缀摘要。
3. 断言地图变化只改变第二级快照内容和后续摘要。

**验证：** 运行 `python -m pytest tests/test_anthropic_provider.py -q`，期望长期前缀摘要恒定、快照级断点位置和内容正确。

## T39：验证聊天历史、恢复与记忆隔离

**文件：** `tests/test_repo_map_integration.py`、`tests/test_session.py`、`tests/test_session_recovery.py`、`tests/test_memory_updater.py`
**依赖：** T27、T34

**步骤：**
1. 完成带 Repo Map 的请求后检查 ChatSession 消息。
2. 触发摘要、保存/恢复和长期记忆更新，检查所有持久化表示。
3. 断言地图边界、提示和 snapshot id 均不存在，下一请求重新临时组装。

**验证：** 运行 `python -m pytest tests/test_repo_map_integration.py tests/test_session.py tests/test_session_recovery.py tests/test_memory_updater.py -q`，期望全部历史隔离断言通过。

## T40：接入 TUI Manager 生命周期并限定主 Agent

**文件：** `src/julycode/tui/app.py`、`tests/test_tui_smoke.py`
**依赖：** T2、T26、T34

**步骤：**
1. JulyCodeApp 持有跨轮次 manager，启用时在 mount 非阻塞 start，unmount close。
2. 以工具上下文启动 cwd 创建 manager，并复用 ContextManager 的估算规则。
3. 仅主 `_run_agent_command` 注入；Skill、子 Agent、团队和隔离 Worktree 路径保持空依赖。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -q`，期望启动/关闭、功能关闭、主 Agent 注入和隔离路径不注入测试通过。

## T41：在 `/status` 暴露 Repo Map 状态

**文件：** `src/julycode/commands/models.py`、`src/julycode/commands/builtin.py`、`src/julycode/tui/app.py`、`tests/test_commands.py`
**依赖：** T26、T40

**步骤：**
1. 在 CommandStatusSnapshot 接入不可变 Repo Map 状态。
2. 格式化 enabled、root、revision、预算、文件数、裁剪、缓存、耗时和原因。
3. 关闭、未就绪和省略状态输出明确值，不显示上次成功残留。

**验证：** 运行 `python -m pytest tests/test_commands.py tests/test_tui_smoke.py -q`，期望所有状态字段和既有 `/status` 输出测试通过。

## T42：完成 Stub Agent Loop 集成测试

**文件：** `tests/test_repo_map_integration.py`、`tests/test_agent.py`
**依赖：** T34、T36、T38、T41

**步骤：**
1. Stub Provider 记录每次 PromptBundle 和序列化请求。
2. 首轮返回只读工具，验证 revision 与地图字节不变。
3. 后续返回真实编辑调用，验证下一模型请求不含旧签名并最终正常回复。

**验证：** 运行 `python -m pytest tests/test_repo_map_integration.py tests/test_agent.py -q`，期望完整多轮工具循环和降级普通对话场景通过。

## T43：验证确定性与性能边界

**文件：** `tests/test_repo_map_graph.py`、`tests/test_repo_map_renderer.py`、`tests/test_repo_map_manager.py`
**依赖：** T26、T42

**步骤：**
1. 随机化发现/解析结果顺序和散列种子，精确比较快照字节。
2. 当前 JulyCode 仓库预热后采集至少 100 次缓存命中组装耗时并计算 P95。
3. 用 asyncio heartbeat 包围初次索引和重建，记录主线程最大同步延迟。

**验证：** 运行 `python -m pytest tests/test_repo_map_graph.py tests/test_repo_map_renderer.py tests/test_repo_map_manager.py -q`，期望字节一致、P95 `< 50ms`、Repo Map 同步阻塞 `< 16ms`。

## T44：建立质量评测模型、加载器和数据集

**文件：** `eval/repo_map_quality/__init__.py`、`eval/repo_map_quality/models.py`、`eval/repo_map_quality/loader.py`、`eval/cases/repo_map_quality/navigation.json`、`tests/test_eval_framework.py`
**依赖：** T17

**步骤：**
1. 定义任务请求、目标文件、Top-K、运行模式和结果结构。
2. 加载并严格校验 JSON 数据集，拒绝目标路径直接泄露到请求的 case。
3. 编写覆盖 JulyCode 入口、上下文、Provider、工具调度等模块的导航任务。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -q`，期望数据集加载、非法 case 拒绝和结构往返测试通过。

## T45：实现质量评测运行器、报告和 CLI

**文件：** `eval/repo_map_quality/runner.py`、`eval/repo_map_quality/report.py`、`eval/run_repo_map_eval.py`、`tests/test_eval_framework.py`
**依赖：** T26、T44

**步骤：**
1. 离线生成快照并计算目标文件 Top-K 命中。
2. 提供可选 enabled/disabled 成对在线运行，统计命中目标前的探索工具调用数。
3. 输出 `results.json` 与 `report.md`，不设置真实模型 CI 通过阈值。

**验证：** 运行 `python -m pytest tests/test_eval_framework.py -q`，再运行 `python eval/run_repo_map_eval.py --help`，期望评测测试通过且 CLI 展示离线/成对运行参数。

## T46：补充用户与评测文档

**文件：** `README.md`、`eval/README.md`
**依赖：** T2、T41、T45

**步骤：**
1. 记录默认启用、`repo_map.enabled`、`max_tokens` 和 `/status` 字段。
2. 说明地图是不可信导航索引、不会进入历史、修改前仍要 read/grep。
3. 记录质量评测命令、Top-K 与探索调用指标及非 CI 门禁性质。

**验证：** 运行 `rg -n "repo_map|Repo Map|Top-K" README.md eval/README.md`，期望配置、安全边界、状态和评测说明均可定位。

## T47：运行全量自动回归

**文件：** `tests/`、`src/julycode/`、`eval/`
**依赖：** T39、T40、T42、T43、T45、T46

**步骤：**
1. 运行全部测试，修复普通模式、Plan Mode、权限、Skill、MCP、压缩、恢复和流式回归。
2. 运行 Python 编译检查，排除循环导入和未加载模块。
3. 检查工作区差异，不覆盖用户无关修改且不新增运行时外部依赖。

**验证：** 分别运行 `python -m pytest -q` 和 `python -m compileall -q src eval`，期望退出码均为 0。

## T48：执行 tmux 真实端到端验证

**文件：** `specs/repo-map/checklist.md`（记录证据，不改验收定义）
**依赖：** T47

**步骤：**
1. 使用 `tmux new-session -d -s julycode-repomap 'julycode'` 启动真实 JulyCode。
2. 发送一段不包含准确路径的仓库导航请求，观察 Repo Map 状态、工具调用和最终回复。
3. 再发送会修改 Python 签名的真实请求，观察同一 Agent Loop 后续模型调用使用新 revision。
4. 捕获 pane 输出，并按已批准 checklist 逐项记录真实结果与 Prompt Cache usage 证据。

**验证：** 运行 `tmux capture-pane -pt julycode-repomap`，期望看到 JulyCode 正常回复、相关工具调用、Repo Map 可观察状态，且修改后 revision 变化；最后运行 `tmux kill-session -t julycode-repomap` 清理会话。

## 执行顺序

```text
基础模型与配置：T1 → T2

仓库发现：T1 → T3 → T4 ─┐
                    T5 ───┴→ T6 → T7

Python 解析：T1 → T8 → T9 → T10 ─┐
                      T11 ─────────┴→ T12

图与渲染：T12 → T13 → T14 → T15 ─┐
            T12 → T16 ─────────────┴→ T17 → T18 → T19 → T20

Manager：T7 + T12 + T15 + T17 + T20 → T21 → T22 → T23 → T24 → T25 → T26

Prompt 与预算：T1 → T27 → T28 → T29 → T30
工具观察：T31 → T32
Agent：T23 + T30 + T32 → T33 → T34

Provider：T27 → T35 → T36
          T27 → T37 → T38

应用集成：T34 → T39
          T2 + T26 + T34 → T40 → T41
          T34 + T36 + T38 + T41 → T42 → T43

质量评测：T17 → T44
            T26 + T44 → T45 → T46

收尾：T39 + T40 + T42 + T43 + T45 + T46 → T47 → T48
```

## 覆盖追踪

| 任务 | 主要覆盖 |
| --- | --- |
| T2-T7 | F1-F3、F13-F14、N1-N2、AC1-AC2、AC9、AC16 |
| T8-T15 | F4-F6、F21、AC3-AC4 |
| T16-T20 | F7-F10、N7、AC5-AC6 |
| T21-T26 | F11-F14、F20、F22、N3-N6、N8、AC8-AC9、AC14-AC16、AC20 |
| T27-T30 | F8-F10、F17、F19、AC6-AC7、AC12 |
| T31-T34 | F11-F12、F23、AC8、AC17 |
| T35-T38 | F15-F17、N9、AC10-AC12 |
| T39-T41 | F1、F18、F20、F22-F23、AC1、AC13-AC14、AC20 |
| T42-T43 | N3-N5、N7、N10-N11、AC15、AC17 |
| T44-T45 | N12、AC18 |
| T46-T48 | N11、AC19 和用户可见配置、评测、端到端验收 |
