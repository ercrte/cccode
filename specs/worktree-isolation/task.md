# MewCode Worktree 隔离 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/mewcode/worktrees/__init__.py` | 导出 Worktree 稳定接口 |
| 新建 | `src/mewcode/worktrees/models.py` | 配置、布局、元数据、lease、状态、处置、清理报告和错误模型 |
| 新建 | `src/mewcode/worktrees/paths.py` | 安全名称、配置路径、仓库布局和边界解析 |
| 新建 | `src/mewcode/worktrees/git.py` | Git 子进程、Worktree、hooks、状态和提交保护 |
| 新建 | `src/mewcode/worktrees/environment.py` | 复制、ignored 文件、软链和失败回滚 |
| 新建 | `src/mewcode/worktrees/manager.py` | 创建、恢复、退出、删除、锁和过期候选处理 |
| 新建 | `src/mewcode/worktrees/janitor.py` | 启动即运行和周期清理任务 |
| 新建 | `tests/test_worktrees.py` | 临时 Git 仓库中的 Worktree 全生命周期测试 |
| 修改 | `src/mewcode/config.py` | 解析并校验 `sub_agents.worktree` |
| 修改 | `src/mewcode/tools/base.py` | 规范化 `ToolContext.cwd` |
| 修改 | `src/mewcode/context/manager.py` | 保存并传播绝对 cwd |
| 修改 | `src/mewcode/memory/recovery.py` | 暴露不恢复会话的项目知识读取入口 |
| 修改 | `src/mewcode/memory/manager.py` | 加载独立 cwd 的运行时知识上下文 |
| 修改 | `src/mewcode/prompting/builder.py` | 注入隔离目录、主目录、分支和越界约束 |
| 修改 | `src/mewcode/subagents/__init__.py` | 导出新增子 Agent 数据结构 |
| 修改 | `src/mewcode/subagents/loader.py` | 解析角色 `isolation` |
| 修改 | `src/mewcode/subagents/models.py` | 扩展角色、工作上下文、提示和结果模型 |
| 修改 | `src/mewcode/subagents/runtime.py` | 从显式工作上下文创建全部 cwd 相关组件 |
| 修改 | `src/mewcode/subagents/manager.py` | 接入 Worktree lease、退出处置和 janitor |
| 修改 | `src/mewcode/subagents/tools.py` | 在委派结果中输出 Worktree 处置信息 |
| 修改 | `src/mewcode/tui/app.py` | 启停 janitor 并报告清理错误 |
| 修改 | `tests/test_config.py` | Worktree 配置解析和错误测试 |
| 修改 | `tests/test_tools.py` | ToolContext 绝对 cwd 测试 |
| 修改 | `tests/test_memory_instructions.py` | 不同 Worktree 项目指令隔离测试 |
| 修改 | `tests/test_prompting.py` | Worktree 运行时提示测试 |
| 修改 | `tests/test_subagents.py` | 角色、runner、manager 和结果集成测试 |
| 修改 | `tests/test_tui_smoke.py` | janitor 启停与主会话不中断测试 |
| 修改 | `tests/e2e_mock_openai_server.py` | 增加隔离子 Agent 写文件的 mock 对话场景 |
| 修改 | `README.md` | 说明角色隔离和环境初始化配置 |

## T1: 建立 Worktree 领域数据模型

**文件：** `src/mewcode/worktrees/models.py`、`src/mewcode/worktrees/__init__.py`  
**依赖：** 无  
**步骤：**
1. 定义 `WorktreeConfig`、`RepositoryLayout`、`WorktreeMetadata`、`WorktreeLease`。
2. 定义 `WorktreeChangeState`、`WorktreeDisposition`、`CleanupItemResult`、`CleanupReport`。
3. 定义带 `stage` 的 `WorktreeError`，并从包入口导出稳定类型。

**验证：** 运行 `python -c "from mewcode.worktrees import WorktreeConfig, WorktreeLease, WorktreeDisposition; assert WorktreeConfig().retention_days == 7.0"`，期望退出码为 0。

