# MewCode Structured System Prompt Manual Scenarios

## 使用方式
1. 在 tmux 中启动 mock 服务：`python tests/e2e_mock_openai_server.py 18765`。
2. 配置 MewCode 使用 OpenAI 协议并指向 mock 服务。
3. 在另一个 tmux pane 中启动 `mewcode`。
4. 按下面场景输入请求，观察工具状态、请求 payload、状态栏和最终回复。

## 场景 1：工具选择
**操作：** 输入“查找 README 里关于 Plan Mode 的说明并总结”。  
**观察：** 模型优先调用 `find_files`、`search_code` 或 `read_file`，而不是直接凭空回答。  
**通过标准：** 界面出现读类工具状态，最终回复引用工具结果或文件内容。

## 场景 2：编辑前读取
**操作：** 输入“把 README 里的 Plan Mode 小节补一句缓存观测说明”。  
**观察：** 出现编辑或写入工具前，先出现 `read_file` 或 `search_code`。  
**通过标准：** 修改基于当前文件内容，文件变化符合请求，且没有未经读取直接编辑的行为。

## 场景 3：Plan Mode 只读约束
**操作：** 输入 `/plan 给这个项目加一个简单文件总结功能`。  
**观察：** 规划阶段只调用读类工具；如果模型请求写入、修改或命令工具，系统返回受限失败而不执行。  
**通过标准：** 最终展示计划并保存待执行计划，规划阶段没有实际文件修改或命令执行。

## 场景 4：动态环境注入
**操作：** 触发普通请求、`/plan` 请求和 `/do` 请求后，查看 mock 服务收到的请求 payload。  
**观察：** payload 中存在 `<mewcode_runtime_context>`，包含 cwd、模式状态和轮次信息。  
**通过标准：** 运行时标签在系统级补充消息中可见；普通 user 消息只包含用户请求，不包含 cwd、模式规则或待执行计划正文。

## 场景 5：缓存观测
**操作：** 连续输入两个相似请求，例如两次询问 README 相关内容。  
**观察：** 状态栏或 usage 事件显示 Cache 状态。mock 服务会返回 `cached_tokens`，真实服务可能显示 hit、write、miss 或 unknown。  
**通过标准：** Cache 状态可观察，字段缺失时显示 unknown 且对话不中断。

## 场景 6：Provider 降级与错误恢复
**操作：** 使用缺少缓存字段的 mock 响应或触发 Provider 错误场景。  
**观察：** 缓存字段缺失时显示 Cache unknown；Provider 错误时显示脱敏错误。  
**通过标准：** TUI 不崩溃，输入区恢复可用，后续普通请求仍可继续。
