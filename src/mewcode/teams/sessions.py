from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from mewcode.context.models import ContextSummary
from mewcode.memory.recovery import SessionHistoryValidator
from mewcode.memory.session_store import message_from_json, message_to_json
from mewcode.providers.base import ChatMessage
from mewcode.session import ChatSession
from mewcode.session_id import new_session_id


@dataclass(frozen=True)
class MemberSessionRestoreReport:
    restored: bool
    skipped_bad_lines: int = 0
    truncated_messages: int = 0
    warnings: tuple[str, ...] = ()


class TeamMemberSessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def create(self) -> ChatSession:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        session = ChatSession()
        session.context_state.session_id = str(new_session_id())
        session.set_recorder(_MemberRecorder(self.path, session.context_state.session_id))
        return session

    def load(self) -> tuple[ChatSession, MemberSessionRestoreReport]:
        if not self.path.exists():
            return self.create(), MemberSessionRestoreReport(restored=False)
        messages: list[ChatMessage] = []
        summary: ContextSummary | None = None
        skipped = 0
        valid_records = 0
        session_id: str | None = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise ValueError("记录必须是对象")
                session_id = str(raw.get("session_id") or session_id or new_session_id())
                if raw.get("kind") == "message":
                    message = raw.get("message")
                    if not isinstance(message, dict):
                        raise ValueError("message 缺失")
                    messages.append(message_from_json(message))
                    valid_records += 1
                elif raw.get("kind") == "checkpoint":
                    raw_messages = raw.get("messages")
                    if not isinstance(raw_messages, list):
                        raise ValueError("checkpoint messages 缺失")
                    messages = [message_from_json(item) for item in raw_messages if isinstance(item, dict)]
                    raw_summary = raw.get("context_summary")
                    if isinstance(raw_summary, dict):
                        summary = ContextSummary(**raw_summary)
                    valid_records += 1
                else:
                    raise ValueError("未知记录")
            except (json.JSONDecodeError, TypeError, ValueError):
                skipped += 1
        validation = SessionHistoryValidator().truncate_to_protocol_safe(messages)
        session = ChatSession(messages=list(validation.messages))
        session.context_state.session_id = session_id or str(new_session_id())
        if summary is not None:
            session.context_state.summary = summary
        session.set_recorder(_MemberRecorder(self.path, session.context_state.session_id))
        warnings = tuple(
            item
            for item in (
                f"恢复时跳过 {skipped} 条坏记录" if skipped else "",
                validation.warning or "",
            )
            if item
        )
        return session, MemberSessionRestoreReport(
            restored=valid_records > 0,
            skipped_bad_lines=skipped,
            truncated_messages=validation.truncated_count,
            warnings=warnings,
        )

    def delivered_message_ids(self, session: ChatSession) -> frozenset[str]:
        identifiers: set[str] = set()
        for message in session.messages:
            metadata = message.metadata or {}
            identifier = metadata.get("team_message_id")
            if isinstance(identifier, str):
                identifiers.add(identifier)
        return frozenset(identifiers)

    def append_external_message(self, session: ChatSession, message_id: str, content: str) -> ChatMessage:
        if message_id in self.delivered_message_ids(session):
            return next(
                message
                for message in session.messages
                if (message.metadata or {}).get("team_message_id") == message_id
            )
        return session.append_user_message(content, metadata={"team_message_id": message_id, "source": "team_mailbox"})


class _MemberRecorder:
    def __init__(self, path: Path, session_id: str) -> None:
        self.path = path
        self.session_id = session_id

    def append_message(self, message: ChatMessage) -> None:
        self._append({"kind": "message", "message": message_to_json(message)})

    def append_checkpoint(self, messages: Sequence[ChatMessage], summary: ContextSummary | None) -> None:
        self._append(
            {
                "kind": "checkpoint",
                "messages": [message_to_json(message) for message in messages],
                "context_summary": _summary_json(summary),
            }
        )

    def _append(self, payload: dict[str, Any]) -> None:
        payload = {"session_id": self.session_id, **payload}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            handle.flush()


def _summary_json(summary: ContextSummary | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "content": summary.content,
        "boundary_notice": summary.boundary_notice,
        "created_at": summary.created_at,
        "source_message_count": summary.source_message_count,
        "kept_message_count": summary.kept_message_count,
        "external_paths": summary.external_paths,
    }
