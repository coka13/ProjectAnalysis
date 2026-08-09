"""Tests for the deterministic fix engine.

The contract that matters is safety: proposals must never be applied without an
explicit confirmation, must never touch a file that changed after the proposal
was made, and must never write outside the project root.
"""

from __future__ import annotations

import warnings

import pytest

from app.ai import fixes


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "svc.py").write_text(
        "import yaml\n"
        "\n"
        "def load(raw, cfg):\n"
        "    if cfg == None:\n"
        "        cfg = {}\n"
        "    try:\n"
        "        return yaml.load(raw)\n"
        "    except:\n"
        "        return cfg\n",
        encoding="utf-8",
    )
    return tmp_path


def _by_rule(payload, rule_id):
    return [p for p in payload["proposals"] if p["rule"] == rule_id]


# --------------------------------------------------------------------------- #
# Proposal generation
# --------------------------------------------------------------------------- #
def test_missing_directory_is_reported_not_raised(tmp_path):
    payload = fixes.propose(tmp_path / "nope")
    assert payload["available"] is False
    assert payload["proposals"] == []


def test_finds_the_three_python_defects(project):
    payload = fixes.propose(project)
    rules = {p["rule"] for p in payload["proposals"]}
    assert {"bare-except", "none-identity", "yaml-unsafe-load"} <= rules


def test_every_proposal_carries_a_full_explanation(project):
    for proposal in fixes.propose(project)["proposals"]:
        for key in ("problem", "root_cause", "impact", "severity", "effort", "confidence"):
            assert proposal[key] not in (None, ""), f"{proposal['rule']} is missing {key}"
        assert 0 < proposal["confidence"] <= 1


def test_auto_fixable_proposals_carry_a_unified_diff(project):
    for proposal in fixes.propose(project)["proposals"]:
        if not proposal["auto_fixable"]:
            continue
        diff = proposal["diff"]
        assert diff.startswith("--- a/"), diff[:80]
        assert "+++ b/" in diff
        assert "@@" in diff


def test_advisory_rules_report_but_offer_no_diff(tmp_path):
    (tmp_path / "run.py").write_text("import subprocess\nsubprocess.run(cmd, shell=True)\n", encoding="utf-8")
    proposal = _by_rule(fixes.propose(tmp_path), "subprocess-shell")[0]
    assert proposal["auto_fixable"] is False
    assert proposal["diff"] == ""
    assert proposal["lines"] == [2]


def test_severities_are_ordered_worst_first(project):
    order = [fixes._SEVERITY_ORDER[p["severity"]] for p in fixes.propose(project)["proposals"]]
    assert order == sorted(order)


def test_rule_filter_narrows_the_scan(project):
    payload = fixes.propose(project, rules=["bare-except"])
    assert {p["rule"] for p in payload["proposals"]} == {"bare-except"}


def test_clean_file_produces_nothing(tmp_path):
    (tmp_path / "clean.py").write_text('"""Fine."""\n\n\ndef f(x):\n    return x is None\n', encoding="utf-8")
    assert fixes.propose(tmp_path)["proposals"] == []


# --------------------------------------------------------------------------- #
# Individual transforms
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("rule_id", "before", "after"),
    [
        ("bare-except", "try:\n    x()\nexcept:\n    pass\n", "try:\n    x()\nexcept Exception:\n    pass\n"),
        ("none-identity", "if a == None:\n", "if a is None:\n"),
        ("none-identity", "if a != None:\n", "if a is not None:\n"),
        ("yaml-unsafe-load", "yaml.load(f)\n", "yaml.safe_load(f)\n"),
        ("trailing-whitespace", "a = 1   \nb = 2\n", "a = 1\nb = 2\n"),
        ("debugger-statement", "f();\n  debugger;\ng();\n", "f();\ng();\n"),
        ("py2-except-syntax", "except ValueError, exc:\n", "except ValueError as exc:\n"),
        ("py2-except-syntax", "except (A, B), exc:\n", "except (A, B) as exc:\n"),
        ("deprecated-unittest-alias", "self.assertEquals(a, b)\n", "self.assertEqual(a, b)\n"),
        ("deprecated-unittest-alias", "self.failUnless(x)\n", "self.assertTrue(x)\n"),
        ("invalid-escape-sequence", 're.compile("\\d+")\n', 're.compile(r"\\d+")\n'),
        ("typeof-loose-equality", "if (typeof x == 'string') {}\n", "if (typeof x === 'string') {}\n"),
        ("typeof-loose-equality", "if (typeof x != 'n') {}\n", "if (typeof x !== 'n') {}\n"),
        ("js-wrapper-constructor", "a = new Array();\n", "a = [];\n"),
        ("js-wrapper-constructor", "a = new Object();\n", "a = {};\n"),
        ("trailing-blank-lines", "a = 1\n\n\n", "a = 1\n"),
    ],
)
def test_transform_output(rule_id, before, after):
    updated, lines = fixes.RULES_BY_ID[rule_id].transform(before)
    assert updated == after
    assert lines


