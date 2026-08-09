"""User interface preferences.

The interface used to keep these in browser storage. They now live beside the
database so the same choices survive a reinstall of the application folder.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import settings

log = logging.getLogger("aai.ui.prefs")

FILE = "ui-prefs.json"

THEMES = ("dark", "light")
CONTRASTS = ("normal", "high")
PALETTES = ("default", "cb")
MOTIONS = ("full", "reduced")
SCALES = (0.9, 1.0, 1.12, 1.25)


@dataclass
class Preferences:
    """Everything the Appearance panel controls."""

    theme: str = "dark"
    language: str = "en"
    contrast: str = "normal"
    palette: str = "default"
    motion: str = "full"
    scale: float = 1.0
    sidebar_collapsed: bool = False

    def normalised(self) -> "Preferences":
        """Clamp every field so a hand-edited file cannot break the window."""
        return Preferences(
            theme=self.theme if self.theme in THEMES else "dark",
            language=self.language if self.language in ("en", "he") else "en",
            contrast=self.contrast if self.contrast in CONTRASTS else "normal",
            palette=self.palette if self.palette in PALETTES else "default",
            motion=self.motion if self.motion in MOTIONS else "full",
            scale=min(SCALES, key=lambda s: abs(s - _number(self.scale, 1.0))),
            sidebar_collapsed=bool(self.sidebar_collapsed),
        )


def _number(value: object, fallback: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _path() -> Path:
    return Path(settings.resolved_data_dir) / FILE


def load() -> Preferences:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Preferences()
    except (OSError, ValueError):
        log.warning("ignoring unreadable preferences", exc_info=True)
        return Preferences()
    if not isinstance(raw, dict):
        return Preferences()
    known = {f for f in Preferences().__dict__}
    return Preferences(**{k: v for k, v in raw.items() if k in known}).normalised()


def save(prefs: Preferences) -> None:
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(prefs), indent=2), encoding="utf-8")
    except OSError:
        log.warning("could not persist preferences", exc_info=True)
