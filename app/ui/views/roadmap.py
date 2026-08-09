"""Improvement plan: the actions worth taking, ordered by return."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, label

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

# The three horizons the roadmap is grouped into, with their headings.
BUCKETS = (
    ("quick_wins", "score.quickWins"),
    ("medium_term", "score.mediumTerm"),
    ("long_term", "score.longTerm"),
)


class RoadmapView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.roadmap", "score.roadmapSubtitle")

        self._summary = label("", role="muted", wrap=True)
        self.add(self._summary)

        card = Card()
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            [t("score.actions"), t("score.effort"), t("score.priority.high"), t("score.points")]
        )
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnWidth(0, 520)
        self.tree.setMinimumHeight(460)
        self.tree.itemSelectionChanged.connect(self._show_detail)
        card.add(self.tree)
        self.add(card, 1)

        self.detail = Card(t("score.howToFix"))
        self._detail_body = self.detail.add(label(t("score.noActionsHint"), role="muted", wrap=True))
        self.add(self.detail)

    def load(self) -> None:
        analysis_id = self.window.current_analysis_id
        if analysis_id is None:
            self._summary.setText(t("score.unavailable"))
            return
        self.fetch(self.api.score_card, {"analysis_id": analysis_id}, on_done=self._show)

    def _show(self, payload: Any) -> None:
        card = (payload or {}).get("scorecard") or {}
        roadmap = card.get("roadmap") or {}
        self.tree.clear()

        total = roadmap.get("total_potential_gain", 0)
        self._summary.setText(
            f"{t('score.potential')}: {card.get('potential_score', card.get('overall', 0))}   "
            f"{t('score.totalGain', n=total)}"
        )

        any_action = False
        for key, title_key in BUCKETS:
            actions = roadmap.get(key) or []
            if not actions:
                continue
            any_action = True
            parent = QTreeWidgetItem(self.tree, [t(title_key), "", "", ""])
            parent.setFirstColumnSpanned(True)
            parent.setExpanded(True)
            for action in actions:
                child = QTreeWidgetItem(
                    parent,
                    [
                        str(action.get("title", "")),
                        t(f"score.effortLevel.{action.get('effort', 'low')}"),
                        t(f"score.priority.{action.get('priority', 'low')}"),
                        f"+{action.get('overall_gain', 0)}",
                    ],
                )
                child.setData(0, 0x0100, action)  # Qt.UserRole
        if not any_action:
            self._detail_body.setText(t("score.noActions"))

    def _show_detail(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        action = items[0].data(0, 0x0100)
        if not isinstance(action, dict):
            return
        parts = [
            f"{t('score.whyItMatters')}: {action.get('why', '')}",
            f"{t('score.howToFix')}: {action.get('how', '')}",
        ]
        files = action.get("files") or []
        if files:
            parts.append(f"{t('score.files')}: " + ", ".join(str(f) for f in files))
        self._detail_body.setText("\n\n".join(parts))