@pytest.mark.parametrize(
    ("rule_id", "source"),
    [
        # Adding `r` to a literal that also contains a real escape would change
        # the string's value, so the rule has to leave it alone.
        ("invalid-escape-sequence", 'x = "line\\nbreak \\d"\n'),
        ("invalid-escape-sequence", 'x = r"\\d+"\n'),
        ("invalid-escape-sequence", 'x = 1  # a note about "\\d"\n'),
        # `new Array(3)` is a three-element array, not `[3]`.
        ("js-wrapper-constructor", "a = new Array(3);\n"),
        ("typeof-loose-equality", "if (a == 'string') {}\n"),
        ("deprecated-unittest-alias", "self.assertEqual(a, b)\n"),
        ("py2-except-syntax", "except ValueError as exc:\n"),
    ],
)
def test_transform_leaves_safe_code_untouched(rule_id, source):
    updated, lines = fixes.RULES_BY_ID[rule_id].transform(source)
    assert updated == source
    assert lines == []


def test_every_transform_is_idempotent():
    """Applying the catalogue twice must be a no-op.

    A transform that keeps finding work produces an endless stream of proposals
    and, worse, a diff that never converges.
    """
    samples = [
        "try:\n    x()\nexcept:\n    pass\n",
        "except ValueError, exc:\n",
        "self.assertEquals(a, b)\n",
        're.compile("\\d+")\n',
        "if (typeof x == 'string') { var a = new Array(); }\n",
        "a = 1   \n\n\n",
        "if a == None:\n    yaml.load(f)\n",
    ]
    for rule in fixes.RULES:
        if not rule.auto_fixable:
            continue
        for sample in samples:
            once, _ = rule.transform(sample)
            twice, lines = rule.transform(once)
            assert twice == once, f"{rule.id} is not idempotent on {sample!r}"
            assert lines == [], f"{rule.id} keeps reporting work on {sample!r}"


def test_invalid_escape_rewrite_preserves_the_string_value():
    """The promoted literal must evaluate to exactly what it did before.

    The original is also asserted to be the thing the rule claims it is: a
    literal Python already warns about. Without that, the rule could be firing
    on perfectly good code and this test would still pass.
    """
    source = 'PATTERN = "\\d+\\s*\\w"\n'
    updated, _ = fixes.RULES_BY_ID["invalid-escape-sequence"].transform(source)
    assert updated != source

    scope_before: dict = {}
    scope_after: dict = {}
    with warnings.catch_warnings(record=True) as before_warnings:
        warnings.simplefilter("always")
        exec(compile(source, "<before>", "exec"), scope_before)  # noqa: S102 - fixed test input
    with warnings.catch_warnings(record=True) as after_warnings:
        warnings.simplefilter("always")
        exec(compile(updated, "<after>", "exec"), scope_after)  # noqa: S102 - fixed test input

    assert any("escape sequence" in str(w.message) for w in before_warnings)
    assert not [w for w in after_warnings if "escape sequence" in str(w.message)]
    assert scope_before["PATTERN"] == scope_after["PATTERN"]


def test_none_identity_leaves_other_comparisons_alone():
    source = "if a != b:\n    x = a == c\n"
    assert fixes.RULES_BY_ID["none-identity"].transform(source)[0] == source


