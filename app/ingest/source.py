"""Source resolution for local folders and git repositories.

Security notes
--------------
* Git is invoked with an argument list (never a shell string) so a malicious
  repository URL or ref cannot inject commands.
* Refs are validated against a conservative pattern and are additionally passed
  after ``--`` where the git CLI supports it.
* Local paths must resolve inside the configured allow-list (when configured)
  which prevents path traversal into unrelated parts of the filesystem.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings

log = logging.getLogger("aai.ingest")

# A windowed (console=False) build must never pop a console for a git call.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_REF_RE = re.compile(r"^[A-Za-z0-9._/\-+]{1,200}$")
# ``file://`` is intentionally excluded: a remote URL must never be able to pull
# arbitrary paths off the server's own filesystem. Local sources use SourceKind.LOCAL,
# which is validated against the configured allow-list.
_ALLOWED_SCHEMES = {"http", "https", "ssh", "git"}


class SourceError(RuntimeError):
    """Raised when a project source cannot be resolved."""


@dataclass(frozen=True)
class ResolvedSource:
    root: Path
    kind: str
    ref: str
    commit_sha: str
    is_git: bool
    cleanup: bool = False


def validate_ref(ref: str) -> str:
    ref = (ref or "").strip()
    if not ref:
        return ""
    if ref.startswith("-") or ".." in ref or not _REF_RE.match(ref):
        raise SourceError(f"Invalid git ref: {ref!r}")
    return ref


def validate_remote(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise SourceError("Repository URL is required")
    if url.startswith("-"):
        raise SourceError("Invalid repository URL")
    # scp-like syntax: git@host:org/repo.git
    if re.match(r"^[A-Za-z0-9._\-]+@[A-Za-z0-9._\-]+:[A-Za-z0-9._/\-~]+$", url):
        return url
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SourceError(f"Unsupported repository scheme: {parsed.scheme or 'none'}")
    if parsed.scheme in {"http", "https", "ssh", "git"} and not parsed.netloc:
        raise SourceError("Repository URL is missing a host")
    return url


def validate_local_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SourceError(f"Local path is not accessible: {raw_path}") from exc
    if not resolved.is_dir():
        raise SourceError("Local project source must be a directory")

    allow_list = settings.local_root_allow_list
    if allow_list:
        if not any(resolved == root or root in resolved.parents for root in allow_list):
            raise SourceError("Local path is outside the configured allow-list")
    return resolved


def _run_git(args: list[str], cwd: Path | None = None, timeout: int | None = None) -> str:
    git = shutil.which("git")
    if not git:
        raise SourceError("git executable was not found on PATH")
    try:
        completed = subprocess.run(  # noqa: S603 - argument list, no shell
            [git, *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout or settings.git_timeout_seconds,
            check=False,
            shell=False,
            # A windowed build has no console. Without CREATE_NO_WINDOW every
            # git call flashes one, and without an explicit stdin git inherits
            # an invalid handle from the frozen process and can fail outright -
            # which is why repository history worked from source but not from
            # the packaged executable.
            stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise SourceError("git command timed out") from exc
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise SourceError(f"git {args[0]} failed: {message[-1] if message else 'unknown error'}")
    return completed.stdout


def repository_root(path: Path) -> Path | None:
    """The working tree that ``path`` belongs to, or None if it is not in one.

    Asking git is the only correct answer. Testing for ``path/.git`` finds a
    repository only when the analysed folder happens to be its top level, so
    pointing the app at a package inside a checkout - ``repo/backend``, or any
    folder in a monorepo - reported "this project is not a git repository" and
    left Repository History permanently empty. ``--show-toplevel`` also handles
    worktrees and submodules, where ``.git`` is a file, and honours GIT_DIR.
    """
    try:
        found = _run_git(["rev-parse", "--show-toplevel"], cwd=path, timeout=30).strip()
    except SourceError:
        return None
    if not found:
        return None
    try:
        return Path(found).resolve()
    except (OSError, RuntimeError):
        return None


def is_git_repository(path: Path) -> bool:
    """Whether ``path`` sits anywhere inside a git working tree.

    The cheap ``.git`` probe is kept as a fast path so the common case costs no
    subprocess, but a miss now falls through to git itself rather than being
    treated as a final answer.
    """
    if (path / ".git").exists():
        return True
    return repository_root(path) is not None


def has_commits(path: Path) -> bool:
    """Whether the repository containing ``path`` has at least one commit.

    A freshly initialised repository fails ``git log`` with "does not have any
    commits yet". That is a normal state, not a fault, and reporting it as one
    put a Retry button in front of the user that could never succeed.
    """
    try:
        _run_git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=path, timeout=30)
    except SourceError:
        return False
    return True


def run_git(args: list[str], cwd: Path | None = None, timeout: int | None = None) -> str:
    """Public wrapper around the git CLI (argument list only, never a shell)."""
    return _run_git(args, cwd=cwd, timeout=timeout)


def head_commit(path: Path) -> str:
    try:
        return _run_git(["rev-parse", "HEAD"], cwd=path, timeout=30).strip()
    except SourceError:
        return ""


def _mirror_dir(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return settings.workspaces_dir / f"repo-{digest}"


def clone_or_update(url: str, ref: str = "") -> Path:
    """Clone (or fetch) a remote repository into the managed cache and checkout ``ref``."""
    if not settings.allow_remote_clone:
        raise SourceError("Remote cloning is disabled by configuration")
    url = validate_remote(url)
    ref = validate_ref(ref)
    target = _mirror_dir(url)

    if target.exists() and is_git_repository(target):
        log.info("fetching updates for %s", target.name)
        _run_git(["fetch", "--all", "--tags", "--prune"], cwd=target)
    else:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        log.info("cloning repository into %s", target.name)
        _run_git(["clone", "--no-single-branch", "--", url, str(target)])

    if ref:
        _run_git(["checkout", "--force", ref], cwd=target)
        # Fast-forward when the ref is a branch; harmless for tags/commits.
        try:
            _run_git(["merge", "--ff-only", f"origin/{ref}"], cwd=target, timeout=60)
        except SourceError:
            pass
    return target


def resolve(source_kind: str, location: str, ref: str = "") -> ResolvedSource:
    """Resolve a project source into an on-disk working tree."""
    ref = validate_ref(ref)
    if source_kind == "git":
        root = clone_or_update(location, ref)
        return ResolvedSource(
            root=root,
            kind="git",
            ref=ref or current_branch(root),
            commit_sha=head_commit(root),
            is_git=True,
        )

    root = validate_local_path(location)
    git_repo = is_git_repository(root)
    if git_repo and ref:
        _run_git(["checkout", "--force", ref], cwd=root)
    return ResolvedSource(
        root=root,
        kind="local",
        ref=ref or (current_branch(root) if git_repo else ""),
        commit_sha=head_commit(root) if git_repo else "",
        is_git=git_repo,
    )


def current_branch(path: Path) -> str:
    try:
        return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path, timeout=30).strip()
    except SourceError:
        return ""


def locate(source_kind: str, location: str) -> Path | None:
    """The working tree for a project *without* touching it.

    ``resolve`` is the write path: for a remote it clones or fetches, and for a
    local project with a ref it runs ``git checkout --force``. Read-only callers
    must not do either. Opening Repository History used to go through
    ``resolve``, so simply viewing the page hit the network for a remote project
    and force-checked-out the user's own working tree for a local one,
    discarding uncommitted work. This returns what is already on disk, or None.
    """
    if source_kind == "git":
        target = _mirror_dir(location)
        return target if target.is_dir() else None
    try:
        return validate_local_path(location)
    except SourceError:
        return None


def list_refs(path: Path, limit: int = 200) -> dict[str, list[str]]:
    """Return available branches and tags for a working tree."""
    result: dict[str, list[str]] = {"branches": [], "tags": []}
    if not is_git_repository(path):
        return result
    try:
        branches = _run_git(["for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"], cwd=path)
        tags = _run_git(["for-each-ref", "--format=%(refname:short)", "refs/tags"], cwd=path)
    except SourceError:
        return result
    result["branches"] = [b.strip() for b in branches.splitlines() if b.strip()][:limit]
    result["tags"] = [t.strip() for t in tags.splitlines() if t.strip()][:limit]
    return result
