# JulyCode

JulyCode 是一个终端 AI 编程助手。当前阶段实现全屏 TUI 对话、本地工具系统和 Agent Loop：用户输入任务后，JulyCode 调用配置的大模型后端，流式展示回复，并允许模型在同一次任务中循环调用工具、观察结果、继续调整，直到完成或触发停止条件。

## 安装

开发环境安装：

```bash
python -m pip install -e ".[dev]"
```

## 配置

用户级配置默认读取：

```text
~/.julycode/config.yaml
```

项目级配置默认读取当前目录向上的首个：

```text
.julycode.yaml
```

当两者同时存在时，项目级配置覆盖用户级配置中的同名字段。

OpenAI 示例：

```yaml
protocol: openai
model: gpt-5.5
base_url: https://api.openai.com/v1
api_key: ${OPENAI_API_KEY}
```

Anthropic 示例：

```yaml
protocol: anthropic
model: claude-sonnet-4-6
base_url: https://api.anthropic.com/v1
api_key: ${ANTHROPIC_API_KEY}
thinking:
  enabled: true
  type: enabled
  budget_tokens: 1024
  display: summarized
```

`api_key` 可以直接写明文，也可以使用完整环境变量引用，例如 `${OPENAI_API_KEY}`。

### Repo Map

Repo Map 默认启用，为主 Agent 的每次模型请求生成一份 Python 仓库导航索引。默认预算为 2,000 Token，可在用户级或项目级配置中调整：

```yaml
repo_map:
  enabled: true
  max_tokens: 2000
```

也可以用 `repo_map: false` 或 `repo_map.enabled: false` 完全关闭。预算不足、索引尚未准备好或仓库中没有 `.py`/`.pyi` 文件时，JulyCode 会安全省略整块地图，不阻塞普通请求。

Git 项目以 Worktree 根目录为范围，包含 tracked 文件以及未被 ignore 的 untracked Python 文件；非 Git 目录使用安全遍历。索引不跟随 symlink，不会越出项目根目录。源码变化按内容哈希产生新的 workspace revision：只读工具后同一快照保持不变，编辑、删除或命令可能修改源码后，下一次模型调用会取得新 revision 的地图。

地图只包含经过清理的类、函数和方法签名及启发式关系，不包含注释和 docstring。它以 `untrusted_repository_data` 标记为不可信导航数据；模型在修改文件或依赖实现细节前仍必须使用 `read_file` 或 `search_code` 查看真实源码。Repo Map 是请求期临时上下文，不写入聊天历史、上下文摘要、会话恢复或长期记忆。

Repo Map 位于长期稳定 Prompt Cache 前缀之后，不参与稳定缓存身份；支持快照级缓存边界的 Provider 可以短期复用同一快照。输入 `/status` 可查看 enabled/state、workspace revision、配置与有效预算、候选/纳入文件数、裁剪、三层缓存命中、耗时和降级原因。

### MCP Server

可以在用户级或项目级配置中通过 `mcp_servers` 声明外部 MCP Server。该字段是一个 map，每个 key 是 Server 名。用户级和项目级都声明时，不同名 Server 会同时保留，项目级同名 Server 会覆盖用户级同名 Server。

stdio 示例：

```yaml
mcp_servers:
  local_demo:
    type: stdio
    command: python
    args: ["tests/fixtures/mcp_stdio_server.py"]
    env:
      API_TOKEN: ${MCP_API_TOKEN}
```

Streamable HTTP 示例：

```yaml
mcp_servers:
  remote_demo:
    type: http
    url: http://127.0.0.1:8765/mcp
    headers:
      Authorization: Bearer ${MCP_API_TOKEN}
```

stdio 的 `env` 值、Streamable HTTP 的 `url` 和 `headers` 值支持 `${VAR}` 展开，也支持出现在字符串片段中，例如 `Bearer ${MCP_API_TOKEN}`。引用的环境变量未设置或为空时，JulyCode 会在启动阶段报告配置错误。

需要 OAuth 2.1 的远程 MCP Server 可以启用 `oauth`。JulyCode 遵循 MCP `2025-06-18` 的 Protected Resource Metadata、Authorization Server Metadata、Authorization Code + PKCE S256 和 Resource Indicators 流程；优先使用动态客户端注册（DCR），Server 不支持 DCR 时回退到配置的预注册客户端：

