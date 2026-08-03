# JulyCode 会话恢复与长期记忆 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/julycode/session_id.py` | 生成和校验 `YYYYMMDD-HHMMSS-xxxx` 会话 ID |
| 新建 | `src/julycode/memory/__init__.py` | 导出记忆子系统公开类型 |
| 新建 | `src/julycode/memory/models.py` | 定义指令、会话恢复、笔记、索引和后台任务模型 |
| 新建 | `src/julycode/memory/instructions.py` | 三层项目指令加载和 `@include` 展开 |
| 新建 | `src/julycode/memory/session_store.py` | JSONL 会话追加写、扫描、恢复和过期清理 |
| 新建 | `src/julycode/memory/recovery.py` | 协议安全截断和启动恢复编排 |
| 新建 | `src/julycode/memory/notes.py` | Markdown 笔记 frontmatter 读写和敏感信息过滤 |
| 新建 | `src/julycode/memory/index.py` | 用户级和项目级记忆索引生成、读取和裁剪 |
| 新建 | `src/julycode/memory/updater.py` | 自然完成后的无工具 LLM 自动笔记更新 |
| 新建 | `src/julycode/memory/manager.py` | 统一启动、运行时知识上下文和后台任务管理 |
| 修改 | `src/julycode/config.py` | 解析 `memory:` 配置并挂到 `AppConfig` |
| 修改 | `src/julycode/context/models.py` | 让 `ContextState.session_id` 使用新会话 ID 格式 |
| 修改 | `src/julycode/context/manager.py` | 重量压缩成功后追加 JSONL checkpoint |
| 修改 | `src/julycode/session.py` | 支持 recorder、持久化追加和 checkpoint |
| 修改 | `src/julycode/prompting/base.py` | 让 `RuntimePromptContext` 携带 `KnowledgeContext` |
| 修改 | `src/julycode/prompting/builder.py` | 注入项目指令、记忆索引和恢复提醒 |
| 修改 | `src/julycode/prompting/__init__.py` | 导出扩展后的提示类型 |
| 修改 | `src/julycode/agent.py` | 请求前读取最新知识上下文，自然完成后调度自动笔记 |
| 修改 | `src/julycode/tui/app.py` | 显示恢复告警，传递 memory manager |
| 修改 | `src/julycode/cli.py` | 解析 `--new-session`，启动时执行会话恢复 |
| 修改 | `.gitignore` | 忽略 `.julycode/sessions/` 和 `.julycode/memory/` 自动产物 |
| 修改 | `README.md` | 说明项目指令、会话恢复、自动记忆和配置 |
| 新建 | `tests/test_session_id.py` | 覆盖会话 ID 格式和同秒防撞 |
| 新建 | `tests/test_memory_instructions.py` | 覆盖三层指令、include、环路和越界 |
| 新建 | `tests/test_session_store.py` | 覆盖 JSONL 追加、坏行跳过、列表扫描和清理 |
| 新建 | `tests/test_session_recovery.py` | 覆盖默认恢复、空会话、协议截断、时间跨度和预算压缩 |
| 新建 | `tests/test_memory_notes.py` | 覆盖 Markdown 笔记、分类、scope 和脱敏 |
| 新建 | `tests/test_memory_index.py` | 覆盖索引生成、读取和 200 行 / 25KB 上限 |
| 新建 | `tests/test_memory_updater.py` | 覆盖无工具自动笔记更新、操作解析和失败不阻断 |
| 修改 | `tests/test_config.py` | 覆盖 `memory:` 配置解析和非法值 |
| 修改 | `tests/test_session.py` | 覆盖 recorder、JSONL 追加和 checkpoint 调用 |
| 修改 | `tests/test_context_manager.py` | 覆盖重量压缩后 checkpoint 追加 |
| 修改 | `tests/test_prompting.py` | 覆盖知识上下文注入、来源边界和稳定提示不污染 |
| 修改 | `tests/test_agent.py` | 覆盖自然完成更新笔记、非自然停止不更新和运行时知识注入 |
| 修改 | `tests/test_tui_smoke.py` | 覆盖恢复提示展示和 memory manager 传递 |
| 修改 | `tests/test_mcp_manager.py` | 覆盖 CLI 新启动流程不破坏 MCP 初始化 |

