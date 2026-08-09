"""Filesystem traversal and language classification."""

from __future__ import annotations

import fnmatch
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

log = logging.getLogger("aai.walker")

DEFAULT_EXCLUDES: tuple[str, ...] = (
    "**/.git/**",
    "**/.hg/**",
    "**/.svn/**",
    "**/node_modules/**",
    "**/bower_components/**",
    "**/vendor/**",
    "**/dist/**",
    "**/build/**",
    "**/out/**",
    "**/bin/**",
    "**/obj/**",
    "**/target/**",
    "**/.venv/**",
    "**/venv/**",
    "**/env/**",
    "**/__pycache__/**",
    "**/.mypy_cache/**",
    "**/.pytest_cache/**",
    "**/.tox/**",
    "**/.idea/**",
    "**/.vs/**",
    "**/.gradle/**",
    "**/coverage/**",
    "**/*.min.js",
    "**/*.min.css",
    "**/*.lock",
    "**/package-lock.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
)

SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "bower_components",
    "dist",
    "build",
    "out",
    "bin",
    "obj",
    "target",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".idea",
    ".vs",
    ".gradle",
    "coverage",
    ".next",
    ".nuxt",
    ".terraform",
}

LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".cs": "csharp",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".sql": "sql",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown",
    ".rst": "markdown",
    ".tf": "terraform",
    ".proto": "proto",
    ".gradle": "gradle",
    ".csproj": "xml",
    ".sln": "text",
    ".sh": "shell",
    ".ps1": "powershell",
    ".dockerfile": "docker",
    ".cfg": "config",
    ".ini": "config",
    ".env": "config",
}

INFRA_FILENAMES = {
    "dockerfile": "docker",
    "docker-compose.yml": "compose",
    "docker-compose.yaml": "compose",
    "compose.yml": "compose",
    "compose.yaml": "compose",
    "package.json": "npm",
    "requirements.txt": "pip",
    "pyproject.toml": "pip",
    "pipfile": "pip",
    "go.mod": "gomod",
    "cargo.toml": "cargo",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "makefile": "make",
    "procfile": "proc",
    "serverless.yml": "serverless",
    "helmfile.yaml": "helm",
    "chart.yaml": "helm",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg", ".pdf",
    ".zip", ".gz", ".tar", ".7z", ".rar", ".jar", ".war", ".exe", ".dll",
    ".so", ".dylib", ".class", ".pyc", ".pyd", ".o", ".a", ".lib", ".bin",
    ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".db",
    ".sqlite", ".sqlite3", ".parquet", ".pkl",
}


@dataclass
class SourceFile:
    path: Path
    relative_path: str
    language: str
    size: int
    infra_kind: str = ""
    _text: str | None = field(default=None, repr=False)

    @property
    def module(self) -> str:
        """Logical module identifier (top-level package/folder chain, max depth 3)."""
        parts = [p for p in Path(self.relative_path).parts[:-1] if p not in {".", ""}]
        return "/".join(parts[:3]) if parts else "(root)"

    @property
    def top_package(self) -> str:
        parts = [p for p in Path(self.relative_path).parts[:-1] if p not in {".", ""}]
        return parts[0] if parts else "(root)"

    def text(self) -> str:
        if self._text is None:
            try:
                self._text = self.path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:  # pragma: no cover - IO edge case
                log.debug("cannot read %s: %s", self.relative_path, exc)
                self._text = ""
        return self._text


def detect_language(path: Path) -> tuple[str, str]:
    name = path.name.lower()
    infra = INFRA_FILENAMES.get(name, "")
    if not infra and name.startswith("dockerfile"):
        infra = "docker"
    suffix = path.suffix.lower()
    language = LANGUAGE_BY_EXTENSION.get(suffix, "")
    if not language and name in {"dockerfile", "makefile", "procfile"}:
        language = "config"
    return language, infra


def _matches_any(relative: str, patterns: tuple[str, ...] | list[str]) -> bool:
    normalized = relative.replace(os.sep, "/")
    for pattern in patterns:
        if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(f"/{normalized}", pattern):
            return True
    return False


def walk(
    root: Path,
    *,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    max_files: int | None = None,
    max_file_bytes: int | None = None,
) -> Iterator[SourceFile]:
    """Yield analyzable source files below ``root``."""
    root = root.resolve()
    excludes = tuple(DEFAULT_EXCLUDES) + tuple(exclude_globs or ())
    includes = list(include_globs or [])
    limit = max_files or settings.max_files
    byte_limit = max_file_bytes or settings.max_file_bytes
    emitted = 0

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".terraform"))
        current = Path(dirpath)
        for filename in sorted(filenames):
            if emitted >= limit:
                log.warning("file limit of %s reached; truncating scan", limit)
                return
            file_path = current / filename
            if file_path.is_symlink():
                continue
            suffix = file_path.suffix.lower()
            if suffix in BINARY_EXTENSIONS:
                continue
            try:
                relative = file_path.relative_to(root).as_posix()
            except ValueError:
                continue
            if _matches_any(relative, excludes):
                continue
            if includes and not _matches_any(relative, includes):
                continue
            language, infra = detect_language(file_path)
            if not language and not infra:
                continue
            try:
                size = file_path.stat().st_size
            except OSError:
                continue
            if size > byte_limit or size == 0:
                continue
            emitted += 1
            yield SourceFile(
                path=file_path,
                relative_path=relative,
                language=language or "config",
                size=size,
                infra_kind=infra,
            )
