from __future__ import annotations

import re
import zlib
from importlib import resources
from importlib.abc import Traversable
from pathlib import Path
from typing import Any

import yaml

from julycode.subagents.models import (
    SubAgentRoleCatalog,
    SubAgentRoleDefinition,
    SubAgentRoleFingerprint,
    SubAgentRoleFrontmatter,
    SubAgentRoleRoots,
    SubAgentRoleSource,
    SubAgentRoleWarning,
)

_ROLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_MODEL_ALIASES = {"inherit", "haiku", "sonnet", "opus"}
_PERMISSION_MODES = {"inherit", "strict", "default", "permissive"}


class SubAgentRoleParseError(ValueError):
    pass


def default_sub_agent_roots(
    cwd: Path | None = None,
    plugin_roots: tuple[str, ...] = (),
) -> SubAgentRoleRoots:
    root = cwd or Path.cwd()
    plugins = tuple(Path(path).expanduser() for path in plugin_roots)
    return SubAgentRoleRoots(
        project=root / ".julycode" / "agents",
        user=Path.home() / ".julycode" / "agents",
        builtin=resources.files("julycode.subagents.builtin"),
        plugins=plugins,
    )


class SubAgentRoleLoader:
    def __init__(self, roots: SubAgentRoleRoots) -> None:
        self.roots = roots

    def discover(self) -> SubAgentRoleCatalog:
        warnings: list[SubAgentRoleWarning] = []
        definitions: dict[str, SubAgentRoleDefinition] = {}
        seen_scope_names: set[tuple[SubAgentRoleSource, str]] = set()

        ordered_roots: list[tuple[SubAgentRoleSource, Path | Traversable]] = [
            ("project", self.roots.project),
            ("user", self.roots.user),
            ("builtin", self.roots.builtin),
            *[("plugin", root) for root in self.roots.plugins],
        ]
        for scope, root in ordered_roots:
            for source in self._candidate_sources(root):
                try:
                    definition = self._parse_source(source, scope)
                except SubAgentRoleParseError as exc:
                    warnings.append(SubAgentRoleWarning(message=str(exc), source_path=_source_label(source)))
                    continue
                scope_key = (scope, definition.name)
                if scope_key in seen_scope_names:
                    warnings.append(
                        SubAgentRoleWarning(
                            message=f"同一层级重复定义子 Agent 角色 `{definition.name}`，已跳过后出现的定义。",
                            source_path=definition.source_path,
                        )
                    )
                    continue
                seen_scope_names.add(scope_key)
                if definition.name not in definitions:
                    definitions[definition.name] = definition

        return SubAgentRoleCatalog(
            definitions=definitions,
            warnings=tuple(warnings),
            fingerprint=self._fingerprint(),
        )

    def _candidate_sources(self, root: Path | Traversable) -> list[Path | Traversable]:
        if isinstance(root, Path):
            if not root.exists():
                return []
            return sorted(path for path in root.glob("*.md") if path.is_file())
        if not root.is_dir():
            return []
        return sorted((child for child in root.iterdir() if child.is_file() and child.name.endswith(".md")), key=lambda item: item.name)

    def _parse_source(self, source: Path | Traversable, scope: SubAgentRoleSource) -> SubAgentRoleDefinition:
        body, frontmatter = self._parse_markdown(source)
        return SubAgentRoleDefinition(
            frontmatter=frontmatter,
            body=body,
            source_scope=scope,
            source_path=_source_label(source),
        )

    def _parse_markdown(self, source: Path | Traversable) -> tuple[str, SubAgentRoleFrontmatter]:
        text = _read_text(source)
        frontmatter_text, body = _split_frontmatter(text)
        try:
            raw = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as exc:
            raise SubAgentRoleParseError(f"frontmatter 不是合法 YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise SubAgentRoleParseError("frontmatter 必须是 YAML 对象")
        frontmatter = _parse_frontmatter(raw)
        if not body.strip():
            raise SubAgentRoleParseError("角色正文不能为空")
        return body.strip(), frontmatter

    def _fingerprint(self) -> SubAgentRoleFingerprint:
        entries: list[tuple[str, int, int]] = []
        for root in (self.roots.project, self.roots.user, self.roots.builtin, *self.roots.plugins):
            entries.extend(_fingerprint_entries(root))
        return SubAgentRoleFingerprint(entries=tuple(sorted(entries)))


def _parse_frontmatter(raw: dict[str, Any]) -> SubAgentRoleFrontmatter:
    name = _required_text(raw, "name")
    if not _ROLE_NAME_RE.match(name):
        raise SubAgentRoleParseError("frontmatter.name 必须以字母开头，只能包含字母、数字、下划线和连字符")

    description = _required_text(raw, "description")
    tools_allow = _string_tuple(raw, "tools_allow", fallback_key="allow_tools")
    tools_deny = _string_tuple(raw, "tools_deny", fallback_key="deny_tools", default=())
    model = str(raw.get("model", "inherit")).strip() or "inherit"
    if model in _MODEL_ALIASES:
        pass
    elif not model:
        raise SubAgentRoleParseError("frontmatter.model 不能为空")
    permission_mode = str(raw.get("permission_mode", raw.get("permissions", "inherit"))).strip() or "inherit"
    if permission_mode not in _PERMISSION_MODES:
        raise SubAgentRoleParseError("frontmatter.permission_mode 必须是 inherit、strict、default 或 permissive")

    isolation = "shared"
    if "isolation" in raw:
        raw_isolation = raw["isolation"]
        if not isinstance(raw_isolation, str) or raw_isolation.strip() != "worktree":
            raise SubAgentRoleParseError("frontmatter.isolation 只允许声明为 worktree；省略时使用共享目录")
        isolation = "worktree"

    max_iterations = raw.get("max_iterations")
    parsed_max_iterations: int | None = None
    if max_iterations is not None:
        try:
            parsed_max_iterations = int(max_iterations)
        except (TypeError, ValueError) as exc:
            raise SubAgentRoleParseError("frontmatter.max_iterations 必须是正整数") from exc
        if parsed_max_iterations <= 0:
            raise SubAgentRoleParseError("frontmatter.max_iterations 必须是正整数")

    return SubAgentRoleFrontmatter(
        name=name,
        description=description,
        tools_allow=tools_allow,
        tools_deny=tools_deny,
        model=model,
        max_iterations=parsed_max_iterations,
        permission_mode=permission_mode,  # type: ignore[arg-type]
        isolation=isolation,  # type: ignore[arg-type]
    )


def _string_tuple(
    raw: dict[str, Any],
    key: str,
    *,
    fallback_key: str | None = None,
    default: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    value = raw.get(key)
    if value is None and fallback_key is not None:
        value = raw.get(fallback_key)
    if value is None:
        if default is not None:
            return default
        raise SubAgentRoleParseError(f"frontmatter.{key} 必须是字符串数组")
    if not isinstance(value, list) or any(not str(item).strip() for item in value):
        raise SubAgentRoleParseError(f"frontmatter.{key} 必须是字符串数组")
    return tuple(str(item).strip() for item in value)


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SubAgentRoleParseError("角色 Markdown 必须以 YAML frontmatter 开头")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise SubAgentRoleParseError("角色 Markdown 缺少 frontmatter 结束标记")


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = str(raw.get(key, "")).strip()
    if not value:
        raise SubAgentRoleParseError(f"frontmatter.{key} 不能为空")
    return value


def _read_text(source: Path | Traversable) -> str:
    try:
        return source.read_text(encoding="utf-8")  # type: ignore[arg-type]
    except OSError as exc:
        raise SubAgentRoleParseError(f"无法读取角色文件: {exc}") from exc


def _source_label(source: Path | Traversable) -> str:
    return str(source)


def _fingerprint_entries(root: Path | Traversable) -> list[tuple[str, int, int]]:
    if isinstance(root, Path):
        if not root.exists():
            return []
        entries: list[tuple[str, int, int]] = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((str(path), int(stat.st_mtime_ns), int(stat.st_size)))
        return entries

    if not root.is_dir():
        return []
    entries = []
    for item in _walk_traversable(root):
        if not item.is_file():
            continue
        data = item.read_bytes()
        entries.append((str(item), zlib.crc32(data), len(data)))
    return entries


def _walk_traversable(root: Traversable) -> list[Traversable]:
    items: list[Traversable] = []
    for child in root.iterdir():
        items.append(child)
        if child.is_dir():
            items.extend(_walk_traversable(child))
    return items
