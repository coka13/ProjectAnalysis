"""About: version, build and where the data lives."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, label

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

FIELDS = (
    ("product", "about.title"),
    ("version", "about.version"),
    ("build", "about.build"),
    ("author", "about.createdBy"),
    ("copyright", "about.copyright"),
    ("data_dir", "settings.dataDir"),
)


class AboutView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.about", "about.title")
        card = Card()
        self._body = card.add(label(t("common.loading"), role="muted", wrap=True))
        self.add(card)
        self.add_stretch()

    def load(self) -> None:
        self.fetch(self.api.health, on_done=self._show)

    def _show(self, health: Any) -> None:
        data = health or {}
        lines = [f"{t(key)}: {data.get(field, '—')}" for field, key in FIELDS]
        self._body.setText("\n".join(lines))
