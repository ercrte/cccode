# JulyCode 多 Agent Worktree 自动合并 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/julycode/teams/integration.py` | 集成状态事务、轮次状态机、恢复、发布与成员同步 |
| 新建 | `tests/test_team_integration.py` | 真实 Git 的自动集成、冲突、发布、恢复和并发测试 |
| 修改 | `src/julycode/worktrees/models.py` | Git 操作与合并结果类型 |
| 修改 | `src/julycode/worktrees/git.py` | 分支/工作区状态、merge、abort、ff-only 和父节点查询 |
| 修改 | `src/julycode/worktrees/manager.py` | 显式 base 创建与已发布内部 Worktree 安全删除 |
| 修改 | `src/julycode/worktrees/__init__.py` | 导出新增 Worktree/Git 稳定类型 |
| 修改 | `src/julycode/teams/models.py` | task attempt/round、成员同步、集成状态与摘要模型 |
| 修改 | `src/julycode/teams/paths.py` | 集成状态文件和锁路径 |
| 修改 | `src/julycode/teams/store.py` | 初始化集成状态、旧数据兼容和成员同步字段级更新 |
| 修改 | `src/julycode/teams/tasks.py` | 创建、重置、领取、完成和删除接入集成生命周期 |
| 修改 | `src/julycode/teams/manager.py` | 集成服务装配、恢复、快照和 Lead 完成守卫 |
| 修改 | `src/julycode/teams/runtime.py` | 成员状态更新保留同步字段 |
| 修改 | `src/julycode/teams/tools.py` | 现有工具输出集成摘要且输入 schema 不变 |
| 修改 | `src/julycode/teams/__init__.py` | 导出用户可见集成类型 |
| 修改 | `src/julycode/prompting/builder.py` | 渲染 Lead 集成状态和成员同步状态 |
| 修改 | `src/julycode/tui/app.py` | TeamManager 复用现有 WorktreeManager |
| 修改 | `tests/test_worktrees.py` | Git 原语、显式 base 和已合并删除保护测试 |
| 修改 | `tests/test_team_store.py` | 新模型持久化、旧 JSON 和字段级更新测试 |
| 修改 | `tests/test_team_tasks.py` | attempt/round、同步领取、完成幂等和删除规则测试 |
| 修改 | `tests/test_team_runtime.py` | 运行状态与成员同步状态并发更新测试 |
| 修改 | `tests/test_teams_integration.py` | TeamManager 恢复、状态与完成守卫测试 |
| 修改 | `tests/test_team_tools.py` | 工具输入兼容与输出摘要测试 |
| 修改 | `tests/test_prompting.py` | 团队集成提示测试 |
| 修改 | `tests/test_team_e2e.py` | TUI 内多成员依赖任务自动发布测试 |
| 修改 | `tests/e2e_mock_openai_server.py` | tmux 成功发布和冲突对话脚本 |
| 修改 | `tests/test_config.py` | README 行为文档断言 |
| 修改 | `README.md` | 自动集成、发布保护、恢复与不做事项说明 |

## T1: 扩展任务尝试与成员同步模型

**文件：** `src/julycode/teams/models.py`、`tests/test_team_store.py`  
**依赖：** 无  
**步骤：**
1. 为 `TeamTask` 增加默认 `attempt=1` 和 `integration_round=1`。
2. 为 `TeamMemberRecord` 增加 `sync_status`、`sync_head`、`sync_error`，校验状态枚举。
3. 让旧 JSON 缺少新字段时使用安全默认值，新 JSON 完整往返。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'integration_fields_backward_compatible or member_sync_round_trip'`，期望全部通过。

## T2: 定义集成领域记录与解析

**文件：** `src/julycode/teams/models.py`、`src/julycode/teams/__init__.py`、`tests/test_team_store.py`  
**依赖：** T1  
**步骤：**
1. 定义 `TaskAttemptRef`、`IntegratedTaskRecord`、`IntegrationIntent`、`IntegrationFailure`。
2. 定义 `IntegrationRoundRecord`、`TeamIntegrationState`、`TeamIntegrationSummary`、`TeamIntegrationFinalizeResult`。
3. 实现严格字典解析，校验枚举、正整数、对象 ID、accepted 唯一性和可选字段类型。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'integration_model_round_trip or integration_model_rejects_invalid'`，期望全部通过。

