"""Checks the native UI only uses translation keys that exist, and fills them.

Run with no arguments; a non-zero exit means the interface would show a raw key
or a literal ``{placeholder}`` to the user.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.ui.i18n import _load  # noqa: E402

UI_DIR = os.path.join(ROOT, "app", "ui")
# A t('key', name=...) call. The argument list may itself contain calls such as
# round(x), so one level of nested brackets has to be allowed - without it the
# whole call goes unmatched and its placeholders are never checked.
CALL = re.compile(r"\bt\(\s*[\"']([a-zA-Z0-9_.]+)[\"']((?:\s*,(?:[^()]|\([^()]*\))*)?)\)")
# Keys held in tuples and constants, referenced indirectly.
CONST = re.compile(
    r"[\"']((?:nav|common|project|analysis|settings|about|a11y|palette|score|hotspots|trends|diagram)"
    r"\.[a-zA-Z0-9_.]+)[\"']"
)
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def python_files() -> list[str]:
    found = []
    for folder, _, files in os.walk(UI_DIR):
        found += [os.path.join(folder, f) for f in files if f.endswith(".py")]
    return found


def main() -> int:
    english = _load("en")
    missing: dict[str, set[str]] = {}
    unfilled: dict[str, set[str]] = {}

    for path in python_files():
        relative = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        for key, args in CALL.findall(source):
            if key not in english:
                missing.setdefault(relative, set()).add(key)
                continue
            needed = set(PLACEHOLDER.findall(english[key]))
            supplied = set(re.findall(r"(\w+)\s*=", args))
            for name in needed - supplied:
                unfilled.setdefault(relative, set()).add(f"{key} -> {{{name}}}")

        for key in CONST.findall(source):
            if key not in english:
                missing.setdefault(relative, set()).add(key)

    for title, table in (("missing key", missing), ("unfilled placeholder", unfilled)):
        for path, keys in sorted(table.items()):
            print(f"{title}: {path}: {sorted(keys)}")

    total = sum(len(v) for v in missing.values()) + sum(len(v) for v in unfilled.values())
    print("all i18n keys resolve" if not total else f"PROBLEMS: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
