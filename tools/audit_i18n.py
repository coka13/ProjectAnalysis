"""Audit helper: report i18n keys used by the UI that are missing from a bundle.

Covers plain `t('a.b')` calls, `data-i18n*` attributes and the template-literal
call sites (``t(`status.${x}`)``) whose value sets are enumerated below.

Run with:  .venv\\Scripts\\python tools\\audit_i18n.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

KEY_CALL = re.compile(r"\bt\(\s*'([a-zA-Z0-9_.]+)'")
KEY_ATTR = re.compile(r"data-i18n(?:-placeholder|-title)?'?\s*:\s*'([a-zA-Z0-9_.]+)'")
KEY_HTML = re.compile(r'data-i18n(?:-placeholder|-title)?="([a-zA-Z0-9_.]+)"')
# Descriptor tables (the NAV list, tab definitions) store keys as bare literals
# and translate them later.
KEY_LITERAL = re.compile(r"\b(?:label|group|title|subtitle)\s*:\s*'([a-zA-Z0-9_]+\.[a-zA-Z0-9_.]+)'")
BUNDLE_KEY = re.compile(r'^\s*"([^"]+)"\s*:', re.MULTILINE)

# Keys assembled at runtime from a known, closed set of values.
DYNAMIC = {
    "status.": ["pending", "running", "succeeded", "failed", "cancelled"],
    "approval.": ["draft", "in_review", "approved", "rejected"],
    "diagram.": [
        "architecture", "component", "class", "dependency", "sequence",
        "dataflow", "database", "deployment", "state",
    ],
    "diagram.detail.": ["executive", "standard", "detailed"],
    "export.": ["mermaid", "plantuml", "markdown", "html", "drawio", "json"],
    "score.cat.": [
        "architecture", "security", "code_quality", "maintainability",
        "testing", "documentation", "performance", "technical_debt",
    ],
    "score.priority.": ["critical", "high", "medium", "low"],
    "score.effortLevel.": ["low", "medium", "high"],
}


def bundle_keys(path: Path) -> set[str]:
    return set(BUNDLE_KEY.findall(path.read_text(encoding="utf-8")))


def used_keys() -> set[str]:
    out: set[str] = set()
    for path in sorted((WEB / "js").glob("*.js")) + [WEB / "index.html"]:
        text = path.read_text(encoding="utf-8")
        out |= set(KEY_CALL.findall(text))
        out |= set(KEY_ATTR.findall(text))
        out |= set(KEY_HTML.findall(text))
        out |= set(KEY_LITERAL.findall(text))
    for prefix, values in DYNAMIC.items():
        out.update(f"{prefix}{value}" for value in values)
    return out


def main() -> int:
    en = bundle_keys(WEB / "i18n" / "en.js")
    he = bundle_keys(WEB / "i18n" / "he.js")
    used = used_keys()

    report = {
        "used_total": len(used),
        "en_total": len(en),
        "he_total": len(he),
        "missing_from_en": sorted(used - en),
        "in_en_missing_from_he": sorted(en - he),
        "in_he_not_in_en": sorted(he - en),
        "declared_but_never_used": sorted(en - used),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report["missing_from_en"] or report["in_en_missing_from_he"] else 0


if __name__ == "__main__":
    sys.exit(main())
