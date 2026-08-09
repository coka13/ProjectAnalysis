"""Infrastructure and manifest analyzer: Docker, Compose, Kubernetes, build files."""

from __future__ import annotations

import json
import logging
import re

import yaml

from app.analyzers.base import Analyzer, AnalysisContext, PendingRef, register
from app.graph.model import EdgeKind, Node, NodeKind
from app.ingest.walker import SourceFile

log = logging.getLogger("aai.analyzers.infra")

IMAGE_TO_TECH = {
    "postgres": (NodeKind.DATABASE, "PostgreSQL"),
    "mysql": (NodeKind.DATABASE, "MySQL"),
    "mariadb": (NodeKind.DATABASE, "MariaDB"),
    "mongo": (NodeKind.DATABASE, "MongoDB"),
    "redis": (NodeKind.DATABASE, "Redis"),
    "elasticsearch": (NodeKind.DATABASE, "Elasticsearch"),
    "cassandra": (NodeKind.DATABASE, "Cassandra"),
    "clickhouse": (NodeKind.DATABASE, "ClickHouse"),
    "rabbitmq": (NodeKind.QUEUE, "RabbitMQ"),
    "kafka": (NodeKind.QUEUE, "Kafka"),
    "zookeeper": (NodeKind.QUEUE, "ZooKeeper"),
    "nats": (NodeKind.QUEUE, "NATS"),
    "nginx": (NodeKind.COMPONENT, "NGINX"),
    "traefik": (NodeKind.COMPONENT, "Traefik"),
    "haproxy": (NodeKind.COMPONENT, "HAProxy"),
    "minio": (NodeKind.DATA_STORE, "MinIO"),
    "vault": (NodeKind.COMPONENT, "Vault"),
    "keycloak": (NodeKind.COMPONENT, "Keycloak"),
}

K8S_WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "ReplicaSet", "Pod"}


def _classify_image(image: str) -> tuple[str, str]:
    lowered = (image or "").lower()
    for token, (kind, label) in IMAGE_TO_TECH.items():
        if token in lowered:
            return kind, label
    return NodeKind.CONTAINER, image or "container"