## T1: 建立会话 ID

**文件：** `src/julycode/session_id.py`、`tests/test_session_id.py`  
**依赖：** 无  
**步骤：**
1. 新建 `SessionId = NewType("SessionId", str)`。
2. 实现 `new_session_id(now: datetime | None = None) -> SessionId`，输出 `YYYYMMDD-HHMMSS-xxxx`。
3. 实现 `is_valid_session_id(value: str) -> bool`，只接受规范格式。
4. 添加同一秒连续生成多个 ID 不相同的测试。

**验证：** 运行 `python -m pytest tests/test_session_id.py -q`，期望全部通过。

## T2: 定义记忆模型

**文件：** `src/julycode/memory/__init__.py`、`src/julycode/memory/models.py`、`tests/test_session_id.py`  
**依赖：** T1  
**步骤：**
1. 在 `models.py` 定义 `SessionMemoryConfig`，默认值与 `plan.md` 一致。
2. 定义 `InstructionBlock`、`InstructionBundle`、`SessionJsonlRecord`、`SessionInfo`、`RestoreReport`、`KnowledgeContext`、`MemoryNote`、`MemoryIndex`、`MemoryUpdateJob`、`BootstrapOptions`、`BootstrapResult`、`ProtocolValidationResult`。
3. 在 `__init__.py` 导出公开模型。
4. 添加模型默认值测试，确认 `SessionMemoryConfig.retention_days == 30`、`index_max_lines == 200`、`index_max_bytes == 25000`。

**验证：** 运行 `python -m pytest tests/test_session_id.py::test_memory_config_defaults -q`，期望通过。

## T3: 解析 memory 配置

**文件：** `src/julycode/config.py`、`tests/test_config.py`  
**依赖：** T2  
**步骤：**
1. 给 `AppConfig` 增加 `memory: SessionMemoryConfig = field(default_factory=SessionMemoryConfig)`。
2. 新增 `_parse_memory(raw)`，解析 `enabled`、`project_dir`、`sessions_dir`、`memory_dir`、`user_dir`、`instruction_filename`、`include_max_depth`、`auto_restore`、`retention_days`、`time_gap_hours`、`index_max_lines`、`index_max_bytes`、`auto_notes_enabled`。
3. 对空目录名、非正整数和非法对象类型抛出 `ConfigError`。
4. 在配置测试中加入默认、显式配置和非法配置用例。

**验证：** 运行 `python -m pytest tests/test_config.py::test_loads_memory_config tests/test_config.py::test_rejects_invalid_memory_config -q`，期望全部通过。

## T4: 让运行期 session 使用持久会话 ID

**文件：** `src/julycode/context/models.py`、`tests/test_session.py`  
**依赖：** T1  
**步骤：**
1. 将 `ContextState.session_id` 默认值从随机十六进制改为 `new_session_id()`。
2. 保持 `ContextState.summary`、`token_anchor`、失败计数和外置路径行为不变。
3. 调整 `test_session_has_context_state`，断言 session ID 符合新格式。

**验证：** 运行 `python -m pytest tests/test_session.py::test_session_has_context_state -q`，期望通过。

## T5: 为 ChatSession 接入 recorder

**文件：** `src/julycode/session.py`、`tests/test_session.py`  
**依赖：** T2、T4  
**步骤：**
1. 定义 `PersistentSessionRecorder` 协议，包含 `append_message()` 和 `append_checkpoint()`。
2. 给 `ChatSession` 增加可选 recorder 字段和 `set_recorder(recorder)`。
3. 在 `append_user_message()`、`append_assistant_message()`、`append_tool_result()` 成功追加内存消息后调用 recorder。
4. 新增 `append_checkpoint()`，把当前消息和摘要交给 recorder，不改写普通消息。
5. 用 fake recorder 覆盖用户消息、助手消息、工具结果和 checkpoint 调用。

**验证：** 运行 `python -m pytest tests/test_session.py::test_session_recorder_appends_messages tests/test_session.py::test_session_appends_checkpoint_to_recorder -q`，期望全部通过。

