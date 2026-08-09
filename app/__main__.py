"""``python -m app`` launches the original WebView2 desktop application.

Two front ends ship side by side and share everything below the window:

    python -m app        the WebView2 interface (the reference implementation)
    python -m app.ui     the native interface, which needs no WebView2

Both drive the same :class:`app.desktop.bridge.Api`, so a change to the
analysis, scoring or diagram code reaches each of them at once.
"""

from __future__ import annotations

from app.desktop.window import run

if __name__ == "__main__":
    run()
