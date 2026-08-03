# JulyCode 跨会话记忆质量 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/julycode/memory/models.py` | 增加关键偏好阈值、笔记证据和提取结果模型 |
| 修改 | `src/julycode/memory/__init__.py` | 导出新增生产模型 |
| 新建 | `src/julycode/memory/extraction.py` | 解析和校验记忆候选 |
| 修改 | `src/julycode/memory/updater.py` | 拆分 extract/apply/update 并强化 Prompt |
| 修改 | `src/julycode/memory/notes.py` | 新元信息兼容读写、敏感检查和删除 |
| 修改 | `src/julycode/memory/index.py` | 关键偏好优先索引 |
| 修改 | `src/julycode/config.py` | 解析并校验关键偏好置信阈值 |
| 修改 | `src/julycode/prompting/builder.py` | 注入长期记忆使用边界 |
| 新建 | `eval/memory_quality/__init__.py` | 专项评测包导出 |
| 新建 | `eval/memory_quality/models.py` | 数据集、结果和报告模型 |
| 新建 | `eval/memory_quality/loader.py` | 人工标注数据加载和校验 |
| 新建 | `eval/memory_quality/matching.py` | 一对一匹配和指标聚合 |
| 新建 | `eval/memory_quality/offline.py` | 确定性专项 Provider |
| 新建 | `eval/memory_quality/runner.py` | 提取与跨会话成对评测 |
| 新建 | `eval/memory_quality/report.py` | JSON 和 Markdown 报告 |
| 新建 | `eval/run_memory_eval.py` | 专项评测 CLI |
| 新建 | `eval/cases/memory_quality/extraction.json` | 120 条人工标注提取用例 |
| 新建 | `eval/cases/memory_quality/inheritance.json` | 20 条跨会话成对用例 |
| 新建 | `tests/test_memory_extraction.py` | 候选和确定性校验测试 |
| 修改 | `tests/test_memory_updater.py` | extract/apply/update、重复和冲突测试 |
| 修改 | `tests/test_memory_notes.py` | 新元信息、兼容和敏感保护测试 |
| 修改 | `tests/test_memory_index.py` | 关键偏好排序测试 |
| 修改 | `tests/test_session_recovery.py` | 空白新会话继承测试 |
| 修改 | `tests/test_prompting.py` | 长期记忆边界提示测试 |
| 修改 | `tests/test_config.py` | 新配置项测试 |
| 新建 | `tests/test_memory_quality_loader.py` | 数据集 schema 和规模测试 |
| 新建 | `tests/test_memory_quality_matching.py` | TP/FP/FN 和指标边界测试 |
| 新建 | `tests/test_memory_quality_runner.py` | 离线提取和成对跨会话流程测试 |
| 新建 | `tests/test_memory_quality_report.py` | 报告与验收阈值测试 |
| 修改 | `tests/e2e_mock_openai_server.py` | 新提取 schema 和 tmux 场景脚本 |
| 修改 | `README.md` | 生产记忆行为和验收入口 |
| 修改 | `eval/README.md` | 专项评测模式、成本和报告说明 |

## T1: 扩展生产记忆模型

**文件：** `src/julycode/memory/models.py`、`src/julycode/memory/__init__.py`

**依赖：** 无

**步骤：**
1. 在 `SessionMemoryConfig` 增加默认值为 `0.95` 的关键偏好置信阈值。
2. 在 `MemoryNote` 末尾增加有默认值的 `source_evidence`、`critical` 和 `confidence` 字段。
3. 增加 Plan 中定义的候选、校验操作、拒绝和提取结果数据结构与 Literal 类型。
4. 从 `julycode.memory` 导出需要被 updater、测试和评测器使用的新类型。

**验证：** 运行 `python -m pytest tests/test_session_id.py tests/test_memory_notes.py -q`，期望既有模型构造与笔记测试通过。

## T2: 解析关键偏好阈值配置

**文件：** `src/julycode/config.py`、`tests/test_config.py`

**依赖：** T1

**步骤：**
1. 从 `memory.critical_preference_min_confidence` 解析浮点数。
2. 拒绝布尔值、非数字以及小于 0 或大于 1 的配置。
3. 增加默认值、自定义值和非法边界测试。

**验证：** 运行 `python -m pytest tests/test_config.py -q`，期望所有配置测试通过。

## T3: 兼容读写笔记质量元信息

**文件：** `src/julycode/memory/notes.py`、`tests/test_memory_notes.py`

**依赖：** T1

**步骤：**
1. 将来源证据、关键偏好标记和置信度写入 Markdown frontmatter。
2. 读取新字段并对旧文件使用 `()`、`False`、`None` 默认值。
3. 保持既有标题、正文、标签和时间字段行为不变。
4. 增加新格式往返和手工旧格式兼容测试。

