"""The scoring subsystem: category maths, the weighted scorecard and its bridge endpoints."""

from __future__ import annotations

import time

import pytest

from app.graph import scoring


def unwrap(response: dict):
    assert response["ok"], response.get("error")
    return response["data"]


def _wait_for(bridge, analysis_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    run = unwrap(bridge.analysis_status({"analysis_id": analysis_id}))
    while run["status"] not in {"succeeded", "failed", "cancelled"} and time.time() < deadline:
        time.sleep(0.2)
        run = unwrap(bridge.analysis_status({"analysis_id": analysis_id}))
    return run


def _run_analysis(bridge, project_id: str) -> dict:
    started = unwrap(bridge.analysis_start({"project_id": project_id, "include_history": False}))
    finished = _wait_for(bridge, started["id"])
    assert finished["status"] == "succeeded", finished["error"]
    return finished


@pytest.fixture()
def scored(bridge, sample_project):
    project = unwrap(
        bridge.project_create(
            {"name": "scored", "source_kind": "local", "source_location": str(sample_project)}
        )
    )
    return project, _run_analysis(bridge, project["id"])


@pytest.fixture()
def card(bridge, scored):
    _project, run = scored
    return unwrap(bridge.score_card({"analysis_id": run["id"]}))["scorecard"]


@pytest.fixture()
def default_weights(bridge):
    """Any test that edits the weights file must leave it as it found it."""
    yield
    unwrap(bridge.score_weights_reset({}))


# --------------------------------------------------------------------- maths
def test_default_weights_form_a_distribution():
    assert set(scoring.DEFAULT_WEIGHTS) == set(scoring.CATEGORY_ORDER)
    assert round(sum(scoring.DEFAULT_WEIGHTS.values()), 6) == 1.0


def test_signals_explain_their_own_arithmetic():
    """A point total is meaningless without the weight that scales it."""
    category = scoring.Category(id="architecture")
    category.deduct("arch.cycles", "Dependency cycles", 8.0, severity="high", detail="2 cycles", value=2)
    built = category.build(0.2)
    signal = built["signals"][0]
    assert signal["category_id"] == "architecture"
    assert signal["weight_pct"] == 20.0
    # -8 inside a category worth a fifth of the total costs 1.6 overall.
    assert signal["overall_impact"] == pytest.approx(-1.6)


def test_the_fix_is_attached_to_the_signal_it_clears():
    """Signals and recommendations were already keyed together but rendered apart."""
    category = scoring.Category(id="architecture")
    category.deduct("arch.cycles", "Dependency cycles", 8.0, severity="high")
    category.deduct("arch.hubs", "Hub modules", 2.0, severity="low")
    category.recommend(
        "break-cycles",
        "Break the dependency cycles",
        why="They prevent independent release",
        how="Invert the closing edge",
        effort="high",
        recovers=8.0,
        signal="arch.cycles",
    )
    signals = {s["id"]: s for s in category.build(0.2)["signals"]}
    assert signals["arch.cycles"]["remediation"]["title"] == "Break the dependency cycles"
    assert signals["arch.cycles"]["remediation"]["how"]
    assert signals["arch.hubs"]["remediation"] is None


def test_missing_weights_fall_back_to_defaults():
    normalised = scoring.normalise_weights({"security": 0.5})
    assert round(sum(normalised.values()), 3) == 1.0
    # every category is still represented, so the ratios shift but nothing vanishes
    assert set(normalised) == set(scoring.CATEGORY_ORDER)
    assert normalised["security"] > scoring.DEFAULT_WEIGHTS["security"]


def test_explicit_zeros_are_honoured_and_totals_renormalised():
    raw = {key: 0.0 for key in scoring.CATEGORY_ORDER}
    raw["security"] = 1.0
    normalised = scoring.normalise_weights(raw)
    assert normalised["security"] == pytest.approx(1.0)
    assert all(normalised[key] == 0.0 for key in scoring.CATEGORY_ORDER if key != "security")


def test_unusable_weights_fall_back_rather_than_divide_by_zero():
    assert scoring.normalise_weights({key: 0 for key in scoring.CATEGORY_ORDER}) == scoring.DEFAULT_WEIGHTS
    assert scoring.normalise_weights({"security": "not a number"})["security"] > 0


@pytest.mark.parametrize(
    ("score", "grade", "band"),
    [
        (100, "A+", "excellent"),
        (91, "A", "excellent"),
        (78, "C+", "good"),
        (62, "D", "fair"),
        (45, "F", "poor"),
        (10, "F", "critical"),
    ],
)
def test_grade_and_band_boundaries(score, grade, band):
    assert scoring.grade_for(score) == grade
    assert scoring.band_for(score) == band


def test_category_accumulates_deductions_and_credits():
    category = scoring.Category("security")
    category.deduct("sec.x", "Hardcoded secret", 30, severity="critical", detail="one found")
    category.credit("sec.y", "No debug endpoints exposed", 5)
    built = category.build(0.2)

    assert built["score"] == 75
    assert built["grade"] == scoring.grade_for(75)
    assert built["issue_count"] == 1
    assert built["contribution"] == pytest.approx(15.0, abs=0.01)
    assert built["lost_points"] == pytest.approx(5.0, abs=0.01)
    assert built["issues"][0]["impact"] == -30
    assert built["strengths"][0]["impact"] == 5
    assert built["summary"]


def test_a_category_score_stays_inside_zero_to_one_hundred():
    drowning = scoring.Category("testing")
    drowning.deduct("t.a", "No tests at all", 400, severity="critical")
    assert drowning.build(0.1)["score"] == 0

    thriving = scoring.Category("testing")
    thriving.credit("t.b", "Exemplary coverage", 40)
    assert thriving.build(0.1)["score"] == 100


def test_zero_impact_signals_are_not_recorded_as_deductions():
    category = scoring.Category("performance")
    category.deduct("p.a", "Nothing to see", 0)
    category.pass_note("p.b", "Checked", detail="no findings")
    built = category.build(0.1)

    assert built["score"] == 100
    assert built["issue_count"] == 0
    assert [signal["id"] for signal in built["signals"]] == ["p.b"]


# ---------------------------------------------------------------- end-to-end
def test_the_scorecard_explains_its_own_arithmetic(card):
    assert card["version"] == 2
    assert 0 <= card["overall"] <= 100
    assert card["grade"] == scoring.grade_for(card["overall"])
    assert card["band"] == scoring.band_for(card["overall"])
    assert [c["id"] for c in card["categories"]] == list(scoring.CATEGORY_ORDER)
    assert round(sum(c["weight"] for c in card["categories"]), 3) == 1.0
    assert card["headline"]

    # the weighted contributions must reconstruct the headline number
    total = sum(c["score_exact"] * c["weight"] for c in card["categories"])
    assert total == pytest.approx(card["overall_exact"], abs=0.5)

    assert card["category_index"] == {c["id"]: c["score"] for c in card["categories"]}
    assert card["weakest_category"]["score"] <= card["strongest_category"]["score"]
    assert card["overall"] <= card["potential_score"] <= 100


def test_every_finding_carries_a_human_explanation(card):
    for category in card["categories"]:
        for issue in category["issues"]:
            assert issue["label"], f"{category['id']}/{issue['id']} has no label"
            assert issue["impact"] < 0
            assert issue["severity"] in {"critical", "high", "medium", "low", "info"}
        for recommendation in category["recommendations"]:
            assert recommendation["why"] and recommendation["how"]
            assert recommendation["effort"] in scoring.EFFORT_ORDER
            assert recommendation["category_gain"] > 0
        assert category["issue_count"] == len(category["issues"])


def test_the_roadmap_is_ranked_by_return_on_effort(card):
    roadmap = card["roadmap"]
    assert set(roadmap) >= {"quick_wins", "medium_term", "long_term", "all", "total_potential_gain"}

    actions = roadmap["all"]
    assert [a["rank"] for a in actions] == list(range(1, len(actions) + 1))
    assert [a["roi"] for a in actions] == sorted((a["roi"] for a in actions), reverse=True)
    assert all(a["overall_gain"] > 0 for a in actions)
    assert all(a["priority"] in {"critical", "high", "medium", "low"} for a in actions)
    assert all(a["effort"] == "low" for a in roadmap["quick_wins"])
    assert all(a["effort"] == "high" for a in roadmap["long_term"])


def test_a_category_can_be_opened_on_its_own(bridge, scored):
    _project, run = scored
    payload = unwrap(bridge.score_category({"analysis_id": run["id"], "category": "architecture"}))
    detail = payload["category"]

    assert detail["id"] == "architecture"
    assert detail["label"] == "Architecture"
    assert detail["summary"]
    assert isinstance(detail["metrics"], dict)
    assert len(detail["issues"]) + len(detail["strengths"]) == len(detail["signals"])
    assert payload["weights"]["architecture"] == detail["weight"]


def test_an_unknown_category_is_refused(bridge, scored):
    _project, run = scored
    assert bridge.score_category({"analysis_id": run["id"], "category": "vibes"})["ok"] is False


def test_evidence_resolves_back_to_the_source(bridge, card, scored):
    _project, run = scored
    inspected = 0

    for category in card["categories"]:
        for signal in category["signals"]:
            if not signal["evidence"]:
                continue
            result = unwrap(
                bridge.score_evidence(
                    {"analysis_id": run["id"], "category": category["id"], "signal": signal["id"]}
                )
            )
            assert result["signal"]["id"] == signal["id"]
            assert result["category"] == category["label"]
            assert len(result["evidence"]) == len(signal["evidence"])

            for item in result["evidence"]:
                if not item.get("file"):
                    continue
                excerpt = item.get("excerpt") or []
                assert excerpt, f"{signal['id']} produced no excerpt for {item['file']}"
                assert all({"line", "text", "highlight"} <= set(row) for row in excerpt)
                if item.get("line"):
                    highlighted = [row["line"] for row in excerpt if row["highlight"]]
                    assert highlighted == [item["line"]]
                inspected += 1

    assert inspected, "the sample project should produce at least one file-backed finding"


def test_evidence_never_reads_outside_the_project(tmp_path):
    from app.desktop.bridge import _read_excerpt

    (tmp_path / "secret.txt").write_text("do not leak me", encoding="utf-8")
    root = tmp_path / "project"
    root.mkdir()

    assert _read_excerpt(root, "../secret.txt", 1) == []
    assert _read_excerpt(root, "", 1) == []


def test_an_unknown_signal_is_refused(bridge, scored):
    _project, run = scored
    response = bridge.score_evidence(
        {"analysis_id": run["id"], "category": "security", "signal": "does.not.exist"}
    )
    assert response["ok"] is False


def test_weights_can_be_tuned_and_reset(bridge, scored, card, default_weights):
    _project, run = scored
    catalogue = unwrap(bridge.score_weights({}))
    assert [c["id"] for c in catalogue["categories"]] == list(scoring.CATEGORY_ORDER)
    assert catalogue["defaults"] == scoring.DEFAULT_WEIGHTS

    security_only = {key: 0.0 for key in scoring.CATEGORY_ORDER}
    security_only["security"] = 1.0
    saved = unwrap(bridge.score_weights_save({"weights": security_only}))["weights"]
    assert saved["security"] == pytest.approx(1.0)
    assert unwrap(bridge.score_weights({}))["weights"]["security"] == pytest.approx(1.0)

    rescored = unwrap(bridge.score_recompute({"analysis_id": run["id"]}))["scorecard"]
    assert rescored["overall"] == pytest.approx(rescored["category_index"]["security"], abs=1)

    unwrap(bridge.score_weights_reset({}))
    restored = unwrap(bridge.score_recompute({"analysis_id": run["id"]}))["scorecard"]
    assert restored["overall"] == card["overall"]


def test_recomputing_persists_the_new_card(bridge, scored, default_weights):
    _project, run = scored
    docs_only = {key: 0.0 for key in scoring.CATEGORY_ORDER}
    docs_only["documentation"] = 1.0
    unwrap(bridge.score_weights_save({"weights": docs_only}))

    recomputed = unwrap(bridge.score_recompute({"analysis_id": run["id"]}))["scorecard"]
    reloaded = unwrap(bridge.score_card({"analysis_id": run["id"]}))["scorecard"]
    assert reloaded["overall"] == recomputed["overall"]
    assert reloaded["weights"]["documentation"] == pytest.approx(1.0)


def test_file_rows_feed_the_hotspot_views(bridge, scored):
    _project, run = scored
    payload = unwrap(bridge.score_files({"analysis_id": run["id"]}))

    assert payload["files"], "every analysed file should yield a row"
    assert payload["total_loc"] > 0
    assert payload["total_files"] >= len(payload["files"])
    risks = [row["risk"] for row in payload["files"]]
    assert risks == sorted(risks, reverse=True)
    assert all({"file", "loc", "findings", "debt_markers", "risk"} <= set(row) for row in payload["files"])
    assert all(row["loc"] > 0 for row in payload["files"])


def test_the_drawer_gets_the_individual_findings_not_just_counters(bridge, scored):
    """A count tells you a file is risky; only the findings tell you why."""
    _project, run = scored
    rows = unwrap(bridge.score_files({"analysis_id": run["id"]}))["files"]
    worst = next((row for row in rows if row["findings"]), rows[0])

    detail = unwrap(bridge.score_file_detail({"analysis_id": run["id"], "file": worst["file"]}))
    assert detail["file"] == worst["file"]
    assert detail["loc"] == worst["loc"]
    assert detail["findings_reported"] == worst["findings"]
    for finding in detail["findings"]:
        assert finding["file"] == worst["file"]
        for key in ("rule", "severity", "title", "why", "fix"):
            assert finding[key], f"{finding['rule']} is missing {key}"
    severities = [scoring_severity(f) for f in detail["findings"]]
    assert severities == sorted(severities)
    assert all(sym["line"] is not None for sym in detail["symbols"])


def scoring_severity(finding):
    from app.ai import fixes

    return fixes.SEVERITY_ORDER.get(finding["severity"], 9)


def test_the_drawer_reports_nothing_for_an_unknown_file(bridge, scored):
    _project, run = scored
    detail = unwrap(bridge.score_file_detail({"analysis_id": run["id"], "file": "not/here.py"}))
    assert detail["findings"] == []
    assert detail["symbols"] == []
    assert detail["findings_truncated"] is False


def test_the_trend_tracks_successive_runs(bridge, scored):
    project, first = scored
    second = _run_analysis(bridge, project["id"])

    trend = unwrap(bridge.score_trend({"project_id": project["id"]}))
    points = trend["points"]
    assert len(points) >= 2
    assert points[0]["at"] <= points[-1]["at"]
    assert {first["id"], second["id"]} <= {point["analysis_id"] for point in points}
    assert set(trend["deltas"]["categories"]) == set(scoring.CATEGORY_ORDER)
    assert trend["deltas"]["overall"] == points[-1]["overall"] - points[0]["overall"]


def test_score_endpoints_reject_unknown_analyses(bridge):
    assert bridge.score_card({"analysis_id": "nope"})["ok"] is False
    assert bridge.score_files({"analysis_id": "nope"})["ok"] is False
    assert bridge.score_file_detail({"analysis_id": "nope", "file": "a.py"})["ok"] is False
    assert bridge.score_recompute({"analysis_id": "nope"})["ok"] is False
