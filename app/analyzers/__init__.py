"""Plugin based source analyzers.

Adding a new language is a matter of subclassing :class:`~app.analyzers.base.Analyzer`
and registering it with :func:`register`.
"""

from app.analyzers.base import Analyzer, AnalysisContext, PendingRef, all_analyzers, register

# Import side effects register the built-in analyzers.
from app.analyzers import (  # noqa: F401  (registration order matters)
    python_analyzer,
    jvm_dotnet_analyzer,
    c_family_analyzer,
    web_analyzer,
    go_rust_analyzer,
    infra_analyzer,
    database_analyzer,
)

__all__ = ["Analyzer", "AnalysisContext", "PendingRef", "all_analyzers", "register"]
