from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Sequence

from julycode.memory.models import (
    MemoryCandidate,
    MemoryExtractionResult,
    MemoryNote,
    MemoryRejection,
    MemoryUpdateJob,
    SessionMemoryConfig,
    ValidatedMemoryOperation,
)
from julycode.memory.notes import MemoryNoteStore


class MemoryExtractionError(ValueError):
    """模型提取结果无法解析。"""


_CRITICAL_MARKERS = (
    "以后",
    "今后",
    "始终",
    "总是",
    "每次",
    "默认",
    "必须",
    "禁止",
    "不要再",
    "一律",
    "不再",
    "请记住",
    "长期记住",
    "永久记住",
    "from now on",
    "always",
    "never",
    "by default",
    "must",
    "do not",
    "don't",
    "remember that",
    "remember permanently",
    "permanently",
    "again",
    "anymore",
    "no longer",
)
_SPACE_RE = re.compile(r"\s+")


def parse_memory_candidates(text: str) -> tuple[MemoryCandidate, ...]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise MemoryExtractionError(f"自动记忆结果不是合法 JSON: {exc.msg}") from exc
    operations = parsed.get("operations") if isinstance(parsed, dict) else parsed
    if not isinstance(operations, list):
        raise MemoryExtractionError("自动记忆结果缺少 operations 数组")
    if not all(isinstance(operation, dict) for operation in operations):
        raise MemoryExtractionError("自动记忆 operations 中存在非法项")
    return tuple(_candidate_from_dict(operation) for operation in operations)


def _candidate_from_dict(operation: dict[str, Any]) -> MemoryCandidate:
    action = operation.get("action")
    errors: list[str] = []
    if not isinstance(action, str):
        errors.append("action 必须是字符串")
    if action != "skip":
        for field in ("scope", "category", "note_id", "title", "body", "durability"):
            if not isinstance(operation.get(field), str):
                errors.append(f"{field} 必须是字符串")
        for field in ("evidence", "tags", "supersedes"):
            value = operation.get(field)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                errors.append(f"{field} 必须是字符串数组")
        if not isinstance(operation.get("critical"), bool):
            errors.append("critical 必须是布尔值")
        confidence = operation.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            errors.append("confidence 必须是数字")
    return MemoryCandidate(
        action=_string(action),  # type: ignore[arg-type]
        scope=_string(operation.get("scope")),
        category=_string(operation.get("category")),
        note_id=_string(operation.get("note_id")),
        title=_string(operation.get("title")),
        body=_string(operation.get("body")),
        evidence=_string_tuple(operation.get("evidence")),
        durability=_string(operation.get("durability")),
        critical=operation.get("critical") if isinstance(operation.get("critical"), bool) else False,
        confidence=_float(operation.get("confidence")),
        tags=_string_tuple(operation.get("tags", ())),
        supersedes=_string_tuple(operation.get("supersedes", ())),
        schema_errors=tuple(errors),
    )


