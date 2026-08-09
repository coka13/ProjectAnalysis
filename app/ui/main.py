"""Starts the native desktop application."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app import branding
from app.config import settings
from app.core.jobs import job_manager
from app.db import init_db
from app.desktop.bridge import APP_VERSION, Api
from app.ui import prefs as prefs_store
from app.ui import theme, views
from app.ui.i18n import translator
from app.ui.shell import MainWindow

log = logging.getLogger("aai.ui")

# Both front ends can be installed at once, so neither may claim the other's
# geometry or overwrite its log.
STATE_FILE = "window-state-native.json"
LOG_FILE = "app-native.log"
MIN_SIZE = (1024, 680)


def _log_to_file() -> Path | None:
    """Mirror the log to a file; a windowed build has no console to print to."""
    try:
        path = Path(settings.resolved_data_dir) / LOG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        return path
    except OSError:  # pragma: no cover - read-only or locked profile
        return None


def _state_path() -> Path:
    return Path(settings.resolved_data_dir) / STATE_FILE


def _load_state() -> dict:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(window: MainWindow) -> None:
    try:
        geometry = window.normalGeometry()
        _state_path().write_text(
            json.dumps(
                {
                    "width": geometry.width(),
                    "height": geometry.height(),
                    "x": geometry.x(),
                    "y": geometry.y(),
                    "maximized": window.isMaximized(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        log.warning("could not persist window state", exc_info=True)


def _restore(window: MainWindow, state: dict) -> None:
    width = int(state.get("width") or settings.window_width)
    height = int(state.get("height") or settings.window_height)
    window.resize(max(MIN_SIZE[0], width), max(MIN_SIZE[1], height))
    x, y = state.get("x"), state.get("y")
    if isinstance(x, int) and isinstance(y, int):
        # Only honour a position that still lands on a connected screen.
        for screen in QApplication.screens():
            if screen.availableGeometry().contains(x + 40, y + 40):
                window.move(x, y)
                break
    if state.get("maximized"):
        window.showMaximized()
    else:
        window.show()


def apply_appearance(app: QApplication, window: MainWindow) -> None:
    """Theme, text size and reading direction, applied to the whole app."""
    prefs = window.prefs
    tokens = theme.palette(prefs.theme, contrast=prefs.contrast, colours=prefs.palette)
    window.palette_tokens = tokens
    app.setStyleSheet(theme.stylesheet(tokens, scale=prefs.scale))
    # The search chip is painted, so it needs the new colours handed to it.
    window.search.set_tokens(tokens)
    translator.language = prefs.language
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if translator.is_rtl else Qt.LayoutDirection.LeftToRight
    )


def run() -> None:
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    _log_to_file()
    init_db()
    log.info("ProjectAnalysis %s - data directory: %s", APP_VERSION, settings.resolved_data_dir)

    # Qt scales to the monitor on its own; this only picks the rounding that
    # keeps 125% and 150% displays from smearing text.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(branding.PRODUCT_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(branding.AUTHOR)
    # Groups the window under our own taskbar identity rather than python.exe.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(branding.APP_ID)
        except Exception:  # pragma: no cover - cosmetic only
            log.debug("could not set the taskbar identity", exc_info=True)

    icon_path = branding.ICON
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    else:
        log.warning("assets/appicon.ico is missing; run 'python tools/make_icon.py'")

    preferences = prefs_store.load()
    api = Api()
    window = MainWindow(api, preferences)
    api._attach(window)
    views.register_all(window)
    apply_appearance(app, window)

    _restore(window, _load_state())
    if preferences.sidebar_collapsed:
        window.sidebar.setVisible(False)
    window.load_pickers()
    window.navigate("dashboard")
    log.info("window shown")

    app.aboutToQuit.connect(lambda: (_save_state(window), job_manager.shutdown()))
    try:
        sys.exit(app.exec())
    finally:
        log.info("shut down")
