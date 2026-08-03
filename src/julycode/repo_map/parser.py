from __future__ import annotations

import ast
import io
import re
import tokenize
import unicodedata
from pathlib import PurePosixPath

from julycode.repo_map.models import (
    ImportRecord,
    ParsedPythonFile,
    ReferenceRecord,
    RepoMapDiagnostic,
    ScannedFile,
    SymbolRecord,
)


PARSER_VERSION = "python-ast-v1"
_CONTROL_CATEGORIES = {"Cc", "Cf"}


class PythonSymbolParser:
    def parse(
        self,
        source: ScannedFile,
        *,
        repository_paths: tuple[str, ...] = (),
    ) -> ParsedPythonFile:
        path = source.fingerprint.relative_path
        module_name, is_package = infer_module_name(path, repository_paths)
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(source.source_bytes).readline)
            text = source.source_bytes.decode(encoding)
            tree = ast.parse(text, filename=path, type_comments=False)
        except (SyntaxError, UnicodeError, LookupError) as exc:
            return ParsedPythonFile(
                fingerprint=source.fingerprint,
                module_name=module_name,
                is_package=is_package,
                diagnostics=(
                    RepoMapDiagnostic("parse-error", f"无法解析 Python 文件：{exc}", path, "error"),
                ),
            )

        symbols = self._symbols(tree)
        imports = self._imports(tree)
        visitor = _ReferenceVisitor()
        visitor.visit(tree)
        return ParsedPythonFile(
            fingerprint=source.fingerprint,
            module_name=module_name,
            is_package=is_package,
            symbols=tuple(sorted(symbols, key=lambda item: (item.line_number, item.qualified_name, item.kind))),
            imports=tuple(sorted(imports, key=lambda item: (item.line_number, item.module, item.symbol or ""))),
            references=tuple(sorted(set(visitor.references), key=lambda item: (item.line_number, item.name, item.kind))),
        )

    def _symbols(self, tree: ast.Module) -> list[SymbolRecord]:
        symbols: list[SymbolRecord] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_name = clean_text(node.name)
                symbols.append(
                    SymbolRecord(
                        kind="class",
                        name=class_name,
                        qualified_name=class_name,
                        line_number=node.lineno,
                        signature=f"class {class_name}",
                        short_signature=f"class {class_name}",
                    )
                )
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append(self._function_symbol(child, parent=class_name))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(self._function_symbol(node, parent=None))
        return symbols

    def _function_symbol(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        parent: str | None,
    ) -> SymbolRecord:
        name = clean_text(node.name)
        async_prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        kind = "async_method" if parent and async_prefix else "method" if parent else "async_function" if async_prefix else "function"
        parameters = _render_parameters(node.args)
        returns = _render_annotation(node.returns)
        suffix = f" -> {returns}" if returns else ""
        signature = f"{async_prefix}def {name}({parameters}){suffix}"
        short_signature = f"{async_prefix}def {name}(...)"
        return SymbolRecord(
            kind=kind,  # type: ignore[arg-type]
            name=name,
            qualified_name=f"{parent}.{name}" if parent else name,
            line_number=node.lineno,
            signature=clean_text(signature),
            short_signature=clean_text(short_signature),
            parent_qualified_name=parent,
        )

    def _imports(self, tree: ast.Module) -> list[ImportRecord]:
        imports: list[ImportRecord] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        ImportRecord(
                            module=clean_text(alias.name),
                            symbol=None,
                            alias=clean_text(alias.asname) if alias.asname else None,
                            level=0,
                            line_number=node.lineno,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                module = clean_text(node.module or "")
                for alias in node.names:
                    imports.append(
                        ImportRecord(
                            module=module,
                            symbol=clean_text(alias.name),
                            alias=clean_text(alias.asname) if alias.asname else None,
                            level=node.level,
                            line_number=node.lineno,
                            is_star=alias.name == "*",
                        )
                    )
        return imports


class _ReferenceVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.references: list[ReferenceRecord] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            self.references.append(ReferenceRecord(clean_text(node.func.id), "call", node.lineno))
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.references.append(ReferenceRecord(clean_text(node.id), "name", node.lineno))

    def visit_Import(self, node: ast.Import) -> None:
        _ = node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        _ = node


def infer_module_name(relative_path: str, repository_paths: tuple[str, ...]) -> tuple[str, bool]:
    path = PurePosixPath(relative_path)
    is_package = path.name in {"__init__.py", "__init__.pyi"}
    parts = list(path.with_suffix("").parts)
    if is_package:
        parts = parts[:-1]
    repository_set = set(repository_paths)
    package_start: int | None = None
    parent_parts = list(path.parent.parts)
    for index in range(len(parent_parts) - 1, -1, -1):
        prefix = PurePosixPath(*parent_parts[: index + 1])
        if f"{prefix.as_posix()}/__init__.py" in repository_set or f"{prefix.as_posix()}/__init__.pyi" in repository_set:
            package_start = index
            continue
        break
    if package_start is not None:
        module_parts = parts[package_start:]
    elif parts and parts[0] == "src":
        module_parts = parts[1:]
    else:
        module_parts = parts
    return ".".join(module_parts) or path.stem, is_package


def clean_text(value: str) -> str:
    cleaned = "".join(
        character
        for character in value
        if character in {"\n", "\t"} or unicodedata.category(character) not in _CONTROL_CATEGORIES
    )
    return re.sub(r"[\t\r\n]+", " ", cleaned).strip()


def _render_parameters(arguments: ast.arguments) -> str:
    rendered: list[str] = []
    positional = [*arguments.posonlyargs, *arguments.args]
    default_start = len(positional) - len(arguments.defaults)
    for index, argument in enumerate(positional):
        has_default = index >= default_start
        rendered.append(_render_argument(argument, default=has_default))
        if arguments.posonlyargs and index + 1 == len(arguments.posonlyargs):
            rendered.append("/")
    if arguments.vararg is not None:
        rendered.append(f"*{_render_argument(arguments.vararg, default=False)}")
    elif arguments.kwonlyargs:
        rendered.append("*")
    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True):
        rendered.append(_render_argument(argument, default=default is not None))
    if arguments.kwarg is not None:
        rendered.append(f"**{_render_argument(arguments.kwarg, default=False)}")
    return ", ".join(rendered)


def _render_argument(argument: ast.arg, *, default: bool) -> str:
    name = clean_text(argument.arg)
    annotation = _render_annotation(argument.annotation)
    result = f"{name}: {annotation}" if annotation else name
    return f"{result} = ..." if default else result


def _render_annotation(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return clean_text(node.id)
    if isinstance(node, ast.Attribute):
        prefix = _render_annotation(node.value)
        return f"{prefix}.{clean_text(node.attr)}" if prefix else "..."
    if isinstance(node, ast.Subscript):
        value = _render_annotation(node.value)
        slice_value = _render_annotation(node.slice)
        return f"{value}[{slice_value}]" if value and slice_value else "..."
    if isinstance(node, (ast.Tuple, ast.List)):
        values = ", ".join(_render_annotation(item) or "..." for item in node.elts)
        opening, closing = ("(", ")") if isinstance(node, ast.Tuple) else ("[", "]")
        return f"{opening}{values}{closing}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _render_annotation(node.left)
        right = _render_annotation(node.right)
        return f"{left} | {right}" if left and right else "..."
    if isinstance(node, ast.Constant) and node.value is None:
        return "None"
    return "..."

