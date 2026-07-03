from __future__ import annotations

from pathlib import Path

import pytest

from mewcode.config import (
    AgentConfig,
    AppConfig,
    McpConfig,
    McpOAuthConfig,
    McpServerConfig,
    PromptCacheConfig,
    load_config,
    resolve_api_key,
)
from mewcode.context.models import ContextConfig
from mewcode.errors import ConfigError, redact_secret
from mewcode.hooks.models import HookConfig
from mewcode.memory.models import SessionMemoryConfig
from mewcode.permissions import PermissionConfig
from mewcode.subagents.models import SubAgentConfig
from mewcode.teams.models import TeamConfig
from mewcode.worktrees import WorktreeConfig


def write_yaml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_redact_secret_masks_exact_secret() -> None:
    secret = "sk-test-secret-1234567890"
    assert secret not in redact_secret(f"bad key {secret}", secret)


def test_redact_secret_masks_common_secret_without_explicit_secret() -> None:
    secret = "sk-test-secret-1234567890"
    assert secret not in redact_secret(f"bad key {secret}")


def test_loads_required_yaml_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
""",
    )

    config = load_config(tmp_path)

    assert config == AppConfig(
        protocol="openai",
        model="test-model",
        base_url="https://example.test/v1",
        api_key="plain-key",
    )
    assert config.agent == AgentConfig(max_iterations=40)
    assert config.sub_agents == SubAgentConfig(default_max_iterations=40)
    assert config.mcp == McpConfig()
    assert config.hooks == HookConfig()


def test_mcp_config_defaults_to_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
""",
    )

    config = load_config(tmp_path)

    assert config.mcp.servers == {}
    assert config.context == ContextConfig()
    assert config.memory == SessionMemoryConfig()


def test_loads_hook_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
hooks:
  - name: inject-context
    event: turn.start
    action:
      type: prompt
      text: 先说明 Hook 已触发
""",
    )

    config = load_config(tmp_path)

    assert len(config.hooks.rules) == 1
    assert config.hooks.rules[0].id == "inject-context"
    assert config.hooks.rules[0].action.prompt is not None


def test_project_hooks_override_user_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    write_yaml(
        home / ".mewcode" / "config.yaml",
        """
protocol: openai
model: user-model
base_url: https://example.test/v1
api_key: user-key
hooks:
  - name: user-hook
    event: turn.start
    action:
      type: prompt
      text: user
""",
    )
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
model: project-model
hooks:
  - name: project-hook
    event: turn.start
    action:
      type: prompt
      text: project
""",
    )

    config = load_config(tmp_path)

    assert config.model == "project-model"
    assert [rule.id for rule in config.hooks.rules] == ["project-hook"]


def test_rejects_invalid_hooks_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
hooks:
  - event: tool.before
    background: true
    action:
      type: prompt
      text: bad
""",
    )

    with pytest.raises(ConfigError, match="tool.before"):
        load_config(tmp_path)


def test_loads_memory_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
memory:
  enabled: false
  project_dir: .custom-mewcode
  sessions_dir: session-log
  memory_dir: notes
  user_dir: ~/.custom-mewcode
  instruction_filename: RULES.md
  include_max_depth: 4
  auto_restore: false
  retention_days: 45
  time_gap_hours: 12
  index_max_lines: 100
  index_max_bytes: 12000
  auto_notes_enabled: false
""",
    )

    config = load_config(tmp_path)

    assert config.memory == SessionMemoryConfig(
        enabled=False,
        project_dir=".custom-mewcode",
        sessions_dir="session-log",
        memory_dir="notes",
        user_dir="~/.custom-mewcode",
        instruction_filename="RULES.md",
        include_max_depth=4,
        auto_restore=False,
        retention_days=45,
        time_gap_hours=12,
        index_max_lines=100,
        index_max_bytes=12_000,
        auto_notes_enabled=False,
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "memory: []",
        "memory:\n  project_dir: ''",
        "memory:\n  sessions_dir: ''",
        "memory:\n  memory_dir: ''",
        "memory:\n  user_dir: ''",
        "memory:\n  instruction_filename: ''",
        "memory:\n  include_max_depth: 0",
        "memory:\n  retention_days: 0",
        "memory:\n  time_gap_hours: 0",
        "memory:\n  index_max_lines: 0",
        "memory:\n  index_max_bytes: 0",
    ],
)
def test_rejects_invalid_memory_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snippet: str,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        f"""
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
{snippet}
""",
    )

    with pytest.raises(ConfigError, match="memory"):
        load_config(tmp_path)


