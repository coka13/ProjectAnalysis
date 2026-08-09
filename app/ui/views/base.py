"""Shared base for views that load their data from the API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from app.ui import workers
from app.ui.i18n import translator as t
from app.ui.widgets import Page

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

log = logging.getLogger("aai.ui.views")


class DataView(Page):
    """A page that fetches on demand and reports failures in place.

    Subclasses implement :meth:`load`, calling :meth:`fetch` for each API call
    so no view ever blocks the window or swallows an error.
    """

    def __init__(self, window: "MainWindow", title_key: str, subtitle_key: str = "") -> None:
        super().__init__(t(title_key), t(subtitle_key) if subtitle_key else "")
        self.window = window
        self.api = window.api
        self._loaded = False

    def refresh(self) -> None:
        self.load()
        self._loaded = True

    def load(self) -> None:  # pragma: no cover - overridden by every view
        raise NotImplementedError

    def fetch(
        self,
        call: Callable[..., Any],
        payload: dict | None = None,
        *,
        on_done: Callable[[Any], None],
    ) -> None:
        """Run an API call off the UI thread and hand back its data."""

        def deliver(envelope: Any) -> None:
            try:
                on_done(workers.unwrap(envelope))
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                log.exception("view could not handle a result")
                self.show_error(str(exc))

        workers.run(call, payload or {}, owner=self, on_done=deliver, on_error=self.show_error)

    def show_error(self, message: str) -> None:
        self.window.notify_error(message)
