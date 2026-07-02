from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from mewcode.context.models import ContextSummary
from mewcode.memory.models import RestoreReport, SessionInfo, SessionMemoryConfig
from mewcode.providers.base import ChatMessage
from mewcode.session import ChatSession
from mewcode.session_id import SessionId, is_valid_session_id, new_session_id
from mewcode.tools.base import ToolCall


class SessionRecordError(ValueError):
    pass


class SessionJsonlStore:
    def __init__(self, cwd: Path, config: SessionMemoryConfig | None = None) -> None:
        self.cwd = cwd.resolve()
        self.config = config or SessionMemoryConfig()
        project_dir = Path(self.config.project_dir)
        if not project_dir.is_absolute():
            project_dir = self.cwd / project_dir
        self.project_dir = project_dir.resolve()
        self._ensure_under_cwd(self.project_dir)
        self.sessions_dir = (self.project_dir / self.config.sessions_dir).resolve()
        self._ensure_under_cwd(self.sessions_dir)

    def create_session(self, session_id: SessionId | None = None) -> ChatSession:
        actual_id = session_id or new_session_id()
        session = ChatSession()
        session.context_state.session_id = str(actual_id)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._path_for(actual_id).touch(exist_ok=True)
        self.attach_recorder(session)
        return session

    def attach_recorder(self, session: ChatSession) -> None:
        session.set_recorder(PersistentSessionRecorder(self, SessionId(session.context_state.session_id)))

    def append_message(self, session_id: SessionId, message: ChatMessage) -> None:
        self._append_record(
            session_id,
            {
                "kind": "message",
                "session_id": str(session_id),
                "created_at": _now_iso(),
                "message": message_to_json(message),
            },
        )

    def append_checkpoint(self, session: ChatSession) -> None:
        session_id = SessionId(session.context_state.session_id)
        payload: dict[str, Any] = {
            "kind": "checkpoint",
            "session_id": str(session_id),
            "created_at": _now_iso(),
            "messages": [message_to_json(message) for message in session.messages],
            "context_summary": _summary_to_json(session.context_state.summary),
        }
        self._append_record(session_id, payload)

    def list_sessions(self, *, now: datetime | None = None) -> tuple[SessionInfo, ...]:
        current = now or datetime.now(timezone.utc)
        infos: list[SessionInfo] = []
        if not self.sessions_dir.exists():
            return ()
        for path in sorted(self.sessions_dir.glob("*.jsonl")):
            if not is_valid_session_id(path.stem):
                continue
            messages, summary, updated_at, skipped = self._read_state(path)
            _ = summary
            if updated_at is None:
                updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            infos.append(
                SessionInfo(
                    session_id=SessionId(path.stem),
                    path=path,
                    title=_session_title(messages, path.stem),
                    message_count=len(messages),
                    updated_at=updated_at,
                    expired=(current - updated_at).days >= self.config.retention_days,
                    warnings=tuple(f"跳过 {skipped} 条坏记录" for _ in range(1) if skipped),
                )
            )
        return tuple(sorted(infos, key=lambda item: item.updated_at, reverse=True))

    def load_session(self, session_id: SessionId) -> tuple[ChatSession, RestoreReport]:
        path = self._path_for(session_id)
        messages, summary, _updated_at, skipped = self._read_state(path)
        session = ChatSession(messages=list(messages))
        session.context_state.session_id = str(session_id)
        if summary is not None:
            session.context_state.summary = summary
        self.attach_recorder(session)
        report = RestoreReport(
            restored=True,
            session_id=session_id,
            source_path=path,
            skipped_bad_lines=skipped,
            warnings=tuple(f"恢复时跳过 {skipped} 条坏记录" for _ in range(1) if skipped),
        )
        return session, report

    def latest_unexpired(self, *, now: datetime | None = None) -> SessionInfo | None:
        for info in self.list_sessions(now=now):
            if not info.expired:
                return info
        return None

    def cleanup_expired(self, *, now: datetime | None = None) -> tuple[SessionInfo, ...]:
        removed: list[SessionInfo] = []
        for info in self.list_sessions(now=now):
            if not info.expired:
                continue
            try:
                info.path.unlink()
            except FileNotFoundError:
                pass
            removed.append(info)
        return tuple(removed)

    def _append_record(self, session_id: SessionId, payload: Mapping[str, Any]) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(session_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def _path_for(self, session_id: SessionId) -> Path:
        return self.sessions_dir / f"{session_id}.jsonl"

    def _read_state(
        self,
        path: Path,
    ) -> tuple[tuple[ChatMessage, ...], ContextSummary | None, datetime | None, int]:
        messages: list[ChatMessage] = []
        summary: ContextSummary | None = None
        updated_at: datetime | None = None
        skipped = 0
        if not path.exists():
            return (), None, None, 0

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
                if not isinstance(record, dict):
                    raise SessionRecordError("记录必须是对象")
                kind = str(record.get("kind", ""))
                created_at = _parse_created_at(record.get("created_at"))
                if kind == "message":
                    raw_message = record.get("message")
                    if not isinstance(raw_message, dict):
                        raise SessionRecordError("message 记录缺少消息对象")
                    messages.append(message_from_json(raw_message))
                elif kind == "checkpoint":
                    raw_messages = record.get("messages")
                    if not isinstance(raw_messages, list):
                        raise SessionRecordError("checkpoint 记录缺少消息数组")
                    messages = [message_from_json(item) for item in raw_messages if isinstance(item, dict)]
                    summary = _summary_from_json(record.get("context_summary"))
                else:
                    raise SessionRecordError(f"未知会话记录类型: {kind}")
                updated_at = created_at
            except (json.JSONDecodeError, OSError, SessionRecordError, TypeError, ValueError):
                skipped += 1
        return tuple(messages), summary, updated_at, skipped

    def _ensure_under_cwd(self, path: Path) -> None:
        try:
            path.relative_to(self.cwd)
        except ValueError as exc:
            raise ValueError(f"memory 存储目录必须位于项目目录内: {path}") from exc


class PersistentSessionRecorder:
    def __init__(self, store: SessionJsonlStore, session_id: SessionId) -> None:
        self.store = store
        self.session_id = session_id

    def append_message(self, message: ChatMessage) -> None:
        self.store.append_message(self.session_id, message)

    def append_checkpoint(self, messages, summary: ContextSummary | None) -> None:
        session = ChatSession(messages=list(messages))
        session.context_state.session_id = str(self.session_id)
        if summary is not None:
            session.context_state.summary = summary
        self.store.append_checkpoint(session)


def message_to_json(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "thinking": message.thinking,
        "tool_calls": [asdict(call) for call in message.tool_calls],
        "tool_call_id": message.tool_call_id,
        "tool_result_is_error": message.tool_result_is_error,
        "provider_payload": message.provider_payload,
        "metadata": message.metadata,
    }


def message_from_json(data: Mapping[str, Any]) -> ChatMessage:
    role = data.get("role")
    if role not in {"user", "assistant", "tool"}:
        raise SessionRecordError(f"无效消息角色: {role}")
    content = data.get("content", "")
    if not isinstance(content, str):
        raise SessionRecordError("消息 content 必须是字符串")

    tool_calls = []
    raw_tool_calls = data.get("tool_calls", [])
    if raw_tool_calls is None:
        raw_tool_calls = []
    if not isinstance(raw_tool_calls, list):
        raise SessionRecordError("tool_calls 必须是数组")
    for raw_call in raw_tool_calls:
        if not isinstance(raw_call, dict):
            raise SessionRecordError("tool_call 必须是对象")
        raw_id = raw_call.get("id")
        raw_name = raw_call.get("name")
        if not isinstance(raw_id, str) or not isinstance(raw_name, str):
            raise SessionRecordError("tool_call 缺少 id 或 name")
        raw_arguments = raw_call.get("arguments", {})
        if not isinstance(raw_arguments, dict):
            raise SessionRecordError("tool_call.arguments 必须是对象")
        tool_calls.append(
            ToolCall(
                id=raw_id,
                name=raw_name,
                arguments=dict(raw_arguments),
                raw_arguments=str(raw_call.get("raw_arguments") or ""),
                parse_error=raw_call.get("parse_error") if isinstance(raw_call.get("parse_error"), str) else None,
            )
        )

    tool_call_id = data.get("tool_call_id")
    if tool_call_id is not None and not isinstance(tool_call_id, str):
        raise SessionRecordError("tool_call_id 必须是字符串")
    provider_payload = data.get("provider_payload")
    if provider_payload is not None and not isinstance(provider_payload, dict):
        raise SessionRecordError("provider_payload 必须是对象")
    metadata = data.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise SessionRecordError("metadata 必须是对象")

    return ChatMessage(
        role=role,  # type: ignore[arg-type]
        content=content,
        thinking=data.get("thinking") if isinstance(data.get("thinking"), str) else None,
        tool_calls=tuple(tool_calls),
        tool_call_id=tool_call_id,
        tool_result_is_error=bool(data.get("tool_result_is_error", False)),
        provider_payload=dict(provider_payload) if isinstance(provider_payload, dict) else None,
        metadata=dict(metadata) if isinstance(metadata, dict) else None,
    )


def _summary_to_json(summary: ContextSummary | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    return asdict(summary)


def _summary_from_json(data: Any) -> ContextSummary | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise SessionRecordError("context_summary 必须是对象")
    return ContextSummary(
        content=str(data.get("content", "")),
        boundary_notice=str(data.get("boundary_notice", "")),
        created_at=str(data.get("created_at", "")),
        source_message_count=int(data.get("source_message_count", 0)),
        kept_message_count=int(data.get("kept_message_count", 0)),
        external_paths=tuple(str(item) for item in data.get("external_paths", ()) or ()),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_created_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise SessionRecordError("created_at 不能为空")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _session_title(messages: tuple[ChatMessage, ...], fallback: str) -> str:
    for message in messages:
        if message.role != "user":
            continue
        first_line = message.content.strip().splitlines()[0] if message.content.strip() else ""
        if first_line:
            return first_line[:60]
    return fallback
