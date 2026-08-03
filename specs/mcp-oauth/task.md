# MCP OAuth 2.1 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `pyproject.toml` | 声明有界 Keyring 运行时依赖 |
| 修改 | `requirements.txt` | 同步开发与测试安装依赖 |
| 修改 | `README.md` | 通用 OAuth 和 GitHub Remote MCP 使用说明 |
| 修改 | `src/julycode/config.py` | OAuth 配置模型、环境变量展开和冲突校验 |
| 修改 | `src/julycode/commands/models.py` | MCP 命令上下文与公开状态快照 |
| 修改 | `src/julycode/commands/builtin.py` | `/mcp` 命令和 `/status` OAuth 状态输出 |
| 修改 | `src/julycode/tui/app.py` | 授权/登出命令实现、URL 展示和生命周期清理 |
| 修改 | `src/julycode/mcp/errors.py` | OAuth challenge 与认证错误类型 |
| 修改 | `src/julycode/mcp/transport.py` | Bearer Header、401 恢复和单次重试 |
| 修改 | `src/julycode/mcp/tools.py` | 按 Server 标记 MCP 工具 origin |
| 修改 | `src/julycode/mcp/manager.py` | OAuth Session、状态隔离、重初始化和注销 |
| 修改 | `src/julycode/mcp/__init__.py` | 导出 OAuth 公开类型 |
| 新建 | `src/julycode/mcp/oauth/__init__.py` | OAuth 子系统公开接口 |
| 新建 | `src/julycode/mcp/oauth/models.py` | OAuth 元数据、凭据和状态模型 |
| 新建 | `src/julycode/mcp/oauth/discovery.py` | Challenge、RFC 9728/RFC 8414 发现与校验 |
| 新建 | `src/julycode/mcp/oauth/client.py` | DCR、令牌交换、刷新和 Session 协调器 |
| 新建 | `src/julycode/mcp/oauth/callback.py` | `127.0.0.1` 临时回调服务 |
| 新建 | `src/julycode/mcp/oauth/store.py` | Keyring 和内存凭据存储 |
| 新建 | `tests/fixtures/mcp_oauth_server.py` | 可控 OAuth/MCP 协议 fixture |
| 修改 | `tests/test_config.py` | OAuth 配置兼容性与脱敏测试 |
| 新建 | `tests/test_mcp_oauth_discovery.py` | challenge、元数据和安全校验测试 |
| 新建 | `tests/test_mcp_oauth_store.py` | Keyring、序列化和内存回退测试 |
| 新建 | `tests/test_mcp_oauth_flow.py` | PKCE、DCR、回调、交换、刷新和 logout 测试 |
| 修改 | `tests/test_mcp_transport.py` | Transport OAuth Header 和 401 重试测试 |
| 修改 | `tests/test_mcp_tools.py` | MCP 工具 Server origin 与注销隔离测试 |
| 修改 | `tests/test_mcp_manager.py` | Manager 认证状态、重初始化和隔离测试 |
| 修改 | `tests/test_commands.py` | `/mcp` 与 `/status` 测试 |
| 修改 | `tests/test_tui_smoke.py` | TUI OAuth 用户流和敏感数据隔离测试 |
| 修改 | `specs/mcp-oauth/checklist.md` | 记录最终自动化与 tmux 验收证据 |

## T1: 添加安全凭据存储依赖

**文件：** `pyproject.toml`、`requirements.txt`
**依赖：** 无
**步骤：**
1. 在运行时依赖中加入支持 Python 3.11 的有界 `keyring` 版本范围。
2. 在 `requirements.txt` 的运行时依赖区同步相同范围。
3. 安装更新后的开发依赖，确认项目可导入 Keyring。

**验证：** 运行 `python -m pip install -e ".[dev]" && python -c "import keyring; print(keyring.__version__ if hasattr(keyring, '__version__') else 'keyring-ok')"`，期望安装成功并输出版本或 `keyring-ok`。

## T2: 扩展 OAuth 配置模型和解析

**文件：** `src/julycode/config.py`、`tests/test_config.py`
**依赖：** T1
**步骤：**
1. 增加 `McpOAuthConfig`，并为 `McpServerConfig` 增加可选 `oauth` 字段。
2. 解析 `enabled`、`client_id`、`client_secret` 和 `scopes`，复用环境变量展开与脱敏规则。
3. 拒绝 stdio OAuth、非法 scope、缺失环境变量，以及启用 OAuth 时大小写任意形式的静态 `Authorization` Header。
4. 保持未配置 OAuth、`enabled: false`、PAT Header 和用户/项目 Server 合并行为不变。

