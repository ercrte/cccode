# MewCode Worktree 隔离 Checklist

> 每一项都通过运行代码、临时 Git 仓库或 tmux 中的真实对话验证；不以阅读实现代码代替验收。
>
> 验收结果（2026-06-21）：38/38 通过；全量测试 564 passed；三个 tmux 场景均通过。

## 兼容性与创建隔离

- [x] AC1 未声明 `isolation` 的 defined 角色和所有 Fork 继续使用主工作目录；未知 isolation 角色不能启动，`delegate_agent` 输入 schema 无变化。（验证：运行 `python -m pytest tests/test_subagents.py -q -k 'isolation or schema_is_stable or fork'`，期望全部通过）
- [x] AC2 `isolation: worktree` defined 角色获得独立目录和分支，主 Agent 的 cwd、分支和未提交文件不变。（验证：运行 `python -m pytest tests/test_subagents.py::test_real_git_isolation_keeps_main_directory_unchanged -q`，期望通过）
- [x] AC3 主工作目录 dirty 时仍可创建 Worktree，隔离目录只包含当前 `HEAD` 内容。（验证：运行 `python -m pytest tests/test_worktrees.py::test_create_git_worktree_excludes_main_uncommitted_changes -q`，期望通过）
- [x] AC4 两个隔离子 Agent 同时修改相同相对路径时互不可见，主目录文件不变。（验证：运行 `python -m pytest tests/test_subagents.py::test_real_git_isolation_parallel_worktrees_do_not_overlap -q`，期望通过）
- [x] AC5 Worktree 位于仓库内专用忽略区域，主工作目录 `git status --porcelain` 不报告该目录。（验证：运行 `python -m pytest tests/test_worktrees.py::test_local_exclude_hides_worktree_storage -q`，期望通过）

## 路径安全与快速恢复

- [x] AC6 合法单段/嵌套名称通过；超长、非法字符、空段、`.`、`..`、绝对路径、反斜杠和解析后越界全部在写操作前失败。（验证：运行 `python -m pytest tests/test_worktrees.py -q -k 'validate_relative_name or resolve_inside'`，期望全部通过）
- [x] AC7 已有无关目录、其他任务目录或冲突分支不被覆盖、重置或复用，并返回明确冲突阶段。（验证：运行 `python -m pytest tests/test_worktrees.py -q -k 'directory_conflict or branch_conflict or task_conflict'`，期望全部通过）
- [x] AC8 首次创建后元数据包含当前仓库、任务、角色、名称、分支、基线和创建时间。（验证：运行 `python -m pytest tests/test_worktrees.py::test_manager_acquire_new_writes_complete_metadata -q`，期望通过）
- [x] AC9 合法已存在目录仅通过文件系统恢复且零 Git；缺失、损坏或不匹配元数据只读失败，目录字节内容不变。（验证：运行 `python -m pytest tests/test_worktrees.py -q -k 'fast_recovery'`，期望全部通过且 fake GitClient/initializer 调用数为 0）

## 显式 cwd、缓存与提示

- [x] AC10 文件工具、命令工具和 Hook 命令均收到隔离绝对 cwd；运行前后 `Path.cwd()` 与主 Agent 工具 cwd 不变。（验证：运行 `python -m pytest tests/test_subagents.py tests/test_worktrees.py -q -k 'real_git_isolation or no_chdir or worktree_hook_cwd'`，期望全部通过）
- [x] AC11 相同相对路径在两个 Worktree 中不会共享文件缓存、项目指令、系统提示、上下文目录或项目记忆。（验证：运行 `python -m pytest tests/test_worktrees.py tests/test_memory_instructions.py tests/test_prompting.py -q -k 'cache_isolated or knowledge_isolated or absolute_cwd'`，期望全部通过）
- [x] AC12 隔离子 Agent 提示包含隔离 cwd、主 cwd、分支和禁止越界约束；最终结果明确显示 cleaned 或 retained 的路径、分支和原因。（验证：运行 `python -m pytest tests/test_prompting.py tests/test_subagents.py -q -k 'worktree_prompt or worktree_payload or completion_notice'`，期望全部通过）

## 环境初始化与 Git hooks

