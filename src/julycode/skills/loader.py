from __future__ import annotations

import re
import zlib
from importlib import resources
from importlib.abc import Traversable
from pathlib import Path
from typing import Any

import yaml

from julycode.skills.models import (
    SkillCatalog,
    SkillDefinition,
    SkillFingerprint,
    SkillFrontmatter,
    SkillRoots,
    SkillSourceScope,
    SkillToolDefinition,
    SkillWarning,
)

_SKILL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_LOCAL_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class SkillParseError(ValueError):
    pass


def default_skill_roots(cwd: Path | None = None) -> SkillRoots:
    root = cwd or Path.cwd()
    return SkillRoots(
        project=root / ".julycode" / "skills",
        user=Path.home() / ".julycode" / "skills",
        builtin=resources.files("julycode.skills.builtin"),
    )


class SkillLoader:
    def __init__(self, roots: SkillRoots) -> None:
        self.roots = roots

    def discover(self) -> SkillCatalog:
        warnings: list[SkillWarning] = []
        definitions: dict[str, SkillDefinition] = {}
        seen_scope_names: set[tuple[SkillSourceScope, str]] = set()

        for scope, root in (
            ("project", self.roots.project),
            ("user", self.roots.user),
            ("builtin", self.roots.builtin),
        ):
            for source in self._candidate_sources(root):
                try:
                    definition = self._parse_source(source, scope)  # type: ignore[arg-type]
                except SkillParseError as exc:
                    warnings.append(SkillWarning(message=str(exc), source_path=_source_label(source)))
                    continue
                scope_key = (scope, definition.name)
                if scope_key in seen_scope_names:
                    warnings.append(
                        SkillWarning(
                            message=f"同一层级重复定义 Skill `{definition.name}`，已跳过后出现的定义。",
                            source_path=definition.source_path,
                        )
                    )
                    continue
                seen_scope_names.add(scope_key)
                if definition.name not in definitions:
                    definitions[definition.name] = definition

        return SkillCatalog(
            definitions=definitions,
            warnings=tuple(warnings),
            fingerprint=self._fingerprint(),
        )

    def _candidate_sources(self, root: Path | Traversable) -> list[Path | Traversable]:
        if isinstance(root, Path):
            if not root.exists():
                return []
            sources: list[Path | Traversable] = []
            sources.extend(sorted(path for path in root.glob("*.md") if path.is_file()))
            sources.extend(sorted(path for path in root.iterdir() if path.is_dir() and (path / "skill.md").is_file()))
            return sources

        if not root.is_dir():
            return []
        sources = []
        children = sorted(root.iterdir(), key=lambda item: item.name)
        sources.extend(child for child in children if child.is_file() and child.name.endswith(".md"))
        sources.extend(child for child in children if child.is_dir() and (child / "skill.md").is_file())
        return sources

    def _parse_source(self, source: Path | Traversable, scope: SkillSourceScope) -> SkillDefinition:
        if _is_directory_source(source):
            entry = source / "skill.md"  # type: ignore[operator]
            body, frontmatter = self._parse_markdown(entry)
            package_dir = _as_path(source)
            tool_definitions = self._parse_directory_tools(source, frontmatter.name)
            return SkillDefinition(
                frontmatter=frontmatter,
                body=body,
                source_scope=scope,
                source_path=_source_label(entry),
                package_dir=package_dir,
                directory_skill=True,
                tool_definitions=tool_definitions,
            )

        body, frontmatter = self._parse_markdown(source)
        return SkillDefinition(
            frontmatter=frontmatter,
            body=body,
            source_scope=scope,
            source_path=_source_label(source),
            package_dir=_as_path(source).parent if isinstance(source, Path) else None,
            directory_skill=False,
        )

    def _parse_markdown(self, source: Path | Traversable) -> tuple[str, SkillFrontmatter]:
        text = _read_text(source)
        frontmatter_text, body = _split_frontmatter(text)
        try:
            raw = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as exc:
            raise SkillParseError(f"frontmatter 不是合法 YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise SkillParseError("frontmatter 必须是 YAML 对象")
        frontmatter = _parse_frontmatter(raw)
        if not body.strip():
            raise SkillParseError("Skill 正文不能为空")
        return body.strip(), frontmatter

    def _parse_directory_tools(
        self,
        source: Path | Traversable,
        skill_name: str,
    ) -> tuple[SkillToolDefinition, ...]:
        tools_dir = source / "tools"  # type: ignore[operator]
        if not tools_dir.is_dir():
            return ()

        package_dir = _as_path(source)
        definitions: list[SkillToolDefinition] = []
        seen: set[str] = set()
        for item in sorted(tools_dir.iterdir(), key=lambda child: child.name):
            if not item.is_file() or not (item.name.endswith(".yaml") or item.name.endswith(".yml")):
                continue
            try:
                raw = yaml.safe_load(_read_text(item))
            except yaml.YAMLError as exc:
                raise SkillParseError(f"专属工具 `{item.name}` 不是合法 YAML: {exc}") from exc
            if not isinstance(raw, dict):
                raise SkillParseError(f"专属工具 `{item.name}` 顶层必须是 YAML 对象")
            definition = _parse_tool_definition(raw, item, skill_name, package_dir)
            if definition.local_name in seen:
                raise SkillParseError(f"专属工具 `{definition.local_name}` 重复定义")
            seen.add(definition.local_name)
            definitions.append(definition)
        return tuple(definitions)

    def _fingerprint(self) -> SkillFingerprint:
        entries: list[tuple[str, int, int]] = []
        for root in (self.roots.project, self.roots.user, self.roots.builtin):
            entries.extend(_fingerprint_entries(root))
        return SkillFingerprint(entries=tuple(sorted(entries)))


def _parse_frontmatter(raw: dict[str, Any]) -> SkillFrontmatter:
    name = _required_text(raw, "name")
    if not _SKILL_NAME_RE.match(name):
        raise SkillParseError("frontmatter.name 必须以字母开头，只能包含字母、数字、下划线和连字符")

    description = _required_text(raw, "description")
    tools_raw = raw.get("tools")
    if not isinstance(tools_raw, list) or any(not str(item).strip() for item in tools_raw):
        raise SkillParseError("frontmatter.tools 必须是字符串数组")
    tools = tuple(str(item).strip() for item in tools_raw)

    mode = _required_text(raw, "mode")
    if mode not in {"shared", "isolated"}:
        raise SkillParseError("frontmatter.mode 必须是 shared 或 isolated")

    try:
        history = int(raw["history"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SkillParseError("frontmatter.history 必须是非负整数") from exc
    if history < 0:
        raise SkillParseError("frontmatter.history 必须是非负整数")

    model = raw.get("model")
    model_text = str(model).strip() if model is not None else None
    if model_text == "":
        model_text = None

    return SkillFrontmatter(
        name=name,
        description=description,
        tools=tools,
        mode=mode,  # type: ignore[arg-type]
        history=history,
        model=model_text,
    )


def _parse_tool_definition(
    raw: dict[str, Any],
    source: Path | Traversable,
    skill_name: str,
    package_dir: Path,
) -> SkillToolDefinition:
    local_name = _required_text(raw, "name")
    if not _LOCAL_TOOL_NAME_RE.match(local_name):
        raise SkillParseError(f"专属工具 `{local_name}` 名字必须以字母开头，只能包含字母、数字和下划线")
    script = _required_text(raw, "script")
    script_path = (package_dir / script).resolve()
    try:
        script_path.relative_to(package_dir.resolve())
    except ValueError as exc:
        raise SkillParseError(f"专属工具 `{local_name}` 的 script 越过 Skill 目录边界") from exc
    if not script_path.is_file():
        raise SkillParseError(f"专属工具 `{local_name}` 的 script 文件不存在: {script}")

    description = _required_text(raw, "description")
    parameters = raw.get("parameters", raw.get("parameters_schema"))
    if not isinstance(parameters, dict):
        raise SkillParseError(f"专属工具 `{local_name}` 的 parameters 必须是 JSON Schema 对象")

    safety = str(raw.get("safety", "side_effect")).strip()
    if safety not in {"read_only", "side_effect"}:
        raise SkillParseError(f"专属工具 `{local_name}` 的 safety 必须是 read_only 或 side_effect")
    try:
        timeout_seconds = float(raw.get("timeout_seconds", 10.0))
    except (TypeError, ValueError) as exc:
        raise SkillParseError(f"专属工具 `{local_name}` 的 timeout_seconds 必须是数字") from exc
    if timeout_seconds <= 0:
        raise SkillParseError(f"专属工具 `{local_name}` 的 timeout_seconds 必须大于 0")

    skill_label = skill_name.replace("-", "_")
    return SkillToolDefinition(
        local_name=local_name,
        global_name=f"skill_{skill_label}__{local_name}",
        description=description,
        parameters_schema=dict(parameters),
        script_path=script_path,
        safety=safety,  # type: ignore[arg-type]
        timeout_seconds=timeout_seconds,
    )


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillParseError("Skill Markdown 必须以 YAML frontmatter 开头")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise SkillParseError("Skill Markdown 缺少 frontmatter 结束标记")


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = str(raw.get(key, "")).strip()
    if not value:
        raise SkillParseError(f"frontmatter.{key} 不能为空")
    return value


def _read_text(source: Path | Traversable) -> str:
    try:
        return source.read_text(encoding="utf-8")  # type: ignore[arg-type]
    except OSError as exc:
        raise SkillParseError(f"无法读取 Skill 文件: {exc}") from exc


def _as_path(source: Path | Traversable) -> Path:
    if isinstance(source, Path):
        return source
    try:
        return Path(str(source))
    except TypeError:
        raise SkillParseError("目录型 Skill 必须来自本地文件系统")


def _is_directory_source(source: Path | Traversable) -> bool:
    return source.is_dir()


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
