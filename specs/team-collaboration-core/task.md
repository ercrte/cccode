# MewCode 长期团队协作内核 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/mewcode/teams/__init__.py` | 团队子系统公共导出 |
| 新建 | `src/mewcode/teams/models.py` | 团队、成员、任务、审批、消息、outbox 和快照模型 |
| 新建 | `src/mewcode/teams/paths.py` | 用户目录、名称和路径边界 |
| 新建 | `src/mewcode/teams/locking.py` | 文件锁、原子 JSON 和运行租约 |
| 新建 | `src/mewcode/teams/store.py` | 团队与花名册持久化 |
| 新建 | `src/mewcode/teams/tasks.py` | 任务 CRUD、依赖图和状态机 |
| 新建 | `src/mewcode/teams/approvals.py` | 审批状态机和授权查询 |
| 新建 | `src/mewcode/teams/mailbox.py` | 注册表、邮箱、协议和广播 |
| 新建 | `src/mewcode/teams/events.py` | 持久 outbox 幂等投递 |
| 新建 | `src/mewcode/teams/sessions.py` | 成员会话保存、恢复和消息去重 |
| 新建 | `src/mewcode/teams/policy.py` | audience、角色和审批工具门禁 |
| 新建 | `src/mewcode/teams/runtime.py` | 成员 Runner、协程和运行租约 |
| 新建 | `src/mewcode/teams/manager.py` | 团队生命周期和领域服务编排 |
| 新建 | `src/mewcode/teams/tools.py` | 团队模型工具 |
| 修改 | `src/mewcode/providers/base.py` | ChatMessage 本地 metadata |
| 修改 | `src/mewcode/session.py` | 带 metadata 的用户消息追加 |
| 修改 | `src/mewcode/memory/session_store.py` | 持久化消息 metadata |
| 修改 | `src/mewcode/tools/base.py` | RuntimePrincipal 和 ToolContext |
| 修改 | `src/mewcode/tools/scheduler.py` | 可组合 ToolGate |
| 修改 | `src/mewcode/agent.py` | AgentLoopController 与完成审查挂点 |
| 修改 | `src/mewcode/prompting/base.py` | TeamPromptContext 接入 |
| 修改 | `src/mewcode/prompting/builder.py` | Lead/成员运行提示 |
| 修改 | `src/mewcode/config.py` | TeamConfig 加载和校验 |
| 修改 | `src/mewcode/worktrees/models.py` | persistent retention 元数据 |
| 修改 | `src/mewcode/worktrees/manager.py` | persistent acquire/release/cleanup |
| 修改 | `src/mewcode/worktrees/git.py` | 任务提交祖先与分支校验 |
| 修改 | `src/mewcode/subagents/runtime.py` | 普通子 Agent 运行身份适配 |
| 修改 | `src/mewcode/tui/app.py` | TeamManager、工具、通知与关闭流程 |
| 修改 | `README.md` | 团队使用方式和范围说明 |
| 新建 | `tests/test_team_store.py` | 模型、路径、锁和 TeamStore 测试 |
| 新建 | `tests/test_team_tasks.py` | 任务与依赖测试 |
| 新建 | `tests/test_team_approvals.py` | 审批状态机测试 |
| 新建 | `tests/test_team_mailbox.py` | 邮箱、广播和 outbox 测试 |
| 新建 | `tests/test_team_runtime.py` | 成员运行时测试 |
| 新建 | `tests/test_team_tools.py` | 工具 schema、身份和可见性测试 |
| 新建 | `tests/test_teams_integration.py` | Lead 闭环和跨重启集成测试 |
| 修改 | `tests/test_session.py` | 消息 metadata 追加回归 |
| 修改 | `tests/test_session_store.py` | 消息 metadata 序列化回归 |
| 修改 | `tests/test_tool_scheduler.py` | 多门禁组合回归 |
| 修改 | `tests/test_agent.py` | loop controller 回归 |
| 修改 | `tests/test_prompting.py` | Team prompt 测试 |
| 修改 | `tests/test_config.py` | teams 配置测试 |
| 修改 | `tests/test_worktrees.py` | persistent Worktree 测试 |
| 修改 | `tests/test_subagents.py` | 子 Agent 团队工具隔离回归 |
| 修改 | `tests/test_tui_smoke.py` | TUI 团队组装和关闭测试 |
| 修改 | `tests/e2e_mock_openai_server.py` | 团队端到端模型脚本 |

## T1: 建立团队领域模型

**文件：** `src/mewcode/teams/models.py`、`tests/test_team_store.py`
**依赖：** 无

**步骤：**
1. 定义 `TeamConfig`、`TeamRecord`、`TeamMemberRecord`、`TeamTask`、`ApprovalRecord`、`TeamMessage`、`OutboxEvent` 及状态 Literal。
2. 增加模型默认值、时间字段和字典序列化往返测试，确保未知状态被解析层拒绝。

**验证：** 运行 `python -m pytest tests/test_team_store.py::test_team_models_round_trip -q`，期望通过。

## T2: 接入 teams 配置

**文件：** `src/mewcode/config.py`、`tests/test_config.py`
**依赖：** T1

