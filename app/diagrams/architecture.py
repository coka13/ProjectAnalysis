"""High-level architecture and component diagrams."""

from __future__ import annotations

from typing import Any

from app.diagrams.base import (
    EDGE_ARROW,
    DiagramFilters,
    DiagramResult,
    EmptyDiagramError,
    collect_edges,
    escape_label,
    group_by_layer,
    header,
    layer_of,
    node_shape,
    safe_id,
    select_nodes,
    style_block,
    wrap_label,
)
from app.graph.model import EdgeKind, KnowledgeGraph, Node, NodeKind

LAYER_TITLE = {
    "presentation": "Presentation",
    "api": "API / Interface",
    "application": "Application",
    "domain": "Domain / Business Logic",
    "data": "Data & Persistence",
    "infrastructure": "Infrastructure",
    "unassigned": "Other Modules",
    "external": "External Systems",
}

ARCH_EDGE_KINDS = (
    EdgeKind.DEPENDS_ON,
    EdgeKind.USES,
    EdgeKind.COMMUNICATES_WITH,
    EdgeKind.CALLS,
    EdgeKind.READS,
    EdgeKind.WRITES,
    EdgeKind.EXPOSES,
)


def _technology(node: Node) -> str:
    attributes = node.attributes
    for key in ("technology", "image", "framework", "package_name"):
        value = attributes.get(key)
        if value:
            return str(value)
    languages = attributes.get("languages") or {}
    if isinstance(languages, dict) and languages:
        return max(languages.items(), key=lambda kv: kv[1])[0]
    return node.language or ""


def _component_label(node: Node, detail: str) -> str:
    label = wrap_label(node.name)
    technology = _technology(node)
    if technology and detail != "executive":
        label += f"<br/><i>{escape_label(technology, 22)}</i>"
    if detail == "detailed":
        files = node.attributes.get("file_count")
        if files:
            label += f"<br/><small>{files} files</small>"
    return label


def _external_nodes(graph: KnowledgeGraph, include_packages: bool) -> list[Node]:
    kinds = [NodeKind.DATABASE, NodeKind.QUEUE, NodeKind.EXTERNAL_API, NodeKind.DATA_STORE]
    if include_packages:
        kinds.append(NodeKind.EXTERNAL_PACKAGE)
    return graph.by_kind(*kinds)


def _render_groups(
    groups: dict[str, list[Node]],
    detail: str,
    aliases: dict[str, str],
    risky: set[str],
    focus: str,
) -> list[str]:
    lines: list[str] = []
    for layer, nodes in groups.items():
        if not nodes:
            continue
        lines.append(f'  subgraph L_{safe_id(layer)}["{LAYER_TITLE.get(layer, layer.title())}"]')
        lines.append("    direction LR")
        for node in nodes:
            alias = aliases[node.id]
            lines.append(f"    {alias}{node_shape(node, _component_label(node, detail))}")
        lines.append("  end")
    for layer, nodes in groups.items():
        for node in nodes:
            alias = aliases[node.id]
            if node.id in risky:
                lines.append(f"  class {alias} risk;")
            elif focus and focus.lower() in node.name.lower():
                lines.append(f"  class {alias} focusNode;")
            else:
                lines.append(f"  class {alias} layer_{safe_id(layer)};")
    return lines


