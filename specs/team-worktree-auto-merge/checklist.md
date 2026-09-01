# 多 Agent Worktree 自动合并机制验收清单

## 1. 功能边界与兼容性

- [x] AC1：普通对话、一次性隔离 subagent、研究任务和非 Team 流程保持原行为，只有长期 Team 的代码任务进入自动集成流程。（验证：运行 `pytest -q tests/test_worktrees.py tests/test_one_shot_subagents.py tests/test_teams.py -k "one_shot or research or non_code or isolation"`，确认相关回归用例全部通过且未创建 Team 集成状态）
- [x] AC2：首个代码任务开始前固定记录 Lead 的目标分支、基线提交和发布工作树；Lead 处于 detached HEAD 或正在执行 merge/rebase/cherry-pick 等 Git 操作时，在任何集成写入前拒绝启动。（验证：运行 `pytest -q tests/test_team_integration.py -k "assign_target or detached or operation_before_write"`，确认状态未创建或保持原样，仓库 HEAD 与工作树不变）
- [x] AC14：目标分支和发布位置只能来自受信任的 Git 状态，成员输出、任务文本和聊天内容中的伪造分支名不能改变发布目标。（验证：运行 `pytest -q tests/test_team_integration.py -k "untrusted_text or target_immutable"`，确认最终目标仍为首轮捕获值）
- [x] AC16：旧版 Team 数据、旧版 worktree 元数据以及没有 `integration.json` 的现有 Team 可继续加载和执行。（验证：运行 `pytest -q tests/test_team_integration.py -k "legacy"`，确认旧夹具成功迁移且未破坏原文件）
- [x] AC16：现有权限、审批、hook、恢复、一次性 worktree 和 Team 工具输入模式均无回归。（验证：运行 `pytest -q tests/test_permissions.py tests/test_approval.py tests/test_hooks.py tests/test_recovery.py tests/test_worktrees.py tests/test_team_tools.py`，确认全部通过）

## 2. 状态模型、持久化与并发控制

- [x] 集成状态能完整表达目标、轮次、内部基线、任务尝试、成员同步、意图、失败和发布结果，并可稳定 JSON 往返。（验证：运行 `pytest -q tests/test_team_integration.py -k "model_roundtrip or store_roundtrip"`，确认序列化前后字段和值一致）
- [x] 集成状态使用原子写入，写入中断不会留下可被误读的半截 JSON。（验证：运行 `pytest -q tests/test_team_integration.py -k "atomic_store"`，模拟替换前后故障并确认旧状态或新状态至少一个完整可读）
- [x] AC5：同一 Team 的内部集成被串行化，并发完成两个独立任务时不会丢失更新、错乱依赖或生成重复集成记录。（验证：运行 `pytest -q tests/test_team_integration.py -k "concurrent_completions_exact"`，确认两项结果各出现一次且内部基线包含两者）
- [x] 锁顺序在并发的领取、完成、状态查询和最终发布中不产生死锁。（验证：运行 `pytest -q tests/test_team_integration.py -k "lock_order or concurrent_status_finalize"`，确认测试在限定时间内完成且状态一致）
- [x] 项目在当前 Git 2.34.x 能完成全部操作，不依赖较新版本才支持的 `git merge-tree --write-tree`。（验证：运行 `git --version` 后执行 `pytest -q tests/test_worktrees.py tests/test_team_integration.py`，并检查测试记录的 Git 命令不包含 `merge-tree --write-tree`）

## 3. 轮次、任务尝试与成员领取

