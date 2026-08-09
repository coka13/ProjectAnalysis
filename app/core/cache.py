"""Small on-disk + in-memory cache for expensive analysis artefacts."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings

_LOCK = threading.RLock()
_MEMORY: dict[str, tuple[float, Any]] = {}
_MEMORY_TTL_SECONDS = 900
_MEMORY_MAX_ENTRIES = 256


def make_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]


def memory_get(key: str) -> Any | None:
    with _LOCK:
        item = _MEMORY.get(key)
        if not item:
            return None
        expires, value = item
        if expires < time.time():
            _MEMORY.pop(key, None)
            return None
        return value


def memory_set(key: str, value: Any, ttl: int = _MEMORY_TTL_SECONDS) -> None:
    with _LOCK:
        if len(_MEMORY) >= _MEMORY_MAX_ENTRIES:
            oldest = sorted(_MEMORY.items(), key=lambda kv: kv[1][0])[: _MEMORY_MAX_ENTRIES // 4]
            for k, _ in oldest:
                _MEMORY.pop(k, None)
        _MEMORY[key] = (time.time() + ttl, value)


def memory_clear() -> None:
    with _LOCK:
        _MEMORY.clear()


def _disk_path(key: str) -> Path:
    return settings.cache_dir / f"{key}.json"


def disk_get(key: str) -> Any | None:
    path = _disk_path(key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def disk_set(key: str, value: Any) -> None:
    path = _disk_path(key)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:  # pragma: no cover - disk issues
        tmp.unlink(missing_ok=True)
