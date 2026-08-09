"""Compare: what changed in the architecture between two runs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QComboBox

from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, Grid, Stat, button, label, row

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow


class CompareView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.compare", "compare.subtitle")
        self._runs: list[dict] = []

        self.base = QComboBox()
        self.base.setMinimumWidth(240)
        self.head = QComboBox()
        self.head.setMinimumWidth(240)
        self.add(
            row(
                label(t("compare.base"), role="dim"),
                self.base,
                label(t("compare.head"), role="dim"),
                self.head,
                button(t("compare.title"), variant="primary", icon_name="compare", on_click=self._compare),
            )
        )

        self._stats = Grid(4)
        self.add(self._stats)
        self._cards: dict[str, Stat] = {}

        summary = Card(t("compare.summary"))
        self._summary = summary.add(label(t("compare.needHint"), role="muted", wrap=True))
        self.add(summary)

        highlights = Card(t("compare.highlights"))
        self._highlights = highlights.add(label("", role="muted", wrap=True))
        self.add(highlights)
        self.add_stretch()

    def load(self) -> None:
        project_id = self.window.project_picker.currentData()
        if project_id is None:
            return
        self.fetch(self.api.analyses_list, {"project_id": project_id}, on_done=self._fill_runs)

    def _fill_runs(self, runs: Any) -> None:
        self._runs = [r for r in (runs or []) if r.get("status") == "succeeded"]
        for box in (self.base, self.head):
            box.blockSignals(True)
            box.clear()
            for run in self._runs:
                when = str(run.get("finished_at") or run.get("created_at") or "")[:19].replace("T", " ")
                box.addItem(f"{run.get('ref') or '—'} · {when}", run.get("id"))
            box.blockSignals(False)
        if len(self._runs) >= 2:
            # Oldest against newest is the comparison that is almost always wanted.
            self.base.setCurrentIndex(len(self._runs) - 1)
            self.head.setCurrentIndex(0)
        else:
            self._summary.setText(f"{t('compare.need')}\n{t('compare.needHint')}")

    def _compare(self) -> None:
        base, head = self.base.currentData(), self.head.currentData()
        if base is None or head is None or base == head:
            self.show_error(t("compare.need"))
            return
        self._summary.setText(t("common.loading"))
        self.fetch(
            self.api.compare_analyses,
            {"base_analysis_id": base, "head_analysis_id": head, "language": self.window.prefs.language},
            on_done=self._show,
        )

    def _show(self, payload: Any) -> None:
        data = payload or {}
        diff = data.get("diff") or {}
        tokens = self.window.palette_tokens

        if not self._cards:
            self._cards = {
                "added": Stat(t("compare.added"), "0", tone=tokens.ok),
                "removed": Stat(t("compare.removed"), "0", tone=tokens.danger),
                "changed": Stat(t("compare.changed"), "0", tone=tokens.warn),
                "delta": Stat(t("compare.scoreDelta"), "0", tone=tokens.accent),
            }
            self._stats.add_all(self._cards.values())

        for key, source in (("added", "added"), ("removed", "removed"), ("changed", "changed")):
            value = diff.get(source)
            self._cards[key].set_value(str(len(value) if isinstance(value, (list, tuple)) else value or 0))

        delta = diff.get("score_delta") or data.get("score_delta") or 0
        self._cards["delta"].set_value(f"{float(delta):+.0f}")

        narrative = data.get("narrative") or {}
        self._summary.setText(str(narrative.get("summary") or narrative.get("text") or t("common.none")))
        points = narrative.get("highlights") or diff.get("highlights") or []
        self._highlights.setText("\n".join(f"•  {p}" for p in points) if points else t("common.none"))