## T2: 实现严格安全名称校验

**文件：** `src/mewcode/worktrees/paths.py`、`tests/test_worktrees.py`  
**依赖：** T1  
**步骤：**
1. 实现 `validate_relative_name()` 的总长度、分段长度、ASCII 字符和首字符规则。
2. 拒绝空段、`.`、`..`、绝对路径、反斜杠和非法字符。
3. 增加合法单段、合法嵌套和全部拒绝类别的参数化测试。

**验证：** 运行 `python -m pytest tests/test_worktrees.py::test_validate_relative_name_accepts_safe_nested_names tests/test_worktrees.py::test_validate_relative_name_rejects_unsafe_names -q`，期望全部通过。

## T3: 实现仓库布局与规范化边界

**文件：** `src/mewcode/worktrees/paths.py`、`tests/test_worktrees.py`  
**依赖：** T2  
**步骤：**
1. 实现 `validate_config_path()` 和 `resolve_inside()`，统一使用 `resolve()` + `relative_to()` 判断归属。
2. 实现只读文件系统的 `discover_repository_layout()`，支持从仓库子目录启动。
3. 实现确定性 `<role>/<task_id>` 目录名与 `mewcode/<role>/<task_id>` 分支名。
4. 测试普通路径、子目录启动、符号链接越界和没有 `.git` 边界的错误。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'repository_layout or config_path or resolve_inside'`，期望全部通过。

## T4: 解析 Worktree 项目配置

**文件：** `src/mewcode/config.py`、`src/mewcode/subagents/models.py`、`tests/test_config.py`  
**依赖：** T1、T3  
**步骤：**
1. 在 `SubAgentConfig` 增加默认 `WorktreeConfig`。
2. 解析 `sub_agents.worktree` 的三类路径、清理间隔和保留天数。
3. 拒绝非数组、空路径、不安全路径、跨类别重复、零值、负数和非数值时间。
4. 增加默认配置、完整配置和非法配置测试。

**验证：** 运行 `python -m pytest tests/test_config.py -q -k 'worktree or sub_agents'`，期望全部通过。

## T5: 扩展角色 isolation 解析

**文件：** `src/mewcode/subagents/models.py`、`src/mewcode/subagents/loader.py`、`tests/test_subagents.py`  
**依赖：** T1  
**步骤：**
1. 定义内部 `SubAgentIsolation`，角色未声明时设为 `shared`。
2. 只把显式 `isolation: worktree` 解析为隔离模式。
3. 未知值、空值和非字符串值使该角色产生清晰解析告警并不可启动。
4. 增加默认、worktree 和非法值测试，确认 Fork 输入 schema 未变化。

**验证：** 运行 `python -m pytest tests/test_subagents.py -q -k 'isolation or schema_is_stable'`，期望全部通过。

## T6: 实现无 shell 的 Git 执行基础

**文件：** `src/mewcode/worktrees/git.py`、`tests/test_worktrees.py`  
**依赖：** T1  
**步骤：**
1. 实现异步 `GitClient.run()`，使用 argv、显式绝对 cwd、stdout/stderr 捕获和固定超时。
2. 实现真实仓库根、`HEAD` 和分支存在查询。
3. 将失败包装为带阶段和脱敏摘要的 `WorktreeError`。
4. 用临时仓库测试成功查询、非仓库错误和 cwd 不变。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'git_client_run or git_repository_identity'`，期望全部通过。

## T7: 实现 Worktree 创建与本地 exclude

