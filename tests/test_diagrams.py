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


def test_html_export_is_offline(graph):
    """Exported HTML must not pull Mermaid from the network."""
    diagram = generate("architecture", graph, DiagramFilters()).to_dict()
    html = export(diagram, "html", language="en")
    assert "cdn.jsdelivr" not in html
    assert "import mermaid from" not in html
    assert "type=\"module\"" not in html
    assert "mermaid.initialize" in html
    assert "mermaid.run" in html
    # Vendored UMD build is inlined so a single file opens on an air-gapped PC.
    assert "JM.mermaid=" in html or "globalThis" in html


def test_bundle_markdown(graph):
    results, _ = generate_all(graph, DiagramFilters())
    bundle = bundle_markdown("sample", [r.to_dict() for r in results], {"score": 80, "grade": "B"}, "en")
    assert "```mermaid" in bundle


def test_database_er_sanitizes_numeric_types(graph):
    """Mermaid rejects attribute types that start with a digit (parse error)."""
    from app.diagrams.database import _normalise_type, _attr_name
    from app.graph.model import Node, NodeKind

    assert _normalise_type("2001").startswith("t_")
    assert not _attr_name("2")[0].isdigit()

    # Inject a pathological table that previously blew up the ER parser.
    graph.add_node(
        Node(
            id="table:broken_er",
            kind=NodeKind.TABLE,
            name="broken_er",
            qualified_name="broken_er",
            language="sql",
            attributes={
                "columns": [
                    {"name": "custid", "type": "string", "primary_key": True, "nullable": False},
                    {"name": "_2_", "type": "2001", "nullable": True},
                    {"name": "_3_", "type": "2001", "nullable": True},
                ],
                "foreign_keys": [],
                "origin": "schema",
            },
        )
    )
    result = generate("database", graph, DiagramFilters())
    assert result.mermaid.startswith("erDiagram")
    for line in result.mermaid.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("erDiagram") or stripped in {"{", "}"}:
            continue
        if stripped.startswith("}") or "||" in stripped or "}o" in stripped or "}|" in stripped:
            continue
        # Attribute lines: type must not begin with a digit.
        token = stripped.split()[0]
        assert not token[0].isdigit(), stripped


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
