"""Java / C# analyzer (annotation and attribute aware)."""

from __future__ import annotations

import re

from app.analyzers.base import Analyzer, AnalysisContext, PendingRef, register
from app.analyzers.common import (
    extract_calls,
    extract_fields,
    extract_methods,
    iter_blocks,
    split_type_list,
)
from app.graph.model import EdgeKind, Node, NodeKind
from app.ingest.walker import SourceFile

TYPE_RE = re.compile(
    r"(?P<modifiers>(?:\b(?:public|private|protected|internal|static|abstract|final|sealed|partial)\b\s+)*)"
    r"\b(?P<keyword>class|interface|enum|record|struct)\b\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?P<generics><[^{]*?>)?\s*"
    r"(?P<inherit>(?::|extends|implements)[^{]*)?\{",
    re.MULTILINE,
)

IMPORT_RE = re.compile(r"^\s*(?:import|using)\s+(?:static\s+)?([A-Za-z_][\w\.]*)\s*;", re.MULTILINE)
NAMESPACE_RE = re.compile(r"^\s*(?:package|namespace)\s+([A-Za-z_][\w\.]*)", re.MULTILINE)
ANNOTATION_RE = re.compile(r"@([A-Za-z_]\w*)(?:\(([^)]*)\))?")
ATTRIBUTE_RE = re.compile(r"\[([A-Za-z_]\w*)(?:\(([^\]]*)\))?\]")
ROUTE_ANNOTATIONS = {
    "getmapping": "GET",
    "postmapping": "POST",
    "putmapping": "PUT",
    "deletemapping": "DELETE",
    "patchmapping": "PATCH",
    "requestmapping": "ANY",
    "httpget": "GET",
    "httppost": "POST",
    "httpput": "PUT",
    "httpdelete": "DELETE",
    "httppatch": "PATCH",
    "route": "ANY",
    "path": "ANY",
}
STEREOTYPE_ANNOTATIONS = {
    "restcontroller": "controller",
    "controller": "controller",
    "apicontroller": "controller",
    "service": "service",
    "repository": "repository",
    "component": "component",
    "entity": "entity",
    "configuration": "configuration",
    "table": "entity",
}
EXTERNAL_HINTS = {
    "org.springframework.jdbc": (NodeKind.DATABASE, "Spring JDBC"),
    "javax.persistence": (NodeKind.DATABASE, "JPA"),
    "jakarta.persistence": (NodeKind.DATABASE, "JPA"),
    "org.hibernate": (NodeKind.DATABASE, "Hibernate"),
    "microsoft.entityframeworkcore": (NodeKind.DATABASE, "EF Core"),
    "system.data": (NodeKind.DATABASE, "ADO.NET"),
    "org.apache.kafka": (NodeKind.QUEUE, "Kafka"),
    "com.rabbitmq": (NodeKind.QUEUE, "RabbitMQ"),
    "masstransit": (NodeKind.QUEUE, "MassTransit"),
    "azure.messaging": (NodeKind.QUEUE, "Azure Messaging"),
    "system.net.http": (NodeKind.EXTERNAL_API, "HttpClient"),
    "okhttp3": (NodeKind.EXTERNAL_API, "OkHttp"),
    "org.springframework.web.client": (NodeKind.EXTERNAL_API, "RestTemplate"),
    "redis": (NodeKind.DATABASE, "Redis"),
    "mongodb": (NodeKind.DATABASE, "MongoDB"),
}


