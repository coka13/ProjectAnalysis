"""Git history mining, exercised against real throwaway repositories.

This module drives ``git`` for real rather than mocking it, because the value of
these tests is in the parsing: the log format uses unit/record separators and a
``--name-only`` body, which no mock would faithfully reproduce.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.history import git_history

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str, files: dict[str, str], author: str = "Ada <ada@example.com>") -> None:
    for name, body in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "-c", f"user.name={author.split(' <')[0]}", "-c", f"user.email={author.split('<')[1][:-1]}",
         "commit", "-m", message, f"--author={author}")


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One shared repository: building it costs ~3s, and no test mutates it."""
    root = tmp_path_factory.mktemp("sample")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Ada")
    _git(root, "config", "user.email", "ada@example.com")

    # core/engine.py is the change magnet; core/util.py rides along with it.
    _commit(root, "initial", {"core/engine.py": "v1", "core/util.py": "u1", "docs/readme.md": "d1"})
    for i in range(2, 6):
        _commit(root, f"engine {i}", {"core/engine.py": f"v{i}", "core/util.py": f"u{i}"})
    _commit(root, "docs", {"docs/readme.md": "d2"}, author="Grace <grace@example.com>")
    return root


def test_a_directory_without_git_is_reported_as_unavailable(tmp_path: Path) -> None:
    report = git_history.analyze(tmp_path)
    assert report["available"] is False
    assert report["reason"]


def test_collect_commits_parses_metadata_and_files(repo: Path) -> None:
    commits = git_history.collect_commits(repo)
    assert len(commits) == 6
    newest = commits[0]
    assert newest["subject"] == "docs"
    assert newest["files"] == ["docs/readme.md"]
    assert newest["author"] == "Grace"
    assert newest["email"] == "grace@example.com"
    assert newest["timestamp"] > 0
    assert newest["date"].endswith("+00:00")


def test_commits_are_newest_first(repo: Path) -> None:
    commits = git_history.collect_commits(repo)
    stamps = [c["timestamp"] for c in commits]
    assert stamps == sorted(stamps, reverse=True)


def test_max_commits_is_honoured(repo: Path) -> None:
    assert len(git_history.collect_commits(repo, max_commits=2)) == 2


def test_hotspots_rank_the_most_changed_file_first(repo: Path) -> None:
    report = git_history.analyze(repo)
    assert report["available"] is True
    assert report["commit_count"] == 6
    top = report["hotspots"][0]
    assert top["path"] == "core/engine.py"
    assert top["changes"] == 5
    assert top["primary_owner"] == "Ada"


def test_contributors_are_counted(repo: Path) -> None:
    report = git_history.analyze(repo)
    assert report["contributors"] == 2
    authors = {c["author"]: c["commits"] for c in report["top_contributors"]}
    assert authors == {"Ada": 5, "Grace": 1}


def test_temporal_coupling_finds_files_that_change_together(repo: Path) -> None:
    report = git_history.analyze(repo)
    pairs = [tuple(sorted(item["files"])) for item in report["temporal_coupling"]]
    assert ("core/engine.py", "core/util.py") in pairs


def test_first_and_last_commit_bracket_the_history(repo: Path) -> None:
    report = git_history.analyze(repo)
    assert report["first_commit"] <= report["last_commit"]


def test_activity_is_bucketed_by_month(repo: Path) -> None:
    report = git_history.analyze(repo)
    months = report["activity_by_month"]
    assert sum(months.values()) == 6
    assert all(len(key) == 7 and key[4] == "-" for key in months)


def test_components_are_aggregated_from_the_top_directory(repo: Path) -> None:
    report = git_history.analyze(repo)
    components = {item["component"]: item["changes"] for item in report["most_changed_components"]}
    assert components["core"] == 10  # engine 5 + util 5
    assert components["docs"] == 2


def test_a_change_magnet_is_reported_as_a_risk(repo: Path) -> None:
    report = git_history.analyze(repo)
    messages = " ".join(risk["message"] for risk in report["risks"])
    assert "change magnet" in messages


def test_risk_grading_boundaries() -> None:
    assert git_history._risk(10, 1, 10) == "high"     # dominant and owned by one person
    assert git_history._risk(10, 5, 10) == "medium"   # dominant but widely shared
    assert git_history._risk(1, 1, 10) == "low"
    assert git_history._risk(0, 0, 0) == "low"        # must not divide by zero


def test_an_empty_repository_is_handled(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    _git(root, "init", "-b", "main")
    assert git_history.analyze(root)["available"] is False
