# MewCode Repo Map Checklist

> 每一项都必须通过运行命令或观察真实行为验证。验收时将 `[ ]` 更新为 `[x]`，并在最终验收报告中记录实际命令、结果和必要的 tmux 输出证据。

## 配置与仓库范围

- [x] C01 默认配置在存在 `.py` 或 `.pyi` 的项目中启用 Repo Map，`repo_map.enabled: false` 后请求中不再出现地图，默认预算为 2,000 Token。（验证：运行 `python -m pytest tests/test_config.py tests/test_repo_map_integration.py -q`，期望默认、关闭和预算测试通过；覆盖 AC1）
- [x] C02 从 Git 仓库子目录启动时以当前 Worktree 根为范围，候选集合包含 tracked 和未忽略 untracked Python 文件，并排除 Git ignore 命中的文件。（验证：运行 `python -m pytest tests/test_repo_map_discovery.py -q`，期望 Git root、tracked、untracked 和 ignored 场景通过；覆盖 AC2）
- [x] C03 非 Git 项目以启动目录为范围，并排除 `.git`、`.mewcode`、`__pycache__`、`.venv`、`venv`、`build`、`dist`、`.tox`、`.pytest_cache`、`.mypy_cache` 和 `node_modules`。（验证：运行 `python -m pytest tests/test_repo_map_discovery.py -q`，期望非 Git 默认排除场景通过；覆盖 AC2）
- [x] C04 文件和目录 symlink、规范化后越出根目录的路径均不进入候选集合，所有地图条目路径使用根目录相对 POSIX 格式。（验证：运行 `python -m pytest tests/test_repo_map_discovery.py tests/test_repo_map_renderer.py -q`，期望 symlink、越界和路径格式测试通过；覆盖 AC2）

## Python 结构、安全与关系

- [x] C05 合法 PEP 263 编码的 `.py`/`.pyi` 能输出类、函数、异步函数、方法、异步方法及可见签名。（验证：运行 `python -m pytest tests/test_repo_map_parser.py -q`，期望编码和全部符号种类测试通过；覆盖 AC3）
- [x] C06 地图不含 docstring、注释、decorator、原始默认值、字符串注解内容和控制字符，默认值显示为 `...`，每行不超过 160 字符。（验证：运行 `python -m pytest tests/test_repo_map_parser.py tests/test_repo_map_renderer.py -q`，期望不可信内容清理和行长测试通过；覆盖 AC3）
- [x] C07 绝对导入、`from` 导入、相对导入、别名、同模块直接名称引用和唯一直接调用形成预期的确定性关系。（验证：运行 `python -m pytest tests/test_repo_map_graph.py -q`，期望所有受支持关系测试通过；覆盖 AC4）
- [x] C08 `obj.method()`、`getattr`、字符串导入、`import *`、动态 re-export、重复符号和无法消歧引用不被输出为精确关系，地图明确说明它不是精确调用图。（验证：运行 `python -m pytest tests/test_repo_map_graph.py tests/test_repo_map_renderer.py -q`，期望歧义关系和导航提示测试通过；覆盖 AC4）
- [x] C09 当前请求明确出现路径、模块、文件名或符号时，对应条目排在仅有图重要度的无关条目前。（验证：运行 `python -m pytest tests/test_repo_map_graph.py -q`，期望请求提示评分优先级测试通过；覆盖 AC5）
- [x] C10 空图、同分、重复符号、随机文件遍历顺序、随机解析完成顺序和不同散列种子下，地图与 golden 字节完全一致。（验证：运行 `python -m pytest tests/test_repo_map_graph.py tests/test_repo_map_renderer.py -q`，期望确定性与 golden 测试通过；覆盖 AC5）

## Token 预算与上下文优先级

