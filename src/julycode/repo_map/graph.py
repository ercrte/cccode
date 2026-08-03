from __future__ import annotations

from collections import defaultdict

from julycode.repo_map.models import GraphEdge, ParsedPythonFile, RepoGraph, RepoMapDiagnostic


DAMPING_FACTOR = 0.85
MAX_ITERATIONS = 50
CONVERGENCE_EPSILON = 1e-12
GRAPH_VERSION = "graph-v1:damping=0.85:max=50:epsilon=1e-12"


class RepoGraphBuilder:
    def build(self, files: tuple[ParsedPythonFile, ...]) -> RepoGraph:
        ordered = tuple(sorted(files, key=lambda item: item.fingerprint.relative_path))
        nodes = tuple(item.fingerprint.relative_path for item in ordered)
        module_index: dict[str, list[ParsedPythonFile]] = defaultdict(list)
        module_symbols: dict[tuple[str, str], list[tuple[ParsedPythonFile, object]]] = defaultdict(list)
        global_symbols: dict[str, list[ParsedPythonFile]] = defaultdict(list)
        for parsed in ordered:
            module_index[parsed.module_name].append(parsed)
            for symbol in parsed.symbols:
                module_symbols[(parsed.module_name, symbol.name)].append((parsed, symbol))
                global_symbols[symbol.name].append(parsed)

        weights: dict[tuple[str, str, str], float] = defaultdict(float)
        diagnostics: list[RepoMapDiagnostic] = []
        for parsed in ordered:
            source_path = parsed.fingerprint.relative_path
            bound_imports: dict[str, ParsedPythonFile] = {}
            for imported in parsed.imports:
                if imported.is_star:
                    diagnostics.append(
                        RepoMapDiagnostic("ambiguous-import-star", "import * 不建立精确关系", source_path)
                    )
                    continue
                module = _absolute_module(parsed, imported.module, imported.level)
                if imported.symbol is None:
                    target = _unique_module(module_index, module)
                    relation, weight = "import-module", 3.0
                else:
                    target = _unique_symbol(module_symbols, module, imported.symbol)
                    if target is None:
                        target = _unique_module(module_index, f"{module}.{imported.symbol}".strip("."))
                    relation, weight = "import-symbol", 5.0
                if target is None:
                    diagnostics.append(
                        RepoMapDiagnostic("unresolved-import", "导入目标无法唯一解析", source_path)
                    )
                    continue
                target_path = target.fingerprint.relative_path
                if target_path != source_path:
                    weights[(source_path, target_path, relation)] += weight
                    if imported.symbol is not None:
                        bound_imports[imported.alias or imported.symbol] = target

            for reference in parsed.references:
                imported_target = bound_imports.get(reference.name)
                if imported_target is not None:
                    candidates = {imported_target.fingerprint.relative_path: imported_target}
                else:
                    candidates = {
                        item.fingerprint.relative_path: item
                        for item in global_symbols.get(reference.name, ())
                        if item.fingerprint.relative_path != source_path
                    }
                if len(candidates) != 1:
                    continue
                target_path = next(iter(candidates))
                relation = "unique-call" if reference.kind == "call" else "unique-name"
                weights[(source_path, target_path, relation)] += 2.0

        edges = tuple(
            GraphEdge(source, target, relation, round(weight, 8))
            for (source, target, relation), weight in sorted(weights.items())
        )
        scores = _page_rank(nodes, edges)
        return RepoGraph(nodes=nodes, edges=edges, scores=scores, diagnostics=tuple(diagnostics))


def _absolute_module(source: ParsedPythonFile, module: str, level: int) -> str:
    if level <= 0:
        return module
    source_parts = source.module_name.split(".") if source.module_name else []
    package_parts = source_parts if source.is_package else source_parts[:-1]
    keep = max(0, len(package_parts) - (level - 1))
    return ".".join([*package_parts[:keep], *([part for part in module.split(".") if part])])


def _unique_module(
    index: dict[str, list[ParsedPythonFile]],
    module: str,
) -> ParsedPythonFile | None:
    matches = index.get(module, ())
    return matches[0] if len(matches) == 1 else None


def _unique_symbol(
    index: dict[tuple[str, str], list[tuple[ParsedPythonFile, object]]],
    module: str,
    symbol: str,
) -> ParsedPythonFile | None:
    matches = index.get((module, symbol), ())
    return matches[0][0] if len(matches) == 1 else None


def _page_rank(nodes: tuple[str, ...], edges: tuple[GraphEdge, ...]) -> tuple[tuple[str, float], ...]:
    if not nodes:
        return ()
    count = len(nodes)
    initial = 1.0 / count
    scores = {node: initial for node in nodes}
    outgoing: dict[str, list[GraphEdge]] = {node: [] for node in nodes}
    for edge in edges:
        outgoing.setdefault(edge.source_path, []).append(edge)
    for edge_list in outgoing.values():
        edge_list.sort(key=lambda item: (item.target_path, item.relation, item.weight))

    for _ in range(MAX_ITERATIONS):
        sink = sum(scores[node] for node in nodes if not outgoing[node])
        next_scores = {
            node: (1.0 - DAMPING_FACTOR) / count + DAMPING_FACTOR * sink / count
            for node in nodes
        }
        for source in nodes:
            source_edges = outgoing[source]
            if not source_edges:
                continue
            total_weight = sum(edge.weight for edge in source_edges)
            if total_weight <= 0:
                continue
            for edge in source_edges:
                next_scores[edge.target_path] += (
                    DAMPING_FACTOR * scores[source] * edge.weight / total_weight
                )
        delta = sum(abs(next_scores[node] - scores[node]) for node in nodes)
        scores = next_scores
        if delta < CONVERGENCE_EPSILON:
            break
    return tuple((node, round(scores[node], 8)) for node in nodes)