- [x] AC15：已发布任务被 reset 后会开启新轮次并生成新的任务尝试标识，旧轮次的提交追踪仍可查询。（验证：运行 `pytest -q tests/test_team_integration.py -k "multi_round or reset_published_task"`，确认新旧轮次记录同时存在且互不覆盖）
- [x] AC15：同一轮次、同一任务尝试的重复完成回调是幂等的，不会再次合并或重复发布。（验证：运行 `pytest -q tests/test_team_integration.py -k "attempt_idempotency or multi_round"`，确认提交数量和集成记录数量均不增加）
- [x] AC3：有依赖的代码任务只能在依赖任务已进入当前内部基线后领取，不能仅凭依赖任务自报完成而解锁。（验证：运行 `pytest -q tests/test_team_tasks.py tests/test_team_integration.py -k "dependency_requires_integration"`，确认未集成时拒绝、集成后允许）
- [x] AC3：成员领取代码任务前会同步到当前内部基线，使其工作树能够读取已集成的上游改动。（验证：运行 `pytest -q tests/test_team_tasks.py tests/test_team_integration.py -k "prepare_claim_sync or dependent_reads_upstream"`，确认成员 HEAD 等于内部基线且文件内容可见）
- [x] AC3：成员工作树含已跟踪修改、未跟踪文件、额外本地提交或进行中的 Git 操作时，领取新代码任务会被拒绝且现场不变。（验证：运行 `pytest -q tests/test_team_integration.py -k "claim_rejects_dirty or claim_rejects_untracked or claim_rejects_extra_commit or claim_rejects_operation"`，确认文件、HEAD 和操作状态均未被改写）
- [x] AC4：只有当前任务、当前成员、当前尝试、当前轮次的已提交源码才能进入内部基线。（验证：运行 `pytest -q tests/test_team_integration.py -k "source_validation or stale_attempt or foreign_member or old_round"`，确认非法来源被拒绝且内部 HEAD 不变）
- [x] AC4：成员源码工作树不干净或提交与任务登记值不一致时，任务不能标记完成，内部集成状态也不能前进。（验证：运行 `pytest -q tests/test_team_integration.py -k "dirty_source or commit_mismatch"`，确认任务仍非 completed 且内部 HEAD 不变）

## 4. 内部增量集成

- [x] AC5、AC9：每个成功代码任务以可追踪的 `--no-ff` 合并提交进入轮次内部基线，合并提交父节点能指向上一内部基线和成员提交。（验证：运行 `pytest -q tests/test_team_integration.py -k "merge_intent_parents or successful_integration"`，确认父提交顺序与任务追踪字段一致）
- [x] AC5：同一任务完成通知、恢复流程和重试多次触发时，内部基线最多包含一次该任务结果。（验证：运行 `pytest -q tests/test_team_integration.py -k "integration_idempotency or repeated_callback"`，确认第二次调用返回同一结果且不新增提交）
- [x] AC6：两个任务修改相同内容产生冲突时，第二个任务不标记完成，Lead 不变，成员提交和先前已集成结果均保留。（验证：运行 `pytest -q tests/test_team_integration.py -k "merge_conflict"`，确认任务状态、三个相关 HEAD 和成员提交可达性符合预期）
- [x] AC6：冲突失败会报告冲突路径、成员工作树位置、内部集成位置和可执行的修复提示。（验证：运行 `pytest -q tests/test_team_integration.py tests/test_team_manager.py -k "conflict_actionable"`，确认输出包含实际冲突文件与两个绝对路径）
- [x] AC6：合并冲突后会安全中止内部工作树的 merge；若中止失败则进入明确阻塞状态，不会把未知 Git 状态当成干净基线继续集成。（验证：运行 `pytest -q tests/test_worktrees.py tests/test_team_integration.py -k "merge_conflict_aborts or merge_abort_failure"`，确认 Git 操作状态与阻塞结果准确）
- [x] 成员任务只有在内部合并及状态落盘都确认成功后才标记 completed；内部合并失败时任务保留为可诊断、可修复状态。（验证：运行 `pytest -q tests/test_team_integration.py tests/test_team_tasks.py -k "task_complete_callback or integration_failure_keeps_task"`，确认任务和集成状态不会出现一边成功一边失败）

## 5. 最终发布与成员同步

