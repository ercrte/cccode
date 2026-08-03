from __future__ import annotations

import hashlib
import os
from pathlib import Path
import random
import subprocess
import sys

from julycode.repo_map import FileFingerprint, ScannedFile
from julycode.repo_map.graph import RepoGraphBuilder
from julycode.repo_map.parser import PythonSymbolParser
from julycode.repo_map.ranking import rank_symbols


def _parse(path: str, text: str, paths: tuple[str, ...]):
    content = text.encode()
    source = ScannedFile(FileFingerprint(path, hashlib.sha256(content).hexdigest(), len(content)), content)
    return PythonSymbolParser().parse(source, repository_paths=paths)


def _fixture_files():
    paths = (
        "pkg/__init__.py",
        "pkg/core.py",
        "pkg/helpers.py",
        "pkg/service.py",
    )
    return (
        _parse("pkg/__init__.py", "", paths),
        _parse("pkg/core.py", "def build(): pass\n", paths),
        _parse("pkg/helpers.py", "def helper(): pass\n", paths),
        _parse(
            "pkg/service.py",
            "from .core import build as make\nimport pkg.helpers\ndef run(): return make()\n",
            paths,
        ),
    )


def test_graph_resolves_imports_aliases_relative_imports_and_unique_calls() -> None:
    graph = RepoGraphBuilder().build(_fixture_files())
    edges = {(edge.source_path, edge.target_path, edge.relation): edge.weight for edge in graph.edges}

    assert edges[("pkg/service.py", "pkg/core.py", "import-symbol")] == 5.0
    assert edges[("pkg/service.py", "pkg/helpers.py", "import-module")] == 3.0
    assert edges[("pkg/service.py", "pkg/core.py", "unique-call")] == 2.0
    assert graph.score_for("pkg/core.py") > graph.score_for("pkg/service.py")


def test_graph_ignores_ambiguous_and_dynamic_relationships() -> None:
    paths = ("a.py", "b.py", "caller.py")
    files = (
        _parse("a.py", "def duplicate(): pass\n", paths),
        _parse("b.py", "def duplicate(): pass\n", paths),
        _parse(
            "caller.py",
            "from a import *\ndef run(obj):\n    duplicate()\n    getattr(obj, 'method')()\n    obj.method()\n",
            paths,
        ),
    )

    graph = RepoGraphBuilder().build(files)

    assert not any(edge.relation == "unique-call" for edge in graph.edges)
    assert any(item.code == "ambiguous-import-star" for item in graph.diagnostics)


def test_graph_and_ranking_are_stable_and_request_hints_dominate() -> None:
    files = list(_fixture_files())
    first_graph = RepoGraphBuilder().build(tuple(files))
    first = rank_symbols(tuple(files), first_graph, "请查看 pkg/service.py 里的 run")
    random.Random(42).shuffle(files)
    second_graph = RepoGraphBuilder().build(tuple(files))
    second = rank_symbols(tuple(files), second_graph, "请查看 pkg/service.py 里的 run")

    assert first_graph == second_graph
    assert first == second
    assert first[0].relative_path == "pkg/service.py"
    assert first[0].symbol.name == "run"


def test_empty_graph_is_deterministic() -> None:
    graph = RepoGraphBuilder().build(())

    assert graph.nodes == ()
    assert graph.edges == ()
    assert graph.scores == ()


def test_rendered_map_is_identical_across_python_hash_seeds() -> None:
    script = """
from pathlib import Path
from tests.test_repo_map_graph import _fixture_files
from julycode.repo_map.graph import RepoGraphBuilder
from julycode.repo_map.ranking import rank_symbols
from julycode.repo_map.renderer import RepoMapRenderer

files = _fixture_files()
graph = RepoGraphBuilder().build(files)
ranked = rank_symbols(files, graph, "请查看 service run")
rendered = RepoMapRenderer().render(
    ranked,
    root=Path("/repo"),
    revision="abcdef123456789",
    budget=2000,
    token_counter=lambda text: max(1, (len(text) + 3) // 4),
)
print(rendered.text if rendered is not None else "")
"""
    outputs = []
    for seed in ("1", "42", "98765"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path.cwd(),
            env={**os.environ, "PYTHONHASHSEED": seed},
            text=True,
            capture_output=True,
            check=True,
        )
        outputs.append(completed.stdout.encode("utf-8"))

    assert outputs[0] == outputs[1] == outputs[2]
