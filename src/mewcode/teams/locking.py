from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mewcode.teams.models import TeamConfig, TeamDataError


@dataclass(frozen=True)
class LockToken:
    value: str


class FileLock:
    def __init__(self, path: Path, config: TeamConfig | None = None) -> None:
        self.path = path.resolve()
        self.config = config or TeamConfig()

    async def acquire(self) -> LockToken:
        deadline = time.monotonic() + self.config.lock_timeout_seconds
        token = LockToken(uuid.uuid4().hex)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                self._take_stale_lock()
                if time.monotonic() >= deadline:
                    raise TeamDataError(f"获取锁超时: {self.path}")
                await asyncio.sleep(self.config.lock_retry_interval_seconds)
                continue
            try:
                payload = {
                    "token": token.value,
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "created_at": time.time(),
                }
                os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            return token

    async def release(self, token: LockToken) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            return
        if raw.get("token") != token.value:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _take_stale_lock(self) -> None:
        try:
            before = self.path.stat()
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            raw = {}
            try:
                before = self.path.stat()
            except OSError:
                return
        age = time.time() - float(raw.get("created_at", before.st_mtime))
        if age < self.config.stale_lock_seconds:
            return
        pid = raw.get("pid")
        host = raw.get("host")
        if host == socket.gethostname() and isinstance(pid, int) and _pid_alive(pid):
            return
        try:
            after = self.path.stat()
            if (before.st_ino, before.st_mtime_ns, before.st_size) != (
                after.st_ino,
                after.st_mtime_ns,
                after.st_size,
            ):
                return
            self.path.unlink()
        except FileNotFoundError:
            pass

    async def __aenter__(self) -> LockToken:
        token = await self.acquire()
        self._context_token = token
        return token

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        token = getattr(self, "_context_token", None)
        if token is not None:
            await self.release(token)


class AtomicJsonFile:
    def __init__(self, path: Path, lock: FileLock) -> None:
        self.path = path.resolve()
        self.lock = lock

    async def read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise TeamDataError(f"数据文件不存在: {self.path}")
        except (OSError, json.JSONDecodeError) as exc:
            raise TeamDataError(f"无法读取 JSON 数据 {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise TeamDataError(f"JSON 数据顶层必须是对象: {self.path}")
        return raw

    async def replace(self, value: Mapping[str, Any]) -> None:
        token = await self.lock.acquire()
        try:
            self._replace_unlocked(value)
        finally:
            await self.lock.release(token)

    async def mutate(self, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        token = await self.lock.acquire()
        try:
            current = await self.read()
            updated = fn(current)
            if not isinstance(updated, dict):
                raise TeamDataError("JSON mutate 必须返回对象")
            self._replace_unlocked(updated)
            return updated
        finally:
            await self.lock.release(token)

    def _replace_unlocked(self, value: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(dict(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise


class ProcessLease:
    def __init__(self, path: Path, config: TeamConfig | None = None) -> None:
        self.path = path.resolve()
        self.config = config or TeamConfig()
        self.token: LockToken | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def acquire(self) -> LockToken:
        lock = FileLock(self.path, self.config)
        token = await lock.acquire()
        self.token = token
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        return token

    async def release(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.token is not None:
            await FileLock(self.path, self.config).release(self.token)
            self.token = None

    async def _heartbeat(self) -> None:
        interval = max(0.05, self.config.stale_lock_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            token = self.token
            if token is None:
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if raw.get("token") != token.value:
                    return
                raw["created_at"] = time.time()
                self.path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
            except OSError:
                return


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
