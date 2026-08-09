"""Window geometry persistence: sanitising, clamping and round-tripping."""

from __future__ import annotations

import json

import pytest

from app.desktop import window as win

ONE_SCREEN = [(0, 0, 1920, 1080)]


@pytest.fixture(autouse=True)
def _single_screen(monkeypatch):
    monkeypatch.setattr(win, "_screens", lambda: ONE_SCREEN)
    path = win._state_file()
    yield
    if path.exists():
        path.unlink()


def _write(payload) -> None:
    win._state_file().write_text(json.dumps(payload), encoding="utf-8")


def test_missing_file_returns_empty_state():
    path = win._state_file()
    if path.exists():
        path.unlink()
    assert win._load_state() == {}


def test_corrupt_file_is_ignored():
    win._state_file().write_text("{ not json", encoding="utf-8")
    assert win._load_state() == {}


def test_valid_geometry_round_trips():
    win._save_state({"width": 1500, "height": 950, "x": 120, "y": 60, "maximized": False})
    assert win._load_state() == {
        "maximized": False,
        "width": 1500,
        "height": 950,
        "x": 120,
        "y": 60,
    }


def test_size_never_drops_below_the_minimum():
    _write({"width": 200, "height": 100})
    state = win._load_state()
    assert (state["width"], state["height"]) == win.MIN_SIZE


def test_position_on_a_disconnected_monitor_is_dropped():
    _write({"width": 1400, "height": 900, "x": -4000, "y": -3000})
    state = win._load_state()
    assert "x" not in state and "y" not in state
    assert state["width"] == 1400


def test_position_below_the_screen_is_dropped():
    _write({"width": 1400, "height": 900, "x": 100, "y": 5000})
    assert "y" not in win._load_state()


def test_maximized_frame_overhang_is_not_a_valid_position():
    # Windows reports a maximized frame at (-8, -8); restoring that as the
    # normal position would nudge the window off the screen edge.
    assert not win._fits_a_screen(-8, -8, 1400, 900)
    assert win._fits_a_screen(0, 0, 1400, 900)


def test_non_numeric_values_are_ignored():
    _write({"width": "wide", "height": None, "maximized": "yes"})
    assert win._load_state() == {"maximized": True}