## T6: 实现 ChatMessage JSON 序列化

**文件：** `src/julycode/memory/session_store.py`、`tests/test_session_store.py`  
**依赖：** T2  
**步骤：**
1. 实现 `message_to_json(message: ChatMessage) -> dict[str, object]`。
2. 实现 `message_from_json(data: Mapping[str, object]) -> ChatMessage`。
3. 覆盖 `role`、`content`、`thinking`、`tool_calls`、`tool_call_id`、`tool_result_is_error`、`provider_payload`。
4. 对缺失必填字段、未知 role、非法 tool call 结构抛出可读异常。

**验证：** 运行 `python -m pytest tests/test_session_store.py::test_chat_message_round_trip_json tests/test_session_store.py::test_rejects_invalid_message_json -q`，期望全部通过。

## T7: 实现 JSONL 会话创建与追加

**文件：** `src/julycode/memory/session_store.py`、`tests/test_session_store.py`、`.gitignore`  
**依赖：** T5、T6  
**步骤：**
1. 实现 `SessionJsonlStore.__init__()`，解析项目内 `.julycode/sessions` 根目录并确保位于项目目录内。
2. 实现 `create_session()`，创建 `ChatSession` 并设置 `context_state.session_id`。
3. 实现 `attach_recorder()`，把 recorder 绑定到已有 session。
4. 实现 `append_message()`，每条消息追加一行 `kind="message"` JSON。
5. 在 `.gitignore` 添加 `.julycode/sessions/`。
6. 测试用户消息和助手消息被追加为两行 JSONL。

**验证：** 运行 `python -m pytest tests/test_session_store.py::test_store_appends_messages_as_jsonl tests/test_session_store.py::test_store_uses_project_sessions_dir -q`，期望全部通过。

## T8: 实现 JSONL checkpoint

**文件：** `src/julycode/memory/session_store.py`、`tests/test_session_store.py`  
**依赖：** T7  
**步骤：**
1. 实现 `append_checkpoint(session)`，追加 `kind="checkpoint"` 记录。
2. checkpoint 记录当前 `session.messages` 和 `session.context_state.summary`。
3. 确保 checkpoint 不创建 meta 文件。
4. 添加测试恢复 checkpoint 后只保留 checkpoint 中的消息和摘要。

**验证：** 运行 `python -m pytest tests/test_session_store.py::test_store_appends_checkpoint tests/test_session_store.py::test_checkpoint_restores_messages_and_summary -q`，期望全部通过。

## T9: 实现会话扫描和列表信息

**文件：** `src/julycode/memory/session_store.py`、`tests/test_session_store.py`  
**依赖：** T7、T8  
**步骤：**
1. 实现 `list_sessions(now=None)`，扫描 `.jsonl` 文件并计算 `SessionInfo`。
2. 标题取第一条用户消息首行，过长标题做短截断。
3. 消息数只统计恢复后有效消息，不把 checkpoint 事件本身算作消息。
4. 最近更新时间取最后一条有效记录的 `created_at`。
5. 添加删除额外 meta 文件不影响列表计算的测试。

**验证：** 运行 `python -m pytest tests/test_session_store.py::test_list_sessions_scans_jsonl_for_title_count_and_time tests/test_session_store.py::test_list_sessions_does_not_require_meta_file -q`，期望全部通过。

## T10: 实现坏行跳过恢复

**文件：** `src/julycode/memory/session_store.py`、`tests/test_session_store.py`  
**依赖：** T9  
**步骤：**
1. 实现 `load_session(session_id)`，逐行读取 JSONL。
2. 对 JSON 解码失败、结构非法或单条消息非法的记录跳过并累计 warning。
3. 对其他有效记录继续应用。
4. 返回绑定 recorder 的 `ChatSession` 和包含 `skipped_bad_lines` 的 `RestoreReport`。
5. 添加最后一行损坏和中间坏行的恢复测试。

**验证：** 运行 `python -m pytest tests/test_session_store.py::test_load_session_skips_bad_lines tests/test_session_store.py::test_load_session_keeps_valid_lines_after_bad_line -q`，期望全部通过。