class JvmDotNetAnalyzer(Analyzer):
    name = "jvm-dotnet"
    languages = {"java", "csharp"}
    priority = 20

    def analyze(self, source: SourceFile, ctx: AnalysisContext) -> None:
        raw = source.text()
        text = self.strip_comments(raw, line_prefixes=("//",))
        file_node = ctx.ensure_file(source)

        namespace_match = NAMESPACE_RE.search(text)
        namespace = namespace_match.group(1) if namespace_match else ""
        if namespace:
            file_node.attributes["namespace"] = namespace

        self._imports(text, source, ctx, file_node.id)

        for block in iter_blocks(text, TYPE_RE):
            self._type(block, text, source, ctx, namespace)

    def _imports(self, text: str, source: SourceFile, ctx: AnalysisContext, file_id: str) -> None:
        for match in IMPORT_RE.finditer(text):
            module = match.group(1)
            ctx.defer(
                PendingRef(
                    source_id=file_id,
                    target_name=module,
                    kind=EdgeKind.IMPORTS,
                    language=source.language,
                    attributes={"symbol": module.split(".")[-1]},
                )
            )
            lowered = module.lower()
            for prefix, (kind, label) in EXTERNAL_HINTS.items():
                if lowered.startswith(prefix) or prefix in lowered:
                    node = ctx.ensure_external(label, kind=kind, language=source.language)
                    edge = EdgeKind.COMMUNICATES_WITH if kind != NodeKind.DATABASE else EdgeKind.USES
                    ctx.add_edge(file_id, node.id, edge, technology=label)
                    break

    def _type(self, block, text: str, source: SourceFile, ctx: AnalysisContext, namespace: str) -> None:
        keyword = block.groups.get("keyword", "class")
        modifiers = block.groups.get("modifiers", "")
        kind = {
            "interface": NodeKind.INTERFACE,
            "enum": NodeKind.ENUM,
            "struct": NodeKind.STRUCT,
            "record": NodeKind.CLASS,
        }.get(keyword, NodeKind.CLASS)
        if kind == NodeKind.CLASS and "abstract" in modifiers:
            kind = NodeKind.ABSTRACT_CLASS

        header_start = max(0, text.rfind("\n", 0, text.find(block.header)) - 400)
        preamble = text[header_start : text.find(block.header)]
        annotations = [m.group(1) for m in ANNOTATION_RE.finditer(preamble)]
        annotations += [m.group(1) for m in ATTRIBUTE_RE.finditer(preamble)]
        stereotype = ""
        for annotation in annotations:
            stereotype = STEREOTYPE_ANNOTATIONS.get(annotation.lower(), stereotype)

        qualified = f"{namespace}.{block.name}" if namespace else block.name
        methods = extract_methods(block.body, default_visibility="package" if source.language == "java" else "private")
        fields = extract_fields(block.body)
        members: list[str] = []
        if kind == NodeKind.ENUM:
            members = [m for m in re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*[,;=]", block.body, re.MULTILINE)]

        node = ctx.declare_type(
            source,
            block.name,
            kind=kind,
            qualified_name=qualified,
            line=block.start_line,
            attributes={
                "namespace": namespace,
                "annotations": annotations,
                "stereotype": stereotype,
                "methods": methods,
                "properties": fields,
                "method_count": len(methods),
                "members": members,
                "is_orm_model": stereotype == "entity",
            },
        )

        inherit_clause = block.groups.get("inherit", "")
        if inherit_clause:
            cleaned = re.sub(r"^\s*(?::|extends|implements)\s*", "", inherit_clause)
            cleaned = cleaned.replace("implements", ",").replace("extends", ",")
            for base in split_type_list(cleaned):
                is_interface = base.startswith("I") and len(base) > 1 and base[1].isupper()
                ctx.defer(
                    PendingRef(
                        source_id=node.id,
                        target_name=base,
                        kind=EdgeKind.IMPLEMENTS if is_interface else EdgeKind.INHERITS,
                        language=source.language,
                        hint_module=source.module,
                    )
                )

        for field in fields:
            field_type = re.sub(r"[\[\]\*&]", "", field["type"].split("<")[-1].replace(">", ""))
            if field_type and field_type[0].isupper():
                ctx.defer(
                    PendingRef(
                        source_id=node.id,
                        target_name=field_type,
                        kind=EdgeKind.COMPOSES,
                        language=source.language,
                        hint_module=source.module,
                    )
                )

        for _, callee in extract_calls(block.body):
            ctx.defer(
                PendingRef(
                    source_id=node.id,
                    target_name=callee,
                    kind=EdgeKind.CALLS,
                    language=source.language,
                    hint_module=source.module,
                )
            )

        self._routes(block, source, ctx, node.id)
        if stereotype == "entity":
            self._entity_table(block, source, ctx, node.id, fields)

    def _routes(self, block, source: SourceFile, ctx: AnalysisContext, class_id: str) -> None:
        pattern = re.compile(
            r"(?:@|\[)(?P<ann>[A-Za-z]+)(?:\(\s*(?:\"|value\s*=\s*\")(?P<path>[^\"]*)\")?",
        )
        for match in pattern.finditer(block.body):
            verb = ROUTE_ANNOTATIONS.get(match.group("ann").lower())
            if not verb:
                continue
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
                    attributes={"framework": source.language},
                )
            )
            ctx.add_edge(endpoint.id, class_id, EdgeKind.EXPOSES)
            ctx.add_edge(ctx.file_id(source.relative_path), endpoint.id, EdgeKind.CONTAINS)

    def _entity_table(self, block, source: SourceFile, ctx: AnalysisContext, class_id: str, fields: list[dict]) -> None:
        table_match = re.search(r"@Table\s*\(\s*name\s*=\s*\"([^\"]+)\"", block.header + block.body)
        table = table_match.group(1) if table_match else block.name.lower()
        columns = [
            {"name": f["name"], "type": f["type"], "primary_key": False, "nullable": True} for f in fields
        ]
        table_node = ctx.graph.add_node(
            Node(
                id=f"table:{table}",
                kind=NodeKind.TABLE,
                name=table,
                qualified_name=table,
                file=source.relative_path,
                module=source.module,
                language="sql",
                attributes={"columns": columns, "foreign_keys": [], "origin": "orm"},
            )
        )
        ctx.add_edge(class_id, table_node.id, EdgeKind.WRITES, mapping="orm")


register(JvmDotNetAnalyzer())
