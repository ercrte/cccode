from __future__ import annotations

import re
from pathlib import Path

from mewcode.memory.models import InstructionBlock, InstructionBundle, InstructionScope, SessionMemoryConfig

_INCLUDE_RE = re.compile(r"^\s*@include\s+<([^>]+)>\s*$")


class InstructionLoader:
    def __init__(self, cwd: Path, config: SessionMemoryConfig | None = None) -> None:
        self.cwd = cwd.resolve()
        self.config = config or SessionMemoryConfig()
        self.user_dir = Path(self.config.user_dir).expanduser().resolve()

    def load(self) -> InstructionBundle:
        blocks: list[InstructionBlock] = []
        warnings: list[str] = []
        for scope, priority, path in self._instruction_paths():
            if not path.exists():
                continue
            boundary = self.cwd if scope != "user" else self.user_dir
            content, block_warnings = self._read_with_includes(
                path=path,
                boundary=boundary,
                visited=frozenset(),
                depth=0,
            )
            warnings.extend(block_warnings)
            if content.strip():
                blocks.append(
                    InstructionBlock(
                        scope=scope,
                        priority=priority,
                        source_path=path,
                        content=content,
                    )
                )
        blocks.sort(key=lambda item: item.priority)
        return InstructionBundle(blocks=tuple(blocks), warnings=tuple(warnings))

    def _instruction_paths(self) -> tuple[tuple[InstructionScope, int, Path], ...]:
        filename = self.config.instruction_filename
        return (
            ("project_private", 0, (self.cwd / self.config.project_dir / filename).resolve()),
            ("project_root", 1, (self.cwd / filename).resolve()),
            ("user", 2, (self.user_dir / filename).resolve()),
        )

    def _read_with_includes(
        self,
        *,
        path: Path,
        boundary: Path,
        visited: frozenset[Path],
        depth: int,
    ) -> tuple[str, list[str]]:
        resolved = path.resolve()
        warnings: list[str] = []
        if resolved in visited:
            return "", [f"指令 include 出现循环引用，已跳过: {resolved}"]
        if not self._is_under(resolved, boundary):
            return "", [f"指令 include 越界，已跳过: {resolved}"]
        try:
            raw = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            return "", [f"无法读取指令文件 {resolved}: {exc}"]

        parts: list[str] = []
        next_visited = frozenset((*visited, resolved))
        for line in raw.splitlines():
            match = _INCLUDE_RE.match(line)
            if match is None:
                parts.append(line)
                continue
            if depth >= self.config.include_max_depth:
                warnings.append(f"指令 include 嵌套过深，已跳过: {line.strip()}")
                continue
            include_path = (resolved.parent / match.group(1)).resolve()
            if not self._is_under(include_path, boundary):
                warnings.append(f"指令 include 越界，已跳过: {include_path}")
                continue
            included, include_warnings = self._read_with_includes(
                path=include_path,
                boundary=boundary,
                visited=next_visited,
                depth=depth + 1,
            )
            warnings.extend(include_warnings)
            if included:
                parts.append(included)
        return "\n".join(parts), warnings

    def _is_under(self, path: Path, boundary: Path) -> bool:
        try:
            path.resolve().relative_to(boundary.resolve())
            return True
        except ValueError:
            return False
