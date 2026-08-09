"""Analyses: start a run and watch it progress."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
)

from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, button, label, row

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

# Runs are polled while one is active; the analysis itself reports its stage.
POLL_MS = 1200


class AnalysesView(DataView):
    COLUMNS = ("analysis.ref", "analysis.status", "analysis.stage", "analysis.duration")

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.analyses", "analysis.subtitle")
        self._projects: list[dict] = []
        self._runs: list[dict] = []

        self.picker = QComboBox()
        self.picker.setMinimumWidth(260)
        self.picker.currentIndexChanged.connect(lambda _: self._load_runs())

        self.add(
            row(
                self.picker,
                button(t("analysis.start"), variant="primary", icon_name="play", on_click=self._start),
                button(t("analysis.cancel"), icon_name="cross", on_click=self._cancel),
                button(t("common.retry"), icon_name="refresh", on_click=self.refresh),
            )
        )

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        self.add(self.progress)
        self._stage = label("", role="dim")
        self.add(self._stage)

        card = Card()
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([t(key) for key in self.COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(340)
        card.add(self.table)
        self.add(card, 1)
        self.add_stretch()

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

    # ------------------------------------------------------------------ data
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
        self._load_runs()

    def _project_id(self) -> int | None:
        return self.picker.currentData()

    def _load_runs(self) -> None:
        project_id = self._project_id()
        if project_id is None:
            self.table.setRowCount(0)
            return
        self.fetch(self.api.analyses_list, {"project_id": project_id}, on_done=self._fill_runs)

    def _fill_runs(self, runs: Any) -> None:
        self._runs = list(runs or [])
        self.table.setRowCount(len(self._runs))
        active = None
        for r, run in enumerate(self._runs):
            cells = (
                str(run.get("ref") or "—"),
                str(run.get("status") or ""),
                str(run.get("stage") or ""),
                str(run.get("started_at") or "")[:19].replace("T", " "),
            )
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
            if run.get("status") in ("running", "pending"):
                active = run
        self._show_active(active)

    def _show_active(self, run: dict | None) -> None:
        if run is None:
            self.progress.setVisible(False)
            self._stage.setText("")
            self._timer.stop()
            self.window.current_analysis_id = self._latest_done_id()
            return
        self.progress.setVisible(True)
        self.progress.setValue(int(float(run.get("progress") or 0) * 100))
        self._stage.setText(str(run.get("stage") or ""))
        if not self._timer.isActive():
            self._timer.start()

    def _latest_done_id(self) -> int | None:
        for run in self._runs:
            if run.get("status") == "succeeded":
                return run.get("id")
        return None

    def _poll(self) -> None:
        self._load_runs()

    # --------------------------------------------------------------- actions
    def _start(self) -> None:
        project_id = self._project_id()
        if project_id is None:
            self.show_error(t("common.required"))
            return
        self.fetch(self.api.analysis_start, {"project_id": project_id}, on_done=lambda _: self._load_runs())

    def _cancel(self) -> None:
        for run in self._runs:
            if run.get("status") in ("running", "pending"):
                self.fetch(
                    self.api.analysis_cancel, {"analysis_id": run.get("id")}, on_done=lambda _: self._load_runs()
                )
                return
