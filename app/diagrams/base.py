"""Shared diagram infrastructure: filtering, pruning, styling and layout helpers.

Diagram quality rules implemented here
--------------------------------------
* Node budgets - every generator selects the most important nodes (PageRank +
  degree + stereotype weighting) and reports what was elided instead of drawing
  an unreadable hairball.
* Grouping - related nodes are emitted inside Mermaid ``subgraph`` blocks so the
  layout engine keeps them together and layers stay separated.
* Direction - layered views use top-to-bottom flow, dependency views use
  left-to-right, which minimises crossing edges for their typical shapes.
* Styling - consistent colour tokens per layer/kind, readable labels, and
  highlighted risk paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.graph.model import EdgeKind, KnowledgeGraph, Node, NodeKind

MAX_LABEL = 34

LAYER_STYLE = {
    "presentation": {"fill": "#1e3a5f", "stroke": "#4f9cf9", "text": "#e8f1ff"},
    "api": {"fill": "#0f3d3e", "stroke": "#28c2b8", "text": "#dffaf7"},
    "application": {"fill": "#2b2a4c", "stroke": "#7b6cf6", "text": "#eae7ff"},
    "domain": {"fill": "#3a2f0b", "stroke": "#e0b33c", "text": "#fff6dd"},
    "data": {"fill": "#3d1f2f", "stroke": "#e0619a", "text": "#ffe6f0"},
    "infrastructure": {"fill": "#26313d", "stroke": "#8aa0b6", "text": "#e6eef6"},
    "unassigned": {"fill": "#2a2f36", "stroke": "#6b7684", "text": "#e3e8ee"},
    "external": {"fill": "#33302a", "stroke": "#b99b6b", "text": "#f5ecdc"},
}

KIND_SHAPE = {
    NodeKind.ACTOR: ("([", "])"),
    NodeKind.API_ENDPOINT: ("[/", "/]"),
    NodeKind.DATABASE: ("[(", ")]"),
    NodeKind.TABLE: ("[(", ")]"),
    NodeKind.DATA_STORE: ("[(", ")]"),
    NodeKind.QUEUE: (">", "]"),
    NodeKind.EXTERNAL_API: ("{{", "}}"),
    NodeKind.EXTERNAL_PACKAGE: ("([", "])"),
    NodeKind.CONTAINER: ("[[", "]]"),
    NodeKind.COMPONENT: ("[[", "]]"),
    NodeKind.INTERFACE: ("(", ")"),
}

EDGE_ARROW = {
    EdgeKind.INHERITS: "-->",
    EdgeKind.IMPLEMENTS: "-.->",
    EdgeKind.COMPOSES: "-->",
    EdgeKind.AGGREGATES: "-->",
    EdgeKind.ASSOCIATES: "---",
    EdgeKind.USES: "-->",
    EdgeKind.DEPENDS_ON: "-->",
    EdgeKind.CALLS: "-->",
    EdgeKind.COMMUNICATES_WITH: "<-->",
    EdgeKind.READS: "-->",
    EdgeKind.WRITES: "==>",
    EdgeKind.IMPORTS: "-.->",
    EdgeKind.EXPOSES: "-->",
    EdgeKind.DEPLOYS: "-->",
    EdgeKind.REFERENCES: "-->",
    EdgeKind.TRANSITIONS_TO: "-->",
}

STEREOTYPE_WEIGHT = {
    "controller": 2.0,
    "service": 2.0,
    "repository": 1.6,
    "entity": 1.4,
    "handler": 1.3,
    "manager": 1.3,
    "factory": 1.1,
    "adapter": 1.1,
}

DETAIL_BUDGET = {"executive": 18, "standard": 45, "detailed": 110}


@dataclass
class DiagramFilters:
    scope: str = "project"
    modules: list[str] = field(default_factory=list)
    nodes: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    include_external: bool = False
    max_nodes: int = 60
    detail: str = "standard"
    focus: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "DiagramFilters":
        payload = payload or {}
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in payload.items() if k in known})

    def budget(self) -> int:
        return max(4, min(self.max_nodes, DETAIL_BUDGET.get(self.detail, 45)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "modules": self.modules,
            "nodes": self.nodes,
            "languages": self.languages,
            "include_external": self.include_external,
            "max_nodes": self.max_nodes,
            "detail": self.detail,
            "focus": self.focus,
        }


@dataclass
class DiagramResult:
    kind: str
    title: str
    mermaid: str
    plantuml: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "mermaid": self.mermaid,
            "plantuml": self.plantuml,
            "payload": self.payload,
            "scope": self.scope,
            "notes": self.notes,
        }


class EmptyDiagramError(RuntimeError):
    """Raised when there is not enough information to draw a meaningful diagram."""


# --------------------------------------------------------------------------- #
# Identifier / label helpers
# --------------------------------------------------------------------------- #
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_]")


def safe_id(raw: str) -> str:
    cleaned = _SAFE_ID_RE.sub("_", raw)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"
    return cleaned[:80]


def escape_label(raw: str, limit: int = MAX_LABEL) -> str:
    text = (raw or "").replace("\n", " ").strip()
    text = text.replace('"', "'").replace("`", "'")
    text = re.sub(r"[<>{}|]", " ", text)
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text or "unnamed"


def wrap_label(raw: str, width: int = 18) -> str:
    words = escape_label(raw, limit=60).split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width and current:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return "<br/>".join(lines[:3])


def node_shape(node: Node, label: str) -> str:
    open_token, close_token = KIND_SHAPE.get(node.kind, ("[", "]"))
    return f'{open_token}"{label}"{close_token}'


def layer_of(node: Node) -> str:
    if node.external:
        return "external"
    layer = node.attributes.get("layer", "unassigned")
    return layer if layer in LAYER_STYLE else "unassigned"


def style_block(layers: Iterable[str]) -> list[str]:
    lines: list[str] = []
    for layer in sorted(set(layers)):
        style = LAYER_STYLE.get(layer, LAYER_STYLE["unassigned"])
        lines.append(
            f"classDef layer_{safe_id(layer)} fill:{style['fill']},stroke:{style['stroke']},"
            f"stroke-width:1.4px,color:{style['text']},rx:6,ry:6;"
        )
    lines.append("classDef risk fill:#4a1220,stroke:#ff5c7a,stroke-width:2.4px,color:#ffe3ea,rx:6,ry:6;")
    lines.append("classDef focusNode fill:#0d3b52,stroke:#4fd1ff,stroke-width:2.6px,color:#e6faff,rx:6,ry:6;")
    return lines


# --------------------------------------------------------------------------- #
# Selection / pruning
# --------------------------------------------------------------------------- #
def importance(graph: KnowledgeGraph, node: Node, ranks: dict[str, float] | None = None) -> float:
    score = float(graph.degree(node.id))
    if ranks:
        score += 800.0 * ranks.get(node.id, 0.0)
    stereotype = node.attributes.get("stereotype", "")
    score *= STEREOTYPE_WEIGHT.get(stereotype, 1.0)
    methods = node.attributes.get("methods") or []
    score += min(len(methods), 25) * 0.15
    if node.external:
        score *= 0.35
    if node.kind in {NodeKind.COMPONENT, NodeKind.SERVICE, NodeKind.CONTAINER, NodeKind.API_ENDPOINT}:
        score *= 1.4
    return score


def rank_map(graph: KnowledgeGraph) -> dict[str, float]:
    from app.graph import metrics

    return {entry["id"]: entry["score"] for entry in metrics.centrality(graph, top=5000)}


def matches_filters(node: Node, filters: DiagramFilters) -> bool:
    if not filters.include_external and node.external:
        return False
    if filters.languages and node.language and node.language not in filters.languages:
        return False
    if filters.modules:
        module = node.module or ""
        if not any(module == m or module.startswith(f"{m}/") for m in filters.modules):
            return False
    if filters.nodes and node.id not in filters.nodes and node.name not in filters.nodes:
        return False
    if filters.focus:
        haystack = " ".join(
            [node.name, node.qualified_name, node.module, node.file, node.attributes.get("stereotype", "")]
        ).lower()
        if filters.focus.lower() not in haystack:
            return False
    return True


def select_nodes(
    graph: KnowledgeGraph,
    candidates: list[Node],
    filters: DiagramFilters,
    *,
    budget: int | None = None,
    keep_connected: bool = True,
) -> tuple[list[Node], int]:
    """Pick the most relevant nodes within the diagram budget."""
    filtered = [node for node in candidates if matches_filters(node, filters)]
    if not filtered and filters.focus:
        # Focus too narrow: fall back to focus-adjacent nodes.
        focus_ids = {
            node.id for node in candidates if filters.focus.lower() in (node.name or "").lower()
        }
        expanded = set(focus_ids)
        for node_id in focus_ids:
            expanded |= graph.neighbors(node_id)
        filtered = [node for node in candidates if node.id in expanded]

    limit = budget or filters.budget()
    if len(filtered) <= limit:
        return filtered, 0

    ranks = rank_map(graph)
    ordered = sorted(filtered, key=lambda node: -importance(graph, node, ranks))
    chosen = ordered[:limit]

    if keep_connected:
        chosen_ids = {node.id for node in chosen}
        # Prefer swapping isolated picks for connected ones to reduce loose nodes.
        connected = [n for n in chosen if graph.neighbors(n.id) & chosen_ids]
        if len(connected) < len(chosen) // 2:
            for candidate in ordered[limit:]:
                if len(chosen) >= limit + 5:
                    break
                if graph.neighbors(candidate.id) & chosen_ids:
                    chosen.append(candidate)
                    chosen_ids.add(candidate.id)
    return chosen, len(filtered) - len(chosen)


def collect_edges(
    graph: KnowledgeGraph,
    node_ids: set[str],
    kinds: Iterable[str] | None = None,
    *,
    max_edges: int = 260,
) -> list:
    wanted = set(kinds) if kinds else None
    edges = [
        edge
        for edge in graph.edges.values()
        if edge.source in node_ids
        and edge.target in node_ids
        and (wanted is None or edge.kind in wanted)
        and edge.source != edge.target
    ]
    edges.sort(key=lambda edge: -edge.weight)
    return edges[:max_edges]


def group_by_layer(nodes: list[Node]) -> dict[str, list[Node]]:
    groups: dict[str, list[Node]] = {}
    for node in nodes:
        groups.setdefault(layer_of(node), []).append(node)
    ordered_keys = [k for k in ["presentation", "api", "application", "domain", "data", "infrastructure", "unassigned", "external"] if k in groups]
    return {key: sorted(groups[key], key=lambda n: n.name.lower()) for key in ordered_keys}


def legend(entries: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": key, "label": value} for key, value in entries.items()]


def header(direction: str = "TB", title: str = "") -> list[str]:
    lines = ["---", f"title: {escape_label(title, 70)}", "---"] if title else []
    lines.append(f"flowchart {direction}")
    return lines
