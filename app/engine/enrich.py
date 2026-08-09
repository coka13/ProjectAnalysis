"""Third pass: derive layers, components, services and data-flow semantics."""

from __future__ import annotations

import re
from collections import defaultdict

from app.graph.model import EdgeKind, KnowledgeGraph, Node, NodeKind

LAYER_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("presentation", ("ui", "web", "frontend", "client", "views", "pages", "components", "widgets", "screens", "static", "templates")),
    ("api", ("api", "controllers", "controller", "routes", "route", "endpoints", "handlers", "resources", "rest", "graphql", "rpc", "grpc")),
    ("application", ("application", "usecases", "use_cases", "commands", "queries", "workflows", "orchestration", "jobs", "tasks")),
    ("domain", ("domain", "core", "business", "services", "service", "logic", "model", "models", "entities", "engine")),
    ("data", ("data", "dal", "repository", "repositories", "persistence", "storage", "store", "db", "database", "migrations", "orm", "dao")),
    ("infrastructure", ("infra", "infrastructure", "config", "configuration", "common", "shared", "utils", "util", "helpers", "lib", "internal", "platform", "security", "logging", "deploy", "scripts", "build")),
]

STEREOTYPE_LAYER = {
    "controller": "api",
    "view": "presentation",
    "component": "presentation",
    "service": "domain",
    "manager": "domain",
    "handler": "application",
    "repository": "data",
    "entity": "data",
    "model": "domain",
    "store": "data",
    "configuration": "infrastructure",
    "middleware": "api",
    "client": "infrastructure",
    "adapter": "infrastructure",
}

SERVICE_HINTS = ("service", "svc", "api", "worker", "daemon", "server", "gateway", "microservice")


def classify_layer(path: str, stereotype: str = "") -> str:
    tokens = {token.lower() for token in re.split(r"[/\\._\-]+", path) if token}
    for layer, keywords in LAYER_RULES:
        if tokens & set(keywords):
            return layer
    if stereotype:
        return STEREOTYPE_LAYER.get(stereotype, "unassigned")
    return "unassigned"


def enrich(graph: KnowledgeGraph) -> None:
    """Annotate the graph with architectural semantics."""
    _assign_layers(graph)
    _build_components(graph)
    _derive_services(graph)
    _derive_dataflow(graph)
    _derive_actors(graph)
    _mark_states(graph)


# --------------------------------------------------------------------------- #
def _assign_layers(graph: KnowledgeGraph) -> None:
    for module in graph.by_kind(NodeKind.MODULE):
        layer = classify_layer(module.qualified_name or module.name)
        module.attributes["layer"] = layer

    for node in graph.nodes.values():
        if node.kind == NodeKind.MODULE or node.external:
            continue
        stereotype = node.attributes.get("stereotype", "")
        module_node = graph.nodes.get(f"module:{node.module}") if node.module else None
        layer = ""
        if node.file:
            layer = classify_layer(node.file, stereotype)
        if layer in ("", "unassigned") and module_node:
            layer = module_node.attributes.get("layer", "unassigned")
        if layer == "unassigned" and stereotype:
            layer = STEREOTYPE_LAYER.get(stereotype, "unassigned")
        if node.kind == NodeKind.API_ENDPOINT:
            layer = "api"
        elif node.kind in {NodeKind.TABLE, NodeKind.DATABASE, NodeKind.DATA_STORE}:
            layer = "data"
        node.attributes["layer"] = layer or "unassigned"

    # A module inherits the dominant layer of its content when still unassigned.
    for module in graph.by_kind(NodeKind.MODULE):
        if module.attributes.get("layer") != "unassigned":
            continue
        counts: dict[str, int] = defaultdict(int)
        for edge in graph.out_edges(module.id, [EdgeKind.CONTAINS]):
            child = graph.nodes.get(edge.target)
            if child:
                counts[child.attributes.get("layer", "unassigned")] += 1
        counts.pop("unassigned", None)
        if counts:
            module.attributes["layer"] = max(counts.items(), key=lambda kv: kv[1])[0]


def _build_components(graph: KnowledgeGraph) -> None:
    """Group modules into logical components (top-level packages)."""
    groups: dict[str, list[Node]] = defaultdict(list)
    for module in graph.by_kind(NodeKind.MODULE):
        name = module.qualified_name or module.name
        top = name.split("/")[0] or "(root)"
        groups[top].append(module)

    for top, modules in groups.items():
        if top == "(root)" and len(groups) > 1:
            continue
        files = sum(
            1
            for module in modules
            for edge in graph.out_edges(module.id, [EdgeKind.CONTAINS])
            if graph.nodes.get(edge.target, Node(id="", kind="", name="")).kind == NodeKind.FILE
        )
        layers = defaultdict(int)
        languages: dict[str, int] = defaultdict(int)
        for module in modules:
            layers[module.attributes.get("layer", "unassigned")] += 1
            if module.language:
                languages[module.language] += 1
        dominant_layer = max(layers.items(), key=lambda kv: (kv[1], kv[0] != "unassigned"))[0] if layers else "unassigned"
        component = graph.add_node(
            Node(
                id=f"component:{top}",
                kind=NodeKind.COMPONENT,
                name=top,
                qualified_name=top,
                module=top,
                language=max(languages.items(), key=lambda kv: kv[1])[0] if languages else "",
                attributes={
                    "layer": dominant_layer,
                    "module_count": len(modules),
                    "file_count": files,
                    "languages": dict(languages),
                    "responsibility": _responsibility(top, dominant_layer),
                },
            )
        )
        for module in modules:
            graph.link(component.id, module.id, EdgeKind.CONTAINS)

    # Component level dependencies derived from module dependencies.
    for edge in list(graph.edges.values()):
        if edge.kind not in {EdgeKind.DEPENDS_ON, EdgeKind.IMPORTS, EdgeKind.USES, EdgeKind.CALLS}:
            continue
        source = graph.nodes.get(edge.source)
        target = graph.nodes.get(edge.target)
        if not source or not target or target.external:
            continue
        source_component = f"component:{(source.module or '').split('/')[0]}"
        target_component = f"component:{(target.module or '').split('/')[0]}"
        if source_component == target_component:
            continue
        if source_component in graph.nodes and target_component in graph.nodes:
            graph.link(source_component, target_component, EdgeKind.DEPENDS_ON)


