"""Dashboard: how healthy the codebase is, and what to fix first."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QHBoxLayout, QWidget

from app.graph import scoring
from app.ui import theme
from app.ui.charts import DonutChart, RadarChart, Slice
from app.ui.i18n import translator as t
from app.ui.score_band import ScoreBand
from app.ui.views.base import DataView
from app.ui.widgets import Card, button, label

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

# A stable colour per language so the composition ring keeps its key.
LANGUAGE_TONES = ("accent", "ok", "warn", "danger", "info", "accent_2", "text_3")


class DashboardView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.dashboard", "dash.subtitle")
        tokens = window.palette_tokens

        self.add_header_action(
            button(t("dash.openScorecard"), on_click=lambda: self.window.navigate("scorecard"))
        )

        self.band = ScoreBand(tokens)
        self.add(self.band)

        lower = QWidget()
        lower_layout = QHBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(theme.S[4])

        balance = Card(t("score.balance"), t("score.balanceHint"))
        self.radar = RadarChart(tokens)
        balance.add(self.radar, 1)
        lower_layout.addWidget(balance, 1)

        composition = Card(t("dash.composition"))
        self.donut = DonutChart(tokens)
        composition.add(self.donut, 1)
        self.legend = label("", role="dim", wrap=True)
        composition.add(self.legend)
        lower_layout.addWidget(composition, 1)

        self.add(lower, 1)

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
        self.radar.set_tokens(tokens)
        self.donut.set_tokens(tokens)

        index = card.get("category_index") or {}
        self.radar.set_data(
            [
                Slice(t(f"score.cat.{key}"), float(index.get(key) or 0), tokens.accent)
                for key in scoring.CATEGORY_ORDER
                if key in index
            ]
        )

        stats = data.get("stats") or {}
        ordered = sorted((stats.get("languages") or {}).items(), key=lambda kv: -int(kv[1] or 0))
        self.donut.set_data(
            [
                Slice(str(name), float(count or 0), getattr(tokens, LANGUAGE_TONES[i % len(LANGUAGE_TONES)]))
                for i, (name, count) in enumerate(ordered)
            ],
            total=str(stats.get("files_analyzed", 0)),
            caption=t("analysis.files"),
        )
        self.legend.setText("   ".join(f"{name} {count}" for name, count in ordered[:8]))