**文件：** `src/mewcode/worktrees/git.py`、`tests/test_worktrees.py`  
**依赖：** T6  
**步骤：**
1. 实现仓库本地 exclude 的幂等写入，忽略 storage root 和元数据标记。
2. 实现 `git worktree add -b`，创建前拒绝同名分支，禁止 reset 或覆盖参数。
3. 实现 `git worktree remove` 和确定性临时分支删除。
4. 测试共享对象库、主目录 dirty 不进入新 Worktree、冲突分支和主状态不追踪 storage root。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'create_git_worktree or local_exclude or branch_conflict'`，期望全部通过。

## T8: 配置 Worktree Git hooks

**文件：** `src/mewcode/worktrees/git.py`、`tests/test_worktrees.py`  
**依赖：** T6、T7  
**步骤：**
1. 读取主工作树当前有效 `core.hooksPath`。
2. 无自定义路径时保持 Git 共享 hooks；有自定义路径时启用 `extensions.worktreeConfig` 并写入 Worktree-local 绝对 hooks 路径。
3. 确认配置前后主工作目录有效 hooks 路径不变。
4. 用拒绝提交的测试 hook 验证隔离目录实际执行同一 hook。

**验证：** 运行 `python -m pytest tests/test_worktrees.py::test_worktree_inherits_custom_git_hooks tests/test_worktrees.py::test_shared_hooks_need_no_override -q`，期望全部通过。

## T9: 实现变更与未推送提交判定

**文件：** `src/mewcode/worktrees/git.py`、`tests/test_worktrees.py`  
**依赖：** T6、T7  
**步骤：**
1. 解析 porcelain 状态，分别记录受追踪修改和未跟踪路径。
2. 计算创建基线后的新增提交数。
3. 无 upstream 时把全部新增提交计为未推送；有 upstream 时计算 upstream 不可达的新增提交。
4. 用临时 bare remote 测试无提交、dirty、untracked、无 upstream、新增未推送和已推送。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'change_state or unpushed'`，期望全部通过。

## T10: 实现环境初始化预检

**文件：** `src/mewcode/worktrees/environment.py`、`tests/test_worktrees.py`  
**依赖：** T3、T6  
**步骤：**
1. 在写入前解析三类规则的主目录源和 Worktree 目标。
2. 拒绝源不存在、源解析越界、源软链、目标已存在、目标越界和类型不匹配。
3. 要求 `ignored_copy_paths` 由 Git 确认为忽略路径，`symlink_paths` 源必须是目录。
4. 验证预检失败时目标目录内容完全不变。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'environment_preflight'`，期望全部通过。

## T11: 实现独立复制与 ignored 文件补齐

**文件：** `src/mewcode/worktrees/environment.py`、`tests/test_worktrees.py`  
**依赖：** T10  
**步骤：**
1. 为 `copy_paths` 实现文件 `copy2` 和目录 `copytree`。
2. 为 `ignored_copy_paths` 实现同路径独立复制。
3. 创建必要父目录并记录本次创建目标。
4. 测试副本修改不影响主目录、未声明忽略内容不复制和基础文件模式保留。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'copy_paths or ignored_copy'`，期望全部通过。

## T12: 实现大型目录软链与失败回滚

**文件：** `src/mewcode/worktrees/environment.py`、`tests/test_worktrees.py`  
**依赖：** T11  
**步骤：**
1. 为 `symlink_paths` 创建指向主工作目录同路径的绝对目录软链。
2. 初始化任一步失败时逆序删除仅由本次调用创建的目标和空父目录。
3. 回滚失败时保留未知现场并把原始错误与回滚错误一并返回。
4. 测试软链目标、复制后软链失败回滚和既有文件不被删除。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'symlink_paths or environment_rollback'`，期望全部通过。

## T13: 实现恢复元数据读写与校验

**文件：** `src/mewcode/worktrees/manager.py`、`tests/test_worktrees.py`  
**依赖：** T1、T3  
**步骤：**
1. 定义固定元数据文件名和版本化 JSON 编解码。
2. 使用临时文件加原子替换写入元数据。
3. 校验仓库 ID、任务、角色、相对名称、确定性分支、提交对象 ID 和时间。
4. 测试完整元数据、坏 JSON、未知版本、错仓库、错任务和错分支。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'metadata'`，期望全部通过。

## T14: 实现首次 acquire 创建流程

