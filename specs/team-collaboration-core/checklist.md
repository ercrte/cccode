# MewCode 长期团队协作内核 Checklist

> 每一项必须通过运行代码或观察真实行为验证；不能以阅读实现代替。验收时记录实际命令输出、临时目录状态和 tmux capture 文本。

## 团队持久化与成员工作目录

- [ ] AC1 创建团队后可查看名称、项目、负责人和用户级持久化位置；销毁并重建 TeamManager 后仍能列出并打开。（验证：运行 `python -m pytest tests/test_team_store.py tests/test_teams_integration.py -q -k 'create_team or list_teams or manager_lifecycle'`，期望全部通过）
- [ ] AC2 每个主会话最多激活一个团队，关闭后数据仍在且可切换；非 Git 项目和不同仓库打开均明确失败。（验证：运行 `python -m pytest tests/test_team_store.py tests/test_teams_integration.py -q -k 'repository_binding or manager_lifecycle or active_team'`，期望全部通过）
- [ ] AC3 花名册完整显示成员名称、角色、协程后端、审批配置、状态、上下文和 Worktree；非法、保留或重复名称在产生目录前失败。（验证：运行 `python -m pytest tests/test_team_store.py tests/test_team_runtime.py -q -k 'member_roster or safe_name or create_member_worktree'`，期望全部通过）
- [ ] AC4 两个成员拥有不同 Worktree/分支并可并行修改同一相对路径，主目录和对方目录不变；重启后目录保持；非 coroutine 后端不降级。（验证：运行 `python -m pytest tests/test_team_runtime.py tests/test_worktrees.py -q -k 'create_member_worktree or parallel_members or persistent_worktree'`，期望全部通过）
- [ ] 长期成员 Worktree 即使 clean 且超过普通保留期也不会被 janitor 删除，一次性子 Agent 的 ephemeral 清理行为不变。（验证：运行 `python -m pytest tests/test_worktrees.py tests/test_subagents.py -q -k 'cleanup_skips_persistent or cleanup_expired_removes_clean or finish_clean'`，期望全部通过）

## 共享任务与 Lead 编排

- [ ] AC5 Lead 收到并行目标后先持久化带依赖任务，再启动成员；可并行任务同时运行，下游保持阻塞。（验证：运行 `python -m pytest tests/test_teams_integration.py::test_lead_decomposes_before_parallel_dispatch -q`，期望通过，并检查记录顺序）
- [ ] AC6 Lead 和成员都能创建、读取、更新和删除任务，全部规定字段、操作者和时间在重启后保持一致。（验证：运行 `python -m pytest tests/test_team_tasks.py -q -k 'task_crud'`，期望全部通过）
- [ ] AC7 不存在依赖、自依赖、直接/间接循环依赖均被拒绝；前置未完成时不能领取，完成后解锁；被依赖任务不能删除。（验证：运行 `python -m pytest tests/test_team_tasks.py -q -k 'dependency_validation or cycle or blocked_state or dependent_delete'`，期望全部通过）
- [ ] AC8 两个成员并发领取同一任务时恰好一个成功；并发更新和注入写入失败后 tasks.json 可解析且已确认数据不丢失。（验证：运行 `python -m pytest tests/test_team_tasks.py::test_concurrent_claim_has_single_winner tests/test_team_store.py -q -k 'concurrent_claim_has_single_winner or atomic_json'`，期望全部通过）
- [ ] 代码任务领取时记录分支起点，只有 clean 且含可达新提交时才能完成；dirty、untracked、旧提交和其他分支提交均被拒绝。（验证：运行 `python -m pytest tests/test_team_tasks.py -q -k 'claim_start_commit or code_completion'`，期望全部通过）

## 工具身份与可见性

- [ ] AC9 未激活团队的主 Agent 只看到生命周期入口；Lead 看到五个团队工具；成员只看到 task/message；defined、fork 和普通子 Agent 看不到任何团队工具。（验证：运行 `python -m pytest tests/test_team_tools.py tests/test_subagents.py tests/test_tui_smoke.py -q -k 'audience_gate or team_tool_visibility or team_tools'`，期望全部通过）
- [ ] 模型参数不能伪造 sender、team 或 actor；ToolContext principal 与花名册不匹配时工具调用失败。（验证：运行 `python -m pytest tests/test_team_tools.py tests/test_teams_integration.py -q -k 'runtime_principal or actor_resolution or sender'`，期望全部通过）
- [ ] 成员角色白名单继续控制基础工具，但无需声明即可获得 task/message；成员不能调用 delegate_agent、manage_team 或 manage_team_member。（验证：运行 `python -m pytest tests/test_team_tools.py -q -k 'member_role_gate'`，期望全部通过）

## 邮箱、锁与协议消息

