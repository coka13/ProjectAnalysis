"""Bilingual insight fallbacks and natural-language diagram interpretation."""

from __future__ import annotations

import pytest

from app.ai import insights


@pytest.mark.parametrize("language", ["en", "he"])
async def test_explain_diagram_without_provider(graph, language):
    from app.diagrams.base import DiagramFilters
    from app.diagrams.registry import generate

    diagram = generate("architecture", graph, DiagramFilters()).to_dict()
    result = await insights.explain_diagram(graph, diagram, language, provider=None)
    assert result["source"] == "static"
    assert result["purpose"]
    if language == "he":
        assert any("\u0590" <= ch <= "\u05ff" for ch in result["purpose"])


@pytest.mark.parametrize("language", ["en", "he"])
def test_every_risk_is_self_explanatory(language):
    """A severity word alone is not actionable, so each risk must justify itself."""
    metrics = {
        "cycles": [{"modules": ["a", "b"], "length": 2}],
        "god_classes": [{"name": "Manager", "file": "a.py", "methods": 40, "properties": 12, "dependencies": 9}],
        "layering_violations": [
            {"from": "a.Repo", "from_layer": "data", "to": "b.View", "to_layer": "ui", "kind": "uses", "file": "a.py"}
        ],
        "hubs": [{"name": "Core", "degree": 22, "file": "c.py"}],
    }
    risks = insights._risks(metrics, language)
    assert len(risks) == 4
    for risk in risks:
        for key in ("severity", "severity_label", "title", "issue", "why", "remediation", "impact", "effort"):
            assert risk[key], f"{risk['title']!r} is missing {key}"
        # The old shape carried a one-word `impact` tag and nothing else, which
        # rendered as a bare "(high)" in the UI.
        assert risk["severity"] != risk["severity_label"] or language == "en"
        assert len(risk["why"]) > 40
        assert risk["evidence"]
        if language == "he":
            assert any("\u0590" <= ch <= "\u05ff" for ch in risk["why"])


def test_clean_metrics_still_produce_an_explained_risk():
    risks = insights._risks({}, "en")
    assert len(risks) == 1
    assert risks[0]["remediation"]
    assert risks[0]["evidence"] == []


@pytest.mark.parametrize("language", ["en", "he"])
async def test_review_without_provider(graph, analysis, language):
    _, report, _ = analysis
    result = await insights.review_architecture(graph, report["metrics"], language, provider=None)
    assert 0 <= result["score"] <= 100
    assert result["strengths"] or result["issues"]


async def test_refactoring_without_provider(graph, analysis):
    _, report, _ = analysis
    result = await insights.refactoring_suggestions(graph, report["metrics"], "en", provider=None)
    assert "suggestions" in result


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Show only the authentication architecture", "architecture"),
        ("Give me the database ER diagram", "database"),
        ("Show the sequence of a login request", "sequence"),
        ("Show module dependencies", "dependency"),
        ("הצג תרשים מחלקות", "class"),
    ],
)
async def test_natural_language_interpretation(graph, prompt, expected):
    spec = await insights.interpret_request(graph, prompt, "en", provider=None)
    assert spec["kind"] == expected
    assert spec["filters"]["detail"] in {"executive", "standard", "detailed"}


async def test_executive_hint_reduces_detail(graph):
    spec = await insights.interpret_request(graph, "Create a simplified executive architecture view", "en", provider=None)
    assert spec["filters"]["detail"] == "executive"


def test_small_class_diagram_does_not_use_size_as_focus(graph):
    """Adjectives like 'small' must shrink the budget, not filter class names."""
    import asyncio

    from app.diagrams.base import DiagramFilters
    from app.diagrams.registry import generate

    spec = asyncio.run(insights.interpret_request(graph, "create small class diagram", "en", provider=None))
    assert spec["kind"] == "class"
    assert not spec["filters"].get("focus")
    assert spec["filters"]["max_nodes"] <= 12
    result = generate(spec["kind"], graph, DiagramFilters.from_payload(spec["filters"]))
    assert "classDiagram" in result.mermaid


async def test_translate_is_a_noop_without_provider():
    result = await insights.translate("Order service", "he", provider=None)
    assert result["translated"] is False
    assert result["text"] == "Order service"