## T11: 实现过期会话清理

**文件：** `src/julycode/memory/session_store.py`、`tests/test_session_store.py`  
**依赖：** T9  
**步骤：**
1. 实现 `latest_unexpired(now=None)`，返回最近未过期会话。
2. 实现 `cleanup_expired(now=None)`，删除超过 `retention_days` 未活动的 JSONL 文件。
3. 确保只删除 sessions 目录内的过期 JSONL，不影响 `.julycode/memory` 和指令文件。
4. 添加未过期保留、过期删除、长期笔记不受影响的测试。

**验证：** 运行 `python -m pytest tests/test_session_store.py::test_latest_unexpired_session tests/test_session_store.py::test_cleanup_expired_sessions_keeps_memory_files -q`，期望全部通过。

## T12: 实现三层指令加载

**文件：** `src/julycode/memory/instructions.py`、`tests/test_memory_instructions.py`  
**依赖：** T2  
**步骤：**
1. 实现 `InstructionLoader.load()`，按 `.julycode/AGENTS.md`、`AGENTS.md`、`~/.julycode/AGENTS.md` 读取。
2. 给每个成功读取的文件生成 `InstructionBlock`，scope 和 priority 与 plan 一致。
3. 缺失文件安静跳过。
4. 添加三层同时存在时顺序正确、缺失文件无告警的测试。

**验证：** 运行 `python -m pytest tests/test_memory_instructions.py::test_loads_three_instruction_layers_in_priority_order tests/test_memory_instructions.py::test_missing_instruction_files_are_silent -q`，期望全部通过。

## T13: 实现指令 include 展开

**文件：** `src/julycode/memory/instructions.py`、`tests/test_memory_instructions.py`  
**依赖：** T12  
**步骤：**
1. 支持独立行 `@include <relative-path>`。
2. 相对路径基于当前文件所在目录解析。
3. 用 visited 集合避免循环引用。
4. 限制 include 深度为 `SessionMemoryConfig.include_max_depth`。
5. 项目级 include 必须仍在项目根目录内，用户级 include 必须仍在 `user_dir` 内。
6. 添加合法展开、循环告警、深度告警、越界告警测试。

**验证：** 运行 `python -m pytest tests/test_memory_instructions.py::test_include_expands_relative_file tests/test_memory_instructions.py::test_include_blocks_cycle_depth_and_path_escape -q`，期望全部通过。

## T14: 实现 Markdown 笔记读写

**文件：** `src/julycode/memory/notes.py`、`tests/test_memory_notes.py`、`.gitignore`  
**依赖：** T2  
**步骤：**
1. 实现 `MemoryNoteStore` 的用户级和项目级根目录解析。
2. 实现 `write_note(note)`，写入带 YAML frontmatter 的 Markdown。
3. 实现 `read_note(scope, note_id)` 和 `list_notes(scope)`。
4. 文件目录按 `scope/category/` 分层，文件名使用安全化 note_id。
5. 在 `.gitignore` 添加 `.julycode/memory/`。
6. 添加四类笔记写入、读取和 list 测试。

**验证：** 运行 `python -m pytest tests/test_memory_notes.py::test_write_and_read_memory_note tests/test_memory_notes.py::test_notes_are_grouped_by_scope_and_category -q`，期望全部通过。

## T15: 添加自动笔记敏感信息过滤

**文件：** `src/julycode/memory/notes.py`、`tests/test_memory_notes.py`  
**依赖：** T14  
**步骤：**
1. 在写入笔记前过滤常见密钥形态和配置中的明文 secret。
2. 对疑似敏感值用 `[REDACTED]` 替换。
3. 确保标题、正文和 tags 都经过过滤。
4. 添加 API key、Bearer token 和普通文本保留测试。

**验证：** 运行 `python -m pytest tests/test_memory_notes.py::test_note_store_redacts_sensitive_values -q`，期望通过。

## T16: 生成记忆索引