def _responsibility(name: str, layer: str) -> str:
    readable = name.replace("_", " ").replace("-", " ").strip() or "root"
    mapping = {
        "presentation": f"User facing surface for {readable}",
        "api": f"External interface and request handling for {readable}",
        "application": f"Use-case orchestration for {readable}",
        "domain": f"Business rules and domain logic for {readable}",
        "data": f"Persistence and data access for {readable}",
        "infrastructure": f"Cross-cutting technical capabilities for {readable}",
    }
    return mapping.get(layer, f"Module group: {readable}")


def _derive_services(graph: KnowledgeGraph) -> None:
    """Promote components that look like deployable services."""
    containers = {node.name.lower(): node for node in graph.by_kind(NodeKind.CONTAINER)}
    for component in graph.by_kind(NodeKind.COMPONENT):
        name = component.name.lower()
        is_service = any(hint in name for hint in SERVICE_HINTS) or name in containers
        endpoints = [
            edge.target
            for edge in graph.out_edges(component.id, [EdgeKind.CONTAINS])
            if graph.nodes.get(edge.target, Node(id="", kind="", name="")).kind == NodeKind.API_ENDPOINT
        ]
        if is_service or endpoints:
            component.attributes["is_service"] = True
            container = containers.get(name)
            if container:
                graph.link(container.id, component.id, EdgeKind.DEPLOYS)


def _derive_dataflow(graph: KnowledgeGraph) -> None:
    """Add read/write semantics between code and data stores."""
    stores = graph.by_kind(NodeKind.TABLE, NodeKind.DATABASE, NodeKind.DATA_STORE)
    store_ids = {node.id for node in stores}
    for edge in list(graph.edges.values()):
        if edge.target not in store_ids:
            continue
        if edge.kind in {EdgeKind.USES, EdgeKind.CALLS, EdgeKind.DEPENDS_ON}:
            graph.link(edge.source, edge.target, EdgeKind.READS)

    # Endpoint -> handler -> store chains become explicit data flows.
    for endpoint in graph.by_kind(NodeKind.API_ENDPOINT):
        visited: set[str] = set()
        frontier = [edge.target for edge in graph.out_edges(endpoint.id, [EdgeKind.EXPOSES])]
        depth = 0
        while frontier and depth < 4:
            next_frontier: list[str] = []
            for node_id in frontier:
                if node_id in visited:
                    continue
                visited.add(node_id)
                for edge in graph.out_edges(node_id, [EdgeKind.CALLS, EdgeKind.USES, EdgeKind.READS, EdgeKind.WRITES]):
                    if edge.target in store_ids:
                        graph.link(endpoint.id, edge.target, EdgeKind.READS, derived=True)
                    else:
                        next_frontier.append(edge.target)
            frontier = next_frontier
            depth += 1


def _derive_actors(graph: KnowledgeGraph) -> None:
    if not graph.by_kind(NodeKind.API_ENDPOINT) and not graph.by_kind(NodeKind.CONTAINER):
        return
    actor = graph.add_node(
        Node(
            id="actor:user",
            kind=NodeKind.ACTOR,
            name="User",
            qualified_name="User",
            attributes={"layer": "presentation", "description": "External consumer of the system"},
        )
    )
    for endpoint in graph.by_kind(NodeKind.API_ENDPOINT):
        graph.link(actor.id, endpoint.id, EdgeKind.CALLS)
    for component in graph.by_kind(NodeKind.COMPONENT):
        if component.attributes.get("layer") == "presentation":
            graph.link(actor.id, component.id, EdgeKind.USES)


def _mark_states(graph: KnowledgeGraph) -> None:
    """Flag enums that look like state machines and infer transitions."""
    for enum_node in graph.by_kind(NodeKind.ENUM):
        members = [str(m) for m in (enum_node.attributes.get("members") or [])]
        if len(members) < 2:
            continue
        name = enum_node.name.lower()
        looks_like_state = any(token in name for token in ("state", "status", "phase", "stage", "step", "mode"))
        member_hints = {"pending", "created", "new", "active", "running", "started", "completed", "done",
                        "failed", "error", "cancelled", "canceled", "closed", "approved", "rejected", "draft"}
        overlap = len({m.lower() for m in members} & member_hints)
        if looks_like_state or overlap >= 2:
            enum_node.attributes["is_state_machine"] = True
            enum_node.attributes["states"] = members
