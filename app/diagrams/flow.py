"""Data-flow, dependency and deployment diagrams."""

from __future__ import annotations

from typing import Any

from app.diagrams.base import (
    EDGE_ARROW,
    DiagramFilters,
    DiagramResult,
    EmptyDiagramError,
    collect_edges,
    escape_label,
    header,
    layer_of,
    node_shape,
    safe_id,
    select_nodes,
    style_block,
    wrap_label,
)
from app.graph.metrics import find_cycles, module_dependency_graph
from app.graph.model import EdgeKind, KnowledgeGraph, Node, NodeKind

DATA_KINDS = (NodeKind.DATABASE, NodeKind.TABLE, NodeKind.DATA_STORE)
SOURCE_KINDS = (NodeKind.ACTOR, NodeKind.EXTERNAL_API, NodeKind.QUEUE)
FLOW_EDGE_KINDS = (
    EdgeKind.READS,
    EdgeKind.WRITES,
    EdgeKind.USES,
    EdgeKind.CALLS,
    EdgeKind.COMMUNICATES_WITH,
    EdgeKind.EXPOSES,
    EdgeKind.DEPENDS_ON,
)


# --------------------------------------------------------------------------- #
# Data flow
# --------------------------------------------------------------------------- #
def generate_dataflow(graph: KnowledgeGraph, filters: DiagramFilters) -> DiagramResult:
    stores = graph.by_kind(*DATA_KINDS)
    processes = graph.by_kind(NodeKind.COMPONENT, NodeKind.SERVICE)
    externals = graph.by_kind(*SOURCE_KINDS)
    endpoints = graph.by_kind(NodeKind.API_ENDPOINT)

    if not stores and not externals:
        raise EmptyDiagramError("No data stores or external systems were detected for a data-flow view.")

    process_budget = max(6, filters.budget() // 2)
    selected_processes, elided = select_nodes(graph, processes, filters, budget=process_budget)
    store_budget = 12 if filters.detail != "detailed" else 30
    selected_stores = sorted(stores, key=lambda n: -graph.degree(n.id))[:store_budget]
    selected_externals = sorted(externals, key=lambda n: -graph.degree(n.id))[:10]
    selected_endpoints = sorted(endpoints, key=lambda n: -graph.degree(n.id))[
        : (8 if filters.detail != "executive" else 3)
    ]

    unique: dict[str, Node] = {}
    for node in [*selected_externals, *selected_endpoints, *selected_processes, *selected_stores]:
        unique[node.id] = node
    if not unique:
        raise EmptyDiagramError("Nothing to draw for the requested data-flow scope.")

    aliases = {node_id: safe_id(node_id) for node_id in unique}
    lines = header("LR", "Data Flow")

    zones: dict[str, list[Node]] = {
        "External Entities (untrusted)": [n for n in unique.values() if n.kind in SOURCE_KINDS],
        "Interface / Entry Points": [n for n in unique.values() if n.kind == NodeKind.API_ENDPOINT],
        "Processing (trusted)": [n for n in unique.values() if n.kind in {NodeKind.COMPONENT, NodeKind.SERVICE}],
        "Data Stores": [n for n in unique.values() if n.kind in DATA_KINDS],
    }
    for title, nodes in zones.items():
        if not nodes:
            continue
        lines.append(f'  subgraph Z_{safe_id(title)}["{title}"]')
        lines.append("    direction TB")
        for node in nodes:
            label = wrap_label(node.name)
            if node.kind == NodeKind.TABLE:
                columns = len(node.attributes.get("columns") or [])
                if columns and filters.detail == "detailed":
                    label += f"<br/><small>{columns} columns</small>"
            lines.append(f"    {aliases[node.id]}{node_shape(node, label)}")
        lines.append("  end")

    node_ids = set(unique)
    edges = collect_edges(graph, node_ids, FLOW_EDGE_KINDS, max_edges=120)
    payload_edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        if (edge.source, edge.target) in seen:
            continue
        seen.add((edge.source, edge.target))
        label = {
            EdgeKind.READS: "reads",
            EdgeKind.WRITES: "writes",
            EdgeKind.COMMUNICATES_WITH: "exchanges data",
            EdgeKind.EXPOSES: "handles",
        }.get(edge.kind, "data")
        arrow = "==>" if edge.kind == EdgeKind.WRITES else "-->"
        lines.append(f'  {aliases[edge.source]} {arrow}|"{label}"| {aliases[edge.target]}')
        payload_edges.append({"source": edge.source, "target": edge.target, "kind": edge.kind, "label": label})

    for node in unique.values():
        lines.append(f"  class {aliases[node.id]} layer_{safe_id(layer_of(node))};")
    lines.extend(f"  {line}" for line in style_block({layer_of(n) for n in unique.values()}))

    notes = ["Trust boundary: external entities must be validated before reaching processing components."]
    if elided:
        notes.append(f"{elided} processing components were omitted for readability.")
    if not stores:
        notes.append("No persistent storage detected - the system may be stateless or use an unrecognised store.")

    return DiagramResult(
        kind="dataflow",
        title="Data Flow Diagram",
        mermaid="\n".join(lines),
        payload={
            "nodes": [
                {
                    "id": node.id,
                    "alias": aliases[node.id],
                    "name": node.name,
                    "kind": node.kind,
                    "zone": next((title for title, group in zones.items() if node in group), ""),
                    "layer": layer_of(node),
                    "file": node.file,
                }
                for node in unique.values()
            ],
            "edges": payload_edges,
            "elided": elided,
            "legend": [
                {"key": "==>", "label": "write / persist"},
                {"key": "-->", "label": "read / transfer"},
                {"key": "untrusted", "label": "outside the trust boundary"},
            ],
        },
        scope=filters.to_dict(),
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Dependency graph
# --------------------------------------------------------------------------- #
def generate_dependency(graph: KnowledgeGraph, filters: DiagramFilters) -> DiagramResult:
    module_graph = module_dependency_graph(graph)
    if module_graph.number_of_nodes() == 0:
        raise EmptyDiagramError("No module dependencies were detected.")

    cycles = find_cycles(module_graph)
    cycle_members = {module for cycle in cycles for module in cycle}

    ranked = sorted(
        module_graph.nodes,
        key=lambda module: (
            module in cycle_members,
            module_graph.in_degree(module) + module_graph.out_degree(module),
        ),
        reverse=True,
    )
    if filters.modules:
        ranked = [m for m in ranked if any(m == mod or m.startswith(f"{mod}/") for mod in filters.modules)]
    if filters.focus:
        needle = filters.focus.lower()
        focused = [m for m in ranked if needle in m.lower()]
        if focused:
            neighbours = set(focused)
            for module in focused:
                neighbours |= set(module_graph.predecessors(module)) | set(module_graph.successors(module))
            ranked = [m for m in ranked if m in neighbours]

    limit = filters.budget()
    visible = ranked[:limit]
    elided = max(0, len(ranked) - len(visible))
    visible_set = set(visible)

    lines = header("LR", "Module Dependencies")
    aliases = {module: safe_id(f"mod_{module}") for module in visible}

    by_top: dict[str, list[str]] = {}
    for module in visible:
        by_top.setdefault(module.split("/")[0], []).append(module)

    for top, modules in by_top.items():
        if len(by_top) > 1:
            lines.append(f'  subgraph G_{safe_id(top)}["{escape_label(top, 30)}"]')
            lines.append("    direction TB")
        for module in modules:
            fan_in = module_graph.in_degree(module)
            fan_out = module_graph.out_degree(module)
            label = wrap_label(module)
            if filters.detail != "executive":
                label += f"<br/><small>in {fan_in} / out {fan_out}</small>"
            lines.append(f'    {aliases[module]}["{label}"]')
        if len(by_top) > 1:
            lines.append("  end")

    payload_edges: list[dict[str, Any]] = []
    cycle_edges = set()
    for cycle in cycles:
        for index, module in enumerate(cycle):
            cycle_edges.add((module, cycle[(index + 1) % len(cycle)]))

    edge_index = 0
    highlight_indexes: list[int] = []
    for source, target, data in module_graph.edges(data=True):
        if source not in visible_set or target not in visible_set:
            continue
        in_cycle = (source, target) in cycle_edges
        weight = data.get("weight", 1)
        arrow = "==>" if in_cycle else "-->"
        label = f"{weight}" if weight > 1 and filters.detail == "detailed" else ""
        suffix = f'|"{label}"|' if label else ""
        lines.append(f"  {aliases[source]} {arrow}{suffix} {aliases[target]}")
        if in_cycle:
            highlight_indexes.append(edge_index)
        payload_edges.append({"source": source, "target": target, "weight": weight, "cycle": in_cycle})
        edge_index += 1

    for module in visible:
        style_class = "risk" if module in cycle_members else f"layer_{safe_id(module_graph.nodes[module].get('layer', 'unassigned'))}"
        lines.append(f"  class {aliases[module]} {style_class};")
    lines.extend(f"  {line}" for line in style_block({module_graph.nodes[m].get("layer", "unassigned") for m in visible}))
    for index in highlight_indexes:
        lines.append(f"  linkStyle {index} stroke:#ff5c7a,stroke-width:2.5px;")

    notes = []
    if cycles:
        notes.append(f"{len(cycles)} circular dependency chain(s) detected and highlighted in red.")
    else:
        notes.append("No circular dependencies detected.")
    if elided:
        notes.append(f"{elided} lower-impact modules were hidden.")

    return DiagramResult(
        kind="dependency",
        title="Dependency Graph",
        mermaid="\n".join(lines),
        payload={
            "nodes": [
                {
                    "id": f"module:{module}",
                    "alias": aliases[module],
                    "name": module,
                    "fan_in": module_graph.in_degree(module),
                    "fan_out": module_graph.out_degree(module),
                    "in_cycle": module in cycle_members,
                    "layer": module_graph.nodes[module].get("layer", "unassigned"),
                }
                for module in visible
            ],
            "edges": payload_edges,
            "cycles": [{"modules": cycle} for cycle in cycles],
            "elided": elided,
            "legend": [
                {"key": "red", "label": "participates in a dependency cycle"},
                {"key": "in/out", "label": "fan-in / fan-out"},
            ],
        },
        scope=filters.to_dict(),
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Deployment
# --------------------------------------------------------------------------- #
def generate_deployment(graph: KnowledgeGraph, filters: DiagramFilters) -> DiagramResult:
    containers = graph.by_kind(NodeKind.CONTAINER)
    infra_components = [
        node
        for node in graph.by_kind(NodeKind.COMPONENT)
        if node.attributes.get("origin") == "kubernetes" or node.language in {"kubernetes", "docker"}
    ]
    stores = [node for node in graph.by_kind(NodeKind.DATABASE, NodeKind.QUEUE) if node.attributes.get("origin") in {"compose", "kubernetes"} or node.external]
    if not containers and not infra_components:
        raise EmptyDiagramError("No Docker, Compose or Kubernetes deployment descriptors were found.")

    actors = graph.by_kind(NodeKind.ACTOR)
    unique: dict[str, Node] = {}
    for node in [*actors, *infra_components, *containers, *stores]:
        unique[node.id] = node

    aliases = {node_id: safe_id(node_id) for node_id in unique}
    lines = header("TB", "Deployment View")

    tiers: dict[str, list[Node]] = {
        "Clients": [n for n in unique.values() if n.kind == NodeKind.ACTOR],
        "Edge / Ingress": [
            n for n in unique.values()
            if (
                n.kind == NodeKind.COMPONENT
                and str(n.attributes.get("workload", "")).lower() in {"ingress", "service"}
            )
            or any(token in n.name.lower() for token in ("nginx", "traefik", "gateway", "ingress", "lb", "proxy"))
        ],
        "Application Runtime": [
            n for n in unique.values()
            if n.kind == NodeKind.CONTAINER
        ],
        "Backing Services": [n for n in unique.values() if n.kind in {NodeKind.DATABASE, NodeKind.QUEUE, NodeKind.DATA_STORE}],
    }
    assigned: set[str] = set()
    for nodes in tiers.values():
        for node in nodes:
            assigned.add(node.id)
    tiers["Other Infrastructure"] = [n for n in unique.values() if n.id not in assigned]

    for title, nodes in tiers.items():
        if not nodes:
            continue
        lines.append(f'  subgraph T_{safe_id(title)}["{title}"]')
        lines.append("    direction LR")
        for node in nodes:
            label = wrap_label(node.name)
            details: list[str] = []
            replicas = node.attributes.get("replicas")
            if replicas and int(replicas or 1) > 1:
                details.append(f"x{replicas}")
            image = node.attributes.get("image") or (node.attributes.get("images") or [""])[0]
            if image and filters.detail != "executive":
                details.append(escape_label(str(image), 24))
            ports = node.attributes.get("ports") or []
            if ports and filters.detail == "detailed":
                details.append("ports " + ",".join(str(p) for p in ports[:3]))
            if details:
                label += "<br/><small>" + " · ".join(details) + "</small>"
            lines.append(f"    {aliases[node.id]}{node_shape(node, label)}")
        lines.append("  end")

    node_ids = set(unique)
    edges = collect_edges(
        graph, node_ids, (EdgeKind.COMMUNICATES_WITH, EdgeKind.DEPLOYS, EdgeKind.USES, EdgeKind.CALLS), max_edges=90
    )
    payload_edges = []
    for edge in edges:
        arrow = EDGE_ARROW.get(edge.kind, "-->")
        lines.append(f"  {aliases[edge.source]} {arrow} {aliases[edge.target]}")
        payload_edges.append({"source": edge.source, "target": edge.target, "kind": edge.kind})

    for node in unique.values():
        lines.append(f"  class {aliases[node.id]} layer_{safe_id(layer_of(node))};")
    lines.extend(f"  {line}" for line in style_block({layer_of(n) for n in unique.values()}))

    notes = []
    if not stores:
        notes.append("No managed data services were declared in the deployment descriptors.")
    replicated = [n.name for n in unique.values() if int(n.attributes.get("replicas") or 1) > 1]
    if replicated:
        notes.append("Horizontally scaled workloads: " + ", ".join(replicated[:5]) + ".")

    return DiagramResult(
        kind="deployment",
        title="Deployment Diagram",
        mermaid="\n".join(lines),
        payload={
            "nodes": [
                {
                    "id": node.id,
                    "alias": aliases[node.id],
                    "name": node.name,
                    "kind": node.kind,
                    "image": node.attributes.get("image", ""),
                    "replicas": node.attributes.get("replicas", 1),
                    "ports": node.attributes.get("ports", []),
                    "workload": node.attributes.get("workload", ""),
                    "file": node.file,
                }
                for node in unique.values()
            ],
            "edges": payload_edges,
            "tiers": {title: len(nodes) for title, nodes in tiers.items() if nodes},
            "legend": [
                {"key": "[[ ]]", "label": "container / workload"},
                {"key": "[( )]", "label": "managed data service"},
            ],
        },
        scope=filters.to_dict(),
        notes=notes,
    )
