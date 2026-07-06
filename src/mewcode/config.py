from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from mewcode.context.models import ContextConfig
from mewcode.errors import ConfigError
from mewcode.hooks.config import parse_hook_config
from mewcode.hooks.models import HookConfig
from mewcode.memory.models import SessionMemoryConfig
from mewcode.permissions.models import PermissionConfig
from mewcode.subagents.models import SubAgentConfig
from mewcode.teams.models import TeamConfig
from mewcode.worktrees.models import WorktreeConfig, WorktreeError
from mewcode.worktrees.paths import validate_config_path


ProtocolName = Literal["openai", "anthropic"]
ThinkingType = Literal["enabled", "adaptive"]
ThinkingDisplay = Literal["summarized", "omitted"]
ThinkingEffort = Literal["low", "medium", "high"]
McpTransportName = Literal["stdio", "http"]
PromptCacheRetention = Literal["in_memory", "24h"]

_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_ENV_INTERPOLATION_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class ThinkingConfig:
    enabled: bool
    type: ThinkingType = "enabled"
    budget_tokens: int | None = 1024
    effort: ThinkingEffort | None = None
    display: ThinkingDisplay = "summarized"


@dataclass(frozen=True)
class AgentConfig:
    max_iterations: int = 40


@dataclass(frozen=True)
class PromptCacheConfig:
    enabled: bool = True
    key_namespace: str = "mewcode"
    openai_cache_key: bool = True
    openai_retention: PromptCacheRetention | None = None
    anthropic_cache_control: bool = True


@dataclass(frozen=True)
class McpOAuthConfig:
    enabled: bool = True
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    transport: McpTransportName
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    oauth: McpOAuthConfig | None = None


