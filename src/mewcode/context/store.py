from __future__ import annotations

import json
import re
import time
from pathlib import Path

from mewcode.context.models import ContextConfig, ContextExternalRef
from mewcode.providers.base import ChatMessage


class ContextStore:
    def __init__(self, cwd: Path, config: ContextConfig | None = None) -> None:
        self.cwd = cwd.resolve()
        self.config = config or ContextConfig()
        root = Path(self.config.store_dir)
        if not root.is_absolute():
            root = self.cwd / root
        self.root = root.resolve()
        self._ensure_under_cwd(self.root)

    def write_tool_result(
        self,
        *,
        session_id: str,
        message: ChatMessage,
        estimated_tokens: int,
    ) -> ContextExternalRef:
        tool_dir = self.root / session_id / "tool-results"
        tool_dir.mkdir(parents=True, exist_ok=True)
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        safe_id = _safe_filename(message.tool_call_id or "tool-result")
        path = tool_dir / f"{int(time.time() * 1000)}-{safe_id}.json"
        content_chars = len(message.content)
        payload = {
            "created_at": created_at,
            "role": message.role,
            "tool_call_id": message.tool_call_id,
            "tool_result_is_error": message.tool_result_is_error,
            "content_chars": content_chars,
            "estimated_tokens": estimated_tokens,
            "content": message.content,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return ContextExternalRef(
            path=str(path.relative_to(self.cwd)),
            original_chars=content_chars,
            estimated_tokens=estimated_tokens,
            preview=message.content[: self.config.tool_preview_chars],
        )

    def _ensure_under_cwd(self, path: Path) -> None:
        try:
            path.relative_to(self.cwd)
        except ValueError as exc:
            raise ValueError(f"context.store_dir 必须位于项目目录内: {path}") from exc


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return safe or "tool-result"