**步骤：**
1. 给 `AppConfig` 增加 `teams`，解析 enabled、锁超时、重试、旧锁和 wait 超时。
2. 拒绝非正数以及 retry 大于 timeout 等不合理组合，保持未配置时默认值。

**验证：** 运行 `python -m pytest tests/test_config.py -q -k 'teams_config'`，期望合法和非法配置用例全部通过。

## T3: 实现团队路径与名称边界

**文件：** `src/mewcode/teams/paths.py`、`tests/test_team_store.py`
**依赖：** T1

**步骤：**
1. 实现固定用户根目录、`TeamPaths` 和 ASCII 安全团队/成员名称校验。
2. 覆盖绝对路径、空段、遍历、反斜杠、保留名 `lead`、过长名称和 symlink 越界。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'team_paths or safe_name'`，期望全部通过。

## T4: 实现文件锁重试与超时

**文件：** `src/mewcode/teams/locking.py`、`tests/test_team_store.py`
**依赖：** T2、T3

**步骤：**
1. 用排他创建实现带 token、PID 和时间戳的 `FileLock.acquire/release`。
2. 测试短暂占锁后重试成功、超时失败、错误 token 不删除他人锁。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'file_lock and (retry or timeout or token)'`，期望全部通过。

## T5: 实现旧锁安全接管

**文件：** `src/mewcode/teams/locking.py`、`tests/test_team_store.py`
**依赖：** T4

**步骤：**
1. 增加锁所有者存活判断和旧锁接管，删除前复核锁文件身份。
2. 测试旧死进程锁可接管、旧活进程锁不可接管、竞争接管至多一方成功。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'stale_lock'`，期望全部通过。

## T6: 实现原子 JSON 快照

**文件：** `src/mewcode/teams/locking.py`、`tests/test_team_store.py`
**依赖：** T4

**步骤：**
1. 实现 `AtomicJsonFile.read/replace/mutate`，使用同目录临时文件、fsync 和 replace。
2. 模拟 replace 前失败和并发 mutate，验证旧快照完整且 revision 无丢失更新。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'atomic_json'`，期望全部通过。

## T7: 实现成员运行租约

**文件：** `src/mewcode/teams/locking.py`、`tests/test_team_runtime.py`
**依赖：** T4、T5

**步骤：**
1. 实现带 PID、token、心跳和显式释放的 `ProcessLease`。
2. 验证活租约阻止第二实例、心跳续租、崩溃租约过期恢复和取消后释放。

**验证：** 运行 `python -m pytest tests/test_team_runtime.py -q -k 'process_lease'`，期望全部通过。

## T8: 持久化 ChatMessage 本地 metadata

**文件：** `src/mewcode/providers/base.py`、`src/mewcode/session.py`、`src/mewcode/memory/session_store.py`、`tests/test_session.py`、`tests/test_session_store.py`
**依赖：** 无

**步骤：**
1. 给 `ChatMessage` 和 `append_user_message` 增加可选本地 metadata，并在会话 JSON 中保存/恢复。
2. 确认 Provider 请求转换忽略本地 metadata，现有 provider_payload 往返不变。

**验证：** 运行 `python -m pytest tests/test_session.py tests/test_session_store.py -q -k 'metadata or round_trip'`，期望全部通过。

## T9: 给工具上下文增加不可伪造运行身份

**文件：** `src/mewcode/tools/base.py`、`tests/test_team_tools.py`
**依赖：** T1

**步骤：**
1. 定义 `RuntimePrincipal`，给 `ToolContext` 增加默认 main principal。
2. 测试默认兼容、team member principal 固定化和普通工具忽略 principal 时行为不变。

**验证：** 运行 `python -m pytest tests/test_team_tools.py tests/test_tools.py -q -k 'runtime_principal or tool_context'`，期望全部通过。

## T10: 把工具策略改为可组合门禁

**文件：** `src/mewcode/tools/scheduler.py`、`tests/test_tool_scheduler.py`
**依赖：** T9

**步骤：**
1. 定义 `ToolGate` 协议，让 `ToolPolicy` 对 allowed specs 和 validate call 使用多个 gate。
2. 保留 Plan Mode、Skill 白名单和原 `SubAgentToolFilter` 兼容适配，测试任一门禁拒绝即阻断执行。

**验证：** 运行 `python -m pytest tests/test_tool_scheduler.py -q -k 'composed_gate or sub_agent or plan_mode'`，期望全部通过。

## T11: 增加 Agent Loop 安全边界回调

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`
**依赖：** T8、T10

**步骤：**
1. 定义可选 `AgentLoopController.before_iteration`，在用户消息已追加且每次模型请求前调用。
2. 增加 `append_user_message=False` 入口，测试控制器注入消息发生在完整工具结果之后且普通 Runner 行为不变。

**验证：** 运行 `python -m pytest tests/test_agent.py -q -k 'loop_controller_before_iteration or runs_multiple_tool'`，期望全部通过。

## T12: 增加候选完成审查

**文件：** `src/mewcode/agent.py`、`tests/test_agent.py`
**依赖：** T11

**步骤：**
1. 实现 `CompletionDecision` 和 `review_completion` 调用，支持接受、替换回复或注入 continuation。
2. 测试 continuation 继续下一迭代、替换消息被持久化、无 controller 时完成路径不变。

**验证：** 运行 `python -m pytest tests/test_agent.py -q -k 'completion_decision or completion_controller'`，期望全部通过。

## T13: 渲染 Lead 与成员团队提示

**文件：** `src/mewcode/prompting/base.py`、`src/mewcode/prompting/builder.py`、`tests/test_prompting.py`
**依赖：** T1

**步骤：**
1. 给运行提示上下文增加 `TeamPromptContext`，分别渲染 Lead 和 member 标签块。
2. 覆盖 roster、任务、当前审批、Worktree、角色正文、协作约束以及无团队时不输出。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q -k 'team_prompt'`，期望全部通过。

