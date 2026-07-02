from __future__ import annotations

from pathlib import Path

from mewcode.memory.models import MemoryIndex, MemoryNote, MemoryScope, SessionMemoryConfig
from mewcode.memory.notes import MemoryNoteStore

_CATEGORY_TITLES = {
    "preference": "用户偏好",
    "correction": "纠正反馈",
    "project_knowledge": "项目知识",
    "reference": "参考资料",
}
_CATEGORY_ORDER = ("preference", "correction", "project_knowledge", "reference")


class MemoryIndexBuilder:
    def __init__(
        self,
        store: MemoryNoteStore,
        config: SessionMemoryConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or store.config

    def build(self, scope: MemoryScope) -> MemoryIndex:
        notes = self.store.list_notes(scope)
        lines = [f"# MewCode {scope} Memory Index", ""]
        for category in _CATEGORY_ORDER:
            grouped = [note for note in notes if note.category == category]
            if not grouped:
                continue
            lines.append(f"## {_CATEGORY_TITLES[category]}")
            for note in sorted(grouped, key=lambda item: item.updated_at, reverse=True):
                lines.append(f"- {note.title}: {note.body}")
            lines.append("")
        content = "\n".join(lines).strip() + "\n"
        content, warnings = self._limit(content)
        path = self._index_path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return MemoryIndex(
            scope=scope,
            path=path,
            content=content,
            line_count=len(content.splitlines()),
            byte_count=len(content.encode("utf-8")),
            warnings=warnings,
        )

    def read_index(self, scope: MemoryScope) -> MemoryIndex | None:
        path = self._index_path(scope)
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8")
        return MemoryIndex(
            scope=scope,
            path=path,
            content=content,
            line_count=len(content.splitlines()),
            byte_count=len(content.encode("utf-8")),
        )

    def _index_path(self, scope: MemoryScope) -> Path:
        return self.store.user_root / "index.md" if scope == "user" else self.store.project_root / "index.md"

    def _limit(self, content: str) -> tuple[str, tuple[str, ...]]:
        max_lines = self.config.index_max_lines
        max_bytes = self.config.index_max_bytes
        if len(content.splitlines()) <= max_lines and len(content.encode("utf-8")) <= max_bytes:
            return content, ()

        kept: list[str] = []
        for line in content.splitlines():
            candidate = "\n".join([*kept, line]).strip() + "\n"
            if len(candidate.splitlines()) > max_lines:
                break
            if len(candidate.encode("utf-8")) > max_bytes:
                break
            kept.append(line)
        limited = "\n".join(kept).strip() + "\n"
        return limited, ("记忆索引超过上限，已裁剪。",)
