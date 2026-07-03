# MCP OAuth 2.1 Plan

## 架构概览
OAuth 能力作为 Streamable HTTP Transport 的认证层接入，不改变 stdio Transport、MCP JSON-RPC 会话和远端工具包装方式。系统由配置模型、OAuth 协调器、发现与令牌客户端、回环回调服务、安全凭据存储、HTTP Transport 认证适配、MCP Manager 生命周期以及本地命令七部分组成。

每个启用 OAuth 的 MCP Server 拥有独立 `McpOAuthSession`。该对象持有公开元数据、认证状态和并发锁，通过 `OAuthCredentialStore` 读取或保存敏感凭据。`StreamableHttpMcpTransport` 只依赖窄接口 `McpHttpAuthProvider`：请求前获取 Bearer token，遇到 `401` 时尝试一次刷新或记录授权挑战。Transport 不负责打开浏览器，也不直接操作 TUI。

MCP Manager 负责把认证状态与 Server 生命周期连接起来。启动时，未授权 Server 被记录为 `authorization_required`，不会进入普通失败路径；用户执行 `/mcp auth <server>` 后，Manager 调用对应 OAuth Session 完成浏览器授权、重新初始化 MCP 会话并注册工具。`/mcp logout <server>` 会注销本地凭据、关闭会话并移除该 Server 的工具。

```text
配置加载
  → 为 OAuth HTTP Server 创建 McpOAuthSession
  → 从 Keyring/内存读取凭据
  → StreamableHttpMcpTransport 发起 initialize
      ├─ token 可用：正常握手并注册工具
      └─ 401：保存 challenge，状态=authorization_required，应用继续启动

/mcp auth github
  → 获取 Protected Resource Metadata
  → 获取 Authorization Server Metadata
  → 启动 127.0.0.1 随机端口回调
  → DCR；不支持时使用预注册客户端
  → 生成 state + PKCE S256
  → 展示并打开授权 URL
  → 校验回调并交换 token
  → 保存凭据
  → 重新 initialize、tools/list、注册 github__* 工具

后续 MCP 请求
  → 每次携带 Bearer token
  → 到期/401 时加锁刷新并仅重试一次
  → 刷新失败：状态=refresh_failed，停止暴露该 Server 工具
```

## 核心数据结构

### `McpOAuthConfig`

```python
@dataclass(frozen=True)
class McpOAuthConfig:
    enabled: bool = True
    client_id: str | None = None
    client_secret: str | None = None
    scopes: tuple[str, ...] = ()
```

作为 `McpServerConfig.oauth: McpOAuthConfig | None` 的子配置。配置格式为：

```yaml
mcp_servers:
  github:
    type: http
    url: https://api.githubcopilot.com/mcp/
    oauth:
      enabled: true
      client_id: ${GITHUB_MCP_CLIENT_ID}
      client_secret: ${GITHUB_MCP_CLIENT_SECRET}
      scopes: [repo, read:user]
```

`oauth` 缺失表示保持现有静态认证行为。`oauth.enabled: false` 等价于不启用 OAuth，便于用户临时关闭。OAuth 启用时，HTTP Header 名按大小写不敏感规则检查，不允许同时配置 `Authorization`。

### OAuth 运行模型

```python
OAuthState = Literal[
    "disabled",
    "connecting",
    "authorization_required",
    "authorizing",
    "authorized",
    "refresh_failed",
    "error",
]

@dataclass(frozen=True)
class OAuthChallenge:
    resource_metadata_url: str
    scopes: tuple[str, ...]

@dataclass(frozen=True)
class ProtectedResourceMetadata:
    resource: str
    authorization_servers: tuple[str, ...]
    scopes_supported: tuple[str, ...]

@dataclass(frozen=True)
class AuthorizationServerMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    code_challenge_methods_supported: tuple[str, ...]
    token_endpoint_auth_methods_supported: tuple[str, ...]

@dataclass(frozen=True)
class OAuthClientRegistration:
    client_id: str
    client_secret: str | None = field(default=None, repr=False)
    token_endpoint_auth_method: str = "none"
    dynamically_registered: bool = False

@dataclass(frozen=True)
class OAuthTokenSet:
    access_token: str = field(repr=False)
    token_type: str = "Bearer"
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: float | None = None
    scopes: tuple[str, ...] = ()

@dataclass(frozen=True)
class OAuthCredentialBundle:
    resource: str
    issuer: str
    registration: OAuthClientRegistration
    token: OAuthTokenSet

@dataclass(frozen=True)
class McpOAuthStatus:
    server_name: str
    state: OAuthState
    message: str = ""
    persistent_storage: bool = False
```

