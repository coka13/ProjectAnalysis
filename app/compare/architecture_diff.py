"""Architecture comparison between two analysis runs (branches, releases, refactorings)."""

from __future__ import annotations

from typing import Any

from app.graph.model import EdgeKind, KnowledgeGraph, NodeKind

COMPARABLE_KINDS = (
    NodeKind.COMPONENT,
    NodeKind.MODULE,
    NodeKind.CLASS,
    NodeKind.INTERFACE,
    NodeKind.ABSTRACT_CLASS,
    NodeKind.STRUCT,
    NodeKind.API_ENDPOINT,
    NodeKind.TABLE,
    NodeKind.CONTAINER,
    NodeKind.DATABASE,
    NodeKind.QUEUE,
)

STRUCTURAL_EDGES = (
    EdgeKind.DEPENDS_ON,
    EdgeKind.USES,
    EdgeKind.INHERITS,
    EdgeKind.IMPLEMENTS,
    EdgeKind.COMMUNICATES_WITH,
    EdgeKind.REFERENCES,
)


def _key(node) -> str:
    return f"{node.kind}:{node.qualified_name or node.name}"


def _index(graph: KnowledgeGraph) -> dict[str, Any]:
    return {_key(node): node for node in graph.by_kind(*COMPARABLE_KINDS)}


def _edge_keys(graph: KnowledgeGraph) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for edge in graph.edges.values():
        if edge.kind not in STRUCTURAL_EDGES:
            continue
        source = graph.nodes.get(edge.source)
        target = graph.nodes.get(edge.target)
        if not source or not target:
            continue
        keys.add((_key(source), _key(target), edge.kind))
    return keys


def compare(base: KnowledgeGraph, head: KnowledgeGraph) -> dict[str, Any]:
    """Structural diff between two knowledge graphs."""
    base_nodes = _index(base)
    head_nodes = _index(head)

    added = sorted(set(head_nodes) - set(base_nodes))
    removed = sorted(set(base_nodes) - set(head_nodes))
    common = set(base_nodes) & set(head_nodes)

    changed: list[dict[str, Any]] = []
    for key in sorted(common):
        before, after = base_nodes[key], head_nodes[key]
        before_methods = len(before.attributes.get("methods") or [])
        after_methods = len(after.attributes.get("methods") or [])
        before_layer = before.attributes.get("layer", "unassigned")
        after_layer = after.attributes.get("layer", "unassigned")
        before_degree = base.degree(before.id)
        after_degree = head.degree(after.id)
        if before_methods != after_methods or before_layer != after_layer or abs(after_degree - before_degree) >= 3:
            changed.append(
                {
                    "name": key,
                    "methods": {"before": before_methods, "after": after_methods},
                    "layer": {"before": before_layer, "after": after_layer},
                    "degree": {"before": before_degree, "after": after_degree},
                }
            )

    base_edges = _edge_keys(base)
    head_edges = _edge_keys(head)
    added_edges = sorted(head_edges - base_edges)
    removed_edges = sorted(base_edges - head_edges)

    base_metrics = (base.meta or {}).get("metrics", {})
    head_metrics = (head.meta or {}).get("metrics", {})
    metric_delta = {
        key: {
            "before": base_metrics.get(key),
            "after": head_metrics.get(key),
            "delta": _delta(base_metrics.get(key), head_metrics.get(key)),
        }
        for key in ("score", "module_count", "density", "abstraction_ratio", "average_instability")
    }
    cycles_before = len(base_metrics.get("cycles") or [])
    cycles_after = len(head_metrics.get("cycles") or [])
    metric_delta["cycles"] = {"before": cycles_before, "after": cycles_after, "delta": cycles_after - cycles_before}

    impact = _impact(added, removed, added_edges, removed_edges, metric_delta)
    highlights = _highlights(added, removed, added_edges, removed_edges, metric_delta)
    risks = _risks(metric_delta, removed, added_edges)

    return {
        "added_components": [name for name in added if name.startswith(("component:", "module:"))][:40],
        "removed_components": [name for name in removed if name.startswith(("component:", "module:"))][:40],
        "added_nodes": added[:120],
        "removed_nodes": removed[:120],
        "changed_nodes": changed[:80],
        "added_dependencies": [
            {"from": a, "to": b, "kind": kind} for a, b, kind in added_edges[:100]
        ],
        "removed_dependencies": [
            {"from": a, "to": b, "kind": kind} for a, b, kind in removed_edges[:100]
        ],
        "metrics": metric_delta,
        "impact": impact,
        "highlights": highlights,
        "risks": risks,
        "summary": _summary(added, removed, changed, added_edges, removed_edges, metric_delta),
        "mermaid": _diff_mermaid(added, removed, changed),
    }


