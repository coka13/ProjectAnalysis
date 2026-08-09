"""Commit graph construction: the DAG behind the repository visualisation.

``git_history`` answers "what changed a lot"; this module answers "what does the
history look like". It reads commits *with* their parents (merges included, which
the churn analysis deliberately skips) and assigns each commit to a lane so the
front end can draw continuous branch lines.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

from app.ingest.source import SourceError, has_commits, is_git_repository, run_git

log = logging.getLogger("aai.history")

_SEPARATOR = "\x1f"
_RECORD = "\x1e"

# Lanes are coloured by index on the client; this is only the count it cycles through.
LANE_COLOURS = 10


def collect_graph_commits(root: Path, *, max_commits: int = 500) -> list[dict[str, Any]]:
    """Return commits newest-first with parent links and any refs pointing at them.

    Unlike the churn scan this keeps merge commits: without them the parent links
    are broken and branches cannot be drawn.

    Raises ``SourceError`` when git itself fails, so an empty list always means
    "no commits" rather than "the command did not work".
    """
    if not is_git_repository(root):
        return []
    # See git_history.collect_commits: a repository with no commits is an empty
    # graph, not a broken one.
    if not has_commits(root):
        return []
    args = [
        "log",
        f"--max-count={max_commits}",
        "--topo-order",
        f"--pretty=format:{_RECORD}%H{_SEPARATOR}%P{_SEPARATOR}%an{_SEPARATOR}%ae{_SEPARATOR}%at{_SEPARATOR}%D{_SEPARATOR}%s",
    ]
    output = run_git(args, cwd=root)

    commits: list[dict[str, Any]] = []
    for chunk in output.split(_RECORD):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split(_SEPARATOR)
        if len(parts) < 7:
            continue
        sha, parents, author, email, timestamp, refs, subject = parts[:7]
        try:
            when = dt.datetime.fromtimestamp(int(timestamp), tz=dt.timezone.utc)
        except (ValueError, OSError):
            when = dt.datetime.now(dt.timezone.utc)
        commits.append(
            {
                "sha": sha,
                "short": sha[:8],
                "parents": [p for p in parents.split(" ") if p],
                "author": author,
                "email": email,
                "date": when.isoformat(),
                "timestamp": int(timestamp) if timestamp.isdigit() else 0,
                "refs": _parse_refs(refs),
                "subject": subject[:200],
            }
        )
    return commits


def _parse_refs(raw: str) -> list[dict[str, str]]:
    """Turn git's ``%D`` decoration into structured refs."""
    refs: list[dict[str, str]] = []
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        if item.startswith("HEAD -> "):
            refs.append({"name": item[len("HEAD -> "):], "kind": "head"})
        elif item == "HEAD":
            refs.append({"name": "HEAD", "kind": "head"})
        elif item.startswith("tag: "):
            refs.append({"name": item[len("tag: "):], "kind": "tag"})
        elif item.startswith("origin/"):
            refs.append({"name": item, "kind": "remote"})
        else:
            refs.append({"name": item, "kind": "branch"})
    return refs


def assign_lanes(commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Place each commit in a lane so branch lines stay continuous.

    Walks newest to oldest keeping a list of "open" lanes, each holding the sha it
    is waiting to reach. A commit takes the leftmost lane already expecting it, so
    a line of development keeps the same lane for its whole life instead of
    hopping columns. Extra parents of a merge open new lanes, and lanes that are
    no longer expected are freed for reuse.

    Returns the same commit dicts with ``lane`` and ``links`` added, where each
    link is the pair of lanes an edge connects.
    """
    lanes: list[str | None] = []
    positioned: list[dict[str, Any]] = []

    def take_lane(sha: str) -> int:
        for index, expected in enumerate(lanes):
            if expected == sha:
                return index
        for index, expected in enumerate(lanes):
            if expected is None:
                lanes[index] = sha
                return index
        lanes.append(sha)
        return len(lanes) - 1

    for commit in commits:
        sha = commit["sha"]
        lane = take_lane(sha)

        # Any other lane waiting for this same commit has merged into it.
        for index, expected in enumerate(lanes):
            if index != lane and expected == sha:
                lanes[index] = None

        parents = commit["parents"]
        links: list[dict[str, int]] = []
        if parents:
            # The first parent continues this line of development in the same lane.
            lanes[lane] = parents[0]
            links.append({"from": lane, "to": lane, "parent": parents[0]})
            for parent in parents[1:]:
                target = take_lane(parent)
                lanes[target] = parent
                links.append({"from": lane, "to": target, "parent": parent})
        else:
            lanes[lane] = None

        entry = dict(commit)
        entry["lane"] = lane
        entry["links"] = links
        # Lanes occupied at this row, so the client can draw pass-through lines
        # for branches that exist but have no commit on this row.
        entry["open_lanes"] = [i for i, expected in enumerate(lanes) if expected is not None]
        positioned.append(entry)

    return positioned


def build(root: Path, *, max_commits: int = 500) -> dict[str, Any]:
    """Full commit graph payload for the UI."""
    try:
        commits = collect_graph_commits(root, max_commits=max_commits)
    except SourceError as exc:
        log.warning("commit graph could not be read: %s", exc)
        return {
            "available": False,
            "failed": True,
            "reason_key": "history.reasonFailed",
            "detail": str(exc),
            "reason": f"The commit graph could not be read: {exc}",
            "commits": [],
            "lanes": 0,
        }
    if not commits:
        key = "history.reasonNoCommits" if is_git_repository(root) else "history.reasonNotGit"
        text = (
            "This repository does not have any commits yet."
            if key == "history.reasonNoCommits"
            else "This project folder is not inside a git repository."
        )
        return {"available": False, "reason_key": key, "reason": text, "commits": [], "lanes": 0}

    positioned = assign_lanes(commits)
    known = {c["sha"] for c in positioned}
    for commit in positioned:
        # Parents outside the window would otherwise render as edges to nowhere.
        commit["truncated_parents"] = [p for p in commit["parents"] if p not in known]

    lanes = max((c["lane"] for c in positioned), default=0) + 1
    branches = sorted({ref["name"] for c in positioned for ref in c["refs"] if ref["kind"] in {"branch", "head"}})
    tags = sorted({ref["name"] for c in positioned for ref in c["refs"] if ref["kind"] == "tag"})
    return {
        "available": True,
        "commits": positioned,
        "lanes": lanes,
        "count": len(positioned),
        "truncated": len(positioned) >= max_commits,
        "branches": branches,
        "tags": tags,
        "authors": sorted({c["author"] for c in positioned}),
    }