**验证：** 运行 `python -m pytest tests/test_memory_notes.py -q`，期望新旧格式均可读取。

## T4: 增加敏感检查与安全删除

**文件：** `src/julycode/memory/notes.py`、`tests/test_memory_notes.py`

**依赖：** T3

**步骤：**
1. 增加检测已知 secret、通用 API token、Bearer 值和私钥头的敏感内容方法。
2. 扩展写入前脱敏到来源证据字段，保留现有标题、正文和标签脱敏。
3. 增加按 scope 和 note ID 定位的 `delete_note()`，限制在合法记忆根目录内。
4. 测试敏感检测、证据脱敏、删除存在笔记和删除不存在笔记。

**验证：** 运行 `python -m pytest tests/test_memory_notes.py -q`，期望敏感内容不以明文落盘且删除行为正确。

## T5: 实现候选 JSON 解析

**文件：** `src/julycode/memory/extraction.py`、`tests/test_memory_extraction.py`

**依赖：** T1

**步骤：**
1. 将模型 JSON 中的 `operations` 转换为 `MemoryCandidate`。
2. 支持纯 JSON 和现有兼容的 JSON fenced block。
3. 在本模块定义 `MemoryExtractionError`；由 updater 以既有 `MemoryUpdateError` 名称重导出，避免循环导入并保持兼容。
4. 对非对象、缺少 operations、非法数组项和错误字段类型产生提取异常或结构拒绝。
5. 测试 create、update、skip 和非法输入。

**验证：** 运行 `python -m pytest tests/test_memory_extraction.py -q -k parse`，期望解析测试通过。

## T6: 实现长期性与用户证据校验

**文件：** `src/julycode/memory/extraction.py`、`tests/test_memory_extraction.py`

**依赖：** T4、T5

**步骤：**
1. 实现 `MemoryCandidateValidator.validate()` 的结构和 `persistent` 门控。
2. 仅从当前轮 user 消息建立证据集合。
3. 要求每段 evidence 是某条用户消息的精确非空子串。
4. 将临时、未知长期性、助手证据和工具证据分别映射到稳定拒绝码。

**验证：** 运行 `python -m pytest tests/test_memory_extraction.py -q -k 'persistent or evidence'`，期望长期性和证据边界全部通过。

## T7: 实现关键偏好与敏感内容门控

**文件：** `src/julycode/memory/extraction.py`、`tests/test_memory_extraction.py`

**依赖：** T2、T6

**步骤：**
1. 实现 preference/correction 类别限制。
2. 增加中英文持续性和强约束标记匹配，使用不区分英文大小写的归一化。
3. 应用关键偏好置信阈值并返回独立低置信拒绝码。
4. 在任何候选落盘前检查标题、正文和证据中的敏感内容。
5. 覆盖明确偏好、隐式偏好、临时偏好、低置信度和敏感值测试。

**验证：** 运行 `python -m pytest tests/test_memory_extraction.py -q -k 'critical or sensitive'`，期望只有满足三重门控的关键偏好被接受。

## T8: 实现重复、更新和替代校验

**文件：** `src/julycode/memory/extraction.py`、`tests/test_memory_extraction.py`

**依赖：** T3、T6

**步骤：**
1. 对标题、正文和证据做大小写、空白和标点归一化，拒绝批内及既有精确重复。
2. 要求 update 指向同作用域真实存在的 note ID，并保留原始创建时间。
3. 校验 supersedes 只引用同作用域真实笔记，不能引用自身或批内重复 ID。
4. 将通过候选转换为完整 `ValidatedMemoryOperation`。

**验证：** 运行 `python -m pytest tests/test_memory_extraction.py -q -k 'duplicate or update or supersede'`，期望重复被跳过、非法更新被拒绝、合法纠正可替代旧规则。

## T9: 拆分 MemoryNoteUpdater.extract

**文件：** `src/julycode/memory/updater.py`、`tests/test_memory_updater.py`

**依赖：** T5、T6、T7、T8

**步骤：**
1. 注入或创建 `MemoryCandidateValidator`。
2. 将 Provider 请求、流事件收集、候选解析和校验迁入 `extract()`。
3. 更新 Prompt，按角色发送消息并声明 durability、evidence、critical、confidence 和 supersedes schema。
4. 在 Prompt 中明确临时任务、猜测、助手/工具内容、敏感值和闲聊返回 skip。
5. 更新 FakeProvider 响应并测试无工具请求、结构错误和 accepted/rejected 结果。

