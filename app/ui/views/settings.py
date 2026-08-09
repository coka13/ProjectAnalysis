"""Settings: appearance, the AI provider, score weights and where data lives."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QFormLayout,
    QLineEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.ui import prefs as prefs_store
from app.ui import theme
from app.ui.i18n import translator as t
from app.ui.views.base import DataView
from app.ui.widgets import Card, Segmented, button, field, label

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

LANGUAGE_NAMES = {"en": "English", "he": "עברית"}
SCALE_LABELS = (("0.9", "A-"), ("1.0", "A"), ("1.12", "A+"), ("1.25", "A++"))


class SettingsView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.settings", "settings.subtitle")

        self.tabs = QTabWidget()
        self.tabs.addTab(self._appearance(), t("settings.appearance"))
        self.tabs.addTab(self._provider(), t("provider.title"))
        self.tabs.addTab(self._weights(), t("score.weights"))
        self.tabs.addTab(self._storage(), t("settings.storage"))
        self.add(self.tabs, 1)

    # ------------------------------------------------------------ appearance
    def _appearance(self) -> QWidget:
        prefs = self.window.prefs
        card = Card(t("settings.appearance"))

        card.add(
            field(
                t("common.theme"),
                Segmented(
                    [("dark", t("settings.dark")), ("light", t("settings.light"))],
                    prefs.theme,
                    lambda value: self._apply("theme", value),
                ),
            )
        )
        card.add(
            field(
                t("settings.contrast"),
                Segmented(
                    [("normal", t("settings.normal")), ("high", t("settings.high"))],
                    prefs.contrast,
                    lambda value: self._apply("contrast", value),
                ),
            )
        )
        card.add(
            field(
                t("settings.palette"),
                Segmented(
                    [("default", t("settings.paletteDefault")), ("cb", t("settings.paletteCb"))],
                    prefs.palette,
                    lambda value: self._apply("palette", value),
                ),
            )
        )
        card.add(
            field(
                t("settings.motion"),
                Segmented(
                    [("full", t("settings.motionFull")), ("reduced", t("settings.motionReduced"))],
                    prefs.motion,
                    lambda value: self._apply("motion", value),
                ),
            )
        )
        card.add(
            field(
                t("settings.textSize"),
                Segmented(
                    list(SCALE_LABELS),
                    str(prefs.scale),
                    lambda value: self._apply("scale", float(value)),
                ),
            )
        )
        card.add(
            field(
                t("common.language"),
                Segmented(
                    [(code, LANGUAGE_NAMES[code]) for code in ("en", "he")],
                    prefs.language,
                    lambda value: self._apply("language", value),
                ),
            )
        )
        card.add(
            field(
                t("shortcuts.title"),
                button(t("shortcuts.title"), on_click=self.window.show_shortcuts),
            )
        )
        return _scrolled(card)

    def _apply(self, name: str, value: Any) -> None:
        """Choosing a pill is the action; there is nothing to save afterwards."""
        from PySide6.QtWidgets import QApplication

        from app.ui.main import apply_appearance

        setattr(self.window.prefs, name, value)
        self.window.prefs = self.window.prefs.normalised()
        prefs_store.save(self.window.prefs)
        app = QApplication.instance()
        if app is not None:
            apply_appearance(app, self.window)

    # -------------------------------------------------------------- provider
    def _provider(self) -> QWidget:
        card = Card(t("provider.title"), t("provider.hint"))
        holder = QWidget()
        holder.setObjectName("Plain")
        form = QFormLayout(holder)
        form.setSpacing(theme.S[3])

        self.base_url = QLineEdit()
        self.model = QLineEdit()
        self.api_key = QLineEdit()
        # The stored credential is masked; leaving it blank keeps it.
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText(t("provider.apiKeyKeep"))
        form.addRow(t("provider.baseUrl"), self.base_url)
        form.addRow(t("provider.model"), self.model)
        form.addRow(t("provider.apiKey"), self.api_key)
        card.add(holder)

        self._provider_state = label("", role="dim", wrap=True)
        card.add(self._provider_state)
        card.add(
            button(t("common.save"), variant="primary", on_click=self._save_provider)
        )
        card.add(button(t("provider.test"), on_click=self._test_provider))
        card.add(button(t("provider.clear"), variant="danger", on_click=self._clear_provider))
        return _scrolled(card)

    def _save_provider(self) -> None:
        payload = {
            "base_url": self.base_url.text().strip(),
            "model": self.model.text().strip(),
        }
        if self.api_key.text().strip():
            payload["api_key"] = self.api_key.text().strip()
        self.fetch(self.api.provider_save, payload, on_done=lambda _: self.refresh())

    def _test_provider(self) -> None:
        self._provider_state.setText(t("ai.thinking"))
        self.fetch(
            self.api.provider_test,
            on_done=lambda data: self._provider_state.setText(str((data or {}).get("message") or t("common.saved"))),
        )

    def _clear_provider(self) -> None:
        self.fetch(self.api.provider_clear, on_done=lambda _: self.refresh())

    # --------------------------------------------------------------- weights
    def _weights(self) -> QWidget:
        card = Card(t("score.weights"), t("score.weightsHint"))
        self._weights_body = label(t("common.loading"), role="muted", wrap=True)
        card.add(self._weights_body)
        card.add(button(t("score.resetWeights"), on_click=self._reset_weights))
        return _scrolled(card)

    def _reset_weights(self) -> None:
        self.fetch(self.api.score_weights_reset, on_done=lambda _: self.refresh())

    # --------------------------------------------------------------- storage
    def _storage(self) -> QWidget:
        card = Card(t("settings.storage"))
        self._storage_body = label(t("common.loading"), role="muted", wrap=True)
        card.add(self._storage_body)
        return _scrolled(card)

    # ------------------------------------------------------------------ data
    def load(self) -> None:
        self.fetch(self.api.settings_summary, on_done=self._show_storage)
        self.fetch(self.api.provider_get, on_done=self._show_provider)
        self.fetch(self.api.score_weights, on_done=self._show_weights)

    def _show_storage(self, summary: Any) -> None:
        data = summary or {}
        lines = [
            f"{t('settings.dataDir')}: {data.get('data_dir', '')}",
            f"{t('settings.database')}: {data.get('database', data.get('database_url', ''))}",
        ]
        lines += [
            f"{key}: {value}"
            for key, value in data.items()
            if key not in ("data_dir", "database", "database_url") and not isinstance(value, (dict, list))
        ]
        self._storage_body.setText("\n".join(lines))

    def _show_provider(self, data: Any) -> None:
        provider = data or {}
        self.base_url.setText(str(provider.get("base_url") or ""))
        self.model.setText(str(provider.get("model") or ""))
        self._provider_state.setText(
            "" if provider.get("configured") else t("provider.none")
        )

    def _show_weights(self, data: Any) -> None:
        weights = (data or {}).get("weights") or data or {}
        self._weights_body.setText(
            "\n".join(f"{t(f'score.cat.{key}')}: {round(float(value) * 100)}%" for key, value in weights.items())
            or t("common.none")
        )


def _scrolled(card: Card) -> QWidget:
    """Tabs hold a single card, aligned to the top rather than stretched."""
    holder = QWidget()
    holder.setObjectName("Plain")
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, theme.S[4], 0, 0)
    layout.addWidget(card)
    layout.addStretch(1)
    return holder
