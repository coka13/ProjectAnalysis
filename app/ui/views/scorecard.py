"""Scorecard: the overall score, then every category that makes it up."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from app.graph import scoring
from app.ui import theme
from app.ui.i18n import translator as t
from app.ui.icons import icon as make_icon
from app.ui.score_band import ScoreBand
from app.ui.views.base import DataView
from app.ui.widgets import button, label

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

# The glyph the rail uses for each category, so both places agree.
CATEGORY_ICONS = {
    "architecture": "layers",
    "code_quality": "code",
    "security": "shield",
    "testing": "beaker",
    "documentation": "book",
    "maintainability": "wrench",
    "performance": "gauge",
    "technical_debt": "hourglass",
}


def category_name(category_id: str) -> str:
    return t(f"score.cat.{category_id}")



class CategoryCard(QFrame):
    """One category: its score, weight and how many issues it carries."""

    def __init__(self, category: dict, tokens: theme.Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        score = float(category.get("score") or 0)
        tone = theme.score_colour(score, tokens)
        # The card is capped by a bar in its own band, as the original is.
        self.setStyleSheet(
            f"#Card {{ border-top: 3px solid {tone}; border-top-left-radius: 0px;"
            f" border-top-right-radius: 0px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.S[4], theme.S[4], theme.S[4], theme.S[4])
        layout.setSpacing(theme.S[2])

        head = QHBoxLayout()
        head.setSpacing(theme.S[2])
        glyph = label("")
        glyph.setPixmap(
            make_icon(CATEGORY_ICONS.get(str(category.get("id")), "info"), tokens.text_2, 16).pixmap(16, 16)
        )
        head.addWidget(glyph)
        head.addWidget(label(category_name(str(category.get("id")))))
        head.addStretch(1)
        grade = label(str(category.get("grade") or ""), role="dim")
        grade.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grade.setFixedSize(22, 20)
        grade.setStyleSheet(
            f"color: {tokens.text_2}; border: 1px solid {tokens.line}; border-radius: {theme.R_XS}px;"
        )
        head.addWidget(grade)
        layout.addLayout(head)

        value = QHBoxLayout()
        value.setSpacing(theme.S[1])
        big = label(f"{score:.0f}", role="h1")
        big.setStyleSheet(f"color: {tone};")
        value.addWidget(big)
        value.addWidget(label(t("score.outOf", max=100), role="dim"))
        value.addStretch(1)
        layout.addLayout(value)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(score))
        bar.setTextVisible(False)
        bar.setStyleSheet(
            f"QProgressBar {{ background: {tokens.surface_3}; border: none; border-radius:"
            f" {theme.R_FULL}px; height: 6px; }}"
            f" QProgressBar::chunk {{ background: {tone}; border-radius: {theme.R_FULL}px; }}"
        )
        layout.addWidget(bar)

        footer = QHBoxLayout()
        weight = float(category.get("weight") or 0)
        footer.addWidget(label(t("score.contributes", n=round(weight * 100)), role="dim"))
        footer.addStretch(1)
        issues = len(category.get("issues") or [])
        chip = label(t("score.issuesN", n=issues), role="dim")
        chip.setStyleSheet(
            f"color: {tone}; background: {tokens.muted_soft}; border-radius: {theme.R_FULL}px;"
            f" padding: 2px {theme.S[2]}px;"
        )
        footer.addWidget(chip)
        layout.addLayout(footer)


class ScorecardView(DataView):
    COLUMNS = 4

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.scorecard", "score.subtitle")
        tokens = window.palette_tokens

        self.add_header_action(
            button(t("score.weights"), icon_name="settings", on_click=lambda: self.window.navigate("settings"))
        )
        self.add_header_action(
            button(t("score.exportReport"), icon_name="download", on_click=self._export)
        )

        self.band = ScoreBand(tokens)
        self.add(self.band)

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(theme.S[4])
        self.add(self._grid_host)
        self.add_stretch()

    def load(self) -> None:
        analysis_id = self.window.current_analysis_id
        if analysis_id is None:
            self.band.headline.setText(t("score.unavailable"))
            return
        self.fetch(self.api.score_card, {"analysis_id": analysis_id}, on_done=self._show)

    def _show(self, payload: Any) -> None:
        data = payload or {}
        card = data.get("scorecard") or {}
        tokens = self.window.palette_tokens
        self.band.show_card(card, tokens)

        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        by_id = {str(c.get("id")): c for c in card.get("categories") or []}
        for position, key in enumerate(scoring.CATEGORY_ORDER):
            category = by_id.get(key)
            if category is None:
                continue
            self._grid.addWidget(
                CategoryCard(category, tokens), position // self.COLUMNS, position % self.COLUMNS
            )

    def _export(self) -> None:
        analysis_id = self.window.current_analysis_id
        if analysis_id is None:
            return
        self.fetch(
            self.api.export_bundle,
            {"analysis_id": analysis_id, "format": "markdown"},
            on_done=lambda data: self.window.notify(
                t("common.savedTo", path=str((data or {}).get("path") or ""))
            ),
        )