## T3: 增加集成状态安全路径

**文件：** `src/julycode/teams/paths.py`、`tests/test_team_store.py`  
**依赖：** 无  
**步骤：**
1. 在 `TeamPaths` 增加固定 `integration.json` 和 `integration.lock` 路径。
2. 复用现有规范化边界检查，拒绝符号链接越过团队根。
3. 保持现有 team/tasks/approvals/mailbox/session 路径不变。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'integration_paths or symlink_boundary'`，期望全部通过。

## T4: 实现集成状态事务存储

**文件：** `src/julycode/teams/integration.py`、`tests/test_team_integration.py`  
**依赖：** T2、T3  
**步骤：**
1. 实现 `TeamIntegrationStore.load_or_create()` 和严格状态解析。
2. 实现单次获取 integration.lock 的 async transaction，允许事务内多次原子 `replace()` 而不重入锁。
3. 对每次写入递增 revision，并复用临时文件、`fsync`、原子替换和脱敏错误。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'integration_store_atomic or integration_transaction_serializes'`，期望全部通过且并发事务不会死锁。

## T5: 初始化集成文件并原子更新成员同步字段

**文件：** `src/julycode/teams/store.py`、`tests/test_team_store.py`  
**依赖：** T1、T2、T3、T4  
**步骤：**
1. 新团队创建时写入初始 `integration.json`，失败回滚仍只删除本次空目录。
2. 旧团队缺少文件时由集成存储惰性创建，损坏文件不覆盖。
3. 增加字段级 `update_member_sync()`，在 team.lock 内基于最新成员记录更新同步字段。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'initializes_integration or legacy_missing_integration or update_member_sync'`，期望全部通过。

## T6: 定义 Git 操作与合并结果类型

**文件：** `src/julycode/worktrees/models.py`、`src/julycode/worktrees/__init__.py`、`tests/test_worktrees.py`  
**依赖：** 无  
**步骤：**
1. 定义 `GitOperation`、`GitMergeStatus` 和不可变 `GitMergeOutcome`。
2. 从 Worktree 包入口导出稳定类型。
3. 增加默认值和导入烟雾测试。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'git_merge_outcome_model'`，期望通过。

## T7: 查询当前分支与完整干净状态

**文件：** `src/julycode/worktrees/git.py`、`tests/test_worktrees.py`  
**依赖：** T6  
**步骤：**
1. 实现 `current_branch()`，命名分支返回短名称，detached HEAD 返回 `None`。
2. 实现 `is_clean()`，同时覆盖 staged、tracked、untracked。
3. 保持显式绝对 cwd 且不改变进程 cwd。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'current_branch or git_is_clean'`，期望全部通过。

## T8: 检测 Worktree 进行中的 Git 操作

**文件：** `src/julycode/worktrees/git.py`、`tests/test_worktrees.py`  
**依赖：** T7  
**步骤：**
1. 通过 `git rev-parse --git-path` 定位当前 Worktree 自己的操作标记。
2. 识别 merge、两种 rebase、cherry-pick、revert，正常状态返回 none。
3. 测试主目录与 linked Worktree 的标记互不串扰。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'git_operation_state'`，期望全部通过。

## T9: 查询提交父节点并校验对象

**文件：** `src/julycode/worktrees/git.py`、`tests/test_worktrees.py`  
**依赖：** T7  
**步骤：**
1. 实现 `commit_parents()`，返回按 Git 顺序排列的完整对象 ID。
2. 拒绝不存在对象、非 commit 对象和无法解析输出。
3. 测试根提交、普通提交与双亲 merge commit。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'commit_parents'`，期望全部通过。

## T10: 实现安全 fast-forward

**文件：** `src/julycode/worktrees/git.py`、`tests/test_worktrees.py`  
**依赖：** T7、T8  
**步骤：**
1. 实现 `fast_forward()`，前置要求 clean、无进行中操作、目标为 commit。
2. 使用 `git merge --ff-only` 并验证执行后 HEAD 等于目标且工作区仍 clean。
3. 测试成功、分叉拒绝、dirty 拒绝和 detached 拒绝。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'git_fast_forward'`，期望全部通过。

