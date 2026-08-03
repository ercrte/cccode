from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from threading import Lock
from typing import NewType


SessionId = NewType("SessionId", str)

_SESSION_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-f0-9]{4}$")
_SESSION_SUFFIX_LOCK = Lock()
_SESSION_SUFFIXES_BY_STAMP: dict[str, set[str]] = {}


def new_session_id(now: datetime | None = None) -> SessionId:
    current = now or datetime.now(timezone.utc)
    stamp = current.strftime("%Y%m%d-%H%M%S")
    with _SESSION_SUFFIX_LOCK:
        used = _SESSION_SUFFIXES_BY_STAMP.setdefault(stamp, set())
        if len(used) >= 0x10000:
            raise RuntimeError(f"当前秒内会话 ID 已耗尽: {stamp}")
        suffix = secrets.token_hex(2)
        while suffix in used:
            suffix = secrets.token_hex(2)
        used.add(suffix)
    return SessionId(f"{stamp}-{suffix}")


def is_valid_session_id(value: str) -> bool:
    return bool(_SESSION_ID_RE.match(value))
