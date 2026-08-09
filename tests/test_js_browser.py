"""Runs the browser-side suite through a real webview.

Driven as a subprocess because ``webview.start()`` needs the main thread and can
only run once per process. Skips rather than fails when no webview is available,
so a headless CI box does not report a false negative.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tools" / "js_harness.py"


@pytest.fixture(scope="module")
def js_results() -> list[dict]:
    proc = subprocess.run(
        [sys.executable, str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=ROOT,
        check=False,
    )
    tail = (proc.stdout or "").strip().splitlines()
    if not tail:
        pytest.skip(f"no webview available for browser tests: {proc.stderr.strip()[:400]}")
    try:
        payload = json.loads(tail[-1])
    except ValueError:
        pytest.skip(f"harness produced no JSON report: {tail[-1][:400]}")
    if not payload.get("ok") and not payload.get("results"):
        pytest.skip(f"harness could not run: {payload.get('error', '')[:400]}")
    return payload["results"]


def test_browser_suite_ran(js_results: list[dict]) -> None:
    assert js_results, "the browser suite reported no tests at all"


def test_browser_suite_passes(js_results: list[dict]) -> None:
    failures = [f"{r['suite']} :: {r['name']}\n{r['error']}" for r in js_results if not r["passed"]]
    assert not failures, "browser tests failed:\n\n" + "\n\n".join(failures)