## T14: 扩展 Worktree 元数据兼容持久保留

**文件：** `src/mewcode/worktrees/models.py`、`src/mewcode/worktrees/manager.py`、`tests/test_worktrees.py`
**依赖：** 无

**步骤：**
1. 给元数据增加 `retention`，读取旧元数据时默认 `ephemeral`，写新元数据包含明确值。
2. 测试旧版本快速恢复、新字段类型校验和现有元数据测试兼容。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'retention_metadata or fast_recovery'`，期望全部通过。

## T15: 实现 persistent Worktree acquire/release

**文件：** `src/mewcode/worktrees/manager.py`、`tests/test_worktrees.py`
**依赖：** T14

**步骤：**
1. 给 `acquire` 增加默认兼容的 retention 参数，并实现只释放 active 映射的 `release`。
2. 验证 persistent Worktree release 后目录和分支存在、可跨 manager 恢复，ephemeral finish 行为不变。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'persistent_worktree or finish_clean'`，期望全部通过。

## T16: 阻止 janitor 清理长期 Worktree

**文件：** `src/mewcode/worktrees/manager.py`、`tests/test_worktrees.py`
**依赖：** T15

**步骤：**
1. 在 cleanup 候选校验后跳过 `persistent` 元数据，并返回可诊断原因。
2. 测试过期且 clean 的 persistent Worktree 仍保留，ephemeral 候选仍可清理。

**验证：** 运行 `python -m pytest tests/test_worktrees.py -q -k 'cleanup_skips_persistent or cleanup_expired_removes_clean'`，期望全部通过。

## T17: 实现团队创建与列表存储

**文件：** `src/mewcode/teams/store.py`、`tests/test_team_store.py`
**依赖：** T1、T3、T6

**步骤：**
1. 实现 `TeamStore.create/list`，原子初始化 team、task、approval 和 lead 邮箱文件。
2. 测试重复名称、非 Git 项目、创建中断和列表跳过坏团队目录。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'create_team or list_teams'`，期望全部通过。

## T18: 实现团队加载与仓库绑定

**文件：** `src/mewcode/teams/store.py`、`tests/test_team_store.py`
**依赖：** T17

**步骤：**
1. 实现 `TeamStore.load` 的 schema、revision、路径和 repository identity 校验。
2. 验证同仓库恢复成功、其他仓库拒绝、坏 JSON 和未知 schema 不产生写入。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'load_team or repository_binding'`，期望全部通过。

## T19: 实现花名册原子更新

**文件：** `src/mewcode/teams/store.py`、`tests/test_team_store.py`
**依赖：** T17