```yaml
mcp_servers:
  remote_oauth:
    type: http
    url: https://mcp.example.com/mcp
    oauth:
      client_id: ${MCP_OAUTH_CLIENT_ID}          # DCR 不可用时必需
      client_secret: ${MCP_OAUTH_CLIENT_SECRET}  # 公共客户端可省略
      scopes: [read, write]
```

启动时未找到有效 token 或收到 401，JulyCode 只把该 Server 标为“需要授权”，不会自动打开浏览器。输入 `/mcp auth remote_oauth` 后，JulyCode 才会在 `127.0.0.1` 随机端口启动 `/oauth/callback`，显示授权 URL 并尝试打开浏览器；授权成功后无需重启即可加载工具。输入 `/mcp logout remote_oauth` 会删除本地凭据并移除该 Server 的工具，`/status` 可查看每个 OAuth Server 的公开状态。

OAuth token、refresh token 和动态注册得到的客户端 secret 优先保存在系统 Keyring；Keyring 不可用或锁定时只保存在当前进程内，并显示告警，不会写入明文 token 文件。原有 `Authorization: Bearer ${MCP_API_TOKEN}` 的 PAT 配置继续可用，但同一个 Server 不能同时启用 OAuth 和静态 `Authorization` Header。

GitHub Remote MCP 示例（使用 GitHub App 或 OAuth App 的预注册客户端）：

```yaml
mcp_servers:
  github:
    type: http
    url: https://api.githubcopilot.com/mcp/
    oauth:
      client_id: ${GITHUB_MCP_CLIENT_ID}
      client_secret: ${GITHUB_MCP_CLIENT_SECRET}
      scopes: [repo, read:user]
```

在 GitHub App/OAuth App 中将回调地址注册为 `http://127.0.0.1/oauth/callback`；实际授权时 JulyCode 会使用相同 host/path 的随机本机端口。当前版本不实现 Device Flow、SSH 跨机器回调、远端 token revoke、自动 scope step-up，也不实现 MCP `2025-11-25` 的 CIMD/OIDC 扩展。纯 SSH 环境可继续使用 PAT Header，或在浏览器和 JulyCode 位于同一台机器时使用 OAuth。

MCP 工具使用按需加载，避免大型工具目录持续占用上下文。JulyCode 启动时仍会连接 Server 并发现完整工具目录，但新用户轮次默认只向模型暴露轻量的 `search_mcp_tools`。模型需要 MCP 能力时先按自然语言意图检索，下一次模型迭代只加载相关候选的完整定义；单次最多 5 个候选，同一轮再次检索会替换上一批，轮次结束后全部清空。

实际 MCP 工具继续使用 `server__tool` 全局名，例如 `local_demo` Server 的 `echo` 工具为 `local_demo__echo`，从而避免覆盖内置工具或其他 Server 的同名工具。检索只读取本地已发现目录，不触发远端业务操作或权限确认；实际 MCP 工具仍按有副作用工具处理，并继续经过 Plan Mode、权限、调度和 Hook。未配置 MCP Server 时不会注册 `search_mcp_tools`，因此不会增加额外上下文开销。

## 启动

```bash
julycode
```

进入全屏界面后，在底部输入问题并按回车发送。使用 `Ctrl+C` 或 `Esc` 退出。

默认启动会恢复当前项目最近 30 天内的会话。如果想临时开启一个不带历史的空会话：

```bash
julycode --new-session
```

## 斜杠命令

输入以 `/` 开头时，JulyCode 会优先按本地命令处理；未命中命令时会提示使用 `/help`，不会把未知斜杠输入发给模型。命令名大小写不敏感，支持别名和 Tab 补全：单个匹配会直接补全，多个匹配会显示候选菜单。

状态栏会显示当前持久对话模式：`[DEFAULT]` 是默认执行模式，`[PLAN]` 是计划模式。`/plan` 只切换到计划模式，后续普通输入会以只读工具策略交给 AI；`/do` 只切回默认模式，不执行旧的待执行计划。

内置命令：