**文件：** `src/julycode/memory/index.py`、`tests/test_memory_index.py`  
**依赖：** T14  
**步骤：**
1. 实现 `MemoryIndexBuilder.build(scope)`，扫描对应 scope 的所有笔记。
2. 按用户偏好、纠正反馈、项目知识、参考资料顺序输出 Markdown 索引。
3. 写入对应 scope 的 `index.md`。
4. 实现 `read_index(scope)`，缺失时返回 `None`。
5. 添加分类顺序、索引写入和读取测试。

**验证：** 运行 `python -m pytest tests/test_memory_index.py::test_builds_memory_index_by_category tests/test_memory_index.py::test_read_index_returns_existing_index -q`，期望全部通过。

## T17: 控制索引体量

**文件：** `src/julycode/memory/index.py`、`tests/test_memory_index.py`  
**依赖：** T16  
**步骤：**
1. 在索引生成后检查行数和 UTF-8 字节数。
2. 超过 `index_max_lines` 或 `index_max_bytes` 时按更新时间和类别优先级裁剪。
3. 保留一条中文 warning 说明索引被裁剪。
4. 确认最终写入的索引满足 200 行和 25KB 默认上限。

**验证：** 运行 `python -m pytest tests/test_memory_index.py::test_memory_index_is_limited_by_lines_and_bytes -q`，期望通过。

## T18: 实现自动笔记更新请求

**文件：** `src/julycode/memory/updater.py`、`tests/test_memory_updater.py`  
**依赖：** T14、T16  
**步骤：**
1. 实现 `MemoryNoteUpdater.update(job, provider)` 的无工具 `ChatRequest(tools=())`。
2. prompt 包含本轮消息、最终回复、现有索引和四类笔记定义。
3. 要求模型返回 JSON 操作列表，操作类型只允许 `create`、`update`、`skip`。
4. 添加测试断言更新请求不携带工具，且 prompt 包含四类分类和用户级/项目级 scope 规则。

**验证：** 运行 `python -m pytest tests/test_memory_updater.py::test_updater_requests_without_tools tests/test_memory_updater.py::test_updater_prompt_contains_categories_and_scopes -q`，期望全部通过。

## T19: 应用自动笔记操作

**文件：** `src/julycode/memory/updater.py`、`tests/test_memory_updater.py`  
**依赖：** T18  
**步骤：**
1. 解析模型返回的 JSON 操作列表。
2. 对 `create` 写入新 `MemoryNote`。
3. 对 `update` 读取既有笔记并覆盖标题、正文、tags、updated_at。
4. 对 `skip` 不写文件。
5. 写入后重建受影响 scope 的索引并返回。
6. 添加 create、update、skip 和重复事实不新增重复索引条目的测试。

**验证：** 运行 `python -m pytest tests/test_memory_updater.py::test_updater_creates_and_updates_notes tests/test_memory_updater.py::test_updater_skip_does_not_write_note tests/test_memory_updater.py::test_updater_deduplicates_by_model_operations -q`，期望全部通过。

## T20: 处理自动笔记失败

**文件：** `src/julycode/memory/updater.py`、`tests/test_memory_updater.py`  
**依赖：** T18  
**步骤：**
1. 当 Provider 报错、返回工具调用、JSON 无法解析或操作字段非法时抛出可读异常。
2. 确保失败时不写入部分笔记。
3. 添加 provider error、工具调用、非法 JSON 和非法 scope 测试。

**验证：** 运行 `python -m pytest tests/test_memory_updater.py::test_updater_fails_without_partial_writes -q`，期望通过。

## T21: 实现协议安全截断

**文件：** `src/julycode/memory/recovery.py`、`tests/test_session_recovery.py`  
**依赖：** T2  
**步骤：**
1. 实现 `SessionHistoryValidator.truncate_to_protocol_safe(messages)`。
2. 将 assistant 工具调用及其连续 tool 结果视为一个协议段。
3. 遇到未配对工具调用、孤立 tool 结果、重复 tool 结果或顺序中断时，从最近安全边界截断。
4. 返回截断后的消息、截断数量和 warning。
5. 添加完整工具段保留和四类异常截断测试。

**验证：** 运行 `python -m pytest tests/test_session_recovery.py::test_validator_keeps_complete_tool_segments tests/test_session_recovery.py::test_validator_truncates_invalid_tool_history -q`，期望全部通过。