@dataclass(frozen=True)
class McpConfig:
    servers: dict[str, McpServerConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    protocol: ProtocolName
    model: str
    base_url: str
    api_key: str
    max_tokens: int = 4096
    timeout_seconds: float = 60.0
    thinking: ThinkingConfig | None = None
    agent: AgentConfig = field(default_factory=AgentConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
    prompt_cache: PromptCacheConfig = field(default_factory=PromptCacheConfig)
    hooks: HookConfig = field(default_factory=HookConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    memory: SessionMemoryConfig = field(default_factory=SessionMemoryConfig)
    sub_agents: SubAgentConfig = field(default_factory=SubAgentConfig)
    teams: TeamConfig = field(default_factory=TeamConfig)


def user_config_path() -> Path:
    return Path.home() / ".mewcode" / "config.yaml"


def discover_project_config(cwd: Path) -> Path | None:
    current = cwd.resolve()
    for path in (current, *current.parents):
        candidate = path / ".mewcode.yaml"
        if candidate.exists():
            return candidate
    return None


def resolve_api_key(raw_value: str) -> str:
    value = str(raw_value).strip()
    if not value:
        raise ConfigError("api_key 不能为空")

    match = _ENV_REF_RE.match(value)
    if match:
        env_name = match.group(1)
        resolved = os.environ.get(env_name, "")
        if not resolved:
            raise ConfigError(f"环境变量 {env_name} 未设置或为空，无法解析 api_key")
        return resolved

    if "${" in value or "}" in value:
        raise ConfigError("api_key 的环境变量引用必须使用完整 ${VAR_NAME} 格式")

    return value


def load_config(cwd: Path | None = None) -> AppConfig:
    root = cwd or Path.cwd()
    user_data = _read_yaml_if_exists(user_config_path())
    project_path = discover_project_config(root)
    project_data = _read_yaml_if_exists(project_path) if project_path else {}

    merged = _merge_config_data(user_data, project_data)
    if not merged:
        raise ConfigError("未找到可用配置，请创建 ~/.mewcode/config.yaml 或项目级 .mewcode.yaml")
    return _parse_config(merged)


def _merge_config_data(user_data: dict[str, Any], project_data: dict[str, Any]) -> dict[str, Any]:
    merged = {**user_data, **project_data}
    user_servers = user_data.get("mcp_servers")
    project_servers = project_data.get("mcp_servers")
    if user_servers is None and project_servers is None:
        return merged
    if user_servers is None or project_servers is None:
        merged["mcp_servers"] = project_servers if project_servers is not None else user_servers
        return merged
    if isinstance(user_servers, dict) and isinstance(project_servers, dict):
        merged["mcp_servers"] = {**user_servers, **project_servers}
        return merged
    merged["mcp_servers"] = project_servers
    return merged


def _read_yaml_if_exists(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 {path} 不是合法 YAML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件 {path}: {exc}") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件 {path} 顶层必须是 YAML 对象")
    return raw


def _parse_config(raw: dict[str, Any]) -> AppConfig:
    required = ("protocol", "model", "base_url", "api_key")
    missing = [field for field in required if not str(raw.get(field, "")).strip()]
    if missing:
        raise ConfigError(f"配置缺少必填字段: {', '.join(missing)}")

    protocol = str(raw["protocol"]).strip().lower()
    if protocol not in {"openai", "anthropic"}:
        raise ConfigError(f"不支持的 protocol: {protocol}")

    return AppConfig(
        protocol=protocol,  # type: ignore[arg-type]
        model=str(raw["model"]).strip(),
        base_url=str(raw["base_url"]).strip().rstrip("/"),
        api_key=resolve_api_key(str(raw["api_key"])),
        max_tokens=_parse_int(raw.get("max_tokens", 4096), "max_tokens"),
        timeout_seconds=_parse_float(raw.get("timeout_seconds", 60.0), "timeout_seconds"),
        thinking=_parse_thinking(raw.get("thinking")),
        agent=_parse_agent(raw.get("agent")),
        prompt_cache=_parse_prompt_cache(raw.get("prompt_cache")),
        permissions=_parse_permissions(raw.get("permissions")),
        hooks=parse_hook_config(raw.get("hooks")),
        mcp=_parse_mcp_config(raw.get("mcp_servers")),
        context=_parse_context(raw.get("context")),
        memory=_parse_memory(raw.get("memory")),
        sub_agents=_parse_sub_agents(raw.get("sub_agents")),
        teams=_parse_teams(raw.get("teams")),
    )


def _parse_thinking(raw: Any) -> ThinkingConfig | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return ThinkingConfig(enabled=raw) if raw else None
    if not isinstance(raw, dict):
        raise ConfigError("thinking 必须是 YAML 对象或布尔值")

    enabled = bool(raw.get("enabled", True))
    if not enabled:
        return None

    thinking_type = str(raw.get("type", "enabled")).strip()
    if thinking_type not in {"enabled", "adaptive"}:
        raise ConfigError(f"不支持的 thinking.type: {thinking_type}")

    display = str(raw.get("display", "summarized")).strip()
    if display not in {"summarized", "omitted"}:
        raise ConfigError(f"不支持的 thinking.display: {display}")

    effort = raw.get("effort")
    if effort is not None:
        effort = str(effort).strip()
        if effort not in {"low", "medium", "high"}:
            raise ConfigError(f"不支持的 thinking.effort: {effort}")

    budget_tokens = raw.get("budget_tokens", 1024)
    return ThinkingConfig(
        enabled=True,
        type=thinking_type,  # type: ignore[arg-type]
        budget_tokens=None if budget_tokens is None else _parse_int(budget_tokens, "thinking.budget_tokens"),
        effort=effort,  # type: ignore[arg-type]
        display=display,  # type: ignore[arg-type]
    )


def _parse_agent(raw: Any) -> AgentConfig:
    if raw is None:
        return AgentConfig()
    if not isinstance(raw, dict):
        raise ConfigError("agent 必须是 YAML 对象")
    return AgentConfig(max_iterations=_parse_int(raw.get("max_iterations", 40), "agent.max_iterations"))


def _parse_prompt_cache(raw: Any) -> PromptCacheConfig:
    if raw is None:
        return PromptCacheConfig()
    if not isinstance(raw, dict):
        raise ConfigError("prompt_cache 必须是 YAML 对象")

    key_namespace = str(raw.get("key_namespace", "mewcode")).strip()
    if not key_namespace:
        raise ConfigError("prompt_cache.key_namespace 不能为空")

    retention_raw = raw.get("openai_retention")
    openai_retention: PromptCacheRetention | None = None
    if retention_raw is not None:
        retention = str(retention_raw).strip()
        if retention not in {"in_memory", "24h"}:
            raise ConfigError(f"不支持的 prompt_cache.openai_retention: {retention}")
        openai_retention = retention  # type: ignore[assignment]

    return PromptCacheConfig(
        enabled=bool(raw.get("enabled", True)),
        key_namespace=key_namespace,
        openai_cache_key=bool(raw.get("openai_cache_key", True)),
        openai_retention=openai_retention,
        anthropic_cache_control=bool(raw.get("anthropic_cache_control", True)),
    )


def _parse_teams(raw: Any) -> TeamConfig:
    if raw is None:
        return TeamConfig()
    if not isinstance(raw, dict):
        raise ConfigError("teams 必须是 YAML 对象")
    timeout = _parse_float(raw.get("lock_timeout_seconds", 2.0), "teams.lock_timeout_seconds")
    retry = _parse_float(raw.get("lock_retry_interval_seconds", 0.05), "teams.lock_retry_interval_seconds")
    stale = _parse_float(raw.get("stale_lock_seconds", 30.0), "teams.stale_lock_seconds")
    wait = _parse_float(raw.get("wait_timeout_seconds", 30.0), "teams.wait_timeout_seconds")
    if retry > timeout:
        raise ConfigError("teams.lock_retry_interval_seconds 不能大于 lock_timeout_seconds")
    if stale <= timeout:
        raise ConfigError("teams.stale_lock_seconds 必须大于 lock_timeout_seconds")
    return TeamConfig(
        enabled=bool(raw.get("enabled", True)),
        lock_timeout_seconds=timeout,
        lock_retry_interval_seconds=retry,
        stale_lock_seconds=stale,
        wait_timeout_seconds=wait,
    )


def _parse_permissions(raw: Any) -> PermissionConfig:
    if raw is None:
        return PermissionConfig()
    if not isinstance(raw, dict):
        raise ConfigError("permissions 必须是 YAML 对象")
    mode = str(raw.get("mode", "default")).strip()
    if mode not in {"strict", "default", "permissive"}:
        raise ConfigError(f"不支持的 permissions.mode: {mode}")
    return PermissionConfig(mode=mode)  # type: ignore[arg-type]


def _parse_context(raw: Any) -> ContextConfig:
    if raw is None:
        return ContextConfig()
    if not isinstance(raw, dict):
        raise ConfigError("context 必须是 YAML 对象")

    store_dir = str(raw.get("store_dir", ".mewcode/context")).strip()
    if not store_dir:
        raise ConfigError("context.store_dir 不能为空")

    return ContextConfig(
        enabled=bool(raw.get("enabled", True)),
        window_tokens=_parse_int(raw.get("window_tokens", 128_000), "context.window_tokens"),
        single_tool_result_tokens=_parse_int(
            raw.get("single_tool_result_tokens", 4_000),
            "context.single_tool_result_tokens",
        ),
        turn_tool_result_tokens=_parse_int(
            raw.get("turn_tool_result_tokens", 8_000),
            "context.turn_tool_result_tokens",
        ),
        tool_preview_chars=_parse_int(raw.get("tool_preview_chars", 2_000), "context.tool_preview_chars"),
        recent_tokens=_parse_int(raw.get("recent_tokens", 10_000), "context.recent_tokens"),
        min_recent_messages=_parse_int(raw.get("min_recent_messages", 5), "context.min_recent_messages"),
        auto_reserve_tokens=_parse_int(raw.get("auto_reserve_tokens", 13_000), "context.auto_reserve_tokens"),
        manual_reserve_tokens=_parse_int(raw.get("manual_reserve_tokens", 3_000), "context.manual_reserve_tokens"),
        summary_failure_limit=_parse_int(raw.get("summary_failure_limit", 3), "context.summary_failure_limit"),
        chars_per_token=_parse_float(raw.get("chars_per_token", 4.0), "context.chars_per_token"),
        store_dir=store_dir,
    )


def _parse_memory(raw: Any) -> SessionMemoryConfig:
    if raw is None:
        return SessionMemoryConfig()
    if not isinstance(raw, dict):
        raise ConfigError("memory 必须是 YAML 对象")

    project_dir = _optional_non_empty_str(raw.get("project_dir", ".mewcode"), "memory.project_dir")
    sessions_dir = _optional_non_empty_str(raw.get("sessions_dir", "sessions"), "memory.sessions_dir")
    memory_dir = _optional_non_empty_str(raw.get("memory_dir", "memory"), "memory.memory_dir")
    user_dir = _optional_non_empty_str(raw.get("user_dir", "~/.mewcode"), "memory.user_dir")
    instruction_filename = _optional_non_empty_str(
        raw.get("instruction_filename", "AGENTS.md"),
        "memory.instruction_filename",
    )
    critical_confidence = _parse_probability(
        raw.get("critical_preference_min_confidence", 0.95),
        "memory.critical_preference_min_confidence",
    )

    return SessionMemoryConfig(
        enabled=bool(raw.get("enabled", True)),
        project_dir=project_dir,
        sessions_dir=sessions_dir,
        memory_dir=memory_dir,
        user_dir=user_dir,
        instruction_filename=instruction_filename,
        include_max_depth=_parse_int(raw.get("include_max_depth", 5), "memory.include_max_depth"),
        auto_restore=bool(raw.get("auto_restore", True)),
        retention_days=_parse_int(raw.get("retention_days", 30), "memory.retention_days"),
        time_gap_hours=_parse_int(raw.get("time_gap_hours", 24), "memory.time_gap_hours"),
        index_max_lines=_parse_int(raw.get("index_max_lines", 200), "memory.index_max_lines"),
        index_max_bytes=_parse_int(raw.get("index_max_bytes", 25_000), "memory.index_max_bytes"),
        auto_notes_enabled=bool(raw.get("auto_notes_enabled", True)),
        critical_preference_min_confidence=critical_confidence,
    )


def _parse_sub_agents(raw: Any) -> SubAgentConfig:
    if raw is None:
        return SubAgentConfig()
    if not isinstance(raw, dict):
        raise ConfigError("sub_agents 必须是 YAML 对象")

    foreground_timeout_seconds = _parse_float(
        raw.get("foreground_timeout_seconds", 30.0),
        "sub_agents.foreground_timeout_seconds",
    )
    if foreground_timeout_seconds <= 0:
        raise ConfigError("sub_agents.foreground_timeout_seconds 必须大于 0")

    default_max_iterations_raw = raw.get("default_max_iterations", 40)
    default_max_iterations = None
    if default_max_iterations_raw is not None:
        default_max_iterations = _parse_int(default_max_iterations_raw, "sub_agents.default_max_iterations")
        if default_max_iterations <= 0:
            raise ConfigError("sub_agents.default_max_iterations 必须大于 0")

    max_background_tasks = _parse_int(raw.get("max_background_tasks", 8), "sub_agents.max_background_tasks")
    if max_background_tasks <= 0:
        raise ConfigError("sub_agents.max_background_tasks 必须大于 0")

    return SubAgentConfig(
        enabled=bool(raw.get("enabled", True)),
        foreground_timeout_seconds=foreground_timeout_seconds,
        default_max_iterations=default_max_iterations,
        max_background_tasks=max_background_tasks,
        global_blocked_tools=_parse_string_tuple(
            raw.get("global_blocked_tools", ("delegate_agent",)),
            "sub_agents.global_blocked_tools",
        ),
        background_allowed_tools=_parse_string_tuple(
            raw.get("background_allowed_tools", ("read_file", "find_files", "search_code")),
            "sub_agents.background_allowed_tools",
        ),
        model_aliases=_parse_string_map(raw.get("model_aliases", {}), "sub_agents.model_aliases", expand=False),
        plugin_role_roots=_parse_string_tuple(
            raw.get("plugin_role_roots", ()),
            "sub_agents.plugin_role_roots",
        ),
        worktree=_parse_worktree_config(raw.get("worktree")),
    )


def _parse_worktree_config(raw: Any) -> WorktreeConfig:
    if raw is None:
        return WorktreeConfig()
    if not isinstance(raw, dict):
        raise ConfigError("sub_agents.worktree 必须是 YAML 对象")

    copy_paths = _parse_safe_worktree_paths(raw.get("copy_paths", ()), "sub_agents.worktree.copy_paths")
    symlink_paths = _parse_safe_worktree_paths(
        raw.get("symlink_paths", ()),
        "sub_agents.worktree.symlink_paths",
    )
    ignored_copy_paths = _parse_safe_worktree_paths(
        raw.get("ignored_copy_paths", ()),
        "sub_agents.worktree.ignored_copy_paths",
    )
    groups = {
        "copy_paths": copy_paths,
        "symlink_paths": symlink_paths,
        "ignored_copy_paths": ignored_copy_paths,
    }
    owners: dict[str, str] = {}
    for group, paths in groups.items():
        for path in paths:
            previous = owners.get(path)
            if previous is not None:
                raise ConfigError(
                    f"sub_agents.worktree 路径 `{path}` 同时出现在 {previous} 和 {group}"
                )
            owners[path] = group

    cleanup_interval = _parse_float(
        raw.get("cleanup_interval_seconds", 3600.0),
        "sub_agents.worktree.cleanup_interval_seconds",
    )
    retention_days = _parse_float(raw.get("retention_days", 7.0), "sub_agents.worktree.retention_days")
    return WorktreeConfig(
        copy_paths=copy_paths,
        symlink_paths=symlink_paths,
        ignored_copy_paths=ignored_copy_paths,
        cleanup_interval_seconds=cleanup_interval,
        retention_days=retention_days,
    )


def _parse_safe_worktree_paths(value: Any, field: str) -> tuple[str, ...]:
    paths = _parse_string_tuple(value, field)
    if len(set(paths)) != len(paths):
        raise ConfigError(f"{field} 不能包含重复路径")
    for path in paths:
        try:
            validate_config_path(path)
        except WorktreeError as exc:
            raise ConfigError(f"{field} 包含无效路径 `{path}`: {exc.message}") from exc
    return paths


def _parse_mcp_config(raw: Any) -> McpConfig:
    if raw is None:
        return McpConfig()
    if not isinstance(raw, dict):
        raise ConfigError("mcp_servers 必须是 YAML 对象")

    servers: dict[str, McpServerConfig] = {}
    for raw_name, raw_server in raw.items():
        name = str(raw_name).strip()
        if not name:
            raise ConfigError("mcp_servers 的 Server 名不能为空")
        if not isinstance(raw_server, dict):
            raise ConfigError(f"mcp_servers.{name} 必须是 YAML 对象")
        servers[name] = _parse_mcp_server(name, raw_server)
    return McpConfig(servers=servers)


def _parse_mcp_server(name: str, raw: dict[str, Any]) -> McpServerConfig:
    transport = str(raw.get("type", "")).strip().lower()
    if transport == "stdio":
        if "oauth" in raw:
            raise ConfigError(f"mcp_servers.{name}.oauth 仅支持 http Server")
        command = _required_str(raw.get("command"), f"mcp_servers.{name}.command")
        return McpServerConfig(
            name=name,
            transport="stdio",
            command=command,
            args=_parse_string_sequence(raw.get("args"), f"mcp_servers.{name}.args"),
            env=_parse_string_map(raw.get("env", {}), f"mcp_servers.{name}.env", expand=True),
        )
    if transport == "http":
        url = _expand_env_interpolations(
            _required_str(raw.get("url"), f"mcp_servers.{name}.url"),
            f"mcp_servers.{name}.url",
        )
        if not url.strip():
            raise ConfigError(f"mcp_servers.{name}.url 不能为空")
        headers = _parse_string_map(raw.get("headers", {}), f"mcp_servers.{name}.headers", expand=True)
        oauth = _parse_mcp_oauth(raw.get("oauth"), f"mcp_servers.{name}.oauth")
        if oauth is not None and oauth.enabled and any(key.casefold() == "authorization" for key in headers):
            raise ConfigError(f"mcp_servers.{name} 启用 OAuth 时不能同时配置 Authorization Header")
        return McpServerConfig(
            name=name,
            transport="http",
            url=url,
            headers=headers,
            oauth=oauth,
        )
    raise ConfigError(f"mcp_servers.{name}.type 必须是 stdio 或 http")


def _parse_mcp_oauth(value: Any, field: str) -> McpOAuthConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError(f"{field} 必须是 YAML 对象")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{field}.enabled 必须是布尔值")

    client_id = _expand_optional_env(value.get("client_id"), f"{field}.client_id")
    client_secret = _expand_optional_env(value.get("client_secret"), f"{field}.client_secret")
    if client_secret is not None and client_id is None:
        raise ConfigError(f"{field}.client_secret 需要同时配置 client_id")

    scopes = _parse_string_tuple(value.get("scopes"), f"{field}.scopes")
    for scope in scopes:
        if not _valid_oauth_scope(scope):
            raise ConfigError(f"{field}.scopes 包含非法 scope `{scope}`")
    if len(set(scopes)) != len(scopes):
        raise ConfigError(f"{field}.scopes 不能包含重复值")
    return McpOAuthConfig(
        enabled=enabled,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
    )


def _expand_optional_env(value: Any, field: str) -> str | None:
    if value is None:
        return None
    text = _optional_non_empty_str(value, field)
    return _expand_env_interpolations(text, field)


def _valid_oauth_scope(value: str) -> bool:
    # RFC 6749 scope-token：可打印 ASCII，排除空格、双引号和反斜杠。
    return bool(value) and all(
        (code := ord(char)) == 0x21 or 0x23 <= code <= 0x5B or 0x5D <= code <= 0x7E
        for char in value
    )


def _required_str(value: Any, field: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ConfigError(f"{field} 不能为空")
    return text


def _optional_non_empty_str(value: Any, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise ConfigError(f"{field} 不能为空")
    return text


def _parse_string_sequence(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{field} 必须是数组")
    return tuple(str(item) for item in value)


def _parse_string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if not isinstance(value, list):
        raise ConfigError(f"{field} 必须是数组")
    parsed = tuple(str(item).strip() for item in value)
    if any(not item for item in parsed):
        raise ConfigError(f"{field} 不能包含空字符串")
    return parsed


def _parse_string_map(value: Any, field: str, *, expand: bool) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{field} 必须是 YAML 对象")
    parsed: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key:
            raise ConfigError(f"{field} 的 key 不能为空")
        text = str(raw_value)
        parsed[key] = _expand_env_interpolations(text, f"{field}.{key}") if expand else text
    return parsed


def _expand_env_interpolations(value: str, field: str) -> str:
    def replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        resolved = os.environ.get(env_name, "")
        if not resolved:
            raise ConfigError(f"{field} 引用的环境变量 {env_name} 未设置或为空")
        return resolved

    return _ENV_INTERPOLATION_RE.sub(replace, value)


def _parse_int(value: Any, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} 必须是整数") from exc
    if parsed <= 0:
        raise ConfigError(f"{field} 必须大于 0")
    return parsed


def _parse_float(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} 必须是数字") from exc
    if parsed <= 0:
        raise ConfigError(f"{field} 必须大于 0")
    return parsed


def _parse_probability(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{field} 必须是 0 到 1 之间的数字")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} 必须是 0 到 1 之间的数字") from exc
    if not 0.0 <= parsed <= 1.0:
        raise ConfigError(f"{field} 必须在 0 到 1 之间")
    return parsed