**文件：** `src/mewcode/worktrees/manager.py`、`src/mewcode/worktrees/__init__.py`、`tests/test_worktrees.py`  
**依赖：** T7、T8、T12、T13  
**步骤：**
1. 实现目标级锁和 active lease 登记。
2. 目标不存在时验证文件系统仓库根与 Git 仓库根一致并读取 `HEAD`。
3. 依次写 exclude、创建 Worktree、写元数据、初始化环境并返回绝对 `WorktreeLease`。
4. 验证从仓库子目录启动时 lease.cwd 指向 checkout 内对应子目录。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'manager_acquire_new'`，期望全部通过。

## T15: 实现目录已存在的零 Git 快速恢复

**文件：** `src/mewcode/worktrees/manager.py`、`tests/test_worktrees.py`  
**依赖：** T13、T14  
**步骤：**
1. 在任何 Git 或初始化调用前检查确定性目标目录是否存在。
2. 目录存在时只读取并校验元数据，登记 active 后返回 `recovered=True`。
3. 元数据缺失或不匹配时只读失败，不修复、不删除、不重新初始化。
4. 注入任何调用都会失败的 fake GitClient 与 initializer，证明合法恢复不会调用二者。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'fast_recovery'`，期望全部通过。

## T16: 实现退出状态检查与无变更清理

**文件：** `src/mewcode/worktrees/manager.py`、`tests/test_worktrees.py`  
**依赖：** T9、T14  
**步骤：**
1. 实现 `finish()` 在目标锁内保持 active，检查状态后再取消登记。
2. 无修改、无未跟踪文件、无新增提交时移除 Worktree 和临时分支。
3. dirty、untracked 或有新增提交时返回 retained 及具体原因。
4. 状态或删除失败时保留并返回可观察错误，不覆盖任务成果。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'finish_clean or finish_retains'`，期望全部通过。

## T17: 实现保护删除与已推送提交判定

**文件：** `src/mewcode/worktrees/manager.py`、`tests/test_worktrees.py`  
**依赖：** T9、T16  
**步骤：**
1. 实现 `delete()` 的路径、元数据、active 和 Git 四项检查，共享不重入的锁内删除逻辑。
2. 普通删除拒绝任意新增提交。
3. `allow_pushed_commits=True` 时仅允许 clean 且全部新增提交已被 upstream 包含的目录。
4. 测试 active、dirty、untracked、无 upstream、新增未推送、全部已推送和分支删除失败。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'protected_delete'`，期望全部通过。

## T18: 实现过期候选扫描与三层过滤

**文件：** `src/mewcode/worktrees/manager.py`、`tests/test_worktrees.py`  
**依赖：** T17  
**步骤：**
1. 只扫描固定 storage root 下的元数据标记父目录。
2. 用注入时钟和 `created_at + retention_days` 判断过期。
3. 依次执行规范路径、仓库元数据、active/Git 安全过滤。
4. 单候选异常写入 `CleanupReport` 并继续其余候选。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'cleanup_expired or cleanup_three_layers'`，期望全部通过。

## T19: 实现启动与周期 janitor

**文件：** `src/mewcode/worktrees/janitor.py`、`src/mewcode/worktrees/__init__.py`、`tests/test_worktrees.py`  
**依赖：** T18  
**步骤：**
1. 实现幂等 `start()`，创建后台任务后立即返回。
2. 后台任务先 `run_once()`，再按配置间隔等待并重复。
3. 实现 `close()` 取消扫描或 sleep 并等待结束。
4. 用短间隔 fake manager 验证启动即运行、周期运行、异常不中止和关闭不阻塞。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'janitor'`，期望全部通过。

## T20: 统一绝对 cwd 构造边界

**文件：** `src/mewcode/tools/base.py`、`src/mewcode/context/manager.py`、`tests/test_tools.py`、`tests/test_worktrees.py`  
**依赖：** 无  
**步骤：**
1. 在 `ToolContext.__post_init__()` 将 cwd 规范化为绝对路径。
2. 在 `ContextManager` 构造时保存规范化绝对 cwd，并继续传给 `ContextStore`。
3. 验证 FileReadCache 对两个 Worktree 中相同相对文件使用不同绝对 key。
4. 验证构造和工具执行不会调用或改变 `os.chdir()`。