所有敏感字段关闭 `repr`。状态模型只包含可展示摘要，不包含 token、授权码、客户端 secret、verifier 或原始响应。

### `OAuthCredentialStore`

```python
class OAuthCredentialStore(Protocol):
    @property
    def persistent(self) -> bool: ...
    @property
    def warning(self) -> str | None: ...
    async def load(self, key: str) -> OAuthCredentialBundle | None: ...
    async def save(self, key: str, value: OAuthCredentialBundle) -> None: ...
    async def delete(self, key: str) -> None: ...
```

实现包括：

- `KeyringCredentialStore`：通过系统 Keyring 保存一个经过严格字段校验的 JSON 凭据包。
- `MemoryCredentialStore`：只保存在当前进程内存。
- `FallbackCredentialStore`：优先 Keyring；后端不可用、锁定或操作失败时转入内存，并暴露一次脱敏警告。

Keyring service 固定为 `mewcode.mcp.oauth`。账户 key 由规范化 MCP URL 的 SHA-256 和 Server 名构成，防止 Server URL 变化后误用旧 token。Keyring 同步调用通过线程执行，并设置有限等待时间。

### `McpHttpAuthProvider`

```python
class McpHttpAuthProvider(Protocol):
    async def authorization_header(self) -> str | None: ...
    async def recover_unauthorized(self, www_authenticate: str | None) -> bool: ...
    def status(self) -> McpOAuthStatus: ...
```

`authorization_header()` 返回完整 Bearer Header 值或 `None`。`recover_unauthorized()` 在已有 refresh token 时加锁刷新；无 token 或刷新失败时保存挑战并返回 `False`。同一请求只允许调用一次恢复，避免 401 循环。

### `McpOAuthSession`

```python
class McpOAuthSession(McpHttpAuthProvider):
    async def initialize_credentials(self) -> None: ...
    async def authorization_header(self) -> str | None: ...
    async def recover_unauthorized(self, www_authenticate: str | None) -> bool: ...
    async def authorize(
        self,
        publish_url: Callable[[str], Awaitable[None]],
    ) -> McpOAuthStatus: ...
    async def logout(self) -> McpOAuthStatus: ...
    async def close(self) -> None: ...
    def status(self) -> McpOAuthStatus: ...
```

每个 Session 使用两个锁：授权锁防止重复浏览器流程；刷新锁合并并发刷新。状态变更通过不含敏感数据的回调通知 MCP Manager。

### `LoopbackOAuthCallback`

```python
class LoopbackOAuthCallback:
    async def start(self) -> str: ...
    async def wait(self, expected_state: str, timeout: float = 120.0) -> OAuthCallbackResult: ...
    async def close(self) -> None: ...
```

`start()` 只绑定 `127.0.0.1` 和端口 `0`，返回 `http://127.0.0.1:<port>/oauth/callback`。处理器限制请求行、Header 和查询参数大小，只接受单次 `GET /oauth/callback`。无论成功、拒绝、错误、取消或超时，`finally` 都关闭监听并释放端口。

### Manager 与命令接口

```python
class McpManager:
    async def authorize_server(
        self,
        name: str,
        publish_url: Callable[[str], Awaitable[None]],
    ) -> McpOAuthStatus: ...
    async def logout_server(self, name: str) -> McpOAuthStatus: ...
    def oauth_statuses(self) -> dict[str, McpOAuthStatus]: ...

class CommandContext(Protocol):
    async def authorize_mcp_server(self, name: str) -> str: ...
    async def logout_mcp_server(self, name: str) -> str: ...
```

`McpLoadReport` 增加 `oauth_statuses` 和安全凭据存储 warning。`/status` 只消费公开状态摘要。

## 模块设计

### OAuth 元数据与 HTTP 客户端

**职责：** 解析 Bearer challenge；获取并验证 Protected Resource Metadata 和 Authorization Server Metadata；执行 DCR、授权码交换和 refresh。

**对外接口：**