## T11: 实现无冲突的任务 merge commit

**文件：** `src/julycode/worktrees/git.py`、`tests/test_worktrees.py`  
**依赖：** T8、T9  
**步骤：**
1. 实现 `merge_no_ff()` 的 clean/operation 前检和 argv 调用。
2. 使用固定消息、`--no-edit`、`--no-gpg-sign`，成功后验证双亲与 clean 状态。
3. 来源已经是祖先时返回 already_integrated，不创建重复提交。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'merge_no_ff_success or merge_already_integrated or merge_hook'`，期望全部通过。

## T12: 实现冲突收集与 merge abort

**文件：** `src/julycode/worktrees/git.py`、`tests/test_worktrees.py`  
**依赖：** T8、T11  
**步骤：**
1. 合并失败时区分内容冲突与其他 Git 错误。
2. 用 NUL 分隔读取未合并路径，执行 `merge --abort`。
3. 验证 HEAD、操作状态和 clean 已恢复；无法恢复时返回 failed 而非 conflicted。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'merge_conflict_aborts or merge_abort_failure'`，期望全部通过且无冲突标记留在工作区。

## T13: 支持按显式 base 创建 Worktree

**文件：** `src/julycode/worktrees/manager.py`、`tests/test_worktrees.py`  
**依赖：** T7、T9  
**步骤：**
1. 为 `acquire()` 增加可选 `base_commit`，验证对象属于当前仓库。
2. 首次创建从显式 base 开始，默认调用仍从当前 HEAD 开始。
3. 快速恢复时要求元数据 base 与调用值相同，不调用 Git 修复不匹配目录。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'explicit_base_acquire or explicit_base_recovery'`，期望全部通过。

## T14: 安全删除已发布的内部 Worktree

**文件：** `src/julycode/worktrees/manager.py`、`src/julycode/worktrees/__init__.py`、`tests/test_worktrees.py`  
**依赖：** T8、T9、T13  
**步骤：**
1. 实现 `delete_merged()`，复用路径、元数据、active lease 和目标锁保护。
2. 只允许 clean、无进行中操作且 Worktree HEAD 为 `merged_into` 祖先时删除。
3. 拒绝 dirty、untracked、未合入、错误 lease 和成员外部调用场景。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'delete_merged'`，期望全部通过。

## T15: 建立集成服务、任务端口与轮次分配

**文件：** `src/julycode/teams/integration.py`、`tests/test_team_integration.py`  
**依赖：** T2、T4、T5  
**步骤：**
1. 定义 `IntegrationTaskPort`，避免导入具体 TaskService。
2. 实现 `TeamIntegrationService` 构造、稳定 owner ID、`snapshot()` 和 `close()`。
3. 实现 `assign_round()`：复用未发布轮次，发布/无需发布后递增新轮次。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'assign_round or integration_service_snapshot or owner_id'`，期望全部通过。

## T16: 捕获 Lead 目标并创建内部 Worktree

**文件：** `src/julycode/teams/integration.py`、`tests/test_team_integration.py`  
**依赖：** T8、T13、T15  
**步骤：**
1. 第一次代码 claim 时校验 Lead 命名分支、HEAD、clean 和 operation=none。
2. 从捕获 base 创建本轮 persistent 内部 Worktree并记录路径、分支、owner 和 head。
3. 创建后再次比较 Lead 分支/HEAD，竞态时清理空内部 Worktree并拒绝。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'captures_target or rejects_detached_or_operation or target_race'`，期望全部通过。

## T17: 在代码 claim 前同步成员 Worktree

**文件：** `src/julycode/teams/integration.py`、`src/julycode/teams/store.py`、`tests/test_team_integration.py`  
**依赖：** T5、T10、T16  
**步骤：**
1. 校验成员花名册路径、实际分支、clean、operation 和祖先关系。
2. 将成员 ff-only 到 integration head，并返回同步后的 start commit。
3. 原子写入 current/blocked 同步状态；失败时成员文件、任务状态不变。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'prepare_claim_syncs_member or prepare_claim_rejects_unsafe_member'`，期望全部通过。

## T18: 校验代码任务来源与幂等键

**文件：** `src/julycode/teams/integration.py`、`tests/test_team_integration.py`  
**依赖：** T9、T15、T17  
**步骤：**
1. 校验 task round/attempt、assignee、member 分支、start commit、当前 HEAD 和 clean 状态。
2. 确认 source commit 位于 start commit 之后且属于当前成员结果。
3. 对 accepted 的同键同 commit 返回幂等命中，同键不同 commit 拒绝；未 accepted 的冲突尝试允许新提交重试。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'validates_task_source or accepted_idempotency or conflict_retry_new_commit'`，期望全部通过。