def generate_architecture(graph: KnowledgeGraph, filters: DiagramFilters) -> DiagramResult:
    components = graph.by_kind(NodeKind.COMPONENT, NodeKind.SERVICE)
    if not components:
        components = graph.by_kind(NodeKind.MODULE)
    if not components:
        raise EmptyDiagramError("The project structure could not be resolved into components.")

    budget = filters.budget()
    selected, elided = select_nodes(graph, components, filters, budget=budget)
    if not selected:
        raise EmptyDiagramError("No components matched the requested filters.")

    externals = _external_nodes(graph, include_packages=False)
    actors = graph.by_kind(NodeKind.ACTOR)
    endpoints = graph.by_kind(NodeKind.API_ENDPOINT)
    gateway_nodes = [n for n in graph.by_kind(NodeKind.COMPONENT) if "gateway" in n.name.lower()]

    display = list(selected)
    display.extend(externals[: 10 if filters.detail != "executive" else 5])
    display.extend(actors)
    if filters.detail == "detailed":
        display.extend(endpoints[:10])

    unique: dict[str, Node] = {node.id: node for node in display}
    node_ids = set(unique)
    aliases = {node_id: safe_id(node_id) for node_id in node_ids}

    metrics = graph.meta.get("metrics", {}) if graph.meta else {}
    risky = {entry["id"] for entry in (metrics.get("hubs") or [])[:5]}
    risky |= {
        f"component:{item['module'].split('/')[0]}"
        for item in (metrics.get("coupling") or [])[:3]
        if item.get("coupling", 0) >= 8
    }
    risky &= node_ids

    groups = group_by_layer(list(unique.values()))
    lines = header("TB", "System Architecture")
    lines.extend(_render_groups(groups, filters.detail, aliases, risky, filters.focus))

    edges = collect_edges(graph, node_ids, ARCH_EDGE_KINDS, max_edges=120)
    payload_edges: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for edge in edges:
        pair = (edge.source, edge.target)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        arrow = EDGE_ARROW.get(edge.kind, "-->")
        label = edge.attributes.get("technology") or edge.kind.replace("_", " ")
        lines.append(f'  {aliases[edge.source]} {arrow}|"{escape_label(label, 20)}"| {aliases[edge.target]}')
        payload_edges.append({"source": edge.source, "target": edge.target, "kind": edge.kind, "label": label})

    lines.extend(f"  {line}" for line in style_block(list(groups.keys())))

    notes: list[str] = []
    if elided:
        notes.append(f"{elided} components were summarised out of the view.")
    if endpoints:
        notes.append(f"{len(endpoints)} API endpoints detected.")
    if gateway_nodes:
        notes.append("An API gateway component was detected.")

    payload_nodes = [
        {
            "id": node.id,
            "alias": aliases[node.id],
            "name": node.name,
            "kind": node.kind,
            "layer": layer_of(node),
            "technology": _technology(node),
            "responsibility": node.attributes.get("responsibility", ""),
            "file_count": node.attributes.get("file_count", 0),
            "module": node.module,
            "external": node.external,
            "risk": node.id in risky,
        }
        for node in unique.values()
    ]

    return DiagramResult(
        kind="architecture",
        title="System Architecture",
        mermaid="\n".join(lines),
        plantuml=_architecture_plantuml(groups, edges, graph),
        payload={
            "nodes": payload_nodes,
            "edges": payload_edges,
            "layers": {layer: len(nodes) for layer, nodes in groups.items()},
            "elided": elided,
            "legend": [
                {"key": "risk", "label": "High coupling / hub component"},
                {"key": "[( )]", "label": "Data store"},
                {"key": "{{ }}", "label": "External system"},
            ],
        },
        scope=filters.to_dict(),
        notes=notes,
    )