```python
def parse_oauth_challenge(value: str | None) -> OAuthChallenge: ...
async def discover_oauth_metadata(challenge: OAuthChallenge, resource_url: str) -> OAuthDiscovery: ...
async def register_client(metadata: AuthorizationServerMetadata, redirect_uri: str) -> OAuthClientRegistration: ...
async def exchange_code(... ) -> OAuthTokenSet: ...
async def refresh_token(... ) -> OAuthTokenSet: ...
```

**依赖：** `httpx`、标准 URL/JSON/加密随机数库。

元数据请求采用流式读取并限制为 1 MiB；超限、重定向、非 JSON、字段类型错误和 issuer/resource 不匹配均拒绝。受保护资源元数据、issuer、授权端点、token 端点和注册端点必须是无 userinfo、无 fragment 的 HTTPS URL。授权服务器列表存在多个值时，首版选择第一个通过验证的 HTTPS issuer。

Authorization Server Metadata 必须声明 `S256`。令牌端点客户端认证根据元数据或 DCR 结果选择 `none`、`client_secret_post` 或 `client_secret_basic`，不支持的方法会在打开浏览器前失败。

scope 选择顺序固定为 challenge → 用户配置 → Protected Resource Metadata，并去重保持顺序。授权和 token 请求都携带 RFC 8707 `resource`。

### OAuth 安全凭据存储

**职责：** 安全持久化凭据、内存回退、序列化校验和存储告警。

**对外接口：** `OAuthCredentialStore`。

**依赖：** `keyring`、OAuth 运行模型。

反序列化采用白名单字段和严格类型校验。Keyring 返回畸形数据时删除该记录并按未授权处理，不把原文写入错误。保存时内存始终保留当前副本；Keyring 失败后切换为非持久模式。

### 回环授权服务

**职责：** 生成回调 URI、接收浏览器回调、校验状态、返回不含敏感数据的浏览器结果页。

**对外接口：** `LoopbackOAuthCallback`。

**依赖：** `asyncio` 标准库。

授权流程先绑定随机端口，再进行 DCR 或选择预注册客户端，确保注册和授权使用同一 redirect URI。GitHub OAuth App 可将预注册回调设置为 `http://127.0.0.1/oauth/callback`；GitHub 对回环地址允许运行时端口不同。

### OAuth Session 协调器

**职责：** 管理单个 Server 的凭据、状态、发现结果、授权、刷新、logout 和并发。

**对外接口：** `McpOAuthSession`、`McpHttpAuthProvider`。

**依赖：** 配置、OAuth HTTP 客户端、回调服务、凭据存储、浏览器打开器。

启动阶段先加载凭据。若 access token 在 60 秒安全窗口外仍有效则直接使用；已过期且有 refresh token 时预刷新；否则允许 Transport 发起无 token 请求以获取标准 challenge。

`authorize()` 要求已有合法 challenge。它在任何网络请求前进入 `authorizing`，在 `finally` 清理回调；成功时原子保存凭据并进入 `authorized`。系统先把授权 URL传给 TUI，再在线程中调用浏览器打开器；打开失败只产生提示，不终止授权。

动态注册优先。存在 `registration_endpoint` 时尝试 DCR；DCR 不可用、明确拒绝或响应不合法时，若配置了预注册 client ID 则回退并记录非敏感提示。DCR 返回的客户端 secret 和 token endpoint 认证方式随 token 一起进入安全凭据存储。

刷新采用 60 秒过期裕量和单飞锁。锁内重新读取最新 token，避免多个并发工具请求重复刷新。刷新成功时保留服务端未轮换的旧 refresh token，或原子替换新 refresh token；失败后清除失效 access token，状态变为 `refresh_failed`，等待显式重新授权。

### Streamable HTTP Transport 认证适配

**职责：** 给每个 MCP HTTP 请求附加 OAuth Header，捕获认证挑战，刷新后重试一次。

**对外接口：** 保持现有 `McpTransport` 接口；构造函数新增可选 `auth_provider`。

**依赖：** `McpHttpAuthProvider`、现有 SSE/JSON 解析。

请求 Header 组装改为异步步骤：先复制现有静态 Header，再加入 Content-Type、Accept、Session 和协议版本 Header，最后加入 OAuth Authorization。配置解析已保证 OAuth 不会覆盖静态 Authorization。

POST request 和 notification 共用认证发送器。收到 `401` 时读取 `WWW-Authenticate`，调用 `recover_unauthorized()`；刷新成功则重放原请求一次，失败则抛出携带公开 challenge 的 `McpAuthorizationRequired`。DELETE 关闭请求携带当前 token，但不因关闭失败触发登录或刷新。

