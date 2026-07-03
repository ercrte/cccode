# MCP OAuth 2.1 Spec

## 背景
MewCode 已支持通过 Streamable HTTP 连接远程 MCP Server，也支持在配置中注入静态请求头。当前客户端遇到需要 OAuth 的远程 Server 时，只会把首次 `401 Unauthorized` 视为连接失败，不能发现授权服务器、引导用户登录、获取或刷新令牌。GitHub Remote MCP 等服务因此只能依赖用户手工配置 PAT，不能使用标准 OAuth 授权。

本阶段在现有 MCP `2025-06-18` 协议范围内补齐通用 OAuth 2.1 授权能力，遵循该版本要求的受保护资源元数据、授权服务器元数据、Authorization Code、PKCE、动态客户端注册和资源指示语义。OAuth 仅适用于 Streamable HTTP，stdio 的凭据行为保持不变。

参考规范：
- MCP `2025-06-18` Authorization
- OAuth 2.0 Protected Resource Metadata（RFC 9728）
- OAuth 2.0 Authorization Server Metadata（RFC 8414）
- OAuth 2.0 Dynamic Client Registration（RFC 7591）
- OAuth 2.0 Resource Indicators（RFC 8707）

## 目标
- 用户可以为通用远程 MCP Server 完成 OAuth 2.1 浏览器授权，不再需要手工生成长期 PAT。
- 未授权、授权中、已授权、令牌刷新失败等状态都可观察、可恢复，且单个 Server 的认证问题不影响其他能力。
- OAuth access token 和 refresh token 不以明文文件形式持久化。
- GitHub Remote MCP 可通过用户自行注册的 GitHub App 或 OAuth App 完成授权。
- 已有静态请求头、PAT、stdio MCP 和无 MCP 配置保持兼容。

## 功能需求
- F1: Streamable HTTP MCP Server 必须可以声明使用 OAuth；未声明 OAuth 的 HTTP Server 继续沿用现有静态请求头行为。
- F2: OAuth Server 配置必须支持可选的预注册客户端标识、客户端凭据和请求 scope；敏感配置必须支持环境变量引用。
- F3: 同一个 Server 不得同时使用静态 `Authorization` 请求头和 OAuth，冲突时必须在启动阶段给出明确且脱敏的配置错误。
- F4: 使用 OAuth 的 Server 在启动连接时必须优先复用尚可用的令牌；没有令牌或远端返回 `401` 时，必须进入“需要授权”状态，不自动打开浏览器，也不得阻止 MewCode 正常启动。
- F5: 用户必须能够通过 `/mcp auth <server>` 显式启动指定 Server 的授权；未知 Server、非 HTTP Server、未启用 OAuth 的 Server 和重复进行中的授权必须返回明确提示。
- F6: 客户端必须从 `401` 响应的 `WWW-Authenticate` 中发现受保护资源元数据地址，并按 RFC 9728 获取资源标识、授权服务器和 scope 信息。
- F7: 客户端必须按 RFC 8414 获取授权服务器元数据，并验证授权端点、令牌端点、PKCE 能力以及必要的授权能力；不满足安全要求时必须拒绝继续授权。
- F8: 客户端注册必须优先尝试 RFC 7591 动态客户端注册；授权服务器不支持动态注册时，必须回退到用户为该 Server 配置的预注册客户端信息；两者都不可用时必须给出可执行的配置提示。
- F9: 用户授权必须使用 Authorization Code Flow 和 PKCE S256，并使用高熵 `state` 防止请求伪造。
- F10: 授权时必须在 `127.0.0.1` 随机可用端口建立临时回调地址，尝试打开系统浏览器，同时向用户显示可手动访问的授权 URL；等待时间最多 120 秒，完成、失败、取消或超时后必须关闭回调监听。
- F11: 回调处理必须拒绝错误 `state`、缺少授权码、远端授权错误和重复回调；错误信息必须可诊断且不得包含授权码、令牌或客户端凭据。
- F12: 授权请求和令牌请求必须携带目标 MCP 资源标识；令牌交换成功后必须立即重试 MCP 初始化并注册远端工具，无需重启 MewCode。
- F13: access token 必须附加到该 MCP Server 的每个 HTTP 请求中，包括初始化、通知、工具请求和会话关闭请求；令牌不得放入 URL 查询参数。
- F14: 有 refresh token 时，客户端必须在 access token 过期前或收到令牌失效的 `401` 后尝试刷新；服务端返回新的 refresh token 时必须替换旧值；刷新失败后必须回到“需要授权”状态。
- F15: access token、refresh token 和动态注册得到的敏感客户端凭据必须优先持久化到操作系统安全凭据存储；安全凭据存储不可用时只能保存在当前进程内存，并向用户明确提示重启后需要重新授权。
- F16: 用户必须能够通过 `/mcp logout <server>` 删除该 Server 在安全凭据存储和内存中的令牌，关闭现有 MCP 会话并将其恢复为“需要授权”状态；本阶段 logout 只清理本地凭据。
- F17: `/status` 必须展示每个 OAuth MCP Server 的认证状态摘要，并区分需要授权、授权中、已授权、刷新失败和认证错误。
- F18: 多个 OAuth MCP Server 的元数据、客户端注册信息、令牌和授权状态必须按 Server 隔离；一个 Server 授权失败不得影响其他 MCP Server、内置工具或普通对话。
- F19: scope 选择必须优先采用初始认证挑战明确要求的 scope；挑战未给出时使用用户配置的 scope；仍未给出时使用受保护资源元数据声明的基础 scope。
- F20: 所有 OAuth 元数据获取、动态注册、令牌交换和刷新请求必须有有限超时；用户退出 MewCode 时，进行中的授权和回调监听必须被取消并清理。
- F21: 用户文档必须说明 OAuth 配置、预注册客户端回退、授权与登出命令、安全凭据存储、内存回退、GitHub App/OAuth App 前置条件和不支持范围。
- F22: 现有 PAT Header 配置、非 OAuth HTTP MCP、stdio MCP、MCP 工具命名、权限检查和 Agent 工具执行行为必须保持不变。