- [x] AC7：存在 running、failed、cancelled、冲突或尚未集成的代码任务时，最终发布被拒绝并列出阻塞任务。（验证：运行 `pytest -q tests/test_team_integration.py -k "finalize_blocked_tasks"`，逐类确认 Lead HEAD 不变且阻塞项完整）
- [x] AC7：仅包含研究任务的 Team 不创建空合并提交，也不执行无意义发布。（验证：运行 `pytest -q tests/test_team_integration.py -k "finalize_no_code"`，确认 Lead HEAD 和提交数量不变）
- [x] AC8：Lead 发布工作树存在已跟踪修改、未跟踪文件、目标分支变化、HEAD 漂移或进行中的 Git 操作时，发布均被拒绝并保留可重试现场。（验证：运行 `pytest -q tests/test_team_integration.py -k "publish_preflight"`，覆盖五类条件并确认内部结果仍在）
- [x] AC9：所有代码任务成功后，Lead 目标分支只通过一次 `--ff-only` 从轮次基线前进到内部基线，发布结果包含所有任务且没有中间部分结果暴露到 Lead。（验证：运行 `pytest -q tests/test_team_integration.py -k "publish_all_at_once"`，确认发布前 Lead 始终位于基线、发布后一次到达内部 HEAD）
- [x] AC9：Lead 上的最终历史可从集成合并提交追踪到任务、成员、任务提交和集成顺序。（验证：运行 `pytest -q tests/test_team_integration.py -k "published_traceability"`，确认所有任务记录与提交父节点可相互对应）
- [x] AC10：外部进程与 JulyCode 竞争推进目标分支时最多一方成功；系统只做快进，不覆盖、不强推，重试不会重复发布。（验证：运行 `pytest -q tests/test_team_integration.py -k "publish_race_exact"`，确认目标分支是某一合法结果且无重复提交）
- [x] AC11：发布后，干净且空闲的成员安全同步到已发布基线；不安全的成员被标记并阻止领取新代码任务，Lead 不受影响。（验证：运行 `pytest -q tests/test_team_integration.py -k "member_sync_after_publish or unsafe_member_blocks_claim"`，确认各成员 sync 状态、HEAD 与领取结果）
- [x] AC11：内部集成 worktree/分支只在确认发布成功且无需恢复后清理；成员 worktree 和冲突现场不会被自动删除。（验证：运行 `pytest -q tests/test_team_integration.py tests/test_worktrees.py -k "cleanup_after_publish or retain_member_worktree"`，确认仅预期内部资源被删除）

## 6. 崩溃恢复、事实核对与多轮运行

- [x] AC12：在“写入集成意图后、执行 Git 前”崩溃，恢复时识别 Git 尚未发生并安全重试，不误报成功。（验证：运行 `pytest -q tests/test_team_integration.py -k "crash_boundary and before_git"`，确认恢复后仅有一次合法合并）
- [x] AC12：在“Git 合并后、完成状态落盘前”崩溃，恢复时根据提交父节点确认事实，不生成第二个合并提交。（验证：运行 `pytest -q tests/test_team_integration.py -k "crash_boundary and after_merge"`，确认合并提交对象和计数保持唯一）
- [x] AC12：在“写入发布意图后、Lead 快进前”崩溃，恢复时保持 Lead 未发布事实并可安全重试。（验证：运行 `pytest -q tests/test_team_integration.py -k "crash_boundary and before_publish"`，确认最终只发生一次快进）
- [x] AC12：在“Lead 快进后、完成状态落盘前”崩溃，恢复时识别目标分支已经到达预期提交并补齐状态，不重复发布。（验证：运行 `pytest -q tests/test_team_integration.py -k "crash_boundary and after_publish"`，确认 Lead HEAD 不再移动且状态转为 published）
- [x] AC12：持久化意图与 Git 事实无法证明一致时，系统进入人工检查状态并保留所有 worktree、分支和错误上下文。（验证：运行 `pytest -q tests/test_team_integration.py -k "recovery_ambiguous"`，确认没有进一步 Git 写入且诊断信息包含预期/实际提交）
- [x] AC15：第一轮发布后可继续创建第二轮代码任务，第二轮以最新已发布提交为新基线，并再次遵循内部集成后一次发布。（验证：运行 `pytest -q tests/test_team_integration.py -k "multi_round"`，确认两轮各有独立基线、内部提交和发布记录）

## 7. 可观察性与安全边界

