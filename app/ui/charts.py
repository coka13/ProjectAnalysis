"""Charts, drawn directly rather than through a plotting library.

Painting them here is what guarantees the two properties the old charts kept
breaking: every label stays inside the widget at any size, and the geometry is
mirrored rather than reversed when the interface is read right to left.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.ui import theme

# Qt measures arc angles in sixteenths of a degree.
DEG = 16


@dataclass(frozen=True)
class Slice:
    """One labelled value in a bar or donut."""

    label: str
    value: float
    colour: str


class ChartBase(QWidget):
    """Shared painting setup: palette, antialiasing and reading direction."""

    def __init__(self, tokens: theme.Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tokens = tokens
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def set_tokens(self, tokens: theme.Palette) -> None:
        self.tokens = tokens
        self.update()

    @property
    def mirrored(self) -> bool:
        return self.layoutDirection() == Qt.LayoutDirection.RightToLeft

    def _painter(self) -> QPainter:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        return painter

    def _font(self, size: int, *, bold: bool = False) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(size)
        font.setBold(bold)
        return font


class ScoreGauge(ChartBase):
    """The headline score: a 270 degree arc, the number and its grade."""

    THICKNESS = 22
    START = 225  # bottom-left, sweeping clockwise over the top
    SWEEP = 270

    def __init__(self, tokens: theme.Palette, parent: QWidget | None = None) -> None:
        super().__init__(tokens, parent)
        self._score = 0.0
        self._grade = ""
        self._caption = ""
        self.setMinimumSize(260, 240)

    def set_value(self, score: float, grade: str, caption: str = "") -> None:
        self._score = max(0.0, min(float(score), 100.0))
        self._grade = grade
        self._caption = caption
        self.update()

    def _tone(self) -> str:
        return theme.score_colour(self._score, self.tokens)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = self._painter()
        side = min(self.width(), self.height())
        if side <= self.THICKNESS:
            painter.end()
            return
        box = QRectF(
            (self.width() - side) / 2 + self.THICKNESS / 2,
            (self.height() - side) / 2 + self.THICKNESS / 2,
            side - self.THICKNESS,
            side - self.THICKNESS,
        )

        track = QPen(QColor(self.tokens.surface_3), self.THICKNESS)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawArc(box, self.START * DEG, -self.SWEEP * DEG)

        if self._score > 0:
            span = int(self.SWEEP * DEG * (self._score / 100.0))
            arc = QPen(QColor(self._tone()), self.THICKNESS)
            arc.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc)
            # Mirrored layouts sweep the other way so progress still reads forwards.
            if self.mirrored:
                painter.drawArc(box, (self.START - self.SWEEP) * DEG, span)
            else:
                painter.drawArc(box, self.START * DEG, -span)

        centre = box.center()
        painter.setPen(QColor(self._tone()))
        painter.setFont(self._font(theme.F_4XL, bold=True))
        painter.drawText(
            QRectF(box.left(), centre.y() - 40, box.width(), 52),
            int(Qt.AlignmentFlag.AlignCenter),
            f"{self._score:.0f}",
        )

        if self._caption:
            painter.setPen(QColor(self.tokens.text_3))
            painter.setFont(self._font(theme.F_SM))
            painter.drawText(
                QRectF(box.left(), centre.y() + 12, box.width(), 18),
                int(Qt.AlignmentFlag.AlignCenter),
                self._caption,
            )
        if self._grade:
            painter.setPen(QColor(self._tone()))
            painter.setFont(self._font(theme.F_SM, bold=True))
            painter.drawText(
                QRectF(box.left(), centre.y() + 32, box.width(), 20),
                int(Qt.AlignmentFlag.AlignCenter),
                self._grade,
            )
        painter.end()


class DonutChart(ChartBase):
    """Composition as a ring, with the total in the middle."""

    THICKNESS = 34

    def __init__(self, tokens: theme.Palette, parent: QWidget | None = None) -> None:
        super().__init__(tokens, parent)
        self._slices: list[Slice] = []
        self._total_label = ""
        self._total_caption = ""
        self.setMinimumHeight(300)

    def set_data(self, slices: list[Slice], total: str = "", caption: str = "") -> None:
        self._slices = [s for s in slices if s.value > 0]
        self._total_label, self._total_caption = total, caption
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = self._painter()
        side = min(self.width(), self.height())
        if side <= self.THICKNESS or not self._slices:
            painter.end()
            return
        box = QRectF(
            (self.width() - side) / 2 + self.THICKNESS / 2,
            (self.height() - side) / 2 + self.THICKNESS / 2,
            side - self.THICKNESS,
            side - self.THICKNESS,
        )

        total = sum(s.value for s in self._slices) or 1.0
        angle = 90 * DEG  # twelve o'clock
        for item in self._slices:
            span = int(-360 * DEG * (item.value / total))
            pen = QPen(QColor(item.colour), self.THICKNESS)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawArc(box, angle, span)
            angle += span

        centre = box.center()
        if self._total_label:
            painter.setPen(QColor(self.tokens.text))
            painter.setFont(self._font(theme.F_3XL, bold=True))
            painter.drawText(
                QRectF(box.left(), centre.y() - 34, box.width(), 44),
                int(Qt.AlignmentFlag.AlignCenter),
                self._total_label,
            )
        if self._total_caption:
            painter.setPen(QColor(self.tokens.text_2))
            painter.setFont(self._font(theme.F_LG))
            painter.drawText(
                QRectF(box.left(), centre.y() + 8, box.width(), 26),
                int(Qt.AlignmentFlag.AlignCenter),
                self._total_caption,
            )
        painter.end()


class RadarChart(ChartBase):
    """Category balance: one spoke per category on a polygonal web."""

    RINGS = 4

    def __init__(self, tokens: theme.Palette, parent: QWidget | None = None) -> None:
        super().__init__(tokens, parent)
        self._slices: list[Slice] = []
        self.setMinimumHeight(340)

    def set_data(self, slices: list[Slice]) -> None:
        self._slices = slices
        self.update()

    def _web_point(self, centre: QPointF, radius: float, index: int, count: int, ratio: float) -> QPointF:
        # Start at twelve o'clock; mirrored layouts run anticlockwise.
        step = 2 * math.pi / max(1, count)
        angle = -math.pi / 2 + (-index if self.mirrored else index) * step
        return QPointF(
            centre.x() + math.cos(angle) * radius * ratio,
            centre.y() + math.sin(angle) * radius * ratio,
        )

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        if len(self._slices) < 3:
            return
        painter = self._painter()
        painter.setFont(self._font(theme.F_SM))
        metrics = QFontMetricsF(painter.font())

        # Reserve room for the widest label so no spoke caption is cut off.
        margin = max(metrics.horizontalAdvance(s.label) for s in self._slices) / 2 + 18
        centre = QPointF(self.width() / 2, self.height() / 2)
        radius = max(30.0, min(self.width() / 2 - margin, self.height() / 2 - 28))
        count = len(self._slices)

        painter.setPen(QPen(QColor(self.tokens.line_soft), 1))
        for ring in range(1, self.RINGS + 1):
            web = QPolygonF(
                [self._web_point(centre, radius, i, count, ring / self.RINGS) for i in range(count)]
            )
            painter.drawPolygon(web)
        for index in range(count):
            painter.drawLine(centre, self._web_point(centre, radius, index, count, 1.0))

        shape = QPolygonF(
            [
                self._web_point(centre, radius, i, count, max(0.0, min(s.value, 100.0)) / 100.0)
                for i, s in enumerate(self._slices)
            ]
        )
        fill = QColor(self.tokens.accent)
        fill.setAlpha(60)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(QColor(self.tokens.accent), 2))
        painter.drawPolygon(shape)

        painter.setBrush(QBrush(QColor(self.tokens.accent)))
        for point in shape:
            painter.drawEllipse(point, 3.5, 3.5)

        painter.setPen(QColor(self.tokens.text_2))
        for index, item in enumerate(self._slices):
            anchor = self._web_point(centre, radius + 16, index, count, 1.0)
            width = metrics.horizontalAdvance(item.label) + 8
            painter.drawText(
                QRectF(anchor.x() - width / 2, anchor.y() - 10, width, 20),
                int(Qt.AlignmentFlag.AlignCenter),
                item.label,
            )
        painter.end()


class BarChart(ChartBase):
    """Horizontal bars with the label and value always readable.

    The label column is measured from the longest string, so nothing is ever
    elided or pushed outside the widget.
    """

    ROW_H = 30
    GAP = 8

    def __init__(self, tokens: theme.Palette, maximum: float = 100.0, parent: QWidget | None = None) -> None:
        super().__init__(tokens, parent)
        self._slices: list[Slice] = []
        self._maximum = maximum

    def set_data(self, slices: list[Slice], maximum: float | None = None) -> None:
        self._slices = slices
        if maximum:
            self._maximum = maximum
        self.setMinimumHeight(max(1, len(slices)) * (self.ROW_H + self.GAP) + self.GAP)
        self.updateGeometry()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        if not self._slices:
            return
        painter = self._painter()
        painter.setFont(self._font(theme.F_SM))
        metrics = QFontMetricsF(painter.font())

        label_w = max(metrics.horizontalAdvance(s.label) for s in self._slices) + 12
        value_w = max(metrics.horizontalAdvance(f"{s.value:.0f}") for s in self._slices) + 12
        track_w = max(40.0, self.width() - label_w - value_w - 16)
        mirrored = self.mirrored

        y = float(self.GAP)
        for item in self._slices:
            label_x = self.width() - label_w if mirrored else 0.0
            painter.setPen(QColor(self.tokens.text_2))
            painter.drawText(
                QRectF(label_x, y, label_w, self.ROW_H),
                int(
                    (Qt.AlignmentFlag.AlignRight if mirrored else Qt.AlignmentFlag.AlignLeft)
                    | Qt.AlignmentFlag.AlignVCenter
                ),
                item.label,
            )

            track_x = label_w + 8 if not mirrored else value_w + 8
            bar_box = QRectF(track_x, y + 9, track_w, 12)
            path = QPainterPath()
            path.addRoundedRect(bar_box, 6, 6)
            painter.fillPath(path, QColor(self.tokens.line))

            ratio = 0.0 if self._maximum <= 0 else max(0.0, min(item.value / self._maximum, 1.0))
            filled_w = track_w * ratio
            if filled_w > 0:
                filled = QRectF(
                    bar_box.right() - filled_w if mirrored else bar_box.left(),
                    bar_box.top(),
                    filled_w,
                    bar_box.height(),
                )
                fill = QPainterPath()
                fill.addRoundedRect(filled, 6, 6)
                painter.fillPath(fill, QColor(item.colour))

            painter.setPen(QColor(self.tokens.text))
            value_x = 0.0 if mirrored else label_w + 8 + track_w + 8
            painter.drawText(
                QRectF(value_x, y, value_w, self.ROW_H),
                int(
                    (Qt.AlignmentFlag.AlignLeft if mirrored else Qt.AlignmentFlag.AlignRight)
                    | Qt.AlignmentFlag.AlignVCenter
                ),
                f"{item.value:.0f}",
            )
            y += self.ROW_H + self.GAP
        painter.end()


class LineChart(ChartBase):
    """A trend line with a gridded plot area and end labels."""

    PAD_L, PAD_R, PAD_T, PAD_B = 44, 16, 16, 28

    def __init__(self, tokens: theme.Palette, parent: QWidget | None = None) -> None:
        super().__init__(tokens, parent)
        self._points: list[tuple[str, float]] = []
        self.setMinimumHeight(240)

    def set_data(self, points: list[tuple[str, float]]) -> None:
        self._points = points
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = self._painter()
        painter.setFont(self._font(theme.F_XS))
        mirrored = self.mirrored

        plot = QRectF(
            self.PAD_R if mirrored else self.PAD_L,
            self.PAD_T,
            max(10.0, self.width() - self.PAD_L - self.PAD_R),
            max(10.0, self.height() - self.PAD_T - self.PAD_B),
        )

        painter.setPen(QPen(QColor(self.tokens.line_soft), 1))
        for step in range(5):
            y = plot.top() + plot.height() * step / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(self.tokens.text_3))
            value = 100 - step * 25
            axis_x = plot.right() + 6 if mirrored else 0
            painter.drawText(
                QRectF(axis_x, y - 9, self.PAD_L - 8, 18),
                int(
                    (Qt.AlignmentFlag.AlignLeft if mirrored else Qt.AlignmentFlag.AlignRight)
                    | Qt.AlignmentFlag.AlignVCenter
                ),
                str(value),
            )
            painter.setPen(QPen(QColor(self.tokens.line_soft), 1))

        if len(self._points) < 2:
            painter.end()
            return

        count = len(self._points)
        step_x = plot.width() / (count - 1)

        def at(index: int, value: float) -> QPointF:
            offset = plot.width() - index * step_x if mirrored else index * step_x
            return QPointF(plot.left() + offset, plot.bottom() - plot.height() * max(0.0, min(value, 100.0)) / 100.0)

        path = QPainterPath()
        for index, (_, value) in enumerate(self._points):
            point = at(index, value)
            path.moveTo(point) if index == 0 else path.lineTo(point)
        painter.setPen(QPen(QColor(self.tokens.accent), 2))
        painter.drawPath(path)

        painter.setBrush(QColor(self.tokens.accent))
        for index, (_, value) in enumerate(self._points):
            painter.drawEllipse(at(index, value), 3.0, 3.0)

        # Only the ends are labelled; intermediate ticks would collide.
        painter.setPen(QColor(self.tokens.text_3))
        for index in (0, count - 1):
            caption = self._points[index][0]
            width = 90.0
            point = at(index, self._points[index][1])
            x = min(max(point.x() - width / 2, 0.0), self.width() - width)
            painter.drawText(
                QRectF(x, self.height() - self.PAD_B + 4, width, 18),
                int(Qt.AlignmentFlag.AlignCenter),
                caption,
            )
        painter.end()
