"""Motion, using the same durations and curves as the original interface.

The design system defines three transitions; they are reproduced here as Qt
easing curves so a panel or a value settles over the same interval it always
has. Every helper honours the reduced-motion preference by finishing
immediately rather than by animating faster.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    QVariantAnimation,
    Qt,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

# --t-fast, --t-base, --t-slow from the stylesheet.
FAST, BASE, SLOW = 110, 180, 320


def _curve(kind: str) -> QEasingCurve:
    """cubic-bezier(0.2, 0, 0.2, 1), and the softer curve used for --t-slow."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    if kind == "slow":  # cubic-bezier(0.16, 1, 0.3, 1)
        curve.addCubicBezierSegment(_p(0.16, 1.0), _p(0.3, 1.0), _p(1.0, 1.0))
    else:
        curve.addCubicBezierSegment(_p(0.2, 0.0), _p(0.2, 1.0), _p(1.0, 1.0))
    return curve


def _p(x: float, y: float):
    from PySide6.QtCore import QPointF

    return QPointF(x, y)


def reduced(widget: QWidget) -> bool:
    """Whether motion is switched off for this window.

    Walks the parent chain rather than calling ``QWidget.window()``: a view
    carries its own ``window`` attribute, which shadows that method.
    """
    node: QWidget | None = widget
    while node is not None:
        prefs = getattr(node, "prefs", None)
        if prefs is not None:
            return getattr(prefs, "motion", "full") == "reduced"
        node = node.parentWidget()
    return False


def fade_in(widget: QWidget, *, duration: int = BASE) -> None:
    """Bring a widget up from transparent, as a view does when it opens."""
    if reduced(widget):
        return
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(_curve("base"))
    # Dropping the effect afterwards keeps later painting unfiltered.
    animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


def count_to(
    widget: QWidget,
    start: float,
    end: float,
    apply: Callable[[float], None],
    *,
    duration: int = SLOW,
) -> None:
    """Run a number up to its value, the way the score figures arrive."""
    if reduced(widget) or start == end:
        apply(end)
        return
    animation = QVariantAnimation(widget)
    animation.setDuration(duration)
    animation.setStartValue(float(start))
    animation.setEndValue(float(end))
    animation.setEasingCurve(_curve("slow"))
    animation.valueChanged.connect(lambda value: apply(float(value)))
    animation.finished.connect(lambda: apply(end))
    animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


def slide_width(widget: QWidget, start: int, end: int, *, duration: int = BASE) -> None:
    """Animate a rail's width, as the sidebar does when it collapses."""
    if reduced(widget):
        widget.setFixedWidth(end)
        return
    animation = QPropertyAnimation(widget, b"maximumWidth", widget)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setEasingCurve(_curve("base"))
    animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