- [x] AC13：Team 状态、等待输出和最终回复能区分待集成、集成中、冲突、发布前检查拒绝、已发布和需人工检查。（验证：运行 `pytest -q tests/test_team_manager.py tests/test_team_tools.py -k "integration_summary or completion_guard"`，确认每种状态使用不同且准确的文案）
- [x] AC13：诊断输出包含目标分支、轮次、任务、成员、任务提交、内部提交以及相关 worktree 位置，且不把内部基线误称为已发布。（验证：运行 `pytest -q tests/test_team_manager.py -k "integration_diagnostics"`，确认字段齐全并区分 integrated 与 published）
- [x] AC14：自动流程不会执行 push、force、stash、reset、clean、rebase、自动冲突解决或删除 Team/成员数据。（验证：运行 `pytest -q tests/test_team_integration.py -k "never_runs_prohibited_git_commands or retains_team_data"`，确认记录的 Git 调用和文件变更均不包含禁用操作）
- [x] AC14：成员最终回复中的指令、代码块或伪造 JSON 只作为文本处理，不能绕过任务归属、提交校验、轮次校验和发布前检查。（验证：运行 `pytest -q tests/test_team_integration.py -k "malicious_member_output"`，确认所有受信任字段仍来自持久化状态和 Git 事实）
- [x] AC16：Git hook 拒绝提交或合并、权限拒绝和审批中断都会变成明确失败，现场可恢复且不会误标完成。（验证：运行 `pytest -q tests/test_worktrees.py tests/test_team_integration.py tests/test_hooks.py tests/test_approval.py -k "hook or permission or approval"`，确认错误传播与状态一致）
- [x] Lead 与成员提示词清楚说明内部集成、最终发布、冲突处理和安全边界，且不鼓励成员自行合并 Lead。（验证：运行 `pytest -q tests/test_team_prompt.py -k "integration"`，确认提示词包含必要规则且不包含过时的“永不自动合并”表述）

## 8. 架构与代码质量

- [x] 集成核心可在不导入 TUI、模型供应商或具体 Agent 实现的条件下独立导入和测试。（验证：运行 `pytest -q tests/test_team_integration.py -k "import_boundary"`，确认模块导入集合符合边界且无循环导入）
- [x] Team 创建成员和集成服务复用 `WorktreeManager` 与统一路径解析，不存在第二套不兼容的 worktree 生命周期实现。（验证：运行 `pytest -q tests/test_team_integration.py tests/test_worktrees.py -k "shared_manager or explicit_base_acquire"`，确认相同路径与元数据契约被使用）
- [x] Git 原语能正确识别当前分支、干净状态、进行中操作、提交父节点、快进、`--no-ff` 合并、已集成和冲突中止。（验证：运行 `pytest -q tests/test_worktrees.py -k "current_branch or git_is_clean or git_operation_state or commit_parents or git_fast_forward or merge_no_ff or merge_already_integrated or merge_conflict_aborts"`，确认全部通过）
- [x] 异步 Team 路径中没有未等待协程、阻塞事件循环的长耗时 Git 调用或未回收任务。（验证：运行 `pytest -q tests/test_team_integration.py tests/test_team_manager.py -W error::RuntimeWarning`，确认无协程和资源警告）
- [x] 所有新增 Python 文件使用中文注释说明非显然约束，公共类型有准确类型标注，格式与现有项目一致。（验证：运行项目既有 lint/type-check 命令；若项目未配置对应命令，则人工审阅新增文件并在本清单验收记录中注明）

## 9. 自动化测试、文档与端到端验收

