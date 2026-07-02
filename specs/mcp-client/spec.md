# MCP 客户端 Spec

## 背景
MewCode 当前已经具备本地工具系统、工具注册中心、Agent Loop、权限控制和用户级/项目级配置合并能力。工具能力目前主要来自内置本地工具，无法在启动时接入外部 MCP Server 提供的工具。用户希望在配置中声明多个 MCP Server 后，MewCode 能自动发现这些 Server 暴露的工具，并把它们纳入现有工具中心，使 Agent 像使用内置工具一样使用远端工具。

本阶段只接入 MCP 的工具能力。MCP 的资源、提示词、采样等非工具能力，以及 Server 健康检查和自动重连不纳入本阶段。

## 目标
- 用户可以在配置中声明多个 MCP Server，并在启动 MewCode 时自动加载。
- MewCode 可以通过 MCP 标准会话发现外部 Server 提供的工具。
- 发现到的远端工具会被注册为 MewCode 可用工具，Agent 使用时不需要区分本地工具和 MCP 工具。
- MewCode 支持本地子进程 stdio 和远程 Streamable HTTP 两种 MCP Server 连接方式。
- 单个 MCP Server 启动、握手、工具发现或调用失败时，不影响其他 MCP Server 和内置工具继续可用。

## 功能需求
- F1: 用户可以在现有配置体系中声明 MCP Server 列表；该列表必须是以 Server 名为 key 的 map。
- F2: MCP Server 配置必须支持本地子进程 stdio 类型和远程 Streamable HTTP 类型。
- F3: 本地子进程 stdio 类型必须允许用户声明启动命令、命令参数和环境变量。
- F4: 远程 Streamable HTTP 类型必须允许用户声明服务地址和请求头。
- F5: 本地子进程环境变量值、远程 Streamable HTTP 服务地址和请求头值，必须支持完整 `${VAR}` 环境变量展开；缺失或为空时必须给出清晰配置错误。
- F6: 用户级配置和项目级配置都可以声明 MCP Server；两层配置合并时，项目级同名 Server 必须覆盖用户级同名 Server，不同名 Server 必须同时保留。
- F7: MewCode 启动时必须尝试连接所有已启用的 MCP Server，并按 MCP 会话要求完成初始化握手。
- F8: 初始化成功后，MewCode 必须从每个 MCP Server 获取工具列表。
- F9: 每个发现到的远端工具必须被包装成现有工具中心可识别的工具，并向 Agent 暴露名称、说明和参数约束。
- F10: 远端工具对 Agent 暴露的全局工具名必须采用 Server 名前缀，格式为 `server__tool`；该规则必须避免远端工具覆盖内置工具或其他 Server 的工具。
- F11: Agent 调用远端工具时，MewCode 必须把全局工具名还原到对应 Server 和远端工具名，并向对应 Server 发起工具调用。
- F12: MCP 通信必须按 JSON-RPC 2.0 语义收发请求和响应；带 id 的请求必须能和对应响应异步配对。
- F13: MCP 工具会话必须覆盖初始化握手、列出工具和调用工具三个步骤。
- F14: 多个 MCP Server 的连接必须有生命周期管理；同一个 Server 的后续工具调用应复用已建立的连接。
- F15: 单个 MCP Server 连接失败、握手失败、列出工具失败或工具调用失败时，MewCode 必须把失败限制在该 Server 或该次工具调用内，不得导致应用退出、其他 Server 不可用或内置工具不可用。
- F16: 远端工具调用成功时，MewCode 必须把远端返回内容转换为现有工具结果格式并回灌给 Agent。
- F17: 远端工具调用失败、超时、返回错误或返回无法理解的数据时，MewCode 必须返回结构化失败结果，使 Agent 能继续解释或调整。
- F18: 当没有配置 MCP Server 时，MewCode 的启动、内置工具、Agent Loop 和权限行为必须保持现状。
- F19: 用户文档必须说明 MCP Server 配置方式、两种连接类型、环境变量展开、工具命名规则和本阶段不支持的 MCP 能力。

## 非功能需求
- N1: MCP 接入不得降低内置工具的稳定性；外部 Server 异常不得破坏本地工具执行链路。
- N2: MCP Server 连接和工具调用错误必须脱敏，不得泄露配置中的密钥、请求头或环境变量值。
- N3: MCP 工具发现和调用必须有明确超时边界，避免启动或 Agent Loop 无限等待外部 Server。
- N4: 对 Agent 暴露的 MCP 工具描述必须尽量保留远端工具的原始说明和参数约束。
- N5: MCP 工具接入应保持供应商协议无关；OpenAI 和 Anthropic 配置下暴露给模型的工具语义应一致。
- N6: 配置错误必须可诊断，错误信息应指出出错的 Server 和字段类别。

## 不做的事
- 不实现 MCP 资源能力。
- 不实现 MCP 提示词能力。
- 不实现 MCP 采样能力。
- 不实现 Server 健康检查。
- 不实现自动重连。
- 不实现 MCP Server 管理界面。
- 不实现插件市场或在线安装 MCP Server。
- 不改变现有内置工具的行为和权限边界。

## 验收标准
- AC1: 配置一个本地子进程 MCP Server 后，启动 MewCode 能完成连接、初始化和工具发现，并向 Agent 暴露该 Server 的工具。
- AC2: 配置一个远程 Streamable HTTP MCP Server 后，启动 MewCode 能完成连接、初始化和工具发现，并向 Agent 暴露该 Server 的工具。
- AC3: 用户级和项目级同时声明 MCP Server 时，不同名 Server 会同时加载，项目级同名 Server 会覆盖用户级同名 Server。
- AC4: 配置中的 `${VAR}` 在环境变量存在时会被展开；环境变量缺失或为空时启动阶段给出清晰配置错误且不泄露原始密钥。
- AC5: 当两个 MCP Server 提供同名远端工具，或远端工具与内置工具同名时，MewCode 暴露给 Agent 的工具名仍保持唯一，且格式为 `server__tool`。
- AC6: Agent 调用一个已发现的 MCP 工具时，MewCode 会把调用转发到正确 Server 的正确远端工具，并把成功结果回灌给 Agent。
- AC7: MCP JSON-RPC 响应乱序返回时，请求和响应仍按 id 正确配对，工具调用结果不会串到其他请求。
- AC8: 单个 MCP Server 启动失败、握手失败或列出工具失败时，MewCode 仍能启动，内置工具和其他成功连接的 MCP Server 工具仍可用。
- AC9: MCP 工具调用超时、返回错误或返回无法理解的数据时，MewCode 返回结构化失败结果，Agent Loop 不崩溃且用户可以继续输入。
- AC10: 未配置 MCP Server 时，默认启动后仍只包含现有内置工具，普通聊天和内置工具调用行为不回退。
- AC11: OpenAI 和 Anthropic 配置下，MCP 工具都能以统一工具描述暴露给模型。
- AC12: README 或等价用户文档包含 MCP 配置示例、两种连接类型、环境变量展开规则、`server__tool` 命名规则和不支持范围。
