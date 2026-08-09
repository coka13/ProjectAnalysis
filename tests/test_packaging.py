"""Behaviours that only differ once the application is frozen into an EXE.

None of this is exercised by running from source, which is exactly why it broke:
a packaged build resolves its own files differently, and the failure mode is a
window that opens on nothing or a setting that silently does not apply.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app import branding
from app.config import _env_files


def test_resource_root_is_the_repository_when_running_from_source():
    assert (branding.resource_root() / "web" / "index.html").is_file()
    assert (branding.resource_root() / "assets" / "appicon.ico").is_file()
    # Diagrams and HTML export require the vendored Mermaid build offline.
    mermaid = branding.resource_root() / "web" / "vendor" / "mermaid.min.js"
    assert mermaid.is_file()
    assert mermaid.stat().st_size > 100_000


def test_resource_root_follows_the_bundle_when_frozen(monkeypatch, tmp_path):
    """PyInstaller unpacks the data files and names the folder in _MEIPASS.

    A module's ``__file__`` then points inside an archive that was never written
    to disk, so walking up from it lands nowhere and the UI cannot be found.
    """
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert branding.resource_root() == tmp_path


def test_env_file_is_read_from_the_working_directory_from_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert _env_files() == (Path(".env"),)


def test_env_file_beside_the_executable_is_read_when_frozen(monkeypatch, tmp_path):
    """A shortcut sets its own working directory.

    Without this the .env a user drops next to the EXE is never opened, and a
    documented setting such as AAI_ALLOWED_LOCAL_ROOTS quietly does nothing.
    """
    exe = tmp_path / "ProjectAnalysis.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    found = _env_files()
    assert Path(".env") in found
    # Last wins in pydantic-settings, so the shipped file must come last.
    assert found[-1] == tmp_path / ".env"


def test_version_tuple_is_four_integers_for_windows():
    parts = branding.version_tuple()
    assert len(parts) == 4
    assert all(isinstance(part, int) for part in parts)


def test_build_id_never_raises_without_git(monkeypatch):
    """The About page must render even when git is missing, as it is in an EXE."""

    def explode(*args, **kwargs):
        raise OSError("git is not installed")

    monkeypatch.setattr(subprocess, "run", explode)
    assert branding.build_id() == "local"