- [x] C11 Repo Map 的现有 Token 估算不超过有效预算，标题、信任提示、边界、根目录、路径、正文和裁剪标记全部计入。（验证：运行 `python -m pytest tests/test_context_estimator.py tests/test_repo_map_renderer.py -q`，期望完整块预算测试通过；覆盖 AC6）
- [x] C12 文件和符号按完整原子单元输出；方法始终带所属类；超长签名降级为完整短签名；超小预算省略整块而不是输出半行或半条目。（验证：运行 `python -m pytest tests/test_repo_map_renderer.py -q`，期望原子性、短签名和超小预算测试通过；覆盖 AC6）
- [x] C13 ContextManager 只在系统/工具协议、当前用户请求、必要工具结果、明确读取源码和必须保留近期对话确定后授予 Repo Map 剩余预算。（验证：运行 `python -m pytest tests/test_context_manager.py -q`，期望高优先级内容保留和有效授权测试通过；覆盖 AC7）
- [x] C14 合并地图后的完整请求会再次估算；超限时先缩小或省略地图，之后仍沿用既有压缩、安全余量和超限处理。（验证：运行 `python -m pytest tests/test_context_manager.py tests/test_context_estimator.py -q`，期望重试、省略和既有超限回归测试通过；覆盖 AC7）

## Workspace revision 与缓存一致性

- [x] C15 同一 revision、请求提示和有效预算下，多次模型调用和连续只读工具调用复用字节一致快照且 revision 不变。（验证：运行 `python -m pytest tests/test_repo_map_manager.py tests/test_repo_map_integration.py -q`，期望快照复用和只读稳定测试通过；覆盖 AC8）
- [x] C16 编辑、创建或删除 Python 文件后，同一 Agent Loop 的下一次模型调用使用新 revision，不再展示旧签名或已删除符号。（验证：运行 `python -m pytest tests/test_repo_map_manager.py tests/test_repo_map_integration.py -q`，期望三类精确修改与轮内刷新测试通过；覆盖 AC8）
- [x] C17 command 批量修改、失败或超时后的部分写入会比较前后候选集合和内容哈希；无法确定影响范围时全仓图和快照失效。（验证：运行 `python -m pytest tests/test_repo_map_manager.py tests/test_tool_scheduler.py -q`，期望批量、部分写入和全局失效测试通过；覆盖 AC9）
- [x] C18 相同 mtime 但内容不同会生成新 revision；新建、删除、切换分支、Worktree 和项目不会返回其他身份的旧地图。（验证：运行 `python -m pytest tests/test_repo_map_discovery.py tests/test_repo_map_manager.py -q`，期望内容哈希与身份隔离测试通过；覆盖 AC9、AC16）
- [x] C19 单文件变化只重新解析内容变化文件；解析、图和快照缓存分别按项目/路径/内容/规则版本、有序文件指纹和 revision/提示/预算/渲染版本隔离。（验证：运行 `python -m pytest tests/test_repo_map_manager.py -q`，期望解析计数、缓存 key、规则版本失效和 LRU 测试通过；覆盖 AC16）
- [x] C20 初始索引未就绪时模型请求立即继续，当前 turn/revision 固定省略地图；revision 不变时不会在后续迭代中途补入。（验证：运行 `python -m pytest tests/test_repo_map_manager.py tests/test_repo_map_integration.py -q`，期望 not-ready 非阻塞与固定省略测试通过；覆盖 AC14）

## Prompt Cache 与 Provider 序列化

