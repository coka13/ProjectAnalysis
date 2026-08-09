"""Diagram registry: single entry point for generating any supported diagram."""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.diagrams import architecture, class_diagram, database, flow, interaction
from app.diagrams.base import DiagramFilters, DiagramResult, EmptyDiagramError
from app.graph.model import KnowledgeGraph

log = logging.getLogger("aai.diagrams")

Generator = Callable[[KnowledgeGraph, DiagramFilters], DiagramResult]

GENERATORS: dict[str, Generator] = {
    "class": class_diagram.generate,
    "architecture": architecture.generate_architecture,
    "component": architecture.generate_component,
    "sequence": interaction.generate_sequence,
    "state": interaction.generate_state,
    "dataflow": flow.generate_dataflow,
    "dependency": flow.generate_dependency,
    "deployment": flow.generate_deployment,
    "database": database.generate,
}

DIAGRAM_KINDS = list(GENERATORS.keys())

DEFAULT_ORDER = [
    "architecture",
    "component",
    "class",
    "dependency",
    "sequence",
    "dataflow",
    "database",
    "deployment",
    "state",
]


def generate(kind: str, graph: KnowledgeGraph, filters: DiagramFilters | None = None) -> DiagramResult:
    generator = GENERATORS.get(kind)
    if not generator:
        raise ValueError(f"Unsupported diagram kind: {kind}")
    return generator(graph, filters or DiagramFilters())


def generate_all(
    graph: KnowledgeGraph,
    filters: DiagramFilters | None = None,
    kinds: list[str] | None = None,
) -> tuple[list[DiagramResult], dict[str, str]]:
    """Generate every applicable diagram, reporting why any were skipped."""
    results: list[DiagramResult] = []
    skipped: dict[str, str] = {}
    for kind in kinds or DEFAULT_ORDER:
        try:
            results.append(generate(kind, graph, filters))
        except EmptyDiagramError as exc:
            skipped[kind] = str(exc)
        except Exception as exc:  # noqa: BLE001 - one bad diagram must not stop the batch
            skipped[kind] = f"generation failed: {exc.__class__.__name__}"
            log.warning("diagram %s failed", kind, exc_info=True)
    return results, skipped