**验证：** 运行 `python -m pytest tests/test_memory_updater.py -q -k 'request or extract or fail'`，期望提取链路测试通过。

## T10: 实现 MemoryNoteUpdater.apply 与兼容 update

**文件：** `src/julycode/memory/updater.py`、`tests/test_memory_updater.py`

**依赖：** T8、T9

**步骤：**
1. 让 `apply()` 写入全部已校验笔记并收集受影响 scope。
2. 新笔记成功写入后再删除 supersedes 指向的旧笔记。
3. 每个受影响 scope 只重建一次索引。
4. 让 `update()` 依次调用 `extract()` 和 `apply()`，保持原有返回类型。
5. 增加重复不落盘、合法更新、冲突替代和写入前全量校验测试。

**验证：** 运行 `python -m pytest tests/test_memory_updater.py -q`，期望 updater 全部测试通过。

## T11: 验证后台更新集成与失败隔离

**文件：** `src/julycode/memory/manager.py`、`tests/test_memory_updater.py`、`tests/test_agent.py`

**依赖：** T10

**步骤：**
1. 仅在构造签名需要时调整 manager 对 updater 的初始化，不改变 schedule/update 契约。
2. 验证自然完成仍异步调度一次更新，非自然停止不调度。
3. 验证 accepted 为空时上下文不损坏，异常仍只形成 warning。
4. 验证更新后的用户或项目索引进入下一次 runtime context。

**验证：** 运行 `python -m pytest tests/test_memory_updater.py tests/test_agent.py -q -k memory`，期望后台更新和 Agent 集成通过。

## T12: 将关键偏好优先写入索引

**文件：** `src/julycode/memory/index.py`、`tests/test_memory_index.py`

**依赖：** T3

**步骤：**
1. 同一类别内先排列 `critical=True` 的笔记，再按更新时间排列。
2. 为关键偏好增加清晰且短小的索引标记。
3. 保持 200 行和 25KB 裁剪逻辑有效。
4. 增加关键优先、普通顺序和体量上限测试。

**验证：** 运行 `python -m pytest tests/test_memory_index.py -q`，期望关键偏好先于普通记忆且索引不超限。

## T13: 强化长期记忆运行时提示

**文件：** `src/julycode/prompting/builder.py`、`tests/test_prompting.py`

**依赖：** T12

**步骤：**
1. 在 memory index 块中说明其为跨会话长期记忆而非当前用户消息。
2. 说明当前用户明确指令可覆盖旧记忆，未覆盖时优先遵循关键偏好。
3. 说明不得要求用户重述索引中已有且当前任务需要的背景。
4. 保持项目指令、记忆索引、恢复通知和上下文摘要边界不变。

**验证：** 运行 `python -m pytest tests/test_prompting.py -q -k 'memory or summary'`，期望提示包含边界说明且各知识块顺序不回退。

## T14: 固化空白新会话继承行为

**文件：** `tests/test_session_recovery.py`、`tests/test_agent.py`

**依赖：** T11、T13

**步骤：**
1. 创建上一会话消息、用户记忆和项目记忆。
2. 使用 `BootstrapOptions(new_session=True)` 启动并断言新 session messages 为空。
3. 运行首个请求并捕获 Provider 请求，断言两类记忆已注入而上一会话消息不存在。
4. 验证恢复通知明确为空会话。

**验证：** 运行 `python -m pytest tests/test_session_recovery.py tests/test_agent.py -q -k 'new_session or memory_context'`，期望空历史与长期记忆注入同时成立。

## T15: 建立专项评测模型

**文件：** `eval/memory_quality/__init__.py`、`eval/memory_quality/models.py`

**依赖：** T1

**步骤：**
1. 实现 Plan 中的 Extraction、Inheritance、Metrics、RunOptions 和 Report dataclass。
2. 复用 `july_eval.models.EvalProviderInfo` 记录 Provider 元数据。
3. 为元组和可选减少率设置安全默认值。
4. 导出 Loader、Runner 和报告模块需要的公共类型。

**验证：** 运行 `python -m compileall -q eval/memory_quality`，期望无语法和导入错误。

## T16: 实现提取数据 schema 加载

**文件：** `eval/memory_quality/loader.py`、`tests/test_memory_quality_loader.py`

**依赖：** T15

**步骤：**
1. 解析 extraction 顶层 version、cases、messages 和 expected。
2. 校验 case ID、消息角色、scope、category、evidence 和 term groups。
3. 确保证据逐字存在于该用例 user 消息中。
4. 为损坏 JSON、重复 ID、非法角色、缺失证据和空 term group 增加测试。

**验证：** 运行 `python -m pytest tests/test_memory_quality_loader.py -q -k extraction`，期望 schema 错误均给出具体中文信息。