def test_readme_mentions_session_memory() -> None:
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")

    for text in (".mewcode/sessions/", ".mewcode/memory/", ".mewcode/context/", "--new-session"):
        assert text in readme
    for text in (".mewcode/sessions/", ".mewcode/memory/", ".mewcode/context/"):
        assert text in gitignore


def test_readme_documents_worktree_isolation() -> None:
    content = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert "isolation: worktree" in content
    assert "copy_paths" in content
    assert "symlink_paths" in content
    assert "ignored_copy_paths" in content
    assert "cleanup_interval_seconds" in content
    assert "retention_days" in content
    assert "不会自动提交、推送、合并或丢弃" in content


def test_loads_context_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
context:
  enabled: false
  window_tokens: 64000
  single_tool_result_tokens: 1000
  turn_tool_result_tokens: 2000
  tool_preview_chars: 300
  recent_tokens: 4000
  min_recent_messages: 6
  auto_reserve_tokens: 9000
  manual_reserve_tokens: 2000
  summary_failure_limit: 4
  chars_per_token: 3.5
  store_dir: .mewcode/custom-context
""",
    )

    config = load_config(tmp_path)

    assert config.context == ContextConfig(
        enabled=False,
        window_tokens=64_000,
        single_tool_result_tokens=1_000,
        turn_tool_result_tokens=2_000,
        tool_preview_chars=300,
        recent_tokens=4_000,
        min_recent_messages=6,
        auto_reserve_tokens=9_000,
        manual_reserve_tokens=2_000,
        summary_failure_limit=4,
        chars_per_token=3.5,
        store_dir=".mewcode/custom-context",
    )


def test_prompt_cache_config_defaults_are_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
""",
    )

    config = load_config(tmp_path)

    assert config.prompt_cache == PromptCacheConfig(
        enabled=True,
        key_namespace="mewcode",
        openai_cache_key=True,
        openai_retention=None,
        anthropic_cache_control=True,
    )


def test_loads_prompt_cache_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
prompt_cache:
  enabled: false
  key_namespace: project-cache
  openai_cache_key: false
  openai_retention: 24h
  anthropic_cache_control: false
""",
    )

    config = load_config(tmp_path)

    assert config.prompt_cache == PromptCacheConfig(
        enabled=False,
        key_namespace="project-cache",
        openai_cache_key=False,
        openai_retention="24h",
        anthropic_cache_control=False,
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "prompt_cache: []",
        "prompt_cache:\n  key_namespace: ''",
        "prompt_cache:\n  openai_retention: forever",
    ],
)
def test_rejects_invalid_prompt_cache_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snippet: str,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        f"""
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
{snippet}
""",
    )

    with pytest.raises(ConfigError, match="prompt_cache"):
        load_config(tmp_path)


def test_loads_sub_agents_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
sub_agents:
  enabled: false
  foreground_timeout_seconds: 12.5
  default_max_iterations: 3
  max_background_tasks: 4
  global_blocked_tools:
    - delegate_agent
    - run_command
  background_allowed_tools:
    - read_file
  model_aliases:
    haiku: cheap-model
  plugin_role_roots:
    - ~/.mewcode/plugin-agents
""",
    )

    config = load_config(tmp_path)

    assert config.sub_agents == SubAgentConfig(
        enabled=False,
        foreground_timeout_seconds=12.5,
        default_max_iterations=3,
        max_background_tasks=4,
        global_blocked_tools=("delegate_agent", "run_command"),
        background_allowed_tools=("read_file",),
        model_aliases={"haiku": "cheap-model"},
        plugin_role_roots=("~/.mewcode/plugin-agents",),
    )


def test_defaults_sub_agent_max_iterations_to_40_when_field_is_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
sub_agents:
  enabled: false