- [x] C21 Repo Map 使用 generated、untrusted_repository_data、request_ephemeral 和 snapshot 语义，Provider 不把它序列化为用户原始输入。（验证：运行 `python -m pytest tests/test_prompting.py tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望生成块元数据和合法角色测试通过；覆盖 AC12）
- [x] C22 OpenAI 在地图关闭、启用、内容变化和快照复用时，从请求起点到长期稳定缓存边界的规范序列化摘要完全相同，稳定 cache key 不含地图身份。（验证：运行 `python -m pytest tests/test_openai_provider.py -q`，期望稳定前缀 golden、摘要和 key 测试通过；覆盖 AC10）
- [x] C23 Anthropic 按 `tools → system → messages` 保持长期稳定前缀摘要；缓存开启时稳定前缀和 Repo Map 快照各有一个 ephemeral 边界，地图变化只影响第二级内容。（验证：运行 `python -m pytest tests/test_anthropic_provider.py -q`，期望前缀 golden 和两级 cache_control 测试通过；覆盖 AC10、AC11）
- [x] C24 OpenAI MVP 与不支持显式断点的兼容接口不收到 `prompt_cache_options` 或 breakpoint 字段，仍能使用自动缓存完成请求；关闭 Anthropic 缓存时不发送 `cache_control`。（验证：运行 `python -m pytest tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望兼容降级请求测试通过；覆盖 AC11）
- [x] C25 OpenAI 与 Anthropic 得到语义一致的 Repo Map 正文和信任提示，差异仅限 Provider 合法表示和缓存控制字段。（验证：运行 `python -m pytest tests/test_openai_provider.py tests/test_anthropic_provider.py -q`，期望跨 Provider 语义等价断言通过；覆盖 AC12）

## 历史、摘要与持久化隔离

- [x] C26 完成带 Repo Map 的请求后，ChatSession 的用户、助手和工具消息中不存在地图边界、正文或 snapshot id。（验证：运行 `python -m pytest tests/test_session.py tests/test_repo_map_integration.py -q`，期望消息历史隔离测试通过；覆盖 AC13）
- [x] C27 触发上下文摘要、保存和恢复会话后，摘要文本、持久化文件和恢复消息中不存在 Repo Map，下一请求重新临时组装当前快照。（验证：运行 `python -m pytest tests/test_session_recovery.py tests/test_repo_map_integration.py -q`，期望摘要与恢复隔离测试通过；覆盖 AC13）
- [x] C28 长期记忆提取、更新和用户/助手记录中不存在 Repo Map 内容或 snapshot id。（验证：运行 `python -m pytest tests/test_memory_updater.py tests/test_repo_map_integration.py -q`，期望长期记忆隔离测试通过；覆盖 AC13）

## 降级、安全与状态观测

- [x] C29 功能关闭或项目无 Python 文件时不启动有效索引、不注入地图，普通对话继续且状态分别显示 disabled 或 no-python-files。（验证：运行 `python -m pytest tests/test_repo_map_manager.py tests/test_repo_map_integration.py tests/test_tui_smoke.py -q`，期望关闭与空仓库降级测试通过；覆盖 AC14）
- [x] C30 单文件语法错误、编码错误或读取失败时仅排除问题文件，其他可分析文件仍形成地图并完成普通对话。（验证：运行 `python -m pytest tests/test_repo_map_parser.py tests/test_repo_map_manager.py tests/test_repo_map_integration.py -q`，期望部分失败隔离测试通过；覆盖 AC14）
- [x] C31 关系构建、渲染或缓存异常不会使 TUI、会话或 Agent Loop 退出；系统省略或降级地图并记录明确原因。（验证：运行 `python -m pytest tests/test_repo_map_manager.py tests/test_repo_map_integration.py tests/test_tui_smoke.py -q`，期望异常注入测试通过；覆盖 AC14）
- [x] C32 `/status` 或自动测试能观察 enabled、root、revision、configured/effective budget、候选/入选文件数、truncated、三层缓存状态、耗时和降级原因。（验证：运行 `python -m pytest tests/test_commands.py tests/test_repo_map_manager.py -q`，期望所有状态字段测试通过；覆盖 AC20）
- [x] C33 地图关闭、未就绪、预算过小或生成失败后，状态字段反映当前原因且不残留上次成功的 revision、入选数、缓存命中或耗时。（验证：运行 `python -m pytest tests/test_commands.py tests/test_repo_map_manager.py -q`，期望状态切换和无残留测试通过；覆盖 AC20）
- [x] C34 Repo Map 索引本身只读，不修改源码、Git 状态或用户配置，也不触发副作用权限确认。（验证：在临时 Git fixture 上运行 `python -m pytest tests/test_repo_map_discovery.py tests/test_repo_map_manager.py tests/test_tui_smoke.py -q`，期望索引前后工作树摘要一致且没有权限请求；覆盖 N2）

