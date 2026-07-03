# MCP OAuth 2.1 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。验收时在条目后补充实际命令输出或观察证据。

## 验收记录（2026-07-03）

- 自动化：`pytest -q` 最终结果为 `734 passed in 24.87s`；`python -m compileall -q src tests` 与 `git diff --check` 退出码均为 0。
- OAuth 专项：challenge/元数据、DCR 与预注册回退、PKCE/回调、Keyring/内存 Store、授权码交换、单飞刷新与 refresh token 轮换、Transport 401 单次重试、Manager 动态注册/注销、命令和 TUI 本地消息隔离均有测试覆盖。
- tmux E2E：隔离会话 `oauth-e2e` 中同时启动静态 MCP、OAuth MCP、失败 Server、mock 模型和 MewCode。启动状态为 `oauth_demo=需要授权`、已加载 Server 1、失败 Server 1；执行 `/mcp auth oauth_demo` 后显示 loopback URL，并变为已加载 Server 2、工具 3、`oauth_demo=已授权`。
- tmux 真实对话：输入“请调用 oauth_demo 工具 echo 内容 hello-oauth”，界面记录 `工具: oauth_demo__echo`、完成状态和最终“工具结果已收到”；fixture 日志确认 DCR、`code_challenge_method=S256`、resource、token exchange 和 `tools/call`。
- tmux logout：执行 `/mcp logout oauth_demo` 后显示“相关工具已移除”，状态恢复为已加载 Server 1、工具 2、`oauth_demo=需要授权`，静态 Server 和失败 Server 状态未受影响。
- 安全与清理：tmux 输出未出现 fixture access/refresh token；工作区未生成 OAuth token 文件；E2E 使用显式内存 Store 并显示降级告警；结束后 `ps` 无 tmux/fixture/mock/TUI 进程。测试注入层只为本机 HTTP fixture 放宽发现，生产 OAuth 仍强制 HTTPS。
- 依赖安装：`keyring>=24.3,<26` 已写入 `pyproject.toml` 和 `requirements.txt`。本机联网安装因 PyPI 连接 `SSLEOFError` 失败；无 Keyring 路径按设计降级为内存 Store，Fake Keyring 持久化/删除/隔离测试通过。

## 实现完整性

- [x] AC1：未启用 OAuth 的 HTTP MCP、静态 PAT Header MCP 和 stdio MCP 行为保持原样（验证：运行 `pytest -q tests/test_config.py tests/test_mcp_transport.py tests/test_mcp_manager.py -k "not oauth"`，并确认现有 MCP 回归用例通过）
- [x] AC2：OAuth 配置可解析预注册 client、secret 环境变量和 scopes，且拒绝静态 Authorization 冲突与缺失环境变量（验证：运行 `pytest -q tests/test_config.py -k "mcp and oauth"`，确认正常、禁用、冲突和脱敏错误用例通过）
- [x] AC3：无 token 的 OAuth Server 只进入“需要授权”，不会自动打开浏览器或阻止 TUI、内置工具和其他 Server 启动（验证：运行 `pytest -q tests/test_mcp_manager.py tests/test_tui_smoke.py -k "oauth and authorization_required"`，确认浏览器 mock 未被调用且应用可继续交互）
- [x] AC4：`/mcp auth <server>` 只对有效 OAuth HTTP Server 启动授权，并正确拒绝未知、stdio、未启用 OAuth 和重复授权目标（验证：运行 `pytest -q tests/test_commands.py tests/test_mcp_manager.py -k "mcp and auth"`）
- [x] AC5：客户端能解析 Bearer challenge，完成 RFC 9728/RFC 8414 发现，并拒绝非 HTTPS、issuer/resource 不匹配和不支持 S256 的元数据（验证：运行 `pytest -q tests/test_mcp_oauth_discovery.py`）
- [x] AC6：存在 registration endpoint 时优先 DCR；DCR 不可用时回退预注册 client；两者都不可用时返回可执行配置提示（验证：运行 `pytest -q tests/test_mcp_oauth_flow.py -k "dynamic_registration or preregistered or registration_fallback"`）
- [x] AC7：授权 URL 包含 PKCE S256、高熵 state、随机回环 redirect URI、正确 scope 和 resource，回调只监听 `127.0.0.1`（验证：运行 `pytest -q tests/test_mcp_oauth_flow.py -k "pkce or authorization_url or loopback or scope_priority"`）
- [x] AC8：浏览器打开失败时仍显示可复制 URL；成功、拒绝、错误 state、重复回调、取消和超时都会关闭监听并恢复明确状态（验证：运行 `pytest -q tests/test_mcp_oauth_flow.py tests/test_tui_smoke.py -k "browser or callback or timeout or cancel"`）
- [x] AC9：授权码交换成功后无需重启即可重新初始化、注册并调用 MCP 工具，所有 MCP HTTP 请求使用 Header 而非 URL 携带 token（验证：运行 `pytest -q tests/test_mcp_transport.py tests/test_mcp_manager.py tests/test_agent.py -k "oauth or remote_mcp"`，检查捕获请求和工具事件）
- [x] AC10：token 临近过期或请求收到 401 时只刷新并重试一次；refresh token 正确轮换，刷新失败进入需要重新授权状态（验证：运行 `pytest -q tests/test_mcp_oauth_flow.py tests/test_mcp_transport.py -k "refresh or rotation or retry"`）
- [x] AC11：Keyring 可用时凭据可跨 Store 实例读取；不可用时仅内存保存并显示警告，磁盘无明文 token 文件（验证：运行 `pytest -q tests/test_mcp_oauth_store.py tests/test_tui_smoke.py -k "keyring or memory or persistent"`，并扫描测试临时目录）
- [x] AC12：`/mcp logout <server>` 删除本地凭据、关闭会话、移除该 Server 工具并恢复需要授权状态（验证：运行 `pytest -q tests/test_commands.py tests/test_mcp_manager.py tests/test_mcp_oauth_flow.py -k logout`）
- [x] AC13：`/status` 显示各 OAuth Server 的需要授权、授权中、已授权、刷新失败和错误状态，多 Server 信息互不串扰（验证：运行 `pytest -q tests/test_commands.py tests/test_mcp_manager.py -k "oauth and status"`）
- [x] AC14：元数据、DCR、token、refresh 和回调都有超时；退出会取消授权；超长或畸形响应被有限读取并拒绝（验证：运行 `pytest -q tests/test_mcp_oauth_discovery.py tests/test_mcp_oauth_flow.py tests/test_tui_smoke.py -k "timeout or response_limit or malformed or cancel"`）
- [x] AC15：异常、状态、报告、TUI、ChatSession 和模型请求均不包含 token、refresh token、client secret、授权码或 PKCE verifier（验证：运行 `pytest -q tests/test_mcp_oauth_discovery.py tests/test_mcp_oauth_store.py tests/test_mcp_oauth_flow.py tests/test_mcp_transport.py tests/test_mcp_manager.py tests/test_tui_smoke.py -k "secret or redacted or oauth"`）
- [x] AC16：README 包含通用 OAuth、GitHub Remote MCP、GitHub App/OAuth App、命令、Keyring、内存回退和不支持范围（验证：运行 `rg -n "oauth:|/mcp auth|/mcp logout|Keyring|GitHub App|OAuth App|127.0.0.1/oauth/callback|Device|2025-06-18" README.md`）