**验证：** 运行 `pytest -q tests/test_config.py -k "mcp and oauth"`，期望 OAuth 正常、禁用、冲突、环境变量和兼容性用例全部通过。

## T3: 定义 OAuth 运行模型和结构化错误

**文件：** `src/julycode/mcp/oauth/models.py`、`src/julycode/mcp/errors.py`、`src/julycode/mcp/oauth/__init__.py`、`src/julycode/mcp/__init__.py`
**依赖：** T2
**步骤：**
1. 定义 challenge、两类元数据、客户端注册、token、凭据包、回调结果和认证状态模型。
2. 对 token、refresh token 和客户端 secret 字段关闭 `repr`。
3. 增加 `McpAuthorizationRequired` 和 OAuth 配置、发现、回调、存储错误类型，只携带公开诊断信息。
4. 从 OAuth 子包和 MCP 包导出稳定公开接口。

**验证：** 运行 `python -m py_compile src/julycode/mcp/oauth/models.py src/julycode/mcp/errors.py && python -c "from julycode.mcp.oauth import McpOAuthStatus, OAuthTokenSet"`，期望编译和导入成功，且测试构造 token 后 `repr()` 不包含 token 值。

## T4: 实现 Bearer challenge 解析和 URL 安全校验

**文件：** `src/julycode/mcp/oauth/discovery.py`、`tests/test_mcp_oauth_discovery.py`
**依赖：** T3
**步骤：**
1. 解析包含多个认证方案和带引号参数的 `WWW-Authenticate`，提取 Bearer `resource_metadata` 与 scope。
2. 对缺失、重复、畸形 challenge 返回结构化错误。
3. 实现 HTTPS URL、禁止 userinfo/fragment、issuer/resource 一致性和规范化校验。
4. 添加不把完整 Header 或敏感参数写入异常的测试。

**验证：** 运行 `pytest -q tests/test_mcp_oauth_discovery.py -k "challenge or url"`，期望合法 Bearer challenge 可解析，畸形和不安全 URL 被拒绝。

## T5: 实现 RFC 9728 与 RFC 8414 元数据发现

**文件：** `src/julycode/mcp/oauth/discovery.py`、`tests/test_mcp_oauth_discovery.py`
**依赖：** T4
**步骤：**
1. 增加有限超时、禁止重定向、最大 1 MiB 的流式 JSON 获取器。
2. 获取 Protected Resource Metadata，验证 resource、authorization_servers 和 scopes_supported。
3. 按 RFC 8414 构造 well-known 地址并获取 Authorization Server Metadata。
4. 验证 issuer、authorization endpoint、token endpoint、registration endpoint、S256 和 token endpoint 认证方法。
5. 对多授权服务器选择第一个通过安全校验的 HTTPS issuer。

**验证：** 运行 `pytest -q tests/test_mcp_oauth_discovery.py -k "metadata or issuer or pkce or response_limit"`，期望成功发现和所有安全拒绝分支通过。

## T6: 实现 DCR、授权 URL、令牌交换和刷新客户端

**文件：** `src/julycode/mcp/oauth/client.py`、`tests/test_mcp_oauth_flow.py`
**依赖：** T5
**步骤：**
1. 生成高熵 state、PKCE verifier 和 S256 challenge，构造包含 redirect URI、scope、state、resource 的授权 URL。
2. 实现 RFC 7591 注册请求和严格响应解析。
3. 实现 `none`、`client_secret_post`、`client_secret_basic` 三种 token endpoint 认证方式。
4. 实现授权码交换和 refresh 请求，携带 resource，并解析 expiry、scope 和 refresh token 轮换。
5. DCR 失败时仅向上返回脱敏原因，由 Session 决定是否回退预注册客户端。

**验证：** 运行 `pytest -q tests/test_mcp_oauth_flow.py -k "pkce or authorization_url or dynamic_registration or exchange or token_endpoint_auth"`，期望参数、认证方式、DCR 和 token 解析用例全部通过。

## T7: 实现 Keyring 与内存凭据存储

**文件：** `src/julycode/mcp/oauth/store.py`、`tests/test_mcp_oauth_store.py`
**依赖：** T3
**步骤：**
1. 实现 Server 名加规范化 URL 哈希的稳定账户 key。
2. 实现凭据白名单 JSON 序列化和严格反序列化。
3. 用线程包装 Keyring 的 load/save/delete，并设置有限等待时间。
4. 实现内存存储和 Keyring 不可用、锁定、畸形数据、保存失败时的内存回退与一次性警告。
5. 确保不创建任何明文 token 文件。

