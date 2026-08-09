"""Creates the native application window."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import webview

from app import branding
from app.config import settings
from app.core.jobs import job_manager
from app.db import init_db
from app.desktop.bridge import APP_VERSION, Api

log = logging.getLogger("aai.desktop")

# Resolved through branding so the frozen build has exactly one rule for where
# its data files live; see branding.resource_root().
WEB_ROOT = branding.resource_root() / "web"
ASSETS_ROOT = branding.resource_root() / "assets"
TITLE = "ProjectAnalysis"
MIN_SIZE = (1024, 680)
STATE_FILE = "window-state.json"
# Matches the dark --bg token so the window does not flash white before the UI
# paints. The in-page bootstrap script keeps the document background in sync.
BACKGROUND = "#0b0f16"

# Groups the window under our own taskbar identity instead of inheriting the
# host interpreter's. Without it Windows files the window under python.exe, so
# the taskbar shows the Python icon and a pinned shortcut opens a second slot.
APP_USER_MODEL_ID = "DanielUralsky.ProjectAnalysis"

# Registered by the Edge WebView2 Runtime installer. pywebview looks for the same
# entry; when it is absent it silently falls back to the Internet Explorer engine.
WEBVIEW2_CLIENT = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_DOWNLOAD = "https://developer.microsoft.com/microsoft-edge/webview2/"

# The variable the WebView2 loader reads to run a specific runtime folder instead
# of the machine's Evergreen install. Setting it lets the app carry its own
# Chromium so a target machine that lacks (or is blocked from installing) the
# runtime still gets a working window.
WEBVIEW2_RUNTIME_ENV = "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"
# Ships next to web/ and assets/ under the resource root; see bundled_runtime_dir.
WEBVIEW2_BUNDLE_DIR = "webview2"


def bundled_runtime_dir() -> Path | None:
    """A WebView2 Fixed Version Runtime shipped with the app, if one is present.

    Returns the folder that directly holds ``msedgewebview2.exe`` so the loader
    can be pointed straight at it. The redistributable extracts to a single
    version folder (``Microsoft.WebView2.FixedVersionRuntime.<ver>.x64``), so
    accept either that folder dropped into ``webview2/`` or its contents copied
    up one level.
    """
    if sys.platform != "win32":
        return None
    base = branding.resource_root() / WEBVIEW2_BUNDLE_DIR
    if (base / "msedgewebview2.exe").exists():
        return base
    if base.is_dir():
        for child in sorted(base.iterdir()):
            if (child / "msedgewebview2.exe").exists():
                return child
    return None


def use_bundled_runtime() -> Path | None:
    """Point the WebView2 loader at the bundled runtime when one ships.

    Leaves an existing value untouched so a deliberate host override still wins,
    and returns the folder in use so the launcher can log and skip the
    missing-runtime warning.
    """
    runtime = bundled_runtime_dir()
    if runtime is None:
        return None
    os.environ.setdefault(WEBVIEW2_RUNTIME_ENV, str(runtime))
    return runtime


def webview2_installed() -> bool:
    """Whether Windows can give us a Chromium-backed window.

    Without the runtime pywebview drops to MSHTML, which cannot parse a single
    one of the application scripts, so the window opens as an empty dark
    rectangle and nothing anywhere says why. Machines that ship without it are
    common enough - older Windows 10 builds, N editions, locked-down images -
    that the launcher has to name the cause itself.
    """
    if sys.platform != "win32":
        return True
    import winreg

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for path in (rf"SOFTWARE\WOW6432Node\{WEBVIEW2_CLIENT}", rf"SOFTWARE\{WEBVIEW2_CLIENT}"):
            try:
                with winreg.OpenKey(hive, path) as key:
                    version = str(winreg.QueryValueEx(key, "pv")[0])
            except OSError:
                continue
            # The installer leaves "0.0.0.0" behind when the runtime is removed.
            if version and version.strip("0."):
                return True
    return False


def _index_file() -> Path:
    index = WEB_ROOT / "index.html"
    if not index.exists():  # pragma: no cover - packaging guard
        raise FileNotFoundError(f"UI files are missing: {index}")
    return index


class WebviewDialogs:
    """Supplies the API with this toolkit's file dialogs.

    The API itself is toolkit-agnostic so both front ends can share it; this is
    the pywebview half of that contract.
    """

    def __init__(self, window) -> None:
        self._window = window

    def _target(self):
        return self._window or (webview.windows[0] if webview.windows else None)

    @staticmethod
    def _first(result) -> str | None:
        if not result:
            return None
        return str(result[0] if isinstance(result, (list, tuple)) else result)

    def pick_folder(self) -> str | None:
        window = self._target()
        if window is None:
            return None
        return self._first(window.create_file_dialog(webview.FOLDER_DIALOG))

    def save_file(self, filename: str) -> str | None:
        window = self._target()
        if window is None:
            return None
        return self._first(window.create_file_dialog(webview.SAVE_DIALOG, save_filename=filename))


LOG_FILE = "app.log"


def _log_to_file() -> Path | None:
    """Mirror the log to a file.

    A windowed build has no console, so a failure that happens before the UI
    paints leaves nothing to read anywhere. This is the only record of why a
    window came up blank on a machine we cannot attach to.
    """
    try:
        path = Path(settings.resolved_data_dir) / LOG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        return path
    except OSError:  # pragma: no cover - read-only or locked profile
        return None


def app_icon() -> Path | None:
    """The one icon file the whole application identifies itself with.

    Returns None rather than raising: a missing icon should cost the window its
    decoration, not its ability to open.
    """
    icon = ASSETS_ROOT / "appicon.ico"
    return icon if icon.is_file() else None


def _claim_taskbar_identity() -> None:
    """Tell Windows this process is its own application.

    pywebview falls back to extracting the icon from ``sys.executable`` when it
    has none of its own, which is how the window ended up wearing the Python
    logo. Setting an explicit AppUserModelID also stops the taskbar from
    merging our button into the interpreter's.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:  # pragma: no cover - cosmetic only
        log.debug("could not set the taskbar application id", exc_info=True)