## T17: 实现继承数据与验收规模校验

**文件：** `eval/memory_quality/loader.py`、`tests/test_memory_quality_loader.py`

**依赖：** T16

**步骤：**
1. 解析 inheritance 用例及 required、forbidden、restatement 断言。
2. 校验两份数据集版本一致和全局 case ID 唯一。
3. 实现 50 个关键偏好、30 个其他正例、40 个纯负例和 20 个继承用例下限检查。
4. 增加各类别少一条、版本不一致和完整最小数据集测试。

**验证：** 运行 `python -m pytest tests/test_memory_quality_loader.py -q`，期望加载和验收规模测试全部通过。

## T18: 实现确定性匹配边判断

**文件：** `eval/memory_quality/matching.py`、`tests/test_memory_quality_matching.py`

**依赖：** T15

**步骤：**
1. 实现大小写、Unicode 空白和常见标点归一化。
2. 检查 scope、category、critical 完全一致。
3. 检查标注证据与预测证据对应。
4. 检查每个 content term group 至少命中一个词。
5. 覆盖正确匹配及各元数据错误测试。

**验证：** 运行 `python -m pytest tests/test_memory_quality_matching.py -q -k edge`，期望只有完整满足条件的预测形成匹配边。

## T19: 实现一对一最大匹配

**文件：** `eval/memory_quality/matching.py`、`tests/test_memory_quality_matching.py`

**依赖：** T18

**步骤：**
1. 为预测与标注构建稳定排序的二分图。
2. 实现确定性一对一最大匹配。
3. 将未匹配预测记为 FP、未匹配标注记为 FN。
4. 验证重复预测只能得到一个 TP，错误 scope/category 同时形成 FP 和 FN。

**验证：** 运行 `python -m pytest tests/test_memory_quality_matching.py -q -k 'matching or duplicate'`，期望重复预测不重复得分。

## T20: 聚合 Precision、Recall 和 F1

**文件：** `eval/memory_quality/matching.py`、`tests/test_memory_quality_matching.py`

**依赖：** T19

**步骤：**
1. 聚合所有用例的 TP、FP 和 FN。
2. 按规格处理零预测和零分母。
3. 独立聚合关键偏好 TP、FP、FN、Precision 和 Recall。
4. 增加全命中、全漏提、零预测、误报、重复和混合数据测试。

**验证：** 运行 `python -m pytest tests/test_memory_quality_matching.py -q`，期望所有指标与手算结果一致。

## T21: 生成机器可读专项报告

**文件：** `eval/memory_quality/report.py`、`tests/test_memory_quality_report.py`

**依赖：** T15、T20

**步骤：**
1. 将 dataclass、tuple、Path 和 Provider 元信息稳定序列化为 JSON。
2. 输出每个提取用例的 matches、FP、FN 和 rejection。
3. 输出每个继承用例的 baseline/enabled 结果和证据。
4. 测试 JSON 可重新加载且不包含 Python 专用对象表示。

**验证：** 运行 `python -m pytest tests/test_memory_quality_report.py -q -k json`，期望 `results.json` 字段完整。

## T22: 生成 Markdown 报告与验收判定

**文件：** `eval/memory_quality/report.py`、`tests/test_memory_quality_report.py`

**依赖：** T21

**步骤：**
1. 输出数据集版本、模式、Provider、模型、开始时间和四组核心指标。
2. 列出 FP、FN、分类错误、拒绝原因和跨会话失败证据。
3. 实现在线门槛：F1 85%、关键 Precision 98%、关键 TP 45、首轮 90%、减少率 80%。
4. 将 baseline 重述数为 0 判定为无效失败。
5. 测试通过、逐项失败、无效基线和 offline 非冒充说明。

**验证：** 运行 `python -m pytest tests/test_memory_quality_report.py -q`，期望报告内容和验收失败原因准确。

## T23: 实现离线提取 Provider

**文件：** `eval/memory_quality/offline.py`、`tests/test_memory_quality_runner.py`

**依赖：** T9、T15

**步骤：**
1. 根据当前 ExtractionCase 的人工标签生成完整新 schema operations。
2. 负例返回 skip，正例使用用户原话证据和可通过门控的置信度。
3. 生成标准 `message_done` 流事件且拒绝工具调用。
4. 明确 Provider 元信息为 offline/scripted，不携带在线模型名。

**验证：** 运行 `python -m pytest tests/test_memory_quality_runner.py -q -k offline_extraction_provider`，期望正例被接受、负例不落入 accepted。

## T24: 实现提取评测 Runner

