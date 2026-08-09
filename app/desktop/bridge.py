"""The application API behind the desktop window.

Every method takes a payload dict and always resolves to
``{"ok": true, "data": ...}`` or ``{"ok": false, "error": ...}``, so a caller
never has to handle an exception. Calls are made from a worker thread, so
blocking database and filesystem work is fine; only the long-running analysis
is pushed onto the job manager.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import functools
import logging
from pathlib import Path
from typing import Any, Callable, Protocol

from sqlalchemy import select

from app import branding
from app.ai import fixes, insights
from app.compare import architecture_diff
from app.config import settings
from app.core.jobs import job_manager
from app.db import session_scope
from app.diagrams.base import DiagramFilters, EmptyDiagramError
from app.diagrams.registry import DIAGRAM_KINDS, DEFAULT_ORDER, generate
from app.export import exporters
from app.graph import metrics as metrics_mod
from app.graph import scoring
from app.history import commit_graph, git_history
from app.ingest import source as source_mod
from app.models import (
    AnalysisRun,
    ApprovalState,
    Comment,
    Diagram,
    DiagramVersion,
    JobStatus,
    Project,
    SourceKind,
)
from app.services import analysis_service, provider_service
from app.security import mask_secret

log = logging.getLogger("aai.bridge")

APP_VERSION = branding.VERSION
LANGUAGES = ["en", "he"]


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #
class BridgeError(Exception):
    """A user-facing failure - the message is shown in the UI."""


class FileDialogHost(Protocol):
    """The only part of the API that needs a window.

    Keeping it behind a protocol is what lets this module stay free of any UI
    toolkit, so the API can be driven by the shell or by a test double.
    """

    def pick_folder(self) -> str | None: ...

    def save_file(self, filename: str) -> str | None: ...


def endpoint(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Normalise the result and never let an exception escape into the WebView."""

    @functools.wraps(fn)
    def wrapper(self: "Api", payload: dict | None = None) -> dict:
        try:
            data = fn(self, payload or {})
            return {"ok": True, "data": data}
        except BridgeError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - the UI must always get an answer
            log.exception("bridge call %s failed", fn.__name__)
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}

    return wrapper


def _iso(value: dt.datetime | None) -> str:
    return value.isoformat() if value else ""


def _need(payload: dict, key: str) -> Any:
    value = payload.get(key)
    if value in (None, ""):
        raise BridgeError(f"'{key}' is required")
    return value


def _language(payload: dict) -> str:
    lang = str(payload.get("language") or "en").lower()
    return lang if lang in LANGUAGES else "en"


def _read_excerpt(root: Path, relative: str, line: int, *, context: int = 3) -> list[dict[str, Any]]:
    """Return the lines surrounding ``line`` so evidence can be shown in place.

    File-level evidence carries no line number; in that case the head of the file
    is returned so the drawer still shows the reader something concrete.
    """
    if not relative:
        return []
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:  # never read outside the analysed tree
        return []
    if not target.is_file() or target.stat().st_size > 2_000_000:
        return []
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if line <= 0:
        start, end = 0, min(len(lines), context * 4)
    else:
        start = max(0, line - 1 - context)
        end = min(len(lines), line + context)
    return [
        {"line": index + 1, "text": lines[index][:400], "highlight": index + 1 == line}
        for index in range(start, end)
    ]


def _project_dict(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "source_kind": project.source_kind.value,
        "source_location": project.source_location,
        "default_ref": project.default_ref,
        "include_globs": list(project.include_globs or []),
        "exclude_globs": list(project.exclude_globs or []),
        "created_at": _iso(project.created_at),
        "updated_at": _iso(project.updated_at),
    }


def _run_dict(run: AnalysisRun) -> dict:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "ref": run.ref,
        "commit_sha": run.commit_sha,
        "status": run.status.value,
        "progress": round(run.progress, 4),
        "stage": run.stage,
        "error": run.error,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "stats": run.stats or {},
        "created_at": _iso(run.created_at),
    }


def _diagram_dict(diagram: Diagram) -> dict:
    return {
        "id": diagram.id,
        "analysis_id": diagram.analysis_id,
        "kind": diagram.kind,
        "title": diagram.title,
        "scope": diagram.scope or {},
        "mermaid": diagram.mermaid,
        "plantuml": diagram.plantuml,
        "payload": diagram.payload or {},
        "explanation": diagram.explanation or {},
        "approval_state": diagram.approval_state.value,
        "version": diagram.version,
        "created_at": _iso(diagram.created_at),
        "updated_at": _iso(diagram.updated_at),
    }


def _comment_dict(comment: Comment) -> dict:
    return {
        "id": comment.id,
        "diagram_id": comment.diagram_id,
        "body": comment.body,
        "anchor": comment.anchor,
        "resolved": comment.resolved,
        "created_at": _iso(comment.created_at),
    }