- [x] AC13 `copy_paths` 产生独立副本，`symlink_paths` 指向主目录同路径，`ignored_copy_paths` 补齐声明文件。（验证：运行 `python -m pytest tests/test_worktrees.py -q -k 'copy_paths or symlink_paths or ignored_copy'`，期望全部通过）
- [x] AC14 绝对路径、空路径、遍历、仓库外源、目标越界和自定义目标在执行前被拒绝，未声明忽略内容不复制。（验证：运行 `python -m pytest tests/test_config.py tests/test_worktrees.py -q -k 'worktree_config_path or environment_preflight or undeclared_ignored'`，期望全部通过且失败前目标快照不变）
- [x] AC15 初始化源缺失、目标冲突、类型错误或文件操作失败时子 Agent 不运行，错误包含 `environment` 阶段和原因。（验证：运行 `python -m pytest tests/test_worktrees.py tests/test_subagents.py -q -k 'environment_failure or acquire_environment_error'`，期望全部通过）
- [x] AC16 自定义 hooks 在隔离目录提交时生效；默认共享 hooks 正常；主目录有效 hooks 配置在初始化前后相同。（验证：运行 `python -m pytest tests/test_worktrees.py::test_worktree_inherits_custom_git_hooks tests/test_worktrees.py::test_shared_hooks_need_no_override -q`，期望全部通过）

## 退出与删除保护

- [x] AC17 子 Agent 无修改且无新增提交时自动删除 Worktree 和临时分支，结果报告 cleaned。（验证：运行 `python -m pytest tests/test_worktrees.py::test_finish_clean_removes_worktree_and_branch tests/test_subagents.py::test_manager_worktree_lifecycle_reports_cleaned -q`，期望全部通过）
- [x] AC18 完成、失败或取消后，只要有 dirty、untracked 或新增提交就保留目录和分支，并报告具体原因。（验证：运行 `python -m pytest tests/test_worktrees.py tests/test_subagents.py -q -k 'finish_retains or lifecycle_retains'`，期望全部通过）
- [x] AC19 删除 dirty、untracked 或无 upstream 新增提交的 Worktree 被拒绝，文件和分支保持存在。（验证：运行 `python -m pytest tests/test_worktrees.py -q -k 'protected_delete and (dirty or untracked or no_upstream)'`，期望全部通过）
- [x] AC20 有 upstream 时，存在未推送新增提交会阻止删除；全部新增提交被 upstream 包含且工作树 clean 时允许保护删除。（验证：运行 `python -m pytest tests/test_worktrees.py -q -k 'protected_delete and upstream'`，期望全部通过）

## 后台清理与故障隔离

- [x] AC21 janitor 启动后立即非阻塞执行，随后按间隔重复；默认 3600 秒/7 天，合法覆盖生效，非法时间配置加载失败。（验证：运行 `python -m pytest tests/test_worktrees.py tests/test_config.py tests/test_tui_smoke.py -q -k 'janitor or cleanup_interval or retention_days or worktree_janitor'`，期望全部通过）
- [x] AC22 未过期、active、根外、元数据无效或仓库身份不匹配的候选均不被清理。（验证：运行 `python -m pytest tests/test_worktrees.py -q -k 'cleanup_three_layers or cleanup_skips'`，期望全部通过）
- [x] AC23 过期、clean、全部新增提交已推送且通过三层过滤的 Worktree 被删除；dirty 或未推送候选仍保留。（验证：运行 `python -m pytest tests/test_worktrees.py -q -k 'cleanup_expired_pushed or cleanup_retains_unsafe'`，期望全部通过）
- [x] AC24 坏元数据、Git 状态失败、upstream 可达性未知或删除异常只形成清理失败记录，候选保留，后续候选和 TUI 输入仍可继续。（验证：运行 `python -m pytest tests/test_worktrees.py tests/test_tui_smoke.py -q -k 'cleanup_failure or janitor_failure'`，期望全部通过）
- [x] AC25 同目标并行 acquire 或 finish/cleanup 竞争不会交错破坏状态，至多一个 acquire 成功进入。（验证：运行 `python -m pytest tests/test_worktrees.py -q -k 'concurrent_same_target or finish_cleanup_race'`，期望全部通过）
- [x] AC26 任一创建、初始化、恢复、退出或清理失败后，主 cwd 不变、未知目录未被删除，并可继续执行普通工具或新委派。（验证：运行 `python -m pytest tests/test_subagents.py tests/test_worktrees.py tests/test_tui_smoke.py -q -k 'failure_keeps_main_running or acquire_environment_error or cleanup_failure'`，期望全部通过）