def test_bare_except_ignores_a_typed_handler():
    source = "except ValueError:\n"
    assert fixes.RULES_BY_ID["bare-except"].transform(source)[0] == source


def test_final_newline_is_added_once():
    once, _ = fixes.RULES_BY_ID["missing-final-newline"].transform("a = 1")
    assert once == "a = 1\n"
    twice, lines = fixes.RULES_BY_ID["missing-final-newline"].transform(once)
    assert twice == once and lines == []


def test_crlf_line_endings_survive_a_fix(tmp_path):
    target = tmp_path / "win.py"
    target.write_bytes(b"if a == None:\r\n    pass\r\n")
    payload = fixes.propose(tmp_path)
    proposal = _by_rule(payload, "none-identity")[0]
    fixes.apply(tmp_path, [proposal], confirm=True)
    assert target.read_bytes() == b"if a is None:\r\n    pass\r\n"


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #
def test_apply_requires_confirmation(project):
    proposals = fixes.propose(project)["proposals"]
    with pytest.raises(fixes.FixError):
        fixes.apply(project, proposals, confirm=False)


def test_proposing_never_writes(project):
    before = (project / "app" / "svc.py").read_bytes()
    fixes.propose(project)
    assert (project / "app" / "svc.py").read_bytes() == before


def test_apply_rewrites_the_file(project):
    proposals = [p for p in fixes.propose(project)["proposals"] if p["auto_fixable"]]
    result = fixes.apply(project, proposals, confirm=True)
    assert result["failed"] == []
    assert result["applied_files"] == 1
    text = (project / "app" / "svc.py").read_text(encoding="utf-8")
    assert "except Exception:" in text
    assert "cfg is None" in text
    assert "yaml.safe_load(" in text
    assert "except:" not in text


def test_applying_twice_is_a_no_op(project):
    proposals = [p for p in fixes.propose(project)["proposals"] if p["auto_fixable"]]
    fixes.apply(project, proposals, confirm=True)
    assert fixes.propose(project)["proposals"] == []


def test_stale_proposal_is_rejected(project):
    proposals = [p for p in fixes.propose(project)["proposals"] if p["auto_fixable"]]
    (project / "app" / "svc.py").write_text("# rewritten by the user\n", encoding="utf-8")
    result = fixes.apply(project, proposals, confirm=True)
    assert result["applied_files"] == 0
    assert "changed since" in result["failed"][0]["error"]
    assert (project / "app" / "svc.py").read_text(encoding="utf-8") == "# rewritten by the user\n"


def test_missing_file_fails_that_entry_only(project):
    proposals = [p for p in fixes.propose(project)["proposals"] if p["auto_fixable"]]
    (project / "extra.py").write_text("x = 1   \n", encoding="utf-8")
    extra = _by_rule(fixes.propose(project, include_cosmetic=True), "trailing-whitespace")
    (project / "extra.py").unlink()
    result = fixes.apply(project, proposals + extra, confirm=True)
    assert result["applied_files"] == 1
    assert [f["file"] for f in result["failed"]] == ["extra.py"]


@pytest.mark.parametrize("escape", ["../outside.py", "app/../../outside.py", "/etc/passwd", "C:/Windows/win.ini"])
def test_paths_outside_the_project_are_refused(project, tmp_path_factory, escape):
    outside = project.parent / "outside.py"
    outside.write_text("x = 1   \n", encoding="utf-8")
    result = fixes.apply(project, [{"file": escape, "rule": "trailing-whitespace", "digest": ""}], confirm=True)
    assert result["applied_files"] == 0
    assert result["failed"]
    assert outside.read_text(encoding="utf-8") == "x = 1   \n"


def test_unknown_rule_is_refused(project):
    target = project / "app" / "svc.py"
    result = fixes.apply(
        project,
        [{"file": "app/svc.py", "rule": "rm -rf", "digest": fixes.digest(fixes.read_source(target))}],
        confirm=True,
    )
    assert result["applied_files"] == 0
    assert result["failed"]


