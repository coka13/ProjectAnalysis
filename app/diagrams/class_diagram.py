"""UML class diagram generation (Mermaid classDiagram + PlantUML)."""

from __future__ import annotations

from typing import Any

from app.diagrams.base import (
    DiagramFilters,
    DiagramResult,
    EmptyDiagramError,
    collect_edges,
    escape_label,
    safe_id,
    select_nodes,
)
from app.graph.model import EdgeKind, KnowledgeGraph, Node, NodeKind

VISIBILITY_SYMBOL = {"public": "+", "private": "-", "protected": "#", "package": "~", "internal": "~"}

RELATION_MERMAID = {
    EdgeKind.INHERITS: "<|--",
    EdgeKind.IMPLEMENTS: "<|..",
    EdgeKind.COMPOSES: "*--",
    EdgeKind.AGGREGATES: "o--",
    EdgeKind.ASSOCIATES: "--",
    EdgeKind.USES: "..>",
    EdgeKind.DEPENDS_ON: "..>",
}

RELATION_PLANTUML = {
    EdgeKind.INHERITS: "<|--",
    EdgeKind.IMPLEMENTS: "<|..",
    EdgeKind.COMPOSES: "*--",
    EdgeKind.AGGREGATES: "o--",
    EdgeKind.ASSOCIATES: "--",
    EdgeKind.USES: "..>",
    EdgeKind.DEPENDS_ON: "..>",
}

RELATION_KINDS = tuple(RELATION_MERMAID.keys())

STEREOTYPE_ANNOTATION = {
    NodeKind.INTERFACE: "interface",
    NodeKind.ABSTRACT_CLASS: "abstract",
    NodeKind.ENUM: "enumeration",
    NodeKind.STRUCT: "struct",
}


def _signature(member: dict[str, Any], detail: str) -> str:
    symbol = VISIBILITY_SYMBOL.get(member.get("visibility", "public"), "+")
    name = escape_label(str(member.get("name", "")), 34)
    params = member.get("params") or []
    if detail == "detailed" and params:
        rendered = ", ".join(
            escape_label(f"{p.get('name', '')}: {p.get('type', '')}".strip(": "), 24) for p in params[:4]
        )
        if len(params) > 4:
            rendered += ", …"
    else:
        rendered = "" if not params else f"{len(params)} args"
    returns = escape_label(str(member.get("returns") or ""), 18)
    suffix = f" {returns}" if returns and detail != "executive" else ""
    marker = "$" if member.get("is_static") else ("*" if member.get("is_abstract") else "")
    return f"{symbol}{name}({rendered}){suffix}{marker}".strip()


def _property_line(prop: dict[str, Any]) -> str:
    symbol = VISIBILITY_SYMBOL.get(prop.get("visibility", "private"), "-")
    declared = escape_label(str(prop.get("type") or ""), 22)
    name = escape_label(str(prop.get("name", "")), 28)
    return f"{symbol}{declared} {name}".replace("  ", " ").strip()


