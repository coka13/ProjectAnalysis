"""Generate the single application icon used everywhere, from one definition.

There is no Pillow and no network on the build machine, so the icon is
rasterised here with the standard library only. Shapes are described as signed
distance fields, which gives exact anti-aliasing from one sample per pixel -
that keeps a 16x16 taskbar icon legible instead of the jagged mess a
nearest-neighbour downscale of a large PNG produces.

The glyph is the "appmark" the UI already uses for the app - three connected
nodes - so the window, the About page, the favicon, the taskbar and the
executable all show one identity. It is deliberately not the `layers` glyph:
that one also labels the Diagrams page, the Architecture category and the
instability card, so the product had no shape of its own.

    python tools/make_icon.py

Writes assets/appicon.ico plus assets/appicon-256.png (used by the About page
and any place that wants a bitmap rather than an icon resource).
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

# Sizes Windows actually asks for: Explorer tiles, the taskbar, Alt-Tab and
# the high-DPI shell all pull different entries out of the same file.
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

# The brand accent from web/css/app.css, deepened towards the bottom so the
# icon still reads as one solid shape against a light or dark taskbar.
TOP = (0x5A, 0xA6, 0xFF)
BOTTOM = (0x1F, 0x5F, 0xC8)
GLYPH = (0xFF, 0xFF, 0xFF)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


def _sd_round_rect(px: float, py: float, half: float, radius: float) -> float:
    """Signed distance to a square with rounded corners, centred on (0, 0)."""
    qx = abs(px) - (half - radius)
    qy = abs(py) - (half - radius)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - radius


def _sd_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    length = vx * vx + vy * vy
    t = 0.0 if length == 0 else _clamp((wx * vx + wy * vy) / length)
    return math.hypot(wx - t * vx, wy - t * vy)


def _sd_polyline(px: float, py: float, points: list[tuple[float, float]], half_width: float) -> float:
    best = min(
        _sd_segment(px, py, points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
        for i in range(len(points) - 1)
    )
    return best - half_width


def _sd_ring(px: float, py: float, cx: float, cy: float, radius: float, half_width: float) -> float:
    """Signed distance to a stroked circle of the given centre-line radius."""
    return abs(math.hypot(px - cx, py - cy) - radius) - half_width


def _sd_disc(px: float, py: float, cx: float, cy: float, radius: float) -> float:
    return math.hypot(px - cx, py - cy) - radius


def _glyph_shapes(size: float) -> tuple[list, float]:
    """The node mark, expressed in pixels for an icon of `size`.

    Modelled on ICON_PATHS.appmark in web/js/dom.js so the executable, the
    window, the favicon and the in-app mark are visibly the same shape. The
    path there is drawn on a 24-unit grid whose ink spans roughly 2..22, so it
    is that inner box - not the full grid - that is mapped onto the glyph area.
    """
    # Below about 32px the ring around a node is only a pixel or so wide, and
    # at 62% the hole closes up into a blob. Small entries therefore draw the
    # mark slightly larger, which buys back the hole rather than dropping the
    # detail. Same shape, one optical correction.
    span = size * (0.72 if size <= 32 else 0.62)
    scale = span / 20.0
    offset = (size - span) / 2.0
    half_stroke = max(1.0, size * 0.055) / 2.0

    def pt(x: float, y: float) -> tuple[float, float]:
        return (offset + (x - 2.0) * scale, offset + (y - 2.0) * scale)

    radius = 2.6 * scale
    top, left, right = pt(12, 6), pt(5.6, 18), pt(18.4, 18)
    nodes = [("ring", (cx, cy, radius)) for cx, cy in (top, left, right)]
    edges = [
        ("stroke", [pt(10.8, 8.3), pt(6.8, 15.7)]),
        ("stroke", [pt(13.2, 8.3), pt(17.2, 15.7)]),
        ("stroke", [pt(8.2, 18), pt(15.8, 18)]),
    ]
    return nodes + edges, half_stroke


def _render(size: int) -> bytes:
    """One RGBA image, straight (non-premultiplied) alpha, top row first."""
    half = size / 2.0
    radius = size * 0.22
    shapes, half_stroke = _glyph_shapes(float(size))
    # Anti-alias over roughly one pixel. Small icons get a tighter band so the
    # mark stays crisp rather than smudged; at 16px a full-pixel band turns the
    # one-pixel ring around each node into an even grey.
    band = 0.6 if size <= 20 else 0.8 if size <= 32 else 1.0
    out = bytearray(size * size * 4)

    for y in range(size):
        py = y + 0.5
        mix = py / size
        base = (
            round(TOP[0] + (BOTTOM[0] - TOP[0]) * mix),
            round(TOP[1] + (BOTTOM[1] - TOP[1]) * mix),
            round(TOP[2] + (BOTTOM[2] - TOP[2]) * mix),
        )
        for x in range(size):
            px = x + 0.5
            bg = _clamp(0.5 - _sd_round_rect(px - half, py - half, half - 0.5, radius) / band)
            if bg <= 0.0:
                continue

            distance = 1e9
            for kind, shape in shapes:
                if kind == "disc":
                    distance = min(distance, _sd_disc(px, py, *shape))
                elif kind == "ring":
                    distance = min(distance, _sd_ring(px, py, *shape, half_stroke))
                else:
                    distance = min(distance, _sd_polyline(px, py, shape, half_stroke))
            glyph = _clamp(0.5 - distance / band)

            red = round(base[0] + (GLYPH[0] - base[0]) * glyph)
            green = round(base[1] + (GLYPH[1] - base[1]) * glyph)
            blue = round(base[2] + (GLYPH[2] - base[2]) * glyph)
            offset = (y * size + x) * 4
            out[offset] = red
            out[offset + 1] = green
            out[offset + 2] = blue
            out[offset + 3] = round(bg * 255)
    return bytes(out)


def _png(size: int, rgba: bytes) -> bytes:
    stride = size * 4
    raw = b"".join(b"\x00" + rgba[y * stride : (y + 1) * stride] for y in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _dib(size: int, rgba: bytes) -> bytes:
    """A BMP-style icon entry: bottom-up BGRA plus the legacy AND mask.

    Explorer still reads the mask on some code paths, and an absent one shows
    up as a black box behind the icon, so it is written even though every pixel
    is already described by the alpha channel.
    """
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0)
    rows = []
    for y in range(size - 1, -1, -1):
        row = bytearray()
        for x in range(size):
            offset = (y * size + x) * 4
            row += bytes((rgba[offset + 2], rgba[offset + 1], rgba[offset], rgba[offset + 3]))
        rows.append(bytes(row))
    mask_stride = ((size + 31) // 32) * 4
    mask = bytearray()
    for y in range(size - 1, -1, -1):
        bits = bytearray(mask_stride)
        for x in range(size):
            if rgba[(y * size + x) * 4 + 3] < 128:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    return header + b"".join(rows) + bytes(mask)


def build() -> Path:
    ASSETS.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[int, bytes]] = []
    for size in ICO_SIZES:
        rgba = _render(size)
        # PNG entries keep the 256px image small; Windows has read them since
        # Vista. The smaller sizes stay as DIB, which every shell path accepts.
        entries.append((size, _png(size, rgba) if size >= 128 else _dib(size, rgba)))
        if size == 256:
            (ASSETS / "appicon-256.png").write_bytes(_png(size, rgba))

    offset = 6 + 16 * len(entries)
    directory = struct.pack("<HHH", 0, 1, len(entries))
    body = b""
    for size, data in entries:
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        body += data
        offset += len(data)

    target = ASSETS / "appicon.ico"
    target.write_bytes(directory + body)
    return target


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size} bytes) with sizes {', '.join(map(str, ICO_SIZES))}")