## 性能与后台生命周期

- [x] C35 当前 MewCode 仓库预热后至少 100 次缓存命中快照组装的 P95 小于 50ms。（验证：运行 `python -m pytest tests/test_repo_map_manager.py -q`，期望性能样本数和 P95 断言通过；覆盖 AC15）
- [x] C36 文件发现、哈希、解析和图更新在后台执行，Repo Map 引入的 TUI 主线程单次同步阻塞小于 16ms。（验证：运行 `python -m pytest tests/test_repo_map_manager.py tests/test_tui_smoke.py -q`，期望 asyncio heartbeat 最大延迟断言通过；覆盖 AC15）
- [x] C37 初次索引可取消；切换项目、分支或 Worktree 后，旧 generation 结果不会覆盖新项目状态。（验证：运行 `python -m pytest tests/test_repo_map_manager.py tests/test_tui_smoke.py -q`，期望取消和 stale generation 丢弃测试通过；覆盖 AC15）

## Agent Loop 与既有功能集成

- [x] C38 Stub model 的首个请求包含临时 Repo Map；只读工具结果回灌后快照不变；编辑工具结果回灌后地图更新；Agent Loop 最终正常结束。（验证：运行 `python -m pytest tests/test_repo_map_integration.py tests/test_agent.py -q`，期望确定性多轮集成场景通过；覆盖 AC17）
- [x] C39 工具 observer 只包裹真正执行的调用；成功、失败和超时均执行必要后置比较，权限拒绝或 hook 拦截的未执行调用不刷新 revision。（验证：运行 `python -m pytest tests/test_tool_scheduler.py tests/test_repo_map_manager.py -q`，期望执行边界测试通过；覆盖 AC8、AC17）
- [x] C40 Repo Map 只注入主 Agent；Skill、子 Agent、团队成员和隔离 Worktree Agent 的既有上下文与生命周期不变。（验证：运行 `python -m pytest tests/test_tui_smoke.py tests/test_skills.py tests/test_subagents.py tests/test_team_runtime.py tests/test_worktrees.py -q`，期望主 Agent 限定和隔离执行回归测试通过；覆盖 AC17）
- [x] C41 普通模式、Plan Mode、权限确认、MCP、上下文压缩、会话恢复和流式回复行为无回归。（验证：运行 `python -m pytest tests/test_agent.py tests/test_permissions.py tests/test_mcp_manager.py tests/test_context_manager.py tests/test_session_recovery.py tests/test_sse.py -q`，期望全部通过；覆盖 AC17）

## 质量评测

- [x] C42 导航评测数据集中的请求不直接包含目标路径，并能离线计算目标文件 Top-K 命中。（验证：运行 `python -m pytest tests/test_eval_framework.py -q`，再运行 `python eval/run_repo_map_eval.py --help`，期望数据校验和离线模式可用；覆盖 AC18）
- [x] C43 质量报告同时记录 enabled/disabled 的目标文件 Top-K 命中率和平均探索工具调用数，不把真实模型结果作为 CI 硬阈值。（验证：运行 `python -m pytest tests/test_eval_framework.py -k repo_map_quality_report -q`，期望 fixture 生成的 `results.json` 与 `report.md` 同时包含两组指标和“非门禁”说明；覆盖 AC18）

## 编译、测试与变更完整性