## 非功能需求
- N1: OAuth 实现必须遵循最小权限原则，不主动扩大认证挑战或用户配置之外的 scope。
- N2: 授权码、access token、refresh token、客户端凭据、PKCE verifier 和完整认证响应不得出现在日志、异常、TUI 消息、会话历史或模型上下文中。
- N3: 授权服务器和令牌端点必须使用 HTTPS；只有本机回环 redirect URI 可以使用 HTTP。
- N4: 回调服务只能监听 `127.0.0.1`，不得监听全部网卡。
- N5: OAuth 网络失败、浏览器失败、用户拒绝、超时、凭据存储失败和刷新失败必须转换为用户可理解的状态，不得导致应用崩溃。
- N6: OAuth 行为必须可用本地模拟 Server 完整测试，不得要求测试环境访问真实 GitHub 或持有真实凭据。
- N7: OAuth Server 的错误响应和元数据必须设置大小边界，避免无限响应或异常数据消耗内存。
- N8: 新增依赖必须限定版本范围，不得提高项目现有最低运行时版本要求。

## 不做的事
- 不升级 MCP 基础协议版本，继续使用 `2025-06-18`。
- 不实现 MCP `2025-11-25` 新增的 Client ID Metadata Documents 和相关 OIDC Discovery 扩展。
- 不实现 Device Authorization Grant。
- 不支持 SSH 跨机器回调、远程回调中继或用户手工粘贴回调 URL。
- 不为 MewCode 内置或分发 GitHub App、GitHub OAuth App 的客户端凭据。
- 不实现 Client Credentials Grant。
- 不为 stdio MCP 引入 OAuth。
- 不实现远端 token revocation；logout 仅删除本地令牌。
- 不实现运行期 `403 insufficient_scope` 的自动增量授权；首版由用户重新授权并调整 scope 配置。
- 不实现多账号切换、账号选择界面或图形化 MCP 管理页面。
- 不改变 MCP 工具当前的全量发现和暴露策略。

## 验收标准
- AC1: 未启用 OAuth 的 HTTP MCP、PAT Header MCP 和 stdio MCP 的配置与连接行为保持原样。（覆盖 F1、F22）
- AC2: OAuth 配置能加载预注册客户端信息和 scope；静态 `Authorization` 与 OAuth 冲突、缺失环境变量时产生明确脱敏错误。（覆盖 F2、F3）
- AC3: OAuth Server 没有可用令牌时，MewCode 正常进入 TUI，该 Server 显示需要授权，其他 Server 和内置工具仍可使用，且不会自动打开浏览器。（覆盖 F4、F18）
- AC4: `/mcp auth <server>` 对有效目标启动授权，对未知、非 HTTP、未启用 OAuth和重复授权目标给出准确提示。（覆盖 F5）
- AC5: 模拟 MCP Server 返回 `401` 后，客户端能解析 `WWW-Authenticate`，获取受保护资源元数据和授权服务器元数据，并拒绝非 HTTPS端点或不支持 PKCE S256 的授权服务器。（覆盖 F6、F7、N3）
- AC6: 支持 DCR 的模拟授权服务器会被自动注册；不支持 DCR 时使用预注册客户端；两种方式都不可用时提示用户配置客户端信息。（覆盖 F8）
- AC7: 授权 URL 包含 PKCE S256、不可预测的 `state`、正确 redirect URI、scope 和目标资源；回调仅监听 `127.0.0.1` 随机端口。（覆盖 F9、F10、F19、N4）
- AC8: 浏览器无法自动打开时用户仍能复制授权 URL；授权成功、拒绝、错误 state 和 120 秒超时均产生明确结果，且回调监听被关闭。（覆盖 F10、F11）
- AC9: 授权码交换成功后，access token 通过 Header 用于 MCP 初始化和后续全部 HTTP 请求，工具无需重启即可注册并调用。（覆盖 F12、F13）
- AC10: access token 到期或远端返回令牌失效 `401` 时会执行一次刷新；刷新成功继续原请求并处理 refresh token 轮换，刷新失败则回到需要授权状态且不无限重试。（覆盖 F14）
- AC11: 安全凭据存储可用时令牌可跨进程复用；不可用时令牌只存在内存、界面给出警告，磁盘中不存在明文 token 文件。（覆盖 F15）
- AC12: `/mcp logout <server>` 清除本地令牌、关闭会话并更新状态，再次使用时要求重新授权。（覆盖 F16）
- AC13: `/status` 能观察每个 OAuth Server 的认证状态；多个 Server 并存时认证信息和失败互不串扰。（覆盖 F17、F18）
- AC14: OAuth 所有网络阶段都有超时，退出应用会取消在途授权；超长或畸形元数据被安全拒绝。（覆盖 F20、N5、N7）
- AC15: 自动化测试输出、错误信息、TUI、会话和模型请求均不包含令牌、授权码、客户端凭据或 PKCE verifier。（覆盖 N2、N6）
- AC16: 用户文档包含通用 OAuth 和 GitHub Remote MCP 示例、命令、安全策略、前置条件和明确的不支持范围。（覆盖 F21）