- [ ] AC10 已注册成员可按名称直接通信，目标邮箱得到发件人不可伪造的未读记录；未知、跨团队、重复、终止或越界名称失败。（验证：运行 `python -m pytest tests/test_team_mailbox.py -q -k 'direct_message or registry'`，期望全部通过）
- [ ] AC11 消息自动获得唯一 ID、时间戳、默认未读和非空摘要，调用方显式正文及摘要保持不变。（验证：运行 `python -m pytest tests/test_team_mailbox.py -q -k 'direct_message and defaults'`，期望通过）
- [ ] AC12 邮箱锁短暂占用会重试，超时明确失败，旧死进程锁可接管，旧活进程锁不可接管；所有路径均无半条消息。（验证：运行 `python -m pytest tests/test_team_store.py tests/test_team_mailbox.py -q -k 'file_lock or stale_lock or mailbox_lock'`，期望全部通过）
- [ ] AC13 广播排除发件人并投递全部有效参与者；单个邮箱失败时逐项结果准确，其他消息不丢失且重试不重复。（验证：运行 `python -m pytest tests/test_team_mailbox.py -q -k 'broadcast'`，期望全部通过）
- [ ] AC14 普通、任务指派、审批请求、批准、驳回、任务完成、任务失败、成员空闲、成员恢复和终止协议均校验必填关联字段；非法结构不落盘。（验证：运行 `python -m pytest tests/test_team_mailbox.py -q -k 'protocol_validation'`，期望十种协议合法用例和非法用例全部通过）
- [ ] AC15 消息成功追加成员会话后才确认已读；追加或恢复失败保持未读；“会话已写、确认前崩溃”重试不会重复上下文。（验证：运行 `python -m pytest tests/test_team_runtime.py tests/test_team_mailbox.py -q -k 'message_delivery_dedup or acknowledge or safe_boundary_delivery'`，期望全部通过）
- [ ] 任务、审批和成员状态已提交但通知未写入邮箱时，outbox 会在重试或重启后补投；邮箱成功后确认前崩溃也不重复。（验证：运行 `python -m pytest tests/test_team_mailbox.py tests/test_team_approvals.py -q -k 'outbox_dispatch or projection_recovery'`，期望全部通过）

## 成员运行、空闲与恢复

- [ ] AC16 运行成员只在完整工具结果后的安全边界接收消息，不中断模型流或工具；空闲成员被唤醒；并发 wake 同名成员只启动一个实例。（验证：运行 `python -m pytest tests/test_team_runtime.py -q -k 'safe_boundary_delivery or wake_single_instance or lost_wakeup'`，期望全部通过）
- [ ] AC17 Lead 与两个成员的消息、权限、上下文、缓存、Hook 和 cwd 相互隔离；一个成员会话坏尾只截断自身安全边界。（验证：运行 `python -m pytest tests/test_team_runtime.py -q -k 'member_runner_factory or member_session_store or parallel_members'`，期望全部通过）
- [ ] AC18 成员自然结束后协程释放、会话 checkpoint、状态 idle 且 Lead 收到 idle；Worktree、分支、花名册和历史继续存在。（验证：运行 `python -m pytest tests/test_team_runtime.py -q -k 'idle_resume'`，期望通过并检查目录和租约）
- [ ] AC19 同进程和重启后向空闲成员发消息，都恢复同一 session、Worktree 和分支，能引用旧历史并发送 resumed，消息只追加一次。（验证：运行 `python -m pytest tests/test_team_runtime.py tests/test_teams_integration.py -q -k 'idle_resume or idle_member_recovers_across_process_restart'`，期望全部通过）
- [ ] 运行租约带 PID/token/心跳：活实例阻止第二实例，崩溃租约可恢复，取消或关闭后租约释放。（验证：运行 `python -m pytest tests/test_team_runtime.py -q -k 'process_lease'`，期望全部通过）

## 计划审批

- [ ] AC20 需审批成员领取任务后先发送带任务和版本的计划请求；等待期间 read/task/message 可用，write/edit/run_command 和其他副作用均被运行时拒绝。（验证：运行 `python -m pytest tests/test_team_approvals.py tests/test_teams_integration.py -q -k 'approval_request or approval_gate or direct_collaboration_and_approval_cycle'`，期望全部通过）
- [ ] AC21 只有匹配当前审批 ID、任务、成员和版本的 Lead 批准能解锁；其他成员、旧版本、错误任务和重复批准都不能解锁。（验证：运行 `python -m pytest tests/test_team_approvals.py -q -k 'approve or stale or forged or duplicate'`，期望全部通过）
- [ ] AC22 Lead 带理由驳回后成员可提交递增版本；新版本批准前保持只读，旧批准不能复用。（验证：运行 `python -m pytest tests/test_team_approvals.py tests/test_teams_integration.py -q -k 'reject or plan_version or direct_collaboration_and_approval_cycle'`，期望全部通过）
- [ ] 审批记录已落盘但任务投影或通知中断时，恢复会按审批事实修复任务；授权查询不依赖内存布尔值。（验证：运行 `python -m pytest tests/test_team_approvals.py -q -k 'approval_projection_recovery'`，期望全部通过）