**文件：** `eval/memory_quality/runner.py`、`tests/test_memory_quality_runner.py`

**依赖：** T20、T23

**步骤：**
1. 为每个用例创建独立临时 workspace、user_dir 和 `MemoryUpdateJob`。
2. 在线模式调用共享真实 Provider，离线模式为每个 case 创建脚本 Provider。
3. 只调用生产 `MemoryNoteUpdater.extract()`，不写入笔记。
4. 使用 Matcher 生成用例结果并聚合指标。

**验证：** 运行 `python -m pytest tests/test_memory_quality_runner.py -q -k run_extraction`，期望离线样例获得确定性指标且临时目录无跨用例记忆。

## T25: 建立跨会话 Agent 测试夹具

**文件：** `eval/memory_quality/runner.py`、`tests/test_memory_quality_runner.py`

**依赖：** T14、T15

**步骤：**
1. 封装在指定 workspace 中创建 ContextManager、工具注册表、执行器和 AgentLoopRunner 的 helper。
2. 增加 RecordingProvider 包装，记录首个模型请求的系统与运行时内容。
3. 将 user_dir 固定到 trial 临时目录，不访问真实 HOME。
4. 测试两个 trial 的工作区、会话和用户记忆根互不相同。

**验证：** 运行 `python -m pytest tests/test_memory_quality_runner.py -q -k harness`，期望所有路径位于 pytest 临时目录且彼此隔离。

## T26: 实现开启记忆的来源阶段

**文件：** `eval/memory_quality/runner.py`、`tests/test_memory_quality_runner.py`

**依赖：** T11、T25

**步骤：**
1. 以 `new_session=True` 启动来源会话并运行 source prompt。
2. 等待 `SessionMemoryManager.wait_for_updates()`，确认用户级和项目级笔记已经形成。
3. 记录来源阶段错误并停止该 case，而不是继续产生伪结果。
4. 使用离线 Provider 验证来源最终回复和两类索引均存在。

**验证：** 运行 `python -m pytest tests/test_memory_quality_runner.py -q -k source_phase`，期望来源会话结束后两类长期记忆可读取。

## T27: 实现开启记忆的空白目标阶段

**文件：** `eval/memory_quality/runner.py`、`tests/test_memory_quality_runner.py`

**依赖：** T26

**步骤：**
1. 在同一 trial 目录重新创建 manager，并用 `new_session=True` 启动目标会话。
2. 目标 manager 关闭自动笔记更新但保留记忆加载，避免无关额外请求。
3. 在发送 target prompt 前记录 session messages 为空。
4. 捕获首个目标请求，检查用户和项目索引存在、source prompt 不在普通历史中。
5. 按 required、forbidden 和 restatement 断言生成 enabled trial。

**验证：** 运行 `python -m pytest tests/test_memory_quality_runner.py -q -k enabled_target`，期望空会话首轮遵循两类记忆且未携带来源消息。

## T28: 实现关闭记忆基线与成对统计

**文件：** `eval/memory_quality/runner.py`、`tests/test_memory_quality_runner.py`

**依赖：** T27

**步骤：**
1. 在独立目录使用 `memory.enabled=False` 运行相同 target prompt。
2. 确认基线请求不含 memory index 和 source prompt。
3. 按 restatement 词组判定背景重述需求。
4. 组合 baseline/enabled 为 InheritanceCaseResult。
5. 聚合首轮正确率、两种重述次数和减少率；零基线返回 None。

**验证：** 运行 `python -m pytest tests/test_memory_quality_runner.py -q -k 'baseline or pair'`，期望开启与关闭记忆结果可区分且减少率计算正确。

## T29: 实现离线跨会话 Provider

**文件：** `eval/memory_quality/offline.py`、`tests/test_memory_quality_runner.py`

**依赖：** T23、T28

**步骤：**
1. 对来源普通请求返回确认回复。
2. 对自动记忆请求返回一条关键用户偏好和一条项目知识候选。
3. 对无 memory index 的目标请求返回标注的背景询问。
4. 对包含两类 memory index 的目标请求返回满足 required 且不命中 forbidden 的回答。
5. 测试请求类型识别不会把普通 Agent 请求误判为提取请求。

**验证：** 运行 `python -m pytest tests/test_memory_quality_runner.py -q -k offline_inheritance_provider`，期望离线基线询问背景、开启记忆直接完成。

## T30: 汇总两类评测为 MemoryQualityReport

**文件：** `eval/memory_quality/runner.py`、`tests/test_memory_quality_runner.py`

**依赖：** T24、T28、T29