- `/help`：显示命令列表，或用 `/help <命令>` 查看单个命令详情。
- `/compact`：手动触发上下文压缩检查，不作为普通 Agent 任务发送给模型。
- `/clear`：清空当前界面消息显示区，并清理已激活 Skill；会话上下文、持久记录和长期记忆仍保留。
- `/plan`：进入 `[PLAN]` 计划模式。
- `/do`：回到 `[DEFAULT]` 默认模式。
- `/session`：显示当前会话标识、恢复状态、消息数量和模式。
- `/memory`：显示长期记忆启用状态、索引可用状态和自动笔记状态。
- `/permission`：显示权限模式和各层规则数量，不修改权限规则。
- `/status`：显示供应商、模型、当前模式、任务状态、最近 Token 用量、Repo Map 状态和 MCP 加载概况。
- `/mcp auth <server>`、`/mcp logout <server>`：授权或退出启用 OAuth 的远程 MCP Server。
- `/agents`：显示可用子 Agent 角色和后台任务详情。
- `/background`：把当前前台等待的子 Agent 任务切到后台，完成后再通知主对话。

启动后还会把已发现的 Skill 注册成斜杠命令。内置 Skill 包括：

- `/commit [参数]`：整理本次改动并准备提交说明。
- `/review [范围或补充要求]`：审查代码或文档变更，优先指出 bug、回归风险和缺失测试。
- `/test [目标或补充要求]`：选择并运行相关测试，解释失败并建议下一步。

## Skill

Skill 用来封装可复用的 AI 操作。JulyCode 启动时只把 Skill 名称和一句说明放进运行时上下文；当模型判断需要某个 Skill 时，会调用系统工具 `load_skill` 加载完整 SOP 和目录型 Skill 的专属工具。用户也可以直接输入对应斜杠命令，例如 `/review README.md`。

Skill 按三层目录加载，同名按优先级覆盖：

```text
<项目>/.julycode/skills/
~/.julycode/skills/
内置 Skill
```

单文件 Skill 是带 YAML frontmatter 的 Markdown：

```markdown
---
name: review
description: 审查代码或文档变更。
tools:
  - read_file
  - search_code
mode: shared
history: 0
model: gpt-5.5
---
你正在执行 review SOP。

用户输入：{{input}}
```

字段含义：

- `name`：唯一名字，也会注册成 `/<name>`。
- `description`：启动时注入的一句话说明。
- `tools`：激活后允许模型看到的工具白名单；`load_skill` 是系统工具，不受白名单限制。
- `mode`：`shared` 使用当前对话执行；`isolated` 用独立对话执行，再把摘要写回主历史。
- `history`：独立模式带入的最近历史消息数量。
- `model`：可选，激活后临时使用指定模型。

正文是发给模型的 SOP 指令，支持 `{{input}}` 和 `{{args}}` 占位符。单个 Skill 文件解析失败会被跳过并报告告警，不会阻断其他 Skill；白名单引用不存在的工具会作为配置错误报告。

目录型 Skill 使用一个目录分发能力包：

```text
my-skill/
  skill.md
  tools/
    helper.yaml
    helper.py
```

`tools/*.yaml` 描述专属工具 schema、脚本、超时和安全级别；脚本通过 stdin 接收 JSON 参数，并向 stdout 输出 JSON 对象。

## 子 Agent

子 Agent 用来把独立子任务交给干净上下文执行。主 Agent 只看到一个稳定工具 `delegate_agent`，不会因为角色增减而动态增删工具；工具参数中的 `type` 决定走定义式子 Agent 还是 Fork 式子 Agent。

两种委派方式：

- `type: defined`：从空白对话启动，使用预定义角色的系统提示和工具限制。必须传 `role`。
- `type: fork`：复制父对话的安全快照并继承父 Agent 当前可见工具集合。Fork 始终强制后台运行，用于利用已有历史继续异步调查。

角色定义是 Markdown + YAML frontmatter。项目角色目录为：

```text
<项目>/.julycode/agents/
```

加载优先级为：

```text
<项目>/.julycode/agents/
~/.julycode/agents/
内置角色
插件角色目录
```

同名角色按上面的优先级覆盖。单个角色解析失败会作为 warning 报告；角色引用不存在的工具会作为配置错误报告，避免半可用角色被启动。

