"""Python analyzer built on the standard library AST (high fidelity)."""

from __future__ import annotations

import ast
import logging
import re

from app.analyzers.base import Analyzer, AnalysisContext, PendingRef, register
from app.graph.model import EdgeKind, Node, NodeKind
from app.ingest.walker import SourceFile

log = logging.getLogger("aai.analyzers.python")

INTERFACE_BASES = {"ABC", "Protocol", "ABCMeta", "Interface"}
ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}
ORM_BASES = {"Base", "Model", "DeclarativeBase", "SQLModel", "Document"}
HTTP_DECORATORS = {"get", "post", "put", "patch", "delete", "head", "options", "route", "websocket"}
HTTP_CLIENT_MODULES = {"requests", "httpx", "aiohttp", "urllib", "urllib3"}
QUEUE_MODULES = {"kombu", "celery", "pika", "kafka", "confluent_kafka", "aio_pika", "redis", "nats"}
DB_MODULES = {"sqlalchemy", "psycopg2", "psycopg", "pymysql", "sqlite3", "asyncpg", "pymongo", "motor", "redis"}
URL_RE = re.compile(r"https?://[A-Za-z0-9._\-/:%?=&+~#]+")


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_decorator_name(node.value)}.{node.attr}" if node.value else node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Subscript):
        return _decorator_name(node.value)
    if isinstance(node, ast.Constant):
        return str(node.value)
    return ""


def _annotation_names(node: ast.expr | None) -> list[str]:
    if node is None:
        return []
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.append(child.id)
        elif isinstance(child, ast.Attribute):
            names.append(child.attr)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            names.append(child.value)
    return names


def _visibility(name: str) -> str:
    if name.startswith("__") and not name.endswith("__"):
        return "private"
    if name.startswith("_"):
        return "protected"
    return "public"