**步骤：**
1. 顺序运行 extraction 与 inheritance，避免真实 Provider 突发并发。
2. 填充数据集版本、模式、Provider 和开始时间。
3. 调用统一验收判定并保留全部失败原因。
4. 验证 offline 报告明确是流程结果，online 报告才应用发布结论。

**验证：** 运行 `python -m pytest tests/test_memory_quality_runner.py -q`，期望完整离线报告稳定生成。

## T31: 实现专项评测 CLI

**文件：** `eval/run_memory_eval.py`、`tests/test_memory_quality_runner.py`

**依赖：** T17、T22、T30

**步骤：**
1. 增加默认 offline 的 `--mode`、`--cases`、`--output` 和 `--model` 参数。
2. offline 创建脚本 Provider 元信息，online 复用当前配置和 Provider 工厂。
3. 完整运行前强制执行验收规模校验。
4. 写入 JSON/Markdown，按通过、指标失败、配置/框架错误返回 0、1、2。
5. 增加 subprocess CLI smoke 和错误路径测试。

**验证：** 运行 `python -m pytest tests/test_memory_quality_runner.py tests/test_memory_quality_report.py -q -k cli`，期望退出码和错误提示符合设计。

## T32: 标注关键偏好用例 1–10

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T17

**步骤：**
1. 建立版本和 cases 顶层结构。
2. 人工编写 10 条中文关键偏好，覆盖始终、以后、默认、必须和禁止。
3. 为每条填写逐字 evidence、scope/category/critical 和 term groups。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('critical_')]) == 10"`，期望退出码为 0。

## T33: 标注关键偏好用例 11–20

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T32

**步骤：**
1. 增加 10 条英文关键偏好。
2. 覆盖 always、never、by default、from now on 和 must。
3. 确认标签含义与用户原话一致，不把一次性请求标成长期偏好。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('critical_')]) == 20"`，期望退出码为 0。

## T34: 标注关键偏好用例 21–30

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T33

**步骤：**
1. 增加 10 条纠正类关键偏好。
2. 覆盖“不要再”“改为默认”“今后禁止”和英文 correction 表达。
3. 对 correction 类别和 user scope 做人工复核。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('critical_')]) == 30"`，期望退出码为 0。

## T35: 标注关键偏好用例 31–40

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T34

**步骤：**
1. 增加 10 条编程助手行为约束。
2. 覆盖语言、提交、测试、注释、命令和安全操作偏好。
3. 使用不同句式，避免只测试固定关键词模板。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('critical_')]) == 40"`，期望退出码为 0。

## T36: 标注关键偏好用例 41–50

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T35

**步骤：**
1. 增加最后 10 条关键偏好，覆盖复合句、否定和中英文混合。
2. 确保每条至少包含一个明确长期或强约束标记。
3. 人工复核 50 条均属于 user preference/correction 且证据逐字存在。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('critical_')]) == 50"`，期望退出码为 0。

## T37: 标注其他长期记忆用例 1–10

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T36

**步骤：**
1. 增加 10 条项目事实用例。
2. 覆盖语言版本、框架、数据库、目录和部署环境。
3. 标记为 project/project_knowledge，critical 为 false。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('memory_')]) == 10"`，期望退出码为 0。

## T38: 标注其他长期记忆用例 11–20

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T37

**步骤：**
1. 增加 10 条长期项目约定和已确认技术决策。
2. 覆盖测试结构、兼容策略、API 风格、存储方案和错误处理。
3. 确认不是当前任务步骤或未确认猜测。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('memory_')]) == 20"`，期望退出码为 0。

## T39: 标注其他长期记忆用例 21–30

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T38

**步骤：**
1. 增加 10 条参考资料和通用但非关键偏好记忆。
2. 覆盖文档入口、设计依据、常用命令和项目术语。
3. 人工复核 scope/category 和内容词组。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('memory_')]) == 30"`，期望退出码为 0。

## T40: 标注负例 1–10

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T39

**步骤：**
1. 增加 10 条仅当前回答有效的格式、长度和语气要求。
2. expected 保持空数组。
3. 覆盖与关键偏好相似但明确带“这次、当前、临时”的表达。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('negative_')]) == 10"`，期望退出码为 0。

## T41: 标注负例 11–20

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T40

**步骤：**
1. 增加 10 条一次性任务步骤、短期进度和工具输出场景。
2. 至少包含仅助手或 tool 消息出现候选事实的用例。
3. expected 保持空数组并标注 negative 类型标签。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('negative_')]) == 20"`，期望退出码为 0。

## T42: 标注负例 21–30

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T41

**步骤：**
1. 增加 10 条模型猜测、用户不确定表达和闲聊。
2. 覆盖“可能、也许、暂时考虑、你猜”等证据不足表达。
3. expected 保持空数组。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('negative_')]) == 30"`，期望退出码为 0。