def _state_file() -> Path:
    return settings.resolved_data_dir / STATE_FILE


def _screens() -> list[tuple[int, int, int, int]]:
    """Bounds of every attached display as ``(x, y, width, height)``."""
    bounds: list[tuple[int, int, int, int]] = []
    try:
        for screen in webview.screens:
            bounds.append((int(screen.x), int(screen.y), int(screen.width), int(screen.height)))
    except Exception:  # pragma: no cover - platform dependent
        log.debug("could not enumerate screens", exc_info=True)
    return bounds


def _fits_a_screen(x: int, y: int, width: int, height: int) -> bool:
    """True when a decent part of the window lands on some connected display.

    Guards against restoring a window onto a monitor that has been unplugged,
    which would leave the app invisible off-screen.
    """
    screens = _screens()
    if not screens:
        return True
    for sx, sy, sw, sh in screens:
        overlap_w = min(x + width, sx + sw) - max(x, sx)
        overlap_h = min(y + height, sy + sh) - max(y, sy)
        # The title bar must be reachable with the mouse, so require a solid
        # horizontal overlap and the top edge to sit inside the display.
        if overlap_w >= 240 and overlap_h >= 80 and sy <= y <= sy + sh - 80:
            return True
    return False


def _load_state() -> dict[str, Any]:
    """Previously saved geometry, sanitised and validated against the displays."""
    path = _state_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("ignoring unreadable window state: %s", path)
        return {}
    if not isinstance(raw, dict):
        return {}

    def number(key: str) -> int | None:
        value = raw.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    width, height = number("width"), number("height")
    state: dict[str, Any] = {"maximized": bool(raw.get("maximized"))}
    if width is None or height is None:
        return state
    width = max(MIN_SIZE[0], min(width, 10_000))
    height = max(MIN_SIZE[1], min(height, 10_000))
    state["width"], state["height"] = width, height

    x, y = number("x"), number("y")
    if x is not None and y is not None and _fits_a_screen(x, y, width, height):
        state["x"], state["y"] = x, y
    return state


def _save_state(state: dict[str, Any]) -> None:
    try:
        _state_file().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:  # pragma: no cover - disk/permission issue
        log.warning("could not persist window state", exc_info=True)