## 协议与安全边界

- [x] `WWW-Authenticate` 多认证方案、引号转义、scope 空白和畸形参数均被确定性解析（验证：运行 `pytest -q tests/test_mcp_oauth_discovery.py -k challenge`）
- [x] Protected Resource Metadata、Authorization Server Metadata 和 OAuth endpoint 不跟随重定向、不接受 userinfo/fragment，响应上限为 1 MiB（验证：运行 `pytest -q tests/test_mcp_oauth_discovery.py -k "redirect or userinfo or fragment or response_limit"`）
- [x] token endpoint 只使用服务端声明且本项目支持的 `none`、`client_secret_post` 或 `client_secret_basic`（验证：运行 `pytest -q tests/test_mcp_oauth_flow.py -k token_endpoint_auth`）
- [x] state、verifier 和 challenge 使用密码学安全随机源；S256 challenge 与 verifier 匹配（验证：运行 `pytest -q tests/test_mcp_oauth_flow.py -k pkce`，重复生成并确认 state/verifier 不重复）
- [x] 回调服务仅接受固定路径的一次 GET，限制请求大小，错误页和成功页不回显授权码或 token（验证：运行 `pytest -q tests/test_mcp_oauth_flow.py -k callback`）
- [x] 并发请求只触发一次 refresh；第二个请求复用锁内更新后的 token（验证：运行 `pytest -q tests/test_mcp_oauth_flow.py -k concurrent_refresh`）
- [x] token 凭据 key 绑定 Server 名和规范化 MCP URL，修改 URL 后不会读取或发送旧 token（验证：运行 `pytest -q tests/test_mcp_oauth_store.py -k "account_key or url_isolation"`）
- [x] OAuth Server 状态和错误只包含公开摘要，敏感模型字段 `repr()` 不泄漏 secret（验证：运行 `pytest -q tests/test_mcp_oauth_flow.py tests/test_mcp_oauth_store.py -k "repr or public_status or redacted"`）

## 集成