""",
    )

    config = load_config(tmp_path)

    assert config.sub_agents.default_max_iterations == 40


def test_loads_worktree_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
sub_agents:
  worktree:
    copy_paths:
      - .mewcode.permissions.local.yaml
    symlink_paths:
      - .venv
    ignored_copy_paths:
      - .env
    cleanup_interval_seconds: 45
    retention_days: 2.5
""",
    )

    config = load_config(tmp_path)

    assert config.sub_agents.worktree == WorktreeConfig(
        copy_paths=(".mewcode.permissions.local.yaml",),
        symlink_paths=(".venv",),
        ignored_copy_paths=(".env",),
        cleanup_interval_seconds=45.0,
        retention_days=2.5,
    )


@pytest.mark.parametrize(
    "worktree_yaml",
    (
        "[]",
        "{cleanup_interval_seconds: 0}",
        "{retention_days: -1}",
        "{copy_paths: ../secret}",
        "{copy_paths: ['']}",
        "{copy_paths: [.env], symlink_paths: [.env]}",
        "{ignored_copy_paths: [.env, .env]}",
    ),
)
def test_rejects_invalid_worktree_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worktree_yaml: str,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        f"""
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
sub_agents:
  worktree: {worktree_yaml}
""",
    )

    with pytest.raises(ConfigError, match="sub_agents.worktree"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "snippet",
    [
        "sub_agents: []",
        "sub_agents:\n  foreground_timeout_seconds: 0",
        "sub_agents:\n  default_max_iterations: 0",
        "sub_agents:\n  max_background_tasks: 0",
        "sub_agents:\n  global_blocked_tools: delegate_agent",
        "sub_agents:\n  background_allowed_tools:\n    - ''",
        "sub_agents:\n  model_aliases: []",
        "sub_agents:\n  plugin_role_roots: ~/.mewcode/agents",
    ],
)
def test_rejects_invalid_sub_agents_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snippet: str,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        f"""
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
{snippet}
""",
    )

    with pytest.raises(ConfigError, match="sub_agents"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "snippet",
    [
        "context: []",
        "context:\n  window_tokens: 0",
        "context:\n  chars_per_token: 0",
        "context:\n  store_dir: ''",
    ],
)
def test_rejects_invalid_context_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snippet: str,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        f"""
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
{snippet}
""",
    )

    with pytest.raises(ConfigError, match="context"):
        load_config(tmp_path)


def test_loads_stdio_mcp_server_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
mcp_servers:
  local_demo:
    type: stdio
    command: python
    args: ["tests/fixtures/mcp_stdio_server.py", "--mode", "ok"]
    env:
      API_TOKEN: static-token
""",
    )

    config = load_config(tmp_path)

    assert config.mcp.servers["local_demo"] == McpServerConfig(
        name="local_demo",
        transport="stdio",
        command="python",
        args=("tests/fixtures/mcp_stdio_server.py", "--mode", "ok"),
        env={"API_TOKEN": "static-token"},
    )


def test_loads_http_mcp_server_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
mcp_servers:
  remote_demo:
    type: http
    url: http://127.0.0.1:8765/mcp
    headers:
      Authorization: Bearer static-token
""",
    )

    config = load_config(tmp_path)

    assert config.mcp.servers["remote_demo"] == McpServerConfig(
        name="remote_demo",
        transport="http",
        url="http://127.0.0.1:8765/mcp",
        headers={"Authorization": "Bearer static-token"},
    )


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("mcp_servers: []", "mcp_servers"),
        ("mcp_servers:\n  bad: []", "mcp_servers.bad"),
        ("mcp_servers:\n  bad:\n    type: stdio", "command"),
        ("mcp_servers:\n  bad:\n    type: http", "url"),
        ("mcp_servers:\n  bad:\n    type: websocket", "type"),
    ],
)
def test_rejects_invalid_mcp_server_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snippet: str,
    message: str,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        f"""
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
{snippet}
""",
    )

    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_mcp_config_expands_environment_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("MCP_TOKEN", "secret-token")
    monkeypatch.setenv("MCP_PORT", "8765")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
mcp_servers:
  local_demo:
    type: stdio
    command: python
    env:
      API_TOKEN: ${MCP_TOKEN}
  remote_demo:
    type: http
    url: http://127.0.0.1:${MCP_PORT}/mcp
    headers:
      Authorization: Bearer ${MCP_TOKEN}
