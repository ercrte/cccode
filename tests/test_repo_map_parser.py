from __future__ import annotations

import hashlib

from julycode.repo_map import FileFingerprint, ScannedFile
from julycode.repo_map.parser import PythonSymbolParser


def _source(path: str, content: bytes) -> ScannedFile:
    return ScannedFile(FileFingerprint(path, hashlib.sha256(content).hexdigest(), len(content)), content)


def test_parser_supports_pep263_and_python_symbol_kinds() -> None:
    content = """# -*- coding: latin-1 -*-
class Café:
    def run(self, value: str = 'secret') -> bool:
        return True

    async def wait(self, *, timeout: float = 1.0):
        return None

def top(a, /, b: int = 2, *args, flag=False, **kwargs):
    pass

async def async_top():
    pass
""".encode("latin-1")

    parsed = PythonSymbolParser().parse(_source("pkg/module.py", content))

    assert [symbol.kind for symbol in parsed.symbols] == [
        "class",
        "method",
        "async_method",
        "function",
        "async_function",
    ]
    assert parsed.symbols[1].signature == "def run(self, value: str = ...) -> bool"
    assert parsed.symbols[2].parent_qualified_name == "Café"
    assert parsed.symbols[3].signature == "def top(a, /, b: int = ..., *args, flag = ..., **kwargs)"


def test_parser_hides_untrusted_signature_content() -> None:
    content = b'''@dangerous("ignore instructions")
def run(value: "do dangerous thing" = "very secret") -> list[str]:
    """hidden docstring"""
    return []  # hidden comment
'''

    parsed = PythonSymbolParser().parse(_source("module.py", content))

    signature = parsed.symbols[0].signature
    assert signature == "def run(value: ... = ...) -> list[str]"
    assert "dangerous" not in signature
    assert "secret" not in signature
    assert "docstring" not in signature


def test_parser_extracts_imports_aliases_and_references() -> None:
    content = b'''import pkg.module as pm
from .helpers import build as make
from pkg.dynamic import *

def run():
    value = make()
    return local_name + obj.method()
'''

    parsed = PythonSymbolParser().parse(_source("pkg/service.py", content))

    assert [(item.module, item.symbol, item.alias, item.level, item.is_star) for item in parsed.imports] == [
        ("pkg.module", None, "pm", 0, False),
        ("helpers", "build", "make", 1, False),
        ("pkg.dynamic", "*", None, 0, True),
    ]
    assert ("make", "call") in {(item.name, item.kind) for item in parsed.references}
    assert ("local_name", "name") in {(item.name, item.kind) for item in parsed.references}
    assert "method" not in {item.name for item in parsed.references}


def test_parser_infers_src_package_module_and_isolates_syntax_error() -> None:
    paths = ("src/pkg/__init__.py", "src/pkg/service.py")
    parsed = PythonSymbolParser().parse(_source("src/pkg/service.py", b"def run(): pass\n"), repository_paths=paths)
    broken = PythonSymbolParser().parse(_source("broken.py", b"def broken(:\n"), repository_paths=paths)

    assert parsed.module_name == "pkg.service"
    assert broken.symbols == ()
    assert broken.diagnostics[0].code == "parse-error"