## T22: 实现启动恢复基础流程

**文件：** `src/julycode/memory/recovery.py`、`tests/test_session_recovery.py`  
**依赖：** T11、T13、T16、T21  
**步骤：**
1. 实现 `SessionBootstrapper.bootstrap()` 的基础路径。
2. 启动时先调用 `cleanup_expired()`。
3. 加载指令和用户级、项目级索引。
4. `BootstrapOptions(new_session=True)` 时创建空会话并绑定 recorder。
5. 默认模式下恢复 `latest_unexpired()` 返回的会话；没有可恢复会话时创建空会话。
6. 添加默认恢复、无会话创建空会话和显式空会话测试。

**验证：** 运行 `python -m pytest tests/test_session_recovery.py::test_bootstrap_restores_latest_session_by_default tests/test_session_recovery.py::test_bootstrap_can_start_new_empty_session -q`，期望全部通过。

## T23: 加入恢复时间跨度提醒

**文件：** `src/julycode/memory/recovery.py`、`tests/test_session_recovery.py`  
**依赖：** T22  
**步骤：**
1. 根据恢复会话 `updated_at` 和当前时间比较 `time_gap_hours`。
2. 超过阈值时写入 `RestoreReport.time_gap_notice`。
3. 未超过阈值时保持空字符串。
4. 添加长间隔有提醒、短间隔无提醒测试。

**验证：** 运行 `python -m pytest tests/test_session_recovery.py::test_bootstrap_adds_time_gap_notice_for_old_session -q`，期望通过。

## T24: 恢复后处理上下文预算

**文件：** `src/julycode/memory/recovery.py`、`tests/test_session_recovery.py`  
**依赖：** T22  
**步骤：**
1. 恢复后用 `ContextManager.prepare_request(mode="manual")` 尝试一次预算压缩检查。
2. 压缩成功时设置 `RestoreReport.compacted=True` 并继续使用恢复会话。
3. 遇到 `ContextLimitError` 时创建空会话并在 `started_empty_reason` 中记录清晰原因。
4. 添加压缩成功继续恢复、压缩失败启动空会话测试。

**验证：** 运行 `python -m pytest tests/test_session_recovery.py::test_bootstrap_compacts_oversized_restored_session tests/test_session_recovery.py::test_bootstrap_starts_empty_when_restored_session_still_over_limit -q`，期望全部通过。

## T25: 实现 SessionMemoryManager

**文件：** `src/julycode/memory/manager.py`、`tests/test_session_recovery.py`、`tests/test_memory_updater.py`  
**依赖：** T20、T24  
**步骤：**
1. 实现 `SessionMemoryManager.bootstrap()`，委托 `SessionBootstrapper` 并保存 `KnowledgeContext`。
2. 实现 `runtime_context()`，每次调用返回当前最新指令、索引和恢复报告。
3. 实现 `schedule_update()`，用 `asyncio.create_task()` 后台执行 `MemoryNoteUpdater.update()`。
4. 捕获后台任务异常并保存 warning，不向 TUI 抛出。
5. 实现 `wait_for_updates()` 供测试等待后台任务结束。
6. 添加 runtime context 刷新和后台失败不阻断测试。

**验证：** 运行 `python -m pytest tests/test_session_recovery.py::test_memory_manager_returns_runtime_context tests/test_memory_updater.py::test_memory_manager_background_update_failure_is_captured -q`，期望全部通过。

## T26: 在提示中注入项目指令

**文件：** `src/julycode/prompting/base.py`、`src/julycode/prompting/builder.py`、`src/julycode/prompting/__init__.py`、`tests/test_prompting.py`  
**依赖：** T13  
**步骤：**
1. 给 `RuntimePromptContext` 增加 `knowledge_context: KnowledgeContext | None`。
2. 在 `PromptBuilder.build_runtime_prompt()` 中追加 `<julycode_project_instructions>` 块。
3. 块内按项目管理目录级、项目根级、用户级输出，并标注来源路径。
4. 确保稳定提示模块仍不包含“自定义指令”或“长期记忆”。
5. 添加三层指令顺序和稳定提示不污染测试。

