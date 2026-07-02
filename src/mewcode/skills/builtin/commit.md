---
name: commit
description: 整理本次改动并准备提交说明，必要时运行检查命令。
tools:
  - read_file
  - find_files
  - search_code
  - run_command
mode: shared
history: 0
---
你正在执行内置 commit Skill。

用户传入参数：
{{input}}

SOP：
1. 先查看当前工作区状态和相关改动，确认哪些文件属于本次提交范围。
2. 识别用户是否要求只生成提交信息、执行检查，或实际运行提交命令。
3. 未经用户明确要求，不要自动创建 git commit。
4. 如需给出提交信息，使用简洁明确的中文或项目既有风格，说明主要行为变化和验证情况。
5. 如执行检查命令，报告命令、结果和任何失败原因。