def _get_project(session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise BridgeError("Project not found")
    return project


def _get_run(session, analysis_id: str) -> AnalysisRun:
    run = session.get(AnalysisRun, analysis_id)
    if run is None:
        raise BridgeError("Analysis not found")
    return run


def _get_diagram(session, diagram_id: str) -> Diagram:
    diagram = session.get(Diagram, diagram_id)
    if diagram is None:
        raise BridgeError("Diagram not found")
    return diagram


def _succeeded_run(session, analysis_id: str) -> AnalysisRun:
    run = _get_run(session, analysis_id)
    if run.status != JobStatus.SUCCEEDED:
        raise BridgeError("This analysis has not finished successfully yet")
    return run


# --------------------------------------------------------------------------- #
# The API object handed to pywebview
# --------------------------------------------------------------------------- #
class Api:
    """One method per UI operation."""

    def __init__(self, host: "FileDialogHost | None" = None) -> None:
        # Injected by the shell. Keeps this class free of any toolkit import so
        # the same API serves the UI and the tests.
        self._host = host

    def _attach(self, host: "FileDialogHost") -> None:
        self._host = host

    def _dialogs(self) -> "FileDialogHost":
        if self._host is None:
            raise BridgeError("No window available")
        return self._host

    # ------------------------------------------------------------- meta
    @endpoint
    def health(self, _payload: dict) -> dict:
        return {
            "status": "ok",
            "version": APP_VERSION,
            "build": branding.build_id(),
            "product": branding.PRODUCT_NAME,
            "author": branding.AUTHOR,
            "copyright": branding.COPYRIGHT,
            "languages": LANGUAGES,
            "diagram_kinds": DEFAULT_ORDER,
            "export_formats": exporters.EXPORT_FORMATS,
            "data_dir": str(settings.resolved_data_dir),
        }

    # ------------------------------------------------------- native dialogs
    @endpoint
    def pick_folder(self, _payload: dict) -> dict:
        return {"path": str(self._dialogs().pick_folder() or "")}

    @endpoint
    def save_file(self, payload: dict) -> dict:
        """Write text (or base64 binary) to a location chosen by the user."""
        import base64

        filename = str(payload.get("filename") or "export.txt")
        chosen = self._dialogs().save_file(filename)
        if not chosen:
            return {"saved": False, "path": ""}
        target = Path(chosen)
        target.parent.mkdir(parents=True, exist_ok=True)

        if payload.get("base64"):
            target.write_bytes(base64.b64decode(str(payload["base64"]).split(",")[-1]))
        else:
            target.write_text(str(payload.get("content") or ""), encoding="utf-8")
        return {"saved": True, "path": str(target)}

    # ---------------------------------------------------------- projects
    @endpoint
    def projects_list(self, _payload: dict) -> list[dict]:
        with session_scope() as session:
            projects = session.scalars(select(Project).order_by(Project.updated_at.desc())).all()
            out = []
            for project in projects:
                latest = session.scalar(
                    select(AnalysisRun)
                    .where(AnalysisRun.project_id == project.id)
                    .order_by(AnalysisRun.created_at.desc())
                )
                item = _project_dict(project)
                item["latest_analysis"] = _run_dict(latest) if latest else None
                out.append(item)
            return out

    @endpoint
    def project_create(self, payload: dict) -> dict:
        name = str(_need(payload, "name")).strip()[:160]
        kind = SourceKind(str(payload.get("source_kind") or "local"))
        location = str(_need(payload, "source_location")).strip()

        if kind is SourceKind.LOCAL:
            location = str(source_mod.validate_local_path(location))
        else:
            location = source_mod.validate_remote(location)

        with session_scope() as session:
            project = Project(
                name=name,
                description=str(payload.get("description") or "")[:2000],
                source_kind=kind,
                source_location=location,
                default_ref=source_mod.validate_ref(str(payload.get("default_ref") or "")),
                include_globs=list(payload.get("include_globs") or []),
                exclude_globs=list(payload.get("exclude_globs") or []),
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            return _project_dict(project)

    @endpoint
    def project_update(self, payload: dict) -> dict:
        with session_scope() as session:
            project = _get_project(session, _need(payload, "project_id"))
            if "name" in payload:
                project.name = str(payload["name"]).strip()[:160]
            if "description" in payload:
                project.description = str(payload["description"])[:2000]
            if "default_ref" in payload:
                project.default_ref = source_mod.validate_ref(str(payload["default_ref"] or ""))
            if "include_globs" in payload:
                project.include_globs = list(payload["include_globs"] or [])
            if "exclude_globs" in payload:
                project.exclude_globs = list(payload["exclude_globs"] or [])
            session.commit()
            session.refresh(project)
            return _project_dict(project)

    @endpoint
    def project_delete(self, payload: dict) -> dict:
        with session_scope() as session:
            project = _get_project(session, _need(payload, "project_id"))
            session.delete(project)
            return {"deleted": True}

    @endpoint
    def project_refs(self, payload: dict) -> dict:
        with session_scope() as session:
            project = _get_project(session, _need(payload, "project_id"))
            kind, location = project.source_kind.value, project.source_location
        # Read-only: never clone/fetch just to list branches.
        root = source_mod.locate(kind, location)
        if root is None or not source_mod.is_git_repository(root):
            return {"is_git": False, "branches": [], "tags": [], "current": ""}
        refs = source_mod.list_refs(root)
        return {
            "is_git": True,
            "branches": refs.get("branches", []),
            "tags": refs.get("tags", []),
            "current": source_mod.current_branch(root),
        }

    # ---------------------------------------------------------- analysis
    @endpoint
    def analysis_start(self, payload: dict) -> dict:
        project_id = _need(payload, "project_id")
        ref = source_mod.validate_ref(str(payload.get("ref") or ""))
        with session_scope() as session:
            project = _get_project(session, project_id)
            run = AnalysisRun(project_id=project.id, ref=ref or project.default_ref, status=JobStatus.PENDING)
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id
            result = _run_dict(run)

        job_manager.submit(
            run_id,
            "analysis",
            analysis_service.run_analysis,
            run_id,
            generate_diagrams=bool(payload.get("generate_diagrams", True)),
            include_history=bool(payload.get("include_history", True)),
        )
        return result

    @endpoint
    def analysis_status(self, payload: dict) -> dict:
        with session_scope() as session:
            return _run_dict(_get_run(session, _need(payload, "analysis_id")))

    @endpoint
    def analysis_cancel(self, payload: dict) -> dict:
        analysis_id = _need(payload, "analysis_id")
        cancelled = job_manager.cancel(analysis_id)
        return {"cancelled": cancelled}

    @endpoint
    def analyses_list(self, payload: dict) -> list[dict]:
        with session_scope() as session:
            query = select(AnalysisRun).order_by(AnalysisRun.created_at.desc()).limit(int(payload.get("limit") or 50))
            if payload.get("project_id"):
                query = query.where(AnalysisRun.project_id == payload["project_id"])
            return [_run_dict(run) for run in session.scalars(query).all()]

    @endpoint
    def analysis_delete(self, payload: dict) -> dict:
        with session_scope() as session:
            run = _get_run(session, _need(payload, "analysis_id"))
            analysis_id, graph_path = run.id, run.graph_path
            session.delete(run)
        if graph_path:
            # Resolve through the service so a data directory that moved since
            # the run still gets its graph file cleaned up.
            try:
                analysis_service.locate_graph(analysis_id, graph_path).unlink(missing_ok=True)
            except FileNotFoundError:
                pass
        return {"deleted": True}

    @endpoint
    def analysis_metrics(self, payload: dict) -> dict:
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            return {"metrics": run.metrics or {}, "stats": run.stats or {}}

    # ------------------------------------------------------------- scoring
    @endpoint
    def score_card(self, payload: dict) -> dict:
        """The full explainable scorecard for one analysis."""
        analysis_id = _need(payload, "analysis_id")
        with session_scope() as session:
            run = _succeeded_run(session, analysis_id)
            metrics = run.metrics or {}
            stats = run.stats or {}
            ref, commit = run.ref, run.commit_sha
            project = session.get(Project, run.project_id)
            project_id = run.project_id
            project_name = project.name if project else ""
        card = metrics.get("scorecard")
        if not card:
            raise BridgeError("This analysis predates the scorecard - re-run it to generate one.")
        return {
            "scorecard": card,
            "project": {"id": project_id, "name": project_name},
            "analysis": {"id": analysis_id, "ref": ref, "commit": (commit or "")[:8]},
            "stats": {
                "files_analyzed": stats.get("files_analyzed", 0),
                "languages": stats.get("languages", {}),
                "node_count": stats.get("node_count", 0),
                "edge_count": stats.get("edge_count", 0),
                "duration_seconds": stats.get("duration_seconds", 0),
            },
        }

    @endpoint
    def score_category(self, payload: dict) -> dict:
        """One category with every signal, evidence item and recommendation."""
        category_id = str(_need(payload, "category"))
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            card = (run.metrics or {}).get("scorecard") or {}
        for category in card.get("categories", []):
            if category["id"] == category_id:
                return {"category": category, "weights": card.get("weights", {}), "overall": card.get("overall", 0)}
        raise BridgeError(f"Unknown score category '{category_id}'")

    @endpoint
    def score_evidence(self, payload: dict) -> dict:
        """Every evidence row behind a single signal, with the source excerpt."""
        category_id = str(_need(payload, "category"))
        signal_id = str(_need(payload, "signal"))
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            card = (run.metrics or {}).get("scorecard") or {}
            project = _get_project(session, run.project_id)
            kind, location = project.source_kind.value, project.source_location
        for category in card.get("categories", []):
            if category["id"] != category_id:
                continue
            for signal in category.get("signals", []):
                if signal["id"] != signal_id:
                    continue
                evidence = [dict(item) for item in signal.get("evidence", [])]
                # locate() only - resolve() would clone/fetch remotes and
                # force-checkout local trees just to show a code excerpt.
                root = source_mod.locate(kind, location)
                if root is not None:
                    try:
                        for item in evidence:
                            item["excerpt"] = _read_excerpt(
                                root, item.get("file", ""), int(item.get("line") or 0)
                            )
                    except Exception:  # noqa: BLE001 - excerpts are a nicety, not a requirement
                        pass
                return {"signal": signal, "evidence": evidence, "category": category["label"]}
        raise BridgeError("That signal is not part of this scorecard")

    @endpoint
    def score_weights(self, _payload: dict) -> dict:
        return {
            "weights": scoring.load_weights(),
            "defaults": scoring.DEFAULT_WEIGHTS,
            "categories": [
                {"id": key, "label": scoring.CATEGORY_LABELS[key], "icon": scoring.CATEGORY_ICONS[key]}
                for key in scoring.CATEGORY_ORDER
            ],
        }

    @endpoint
    def score_weights_save(self, payload: dict) -> dict:
        weights = scoring.save_weights(payload.get("weights") or {})
        return {"weights": weights}

    @endpoint
    def score_weights_reset(self, _payload: dict) -> dict:
        return {"weights": scoring.reset_weights()}

    @endpoint
    def score_recompute(self, payload: dict) -> dict:
        """Re-score a stored analysis with the current weights - no re-parse needed."""
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            graph = analysis_service.load_graph(run)
            metrics = dict(run.metrics or {})
            signals = metrics.get("signals") or {}
            history = (run.stats or {}).get("history") or {}
            card = scoring.rescore(graph, metrics, signals, history)
            metrics["scorecard"] = card
            metrics["score"] = card["overall"]
            metrics["grade"] = card["grade"]
            run.metrics = metrics
            session.commit()
        return {"scorecard": card}

    @endpoint
    def score_trend(self, payload: dict) -> dict:
        """Score history across every successful run of a project."""
        limit = max(2, min(int(payload.get("limit") or 30), 100))
        with session_scope() as session:
            project_id = payload.get("project_id")
            if not project_id:
                run = _get_run(session, _need(payload, "analysis_id"))
                project_id = run.project_id
            query = (
                select(AnalysisRun)
                .where(AnalysisRun.project_id == project_id, AnalysisRun.status == JobStatus.SUCCEEDED)
                .order_by(AnalysisRun.created_at.asc())
                .limit(limit)
            )
            runs = list(session.scalars(query).all())
            points = []
            for run in runs:
                card = (run.metrics or {}).get("scorecard")
                if not card:
                    continue
                points.append(
                    {
                        "analysis_id": run.id,
                        "at": _iso(run.finished_at or run.created_at),
                        "ref": run.ref,
                        "commit": (run.commit_sha or "")[:8],
                        "overall": card.get("overall", 0),
                        "grade": card.get("grade", ""),
                        "categories": card.get("category_index", {}),
                        "files": (run.stats or {}).get("files_analyzed", 0),
                        "nodes": (run.stats or {}).get("node_count", 0),
                    }
                )
        deltas = {}
        if len(points) >= 2:
            first, last = points[0], points[-1]
            deltas = {
                "overall": last["overall"] - first["overall"],
                "categories": {
                    key: last["categories"].get(key, 0) - first["categories"].get(key, 0)
                    for key in scoring.CATEGORY_ORDER
                },
            }
        return {"points": points, "project_id": project_id, "deltas": deltas}

    @endpoint
    def score_files(self, payload: dict) -> dict:
        """Per-file risk rows that drive the hotspot heatmap and treemap."""
        limit = max(20, min(int(payload.get("limit") or 300), 2000))
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            graph = analysis_service.load_graph(run)
        rows: list[dict[str, Any]] = []
        for node in graph.by_kind("file"):
            attrs = node.attributes or {}
            loc = int(attrs.get("loc") or 0)
            if not loc:
                continue
            findings = int(attrs.get("findings") or 0)
            risky = int(attrs.get("risk_findings") or 0)
            markers = int(attrs.get("debt_markers") or 0)
            churn = int(attrs.get("change_count") or 0)
            risk = risky * 6 + (findings - risky) * 2 + markers + min(loc / 120, 8) + min(churn / 4, 10)
            rows.append(
                {
                    "file": node.file or node.qualified_name,
                    "module": node.module,
                    "language": node.language,
                    "loc": loc,
                    "comment_lines": int(attrs.get("comment_lines") or 0),
                    "is_test": bool(attrs.get("is_test")),
                    "findings": findings,
                    "risk_findings": risky,
                    "debt_markers": markers,
                    "changes": churn,
                    "authors": int(attrs.get("authors") or 0),
                    "last_changed": attrs.get("last_changed", ""),
                    "risk": round(risk, 1),
                }
            )
        rows.sort(key=lambda r: -r["risk"])
        return {
            "files": rows[:limit],
            "total_files": len(rows),
            "total_loc": sum(r["loc"] for r in rows),
            "modules": sorted({r["module"] for r in rows if r["module"]})[:200],
        }

    @endpoint
    def score_file_detail(self, payload: dict) -> dict:
        """Everything the quality scan recorded about one file.

        The hotspot rows only carry counters, which tell you a file is risky but
        not why. The scan already produces a rule, a reason, a fix and a snippet
        for every finding, so the drawer serves those verbatim rather than
        making the reader guess what "7 findings" refers to.
        """
        path = str(_need(payload, "file"))
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            signals = (run.metrics or {}).get("signals") or {}
            graph = analysis_service.load_graph(run)

        def for_file(items: Any) -> list[dict[str, Any]]:
            return [item for item in (items or []) if isinstance(item, dict) and item.get("file") == path]

        findings = for_file(signals.get("findings"))
        findings.sort(key=lambda f: (fixes.SEVERITY_ORDER.get(f.get("severity", "low"), 9), f.get("line") or 0))
        complexity = signals.get("complexity") or {}
        symbol_docs = signals.get("symbol_docs") or {}

        node = next(
            (n for n in graph.by_kind("file") if (n.file or n.qualified_name) == path),
            None,
        )
        attrs = (node.attributes if node else {}) or {}
        symbols = [
            {
                "name": sym.name,
                "kind": sym.kind,
                "line": sym.line,
                "complexity": sym.attributes.get("complexity"),
                "documented": bool(str(sym.attributes.get("docstring") or "").strip()),
            }
            for sym in graph.nodes.values()
            if sym.file == path
            and not sym.external
            and sym.kind in ("class", "interface", "abstract_class", "struct", "function", "method")
        ]
        symbols.sort(key=lambda s: s["line"] or 0)

        reported = int(attrs.get("findings") or 0)
        return {
            "file": path,
            "module": node.module if node else "",
            "language": node.language if node else "",
            "loc": int(attrs.get("loc") or 0),
            "comment_lines": int(attrs.get("comment_lines") or 0),
            "is_test": bool(attrs.get("is_test")),
            "changes": int(attrs.get("change_count") or 0),
            "authors": int(attrs.get("authors") or 0),
            "last_changed": attrs.get("last_changed", ""),
            "findings": findings,
            # The scan keeps only the worst 250 findings across the whole
            # repository, so say so rather than quietly showing fewer than the
            # counter promised.
            "findings_reported": reported,
            "findings_truncated": reported > len(findings),
            "debt_markers": for_file(signals.get("debt_markers")),
            "complex_functions": for_file(complexity.get("offenders")),
            "wide_signatures": for_file(complexity.get("wide_signatures")),
            "undocumented": for_file(symbol_docs.get("undocumented")),
            "symbols": symbols[:200],
        }

    @endpoint
    def analysis_history(self, payload: dict) -> dict:
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            stored = (run.stats or {}).get("history")
            # Only a *successful* report is worth caching. A stored "not a git
            # repository" answer used to be returned forever, so a project that
            # gained a repository after its first analysis showed an empty
            # history page until it was analysed again.
            if stored and stored.get("available"):
                return stored
            project = _get_project(session, run.project_id)
            kind, location = project.source_kind.value, project.source_location
        root = source_mod.locate(kind, location)
        if root is None:
            return {
                "available": False,
                "failed": True,
                "reason_key": "history.reasonMissing",
                "reason": "The project source folder could not be found on this computer.",
            }
        return git_history.analyze(root, max_commits=settings.history_max_commits)

    @endpoint
    def analysis_commit_graph(self, payload: dict) -> dict:
        """The commit DAG for the repository behind an analysis."""
        limit = max(20, min(int(payload.get("limit") or 300), 2000))
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            project = _get_project(session, run.project_id)
            kind, location = project.source_kind.value, project.source_location
        root = source_mod.locate(kind, location)
        if root is None:
            return {
                "available": False,
                "failed": True,
                "reason_key": "history.reasonMissing",
                "reason": "The project source folder could not be found on this computer.",
                "commits": [],
                "lanes": 0,
            }
        return commit_graph.build(root, max_commits=limit)

    def _writable_root(self, analysis_id: str) -> Path:
        """Working tree for an analysis, refusing sources we must not mutate.

        A remote git project is resolved into a throwaway clone, so editing it
        would silently discard the user's work on the next analysis.
        """
        with session_scope() as session:
            run = _succeeded_run(session, analysis_id)
            project = _get_project(session, run.project_id)
            kind, location, ref = project.source_kind.value, project.source_location, run.ref
        if kind != "local":
            raise BridgeError("Fixes can only be applied to a local project folder.")
        return source_mod.resolve(kind, location, ref).root

    @endpoint
    def analysis_fix_proposals(self, payload: dict) -> dict:
        """Repair proposals. Read-only - nothing is written here.

        Runs in one of two modes. Without a provider the deterministic rules and
        the structural findings from the analysis are returned as-is. With a
        provider configured, the findings that no rule can repair mechanically
        are additionally sent for a real patch, which is still only a proposal:
        the diff is computed locally and applying it remains a separate,
        confirmed call.
        """
        limit = max(10, min(int(payload.get("limit") or fixes.DEFAULT_LIMIT), 500))
        rules = [str(r) for r in (payload.get("rules") or []) if r]
        include_cosmetic = bool(payload.get("include_cosmetic"))
        analysis_id = _need(payload, "analysis_id")
        root = self._writable_root(analysis_id)
        with session_scope() as session:
            run = _succeeded_run(session, analysis_id)
            run_metrics = run.metrics or {}
            provider = None if payload.get("offline") else provider_service.build_provider(session)

        result = fixes.propose(
            root, limit=limit, rules=rules or None, include_cosmetic=include_cosmetic, metrics=run_metrics
        )
        result["ai_available"] = provider is not None
        if provider is None or not result.get("proposals"):
            return result

        language = str(payload.get("language") or "en")
        try:
            enriched = asyncio.run(
                fixes.enrich_with_ai(root, result["proposals"], provider, language=language)
            )
        except Exception as exc:  # noqa: BLE001 - the static result is still valid
            log.warning("AI fix enrichment failed: %s", exc)
            result["ai_error"] = str(exc)
            return result
        result.update(enriched)
        # Provider was consulted even when every candidate failed or was skipped.
        result["mode"] = "ai"
        return result

    @endpoint
    def analysis_fix_preview(self, payload: dict) -> dict:
        """Recompute one file's diff against its contents right now."""
        root = self._writable_root(_need(payload, "analysis_id"))
        rules = [str(r) for r in (payload.get("rules") or []) if r]
        try:
            return fixes.preview(root, str(_need(payload, "file")), rules)
        except fixes.FixError as exc:
            raise BridgeError(str(exc)) from exc

    @endpoint
    def analysis_apply_fixes(self, payload: dict) -> dict:
        """Write selected fixes to disk.

        Requires an explicit ``confirm`` flag from the review UI - proposals are
        never applied as a side effect of generating or previewing them.
        """
        if not payload.get("confirm"):
            raise BridgeError("Fixes must be confirmed before they are applied.")
        selections = [s for s in (payload.get("selections") or []) if isinstance(s, dict)]
        if not selections:
            raise BridgeError("Select at least one fix to apply.")
        root = self._writable_root(_need(payload, "analysis_id"))
        try:
            return fixes.apply(root, selections, confirm=True)
        except fixes.FixError as exc:
            raise BridgeError(str(exc)) from exc

    @endpoint
    def analysis_graph(self, payload: dict) -> dict:
        limit = max(10, min(int(payload.get("limit") or 400), 4000))
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            graph = analysis_service.load_graph(run)

        ranked = sorted(graph.nodes.values(), key=lambda n: graph.degree(n.id), reverse=True)[:limit]
        keep = {node.id for node in ranked}
        return {
            "nodes": [
                {
                    "id": node.id,
                    "kind": node.kind,
                    "name": node.name,
                    "qualified_name": node.qualified_name,
                    "module": node.module,
                    "language": node.language,
                    "file": node.file,
                    "external": node.external,
                    "degree": graph.degree(node.id),
                }
                for node in ranked
            ],
            "edges": [
                {"source": e.source, "target": e.target, "kind": e.kind, "label": e.label}
                for e in graph.edges.values()
                if e.source in keep and e.target in keep
            ],
            "truncated": len(graph.nodes) > len(keep),
            "total_nodes": len(graph.nodes),
            "stats": graph.stats(),
        }

    @endpoint
    def analysis_search(self, payload: dict) -> list[dict]:
        term = str(_need(payload, "query")).strip().lower()
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            graph = analysis_service.load_graph(run)
        hits = [
            {
                "id": node.id,
                "kind": node.kind,
                "name": node.name,
                "qualified_name": node.qualified_name,
                "file": node.file,
                "module": node.module,
            }
            for node in graph.nodes.values()
            if term in node.name.lower() or term in node.qualified_name.lower()
        ]
        hits.sort(key=lambda h: len(h["name"]))
        return hits[:50]

    # ---------------------------------------------------------- diagrams
    @endpoint
    def diagram_kinds(self, _payload: dict) -> list[str]:
        return DEFAULT_ORDER

    @endpoint
    def diagrams_list(self, payload: dict) -> list[dict]:
        with session_scope() as session:
            _get_run(session, _need(payload, "analysis_id"))
            query = (
                select(Diagram)
                .where(Diagram.analysis_id == payload["analysis_id"])
                .order_by(Diagram.created_at.asc())
            )
            diagrams = session.scalars(query).all()
            order = {kind: idx for idx, kind in enumerate(DEFAULT_ORDER)}
            items = [_diagram_dict(d) for d in diagrams]
            items.sort(key=lambda d: (order.get(d["kind"], 99), d["created_at"]))
            return items

    @endpoint
    def diagram_get(self, payload: dict) -> dict:
        with session_scope() as session:
            return _diagram_dict(_get_diagram(session, _need(payload, "diagram_id")))

    @endpoint
    def diagram_generate(self, payload: dict) -> dict:
        kind = str(_need(payload, "kind"))
        if kind not in DIAGRAM_KINDS:
            raise BridgeError(f"Unsupported diagram kind: {kind}")
        filters = DiagramFilters.from_payload(payload.get("filters") or {})

        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            graph = analysis_service.load_graph(run)
            result = generate(kind, graph, filters)
            diagram = Diagram(
                analysis_id=run.id,
                kind=result.kind,
                title=payload.get("title") or result.title,
                scope={**result.scope, "filters": filters.to_dict()},
                mermaid=result.mermaid,
                plantuml=result.plantuml,
                payload={**result.payload, "notes": result.notes},
            )
            session.add(diagram)
            session.commit()
            session.refresh(diagram)
            return _diagram_dict(diagram)

    @endpoint
    def diagram_update(self, payload: dict) -> dict:
        with session_scope() as session:
            diagram = _get_diagram(session, _need(payload, "diagram_id"))
            session.add(
                DiagramVersion(
                    diagram_id=diagram.id,
                    version=diagram.version,
                    mermaid=diagram.mermaid,
                    plantuml=diagram.plantuml,
                    payload=diagram.payload or {},
                    note=str(payload.get("note") or "")[:2000],
                )
            )
            if "mermaid" in payload:
                diagram.mermaid = str(payload["mermaid"])
            if "plantuml" in payload:
                diagram.plantuml = str(payload["plantuml"])
            if "title" in payload:
                diagram.title = str(payload["title"])[:240]
            diagram.version += 1
            session.commit()
            session.refresh(diagram)
            return _diagram_dict(diagram)

    @endpoint
    def diagram_versions(self, payload: dict) -> list[dict]:
        with session_scope() as session:
            _get_diagram(session, _need(payload, "diagram_id"))
            versions = session.scalars(
                select(DiagramVersion)
                .where(DiagramVersion.diagram_id == payload["diagram_id"])
                .order_by(DiagramVersion.version.desc())
            ).all()
            return [
                {
                    "id": v.id,
                    "version": v.version,
                    "note": v.note,
                    "mermaid": v.mermaid,
                    "created_at": _iso(v.created_at),
                }
                for v in versions
            ]

    @endpoint
    def diagram_restore(self, payload: dict) -> dict:
        with session_scope() as session:
            version = session.get(DiagramVersion, _need(payload, "version_id"))
            if version is None:
                raise BridgeError("Version not found")
            diagram = _get_diagram(session, version.diagram_id)
            session.add(
                DiagramVersion(
                    diagram_id=diagram.id,
                    version=diagram.version,
                    mermaid=diagram.mermaid,
                    plantuml=diagram.plantuml,
                    payload=diagram.payload or {},
                    note=f"snapshot before restoring v{version.version}",
                )
            )
            diagram.mermaid = version.mermaid
            diagram.plantuml = version.plantuml
            diagram.payload = version.payload or {}
            diagram.version += 1
            session.commit()
            session.refresh(diagram)
            return _diagram_dict(diagram)

    @endpoint
    def diagram_approval(self, payload: dict) -> dict:
        state = str(_need(payload, "state"))
        try:
            approval = ApprovalState(state)
        except ValueError as exc:
            raise BridgeError(f"Unknown approval state: {state}") from exc
        with session_scope() as session:
            diagram = _get_diagram(session, _need(payload, "diagram_id"))
            diagram.approval_state = approval
            session.commit()
            session.refresh(diagram)
            return _diagram_dict(diagram)

    # ---------------------------------------------------------- comments
    @endpoint
    def comments_list(self, payload: dict) -> list[dict]:
        with session_scope() as session:
            _get_diagram(session, _need(payload, "diagram_id"))
            comments = session.scalars(
                select(Comment)
                .where(Comment.diagram_id == payload["diagram_id"])
                .order_by(Comment.created_at.asc())
            ).all()
            return [_comment_dict(c) for c in comments]

    @endpoint
    def comment_add(self, payload: dict) -> dict:
        with session_scope() as session:
            diagram = _get_diagram(session, _need(payload, "diagram_id"))
            comment = Comment(
                diagram_id=diagram.id,
                body=str(_need(payload, "body"))[:4000],
                anchor=str(payload.get("anchor") or "")[:240],
            )
            session.add(comment)
            session.commit()
            session.refresh(comment)
            return _comment_dict(comment)

    @endpoint
    def comment_toggle(self, payload: dict) -> dict:
        with session_scope() as session:
            comment = session.get(Comment, _need(payload, "comment_id"))
            if comment is None:
                raise BridgeError("Comment not found")
            comment.resolved = not comment.resolved
            session.commit()
            session.refresh(comment)
            return _comment_dict(comment)

    @endpoint
    def comment_delete(self, payload: dict) -> dict:
        with session_scope() as session:
            comment = session.get(Comment, _need(payload, "comment_id"))
            if comment is None:
                raise BridgeError("Comment not found")
            session.delete(comment)
            return {"deleted": True}

    # ---------------------------------------------------------------- AI
    @endpoint
    def ai_explain(self, payload: dict) -> dict:
        language = _language(payload)
        with session_scope() as session:
            diagram = _get_diagram(session, _need(payload, "diagram_id"))
            cached = (diagram.explanation or {}).get(language)
            if cached and not payload.get("refresh"):
                return cached
            run = _succeeded_run(session, diagram.analysis_id)
            graph = analysis_service.load_graph(run)
            provider = provider_service.build_provider(session)
            result = asyncio.run(
                insights.explain_diagram(graph, _diagram_dict(diagram), language, provider)
            )
            diagram.explanation = {**(diagram.explanation or {}), language: result}
            session.commit()
            return result

    @endpoint
    def ai_review(self, payload: dict) -> dict:
        language = _language(payload)
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            graph = analysis_service.load_graph(run)
            provider = provider_service.build_provider(session)
            return asyncio.run(insights.review_architecture(graph, run.metrics or {}, language, provider))

    @endpoint
    def ai_refactor(self, payload: dict) -> dict:
        language = _language(payload)
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            graph = analysis_service.load_graph(run)
            provider = provider_service.build_provider(session)
            return asyncio.run(insights.refactoring_suggestions(graph, run.metrics or {}, language, provider))

    @endpoint
    def ai_query(self, payload: dict) -> dict:
        """Turn a natural-language request into a diagram and render it."""
        language = _language(payload)
        prompt = str(_need(payload, "prompt"))[:2000]
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            graph = analysis_service.load_graph(run)
            provider = provider_service.build_provider(session)
            spec = asyncio.run(insights.interpret_request(graph, prompt, language, provider))

            filters = DiagramFilters.from_payload(spec.get("filters") or {})
            try:
                result = generate(spec["kind"], graph, filters)
            except EmptyDiagramError:
                # Narrow focus/module filters from NL interpretation often match
                # nothing; retry once with a project-wide scope before failing.
                relaxed = DiagramFilters(
                    scope="project",
                    include_external=filters.include_external,
                    detail=filters.detail,
                    max_nodes=filters.max_nodes,
                    languages=filters.languages,
                )
                result = generate(spec["kind"], graph, relaxed)
                filters = relaxed
                spec = {**spec, "filters": filters.to_dict(), "relaxed": True}
            diagram = Diagram(
                analysis_id=run.id,
                kind=result.kind,
                title=spec.get("title") or result.title,
                scope={**result.scope, "filters": filters.to_dict(), "prompt": prompt},
                mermaid=result.mermaid,
                plantuml=result.plantuml,
                payload={**result.payload, "notes": result.notes},
            )
            session.add(diagram)
            session.commit()
            session.refresh(diagram)
            return {"spec": spec, "diagram": _diagram_dict(diagram)}

    @endpoint
    def ai_translate(self, payload: dict) -> dict:
        target = str(payload.get("target") or "he")
        with session_scope() as session:
            provider = provider_service.build_provider(session)
            return asyncio.run(insights.translate(str(_need(payload, "text")), target, provider))

    # ---------------------------------------------------------- provider
    @endpoint
    def provider_get(self, _payload: dict) -> dict:
        with session_scope() as session:
            config = provider_service.find_config(session)
            fallback = None
            if config is None and settings.ai_base_url and settings.ai_model:
                from app.ai.provider import ProviderSettings

                fallback = ProviderSettings.from_settings()
            data = provider_service.to_public(config, fallback)
            data["configured"] = bool(data)
            return data

    @endpoint
    def provider_save(self, payload: dict) -> dict:
        body = {
            "name": str(payload.get("name") or "default")[:120],
            "base_url": str(_need(payload, "base_url")),
            "model": str(_need(payload, "model")),
            "api_key": str(payload.get("api_key") or ""),
            "clear_api_key": bool(payload.get("clear_api_key")),
            "headers": payload.get("headers") or {},
            "temperature": float(payload.get("temperature", 0.2)),
            "max_tokens": int(payload.get("max_tokens", 2048)),
            "timeout_seconds": int(payload.get("timeout_seconds", 120)),
            "max_retries": int(payload.get("max_retries", 3)),
            "streaming": bool(payload.get("streaming", True)),
        }
        with session_scope() as session:
            config = provider_service.upsert(session, body)
            return provider_service.to_public(config, None)

    @endpoint
    def provider_test(self, _payload: dict) -> dict:
        with session_scope() as session:
            provider = provider_service.build_provider(session)
        if provider is None:
            raise BridgeError("No AI provider is configured yet")
        try:
            return asyncio.run(provider.health())
        except Exception as exc:  # noqa: BLE001 - reported to the user verbatim
            return {"ok": False, "error": str(exc), "model": provider.config.model}

    @endpoint
    def provider_clear(self, _payload: dict) -> dict:
        with session_scope() as session:
            config = provider_service.find_config(session)
            if config is not None:
                session.delete(config)
        return {"cleared": True}

    # ------------------------------------------------------------ export
    @endpoint
    def export_diagram(self, payload: dict) -> dict:
        fmt = str(payload.get("format") or "mermaid")
        if fmt not in exporters.EXPORT_FORMATS:
            raise BridgeError(f"Unsupported export format: {fmt}")
        language = _language(payload)
        with session_scope() as session:
            diagram = _get_diagram(session, _need(payload, "diagram_id"))
            data = _diagram_dict(diagram)
        content = exporters.export(data, fmt, language=language)
        stem = (data["title"] or data["kind"]).replace(" ", "-").lower()[:60]
        return {
            "content": content,
            "mime": exporters.MIME_TYPES.get(fmt, "text/plain"),
            "filename": f"{stem}.{exporters.FILE_EXTENSIONS.get(fmt, 'txt')}",
        }

    @endpoint
    def export_bundle(self, payload: dict) -> dict:
        language = _language(payload)
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            project = _get_project(session, run.project_id)
            diagrams = [
                _diagram_dict(d)
                for d in session.scalars(
                    select(Diagram).where(Diagram.analysis_id == run.id).order_by(Diagram.created_at.asc())
                ).all()
            ]
            graph = analysis_service.load_graph(run)
            provider = provider_service.build_provider(session)
            review = asyncio.run(insights.review_architecture(graph, run.metrics or {}, language, provider))
            name = project.name
        content = exporters.bundle_markdown(name, diagrams, review, language)
        stem = name.replace(" ", "-").lower()[:60] or "architecture"
        return {"content": content, "mime": "text/markdown", "filename": f"{stem}-architecture.md"}

    # ----------------------------------------------------------- compare
    @endpoint
    def compare_analyses(self, payload: dict) -> dict:
        language = _language(payload)
        with session_scope() as session:
            base_run = _succeeded_run(session, _need(payload, "base_analysis_id"))
            head_run = _succeeded_run(session, _need(payload, "head_analysis_id"))
            base_graph = analysis_service.load_graph(base_run)
            head_graph = analysis_service.load_graph(head_run)
            diff = architecture_diff.compare(base_graph, head_graph)
            provider = provider_service.build_provider(session)
            narrative = asyncio.run(insights.explain_comparison(diff, language, provider))
            return {
                "base": _run_dict(base_run),
                "head": _run_dict(head_run),
                "diff": diff,
                "narrative": narrative,
            }

    # -------------------------------------------------------- diagnostics
    @endpoint
    def recompute_metrics(self, payload: dict) -> dict:
        with session_scope() as session:
            run = _succeeded_run(session, _need(payload, "analysis_id"))
            graph = analysis_service.load_graph(run)
            metrics = metrics_mod.compute(graph)
            metrics["centrality"] = metrics_mod.centrality(graph)
            metrics["signals"] = (run.metrics or {}).get("signals", {})
            history = (run.stats or {}).get("history") or {}
            card = scoring.rescore(graph, metrics, metrics["signals"], history)
            metrics["scorecard"] = card
            metrics["score"] = card["overall"]
            metrics["grade"] = card["grade"]
            run.metrics = metrics
            session.commit()
            return metrics

    @endpoint
    def settings_summary(self, _payload: dict) -> dict:
        return {
            "data_dir": str(settings.resolved_data_dir),
            "database": settings.resolved_database_url,
            "allowed_roots": [str(p) for p in settings.local_root_allow_list],
            "allow_remote_clone": settings.allow_remote_clone,
            "max_files": settings.max_files,
            "env_api_key": mask_secret(settings.ai_api_key),
        }