**验证：** 运行 `python -m pytest tests/test_prompting.py::test_runtime_prompt_includes_project_instructions_by_priority tests/test_prompting.py::test_stable_prompt_is_deterministic_and_has_no_empty_optional_sections -q`，期望全部通过。

## T27: 在提示中注入记忆索引和恢复提醒

**文件：** `src/julycode/prompting/builder.py`、`tests/test_prompting.py`  
**依赖：** T16、T23、T26  
**步骤：**
1. 在运行时提示中追加 `<julycode_memory_index>` 块。
2. 分别输出用户级和项目级索引，并标明 scope。
3. 在存在 `RestoreReport.time_gap_notice` 或 warnings 时追加 `<julycode_restore_notice>` 块。
4. 保持现有 `<julycode_context_summary>` 独立输出，不混入长期记忆块。
5. 添加索引注入、恢复提醒注入和摘要边界独立测试。

**验证：** 运行 `python -m pytest tests/test_prompting.py::test_runtime_prompt_includes_memory_indexes tests/test_prompting.py::test_runtime_prompt_includes_restore_notice tests/test_prompting.py::test_runtime_prompt_keeps_memory_and_context_summary_separate -q`，期望全部通过。

## T28: 压缩成功后追加 checkpoint

**文件：** `src/julycode/context/manager.py`、`tests/test_context_manager.py`  
**依赖：** T5、T8  
**步骤：**
1. 在 `ContextManager._summarize_segments()` 成功替换消息并设置摘要后调用 `session.append_checkpoint()`。
2. 手动 `/compact` 和自动重量兜底都走同一 checkpoint 路径。
3. 轻量工具结果外置不强制追加 checkpoint。
4. 添加自动重量压缩和手动压缩都会写 checkpoint 的测试。

**验证：** 运行 `python -m pytest tests/test_context_manager.py::test_heavy_compaction_appends_session_checkpoint tests/test_context_manager.py::test_manual_compact_appends_session_checkpoint -q`，期望全部通过。

## T29: Agent 请求前读取最新知识上下文

