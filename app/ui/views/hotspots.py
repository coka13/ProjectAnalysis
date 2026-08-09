"""Hotspots: the files carrying the most risk."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
)

from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, label

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

COLUMNS = (
    "hotspots.file",
    "hotspots.language",
    "hotspots.loc",
    "hotspots.findings",
    "hotspots.debt",
    "hotspots.changes",
    "hotspots.risk",
)


class HotspotsView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.hotspots", "hotspots.subtitle")
        self._rows: list[dict] = []

        self.filter = QLineEdit()
        self.filter.setPlaceholderText(t("hotspots.filter"))
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        self.add(self.filter)

        self._summary = label("", role="dim")
        self.add(self._summary)

        card = Card()
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([t(key) for key in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(420)
        card.add(self.table)
        self.add(card, 1)

        self._empty = label(t("hotspots.empty"), role="muted")
        self.add(self._empty)

    def load(self) -> None:
        analysis_id = self.window.current_analysis_id
        if analysis_id is None:
            self._empty.setVisible(True)
            self.table.setRowCount(0)
            return
        self.fetch(self.api.score_files, {"analysis_id": analysis_id}, on_done=self._show)

    def _show(self, payload: Any) -> None:
        data = payload or {}
        self._rows = list(data.get("files") or [])
        self._summary.setText(f"{len(self._rows)} / {data.get('total_files', 0)}")
        self._fill(self._rows)

    def _risk_colour(self, risk: float) -> QColor:
        tokens = self.window.palette_tokens
        if risk >= 25:
            return QColor(tokens.danger)
        return QColor(tokens.warn) if risk >= 12 else QColor(tokens.text_2)

    def _fill(self, rows: list[dict]) -> None:
        # Sorting is disabled while filling so rows cannot be reordered midway.
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            values = (
                str(item.get("file", "")),
                str(item.get("language", "")),
                item.get("loc", 0),
                item.get("findings", 0),
                item.get("debt_markers", 0),
                item.get("changes", 0),
                item.get("risk", 0),
            )
            for c, value in enumerate(values):
                cell = QTableWidgetItem()
                if isinstance(value, str):
                    cell.setText(value)
                else:
                    # Numeric sorting needs a real number, not its text form.
                    cell.setData(Qt.ItemDataRole.DisplayRole, value)
                if c == len(values) - 1:
                    cell.setForeground(self._risk_colour(float(item.get("risk") or 0)))
                self.table.setItem(r, c, cell)
        self.table.setSortingEnabled(True)
        self._empty.setVisible(not rows)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        if not needle:
            self._fill(self._rows)
            return
        self._fill(
            [
                row
                for row in self._rows
                if needle in str(row.get("file", "")).lower()
                or needle in str(row.get("module", "")).lower()
                or needle in str(row.get("language", "")).lower()
            ]
        )
