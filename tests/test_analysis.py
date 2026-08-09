"""Analysis pipeline, analyzers and graph metrics."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.graph.metrics import compute
from app.graph.model import EdgeKind, NodeKind
from app.services import analysis_service


def names(graph, kind):
    return {node.name for node in graph.by_kind(kind)}


def test_python_classes_and_inheritance(graph):
    classes = names(graph, NodeKind.CLASS) | names(graph, NodeKind.ABSTRACT_CLASS)
    assert {"OrderService", "OrderRepository", "Order"} <= classes

    inherits = [
        (graph.nodes[e.source].name, graph.nodes[e.target].name)
        for e in graph.edges.values()
        if e.kind in (EdgeKind.INHERITS, EdgeKind.IMPLEMENTS)
    ]
    assert ("OrderRepository", "Repository") in inherits


def test_enum_and_state_detection(graph):
    enums = names(graph, NodeKind.ENUM)
    assert "OrderStatus" in enums
    node = next(n for n in graph.by_kind(NodeKind.ENUM) if n.name == "OrderStatus")
    assert node.attributes.get("is_state_machine") is True
    assert "NEW" in (node.attributes.get("states") or [])


def test_api_endpoints_detected(graph):
    endpoints = {node.name for node in graph.by_kind(NodeKind.API_ENDPOINT)}
    assert any("/orders" in name for name in endpoints)


def test_sql_tables_and_foreign_keys(graph):
    tables = names(graph, NodeKind.TABLE)
    assert {"customers", "orders"} <= tables
    fks = [
        (graph.nodes[e.source].name, graph.nodes[e.target].name)
        for e in graph.edges.values()
        if e.kind == EdgeKind.REFERENCES
    ]
    assert ("orders", "customers") in fks


def test_infrastructure_detected(graph):
    containers = names(graph, NodeKind.CONTAINER) | names(graph, NodeKind.COMPONENT) | names(graph, NodeKind.SERVICE)
    assert containers, "expected container/service nodes from Dockerfile and compose"


def test_typescript_interface_implementation(graph):
    interfaces = names(graph, NodeKind.INTERFACE)
    assert "Notifier" in interfaces


def test_metrics_report(graph):
    report = compute(graph)
    assert 0 <= report["score"] <= 100
    assert report["grade"] in {"A", "B", "C", "D", "E"}
    assert "cycles" in report and isinstance(report["cycles"], list)


def test_pipeline_reports_no_warnings(analysis):
    _, report, warnings = analysis
    assert warnings == []
    assert report["stats"]["files_scanned"] >= 6
    assert report["stats"]["files_analyzed"] >= 1


def test_a_relocated_data_directory_still_finds_the_stored_graph(graph, tmp_path):
    """Renaming the data directory must not orphan every earlier analysis.

    ``graph_path`` is persisted as an absolute path, so when the application
    folder was renamed every stored run pointed at a directory that no longer
    existed and every graph-backed view failed with "stored graph is missing on
    disk". The file is always written to <data_dir>/graphs/<id>.json, so that
    canonical location is the fallback.
    """
    run_id = "relocated-run"
    canonical = analysis_service.graph_path_for(run_id)
    graph.save(canonical)
    stale = tmp_path / "OldProductName" / "graphs" / f"{run_id}.json"

    run = SimpleNamespace(id=run_id, graph_path=str(stale))
    loaded = analysis_service.load_graph(run)

    assert loaded.nodes
    # The row is healed in place so the fallback is paid once, not per call.
    assert run.graph_path == str(canonical)


def test_a_graph_that_was_really_deleted_still_reports_the_truth(tmp_path):
    run = SimpleNamespace(id="never-stored", graph_path=str(tmp_path / "gone.json"))
    with pytest.raises(FileNotFoundError):
        analysis_service.load_graph(run)