角色示例：

```markdown
---
name: reviewer
description: 审查代码变更并指出风险。
tools_allow:
  - read_file
  - search_code
tools_deny: []
model: inherit
max_iterations: 40
permission_mode: inherit
isolation: worktree
---
你是代码审查子 Agent。

只输出发现、风险和建议，不修改文件。
```

frontmatter 字段：

- `name`：角色名，供 `delegate_agent` 的 `role` 使用。
- `description`：主 Agent 提示中展示的一句话用途说明。
- `tools_allow`：角色工具白名单，也兼容 `allow_tools`。
- `tools_deny`：角色工具黑名单，也兼容 `deny_tools`。
- `model`：`inherit` 或具体模型名；也可以写 `haiku`、`sonnet`、`opus` 并通过配置映射到实际模型。
- `max_iterations`：该角色最大 Agent Loop 轮次。
- `permission_mode`：`inherit`、`strict`、`default` 或 `permissive`。
- `isolation`：可选。省略时共享主工作目录；设为 `worktree` 时，定义式子 Agent 使用独立 Git Worktree。Fork 式子 Agent 不支持该字段。

内置 `reviewer` 和 `code-searcher` 角色的迭代上限均为 40。自定义角色可以通过 `max_iterations` 显式覆盖。

### 子 Agent Worktree 隔离

`isolation: worktree` 会为每次定义式委派创建独立目录和临时分支。Worktree 基于主工作目录当前已提交的 `HEAD`；主目录未提交修改不会被带入，也不会阻止创建。子 Agent 的工具、Hook、项目指令、上下文和项目记忆都使用隔离目录的绝对路径。

环境初始化只处理项目明确声明的路径：

```yaml
sub_agents:
  worktree:
    copy_paths:
      - .julycode.permissions.local.yaml
    symlink_paths:
      - .venv
    ignored_copy_paths:
      - .env
    cleanup_interval_seconds: 3600
    retention_days: 7
```

- `copy_paths`：复制文件或目录，Worktree 内修改不会影响主目录副本。
- `symlink_paths`：为大型依赖目录建立指向主目录同路径的软链。
- `ignored_copy_paths`：补齐已被 Git 忽略但运行所需的文件或目录。

三类配置都只接受仓库根目录相对路径，不支持 glob、独立目标、仓库外源或初始化 shell 命令。默认每小时检查一次，清理超过 7 天且确认安全的目录。

任务结束时，无修改且无新增提交的 Worktree 会自动删除；存在未提交、未跟踪或新增提交时会保留，并把目录、分支和原因返回主 Agent。JulyCode 不会自动提交、推送、合并或丢弃改动，分支合并仍由上层使用 Git 完成。

## 长期团队协作

JulyCode 可以把当前主 Agent 作为 Team Lead，维护一个跨会话存在的团队。团队功能默认开启，可调整文件锁和事件等待参数：

```yaml
teams:
  enabled: true
  lock_timeout_seconds: 2
  lock_retry_interval_seconds: 0.05
  stale_lock_seconds: 30
  wait_timeout_seconds: 30
```

在普通对话中可以让 Lead 创建或打开团队、先建立带依赖的共享任务，再派生成员。例如：“创建团队 demo，把目标拆成两个可并行任务，派 reviewer 和 code-searcher 协作”。团队激活后，Lead 可管理成员、任务和消息并等待团队事件；普通主 Agent 只看到团队生命周期入口。每个成员可以直接读取或更新共享任务，并通过点对点邮箱与 Lead 或其他成员通信，不要求所有消息经 Lead 转发。

成员引用现有子 Agent 角色定义，但使用长期、固定的 Git Worktree 和独立会话。成员自然结束一轮后变为空闲；后续消息会从磁盘恢复原会话、目录和分支。角色可配置 `require_approval`：需要审批的成员领取任务后先向 Lead 提交版本化计划，批准前运行时只允许读取、共享任务和团队消息工具。

团队数据按名称保存在：

```text
~/.julycode/teams/<team>/
  team.json
  tasks.json
  approvals.json
  mailboxes/
  sessions/
  runtime/
```

