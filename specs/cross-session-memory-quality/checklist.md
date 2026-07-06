# MewCode 跨会话记忆质量 Checklist

> 每一项必须通过运行命令、检查报告或观察真实行为验证。离线脚本结果只验证流程，不能作为真实模型质量达标证据。

## 实现完整性

- [ ] 自动记忆已经拆分为候选提取、确定性校验、落盘和索引重建四步，既有 `update()` 调用方无需改变。（验证：运行 `python -m pytest tests/test_memory_updater.py tests/test_agent.py -q -k memory`，期望 extract/apply/update 和后台调度测试通过）
- [ ] 非 skip 候选必须携带长期性、用户原话证据、关键标记和置信度，缺少字段不会被静默写入。（验证：运行 `python -m pytest tests/test_memory_extraction.py -q -k 'parse or schema'`，期望缺字段和错误类型被拒绝）
- [ ] 只有 `persistent` 候选可以成为长期记忆，临时和不确定候选均被拒绝。（验证：运行 `python -m pytest tests/test_memory_extraction.py -q -k persistent`，期望 temporary/uncertain 对应拒绝码正确）
- [ ] 自动提取证据只能来自当前轮 user 消息的精确子串，助手回复和工具结果不能单独成为偏好或纠正证据。（验证：运行 `python -m pytest tests/test_memory_extraction.py -q -k evidence`，期望用户证据通过、assistant/tool 证据失败）
- [ ] 关键偏好同时经过类别、显式长期约束标记和最低置信度门控。（验证：运行 `python -m pytest tests/test_memory_extraction.py -q -k critical`，期望明确偏好通过，隐式、临时和低置信候选失败）
- [ ] 敏感值在候选校验阶段被拒绝，写入阶段仍执行脱敏保护。（验证：运行 `python -m pytest tests/test_memory_extraction.py tests/test_memory_notes.py -q -k sensitive`，期望 token、Bearer、私钥和已知 secret 不以明文落盘）
- [ ] 新自动笔记保存来源会话、用户证据、类别、scope、关键标记和置信度。（验证：运行 `python -m pytest tests/test_memory_notes.py -q -k 'metadata or round_trip'`，期望 frontmatter 往返字段完整）
- [ ] 旧格式笔记缺少新增字段时仍能加载和注入。（验证：运行 `python -m pytest tests/test_memory_notes.py tests/test_memory_index.py -q -k 'legacy or old_format'`，期望旧笔记使用安全默认值并出现在索引）
- [ ] 重复候选不会产生第二条有效笔记，合法更新保留原创建时间。（验证：运行 `python -m pytest tests/test_memory_extraction.py tests/test_memory_updater.py -q -k 'duplicate or update'`，期望索引只保留一条且 created_at 不变）
- [ ] 最近一次明确纠正可以通过 supersedes 替代旧规则，并且新笔记写入成功后才删除旧笔记。（验证：运行 `python -m pytest tests/test_memory_extraction.py tests/test_memory_updater.py -q -k 'supersede or conflict'`，期望最终索引只包含新规则）
- [ ] 关键偏好在记忆索引中优先于普通记忆，同时索引仍满足行数和字节上限。（验证：运行 `python -m pytest tests/test_memory_index.py -q`，期望关键标记先出现且体量限制测试通过）
- [ ] 运行时提示明确区分长期记忆和当前用户消息，允许当前明确指令覆盖旧记忆，并要求不要重复询问已有背景。（验证：运行 `python -m pytest tests/test_prompting.py -q -k 'memory or summary'`，期望边界说明存在且知识块顺序正确）

## 人工标注数据集（AC1）

- [ ] 提取数据集和跨会话数据集具有相同、非空且可报告的版本号。（验证：运行 `python -m pytest tests/test_memory_quality_loader.py -q -k version`，期望版本一致性测试通过）
- [ ] 提取数据集包含至少 120 个唯一用例：关键偏好正例不少于 50、其他长期记忆正例不少于 30、纯负例不少于 40。（验证：运行 `python -m pytest tests/test_memory_quality_loader.py -q -k acceptance_size`，期望真实数据文件通过数量校验，任一类别少一条的 fixture 被拒绝）
- [ ] 跨会话数据集包含至少 20 个唯一成对用例。（验证：运行 `python -m pytest tests/test_memory_quality_loader.py -q -k acceptance_size`，期望继承用例数量为 20 或更多）
- [ ] 每个正例 evidence 都逐字存在于对应 user 消息，scope、category、critical 和 term groups 标签完整。（验证：运行 `python -m pytest tests/test_memory_quality_loader.py -q -k extraction`，期望真实数据加载成功，伪造证据和空 term group 被拒绝）
- [ ] 数据集覆盖中英文、否定、纠正、冲突、重复、临时要求、模型猜测、助手/工具来源和敏感信息。（验证：运行 `python -m pytest tests/test_memory_quality_loader.py -q -k coverage`，期望必需覆盖标签集合无缺项）

