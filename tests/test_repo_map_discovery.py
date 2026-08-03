from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from julycode.repo_map.discovery import RepositoryDiscovery, build_workspace_state, scan_repository


def _git(path: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(path), *args), check=True, capture_output=True)


def test_non_git_discovery_excludes_defaults_and_normalizes_paths(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "main.py").write_text("def main(): pass\n", encoding="utf-8")
    (tmp_path / "types.pyi").write_text("def value() -> int: ...\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("x = 1\n", encoding="utf-8")

    result = scan_repository(tmp_path)

    assert result.identity.root == tmp_path.resolve()
    assert result.identity.is_git is False
    assert [item.fingerprint.relative_path for item in result.files] == ["pkg/main.py", "types.pyi"]


def test_git_discovery_uses_worktree_root_and_effective_ignore(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (tmp_path / "tracked.py").write_text("def tracked(): pass\n", encoding="utf-8")
    (tmp_path / "untracked.py").write_text("def untracked(): pass\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("def ignored(): pass\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    _git(tmp_path, "add", "tracked.py", ".gitignore")

    result = scan_repository(tmp_path / "nested")

    assert result.identity.root == tmp_path.resolve()
    assert result.identity.is_git is True
    assert [item.fingerprint.relative_path for item in result.files] == ["tracked.py", "untracked.py"]


def test_discovery_rejects_file_and_directory_symlinks(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("def outside(): pass\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("def real(): pass\n", encoding="utf-8")
    try:
        (tmp_path / "linked.py").symlink_to(outside)
        (tmp_path / "linked-dir").symlink_to(tmp_path.parent, target_is_directory=True)
    except OSError:
        pytest.skip("当前平台不允许创建符号链接")

    result = scan_repository(tmp_path)

    assert [item.fingerprint.relative_path for item in result.files] == ["real.py"]


def test_workspace_revision_uses_content_not_mtime(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    first = build_workspace_state(scan_repository(tmp_path))
    original_times = source.stat().st_atime_ns, source.stat().st_mtime_ns

    source.write_text("value = 2\n", encoding="utf-8")
    os.utime(source, ns=original_times)
    second = build_workspace_state(scan_repository(tmp_path))

    assert first.revision != second.revision
    assert first.ordered_fingerprints[0].content_hash != second.ordered_fingerprints[0].content_hash


def test_git_identity_changes_when_head_changes(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    before, _ = RepositoryDiscovery(tmp_path).identify()
    _git(tmp_path, "checkout", "-b", "feature")
    after, _ = RepositoryDiscovery(tmp_path).identify()

    assert before.repo_id == after.repo_id
    assert before.worktree_id == after.worktree_id
    assert before.head_id != after.head_id
