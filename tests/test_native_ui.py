"""Tests for the native interface.

Qt needs a display; on a headless machine the offscreen platform provides one,
so these run anywhere pytest does. Everything here is about the parts that have
actually broken: translation lookup, icon parsing, the theme's score bands, and
the threading rule that decides whether a view ever repaints.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="the native interface is optional")

# Must be set before QApplication is created.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.graph import scoring  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.i18n import LANGUAGES, Translator, _load  # noqa: E402
from app.ui.icons import icon, paths  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------------- i18n
def test_both_languages_carry_the_same_keys():
    english, hebrew = _load("en"), _load("he")
    assert english, "English strings failed to parse"
    assert set(english) == set(hebrew)


def test_translator_substitutes_placeholders():
    translate = Translator("en")
    assert translate("common.savedTo", path="C:/x.svg").endswith("C:/x.svg")


def test_translator_falls_back_to_the_key():
    assert Translator("en")("no.such.key") == "no.such.key"


def test_hebrew_is_right_to_left():
    translate = Translator("he")
    assert translate.is_rtl
    assert translate("nav.dashboard") != "nav.dashboard"
    assert not Translator("en").is_rtl


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_language_loads(language: str):
    assert _load(language)


# -------------------------------------------------------------------- icons
def test_icon_paths_parse_including_concatenated_ones():
    glyphs = paths()
    assert len(glyphs) >= 30
    # These two are written as several joined string literals in dom.js.
    assert "M8.2 18h7.6" in glyphs["appmark"]
    assert len(glyphs["settings"]) > 100


def test_icons_render_at_twice_the_size_for_high_dpi(app: QApplication):
    rendered = icon("dashboard", "#4f9cf9", 18)
    assert not rendered.isNull()
    assert rendered.availableSizes()[0].width() == 36


def test_unknown_icon_falls_back_rather_than_raising(app: QApplication):
    assert not icon("definitely-not-a-glyph").isNull()


# -------------------------------------------------------------------- theme
@pytest.mark.parametrize(
    ("score", "expected"),
    [(95, "#37d399"), (80, "#86c440"), (65, "#f5b544"), (49, "#f08b3c"), (10, "#f4676a")],
)
def test_score_colours_follow_the_five_bands(score: int, expected: str):
    assert theme.score_colour(score, theme.DARK) == expected


def test_score_colour_agrees_with_the_scorecard():
    for score in (0, 39, 40, 59, 60, 74, 75, 89, 90, 100):
        band = scoring.band_for(score)
        tone = theme.BAND_TONES[band]
        expected = tone if tone.startswith("#") else getattr(theme.DARK, tone)
        assert theme.score_colour(score, theme.DARK) == expected


def test_stylesheet_uses_pixels_so_it_matches_the_original():
    sheet = theme.stylesheet(theme.DARK)
    assert f"font-size: {theme.F_MD}px" in sheet
    assert "pt;" not in sheet


def test_labels_are_transparent_over_cards():
    # A QLabel inheriting the QWidget rule paints the window colour over a card.
    assert "QLabel, QCheckBox, QRadioButton {\n        background: transparent;" in theme.stylesheet(
        theme.DARK
    )


def test_high_contrast_and_colour_blind_palettes_differ():
    plain = theme.palette("dark")
    assert theme.palette("dark", contrast="high").line != plain.line
    assert theme.palette("dark", colours="cb").ok != plain.ok


# ------------------------------------------------------------------ widgets
def test_elided_label_shortens_instead_of_clipping(app: QApplication):
    from PySide6.QtGui import QFontMetrics

    from app.ui.widgets import ElidedLabel

    widget = ElidedLabel("C:/a/very/long/path/that/will/not/fit/in/the/card.py")
    widget.resize(120, 20)
    shown = QFontMetrics(widget.font()).elidedText(
        widget._full, Qt.TextElideMode.ElideRight, widget.width()
    )
    assert shown.endswith("\u2026")
    assert widget.toolTip() == widget._full


def test_search_field_reserves_room_for_its_chip(app: QApplication):
    from app.ui.widgets import SearchField

    field = SearchField("Ctrl K", theme.DARK)
    field.resize(420, 32)
    box = field._chip_rect()
    assert box.width() > 20
    assert 0 < box.x() < field.width()
    # The text must not run under the chip.
    assert field.textMargins().right() >= box.width()


def test_badge_uses_the_tone_it_was_given(app: QApplication):
    from app.ui.widgets import badge

    assert theme.DARK.ok in badge("Completed", "ok", theme.DARK).styleSheet()


# ------------------------------------------------------------------ workers
def test_unwrap_returns_data_and_raises_the_error():
    from app.ui.workers import unwrap

    assert unwrap({"ok": True, "data": [1, 2]}) == [1, 2]
    with pytest.raises(RuntimeError, match="boom"):
        unwrap({"ok": False, "error": "boom"})


def test_results_are_delivered_on_the_calling_thread(app: QApplication):
    """A direct connection would run the callback on the worker thread.

    That bug left every view stuck on "Loading...", so it is worth pinning.
    """
    import threading

    from PySide6.QtCore import QEventLoop, QTimer

    from app.ui import workers

    seen: dict[str, object] = {}
    loop = QEventLoop()

    def done(value):
        seen["value"] = value
        seen["thread"] = threading.current_thread().ident
        loop.quit()

    workers.run(lambda payload: {"ok": True, "data": payload}, {"n": 1}, on_done=done)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()

    assert seen.get("value") == {"ok": True, "data": {"n": 1}}
    assert seen["thread"] == threading.current_thread().ident


# -------------------------------------------------------------------- shell
def test_navigation_covers_every_registered_view():
    from app.ui.shell import NAVIGATION

    keys = [item.key for group in NAVIGATION for item in group.items]
    assert len(keys) == len(set(keys))
    assert {"dashboard", "projects", "analyses", "settings", "about"} <= set(keys)


def test_the_api_needs_no_ui_toolkit():
    """The bridge is shared, so it must not import a front end."""
    import inspect

    from app.desktop import bridge

    source = inspect.getsource(bridge)
    assert "import webview" not in source
    assert "PySide6" not in source
