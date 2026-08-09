"""SQL schema and migration analyzer."""

from __future__ import annotations

import re

from app.analyzers.base import Analyzer, AnalysisContext, PendingRef, register
from app.graph.model import EdgeKind, Node, NodeKind
from app.ingest.walker import SourceFile

CREATE_TABLE_RE = re.compile(
    r"CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?(?P<name>[\w\.]+)[\"`\]]?\s*\((?P<body>.*?)\)\s*(?:;|$)",
    re.IGNORECASE | re.DOTALL,
)
CREATE_INDEX_RE = re.compile(
    r"CREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?(?P<name>[\w]+)[\"`\]]?\s+ON\s+[\"`\[]?(?P<table>[\w\.]+)[\"`\]]?\s*\((?P<columns>[^)]*)\)",
    re.IGNORECASE,
)
ALTER_FK_RE = re.compile(
    r"ALTER\s+TABLE\s+[\"`\[]?(?P<table>[\w\.]+)[\"`\]]?\s+ADD\s+(?:CONSTRAINT\s+[\w]+\s+)?FOREIGN\s+KEY\s*\((?P<column>[^)]*)\)\s*REFERENCES\s+[\"`\[]?(?P<ref_table>[\w\.]+)[\"`\]]?\s*\((?P<ref_column>[^)]*)\)",
    re.IGNORECASE,
)
INLINE_FK_RE = re.compile(
    r"(?:FOREIGN\s+KEY\s*\(\s*[\"`\[]?(?P<fk_col>\w+)[\"`\]]?\s*\)\s*)?REFERENCES\s+[\"`\[]?(?P<ref_table>[\w\.]+)[\"`\]]?\s*\(\s*[\"`\[]?(?P<ref_col>\w+)",
    re.IGNORECASE,
)
VIEW_RE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+[\"`\[]?(?P<name>[\w\.]+)", re.IGNORECASE)
COLUMN_SPLIT_RE = re.compile(r",(?![^()]*\))")
CONSTRAINT_TOKENS = {"primary", "foreign", "unique", "constraint", "check", "key", "index"}


class DatabaseAnalyzer(Analyzer):
    name = "database"
    languages = {"sql"}
    priority = 45

    def analyze(self, source: SourceFile, ctx: AnalysisContext) -> None:
        text = self.strip_comments(source.text(), line_prefixes=("--",), block=("/*", "*/"))
        file_node = ctx.ensure_file(source)
        is_migration = "migration" in source.relative_path.lower() or bool(
            re.match(r"^\d{3,}[_\-]", source.path.name)
        )

        schema_node = ctx.graph.add_node(
            Node(
                id="database:primary",
                kind=NodeKind.DATABASE,
                name="Relational Database",
                qualified_name="database",
                language="sql",
                attributes={"origin": "sql"},
            )
        )
        ctx.add_edge(file_node.id, schema_node.id, EdgeKind.WRITES if is_migration else EdgeKind.USES)

        for match in CREATE_TABLE_RE.finditer(text):
            table = match.group("name").split(".")[-1]
            columns, foreign_keys = self._columns(match.group("body"))
            node = ctx.graph.add_node(
                Node(
                    id=f"table:{table}",
                    kind=NodeKind.TABLE,
                    name=table,
                    qualified_name=table,
                    file=source.relative_path,
                    module=source.module,
                    language="sql",
                    line=self.line_of(text, match.start()),
                    attributes={
                        "columns": columns,
                        "foreign_keys": foreign_keys,
                        "indexes": [],
                        "origin": "migration" if is_migration else "schema",
                    },
                )
            )
            ctx.add_edge(schema_node.id, node.id, EdgeKind.CONTAINS)
            for fk in foreign_keys:
                ctx.defer(
                    PendingRef(
                        source_id=node.id,
                        target_name=fk["references_table"],
                        kind=EdgeKind.REFERENCES,
                        language="sql",
                        attributes={"foreign_key": fk},
                    )
                )

        for match in ALTER_FK_RE.finditer(text):
            table = match.group("table").split(".")[-1]
            table_node = ctx.graph.nodes.get(f"table:{table}")
            fk = {
                "column": match.group("column").strip().strip('"`[]'),
                "references_table": match.group("ref_table").split(".")[-1],
                "references_column": match.group("ref_column").strip().strip('"`[]'),
            }
            if table_node:
                table_node.attributes.setdefault("foreign_keys", []).append(fk)
            ctx.defer(
                PendingRef(
                    source_id=f"table:{table}",
                    target_name=fk["references_table"],
                    kind=EdgeKind.REFERENCES,
                    language="sql",
                    attributes={"foreign_key": fk},
                )
            )

        for match in CREATE_INDEX_RE.finditer(text):
            table_node = ctx.graph.nodes.get(f"table:{match.group('table').split('.')[-1]}")
            if table_node:
                table_node.attributes.setdefault("indexes", []).append(
                    {
                        "name": match.group("name"),
                        "columns": [c.strip().strip('"`[]') for c in match.group("columns").split(",")],
                        "unique": bool(match.group("unique")),
                    }
                )

        for match in VIEW_RE.finditer(text):
            view = ctx.graph.add_node(
                Node(
                    id=f"view:{match.group('name').split('.')[-1]}",
                    kind=NodeKind.DATA_STORE,
                    name=match.group("name").split(".")[-1],
                    qualified_name=match.group("name"),
                    file=source.relative_path,
                    module=source.module,
                    language="sql",
                    attributes={"object": "view"},
                )
            )
            ctx.add_edge(schema_node.id, view.id, EdgeKind.CONTAINS)

    @staticmethod
    def _columns(body: str) -> tuple[list[dict], list[dict]]:
        columns: list[dict] = []
        foreign_keys: list[dict] = []
        for raw in COLUMN_SPLIT_RE.split(body):
            definition = raw.strip()
            if not definition:
                continue
            fk_match = INLINE_FK_RE.search(definition)
            tokens = definition.replace('"', " ").replace("`", " ").replace("[", " ").replace("]", " ").split()
            if not tokens:
                continue
            first = tokens[0].lower()
            if first in CONSTRAINT_TOKENS:
                if fk_match:
                    foreign_keys.append(
                        {
                            "column": fk_match.group("fk_col") or "",
                            "references_table": fk_match.group("ref_table").split(".")[-1],
                            "references_column": fk_match.group("ref_col"),
                        }
                    )
                continue
            name = tokens[0]
            column_type = tokens[1].upper() if len(tokens) > 1 else "TEXT"
            upper = definition.upper()
            column = {
                "name": name,
                "type": column_type,
                "primary_key": "PRIMARY KEY" in upper,
                "nullable": "NOT NULL" not in upper,
                "unique": "UNIQUE" in upper,
            }
            columns.append(column)
            if fk_match:
                foreign_keys.append(
                    {
                        "column": name,
                        "references_table": fk_match.group("ref_table").split(".")[-1],
                        "references_column": fk_match.group("ref_col"),
                    }
                )
        return columns, foreign_keys


register(DatabaseAnalyzer())
