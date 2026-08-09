"""Analyses: every run of a project, and starting a new one."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMessageBox, QProgressBar, QVBoxLayout, QWidget

from app.ui import theme
from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, badge, button, label

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

POLL_MS = 1200
ACTIVE = ("running", "pending")
STATUS_TONES = {
    "succeeded": "ok",
    "running": "info",
    "pending": "info",
    "failed": "err",
    "cancelled": "warn",
}


class RunRow(QFrame):
    """One run: its state, its ref, when it finished, and how to remove it."""

    def __init__(self, run: dict, view: "AnalysesView", selected: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Row")
        tokens = view.window.palette_tokens
        border = tokens.accent if selected else tokens.line
        self.setStyleSheet(
            f"#Row {{ border: 1px solid {border}; border-radius: {theme.R_MD}px;"
            f" background: {tokens.surface_2}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.S[4], theme.S[3], theme.S[4], theme.S[3])
        layout.setSpacing(theme.S[2])

        top = QHBoxLayout()
        top.setSpacing(theme.S[3])
        status = str(run.get("status") or "")
        top.addWidget(badge(t(f"status.{status}"), STATUS_TONES.get(status, "muted"), tokens))
        top.addWidget(label(str(run.get("ref") or "—"), role="muted"))
        when = str(run.get("finished_at") or run.get("created_at") or "")
        top.addWidget(label(when[:19].replace("T", " ")))
        top.addStretch(1)

        remove = button(
            "", variant="ghost", icon_name="trash", colour=tokens.danger, tooltip=t("analysis.delete")
        )
        remove.setFixedWidth(36)
        remove.clicked.connect(lambda: view.delete(run))
        top.addWidget(remove)
        layout.addLayout(top)

        if status in ACTIVE:
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(float(run.get("progress") or 0) * 100))
            bar.setTextVisible(False)
            layout.addWidget(bar)
            layout.addWidget(label(str(run.get("stage") or t("analysis.running")), role="dim"))


class AnalysesView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.analyses", "analysis.subtitle")
        self._runs: list[dict] = []

        self.add_header_action(
            button(t("analysis.start"), variant="primary", icon_name="play", on_click=self._start)
        )

        card = Card()
        holder = QWidget()
        holder.setObjectName("Plain")
        self._rows = QVBoxLayout(holder)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(theme.S[3])
        card.add(holder)
        self._empty = label(t("analysis.empty"), role="muted")
        card.add(self._empty)
        self.add(card)
        self.add_stretch()

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.load)

    # ------------------------------------------------------------------ data
    def load(self) -> None:
        project_id = self.window.project_picker.currentData()
        if project_id is None:
            self._clear()
            self._empty.setVisible(True)
            return
        self.fetch(self.api.analyses_list, {"project_id": project_id}, on_done=self._show)

    def _clear(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _show(self, runs: Any) -> None:
        self._runs = list(runs or [])
        self._clear()
        current = self.window.current_analysis_id
        for run in self._runs:
            self._rows.addWidget(RunRow(run, self, run.get("id") == current))
        self._empty.setVisible(not self._runs)

        # Poll only while something is in flight; an idle screen stays still.
        if any(run.get("status") in ACTIVE for run in self._runs):
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    # --------------------------------------------------------------- actions
    def _start(self) -> None:
        project_id = self.window.project_picker.currentData()
        if project_id is None:
            self.show_error(t("common.required"))
            return
        self.fetch(self.api.analysis_start, {"project_id": project_id}, on_done=lambda _: self.load())

    def delete(self, run: dict) -> None:
        confirm = QMessageBox.question(self, t("analysis.delete"), str(run.get("ref") or ""))
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.fetch(
            self.api.analysis_delete, {"analysis_id": run.get("id")}, on_done=lambda _: self.load()
        )
