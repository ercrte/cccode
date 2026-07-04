#!/usr/bin/env python3
"""估算 GitHub MCP 工具定义的 token 占用"""
import json

# === 采集实际工具的描述和 schema 样本 ===

samples = {
    "get_me": {
        "desc": "MCP Server `github` 的远端工具 `get_me`。\nGet details of the authenticated GitHub user. Use this when a request is about the user's own profile for GitHub. Or when information is missing to build other tool calls.",
        "schema": {"type": "object", "properties": {}, "required": []}
    },
    "add_issue_comment": {
        "desc": "MCP Server `github` 的远端工具 `add_issue_comment`。\nAdd a comment and/or reaction to a specific issue or issue comment in a GitHub repository. Use this tool with pull requests as well (in this case pass pull request number as issue_number), but only if user is not asking specifically to add or react to review comments. At least one of body or reaction is required.",
        "schema": {
            "type": "object",
            "properties": {
                "body": {"type": "string", "description": "Comment content. Required unless reaction is provided."},
                "comment_id": {"type": "integer", "description": "The numeric ID of the issue or pull request comment to react to.", "minimum": 1},
                "issue_number": {"type": "integer", "description": "Issue or pull request number to comment on or react to."},
                "owner": {"type": "string", "description": "Repository owner", "x-mcp-header": "owner"},
                "reaction": {"type": "string", "description": "Emoji reaction to add.", "enum": ["+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes"]},
                "repo": {"type": "string", "description": "Repository name", "x-mcp-header": "repo"}
            },
            "required": ["owner", "repo", "issue_number"]
        }
    },
    "create_or_update_file": {
        "desc": "MCP Server `github` 的远端工具 `create_or_update_file`。\nCreate or update a single file in a GitHub repository. \nIf updating, you should provide the SHA of the file you want to update. Use this tool to create or update a file in a GitHub repository remotely; do not use it for local file operations.\n\nIn order to obtain the SHA of original file version before updating, use the following git command:\ngit rev-parse <branch>:<path to file>\n\nSHA MUST be provided for existing file updates.",
        "schema": {
            "type": "object",
            "properties": {
                "branch": {"type": "string", "description": "Branch to create/update the file in"},
                "content": {"type": "string", "description": "Content of the file"},
                "message": {"type": "string", "description": "Commit message"},
                "owner": {"type": "string", "description": "Repository owner (username or organization)", "x-mcp-header": "owner"},
                "path": {"type": "string", "description": "Path where to create/update the file"},
                "repo": {"type": "string", "description": "Repository name", "x-mcp-header": "repo"},
                "sha": {"type": "string", "description": "The blob SHA of the file being replaced. Required if the file already exists."}
            },
            "required": ["owner", "repo", "path", "content", "message", "branch"]
        }
    },
    "search_code": {
        "desc": "MCP Server `github` 的远端工具 `search_code`。\nFast and precise code search across ALL GitHub repositories using GitHub's native search engine. Best for finding exact symbols, functions, classes, or specific code patterns.",
        "schema": {
            "type": "object",
            "properties": {
                "order": {"type": "string", "description": "Sort order for results", "enum": ["asc", "desc"]},
                "page": {"type": "integer", "description": "Page number for pagination (min 1)", "minimum": 1},
                "perPage": {"type": "integer", "description": "Results per page for pagination (min 1, max 100)", "minimum": 1, "maximum": 100},
                "query": {"type": "string", "description": "Search query (GitHub code search REST). Implicit AND between terms; supports `OR`, `NOT`, and `\"quoted phrase\"` for exact match. Qualifiers: `repo:owner/repo`, `org:`, `user:`, `language:`, `path:dir` (prefix match), `filename:exact.ext`, `extension:`, `in:file`, `in:path`, `size:`, `is:archived`, `is:fork`. Max 256 chars."},
                "sort": {"type": "string", "description": "Sort field ('indexed' only)"}
            },
            "required": ["query"]
        }
    },
    "pull_request_read": {
        "desc": "MCP Server `github` 的远端工具 `pull_request_read`。\nGet information on a specific pull request in GitHub repository.",
        "schema": {
            "type": "object",
            "properties": {
                "after": {"type": "string", "description": "Cursor for pagination"},
                "method": {"type": "string", "description": "Action to specify what pull request data needs to be retrieved.", "enum": ["get", "get_diff", "get_status", "get_files", "get_commits", "get_review_comments", "get_reviews", "get_comments", "get_check_runs"]},
                "owner": {"type": "string", "description": "Repository owner", "x-mcp-header": "owner"},
                "page": {"type": "integer", "description": "Page number for pagination (min 1)", "minimum": 1},
                "perPage": {"type": "integer", "description": "Results per page for pagination (min 1, max 100)", "minimum": 1, "maximum": 100},
                "pullNumber": {"type": "integer", "description": "Pull request number"},
                "repo": {"type": "string", "description": "Repository name", "x-mcp-header": "repo"}
            },
            "required": ["method", "owner", "repo", "pullNumber"]
        }
    },
}