## T19: 持久化 merge intent 并完成成功合并

**文件：** `src/julycode/teams/integration.py`、`tests/test_team_integration.py`  
**依赖：** T11、T18  
**步骤：**
1. Git 写操作前原子写入包含 result text、expected/source 的 merge_task intent。
2. 在内部 Worktree 调用 `merge_no_ff()`，验证新 HEAD 双亲。
3. 构造 `IntegratedTaskRecord`，暂不清除 intent，等待任务状态完成。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'writes_intent_before_merge or integrates_task_with_traceable_parents'`，期望全部通过。

## T20: 持久记录冲突并保持任务未完成

**文件：** `src/julycode/teams/integration.py`、`tests/test_team_integration.py`  
**依赖：** T12、T18、T19  
**步骤：**
1. conflicted outcome 写入 blocked failure，包含 task/member/commit/规范化冲突路径。
2. 清除已安全 abort 的 intent，保留此前 accepted 与 integration head。
3. failed/abort 未确认时保留 intent 和内部现场，错误不得伪装为普通冲突。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'conflict_blocks_without_lead_change or abort_unknown_retains_intent'`，期望全部通过。

## T21: 完成任务状态后提交 accepted 记录

**文件：** `src/julycode/teams/integration.py`、`src/julycode/teams/tasks.py`、`tests/test_team_integration.py`  
**依赖：** T19、T20  
**步骤：**
1. 成功 Git 合并后调用注入的 `complete_task`，只产生一次 completed/outbox。
2. 任务完成后追加 accepted、更新 integration head、清 intent/failure。
3. 回调失败时保留 intent，让恢复流程依据 Git 事实补写，不重复合并。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'task_completes_after_merge or callback_failure_is_recoverable'`，期望全部通过。

## T22: 恢复无 intent 的内部状态

**文件：** `src/julycode/teams/integration.py`、`tests/test_team_integration.py`  
**依赖：** T8、T9、T15  
**步骤：**
1. 恢复内部 lease，并验证元数据路径、分支与记录完全一致。
2. intent 为空时确认实际 HEAD 等于 integration head、工作区 clean、operation=none。
3. 任一不一致进入 recovery blocked，不自动移动或清理分支。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'recover_clean_state or recover_rejects_unexpected_head_or_dirty'`，期望全部通过。

## T23: 恢复 merge intent 的前后边界

**文件：** `src/julycode/teams/integration.py`、`src/julycode/teams/tasks.py`、`tests/test_team_integration.py`  
**依赖：** T21、T22  
**步骤：**
1. HEAD 未变化且无 merge 时保留可重试状态；存在 merge 时收集冲突并安全 abort。
2. HEAD 已变化时严格验证 expected/source 双亲，再调用 `complete_recovered()`。
3. 处理“任务已 completed 但 accepted 未写”边界，不追加第二条 outbox。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'recover_before_merge or recover_after_merge or recover_after_task_complete'`，期望全部通过。

## T24: 导入旧团队已完成代码任务

**文件：** `src/julycode/teams/integration.py`、`tests/test_team_integration.py`  
**依赖：** T16、T18、T23  
**步骤：**
1. 缺少新字段的旧任务按 round/attempt 1 读取。
2. 按依赖拓扑、created_at、task ID 稳定排序已完成代码任务。
3. 在内部 Worktree 导入有效 commit；冲突或缺失进入 blocked，open 阶段不发布 Lead。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'legacy_completed_tasks_import or legacy_import_conflict'`，期望全部通过。

## T25: 判断最终发布条件与研究轮次