### MCP Manager 生命周期

**职责：** 创建 OAuth Session，隔离认证状态，初始化/重初始化 Server，注册/移除工具，向命令层提供授权操作。

**对外接口：** 扩展 `McpManager`。

**依赖：** MCP Client、Transport、OAuth Session、ToolRegistry。

初始化逻辑抽成单 Server 方法。捕获 `McpAuthorizationRequired` 时不写入普通 `failed_servers`，而是保留 challenge 并更新认证状态。其他 Server 继续初始化。

Manager 在首次 `register_tools()` 时保存 Registry 引用。MCP 工具的 `origin` 改为 `mcp:<server>`；授权成功重初始化前先关闭旧会话并移除该 origin，成功后只注册该 Server 新发现的工具。logout 或刷新失败同样移除对应 origin，避免向模型继续暴露不可用工具。

Manager 关闭时取消所有进行中的授权、关闭回调服务、关闭 MCP Session 和 OAuth HTTP Client，但不删除持久化 token。

### 本地命令与 TUI

**职责：** 提供 `/mcp auth <server>`、`/mcp logout <server>` 和状态展示，不把 OAuth 内容写入模型会话。

**对外接口：** `CommandContext` 新增两个异步方法；内置命令注册 `/mcp`。

**依赖：** MCP Manager、现有命令系统。

`/mcp` 只接受以下形式：

```text
/mcp auth <server>
/mcp logout <server>
```

授权 URL 通过 TUI 本地消息展示，不追加到 `ChatSession`。命令 Handler 只处理参数和显示结果，OAuth 业务逻辑留在 Manager/Session。`/status` 为每个 OAuth Server 输出状态和非敏感说明，并展示 Keyring 不可用时的内存回退警告。

### 配置与文档

**职责：** 解析 OAuth 配置、环境变量、冲突校验，维护用户文档和依赖清单。

**对外接口：** `McpOAuthConfig`。

**依赖：** 现有 YAML 合并和环境变量展开逻辑。

OAuth 仅允许出现在 `type: http` Server。`client_id` 和 `client_secret` 使用现有环境变量展开与脱敏机制。`scopes` 必须是非空字符串数组，重复项去重。启用 OAuth 且存在大小写任意形式的 `Authorization` Header 时拒绝配置。

运行时依赖新增有界版本的 `keyring`，并同步维护打包依赖与 `requirements.txt`。

## 模块交互

### 首次启动与授权

```text
MewCodeApp.on_mount
  → McpManager.initialize
    → McpOAuthSession.initialize_credentials
      → OAuthCredentialStore.load
    → McpClientSession.initialize
      → StreamableHttpMcpTransport.request
        → McpOAuthSession.authorization_header
        → MCP Server: 401 + WWW-Authenticate
        → McpOAuthSession.recover_unauthorized
        → raise McpAuthorizationRequired
    → Manager 标记 authorization_required，继续其他 Server

用户输入 /mcp auth github
  → CommandContext.authorize_mcp_server
    → McpManager.authorize_server
      → McpOAuthSession.authorize
        → 元数据发现 → 回调监听 → DCR/预注册 → PKCE → 浏览器
        → 回调 → token exchange → CredentialStore.save
      → Manager 重新初始化 github
      → tools/list → register github__*
    → TUI 显示成功结果
```

### Token 刷新

```text
Transport 请求前
  → OAuth Session 检查 expires_at
  → 接近过期：refresh lock → token endpoint → 保存轮换 token
  → 携带新 token 请求 MCP

Transport 收到 401
  → 若本请求未重试：refresh lock → 刷新
      ├─ 成功：原请求重试一次
      └─ 失败：状态 refresh_failed → Manager 移除工具 → 返回结构化认证错误
```

## 文件组织

