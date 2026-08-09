"""C / C++ analyzer (headers, classes, structs, namespaces, includes)."""

from __future__ import annotations

import re

from app.analyzers.base import Analyzer, AnalysisContext, PendingRef, register
from app.analyzers.common import (
    METHOD_RE,
    extract_calls,
    extract_fields,
    extract_methods,
    iter_blocks,
    split_type_list,
)
from app.graph.model import EdgeKind, NodeKind
from app.ingest.walker import SourceFile

TYPE_RE = re.compile(
    r"\b(?P<keyword>class|struct)\b\s+(?:[A-Z_]+_API\s+)?(?P<name>[A-Za-z_]\w*)\s*"
    r"(?P<inherit>:[^{;]*)?\{",
    re.MULTILINE,
)
ENUM_RE = re.compile(r"\benum\s+(?:class\s+)?(?P<name>[A-Za-z_]\w*)[^{]*\{(?P<body>[^}]*)\}", re.MULTILINE)
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)
NAMESPACE_RE = re.compile(r"^\s*namespace\s+([A-Za-z_]\w*)", re.MULTILINE)
FREE_FUNC_RE = re.compile(
    r"^(?!\s*(?:if|for|while|switch|return|else)\b)\s*"
    r"(?P<ret>[A-Za-z_][\w:<>,\*&\s]*?)\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^;{)]*)\)\s*(?:const\s*)?\{",
    re.MULTILINE,
)
EXTERNAL_HINTS = {
    "sqlite3": (NodeKind.DATABASE, "SQLite"),
    "mysql": (NodeKind.DATABASE, "MySQL"),
    "libpq-fe": (NodeKind.DATABASE, "PostgreSQL"),
    "hiredis": (NodeKind.DATABASE, "Redis"),
    "curl/curl": (NodeKind.EXTERNAL_API, "libcurl"),
    "cpprest": (NodeKind.EXTERNAL_API, "cpprestsdk"),
    "zmq": (NodeKind.QUEUE, "ZeroMQ"),
    "rdkafka": (NodeKind.QUEUE, "librdkafka"),
    "mqtt": (NodeKind.QUEUE, "MQTT"),
    "grpc": (NodeKind.EXTERNAL_API, "gRPC"),
    "boost/asio": (NodeKind.EXTERNAL_API, "Boost.Asio"),
}
STANDARD_HEADERS = {
    "stdio.h", "stdlib.h", "string.h", "math.h", "vector", "string", "memory",
    "map", "set", "algorithm", "iostream", "cstdint", "cstring", "thread",
    "mutex", "chrono", "functional", "optional", "array", "utility",
}


class CFamilyAnalyzer(Analyzer):
    name = "c-family"
    languages = {"c", "cpp"}
    priority = 30

    def analyze(self, source: SourceFile, ctx: AnalysisContext) -> None:
        text = self.strip_comments(source.text(), line_prefixes=("//",))
        file_node = ctx.ensure_file(source)

        namespace_match = NAMESPACE_RE.search(text)
        namespace = namespace_match.group(1) if namespace_match else ""
        if namespace:
            file_node.attributes["namespace"] = namespace

        for match in INCLUDE_RE.finditer(text):
            header = match.group(1)
            if header.lower() in STANDARD_HEADERS:
                continue
            ctx.defer(
                PendingRef(
                    source_id=file_node.id,
                    target_name=header,
                    kind=EdgeKind.IMPORTS,
                    language=source.language,
                    attributes={"header": header},
                )
            )
            lowered = header.lower()
            for token, (kind, label) in EXTERNAL_HINTS.items():
                if token in lowered:
                    node = ctx.ensure_external(label, kind=kind, language=source.language)
                    edge = EdgeKind.USES if kind == NodeKind.DATABASE else EdgeKind.COMMUNICATES_WITH
                    ctx.add_edge(file_node.id, node.id, edge, technology=label)
                    break

        consumed_spans: list[tuple[int, int]] = []
        for block in iter_blocks(text, TYPE_RE):
            self._type(block, source, ctx, namespace)
            index = text.find(block.header)
            if index >= 0:
                consumed_spans.append((index, index + len(block.header) + len(block.body)))

        for match in ENUM_RE.finditer(text):
            members = [m.strip().split("=")[0].strip() for m in match.group("body").split(",") if m.strip()]
            ctx.declare_type(
                source,
                match.group("name"),
                kind=NodeKind.ENUM,
                line=self.line_of(text, match.start()),
                attributes={"members": members[:40], "namespace": namespace},
            )

        for match in FREE_FUNC_RE.finditer(text):
            position = match.start()
            if any(start <= position <= end for start, end in consumed_spans):
                continue
            name = match.group("name")
            if name in {"if", "for", "while", "switch", "catch", "return"}:
                continue
            ctx.declare_function(
                source,
                name,
                line=self.line_of(text, position),
                attributes={"returns": match.group("ret").strip().split()[-1], "visibility": "public"},
            )

    def _type(self, block, source: SourceFile, ctx: AnalysisContext, namespace: str) -> None:
        keyword = block.groups.get("keyword", "class")
        default_visibility = "private" if keyword == "class" else "public"
        methods = extract_methods(block.body, default_visibility=default_visibility)
        fields = extract_fields(block.body, default_visibility=default_visibility)
        is_abstract = bool(re.search(r"virtual[^;{]*=\s*0\s*;", block.body))
        qualified = f"{namespace}::{block.name}" if namespace else block.name

        node = ctx.declare_type(
            source,
            block.name,
            kind=NodeKind.ABSTRACT_CLASS if is_abstract else (NodeKind.STRUCT if keyword == "struct" else NodeKind.CLASS),
            qualified_name=qualified,
            line=block.start_line,
            attributes={
                "namespace": namespace,
                "methods": methods,
                "properties": fields,
                "method_count": len(methods),
                "is_abstract": is_abstract,
            },
        )

        inherit = block.groups.get("inherit", "")
        if inherit:
            for base in split_type_list(inherit.lstrip(":")):
                ctx.defer(
                    PendingRef(
                        source_id=node.id,
                        target_name=base,
                        kind=EdgeKind.INHERITS,
                        language=source.language,
                        hint_module=source.module,
                    )
                )

        for field in fields:
            field_type = re.sub(r"[\*&]", "", field["type"].split("<")[-1].replace(">", "")).split("::")[-1]
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

        for _, callee in extract_calls(block.body, limit=40):
            ctx.defer(
                PendingRef(
                    source_id=node.id,
                    target_name=callee,
                    kind=EdgeKind.CALLS,
                    language=source.language,
                    hint_module=source.module,
                )
            )


register(CFamilyAnalyzer())