**步骤：**
1. 实现成员查询、添加和状态更新，递增 revision 与更新时间。
2. 测试重复成员、保留名、并发更新、backend 非 coroutine 和陈旧 revision 拒绝。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'member_roster'`，期望全部通过。

## T20: 识别并持久化中断成员

**文件：** `src/mewcode/teams/store.py`、`tests/test_team_store.py`
**依赖：** T7、T19

**步骤：**
1. 实现 `reconcile_interrupted`，只把无有效租约的 running 成员改为 failed 并返回其任务 ID。
2. 保持 awaiting_approval 和有效运行租约成员不变，记录中文中断原因。

**验证：** 运行 `python -m pytest tests/test_team_store.py -q -k 'reconcile_interrupted'`，期望全部通过。

## T21: 实现成员会话创建与恢复

**文件：** `src/mewcode/teams/sessions.py`、`tests/test_team_runtime.py`
**依赖：** T3、T4、T8

**步骤：**
1. 实现团队目录内单成员 JSONL session recorder、create/load 和 checkpoint。
2. 测试消息与摘要恢复、坏尾跳过、合法坏行后继续读取和未配对工具调用安全截断。

**验证：** 运行 `python -m pytest tests/test_team_runtime.py -q -k 'member_session_store'`，期望全部通过。

## T22: 实现邮箱消息会话去重

**文件：** `src/mewcode/teams/sessions.py`、`tests/test_team_runtime.py`
**依赖：** T21

**步骤：**
1. 从 ChatMessage metadata 提取已交付 `team_message_id`，提供原子追加外部消息入口。
2. 模拟“会话已写、邮箱未确认”崩溃窗口，验证恢复时不重复追加同 ID。

**验证：** 运行 `python -m pytest tests/test_team_runtime.py -q -k 'message_delivery_dedup'`，期望全部通过。

## T23: 实现共享任务基础 CRUD

**文件：** `src/mewcode/teams/tasks.py`、`tests/test_team_tasks.py`
**依赖：** T1、T6、T17

**步骤：**
1. 实现任务文件解析、create/get/list/update/delete 和操作者/时间字段。
2. 测试重启往返、缺失任务、非法状态转换和任务 ID 唯一性。

**验证：** 运行 `python -m pytest tests/test_team_tasks.py -q -k 'task_crud'`，期望全部通过。

## T24: 校验任务依赖图

**文件：** `src/mewcode/teams/tasks.py`、`tests/test_team_tasks.py`
**依赖：** T23

**步骤：**
1. 在创建和更新依赖时检查不存在、自依赖、直接环和间接环。
2. 保证校验失败前后 tasks.json 字节内容不变。

**验证：** 运行 `python -m pytest tests/test_team_tasks.py -q -k 'dependency_validation or cycle'`，期望全部通过。

## T25: 实现阻塞状态重算和删除保护

**文件：** `src/mewcode/teams/tasks.py`、`tests/test_team_tasks.py`
**依赖：** T24

**步骤：**
1. 每次任务变更后重算 pending/blocked 和 blocked_reason，前置全部完成时解锁。
2. 禁止删除被依赖任务，验证失败或取消依赖不会错误解锁下游。

**验证：** 运行 `python -m pytest tests/test_team_tasks.py -q -k 'blocked_state or dependent_delete'`，期望全部通过。

## T26: 实现并发原子领取

**文件：** `src/mewcode/teams/tasks.py`、`tests/test_team_tasks.py`
**依赖：** T25

**步骤：**
1. 实现 `claim` compare-and-set，要求任务可领取、成员有效且当前无人占用。
2. 用 asyncio 并发两个成员领取同一任务，断言仅一方成功且文件可解析。

**验证：** 运行 `python -m pytest tests/test_team_tasks.py::test_concurrent_claim_has_single_winner -q`，期望通过。

## T27: 领取任务时记录审批状态和 Git 起点

**文件：** `src/mewcode/teams/tasks.py`、`tests/test_team_tasks.py`
**依赖：** T15、T19、T26

**步骤：**
1. 代码任务领取时记录成员分支 HEAD；按成员配置进入 in_progress 或 awaiting_approval。
2. 测试非成员、错误 Worktree、已有当前任务以及未满足依赖均不能领取。

**验证：** 运行 `python -m pytest tests/test_team_tasks.py -q -k 'claim_start_commit or claim_approval_state'`，期望全部通过。

## T28: 实现研究任务完成、失败和 outbox

**文件：** `src/mewcode/teams/tasks.py`、`tests/test_team_tasks.py`
**依赖：** T27

**步骤：**
1. 实现 research 任务 complete/fail/cancel，原子清理 assignee 并写完成或失败 outbox。
2. 将 Lead 和直接依赖任务的已分配成员加入收件人，拒绝空结果和重复完成。

**验证：** 运行 `python -m pytest tests/test_team_tasks.py -q -k 'research_completion or task_outbox'`，期望全部通过。

## T29: 校验代码任务提交成果

**文件：** `src/mewcode/teams/tasks.py`、`src/mewcode/worktrees/git.py`、`tests/test_team_tasks.py`
**依赖：** T15、T28

**步骤：**
1. 增加 Git 祖先/分支提交查询，完成 code 任务前检查 clean、commit 可达且晚于 start_commit。
2. 覆盖 dirty、untracked、旧提交、其他分支提交、有效新提交和只读任务无需提交。

**验证：** 运行 `python -m pytest tests/test_team_tasks.py -q -k 'code_completion'`，期望全部通过。

## T30: 实现审批请求和版本递增

**文件：** `src/mewcode/teams/approvals.py`、`tests/test_team_approvals.py`
**依赖：** T6、T23、T27

**步骤：**
1. 实现 `request`，校验成员、当前任务、awaiting_approval 和非空计划。
2. 首次创建 version 1；驳回后新请求递增版本并把旧记录保留为历史。

**验证：** 运行 `python -m pytest tests/test_team_approvals.py -q -k 'approval_request or plan_version'`，期望全部通过。

## T31: 实现批准与驳回状态转换

**文件：** `src/mewcode/teams/approvals.py`、`tests/test_team_approvals.py`
**依赖：** T30

**步骤：**
1. 实现 Lead-only approve/reject，批准记录决定并投影任务为 in_progress，驳回要求非空理由。
2. 状态变更和通知 outbox 同一 approvals 快照提交。

**验证：** 运行 `python -m pytest tests/test_team_approvals.py -q -k 'approve or reject'`，期望全部通过。

## T32: 拒绝陈旧、重复和伪造审批

**文件：** `src/mewcode/teams/approvals.py`、`tests/test_team_approvals.py`
**依赖：** T31

**步骤：**
1. 精确匹配 approval ID、任务、成员、版本和 Lead 身份，终态决定不可重复。
2. 测试其他成员、旧版本、错误任务、重复批准和历史批准不能授权当前任务。

**验证：** 运行 `python -m pytest tests/test_team_approvals.py -q -k 'stale or forged or duplicate'`，期望全部通过。

## T33: 修复审批与任务投影崩溃窗口

**文件：** `src/mewcode/teams/approvals.py`、`src/mewcode/teams/tasks.py`、`tests/test_team_approvals.py`
**依赖：** T28、T32

**步骤：**
1. 实现按审批事实修复任务状态的 reconcile，并让授权查询只读审批事实。
2. 模拟审批已落盘、任务未更新和通知未投递，验证读取或恢复后自动修复。

**验证：** 运行 `python -m pytest tests/test_team_approvals.py -q -k 'approval_projection_recovery'`，期望全部通过。

## T34: 实现名称注册与点对点邮箱

**文件：** `src/mewcode/teams/mailbox.py`、`tests/test_team_mailbox.py`
**依赖：** T1、T5、T6、T17

**步骤：**
1. 从 TeamStore 解析 Lead/成员邮箱，实现普通消息 send/unread 和确定性摘要默认值。
2. 拒绝未知、跨团队、已终止收件人和参数伪造 sender，默认 timestamp/read/id 正确。

**验证：** 运行 `python -m pytest tests/test_team_mailbox.py -q -k 'direct_message or registry'`，期望全部通过。

## T35: 校验全部结构化消息协议

**文件：** `src/mewcode/teams/mailbox.py`、`tests/test_team_mailbox.py`
**依赖：** T33、T34

**步骤：**
1. 为十种协议定义必填 task/approval/version/reason 组合，并路由审批协议到 ApprovalService。
2. 测试每种合法协议可构造，缺字段、额外冲突字段和非法发送方均失败且不写邮箱。

**验证：** 运行 `python -m pytest tests/test_team_mailbox.py -q -k 'protocol_validation'`，期望全部通过。

## T36: 实现邮箱幂等写入和已读确认

**文件：** `src/mewcode/teams/mailbox.py`、`tests/test_team_mailbox.py`
**依赖：** T22、T34

**步骤：**
1. 相同消息 ID 重试只保留一条；`acknowledge` 只更新存在消息并原子保存。
2. 测试锁超时、旧锁接管、失败保持未读和已读确认不改变消息其他字段。

**验证：** 运行 `python -m pytest tests/test_team_mailbox.py -q -k 'idempotent or acknowledge or mailbox_lock'`，期望全部通过。

## T37: 实现广播逐项投递

**文件：** `src/mewcode/teams/mailbox.py`、`tests/test_team_mailbox.py`
**依赖：** T34

**步骤：**
1. 按注册名称排序向除发件人外参与者逐个投递，共享 broadcast_id、独立 message ID。
2. 注入单个邮箱失败，验证其他成功、逐项结果准确且重试不重复。

**验证：** 运行 `python -m pytest tests/test_team_mailbox.py -q -k 'broadcast'`，期望全部通过。

## T38: 实现持久 outbox 补投

**文件：** `src/mewcode/teams/events.py`、`tests/test_team_mailbox.py`
**依赖：** T28、T33、T36、T37

**步骤：**
1. 扫描 team/tasks/approvals 三类 outbox，按 event+recipient 生成确定消息 ID并逐项确认。
2. 模拟邮箱成功后确认前崩溃、锁失败和重启补投，验证消息不重不丢。

**验证：** 运行 `python -m pytest tests/test_team_mailbox.py -q -k 'outbox_dispatch'`，期望全部通过。

## T39: 实现团队 audience 工具门禁

**文件：** `src/mewcode/teams/policy.py`、`tests/test_team_tools.py`
**依赖：** T10、T17

**步骤：**
1. 实现 main、active Lead、team member 和 sub_agent 四类 audience 的允许工具集合。
2. 同时测试 allowed specs 隐藏和 validate call 强行调用拒绝，错误包含身份与原因。

**验证：** 运行 `python -m pytest tests/test_team_tools.py -q -k 'audience_gate'`，期望全部通过。

## T40: 合并角色工具规则与协作工具

**文件：** `src/mewcode/teams/policy.py`、`tests/test_team_tools.py`
**依赖：** T39

**步骤：**
1. 实现成员角色 allow/deny 与固定 team_task/team_message 并集，阻止 delegate_agent 和管理工具。
2. 测试角色未声明协作工具仍可见、基础工具白黑名单继续生效。

**验证：** 运行 `python -m pytest tests/test_team_tools.py -q -k 'member_role_gate'`，期望全部通过。

## T41: 实现审批副作用门禁

**文件：** `src/mewcode/teams/policy.py`、`tests/test_team_approvals.py`
**依赖：** T32、T40

**步骤：**
1. 等待审批时只允许 read_only 基础工具以及 task/message，明确拒绝 write/edit/run_command。
2. 批准后恢复角色允许工具；新任务不能复用旧批准，非审批成员领取后直接可写。

**验证：** 运行 `python -m pytest tests/test_team_approvals.py -q -k 'approval_gate'`，期望全部通过。

## T42: 构造成员独立 Agent Runner

**文件：** `src/mewcode/teams/runtime.py`、`tests/test_team_runtime.py`
**依赖：** T13、T15、T21、T41

**步骤：**
1. 实现 `TeamMemberRunnerFactory`，复用角色模型/提示/权限并创建独立 session、context、cache、Hook 和 cwd。
2. 测试两个成员状态隔离、角色 isolation 被固定 Worktree 覆盖、普通子 Agent Manager 未被复用。

**验证：** 运行 `python -m pytest tests/test_team_runtime.py -q -k 'member_runner_factory'`，期望全部通过。

## T43: 在安全边界投递成员邮箱

**文件：** `src/mewcode/teams/runtime.py`、`tests/test_team_runtime.py`
**依赖：** T11、T22、T36、T42

**步骤：**
1. 实现 `TeamMemberLoopController.before_iteration`，先去重追加会话，再确认已读。
2. 测试消息不插入 assistant tool_calls 与 tool result 之间，追加失败保持未读。

**验证：** 运行 `python -m pytest tests/test_team_runtime.py -q -k 'safe_boundary_delivery'`，期望全部通过。

## T44: 创建长期成员与固定 Worktree

**文件：** `src/mewcode/teams/runtime.py`、`tests/test_team_runtime.py`
**依赖：** T7、T15、T19、T42

**步骤：**
1. 实现 create_member 的角色/backend 校验、persistent Worktree、邮箱/会话初始化和花名册发布顺序。
2. 测试两个成员目录隔离、主 dirty 不进入、失败回滚和非 coroutine 明确报错。

**验证：** 运行 `python -m pytest tests/test_team_runtime.py -q -k 'create_member_worktree'`，期望全部通过。

## T45: 唤醒成员并保证单实例

**文件：** `src/mewcode/teams/runtime.py`、`tests/test_team_runtime.py`
**依赖：** T7、T43、T44

**步骤：**
1. 实现 wake：获取 ProcessLease、加载会话、创建 asyncio.Task，并复用活跃 task。
2. 并发两次 wake 同一成员，验证只启动一个 Runner；不同成员可并行运行。

**验证：** 运行 `python -m pytest tests/test_team_runtime.py -q -k 'wake_single_instance or parallel_members'`，期望全部通过。

## T46: 实现成员 idle 与上下文恢复

**文件：** `src/mewcode/teams/runtime.py`、`tests/test_team_runtime.py`
**依赖：** T38、T43、T45

**步骤：**
1. 自然完成后 checkpoint、标记 idle、写 member_idle outbox 并释放协程；新消息时发送 member_resumed。
2. 结束回调再次检查未读邮箱，测试同进程恢复引用旧历史且临界消息不会丢唤醒。

**验证：** 运行 `python -m pytest tests/test_team_runtime.py -q -k 'idle_resume or lost_wakeup'`，期望全部通过。

## T47: 实现终止与安全关闭

**文件：** `src/mewcode/teams/runtime.py`、`tests/test_team_runtime.py`
**依赖：** T27、T46

**步骤：**
1. terminate 取消 running/awaiting/idle 成员，释放任务和租约，写终止 outbox，保留 Worktree/session。
2. shutdown 并发停止所有活跃成员并等待清理，验证无遗留 asyncio task 告警。

**验证：** 运行 `python -m pytest tests/test_team_runtime.py -q -k 'terminate_member or supervisor_shutdown'`，期望全部通过。

## T48: 实现 TeamManager 生命周期

**文件：** `src/mewcode/teams/manager.py`、`tests/test_teams_integration.py`
**依赖：** T18、T20、T38、T47

**步骤：**
1. 组合 stores/services/supervisor，实现 create/list/open/close/status 和单 active_team。
2. open 时校验仓库、reconcile 中断成员并 flush outbox；close 不停止成员或删除数据。

**验证：** 运行 `python -m pytest tests/test_teams_integration.py -q -k 'manager_lifecycle'`，期望全部通过。

## T49: 解析 Actor 并编排成员操作

**文件：** `src/mewcode/teams/manager.py`、`tests/test_teams_integration.py`
**依赖：** T44、T48

**步骤：**
1. 从 RuntimePrincipal 解析不可伪造 TeamActor，实现 spawn/list/terminate 转发。
2. 验证 main 未激活、跨团队 member、未知成员和普通 sub_agent 都不能取得 Actor。

**验证：** 运行 `python -m pytest tests/test_teams_integration.py -q -k 'actor_resolution or manager_members'`，期望全部通过。

## T50: 消息投递后唤醒正确成员

**文件：** `src/mewcode/teams/manager.py`、`tests/test_teams_integration.py`
**依赖：** T37、T46、T49

**步骤：**
1. manager send/broadcast 在成功投递后逐个通知 Supervisor，Lead 只设置主事件不创建成员 Runner。
2. 测试部分广播失败只唤醒成功目标，running 成员只收到边界事件。

**验证：** 运行 `python -m pytest tests/test_teams_integration.py -q -k 'message_wakes_recipient'`，期望全部通过。

## T51: 实现团队事件等待

**文件：** `src/mewcode/teams/manager.py`、`tests/test_teams_integration.py`
**依赖：** T38、T48

**步骤：**
1. 用 asyncio.Event 实现 wait_for_event，等待前 flush outbox，返回任务、成员和 Lead 未读摘要。
2. 测试事件到达立即返回、超时返回当前快照、取消不丢后续事件。

**验证：** 运行 `python -m pytest tests/test_teams_integration.py -q -k 'wait_for_team_event'`，期望全部通过。

## T52: 实现 Lead 邮箱注入与完成守卫

**文件：** `src/mewcode/teams/manager.py`、`tests/test_teams_integration.py`
**依赖：** T12、T13、T23、T51

**步骤：**
1. 实现 Lead loop controller：安全边界注入 lead 邮箱，按任务状态 review completion。
2. 活跃任务注入 continuation；失败/取消替换为未达成摘要；全部完成追加分支与未合并说明。

**验证：** 运行 `python -m pytest tests/test_teams_integration.py -q -k 'lead_completion_guard or lead_mailbox'`，期望全部通过。

## T53: 实现团队生命周期工具

**文件：** `src/mewcode/teams/tools.py`、`tests/test_team_tools.py`
**依赖：** T39、T48

**步骤：**
1. 实现稳定 `manage_team` schema 和 create/list/open/close/status 路由。
2. 验证缺参、未知 action、非 Git、重复团队和结构化中文错误。

**验证：** 运行 `python -m pytest tests/test_team_tools.py -q -k 'manage_team_tool'`，期望全部通过。

## T54: 实现成员管理工具

**文件：** `src/mewcode/teams/tools.py`、`tests/test_team_tools.py`
**依赖：** T39、T49

**步骤：**
1. 实现 Lead-only `manage_team_member` 的 spawn/list/terminate 和 backend/approval 参数。
2. 验证 member/sub_agent 强行调用、未知角色和不支持 backend 均失败且无花名册残留。

**验证：** 运行 `python -m pytest tests/test_team_tools.py -q -k 'manage_team_member_tool'`，期望全部通过。

## T55: 实现共享任务工具

**文件：** `src/mewcode/teams/tools.py`、`tests/test_team_tools.py`
**依赖：** T23、T49

**步骤：**
1. 实现 `team_task` create/get/list/update/delete/claim 参数解析和结果序列化。
2. 验证 Lead/member 可调用、普通 main/sub_agent 不可见，依赖与状态错误完整回灌模型。

**验证：** 运行 `python -m pytest tests/test_team_tools.py -q -k 'team_task_tool'`，期望全部通过。

## T56: 实现团队消息工具

**文件：** `src/mewcode/teams/tools.py`、`tests/test_team_tools.py`
**依赖：** T35、T50、T39

**步骤：**
1. 实现 `team_message` send/broadcast/read，覆盖协议关联字段和逐项投递结果。
2. sender 只取 ToolContext principal；测试参数中注入 sender 无法伪造身份。

**验证：** 运行 `python -m pytest tests/test_team_tools.py -q -k 'team_message_tool'`，期望全部通过。

## T57: 实现 Lead 等待工具

**文件：** `src/mewcode/teams/tools.py`、`tests/test_team_tools.py`
**依赖：** T39、T51

**步骤：**
1. 实现 Lead-only `team_wait`，校验可选正 timeout 并返回 TeamEventSnapshot。
2. 验证 member/main/sub_agent 被拒绝，超时不会被 ToolExecutor 默认超时提前截断。

**验证：** 运行 `python -m pytest tests/test_team_tools.py -q -k 'team_wait_tool'`，期望全部通过。

## T58: 完成 teams 公共导出和工具集合

**文件：** `src/mewcode/teams/__init__.py`、`src/mewcode/teams/tools.py`、`tests/test_team_tools.py`
**依赖：** T53、T54、T55、T56、T57

**步骤：**
1. 导出 TeamManager、配置、模型、工具常量和 `create_team_tools` 工厂。
2. 断言五个工具名称唯一、schema 稳定、origin 正确且可独立 import。

**验证：** 运行 `python -m pytest tests/test_team_tools.py -q -k 'team_tool_set or public_exports'`，期望全部通过。

## T59: 适配普通子 Agent 的运行身份

**文件：** `src/mewcode/subagents/runtime.py`、`tests/test_subagents.py`
**依赖：** T9、T10、T39

**步骤：**
1. 子 Agent child ToolContext 使用 sub_agent principal，并组合团队 audience gate。
2. 验证 defined/fork 子 Agent 看不到五个团队工具，原有 delegate_agent 防嵌套和角色过滤不变。

**验证：** 运行 `python -m pytest tests/test_subagents.py -q -k 'team_tools or nested or policy'`，期望全部通过。

## T60: 在 TUI 初始化 TeamManager 与工具

**文件：** `src/mewcode/tui/app.py`、`tests/test_tui_smoke.py`
**依赖：** T2、T58、T59

**步骤：**
1. TUI 构造或接收 TeamManager，注册五个团队工具且避免重复注册。
2. teams disabled 时只返回明确关闭状态；普通启动仍能输入和回复。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -q -k 'team_manager_setup or registers_team_tools'`，期望全部通过。

