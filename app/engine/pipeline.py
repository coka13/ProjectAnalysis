"""Analysis pipeline: scan -> parse -> resolve -> enrich -> measure."""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.analyzers.base import AnalysisContext, analyzers_for
from app.engine.enrich import enrich
from app.engine.quality import scan as scan_quality
from app.engine.resolver import Resolver
from app.graph import metrics as metrics_module
from app.graph import scoring
from app.graph.model import KnowledgeGraph, NodeKind
from app.ingest.walker import walk

log = logging.getLogger("aai.pipeline")

ProgressCallback = Callable[[float, str], None]


class AnalysisCancelled(RuntimeError):
    pass


def analyze_project(
    root: Path,
    *,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[KnowledgeGraph, dict[str, Any], list[str]]:
    """Run the full analysis pipeline over ``root``."""
    started = time.perf_counter()
    graph = KnowledgeGraph()
    ctx = AnalysisContext(root=root, graph=graph)

    def report(fraction: float, stage: str) -> None:
        if progress:
            progress(round(min(max(fraction, 0.0), 1.0), 3), stage)

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise AnalysisCancelled("analysis cancelled")

    report(0.02, "scanning files")
    files = list(walk(root, include_globs=include_globs, exclude_globs=exclude_globs))
    total = len(files) or 1
    log.info("scanning %s files under %s", total, root)

    language_counter: Counter[str] = Counter()
    parsed = 0
    for index, source in enumerate(files):
        check_cancelled()
        applicable = analyzers_for(source)
        if not applicable:
            continue
        language_counter[source.language] += 1
        for analyzer in applicable:
            try:
                analyzer.analyze(source, ctx)
            except Exception as exc:  # noqa: BLE001 - a single bad file must not fail the run
                ctx.warnings.append(f"{source.relative_path}: {analyzer.name} failed ({exc.__class__.__name__})")
                log.debug("analyzer %s failed on %s", analyzer.name, source.relative_path, exc_info=True)
        parsed += 1
        if index % 25 == 0:
            report(0.05 + 0.55 * (index / total), f"analyzing {source.relative_path}")

    ctx.file_count = parsed
    check_cancelled()

    report(0.65, "resolving references")
    resolution = Resolver(ctx).run()

    check_cancelled()
    report(0.78, "deriving architecture")
    enrich(graph)

    check_cancelled()
    report(0.84, "scanning quality signals")
    signals = scan_quality(files, graph)

    check_cancelled()
    report(0.9, "computing metrics")
    computed = metrics_module.compute(graph)
    computed["centrality"] = metrics_module.centrality(graph)
    computed["signals"] = signals

    report(0.94, "scoring architecture")
    scorecard = scoring.rescore(graph, computed, signals)
    computed["scorecard"] = scorecard
    computed["score"] = scorecard["overall"]
    computed["grade"] = scorecard["grade"]

    duration = time.perf_counter() - started
    stats: dict[str, Any] = {
        "files_scanned": len(files),
        "files_analyzed": parsed,
        "languages": dict(language_counter.most_common()),
        "duration_seconds": round(duration, 2),
        "warnings": len(ctx.warnings),
        "resolution": resolution,
        **graph.stats(),
    }
    stats["primary_language"] = language_counter.most_common(1)[0][0] if language_counter else ""
    stats["components"] = len(graph.by_kind(NodeKind.COMPONENT))
    stats["api_endpoints"] = len(graph.by_kind(NodeKind.API_ENDPOINT))
    stats["tables"] = len(graph.by_kind(NodeKind.TABLE))
    stats["containers"] = len(graph.by_kind(NodeKind.CONTAINER))

    graph.meta = {
        "root": str(root),
        "stats": stats,
        "metrics": computed,
    }
    report(1.0, "completed")
    log.info("analysis finished in %.2fs: %s nodes / %s edges", duration, len(graph.nodes), len(graph.edges))
    return graph, {"stats": stats, "metrics": computed}, ctx.warnings[:200]
