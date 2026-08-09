"""The views, registered against the shell."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for typing
    from app.ui.shell import MainWindow


def register_all(window: "MainWindow") -> None:
    """Bind every view key the navigation rail can reach."""
    from app.ui.views.about import AboutView
    from app.ui.views.analyses import AnalysesView
    from app.ui.views.compare import CompareView
    from app.ui.views.dashboard import DashboardView
    from app.ui.views.diagrams import DiagramsView
    from app.ui.views.fixes import FixesView
    from app.ui.views.history import HistoryView
    from app.ui.views.hotspots import HotspotsView
    from app.ui.views.insights import InsightsView
    from app.ui.views.projects import ProjectsView
    from app.ui.views.roadmap import RoadmapView
    from app.ui.views.scorecard import ScorecardView
    from app.ui.views.settings import SettingsView
    from app.ui.views.trends import TrendsView

    for key, builder in (
        ("dashboard", DashboardView),
        ("projects", ProjectsView),
        ("analyses", AnalysesView),
        ("scorecard", ScorecardView),
        ("roadmap", RoadmapView),
        ("hotspots", HotspotsView),
        ("fixes", FixesView),
        ("trends", TrendsView),
        ("diagrams", DiagramsView),
        ("insights", InsightsView),
        ("history", HistoryView),
        ("compare", CompareView),
        ("settings", SettingsView),
        ("about", AboutView),
    ):
        window.register(key, lambda cls=builder: cls(window))