def _track_window(window: webview.Window, restored: dict[str, Any]) -> None:
    """Remember the window geometry so the next launch reopens where it was."""
    normal = {
        "width": restored.get("width", settings.window_width),
        "height": restored.get("height", settings.window_height),
    }
    if "x" in restored:
        normal["x"], normal["y"] = restored["x"], restored["y"]
    flags = {"maximized": bool(restored.get("maximized"))}

    def on_resized(width: int, height: int) -> None:
        # Event handlers run on background threads, so the maximized event can
        # land after the resize it caused. Deciding from the size itself keeps
        # the saved state deterministic.
        screens = _screens()
        if screens:
            if any(width >= sw * 0.96 and height >= sh * 0.9 for _, _, sw, sh in screens):
                flags["maximized"] = True
                return
            flags["maximized"] = False
        elif flags["maximized"]:
            return
        normal["width"], normal["height"] = int(width), int(height)

    def on_moved(x: int, y: int) -> None:
        if flags["maximized"]:
            return
        # A maximized frame overhangs the screen edge (-8,-8 on Windows) and can
        # report that as a move before the maximized event lands.
        if not _fits_a_screen(int(x), int(y), normal["width"], normal["height"]):
            return
        normal["x"], normal["y"] = int(x), int(y)

    def on_maximized() -> None:
        flags["maximized"] = True

    def on_restored() -> None:
        flags["maximized"] = False

    def on_closing() -> None:
        _save_state({**normal, **flags})

    window.events.resized += on_resized
    window.events.moved += on_moved
    window.events.maximized += on_maximized
    window.events.restored += on_restored
    window.events.closing += on_closing


def _report_boot(window: webview.Window) -> None:
    """Record whether the page reached the point of painting.

    Distinguishes the two causes of a black window that look identical from
    outside: the document never loaded (engine or file access), or it loaded and
    the scripts failed (which the page reports in ``__BOOT_ERRORS__``).
    """

    def on_loaded() -> None:
        log.info("document loaded")
        try:
            report = window.evaluate_js(
                "JSON.stringify({"
                "  booted: !!(document.getElementById('root') || {}).firstChild,"
                "  errors: (window.__BOOT_ERRORS__ || []).slice(0, 10)"
                "})"
            )
        except Exception:  # pragma: no cover - engine refused to evaluate
            log.exception("could not query the page after load")
            return
        log.info("boot report: %s", report)

    window.events.loaded += on_loaded


def run() -> None:
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    _log_to_file()
    init_db()
    log.info("ProjectAnalysis %s - data directory: %s", APP_VERSION, settings.resolved_data_dir)
    bundled = use_bundled_runtime()
    if bundled is not None:
        # The env var is read by the WebView2 loader; this setting is pywebview's
        # own path and is honoured even when the loader ignores the environment.
        webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(bundled)
        log.info("Using bundled WebView2 runtime: %s", bundled)
    elif not webview2_installed():
        log.error(
            "The Edge WebView2 Runtime is missing, so this window will fall back to the "
            "Internet Explorer engine and cannot display the interface. Install it from %s "
            "and start the application again.",
            WEBVIEW2_DOWNLOAD,
        )
    log.info("Evergreen WebView2 present: %s", webview2_installed())

    saved = _load_state()
    _claim_taskbar_identity()
    icon = app_icon()
    if icon is None:
        log.warning(
            "assets/appicon.ico is missing, so the window will fall back to the "
            "interpreter's icon. Run 'python tools/make_icon.py' to regenerate it.",
        )
    api = Api()
    index = _index_file()
    log.info("loading UI from %s", index)
    window = webview.create_window(
        TITLE,
        url=index.as_uri(),
        js_api=api,
        width=saved.get("width", settings.window_width),
        height=saved.get("height", settings.window_height),
        x=saved.get("x"),
        y=saved.get("y"),
        maximized=bool(saved.get("maximized")),
        min_size=MIN_SIZE,
        background_color=BACKGROUND,
        text_select=True,
        confirm_close=False,
    )
    api._attach(WebviewDialogs(window))
    _track_window(window, saved)
    _report_boot(window)

    try:
        webview.start(debug=settings.debug, icon=str(icon) if icon else None)
    finally:
        job_manager.shutdown()
        log.info("shut down")
