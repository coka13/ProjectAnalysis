"""Second pass: resolve deferred references into concrete graph edges."""

from __future__ import annotations

import logging
from collections import defaultdict

from app.analyzers.base import AnalysisContext, PendingRef
from app.graph.model import EdgeKind, KnowledgeGraph, NodeKind

log = logging.getLogger("aai.resolver")

FILE_EXTENSION_CANDIDATES = (
    "",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".py",
    "/index.ts",
    "/index.js",
    "/index.tsx",
    "/__init__.py",
)


class Resolver:
    """Resolves symbol names produced by analyzers to knowledge-graph nodes."""

    def __init__(self, ctx: AnalysisContext) -> None:
        self.ctx = ctx
        self.graph: KnowledgeGraph = ctx.graph
        self._files_by_path = {
            node.qualified_name: node.id for node in self.graph.by_kind(NodeKind.FILE) if node.qualified_name
        }
        self._python_modules = self._index_python_modules()
        self._tables = {node.name.lower(): node.id for node in self.graph.by_kind(NodeKind.TABLE)}
        self._containers = {
            node.name.lower(): node.id
            for node in self.graph.by_kind(NodeKind.CONTAINER, NodeKind.COMPONENT, NodeKind.DATABASE, NodeKind.QUEUE)
        }
        self.unresolved: dict[str, int] = defaultdict(int)

    def _index_python_modules(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for node in self.graph.by_kind(NodeKind.FILE):
            if node.language != "python":
                continue
            path = node.qualified_name
            dotted = path.removesuffix(".py").replace("/", ".")
            index[dotted] = node.id
            if dotted.endswith(".__init__"):
                index[dotted.removesuffix(".__init__")] = node.id
            # Also index without the top-level source folder (src/, lib/, app/ layouts).
            parts = dotted.split(".")
            if len(parts) > 1:
                index.setdefault(".".join(parts[1:]), node.id)
        return index

    # ------------------------------------------------------------------ run
    def run(self) -> dict[str, int]:
        for ref in self.ctx.pending:
            if ref.source_id not in self.graph.nodes:
                continue
            if ref.kind == EdgeKind.IMPORTS:
                self._resolve_import(ref)
            elif ref.kind == EdgeKind.REFERENCES:
                self._resolve_table(ref)
            elif ref.kind == EdgeKind.COMMUNICATES_WITH:
                self._resolve_infra(ref)
            else:
                self._resolve_symbol(ref)
        return {
            "pending": len(self.ctx.pending),
            "unresolved_unique": len(self.unresolved),
        }

    # -------------------------------------------------------------- imports
    def _resolve_import(self, ref: PendingRef) -> None:
        name = ref.target_name
        target_id = ""

        if ref.attributes.get("relative") or "/" in name:
            for suffix in FILE_EXTENSION_CANDIDATES:
                candidate = f"{name}{suffix}"
                if candidate in self._files_by_path:
                    target_id = self._files_by_path[candidate]
                    break

        if not target_id and ref.language == "python":
            dotted = name
            while dotted and not target_id:
                target_id = self._python_modules.get(dotted, "")
                if target_id:
                    break
                dotted = dotted.rsplit(".", 1)[0] if "." in dotted else ""

        if not target_id and ref.language in {"java", "csharp"}:
            simple = name.split(".")[-1]
            candidates = self.ctx.symbol_index.get(simple.lower(), set())
            internal = [c for c in candidates if c in self.graph.nodes and not self.graph.nodes[c].external]
            if len(internal) == 1:
                target_id = internal[0]

        if not target_id and ref.language in {"c", "cpp"}:
            base = name.split("/")[-1]
            for path, file_id in self._files_by_path.items():
                if path.endswith(f"/{base}") or path == base:
                    target_id = file_id
                    break

        if target_id:
            self.graph.link(ref.source_id, target_id, EdgeKind.IMPORTS)
            source = self.graph.nodes[ref.source_id]
            target = self.graph.nodes[target_id]
            if source.module and target.module and source.module != target.module:
                module_a = self.ctx.module_id(source.module)
                module_b = self.ctx.module_id(target.module)
                self.graph.link(module_a, module_b, EdgeKind.DEPENDS_ON)
            return

        # Unknown -> treat as an external dependency (third party package).
        root = name.split(".")[0].split("/")[0]
        if not root or root.startswith("."):
            self.unresolved[name] += 1
            return
        external = self.ctx.ensure_external(root, kind=NodeKind.EXTERNAL_PACKAGE, language=ref.language)
        self.graph.link(ref.source_id, external.id, EdgeKind.DEPENDS_ON)

    # --------------------------------------------------------------- tables
    def _resolve_table(self, ref: PendingRef) -> None:
        target_id = self._tables.get(ref.target_name.lower())
        if not target_id:
            # ORM relationship pointing at a class name.
            candidates = self.ctx.symbol_index.get(ref.target_name.lower(), set())
            for candidate in candidates:
                node = self.graph.nodes.get(candidate)
                if node and node.attributes.get("table"):
                    target_id = self._tables.get(str(node.attributes["table"]).lower(), "")
                    break
        if target_id:
            self.graph.link(ref.source_id, target_id, EdgeKind.REFERENCES, **ref.attributes)
        else:
            self.unresolved[f"table:{ref.target_name}"] += 1

    def _resolve_infra(self, ref: PendingRef) -> None:
        target_id = self._containers.get(ref.target_name.lower())
        if target_id:
            self.graph.link(ref.source_id, target_id, EdgeKind.COMMUNICATES_WITH)
        else:
            self.unresolved[f"infra:{ref.target_name}"] += 1

    # -------------------------------------------------------------- symbols
    def _resolve_symbol(self, ref: PendingRef) -> None:
        candidates = self.ctx.symbol_index.get(ref.target_name.lower(), set())
        candidates = {c for c in candidates if c in self.graph.nodes and c != ref.source_id}
        if not candidates:
            self.unresolved[ref.target_name] += 1
            return

        source = self.graph.nodes[ref.source_id]
        best = self._pick(source, candidates, ref)
        if not best:
            return
        target = self.graph.nodes[best]

        kind = ref.kind
        if kind == EdgeKind.INHERITS and target.kind == NodeKind.INTERFACE:
            kind = EdgeKind.IMPLEMENTS
        if kind == EdgeKind.CALLS and target.kind in {NodeKind.CLASS, NodeKind.STRUCT, NodeKind.ABSTRACT_CLASS}:
            kind = EdgeKind.USES

        self.graph.link(ref.source_id, best, kind, **ref.attributes)

        if source.module and target.module and source.module != target.module:
            self.graph.link(self.ctx.module_id(source.module), self.ctx.module_id(target.module), EdgeKind.DEPENDS_ON)

    def _pick(self, source, candidates: set[str], ref: PendingRef) -> str:
        if len(candidates) == 1:
            return next(iter(candidates))

        def rank(candidate_id: str) -> tuple:
            node = self.graph.nodes[candidate_id]
            same_module = node.module == (ref.hint_module or source.module)
            same_language = node.language == (ref.language or source.language)
            same_file = node.file == source.file
            type_pref = 0
            if ref.kind in {EdgeKind.INHERITS, EdgeKind.IMPLEMENTS, EdgeKind.COMPOSES}:
                type_pref = 1 if node.kind in {NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ABSTRACT_CLASS, NodeKind.STRUCT} else 0
            elif ref.kind == EdgeKind.CALLS:
                type_pref = 1 if node.kind in {NodeKind.FUNCTION, NodeKind.METHOD} else 0
            return (type_pref, same_file, same_module, same_language, -self.graph.degree(candidate_id))

        ordered = sorted(candidates, key=rank, reverse=True)
        best = ordered[0]
        node = self.graph.nodes[best]
        # Ambiguous cross-module matches with no signal are dropped to avoid noise.
        if node.module != source.module and node.language != source.language and len(candidates) > 3:
            self.unresolved[ref.target_name] += 1
            return ""
        return best
