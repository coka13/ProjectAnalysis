"""One source of truth for how the application identifies itself.

The window title, the About page, the taskbar identity and the version resource
compiled into the executable all read from here. When these drifted apart the
symptom was cosmetic but confusing: an EXE reporting a different version from
the page inside it.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

from app import __version__

PRODUCT_NAME = "ProjectAnalysis"
DESCRIPTION = "Local architecture analysis for a codebase"
COMPANY = "Daniel Uralsky"
AUTHOR = "Daniel Uralsky"
VERSION = __version__
COPYRIGHT = f"© {date.today().year} {AUTHOR}"

# Reverse-DNS style, required by Windows for taskbar grouping and jump lists.
APP_ID = "DanielUralsky.ProjectAnalysis"


def resource_root() -> Path:
    """The directory that ships `web/` and `assets/`.

    Running from source that is the repository. Frozen by PyInstaller the data
    files are unpacked into a bundle directory instead, and a module's own
    `__file__` then names a path inside an archive that was never written to
    disk, so walking up from it points at nothing. `sys._MEIPASS` is the only
    thing that reliably names the bundle.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle) if bundle else Path(__file__).resolve().parents[1]


ROOT = resource_root()
ICON = ROOT / "assets" / "appicon.ico"

# Stops a windowed build from flashing a console when it shells out to git.
# CREATE_NO_WINDOW only exists on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def version_tuple() -> tuple[int, int, int, int]:
    """The version as Windows wants it: exactly four integers."""
    parts = [int(part) for part in VERSION.split(".") if part.isdigit()]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])  # type: ignore[return-value]


def build_id() -> str:
    """A short identifier for the exact source this build came from.

    Falls back to the plain version when git is unavailable, which is the case
    inside a packaged executable. Never raises: a missing build number must not
    stop the About page from rendering.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return "local"
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else "local"
