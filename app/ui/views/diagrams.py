"""Diagrams: generate, browse and read them."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QComboBox, QLineEdit, QListWidget, QListWidgetItem, QSplitter
from PySide6.QtCore import Qt

from app.ui.diagram import DiagramView
from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, button, label, row

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

log = logging.getLogger("aai.ui.views.diagrams")


class DiagramsView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.diagrams", "diagram.subtitle")
        self._diagrams: list[dict] = []

        self.kind = QComboBox()
        self.kind.setMinimumWidth(220)
        self.add(
            row(
                self.kind,
                button(t("diagram.generate"), variant="primary", icon_name="sparkle", on_click=self._generate),
                button(t("common.refresh"), icon_name="refresh", on_click=self.refresh),
            )
        )

        # ai_query turns a sentence into a diagram, so the prompt lives here.
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText(t("ai.askPlaceholder"))
        self.prompt.returnPressed.connect(self._ask)
        self.add(row(self.prompt, button(t("ai.ask"), icon_name="sparkle", on_click=self._ask), stretch_last=True))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setMinimumWidth(240)
        self.list.currentRowChanged.connect(self._select)
        splitter.addWidget(self.list)

        holder = Card()
        self.canvas = DiagramView(window.palette_tokens)
        holder.add(self.canvas, 1)
        holder.add(
            row(
                button("-", on_click=lambda: self.canvas.zoom_by(1 / 1.15)),
                button("+", on_click=lambda: self.canvas.zoom_by(1.15)),
                button(t("common.refresh"), on_click=self.canvas.fit),
            )
        )
        splitter.addWidget(holder)
        splitter.setStretchFactor(1, 1)
        self.add(splitter, 1)

        self._notes = label("", role="dim", wrap=True)
        self.add(self._notes)

    def load(self) -> None:
        if not self.kind.count():
            self.fetch(self.api.diagram_kinds, on_done=self._fill_kinds)
        analysis_id = self.window.current_analysis_id
        if analysis_id is None:
            self._notes.setText(t("diagram.emptyHint"))
            return
        self.fetch(self.api.diagrams_list, {"analysis_id": analysis_id}, on_done=self._fill_list)

    def _fill_kinds(self, kinds: Any) -> None:
        self.kind.clear()
        for name in kinds or []:
            # Every kind has its own label; fall back to the raw name if not.
            self.kind.addItem(t(f"diagram.{name}"), name)

    def _fill_list(self, diagrams: Any) -> None:
        self._diagrams = list(diagrams or [])
        # Repopulating re-emits currentRowChanged, so selection is suspended
        # until the rows and their data are both in place.
        self.list.blockSignals(True)
        self.list.clear()
        for diagram in self._diagrams:
            entry = QListWidgetItem(str(diagram.get("title") or diagram.get("kind")))
            entry.setData(Qt.ItemDataRole.UserRole, diagram)
            self.list.addItem(entry)
        self.list.blockSignals(False)
        if self._diagrams:
            self.list.setCurrentRow(0)
        else:
            self._notes.setText(t("diagram.empty"))

    def _select(self, index: int) -> None:
        entry = self.list.item(index)
        if entry is None:
            return
        diagram = entry.data(Qt.ItemDataRole.UserRole) or {}
        diagram_id = diagram.get("id")
        if not diagram_id:
            return
        self.fetch(self.api.diagram_get, {"diagram_id": diagram_id}, on_done=self._render)

    def _render(self, diagram: Any) -> None:
        data = diagram or {}
        payload = data.get("payload") or {}
        nodes, edges = payload.get("nodes") or [], payload.get("edges") or []
        self.canvas.set_tokens(self.window.palette_tokens)
        if not nodes:
            self._notes.setText(t("diagram.emptyHint"))
            return
        self.canvas.load(nodes, edges)
        notes = payload.get("notes") or []
        self._notes.setText("  ".join(str(n) for n in notes))

    def _generate(self) -> None:
        analysis_id = self.window.current_analysis_id
        if analysis_id is None or self.kind.currentData() is None:
            self.show_error(t("diagram.emptyHint"))
            return
        self.fetch(
            self.api.diagram_generate,
            {"analysis_id": analysis_id, "kind": self.kind.currentData()},
            on_done=lambda _: self.refresh(),
        )

    def _ask(self) -> None:
        prompt = self.prompt.text().strip()
        analysis_id = self.window.current_analysis_id
        if not prompt or analysis_id is None:
            return
        self._notes.setText(t("ai.thinking"))
        self.fetch(
            self.api.ai_query,
            {"analysis_id": analysis_id, "prompt": prompt, "language": self.window.prefs.language},
            on_done=lambda _: (self.prompt.clear(), self.refresh()),
        )