- [x] 新增与修改模块能够被 Python 编译器完整加载。（验证：运行 `python -m compileall -q src tests`，退出码为 0）
- [x] 自动合并机制的聚焦测试全部通过。（验证：运行 `pytest -q tests/test_worktrees.py tests/test_team_integration.py tests/test_team_tasks.py tests/test_team_manager.py tests/test_team_prompt.py tests/test_team_tools.py tests/test_team_e2e.py`，结果全部通过）
- [x] AC16：项目完整测试套件无回归。（验证：运行 `pytest -q`，结果全部通过）
- [x] README 和 Team 文档准确描述“任务先进入内部基线、全部成功后 Lead 一次快进发布”、冲突处理、恢复方法和不执行的危险 Git 操作。（验证：运行文档测试并人工对照 `README.md`、`spec.md`、`plan.md`，确认不存在与实现冲突的旧说明）
- [x] AC17：进程内端到端成功场景包含至少两个成员和一条依赖链；下游读到上游改动，Lead 在最终阶段前不变，最终只发布一次。（验证：运行 `pytest -q tests/test_team_e2e.py -k "two_members_dependency_publish_once"`，确认提交图和对话结果符合预期）
- [x] AC17：进程内端到端冲突场景保持 Lead 不变，并向用户返回冲突文件与保留现场位置。（验证：运行 `pytest -q tests/test_team_e2e.py -k "conflict_keeps_lead"`，确认 Lead HEAD、冲突路径和 worktree 路径）
- [x] AC17：按 AGENTS.md 在 tmux 中启动 JulyCode，发送一段真实对话请求，让两个成员依次修改有依赖关系的代码；观察工具调用、内部集成和最终回复，确认 Lead 最终一次性发布完整结果。（验证：保存 tmux 会话输出，记录发布前后 `git rev-parse HEAD` 与 `git log --graph --oneline`，逐项对照本清单）
- [x] AC17：在 tmux 中再发送一段会让两个成员修改同一行的真实请求；确认 JulyCode 报告冲突文件与现场位置，Lead HEAD 不变且成员提交仍可恢复。（验证：保存 tmux 会话输出，并记录冲突前后 Lead HEAD、成员提交和相关 worktree 路径）

## 10. 最终交付判定

- [x] `spec.md` 的 AC1–AC17 均至少由一项自动化测试或 tmux 观察证据覆盖，未通过项有明确原因和后续处理。（验证：逐项核对本清单中的 AC 编号、测试输出和 tmux 记录，不接受仅凭代码阅读勾选）
- [x] 所有实现任务与本清单全部完成后，再把功能声明为交付完成。（验证：核对 `task.md` 的 T1–T49 和本文件所有复选框均已完成，并在最终回复中列出测试结果与任何已知限制）

## 验收记录（2026-08-04）

- 聚焦自动化：`pytest -q tests/test_worktrees.py tests/test_team_integration.py tests/test_team_store.py tests/test_team_tasks.py tests/test_team_runtime.py tests/test_team_tools.py tests/test_teams_integration.py tests/test_prompting.py tests/test_team_e2e.py tests/test_config.py`，结果 `268 passed`。
- 完整回归：`pytest -q`，结果 `933 passed in 52.87s`，未出现未等待协程、任务销毁或资源泄漏警告。
- 编译与格式：`python -m compileall -q src/julycode tests/e2e_mock_openai_server.py` 和 `git diff --check` 均以退出码 0 完成；项目未配置独立 lint/type-check 命令。
- 进程内真实 TUI：成功场景与冲突场景均通过；成功场景包含 alice、bob、两个并行代码任务和一个依赖代码任务，下游实际读取 `team-bob.txt` 后生成 `team-summary.txt`；冲突场景保持 Lead 不变。
- tmux 安全拒绝证据：首次验收故意暴露出 Lead 仓库内未跟踪的临时 HOME，两个成员 claim 均收到“Lead 工作树不干净”，Lead HEAD 未变化，证明发布/领取前安全检查生效。
- tmux 成功证据：仓库 `/tmp/julycode-team-e2e-success2.kQ76wZ` 从 `b5157e4` 一次发布到 `74c9dea`；3 个任务均为 completed，`integration.json` 归档为 published，accepted 共 3 条；提交图含 3 个双亲 `JulyCode integrate` 提交，Lead 最终包含 `team-alice.txt`、`team-bob.txt` 和依赖产物 `team-summary.txt`。
- tmux 冲突证据：仓库 `/tmp/julycode-team-e2e-conflict2.jNx5Qz` 的 Lead 始终保持 `e75f913` 且 `shared.txt` 保持 `base`；alice 结果进入内部提交 `97223e3`，bob 提交 `15e1949` 保留，状态为 blocked，冲突路径为 `shared.txt`，成员与内部 Worktree 绝对位置均写入错误/状态记录。
- 本次创建的 `julycode-success2-0804`、`julycode-conflict-0804` 和 `julycode-server-0804` 临时 tmux 会话已在验收后关闭；临时 Git 仓库保留用于复核。
