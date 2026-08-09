"""Commit graph construction and lane assignment.

The lane algorithm is what keeps branch lines continuous, so it is tested both as
pure data (fast, exhaustive) and against a real repository with a genuine merge.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.history import commit_graph

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _c(sha: str, *parents: str, refs: list | None = None) -> dict:
    return {"sha": sha, "parents": list(parents), "refs": refs or [], "author": "a", "subject": sha}


# --------------------------------------------------------------------- lanes


def test_a_linear_history_stays_in_one_lane() -> None:
    rows = commit_graph.assign_lanes([_c("c", "b"), _c("b", "a"), _c("a")])
    assert [r["lane"] for r in rows] == [0, 0, 0]


def test_every_commit_links_to_its_parent() -> None:
    rows = commit_graph.assign_lanes([_c("c", "b"), _c("b", "a"), _c("a")])
    # A missing link is exactly how a branch line ends up with a visible gap.
    assert [len(r["links"]) for r in rows] == [1, 1, 0]


def test_a_branch_gets_its_own_lane() -> None:
    #   d (main)   e (feature)
    #    \        /
    #      \    /
    #        b
    rows = commit_graph.assign_lanes([_c("d", "b"), _c("e", "b"), _c("b", "a"), _c("a")])
    lanes = {r["sha"]: r["lane"] for r in rows}
    assert lanes["d"] != lanes["e"], "diverging branches must not share a lane"
    assert lanes["b"] in (lanes["d"], lanes["e"])


def test_a_merge_links_both_parents() -> None:
    #   m
    #  / \
    # a   b
    rows = commit_graph.assign_lanes([_c("m", "a", "b"), _c("a", "root"), _c("b", "root"), _c("root")])
    merge = rows[0]
    assert len(merge["links"]) == 2, "a merge must draw an edge to each parent"
    assert {link["parent"] for link in merge["links"]} == {"a", "b"}


def test_a_merge_reuses_a_free_lane_rather_than_growing_forever() -> None:
    rows = commit_graph.assign_lanes(
        [_c("m", "a", "b"), _c("a", "root"), _c("b", "root"), _c("root"), _c("older")]
    )
    assert max(r["lane"] for r in rows) <= 1, "lanes should be recycled once a branch is merged"


def test_lane_continuity_across_a_merge() -> None:
    """The first parent keeps the merge commit's lane, so the trunk never jumps."""
    rows = commit_graph.assign_lanes([_c("m", "a", "b"), _c("a", "root"), _c("b", "root"), _c("root")])
    by_sha = {r["sha"]: r for r in rows}
    first_parent_link = next(link for link in by_sha["m"]["links"] if link["parent"] == "a")
    assert first_parent_link["from"] == first_parent_link["to"] == by_sha["m"]["lane"]
    assert by_sha["a"]["lane"] == by_sha["m"]["lane"]


def test_no_commit_is_left_without_a_lane() -> None:
    rows = commit_graph.assign_lanes([_c("m", "a", "b"), _c("a", "r"), _c("b", "r"), _c("r")])
    assert all(isinstance(r["lane"], int) and r["lane"] >= 0 for r in rows)


def test_octopus_merges_are_supported() -> None:
    rows = commit_graph.assign_lanes([_c("m", "a", "b", "c"), _c("a"), _c("b"), _c("c")])
    assert len(rows[0]["links"]) == 3


def test_an_empty_history_is_safe() -> None:
    assert commit_graph.assign_lanes([]) == []


# ----------------------------------------------------------------- ref parsing


def test_refs_are_classified() -> None:
    refs = commit_graph._parse_refs("HEAD -> main, origin/main, tag: v1.0, feature/x")
    kinds = {r["name"]: r["kind"] for r in refs}
    assert kinds["main"] == "head"
    assert kinds["origin/main"] == "remote"
    assert kinds["v1.0"] == "tag"
    assert kinds["feature/x"] == "branch"


def test_no_refs_is_empty() -> None:
    assert commit_graph._parse_refs("") == []


# ------------------------------------------------------------- real repository


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, name: str, body: str) -> None:
    (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", name)


@pytest.fixture(scope="module")
def branched_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """main with a feature branch merged back in, plus a tag."""
    root = tmp_path_factory.mktemp("branched")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Ada")
    _git(root, "config", "user.email", "ada@example.com")

    _write(root, "a.txt", "1")
    _git(root, "commit", "-m", "first")
    _git(root, "tag", "v1.0")

    _git(root, "checkout", "-b", "feature")
    _write(root, "b.txt", "1")
    _git(root, "commit", "-m", "feature work")

    _git(root, "checkout", "main")
    _write(root, "c.txt", "1")
    _git(root, "commit", "-m", "main work")

    _git(root, "merge", "--no-ff", "feature", "-m", "merge feature")
    return root


def test_graph_is_unavailable_outside_a_repository(tmp_path: Path) -> None:
    payload = commit_graph.build(tmp_path)
    assert payload["available"] is False
    assert payload["commits"] == []


def test_real_repository_produces_a_connected_graph(branched_repo: Path) -> None:
    payload = commit_graph.build(branched_repo)
    assert payload["available"] is True
    assert payload["count"] == 4
    assert payload["lanes"] >= 2, "a merged feature branch needs at least two lanes"


def test_merge_commit_is_detected_in_a_real_repository(branched_repo: Path) -> None:
    payload = commit_graph.build(branched_repo)
    merges = [c for c in payload["commits"] if len(c["parents"]) > 1]
    assert len(merges) == 1
    assert len(merges[0]["links"]) == 2


def test_every_edge_lands_on_a_known_commit(branched_repo: Path) -> None:
    """An edge pointing at a commit outside the window is how lines vanish."""
    payload = commit_graph.build(branched_repo)
    known = {c["sha"] for c in payload["commits"]}
    for commit in payload["commits"]:
        for link in commit["links"]:
            assert link["parent"] in known or link["parent"] in commit["truncated_parents"]


def test_branches_and_tags_are_reported(branched_repo: Path) -> None:
    payload = commit_graph.build(branched_repo)
    assert "main" in payload["branches"]
    assert "v1.0" in payload["tags"]


def test_commits_carry_display_metadata(branched_repo: Path) -> None:
    commit = commit_graph.build(branched_repo)["commits"][0]
    assert len(commit["short"]) == 8
    assert commit["author"] == "Ada"
    assert commit["subject"]
    assert commit["date"].endswith("+00:00")


def test_truncation_is_flagged(branched_repo: Path) -> None:
    payload = commit_graph.build(branched_repo, max_commits=2)
    assert payload["truncated"] is True
    assert payload["count"] == 2
    # The oldest kept commit has a parent outside the window and must say so.
    assert any(c["truncated_parents"] for c in payload["commits"])