**验证：** 运行 `pytest -q tests/test_mcp_oauth_store.py`，期望持久存储、删除、URL 隔离、畸形数据、超时和内存回退全部通过。

## T8: 实现本机回环回调服务

**文件：** `src/julycode/mcp/oauth/callback.py`、`tests/test_mcp_oauth_flow.py`
**依赖：** T3
**步骤：**
1. 使用 `asyncio` 仅绑定 `127.0.0.1:0`，生成固定 `/oauth/callback` 路径。
2. 限制请求行、Header 和查询字符串大小，只接受一次合法 GET 回调。
3. 校验 state、授权码和 OAuth error，并返回不含敏感数据的浏览器结果页。
4. 覆盖成功、错误 state、拒绝、重复回调、取消和超时清理。

**验证：** 运行 `pytest -q tests/test_mcp_oauth_flow.py -k "loopback or callback"`，期望监听地址、回调校验和所有清理分支通过，测试结束后端口可再次绑定。

## T9: 实现 OAuth Session 的凭据恢复和状态机

**文件：** `src/julycode/mcp/oauth/client.py`、`tests/test_mcp_oauth_flow.py`
**依赖：** T6、T7、T8
**步骤：**
1. 实现 `McpOAuthSession` 初始化、公开状态快照和状态变更通知。
2. 从 Credential Store 恢复与当前 Server resource/issuer 匹配的凭据。
3. access token 有效时返回 Bearer Header；临近过期时进入刷新路径；无凭据时允许首次无 token challenge。
4. 对状态消息和对象表示执行敏感数据断言。

**验证：** 运行 `pytest -q tests/test_mcp_oauth_flow.py -k "session and (restore or status or authorization_header)"`，期望恢复、过期判断和公开状态测试通过。

## T10: 实现显式浏览器授权编排

**文件：** `src/julycode/mcp/oauth/client.py`、`tests/test_mcp_oauth_flow.py`
**依赖：** T9
**步骤：**
1. 授权锁内执行 challenge 校验、元数据发现、回调启动和 DCR 优先/预注册回退。
2. 先发布授权 URL，再在线程中尝试打开浏览器；浏览器失败只生成提示。
3. 等待并校验回调，交换 token，原子保存凭据并进入 authorized。
4. 处理重复授权、用户拒绝、错误回调、网络异常、取消和 120 秒超时，确保回调关闭且状态可恢复。
5. scope 严格按 challenge、配置、资源元数据顺序选择。

**验证：** 运行 `pytest -q tests/test_mcp_oauth_flow.py -k "authorize or scope_priority or browser"`，期望 DCR、预注册、URL 发布、浏览器失败和授权状态用例通过。

## T11: 实现 token 刷新、轮换和 logout

**文件：** `src/julycode/mcp/oauth/client.py`、`tests/test_mcp_oauth_flow.py`
**依赖：** T10
**步骤：**
1. 用单飞刷新锁合并并发刷新，锁内重新检查当前 token。
2. 实现提前 60 秒刷新、401 后刷新和只重试一次所需的公开接口。
3. 刷新响应缺少新 refresh token 时保留旧值，返回新值时原子轮换。
4. 刷新失败时清除失效 access token并进入 refresh_failed。
5. logout 删除 Keyring/内存凭据、取消授权并恢复 authorization_required。

**验证：** 运行 `pytest -q tests/test_mcp_oauth_flow.py -k "refresh or rotation or concurrent or logout"`，期望单飞、轮换、失败状态和删除测试通过。

## T12: 将 OAuth 认证接入 Streamable HTTP Transport

**文件：** `src/julycode/mcp/transport.py`、`src/julycode/mcp/errors.py`、`tests/test_mcp_transport.py`
**依赖：** T11
**步骤：**
1. 为 HTTP Transport 增加可选 `McpHttpAuthProvider`，异步组装请求 Header。
2. 对 request、notification 和 DELETE 注入 Bearer Header，保持静态 Header 行为不变。
3. 401 时读取 `WWW-Authenticate`；刷新成功重放原请求一次，否则抛出 `McpAuthorizationRequired`。
4. 确保 SSE、JSON、Session Header、协议版本、超时和关闭逻辑保持兼容。
5. 确保错误和测试输出不包含 token。

**验证：** 运行 `pytest -q tests/test_mcp_transport.py`，期望现有 Transport 测试及 OAuth Header、challenge、刷新重试、重试上限和脱敏测试全部通过。

## T13: 为 MCP 工具增加 Server origin