def generate(graph: KnowledgeGraph, filters: DiagramFilters) -> DiagramResult:
    candidates = graph.by_kind(
        NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ABSTRACT_CLASS, NodeKind.STRUCT, NodeKind.ENUM
    )
    if not candidates:
        raise EmptyDiagramError("No classes or interfaces were detected in this project.")

    budget = min(filters.budget(), 40 if filters.detail != "detailed" else 90)
    selected, elided = select_nodes(graph, candidates, filters, budget=budget)
    if not selected:
        raise EmptyDiagramError("No classes matched the requested filters.")

    selected_ids = {node.id for node in selected}
    edges = collect_edges(graph, selected_ids, RELATION_KINDS, max_edges=180)

    detail = filters.detail
    member_limit = {"executive": 0, "standard": 6, "detailed": 14}.get(detail, 6)

    lines: list[str] = ["classDiagram", "  direction TB"]
    payload_nodes: list[dict[str, Any]] = []

    by_module: dict[str, list[Node]] = {}
    for node in selected:
        by_module.setdefault(node.module or "(root)", []).append(node)

    for node in selected:
        alias = safe_id(node.qualified_name or node.name)
        annotation = STEREOTYPE_ANNOTATION.get(node.kind, "")
        lines.append(f"  class {alias}[\"{escape_label(node.name, 40)}\"] {{")
        if annotation:
            lines.append(f"    <<{annotation}>>")
        elif node.attributes.get("stereotype"):
            lines.append(f"    <<{escape_label(node.attributes['stereotype'], 20)}>>")
        if member_limit:
            if node.kind == NodeKind.ENUM:
                for member in (node.attributes.get("members") or [])[:member_limit]:
                    lines.append(f"    +{escape_label(str(member), 30)}")
            else:
                properties = (node.attributes.get("properties") or [])[:member_limit]
                for prop in properties:
                    if prop.get("name"):
                        lines.append(f"    {_property_line(prop)}")
                methods = (node.attributes.get("methods") or [])[:member_limit]
                for method in methods:
                    if method.get("name"):
                        lines.append(f"    {_signature(method, detail)}")
                remaining = len(node.attributes.get("methods") or []) - len(methods)
                if remaining > 0:
                    lines.append(f"    +… {remaining} more")
        lines.append("  }")
        payload_nodes.append(
            {
                "id": node.id,
                "alias": alias,
                "name": node.name,
                "kind": node.kind,
                "module": node.module,
                "file": node.file,
                "line": node.line,
                "language": node.language,
                "stereotype": node.attributes.get("stereotype", ""),
                "methods": len(node.attributes.get("methods") or []),
                "properties": len(node.attributes.get("properties") or []),
                "layer": node.attributes.get("layer", "unassigned"),
            }
        )

    payload_edges: list[dict[str, Any]] = []
    for edge in edges:
        source = graph.nodes[edge.source]
        target = graph.nodes[edge.target]
        relation = RELATION_MERMAID.get(edge.kind, "-->")
        source_alias = safe_id(source.qualified_name or source.name)
        target_alias = safe_id(target.qualified_name or target.name)
        label = edge.kind.replace("_", " ")
        if edge.kind in {EdgeKind.INHERITS, EdgeKind.IMPLEMENTS}:
            lines.append(f"  {target_alias} {relation} {source_alias} : {label}")
        else:
            lines.append(f"  {source_alias} {relation} {target_alias} : {label}")
        payload_edges.append(
            {"source": edge.source, "target": edge.target, "kind": edge.kind, "label": label}
        )

    for node in selected:
        if node.file:
            alias = safe_id(node.qualified_name or node.name)
            lines.append(f"  note for {alias} \"{escape_label(node.file, 46)}\"")
            break  # a single provenance note keeps the diagram readable

    mermaid = "\n".join(lines)
    plantuml = _plantuml(graph, selected, edges, member_limit, detail)

    notes: list[str] = []
    if elided:
        notes.append(f"{elided} additional types were hidden to keep the diagram readable.")
    if len(by_module) > 1:
        notes.append(f"Types span {len(by_module)} modules.")

    return DiagramResult(
        kind="class",
        title="Class Diagram",
        mermaid=mermaid,
        plantuml=plantuml,
        payload={
            "nodes": payload_nodes,
            "edges": payload_edges,
            "elided": elided,
            "modules": sorted(by_module.keys()),
            "legend": [
                {"key": "<|--", "label": "inheritance"},
                {"key": "<|..", "label": "implementation"},
                {"key": "*--", "label": "composition"},
                {"key": "o--", "label": "aggregation"},
                {"key": "..>", "label": "dependency"},
            ],
        },
        scope=filters.to_dict(),
        notes=notes,
    )


def _plantuml(
    graph: KnowledgeGraph,
    selected: list[Node],
    edges: list,
    member_limit: int,
    detail: str,
) -> str:
    lines = [
        "@startuml",
        "skinparam backgroundColor transparent",
        "skinparam shadowing false",
        "skinparam classAttributeIconSize 0",
        "skinparam linetype ortho",
        "hide empty members",
        "left to right direction",
    ]
    modules: dict[str, list[Node]] = {}
    for node in selected:
        modules.setdefault(node.module or "root", []).append(node)

    for module, nodes in modules.items():
        lines.append(f'package "{escape_label(module, 40)}" {{')
        for node in nodes:
            keyword = {
                NodeKind.INTERFACE: "interface",
                NodeKind.ABSTRACT_CLASS: "abstract class",
                NodeKind.ENUM: "enum",
                NodeKind.STRUCT: "class",
            }.get(node.kind, "class")
            alias = safe_id(node.qualified_name or node.name)
            lines.append(f'  {keyword} "{escape_label(node.name, 40)}" as {alias} {{')
            if member_limit:
                if node.kind == NodeKind.ENUM:
                    for member in (node.attributes.get("members") or [])[:member_limit]:
                        lines.append(f"    {escape_label(str(member), 30)}")
                else:
                    for prop in (node.attributes.get("properties") or [])[:member_limit]:
                        if prop.get("name"):
                            lines.append(f"    {_property_line(prop)}")
                    for method in (node.attributes.get("methods") or [])[:member_limit]:
                        if method.get("name"):
                            lines.append(f"    {_signature(method, detail)}")
            lines.append("  }")
        lines.append("}")

    for edge in edges:
        source = graph.nodes[edge.source]
        target = graph.nodes[edge.target]
        relation = RELATION_PLANTUML.get(edge.kind, "-->")
        source_alias = safe_id(source.qualified_name or source.name)
        target_alias = safe_id(target.qualified_name or target.name)
        if edge.kind in {EdgeKind.INHERITS, EdgeKind.IMPLEMENTS}:
            lines.append(f"{target_alias} {relation} {source_alias}")
        else:
            lines.append(f"{source_alias} {relation} {target_alias} : {edge.kind.replace('_', ' ')}")
    lines.append("@enduml")
    return "\n".join(lines)