邮箱和共享状态使用锁文件与原子 JSON 更新，旧死进程锁超过配置期限后才会接管。团队绑定创建时的 Git 仓库，不能从其他项目打开。

当前阶段只支持同进程 `coroutine` 成员后端；请求终端窗格等后端会明确失败，不会静默降级。全部任务完成后 Lead 会汇总成员分支，但不会自动合并、删除 Worktree 或清理团队数据。跨机器团队、实时流式成员通信和复杂条件依赖尚未实现。

子 Agent 运行时会隔离消息、权限控制器、文件读取缓存、上下文管理和 token 记录；共享的是模型访问、Hook 配置与动作执行能力、工具注册表、当前项目目录和文件系统视图。子 Agent 的中间工具结果不会直接进入主对话，主对话只收到 `delegate_agent` 工具结果或后台完成通知。

后台行为：

- 显式传 `background: true` 会立即返回后台任务 ID。
- Fork 式委派总是后台运行。
- 前台定义式子 Agent 超过 `sub_agents.foreground_timeout_seconds` 会自动切到后台。
- 用户可以在等待前台子 Agent 时输入 `/background` 手动切后台。
- 后台完成通知会追加到主会话，包含任务 ID、状态、摘要、停止原因和关键结果。

配置示例：

```yaml
sub_agents:
  enabled: true
  foreground_timeout_seconds: 30
  default_max_iterations: 40
  max_background_tasks: 8
  global_blocked_tools:
    - delegate_agent
  background_allowed_tools:
    - read_file
    - find_files
    - search_code
  model_aliases:
    haiku: claude-haiku-4-6
    sonnet: claude-sonnet-4-6
    opus: claude-opus-4-6
  plugin_role_roots:
    - ~/.julycode/plugin-agents
```

本阶段不做 Worktree 文件隔离、多 Agent 团队编排，也不做后台任务跨会话持久化。应用退出时未完成后台任务会被清理。

## Agent Loop 与工具

JulyCode 会把核心工具暴露给当前模型；如果配置了 MCP Server，则额外暴露 `search_mcp_tools`，远端工具在检索命中后按当前用户轮次加载：

- `read_file`：读取 UTF-8 文本文件内容；可用 1-based `offset` 指定起始行，用 `limit` 指定最多读取行数，并返回实际范围、总行数和是否还有后续内容。
- `write_file`：创建或覆盖写入 UTF-8 文本文件。
- `edit_file`：按原文唯一匹配替换文件内容；匹配不到或匹配多次都不会写入。
- `run_command`：在当前项目目录按 argv 执行单个本地命令，返回退出码、标准输出和标准错误；不经过 Shell，不支持管道、重定向、`cd` 或复合命令。
- `find_files`：按 glob 模式查找文件路径；默认遵守 Git ignore，并排除 `.julycode`、缓存、依赖和构建目录。
- `search_code`：搜索代码内容并返回匹配文件、行列和文本摘要；默认使用与 `find_files` 相同的忽略范围，显式指定项目内非根文件或目录时可搜索被忽略目标。
- `search_mcp_tools`：按任务意图检索 MCP 工具，下一迭代加载最多 5 个候选。
- `delegate_agent`：把独立子任务委派给子 Agent；主 Agent 中稳定暴露，子 Agent 中默认不可用以防无限嵌套。

Agent Loop 会按 ReAct 风格工作：模型先输出文本或工具调用，JulyCode 执行工具并把结果回灌给模型，然后继续下一轮，直到模型不再请求工具并给出最终回复。一次模型响应中包含多个工具调用时，读类工具可以并发执行；写入、修改和命令工具会按顺序串行执行。

`/status` 的 MCP 摘要会区分已发现工具数和当前轮次暴露工具数。前者表示启动时从 Server 获取的目录规模，后者最多为本轮最近一次检索命中的 5 个候选；任务结束后当前轮次暴露数恢复为 0。

Agent Loop 有几类停止条件：

- 模型给出最终回复且不再请求工具。
- 达到 `agent.max_iterations` 迭代上限。
- 用户在运行中按 `Ctrl+C` 取消任务。
- 模型连续请求不存在的工具。
- 模型流式输出或供应商请求出错。

可在配置中设置迭代上限：

```yaml
agent:
  max_iterations: 40
```