class PythonAnalyzer(Analyzer):
    name = "python"
    languages = {"python"}
    priority = 10

    def analyze(self, source: SourceFile, ctx: AnalysisContext) -> None:
        text = source.text()
        try:
            tree = ast.parse(text, filename=source.relative_path)
        except SyntaxError as exc:
            ctx.warnings.append(f"{source.relative_path}: python syntax error line {exc.lineno}")
            return

        file_node = ctx.ensure_file(source)
        self._imports(tree, source, ctx, file_node.id)

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self._class(node, source, ctx)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._function(node, source, ctx, owner_id="", owner_name="")

        self._external_endpoints(text, source, ctx, file_node.id)

    # ------------------------------------------------------------- imports
    def _imports(self, tree: ast.AST, source: SourceFile, ctx: AnalysisContext, file_id: str) -> None:
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    base = source.module.replace("/", ".")
                    modules = [f"{base}.{node.module}" if node.module else base]
                elif node.module:
                    modules = [node.module]
            for module in modules:
                root = module.split(".")[0]
                ctx.defer(
                    PendingRef(
                        source_id=file_id,
                        target_name=module,
                        kind=EdgeKind.IMPORTS,
                        language="python",
                        attributes={"root": root},
                    )
                )
                if root in DB_MODULES:
                    db = ctx.ensure_external(root, kind=NodeKind.DATABASE, language="python")
                    ctx.add_edge(file_id, db.id, EdgeKind.USES, technology=root)
                elif root in QUEUE_MODULES:
                    queue = ctx.ensure_external(root, kind=NodeKind.QUEUE, language="python")
                    ctx.add_edge(file_id, queue.id, EdgeKind.COMMUNICATES_WITH, technology=root)
                elif root in HTTP_CLIENT_MODULES:
                    api = ctx.ensure_external(f"{root} (HTTP client)", kind=NodeKind.EXTERNAL_API, language="python")
                    ctx.add_edge(file_id, api.id, EdgeKind.COMMUNICATES_WITH, technology=root)

    # --------------------------------------------------------------- class
    def _class(self, node: ast.ClassDef, source: SourceFile, ctx: AnalysisContext, prefix: str = "") -> None:
        bases = [_decorator_name(base) for base in node.bases if _decorator_name(base)]
        decorators = [_decorator_name(d) for d in node.decorator_list]
        base_simple = {b.split(".")[-1] for b in bases}

        kind = NodeKind.CLASS
        if base_simple & ENUM_BASES:
            kind = NodeKind.ENUM
        elif base_simple & INTERFACE_BASES:
            kind = NodeKind.INTERFACE
        elif any(isinstance(item, ast.FunctionDef) and any(
            _decorator_name(d).endswith("abstractmethod") for d in item.decorator_list
        ) for item in node.body):
            kind = NodeKind.ABSTRACT_CLASS

        tablename = ""
        properties: list[dict] = []
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        if target.id == "__tablename__" and isinstance(item.value, ast.Constant):
                            tablename = str(item.value.value)
                        elif not target.id.startswith("__"):
                            properties.append(
                                {
                                    "name": target.id,
                                    "type": _annotation_names(item.value)[:1][0] if _annotation_names(item.value) else "",
                                    "visibility": _visibility(target.id),
                                }
                            )
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                annotation = _annotation_names(item.annotation)
                properties.append(
                    {
                        "name": item.target.id,
                        "type": annotation[0] if annotation else "",
                        "visibility": _visibility(item.target.id),
                    }
                )
                for ann in annotation:
                    ctx.defer(
                        PendingRef(
                            source_id=ctx.type_id(source.language, self._qualified(source, node.name, prefix)),
                            target_name=ann,
                            kind=EdgeKind.COMPOSES,
                            language="python",
                            hint_module=source.module,
                        )
                    )

        qualified = self._qualified(source, node.name, prefix)
        is_orm = bool(tablename) or bool(base_simple & ORM_BASES)
        class_node = ctx.declare_type(
            source,
            node.name,
            kind=kind,
            qualified_name=qualified,
            line=node.lineno,
            attributes={
                "bases": bases,
                "decorators": decorators,
                "properties": properties,
                "docstring": (ast.get_docstring(node) or "")[:400],
                "is_orm_model": is_orm,
                "table": tablename,
                "stereotype": self._stereotype(node.name, decorators, is_orm),
            },
        )

        for base in bases:
            simple = base.split(".")[-1]
            if simple in ENUM_BASES | INTERFACE_BASES | ORM_BASES and simple not in {"Base", "Model"}:
                continue
            edge_kind = EdgeKind.IMPLEMENTS if simple.startswith("I") and simple[1:2].isupper() else EdgeKind.INHERITS
            ctx.defer(
                PendingRef(
                    source_id=class_node.id,
                    target_name=simple,
                    kind=edge_kind,
                    language="python",
                    hint_module=source.module,
                )
            )

        if is_orm:
            self._orm_model(node, source, ctx, class_node.id, tablename or node.name.lower())

        if kind == NodeKind.ENUM:
            members = [p["name"] for p in properties]
            class_node.attributes["members"] = members

        methods: list[dict] = []
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info = self._function(item, source, ctx, owner_id=class_node.id, owner_name=qualified)
                methods.append(info)
                if item.name == "__init__":
                    properties.extend(self._init_attributes(item, class_node.id, source, ctx))
            elif isinstance(item, ast.ClassDef):
                self._class(item, source, ctx, prefix=f"{qualified}.")
        class_node.attributes["methods"] = methods
        class_node.attributes["properties"] = properties
        class_node.attributes["method_count"] = len(methods)

    @staticmethod
    def _qualified(source: SourceFile, name: str, prefix: str = "") -> str:
        if prefix:
            return f"{prefix}{name}"
        module = source.module.replace("/", ".")
        return f"{module}.{name}" if module and module != "(root)" else name

    @staticmethod
    def _stereotype(name: str, decorators: list[str], is_orm: bool) -> str:
        lowered = name.lower()
        if is_orm:
            return "entity"
        for token, stereotype in (
            ("controller", "controller"),
            ("service", "service"),
            ("repository", "repository"),
            ("dao", "repository"),
            ("factory", "factory"),
            ("handler", "handler"),
            ("manager", "manager"),
            ("client", "client"),
            ("adapter", "adapter"),
            ("middleware", "middleware"),
            ("view", "view"),
            ("model", "model"),
            ("config", "configuration"),
        ):
            if token in lowered:
                return stereotype
        if any("dataclass" in d for d in decorators):
            return "value object"
        return ""

    def _init_attributes(
        self, func: ast.FunctionDef | ast.AsyncFunctionDef, class_id: str, source: SourceFile, ctx: AnalysisContext
    ) -> list[dict]:
        found: list[dict] = []
        param_types = {
            arg.arg: (_annotation_names(arg.annotation)[0] if _annotation_names(arg.annotation) else "")
            for arg in list(func.args.args) + list(func.args.kwonlyargs)
        }
        for node in ast.walk(func):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    value_type = ""
                    if isinstance(node.value, ast.Name):
                        value_type = param_types.get(node.value.id, "")
                    elif isinstance(node.value, ast.Call):
                        value_type = _decorator_name(node.value.func).split(".")[-1]
                    found.append({"name": target.attr, "type": value_type, "visibility": _visibility(target.attr)})
                    if value_type and value_type[0].isupper():
                        ctx.defer(
                            PendingRef(
                                source_id=class_id,
                                target_name=value_type,
                                kind=EdgeKind.COMPOSES,
                                language="python",
                                hint_module=source.module,
                            )
                        )
        return found

    # ------------------------------------------------------------ function
    def _function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        source: SourceFile,
        ctx: AnalysisContext,
        *,
        owner_id: str,
        owner_name: str,
    ) -> dict:
        decorators = [_decorator_name(d) for d in node.decorator_list]
        params = [
            {"name": arg.arg, "type": (_annotation_names(arg.annotation) or [""])[0]}
            for arg in node.args.args
            if arg.arg not in {"self", "cls"}
        ]
        returns = (_annotation_names(node.returns) or [""])[0]
        qualified = f"{owner_name}.{node.name}" if owner_name else self._qualified(source, node.name)
        func_node = ctx.declare_function(
            source,
            node.name,
            qualified_name=qualified,
            line=node.lineno,
            owner_id=owner_id,
            attributes={
                "params": params,
                "returns": returns,
                "visibility": _visibility(node.name),
                "is_async": isinstance(node, ast.AsyncFunctionDef),
                "decorators": decorators,
                "docstring": (ast.get_docstring(node) or "")[:300],
                "complexity": self._complexity(node),
            },
        )

        self._detect_route(node, decorators, source, ctx, func_node.id)

        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                target = _decorator_name(child.func)
                if not target:
                    continue
                simple = target.split(".")[-1]
                if simple in {"print", "len", "str", "int", "list", "dict", "set", "range", "isinstance"}:
                    continue
                ctx.defer(
                    PendingRef(
                        source_id=func_node.id,
                        target_name=simple,
                        kind=EdgeKind.CALLS,
                        language="python",
                        hint_module=source.module,
                        attributes={"expression": target, "line": child.lineno},
                    )
                )

        return {
            "name": node.name,
            "visibility": _visibility(node.name),
            "params": params,
            "returns": returns,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "is_abstract": any("abstractmethod" in d for d in decorators),
            "is_static": any(d in {"staticmethod", "classmethod"} for d in decorators),
        }

    @staticmethod
    def _complexity(node: ast.AST) -> int:
        score = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.With, ast.Assert)):
                score += 1
            elif isinstance(child, ast.BoolOp):
                score += len(child.values) - 1
        return score

    def _detect_route(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        decorators: list[str],
        source: SourceFile,
        ctx: AnalysisContext,
        func_id: str,
    ) -> None:
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            name = _decorator_name(decorator.func)
            verb = name.split(".")[-1].lower()
            if verb not in HTTP_DECORATORS:
                continue
            path = ""
            for arg in decorator.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    path = arg.value
                    break
            method = verb.upper() if verb != "route" else "ANY"
            endpoint = ctx.graph.add_node(
                Node(
                    id=f"api:{method}:{path or node.name}",
                    kind=NodeKind.API_ENDPOINT,
                    name=f"{method} {path or '/' + node.name}",
                    qualified_name=f"{method} {path}",
                    file=source.relative_path,
                    module=source.module,
                    language="python",
                    line=node.lineno,
                    attributes={"framework": name.split(".")[0], "handler": node.name},
                )
            )
            ctx.add_edge(endpoint.id, func_id, EdgeKind.EXPOSES)
            ctx.add_edge(ctx.file_id(source.relative_path), endpoint.id, EdgeKind.CONTAINS)

    # ----------------------------------------------------------------- orm
    def _orm_model(
        self, node: ast.ClassDef, source: SourceFile, ctx: AnalysisContext, class_id: str, table: str
    ) -> None:
        columns: list[dict] = []
        foreign_keys: list[dict] = []
        for item in node.body:
            targets: list[str] = []
            value: ast.expr | None = None
            if isinstance(item, ast.Assign):
                targets = [t.id for t in item.targets if isinstance(t, ast.Name)]
                value = item.value
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                targets = [item.target.id]
                value = item.value
            if not targets or value is None or not isinstance(value, ast.Call):
                continue
            callee = _decorator_name(value.func).split(".")[-1]
            if callee not in {"Column", "mapped_column", "relationship", "ForeignKey"} and not callee.endswith("Field"):
                continue
            column_type = ""
            nullable = True
            primary_key = False
            for arg in value.args:
                simple = _decorator_name(arg).split(".")[-1]
                if simple and simple[0].isupper() and not column_type:
                    column_type = simple
                if isinstance(arg, ast.Call) and _decorator_name(arg.func).endswith("ForeignKey"):
                    for fk_arg in arg.args:
                        if isinstance(fk_arg, ast.Constant) and isinstance(fk_arg.value, str):
                            foreign_keys.append({"column": targets[0], "references": fk_arg.value})
            for kw in value.keywords:
                if kw.arg == "primary_key" and isinstance(kw.value, ast.Constant):
                    primary_key = bool(kw.value.value)
                elif kw.arg == "nullable" and isinstance(kw.value, ast.Constant):
                    nullable = bool(kw.value.value)
                elif kw.arg == "index" and isinstance(kw.value, ast.Constant) and kw.value.value:
                    pass
            if callee == "relationship":
                for arg in value.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        ctx.defer(
                            PendingRef(
                                source_id=class_id,
                                target_name=arg.value,
                                kind=EdgeKind.ASSOCIATES,
                                language="python",
                                hint_module=source.module,
                            )
                        )
                continue
            columns.append(
                {
                    "name": targets[0],
                    "type": column_type or "TEXT",
                    "primary_key": primary_key,
                    "nullable": nullable,
                }
            )

        table_node = ctx.graph.add_node(
            Node(
                id=f"table:{table}",
                kind=NodeKind.TABLE,
                name=table,
                qualified_name=table,
                file=source.relative_path,
                module=source.module,
                language="sql",
                attributes={"columns": columns, "foreign_keys": foreign_keys, "origin": "orm"},
            )
        )
        ctx.add_edge(class_id, table_node.id, EdgeKind.WRITES, mapping="orm")
        for fk in foreign_keys:
            referenced = fk["references"].split(".")[0]
            ctx.defer(
                PendingRef(
                    source_id=table_node.id,
                    target_name=referenced,
                    kind=EdgeKind.REFERENCES,
                    language="sql",
                    attributes={"foreign_key": fk},
                )
            )

    # ------------------------------------------------------------ external
    def _external_endpoints(self, text: str, source: SourceFile, ctx: AnalysisContext, file_id: str) -> None:
        seen: set[str] = set()
        for match in URL_RE.finditer(text):
            url = match.group(0).rstrip("\"'),.;")
            host = url.split("//", 1)[-1].split("/")[0]
            if not host or host in seen or host.startswith("localhost") or "schema" in url or "w3.org" in host:
                continue
            seen.add(host)
            if len(seen) > 8:
                break
            node = ctx.ensure_external(host, kind=NodeKind.EXTERNAL_API, language="http")
            ctx.add_edge(file_id, node.id, EdgeKind.COMMUNICATES_WITH, url=url)


register(PythonAnalyzer())