def test_advisory_rule_cannot_be_applied(project):
    proposal = {
        "file": "app/svc.py",
        "rule": "subprocess-shell",
        "digest": fixes.digest(fixes.read_source(project / "app" / "svc.py")),
    }
    result = fixes.apply(project, [proposal], confirm=True)
    assert result["applied_files"] == 0
    assert "cannot be applied automatically" in result["failed"][0]["error"]


# --------------------------------------------------------------------------- #
# Preview
# --------------------------------------------------------------------------- #
def test_preview_reflects_the_file_on_disk(project):
    payload = fixes.preview(project, "app/svc.py", ["bare-except"])
    assert payload["changed"] is True
    assert "+    except Exception:" in payload["diff"]
    assert payload["digest"] == fixes.digest(fixes.read_source(project / "app" / "svc.py"))


# --------------------------------------------------------------------------- #
# Depth: formatting noise is held back, real defects are found
# --------------------------------------------------------------------------- #
def test_formatting_rules_are_excluded_by_default(tmp_path):
    (tmp_path / "messy.py").write_text("x = 1   \ny = 2", encoding="utf-8")
    assert fixes.propose(tmp_path)["proposals"] == []
    opted_in = {p["rule"] for p in fixes.propose(tmp_path, include_cosmetic=True)["proposals"]}
    assert {"trailing-whitespace", "missing-final-newline"} <= opted_in


def test_formatting_rules_are_still_available_by_name(tmp_path):
    (tmp_path / "messy.py").write_text("x = 1   \n", encoding="utf-8")
    payload = fixes.propose(tmp_path, rules=["trailing-whitespace"])
    assert {p["rule"] for p in payload["proposals"]} == {"trailing-whitespace"}


@pytest.mark.parametrize(
    ("rule_id", "name", "body"),
    [
        ("mutable-default-arg", "a.py", "def f(items=[]):\n    return items\n"),
        ("except-pass", "b.py", "try:\n    go()\nexcept ValueError:\n    pass\n"),
        ("star-import", "c.py", "from os.path import *\n"),
        ("dynamic-eval", "d.py", "value = eval(raw)\n"),
        ("unsafe-deserialisation", "e.py", "import pickle\nobj = pickle.loads(blob)\n"),
        ("os-command", "f.py", "import os\nos.system(cmd)\n"),
        ("weak-hash", "g.py", "import hashlib\nh = hashlib.md5(data)\n"),
        ("naive-utcnow", "h.py", "from datetime import datetime\nnow = datetime.utcnow()\n"),
        ("lambda-assignment", "i.py", "double = lambda n: n * 2\n"),
        ("unclosed-file", "j.py", "handle = open('data.txt')\n"),
        ("request-without-timeout", "k.py", "import requests\nr = requests.get(url)\n"),
        ("hardcoded-secret", "l.py", 'API_KEY = "sk-9f2b71c4d8e0a35b"\n'),
        ("sql-string-building", "m.py", 'cur.execute(f"select * from users where id = {uid}")\n'),
        ("merge-conflict-marker", "n.py", "<<<<<<< HEAD\nx = 1\n>>>>>>> other\n"),
        ("loose-equality", "o.js", "if (a == b) { go(); }\n"),
        ("var-declaration", "p.js", "var total = 0;\n"),
        ("document-write", "q.js", "document.write(html);\n"),
        ("timer-string-body", "r.js", "setTimeout('tick()', 100);\n"),
    ],
)
def test_substantive_defects_are_detected(tmp_path, rule_id, name, body):
    (tmp_path / name).write_text(body, encoding="utf-8")
    found = _by_rule(fixes.propose(tmp_path), rule_id)
    assert found, f"{rule_id} did not fire on {name}"
    assert found[0]["lines"]


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("ok1.py", "def f(items=None):\n    return items or []\n"),
        ("ok2.py", "import requests\nr = requests.get(url, timeout=5)\n"),
        ("ok3.py", 'PASSWORD = os.environ["PASSWORD"]\n'),
        ("ok4.py", 'API_KEY = "your-key-here"\n'),
        ("ok5.js", "if (a === b) { go(); }\n"),
        ("ok6.js", "if (a !== b) { go(); }\n"),
        ("ok7.js", "// legacy code used var here\nconst total = 0;\n"),
    ],
)
def test_correct_code_is_not_flagged(tmp_path, name, body):
    (tmp_path / name).write_text(body, encoding="utf-8")
    assert fixes.propose(tmp_path)["proposals"] == [], body