def generate_component(graph: KnowledgeGraph, filters: DiagramFilters) -> DiagramResult:
    """Component view: components, the interfaces they provide, and their boundaries."""
    components = graph.by_kind(NodeKind.COMPONENT)
    interfaces = graph.by_kind(NodeKind.INTERFACE)
    if not components and not interfaces:
        raise EmptyDiagramError("No components or interfaces were detected.")

    selected, elided = select_nodes(graph, components or graph.by_kind(NodeKind.MODULE), filters)
    selected_ids = {node.id for node in selected}

    interface_budget = {"executive": 0, "standard": 10, "detailed": 24}.get(filters.detail, 10)
    chosen_interfaces: list[Node] = []
    for interface in sorted(interfaces, key=lambda n: -graph.degree(n.id))[:interface_budget]:
        component_id = f"component:{(interface.module or '').split('/')[0]}"
        if component_id in selected_ids or not selected_ids:
            chosen_interfaces.append(interface)

    unique: dict[str, Node] = {node.id: node for node in [*selected, *chosen_interfaces]}
    if filters.include_external:
        for node in _external_nodes(graph, include_packages=False)[:8]:
            unique[node.id] = node
    if not unique:
        raise EmptyDiagramError("No components matched the requested filters.")

    aliases = {node_id: safe_id(node_id) for node_id in unique}
    lines = header("LR", "Component View")

    groups = group_by_layer(list(unique.values()))
    for layer, nodes in groups.items():
        lines.append(f'  subgraph B_{safe_id(layer)}["{LAYER_TITLE.get(layer, layer.title())} boundary"]')
        lines.append("    direction TB")
        for node in nodes:
            label = wrap_label(node.name)
            if node.kind == NodeKind.INTERFACE:
                label = f"«interface»<br/>{label}"
            lines.append(f"    {aliases[node.id]}{node_shape(node, label)}")
        lines.append("  end")

    for layer, nodes in groups.items():
        for node in nodes:
            lines.append(f"  class {aliases[node.id]} layer_{safe_id(layer)};")

    node_ids = set(unique)
    edges = collect_edges(
        graph,
        node_ids,
        (EdgeKind.DEPENDS_ON, EdgeKind.USES, EdgeKind.IMPLEMENTS, EdgeKind.CONTAINS, EdgeKind.COMMUNICATES_WITH),
        max_edges=110,
    )
    payload_edges = []
    for edge in edges:
        if edge.kind == EdgeKind.CONTAINS and graph.nodes[edge.target].kind != NodeKind.INTERFACE:
            continue
        arrow = "-.->" if edge.kind == EdgeKind.IMPLEMENTS else EDGE_ARROW.get(edge.kind, "-->")
        label = "provides" if edge.kind == EdgeKind.CONTAINS else edge.kind.replace("_", " ")
        lines.append(f'  {aliases[edge.source]} {arrow}|"{escape_label(label, 18)}"| {aliases[edge.target]}')
        payload_edges.append({"source": edge.source, "target": edge.target, "kind": edge.kind, "label": label})

    lines.extend(f"  {line}" for line in style_block(list(groups.keys())))

    notes = []
    if elided:
        notes.append(f"{elided} components hidden by the current budget.")
    if not chosen_interfaces:
        notes.append("No explicit interfaces were detected - consider introducing abstractions at module boundaries.")

    return DiagramResult(
        kind="component",
        title="Component Diagram",
        mermaid="\n".join(lines),
        payload={
            "nodes": [
                {
                    "id": node.id,
                    "alias": aliases[node.id],
                    "name": node.name,
                    "kind": node.kind,
                    "layer": layer_of(node),
                    "module": node.module,
                    "file": node.file,
                    "responsibility": node.attributes.get("responsibility", ""),
                }
                for node in unique.values()
            ],
            "edges": payload_edges,
            "elided": elided,
            "legend": [
                {"key": "«interface»", "label": "Provided interface"},
                {"key": "boundary", "label": "Layer boundary"},
            ],
        },
        scope=filters.to_dict(),
        notes=notes,
    )


def _architecture_plantuml(groups: dict[str, list[Node]], edges: list, graph: KnowledgeGraph) -> str:
    lines = [
        "@startuml",
        "skinparam backgroundColor transparent",
        "skinparam shadowing false",
        "skinparam componentStyle rectangle",
        "left to right direction",
    ]
    for layer, nodes in groups.items():
        lines.append(f'package "{LAYER_TITLE.get(layer, layer.title())}" {{')
        for node in nodes:
            keyword = "database" if node.kind in {NodeKind.DATABASE, NodeKind.TABLE, NodeKind.DATA_STORE} else (
                "actor" if node.kind == NodeKind.ACTOR else (
                    "queue" if node.kind == NodeKind.QUEUE else "component"
                )
            )
            lines.append(f'  {keyword} "{escape_label(node.name, 36)}" as {safe_id(node.id)}')
        lines.append("}")
    for edge in edges:
        label = edge.attributes.get("technology") or edge.kind.replace("_", " ")
        lines.append(f"{safe_id(edge.source)} --> {safe_id(edge.target)} : {escape_label(label, 20)}")
    lines.append("@enduml")
    return "\n".join(lines)