""",
    )

    config = load_config(tmp_path)

    assert config.mcp.servers["local_demo"].env == {"API_TOKEN": "secret-token"}
    assert config.mcp.servers["remote_demo"].url == "http://127.0.0.1:8765/mcp"
    assert config.mcp.servers["remote_demo"].headers == {"Authorization": "Bearer secret-token"}


def test_mcp_config_rejects_missing_environment_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("MCP_MISSING_TOKEN", raising=False)
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
mcp_servers:
  remote_demo:
    type: http
    url: http://127.0.0.1:8765/mcp
    headers:
      Authorization: Bearer ${MCP_MISSING_TOKEN}
""",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)

    message = str(exc_info.value)
    assert "remote_demo" in message
    assert "headers.Authorization" in message
    assert "MCP_MISSING_TOKEN" in message
    assert "${MCP_MISSING_TOKEN}" not in message


def test_loads_agent_max_iterations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
agent:
  max_iterations: 3
""",
    )

    config = load_config(tmp_path)

    assert config.agent.max_iterations == 3


def test_loads_permissions_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
permissions:
  mode: permissive
""",
    )

    config = load_config(tmp_path)

    assert config.permissions == PermissionConfig(mode="permissive")


def test_rejects_invalid_permissions_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
permissions:
  mode: unsafe
""",
    )

    with pytest.raises(ConfigError, match="permissions.mode"):
        load_config(tmp_path)


def test_rejects_invalid_agent_max_iterations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
agent:
  max_iterations: 0
""",
    )

    with pytest.raises(ConfigError, match="agent.max_iterations"):
        load_config(tmp_path)


def test_missing_required_field_raises_config_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
""",
    )

    with pytest.raises(ConfigError, match="api_key"):
        load_config(tmp_path)


def test_project_config_overrides_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    write_yaml(
        home / ".mewcode" / "config.yaml",
        """
protocol: openai
model: user-model
base_url: https://user.test/v1
api_key: user-key
""",
    )
    write_yaml(
        project / ".mewcode.yaml",
        """
protocol: anthropic
model: project-model
base_url: https://project.test/v1
""",
    )

    config = load_config(project)

    assert config.protocol == "anthropic"
    assert config.model == "project-model"
    assert config.base_url == "https://project.test/v1"
    assert config.api_key == "user-key"


def test_project_mcp_servers_override_user_servers_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    write_yaml(
        home / ".mewcode" / "config.yaml",
        """
protocol: openai
model: user-model
base_url: https://user.test/v1
api_key: user-key
mcp_servers:
  shared:
    type: stdio
    command: user-command
  user_only:
    type: stdio
    command: user-only-command
""",
    )
    write_yaml(
        project / ".mewcode.yaml",
        """
model: project-model
mcp_servers:
  shared:
    type: http
    url: http://127.0.0.1:8765/mcp
  project_only:
    type: stdio
    command: project-only-command
""",
    )

    config = load_config(project)

    assert set(config.mcp.servers) == {"shared", "user_only", "project_only"}
    assert config.mcp.servers["shared"].transport == "http"
    assert config.mcp.servers["shared"].url == "http://127.0.0.1:8765/mcp"
    assert config.mcp.servers["user_only"].command == "user-only-command"
    assert config.mcp.servers["project_only"].command == "project-only-command"


def test_api_key_can_reference_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEWCODE_TEST_KEY", "resolved-key")
    assert resolve_api_key("${MEWCODE_TEST_KEY}") == "resolved-key"


def test_missing_environment_api_key_is_clear_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEWCODE_MISSING_KEY", raising=False)
    with pytest.raises(ConfigError) as exc_info:
        resolve_api_key("${MEWCODE_MISSING_KEY}")
    message = str(exc_info.value)
    assert "MEWCODE_MISSING_KEY" in message
    assert "${MEWCODE_MISSING_KEY}" not in message


def test_unknown_protocol_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: other
model: test-model
base_url: https://example.test/v1
api_key: key
""",
    )

    with pytest.raises(ConfigError, match="protocol"):
        load_config(tmp_path)


