"""Native diagram rendering.

Diagrams are drawn from the structured node and edge payload each generator
already produces, rather than from its Mermaid text, so the picture comes from
the same data the rest of the interface uses.

Layout is a layered (Sugiyama-style) pass: assign a rank, order each rank to
cut crossings, then place. Boxes are measured from their text, which is what
keeps a label from ever being clipped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

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
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsScene,
    QGraphicsView,
    QSizePolicy,
)

from app.ui import theme

log = logging.getLogger("aai.ui.diagram")

H_GAP = 46
V_GAP = 96
PAD_X, PAD_Y = 18, 12
MIN_W = 120
MAX_TEXT_W = 260

# Ranks fall back to this order when a node carries no layer of its own.
LAYER_ORDER = ("client", "presentation", "api", "application", "domain", "service", "data", "external")

ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.15, 6.0, 1.15


@dataclass
class Node:
    id: str
    name: str
    kind: str = ""
    layer: str = ""
    external: bool = False
    risk: bool = False
    rank: int = 0
    order: float = 0.0
    rect: QRectF = field(default_factory=QRectF)


@dataclass
class Edge:
    source: str
    target: str
    label: str = ""
    kind: str = ""


def _wrap(text: str, metrics: QFontMetricsF, limit: float) -> list[str]:
    """Break a label on spaces so a long name grows the box instead of spilling."""
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and metrics.horizontalAdvance(candidate) > limit:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


class DiagramScene(QGraphicsScene):
    """Lays out and paints one diagram."""

    def __init__(self, tokens: theme.Palette) -> None:
        super().__init__()
        self.tokens = tokens
        self._nodes: dict[str, Node] = {}
        self._edges: list[Edge] = []

    # -------------------------------------------------------------- building
    def set_data(self, nodes: Iterable[dict], edges: Iterable[dict], font: QFont) -> None:
        self._nodes = {
            str(n.get("id")): Node(
                id=str(n.get("id")),
                name=str(n.get("name") or n.get("id") or ""),
                kind=str(n.get("kind") or ""),
                layer=str(n.get("layer") or ""),
                external=bool(n.get("external")),
                risk=bool(n.get("risk")),
            )
            for n in nodes
            if n.get("id") is not None
        }
        self._edges = [
            Edge(str(e.get("source")), str(e.get("target")), str(e.get("label") or ""), str(e.get("kind") or ""))
            for e in edges
            if str(e.get("source")) in self._nodes and str(e.get("target")) in self._nodes
        ]
        self._measure(font)
        self._rank()
        self._order()
        self._place()
        self._draw(font)

    def _measure(self, font: QFont) -> None:
        metrics = QFontMetricsF(font)
        for node in self._nodes.values():
            lines = _wrap(node.name, metrics, MAX_TEXT_W)
            width = max([metrics.horizontalAdvance(line) for line in lines] + [MIN_W - 2 * PAD_X])
            height = len(lines) * metrics.height()
            node.rect = QRectF(0, 0, width + 2 * PAD_X, height + 2 * PAD_Y)

    def _rank(self) -> None:
        """Rank by declared layer, else by depth from the roots."""
        declared = {n.layer for n in self._nodes.values() if n.layer}
        if declared:
            known = [layer for layer in LAYER_ORDER if layer in declared]
            extra = sorted(declared - set(known))
            index = {layer: i for i, layer in enumerate(known + extra)}
            for node in self._nodes.values():
                node.rank = index.get(node.layer, len(index))
            return

        incoming = {key: 0 for key in self._nodes}
        for edge in self._edges:
            incoming[edge.target] += 1
        frontier = [key for key, count in incoming.items() if count == 0] or list(self._nodes)
        seen: set[str] = set()
        rank = 0
        while frontier:
            nxt: list[str] = []
            for key in frontier:
                if key in seen:
                    continue
                seen.add(key)
                self._nodes[key].rank = rank
                nxt += [e.target for e in self._edges if e.source == key and e.target not in seen]
            frontier, rank = nxt, rank + 1
            if rank > len(self._nodes):  # cycle guard
                break

    def _order(self) -> None:
        """Two barycentre passes; enough to remove most crossings cheaply."""
        ranks: dict[int, list[Node]] = {}
        for node in self._nodes.values():
            ranks.setdefault(node.rank, []).append(node)
        for group in ranks.values():
            for position, node in enumerate(sorted(group, key=lambda n: n.name)):
                node.order = float(position)

        for _ in range(2):
            for rank in sorted(ranks):
                for node in ranks[rank]:
                    neighbours = [
                        self._nodes[e.source].order for e in self._edges if e.target == node.id
                    ] + [self._nodes[e.target].order for e in self._edges if e.source == node.id]
                    if neighbours:
                        node.order = sum(neighbours) / len(neighbours)
                for position, node in enumerate(sorted(ranks[rank], key=lambda n: n.order)):
                    node.order = float(position)

    def _place(self) -> None:
        ranks: dict[int, list[Node]] = {}
        for node in self._nodes.values():
            ranks.setdefault(node.rank, []).append(node)

        widths = {
            rank: sum(n.rect.width() for n in group) + H_GAP * (len(group) - 1)
            for rank, group in ranks.items()
        }
        widest = max(widths.values(), default=0.0)

        y = 0.0
        for rank in sorted(ranks):
            group = sorted(ranks[rank], key=lambda n: n.order)
            x = (widest - widths[rank]) / 2
            tallest = max(n.rect.height() for n in group)
            for node in group:
                node.rect.moveTo(x, y + (tallest - node.rect.height()) / 2)
                x += node.rect.width() + H_GAP
            y += tallest + V_GAP

    # --------------------------------------------------------------- drawing
    def _tone(self, node: Node) -> tuple[str, str]:
        if node.risk:
            return self.tokens.danger, self.tokens.surface_3
        if node.external:
            return self.tokens.line_strong, self.tokens.surface_2
        return self.tokens.accent, self.tokens.surface

    def _draw(self, font: QFont) -> None:
        self.clear()
        self.setBackgroundBrush(QBrush(QColor(self.tokens.bg)))

        for edge in self._edges:
            self._draw_edge(edge, font)
        for node in self._nodes.values():
            self._draw_node(node, font)

        # A margin keeps the outermost strokes from touching the viewport edge.
        self.setSceneRect(self.itemsBoundingRect().adjusted(-40, -40, 40, 40))

    def _draw_node(self, node: Node, font: QFont) -> None:
        border, fill = self._tone(node)
        path = QPainterPath()
        radius = theme.R_MD
        if node.external:
            path.addRoundedRect(node.rect, radius * 2, radius * 2)
        else:
            path.addRoundedRect(node.rect, radius, radius)
        item = self.addPath(path, QPen(QColor(border), 1.6), QBrush(QColor(fill)))
        item.setZValue(1)

        text = self.addText(node.name, font)
        text.setDefaultTextColor(QColor(self.tokens.text))
        text.setTextWidth(node.rect.width() - 2 * PAD_X)
        bounds = text.boundingRect()
        text.setPos(
            node.rect.center().x() - bounds.width() / 2,
            node.rect.center().y() - bounds.height() / 2,
        )
        text.setZValue(2)
        if node.kind or node.layer:
            item.setToolTip(f"{node.name}\n{node.kind} {node.layer}".strip())

    def _draw_edge(self, edge: Edge, font: QFont) -> None:
        source, target = self._nodes[edge.source], self._nodes[edge.target]
        start = QPointF(source.rect.center().x(), source.rect.bottom())
        end = QPointF(target.rect.center().x(), target.rect.top())
        if target.rect.top() < source.rect.top():  # edge runs back up the ranks
            start = QPointF(source.rect.center().x(), source.rect.top())
            end = QPointF(target.rect.center().x(), target.rect.bottom())

        path = QPainterPath(start)
        midpoint = (start.y() + end.y()) / 2
        path.cubicTo(QPointF(start.x(), midpoint), QPointF(end.x(), midpoint), end)
        line = self.addPath(path, QPen(QColor(self.tokens.line_strong), 1.3))
        line.setZValue(0)

        self._arrow(end, start)
        if edge.label:
            self._edge_label(edge.label, path.pointAtPercent(0.5), font)

    def _edge_label(self, text: str, at: QPointF, font: QFont) -> None:
        """Draw the label on its own chip, but never on top of a node.

        Edge labels sit between the connectors and the boxes: readable against
        the background, and hidden by any node they would otherwise obscure.
        """
        caption = self.addText(text, font)
        caption.setDefaultTextColor(QColor(self.tokens.text_2))
        bounds = caption.boundingRect()
        caption.setPos(at.x() - bounds.width() / 2, at.y() - bounds.height() / 2)
        caption.setZValue(0.5)

        chip = QPainterPath()
        chip.addRoundedRect(
            QRectF(
                at.x() - bounds.width() / 2 - 4,
                at.y() - bounds.height() / 2 + 1,
                bounds.width() + 8,
                bounds.height() - 2,
            ),
            theme.R_XS,
            theme.R_XS,
        )
        backdrop = self.addPath(chip, QPen(Qt.PenStyle.NoPen), QBrush(QColor(self.tokens.bg)))
        backdrop.setZValue(0.4)

    def _arrow(self, tip: QPointF, tail: QPointF) -> None:
        direction = 1 if tip.y() >= tail.y() else -1
        head = QPolygonF(
            [tip, QPointF(tip.x() - 5, tip.y() - 9 * direction), QPointF(tip.x() + 5, tip.y() - 9 * direction)]
        )
        item = self.addPolygon(head, QPen(Qt.PenStyle.NoPen), QBrush(QColor(self.tokens.line_strong)))
        item.setZValue(1)


class DiagramView(QGraphicsView):
    """Scrolls, zooms and fits a diagram without ever clipping it."""

    def __init__(self, tokens: theme.Palette, parent=None) -> None:
        super().__init__(parent)
        self.diagram = DiagramScene(tokens)
        self.setScene(self.diagram)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(420)
        # The scene owns its own reading order, so it must not be mirrored.
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

    def load(self, nodes: Iterable[dict], edges: Iterable[dict]) -> None:
        self.diagram.set_data(nodes, edges, self.font())
        self.fit()

    def set_tokens(self, tokens: theme.Palette) -> None:
        self.diagram.tokens = tokens

    def fit(self) -> None:
        rect = self.diagram.sceneRect()
        if rect.isEmpty():
            return
        self.resetTransform()
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        # Never enlarge a small diagram past its natural size.
        if self.transform().m11() > 1.0:
            self.resetTransform()
        self.centerOn(rect.center())

    def reset_zoom(self) -> None:
        self.resetTransform()
        self.centerOn(self.diagram.sceneRect().center())

    def zoom_by(self, factor: float) -> None:
        scale = self.transform().m11() * factor
        if ZOOM_MIN <= scale <= ZOOM_MAX:
            self.scale(factor, factor)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt naming
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(ZOOM_STEP if event.angleDelta().y() > 0 else 1 / ZOOM_STEP)
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if self.transform().m11() <= 1.0:
            self.fit()