**文件：** `src/julycode/teams/integration.py`、`tests/test_team_integration.py`  
**依赖：** T15、T21  
**步骤：**
1. `finalize()` 只查看当前 round 任务，存在非 completed、failed 或 cancelled 时返回 waiting/blocked。
2. 校验所有 completed code attempt 都有 accepted 记录。
3. 仅研究任务时归档 not_needed，不创建 Worktree、分支或空提交。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'finalize_waits_for_tasks or research_round_not_needed'`，期望全部通过。

## T26: 实现 Lead 发布安全预检

**文件：** `src/julycode/teams/integration.py`、`tests/test_team_integration.py`  
**依赖：** T8、T10、T25  
**步骤：**
1. 校验 Lead 当前 branch/HEAD 与轮次 target/base 完全一致。
2. 拒绝 staged、tracked、untracked、detached 及任一进行中 Git 操作。
3. 拒绝 integration Worktree head/clean/operation 不匹配并保留内部成果。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'publish_preflight_rejects_lead_state or publish_preflight_rejects_integration_state'`，期望全部通过。

## T27: 发布、持久化并恢复 publish intent

**文件：** `src/julycode/teams/integration.py`、`tests/test_team_integration.py`  
**依赖：** T10、T23、T26  
**步骤：**
1. 写 publish intent/phase 后在 Lead cwd ff-only 到 integration head。
2. 验证 Lead HEAD、clean 和目标分支，再持久化 published。
3. 恢复时区分“仍在 base 可重试”“已到 integration head 可补写”“其他状态 blocked”。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'publishes_once or recover_publish_before_or_after_ff or publish_unexpected_head'`，期望全部通过。

## T28: 发布后同步成员并清理内部 Worktree

**文件：** `src/julycode/teams/integration.py`、`src/julycode/teams/store.py`、`tests/test_team_integration.py`  
**依赖：** T14、T17、T27  
**步骤：**
1. 对 idle、clean、可快进成员执行 ff-only 并记录 current/sync head。
2. 对 running、dirty、分叉或错误成员记录 pending/blocked warning，不回滚 Lead。
3. 归档轮次后调用 `delete_merged()`；清理失败只追加 cleanup warning。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'syncs_members_after_publish or member_sync_warning_does_not_rollback or cleans_internal_worktree'`，期望全部通过。

## T29: 任务创建与重置分配 round/attempt

**文件：** `src/julycode/teams/tasks.py`、`tests/test_team_tasks.py`  
**依赖：** T1、T15  
**步骤：**
1. TaskService 注入 TeamIntegrationService，create 前获取 round。
2. completed/failed/cancelled 重置为 pending 时递增 attempt、重新分配 round、清理本次结果字段。
3. 同轮新增复用 round，已归档后新增进入下一轮；研究任务同样登记轮次。

**验证：** 运行 `python -m pytest tests/test_team_tasks.py -q -k 'assigns_integration_round or reset_increments_attempt or next_round_after_publish'`，期望全部通过。

## T30: 代码 claim 接入同步而研究 claim 保持不变

**文件：** `src/julycode/teams/tasks.py`、`tests/test_team_tasks.py`  
**依赖：** T17、T29  
**步骤：**
1. code claim 在持久任务状态改变前调用 `prepare_code_claim()`。
2. 使用同步后的 HEAD 记录 start_commit，再执行原有并发 claim 状态机。
3. research claim 不触发 Git/IntegrationService，现有并发单赢家语义不变。

**验证：** 运行 `python -m pytest tests/test_team_tasks.py -q -k 'code_claim_prepares_worktree or research_claim_skips_integration or concurrent_claim'`，期望全部通过。

## T31: 代码 complete 接入集成与重复调用

**文件：** `src/julycode/teams/tasks.py`、`tests/test_team_tasks.py`  
**依赖：** T18、T21、T23、T30  
**步骤：**
1. 保留现有 commit/head/ancestry/clean 校验并委托 `integrate_code_task()`。
2. 实现 `complete_recovered()`，只接受匹配 intent 的 task/attempt/commit。
3. accepted 的同 commit 重复 complete 返回原任务且不新增 outbox，不同 commit 拒绝。

**验证：** 运行 `python -m pytest tests/test_team_tasks.py -q -k 'code_completion_integrates_first or completion_is_idempotent or complete_recovered'`，期望全部通过。

## T32: 保护当前轮次已集成任务的删除

