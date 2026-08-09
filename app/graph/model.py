"""Core knowledge-graph data model shared by analyzers and diagram generators."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


class NodeKind:
    PROJECT = "project"
    MODULE = "module"
    PACKAGE = "package"
    FILE = "file"
    NAMESPACE = "namespace"
    CLASS = "class"
    INTERFACE = "interface"
    ABSTRACT_CLASS = "abstract_class"
    STRUCT = "struct"
    ENUM = "enum"
    FUNCTION = "function"
    METHOD = "method"
    SERVICE = "service"
    COMPONENT = "component"
    LAYER = "layer"
    DATABASE = "database"
    TABLE = "table"
    API_ENDPOINT = "api_endpoint"
    EXTERNAL_API = "external_api"
    QUEUE = "queue"
    CONTAINER = "container"
    NODE_HOST = "host"
    CONFIG = "config"
    STATE = "state"
    EXTERNAL_PACKAGE = "external_package"
    DATA_STORE = "data_store"
    ACTOR = "actor"


CONTAINER_KINDS = {NodeKind.MODULE, NodeKind.PACKAGE, NodeKind.FILE, NodeKind.NAMESPACE}
TYPE_KINDS = {
    NodeKind.CLASS,
    NodeKind.INTERFACE,
    NodeKind.ABSTRACT_CLASS,
    NodeKind.STRUCT,
    NodeKind.ENUM,
}


class EdgeKind:
    CONTAINS = "contains"
    IMPORTS = "imports"
    USES = "uses"
    DEPENDS_ON = "depends_on"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    COMPOSES = "composes"
    AGGREGATES = "aggregates"
    ASSOCIATES = "associates"
    CALLS = "calls"
    COMMUNICATES_WITH = "communicates_with"
    READS = "reads"
    WRITES = "writes"
    EXPOSES = "exposes"
    DEPLOYS = "deploys"
    TRANSITIONS_TO = "transitions_to"
    REFERENCES = "references"


RELATIONSHIP_LABELS = {
    EdgeKind.INHERITS: "inherits",
    EdgeKind.IMPLEMENTS: "implements",
    EdgeKind.COMPOSES: "composition",
    EdgeKind.AGGREGATES: "aggregation",
    EdgeKind.ASSOCIATES: "association",
    EdgeKind.USES: "uses",
    EdgeKind.DEPENDS_ON: "depends on",
    EdgeKind.CALLS: "calls",
    EdgeKind.COMMUNICATES_WITH: "communicates with",
    EdgeKind.READS: "reads",
    EdgeKind.WRITES: "writes",
    EdgeKind.EXPOSES: "exposes",
    EdgeKind.DEPLOYS: "deploys",
    EdgeKind.IMPORTS: "imports",
    EdgeKind.CONTAINS: "contains",
    EdgeKind.TRANSITIONS_TO: "transitions to",
    EdgeKind.REFERENCES: "references",
}


@dataclass
class Node:
    id: str
    kind: str
    name: str
    qualified_name: str = ""
    file: str = ""
    module: str = ""
    language: str = ""
    external: bool = False
    line: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)

    def label(self) -> str:
        return self.name or self.qualified_name or self.id


@dataclass
class Edge:
    source: str
    target: str
    kind: str
    label: str = ""
    weight: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source, self.target, self.kind)


class KnowledgeGraph:
    """In-memory project knowledge graph with indexing helpers."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}
        self.meta: dict[str, Any] = {}
        self._by_kind: dict[str, set[str]] = defaultdict(set)
        self._by_name: dict[str, set[str]] = defaultdict(set)
        self._out: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        self._in: dict[str, set[tuple[str, str, str]]] = defaultdict(set)

    # ---------------------------------------------------------------- mutate
    def add_node(self, node: Node) -> Node:
        existing = self.nodes.get(node.id)
        if existing:
            # Merge: keep richer information.
            if not existing.file and node.file:
                existing.file = node.file
            if not existing.module and node.module:
                existing.module = node.module
            if not existing.language and node.language:
                existing.language = node.language
            if existing.kind == NodeKind.CLASS and node.kind in {NodeKind.INTERFACE, NodeKind.ABSTRACT_CLASS}:
                existing.kind = node.kind
            merged = dict(node.attributes)
            merged.update(existing.attributes)
            existing.attributes = merged
            return existing
        self.nodes[node.id] = node
        self._by_kind[node.kind].add(node.id)
        self._by_name[node.name.lower()].add(node.id)
        return node

    def add_edge(self, edge: Edge) -> Edge:
        if edge.source == edge.target and edge.kind not in {EdgeKind.TRANSITIONS_TO, EdgeKind.CALLS}:
            return edge
        existing = self.edges.get(edge.key)
        if existing:
            existing.weight += edge.weight
            existing.attributes.update(edge.attributes)
            return existing
        self.edges[edge.key] = edge
        self._out[edge.source].add(edge.key)
        self._in[edge.target].add(edge.key)
        return edge

    def link(self, source: str, target: str, kind: str, **attributes: Any) -> Edge | None:
        if source not in self.nodes or target not in self.nodes:
            return None
        return self.add_edge(
            Edge(source=source, target=target, kind=kind, label=RELATIONSHIP_LABELS.get(kind, kind), attributes=attributes)
        )

    def merge(self, other: "KnowledgeGraph") -> None:
        for node in other.nodes.values():
            self.add_node(node)
        for edge in other.edges.values():
            self.add_edge(edge)

    # ---------------------------------------------------------------- query
    def by_kind(self, *kinds: str) -> list[Node]:
        result: list[Node] = []
        for kind in kinds:
            result.extend(self.nodes[nid] for nid in self._by_kind.get(kind, ()) if nid in self.nodes)
        return result

    def find_by_name(self, name: str) -> list[Node]:
        return [self.nodes[nid] for nid in self._by_name.get(name.lower(), set()) if nid in self.nodes]

    def out_edges(self, node_id: str, kinds: Iterable[str] | None = None) -> list[Edge]:
        wanted = set(kinds) if kinds else None
        return [
            self.edges[key]
            for key in self._out.get(node_id, set())
            if key in self.edges and (wanted is None or self.edges[key].kind in wanted)
        ]

    def in_edges(self, node_id: str, kinds: Iterable[str] | None = None) -> list[Edge]:
        wanted = set(kinds) if kinds else None
        return [
            self.edges[key]
            for key in self._in.get(node_id, set())
            if key in self.edges and (wanted is None or self.edges[key].kind in wanted)
        ]

    def neighbors(self, node_id: str, kinds: Iterable[str] | None = None) -> set[str]:
        result = {edge.target for edge in self.out_edges(node_id, kinds)}
        result |= {edge.source for edge in self.in_edges(node_id, kinds)}
        result.discard(node_id)
        return result

    def degree(self, node_id: str) -> int:
        return len(self._out.get(node_id, ())) + len(self._in.get(node_id, ()))

    def subgraph(self, node_ids: Iterable[str]) -> "KnowledgeGraph":
        keep = {nid for nid in node_ids if nid in self.nodes}
        sub = KnowledgeGraph()
        sub.meta = dict(self.meta)
        for nid in keep:
            sub.add_node(self.nodes[nid])
        for edge in self.edges.values():
            if edge.source in keep and edge.target in keep:
                sub.add_edge(edge)
        return sub

    # --------------------------------------------------------------- export
    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "nodes": [asdict(node) for node in self.nodes.values()],
            "edges": [asdict(edge) for edge in self.edges.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeGraph":
        graph = cls()
        graph.meta = data.get("meta", {})
        for raw in data.get("nodes", []):
            graph.add_node(Node(**raw))
        for raw in data.get("edges", []):
            graph.add_edge(Edge(**raw))
        return graph

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: Path) -> "KnowledgeGraph":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_networkx(self, edge_kinds: Iterable[str] | None = None):
        import networkx as nx

        wanted = set(edge_kinds) if edge_kinds else None
        graph = nx.DiGraph()
        for node in self.nodes.values():
            graph.add_node(node.id, kind=node.kind, name=node.name, module=node.module, external=node.external)
        for edge in self.edges.values():
            if wanted is not None and edge.kind not in wanted:
                continue
            graph.add_edge(edge.source, edge.target, kind=edge.kind, weight=edge.weight)
        return graph

    def stats(self) -> dict[str, Any]:
        kind_counts = {kind: len(ids) for kind, ids in self._by_kind.items() if ids}
        edge_counts: dict[str, int] = defaultdict(int)
        for edge in self.edges.values():
            edge_counts[edge.kind] += 1
        languages: dict[str, int] = defaultdict(int)
        for node in self.nodes.values():
            if node.kind == NodeKind.FILE and node.language:
                languages[node.language] += 1
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes_by_kind": dict(sorted(kind_counts.items(), key=lambda kv: -kv[1])),
            "edges_by_kind": dict(sorted(edge_counts.items(), key=lambda kv: -kv[1])),
            "files_by_language": dict(sorted(languages.items(), key=lambda kv: -kv[1])),
        }

    def __len__(self) -> int:
        return len(self.nodes)
