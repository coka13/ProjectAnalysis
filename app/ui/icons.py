"""Icons, drawn from the same path data the interface has always used.

The shapes live in `web/js/dom.js`. Reading them here keeps one definition of
every glyph - notably the product mark, which an audit checks is identical in
each place it appears.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from app import branding

log = logging.getLogger("aai.ui.icons")

_BLOCK = re.compile(r"const ICON_PATHS\s*=\s*\{(.*?)\n\s*\};", re.S)
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)
# A value is one or more single-quoted chunks joined with `+`.
_ENTRY = re.compile(r"(\w+)\s*:\s*((?:'[^']*'\s*\+?\s*)+)", re.S)
_CHUNK = re.compile(r"'([^']*)'")

FALLBACK = "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm0-13v.5m0 3.5v5"


@lru_cache(maxsize=1)
def paths() -> dict[str, str]:
    """Every named glyph, parsed once."""
    source = branding.resource_root() / "web" / "js" / "dom.js"
    try:
        block = _BLOCK.search(source.read_text(encoding="utf-8"))
        if not block:
            raise ValueError("ICON_PATHS block not found")
        body = _LINE_COMMENT.sub("", block.group(1))
        return {name: "".join(_CHUNK.findall(value)) for name, value in _ENTRY.findall(body)}
    except (OSError, ValueError):
        log.exception("could not read icon paths")
        return {}


def _svg(name: str, colour: str, weight: float) -> bytes:
    d = paths().get(name) or FALLBACK
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{colour}" stroke-width="{weight}" stroke-linecap="round" '
        f'stroke-linejoin="round"><path d="{d}"/></svg>'
    ).encode("utf-8")


@lru_cache(maxsize=512)
def icon(name: str, colour: str = "#e8edf5", size: int = 18, weight: float = 1.7) -> QIcon:
    """A crisp icon at the requested size.

    Rendered at 2x and marked device-independent so it stays sharp on a
    high-DPI display instead of being upscaled from a small bitmap.
    """
    renderer = QSvgRenderer(QByteArray(_svg(name, colour, weight)))
    pixmap = QPixmap(QSize(size * 2, size * 2))
    pixmap.fill(QColor(Qt.GlobalColor.transparent))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size * 2, size * 2))
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return QIcon(pixmap)