# 打印每个样本
for name, data in samples.items():
    desc_chars = len(data["desc"])
    schema_chars = len(json.dumps(data["schema"], ensure_ascii=False, separators=(",", ":")))
    desc_tokens = desc_chars / 4
    schema_tokens = schema_chars / 4
    total = desc_tokens + schema_tokens
    print(f"{name:30s} desc={desc_chars:4d}ch({desc_tokens:5.0f}t) schema={schema_chars:4d}ch({schema_tokens:5.0f}t) = {total:5.0f}t")

# Anthropic 格式的序列化开销
# {"name":"github__xxx","description":"...","input_schema":{...}}
print()

# 分类所有 GitHub MCP 工具
simple_tools = [
    "get_me", "get_latest_release", "list_branches", "list_tags",
    "list_releases", "list_issue_types", "get_label",
    "get_tag", "get_release_by_tag", "list_issue_fields", "get_teams",
    "fork_repository", "run_secret_scanning", "request_copilot_review",
    "get_commit", "get_file_contents", "list_commits", "get_team_members"
]

medium_tools = [
    "search_code", "search_issues", "search_pull_requests", "search_repositories",
    "search_users", "search_commits", "list_issues", "list_pull_requests",
    "list_repository_collaborators",
    "issue_read", "pull_request_read", "add_issue_comment",
    "add_reply_to_pull_request_comment", "add_comment_to_pending_review",
    "delete_file", "merge_pull_request", "update_pull_request",
    "update_pull_request_branch", "create_branch", "sub_issue_write",
    "get_file_contents"
]

complex_tools = [
    "create_or_update_file", "create_pull_request", "issue_write",
    "pull_request_review_write", "push_files",
    "create_repository"
]

# 用代表性样本估算
simple = samples["get_me"]
simple_total = (len(simple["desc"]) + len(json.dumps(simple["schema"], separators=(",", ":")))) / 4

medium = samples["add_issue_comment"]
medium_total = (len(medium["desc"]) + len(json.dumps(medium["schema"], separators=(",", ":")))) / 4

complex_t = samples["create_or_update_file"]
complex_total = (len(complex_t["desc"]) + len(json.dumps(complex_t["schema"], separators=(",", ":")))) / 4

print(f"简单工具平均: {simple_total:.0f} tokens/个")
print(f"中等工具平均: {medium_total:.0f} tokens/个")
print(f"复杂工具平均: {complex_total:.0f} tokens/个")

subtotal_simple = simple_total * len(simple_tools)
subtotal_medium = medium_total * len(medium_tools)
subtotal_complex = complex_total * len(complex_tools)

total_tools = len(simple_tools) + len(medium_tools) + len(complex_tools)

print(f"\n=== GitHub MCP 工具定义 Token 估算 ===")
print(f"  简单工具 {len(simple_tools):2d}个 × {simple_total:.0f} = {subtotal_simple:.0f}t")
print(f"  中等工具 {len(medium_tools):2d}个 × {medium_total:.0f} = {subtotal_medium:.0f}t")
print(f"  复杂工具 {len(complex_tools):2d}个 × {complex_total:.0f} = {subtotal_complex:.0f}t")
print(f"  {'─'*40}")
total = subtotal_simple + subtotal_medium + subtotal_complex
print(f"  GitHub MCP 合计 {total_tools}个: {total:.0f} tokens")
print(f"  Anthropic API 包装开销 (JSON key+struct): +~{total_tools*20:.0f}t")
print(f"  GitHub MCP 定义总占用: ~{total + total_tools*20:.0f} tokens")
print()
print(f"  作为对比：内置工具 6个 × ~200 = {6*200} tokens")
print(f"  全量工具定义总计: ~{total + total_tools*20 + 1200:.0f} tokens")

# 延迟加载对比：空闲轮次只暴露固定检索入口；活跃轮次最多再暴露 5 个候选。
search_description = (
    "按自然语言意图检索已配置 MCP Server 的工具。"
    "需要 MCP 能力时先调用本工具；命中工具会在下一次模型迭代按需加载。"
    "跨语言检索时可在 query 中补充英文能力关键词。"
)
search_schema = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "要查找的 MCP 能力或任务意图；跨语言时可补充英文关键词"},
        "server": {"type": "string", "description": "可选的 MCP Server 名称"},
    },
    "required": ["query"],
    "additionalProperties": False,
}
search_tokens = (len(search_description) + len(json.dumps(search_schema, ensure_ascii=False, separators=(",", ":")))) / 4 + 20
full_mcp_tokens = total + total_tools * 20
idle_lazy_tokens = search_tokens
active_lazy_tokens = search_tokens + simple_total + medium_total * 3 + complex_total + 5 * 20
idle_reduction = (1 - idle_lazy_tokens / full_mcp_tokens) * 100

print("\n=== 延迟加载对比 ===")
print(f"  full:        ~{full_mcp_tokens:.0f} tokens（45 个 GitHub 工具）")
print(f"  idle lazy:   ~{idle_lazy_tokens:.0f} tokens（仅 search_mcp_tools）")
print(f"  active lazy: ~{active_lazy_tokens:.0f} tokens（检索入口 + 5 个候选）")
print(f"  idle lazy 降幅: {idle_reduction:.1f}%")
print(f"  是否达到 >= 90%: {'是' if idle_reduction >= 90 else '否'}")
