"""Runs the static UI audits as part of the test suite.

They guard the defect class where the JS toggles a class or writes a preference
that no CSS rule consumes - invisible in unit tests, very visible to users.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"aai_audit_{name}", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ui_behaviours_have_matching_styles():
    audit = _load("audit_ui")
    assert [label for label, ok in audit.CHECKS if not ok] == []


def test_every_used_translation_key_exists_in_both_bundles():
    audit = _load("audit_i18n")
    used = audit.used_keys()
    en = audit.bundle_keys(audit.WEB / "i18n" / "en.js")
    he = audit.bundle_keys(audit.WEB / "i18n" / "he.js")
    assert sorted(used - en) == []
    assert sorted(en ^ he) == []