def test_requests_timeout_is_seen_across_line_breaks(tmp_path):
    (tmp_path / "http.py").write_text(
        "import requests\nr = requests.post(\n    url,\n    json=body,\n    timeout=10,\n)\n", encoding="utf-8"
    )
    assert _by_rule(fixes.propose(tmp_path), "request-without-timeout") == []


def test_findings_without_a_patch_still_carry_a_procedure(tmp_path):
    (tmp_path / "run.py").write_text("import os\nos.system(cmd)\n", encoding="utf-8")
    proposal = _by_rule(fixes.propose(tmp_path), "os-command")[0]
    assert proposal["auto_fixable"] is False
    assert len(proposal["steps"]) >= 2
    assert all(step.strip() for step in proposal["steps"])


# --------------------------------------------------------------------------- #
# Structural proposals - the work no per-line rule can see
# --------------------------------------------------------------------------- #
METRICS = {
    "cycles": [{"modules": ["app.a", "app.b"], "length": 2}],
    "god_classes": [{"name": "Manager", "file": "app/mgr.py", "methods": 44, "properties": 20, "dependencies": 12}],
    "layering_violations": [
        {"from": "app.repo", "from_layer": "data", "to": "app.view", "to_layer": "ui", "kind": "uses", "file": "app/repo.py"}
    ],
    "signals": {
        "complexity": {"offenders": [{"name": "handle", "file": "app/h.py", "line": 4, "complexity": 27}]},
        "untested_modules": ["app.a", "app.b", "app.c"],
        "largest_files": [{"file": "app/huge.py", "loc": 1800}],
        "symbol_docs": {"undocumented": [{"name": f"f{i}", "file": "app/a.py", "kind": "function"} for i in range(9)]},
    },
}


def test_structural_proposals_cover_every_kind_of_cross_file_work():
    rules = {p["rule"] for p in fixes.structural_proposals(METRICS)}
    assert rules == {
        "dependency-cycle",
        "god-class",
        "layering-violation",
        "complex-function",
        "untested-module",
        "oversized-file",
        "undocumented-api",
    }


def test_structural_proposals_are_actionable_and_never_patched():
    for proposal in fixes.structural_proposals(METRICS):
        assert proposal["kind"] == "structural"
        assert proposal["auto_fixable"] is False
        assert proposal["diff"] == ""
        assert len(proposal["steps"]) >= 2
        assert proposal["files"]
        for key in ("title", "problem", "root_cause", "impact"):
            assert proposal[key]


def test_structural_work_leads_the_list(project):
    payload = fixes.propose(project, metrics=METRICS)
    kinds = [p["kind"] for p in payload["proposals"]]
    assert kinds[0] == "structural"
    assert "code" in kinds
    assert payload["structural_count"] == kinds.count("structural")


def test_no_metrics_means_no_structural_noise(project):
    assert fixes.structural_proposals(None) == []
    assert all(p["kind"] == "code" for p in fixes.propose(project)["proposals"])


# --------------------------------------------------------------------------- #
# AI-assisted mode
# --------------------------------------------------------------------------- #
class FakeProvider:
    """Returns a canned JSON reply, recording what it was asked."""

    def __init__(self, replacement, confidence=0.8):
        self.replacement = replacement
        self.confidence = confidence
        self.prompts = []

    async def chat(self, messages, json_mode=False):
        self.prompts.append(messages)
        import json

        return json.dumps(
            {
                "diagnosis": "shell=True lets the command string be interpreted",
                "explanation": "passes an argument list instead",
                "replacement": self.replacement,
                "confidence": self.confidence,
                "risk": "check the caller still quotes nothing",
            }
        )


@pytest.fixture()
def shell_project(tmp_path):
    (tmp_path / "run.py").write_text("import subprocess\n\n\ndef go(cmd):\n    subprocess.run(cmd, shell=True)\n", encoding="utf-8")
    return tmp_path