## T61: 给主 Runner 接入团队 gate、提示和 controller

**文件：** `src/mewcode/tui/app.py`、`src/mewcode/agent.py`、`tests/test_tui_smoke.py`
**依赖：** T13、T52、T60

**步骤：**
1. 每次主请求从 TeamManager 获取动态 tool gates、prompt context 和 loop controller。
2. 验证同一轮 open team 后下一迭代出现 Lead 工具，close 后消失，普通聊天提示无团队块。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -q -k 'team_runner_context or team_tool_visibility'`，期望全部通过。

## T62: 接入团队通知和 TUI 安全关闭

**文件：** `src/mewcode/tui/app.py`、`tests/test_tui_smoke.py`
**依赖：** T47、T61

**步骤：**
1. 显示成员 idle/resume/failure 非致命通知，邮箱仍保留为事实来源。
2. on_unmount 等待 TeamManager.shutdown；注入关闭失败时打印脱敏告警且 TUI 正常退出。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py -q -k 'team_notification or team_shutdown'`，期望全部通过且无异步任务告警。

## T63: 编写团队配置与用户流程文档

**文件：** `README.md`、`tests/test_config.py`
**依赖：** T2、T58

**步骤：**
1. 记录 teams 配置、创建/打开、角色、任务、消息、审批、idle/resume 和用户数据目录。
2. 明确协程-only、固定 Worktree、不自动合并及后续阶段范围。

