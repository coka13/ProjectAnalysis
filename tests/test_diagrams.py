"""Diagram generation, export and comparison."""

from __future__ import annotations

import pytest

from app.compare.architecture_diff import compare
from app.diagrams.base import DiagramFilters, EmptyDiagramError
from app.diagrams.registry import DIAGRAM_KINDS, generate, generate_all
from app.export.exporters import EXPORT_FORMATS, bundle_markdown, export

MERMAID_HEADERS = (
    "flowchart",
    "graph",
    "classDiagram",
    "sequenceDiagram",
    "erDiagram",
    "stateDiagram",
)


@pytest.mark.parametrize("kind", DIAGRAM_KINDS)
def test_every_diagram_kind_renders_or_explains_itself(graph, kind):
    try:
        result = generate(kind, graph, DiagramFilters())
    except EmptyDiagramError as exc:
        assert str(exc), "an empty diagram must explain why"
        return
    assert result.kind == kind
    assert result.title
    assert any(header in result.mermaid for header in MERMAID_HEADERS)
    assert "%%" in result.mermaid or result.mermaid.strip()


def test_generate_all_covers_the_sample_project(graph):
    results, skipped = generate_all(graph, DiagramFilters())
    produced = {item.kind for item in results}
    assert {"architecture", "class", "database", "deployment"} <= produced
    assert set(skipped) & set(DIAGRAM_KINDS) == set(skipped)


def test_detail_level_limits_diagram_size(graph):
    executive = generate("architecture", graph, DiagramFilters(detail="executive"))
    detailed = generate("architecture", graph, DiagramFilters(detail="detailed"))
    assert len(executive.mermaid) <= len(detailed.mermaid)


@pytest.mark.parametrize("fmt", EXPORT_FORMATS)
def test_export_formats(graph, fmt):
    diagram = generate("architecture", graph, DiagramFilters()).to_dict()
    content = export(diagram, fmt, language="en")
    assert content.strip()


def test_export_rejects_unknown_format(graph):
    diagram = generate("architecture", graph, DiagramFilters()).to_dict()
    with pytest.raises(ValueError):
        export(diagram, "postscript")


def test_hebrew_export_is_rtl(graph):
    diagram = generate("architecture", graph, DiagramFilters()).to_dict()
    html = export(diagram, "html", language="he")
    assert 'dir="rtl"' in html


def test_bundle_markdown(graph):
    results, _ = generate_all(graph, DiagramFilters())
    bundle = bundle_markdown("sample", [r.to_dict() for r in results], {"score": 80, "grade": "B"}, "en")
    assert "```mermaid" in bundle


def test_compare_detects_removal(graph):
    trimmed_ids = [node.id for node in list(graph.nodes.values())[: max(1, len(graph.nodes) - 3)]]
    trimmed = graph.subgraph(trimmed_ids)
    diff = compare(trimmed, graph)
    assert diff["impact"] in {"low", "medium", "high"}
    assert diff["summary"]
    assert isinstance(diff["added_nodes"], list)


def test_compare_identical_graphs_is_low_impact(graph):
    diff = compare(graph, graph)
    assert diff["added_nodes"] == []
    assert diff["removed_nodes"] == []
    assert diff["impact"] == "low"