**验证：** 运行 `python -m pytest tests/test_tools.py tests/test_worktrees.py -q -k 'absolute_cwd or cache_isolated or no_chdir'`，期望全部通过。

## T21: 提供不恢复历史的项目知识加载

**文件：** `src/mewcode/memory/recovery.py`、`src/mewcode/memory/manager.py`、`tests/test_memory_instructions.py`  
**依赖：** T20  
**步骤：**
1. 将现有项目知识读取整理为 `SessionBootstrapper.load_knowledge()`。
2. 增加 `SessionMemoryManager.load_runtime_context()`，只加载项目指令和记忆索引，不恢复会话。
3. 保持现有主会话 bootstrap 行为调用同一入口。
4. 用两个目录中不同 `AGENTS.md` 和项目记忆验证上下文互不串用。

**验证：** 运行 `python -m pytest tests/test_memory_instructions.py tests/test_session_recovery.py -q -k 'worktree or instruction or knowledge'`，期望全部通过。

## T22: 扩展子 Agent 工作上下文和结果模型

**文件：** `src/mewcode/subagents/models.py`、`src/mewcode/subagents/__init__.py`、`tests/test_subagents.py`  
**依赖：** T1、T5  
**步骤：**
1. 定义 `SubAgentWorkingContext` 和 `SubAgentWorktreeInfo`。
2. 扩展 `ActiveSubAgentPrompt` 的 isolation、cwd、main_cwd、branch。
3. 扩展 `SubAgentResult.worktree` 和后台记录的可选 lease/处置信息。
4. 保持共享角色和 Fork 构造的默认兼容性。

**验证：** 运行 `python -m pytest tests/test_subagents.py -q -k 'working_context or worktree_result or fork'`，期望全部通过。

## T23: 让 runner 使用显式工作上下文

**文件：** `src/mewcode/subagents/runtime.py`、`tests/test_subagents.py`  
**依赖：** T20、T21、T22  
**步骤：**
1. 为 `create_runner()` 增加必需 `working_context` 参数。
2. 用其绝对 cwd 创建子 ToolExecutor、PermissionController、ContextManager 和 SessionMemoryManager。
3. 加载该目录的项目知识，构造带 Worktree 信息的 active prompt。
4. 调整现有定义式/Fork 测试，验证主执行器 cwd 未改变且共享模式仍使用主 cwd。

**验证：** 运行 `python -m pytest tests/test_subagents.py -q -k 'runner_factory'`，期望全部通过。

## T24: 注入 Worktree 运行时提示