主 Agent 和未显式指定上限的子 Agent 默认最多运行 40 轮。40 是默认值而非硬上限，合法的正整数配置可以显式覆盖。

## 上下文管理

JulyCode 会在每次模型请求前做上下文管理，避免长任务因为历史过大而超过模型窗口。第一层是轻量预防：当工具结果过大，系统会把完整结果保存到项目内 `.julycode/context/<session_id>/tool-results/`，对话里只保留预览、规模信息和可重新读取的路径。后续模型如果需要完整细节，应使用 `read_file` 读取这个路径。

第二层是重量兜底：当整体会话接近上下文窗口上限时，系统会生成结构化摘要，压缩较早历史，并保留最近约 1 万 Token 或至少最近 5 条消息原文。摘要会附带边界提示，提醒模型不要凭摘要或预览脑补代码细节。

可以通过配置调整上下文窗口和阈值：

```yaml
context:
  enabled: true
  window_tokens: 128000
  single_tool_result_tokens: 4000
  turn_tool_result_tokens: 8000
  tool_preview_chars: 2000
  recent_tokens: 10000
  min_recent_messages: 5
  auto_reserve_tokens: 13000
  manual_reserve_tokens: 3000
  summary_failure_limit: 3
  chars_per_token: 4.0
  store_dir: .julycode/context
```

用户也可以在 TUI 中输入 `/compact` 手动触发压缩。手动压缩不会作为普通任务发给模型；系统会直接显示压缩报告或说明当前历史较短无需压缩。

## 会话恢复与长期记忆

JulyCode 启动时会加载三层项目指令文件，并按优先级注入模型上下文：

- `.julycode/AGENTS.md`：项目管理目录级，优先级最高。
- `AGENTS.md`：项目根级，优先级次之。
- `~/.julycode/AGENTS.md`：用户级，优先级最低。

指令文件支持 `@include <相对路径>` 引用同项目内的其他 Markdown 文件。加载器会限制嵌套深度、检测循环引用，并拦截跳出项目目录的路径。

会话历史以 JSONL 追加写入 `.julycode/sessions/`，每个会话一个文件，ID 形如 `YYYYMMDD-HHMMSS-xxxx`。恢复时会跳过坏行，遇到未配对工具调用会截断到安全边界；如果历史过大，会先尝试一次上下文压缩。超过 30 天未活动的会话会定期清理。

长期记忆分用户级和项目级存储：

- 用户级：`~/.julycode/memory/`
- 项目级：`.julycode/memory/`

每条记忆是一份带 frontmatter 的 Markdown 文件，分类为 `preference`、`correction`、`project_knowledge`、`reference`。系统会维护各自的 `index.md`，并控制在 200 行和 25KB 内。每轮 Agent Loop 自然结束后，JulyCode 会在后台用一次无工具模型请求更新自动笔记；失败只记录告警，不影响当前对话。

自动提取采用保守策略：只有用户明确表达或确认、且跨任务持续有效的信息才会落盘。新笔记会记录用户原话证据、作用域、类别和置信度；关键偏好还必须包含“以后、始终、默认、必须、禁止”等明确长期约束，并达到配置的置信阈值。临时要求、模型猜测、助手或工具单独提供的内容以及敏感凭据不会成为长期记忆。旧格式笔记仍可兼容读取。

`julycode --new-session` 只关闭最近会话消息恢复，不会关闭长期记忆：新会话的普通消息历史为空，但首个模型请求仍会加载同一项目和用户的长期记忆。因此可以严格验证跨会话继承，而不是依赖旧对话历史。

可以通过配置关闭或调整记忆功能：

```yaml
memory:
  enabled: true
  auto_restore: true
  auto_notes_enabled: true
  retention_days: 30
  time_gap_hours: 24
  index_max_lines: 200
  index_max_bytes: 25000
  critical_preference_min_confidence: 0.95
```

记忆提取质量和空白新会话继承可运行专项评测：

```bash
# 确定性流程回归，不代表真实模型质量
python eval/run_memory_eval.py --mode offline --output eval/results/memory-quality/offline

# 真实模型发布验收
python eval/run_memory_eval.py --mode online --output eval/results/memory-quality/latest
```

