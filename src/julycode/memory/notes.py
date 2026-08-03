from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from julycode.errors import redact_secret
from julycode.memory.models import MemoryCategory, MemoryNote, MemoryScope, SessionMemoryConfig

_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*\S{6,}")


class MemoryNoteStore:
    def __init__(
        self,
        cwd: Path,
        config: SessionMemoryConfig | None = None,
        *,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.cwd = cwd.resolve()
        self.config = config or SessionMemoryConfig()
        self.secrets = secrets
        self.project_root = (self.cwd / self.config.project_dir / self.config.memory_dir).resolve()
        self.user_root = (Path(self.config.user_dir).expanduser() / self.config.memory_dir).resolve()

    def list_notes(self, scope: MemoryScope) -> tuple[MemoryNote, ...]:
        root = self._root(scope)
        if not root.exists():
            return ()
        notes: list[MemoryNote] = []
        for path in sorted(root.glob("*/*.md")):
            if path.name == "index.md":
                continue
            note = self._read_path(scope, path)
            if note is not None:
                notes.append(note)
        return tuple(notes)

    def read_note(self, scope: MemoryScope, note_id: str) -> MemoryNote | None:
        safe_id = _safe_filename(note_id)
        for category in ("preference", "correction", "project_knowledge", "reference"):
            path = self._root(scope) / category / f"{safe_id}.md"
            note = self._read_path(scope, path)
            if note is not None:
                return note
        return None

    def write_note(self, note: MemoryNote) -> Path:
        clean = self._redact_note(note)
        path = self._root(clean.scope) / clean.category / f"{_safe_filename(clean.note_id)}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = {
            "note_id": clean.note_id,
            "scope": clean.scope,
            "category": clean.category,
            "title": clean.title,
            "source_session_id": str(clean.source_session_id),
            "created_at": clean.created_at,
            "updated_at": clean.updated_at,
            "tags": list(clean.tags),
            "source_evidence": list(clean.source_evidence),
            "critical": clean.critical,
            "confidence": clean.confidence,
        }
        text = "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=True) + "---\n" + clean.body + "\n"
        path.write_text(text, encoding="utf-8")
        return path

    def delete_note(self, scope: MemoryScope, note_id: str) -> bool:
        safe_id = _safe_filename(note_id)
        deleted = False
        for category in ("preference", "correction", "project_knowledge", "reference"):
            path = self._root(scope) / category / f"{safe_id}.md"
            if path.exists():
                path.unlink()
                deleted = True
        return deleted

    def contains_sensitive(self, text: str) -> bool:
        if any(secret and secret in text for secret in self.secrets):
            return True
        if redact_secret(text) != text:
            return True
        return bool(_BEARER_RE.search(text) or _PRIVATE_KEY_RE.search(text) or _PASSWORD_RE.search(text))

    def _read_path(self, scope: MemoryScope, path: Path) -> MemoryNote | None:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---\n"):
            return None
        try:
            _head, meta_text, body = raw.split("---\n", 2)
            meta = yaml.safe_load(meta_text) or {}
        except (ValueError, yaml.YAMLError):
            return None
        if not isinstance(meta, dict):
            return None
        category = str(meta.get("category", ""))
        if category not in {"preference", "correction", "project_knowledge", "reference"}:
            return None
        raw_tags = meta.get("tags", ())
        tags = tuple(str(item) for item in raw_tags) if isinstance(raw_tags, list) else ()
        raw_evidence = meta.get("source_evidence", ())
        evidence = tuple(str(item) for item in raw_evidence) if isinstance(raw_evidence, list) else ()
        raw_confidence = meta.get("confidence")
        try:
            confidence = None if raw_confidence is None else float(raw_confidence)
        except (TypeError, ValueError):
            confidence = None
        return MemoryNote(
            note_id=str(meta.get("note_id", path.stem)),
            scope=scope,
            category=category,  # type: ignore[arg-type]
            title=str(meta.get("title", "")),
            body=body.strip(),
            source_session_id=str(meta.get("source_session_id", "")),  # type: ignore[arg-type]
            created_at=str(meta.get("created_at", "")),
            updated_at=str(meta.get("updated_at", "")),
            tags=tags,
            source_evidence=evidence,
            critical=bool(meta.get("critical", False)),
            confidence=confidence,
        )

    def _root(self, scope: MemoryScope) -> Path:
        return self.user_root if scope == "user" else self.project_root

    def _redact_note(self, note: MemoryNote) -> MemoryNote:
        title = self._redact(note.title)
        body = self._redact(note.body)
        tags = tuple(self._redact(tag) for tag in note.tags)
        evidence = tuple(self._redact(item) for item in note.source_evidence)
        return replace(note, title=title, body=body, tags=tags, source_evidence=evidence)

    def _redact(self, text: str) -> str:
        redacted = text
        for secret in self.secrets:
            redacted = redact_secret(redacted, secret)
        redacted = redact_secret(redacted)
        redacted = _BEARER_RE.sub("Bearer [REDACTED]", redacted)
        redacted = _PRIVATE_KEY_RE.sub("-----BEGIN [REDACTED] PRIVATE KEY-----", redacted)
        return _PASSWORD_RE.sub("password=[REDACTED]", redacted)


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return safe or "note"
