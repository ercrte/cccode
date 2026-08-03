from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileReadCacheEntry:
    path: Path
    mtime_ns: int
    size: int
    content: str


class FileReadCache:
    def __init__(self) -> None:
        self._entries: dict[Path, FileReadCacheEntry] = {}

    def get(self, path: Path) -> str | None:
        resolved = path.resolve()
        try:
            stat = resolved.stat()
        except OSError:
            self._entries.pop(resolved, None)
            return None
        entry = self._entries.get(resolved)
        if entry is None:
            return None
        if entry.mtime_ns != stat.st_mtime_ns or entry.size != stat.st_size:
            self._entries.pop(resolved, None)
            return None
        return entry.content

    def put(self, path: Path, content: str) -> None:
        resolved = path.resolve()
        stat = resolved.stat()
        self._entries[resolved] = FileReadCacheEntry(
            path=resolved,
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            content=content,
        )