**文件：** `src/julycode/mcp/tools.py`、`tests/test_mcp_tools.py`
**依赖：** T3
**步骤：**
1. 将远端 MCP 工具的 `origin` 设置为 `mcp:<server>`。
2. 保持工具全局名、Schema、安全等级、执行和错误映射不变。
3. 添加按 Server origin 注销不影响内置工具和其他 MCP Server 的测试。

**验证：** 运行 `pytest -q tests/test_mcp_tools.py tests/test_tools.py`，期望 MCP origin 和全部工具回归测试通过。

## T14: 扩展 MCP Manager 的 OAuth 初始化和状态隔离

**文件：** `src/julycode/mcp/manager.py`、`tests/test_mcp_manager.py`
**依赖：** T2、T11、T12、T13
**步骤：**
1. 为每个启用 OAuth 的 HTTP Server 创建独立 OAuth Session，并注入 Transport。
2. 把初始化抽成单 Server 方法，启动前恢复凭据。
3. 捕获 `McpAuthorizationRequired` 并记录 authorization_required，不放入普通失败 Server。
4. 扩展加载报告，返回每个 OAuth Server 的公开状态和 Credential Store warning。
5. 状态变化为 refresh_failed 或 authorization_required 时按 origin 移除该 Server 工具。

**验证：** 运行 `pytest -q tests/test_mcp_manager.py -k "oauth or initializes_servers or failed_server"`，期望未授权隔离、多 Server 状态和原有初始化测试通过。

## T15: 实现 Manager 授权、重初始化与登出

**文件：** `src/julycode/mcp/manager.py`、`tests/test_mcp_manager.py`
**依赖：** T14
**步骤：**
1. 实现 `authorize_server()` 的目标校验和重复授权拒绝。
2. 授权成功后关闭旧会话、重新 initialize、tools/list 并向已附着 Registry 注册该 Server 工具。
3. 实现 `logout_server()`，关闭会话、删除凭据、移除工具并更新状态。
4. Manager close 时取消授权、关闭 OAuth Session 和 MCP Session，但保留持久 token。

**验证：** 运行 `pytest -q tests/test_mcp_manager.py -k "authorize_server or reinitialize or logout or closes"`，期望无需重启注册工具、logout 清理和生命周期测试通过。

## T16: 增加 `/mcp` 命令和 OAuth 状态输出

**文件：** `src/julycode/commands/models.py`、`src/julycode/commands/builtin.py`、`tests/test_commands.py`
**依赖：** T15
**步骤：**
1. 扩展 CommandContext 的授权与登出异步接口，以及公开 MCP OAuth 状态快照。
2. 注册 `/mcp` 本地命令并解析 `auth <server>`、`logout <server>`。
3. 对缺少参数、未知 action、多余参数和 Manager 返回错误提供明确提示。
4. 扩展 `/status`，逐 Server 展示认证状态和内存回退 warning，不展示敏感字段。

**验证：** 运行 `pytest -q tests/test_commands.py -k "mcp or status"`，期望命令解析、帮助、成功、错误和状态格式测试通过。

## T17: 接入 TUI 本地授权交互

**文件：** `src/julycode/tui/app.py`、`tests/test_tui_smoke.py`
**依赖：** T16
**步骤：**
1. 实现 CommandContext 的 `authorize_mcp_server()` 与 `logout_mcp_server()`。
2. 在授权开始时把 URL 和浏览器打开失败提示显示为本地消息，不写入 ChatSession。
3. 授权成功后刷新状态栏和工具注册状态，失败后保持输入区可用。
4. 退出 TUI 时取消进行中的授权并验证回调监听关闭。
5. 添加 TUI 输出、会话隔离、取消和敏感数据不显示测试。

**验证：** 运行 `pytest -q tests/test_tui_smoke.py -k "mcp and oauth"`，期望授权 URL、成功/失败、logout、取消和会话隔离用例通过。

## T18: 建立可控 OAuth/MCP 集成 fixture

**文件：** `tests/fixtures/mcp_oauth_server.py`、`tests/test_mcp_oauth_flow.py`、`tests/test_mcp_manager.py`
**依赖：** T15
**步骤：**
1. 实现可配置的 401 challenge、资源元数据、授权服务器元数据、DCR、token、refresh 和 MCP tools/list/tools/call 行为。
2. 支持成功、用户拒绝、错误 state、DCR 不支持、token 轮换、畸形/超长响应和单 Server 失败模式。
3. 使用依赖注入的 HTTP Client 驱动完整授权到工具注册集成测试，不访问真实 GitHub。
4. 编译 fixture 并确保测试结束后无监听端口或后台任务残留。

