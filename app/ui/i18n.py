"""Translations, read from the same files the interface has always used.

`web/i18n/*.js` are flat key/value objects wrapped in one assignment, so the
object literal is valid JSON once the wrapper is removed. Reading them keeps a
single source of truth for every string instead of a second copy that would
drift.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app import branding

log = logging.getLogger("aai.ui.i18n")

LANGUAGES = ("en", "he")
RTL_LANGUAGES = frozenset({"he"})
DEFAULT = "en"

_ASSIGNMENT = re.compile(r"window\.AAI_I18N\.(\w+)\s*=\s*(\{.*)", re.S)
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _parse(source: str) -> dict[str, str]:
    match = _ASSIGNMENT.search(source)
    if not match:
        raise ValueError("no window.AAI_I18N assignment found")
    body = match.group(2).rsplit("}", 1)[0] + "}"
    body = _BLOCK_COMMENT.sub("", body)
    body = _LINE_COMMENT.sub("", body)
    body = _TRAILING_COMMA.sub(r"\1", body)
    return json.loads(body)


def _load(language: str) -> dict[str, str]:
    path = Path(branding.resource_root()) / "web" / "i18n" / f"{language}.js"
    try:
        return _parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("could not load translations for %s", language)
        return {}


class Translator:
    """Looks up a key, falling back to English and then to the key itself."""

    def __init__(self, language: str = DEFAULT) -> None:
        self._cache: dict[str, dict[str, str]] = {}
        self._language = DEFAULT
        self.language = language

    @property
    def language(self) -> str:
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        self._language = value if value in LANGUAGES else DEFAULT

    @property
    def is_rtl(self) -> bool:
        return self._language in RTL_LANGUAGES

    def _strings(self, language: str) -> dict[str, str]:
        if language not in self._cache:
            self._cache[language] = _load(language)
        return self._cache[language]

    def __call__(self, key: str, **params: Any) -> str:
        text = self._strings(self._language).get(key)
        if text is None and self._language != DEFAULT:
            text = self._strings(DEFAULT).get(key)
        if text is None:
            log.debug("missing translation: %s", key)
            return key
        # The originals use {name} placeholders; a missing one must not raise.
        for name, value in params.items():
            text = text.replace("{" + name + "}", str(value))
        return text


translator = Translator()