```text
project/
├── pyproject.toml                         — 增加 Keyring 运行时依赖
├── requirements.txt                      — 同步 Keyring 依赖
├── README.md                             — OAuth 与 GitHub Remote MCP 使用说明
├── src/mewcode/config.py                 — OAuth 配置模型、解析和冲突校验
├── src/mewcode/commands/models.py        — MCP 命令上下文接口和状态快照
├── src/mewcode/commands/builtin.py       — /mcp 命令与 /status OAuth 展示
├── src/mewcode/tui/app.py                — 命令上下文实现和授权 URL 本地展示
├── src/mewcode/mcp/errors.py             — 认证挑战与 OAuth 结构化错误
├── src/mewcode/mcp/transport.py          — Bearer 注入、401 恢复与单次重试
├── src/mewcode/mcp/tools.py              — MCP 工具 origin 标识
├── src/mewcode/mcp/manager.py            — OAuth Session 生命周期与重初始化
├── src/mewcode/mcp/oauth/
│   ├── __init__.py                       — OAuth 对外接口
│   ├── models.py                         — 公开元数据、token 和状态模型
│   ├── discovery.py                      — Challenge、RFC 9728/RFC 8414 发现与校验
│   ├── client.py                         — DCR、token exchange、refresh 和协调器
│   ├── callback.py                       — 127.0.0.1 临时回调服务
│   └── store.py                          — Keyring 与内存凭据存储
├── tests/fixtures/mcp_oauth_server.py    — OAuth + MCP 行为模拟 fixture
├── tests/test_config.py                  — OAuth 配置与脱敏测试
├── tests/test_mcp_oauth_discovery.py     — Challenge 和元数据发现测试
├── tests/test_mcp_oauth_store.py         — Keyring/内存回退测试
├── tests/test_mcp_oauth_flow.py          — PKCE、DCR、回调、交换与刷新测试
├── tests/test_mcp_transport.py           — Authorization Header、401 和重试测试
├── tests/test_mcp_manager.py             — 状态隔离、重初始化、logout 测试
├── tests/test_commands.py                — /mcp 与 /status 测试
└── tests/test_tui_smoke.py               — TUI 本地授权交互与回归测试
```

## 需求归属

| 需求 | 架构归属 |
|------|----------|
| F1、F2、F3 | 配置与文档 |
| F4、F5 | MCP Manager、本地命令与 TUI |
| F6、F7、F8、F9、F19 | OAuth 元数据与 HTTP 客户端、OAuth Session |
| F10、F11 | 回环授权服务、OAuth Session |
| F12 | OAuth Session、MCP Manager |
| F13、F14 | Streamable HTTP Transport、OAuth Session |
| F15、F16 | 安全凭据存储、MCP Manager、本地命令 |
| F17、F18 | MCP Manager、状态模型、本地命令 |
| F20 | 全部 OAuth 网络模块与生命周期 |
| F21 | 配置与文档 |
| F22 | Transport 适配边界与回归测试 |

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| OAuth 版本 | MCP `2025-06-18` + OAuth 2.1 Authorization Code | 与项目当前协议一致，避免把认证功能和基础协议升级耦合 |
| 客户端注册 | DCR 优先，预注册 client 回退 | 满足通用 MCP，同时兼容 GitHub 不提供 DCR 的场景 |
| PKCE | 强制 S256，元数据未声明则拒绝 | 满足 MCP 安全要求，防止授权码截获 |
| 回调地址 | `127.0.0.1` 随机端口固定路径 | 避免端口冲突；GitHub 官方支持回环地址动态端口 |
| 登录触发 | 401 后标记状态，用户显式 `/mcp auth` | 不在启动时意外打开多个浏览器，失败可恢复 |
| Token 存储 | 系统 Keyring，失败时仅内存回退 | 不产生明文 token 文件，同时兼顾无 Keyring 环境可用性 |
| Token 身份绑定 | Server 名 + MCP URL 哈希 | 防止配置 URL 变化后把 token 发送给错误资源 |
| 刷新并发 | 单 Server 单飞锁，重试一次 | 防止刷新风暴和无限 401 循环 |
| Transport 集成 | 可选认证 Provider | 保持现有 stdio、PAT 和无 OAuth HTTP 路径不变 |
| 工具注销 | 使用 `origin=mcp:<server>` 按 Server 移除 | logout/刷新失败时停止向模型暴露不可用工具，不影响其他 Server |
| 状态展示 | 公开摘要模型，不暴露原始响应 | 满足可诊断性，同时隔离敏感认证数据 |
| HTTP 安全 | HTTPS、禁止重定向、1 MiB 响应上限 | 降低令牌泄漏、SSRF 跳转和资源耗尽风险 |
| 测试策略 | 注入 HTTP/Keyring/浏览器依赖并使用本地 fixture | 无需真实 GitHub 凭据即可覆盖成功与失败路径 |
| 新协议能力 | 不实现 CIMD、OIDC Discovery、Device Flow | 遵守批准范围，控制单次实现复杂度 |
