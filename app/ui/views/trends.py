"""Trends: how the score has moved across runs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QComboBox

from app.graph import scoring
from app.ui.charts import BarChart, LineChart, Slice
from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.views.scorecard import category_name
from app.ui.widgets import Card, label, row

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow


class TrendsView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.trends", "trends.subtitle")
        tokens = window.palette_tokens
        self._projects: list[dict] = []

        self.picker = QComboBox()
        self.picker.setMinimumWidth(260)
        self.picker.currentIndexChanged.connect(lambda _: self._load_trend())
        self.add(row(self.picker))

        history = Card(t("score.history"), t("score.historyHint"))
        self.line = LineChart(tokens)
        history.add(self.line)
        self.add(history)

        deltas = Card(t("score.categoryTrends"))
        self.bars = BarChart(tokens)
        deltas.add(self.bars)
        self.add(deltas)

        self._note = label("", role="muted", wrap=True)
        self.add(self._note)
        self.add_stretch()

    def load(self) -> None:
        self.fetch(self.api.projects_list, on_done=self._fill_projects)

    def _fill_projects(self, projects: Any) -> None:
        self._projects = list(projects or [])
        current = self.picker.currentData()
        self.picker.blockSignals(True)
        self.picker.clear()
        for project in self._projects:
            self.picker.addItem(str(project.get("name", "")), project.get("id"))
        if current is not None:
            index = self.picker.findData(current)
            if index >= 0:
                self.picker.setCurrentIndex(index)
        self.picker.blockSignals(False)
        self._load_trend()

    def _load_trend(self) -> None:
        project_id = self.picker.currentData()
        if project_id is None:
            return
        self.fetch(self.api.score_trend, {"project_id": project_id}, on_done=self._show)

    def _show(self, payload: Any) -> None:
        data = payload or {}
        points = list(data.get("points") or [])
        tokens = self.window.palette_tokens
        self.line.set_tokens(tokens)
        self.bars.set_tokens(tokens)

        if len(points) < 2:
            self.line.set_data([])
            self.bars.set_data([])
            self._note.setText(f"{t('score.needTwoRuns')}\n{t('score.needTwoRunsHint')}")
            return

        self._note.setText("")
        self.line.set_data(
            [(str(p.get("commit") or p.get("ref") or ""), float(p.get("overall") or 0)) for p in points]
        )

        deltas = (data.get("deltas") or {}).get("categories") or {}
        slices = []
        for key in scoring.CATEGORY_ORDER:
            if key not in deltas:
                continue
            change = float(deltas.get(key) or 0)
            colour = tokens.ok if change > 0 else (tokens.danger if change < 0 else tokens.text_3)
            slices.append(Slice(category_name(key), abs(change), colour))
        largest = max((s.value for s in slices), default=1.0)
        self.bars.set_data(slices, maximum=max(1.0, largest))