class InfraAnalyzer(Analyzer):
    name = "infrastructure"
    languages = set()
    infra_kinds = {"docker", "compose", "npm", "pip", "gomod", "cargo", "maven", "gradle", "helm", "serverless", "proc", "make"}
    priority = 40

    def accepts(self, source: SourceFile) -> bool:
        if source.infra_kind in self.infra_kinds:
            return True
        # Kubernetes manifests can be any yaml file.
        return source.language == "yaml"

    def analyze(self, source: SourceFile, ctx: AnalysisContext) -> None:
        file_node = ctx.ensure_file(source)
        kind = source.infra_kind
        try:
            if kind == "docker":
                self._dockerfile(source, ctx, file_node)
            elif kind == "compose":
                self._compose(source, ctx, file_node)
            elif kind == "npm":
                self._package_json(source, ctx, file_node)
            elif kind == "pip":
                self._python_manifest(source, ctx, file_node)
            elif kind == "gomod":
                self._simple_manifest(source, ctx, file_node, re.compile(r"^\s*([\w\.\-/]+)\s+v[\w\.\-+]+", re.MULTILINE))
            elif kind == "cargo":
                self._cargo(source, ctx, file_node)
            elif kind == "maven":
                self._maven(source, ctx, file_node)
            elif kind == "gradle":
                self._simple_manifest(
                    source, ctx, file_node, re.compile(r"""(?:implementation|api|compile)\s*[\('"]+([\w\.\-:]+)""")
                )
            elif source.language == "yaml":
                self._kubernetes(source, ctx, file_node)
        except (yaml.YAMLError, json.JSONDecodeError, ValueError) as exc:
            ctx.warnings.append(f"{source.relative_path}: manifest parse error ({exc.__class__.__name__})")

    # ------------------------------------------------------------- docker
    def _dockerfile(self, source: SourceFile, ctx: AnalysisContext, file_node: Node) -> None:
        text = source.text()
        images = re.findall(r"^\s*FROM\s+([^\s]+)", text, re.MULTILINE | re.IGNORECASE)
        ports = re.findall(r"^\s*EXPOSE\s+([0-9\s]+)", text, re.MULTILINE | re.IGNORECASE)
        entrypoint = re.search(r"^\s*(?:ENTRYPOINT|CMD)\s+(.+)$", text, re.MULTILINE | re.IGNORECASE)
        service_name = source.module if source.module != "(root)" else "app"
        container = ctx.graph.add_node(
            Node(
                id=f"container:{service_name}",
                kind=NodeKind.CONTAINER,
                name=service_name,
                qualified_name=service_name,
                file=source.relative_path,
                module=source.module,
                language="docker",
                attributes={
                    "base_images": images,
                    "ports": [p for group in ports for p in group.split()],
                    "entrypoint": entrypoint.group(1).strip() if entrypoint else "",
                    "origin": "dockerfile",
                },
            )
        )
        ctx.add_edge(file_node.id, container.id, EdgeKind.DEPLOYS)

    def _compose(self, source: SourceFile, ctx: AnalysisContext, file_node: Node) -> None:
        data = yaml.safe_load(source.text()) or {}
        services = data.get("services") or {}
        if not isinstance(services, dict):
            return
        created: dict[str, Node] = {}
        for name, spec in services.items():
            spec = spec or {}
            image = spec.get("image", "") if isinstance(spec, dict) else ""
            kind, label = _classify_image(image or name)
            node = ctx.graph.add_node(
                Node(
                    id=f"container:{name}",
                    kind=kind,
                    name=name,
                    qualified_name=name,
                    file=source.relative_path,
                    module=source.module,
                    language="docker",
                    attributes={
                        "image": image,
                        "technology": label,
                        "ports": [str(p) for p in (spec.get("ports") or [])] if isinstance(spec, dict) else [],
                        "environment_keys": sorted(
                            (spec.get("environment") or {}).keys()
                            if isinstance(spec.get("environment"), dict)
                            else [str(e).split("=")[0] for e in (spec.get("environment") or [])]
                        )[:20],
                        "replicas": ((spec.get("deploy") or {}).get("replicas") if isinstance(spec, dict) else None) or 1,
                        "origin": "compose",
                    },
                )
            )
            created[name] = node
            ctx.add_edge(file_node.id, node.id, EdgeKind.DEPLOYS)
        for name, spec in services.items():
            spec = spec or {}
            if not isinstance(spec, dict):
                continue
            for dependency in list(spec.get("depends_on") or []) + list(spec.get("links") or []):
                dep_name = dependency if isinstance(dependency, str) else str(dependency)
                target = created.get(dep_name.split(":")[0])
                if target and name in created:
                    ctx.add_edge(created[name].id, target.id, EdgeKind.COMMUNICATES_WITH, via="compose")

    # --------------------------------------------------------- kubernetes
    def _kubernetes(self, source: SourceFile, ctx: AnalysisContext, file_node: Node) -> None:
        text = source.text()
        if "apiVersion" not in text or "kind" not in text:
            return
        documents = [doc for doc in yaml.safe_load_all(text) if isinstance(doc, dict)]
        for doc in documents:
            kind = doc.get("kind", "")
            metadata = doc.get("metadata") or {}
            name = metadata.get("name") or "unnamed"
            if kind in K8S_WORKLOADS:
                spec = doc.get("spec") or {}
                template = ((spec.get("template") or {}).get("spec")) or {}
                containers = template.get("containers") or []
                images = [c.get("image", "") for c in containers if isinstance(c, dict)]
                node_kind, label = _classify_image(images[0] if images else name)
                workload = ctx.graph.add_node(
                    Node(
                        id=f"k8s:{kind}:{name}",
                        kind=node_kind if node_kind != NodeKind.CONTAINER else NodeKind.CONTAINER,
                        name=name,
                        qualified_name=f"{kind}/{name}",
                        file=source.relative_path,
                        module=source.module,
                        language="kubernetes",
                        attributes={
                            "workload": kind,
                            "images": images,
                            "replicas": spec.get("replicas", 1),
                            "namespace": metadata.get("namespace", "default"),
                            "technology": label,
                            "origin": "kubernetes",
                        },
                    )
                )
                ctx.add_edge(file_node.id, workload.id, EdgeKind.DEPLOYS)
            elif kind in {"Service", "Ingress"}:
                spec = doc.get("spec") or {}
                node = ctx.graph.add_node(
                    Node(
                        id=f"k8s:{kind}:{name}",
                        kind=NodeKind.COMPONENT,
                        name=name,
                        qualified_name=f"{kind}/{name}",
                        file=source.relative_path,
                        module=source.module,
                        language="kubernetes",
                        attributes={
                            "workload": kind,
                            "type": spec.get("type", ""),
                            "ports": [str(p.get("port")) for p in (spec.get("ports") or []) if isinstance(p, dict)],
                            "origin": "kubernetes",
                        },
                    )
                )
                ctx.add_edge(file_node.id, node.id, EdgeKind.DEPLOYS)
                selector = spec.get("selector") or {}
                target_name = selector.get("app") if isinstance(selector, dict) else None
                if target_name:
                    ctx.defer(
                        PendingRef(
                            source_id=node.id,
                            target_name=str(target_name),
                            kind=EdgeKind.COMMUNICATES_WITH,
                            language="kubernetes",
                        )
                    )

    # ------------------------------------------------------------ manifests
    def _package_json(self, source: SourceFile, ctx: AnalysisContext, file_node: Node) -> None:
        data = json.loads(source.text())
        module_node = ctx.ensure_module(source.module, "javascript")
        module_node.attributes["package_name"] = data.get("name", "")
        module_node.attributes["scripts"] = list((data.get("scripts") or {}).keys())[:20]
        dependencies = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        for package, version in list(dependencies.items())[:200]:
            node = ctx.ensure_external(package, kind=NodeKind.EXTERNAL_PACKAGE, language="javascript")
            node.attributes["version"] = version
            ctx.add_edge(module_node.id, node.id, EdgeKind.DEPENDS_ON, version=version)

    def _python_manifest(self, source: SourceFile, ctx: AnalysisContext, file_node: Node) -> None:
        text = source.text()
        module_node = ctx.ensure_module(source.module, "python")
        packages: list[str] = []
        if source.path.name == "pyproject.toml":
            block = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
            if block:
                packages = re.findall(r'"([A-Za-z0-9_\-\[\]\.]+)', block.group(1))
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-")):
                    continue
                packages.append(re.split(r"[<>=!~;\[]", line)[0].strip())
        for package in packages[:200]:
            if not package:
                continue
            node = ctx.ensure_external(package, kind=NodeKind.EXTERNAL_PACKAGE, language="python")
            ctx.add_edge(module_node.id, node.id, EdgeKind.DEPENDS_ON)

    def _cargo(self, source: SourceFile, ctx: AnalysisContext, file_node: Node) -> None:
        text = source.text()
        module_node = ctx.ensure_module(source.module, "rust")
        block = re.search(r"\[dependencies\](.*?)(?:\n\[|\Z)", text, re.DOTALL)
        if not block:
            return
        for match in re.finditer(r"^\s*([A-Za-z0-9_\-]+)\s*=", block.group(1), re.MULTILINE):
            node = ctx.ensure_external(match.group(1), kind=NodeKind.EXTERNAL_PACKAGE, language="rust")
            ctx.add_edge(module_node.id, node.id, EdgeKind.DEPENDS_ON)

    def _maven(self, source: SourceFile, ctx: AnalysisContext, file_node: Node) -> None:
        text = source.text()
        module_node = ctx.ensure_module(source.module, "java")
        for match in re.finditer(
            r"<dependency>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>", text, re.DOTALL
        ):
            name = f"{match.group(1)}:{match.group(2)}"
            node = ctx.ensure_external(name, kind=NodeKind.EXTERNAL_PACKAGE, language="java")
            ctx.add_edge(module_node.id, node.id, EdgeKind.DEPENDS_ON)

    def _simple_manifest(self, source: SourceFile, ctx: AnalysisContext, file_node: Node, pattern: re.Pattern[str]) -> None:
        module_node = ctx.ensure_module(source.module, source.language)
        for match in list(pattern.finditer(source.text()))[:200]:
            name = match.group(1)
            node = ctx.ensure_external(name, kind=NodeKind.EXTERNAL_PACKAGE, language=source.language)
            ctx.add_edge(module_node.id, node.id, EdgeKind.DEPENDS_ON)


register(InfraAnalyzer())