## 指标正确性（AC2）

- [ ] 全命中样例计算得到 Precision=Recall=F1=1。（验证：运行 `python -m pytest tests/test_memory_quality_matching.py -q -k full_match`，期望三个值均为 1）
- [ ] 全部漏提和零预测样例的 Precision 与 F1 为 0，不会得到虚假的 100% Precision。（验证：运行 `python -m pytest tests/test_memory_quality_matching.py -q -k 'all_missed or zero_prediction'`，期望符合规格零分母规则）
- [ ] 重复预测最多匹配一个标注单元，多余预测计为 FP。（验证：运行 `python -m pytest tests/test_memory_quality_matching.py -q -k duplicate`，期望 TP=1 且多余数量进入 FP）
- [ ] 错误 scope、category 或 critical 标记不会匹配，并同时形成 FP 与 FN。（验证：运行 `python -m pytest tests/test_memory_quality_matching.py -q -k 'scope or category or critical'`，期望错误分类不获得 TP）
- [ ] 一对一最大匹配不会因为预测或标注顺序不同而改变 TP/FP/FN。（验证：运行 `python -m pytest tests/test_memory_quality_matching.py -q -k stable_matching`，期望重排前后指标一致）
- [ ] 关键偏好 Precision、Recall、TP、FP、FN 与整体指标独立统计。（验证：运行 `python -m pytest tests/test_memory_quality_matching.py -q -k critical_metrics`，期望手工构造混合样例结果一致）

## 自动提取行为（AC3–AC5）

- [ ] 临时格式、一次性任务和短期进度负例不产生 accepted 长期记忆。（验证：运行离线专项评测后检查 `results.json` 中 `negative_temporary` 标签用例均无 accepted/FP；同时运行 `python -m pytest tests/test_memory_extraction.py -q -k temporary`）
- [ ] 模型猜测、不确定陈述和闲聊负例不产生 accepted 长期记忆。（验证：检查离线报告中 `negative_uncertain`、`negative_chat` 标签用例无 FP，并运行对应 validator 测试）
- [ ] 仅出现在助手回复或工具结果中的内容不会成为用户偏好或纠正。（验证：检查离线报告中 `negative_assistant`、`negative_tool` 标签用例无 FP，并运行 `python -m pytest tests/test_memory_extraction.py -q -k evidence`）
- [ ] 敏感信息负例不产生长期笔记，报告和落盘文件也不包含完整测试凭据。（验证：运行 `rg -n "BEGIN .*PRIVATE KEY|Bearer [A-Za-z0-9._~-]{8,}|sk-[A-Za-z0-9_-]{8,}" eval/results/memory-quality tests/.pytest_cache .mewcode 2>/dev/null`，期望无未脱敏测试值；并检查负例结果无 FP）
- [ ] 重复和冲突序列执行后索引中无重复或相互矛盾的有效规则，最新用户纠正生效。（验证：运行 `python -m pytest tests/test_memory_updater.py -q -k 'duplicate or conflict or supersede'`，期望最终索引断言通过）
- [ ] 在线完整提取评测整体 F1 不低于 85%。（验证：运行 `python eval/run_memory_eval.py --mode online --output eval/results/memory-quality/latest` 后读取 `results.json` 的 `extraction_metrics.f1`，期望 `>= 0.85`）
- [ ] 在线完整提取评测关键偏好 Precision 不低于 98%。（验证：读取同一报告的 `extraction_metrics.critical_precision`，期望 `>= 0.98`）
- [ ] 在线完整提取评测关键偏好至少命中 45 条，并单独报告 Recall。（验证：读取同一报告的 `critical_tp` 和 `critical_recall`，期望 TP `>= 45` 且 Recall 字段存在）
- [ ] 在线提取失败明细可定位每个 FP、FN、分类错误、预测内容、期望内容和证据。（验证：在 `report.md` 中抽查至少一个人工注入的失败 fixture 或实际失败项，期望包含 case ID、预测/期望和匹配或拒绝原因）