完整在线评测约发起 200 次模型请求，会消耗真实额度。报告包含整体 Precision/Recall/F1、关键偏好 Precision/Recall、首轮理解正确率和背景重复说明减少率。

`.julycode/context/`、`.julycode/sessions/` 和 `.julycode/memory/` 都是本地自动产物，默认已在 `.gitignore` 中忽略。

## 权限系统

JulyCode 在执行本地工具前会经过权限系统，核心防御包括高危命令黑名单、项目路径沙箱、可配置规则、权限模式和用户确认。权限拒绝会作为工具失败结果回灌给模型，Agent Loop 不会仅因为一次权限拒绝就终止，模型可以改用更安全的方案继续。

权限模式通过 `permissions.mode` 配置：

```yaml
permissions:
  mode: default
```

- `strict`：严格模式。有副作用工具即使命中 allow 规则，也需要用户确认。
- `default`：默认模式。明确 allow 自动执行，明确 deny 自动拒绝；未命中的有副作用工具需要用户确认。
- `permissive`：放行模式。未命中的工具自动允许，但显式 deny、高危命令和路径沙箱仍然生效。

权限规则使用单独 YAML 文件：

```text
~/.julycode/permissions.yaml          # 用户级
.julycode.permissions.yaml            # 项目级
.julycode.permissions.local.yaml      # 本地级
```

优先级为：会话级 > 本地级 > 项目级 > 用户级。规则写在 `rules` 对象中，格式为 `工具名(模式): allow|deny`，支持精确和 glob 匹配。命令工具兼容 `Bash(...)` 写法：

```yaml
rules:
  "Bash(git *)": allow
  "read_file(README.md)": allow
  "write_file(src/generated/**)": deny
```

高危命令黑名单和项目路径沙箱不可被配置、权限模式或人工确认绕过。内置文件类工具只能访问项目目录内路径；路径判断会先解析符号链接再判断是否仍在项目内。

## Hook

Hook 用来在 Agent 生命周期事件上挂自动动作。规则写在主配置的 `hooks:` 字段中；项目级 `.julycode.yaml` 的 `hooks` 会整体覆盖用户级 `~/.julycode/config.yaml` 的 `hooks`。每条规则由 `event`、可选 `if` 和 `action` 组成，按声明顺序执行。

```yaml
hooks:
  - name: block-dangerous-command
    event: tool.before
    if:
      all:
        - field: tool.name
          match: run_command
        - field: tool.arguments.command
          match: "regex:^curl\\s+.*\\|\\s*sh"
    action:
      type: prompt
      text: "命令已被项目 Hook 拦截，请改用可审查的下载和执行步骤。"
      tool_block:
        reason: "项目策略禁止直接执行 curl | sh"
        error_type: hook_blocked

  - name: inject-turn-context
    event: turn.start
    action:
      type: prompt
      text: "本项目要求修改后优先运行相关 pytest。"

  - name: format-after-tool
    event: tool.after
    if:
      any:
        - field: tool.name
          match: write_file
        - field: tool.name
          match: edit_file
    action:
      type: command
      command: python -m compileall src tests -q
      timeout_seconds: 15
    once: true

  - name: notify-stop
    event: system.stopped
    action:
      type: http
      method: POST
      url: http://127.0.0.1:9000/julycode-hook
      json:
        source: julycode
    background: true

  - name: future-worker
    event: turn.end
    action:
      type: sub_agent
      name: summary-worker
      prompt: "后续 SubAgent 章节对接。"
```

事件覆盖会话、轮次、消息、工具和系统状态，例如 `session.start`、`session.end`、`turn.start`、`turn.end`、`message.user`、`message.assistant`、`tool.before`、`tool.after`、`system.context_compacted`、`system.stopped` 和 `system.error`。`tool.before` 可以拦截工具；拦截后目标工具不会执行，拒绝原因会作为 `hook_blocked` 工具失败结果回灌给模型。

条件使用字段路径匹配事件数据，例如 `tool.name`、`tool.arguments.command`、`result.error_type`。匹配语法与权限系统共用，支持精确匹配、glob、`regex:` 正则和 `!` 反向匹配。逻辑组合只能二选一：`if.all` 表示全部满足，`if.any` 表示任一满足。

