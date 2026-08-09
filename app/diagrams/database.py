"""Entity relationship diagrams derived from SQL schemas, ORM models and migrations."""

from __future__ import annotations

import re
from typing import Any

from app.diagrams.base import DiagramFilters, DiagramResult, EmptyDiagramError, escape_label, safe_id
from app.graph.model import EdgeKind, KnowledgeGraph, Node, NodeKind

_TYPE_RE = re.compile(r"[^A-Za-z0-9_]+")


def _normalise_type(raw: str) -> str:
    """Mermaid ER attribute types must be identifiers, not bare numbers."""
    cleaned = _TYPE_RE.sub("_", (raw or "string")).strip("_")
    if not cleaned:
        cleaned = "string"
    # Lexer rejects digit-leading tokens (e.g. DEFAULT 2001 misread as a type).
    if cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned[:24]


def _attr_name(raw: str) -> str:
    """Attribute names must also be valid Mermaid ATTRIBUTE_WORD tokens."""
    name = safe_id(str(raw or "column"))
    if name[0].isdigit():
        name = f"c_{name}"
    return name[:48]


def _entity_name(name: str) -> str:
    return safe_id(name).upper()[:48]


def generate(graph: KnowledgeGraph, filters: DiagramFilters) -> DiagramResult:
    tables = graph.by_kind(NodeKind.TABLE)
    if not tables:
        raise EmptyDiagramError("No database schema, ORM model or migration was detected.")

    if filters.focus:
        needle = filters.focus.lower()
        focused = [t for t in tables if needle in t.name.lower()]
        if focused:
            keep = {t.id for t in focused}
            for table in focused:
                keep |= graph.neighbors(table.id, [EdgeKind.REFERENCES])
            tables = [t for t in tables if t.id in keep]

    limit = min(filters.budget(), 30 if filters.detail != "detailed" else 60)
    ordered = sorted(tables, key=lambda t: (-graph.degree(t.id), t.name))
    visible = ordered[:limit]
    elided = max(0, len(ordered) - len(visible))
    visible_ids = {table.id for table in visible}

    column_limit = {"executive": 3, "standard": 8, "detailed": 24}.get(filters.detail, 8)

    lines = ["erDiagram"]
    payload_entities: list[dict[str, Any]] = []
    for table in visible:
        entity = _entity_name(table.name)
        columns = table.attributes.get("columns") or []
        foreign_columns = {fk.get("column") for fk in (table.attributes.get("foreign_keys") or [])}
        lines.append(f"  {entity} {{")
        for column in columns[:column_limit]:
            name = _attr_name(column.get("name", "column"))
            column_type = _normalise_type(str(column.get("type", "string")))
            markers = []
            if column.get("primary_key"):
                markers.append("PK")
            elif column.get("name") in foreign_columns:
                markers.append("FK")
            elif column.get("unique"):
                markers.append("UK")
            comment = "" if column.get("nullable", True) else ' "required"'
            marker_text = f" {','.join(markers)}" if markers else ""
            lines.append(f"    {column_type} {name}{marker_text}{comment}")
        if len(columns) > column_limit:
            lines.append(f"    string more_{len(columns) - column_limit}_columns")
        if not columns:
            lines.append("    string undetected_schema")
        lines.append("  }")
        payload_entities.append(
            {
                "id": table.id,
                "entity": entity,
                "name": table.name,
                "columns": columns,
                "indexes": table.attributes.get("indexes") or [],
                "foreign_keys": table.attributes.get("foreign_keys") or [],
                "origin": table.attributes.get("origin", ""),
                "file": table.file,
            }
        )

    payload_relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for edge in graph.edges.values():
        if edge.kind not in {EdgeKind.REFERENCES, EdgeKind.ASSOCIATES}:
            continue
        if edge.source not in visible_ids or edge.target not in visible_ids:
            continue
        pair = (edge.source, edge.target)
        if pair in seen:
            continue
        seen.add(pair)
        child = graph.nodes[edge.source]
        parent = graph.nodes[edge.target]
        foreign_key = (edge.attributes.get("foreign_key") or {}) if isinstance(edge.attributes, dict) else {}
        column = foreign_key.get("column") or ""
        nullable = True
        for candidate in child.attributes.get("columns") or []:
            if candidate.get("name") == column:
                nullable = bool(candidate.get("nullable", True))
        cardinality = "}o--||" if nullable else "}|--||"
        label = escape_label(column or "references", 24)
        lines.append(f'  {_entity_name(child.name)} {cardinality} {_entity_name(parent.name)} : "{label}"')
        payload_relations.append(
            {"from": child.name, "to": parent.name, "column": column, "optional": nullable}
        )

    orphans = [
        table.name
        for table in visible
        if not graph.out_edges(table.id, [EdgeKind.REFERENCES]) and not graph.in_edges(table.id, [EdgeKind.REFERENCES])
    ]

    notes: list[str] = []
    if elided:
        notes.append(f"{elided} tables were hidden to keep the diagram readable.")
    if orphans:
        notes.append("Tables without detected relationships: " + ", ".join(orphans[:6]) + ".")
    origins = {table.attributes.get("origin", "schema") for table in visible}
    notes.append("Schema sources: " + ", ".join(sorted(o for o in origins if o)) + ".")

    plantuml_lines = ["@startuml", "skinparam backgroundColor transparent", "hide circle", "skinparam linetype ortho"]
    for entry in payload_entities:
        plantuml_lines.append(f'entity "{escape_label(entry["name"], 36)}" as {safe_id(entry["name"])} {{')
        for column in entry["columns"][:column_limit]:
            marker = "*" if column.get("primary_key") else " "
            plantuml_lines.append(
                f'  {marker} {_attr_name(column.get("name", "col"))} : {_normalise_type(str(column.get("type", "")))}'
            )
        plantuml_lines.append("}")
    for relation in payload_relations:
        plantuml_lines.append(f'{safe_id(relation["from"])} }}o--|| {safe_id(relation["to"])}')
    plantuml_lines.append("@enduml")

    return DiagramResult(
        kind="database",
        title="Database / ER Diagram",
        mermaid="\n".join(lines),
        plantuml="\n".join(plantuml_lines),
        payload={
            "entities": payload_entities,
            "relations": payload_relations,
            "elided": elided,
            "orphans": orphans,
            "legend": [
                {"key": "PK", "label": "primary key"},
                {"key": "FK", "label": "foreign key"},
                {"key": "}o--||", "label": "many-to-one (optional)"},
                {"key": "}|--||", "label": "many-to-one (required)"},
            ],
        },
        scope=filters.to_dict(),
        notes=notes,
    )
