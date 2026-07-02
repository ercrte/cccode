from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from mewcode.memory.index import MemoryIndexBuilder
from mewcode.memory.models import MemoryNote, MemoryUpdateJob, MemoryScope
from mewcode.memory.notes import MemoryNoteStore
from mewcode.providers.base import ChatMessage, ChatRequest, LLMProvider


class MemoryUpdateError(ValueError):
    pass


class MemoryNoteUpdater:
    def __init__(self, note_store: MemoryNoteStore, index_builder: MemoryIndexBuilder) -> None:
        self.note_store = note_store
        self.index_builder = index_builder

    async def update(self, *, job: MemoryUpdateJob, provider: LLMProvider):
        request = ChatRequest(messages=(ChatMessage(role="user", content=self._prompt(job)),), tools=())
        text_parts: list[str] = []
        final_message: ChatMessage | None = None
        try:
            async for event in provider.stream_chat(request):
                if event.type == "text_delta":
                    text_parts.append(event.text)
                elif event.type == "message_done":
                    final_message = event.message
                elif event.type == "error":
                    raise MemoryUpdateError(event.error or "自动记忆更新失败")
        except MemoryUpdateError:
            raise
        except Exception as exc:
            raise MemoryUpdateError(f"自动记忆更新失败: {exc}") from exc

        if final_message is not None and final_message.tool_calls:
            raise MemoryUpdateError("自动记忆更新请求中模型尝试调用工具")
        raw_text = (final_message.content if final_message is not None else "") or "".join(text_parts)
        operations = self._parse_operations(raw_text)
        validated = [self._validate_operation(operation, job) for operation in operations]

        affected_scopes: set[MemoryScope] = set()
        for action, note in validated:
            if action == "skip" or note is None:
                continue
            self.note_store.write_note(note)
            affected_scopes.add(note.scope)

        return tuple(self.index_builder.build(scope) for scope in sorted(affected_scopes))

    def _prompt(self, job: MemoryUpdateJob) -> str:
        payload = {
            "session_id": str(job.session_id),
            "turn_messages": [message.content for message in job.turn_messages],
            "final_message": job.final_message.content,
            "user_memory_index": (
                job.knowledge_context.user_memory_index.content
                if job.knowledge_context.user_memory_index is not None
                else ""
            ),
            "project_memory_index": (
                job.knowledge_context.project_memory_index.content
                if job.knowledge_context.project_memory_index is not None
                else ""
            ),
        }
        return (
            "你正在为 MewCode 更新长期记忆。禁止调用任何工具。\n"
            "只返回 JSON，不要输出 Markdown。JSON 格式为 {\"operations\": [...]}。\n"
            "operation.action 只能是 create、update、skip。\n"
            "category 只能是 preference（用户偏好）、correction（纠正反馈）、"
            "project_knowledge（项目知识）、reference（参考资料）。\n"
            "scope 只能是 user 或 project。跨项目通用偏好和纠正反馈放 user；"
            "项目事实、约定和参考资料放 project。\n"
            "重复事实应通过 update 或 skip 去重，不要创建重复笔记。\n\n"
            f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
        )

    def _parse_operations(self, text: str) -> list[dict[str, Any]]:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise MemoryUpdateError(f"自动记忆结果不是合法 JSON: {exc.msg}") from exc
        operations = parsed.get("operations") if isinstance(parsed, dict) else parsed
        if not isinstance(operations, list):
            raise MemoryUpdateError("自动记忆结果缺少 operations 数组")
        if not all(isinstance(operation, dict) for operation in operations):
            raise MemoryUpdateError("自动记忆 operations 中存在非法项")
        return operations

    def _validate_operation(
        self,
        operation: dict[str, Any],
        job: MemoryUpdateJob,
    ) -> tuple[str, MemoryNote | None]:
        action = str(operation.get("action", "")).strip()
        if action not in {"create", "update", "skip"}:
            raise MemoryUpdateError(f"不支持的自动记忆操作: {action}")
        if action == "skip":
            return "skip", None

        scope = str(operation.get("scope", "")).strip()
        if scope not in {"user", "project"}:
            raise MemoryUpdateError(f"不支持的记忆 scope: {scope}")
        category = str(operation.get("category", "")).strip()
        if category not in {"preference", "correction", "project_knowledge", "reference"}:
            raise MemoryUpdateError(f"不支持的记忆 category: {category}")
        note_id = str(operation.get("note_id", "")).strip()
        title = str(operation.get("title", "")).strip()
        body = str(operation.get("body", "")).strip()
        if not note_id or not title or not body:
            raise MemoryUpdateError("自动记忆操作缺少 note_id、title 或 body")
        now = _now_iso()
        existing = self.note_store.read_note(scope, note_id) if action == "update" else None  # type: ignore[arg-type]
        raw_tags = operation.get("tags", ())
        tags = tuple(str(item) for item in raw_tags) if isinstance(raw_tags, list) else ()
        return action, MemoryNote(
            note_id=note_id,
            scope=scope,  # type: ignore[arg-type]
            category=category,  # type: ignore[arg-type]
            title=title,
            body=body,
            source_session_id=job.session_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
            tags=tags,
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
