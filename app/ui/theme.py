"""Design tokens and the Qt stylesheet built from them.

Ported verbatim from the palette the interface has always used, so the native
window keeps the same colours, spacing and type scale. Everything the rest of
the UI needs to draw with lives here; no widget hard-codes a colour.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# 4px spacing scale.
S = (0, 4, 8, 12, 16, 20, 24, 32, 40, 56, 72)

# Corner radii.
R_XS, R_SM, R_MD, R_LG, R_XL = 4, 6, 10, 14, 20
R_FULL = 999

# Type scale in pixels: the rem values of the design system resolved against
# its 16px root, so the native window measures the same as the original.
F_XS, F_SM, F_MD, F_LG, F_XL, F_2XL, F_3XL, F_4XL = 11, 12, 14, 16, 20, 26, 34, 46

# Inter first, exactly as the interface asks for it, then the same fallbacks.
FONT_STACK = "Inter, Segoe UI Variable Text, Segoe UI, Noto Sans Hebrew, Arial, sans-serif"
MONO_STACK = "Cascadia Code, JetBrains Mono, Fira Code, Consolas, Courier New, monospace"

NAV_WIDTH = 244
TOPBAR_H = 52
NAV_ITEM_H = 33
BUTTON_H = 32


@dataclass(frozen=True)
class Palette:
    """One resolved colour scheme."""

    bg: str
    bg_alt: str
    surface: str
    surface_2: str
    surface_3: str
    line: str
    line_soft: str
    line_strong: str
    text: str
    text_2: str
    text_3: str
    accent: str
    accent_2: str
    accent_ink: str
    accent_soft: str
    ok: str
    warn: str
    danger: str
    info: str
    ok_soft: str
    warn_soft: str
    danger_soft: str
    info_soft: str
    muted_soft: str
    grid_line: str
    overlay: str


DARK = Palette(
    bg="#0b0f16",
    bg_alt="#0f151f",
    surface="#131a25",
    surface_2="#18212e",
    surface_3="#1e2836",
    line="#253044",
    line_soft="#1c2534",
    line_strong="#35435c",
    text="#e8edf5",
    text_2="#a9b6c9",
    text_3="#6f7f96",
    accent="#4f9cf9",
    accent_2="#7db8ff",
    accent_ink="#04121f",
    accent_soft="rgba(79, 156, 249, 0.14)",
    ok="#37d399",
    warn="#f5b544",
    danger="#f4676a",
    info="#6ea8fe",
    ok_soft="rgba(55, 211, 153, 0.14)",
    warn_soft="rgba(245, 181, 68, 0.15)",
    danger_soft="rgba(244, 103, 106, 0.15)",
    info_soft="rgba(110, 168, 254, 0.14)",
    muted_soft="rgba(150, 168, 190, 0.12)",
    grid_line="rgba(255, 255, 255, 0.07)",
    overlay="rgba(4, 7, 12, 0.72)",
)

LIGHT = Palette(
    bg="#f5f7fb",
    bg_alt="#eef2f8",
    surface="#ffffff",
    surface_2="#f7f9fc",
    surface_3="#eef2f8",
    line="#dbe3ee",
    line_soft="#e8edf4",
    line_strong="#c3cfe0",
    text="#101828",
    text_2="#48566b",
    text_3="#78889e",
    accent="#1f6fd6",
    accent_2="#1257ad",
    accent_ink="#ffffff",
    accent_soft="rgba(31, 111, 214, 0.10)",
    ok="#0e9f6e",
    warn="#b7791f",
    danger="#d92d20",
    info="#1f6fd6",
    ok_soft="rgba(14, 159, 110, 0.12)",
    warn_soft="rgba(183, 121, 31, 0.14)",
    danger_soft="rgba(217, 45, 32, 0.11)",
    info_soft="rgba(31, 111, 214, 0.10)",
    muted_soft="rgba(72, 86, 107, 0.08)",
    grid_line="rgba(16, 24, 40, 0.08)",
    overlay="rgba(18, 26, 38, 0.35)",
)

# Colour-blind safe score palette, layered over either theme.
CB_OVERRIDES = {
    "ok": "#0072b2",
    "warn": "#e69f00",
    "danger": "#d55e00",
    "info": "#56b4e9",
    "ok_soft": "rgba(0, 114, 178, 0.16)",
    "warn_soft": "rgba(230, 159, 0, 0.16)",
    "danger_soft": "rgba(213, 94, 0, 0.16)",
}

# The five score bands, keyed by what scoring.band_for() returns. "good" and
# "poor" sit between the theme tones and have literal colours of their own.
BAND_TONES = {
    "excellent": "ok",
    "good": "#86c440",
    "fair": "warn",
    "poor": "#f08b3c",
    "critical": "danger",
}


def score_colour(score: float, p: Palette) -> str:
    """The colour a score is drawn in, using the same bands as the report."""
    from app.graph.scoring import band_for

    tone = BAND_TONES.get(band_for(score), "danger")
    return tone if tone.startswith("#") else getattr(p, tone)


def palette(theme: str = "dark", *, contrast: str = "normal", colours: str = "default") -> Palette:
    """Resolve a palette the same way the stylesheet layers used to."""
    base = LIGHT if theme == "light" else DARK
    if colours == "cb":
        base = replace(base, **CB_OVERRIDES)
    if contrast == "high":
        # Borders become text-weight and the dimmest tier is lifted, matching
        # the high-contrast layer of the original design.
        base = replace(base, line=base.text_2, line_soft=base.text_3, line_strong=base.text, text_2=base.text, text_3=base.text_2)
    return base


def stylesheet(p: Palette, *, scale: float = 1.0) -> str:
    """The application-wide Qt stylesheet for a resolved palette.

    Sizes are the measured pixel values of the original interface, so the two
    render at the same dimensions rather than merely looking similar.
    """
    f = lambda px: max(8, round(px * scale))  # noqa: E731 - short local helper

    return f"""
    * {{
        font-family: "{FONT_STACK}";
        outline: none;
    }}
    QWidget {{
        background: {p.bg};
        color: {p.text};
        font-size: {f(F_MD)}px;
    }}
    /* Text must not paint the window colour over the surface it sits on. */
    QLabel, QCheckBox, QRadioButton {{
        background: transparent;
    }}
    QToolTip {{
        background: {p.surface_3};
        color: {p.text};
        border: 1px solid {p.line};
        border-radius: {R_SM}px;
        padding: {S[2]}px {S[3]}px;
    }}

    #Topbar {{
        background: {p.surface};
        border-bottom: 1px solid {p.line};
    }}
    #Sidebar {{
        background: {p.bg_alt};
        border-right: 1px solid {p.line};
    }}
    #Content {{
        background: {p.bg};
    }}
    #Breadcrumb {{
        background: {p.bg};
        border-bottom: 1px solid {p.line_soft};
    }}
    #CrumbText {{
        color: {p.text_3};
        font-size: {f(F_SM)}px;
    }}
    /* The mark sits on an accent tile, as it does in the original plate. */
    #BrandMark {{
        background: {p.accent};
        border-radius: {R_SM}px;
    }}
    #Search {{
        background: {p.bg_alt};
        border: 1px solid {p.line};
        border-radius: {R_SM}px;
        color: {p.text};
        padding: 6px {S[3]}px;
    }}
    /* The shortcut chip shown at the end of the search field. */
    #Kbd {{
        background: {p.surface_3};
        border: 1px solid {p.line};
        border-radius: {R_XS}px;
        color: {p.text_3};
        font-size: {f(F_XS)}px;
        font-weight: 600;
        padding: 1px 6px;
    }}

    #BrandName {{
        font-size: {f(F_LG)}px;
        font-weight: 600;
        color: {p.text};
    }}
    #BrandTag {{
        font-size: {f(F_XS)}px;
        color: {p.text_3};
    }}

    QLabel[role="h1"] {{ font-size: {f(F_2XL)}px; font-weight: 600; }}
    QLabel[role="h2"] {{ font-size: {f(F_XL)}px;  font-weight: 600; }}
    QLabel[role="h3"] {{ font-size: {f(F_LG)}px;  font-weight: 600; }}
    QLabel[role="muted"] {{ color: {p.text_2}; }}
    QLabel[role="dim"] {{ color: {p.text_3}; font-size: {f(F_SM)}px; }}

    #NavGroup {{
        color: {p.text_3};
        font-size: {f(F_XS)}px;
        font-weight: 700;
        padding: {S[3]}px {S[3]}px {S[1]}px {S[3]}px;
    }}
    QPushButton[nav="true"] {{
        background: transparent;
        border: none;
        border-radius: {R_SM}px;
        color: {p.text_2};
        font-size: {f(F_SM)}px;
        font-weight: 500;
        min-height: {NAV_ITEM_H}px;
        padding: 7px {S[3]}px;
        text-align: left;
    }}
    QPushButton[nav="true"]:hover {{
        background: {p.muted_soft};
        color: {p.text};
    }}
    QPushButton[nav="true"]:checked {{
        background: {p.accent_soft};
        color: {p.accent};
        font-weight: 600;
        /* The rail the original draws down the active entry; the padding is
           reduced by the same amount so the label does not shift. */
        border-left: 3px solid {p.accent};
        padding-left: {S[3] - 3}px;
    }}
    QPushButton[nav="true"]:disabled {{
        color: {p.text_3};
    }}

    QPushButton {{
        background: {p.surface_2};
        border: 1px solid {p.line};
        border-radius: {R_SM}px;
        color: {p.text};
        font-size: {f(F_SM)}px;
        font-weight: 500;
        min-height: {BUTTON_H}px;
        padding: 7px 13px;
    }}
    QPushButton:hover {{ background: {p.surface_3}; border-color: {p.line_strong}; }}
    QPushButton:disabled {{ color: {p.text_3}; background: {p.surface}; }}
    QPushButton[variant="primary"] {{
        background: {p.accent};
        border-color: {p.accent};
        color: {p.accent_ink};
        font-weight: 600;
    }}
    QPushButton[variant="primary"]:hover {{ background: {p.accent_2}; border-color: {p.accent_2}; }}
    QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; color: {p.text_2}; }}
    QPushButton[variant="ghost"]:hover {{ background: {p.muted_soft}; color: {p.text}; }}
    /* Destructive actions are outlined, not filled - the same weight the
       original gives them, so they do not shout louder than the primary. */
    QPushButton[variant="danger"] {{
        background: {p.surface_2};
        border-color: {p.line};
        color: {p.danger};
    }}
    QPushButton[variant="danger"]:hover {{ background: {p.danger_soft}; border-color: {p.danger}; }}

    #Card {{
        background: {p.surface};
        border: 1px solid {p.line};
        border-radius: {R_LG}px;
    }}

    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {p.surface_2};
        border: 1px solid {p.line};
        border-radius: {R_SM}px;
        color: {p.text};
        font-size: {f(F_SM)}px;
        min-height: {BUTTON_H}px;
        padding: 6px {S[3]}px;
        selection-background-color: {p.accent};
        selection-color: {p.accent_ink};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
        border-color: {p.accent};
    }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {p.surface_2};
        border: 1px solid {p.line};
        selection-background-color: {p.accent_soft};
        selection-color: {p.text};
        outline: none;
    }}

    QTableView, QTreeView, QListView {{
        background: {p.surface};
        alternate-background-color: {p.surface_2};
        border: 1px solid {p.line};
        border-radius: {R_LG}px;
        font-size: {f(F_SM)}px;
        gridline-color: {p.line_soft};
        selection-background-color: {p.accent_soft};
        selection-color: {p.text};
    }}
    QTableView::item, QTreeView::item {{ padding: 5px {S[2]}px; }}
    QHeaderView::section {{
        background: {p.surface_3};
        color: {p.text_2};
        border: none;
        border-bottom: 1px solid {p.line};
        padding: {S[2]}px {S[3]}px;
        font-size: {f(F_XS)}px;
        font-weight: 600;
    }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {p.line_strong}; border-radius: 5px; min-height: 32px; }}
    QScrollBar::handle:vertical:hover {{ background: {p.text_3}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {p.line_strong}; border-radius: 5px; min-width: 32px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QProgressBar {{
        background: {p.surface_3};
        border: none;
        border-radius: {R_FULL}px;
        height: 6px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: {R_FULL}px; }}

    QTabBar::tab {{
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        color: {p.text_2};
        padding: {S[3]}px {S[4]}px;
    }}
    QTabBar::tab:selected {{ color: {p.text}; border-bottom-color: {p.accent}; font-weight: 600; }}
    QTabWidget::pane {{ border: none; }}

    QMenu {{
        background: {p.surface_2};
        border: 1px solid {p.line};
        border-radius: {R_SM}px;
        padding: {S[1]}px;
    }}
    QMenu::item {{ padding: {S[2]}px {S[4]}px; border-radius: {R_XS}px; }}
    QMenu::item:selected {{ background: {p.accent_soft}; color: {p.text}; }}

    QCheckBox, QRadioButton {{ spacing: {S[2]}px; }}
    QSplitter::handle {{ background: {p.line_soft}; }}
    """
