"""Calling the application API without blocking the window.

Every API method touches the database or the filesystem, so it must not run on
the UI thread. A result is handed back through an object that lives on the UI
thread, which is what makes Qt queue the callback there - touching a widget
from a worker thread is undefined behaviour.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

log = logging.getLogger("aai.ui.workers")


class _Delivery(QObject):
    """Marshals a worker result onto the thread that created this object."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        on_done: Callable[[Any], None] | None,
        on_error: Callable[[str], None] | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_done = on_done
        self._on_error = on_error
        # Connecting to our own bound slots is what earns a queued delivery:
        # the receiver is this object, which belongs to the UI thread.
        self.done.connect(self._deliver)
        self.failed.connect(self._fail)

    @Slot(object)
    def _deliver(self, payload: Any) -> None:
        if self._on_done is not None:
            self._on_done(payload)
        self.deleteLater()

    @Slot(str)
    def _fail(self, message: str) -> None:
        if self._on_error is not None:
            self._on_error(message)
        self.deleteLater()


class Task(QRunnable):
    """One API call, run on the shared pool."""

    def __init__(self, fn: Callable[..., Any], delivery: _Delivery, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs
        self._delivery = delivery

    @Slot()
    def run(self) -> None:
        try:
            self._delivery.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as exc:  # noqa: BLE001 - a worker must never take down the app
            log.exception("background task failed")
            self._delivery.failed.emit(f"{exc.__class__.__name__}: {exc}")


def run(
    fn: Callable[..., Any],
    *args: Any,
    owner: QObject | None = None,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> None:
    """Queue work and hand the result back on the UI thread.

    `owner` keeps the delivery alive for as long as the widget that asked for
    it exists, so a view that closes mid-flight cannot be written to.
    """
    delivery = _Delivery(on_done, on_error, parent=owner)
    QThreadPool.globalInstance().start(Task(fn, delivery, *args, **kwargs))


def unwrap(result: Any) -> Any:
    """Turn an API envelope into data, raising the message it carried."""
    if isinstance(result, dict) and "ok" in result:
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "Unknown error"))
        return result.get("data")
    return result
