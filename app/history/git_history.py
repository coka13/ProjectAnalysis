"""Git history mining: churn, hotspots, ownership and architecture evolution."""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.graph.model import KnowledgeGraph, NodeKind
from app.ingest.source import SourceError, has_commits, is_git_repository, run_git

log = logging.getLogger("aai.history")

_SEPARATOR = "\x1f"
_RECORD = "\x1e"


def collect_commits(root: Path, *, max_commits: int = 1500, since_days: int | None = None) -> list[dict[str, Any]]:
    """Return commit metadata with touched files (newest first).

    Raises ``SourceError`` when git itself fails. An empty list therefore means
    "this repository has no commits", never "the command did not work" - the
    caller has to be able to tell those apart or a timeout gets reported to the
    user as a repository with no history.
    """
    if not is_git_repository(root):
        return []
    # A repository that has been initialised but never committed to fails
    # ``git log`` with "does not have any commits yet". Asking first turns that
    # into the empty history it actually is, instead of an error with a Retry
    # button that can never succeed.
    if not has_commits(root):
        return []
    args = [
        "log",
        f"--max-count={max_commits}",
        f"--pretty=format:{_RECORD}%H{_SEPARATOR}%an{_SEPARATOR}%ae{_SEPARATOR}%at{_SEPARATOR}%s",
        "--name-only",
        "--no-merges",
    ]
    if since_days:
        args.append(f"--since={since_days}.days.ago")
    output = run_git(args, cwd=root)

    commits: list[dict[str, Any]] = []
    for chunk in output.split(_RECORD):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        header, _, body = chunk.partition("\n")
        parts = header.split(_SEPARATOR)
        if len(parts) < 5:
            continue
        sha, author, email, timestamp, subject = parts[0], parts[1], parts[2], parts[3], parts[4]
        files = [line.strip() for line in body.splitlines() if line.strip()]
        try:
            when = dt.datetime.fromtimestamp(int(timestamp), tz=dt.timezone.utc)
        except (ValueError, OSError):
            when = dt.datetime.now(dt.timezone.utc)
        commits.append(
            {
                "sha": sha,
                "author": author,
                "email": email,
                "date": when.isoformat(),
                "timestamp": int(timestamp) if timestamp.isdigit() else 0,
                "subject": subject[:200],
                "files": files,
            }
        )
    return commits


