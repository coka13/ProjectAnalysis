"""Repository history: activity, contributors and the commit graph."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from app.ui.charts import BarChart, Slice
from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, Grid, Stat, label

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

PEOPLE_COLUMNS = ("history.owner", "history.commits", "history.share")
HOTSPOT_COLUMNS = ("hotspots.file", "history.changes", "history.contributors", "history.riskLevel")


class HistoryView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.history", "history.subtitle")
        tokens = window.palette_tokens

        self._stats = Grid(4)
        self.add(self._stats)
        self._cards: dict[str, Stat] = {}

        self._notice = label("", role="muted", wrap=True)
        self.add(self._notice)

        people = Card(t("history.contributors"), t("history.podiumHint"))
        self.people = self._table(PEOPLE_COLUMNS, height=240)
        people.add(self.people)
        self.add(people)

        churn = Card(t("history.hotspots"), t("history.coupling"))
        self.bars = BarChart(tokens)
        churn.add(self.bars)
        self.hotspots = self._table(HOTSPOT_COLUMNS, height=260)
        churn.add(self.hotspots)
        self.add(churn)

    def _table(self, columns: tuple[str, ...], *, height: int) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels([t(key) for key in columns])
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.setMinimumHeight(height)
        return table

    def load(self) -> None:
        analysis_id = self.window.current_analysis_id
        if analysis_id is None:
            self._notice.setText(t("history.unavailableHint"))
            return
        self.fetch(self.api.analysis_history, {"analysis_id": analysis_id}, on_done=self._show)

    def _show(self, payload: Any) -> None:
        data = payload or {}
        if not data.get("available"):
            # git_history names the reason by key when the repository is unusable.
            self._notice.setText(t(str(data.get("reason_key") or "history.unavailable")))
            for table in (self.people, self.hotspots):
                table.setRowCount(0)
            self.bars.set_data([])
            return

        self._notice.setText("")
        tokens = self.window.palette_tokens
        if not self._cards:
            self._cards = {
                "commits": Stat(t("history.commits"), "0", tone=tokens.accent),
                "people": Stat(t("history.contributors"), "0", tone=tokens.info),
                "months": Stat(t("history.activity"), "0", tone=tokens.ok),
                "coupling": Stat(t("history.coupling"), "0", tone=tokens.warn),
            }
            self._stats.add_all(self._cards.values())

        self._cards["commits"].set_value(str(data.get("commit_count") or 0))
        # `contributors` is a count; the people themselves are in top_contributors.
        self._cards["people"].set_value(str(data.get("contributors") or 0))
        self._cards["months"].set_value(str(len(data.get("activity_by_month") or {})))
        self._cards["coupling"].set_value(str(len(data.get("temporal_coupling") or [])))

        people = list(data.get("top_contributors") or [])
        total = sum(int(p.get("commits") or 0) for p in people) or 1
        self.people.setRowCount(len(people))
        for r, person in enumerate(people):
            count = int(person.get("commits") or 0)
            for column, value in (
                (0, str(person.get("author") or "")),
                (1, str(count)),
                (2, f"{count * 100 // total}%"),
            ):
                self.people.setItem(r, column, QTableWidgetItem(value))

        hotspots = list(data.get("hotspots") or [])
        self.hotspots.setRowCount(len(hotspots))
        for r, spot in enumerate(hotspots):
            for column, value in (
                (0, str(spot.get("path") or "")),
                (1, str(spot.get("changes") or 0)),
                (2, str(spot.get("authors") or 0)),
                (3, str(spot.get("risk") or "")),
            ):
                self.hotspots.setItem(r, column, QTableWidgetItem(value))

        top = hotspots[:10]
        self.bars.set_tokens(tokens)
        self.bars.set_data(
            [Slice(str(s.get("path") or ""), float(s.get("changes") or 0), tokens.warn) for s in top],
            maximum=max((float(s.get("changes") or 0) for s in top), default=1.0),
        )
