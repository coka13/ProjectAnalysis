"""Go and Rust analyzer."""

from __future__ import annotations

import re

from app.analyzers.base import Analyzer, AnalysisContext, PendingRef, register
from app.analyzers.common import extract_calls, iter_blocks
from app.graph.model import EdgeKind, Node, NodeKind
from app.ingest.walker import SourceFile

# --- Go -------------------------------------------------------------------
GO_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z_]\w*)", re.MULTILINE)
GO_IMPORT_BLOCK_RE = re.compile(r"import\s*\((?P<body>[^)]*)\)", re.MULTILINE)
GO_IMPORT_SINGLE_RE = re.compile(r'^\s*import\s+(?:[A-Za-z_\.]+\s+)?"(?P<module>[^"]+)"', re.MULTILINE)
GO_STRUCT_RE = re.compile(r"type\s+(?P<name>[A-Za-z_]\w*)\s+struct\s*\{", re.MULTILINE)
GO_INTERFACE_RE = re.compile(r"type\s+(?P<name>[A-Za-z_]\w*)\s+interface\s*\{", re.MULTILINE)
GO_METHOD_RE = re.compile(
    r"func\s*\(\s*\w+\s+\*?(?P<recv>[A-Za-z_]\w*)\s*\)\s*(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\)\s*(?P<ret>[^{\n]*)\{"
)
GO_FUNC_RE = re.compile(r"^func\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\)\s*(?P<ret>[^{\n]*)\{", re.MULTILINE)
GO_FIELD_RE = re.compile(r"^\s*(?P<name>[A-Z_]\w*)\s+(?P<type>[\w\*\[\]\.\{\}]+)", re.MULTILINE)
GO_ROUTE_RE = re.compile(
    r"\.(?:Handle|HandleFunc|GET|POST|PUT|PATCH|DELETE|Get|Post|Put|Patch|Delete)\(\s*\"(?P<path>[^\"]+)\""
)

# --- Rust -----------------------------------------------------------------
RS_USE_RE = re.compile(r"^\s*(?:pub\s+)?use\s+(?P<path>[A-Za-z_][\w:]*)", re.MULTILINE)
RS_MOD_RE = re.compile(r"^\s*(?:pub\s+)?mod\s+(?P<name>[A-Za-z_]\w*)\s*;", re.MULTILINE)
RS_STRUCT_RE = re.compile(r"(?:pub\s+)?struct\s+(?P<name>[A-Za-z_]\w*)(?:<[^>{]*>)?\s*\{", re.MULTILINE)
RS_ENUM_RE = re.compile(r"(?:pub\s+)?enum\s+(?P<name>[A-Za-z_]\w*)(?:<[^>{]*>)?\s*\{", re.MULTILINE)
RS_TRAIT_RE = re.compile(r"(?:pub\s+)?trait\s+(?P<name>[A-Za-z_]\w*)(?:<[^>{]*>)?[^{]*\{", re.MULTILINE)
RS_IMPL_RE = re.compile(
    r"impl(?:<[^>]*>)?\s+(?:(?P<trait>[A-Za-z_][\w:]*)(?:<[^>]*>)?\s+for\s+)?(?P<type>[A-Za-z_][\w:]*)(?:<[^>{]*>)?\s*\{",
    re.MULTILINE,
)
RS_FN_RE = re.compile(r"(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(?P<name>[A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*\((?P<params>[^)]*)\)")
RS_FIELD_RE = re.compile(r"^\s*(?P<vis>pub(?:\([^)]*\))?\s+)?(?P<name>[a-z_]\w*)\s*:\s*(?P<type>[^,\n]+)", re.MULTILINE)

EXTERNAL_HINTS = {
    "database/sql": (NodeKind.DATABASE, "database/sql"),
    "gorm.io": (NodeKind.DATABASE, "GORM"),
    "go-redis": (NodeKind.DATABASE, "Redis"),
    "mongo-driver": (NodeKind.DATABASE, "MongoDB"),
    "sqlx": (NodeKind.DATABASE, "SQLx"),
    "diesel": (NodeKind.DATABASE, "Diesel"),
    "sea_orm": (NodeKind.DATABASE, "SeaORM"),
    "redis": (NodeKind.DATABASE, "Redis"),
    "net/http": (NodeKind.EXTERNAL_API, "net/http"),
    "reqwest": (NodeKind.EXTERNAL_API, "reqwest"),
    "hyper": (NodeKind.EXTERNAL_API, "hyper"),
    "grpc": (NodeKind.EXTERNAL_API, "gRPC"),
    "tonic": (NodeKind.EXTERNAL_API, "tonic gRPC"),
    "sarama": (NodeKind.QUEUE, "Kafka"),
    "rdkafka": (NodeKind.QUEUE, "Kafka"),
    "amqp": (NodeKind.QUEUE, "AMQP"),
    "nats": (NodeKind.QUEUE, "NATS"),
    "lapin": (NodeKind.QUEUE, "RabbitMQ"),
}