## 完成、失败与终止

- [ ] AC23 代码任务完成后任务记录结果和 commit，Lead 与直接受影响成员收到完成协议；失败任务记录原因并发送失败协议。（验证：运行 `python -m pytest tests/test_team_tasks.py tests/test_team_mailbox.py -q -k 'code_completion or task_outbox or outbox_dispatch'`，期望全部通过）
- [ ] AC24 完成、失败、等待审批、终止和恢复都会同步花名册状态、当前任务和最后活动时间；异常 running 会被识别并可重新指派。（验证：运行 `python -m pytest tests/test_team_store.py tests/test_team_runtime.py -q -k 'reconcile_interrupted or idle_resume or terminate_member'`，期望全部通过）
- [ ] AC25 终止 running、awaiting_approval 或 idle 成员后无运行协程和任务占用，收到终止协议；session、Worktree、分支和成果均保留。（验证：运行 `python -m pytest tests/test_team_runtime.py -q -k 'terminate_member'`，期望全部通过）
- [ ] AC26 有 pending/blocked/running/awaiting/failed/cancelled 时 Lead 不给成功结论；全部 completed 后汇总任务、commit、分支并注明未自动合并；无法恢复失败时输出未达成。（验证：运行 `python -m pytest tests/test_teams_integration.py -q -k 'lead_completion_guard'`，期望所有状态分支通过）

## 架构、故障隔离与兼容性

- [ ] `teams` 配置默认值和显式值可加载，非正超时及 retry/timeout 非法组合在启动前失败。（验证：运行 `python -m pytest tests/test_config.py -q -k 'teams_config'`，期望全部通过）
- [ ] 团队模型、路径、锁、存储、任务、审批、邮箱和事件模块可独立导入，不依赖 TUI widget。（验证：运行 `python -c "from mewcode.teams import TeamConfig, TeamManager; from mewcode.teams.locking import FileLock; from mewcode.teams.tasks import TaskService; print(TeamConfig())"`，期望成功输出）
- [ ] 共享 JSON 更新使用锁和原子替换；注入 replace、fsync 或解析失败后旧快照保持完整，其他团队不受影响。（验证：运行 `python -m pytest tests/test_team_store.py -q -k 'atomic_json or file_lock'`，期望全部通过）
- [ ] 协程成员所有文件、命令、Hook、上下文和记忆操作使用各自绝对 cwd，不调用全局 chdir。（验证：运行 `python -m pytest tests/test_team_runtime.py tests/test_worktrees.py -q -k 'member_runner_factory or parallel_members or no_chdir'`，期望全部通过）
- [ ] TeamManager、成员、邮箱、outbox、恢复或通知单点失败不导致主 TUI 退出，后续普通输入和其他成员仍可继续。（验证：运行 `python -m pytest tests/test_tui_smoke.py tests/test_teams_integration.py -q -k 'team_notification or team_shutdown or failure'`，期望全部通过）
- [ ] 团队名称、成员名称和配置不能越过 `~/.mewcode/teams/<team>`；团队/项目数据互相隔离。（验证：运行 `python -m pytest tests/test_team_store.py tests/test_team_mailbox.py -q -k 'team_paths or safe_name or repository_binding or cross_team'`，期望全部通过）
- [ ] 用户可见团队错误、状态、审批和恢复消息为中文并经过敏感信息脱敏。（验证：运行 `python -m pytest tests/test_team_tools.py tests/test_tui_smoke.py -q -k 'chinese_error or redacted or team_notification'`，期望全部通过）
- [ ] 团队业务规则在 OpenAI 与 Anthropic provider 下产生相同任务、消息、审批和恢复状态。（验证：运行 `python -m pytest tests/test_teams_integration.py -q -k 'provider_parity'`，期望两个 provider 参数化用例通过）
- [ ] AC27 普通聊天、Plan Mode、权限、Hook、上下文、会话恢复、Skill、MCP、一次性子 Agent、后台通知和 ephemeral Worktree 无回归。（验证：运行 `python -m pytest tests/test_agent.py tests/test_permissions.py tests/test_hooks.py tests/test_context_manager.py tests/test_session_recovery.py tests/test_skills.py tests/test_mcp_manager.py tests/test_subagents.py tests/test_worktrees.py tests/test_tui_smoke.py -q`，期望全部通过）
- [ ] README 记录 teams 配置、生命周期、任务、消息、审批、idle/resume、用户数据位置和协程-only/不自动合并边界。（验证：运行 `python -m pytest tests/test_config.py::test_readme_documents_team_collaboration -q`，期望通过）