**验证：** 运行 `python -m pytest tests/test_config.py::test_readme_documents_team_collaboration -q`，期望通过。

## T64: 扩展 E2E mock 的团队脚本

**文件：** `tests/e2e_mock_openai_server.py`、`tests/test_tui_smoke.py`
**依赖：** T58、T61

**步骤：**
1. 为 Lead 建任务、spawn、派发、wait、审批和汇总定义确定性工具调用序列。
2. 为两个成员定义直接消息、计划请求、代码提交结果和恢复时引用旧上下文的响应。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_team_e2e_mock_sequence_contract -q`，期望通过。

## T65: 集成验证 Lead 先建任务再派成员

**文件：** `tests/test_teams_integration.py`
**依赖：** T52、T58、T61、T64

**步骤：**
1. 使用 fake provider 驱动 Lead 建立含依赖任务并派生两个成员。
2. 断言首个成员副作用前任务已持久化、并行任务同时 running、下游保持 blocked。

**验证：** 运行 `python -m pytest tests/test_teams_integration.py::test_lead_decomposes_before_parallel_dispatch -q`，期望通过。

## T66: 集成验证成员直连与审批闭环

**文件：** `tests/test_teams_integration.py`
**依赖：** T41、T50、T65

**步骤：**
1. 让成员 A 给 B 发任务消息，B 提交计划，Lead 先驳回再批准。
2. 断言批准前 shell/write 均拒绝，新版本批准后可提交，任务完成消息直达相关成员。

**验证：** 运行 `python -m pytest tests/test_teams_integration.py::test_direct_collaboration_and_approval_cycle -q`，期望通过。

## T67: 集成验证跨重启上下文恢复

**文件：** `tests/test_teams_integration.py`
**依赖：** T20、T22、T46、T65

**步骤：**
1. 完成成员一轮工作后销毁 TeamManager，再从同一团队目录和仓库创建新实例。
2. 给原成员发消息，断言恢复同一 session、Worktree、分支和历史，消息只追加一次。

**验证：** 运行 `python -m pytest tests/test_teams_integration.py::test_idle_member_recovers_across_process_restart -q`，期望通过。

## T68: 运行编译与聚焦回归

**文件：** 上述全部实现与测试文件
**依赖：** T16、T29、T33、T38、T47、T62、T63、T64、T66、T67

**步骤：**
1. 运行 compileall 和团队全部专项测试，修复语法、导入和领域集成失败。
2. 运行 Agent、工具、配置、Worktree、子 Agent、提示和 TUI 聚焦测试，修复相关回归。

**验证：** 分别运行 `python -m compileall -q src/mewcode tests/e2e_mock_openai_server.py` 和 `python -m pytest tests/test_team_store.py tests/test_team_tasks.py tests/test_team_approvals.py tests/test_team_mailbox.py tests/test_team_runtime.py tests/test_team_tools.py tests/test_teams_integration.py tests/test_agent.py tests/test_tool_scheduler.py tests/test_worktrees.py tests/test_subagents.py tests/test_prompting.py tests/test_config.py tests/test_tui_smoke.py -q`，期望退出码均为 0。

## T69: 运行全量回归

**文件：** 上述全部实现与测试文件
**依赖：** T68

**步骤：**
1. 运行全量 pytest，定位并修复聚焦集合之外的兼容性失败。
2. 重跑全量测试，确认没有未处理异步任务、资源告警或偶发并发失败。

**验证：** 运行 `python -m pytest -q`，期望全部通过，且无 `Task was destroyed`、`coroutine was never awaited` 或资源告警。

## 执行顺序

```text
基础运行时：T1 → T2 → T3 → T4 → T5 → T6 → T7
消息/Agent：T8 → T9 → T10 → T11 → T12
提示与 Git：T13；T14 → T15 → T16

团队存储：T17 → T18 → T19 → T20
成员会话：T21 → T22
任务领域：T23 → T24 → T25 → T26 → T27 → T28 → T29
审批领域：T30 → T31 → T32 → T33
邮箱领域：T34 → T35 → T36 → T37 → T38
工具门禁：T39 → T40 → T41

成员运行时：T42 → T43 → T44 → T45 → T46 → T47
团队编排：T48 → T49 → T50 → T51 → T52
模型工具：T53 → T54 → T55 → T56 → T57 → T58
应用集成：T59 → T60 → T61 → T62 → T63 → T64
闭环集成：T65 → T66 → T67 → T68 → T69
```

允许并行的起始分支：T8、T13、T14 可与 T1-T7 并行；T21、T23、T30、T34、T39 在各自依赖满足后可并行。其余任务严格按各任务“依赖”字段执行。