class GoRustAnalyzer(Analyzer):
    name = "go-rust"
    languages = {"go", "rust"}
    priority = 35

    def analyze(self, source: SourceFile, ctx: AnalysisContext) -> None:
        text = self.strip_comments(source.text(), line_prefixes=("//",))
        file_node = ctx.ensure_file(source)
        if source.language == "go":
            self._go(text, source, ctx, file_node)
        else:
            self._rust(text, source, ctx, file_node)

    # ------------------------------------------------------------------ go
    def _go(self, text: str, source: SourceFile, ctx: AnalysisContext, file_node: Node) -> None:
        package = GO_PACKAGE_RE.search(text)
        if package:
            file_node.attributes["package"] = package.group(1)

        imports: set[str] = set()
        for block in GO_IMPORT_BLOCK_RE.finditer(text):
            imports |= set(re.findall(r'"([^"]+)"', block.group("body")))
        imports |= {m.group("module") for m in GO_IMPORT_SINGLE_RE.finditer(text)}
        self._register_imports(imports, source, ctx, file_node.id)

        for block in iter_blocks(text, GO_STRUCT_RE):
            fields = [
                {"name": m.group("name"), "type": m.group("type"), "visibility": "public"}
                for m in GO_FIELD_RE.finditer(block.body)
            ][:30]
            node = ctx.declare_type(
                source,
                block.name,
                kind=NodeKind.STRUCT,
                line=block.start_line,
                attributes={"properties": fields, "methods": [], "stereotype": self._stereotype(block.name)},
            )
            for field in fields:
                declared = field["type"].lstrip("*[]").split(".")[-1]
                if declared and declared[0].isupper():
                    ctx.defer(PendingRef(node.id, declared, EdgeKind.COMPOSES, source.language, source.module))

        for block in iter_blocks(text, GO_INTERFACE_RE):
            methods = [
                {"name": m.group(1), "params": [], "returns": "", "visibility": "public"}
                for m in re.finditer(r"^\s*([A-Z]\w*)\s*\(", block.body, re.MULTILINE)
            ]
            ctx.declare_type(
                source,
                block.name,
                kind=NodeKind.INTERFACE,
                line=block.start_line,
                attributes={"methods": methods, "properties": []},
            )

        for match in GO_METHOD_RE.finditer(text):
            receiver = match.group("recv")
            owner = ctx.graph.nodes.get(ctx.type_id("go", f"{source.module}.{receiver}"))
            method = {
                "name": match.group("name"),
                "params": [],
                "returns": match.group("ret").strip(),
                "visibility": "public" if match.group("name")[0].isupper() else "private",
            }
            if owner:
                owner.attributes.setdefault("methods", []).append(method)
                owner.attributes["method_count"] = len(owner.attributes["methods"])
            func_node = ctx.declare_function(
                source,
                match.group("name"),
                qualified_name=f"{source.module}.{receiver}.{match.group('name')}",
                line=self.line_of(text, match.start()),
                owner_id=owner.id if owner else "",
                attributes=method,
            )
            open_index = text.find("{", match.end() - 1)
            if open_index != -1:
                from app.analyzers.common import matching_block

                _, body = matching_block(text, open_index)
                for _, callee in extract_calls(body, limit=25):
                    ctx.defer(PendingRef(func_node.id, callee, EdgeKind.CALLS, "go", source.module))

        for match in GO_FUNC_RE.finditer(text):
            ctx.declare_function(
                source,
                match.group("name"),
                line=self.line_of(text, match.start()),
                attributes={"returns": match.group("ret").strip(), "visibility": "public"},
            )

        for match in GO_ROUTE_RE.finditer(text):
            path = match.group("path")
            endpoint = ctx.graph.add_node(
                Node(
                    id=f"api:ANY:{path}",
                    kind=NodeKind.API_ENDPOINT,
                    name=f"HTTP {path}",
                    qualified_name=path,
                    file=source.relative_path,
                    module=source.module,
                    language="go",
                    attributes={"framework": "go-http"},
                )
            )
            ctx.add_edge(file_node.id, endpoint.id, EdgeKind.CONTAINS)

    # ---------------------------------------------------------------- rust
    def _rust(self, text: str, source: SourceFile, ctx: AnalysisContext, file_node: Node) -> None:
        imports = {m.group("path").split("::")[0] for m in RS_USE_RE.finditer(text)}
        imports |= {m.group("name") for m in RS_MOD_RE.finditer(text)}
        self._register_imports(imports, source, ctx, file_node.id)

        for block in iter_blocks(text, RS_STRUCT_RE):
            fields = [
                {
                    "name": m.group("name"),
                    "type": m.group("type").strip().rstrip(","),
                    "visibility": "public" if m.group("vis") else "private",
                }
                for m in RS_FIELD_RE.finditer(block.body)
            ][:30]
            node = ctx.declare_type(
                source,
                block.name,
                kind=NodeKind.STRUCT,
                line=block.start_line,
                attributes={"properties": fields, "methods": [], "stereotype": self._stereotype(block.name)},
            )
            for field in fields:
                declared = re.sub(r"[<>&\s]", " ", field["type"]).split()
                for token in declared:
                    simple = token.split("::")[-1]
                    if simple[:1].isupper() and simple not in {"String", "Vec", "Option", "Result", "Box", "Arc", "Rc"}:
                        ctx.defer(PendingRef(node.id, simple, EdgeKind.COMPOSES, "rust", source.module))

        for block in iter_blocks(text, RS_ENUM_RE):
            members = [m.strip().split("(")[0] for m in block.body.split(",") if m.strip() and m.strip()[0].isupper()]
            ctx.declare_type(
                source,
                block.name,
                kind=NodeKind.ENUM,
                line=block.start_line,
                attributes={"members": members[:40]},
            )

        for block in iter_blocks(text, RS_TRAIT_RE):
            methods = [
                {"name": m.group("name"), "params": [], "returns": "", "visibility": "public"}
                for m in RS_FN_RE.finditer(block.body)
            ]
            ctx.declare_type(
                source,
                block.name,
                kind=NodeKind.INTERFACE,
                line=block.start_line,
                attributes={"methods": methods, "properties": []},
            )

        for block in iter_blocks(text, RS_IMPL_RE):
            type_name = block.groups.get("type", "").split("::")[-1]
            trait_name = (block.groups.get("trait") or "").split("::")[-1]
            type_id = ctx.type_id("rust", f"{source.module}.{type_name}")
            if trait_name and type_id in ctx.graph.nodes:
                ctx.defer(PendingRef(type_id, trait_name, EdgeKind.IMPLEMENTS, "rust", source.module))
            owner = ctx.graph.nodes.get(type_id)
            for match in RS_FN_RE.finditer(block.body):
                method = {"name": match.group("name"), "params": [], "returns": "", "visibility": "public"}
                if owner:
                    owner.attributes.setdefault("methods", []).append(method)
                    owner.attributes["method_count"] = len(owner.attributes["methods"])
            for _, callee in extract_calls(block.body, limit=30):
                if owner:
                    ctx.defer(PendingRef(owner.id, callee, EdgeKind.CALLS, "rust", source.module))

    # -------------------------------------------------------------- shared
    def _register_imports(self, imports: set[str], source: SourceFile, ctx: AnalysisContext, file_id: str) -> None:
        for module in sorted(imports):
            ctx.defer(
                PendingRef(
                    source_id=file_id,
                    target_name=module,
                    kind=EdgeKind.IMPORTS,
                    language=source.language,
                )
            )
            lowered = module.lower()
            for token, (kind, label) in EXTERNAL_HINTS.items():
                if token in lowered:
                    node = ctx.ensure_external(label, kind=kind, language=source.language)
                    edge = EdgeKind.USES if kind == NodeKind.DATABASE else EdgeKind.COMMUNICATES_WITH
                    ctx.add_edge(file_id, node.id, edge, technology=label)
                    break

    @staticmethod
    def _stereotype(name: str) -> str:
        lowered = name.lower()
        for token, stereotype in (
            ("service", "service"),
            ("repository", "repository"),
            ("handler", "handler"),
            ("client", "client"),
            ("server", "server"),
            ("config", "configuration"),
            ("store", "store"),
        ):
            if token in lowered:
                return stereotype
        return ""


register(GoRustAnalyzer())