## 编译与测试

- [ ] 源码与 E2E mock 可编译。（验证：运行 `python -m compileall -q src/mewcode tests/e2e_mock_openai_server.py`，期望退出码为 0）
- [ ] 团队领域专项测试全部通过。（验证：运行 `python -m pytest tests/test_team_store.py tests/test_team_tasks.py tests/test_team_approvals.py tests/test_team_mailbox.py -q`，期望全部通过）
- [ ] 团队运行时、工具和集成测试全部通过。（验证：运行 `python -m pytest tests/test_team_runtime.py tests/test_team_tools.py tests/test_teams_integration.py -q`，期望全部通过）
- [ ] 相关聚焦回归测试全部通过。（验证：运行 `python -m pytest tests/test_agent.py tests/test_tool_scheduler.py tests/test_worktrees.py tests/test_subagents.py tests/test_prompting.py tests/test_config.py tests/test_tui_smoke.py -q`，期望全部通过）
- [ ] 全量测试通过且无未处理协程、销毁任务或资源告警。（验证：运行 `python -m pytest -q`，期望全部通过，输出不含 `Task was destroyed`、`coroutine was never awaited` 或资源告警）
- [ ] 项目未配置 lint 工具时不增加伪 lint 门禁；若开发期间加入 lint 配置则执行对应命令。（验证：检查 `pyproject.toml` 的 lint 配置；当前预期仍以 compileall 和 pytest 为门禁）

## tmux 端到端场景

- [ ] AC28 场景 1——完整团队闭环：在临时 Git 仓库的 tmux 中启动 mock provider 和 MewCode，输入“创建长期团队，拆成两个并行代码任务和一个依赖任务；派两个成员，其中 reviewer 每次任务需审批，让成员直接协作并汇总结果”。Lead 先写任务图再 spawn；两个协程成员并行，至少一条消息不经 Lead，reviewer 经计划驳回、修改、批准后写入；全部成员 idle，Lead 列出 commit/branch 并明确未合并。（验证：使用 `tmux new-session`/`split-window` 启动 `tests/e2e_mock_openai_server.py` 和 `mewcode`，用 `tmux send-keys` 输入请求，`tmux capture-pane -p -S -500` 保存证据；检查 `~/.mewcode/teams/<team>/`、`git worktree list`、tasks/approvals/mailboxes 文件和主目录内容）
- [ ] 场景 2——跨重启恢复：完成场景 1 后退出 MewCode，不删除用户团队目录或 Worktree；在新 tmux 会话从同一仓库重启，要求打开原团队并给原成员发送“继续说明你之前改了什么”。成员使用原 session/Worktree/branch 恢复，回复引用先前工作，Lead 收到 resumed，邮箱中该消息只出现一次。（验证：对比重启前后 member/session 路径和 branch，捕获第二个 tmux pane 输出，并统计对应 message ID 与会话 metadata 均为一次）
- [ ] 场景 3——终止与失败隔离：在 tmux 中启动团队任务，让一个成员等待审批、另一个成员运行；要求 Lead 终止等待成员并向运行成员发送后续消息。被终止成员任务释放且文件保留，另一成员继续完成，主 TUI 可继续普通对话。（验证：捕获终止协议、花名册状态和后续回复；检查被终止成员 Worktree/session 仍存在且无对应运行租约）
- [ ] 场景 4——非团队兼容：在同一构建中用新会话执行普通文件读取、Plan Mode 和一次性 `delegate_agent`；模型请求日志中普通主 Agent 仅有 lifecycle 团队入口，一次性子 Agent 无团队工具，原流程均完成。（验证：tmux 中依次发送普通读取请求、`/plan` 请求和子 Agent 委派请求，捕获输出并检查 mock request log 的 tools 字段）

## 验收记录（2026-06-22）

- `python -m compileall -q src/mewcode tests/e2e_mock_openai_server.py`：通过。
- `python -m pytest -q`：644 passed，无未等待协程、销毁任务或资源告警。
- `python -m pytest tests/test_team_e2e.py -q -s`：1 passed。真实 `MewCodeApp` 完成三个带依赖任务、两个长期成员、代码提交、计划审批、成员直连、Lead 汇总及跨应用重启恢复；验证了固定 session、Worktree、resumed 和恢复后回复。
- E2E 期间发现 Git SHA 被通用敏感信息规则脱敏，模型无法回传 commit；已改为任务服务从 clean Worktree HEAD 自动记录，并继续校验提交严格晚于领取起点。
- tmux 场景未勾选：当前执行沙箱禁止创建 Unix socket，显式 `/tmp/mewcode-e2e-1420.sock` 仍返回 `Operation not permitted`；TCP mock socket同样被拒绝。进程内 TUI 验收覆盖业务链路，但不冒充 tmux capture 证据。
