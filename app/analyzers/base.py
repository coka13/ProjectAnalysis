"""Analyzer plugin contract and shared helpers."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.graph.model import Edge, EdgeKind, KnowledgeGraph, Node, NodeKind
from app.ingest.walker import SourceFile

log = logging.getLogger("aai.analyzers")


@dataclass
class PendingRef:
    """A reference that must be resolved after every file has been parsed."""

    source_id: str
    target_name: str
    kind: str
    language: str = ""
    hint_module: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisContext:
    root: Path
    graph: KnowledgeGraph
    pending: list[PendingRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    symbol_index: dict[str, set[str]] = field(default_factory=dict)
    file_count: int = 0

    # ------------------------------------------------------------------ ids
    @staticmethod
    def file_id(relative_path: str) -> str:
        return f"file:{relative_path}"

    @staticmethod
    def module_id(module: str) -> str:
        return f"module:{module}"

    @staticmethod
    def type_id(language: str, qualified_name: str) -> str:
        return f"type:{language}:{qualified_name}"

    @staticmethod
    def function_id(language: str, qualified_name: str) -> str:
        return f"func:{language}:{qualified_name}"

    @staticmethod
    def external_id(name: str) -> str:
        return f"ext:{name}"

    # -------------------------------------------------------------- helpers
    def ensure_module(self, module: str, language: str = "") -> Node:
        node = self.graph.nodes.get(self.module_id(module))
        if node:
            return node
        return self.graph.add_node(
            Node(
                id=self.module_id(module),
                kind=NodeKind.MODULE,
                name=module.split("/")[-1] or module,
                qualified_name=module,
                module=module,
                language=language,
            )
        )

    def ensure_file(self, source: SourceFile) -> Node:
        module = self.ensure_module(source.module, source.language)
        node = self.graph.add_node(
            Node(
                id=self.file_id(source.relative_path),
                kind=NodeKind.FILE,
                name=Path(source.relative_path).name,
                qualified_name=source.relative_path,
                file=source.relative_path,
                module=source.module,
                language=source.language,
                attributes={"size": source.size, "loc": source.text().count("\n") + 1},
            )
        )
        self.graph.link(module.id, node.id, EdgeKind.CONTAINS)
        return node

    def declare_type(
        self,
        source: SourceFile,
        name: str,
        *,
        kind: str = NodeKind.CLASS,
        qualified_name: str = "",
        line: int = 0,
        attributes: dict[str, Any] | None = None,
    ) -> Node:
        qualified = qualified_name or (f"{source.module}.{name}" if source.module != "(root)" else name)
        node = self.graph.add_node(
            Node(
                id=self.type_id(source.language, qualified),
                kind=kind,
                name=name,
                qualified_name=qualified,
                file=source.relative_path,
                module=source.module,
                language=source.language,
                line=line,
                attributes=attributes or {},
            )
        )
        self.graph.link(self.file_id(source.relative_path), node.id, EdgeKind.CONTAINS)
        self.index_symbol(name, node.id)
        if qualified != name:
            self.index_symbol(qualified, node.id)
        return node

    def declare_function(
        self,
        source: SourceFile,
        name: str,
        *,
        qualified_name: str = "",
        line: int = 0,
        attributes: dict[str, Any] | None = None,
        owner_id: str = "",
    ) -> Node:
        qualified = qualified_name or (f"{source.module}.{name}" if source.module != "(root)" else name)
        node = self.graph.add_node(
            Node(
                id=self.function_id(source.language, qualified),
                kind=NodeKind.METHOD if owner_id else NodeKind.FUNCTION,
                name=name,
                qualified_name=qualified,
                file=source.relative_path,
                module=source.module,
                language=source.language,
                line=line,
                attributes=attributes or {},
            )
        )
        self.graph.link(owner_id or self.file_id(source.relative_path), node.id, EdgeKind.CONTAINS)
        self.index_symbol(name, node.id)
        return node

    def ensure_external(self, name: str, *, kind: str = NodeKind.EXTERNAL_PACKAGE, language: str = "") -> Node:
        return self.graph.add_node(
            Node(
                id=self.external_id(name),
                kind=kind,
                name=name,
                qualified_name=name,
                language=language,
                external=True,
            )
        )

    def index_symbol(self, name: str, node_id: str) -> None:
        self.symbol_index.setdefault(name.lower(), set()).add(node_id)

    def defer(self, ref: PendingRef) -> None:
        self.pending.append(ref)

    def add_edge(self, source_id: str, target_id: str, kind: str, **attributes: Any) -> Edge | None:
        return self.graph.link(source_id, target_id, kind, **attributes)


class Analyzer(ABC):
    """Base class for all language / artefact analyzers."""

    name: str = "analyzer"
    languages: set[str] = set()
    infra_kinds: set[str] = set()
    priority: int = 100

    def accepts(self, source: SourceFile) -> bool:
        if source.infra_kind and source.infra_kind in self.infra_kinds:
            return True
        return source.language in self.languages

    @abstractmethod
    def analyze(self, source: SourceFile, ctx: AnalysisContext) -> None:
        """Populate ``ctx`` with nodes, edges and pending references."""

    # --------------------------------------------------------------- shared
    @staticmethod
    def strip_comments(text: str, *, line_prefixes: Iterable[str] = ("//",), block: tuple[str, str] | None = ("/*", "*/")) -> str:
        result = text
        if block:
            start, end = block
            result = re.sub(re.escape(start) + r".*?" + re.escape(end), " ", result, flags=re.DOTALL)
        for prefix in line_prefixes:
            result = re.sub(rf"{re.escape(prefix)}[^\n]*", " ", result)
        return result

    @staticmethod
    def line_of(text: str, index: int) -> int:
        return text.count("\n", 0, index) + 1


_REGISTRY: list[Analyzer] = []


def register(analyzer: Analyzer) -> Analyzer:
    _REGISTRY.append(analyzer)
    _REGISTRY.sort(key=lambda item: item.priority)
    return analyzer


def all_analyzers() -> list[Analyzer]:
    return list(_REGISTRY)


def analyzers_for(source: SourceFile) -> list[Analyzer]:
    return [analyzer for analyzer in _REGISTRY if analyzer.accepts(source)]
