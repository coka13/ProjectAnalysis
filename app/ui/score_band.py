"""The overall-score band, shared by the dashboard and the scorecard.

Both screens open with the same block - gauge, narrative and four figures - so
it is defined once here rather than assembled twice.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from app.ui import motion, theme
from app.ui.charts import ScoreGauge
from app.ui.i18n import translator as t
from app.ui.widgets import Card, label


class Tile(QFrame):
    """One of the four figures beside the gauge."""

    def __init__(self, caption: str, tone: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.S[4], theme.S[3], theme.S[4], theme.S[3])
        layout.setSpacing(2)
        self._value = label("—", role="h2")
        self._value.setStyleSheet(f"color: {tone};")
        layout.addWidget(self._value)
        layout.addWidget(label(caption.upper(), role="dim"))

    def set_value(self, value: str) -> None:
        self._value.setText(value)

    def set_tone(self, tone: str) -> None:
        self._value.setStyleSheet(f"color: {tone};")


class ScoreBand(Card):
    """Gauge, headline and the four score figures."""

    def __init__(self, tokens: theme.Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)
        band = QWidget()
        layout = QHBoxLayout(band)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.S[6])

        self.gauge = ScoreGauge(tokens)
        self.gauge.setFixedSize(300, 250)
        layout.addWidget(self.gauge, 0, Qt.AlignmentFlag.AlignTop)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, theme.S[2], 0, 0)
        side_layout.setSpacing(theme.S[4])
        side_layout.addWidget(label(t("score.overallTitle"), role="h2"))
        self.headline = label(t("common.loading"), role="muted", wrap=True)
        side_layout.addWidget(self.headline)

        tiles = QWidget()
        tiles_layout = QHBoxLayout(tiles)
        tiles_layout.setContentsMargins(0, 0, 0, 0)
        tiles_layout.setSpacing(theme.S[4])
        self.tiles = {
            "current": Tile(t("score.current"), tokens.text),
            "potential": Tile(t("score.potential"), tokens.ok),
            "confidence": Tile(t("score.confidence"), tokens.text),
            "actions": Tile(t("score.actions"), tokens.text),
        }
        for tile in self.tiles.values():
            tiles_layout.addWidget(tile)
        side_layout.addWidget(tiles)
        side_layout.addStretch(1)
        layout.addWidget(side, 1)
        self.add(band)

    def show_card(self, card: dict[str, Any], tokens: theme.Palette) -> None:
        overall = float(card.get("overall") or 0)
        grade = f"{t('score.grade')} {card.get('grade', '')}"
        caption = t("score.outOf", max=100)

        self.gauge.set_tokens(tokens)
        self.gauge.set_value(overall, grade, caption)
        motion.count_to(
            self.gauge, 0.0, overall, lambda value: self.gauge.set_value(value, grade, caption)
        )
        self.headline.setText(str(card.get("headline") or ""))

        potential = float(card.get("potential_score") or overall)
        self.tiles["current"].set_value(f"{overall:.0f} / 100")
        self.tiles["current"].set_tone(theme.score_colour(overall, tokens))
        self.tiles["potential"].set_value(f"+{max(0, round(potential - overall))}")
        self.tiles["confidence"].set_value(f"{round(float(card.get('confidence') or 0) * 100)}%")
        self.tiles["actions"].set_value(str(len((card.get("roadmap") or {}).get("all") or [])))
