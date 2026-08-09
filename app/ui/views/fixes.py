"""Guided fixes: what can be repaired, and applying the chosen ones."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHeaderView,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
)

from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, button, label, row

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

COLUMNS = ("fixes.select", "fixes.problem", "fixes.severity", "fixes.files", "fixes.impact")
SEVERITIES = ("critical", "high", "medium", "low", "info")


class FixesView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.fixes", "fixes.subtitle")
        self._proposals: list[dict] = []

        self.severity = QComboBox()
        self.severity.addItem(t("fixes.allSeverities"), "")
        for level in SEVERITIES:
            self.severity.addItem(t(f"score.sev.{level}"), level)
        self.severity.currentIndexChanged.connect(lambda _: self._fill())

        self.only_fixable = QCheckBox(t("fixes.onlyFixable"))
        self.only_fixable.stateChanged.connect(lambda _: self._fill())
        self.include_cosmetic = QCheckBox(t("fixes.includeCosmetic"))
        self.include_cosmetic.stateChanged.connect(lambda _: self.refresh())

        self.add(
            row(
                self.severity,
                self.only_fixable,
                self.include_cosmetic,
                button(t("fixes.selectAll"), on_click=self._select_all),
                button(t("fixes.clearSelection"), on_click=self._clear_selection),
                button(t("fixes.apply"), variant="primary", icon_name="wrench", on_click=self._apply),
            )
        )

        self._summary = label("", role="dim")
        self.add(self._summary)

        card = Card()
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([t(key) for key in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(380)
        self.table.itemSelectionChanged.connect(self._show_detail)
        card.add(self.table)
        self.add(card, 1)

        self.detail = Card(t("fixes.howTo"))
        self._detail_body = self.detail.add(label(t("fixes.cleanHint"), role="muted", wrap=True))
        self.add(self.detail)

    # ------------------------------------------------------------------ data
    def load(self) -> None:
        analysis_id = self.window.current_analysis_id
        if analysis_id is None:
            self._summary.setText(t("score.unavailable"))
            return
        self.fetch(
            self.api.analysis_fix_proposals,
            {
                "analysis_id": analysis_id,
                "limit": 200,
                "include_cosmetic": self.include_cosmetic.isChecked(),
                "language": self.window.prefs.language,
            },
            on_done=self._show,
        )

    def _show(self, payload: Any) -> None:
        data = payload or {}
        self._proposals = list(data.get("proposals") or data.get("items") or [])
        mode = (
            t("fixes.modeAi", n=int(data.get("ai_patches") or 0))
            if data.get("ai_used")
            else t("fixes.modeOffline")
        )
        self._summary.setText(f"{mode}   {t('fixes.total')}: {len(self._proposals)}")
        self._fill()

    def _visible(self) -> list[dict]:
        level = self.severity.currentData()
        rows = self._proposals
        if level:
            rows = [p for p in rows if str(p.get("severity")) == level]
        if self.only_fixable.isChecked():
            rows = [p for p in rows if p.get("patch") or p.get("diff") or p.get("auto_fixable")]
        return rows

    def _fill(self) -> None:
        rows = self._visible()
        self.table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            check = QTableWidgetItem()
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, item)
            self.table.setItem(r, 0, check)

            files = item.get("files") or ([item["file"]] if item.get("file") else [])
            for column, value in (
                (1, str(item.get("title") or item.get("problem") or item.get("message") or "")),
                (2, t(f"score.sev.{item.get('severity', 'info')}")),
                (3, str(len(files))),
                (4, str(item.get("impact") or item.get("rule") or "")),
            ):
                self.table.setItem(r, column, QTableWidgetItem(value))
        self.table.setProperty("rows", len(rows))

    def _show_detail(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return
        cell = self.table.item(rows[0].row(), 0)
        item = cell.data(Qt.ItemDataRole.UserRole) if cell else None
        if not isinstance(item, dict):
            return
        parts = [
            f"{t('fixes.rootCause')}: {item.get('why') or item.get('detail') or ''}",
            f"{t('fixes.howTo')}: {item.get('how') or item.get('remediation') or ''}",
        ]
        files = item.get("files") or ([item["file"]] if item.get("file") else [])
        if files:
            parts.append(
                f"{t('fixes.filesAffected', n=len(files))}: "
                + ", ".join(str(f) for f in files[:12])
            )
        diff = item.get("diff") or item.get("patch")
        parts.append(str(diff) if diff else t("fixes.noDiff"))
        self._detail_body.setText("\n\n".join(parts))

    # --------------------------------------------------------------- actions
    def _set_all(self, state: Qt.CheckState) -> None:
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 0)
            if cell is not None:
                cell.setCheckState(state)

    def _select_all(self) -> None:
        self._set_all(Qt.CheckState.Checked)

    def _clear_selection(self) -> None:
        self._set_all(Qt.CheckState.Unchecked)

    def _chosen(self) -> list[dict]:
        chosen = []
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 0)
            if cell is not None and cell.checkState() == Qt.CheckState.Checked:
                item = cell.data(Qt.ItemDataRole.UserRole)
                if isinstance(item, dict):
                    chosen.append(item)
        return chosen

    def _apply(self) -> None:
        """Writing to the working tree always needs an explicit confirmation."""
        selections = self._chosen()
        if not selections:
            self.show_error(t("fixes.select"))
            return
        touched = {str(s.get("file")) for s in selections if s.get("file")}
        confirm = QMessageBox.question(
            self,
            t("fixes.applyConfirm"),
            f"{t('fixes.confirmBody', n=len(selections), files=len(touched))}\n\n"
            f"{t('fixes.selectedCount', n=len(selections))}",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.fetch(
            self.api.analysis_apply_fixes,
            {
                "analysis_id": self.window.current_analysis_id,
                "selections": [
                    {"file": s.get("file"), "rules": s.get("rules") or ([s["rule"]] if s.get("rule") else [])}
                    for s in selections
                ],
                "confirm": True,
            },
            on_done=self._applied,
        )

    def _applied(self, result: Any) -> None:
        data = result or {}
        self.window.notify(t("fixes.applied", n=int(data.get("applied") or 0)))
        self.refresh()