- [x] C44 Repo Map 核心、Provider、TUI 和评测模块均可被 Python 编译加载，不存在循环导入。（验证：运行 `python -m compileall -q src eval`，期望退出码为 0）
- [x] C45 Repo Map 的 discovery、parser、graph、renderer、manager 和 integration 专项测试全部通过，关键地图使用字节精确 golden。（验证：运行 `python -m pytest tests/test_repo_map_discovery.py tests/test_repo_map_parser.py tests/test_repo_map_graph.py tests/test_repo_map_renderer.py tests/test_repo_map_manager.py tests/test_repo_map_integration.py -q`，期望全部通过）
- [x] C46 OpenAI、Anthropic、Prompt、ContextManager、Scheduler、Agent、Session、命令和 TUI 的相关回归测试全部通过。（验证：运行 `python -m pytest tests/test_openai_provider.py tests/test_anthropic_provider.py tests/test_prompting.py tests/test_context_estimator.py tests/test_context_manager.py tests/test_tool_scheduler.py tests/test_agent.py tests/test_session.py tests/test_session_recovery.py tests/test_commands.py tests/test_tui_smoke.py -q`，期望全部通过）
- [x] C47 全量测试通过，源码与文档无尾随空白或补丁格式错误，且没有新增未批准的运行时依赖。（验证：依次运行 `python -m pytest -q`、`git diff --check`、`rg -n "[[:blank:]]+$" src tests eval specs README.md` 并检查 `git diff -- pyproject.toml`；期望测试和 diff 检查退出码为 0、`rg` 无输出、依赖文件无 Repo Map 相关改动）

## tmux 真实端到端场景

- [x] C48 在当前 MewCode 仓库启动真实 TUI，发送“不含准确路径”的请求“请找出负责在模型调用前压缩超长工具结果的入口，说明主要调用链，并在下结论前读取相关实现”，MewCode 正常注入地图、调用合法工具并生成有依据的回复。（验证：运行 `tmux new-session -d -s mewcode-repomap 'mewcode'`，用 `tmux send-keys` 发送请求并用 `tmux capture-pane -pt mewcode-repomap` 记录输出；不要求模型固定选择某个工具；覆盖 AC19）
- [x] C49 在可清理的测试工作区中让真实模型完成一次 Python 签名修改，观察同一 Agent Loop 修改后的后续请求使用新 revision，地图不再包含旧签名。（验证：用 `tmux send-keys -t mewcode-repomap '<真实修改请求>' Enter` 发送请求，修改前后分别发送 `/status`，再运行 `tmux capture-pane -pt mewcode-repomap`；期望 revision 改变、旧签名消失、工具调用和最终回复正常；覆盖 AC8、AC19）
- [x] C50 真实请求中 Repo Map 状态、Provider Prompt Cache usage、工具结果回灌和最终回复均可观察且无异常；关闭 Repo Map 后 `/status` 明确显示 disabled，普通请求仍成功。（验证：捕获开启与关闭配置下的 tmux 输出和 Provider usage 日志，期望长期缓存行为正常、关闭状态无旧字段残留；覆盖 AC1、AC19、AC20）
- [x] C51 tmux 验收完成后测试工作区与会话均被清理，不把 E2E 临时改动留在用户工作树。（验证：运行 `tmux kill-session -t mewcode-repomap`，再运行 `git status --short` 并与验收前基线比较，期望只保留本功能计划内变更）

## 验收标准追踪

| Spec 验收标准 | Checklist 条目 |
| --- | --- |
| AC1 | C01、C29、C50 |
| AC2 | C02-C04 |
| AC3 | C05-C06 |
| AC4 | C07-C08 |
| AC5 | C09-C10 |
| AC6 | C11-C12 |
| AC7 | C13-C14 |
| AC8 | C15-C16、C39、C49 |
| AC9 | C17-C18 |
| AC10 | C22-C23 |
| AC11 | C23-C24 |
| AC12 | C21、C25 |
| AC13 | C26-C28 |
| AC14 | C20、C29-C31 |
| AC15 | C35-C37 |
| AC16 | C18-C19 |
| AC17 | C38-C41、C45-C47 |
| AC18 | C42-C43 |
| AC19 | C48-C50 |
| AC20 | C32-C33、C50 |
