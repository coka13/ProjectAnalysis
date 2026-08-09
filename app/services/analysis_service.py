"""Analysis orchestration: runs the pipeline in a background job and persists results."""

from __future__ import annotations

import datetime as dt
import logging
import threading
from pathlib import Path
from typing import Any

from app.config import settings
from app.core import cache
from app.diagrams.base import DiagramFilters
from app.diagrams.registry import generate_all
from app.engine.pipeline import AnalysisCancelled, analyze_project
from app.graph import scoring
from app.graph.model import KnowledgeGraph
from app.history import git_history
from app.ingest import source as source_mod
from app.models import AnalysisRun, Diagram, JobStatus, Project
from app.db import session_scope

log = logging.getLogger("aai.analysis")

_GRAPH_CACHE_TTL = 900


def graph_path_for(analysis_id: str) -> Path:
    directory = settings.resolved_data_dir / "graphs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{analysis_id}.json"


def locate_graph(analysis_id: str, stored_path: str) -> Path:
    """Find a run's graph file even when the data directory has moved.

    ``graph_path`` is persisted as an absolute path, so anything that relocates
    the data directory - renaming the application folder, pointing AAI_DATA_DIR
    somewhere else, restoring a backup on another machine - orphans every run
    recorded before the move. The file itself is always written to
    ``<data_dir>/graphs/<analysis_id>.json``, so that canonical location is a
    reliable second place to look.
    """
    stored = Path(stored_path) if stored_path else None
    if stored and stored.exists():
        return stored
    canonical = graph_path_for(analysis_id)
    if canonical.exists():
        return canonical
    raise FileNotFoundError("stored graph is missing on disk")


def load_graph(analysis: AnalysisRun) -> KnowledgeGraph:
    """Load a persisted knowledge graph, memoised in process memory."""
    if not analysis.graph_path:
        raise FileNotFoundError("analysis has no stored graph")
    path = locate_graph(analysis.id, analysis.graph_path)
    # Repair the stale row so the fallback runs once rather than on every call.
    if str(path) != analysis.graph_path:
        analysis.graph_path = str(path)
    key = cache.make_key("graph", analysis.id, str(path))
    cached = cache.memory_get(key)
    if isinstance(cached, KnowledgeGraph):
        return cached
    graph = KnowledgeGraph.load(path)
    cache.memory_set(key, graph, ttl=_GRAPH_CACHE_TTL)
    return graph


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _set_progress(analysis_id: str, fraction: float, stage: str) -> None:
    with session_scope() as session:
        run = session.get(AnalysisRun, analysis_id)
        if run and run.status == JobStatus.RUNNING:
            run.progress = fraction
            run.stage = stage


def run_analysis(
    analysis_id: str,
    *,
    generate_diagrams: bool = True,
    include_history: bool = True,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Background entry point. Owns its own DB sessions - never share the request session."""
    with session_scope() as session:
        run = session.get(AnalysisRun, analysis_id)
        if run is None:
            raise ValueError("analysis run not found")
        project = session.get(Project, run.project_id)
        if project is None:
            raise ValueError("project not found")
        run.status = JobStatus.RUNNING
        run.started_at = _now()
        run.stage = "resolving source"
        run.progress = 0.01
        run.error = ""
        payload = {
            "source_kind": project.source_kind.value,
            "location": project.source_location,
            "ref": run.ref or project.default_ref,
            "include_globs": list(project.include_globs or []),
            "exclude_globs": list(project.exclude_globs or []),
            "project_name": project.name,
        }

    resolved = None
    try:
        resolved = source_mod.resolve(payload["source_kind"], payload["location"], payload["ref"])
        if cancel_event is not None and cancel_event.is_set():
            raise AnalysisCancelled("analysis cancelled")

        graph, report, warnings = analyze_project(
            resolved.root,
            include_globs=payload["include_globs"],
            exclude_globs=payload["exclude_globs"],
            progress=lambda fraction, stage: _set_progress(analysis_id, fraction * 0.75, stage),
            cancel_event=cancel_event,
        )

        history: dict[str, Any] = {}
        if include_history and resolved.is_git:
            _set_progress(analysis_id, 0.8, "analysing repository history")
            try:
                history = git_history.analyze(resolved.root, graph, max_commits=settings.history_max_commits)
            except Exception as exc:  # noqa: BLE001 - history is best effort
                warnings.append(f"history analysis skipped: {exc}")

        if history.get("available"):
            # Churn hotspots only exist once the git pass has run, so the scorecard
            # is rebuilt with them instead of being scored twice on partial data.
            _set_progress(analysis_id, 0.84, "scoring architecture")
            computed = report.get("metrics", {})
            try:
                scorecard = scoring.rescore(graph, computed, computed.get("signals", {}), history)
                computed["scorecard"] = scorecard
                computed["score"] = scorecard["overall"]
                computed["grade"] = scorecard["grade"]
            except Exception as exc:  # noqa: BLE001 - keep the pipeline scorecard
                warnings.append(f"history-aware scoring skipped: {exc}")

        stored_path = graph_path_for(analysis_id)
        graph.save(stored_path)

        diagram_count = 0
        skipped: dict[str, str] = {}
        if generate_diagrams:
            _set_progress(analysis_id, 0.88, "rendering diagrams")
            results, skipped = generate_all(graph, DiagramFilters())
            with session_scope() as session:
                for result in results:
                    session.add(
                        Diagram(
                            analysis_id=analysis_id,
                            kind=result.kind,
                            title=result.title,
                            scope=result.scope,
                            mermaid=result.mermaid,
                            plantuml=result.plantuml,
                            payload={**result.payload, "notes": result.notes},
                        )
                    )
                diagram_count = len(results)

        with session_scope() as session:
            run = session.get(AnalysisRun, analysis_id)
            if run is None:
                raise ValueError("analysis run disappeared")
            run.status = JobStatus.SUCCEEDED
            run.progress = 1.0
            run.stage = "completed"
            run.commit_sha = resolved.commit_sha
            run.ref = resolved.ref or payload["ref"]
            run.graph_path = str(stored_path)
            run.metrics = report.get("metrics", {})
            run.stats = {
                **report.get("stats", {}),
                "warnings": warnings[:50],
                "diagrams": diagram_count,
                "skipped_diagrams": skipped,
                "history": history,
            }
            run.finished_at = _now()

        log.info("analysis %s completed (%s diagrams)", analysis_id, diagram_count)
        return {"analysis_id": analysis_id, "diagrams": diagram_count, "warnings": warnings}

    except AnalysisCancelled:
        _finish_with_status(analysis_id, JobStatus.CANCELLED, "cancelled by user")
        return {"analysis_id": analysis_id, "status": "cancelled"}
    except Exception as exc:  # noqa: BLE001 - surfaced to the client through the run record
        log.exception("analysis %s failed", analysis_id)
        _finish_with_status(analysis_id, JobStatus.FAILED, f"{exc.__class__.__name__}: {exc}")
        raise


def _finish_with_status(analysis_id: str, status: JobStatus, message: str) -> None:
    with session_scope() as session:
        run = session.get(AnalysisRun, analysis_id)
        if run is None:
            return
        run.status = status
        run.error = message[:2000]
        run.stage = status.value
        run.finished_at = _now()
