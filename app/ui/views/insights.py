"""AI insights: an explanation of the architecture, and a place to ask."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QTextBrowser

from app.ui import theme
from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, button, label

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

# The sections an architecture review returns, in reading order.
SECTIONS = (
    ("summary", "ai.description"),
    ("strengths", "ai.strengths"),
    ("issues", "ai.issues"),
    ("recommendations", "ai.recommendations"),
    ("quick_wins", "ai.improvements"),
)


class InsightsView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.insights", "ai.subtitle")

        self.add_header_action(
            button(t("ai.explain"), variant="primary", icon_name="sparkle", on_click=self.refresh)
        )

        self.notice = label("", role="dim", wrap=True)
        self.add(self.notice)

        narrative = Card(t("ai.interpretation"))
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(False)
        self.body.setMinimumHeight(420)
        self.body.setStyleSheet("background: transparent; border: none;")
        narrative.add(self.body, 1)
        self.add(narrative, 1)

    def load(self) -> None:
        analysis_id = self.window.current_analysis_id
        if analysis_id is None:
            self.body.setPlainText(t("score.unavailable"))
            return
        self.body.setPlainText(t("ai.thinking"))
        # ai_explain describes a single diagram; the architecture-wide reading
        # of an analysis is ai_review.
        self.fetch(
            self.api.ai_review,
            {"analysis_id": analysis_id, "language": self.window.prefs.language},
            on_done=self._show,
        )

    def _show(self, payload: Any) -> None:
        data = payload or {}
        if data.get("source") == "static":
            self.notice.setText(t("ai.fallbackNotice"))

        tokens = self.window.palette_tokens
        blocks = []
        for key, title_key in SECTIONS:
            value = data.get(key)
            if not value:
                continue
            if isinstance(value, (list, tuple)):
                items = "".join(f"<li>{_escape(_line(v))}</li>" for v in value)
                content = f"<ul style='margin:0 0 12px 18px'>{items}</ul>"
            else:
                content = f"<p style='margin:0 0 12px'>{_escape(str(value))}</p>"
            blocks.append(
                f"<h3 style='color:{tokens.text};font-size:{theme.F_LG}px;margin:0 0 6px'>"
                f"{_escape(t(title_key))}</h3>{content}"
            )

        if not blocks:
            self.body.setPlainText(str(data.get("summary") or data.get("text") or t("common.none")))
            return
        self.body.setHtml(
            f"<div style='color:{tokens.text_2};font-size:{theme.F_MD}px;"
            f"font-family:{theme.FONT_STACK}'>{''.join(blocks)}</div>"
        )


def _line(value: object) -> str:
    """Findings arrive as dicts; show the sentence rather than the structure."""
    if isinstance(value, dict):
        return str(value.get("title") or value.get("detail") or value.get("text") or value)
    return str(value)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
