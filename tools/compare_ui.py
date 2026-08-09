"""Captures the same screen from both front ends, for side-by-side review.

    python tools/compare_ui.py --target original
    python tools/compare_ui.py --target native
    python tools/compare_ui.py --target both

Images land in ``build/compare`` as ``<view>_original.png`` and
``<view>_native.png``. Each front end owns the main thread while it runs, so
``both`` simply runs this file twice as a subprocess.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "build" / "compare"
WIDTH, HEIGHT = 1400, 900

# Renders a window's full content, including surfaces the screen cannot see.
PW_RENDERFULLCONTENT = 2

# The navigation rail lists these in this order in both implementations.
VIEWS = (
    "dashboard",
    "projects",
    "analyses",
    "scorecard",
    "roadmap",
    "hotspots",
    "fixes",
    "trends",
    "diagrams",
    "insights",
    "history",
    "compare",
    "settings",
    "about",
)


# --------------------------------------------------------------------------- #
# Capturing a native window by handle, which works even when it is occluded
# --------------------------------------------------------------------------- #
class _BitmapHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


def grab_window(title: str, path: Path) -> object:
    """Save a window's pixels.

    Returns True, or a string explaining why the capture is unusable. WebView2
    paints into a composited surface that ``PW_CLIENTONLY`` cannot see, so the
    whole window is rendered with ``PW_RENDERFULLCONTENT``; anything else comes
    back blank while still reporting success.
    """
    from PySide6.QtGui import QImage

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return "window not found"

    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return "window has no size"

    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)
    user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)

    header = _BitmapHeader()
    header.biSize = ctypes.sizeof(_BitmapHeader)
    header.biWidth, header.biHeight = width, -height
    header.biPlanes, header.biBitCount = 1, 32
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(hwnd, window_dc)

    image = QImage(bytes(buffer), width, height, QImage.Format.Format_ARGB32)
    if _is_blank(image):
        return "captured but blank"
    path.parent.mkdir(parents=True, exist_ok=True)
    return True if image.save(str(path)) else "could not be written"


def _is_blank(image, samples: int = 400) -> bool:
    """Whether an image is effectively one flat colour.

    A blank capture is the usual failure here and it still saves happily, so
    every screenshot is checked for actual content before being trusted.
    """
    width, height = image.width(), image.height()
    if width < 2 or height < 2:
        return True
    seen = set()
    step_x, step_y = max(1, width // 20), max(1, height // 20)
    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            seen.add(image.pixel(x, y))
            if len(seen) > 6:
                return False
            if len(seen) * len(seen) > samples:
                break
    return len(seen) <= 6


# --------------------------------------------------------------------------- #
# The WebView2 front end
# --------------------------------------------------------------------------- #
def capture_original() -> dict:
    import webview

    from app import branding
    from app.desktop.bridge import Api
    from app.desktop.window import TITLE, use_bundled_runtime

    use_bundled_runtime()
    index = (branding.resource_root() / "web" / "index.html").as_uri()
    report: dict[str, object] = {}

    def drive(window) -> None:
        time.sleep(18)  # the interface boots, then loads its pickers
        for position, key in enumerate(VIEWS):
            try:
                window.evaluate_js(
                    "(function(){var n=document.querySelectorAll('.nav-item');"
                    f"if(n[{position}]) n[{position}].click(); return !!n[{position}];}})()"
                )
            except Exception as exc:  # noqa: BLE001 - recorded, not fatal
                report[key] = f"click failed: {exc!r}"
                continue
            time.sleep(2.2)
            report[key] = grab_window(TITLE, OUT / f"{key}_original.png")
        _write(report, "original")
        os._exit(0)

    api = Api()
    window = webview.create_window(TITLE, url=index, js_api=api, width=WIDTH, height=HEIGHT)
    from app.desktop.window import WebviewDialogs

    api._attach(WebviewDialogs(window))
    threading.Thread(target=drive, args=(window,), daemon=True).start()
    webview.start()
    return report


# --------------------------------------------------------------------------- #
# The native front end
# --------------------------------------------------------------------------- #
def capture_native() -> dict:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QFrame, QWidget

    from app.db import init_db
    from app.desktop.bridge import Api
    from app.ui import prefs as prefs_store
    from app.ui import views
    from app.ui.main import apply_appearance
    from app.ui.shell import MainWindow

    init_db()
    app = QApplication([])
    preferences = prefs_store.load()
    preferences.theme, preferences.language = "dark", "en"
    # Motion off, so a capture never lands mid-transition.
    preferences.motion = "reduced"
    api = Api()
    window = MainWindow(api, preferences)
    api._attach(window)
    views.register_all(window)
    apply_appearance(app, window)
    window.resize(WIDTH, HEIGHT)
    window.show()
    window.load_pickers()

    report: dict[str, object] = {}
    queue = list(VIEWS)

    def step() -> None:
        if not queue:
            report["_topbar_overlaps"] = overlaps()
            _write(report, "native")
            app.quit()
            return
        key = queue.pop(0)
        window.navigate(key)
        QTimer.singleShot(1400, lambda k=key: shoot(k))

    def shoot(key: str) -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        pixmap = window.grab()
        image = pixmap.toImage()
        if _is_blank(image):
            report[key] = "captured but blank"
        else:
            report[key] = True if pixmap.save(str(OUT / f"{key}_native.png")) else "could not be written"
        step()

    def overlaps() -> list[str]:
        """Top bar controls that sit on top of one another.

        A fixed width is only a request: Qt shrinks a widget when the row does
        not fit, and the result reads as one control drawn over another.
        """
        bar = window.findChild(QFrame, "Topbar")
        if bar is None:
            return []
        boxes = [
            (child.objectName() or type(child).__name__, child.geometry())
            for child in bar.children()
            if isinstance(child, QWidget) and child.isVisible()
        ]
        clashes = []
        for i, (name, box) in enumerate(boxes):
            for other_name, other in boxes[i + 1 :]:
                if box.intersects(other):
                    clashes.append(f"{name} overlaps {other_name}")
        return clashes

    QTimer.singleShot(3000, step)
    app.exec()
    return report


def _write(report: dict, target: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"_{target}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("original", "native", "both"), default="both")
    args = parser.parse_args()

    if args.target == "both":
        for target in ("original", "native"):
            result = subprocess.run(
                [sys.executable, __file__, "--target", target], cwd=ROOT, check=False
            )
            if result.returncode:
                return result.returncode
        return 0

    capture_original() if args.target == "original" else capture_native()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