## 空白新会话继承（AC6–AC8）

- [ ] `BootstrapOptions(new_session=True)` 创建的 session 在用户输入前 messages 为空。（验证：运行 `python -m pytest tests/test_session_recovery.py -q -k new_session`，期望空历史断言通过）
- [ ] 空白新会话首个模型请求包含同项目长期记忆和用户长期记忆。（验证：运行 `python -m pytest tests/test_session_recovery.py tests/test_agent.py -q -k 'new_session or memory_context'`，检查 RecordingProvider 请求同时包含 `scope=user` 和 `scope=project`）
- [ ] 空白新会话首个模型请求不包含上一会话普通消息。（验证：运行同一测试，期望唯一来源消息标记不出现在目标请求普通 history 中）
- [ ] 开启与关闭记忆的两个 trial 使用不同 workspace、session 和 user memory 根，不读取真实 HOME。（验证：运行 `python -m pytest tests/test_memory_quality_runner.py -q -k harness`，期望隔离路径断言通过）
- [ ] 开启记忆 trial 等待来源会话后台提取完成后才启动目标空白会话。（验证：运行 `python -m pytest tests/test_memory_quality_runner.py -q -k source_phase`，期望启动目标前两类索引已存在）
- [ ] 跨会话评测的“首轮”允许内部工具迭代，但只包含一个目标用户请求。（验证：运行 `python -m pytest tests/test_memory_quality_runner.py -q -k first_turn`，期望目标 session 只有一个 user turn 且结果按完整 Agent Loop 计分）
- [ ] 在线至少 20 个成对用例全部运行，其中至少 18 个开启记忆 trial 首轮理解正确，正确率不低于 90%。（验证：读取 online `results.json`，期望 inheritance result 数量 `>=20`、`first_turn_accuracy >= 0.90`）
- [ ] 关闭记忆基线至少出现一次背景重述需求。（验证：读取 online 报告的 `baseline_restatements`，期望 `> 0`；为 0 时报告必须标为无效失败）
- [ ] 开启记忆后的背景重复说明减少率不低于 80%。（验证：读取 online 报告的 `restatement_reduction`，期望非 null 且 `>= 0.80`）
- [ ] 跨会话失败报告列出 case ID、baseline/enabled 回复、缺失必需项、命中禁止项和背景重述证据。（验证：检查 `report.md` 的跨会话失败章节，或用失败 fixture 运行 `tests/test_memory_quality_report.py` 验证字段完整）

## 离线评测与报告（AC9）

- [ ] 普通专项测试完全使用确定性替身，不需要网络、API key 或真实模型。（验证：在空 HOME 环境运行 `env HOME=/tmp/mew-memory-empty-home python -m pytest tests/test_memory_quality_*.py -q`，期望通过且无网络配置错误）
- [ ] 完整 offline 专项评测可通过一个命令运行并退出 0。（验证：运行 `python eval/run_memory_eval.py --mode offline --output eval/results/memory-quality/offline`，期望输出提取用例 120、跨会话用例 20、错误 0、退出码 0）
- [ ] offline JSON 明确记录 `mode=offline`、脚本 Provider 和数据集版本。（验证：读取 `eval/results/memory-quality/offline/results.json`，期望 mode/provider/version 字段正确）
- [ ] offline Markdown 明确说明结果不代表真实模型质量。（验证：运行 `rg -n "不代表真实模型质量|offline|scripted" eval/results/memory-quality/offline/report.md`，期望三类说明可见）
- [ ] offline 报告同时包含整体 Precision/Recall/F1、关键偏好指标、首轮理解率、两种背景重述次数和减少率。（验证：运行 `rg -n "Precision|Recall|F1|首轮|背景重述|减少率" eval/results/memory-quality/offline/report.md`，期望字段齐全）

## 在线评测与可审计性（AC10）

