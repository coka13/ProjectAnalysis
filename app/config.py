"""Application configuration for the local desktop app.

Settings are optional - the application runs with sensible defaults out of the
box. Values can still be overridden with ``AAI_*`` environment variables or a
``.env`` file, which is handy for pointing the app at a local model server or
restricting which folders may be analysed.
"""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("aai.config")

APP_DIR_NAME = "ProjectAnalysis"
LEGACY_DIR_NAMES = ("ArchitectureIntelligence",)


def _env_files() -> tuple[Path, ...]:
    """Where a ``.env`` may legitimately live.

    A bare ``".env"`` resolves against the working directory, which is fine when
    the app is started from its source folder but not when it is packaged: a
    shortcut or a Start Menu entry sets its own working directory, so the file
    the user placed beside the executable would never be read and a documented
    setting like ``AAI_ALLOWED_LOCAL_ROOTS`` would silently do nothing.

    Frozen builds therefore also look next to the executable, and that copy wins,
    because it is the one deliberately shipped with the application. Under a
    plain interpreter ``sys.executable`` is python.exe, so it is not consulted.
    """
    candidates = [Path(".env")]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / ".env")
    return tuple(candidates)


def _default_data_dir() -> Path:
    """Per-user application data directory.

    The application used to store its data under a different name. If only the
    legacy folder exists it is moved across, so renaming the product does not
    silently orphan someone's database, clones and encryption key. A failed move
    falls back to the legacy path rather than starting from an empty profile.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"

    root = Path(base)
    current = root / APP_DIR_NAME
    if current.exists():
        return current
    for legacy_name in LEGACY_DIR_NAMES:
        legacy = root / legacy_name
        if not legacy.is_dir():
            continue
        try:
            legacy.rename(current)
            log.info("migrated data directory %s -> %s", legacy, current)
            return current
        except OSError as exc:
            log.warning("could not migrate %s to %s (%s); using the existing folder", legacy, current, exc)
            return legacy
    return current


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AAI_",
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Window
    window_width: int = 1400
    window_height: int = 900
    debug: bool = False

    # Storage
    data_dir: Path = Field(default_factory=_default_data_dir)
    database_url: str = ""
    secret_key: str = ""  # Fernet key for encrypting the stored AI credential

    # Ingestion
    allowed_local_roots: str = ""
    allow_remote_clone: bool = True
    max_file_bytes: int = 1_500_000
    max_files: int = 25_000
    git_timeout_seconds: int = 600

    # Default AI provider (any OpenAI-compatible endpoint)
    ai_base_url: str = ""
    ai_model: str = ""
    ai_api_key: str = ""
    ai_temperature: float = 0.2
    ai_max_tokens: int = 2048
    ai_timeout_seconds: int = 120
    ai_max_retries: int = 3
    ai_streaming: bool = True

    # Runtime
    worker_threads: int = Field(default=2, ge=1, le=16)
    history_max_commits: int = Field(default=1500, ge=0, le=50_000)

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(os.path.expandvars(value)).expanduser()
        return value

    @property
    def resolved_data_dir(self) -> Path:
        path = Path(self.data_dir).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.resolved_data_dir / 'platform.sqlite3').as_posix()}"

    @property
    def workspaces_dir(self) -> Path:
        """Where remote repositories are cloned for analysis."""
        path = self.resolved_data_dir / "repositories"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def cache_dir(self) -> Path:
        path = self.resolved_data_dir / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def local_root_allow_list(self) -> list[Path]:
        """Optional restriction on which folders may be analysed. Empty means any folder."""
        roots: list[Path] = []
        for chunk in self.allowed_local_roots.split(","):
            chunk = chunk.strip()
            if chunk:
                roots.append(Path(chunk).expanduser().resolve())
        return roots


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
