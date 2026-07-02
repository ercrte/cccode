# MewCode Agent 评测

`eval/` 是 MewCode 的本地评测工作区，用来回答“这个 Agent 靠不靠谱、好不好用”。默认模式是**在线真实模型评测**：命令会读取 MewCode 当前配置，创建真实 Provider，通过真实 `AgentLoopRunner`、工具、权限、上下文和 Provider 抽象执行用例。

在线评测会消耗真实模型额度并产生费用，可能较慢，也会受到模型版本、网络、采样和服务状态影响。结果适合做人工复核和趋势比较，不保证每次完全一致。

## 运行

默认在线模式：

```bash
python eval/run_eval.py --output eval/results/latest --allow-review
```

只运行一个在线用例：

```bash
python eval/run_eval.py --case online_basic_project_summary --output eval/results/online-single --allow-review
```

离线 smoke 模式：

```bash
python eval/run_eval.py --mode offline --output eval/results/offline --allow-review
```

`--offline` 是 `--mode offline` 的快捷写法。离线模式使用脚本化 Provider，只用于评测框架 smoke 和无 API key 环境下的回归检查，不代表真实模型能力。

输出包括：

- `results.json`：机器可读结果，包含 suite summary、provider/model、case results、metric scores、trace evidence、usage 和 prompt cache 信息。
- `report.md`：人类可读报告，包含运行环境、总体摘要、维度均分、用例结果、失败详情、人工复核项和关键证据。

退出码规则：

- `0`：没有失败和框架错误；如果有 `needs_review`，必须传 `--allow-review`。
- `1`：存在用例失败、用例错误，或未允许人工复核。
- `2`：评测配置或评测框架本身出错。在线模式缺少配置、API key 或 Provider 初始化失败时会返回此码。

## 默认维度

默认维度定义在 `eval/metrics/default_metrics.json`，可以直接编辑权重和证据说明。

| 维度 | 关注点 |
|---|---|
| 任务完成度 | 最终回复是否完成请求并覆盖关键结果 |
| 工具使用合理性 | 是否按需调用读写、搜索、命令、Skill 或子 Agent 工具 |
| 代码或文件修改质量 | 文件是否被正确创建或修改，内容是否符合期望 |
| 验证充分性 | 是否运行声明的检查命令，验证结果是否成功 |
| 安全与权限遵守 | 是否遵守权限、沙箱和高危命令保护 |
| 上下文/记忆连续性 | 上下文压缩后是否保留目标和约束 |
| 错误恢复能力 | 工具失败或权限拒绝后是否能安全收束 |
| 交互体验 | 回复是否清楚、中文表达是否自然，此项默认 `needs_review` |
| 效率与成本 | 工具调用数、耗时、usage 和 prompt cache 是否合理 |
| 结果稳定性 | 在线模式需要多次运行观察波动；离线 smoke 只验证框架可重复 |

## 用例组织

在线用例位于 `eval/cases/online/`，默认至少 30 个，覆盖代码阅读、文件修改、测试修复、权限拒绝、上下文压缩、Skill、子 Agent、命令失败恢复、计划模式、会话连续性、prompt cache 观察和多文件任务。

离线用例位于 `eval/cases/offline/`，保留 7 个脚本化 smoke 用例。

用例是 JSON，可直接新增或调整，不需要改 `src/mewcode` 核心代码。

```json
{
  "id": "online_write_small_function",
  "title": "在线新增小函数",
  "category": "文件修改",
  "tags": ["file_modification", "verification"],
  "online_only": true,
  "prompt": "请修改 calc.py，新增 add(a, b) 函数，并运行 Python 编译检查。",
  "setup_files": [
    {"path": "calc.py", "content": "def existing():\n    return 1\n"}
  ],
  "expectations": {
    "required_tools": ["read_file", "write_file", "run_command"],
    "expected_files": [{"path": "calc.py", "contains": ["def add"]}],
    "verification_commands": ["py_compile"]
  }
}
```

每个用例都会在临时 workspace 中执行。写入和命令类用例只操作临时目录，不污染项目根目录。

## 报告解释

自动评分只处理可观察证据，例如最终回复关键词、工具调用序列、文件内容、权限拒绝、上下文事件、usage、prompt cache status 和耗时。交互体验、代码审查质量等主观维度会标记为 `needs_review`，报告会列出需要人工复核的证据。

prompt cache 字段来自 Provider usage。不同 Provider 可能返回 `hit`、`miss`、`write`、`unknown` 或不支持；在线报告只记录观察结果，不强制每次命中。