- [ ] online 专项评测可通过一个命令启动，且输出目录同时包含 `results.json` 和 `report.md`。（验证：运行 `python eval/run_memory_eval.py --mode online --output eval/results/memory-quality/latest`，期望两个文件存在）
- [ ] online 报告记录数据集版本、Provider、模型和运行时间，不能标记为 scripted/offline。（验证：检查 `results.json` 的 mode/provider/model/started_at，期望 mode 为 online 且模型非 scripted）
- [ ] online 指标未达到任一门槛时 CLI 退出 1，并在报告中逐项说明原因。（验证：运行 `python -m pytest tests/test_memory_quality_report.py tests/test_memory_quality_runner.py -q -k 'threshold or exit_code'`，期望失败 fixture 的退出码和原因断言通过）
- [ ] online 配置或框架错误时 CLI 退出 2 且给出中文诊断。（验证：在隔离空 HOME 中运行 online CLI，期望退出 2 且 stderr 包含配置或 Provider 错误）
- [ ] 报告中的 FP、FN、拒绝和跨会话回复不包含未脱敏敏感值。（验证：运行敏感 fixture 报告测试，并用 `rg` 搜索测试 token，期望只出现 `[REDACTED]` 或完全不存在）

## 编译与回归（AC12）

- [ ] 生产源码、评测器和 E2E mock 均可编译。（验证：运行 `python -m compileall -q src eval tests`，期望退出码 0）
- [ ] 所有 memory、session recovery、prompting、agent 和 config 定向测试通过。（验证：运行 `python -m pytest tests/test_memory_*.py tests/test_session_recovery.py tests/test_prompting.py tests/test_agent.py tests/test_config.py -q`，期望退出码 0）
- [ ] 项目全量测试通过。（验证：运行 `python -m pytest -q`，期望零失败、零错误）
- [ ] 自动记忆 Provider 失败只产生 warning，不终止普通会话或破坏已有索引。（验证：运行 `python -m pytest tests/test_memory_updater.py tests/test_agent.py -q -k 'failure or warning'`，期望失败隔离测试通过）
- [ ] 专项评测失败只影响评测退出码和报告，不修改真实项目记忆或用户记忆。（验证：运行失败 fixture 后比较隔离 HOME 与项目真实 `.mewcode/memory`，期望真实目录无新增或修改）
- [ ] 普通聊天、工具调用、权限和上下文压缩现有测试未回退。（验证：运行 `python -m pytest tests/test_agent.py tests/test_tools.py tests/test_permissions.py tests/test_context_manager.py tests/test_context_compactor.py -q`，期望全部通过）

## tmux 端到端场景（AC11）

- [ ] 场景 1：第一会话提取两类长期记忆。使用隔离 HOME 和项目目录，在 tmux 中启动 mock Provider 与 MewCode；输入“请长期记住：本项目统一使用 pytest；以后始终用中文回答”，等待自然结束后，用户级关键偏好和项目级技术约定分别落盘并出现在索引。（验证：运行 `tmux capture-pane -p -S -200 -t mew-memory-quality-e2e`，并检查隔离目录的 user/project memory Markdown，期望包含来源证据、critical 标记、pytest 和中文偏好）
- [ ] 场景 2：第二会话严格为空白新会话。退出第一会话后，以同项目和 HOME 运行 `mewcode --new-session`，界面显示空会话启动而不是恢复旧会话。（验证：捕获第二会话 pane，并检查最新 session 在首次输入前没有旧消息）
- [ ] 场景 3：第二会话无需背景复述。直接输入“按既定项目约定说明应使用什么测试框架，并按我的长期偏好回答”；最终首轮回复同时包含 pytest 和中文，不询问测试框架或语言偏好。（验证：tmux pane 最终回复及 mock 请求日志同时证明 memory index 已注入、上一会话消息不在 history、回复未命中询问背景词组）
- [ ] 场景 4：当前明确指令覆盖旧偏好。在同一空白新会话再输入“这一次改用英文回答”，回复使用英文但长期中文偏好笔记未被临时要求覆盖或删除。（验证：tmux 输出为英文；检查原偏好 Markdown 和索引仍保留，后台提取不产生相反长期偏好）
- [ ] 场景 5：验收环境清理。关闭 MewCode、mock Provider 和 tmux，删除隔离 HOME、配置、会话与请求日志。（验证：运行 `tmux ls`、`ps -ef | rg "e2e_mock_openai_server|mewcode"` 和 `git status --short`，期望无本次残留进程或临时文件，工作区只含预期实现与报告）

## 最终证据汇总

- [ ] 验收报告逐项记录自动化命令、退出码、online 指标、tmux 捕获摘要和未通过项，不以“应该通过”代替实际结果。（验证：完成验收后检查本文件所有勾选项均附有实际证据或明确失败原因）
- [ ] 最终结论绑定具体数据集版本、模型、Provider 和运行时间。（验证：对照 online `results.json` 与最终验收报告，期望四项元数据完全一致）