- [x] OAuth 认证层仅接入 Streamable HTTP，stdio Transport 的环境变量凭据和 JSON-RPC 流程不变（验证：运行 `pytest -q tests/test_mcp_transport.py -k stdio`）
- [x] MCP Manager 对授权挑战单独记为 authorization_required，不错误计入 failed_servers（验证：运行 `pytest -q tests/test_mcp_manager.py -k "authorization_required and report"`）
- [x] OAuth 成功后只注册目标 Server 的工具；刷新失败或 logout 只移除对应 `mcp:<server>` origin，不影响内置工具和其他 MCP Server（验证：运行 `pytest -q tests/test_mcp_manager.py tests/test_mcp_tools.py -k "origin or isolated or logout"`）
- [x] Agent Loop 继续把授权后的 `server__tool` 当作普通工具执行并把结果回灌模型（验证：运行 `pytest -q tests/test_agent.py -k remote_mcp`）
- [x] `/mcp` 是纯本地命令，授权 URL、OAuth 状态和命令结果不进入 ChatSession 或模型请求（验证：运行 `pytest -q tests/test_tui_smoke.py tests/test_commands.py -k "mcp and oauth"`，检查 session message 数量和 Provider 请求）
- [x] MewCode 退出会关闭 OAuth HTTP Client、MCP Session 和回调监听，但不会删除持久 token（验证：运行 `pytest -q tests/test_mcp_manager.py tests/test_tui_smoke.py -k "oauth and close"`）
- [x] OpenAI 与 Anthropic Provider 的 MCP 工具格式和权限行为没有回归（验证：运行 `pytest -q tests/test_openai_provider.py tests/test_anthropic_provider.py tests/test_tool_scheduler.py -k "mcp or tool"`）

## 编译与测试

- [x] OAuth 模块和 fixture 可编译（验证：运行 `python -m py_compile src/mewcode/mcp/oauth/*.py tests/fixtures/mcp_oauth_server.py`，期望退出码为 0）
- [x] OAuth 专项测试全部通过（验证：运行 `pytest -q tests/test_mcp_oauth_discovery.py tests/test_mcp_oauth_store.py tests/test_mcp_oauth_flow.py`）
- [x] 配置、MCP、命令和 TUI 受影响测试全部通过（验证：运行 `pytest -q tests/test_config.py tests/test_mcp_transport.py tests/test_mcp_client.py tests/test_mcp_tools.py tests/test_mcp_manager.py tests/test_commands.py tests/test_tui_smoke.py`）
- [x] Agent、Provider、权限和工具回归测试通过（验证：运行 `pytest -q tests/test_agent.py tests/test_openai_provider.py tests/test_anthropic_provider.py tests/test_tool_scheduler.py tests/test_permissions.py tests/test_tools.py`）
- [x] 全量测试通过（验证：运行 `pytest -q`，期望无失败、错误或跳过的必需 OAuth 用例）
- [x] 全部源码和测试可编译（验证：运行 `python -m compileall -q src tests`，期望退出码为 0）
- [x] Git diff 无空白错误且不存在 OAuth 临时 token、测试配置、证书或进程输出文件（验证：运行 `git diff --check && git status --short`，人工核对仅包含预期源码、测试、文档和规格文件）
- [x] 项目未配置 lint 时记录为不适用；若检测到 lint 配置则运行对应命令（验证：运行 `rg -n "\[tool\.(ruff|black|mypy|pyright)" pyproject.toml || true`，无输出记为不适用）

## 端到端场景

- [x] 场景 1：首次 OAuth 授权并调用工具——在 tmux 启动 OAuth/MCP fixture、mock 模型和 MewCode；启动后不弹浏览器，`/status` 显示 `oauth_demo` 需要授权；执行 `/mcp auth oauth_demo`，访问显示的 URL 完成回调；无需重启即可在真实对话中调用 `oauth_demo__echo` 并获得最终回复（验证：`tmux capture-pane -p -S -200` 同时包含需要授权、授权 URL、授权成功、工具调用和最终回复）
- [x] 场景 2：认证隔离——同时配置 `oauth_demo`、静态 `local_demo` 和不可达 `broken_demo`；OAuth 未授权和失败 Server 不影响 `local_demo__echo` 与内置 `read_file`（验证：在 tmux 中分别发送两段真实请求，观察两个工具成功并在 `/status` 中看到三个独立状态）
- [x] 场景 3：刷新与轮换——fixture 令首次 access token 失效并返回一次 401；下一次真实工具请求触发单次 refresh，使用轮换后的 token 成功，界面不要求重新授权（验证：fixture 请求计数显示一次 refresh、一次重试，tmux 最终回复成功）
- [x] 场景 4：logout——执行 `/mcp logout oauth_demo` 后 `/status` 恢复需要授权，`oauth_demo__echo` 从可用工具中移除，静态 MCP 和内置工具继续工作（验证：tmux 输出包含 logout 成功、OAuth 状态变化和静态工具后续成功调用）
- [x] 场景 5：安全存储降级——在无可用 Keyring backend 的隔离 HOME 启动 MewCode，授权成功后显示“仅当前进程有效”警告；退出并重启后重新要求授权，且隔离 HOME 中找不到明文 token（验证：`rg -uuu "test-access-token|test-refresh-token" <隔离HOME>` 无命中）
- [x] 场景 6：验收环境清理——完成测试后关闭 MewCode、mock 模型、OAuth/MCP fixture 和浏览器模拟进程，删除临时配置与测试 Keyring 记录（验证：`tmux ls` 不包含测试会话，`ps` 不包含 fixture/mock 命令，`git status --short` 不包含临时文件）
