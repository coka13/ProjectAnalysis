"""The quality scanner: rule findings, debt markers and the per-file signals it derives."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engine import quality
from app.graph.model import KnowledgeGraph
from app.ingest.walker import SourceFile


def _source(tmp_path: Path, relative: str, body: str) -> SourceFile:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    language = {".py": "python", ".ts": "typescript", ".md": "markdown"}.get(path.suffix, "python")
    return SourceFile(path=path, relative_path=relative, language=language, size=path.stat().st_size)


def _scan(tmp_path: Path, files: dict[str, str]) -> dict:
    sources = [_source(tmp_path, name, body) for name, body in files.items()]
    return quality.scan(sources, KnowledgeGraph())


@pytest.mark.parametrize(
    "relative",
    [
        "tests/test_orders.py",
        "src/__tests__/orders.spec.ts",
        "app/orders_test.go",
        "src/OrderServiceTests.cs",
        "e2e/checkout.spec.ts",
    ],
)
def test_test_files_are_recognised(relative):
    assert quality._is_test_file(relative)


@pytest.mark.parametrize("relative", ["src/orders.py", "app/latest.py", "lib/contest.ts", "src/protest.py"])
def test_ordinary_files_are_not_mistaken_for_tests(relative):
    assert not quality._is_test_file(relative)


def test_line_stats_separate_code_comments_and_blanks():
    text = '"""Module doc."""\n\n# a comment\nvalue = 1\n\n\ndef run():\n    return value\n'
    code, comments, blank = quality._line_stats(text, "python")
    assert (code, comments, blank) == (3, 2, 3)


def test_block_comments_are_counted_until_they_close():
    text = "/*\n still inside\n*/\nconst a = 1;\n"
    code, comments, _blank = quality._line_stats(text, "typescript")
    assert comments == 3
    assert code == 1


def test_module_documentation_is_detected_per_language():
    assert quality._has_module_doc('"""Docs."""\nimport os\n', "python")
    assert quality._has_module_doc("# leading comment\n\n'''Docs.'''\n", "python")
    assert not quality._has_module_doc("import os\n", "python")


def test_findings_carry_the_advice_needed_to_act_on_them(tmp_path):
    result = _scan(tmp_path, {"app/settings.py": 'API_KEY = "AKIAIOSFODNN7EXAMPLE"\npassword = "hunter2hunter2"\n'})

    assert result["findings"], "a hardcoded credential should be reported"
    for finding in result["findings"]:
        assert finding["file"] == "app/settings.py"
        assert finding["line"] >= 1
        assert finding["title"] and finding["why"] and finding["fix"]
        assert finding["severity"] in quality.SEVERITY_ORDER
        assert finding["snippet"]


def test_security_rules_are_not_run_against_test_fixtures(tmp_path):
    body = 'API_KEY = "AKIAIOSFODNN7EXAMPLE"\npassword = "hunter2hunter2"\n'
    production = _scan(tmp_path, {"app/settings.py": body})
    fixture = _scan(tmp_path, {"tests/test_settings.py": body})

    assert any(f["category"] == "security" for f in production["findings"])
    assert not any(f["category"] == "security" for f in fixture["findings"])


def test_debt_markers_are_collected_with_their_note(tmp_path):
    result = _scan(tmp_path, {"app/jobs.py": "# TODO: retry on failure\nx = 1\n# FIXME broken since v2\n"})

    markers = result["debt_markers"]
    assert [m["marker"] for m in markers] == ["TODO", "FIXME"]
    assert markers[0]["note"] == "retry on failure"
    assert markers[0]["line"] == 1
    assert result["marker_counts"] == {"TODO": 1, "FIXME": 1}


def test_words_that_merely_contain_a_marker_are_ignored(tmp_path):
    result = _scan(tmp_path, {"app/notes.py": "mastodon = 1\nfixtures = 2\n"})
    assert result["debt_markers"] == []


def test_totals_split_test_code_from_production_code(tmp_path):
    result = _scan(
        tmp_path,
        {
            "app/orders.py": '"""Orders."""\nvalue = 1\n',
            "tests/test_orders.py": "def test_it():\n    assert value == 1\n",
        },
    )
    totals = result["totals"]

    assert totals["files"] == 2
    assert totals["source_files"] == 1
    assert totals["test_files"] == 1
    assert totals["documented_modules"] == 1
    assert totals["assertions"] >= 1


def test_project_documentation_and_infrastructure_are_noticed(tmp_path):
    result = _scan(
        tmp_path,
        {
            "README.md": "# Project\n\nSome words about it.\n",
            "LICENSE": "MIT",
            "CONTRIBUTING.md": "# How to help\n",
            ".github/workflows/ci.yml": "on: [push]\n",
            "app/main.py": "x = 1\n",
        },
    )

    assert result["project_docs"]["readme"] is True
    assert result["project_docs"]["license"] is True
    assert result["project_docs"]["contributing"] is True
    assert result["project_docs"]["doc_pages"] >= 1
    assert result["infra"]["ci"] is True


def test_untested_modules_are_reported(tmp_path):
    result = _scan(
        tmp_path,
        {
            "billing/invoice.py": "x = 1\n",
            "shipping/label.py": "y = 2\n",
            "billing/tests/test_invoice.py": "def test_x():\n    assert True\n",
        },
    )

    assert "shipping" in result["untested_modules"]
    assert result["module_count"] == 2


@pytest.fixture()
def graph_with_file(tmp_path):
    from app.graph.model import Node, NodeKind

    body = "# TODO: split this up\n" + 'token = "AKIAIOSFODNN7EXAMPLE"\n' + "x = 1\n" * 50
    source = _source(tmp_path, "app/big.py", body)
    graph = KnowledgeGraph()
    graph.add_node(
        Node(id="file:app/big.py", kind=NodeKind.FILE, name="big.py", qualified_name="app/big.py", file="app/big.py")
    )
    return graph, source


def test_file_nodes_are_annotated_in_place(graph_with_file):
    graph, source = graph_with_file
    quality.scan([source], graph)

    attributes = graph.nodes["file:app/big.py"].attributes
    assert attributes["loc"] > 0
    assert attributes["is_test"] is False
    assert attributes["debt_markers"] == 1
    assert attributes["findings"] >= 1
    assert attributes["risk_findings"] >= 1


def test_the_scan_output_is_json_serialisable(tmp_path):
    import json

    result = _scan(tmp_path, {"app/main.py": '"""Docs."""\n# TODO: tidy\nx = 1\n'})
    assert json.loads(json.dumps(result))["totals"]["files"] == 1