async def test_ai_mode_patches_what_no_rule_can_fix(shell_project):
    static = fixes.propose(shell_project)
    assert not any(p["auto_fixable"] for p in static["proposals"])
    provider = FakeProvider("import subprocess\n\n\ndef go(cmd):\n    subprocess.run(cmd)\n")

    result = await fixes.enrich_with_ai(shell_project, static["proposals"], provider)
    patched = [p for p in result["proposals"] if p["source"] == "ai"]
    assert result["ai_patched"] == 1
    assert patched[0]["auto_fixable"] is True
    # The diff is built locally, so it always describes the real change.
    assert "-    subprocess.run(cmd, shell=True)" in patched[0]["diff"]
    assert patched[0]["ai_diagnosis"] and patched[0]["ai_risk"]
    assert patched[0]["ai_confidence"] == 0.8
    # Nothing was written by proposing.
    assert "shell=True" in (shell_project / "run.py").read_text(encoding="utf-8")


async def test_ai_patch_is_applied_by_reference_not_by_content(shell_project):
    static = fixes.propose(shell_project)
    provider = FakeProvider("import subprocess\n\n\ndef go(cmd):\n    subprocess.run(cmd)\n")
    result = await fixes.enrich_with_ai(shell_project, static["proposals"], provider)
    patched = next(p for p in result["proposals"] if p["source"] == "ai")

    applied = fixes.apply(
        shell_project,
        [{"file": patched["file"], "rule": patched["rule"], "digest": patched["digest"], "ai_fix_id": patched["ai_fix_id"]}],
        confirm=True,
    )
    assert applied["failed"] == []
    assert "shell=True" not in (shell_project / "run.py").read_text(encoding="utf-8")


async def test_ai_patch_still_requires_confirmation(shell_project):
    static = fixes.propose(shell_project)
    provider = FakeProvider("import subprocess\n\n\ndef go(cmd):\n    subprocess.run(cmd)\n")
    result = await fixes.enrich_with_ai(shell_project, static["proposals"], provider)
    patched = next(p for p in result["proposals"] if p["source"] == "ai")
    with pytest.raises(fixes.FixError):
        fixes.apply(shell_project, [patched], confirm=False)


async def test_ai_patch_is_rejected_once_the_file_moves_on(shell_project):
    static = fixes.propose(shell_project)
    provider = FakeProvider("import subprocess\n\n\ndef go(cmd):\n    subprocess.run(cmd)\n")
    result = await fixes.enrich_with_ai(shell_project, static["proposals"], provider)
    patched = next(p for p in result["proposals"] if p["source"] == "ai")
    (shell_project / "run.py").write_text("# rewritten by the user\n", encoding="utf-8")

    applied = fixes.apply(
        shell_project,
        [{"file": patched["file"], "rule": patched["rule"], "digest": patched["digest"], "ai_fix_id": patched["ai_fix_id"]}],
        confirm=True,
    )
    assert applied["applied_files"] == 0
    assert (shell_project / "run.py").read_text(encoding="utf-8") == "# rewritten by the user\n"


@pytest.mark.parametrize("reply", ["", "   \n", "x = 1\n" * 500])
async def test_implausible_model_output_is_discarded(shell_project, reply):
    static = fixes.propose(shell_project)
    result = await fixes.enrich_with_ai(shell_project, static["proposals"], FakeProvider(reply))
    assert result["ai_patched"] == 0
    assert all(p["source"] == "static" for p in result["proposals"])


async def test_ai_mode_never_touches_the_mechanical_rules(project):
    """The deterministic path must be unchanged when a provider is present."""
    static = fixes.propose(project)
    result = await fixes.enrich_with_ai(project, static["proposals"], FakeProvider("whatever\n"))
    assert [p["rule"] for p in result["proposals"]] == [p["rule"] for p in static["proposals"]]
    assert result["ai_attempted"] == 0


def test_preview_refuses_to_escape_the_root(project):
    with pytest.raises(fixes.FixError):
        fixes.preview(project, "../outside.py", ["trailing-whitespace"])