def _delta(before: Any, after: Any) -> Any:
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return round(after - before, 4)
    return None


def _impact(added, removed, added_edges, removed_edges, metric_delta) -> str:
    score_delta = metric_delta.get("score", {}).get("delta") or 0
    churn = len(added) + len(removed) + len(added_edges) + len(removed_edges)
    if abs(score_delta) >= 10 or churn > 120 or (metric_delta.get("cycles", {}).get("delta") or 0) > 0:
        return "high"
    if churn > 30 or abs(score_delta) >= 4:
        return "medium"
    return "low"


def _highlights(added, removed, added_edges, removed_edges, metric_delta) -> list[str]:
    highlights: list[str] = []
    if added:
        highlights.append(f"{len(added)} new architectural elements (e.g. {', '.join(n.split(':', 1)[-1] for n in added[:3])}).")
    if removed:
        highlights.append(f"{len(removed)} elements removed (e.g. {', '.join(n.split(':', 1)[-1] for n in removed[:3])}).")
    if added_edges:
        highlights.append(f"{len(added_edges)} new dependencies introduced.")
    if removed_edges:
        highlights.append(f"{len(removed_edges)} dependencies removed.")
    score = metric_delta.get("score", {})
    if score.get("delta"):
        direction = "improved" if score["delta"] > 0 else "regressed"
        highlights.append(f"Architecture score {direction} by {abs(score['delta'])} points.")
    return highlights or ["No structural differences were detected."]


def _risks(metric_delta, removed, added_edges) -> list[str]:
    risks: list[str] = []
    if (metric_delta.get("cycles", {}).get("delta") or 0) > 0:
        risks.append("New circular dependencies were introduced.")
    if (metric_delta.get("score", {}).get("delta") or 0) < -5:
        risks.append("The architecture score regressed significantly.")
    if len(added_edges) > 40:
        risks.append("A large number of new dependencies increases coupling.")
    removed_public = [name for name in removed if name.startswith("api_endpoint:")]
    if removed_public:
        risks.append(f"{len(removed_public)} API endpoint(s) were removed - verify backward compatibility.")
    return risks


def _summary(added, removed, changed, added_edges, removed_edges, metric_delta) -> str:
    score = metric_delta.get("score", {})
    return (
        f"Compared two analyses: {len(added)} added, {len(removed)} removed and {len(changed)} modified elements, "
        f"with {len(added_edges)} new and {len(removed_edges)} removed dependencies. "
        f"Architecture score moved from {score.get('before', '-')} to {score.get('after', '-')}."
    )


def _diff_mermaid(added: list[str], removed: list[str], changed: list[dict[str, Any]]) -> str:
    from app.diagrams.base import escape_label, safe_id

    lines = ["flowchart LR", '  subgraph ADDED["Added"]', "    direction TB"]
    for name in added[:12]:
        lines.append(f'    A_{safe_id(name)}["{escape_label(name.split(":", 1)[-1], 30)}"]')
    lines.append("  end")
    lines.append('  subgraph REMOVED["Removed"]')
    lines.append("    direction TB")
    for name in removed[:12]:
        lines.append(f'    R_{safe_id(name)}["{escape_label(name.split(":", 1)[-1], 30)}"]')
    lines.append("  end")
    lines.append('  subgraph CHANGED["Changed"]')
    lines.append("    direction TB")
    for item in changed[:12]:
        lines.append(f'    C_{safe_id(item["name"])}["{escape_label(item["name"].split(":", 1)[-1], 30)}"]')
    lines.append("  end")
    lines.append("  classDef added fill:#123524,stroke:#3ddc84,color:#dcffe9,rx:6,ry:6;")
    lines.append("  classDef removed fill:#3a1220,stroke:#ff5c7a,color:#ffe3ea,rx:6,ry:6;")
    lines.append("  classDef changed fill:#3a2f0b,stroke:#e0b33c,color:#fff6dd,rx:6,ry:6;")
    for name in added[:12]:
        lines.append(f"  class A_{safe_id(name)} added;")
    for name in removed[:12]:
        lines.append(f"  class R_{safe_id(name)} removed;")
    for item in changed[:12]:
        lines.append(f'  class C_{safe_id(item["name"])} changed;')
    return "\n".join(lines)