**文件：** `src/julycode/teams/tasks.py`、`src/julycode/teams/integration.py`、`tests/test_team_tasks.py`  
**依赖：** T15、T29、T31  
**步骤：**
1. delete 前调用 `validate_task_delete()`。
2. 拒绝删除当前未发布轮次中已有 accepted attempt 的代码任务。
3. 允许未接受任务和已发布历史任务按原依赖规则删除，integration history 不变。

**验证：** 运行 `python -m pytest tests/test_team_tasks.py -q -k 'delete_integrated_task_guard'`，期望全部通过。

## T33: 在 TeamManager 装配与恢复集成服务

**文件：** `src/julycode/teams/manager.py`、`src/julycode/tui/app.py`、`tests/test_teams_integration.py`  
**依赖：** T24、T25、T28、T29、T31  
**步骤：**
1. `TeamServices` 增加 integration，先构造 IntegrationService 再注入 TaskService。
2. TeamManager 接收 WorktreeManager；TUI 注入 SubAgentManager 已有实例。
3. create/open 调用 recover，shutdown 关闭服务 lease；单元测试构造保持可注入。

**验证：** 运行 `python -m pytest tests/test_teams_integration.py -q -k 'manager_wires_integration or create_open_recover'`，期望全部通过。

## T34: 在快照、等待与提示上下文暴露集成状态

**文件：** `src/julycode/teams/models.py`、`src/julycode/teams/manager.py`、`tests/test_teams_integration.py`  
**依赖：** T2、T33  
**步骤：**
1. 扩展 `TeamSnapshot`、`TeamEventSnapshot`、`TeamPromptContext` 的 integration 字段。
2. status、wait、refresh prompt 使用同一 snapshot，不触发发布副作用。
3. Lead 和成员上下文获得 phase/failure；成员摘要含 sync 状态。

**验证：** 运行 `python -m pytest tests/test_teams_integration.py -q -k 'snapshot_includes_integration or wait_includes_integration or prompt_context_integration'`，期望全部通过。

## T35: 完成守卫自动发布并生成权威结论

**文件：** `src/julycode/teams/manager.py`、`tests/test_teams_integration.py`  
**依赖：** T25、T27、T33、T34  
**步骤：**
1. 有 active/failed/cancelled 任务时保持现有未达成规则且不调用发布。
2. 全部 completed 时调用 finalize，分别处理 blocked、not_needed、published。
3. 用目标分支、最终提交和已集成任务摘要替换“待集成/未自动合并”文案。

**验证：** 运行 `python -m pytest tests/test_teams_integration.py -q -k 'completion_guard_publish or completion_guard_blocked or completion_guard_not_needed'`，期望全部通过。

## T36: 保证成员运行状态不会覆盖同步字段

**文件：** `src/julycode/teams/runtime.py`、`src/julycode/teams/store.py`、`tests/test_team_runtime.py`  
**依赖：** T5、T28、T33  
**步骤：**
1. 审核 running/idle/failed/terminated 更新，始终基于最新成员记录 replace。
2. 让集成字段更新与运行时生命周期更新并发时互不丢失。
3. shutdown 同时释放成员和内部 integration leases，无悬空 active owner。

**验证：** 运行 `python -m pytest tests/test_team_runtime.py -q -k 'preserves_member_sync_fields or releases_integration_lease'`，期望全部通过。

## T37: 渲染 Lead 集成与成员同步提示

