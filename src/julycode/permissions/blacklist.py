from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern

from julycode.permissions.models import PermissionDecision


@dataclass(frozen=True)
class DangerousCommandPattern:
    name: str
    pattern: Pattern[str]


class DangerousCommandGuard:
    def __init__(self, patterns: tuple[DangerousCommandPattern, ...] | None = None) -> None:
        self.patterns = patterns or _DEFAULT_PATTERNS

    def check(self, command: str) -> PermissionDecision | None:
        normalized = _normalize_command(command)
        for item in self.patterns:
            if item.pattern.search(normalized):
                return PermissionDecision(
                    kind="deny",
                    reason=f"命令命中高危黑名单: {item.name}",
                    error_type="permission_dangerous_command",
                )
        return None


def _compile(name: str, pattern: str) -> DangerousCommandPattern:
    return DangerousCommandPattern(name=name, pattern=re.compile(pattern, re.IGNORECASE))


def _normalize_command(command: str) -> str:
    return " ".join(str(command).strip().split())


_DEFAULT_PATTERNS = (
    _compile("递归删除根目录", r"\brm\s+(?:-[^\s]*[rf][^\s]*\s+){0,3}(?:/|/\*)(?:\s|$)"),
    _compile("递归删除家目录", r"\brm\s+(?:-[^\s]*[rf][^\s]*\s+){0,3}(?:~|~/|\$HOME)(?:\s|$)"),
    _compile("sudo rm", r"\bsudo\s+rm\b"),
    _compile("磁盘格式化", r"\bmkfs(?:\.[a-z0-9_+-]+)?\b|\bformat\s+/(?:fs|q)\b"),
    _compile("裸设备写入", r"\bdd\b.*\bof=/dev/(?:sd|hd|vd|nvme|disk)[^\s]*"),
    _compile("关机重启", r"\b(?:shutdown|reboot|halt|poweroff)\b"),
    _compile("fork bomb", r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:"),
    _compile("全局权限破坏", r"\bchmod\b(?=[^\n]*\s(?:-R|--recursive)\b)(?=[^\n]*\s(?:777|666|000)\s)(?=[^\n]*(?:\s/|\s~|\s\$HOME)(?:\s|$))"),
    _compile("kill all", r"\bkill\s+-9\s+-1\b"),
    _compile("git clean -fdx", r"\bgit\s+clean\b(?=[^\n]*-)(?=[^\n]*f)(?=[^\n]*d)(?=[^\n]*x)"),
)