## T43: 标注负例 31–40

**文件：** `eval/cases/memory_quality/extraction.json`

**依赖：** T42

**步骤：**
1. 增加 10 条 API key、Bearer token、私钥、密码和其他敏感信息场景。
2. 使用显然虚构的测试值，不写入真实凭据。
3. expected 保持空数组并人工检查没有可被当作长期事实的附带内容。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/extraction.json').read_text()); assert len([c for c in d['cases'] if c['id'].startswith('negative_')]) == 40"`，期望退出码为 0。

## T44: 标注跨会话用例 1–10

**文件：** `eval/cases/memory_quality/inheritance.json`

**依赖：** T17

**步骤：**
1. 建立与 extraction 相同的数据集版本。
2. 编写 10 个来源提示，每个同时包含项目事实和关键用户偏好。
3. 编写不泄露答案的目标任务及 required、forbidden、restatement 断言。
4. 覆盖语言、测试框架、命名、依赖和版本约定。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/inheritance.json').read_text()); assert len(d['cases']) == 10"`，期望退出码为 0。

## T45: 标注跨会话用例 11–20

**文件：** `eval/cases/memory_quality/inheritance.json`

**依赖：** T44

**步骤：**
1. 增加 10 个成对用例。
2. 覆盖目录、API、数据库、兼容性、禁止提交和架构决策。
3. 确保 target prompt 要求未知时询问但不包含来源答案。
4. 人工复核开启记忆时可由关键词客观判断首轮正确。

**验证：** 运行 `python -c "from pathlib import Path; import json; d=json.loads(Path('eval/cases/memory_quality/inheritance.json').read_text()); assert len(d['cases']) == 20"`，期望退出码为 0。

## T46: 对完整人工标注数据执行校验

**文件：** `eval/cases/memory_quality/extraction.json`、`eval/cases/memory_quality/inheritance.json`、`tests/test_memory_quality_loader.py`

**依赖：** T32–T45

**步骤：**
1. 用生产 Loader 加载两份真实数据文件。
2. 执行 evidence、唯一 ID、版本和 term group 校验。
3. 断言关键偏好正例 50、其他正例 30、纯负例 40、继承用例 20。
4. 人工抽查中英文、否定、纠正、临时、猜测和敏感标签均存在。

**验证：** 运行 `python -m pytest tests/test_memory_quality_loader.py -q`，期望真实数据集和构造的边界用例全部通过。

## T47: 更新端到端 mock 记忆 schema

**文件：** `tests/e2e_mock_openai_server.py`、`tests/test_memory_updater.py`

**依赖：** T10、T13

**步骤：**
1. 为既有“默认中文”和测试命名操作补齐 durability、evidence、critical、confidence 和 supersedes。
2. 增加同一来源消息同时提取项目决策与关键偏好的 tmux 专用响应。
3. 增加新会话目标请求：只有 memory index 存在时直接按两项约定回答，否则询问背景。
4. 保持其他 E2E 场景响应不变。

**验证：** 运行 `python -m compileall -q tests/e2e_mock_openai_server.py` 和 `python -m pytest tests/test_memory_updater.py -q`，期望 mock 可编译且新 schema 被生产提取器接受。

## T48: 补充用户和评测文档

**文件：** `README.md`、`eval/README.md`

**依赖：** T31、T46

**步骤：**
1. 说明自动记忆只记录明确长期信息、关键偏好证据门控和旧笔记兼容。
2. 说明 `--new-session` 不恢复消息但会加载长期记忆。
3. 记录 offline 和 online 专项评测命令、约 200 次在线请求成本提示和退出码。
4. 说明报告指标、数据集版本和 offline 不能证明真实质量。

**验证：** 运行 `rg -n "run_memory_eval|关键偏好|--new-session|offline|online" README.md eval/README.md`，期望两份文档均包含行为与评测入口。

## T49: 运行生产链路定向回归

**文件：** `src/julycode/`、`tests/test_memory_*.py`、`tests/test_session_recovery.py`、`tests/test_prompting.py`、`tests/test_agent.py`、`tests/test_config.py`

**依赖：** T1–T14、T47

**步骤：**
1. 编译生产源码和相关测试。
2. 运行全部 memory、session recovery、prompting、agent 和 config 测试。
3. 修复失败后重复运行，不能以跳过测试收束。

**验证：** 运行 `python -m compileall -q src/julycode tests`，再运行 `python -m pytest tests/test_memory_*.py tests/test_session_recovery.py tests/test_prompting.py tests/test_agent.py tests/test_config.py -q`，期望退出码均为 0。

