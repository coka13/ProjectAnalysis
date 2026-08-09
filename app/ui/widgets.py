"""Small reusable pieces shared by every view.

These exist so a view never repeats layout or styling decisions; the spacing,
radii and colours all come from :mod:`app.ui.theme`.
"""

from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui import theme
from app.ui.icons import icon as make_icon


def label(text: str, *, role: str = "", wrap: bool = False, align: Qt.AlignmentFlag | None = None) -> QLabel:
    """A text label; `role` selects the size and colour from the stylesheet."""
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    if align is not None:
        widget.setAlignment(align)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def button(
    text: str,
    *,
    variant: str = "default",
    on_click: Callable[[], None] | None = None,
    icon_name: str = "",
    colour: str = "",
    tooltip: str = "",
) -> QPushButton:
    widget = QPushButton(text)
    widget.setProperty("variant", variant)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        widget.setIcon(make_icon(icon_name, colour or "#e8edf5"))
    if tooltip:
        widget.setToolTip(tooltip)
    if on_click is not None:
        widget.clicked.connect(lambda: on_click())
    return widget


class ElidedLabel(QLabel):
    """A single line that shortens itself to fit.

    Elision happens while painting, because a widget's real font is only known
    once the stylesheet has reached it - measuring in the constructor gives the
    default font and the text ends up clipped instead of shortened.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setToolTip(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._full = text
        self.setToolTip(text)
        super().setText(text)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, self.width())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        painter.drawText(self.rect(), int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), elided)
        painter.end()


class SearchField(QLineEdit):
    """The search box, with its keyboard hint drawn inside the trailing edge."""

    def __init__(self, hint: str, tokens: "theme.Palette", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hint = hint
        self._tokens = tokens
        self._reserve_space()

    def set_tokens(self, tokens: "theme.Palette") -> None:
        self._tokens = tokens
        self._reserve_space()
        self.update()

    def _chip_rect(self) -> QRectF:
        metrics = QFontMetricsF(self.font())
        width = metrics.horizontalAdvance(self._hint) + 14
        height = metrics.height() + 2
        return QRectF(self.width() - width - 8, (self.height() - height) / 2, width, height)

    def _reserve_space(self) -> None:
        """Keep the text and caret clear of the chip.

        Done on resize rather than while painting: changing a widget property
        during a paint schedules another paint, which loops and flickers.
        """
        self.setTextMargins(0, 0, int(self._chip_rect().width()) + 12, 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._reserve_space()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        box = self._chip_rect()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(self._tokens.line), 1))
        painter.setBrush(QColor(self._tokens.surface_3))
        painter.drawRoundedRect(box, theme.R_XS, theme.R_XS)
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(self._tokens.text_3))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), self._hint)
        painter.end()


def badge(text: str, tone: str, tokens: "theme.Palette") -> QLabel:
    """A pill, matching the `.badge` rule: soft fill, full radius, 11px bold."""
    tints = {
        "ok": (tokens.ok_soft, tokens.ok),
        "info": (tokens.info_soft, tokens.info),
        "warn": (tokens.warn_soft, tokens.warn),
        "err": (tokens.danger_soft, tokens.danger),
        "muted": (tokens.muted_soft, tokens.text_2),
    }
    background, colour = tints.get(tone, tints["muted"])
    widget = QLabel(text)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.setStyleSheet(
        f"background: {background}; color: {colour}; border-radius: {theme.R_FULL}px;"
        f" padding: 2px {theme.S[2]}px; font-size: {theme.F_XS}px; font-weight: 600;"
    )
    return widget


def row(*widgets: QWidget, spacing: int = theme.S[3], stretch_last: bool = False) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    if not stretch_last:
        layout.addStretch(1)
    return holder


def column(*widgets: QWidget, spacing: int = theme.S[3]) -> QWidget:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    return holder


class Card(QFrame):
    """A titled surface - the unit every view is built from."""

    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        # .card: 20px padding, 12px between rows.
        self._layout.setContentsMargins(theme.S[5], theme.S[5], theme.S[5], theme.S[5])
        self._layout.setSpacing(theme.S[3])
        if title:
            self._layout.addWidget(label(title, role="h2"))
        if subtitle:
            self._layout.addWidget(label(subtitle, role="muted", wrap=True))

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    def add_stretch(self) -> None:
        self._layout.addStretch(1)

    def body(self) -> QVBoxLayout:
        return self._layout


class Page(QScrollArea):
    """A scrollable view body with the standard page padding.

    Scrolling is owned here so no view has to think about small windows; the
    content keeps its natural height and never clips.
    """

    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._inner = QWidget()
        self._layout = QVBoxLayout(self._inner)
        # .content: 24px padding on every side.
        self._layout.setContentsMargins(theme.S[6], theme.S[6], theme.S[6], theme.S[6])
        self._layout.setSpacing(theme.S[4])
        self.setWidget(self._inner)

        self._heading = label(title, role="h1")
        self._sub = label(subtitle, role="muted", wrap=True)
        if title:
            # The heading shares its row with any page-level action, as the
            # original does, instead of pushing it onto a line of its own.
            header = QWidget()
            self._header = QHBoxLayout(header)
            self._header.setContentsMargins(0, 0, 0, 0)
            self._header.setSpacing(theme.S[3])
            self._header.addWidget(self._heading)
            self._header.addStretch(1)
            self._layout.addWidget(header)
        if subtitle:
            self._layout.addWidget(self._sub)

    def add_header_action(self, widget: QWidget) -> QWidget:
        """Place a control at the end of the heading row."""
        self._header.addWidget(widget)
        return widget

    def set_heading(self, title: str, subtitle: str = "") -> None:
        self._heading.setText(title)
        self._sub.setText(subtitle)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    def add_stretch(self) -> None:
        self._layout.addStretch(1)


class Grid(QWidget):
    """A responsive row of cards that wraps instead of clipping."""

    def __init__(self, columns: int = 4, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QGridLayout

        self._columns = max(1, columns)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(theme.S[4])
        self._count = 0

    def add(self, widget: QWidget) -> None:
        self._grid.addWidget(widget, self._count // self._columns, self._count % self._columns)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._count += 1

    def add_all(self, widgets: Iterable[QWidget]) -> None:
        for widget in widgets:
            self.add(widget)


class Stat(QFrame):
    """A single headline number with its caption."""

    def __init__(self, caption: str, value: str, *, tone: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.S[4], theme.S[4], theme.S[4], theme.S[4])
        layout.setSpacing(theme.S[1])
        self._value = label(value, role="h2")
        if tone:
            self._value.setStyleSheet(f"color: {tone};")
        layout.addWidget(label(caption, role="dim"))
        layout.addWidget(self._value)

    def set_value(self, value: str) -> None:
        self._value.setText(value)