动作支持四种类型：`command` 执行 shell 命令，`prompt` 注入下一次模型请求的运行时提示，`http` 发送 HTTP 请求，`sub_agent` 只记录占位状态。`once: true` 表示当前运行期只执行一次，重启后会重新计算。`background: true` 表示后台异步执行，不阻塞 Agent 主流程；`tool.before` 不允许后台执行。`timeout_seconds` 控制命令和 HTTP 请求超时。

Hook 自身失败只记录状态，不会中断 Agent Loop 或让 TUI 崩溃。Hook 也不能绕过权限系统、Plan Mode、Skill 工具白名单、危险命令黑名单或项目路径沙箱；例如 command 动作仍会经过 `run_command` 的权限判断。

## 结构化系统提示与缓存观测

JulyCode 会为每次模型请求构造结构化系统提示。稳定提示按固定优先级组织为身份、系统约束、任务模式、动作执行、工具使用、语气风格和文本输出七个模块；这些内容不包含当前工作目录、用户消息或密钥，便于供应商复用提示缓存。

运行时补充会拆成两类系统级补充。可缓存运行时前缀包含允许工具摘要和项目指令等相对稳定内容，会放在动态内容之前；动态运行时补充通过 `<julycode_runtime_context>` 标签注入，包含当前工作目录、Agent 模式、轮次、当前用户目标、Hook 注入、记忆索引、恢复提示和上下文摘要。运行时补充不会作为普通用户消息写入会话历史。

缓存优化可以在主配置中调整：

```yaml
prompt_cache:
  enabled: true
  key_namespace: julycode
  openai_cache_key: true
  openai_retention: 24h
  anthropic_cache_control: true
```

OpenAI 协议默认会为稳定提示、可缓存运行时前缀和工具定义生成短 hash 形式的 `prompt_cache_key`，不会把原始提示、路径、用户输入或密钥写入 key；`prompt_cache_retention` 仅在显式配置 `openai_retention` 时发送。若 OpenAI 兼容接口明确拒绝缓存参数，JulyCode 会重试一次不带缓存参数的同一请求。

Anthropic 协议会把 `cache_control` 放在最后一个可缓存前缀块上，动态运行时补充不设置 `cache_control`，避免每轮变化内容导致持续写入但很少读取缓存。关闭 `prompt_cache.enabled` 或 `anthropic_cache_control` 后，请求仍会正常发送，只是不带显式缓存断点。

工具规则会同时出现在全局系统提示和工具描述中，例如优先使用专用读取/查找/搜索工具、编辑前先读取或搜索目标文件、谨慎使用完整写入和本地命令。工具失败结果会回灌给模型，模型应根据失败原因调整下一步。

Provider 返回 usage 时，JulyCode 会尝试解析缓存观测字段。OpenAI 协议读取 `cached_tokens`；Anthropic 协议读取缓存创建和缓存读取 token 字段。状态栏可能显示 `Cache: hit`、`Cache: write`、`Cache: miss`、`Cache: unknown` 或 `Cache: unsupported`。当供应商没有返回缓存字段时，请求仍会正常完成，缓存状态显示为 unknown。

实际缓存命中仍取决于供应商、模型、请求长度、请求间隔、缓存 TTL、路由策略和完全一致的前缀。JulyCode 只提高命中概率并保留观测结果，不保证每次请求都命中。

## Plan Mode

JulyCode 的 Plan Mode 是持久模式切换：输入 `/plan` 后进入 `[PLAN]`，后续普通输入只开放读取、查找和搜索类工具，让模型了解现状并生成计划；输入 `/do` 后回到 `[DEFAULT]`，后续普通输入恢复完整工具能力。

`/plan` 和 `/do` 本身不会作为普通用户消息发送给模型，也不会创建或执行待执行计划。模式约束通过运行时补充注入，不污染用户对话历史，也不会破坏稳定提示缓存。

## 范围

当前版本不实现向量数据库、RAG 检索、团队记忆同步、交互式命令、操作系统级沙箱隔离、网络请求限制、资源配额、审计日志或插件系统。MCP 当前只接入工具能力，不实现 MCP 资源、提示词、采样、Server 健康检查或自动重连。