**文件：** `src/mewcode/prompting/builder.py`、`tests/test_prompting.py`  
**依赖：** T22  
**步骤：**
1. 在 active 子 Agent 块输出 isolation 状态。
2. 隔离模式输出绝对隔离 cwd、主 cwd、分支和所有文件操作不得越界的说明。
3. shared/Fork 模式不输出虚假 Worktree 路径。
4. 增加完整字段和缺省行为测试。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q -k 'sub_agent and worktree'`，期望全部通过。

## T25: 在 SubAgentManager 接入 lease 生命周期

**文件：** `src/mewcode/subagents/manager.py`、`tests/test_subagents.py`  
**依赖：** T14、T16、T19、T23  
**步骤：**
1. 构造器支持注入或默认创建 `WorktreeManager` 与 `WorktreeJanitor`。
2. defined + worktree 角色在 runner 前 acquire；shared 与 Fork 直接构造主目录工作上下文。
3. 用 `try/finally` 保证完成、失败、取消和 runner 工厂异常都调用 finish。
4. 把处置结果合并进 `SubAgentResult`；acquire 失败走现有任务失败路径且主会话可继续。

**验证：** 运行 `python -m pytest tests/test_subagents.py -q -k 'manager_worktree_lifecycle'`，期望全部通过。

## T26: 输出 Worktree 结果与后台通知

**文件：** `src/mewcode/subagents/tools.py`、`src/mewcode/subagents/manager.py`、`tests/test_subagents.py`  
**依赖：** T22、T25  
**步骤：**
1. 在前台 `delegate_agent` 结果 payload 中增加可选 Worktree 字段。
2. 在后台完成通知中显示 cleaned，或 retained 的目录、分支和原因。
3. 后台任务初始返回保持原 schema，只在已有确定信息时附加路径。
4. 确认输入 schema 没有新增 Worktree 参数。

**验证：** 运行 `python -m pytest tests/test_subagents.py -q -k 'worktree_payload or completion_notice or schema_is_stable'`，期望全部通过。

## T27: 接入 TUI 启停与错误报告

**文件：** `src/mewcode/tui/app.py`、`tests/test_tui_smoke.py`  
**依赖：** T19、T25  
**步骤：**
1. `on_mount()` 在角色刷新后调用 `SubAgentManager.start()`，不等待首次清理完成。
2. `on_unmount()` 继续通过 `SubAgentManager.close()` 取消子任务并关闭 janitor。
3. 清理报告中的失败项输出脱敏 stderr 警告，不弹权限框、不阻断输入。
4. 增加启动、清理异常和退出的 smoke test。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -q -k 'worktree_janitor'`，期望全部通过。

## T28: 验证同目标并发串行化

