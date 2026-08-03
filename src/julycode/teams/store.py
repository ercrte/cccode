from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from julycode.teams.locking import AtomicJsonFile, FileLock
from julycode.teams.models import (
    RecoveryReport,
    TeamConfig,
    TeamDataError,
    TeamMemberRecord,
    TeamRecord,
    TeamSummary,
    member_from_dict,
    outbox_from_dict,
)
from julycode.teams.paths import TeamPaths, team_root, validate_member_name, validate_team_name
from julycode.worktrees.models import RepositoryLayout, WorktreeError
from julycode.worktrees.paths import discover_repository_layout


TEAM_SCHEMA_VERSION = 1


class TeamStore:
    def __init__(
        self,
        main_cwd: Path,
        config: TeamConfig | None = None,
        *,
        root: Path | None = None,
    ) -> None:
        self.main_cwd = main_cwd.resolve()
        self.config = config or TeamConfig()
        self.root = (root or team_root()).resolve()

    def repository_layout(self) -> RepositoryLayout:
        try:
            return discover_repository_layout(self.main_cwd)
        except WorktreeError as exc:
            raise TeamDataError(str(exc)) from exc

    async def create(self, name: str, repository: RepositoryLayout | None = None) -> TeamRecord:
        safe_name = validate_team_name(name)
        layout = repository or self.repository_layout()
        paths = TeamPaths.for_team(safe_name, base=self.root)
        paths.ensure_directories()
        token = await FileLock(paths.team_lock, self.config).acquire()
        try:
            if paths.team_file.exists():
                raise TeamDataError(f"团队已存在: {safe_name}")
            now = _now()
            raw = {
                "schema_version": TEAM_SCHEMA_VERSION,
                "revision": 1,
                "name": safe_name,
                "repository_root": str(layout.repository_root),
                "repository_id": layout.repository_id,
                "lead_name": "lead",
                "created_at": now,
                "updated_at": now,
                "members": {},
                "outbox": [],
            }
            AtomicJsonFile(paths.team_file, FileLock(paths.team_lock, self.config))._replace_unlocked(raw)
            await AtomicJsonFile(paths.tasks_file, FileLock(paths.tasks_lock, self.config)).replace(
                {"schema_version": 1, "revision": 1, "tasks": [], "outbox": []}
            )
            await AtomicJsonFile(paths.approvals_file, FileLock(paths.approvals_lock, self.config)).replace(
                {"schema_version": 1, "revision": 1, "approvals": [], "outbox": []}
            )
            await AtomicJsonFile(
                paths.mailbox_file("lead"), FileLock(paths.mailbox_lock("lead"), self.config)
            ).replace({"schema_version": 1, "revision": 1, "messages": []})
            return self._parse_record(raw)
        except Exception:
            if not paths.team_file.exists():
                self._remove_empty_tree(paths.root)
            raise
        finally:
            await FileLock(paths.team_lock, self.config).release(token)

    async def list(self) -> tuple[TeamSummary, ...]:
        if not self.root.exists():
            return ()
        summaries: list[TeamSummary] = []
        for directory in sorted(path for path in self.root.iterdir() if path.is_dir()):
            try:
                record = await self._load_path(TeamPaths.for_team(directory.name, base=self.root))
            except (OSError, TeamDataError):
                continue
            summaries.append(
                TeamSummary(
                    name=record.name,
                    repository_root=record.repository_root,
                    lead_name=record.lead_name,
                    member_count=len(record.members),
                    updated_at=record.updated_at,
                    path=str(directory.resolve()),
                )
            )
        return tuple(summaries)

    async def load(self, name: str, repository: RepositoryLayout | None = None) -> TeamRecord:
        layout = repository or self.repository_layout()
        paths = TeamPaths.for_team(name, base=self.root)
        record = await self._load_path(paths)
        if record.repository_id != layout.repository_id or Path(record.repository_root).resolve() != layout.repository_root:
            raise TeamDataError(
                f"团队 {name} 属于其他项目: {record.repository_root}"
            )
        return record

    async def update_member(self, team: str, member: TeamMemberRecord) -> TeamRecord:
        validate_member_name(member.name)
        if member.backend != "coroutine":
            raise TeamDataError(f"本阶段不支持成员后端: {member.backend}")
        paths = TeamPaths.for_team(team, base=self.root)
        self._validate_member_paths(paths, member)
        store = AtomicJsonFile(paths.team_file, FileLock(paths.team_lock, self.config))

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            record = self._parse_record(raw)
            members = dict(record.members)
            members[member.name] = member
            raw["members"] = {name: asdict(value) for name, value in sorted(members.items())}
            raw["revision"] = record.revision + 1
            raw["updated_at"] = _now()
            return raw

        return self._parse_record(await store.mutate(mutate))

    async def add_member(self, team: str, member: TeamMemberRecord) -> TeamRecord:
        validate_member_name(member.name)
        if member.backend != "coroutine":
            raise TeamDataError(f"本阶段不支持成员后端: {member.backend}")
        paths = TeamPaths.for_team(team, base=self.root)
        self._validate_member_paths(paths, member)
        store = AtomicJsonFile(paths.team_file, FileLock(paths.team_lock, self.config))

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            record = self._parse_record(raw)
            if member.name in record.members:
                raise TeamDataError(f"团队成员已存在: {member.name}")
            members = {**record.members, member.name: member}
            raw["members"] = {name: asdict(value) for name, value in sorted(members.items())}
            raw["revision"] = record.revision + 1
            raw["updated_at"] = _now()
            return raw

        return self._parse_record(await store.mutate(mutate))

    async def get_member(self, team: str, member: str) -> TeamMemberRecord:
        record = await self._load_path(TeamPaths.for_team(team, base=self.root))
        try:
            return record.members[member]
        except KeyError as exc:
            raise TeamDataError(f"未知团队成员: {member}") from exc

    async def reconcile_interrupted(self, team: str) -> RecoveryReport:
        paths = TeamPaths.for_team(team, base=self.root)
        interrupted: list[str] = []
        task_ids: list[str] = []
        store = AtomicJsonFile(paths.team_file, FileLock(paths.team_lock, self.config))

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            record = self._parse_record(raw)
            members = dict(record.members)
            now = _now()
            for name, member in members.items():
                if member.status != "running" or _lease_owner_alive(paths.runtime_lease(name)):
                    continue
                interrupted.append(name)
                if member.current_task_id:
                    task_ids.append(member.current_task_id)
                members[name] = replace(
                    member,
                    status="failed",
                    last_error="成员进程已中断，任务可重新指派。",
                    updated_at=now,
                    last_active_at=now,
                )
            if not interrupted:
                return raw
            raw["members"] = {name: asdict(value) for name, value in sorted(members.items())}
            raw["revision"] = record.revision + 1
            raw["updated_at"] = now
            return raw

        await store.mutate(mutate)
        return RecoveryReport(
            interrupted_members=tuple(interrupted),
            released_task_ids=tuple(task_ids),
        )

    async def append_outbox(self, team: str, event: dict[str, Any]) -> None:
        paths = TeamPaths.for_team(team, base=self.root)
        store = AtomicJsonFile(paths.team_file, FileLock(paths.team_lock, self.config))

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            events = list(raw.get("outbox", []))
            events.append(event)
            raw["outbox"] = events
            raw["revision"] = int(raw.get("revision", 0)) + 1
            raw["updated_at"] = _now()
            return raw

        await store.mutate(mutate)

    async def pending_events(self, team_name: str):
        record = await self._load_path(TeamPaths.for_team(team_name, base=self.root))
        return record.outbox

    async def mark_delivered(self, team_name: str, event_id: str, recipient: str) -> None:
        paths = TeamPaths.for_team(team_name, base=self.root)
        store = AtomicJsonFile(paths.team_file, FileLock(paths.team_lock, self.config))

        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            record = self._parse_record(raw)
            events = tuple(
                replace(event, delivered_to=(*event.delivered_to, recipient))
                if event.id == event_id and recipient not in event.delivered_to
                else event
                for event in record.outbox
            )
            raw["outbox"] = [asdict(event) for event in events]
            raw["revision"] = record.revision + 1
            raw["updated_at"] = _now()
            return raw

        await store.mutate(mutate)

    async def _load_path(self, paths: TeamPaths) -> TeamRecord:
        raw = await AtomicJsonFile(paths.team_file, FileLock(paths.team_lock, self.config)).read()
        record = self._parse_record(raw)
        for member in record.members.values():
            self._validate_member_paths(paths, member)
        return record

    def _validate_member_paths(self, paths: TeamPaths, member: TeamMemberRecord) -> None:
        expected_session = paths.session_file(member.name)
        if Path(member.session_path).resolve() != expected_session:
            raise TeamDataError(f"成员 {member.name} 的上下文路径越过团队目录边界")

    def _parse_record(self, raw: dict[str, Any]) -> TeamRecord:
        if raw.get("schema_version") != TEAM_SCHEMA_VERSION:
            raise TeamDataError(f"未知团队 schema_version: {raw.get('schema_version')}")
        members_raw = raw.get("members", {})
        if not isinstance(members_raw, dict):
            raise TeamDataError("团队 members 必须是对象")
        outbox_raw = raw.get("outbox", [])
        if not isinstance(outbox_raw, list):
            raise TeamDataError("团队 outbox 必须是数组")
        return TeamRecord(
            schema_version=TEAM_SCHEMA_VERSION,
            revision=int(raw.get("revision", 0)),
            name=_required(raw, "name"),
            repository_root=_required(raw, "repository_root"),
            repository_id=_required(raw, "repository_id"),
            lead_name=_required(raw, "lead_name"),
            created_at=_required(raw, "created_at"),
            updated_at=_required(raw, "updated_at"),
            members={name: member_from_dict(value) for name, value in members_raw.items() if isinstance(value, dict)},
            outbox=tuple(outbox_from_dict(value) for value in outbox_raw if isinstance(value, dict)),
        )

    def _remove_empty_tree(self, root: Path) -> None:
        if not root.exists():
            return
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        root.rmdir()


def _required(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise TeamDataError(f"团队字段 {key} 必须是非空字符串")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_owner_alive(path: Path) -> bool:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if raw.get("host") != socket.gethostname() or not isinstance(raw.get("pid"), int):
        return False
    try:
        os.kill(raw["pid"], 0)
    except (OSError, ProcessLookupError):
        return False
    return True
