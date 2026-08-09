"""Settings: appearance, and the details of where data lives."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QComboBox, QFormLayout, QWidget

from app.ui import prefs as prefs_store
from app.ui import theme
from app.ui.i18n import LANGUAGES
from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, button, label

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

LANGUAGE_NAMES = {"en": "English", "he": "עברית"}
SCALE_LABELS = {0.9: "A-", 1.0: "A", 1.12: "A+", 1.25: "A++"}


class SettingsView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.settings", "settings.subtitle")

        appearance = Card(t("settings.appearance"))
        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setSpacing(theme.S[3])

        self.theme_box = self._choice(
            [("dark", t("settings.dark")), ("light", t("settings.light"))], window.prefs.theme
        )
        self.language_box = self._choice(
            [(code, LANGUAGE_NAMES[code]) for code in LANGUAGES], window.prefs.language
        )
        self.contrast_box = self._choice(
            [("normal", t("settings.normal")), ("high", t("settings.high"))], window.prefs.contrast
        )
        self.palette_box = self._choice(
            [("default", t("settings.paletteDefault")), ("cb", t("settings.paletteCb"))],
            window.prefs.palette,
        )
        self.scale_box = self._choice(
            [(str(v), lbl) for v, lbl in SCALE_LABELS.items()], str(window.prefs.scale)
        )

        form.addRow(t("common.theme"), self.theme_box)
        form.addRow(t("common.language"), self.language_box)
        form.addRow(t("settings.contrast"), self.contrast_box)
        form.addRow(t("settings.palette"), self.palette_box)
        form.addRow(t("settings.textSize"), self.scale_box)
        appearance.add(form_host)
        appearance.add(button(t("common.save"), variant="primary", on_click=self._apply))
        self.add(appearance)

        self._storage = Card(t("settings.storage"))
        self._storage_body = self._storage.add(label(t("common.loading"), role="muted", wrap=True))
        self.add(self._storage)
        self.add_stretch()

    def _choice(self, options: list[tuple[str, str]], current: str) -> QComboBox:
        box = QComboBox()
        for value, text in options:
            box.addItem(text, value)
        index = box.findData(current)
        if index >= 0:
            box.setCurrentIndex(index)
        return box

    def load(self) -> None:
        self.fetch(self.api.settings_summary, on_done=self._show_storage)

    def _show_storage(self, summary: Any) -> None:
        data = summary or {}
        lines = [f"{key}: {value}" for key, value in data.items() if not isinstance(value, (dict, list))]
        self._storage_body.setText("\n".join(lines) or t("common.none"))

    def _apply(self) -> None:
        """Persist the choices and restyle the running window in place."""
        from PySide6.QtWidgets import QApplication

        from app.ui.main import apply_appearance

        prefs = self.window.prefs
        prefs.theme = self.theme_box.currentData()
        prefs.language = self.language_box.currentData()
        prefs.contrast = self.contrast_box.currentData()
        prefs.palette = self.palette_box.currentData()
        prefs.scale = float(self.scale_box.currentData())
        self.window.prefs = prefs.normalised()
        prefs_store.save(self.window.prefs)

        app = QApplication.instance()
        if app is not None:
            apply_appearance(app, self.window)
        self.window.notify(t("common.saved"))
