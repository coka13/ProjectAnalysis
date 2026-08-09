"""Runs the browser-side test suite inside a real webview.

The UI is deliberately built without a bundler, and this machine has neither node
nor network access, so there is no jsdom to fall back on. That turns out to be the
right thing anyway: the defects worth catching in the chart code only reproduce in
a real rendering engine, because they depend on SVG geometry (``getTotalLength``)
and resolved styles.

Prints the result as JSON on stdout and exits non-zero if anything failed.
``webview.start()`` must own the main thread and can only run once per process, so
the pytest wrapper drives this as a subprocess.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "web" / "tests" / "harness.html"
TIMEOUT_SECONDS = 60


class _Collector:
    """Receives the report the harness page pushes back over the bridge."""

    def __init__(self) -> None:
        self.payload: dict | None = None
        self.done = threading.Event()

    def report(self, payload: dict) -> bool:
        self.payload = payload
        self.done.set()
        return True


def main() -> int:
    if not HARNESS.exists():  # pragma: no cover - packaging guard
        print(json.dumps({"ok": False, "error": f"missing harness: {HARNESS}"}))
        return 2

    collector = _Collector()
    window = webview.create_window(
        "test harness",
        url=HARNESS.as_uri(),
        js_api=collector,
        width=1000,
        height=700,
        hidden=True,
    )

    def wait_then_close() -> None:
        if not collector.done.wait(TIMEOUT_SECONDS):
            # Fall back to reading the value directly: the bridge can be
            # unavailable if the page failed before pywebview finished wiring up.
            try:
                collector.payload = window.evaluate_js("window.__TEST_RESULTS__ || null")
            except Exception as exc:  # pragma: no cover - engine dependent
                collector.payload = {"ok": False, "error": f"evaluate_js failed: {exc}"}
        try:
            window.destroy()
        except Exception:  # pragma: no cover - already closing
            pass

    threading.Thread(target=wait_then_close, daemon=True).start()
    webview.start()

    payload = collector.payload
    if not payload:
        print(json.dumps({"ok": False, "error": "the harness never reported back"}))
        return 2

    print(json.dumps(payload))
    failed = [r for r in payload.get("results", []) if not r.get("passed")]
    return 0 if payload.get("ok") and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