**文件：** `src/julycode/agent.py`、`tests/test_agent.py`  
**依赖：** T25、T27  
**步骤：**
1. 给 `AgentLoopRunner` 增加可选 `memory_manager` 构造参数。
2. 在 `prompt_factory()` 内调用 `memory_manager.runtime_context()`。
3. 将返回的 `KnowledgeContext` 放入 `RuntimePromptContext`。
4. 没有 memory manager 时保持当前行为。
5. 添加请求 prompt 中包含最新索引内容的测试。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_injects_memory_context_before_model_request tests/test_agent.py::test_runner_works_without_memory_manager -q`，期望全部通过。

## T30: Agent 自然完成后调度自动笔记

**文件：** `src/julycode/agent.py`、`tests/test_agent.py`  
**依赖：** T25、T29  
**步骤：**
1. 在模型最终回复无工具调用并追加 assistant 消息后构造 `MemoryUpdateJob`。
2. job 包含本轮用户消息、相关工具结果、最终 assistant 消息、session_id、cwd 和当前 `KnowledgeContext`。
3. 调用 `memory_manager.schedule_update(job=memory_job, provider=self.provider)`。
4. 在取消、Provider 错误、上下文超限、未知工具连续失败、迭代上限、仍有工具调用时不调度。
5. 添加自然完成调度和非自然停止不调度测试。

**验证：** 运行 `python -m pytest tests/test_agent.py::test_runner_schedules_memory_update_on_natural_completion tests/test_agent.py::test_runner_does_not_schedule_memory_update_on_non_natural_stop -q`，期望全部通过。

## T31: TUI 显示恢复状态

**文件：** `src/julycode/tui/app.py`、`tests/test_tui_smoke.py`  
**依赖：** T25  
**步骤：**
1. 给 `JulyCodeApp` 构造参数增加 `memory_manager` 和 `restore_report`。
2. `on_mount()` 中显示恢复成功、启动空会话原因、指令告警、坏行告警和时间跨度提醒。
3. 创建 `AgentLoopRunner` 时传入 memory manager。
4. 保持 MCP 初始化、权限确认和 `/compact` 行为不变。
5. 添加恢复提示展示和 runner 收到 memory manager 的测试。

**验证：** 运行 `python -m pytest tests/test_tui_smoke.py::test_tui_displays_restore_report tests/test_tui_smoke.py::test_tui_passes_memory_manager_to_runner -q`，期望全部通过。

## T32: CLI 接入启动恢复

**文件：** `src/julycode/cli.py`、`tests/test_mcp_manager.py`、`tests/test_config.py`  
**依赖：** T24、T25、T31  
**步骤：**
1. 用 `argparse` 解析 `--new-session`。
2. 创建 `SessionMemoryManager` 并在启动 TUI 前执行 `bootstrap()`。
3. 将 bootstrap 返回的 session、memory manager 和 restore report 传给 `JulyCodeApp`。
4. `memory.enabled=false` 时创建普通空 `ChatSession`，不落盘、不恢复、不自动记忆。
5. 保持配置错误脱敏和 MCP manager 初始化路径不变。
6. 更新 CLI 相关测试以适配新参数和 bootstrap 流程。

**验证：** 运行 `python -m pytest tests/test_mcp_manager.py::test_cli_initializes_mcp_manager_and_closes_it tests/test_config.py::test_cli_config_error_is_reported -q`，期望全部通过。

## T33: 更新 README 和忽略规则

**文件：** `README.md`、`.gitignore`  
**依赖：** T32  
**步骤：**
1. 在 README 增加项目指令三层加载、`@include` 约束、会话恢复、`--new-session`、JSONL 存档、自动笔记和索引上限说明。
2. 移除 README 范围中“跨启动历史恢复、项目指令文件加载或自动记忆不实现”的旧描述。
3. 确认 `.gitignore` 包含 `.julycode/sessions/`、`.julycode/memory/` 和已有 `.julycode/context/`。
4. 添加或更新文档相关测试，确认 README 包含关键命令和目录。

**验证：** 运行 `python -m pytest tests/test_config.py::test_readme_mentions_session_memory -q`，期望通过。

## T34: 跑核心回归

**文件：** `tests/test_session_id.py`、`tests/test_memory_instructions.py`、`tests/test_session_store.py`、`tests/test_session_recovery.py`、`tests/test_memory_notes.py`、`tests/test_memory_index.py`、`tests/test_memory_updater.py`、`tests/test_prompting.py`、`tests/test_agent.py`、`tests/test_tui_smoke.py`  
**依赖：** T1 到 T33  
**步骤：**
1. 运行本阶段新增和修改的测试文件。
2. 修复因集成引起的导入、类型、异步任务或测试隔离问题。
3. 确认临时目录内的用户级 `~/.julycode` 和项目级 `.julycode` 不互相污染。
4. 确认后台自动笔记任务在测试中可等待完成。

**验证：** 运行 `python -m pytest tests/test_session_id.py tests/test_memory_instructions.py tests/test_session_store.py tests/test_session_recovery.py tests/test_memory_notes.py tests/test_memory_index.py tests/test_memory_updater.py tests/test_prompting.py tests/test_agent.py tests/test_tui_smoke.py -q`，期望全部通过。

## T35: 跑全量测试

**文件：** `src/julycode/`、`tests/`  
**依赖：** T34  
**步骤：**
1. 运行全量 pytest。
2. 修复普通聊天、工具调用、Plan Mode、`/do`、权限确认、MCP 工具、上下文压缩和流式显示的回归。
3. 确认没有测试依赖真实用户目录或真实项目 `.julycode` 状态。

**验证：** 运行 `python -m pytest -q`，期望全部通过。

## 执行顺序

```text
T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8 -> T9 -> T10 -> T11 -> T12 -> T13 -> T14 -> T15 -> T16 -> T17 -> T18 -> T19 -> T20 -> T21 -> T22 -> T23 -> T24 -> T25 -> T26 -> T27 -> T28 -> T29 -> T30 -> T31 -> T32 -> T33 -> T34 -> T35
```
