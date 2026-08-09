"""Architecture metrics: coupling, cohesion, cycles, layering and scoring."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import networkx as nx

from app.graph.model import EdgeKind, KnowledgeGraph, Node, NodeKind

LAYER_ORDER = ["presentation", "api", "application", "domain", "data", "infrastructure", "unassigned"]
LAYER_RANK = {name: index for index, name in enumerate(LAYER_ORDER)}

GOD_CLASS_METHODS = 20
GOD_CLASS_DEPENDENCIES = 12
HIGH_FANOUT = 10


def _module_of(node: Node) -> str:
    return node.module or "(root)"


def module_dependency_graph(graph: KnowledgeGraph) -> nx.DiGraph:
    """Collapse file/type level edges into a module level dependency graph."""
    result = nx.DiGraph()
    for node in graph.by_kind(NodeKind.MODULE):
        result.add_node(node.qualified_name or node.name, layer=node.attributes.get("layer", "unassigned"))

    relation_kinds = {
        EdgeKind.IMPORTS,
        EdgeKind.USES,
        EdgeKind.CALLS,
        EdgeKind.DEPENDS_ON,
        EdgeKind.COMPOSES,
        EdgeKind.INHERITS,
        EdgeKind.IMPLEMENTS,
        EdgeKind.ASSOCIATES,
        EdgeKind.AGGREGATES,
    }
    for edge in graph.edges.values():
        if edge.kind not in relation_kinds:
            continue
        source = graph.nodes.get(edge.source)
        target = graph.nodes.get(edge.target)
        if not source or not target or target.external:
            continue
        source_module = _module_of(source)
        target_module = _module_of(target)
        if source_module == target_module:
            continue
        if not result.has_node(source_module):
            result.add_node(source_module, layer="unassigned")
        if not result.has_node(target_module):
            result.add_node(target_module, layer="unassigned")
        if result.has_edge(source_module, target_module):
            result[source_module][target_module]["weight"] += 1
        else:
            result.add_edge(source_module, target_module, weight=1)
    return result


def find_cycles(graph: nx.DiGraph, limit: int = 25) -> list[list[str]]:
    cycles: list[list[str]] = []
    try:
        for cycle in nx.simple_cycles(graph):
            if len(cycle) > 1:
                cycles.append(cycle)
            if len(cycles) >= limit:
                break
    except (nx.NetworkXError, RecursionError):  # pragma: no cover - very large graphs
        return cycles
    cycles.sort(key=len)
    return cycles


def coupling_report(module_graph: nx.DiGraph, limit: int = 25) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for module in module_graph.nodes:
        fan_out = module_graph.out_degree(module)
        fan_in = module_graph.in_degree(module)
        total = fan_in + fan_out
        instability = round(fan_out / total, 3) if total else 0.0
        report.append(
            {
                "module": module,
                "fan_in": fan_in,
                "fan_out": fan_out,
                "instability": instability,
                "coupling": total,
            }
        )
    report.sort(key=lambda item: (-item["coupling"], item["module"]))
    return report[:limit]


def god_classes(graph: KnowledgeGraph, limit: int = 15) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for node in graph.by_kind(NodeKind.CLASS, NodeKind.ABSTRACT_CLASS, NodeKind.STRUCT):
        methods = node.attributes.get("methods") or []
        properties = node.attributes.get("properties") or []
        dependencies = len(
            {
                edge.target
                for edge in graph.out_edges(node.id, [EdgeKind.CALLS, EdgeKind.USES, EdgeKind.COMPOSES, EdgeKind.DEPENDS_ON])
            }
        )
        score = len(methods) / GOD_CLASS_METHODS + dependencies / GOD_CLASS_DEPENDENCIES + len(properties) / 30
        if len(methods) >= GOD_CLASS_METHODS or dependencies >= GOD_CLASS_DEPENDENCIES:
            findings.append(
                {
                    "id": node.id,
                    "name": node.name,
                    "file": node.file,
                    "methods": len(methods),
                    "properties": len(properties),
                    "dependencies": dependencies,
                    "severity": round(min(score, 3.0), 2),
                }
            )
    findings.sort(key=lambda item: -item["severity"])
    return findings[:limit]


def hub_nodes(graph: KnowledgeGraph, limit: int = 15) -> list[dict[str, Any]]:
    scored = []
    for node in graph.nodes.values():
        if node.external or node.kind in {NodeKind.FILE, NodeKind.MODULE}:
            continue
        degree = graph.degree(node.id)
        if degree >= HIGH_FANOUT:
            scored.append({"id": node.id, "name": node.name, "kind": node.kind, "degree": degree, "file": node.file})
    scored.sort(key=lambda item: -item["degree"])
    return scored[:limit]


def layering_violations(graph: KnowledgeGraph, limit: int = 25) -> list[dict[str, Any]]:
    """Detect dependencies that flow against the intended layer direction."""
    violations: list[dict[str, Any]] = []
    for edge in graph.edges.values():
        if edge.kind not in {EdgeKind.IMPORTS, EdgeKind.USES, EdgeKind.CALLS, EdgeKind.DEPENDS_ON}:
            continue
        source = graph.nodes.get(edge.source)
        target = graph.nodes.get(edge.target)
        if not source or not target or target.external:
            continue
        source_layer = source.attributes.get("layer", "unassigned")
        target_layer = target.attributes.get("layer", "unassigned")
        if source_layer == "unassigned" or target_layer == "unassigned" or source_layer == target_layer:
            continue
        if LAYER_RANK[target_layer] < LAYER_RANK[source_layer]:
            violations.append(
                {
                    "from": source.qualified_name or source.name,
                    "from_layer": source_layer,
                    "to": target.qualified_name or target.name,
                    "to_layer": target_layer,
                    "kind": edge.kind,
                    "file": source.file,
                }
            )
    violations.sort(key=lambda item: item["from"])
    return violations[:limit]


def orphan_modules(module_graph: nx.DiGraph, limit: int = 15) -> list[str]:
    return [
        module
        for module in module_graph.nodes
        if module_graph.in_degree(module) == 0 and module_graph.out_degree(module) == 0
    ][:limit]


def detect_patterns(graph: KnowledgeGraph) -> list[dict[str, Any]]:
    """Heuristic design-pattern detection based on structure and naming."""
    stereotypes: dict[str, int] = defaultdict(int)
    for node in graph.by_kind(NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ABSTRACT_CLASS, NodeKind.STRUCT):
        stereotype = node.attributes.get("stereotype") or ""
        if stereotype:
            stereotypes[stereotype] += 1

    patterns: list[dict[str, Any]] = []

    def add(name: str, confidence: float, evidence: str) -> None:
        if confidence >= 0.35:
            patterns.append({"pattern": name, "confidence": round(min(confidence, 0.99), 2), "evidence": evidence})

    if stereotypes.get("repository"):
        add("Repository", 0.5 + 0.1 * min(stereotypes["repository"], 4), f"{stereotypes['repository']} repository types")
    if stereotypes.get("controller") and stereotypes.get("service"):
        add(
            "MVC / Layered",
            0.5 + 0.05 * min(stereotypes["controller"] + stereotypes["service"], 8),
            f"{stereotypes['controller']} controllers and {stereotypes['service']} services",
        )
    if stereotypes.get("factory"):
        add("Factory", 0.4 + 0.1 * min(stereotypes["factory"], 3), f"{stereotypes['factory']} factory types")
    if stereotypes.get("adapter"):
        add("Adapter", 0.4 + 0.1 * min(stereotypes["adapter"], 3), f"{stereotypes['adapter']} adapter types")
    if stereotypes.get("middleware"):
        add("Chain of Responsibility", 0.4, f"{stereotypes['middleware']} middleware components")

    interfaces = graph.by_kind(NodeKind.INTERFACE)
    implementations = sum(1 for edge in graph.edges.values() if edge.kind == EdgeKind.IMPLEMENTS)
    if interfaces and implementations:
        add(
            "Dependency Inversion / DI",
            0.4 + 0.02 * min(implementations, 20),
            f"{len(interfaces)} interfaces with {implementations} implementations",
        )

    observers = [n for n in graph.nodes.values() if any(t in n.name.lower() for t in ("observer", "listener", "subscriber", "handler"))]
    if len(observers) >= 3:
        add("Observer / Event-driven", 0.45, f"{len(observers)} listener-like types")

    singletons = [
        n
        for n in graph.by_kind(NodeKind.CLASS)
        if any(m.get("name") in {"get_instance", "getInstance", "instance"} for m in (n.attributes.get("methods") or []))
    ]
    if singletons:
        add("Singleton", 0.55, f"{len(singletons)} types expose an instance accessor")

    if graph.by_kind(NodeKind.QUEUE):
        add("Message-driven integration", 0.6, f"{len(graph.by_kind(NodeKind.QUEUE))} queue/broker integrations")

    if len(graph.by_kind(NodeKind.CONTAINER)) >= 3:
        add("Microservices / Containerised deployment", 0.55, f"{len(graph.by_kind(NodeKind.CONTAINER))} containers")

    patterns.sort(key=lambda item: -item["confidence"])
    return patterns


def compute(graph: KnowledgeGraph) -> dict[str, Any]:
    """Compute the full architecture metric set and an overall score."""
    module_graph = module_dependency_graph(graph)
    cycles = find_cycles(module_graph)
    coupling = coupling_report(module_graph)
    gods = god_classes(graph)
    hubs = hub_nodes(graph)
    violations = layering_violations(graph)
    patterns = detect_patterns(graph)

    module_count = max(module_graph.number_of_nodes(), 1)
    edge_count = module_graph.number_of_edges()
    density = edge_count / (module_count * max(module_count - 1, 1)) if module_count > 1 else 0.0
    avg_instability = sum(item["instability"] for item in coupling) / len(coupling) if coupling else 0.0

    penalties = {
        "cycles": min(len(cycles) * 6, 25),
        "god_classes": min(len(gods) * 4, 16),
        "layering": min(len(violations) * 3, 18),
        "coupling": min(int(density * 100), 15),
        "hubs": min(len(hubs) * 2, 10),
    }
    score = max(0, 100 - sum(penalties.values()))
    interfaces = len(graph.by_kind(NodeKind.INTERFACE))
    types = len(graph.by_kind(NodeKind.CLASS, NodeKind.ABSTRACT_CLASS, NodeKind.STRUCT)) or 1
    abstraction = round(interfaces / (interfaces + types), 3)
    score = min(100, score + (5 if abstraction > 0.15 else 0))

    return {
        "score": int(score),
        "grade": _grade(score),
        "penalties": penalties,
        "abstraction_ratio": abstraction,
        "module_count": module_graph.number_of_nodes(),
        "module_dependency_count": edge_count,
        "density": round(density, 4),
        "average_instability": round(avg_instability, 3),
        "cycles": [{"modules": cycle, "length": len(cycle)} for cycle in cycles],
        "coupling": coupling,
        "god_classes": gods,
        "hubs": hubs,
        "layering_violations": violations,
        "orphan_modules": orphan_modules(module_graph),
        "patterns": patterns,
        "layers": layer_summary(graph),
    }


def layer_summary(graph: KnowledgeGraph) -> dict[str, int]:
    summary: dict[str, int] = defaultdict(int)
    for node in graph.by_kind(NodeKind.MODULE):
        summary[node.attributes.get("layer", "unassigned")] += 1
    return dict(sorted(summary.items(), key=lambda kv: LAYER_RANK.get(kv[0], 99)))


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "E"


def _pagerank(nx_graph, alpha: float = 0.85, max_iter: int = 60, tol: float = 1.0e-6) -> dict[str, float]:
    """Pure-Python PageRank.

    ``networkx.pagerank`` delegates to SciPy/NumPy; implementing the power
    iteration here keeps the dependency footprint to pure-Python wheels.
    """
    nodes = list(nx_graph.nodes)
    count = len(nodes)
    if count == 0:
        return {}
    ranks = {node: 1.0 / count for node in nodes}
    out_weight = {node: sum(data.get("weight", 1.0) for _, _, data in nx_graph.out_edges(node, data=True)) for node in nodes}
    dangling = [node for node in nodes if out_weight[node] <= 0]
    teleport = (1.0 - alpha) / count

    for _ in range(max_iter):
        nxt = dict.fromkeys(nodes, 0.0)
        dangling_mass = alpha * sum(ranks[node] for node in dangling) / count
        for source, target, data in nx_graph.edges(data=True):
            share = data.get("weight", 1.0) / out_weight[source]
            nxt[target] += alpha * ranks[source] * share
        for node in nodes:
            nxt[node] += teleport + dangling_mass
        delta = sum(abs(nxt[node] - ranks[node]) for node in nodes)
        ranks = nxt
        if delta < tol * count:
            break
    return ranks


def centrality(graph: KnowledgeGraph, top: int = 20) -> list[dict[str, Any]]:
    """PageRank based importance of non-external nodes (used for diagram pruning)."""
    nx_graph = graph.to_networkx()
    if nx_graph.number_of_nodes() == 0:
        return []
    ranks = _pagerank(nx_graph)
    scored = [
        {"id": node_id, "score": round(rank, 6), "name": graph.nodes[node_id].name}
        for node_id, rank in ranks.items()
        if node_id in graph.nodes and not graph.nodes[node_id].external
    ]
    scored.sort(key=lambda item: -item["score"])
    return scored[:top]


def entropy(values: list[int]) -> float:
    total = sum(values)
    if total <= 0:
        return 0.0
    return round(-sum((v / total) * math.log2(v / total) for v in values if v > 0), 3)
