"""The desktop bridge: the exact surface the UI calls through window.pywebview.api."""

from __future__ import annotations

import time

import pytest


def unwrap(response: dict):
    """Assert a bridge call succeeded and return its payload."""
    assert response["ok"], response.get("error")
    return response["data"]


def _wait_for(bridge, analysis_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    run = unwrap(bridge.analysis_status({"analysis_id": analysis_id}))
    while run["status"] not in {"succeeded", "failed", "cancelled"} and time.time() < deadline:
        time.sleep(0.2)
        run = unwrap(bridge.analysis_status({"analysis_id": analysis_id}))
    return run


@pytest.fixture()
def completed(bridge, sample_project):
    project = unwrap(
        bridge.project_create(
            {"name": "sample", "source_kind": "local", "source_location": str(sample_project)}
        )
    )
    run = unwrap(bridge.analysis_start({"project_id": project["id"], "include_history": False}))
    finished = _wait_for(bridge, run["id"])
    assert finished["status"] == "succeeded", finished["error"]
    return project, finished


def test_health_reports_capabilities(bridge):
    payload = unwrap(bridge.health())
    assert payload["status"] == "ok"
    assert set(payload["languages"]) == {"en", "he"}
    assert len(payload["diagram_kinds"]) == 9
    assert "markdown" in payload["export_formats"]


def test_errors_are_returned_not_raised(bridge):
    response = bridge.analysis_status({"analysis_id": "does-not-exist"})
    assert response["ok"] is False
    assert "not found" in response["error"].lower()


def test_missing_required_argument_is_reported(bridge):
    response = bridge.project_create({"name": "no source"})
    assert response["ok"] is False
    assert "source_location" in response["error"]


@pytest.mark.parametrize(
    "location",
    ["c:\\definitely\\missing\\path", "https://example.com/repo.git\n--upload-pack=evil"],
)
def test_invalid_sources_are_rejected(bridge, location):
    response = bridge.project_create({"name": "bad", "source_kind": "local", "source_location": location})
    assert response["ok"] is False


def test_project_lifecycle(bridge, sample_project):
    created = unwrap(
        bridge.project_create(
            {"name": "lifecycle", "source_kind": "local", "source_location": str(sample_project)}
        )
    )
    assert created["source_kind"] == "local"

    renamed = unwrap(bridge.project_update({"project_id": created["id"], "name": "renamed"}))
    assert renamed["name"] == "renamed"

    listing = unwrap(bridge.projects_list())
    assert any(p["id"] == created["id"] for p in listing)

    unwrap(bridge.project_delete({"project_id": created["id"]}))
    assert all(p["id"] != created["id"] for p in unwrap(bridge.projects_list()))


def test_analysis_produces_graph_metrics_and_diagrams(bridge, completed):
    _project, run = completed

    assert run["stats"]["files_scanned"] > 0
    metrics = unwrap(bridge.analysis_metrics({"analysis_id": run["id"]}))
    assert 0 <= metrics["metrics"]["score"] <= 100

    graph = unwrap(bridge.analysis_graph({"analysis_id": run["id"]}))
    assert graph["nodes"] and graph["edges"]

    hits = unwrap(bridge.analysis_search({"analysis_id": run["id"], "query": "Order"}))
    assert any("Order" in hit["name"] for hit in hits)

    diagrams = unwrap(bridge.diagrams_list({"analysis_id": run["id"]}))
    assert diagrams, "the analysis should have produced diagrams"
    assert all(d["mermaid"] for d in diagrams)


def test_diagram_generation_versioning_and_approval(bridge, completed):
    _project, run = completed

    generated = unwrap(
        bridge.diagram_generate(
            {"analysis_id": run["id"], "kind": "class", "filters": {"detail": "executive", "max_nodes": 12}}
        )
    )
    assert generated["kind"] == "class"
    assert generated["version"] == 1

    updated = unwrap(
        bridge.diagram_update({"diagram_id": generated["id"], "title": "Renamed", "note": "manual tweak"})
    )
    assert updated["title"] == "Renamed"
    assert updated["version"] == 2

    versions = unwrap(bridge.diagram_versions({"diagram_id": generated["id"]}))
    assert versions[0]["version"] == 1

    restored = unwrap(bridge.diagram_restore({"version_id": versions[0]["id"]}))
    assert restored["version"] == 3

    approved = unwrap(bridge.diagram_approval({"diagram_id": generated["id"], "state": "approved"}))
    assert approved["approval_state"] == "approved"
    assert bridge.diagram_approval({"diagram_id": generated["id"], "state": "bogus"})["ok"] is False


def test_comments_are_local_notes(bridge, completed):
    _project, run = completed
    diagram_id = unwrap(bridge.diagrams_list({"analysis_id": run["id"]}))[0]["id"]

    comment = unwrap(bridge.comment_add({"diagram_id": diagram_id, "body": "Check the data layer"}))
    assert comment["resolved"] is False

    assert unwrap(bridge.comment_toggle({"comment_id": comment["id"]}))["resolved"] is True
    assert unwrap(bridge.comments_list({"diagram_id": diagram_id}))

    unwrap(bridge.comment_delete({"comment_id": comment["id"]}))
    assert unwrap(bridge.comments_list({"diagram_id": diagram_id})) == []


def test_ai_falls_back_to_static_analysis(bridge, completed):
    _project, run = completed
    diagram_id = unwrap(bridge.diagrams_list({"analysis_id": run["id"]}))[0]["id"]

    explanation = unwrap(bridge.ai_explain({"diagram_id": diagram_id, "language": "he"}))
    assert explanation["source"] == "static"
    assert explanation["purpose"]

    # The explanation is cached per language on the diagram row.
    assert unwrap(bridge.diagram_get({"diagram_id": diagram_id}))["explanation"]["he"]

    review = unwrap(bridge.ai_review({"analysis_id": run["id"], "language": "en"}))
    assert 0 <= review["score"] <= 100

    refactor = unwrap(bridge.ai_refactor({"analysis_id": run["id"], "language": "en"}))
    assert "suggestions" in refactor

    query = unwrap(
        bridge.ai_query(
            {"analysis_id": run["id"], "prompt": "Show a simplified executive architecture view", "language": "en"}
        )
    )
    assert query["diagram"]["mermaid"]


def test_export_and_bundle(bridge, completed):
    _project, run = completed
    diagram_id = unwrap(bridge.diagrams_list({"analysis_id": run["id"]}))[0]["id"]

    markdown = unwrap(bridge.export_diagram({"diagram_id": diagram_id, "format": "markdown", "language": "en"}))
    assert "```mermaid" in markdown["content"]
    assert markdown["filename"].endswith(".md")

    assert bridge.export_diagram({"diagram_id": diagram_id, "format": "docx"})["ok"] is False

    bundle = unwrap(bridge.export_bundle({"analysis_id": run["id"], "language": "en"}))
    assert bundle["content"].startswith("#")
    assert bundle["filename"].endswith(".md")


def test_compare_two_analyses(bridge, completed):
    project, first = completed
    second = _wait_for(bridge, unwrap(bridge.analysis_start({"project_id": project["id"], "include_history": False}))["id"])
    assert second["status"] == "succeeded"

    result = unwrap(
        bridge.compare_analyses(
            {"base_analysis_id": first["id"], "head_analysis_id": second["id"], "language": "en"}
        )
    )
    assert result["diff"]["impact"] in {"none", "low", "medium", "high"}
    assert result["narrative"]["source"] == "static"


def test_provider_configuration_is_encrypted_at_rest(bridge):
    saved = unwrap(
        bridge.provider_save(
            {"base_url": "http://localhost:11434/v1", "model": "qwen2.5-coder:14b", "api_key": "sk-local-secret"}
        )
    )
    assert saved["model"] == "qwen2.5-coder:14b"
    assert "sk-local-secret" not in saved["api_key_masked"]

    from app.db import session_scope
    from app.services import provider_service

    with session_scope() as session:
        stored = provider_service.find_config(session)
        assert stored is not None
        assert stored.api_key_encrypted != "sk-local-secret"

    unwrap(bridge.provider_clear())
    assert unwrap(bridge.provider_get()) == {"configured": False}


def test_provider_test_without_configuration_reports_error(bridge):
    unwrap(bridge.provider_clear())
    response = bridge.provider_test()
    assert response["ok"] is False
    assert "provider" in response["error"].lower()


def test_settings_summary_points_at_the_local_data_dir(bridge):
    summary = unwrap(bridge.settings_summary())
    assert summary["data_dir"]
    assert summary["database"].startswith("sqlite:///")


def test_the_launcher_can_tell_whether_the_window_engine_is_usable():
    """The runtime probe runs before anything is on screen, so it must not throw.

    A machine without the WebView2 runtime silently gets the Internet Explorer
    engine and a completely blank window; the probe is what lets the launcher
    name that cause. An exception here would replace the black screen with a
    crash, which is not an improvement.
    """
    from app.desktop import window

    assert window.webview2_installed() in (True, False)


def test_a_blank_window_is_never_left_unexplained():
    """index.html must be able to report a failure without any of its scripts."""
    from app.desktop.window import WEB_ROOT

    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    assert "__BOOT_ERRORS__" in html
    # The recovery path must come first, or a parse error beats it to the punch.
    assert html.index("__BOOT_ERRORS__") < html.index("js/app.js")