## T50: 运行完整离线专项评测

**文件：** `eval/run_memory_eval.py`、`eval/results/memory-quality/offline/`

**依赖：** T30、T31、T46、T49

**步骤：**
1. 用完整 120+20 数据集运行 offline 模式。
2. 检查 JSON 中 mode/provider 明确为 offline/scripted。
3. 检查 Markdown 包含指标、FP/FN、跨会话和“不代表真实模型质量”说明。
4. 确认退出码为 0。

**验证：** 运行 `python eval/run_memory_eval.py --mode offline --output eval/results/memory-quality/offline`，期望用例数、报告字段和退出码正确。

## T51: 运行项目全量自动化测试

**文件：** 全项目源码与测试

**依赖：** T48、T50

**步骤：**
1. 运行完整 pytest。
2. 运行 compileall。
3. 检查没有测试写入真实用户记忆目录。
4. 修复任何回归并重新运行全量测试。

**验证：** 运行 `python -m pytest -q` 和 `python -m compileall -q src eval tests`，期望退出码均为 0。

## T52: 运行真实模型专项评测

**文件：** `eval/results/memory-quality/latest/results.json`、`eval/results/memory-quality/latest/report.md`

**依赖：** T51

**步骤：**
1. 使用当前有效 JulyCode Provider 配置运行完整 online 数据集。
2. 记录实际模型、Provider、数据集版本和时间。
3. 检查整体 F1、关键偏好 Precision/Recall、首轮理解率和背景重复说明减少率。
4. 若未达标，依据 FP/FN 明细调整生产提取规则或 Prompt，重新运行相关测试与在线评测。

**验证：** 运行 `python eval/run_memory_eval.py --mode online --output eval/results/memory-quality/latest`，期望退出码为 0，F1 ≥ 85%、关键偏好 Precision ≥ 98%、关键 TP ≥ 45、首轮理解率 ≥ 90%、背景重述减少率 ≥ 80%。

## T53: 使用 tmux 完成真实端到端验收

**文件：** `tests/e2e_mock_openai_server.py`、临时测试目录、`specs/cross-session-memory-quality/checklist.md`

**依赖：** T47、T51

**步骤：**
1. 在隔离临时目录和 HOME 中配置 JulyCode 指向 mock OpenAI server。
2. 在 tmux 启动第一会话，输入同时包含项目技术决策和关键用户偏好的真实对话请求。
3. 等待最终回复和后台记忆写入，检查两类 Markdown 笔记和索引。
4. 退出后以 `julycode --new-session` 启动第二会话，直接输入依赖两项背景的任务。
5. 捕获 pane 和 mock 请求日志，确认第二会话未恢复旧消息、首个请求含两类长期记忆、最终回复不询问背景并遵循两项约定。

**验证：** 运行 `tmux capture-pane -p -S -300 -t mew-memory-quality-e2e`，期望看到第一会话完成、第二会话空白启动和按既定技术决策及关键偏好完成的首轮回复；同时检查隔离目录中的记忆文件和请求日志。

## T54: 清理验收环境并确认工作区

**文件：** 工作区和临时验收目录

**依赖：** T52、T53

**步骤：**
1. 关闭专项 tmux 会话和 mock server。
2. 删除临时 HOME、配置、会话和请求日志，不删除项目内正式评测报告。
3. 检查没有残留 mock/JulyCode 进程。
4. 检查 git diff 只包含本功能预期文件。

**验证：** 运行 `tmux ls`、`ps -ef | rg "e2e_mock_openai_server|julycode"` 和 `git status --short`，期望无本次验收残留进程或临时文件，工作区只包含预期改动。

## 执行顺序

```text
T1 → T2
 ├→ T3 → T4
 └→ T5

T4 + T5 → T6 → T7
T3 + T6 → T8
T5 + T6 + T7 + T8 → T9 → T10 → T11
T3 → T12 → T13
T11 + T13 → T14

T1 → T15 → T16 → T17
T15 → T18 → T19 → T20 → T21 → T22
T9 + T15 → T23
T20 + T23 → T24
T14 + T15 → T25 → T26 → T27 → T28
T23 + T28 → T29
T24 + T28 + T29 → T30
T17 + T22 + T30 → T31

T17 → T32 → T33 → T34 → T35 → T36
    → T37 → T38 → T39
    → T40 → T41 → T42 → T43
T17 → T44 → T45
T32..T45 → T46

T10 + T13 → T47
T31 + T46 → T48
T1..T14 + T47 → T49
T30 + T31 + T46 + T49 → T50
T48 + T50 → T51
T51 → T52
T47 + T51 → T53
T52 + T53 → T54
```
