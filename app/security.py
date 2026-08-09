"""Local credential encryption.

The desktop app has no accounts and no login. The only secret it holds is the
optional API key for an AI provider, which is encrypted at rest with a Fernet
key stored in the per-user data directory.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


# --------------------------------------------------------------------------- #
# Secret storage (AI credentials at rest)
# --------------------------------------------------------------------------- #
def _key_file() -> Path:
    return settings.resolved_data_dir / "secret.key"


def _load_or_create_key() -> bytes:
    if settings.secret_key:
        return settings.secret_key.encode()
    path = _key_file()
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - platform dependent
        pass
    return key


_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def mask_secret(value: str) -> str:
    """Return a display-safe representation of a credential."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}{'*' * 8}{value[-2:]}"