def analyze(root: Path, graph: KnowledgeGraph | None = None, *, max_commits: int = 1500) -> dict[str, Any]:
    """Produce an architecture evolution report."""
    try:
        commits = collect_commits(root, max_commits=max_commits)
    except SourceError as exc:
        # A timeout or a broken git install is a failure to report, not an
        # absence of history. Saying "no history" here sends the reader looking
        # for a problem in their repository that is not there.
        log.warning("git history could not be read: %s", exc)
        return {
            "available": False,
            "failed": True,
            # `reason` is the raw git message, which cannot be translated; the
            # key lets the UI put a translated sentence in front of it.
            "reason_key": "history.reasonFailed",
            "detail": str(exc),
            "reason": f"The repository history could not be read: {exc}",
        }
    if not commits:
        # Two different empty states that a reader must be able to tell apart:
        # a folder outside version control, and a repository nobody has
        # committed to yet.
        if is_git_repository(root):
            return {
                "available": False,
                "reason_key": "history.reasonNoCommits",
                "reason": "This repository does not have any commits yet.",
            }
        return {
            "available": False,
            "reason_key": "history.reasonNotGit",
            "reason": "This project folder is not inside a git repository.",
        }

    file_changes: Counter[str] = Counter()
    file_authors: dict[str, Counter[str]] = defaultdict(Counter)
    file_last_change: dict[str, str] = {}
    author_commits: Counter[str] = Counter()
    activity_by_month: Counter[str] = Counter()
    co_change: Counter[tuple[str, str]] = Counter()

    for commit in commits:
        author_commits[commit["author"]] += 1
        activity_by_month[commit["date"][:7]] += 1
        files = commit["files"][:200]
        for path in files:
            file_changes[path] += 1
            file_authors[path][commit["author"]] += 1
            file_last_change.setdefault(path, commit["date"])
        if 1 < len(files) <= 25:
            ordered = sorted(files)
            for i, first in enumerate(ordered):
                for second in ordered[i + 1 :]:
                    co_change[(first, second)] += 1

    component_changes: Counter[str] = Counter()
    component_authors: dict[str, Counter[str]] = defaultdict(Counter)
    for path, count in file_changes.items():
        component = path.split("/")[0] if "/" in path else "(root)"
        component_changes[component] += count
        for author, author_count in file_authors[path].items():
            component_authors[component][author] += author_count

    if graph is not None:
        for node in graph.by_kind(NodeKind.FILE):
            changes = file_changes.get(node.qualified_name, 0)
            if changes:
                node.attributes["change_count"] = changes
                node.attributes["last_changed"] = file_last_change.get(node.qualified_name, "")
                node.attributes["authors"] = len(file_authors[node.qualified_name])
        for component in graph.by_kind(NodeKind.COMPONENT):
            changes = component_changes.get(component.name, 0)
            if changes:
                component.attributes["change_count"] = changes
                component.attributes["contributors"] = len(component_authors[component.name])

    hotspots = [
        {
            "path": path,
            "changes": count,
            "authors": len(file_authors[path]),
            "primary_owner": file_authors[path].most_common(1)[0][0] if file_authors[path] else "",
            "last_changed": file_last_change.get(path, ""),
            "risk": _risk(count, len(file_authors[path]), max(file_changes.values())),
        }
        for path, count in file_changes.most_common(25)
    ]

    coupled = [
        {"files": [pair[0], pair[1]], "together": count}
        for pair, count in co_change.most_common(15)
        if count >= 3
    ]

    return {
        "available": True,
        "commit_count": len(commits),
        "first_commit": commits[-1]["date"],
        "last_commit": commits[0]["date"],
        "contributors": len(author_commits),
        "top_contributors": [{"author": a, "commits": c} for a, c in author_commits.most_common(10)],
        "activity_by_month": dict(sorted(activity_by_month.items())),
        "most_changed_components": [
            {
                "component": component,
                "changes": count,
                "contributors": len(component_authors[component]),
                "primary_owner": component_authors[component].most_common(1)[0][0]
                if component_authors[component]
                else "",
            }
            for component, count in component_changes.most_common(12)
        ],
        "hotspots": hotspots,
        "temporal_coupling": coupled,
        "risks": _risks(component_changes, component_authors, hotspots),
    }


def _risk(changes: int, authors: int, max_changes: int) -> str:
    normalized = changes / max(max_changes, 1)
    if normalized > 0.6 and authors <= 2:
        return "high"
    if normalized > 0.4:
        return "medium"
    return "low"


def _risks(
    component_changes: Counter[str],
    component_authors: dict[str, Counter[str]],
    hotspots: list[dict[str, Any]],
) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    if not component_changes:
        return risks
    top_component, top_count = component_changes.most_common(1)[0]
    total = sum(component_changes.values())
    if total and top_count / total > 0.35:
        risks.append(
            {
                "severity": "high",
                "area": top_component,
                "message": f"{top_component} accounts for {round(100 * top_count / total)}% of all changes - a change magnet.",
            }
        )
    for component, authors in component_authors.items():
        if component_changes[component] >= 20 and len(authors) == 1:
            risks.append(
                {
                    "severity": "medium",
                    "area": component,
                    "message": f"{component} is frequently modified but has a single contributor ({authors.most_common(1)[0][0]}) - bus factor risk.",
                }
            )
    for hotspot in hotspots[:3]:
        if hotspot["risk"] == "high":
            risks.append(
                {
                    "severity": "medium",
                    "area": hotspot["path"],
                    "message": f"{hotspot['path']} changed {hotspot['changes']} times with only {hotspot['authors']} author(s).",
                }
            )
    return risks[:10]