**文件：** `src/mewcode/worktrees/manager.py`、`tests/test_worktrees.py`  
**依赖：** T15、T17、T18  
**步骤：**
1. 确认 acquire、finish、delete 和 cleanup 对相同确定性目标复用同一锁。
2. 清理不删除 active lease，finish 期间 active 标记不提前释放。
3. 并发运行两个 acquire，以及 finish 与 janitor 竞争场景。
4. 断言最多一个 acquire 成功进入目标，目录、元数据和分支保持一致。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'concurrent_same_target or finish_cleanup_race'`，期望全部通过。

## T29: 增加真实 Git 子 Agent 集成测试

**文件：** `tests/test_subagents.py`、`tests/test_worktrees.py`  
**依赖：** T24、T25、T26、T28  
**步骤：**
1. 在临时 Git 仓库创建 `isolation: worktree` 定义式角色和可写工具。
2. 让 fake provider 驱动子 Agent 在隔离目录修改与主目录同名文件。
3. 断言工具、Hook、项目指令和上下文路径均指向隔离 cwd，主目录和进程 cwd 不变。
4. 验证无变更任务自动清理、有变更任务保留并返回路径/分支。

**验证：** 运行 `python -m pytest tests/test_subagents.py tests/test_worktrees.py -q -k 'real_git_isolation'`，期望全部通过。

## T30: 更新用户文档

**文件：** `README.md`、`tests/test_config.py`  
**依赖：** T4、T5、T26  
**步骤：**
1. 在子 Agent 角色字段中说明 `isolation: worktree`、默认 shared 和仅 defined 支持。
2. 增加 `sub_agents.worktree` 三类初始化规则、默认清理间隔和保留时间示例。
3. 说明主目录 dirty 不带入、变更保留、无变更清理和不自动合并。
4. 增加 README 关键配置断言测试。

**验证：** 运行 `python -m pytest tests/test_config.py::test_readme_documents_worktree_isolation -q`，期望通过。

## T31: 增加 tmux 对话用 mock 场景

**文件：** `tests/e2e_mock_openai_server.py`  
**依赖：** T26  
**步骤：**
1. 识别“Worktree 隔离子 Agent”主请求并调用指定定义式角色。
2. 识别 active worktree 子 Agent 提示并调用 `write_file` 写入 `isolated.txt`。
3. 工具完成后让子 Agent 返回摘要，主 Agent 收到委派结果后输出保留目录和分支。
4. 保持既有 mock 场景匹配顺序和行为不变。

**验证：** 运行 `python -m py_compile tests/e2e_mock_openai_server.py`，再运行 `python -c "from tests.e2e_mock_openai_server import Handler; h=object.__new__(Handler); body={'messages':[{'role':'user','content':'Worktree 隔离子 Agent'}],'tools':[{'function':{'name':'delegate_agent'}}]}; assert h._tool_calls(body)[0]['name']=='delegate_agent'"`，期望两个命令退出码均为 0；再用包含 active Worktree 提示的 payload 调用同一方法，期望首个工具名为 `write_file`。

## T32: 运行聚焦测试与静态编译检查

**文件：** 所有本功能修改文件  
**依赖：** T27、T29、T30、T31  
**步骤：**
1. 运行 Worktree、子 Agent、配置、提示、记忆、工具和 TUI 聚焦测试。
2. 运行 `compileall` 检查全部源码语法和导入。
3. 修复失败并重复执行，直到全部通过。

**验证：** 运行 `python -m pytest tests/test_worktrees.py tests/test_subagents.py tests/test_config.py tests/test_prompting.py tests/test_memory_instructions.py tests/test_tools.py tests/test_tui_smoke.py -q && python -m compileall -q src/mewcode tests/e2e_mock_openai_server.py`，期望退出码为 0。

## T33: 运行全量回归测试

**文件：** 全项目  
**依赖：** T32  
**步骤：**
1. 运行完整 pytest 套件。
2. 记录测试总数、耗时和失败详情。
3. 修复本功能引入的回归并重新运行全量测试。

**验证：** 运行 `python -m pytest -q`，期望全部测试通过且无未处理异步任务告警。

## T34: 在 tmux 中完成端到端验收

**文件：** `tests/e2e_mock_openai_server.py`、临时 Git 仓库与临时 MewCode 配置  
**依赖：** T31、T33  
**步骤：**
1. 在 `/tmp` 创建临时 Git 仓库、初始提交、`isolation: worktree` 可写角色和指向 mock provider 的项目配置。
2. 在 tmux 中启动 mock provider 和 MewCode，从临时仓库输入真实请求：“请派一个 Worktree 隔离子 Agent 创建 isolated.txt，并告诉我隔离目录和分支。”
3. 捕获 pane 输出，确认主 Agent 调用 `delegate_agent`，子 Agent 调用 `write_file`，最终报告 retained 路径和分支。
4. 在 tmux 外检查主仓库没有 `isolated.txt`、保留 Worktree 中存在该文件、主分支与进程 cwd 未改变。
5. 对照已批准 `checklist.md` 逐项记录证据。

**验证：** 运行 tmux 场景并执行 `tmux capture-pane -p -S -200`，期望看到工具调用完成和 Worktree 保留信息；随后用文件系统与 Git 命令确认隔离结果。

## 执行顺序

```text
T1 → T2 → T3 ─┬→ T4
              └→ T10 → T11 → T12 ───────────────┐
T1 → T5 ───────────────────────────────────┐      │
T1 → T6 → T7 ─┬→ T8 ──────────────────────┼→ T14 → T15 ───────┐
              └→ T9 ──────────────────────┘   │    │           │
T1 + T3 ───────────────────────────────→ T13 ─┘    └→ T16 → T17 → T18 → T19
T20 → T21 ───────────────────────────────────────────────┐
T1 + T5 → T22 ─┬→ T23 ──────────────────────────────────┼→ T25 → T26 → T27
               └→ T24 ──────────────────────────────────┘          │
T19 ───────────────────────────────────────────────────────────→ T25
T15 + T17 + T18 → T28 ─────────────────────────────────────────────┤
T24 + T25 + T26 + T28 → T29 ───────────────────────────────────────┤
T4 + T5 + T26 → T30 ───────────────────────────────────────────────┤
T26 → T31 ─────────────────────────────────────────────────────────┘
T27 + T29 + T30 + T31 → T32 → T33 → T34
```