def test_readme_documents_context_management() -> None:
    text = Path("README.md").read_text(encoding="utf-8")

    assert "context:" in text
    assert "/compact" in text
    assert ".mewcode/context/" in text


def test_readme_documents_team_collaboration() -> None:
    text = Path("README.md").read_text(encoding="utf-8")

    assert "teams:" in text
    assert "~/.mewcode/teams/<team>/" in text
    assert "require_approval" in text
    assert "coroutine" in text
    assert "不会自动合并" in text


def test_cli_reports_config_error_without_secret(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from mewcode import cli

    secret = "sk-cli-secret-1234567890"

    def fail_load_config() -> None:
        raise ConfigError(f"bad secret {secret}")

    monkeypatch.setattr(cli, "load_config", fail_load_config)

    assert cli.main([]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.err
    assert "[REDACTED]" in captured.err


def test_teams_config_loads_defaults_and_explicit_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
teams:
  enabled: false
  lock_timeout_seconds: 4
  lock_retry_interval_seconds: 0.2
  stale_lock_seconds: 45
  wait_timeout_seconds: 12
""",
    )

    config = load_config(tmp_path)

    assert config.teams == TeamConfig(False, 4.0, 0.2, 45.0, 12.0)


@pytest.mark.parametrize(
    "teams_yaml, expected",
    (
        ("lock_timeout_seconds: 0", "大于 0"),
        ("lock_retry_interval_seconds: -1", "大于 0"),
        ("wait_timeout_seconds: 0", "大于 0"),
        ("lock_timeout_seconds: 1\n  lock_retry_interval_seconds: 2", "不能大于"),
        ("lock_timeout_seconds: 3\n  stale_lock_seconds: 2", "必须大于"),
    ),
)
def test_teams_config_rejects_invalid_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    teams_yaml: str,
    expected: str,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        f"""
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
teams:
  {teams_yaml}
""",
    )

    with pytest.raises(ConfigError, match=expected):
        load_config(tmp_path)


def test_mcp_http_oauth_config_expands_preregistered_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("GITHUB_MCP_CLIENT_ID", "client-id")
    monkeypatch.setenv("GITHUB_MCP_CLIENT_SECRET", "client-secret-value")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
mcp_servers:
  github:
    type: http
    url: https://api.githubcopilot.com/mcp/
    oauth:
      client_id: ${GITHUB_MCP_CLIENT_ID}
      client_secret: ${GITHUB_MCP_CLIENT_SECRET}
      scopes: [repo, read:user]
""",
    )

    server = load_config(tmp_path).mcp.servers["github"]

    assert server.oauth == McpOAuthConfig(
        enabled=True,
        client_id="client-id",
        client_secret="client-secret-value",
        scopes=("repo", "read:user"),
    )
    assert "client-secret-value" not in repr(server)


def test_mcp_http_disabled_oauth_keeps_static_authorization_header(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        """
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
mcp_servers:
  github:
    type: http
    url: https://api.githubcopilot.com/mcp/
    headers:
      Authorization: Bearer pat-value
    oauth:
      enabled: false
""",
    )

    server = load_config(tmp_path).mcp.servers["github"]

    assert server.oauth == McpOAuthConfig(enabled=False)
    assert server.headers["Authorization"] == "Bearer pat-value"


@pytest.mark.parametrize(
    "server_yaml, expected",
    (
        (
            "type: http\n    url: https://mcp.test/mcp\n    headers:\n      authorization: Bearer token\n    oauth: {}",
            "Authorization Header",
        ),
        ("type: stdio\n    command: python\n    oauth: {}", "仅支持 http"),
        ("type: http\n    url: https://mcp.test/mcp\n    oauth:\n      scopes: ['bad scope']", "非法 scope"),
        ("type: http\n    url: https://mcp.test/mcp\n    oauth:\n      client_secret: secret", "需要同时配置"),
    ),
)
def test_mcp_oauth_config_rejects_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_yaml: str,
    expected: str,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_yaml(
        tmp_path / ".mewcode.yaml",
        f"""
protocol: openai
model: test-model
base_url: https://example.test/v1
api_key: plain-key
mcp_servers:
  demo:
    {server_yaml}
""",
    )

    with pytest.raises(ConfigError, match=expected):
        load_config(tmp_path)