## 架构与集成完整性

- [x] Worktree 领域模块可独立导入，不依赖 TUI、Provider 或 Agent Loop。（验证：运行 `python -c "from mewcode.worktrees import WorktreeConfig, WorktreeManager, WorktreeJanitor; print(WorktreeConfig())"`，期望成功输出默认配置）
- [x] Worktree 管理没有注册新的模型工具或斜杠命令，`delegate_agent` 输入字段保持原集合。（验证：运行 `python -m pytest tests/test_subagents.py::test_delegate_agent_tool_schema_is_stable tests/test_commands.py -q`，期望全部通过）
- [x] defined 隔离角色的工具、权限、Hook、上下文和记忆使用同一个 `SubAgentWorkingContext.cwd`，共享角色与 Fork 仍使用主 cwd。（验证：运行 `python -m pytest tests/test_subagents.py -q -k 'runner_factory or real_git_isolation'`，期望全部通过）
- [x] 完成、模型失败、取消和 runner 构造异常四条路径均执行一次退出处置，不泄漏 active lease。（验证：运行 `python -m pytest tests/test_subagents.py -q -k 'manager_worktree_lifecycle'`，期望全部通过）
- [x] Worktree 创建共享 Git 对象库，未复制完整 `.git` 历史。（验证：运行 `python -m pytest tests/test_worktrees.py::test_create_git_worktree_shares_repository_objects -q`，期望通过）
- [x] README 说明 `isolation: worktree`、环境初始化、默认清理策略、变更保护及不自动合并。（验证：运行 `python -m pytest tests/test_config.py::test_readme_documents_worktree_isolation -q`，期望通过）

## 编译与测试

- [x] 源码和 E2E mock 脚本可编译。（验证：运行 `python -m compileall -q src/mewcode tests/e2e_mock_openai_server.py`，期望退出码为 0）
- [x] Worktree、子 Agent、配置、提示、记忆、工具和 TUI 聚焦测试全部通过。（验证：运行 `python -m pytest tests/test_worktrees.py tests/test_subagents.py tests/test_config.py tests/test_prompting.py tests/test_memory_instructions.py tests/test_tools.py tests/test_tui_smoke.py -q`，期望全部通过）
- [x] 全项目无回归且没有未处理异步任务告警。（验证：运行 `python -m pytest -q`，期望全部通过且输出中没有 `Task was destroyed` 或 `coroutine was never awaited`）

## 端到端场景

- [x] 场景 1：主目录有未提交文件时，请求 Worktree 隔离子 Agent 修改同名文件；主目录内容保持原样，子 Agent 结果报告 retained 目录和分支。（验证：在临时 Git 仓库和 tmux 中启动 mock provider 与 MewCode，输入“请派一个 Worktree 隔离子 Agent 创建 isolated.txt，并告诉我隔离目录和分支”，用 `tmux capture-pane -p -S -200` 确认 `delegate_agent`/`write_file` 成功，再分别读取主目录与返回的 Worktree 路径）
- [x] 场景 2：隔离子 Agent 只读取文件且不产生修改或提交；任务结束后目录和临时分支消失，回复报告 cleaned。（验证：在 tmux 中输入只读隔离委派请求，捕获回复中的 cleaned，再运行 `git worktree list` 和 `git branch --list 'mewcode/*'`，期望找不到对应任务）
- [x] 场景 3：保留目录含未提交文件后触发过期清理；即使元数据时间已过期，目录和分支仍存在，主会话可继续回答下一条请求。（验证：在临时仓库将测试保留期设为短值，等待至少两次清理间隔后检查目录/分支，并在同一 tmux 会话发送“读取 README”，期望工具与回复正常）
