"""JavaScript / TypeScript analyzer."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from app.analyzers.base import Analyzer, AnalysisContext, PendingRef, register
from app.analyzers.common import extract_calls, iter_blocks, split_type_list
from app.graph.model import EdgeKind, Node, NodeKind
from app.ingest.walker import SourceFile

CLASS_RE = re.compile(
    r"(?:export\s+)?(?:abstract\s+)?\bclass\b\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?P<generics><[^{]*?>)?\s*"
    r"(?P<inherit>(?:extends|implements)[^{]*)?\{",
    re.MULTILINE,
)
INTERFACE_RE = re.compile(
    r"(?:export\s+)?\binterface\b\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?P<generics><[^{]*?>)?\s*"
    r"(?P<inherit>extends[^{]*)?\{",
    re.MULTILINE,
)
ENUM_RE = re.compile(r"(?:export\s+)?\benum\s+(?P<name>[A-Za-z_$][\w$]*)\s*\{(?P<body>[^}]*)\}", re.MULTILINE)
TYPE_ALIAS_RE = re.compile(r"(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?P<value>[^;\n]+)")
FUNCTION_RE = re.compile(
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^)]*)\)"
)
ARROW_RE = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?\((?P<params>[^)]*)\)\s*(?::[^=]+)?=>"
)
IMPORT_RE = re.compile(r"""import\s+(?:[^'"]*?\sfrom\s+)?['"](?P<module>[^'"]+)['"]""")
REQUIRE_RE = re.compile(r"""require\(\s*['"](?P<module>[^'"]+)['"]\s*\)""")
ROUTE_RE = re.compile(
    r"\b(?:app|router|server|api)\.(?P<verb>get|post|put|patch|delete|all|use)\(\s*['\"](?P<path>[^'\"]+)['\"]"
)
DECORATOR_ROUTE_RE = re.compile(r"@(?P<verb>Get|Post|Put|Patch|Delete)\(\s*['\"]?(?P<path>[^'\")]*)['\"]?\s*\)")
MEMBER_RE = re.compile(
    r"^\s*(?P<modifiers>(?:public|private|protected|readonly|static|async|abstract)\s+)*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?P<call>\([^)]*\))?\s*(?::\s*(?P<type>[^;={\n]+))?[;={]",
    re.MULTILINE,
)
EXTERNAL_HINTS = {
    "axios": (NodeKind.EXTERNAL_API, "axios"),
    "node-fetch": (NodeKind.EXTERNAL_API, "fetch"),
    "got": (NodeKind.EXTERNAL_API, "got"),
    "mongoose": (NodeKind.DATABASE, "MongoDB"),
    "mongodb": (NodeKind.DATABASE, "MongoDB"),
    "pg": (NodeKind.DATABASE, "PostgreSQL"),
    "mysql2": (NodeKind.DATABASE, "MySQL"),
    "mysql": (NodeKind.DATABASE, "MySQL"),
    "sqlite3": (NodeKind.DATABASE, "SQLite"),
    "typeorm": (NodeKind.DATABASE, "TypeORM"),
    "sequelize": (NodeKind.DATABASE, "Sequelize"),
    "@prisma/client": (NodeKind.DATABASE, "Prisma"),
    "redis": (NodeKind.DATABASE, "Redis"),
    "ioredis": (NodeKind.DATABASE, "Redis"),
    "amqplib": (NodeKind.QUEUE, "RabbitMQ"),
    "kafkajs": (NodeKind.QUEUE, "Kafka"),
    "bull": (NodeKind.QUEUE, "Bull"),
    "socket.io": (NodeKind.QUEUE, "Socket.IO"),
}
UI_FRAMEWORKS = {"react", "react-dom", "vue", "svelte", "@angular/core", "next", "solid-js"}


class WebAnalyzer(Analyzer):
    name = "web"
    languages = {"javascript", "typescript"}
    priority = 25

    def analyze(self, source: SourceFile, ctx: AnalysisContext) -> None:
        text = self.strip_comments(source.text(), line_prefixes=("//",))
        file_node = ctx.ensure_file(source)

        self._imports(text, source, ctx, file_node)
        self._classes(text, source, ctx)
        self._interfaces(text, source, ctx)
        self._enums(text, source, ctx)
        self._functions(text, source, ctx, file_node.id)
        self._routes(text, source, ctx, file_node.id)

    # -------------------------------------------------------------- imports
    def _imports(self, text: str, source: SourceFile, ctx: AnalysisContext, file_node: Node) -> None:
        modules = {m.group("module") for m in IMPORT_RE.finditer(text)}
        modules |= {m.group("module") for m in REQUIRE_RE.finditer(text)}
        for module in modules:
            if module.startswith("."):
                resolved = self._resolve_relative(source.relative_path, module)
                ctx.defer(
                    PendingRef(
                        source_id=file_node.id,
                        target_name=resolved,
                        kind=EdgeKind.IMPORTS,
                        language=source.language,
                        attributes={"relative": True},
                    )
                )
                continue
            ctx.defer(
                PendingRef(
                    source_id=file_node.id,
                    target_name=module,
                    kind=EdgeKind.IMPORTS,
                    language=source.language,
                    attributes={"package": True},
                )
            )
            if module in UI_FRAMEWORKS:
                file_node.attributes["ui_framework"] = module
            hint = EXTERNAL_HINTS.get(module) or EXTERNAL_HINTS.get(module.split("/")[0])
            if hint:
                kind, label = hint
                node = ctx.ensure_external(label, kind=kind, language=source.language)
                edge = EdgeKind.USES if kind == NodeKind.DATABASE else EdgeKind.COMMUNICATES_WITH
                ctx.add_edge(file_node.id, node.id, edge, technology=label)

    @staticmethod
    def _resolve_relative(current: str, module: str) -> str:
        base = PurePosixPath(current).parent
        joined = (base / module).as_posix()
        parts: list[str] = []
        for part in joined.split("/"):
            if part == "." or part == "":
                continue
            if part == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(part)
        return "/".join(parts)

    # -------------------------------------------------------------- classes
    def _classes(self, text: str, source: SourceFile, ctx: AnalysisContext) -> None:
        for block in iter_blocks(text, CLASS_RE):
            members = self._members(block.body)
            methods = [m for m in members if m["kind"] == "method"]
            properties = [m for m in members if m["kind"] == "property"]
            node = ctx.declare_type(
                source,
                block.name,
                kind=NodeKind.CLASS,
                line=block.start_line,
                attributes={
                    "methods": methods,
                    "properties": properties,
                    "method_count": len(methods),
                    "stereotype": self._stereotype(block.name, block.body),
                },
            )
            inherit = block.groups.get("inherit", "")
            if inherit:
                extends = re.search(r"extends\s+([^{]*?)(?:implements|$)", inherit)
                implements = re.search(r"implements\s+([^{]*)$", inherit)
                for base in split_type_list(extends.group(1) if extends else ""):
                    ctx.defer(PendingRef(node.id, base, EdgeKind.INHERITS, source.language, source.module))
                for iface in split_type_list(implements.group(1) if implements else ""):
                    ctx.defer(PendingRef(node.id, iface, EdgeKind.IMPLEMENTS, source.language, source.module))
            for _, callee in extract_calls(block.body, limit=40):
                ctx.defer(PendingRef(node.id, callee, EdgeKind.CALLS, source.language, source.module))
            for prop in properties:
                declared = (prop.get("type") or "").split("<")[0].strip().rstrip("[]")
                if declared and declared[0].isupper():
                    ctx.defer(PendingRef(node.id, declared, EdgeKind.COMPOSES, source.language, source.module))

    def _interfaces(self, text: str, source: SourceFile, ctx: AnalysisContext) -> None:
        for block in iter_blocks(text, INTERFACE_RE):
            members = self._members(block.body)
            node = ctx.declare_type(
                source,
                block.name,
                kind=NodeKind.INTERFACE,
                line=block.start_line,
                attributes={
                    "methods": [m for m in members if m["kind"] == "method"],
                    "properties": [m for m in members if m["kind"] == "property"],
                },
            )
            inherit = block.groups.get("inherit", "")
            for base in split_type_list(inherit.replace("extends", "")):
                ctx.defer(PendingRef(node.id, base, EdgeKind.INHERITS, source.language, source.module))

    def _enums(self, text: str, source: SourceFile, ctx: AnalysisContext) -> None:
        for match in ENUM_RE.finditer(text):
            members = [m.strip().split("=")[0].strip() for m in match.group("body").split(",") if m.strip()]
            ctx.declare_type(
                source,
                match.group("name"),
                kind=NodeKind.ENUM,
                line=self.line_of(text, match.start()),
                attributes={"members": members[:40]},
            )

    def _functions(self, text: str, source: SourceFile, ctx: AnalysisContext, file_id: str) -> None:
        seen: set[str] = set()
        for pattern in (FUNCTION_RE, ARROW_RE):
            for match in pattern.finditer(text):
                name = match.group("name")
                if name in seen:
                    continue
                seen.add(name)
                is_component = bool(name[:1].isupper() and re.search(r"return\s*\(?\s*<", text))
                ctx.declare_function(
                    source,
                    name,
                    line=self.line_of(text, match.start()),
                    attributes={
                        "params": [
                            {"name": p.strip().split(":")[0], "type": (p.split(":", 1)[1].strip() if ":" in p else "")}
                            for p in (match.group("params") or "").split(",")
                            if p.strip()
                        ],
                        "visibility": "public" if "export" in match.group(0) else "internal",
                        "is_component": is_component,
                    },
                )

    def _routes(self, text: str, source: SourceFile, ctx: AnalysisContext, file_id: str) -> None:
        for match in ROUTE_RE.finditer(text):
            verb = match.group("verb").upper()
            if verb == "USE":
                continue
            path = match.group("path")
            endpoint = ctx.graph.add_node(
                Node(
                    id=f"api:{verb}:{path}",
                    kind=NodeKind.API_ENDPOINT,
                    name=f"{verb} {path}",
                    qualified_name=f"{verb} {path}",
                    file=source.relative_path,
                    module=source.module,
                    language=source.language,
                    attributes={"framework": "express-like"},
                )
            )
            ctx.add_edge(file_id, endpoint.id, EdgeKind.CONTAINS)
        for match in DECORATOR_ROUTE_RE.finditer(text):
            verb = match.group("verb").upper()
            path = match.group("path") or "/"
            endpoint = ctx.graph.add_node(
                Node(
                    id=f"api:{verb}:{path}",
                    kind=NodeKind.API_ENDPOINT,
                    name=f"{verb} {path}",
                    qualified_name=f"{verb} {path}",
                    file=source.relative_path,
                    module=source.module,
                    language=source.language,
                    attributes={"framework": "nestjs"},
                )
            )
            ctx.add_edge(file_id, endpoint.id, EdgeKind.CONTAINS)

    @staticmethod
    def _members(body: str, limit: int = 40) -> list[dict]:
        members: list[dict] = []
        seen: set[str] = set()
        for match in MEMBER_RE.finditer(body):
            name = match.group("name")
            if name in seen or name in {"if", "for", "while", "switch", "return", "constructor" }:
                continue
            seen.add(name)
            modifiers = match.group("modifiers") or ""
            visibility = "private" if "private" in modifiers else ("protected" if "protected" in modifiers else "public")
            members.append(
                {
                    "kind": "method" if match.group("call") else "property",
                    "name": name,
                    "type": (match.group("type") or "").strip(),
                    "visibility": visibility,
                    "is_static": "static" in modifiers,
                    "params": [],
                    "returns": (match.group("type") or "").strip(),
                }
            )
            if len(members) >= limit:
                break
        return members

    @staticmethod
    def _stereotype(name: str, body: str) -> str:
        lowered = name.lower()
        for token, stereotype in (
            ("controller", "controller"),
            ("service", "service"),
            ("repository", "repository"),
            ("store", "store"),
            ("component", "component"),
            ("provider", "provider"),
            ("guard", "guard"),
            ("middleware", "middleware"),
            ("model", "model"),
            ("client", "client"),
        ):
            if token in lowered:
                return stereotype
        if "@Injectable" in body:
            return "service"
        return ""


register(WebAnalyzer())