**文件：** `src/julycode/prompting/builder.py`、`tests/test_prompting.py`  
**依赖：** T34  
**步骤：**
1. Lead 提示显示 round、phase、target、accepted 和 failure/conflict paths。
2. 成员提示显示 sync status/head/error，并在 blocked 时说明不能领取新 code task。
3. 无团队或 idle 状态保持紧凑，既有提示顺序不变。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q -k 'team_integration_prompt or member_sync_prompt'`，期望全部通过。

## T38: 保持工具输入 schema 并扩展输出

**文件：** `src/julycode/teams/tools.py`、`src/julycode/teams/__init__.py`、`tests/test_team_tools.py`  
**依赖：** T31、T34、T37  
**步骤：**
1. `manage_team status`、`team_wait` 输出包含 integration 摘要。
2. `team_task` 成功结果包含 attempt/round；集成失败沿用结构化 team_error。
3. 断言所有团队工具输入字段与 action 枚举未增加自由 Git 参数。

**验证：** 运行 `python -m pytest tests/test_team_tools.py -q -k 'integration_payload or schema_is_stable'`，期望全部通过。

## T39: 验证四个崩溃恢复边界

**文件：** `tests/test_team_integration.py`、`src/julycode/teams/integration.py`  
**依赖：** T23、T27  
**步骤：**
1. 注入“merge 前”“merge 后 task 前”“task 后 accepted 前”故障。
2. 注入“publish ff 后 published 前”故障。
3. 每次重建服务并 recover，断言无重复 merge/outbox、无误报、成果不丢。

**验证：** 运行 `python -m pytest tests/test_team_integration.py -q -k 'crash_boundary'`，期望四个边界全部通过。

## T40: 验证并发完成串行化

**文件：** `tests/test_team_integration.py`、`src/julycode/teams/integration.py`  
**依赖：** T19、T21、T31  
**步骤：**
1. 两个成员从同一 base 产生不冲突提交并并发 complete。
2. 证明 integration.lock 使 merge 顺序串行且 accepted 无重复。
3. 验证最终树同时包含两项成果、Lead 发布前保持 base。

**验证：** 运行 `python -m pytest tests/test_team_integration.py::test_concurrent_completions_serialize_without_lost_results -q`，期望通过。

## T41: 验证发布与外部分支更新竞态

**文件：** `tests/test_team_integration.py`、`src/julycode/teams/integration.py`  
**依赖：** T26、T27  
**步骤：**
1. 在发布预检与 ff-only 之间注入外部目标分支推进。
2. 断言 JulyCode 发布失败且不覆盖外部提交、不 force update。
3. 恢复时进入 blocked，内部分支和 Worktree 保留。

**验证：** 运行 `python -m pytest tests/test_team_integration.py::test_publish_race_never_overwrites_external_commit -q`，期望通过。

## T42: 验证多轮、任务重置与成员回同步

**文件：** `tests/test_team_integration.py`、`tests/test_team_tasks.py`  
**依赖：** T28、T29、T31  
**步骤：**
1. 发布 round 1 后重置同一任务，断言 attempt/round 递增。
2. 成员从已发布 head 开始 round 2，并只集成新提交一次。
3. 历史保留两轮记录，旧提交仍可追溯。

**验证：** 运行 `python -m pytest tests/test_team_integration.py tests/test_team_tasks.py -q -k 'multi_round or reset_after_publish'`，期望全部通过。

## T43: 更新既有团队测试夹具与兼容断言

**文件：** `tests/test_team_store.py`、`tests/test_team_tasks.py`、`tests/test_team_runtime.py`、`tests/test_team_tools.py`、`tests/test_teams_integration.py`  
**依赖：** T1、T29、T33、T38  
**步骤：**
1. 更新位置参数构造为不会因新增默认字段错位的构造方式。
2. 为不需要真实集成的测试注入 fake integration/worktree 服务。
3. 保留原团队 CRUD、审批、邮箱、恢复、权限和并发断言。

**验证：** 运行 `python -m pytest tests/test_team_store.py tests/test_team_tasks.py tests/test_team_runtime.py tests/test_team_tools.py tests/test_teams_integration.py -q`，期望全部通过。

## T44: 更新 TUI 成功发布端到端脚本

**文件：** `tests/e2e_mock_openai_server.py`、`tests/test_team_e2e.py`  
**依赖：** T35、T36、T38、T43  
**步骤：**
1. 让 mock Lead 创建至少两个 code task 和一个跨成员依赖 task。
2. 上游成员提交后，下游成员读取上游文件再产生自己的提交。
3. 断言最终 Lead branch 包含全部文件、任务提交可追溯、回复显示 published 而非待集成。

**验证：** 运行 `python -m pytest tests/test_team_e2e.py::test_real_tui_team_end_to_end_without_tmux_wrapper -q`，期望通过。

## T45: 增加 TUI 冲突端到端脚本

**文件：** `tests/e2e_mock_openai_server.py`、`tests/test_team_e2e.py`  
**依赖：** T20、T35、T44  
**步骤：**
1. 让两个成员从同一 base 修改同一行并提交。
2. 断言第二项 complete 返回冲突、任务未完成、Lead HEAD/文件不变。
3. 断言回复/状态包含冲突路径、内部 Worktree和成员成果保留位置。

**验证：** 运行 `python -m pytest tests/test_team_e2e.py::test_real_tui_team_conflict_keeps_lead_unchanged -q`，期望通过。

## T46: 更新 README 与文档断言

**文件：** `README.md`、`tests/test_config.py`  
**依赖：** T35、T38  
**步骤：**
1. 把长期团队“不自动合并”更新为内部增量集成和最终发布语义。
2. 说明 Lead dirty/分支变化/冲突拒绝、成员同步、跨重启恢复与成果保留。
3. 明确一次性子 Agent、push、PR、自动冲突解决和成员 Worktree 删除仍不在范围。

**验证：** 运行 `python -m pytest tests/test_config.py -q -k 'readme_documents_team_auto_merge or readme_documents_worktree_isolation'`，期望全部通过。

## T47: 运行聚焦自动化测试与编译

**文件：** 所有本功能源文件与测试  
**依赖：** T39、T40、T41、T42、T43、T44、T45、T46  
**步骤：**
1. 运行 Worktree、团队状态、任务、运行时、工具、提示和 E2E 聚焦测试。
2. 编译全部源码与 E2E mock 脚本。
3. 修复所有失败、未等待协程和资源泄漏告警后重跑。

**验证：** 运行 `python -m pytest tests/test_worktrees.py tests/test_team_integration.py tests/test_team_store.py tests/test_team_tasks.py tests/test_team_runtime.py tests/test_team_tools.py tests/test_teams_integration.py tests/test_prompting.py tests/test_team_e2e.py tests/test_config.py -q && python -m compileall -q src/julycode tests/e2e_mock_openai_server.py`，期望退出码为 0 且无异步资源告警。

## T48: 运行全项目回归测试

**文件：** 全项目  
**依赖：** T47  
**步骤：**
1. 运行完整 pytest。
2. 检查没有 `Task was destroyed`、`coroutine was never awaited` 或 Git 临时目录泄漏。
3. 若修复回归，重新运行聚焦测试与全量测试。

**验证：** 运行 `python -m pytest -q`，期望全部通过且无未处理异步告警。

## T49: 使用 tmux 完成真实对话验收

**文件：** `tests/e2e_mock_openai_server.py`、`specs/team-worktree-auto-merge/checklist.md`  
**依赖：** T48、已批准 checklist.md  
**步骤：**
1. 在 `/tmp` 的干净真实 Git 仓库配置 mock provider、两个可写团队角色和 JulyCode。
2. 在 tmux 启动 JulyCode，依次执行含跨成员依赖的成功发布对话和同文件冲突对话。
3. 捕获 pane、检查工具调用、Lead/member/internal 分支与文件状态，并逐项回填 checklist 证据。

**验证：** 运行 tmux 场景后，成功对话的 Lead 分支一次性包含全部成果且回复已发布；冲突对话的 Lead HEAD/文件不变、任务未完成、回复包含冲突路径和保留位置；checklist 每项都有实际证据。

## 执行顺序

```text
T1 → T2 ─┬→ T4 → T5 ───────────────────────────────┐
T3 ──────┘                                           │
T6 → T7 → T8 ─┬→ T10 ─┬→ T11 → T12                 │
        └→ T9 ─┘      └──────────────┐              │
T7 + T9 → T13 → T14                 │              │
T2 + T4 + T5 → T15 → T16 → T17 → T18 → T19 → T20  │
                                      └→ T21 → T22 → T23 → T24
T15 + T21 → T25 → T26 → T27 → T28                  │
T1 + T15 → T29 → T30 → T31 → T32                  │
T24 + T28 + T29 + T31 → T33 → T34 → T35           │
T28 + T33 → T36                                    │
T34 → T37 → T38                                    │
T23 + T27 → T39                                    │
T19 + T21 + T31 → T40                              │
T26 + T27 → T41                                    │
T28 + T29 + T31 → T42                              │
T1 + T29 + T33 + T38 → T43                         │
T35 + T36 + T38 + T43 → T44 → T45                 │
T35 + T38 → T46                                    │
T39 + T40 + T41 + T42 + T43 + T45 + T46 → T47
T47 → T48 → T49
```