**验证：** 运行 `python -m py_compile tests/fixtures/mcp_oauth_server.py && pytest -q tests/test_mcp_oauth_flow.py tests/test_mcp_manager.py -k oauth`，期望完整模拟授权链路通过且测试进程正常退出。

## T19: 更新用户文档和 GitHub 示例

**文件：** `README.md`
**依赖：** T2、T16
**步骤：**
1. 增加通用 HTTP OAuth 配置示例和字段说明。
2. 增加 GitHub Remote MCP 示例、GitHub App/OAuth App 注册前置条件和 `127.0.0.1/oauth/callback` 回调说明。
3. 说明 `/mcp auth`、`/mcp logout`、`/status`、Keyring、内存回退和 PAT 兼容路径。
4. 明确 `2025-06-18`、无 Device Flow、无 SSH 跨机器回调、无远端 revoke 和无自动 scope step-up。

**验证：** 运行 `rg -n "oauth:|/mcp auth|/mcp logout|Keyring|127.0.0.1/oauth/callback|GitHub App|2025-06-18|Device" README.md`，期望全部主题均有命中。

## T20: 执行认证数据泄漏专项检查

**文件：** `tests/test_mcp_oauth_discovery.py`、`tests/test_mcp_oauth_store.py`、`tests/test_mcp_oauth_flow.py`、`tests/test_mcp_transport.py`、`tests/test_mcp_manager.py`、`tests/test_tui_smoke.py`
**依赖：** T17、T18
**步骤：**
1. 使用固定假 token、refresh token、client secret、授权码和 PKCE verifier 贯穿错误分支。
2. 断言异常、状态快照、加载报告、TUI 输出、ChatSession 和模型请求不包含这些值。
3. 断言认证 URL 不进入模型消息，token 不出现在 URL 查询参数。
4. 断言磁盘工作目录和用户临时目录不存在明文 token 文件。

**验证：** 运行 `pytest -q tests/test_mcp_oauth_discovery.py tests/test_mcp_oauth_store.py tests/test_mcp_oauth_flow.py tests/test_mcp_transport.py tests/test_mcp_manager.py tests/test_tui_smoke.py -k "oauth or redacted or secret"`，期望专项测试全部通过。

## T21: 执行 MCP、命令、TUI 与全项目回归

**文件：** 全部受影响文件
**依赖：** T19、T20
**步骤：**
1. 运行 OAuth 专项测试。
2. 运行现有 MCP、配置、命令、权限、Provider、Agent 和 TUI 回归测试。
3. 运行全量 pytest 和 Python 编译检查。
4. 运行 `git diff --check`，确认没有格式错误或意外临时文件。

**验证：** 运行 `pytest -q && python -m compileall -q src tests && git diff --check`，期望全部退出码为 0。

## T22: 使用 tmux 完成端到端验收

**文件：** `tests/fixtures/mcp_oauth_server.py`、`checklist.md`
**依赖：** T21
**步骤：**
1. 在 tmux 中启动可控 OAuth/MCP fixture、mock 模型服务和 JulyCode TUI。
2. 配置一个 OAuth Server、一个静态 MCP Server 和一个失败 Server，确认 JulyCode 启动时不自动打开浏览器。
3. 输入 `/status`，观察 OAuth Server 为需要授权、静态 Server 工具已注册、失败 Server 被隔离。
4. 输入 `/mcp auth oauth_demo`，使用 fixture 完成浏览器回调，观察无需重启即注册 `oauth_demo__echo`。
5. 输入真实对话请求调用 `oauth_demo__echo`，观察工具调用和最终回复。
6. 输入 `/mcp logout oauth_demo`，确认工具移除且状态恢复为需要授权。
7. 对照 `checklist.md` 逐项记录证据并清理 tmux、临时配置、Keyring 测试记录和 fixture 进程。

**验证：** 运行 `tmux capture-pane -p -S -200` 保存验收输出，期望包含需要授权状态、授权 URL、授权成功、`oauth_demo__echo` 调用、最终回复和 logout 后状态，且 `tmux ls` 不再存在测试会话。

## 执行顺序

```text
T1 → T2 → T3
T3 → T4 → T5 → T6
T3 → T7
T3 → T8
T6 + T7 + T8 → T9 → T10 → T11 → T12
T3 → T13
T2 + T11 + T12 + T13 → T14 → T15 → T16 → T17
T15 → T18
T2 + T16 → T19
T17 + T18 → T20
T19 + T20 → T21 → T22
```
