from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from julycode.tools.file_catalog import DEFAULT_SEARCH_EXCLUDED_DIRS, FileCatalog


def _git(path: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(path), *args), check=True, capture_output=True)


def _relative(catalog: FileCatalog, files: tuple[Path, ...]) -> list[str]:
    return [path.relative_to(catalog.cwd).as_posix() for path in files]


def test_non_git_default_files_excludes_runtime_cache_and_build_directories(tmp_path: Path) -> None:
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "root.py").write_text("", encoding="utf-8")
    (tmp_path / "src/pkg/app.py").write_text("", encoding="utf-8")
    for name in DEFAULT_SEARCH_EXCLUDED_DIRS:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "ignored.py").write_text("", encoding="utf-8")

    catalog = FileCatalog(tmp_path)

    assert _relative(catalog, catalog.default_files()) == ["root.py", "src/pkg/app.py"]


def test_non_git_default_files_skip_directory_and_file_symlinks(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.py").write_text("", encoding="utf-8")
    (tmp_path / "safe.py").write_text("", encoding="utf-8")
    try:
        (tmp_path / "linked-dir").symlink_to(outside, target_is_directory=True)
        (tmp_path / "linked.py").symlink_to(outside / "secret.py")
    except OSError:
        pytest.skip("当前平台不允许创建符号链接")

    catalog = FileCatalog(tmp_path)

    assert _relative(catalog, catalog.default_files()) == ["safe.py"]


def test_git_default_files_include_tracked_and_untracked_but_not_ignored(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    (tmp_path / ".gitignore").write_text("ignored.py\n.julycode/\n", encoding="utf-8")
    (tmp_path / "tracked.py").write_text("", encoding="utf-8")
    (tmp_path / "untracked.py").write_text("", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("", encoding="utf-8")
    (tmp_path / ".julycode").mkdir()
    (tmp_path / ".julycode/session.py").write_text("", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore", "tracked.py")

    catalog = FileCatalog(tmp_path)

    assert _relative(catalog, catalog.default_files()) == [".gitignore", "tracked.py", "untracked.py"]


def test_git_worktree_subdirectory_scope_returns_relative_posix_paths(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    (tmp_path / "root.py").write_text("", encoding="utf-8")
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/direct.py").write_text("", encoding="utf-8")
    (tmp_path / "src/pkg/deep.py").write_text("", encoding="utf-8")
    _git(tmp_path, "add", "root.py", "src/direct.py")

    catalog = FileCatalog(tmp_path / "src")

    assert _relative(catalog, catalog.default_files()) == ["direct.py", "pkg/deep.py"]


def test_git_failure_fallback_uses_pruned_filesystem_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("", encoding="utf-8")
    (tmp_path / ".julycode").mkdir()
    (tmp_path / ".julycode/session.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(FileCatalog, "_run_git", staticmethod(lambda args, cwd: None))

    catalog = FileCatalog(tmp_path)

    assert _relative(catalog, catalog.default_files()) == ["src/app.py"]


def test_explicit_ignored_target_is_searchable_but_root_scope_stays_pruned(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("", encoding="utf-8")
    (tmp_path / ".julycode").mkdir()
    (tmp_path / ".julycode/session.txt").write_text("", encoding="utf-8")
    catalog = FileCatalog(tmp_path)

    explicit = catalog.explicit_files(tmp_path / ".julycode")
    root = catalog.explicit_files(tmp_path)

    assert _relative(catalog, explicit) == [".julycode/session.txt"]
    assert _relative(catalog, root) == ["src/app.py"]


def test_explicit_file_and_outside_boundary(tmp_path: Path) -> None:
    target = tmp_path / "src/app.py"
    target.parent.mkdir()
    target.write_text("", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("", encoding="utf-8")
    catalog = FileCatalog(tmp_path)

    assert catalog.explicit_files(target) == (target.resolve(),)
    with pytest.raises(PermissionError):
        catalog.explicit_files(outside)


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("*.py", ["a.py"]),
        ("**/*.py", ["a.py", "src/a.py", "src/b.py", "src/pkg/deep.py"]),
        ("src/*.py", ["src/a.py", "src/b.py"]),
        ("src/**/*.py", ["src/a.py", "src/b.py", "src/pkg/deep.py"]),
        ("src/?.py", ["src/a.py", "src/b.py"]),
        ("src/[ab].py", ["src/a.py", "src/b.py"]),
        ("**/*.missing", []),
    ],
)
def test_glob_matching_uses_path_segments(
    tmp_path: Path,
    pattern: str,
    expected: list[str],
) -> None:
    (tmp_path / "src/pkg").mkdir(parents=True)
    for name in ("a.py", "src/a.py", "src/b.py", "src/pkg/deep.py", "src/readme.md"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    catalog = FileCatalog(tmp_path)

    matches = catalog.matching_files(pattern)

    assert _relative(catalog, matches) == expected


def test_glob_matching_respects_max_results(tmp_path: Path) -> None:
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("", encoding="utf-8")
    catalog = FileCatalog(tmp_path)

    matches = catalog.matching_files("*.py", max_results=2)

    assert _relative(catalog, matches) == ["a.py", "b.py"]
