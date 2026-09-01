from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from julycode.teams.models import TeamDataError


_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", re.ASCII)


def team_root() -> Path:
    return (Path.home() / ".julycode" / "teams").resolve()


def validate_team_name(value: str) -> str:
    return _validate_name(value, "团队")


def validate_member_name(value: str) -> str:
    name = _validate_name(value, "成员")
    if name == "lead":
        raise TeamDataError("成员名称 lead 为系统保留名称")
    return name


def _validate_name(value: str, label: str) -> str:
    if not isinstance(value, str) or _SAFE_NAME_RE.fullmatch(value) is None:
        raise TeamDataError(f"{label}名称必须为 1-64 位 ASCII 字母、数字、下划线或连字符")
    return value


def resolve_inside(root: Path, *parts: str) -> Path:
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*parts)
    resolved_parent = candidate.parent.resolve()
    try:
        resolved_parent.relative_to(resolved_root)
    except ValueError as exc:
        raise TeamDataError(f"路径越过团队目录边界: {candidate}") from exc
    resolved = resolved_parent / candidate.name
    if candidate.is_symlink() or candidate.exists():
        followed = candidate.resolve()
        try:
            followed.relative_to(resolved_root)
        except ValueError as exc:
            raise TeamDataError(f"路径通过符号链接越过团队目录边界: {candidate}") from exc
        return followed
    return resolved


@dataclass(frozen=True)
class TeamPaths:
    name: str
    root: Path

    @classmethod
    def for_team(cls, name: str, *, base: Path | None = None) -> TeamPaths:
        safe = validate_team_name(name)
        teams_root = (base or team_root()).resolve()
        return cls(name=safe, root=resolve_inside(teams_root, safe))

    @property
    def team_file(self) -> Path:
        return resolve_inside(self.root, "team.json")

    @property
    def team_lock(self) -> Path:
        return resolve_inside(self.root, "team.lock")

    @property
    def tasks_file(self) -> Path:
        return resolve_inside(self.root, "tasks.json")

    @property
    def tasks_lock(self) -> Path:
        return resolve_inside(self.root, "tasks.lock")

    @property
    def integration_file(self) -> Path:
        return resolve_inside(self.root, "integration.json")

    @property
    def integration_lock(self) -> Path:
        return resolve_inside(self.root, "integration.lock")

    @property
    def approvals_file(self) -> Path:
        return resolve_inside(self.root, "approvals.json")

    @property
    def approvals_lock(self) -> Path:
        return resolve_inside(self.root, "approvals.lock")

    def mailbox_file(self, actor_name: str) -> Path:
        safe = "lead" if actor_name == "lead" else validate_member_name(actor_name)
        return resolve_inside(self.root, "mailboxes", f"{safe}.json")

    def mailbox_lock(self, actor_name: str) -> Path:
        return self.mailbox_file(actor_name).with_suffix(".lock")

    def session_file(self, member_name: str) -> Path:
        safe = validate_member_name(member_name)
        return resolve_inside(self.root, "sessions", f"{safe}.jsonl")

    def session_lock(self, member_name: str) -> Path:
        return self.session_file(member_name).with_suffix(".lock")

    def runtime_lease(self, member_name: str) -> Path:
        safe = validate_member_name(member_name)
        return resolve_inside(self.root, "runtime", f"{safe}.lease")

    def ensure_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "mailboxes").mkdir(exist_ok=True)
        (self.root / "sessions").mkdir(exist_ok=True)
        (self.root / "runtime").mkdir(exist_ok=True)