class MemoryCandidateValidator:
    def __init__(
        self,
        note_store: MemoryNoteStore,
        config: SessionMemoryConfig | None = None,
        *,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.note_store = note_store
        self.config = config or note_store.config
        self.secrets = tuple(item for item in secrets if item)

    def validate(
        self,
        candidates: Sequence[MemoryCandidate],
        job: MemoryUpdateJob,
    ) -> MemoryExtractionResult:
        user_messages = tuple(
            message.content
            for message in job.turn_messages
            if message.role == "user" and message.content
        )
        accepted: list[ValidatedMemoryOperation] = []
        rejected: list[MemoryRejection] = []
        batch_fingerprints: set[str] = set()
        batch_ids: set[tuple[str, str]] = set()
        for candidate in candidates:
            if candidate.action == "skip":
                continue
            outcome = self._validate_one(
                candidate,
                job,
                user_messages=user_messages,
                batch_fingerprints=batch_fingerprints,
                batch_ids=batch_ids,
            )
            if isinstance(outcome, MemoryRejection):
                rejected.append(outcome)
                continue
            accepted.append(outcome)
            batch_fingerprints.add(_note_fingerprint(outcome.note))
            batch_ids.add((outcome.note.scope, outcome.note.note_id))
        return MemoryExtractionResult(accepted=tuple(accepted), rejected=tuple(rejected))

    def _validate_one(
        self,
        candidate: MemoryCandidate,
        job: MemoryUpdateJob,
        *,
        user_messages: tuple[str, ...],
        batch_fingerprints: set[str],
        batch_ids: set[tuple[str, str]],
    ) -> ValidatedMemoryOperation | MemoryRejection:
        if candidate.schema_errors:
            return _reject(candidate, "invalid_schema", "；".join(candidate.schema_errors))
        if candidate.action not in {"create", "update"}:
            return _reject(candidate, "invalid_schema", f"不支持的自动记忆操作: {candidate.action}")
        if candidate.scope not in {"user", "project"}:
            return _reject(candidate, "invalid_schema", f"不支持的记忆 scope: {candidate.scope}")
        if candidate.category not in {"preference", "correction", "project_knowledge", "reference"}:
            return _reject(candidate, "invalid_schema", f"不支持的记忆 category: {candidate.category}")
        if not candidate.note_id or not candidate.title or not candidate.body:
            return _reject(candidate, "invalid_schema", "自动记忆操作缺少 note_id、title 或 body")
        if not 0.0 <= candidate.confidence <= 1.0:
            return _reject(candidate, "invalid_schema", "自动记忆 confidence 必须在 0 到 1 之间")
        if candidate.durability != "persistent":
            return _reject(candidate, "not_persistent", "候选不是明确的长期记忆")
        normalized_messages = tuple(_normalize(m) for m in user_messages)
        if not candidate.evidence or any(
            not evidence or not any(_normalize(evidence) in norm_msg for norm_msg in normalized_messages)
            for evidence in candidate.evidence
        ):
            return _reject(candidate, "missing_user_evidence", "来源证据必须逐字来自当前轮用户消息")
        sensitive_text = "\n".join((candidate.title, candidate.body, *candidate.evidence))
        if self.note_store.contains_sensitive(sensitive_text) or any(secret in sensitive_text for secret in self.secrets):
            return _reject(candidate, "sensitive_content", "候选包含敏感信息，不得写入长期记忆")
        if candidate.critical:
            if candidate.category not in {"preference", "correction", "project_knowledge"}:
                return _reject(candidate, "critical_not_explicit", "关键偏好只能属于 preference、correction 或 project_knowledge")
            normalized_evidence = " ".join(_normalize(item) for item in candidate.evidence)
            if not any(marker in normalized_evidence for marker in _CRITICAL_MARKERS):
                return _reject(candidate, "critical_not_explicit", "关键偏好缺少明确长期或强约束标记")
            if candidate.confidence < self.config.critical_preference_min_confidence:
                return _reject(candidate, "critical_low_confidence", "关键偏好置信度低于配置阈值")

        scope = candidate.scope  # 已完成枚举校验
        existing = self.note_store.read_note(scope, candidate.note_id)  # type: ignore[arg-type]
        if candidate.action == "update":
            if existing is None or existing.category != candidate.category:
                return _reject(candidate, "invalid_update", "update 必须指向同 scope、同 category 的既有笔记")
        elif existing is not None:
            return _reject(candidate, "invalid_update", "create 的 note_id 已存在，应使用 update 或 skip")
        if (scope, candidate.note_id) in batch_ids:
            return _reject(candidate, "duplicate", "同一批次包含重复 note_id")

        superseded: list[str] = []
        for note_id in candidate.supersedes:
            if note_id == candidate.note_id or note_id in superseded:
                return _reject(candidate, "invalid_supersedes", "supersedes 不能引用自身或包含重复 ID")
            target = self.note_store.read_note(scope, note_id)  # type: ignore[arg-type]
            if target is None:
                return _reject(candidate, "invalid_supersedes", f"被替代笔记不存在: {note_id}")
            superseded.append(note_id)

        now = _now_iso()
        note = MemoryNote(
            note_id=candidate.note_id,
            scope=scope,  # type: ignore[arg-type]
            category=candidate.category,  # type: ignore[arg-type]
            title=candidate.title,
            body=candidate.body,
            source_session_id=job.session_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
            tags=candidate.tags,
            source_evidence=candidate.evidence,
            critical=candidate.critical,
            confidence=candidate.confidence,
        )
        fingerprint = _note_fingerprint(note)
        existing_fingerprints = {
            _note_fingerprint(item)
            for item in self.note_store.list_notes(note.scope)
            if existing is None or item.note_id != existing.note_id
        }
        if fingerprint in batch_fingerprints or fingerprint in existing_fingerprints:
            return _reject(candidate, "duplicate", "候选与已有长期记忆重复")
        return ValidatedMemoryOperation(
            action=candidate.action,  # type: ignore[arg-type]
            note=note,
            supersedes=tuple(superseded),
        )


def _reject(candidate: MemoryCandidate, code: str, message: str) -> MemoryRejection:
    return MemoryRejection(candidate=candidate, code=code, message=message)


def _note_fingerprint(note: MemoryNote) -> str:
    return "|".join((_normalize(note.scope), _normalize(note.category), _normalize(note.body)))


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.category(char).startswith("P"))
    return _SPACE_RE.sub(" ", normalized).strip()


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        return ()
    return tuple(item.strip() for item in value if item.strip())


def _float(value: Any) -> float:
    if isinstance(value, bool):
        return -1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
