"""The explainable architecture scorecard.

Every number the UI shows comes from here. A score is never a bare integer: each
category carries the signals that moved it, the evidence behind each signal and a
recommendation that states the expected gain if it is addressed.

    scorecard = evaluate(graph, metrics, signals, history=git_history_payload)
    scorecard["overall"]            # 0-100
    scorecard["categories"][0]      # {"id": "architecture", "score": 74, "signals": [...]}
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("aai.scoring")

CATEGORY_ORDER = [
    "architecture",
    "code_quality",
    "security",
    "testing",
    "documentation",
    "maintainability",
    "performance",
    "technical_debt",
]

CATEGORY_LABELS = {
    "architecture": "Architecture",
    "code_quality": "Code Quality",
    "security": "Security",
    "testing": "Testing",
    "documentation": "Documentation",
    "maintainability": "Maintainability",
    "performance": "Performance",
    "technical_debt": "Technical Debt",
}

CATEGORY_ICONS = {
    "architecture": "layers",
    "code_quality": "code",
    "security": "shield",
    "testing": "beaker",
    "documentation": "book",
    "maintainability": "wrench",
    "performance": "gauge",
    "technical_debt": "hourglass",
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "architecture": 0.20,
    "security": 0.20,
    "code_quality": 0.15,
    "maintainability": 0.12,
    "testing": 0.12,
    "documentation": 0.08,
    "performance": 0.08,
    "technical_debt": 0.05,
}

GRADE_BANDS = [
    (95, "A+"), (90, "A"), (85, "B+"), (80, "B"),
    (75, "C+"), (70, "C"), (60, "D"), (50, "E"), (0, "F"),
]

EFFORT_ORDER = {"low": 0, "medium": 1, "high": 2}
SEVERITY_WEIGHT = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def grade_for(score: float) -> str:
    for threshold, grade in GRADE_BANDS:
        if score >= threshold:
            return grade
    return "F"


def band_for(score: float) -> str:
    """Colour band used by the UI: excellent / good / fair / poor / critical."""
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 60:
        return "fair"
    if score >= 40:
        return "poor"
    return "critical"


def normalise_weights(raw: dict[str, Any] | None) -> dict[str, float]:
    """Coerce a user-supplied weight map into a normalised distribution."""
    weights: dict[str, float] = {}
    for key in CATEGORY_ORDER:
        try:
            value = float((raw or {}).get(key, DEFAULT_WEIGHTS[key]))
        except (TypeError, ValueError):
            value = DEFAULT_WEIGHTS[key]
        weights[key] = max(0.0, min(value, 1.0))
    total = sum(weights.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {key: round(value / total, 4) for key, value in weights.items()}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(value, high))


def _ratio(numerator: float, denominator: float) -> float:
    return (numerator / denominator) if denominator else 0.0


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


# --------------------------------------------------------------------------- #
# Category assembly
# --------------------------------------------------------------------------- #
@dataclass
class Category:
    """Accumulates deductions, credits, evidence and recommendations."""

    id: str
    signals: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.9
    unmeasured: list[str] = field(default_factory=list)
    _deducted: float = 0.0
    _credited: float = 0.0

    # -- signals ---------------------------------------------------------- #
    def deduct(
        self,
        signal_id: str,
        label: str,
        points: float,
        *,
        severity: str = "medium",
        detail: str = "",
        value: Any = None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> float:
        points = round(max(0.0, points), 1)
        if points <= 0:
            return 0.0
        self._deducted += points
        self.signals.append(
            {
                "id": signal_id,
                "label": label,
                "status": "fail" if severity in {"critical", "high"} else "warn",
                "severity": severity,
                "impact": -points,
                "detail": detail,
                "value": value,
                "evidence": (evidence or [])[:12],
            }
        )
        return points

    def credit(self, signal_id: str, label: str, points: float, *, detail: str = "", value: Any = None) -> None:
        points = round(max(0.0, points), 1)
        self._credited += points
        self.signals.append(
            {
                "id": signal_id,
                "label": label,
                "status": "pass",
                "severity": "info",
                "impact": points,
                "detail": detail,
                "value": value,
                "evidence": [],
            }
        )

    def pass_note(self, signal_id: str, label: str, *, detail: str = "", value: Any = None) -> None:
        self.signals.append(
            {
                "id": signal_id,
                "label": label,
                "status": "pass",
                "severity": "info",
                "impact": 0,
                "detail": detail,
                "value": value,
                "evidence": [],
            }
        )

    # -- recommendations --------------------------------------------------- #
    def recommend(
        self,
        rec_id: str,
        title: str,
        *,
        why: str,
        how: str,
        effort: str,
        recovers: float,
        confidence: float = 0.8,
        files: list[str] | None = None,
        signal: str = "",
    ) -> None:
        if recovers <= 0:
            return
        self.recommendations.append(
            {
                "id": rec_id,
                "category": self.id,
                "title": title,
                "why": why,
                "how": how,
                "effort": effort,
                "category_gain": round(recovers, 1),
                "confidence": round(confidence, 2),
                "files": (files or [])[:8],
                "signal": signal,
            }
        )

    # -- output ------------------------------------------------------------ #
    @property
    def score(self) -> float:
        return round(_clamp(100.0 - self._deducted + self._credited), 1)

    def build(self, weight: float) -> dict[str, Any]:
        score = self.score
        # A severity word and a point total say nothing on their own: the reader
        # cannot tell whether "-8" is a rounding error or a third of the grade,
        # nor what to do about it. Each signal is therefore annotated with the
        # category it belongs to, that category's weight, the points it actually
        # moves on the overall score, and the recommendation that clears it.
        # The recommendations were already keyed by signal id - they were simply
        # rendered as a separate list, so the fix never appeared beside the
        # problem it solves.
        by_signal = {rec["signal"]: rec for rec in self.recommendations if rec.get("signal")}
        for signal in self.signals:
            signal["category_id"] = self.id
            signal["category_label"] = CATEGORY_LABELS[self.id]
            signal["weight_pct"] = round(weight * 100, 1)
            signal["overall_impact"] = round(signal["impact"] * weight, 2)
            signal["remediation"] = by_signal.get(signal["id"])
        failing = [s for s in self.signals if s["status"] != "pass"]
        failing.sort(key=lambda s: s["impact"])
        passing = [s for s in self.signals if s["status"] == "pass"]
        return {
            "id": self.id,
            "label": CATEGORY_LABELS[self.id],
            "icon": CATEGORY_ICONS[self.id],
            "score": int(round(score)),
            "score_exact": score,
            "grade": grade_for(score),
            "band": band_for(score),
            "weight": round(weight, 4),
            "weight_pct": round(weight * 100, 1),
            "contribution": round(score * weight, 2),
            "max_contribution": round(100 * weight, 2),
            "lost_points": round((100 - score) * weight, 2),
            "confidence": round(self.confidence, 2),
            "signals": self.signals,
            "issues": failing,
            "strengths": passing,
            "issue_count": len(failing),
            "recommendations": sorted(self.recommendations, key=lambda r: -r["category_gain"]),
            "metrics": self.metrics,
            "unmeasured": self.unmeasured,
            "summary": _summarise(self.id, score, failing),
        }


def _cycle_modules(cycle: Any) -> list[str]:
    """``find_cycles`` yields ``{"modules": [...]}`` but plain lists are tolerated."""
    if isinstance(cycle, dict):
        return [str(m) for m in (cycle.get("modules") or [])]
    if isinstance(cycle, (list, tuple)):
        return [str(m) for m in cycle]
    return [str(cycle)]


def _summarise(category_id: str, score: float, failing: list[dict[str, Any]]) -> str:
    label = CATEGORY_LABELS[category_id]
    if not failing:
        return f"{label} is clean - no deductions were triggered by the current signals."
    worst = failing[0]
    if score >= 80:
        tone = "is in good shape"
    elif score >= 60:
        tone = "needs attention"
    else:
        tone = "is a primary risk area"
    extra = f" and {len(failing) - 1} further issue{'s' if len(failing) > 2 else ''}" if len(failing) > 1 else ""
    return f"{label} {tone}. The largest deduction is \"{worst['label']}\" ({worst['impact']} pts){extra}."


# --------------------------------------------------------------------------- #
# Individual category scorers
# --------------------------------------------------------------------------- #
def _score_architecture(metrics: dict[str, Any], signals: dict[str, Any]) -> Category:
    cat = Category("architecture")
    cycles = metrics.get("cycles") or []
    violations = metrics.get("layering_violations") or []
    hubs = metrics.get("hubs") or []
    orphans = metrics.get("orphan_modules") or []
    patterns = metrics.get("patterns") or []
    layers = metrics.get("layers") or {}
    populated_layers = (
        [name for name, count in layers.items() if count]
        if isinstance(layers, dict)
        else [layer for layer in layers if layer]
    )
    density = float(metrics.get("density") or 0.0)
    modules = int(metrics.get("module_count") or 0)
    abstraction = float(metrics.get("abstraction_ratio") or 0.0)

    cat.metrics = {
        "modules": modules,
        "module_dependencies": metrics.get("module_dependency_count", 0),
        "density": round(density, 4),
        "average_instability": metrics.get("average_instability", 0),
        "abstraction_ratio": round(abstraction, 4),
        "cycle_count": len(cycles),
        "violation_count": len(violations),
        "layer_count": len(populated_layers),
        "detected_patterns": len(patterns),
    }

    if cycles:
        points = cat.deduct(
            "arch.cycles",
            "Circular dependencies between modules",
            min(len(cycles) * 5.0, 28),
            severity="high" if len(cycles) > 2 else "medium",
            detail=f"{len(cycles)} dependency cycle(s) prevent modules from being built, tested or replaced independently.",
            value=len(cycles),
            evidence=[
                {
                    "label": " -> ".join(_cycle_modules(cycle)),
                    "kind": "cycle",
                    "value": len(_cycle_modules(cycle)),
                }
                for cycle in cycles[:12]
            ],
        )
        cat.recommend(
            "arch.break_cycles",
            "Break the dependency cycles",
            why="Cycles couple modules bidirectionally, so a change anywhere in the loop can break everything else in it.",
            how="Extract the shared contract into an interface owned by the lower layer, then invert the upward dependency.",
            effort="medium" if len(cycles) <= 3 else "high",
            recovers=points,
            files=[_cycle_modules(c)[0] for c in cycles[:5] if _cycle_modules(c)],
            signal="arch.cycles",
        )
    else:
        cat.credit("arch.acyclic", "Acyclic module graph", 3, detail="No circular dependencies were found between modules.")

    if violations:
        points = cat.deduct(
            "arch.layering",
            "Layering violations",
            min(len(violations) * 3.0, 22),
            severity="high" if len(violations) > 6 else "medium",
            detail=f"{len(violations)} dependenc(ies) point from a lower layer up into a higher one, inverting the intended flow.",
            value=len(violations),
            evidence=[
                {
                    "label": f"{v.get('from')} ({v.get('from_layer')}) -> {v.get('to')} ({v.get('to_layer')})",
                    "file": v.get("file", ""),
                    "kind": v.get("kind", ""),
                }
                for v in violations[:12]
            ],
        )
        cat.recommend(
            "arch.restore_layering",
            "Restore the layer boundaries",
            why="Upward dependencies make the lower layers un-reusable and drag presentation concerns into the domain.",
            how="Introduce an abstraction in the lower layer and inject the concrete implementation from the composition root.",
            effort="medium",
            recovers=points,
            files=[v.get("file", "") for v in violations[:6] if v.get("file")],
            signal="arch.layering",
        )

    if hubs:
        heavy = [h for h in hubs if (h.get("degree") or 0) >= 15]
        points = cat.deduct(
            "arch.hubs",
            "Hub components with excessive fan-in/out",
            min(len(hubs) * 2.0 + len(heavy) * 1.5, 14),
            severity="medium",
            detail=f"{len(hubs)} node(s) sit on a disproportionate number of relationships and act as change amplifiers.",
            value=len(hubs),
            evidence=[
                {"label": h.get("name", ""), "file": h.get("file", ""), "value": h.get("degree", 0), "kind": h.get("kind", "")}
                for h in hubs[:12]
            ],
        )
        cat.recommend(
            "arch.split_hubs",
            "Split the highest-traffic hubs",
            why="Every consumer of a hub is exposed to changes made for any other consumer.",
            how="Group the hub's members by the audience that uses them and publish narrow, role-specific interfaces.",
            effort="high",
            recovers=points * 0.7,
            files=[h.get("file", "") for h in hubs[:5] if h.get("file")],
            signal="arch.hubs",
        )

    if orphans:
        cat.deduct(
            "arch.orphans",
            "Disconnected modules",
            min(len(orphans) * 1.5, 8),
            severity="low",
            detail=f"{len(orphans)} module(s) have no incoming or outgoing dependencies - dead code or an undocumented entry point.",
            value=len(orphans),
            evidence=[{"label": name} for name in orphans[:12]],
        )

    if density > 0.12 and modules > 5:
        points = cat.deduct(
            "arch.density",
            "Densely connected module graph",
            min((density - 0.12) * 180, 12),
            severity="medium",
            detail=f"Module dependency density is {round(density * 100, 1)}% - the recommended ceiling is 12%.",
            value=round(density, 4),
        )
        cat.recommend(
            "arch.reduce_density",
            "Reduce cross-module coupling",
            why="A dense graph means almost every module can be affected by almost every change.",
            how="Introduce a facade per bounded context and route cross-context traffic through it.",
            effort="high",
            recovers=points,
            signal="arch.density",
        )

    strong_patterns = [p for p in patterns if float(p.get("confidence") or 0) >= 0.5]
    if len(strong_patterns) >= 3:
        cat.credit(
            "arch.patterns",
            "Recognised architectural patterns",
            5,
            detail="Detected: " + ", ".join(p.get("pattern", "") for p in strong_patterns[:5]),
            value=len(strong_patterns),
        )
    if len(populated_layers) >= 4:
        cat.credit(
            "arch.layered",
            "Clear layer separation",
            4,
            detail=f"{len(populated_layers)} distinct layers are populated with modules.",
            value=len(populated_layers),
        )
    if abstraction > 0.15:
        cat.credit(
            "arch.abstraction",
            "Healthy abstraction ratio",
            3,
            detail=f"{_pct(abstraction)} of types are interfaces or abstract classes.",
            value=round(abstraction, 4),
        )

    if modules < 3:
        cat.confidence = 0.5
        cat.unmeasured.append("Too few modules were detected for a meaningful structural verdict.")
    return cat


def _score_code_quality(metrics: dict[str, Any], signals: dict[str, Any]) -> Category:
    cat = Category("code_quality")
    complexity = signals.get("complexity") or {}
    totals = signals.get("totals") or {}
    measured = int(complexity.get("measured_functions") or 0)
    average = float(complexity.get("average") or 0.0)
    over = int(complexity.get("over_threshold") or 0)
    peak = int(complexity.get("max") or 0)
    offenders = complexity.get("offenders") or []
    wide = complexity.get("wide_signatures") or []
    code_lines = int(totals.get("code_lines") or 0)
    long_lines = int(totals.get("long_lines") or 0)
    huge_files = int(totals.get("huge_files") or 0)
    large_files = int(totals.get("large_files") or 0)

    cat.metrics = {
        "measured_functions": measured,
        "average_complexity": average,
        "median_complexity": complexity.get("median", 0),
        "p90_complexity": complexity.get("p90", 0),
        "max_complexity": peak,
        "complex_functions": over,
        "distribution": complexity.get("distribution", []),
        "long_lines": long_lines,
        "files_over_600_loc": large_files + huge_files,
    }

    if measured:
        if average > 6:
            cat.deduct(
                "quality.average_complexity",
                "Average function complexity above target",
                min((average - 6) * 4.0, 18),
                severity="medium",
                detail=f"Mean cyclomatic complexity is {average}; a maintainable target is 6 or lower.",
                value=average,
            )
        ratio = _ratio(over, measured)
        if over:
            points = cat.deduct(
                "quality.complex_functions",
                "Functions above the complexity threshold",
                min(ratio * 130, 24),
                severity="high" if ratio > 0.15 else "medium",
                detail=f"{over} of {measured} functions ({_pct(ratio)}) exceed a cyclomatic complexity of 10.",
                value=over,
                evidence=[
                    {"label": o["name"], "file": o.get("file", ""), "line": o.get("line", 0), "value": o["complexity"]}
                    for o in offenders[:12]
                ],
            )
            cat.recommend(
                "quality.decompose_functions",
                f"Decompose the {min(over, 10)} most complex functions",
                why="Every extra branch multiplies the paths a reader and a test must cover.",
                how="Extract guard clauses and cohesive blocks into named helpers, then cover each with a focused test.",
                effort="low" if over <= 5 else "medium",
                recovers=points,
                files=[o.get("file", "") for o in offenders[:6] if o.get("file")],
                signal="quality.complex_functions",
            )
        if peak >= 25:
            cat.deduct(
                "quality.peak_complexity",
                "Extremely complex function present",
                8 if peak >= 40 else 4,
                severity="high" if peak >= 40 else "medium",
                detail=f"The most complex function scores {peak}; anything above 25 is effectively untestable.",
                value=peak,
                evidence=[
                    {"label": o["name"], "file": o.get("file", ""), "line": o.get("line", 0), "value": o["complexity"]}
                    for o in offenders[:3]
                ],
            )
        if average <= 4 and not over:
            cat.credit("quality.simple", "Consistently simple functions", 5, detail=f"Mean complexity {average} with no outliers.")
    else:
        cat.confidence = 0.4
        cat.unmeasured.append("No per-function complexity was available for the detected languages.")

    if wide:
        cat.deduct(
            "quality.wide_signatures",
            "Functions with long parameter lists",
            min(len(wide) * 1.5, 9),
            severity="low",
            detail=f"{len(wide)} function(s) take six or more parameters, which usually hides a missing value object.",
            value=len(wide),
            evidence=[
                {"label": w["name"], "file": w.get("file", ""), "line": w.get("line", 0), "value": w["params"]}
                for w in wide[:12]
            ],
        )

    if huge_files or large_files:
        points = cat.deduct(
            "quality.file_size",
            "Oversized source files",
            min(huge_files * 3.0 + large_files * 1.2, 14),
            severity="medium" if huge_files else "low",
            detail=f"{huge_files} file(s) exceed 1000 lines and {large_files} exceed 600 lines.",
            value=huge_files + large_files,
            evidence=[
                {"label": f["file"], "file": f["file"], "value": f["loc"]}
                for f in (signals.get("largest_files") or [])[:12]
            ],
        )
        cat.recommend(
            "quality.split_files",
            "Split the largest source files",
            why="Large files hide multiple responsibilities and make every merge a conflict.",
            how="Move each cohesive group of functions into its own module and re-export a narrow public surface.",
            effort="medium",
            recovers=points,
            files=[f["file"] for f in (signals.get("largest_files") or [])[:6]],
            signal="quality.file_size",
        )

    if code_lines and _ratio(long_lines, code_lines) > 0.05:
        cat.deduct(
            "quality.long_lines",
            "Lines exceeding the readable width",
            min(_ratio(long_lines, code_lines) * 60, 6),
            severity="low",
            detail=f"{long_lines} line(s) are longer than 120 characters.",
            value=long_lines,
        )
    return cat


def _score_security(metrics: dict[str, Any], signals: dict[str, Any]) -> Category:
    cat = Category("security")
    cat.confidence = 0.7
    findings = [f for f in (signals.get("findings") or []) if f["category"] == "security"]
    counts = (signals.get("finding_counts") or {}).get("security", {})
    totals = signals.get("totals") or {}
    critical = [f for f in findings if f["severity"] == "critical"]
    high = [f for f in findings if f["severity"] == "high"]
    medium = [f for f in findings if f["severity"] == "medium"]
    low = [f for f in findings if f["severity"] == "low"]

    cat.metrics = {
        "total_findings": len(findings),
        "by_severity": {"critical": len(critical), "high": len(high), "medium": len(medium), "low": len(low)},
        "counts": counts,
        "scanned_files": totals.get("files", 0),
        "affected_files": len({f["file"] for f in findings}),
    }

    buckets = [
        ("critical", critical, 20.0, 60.0, "critical"),
        ("high", high, 9.0, 32.0, "high"),
        ("medium", medium, 3.0, 16.0, "medium"),
        ("low", low, 1.0, 6.0, "low"),
    ]
    for name, group, per, cap, severity in buckets:
        if not group:
            continue
        titles = sorted({f["title"] for f in group})
        points = cat.deduct(
            f"security.{name}",
            f"{name.capitalize()} security findings",
            min(len(group) * per, cap),
            severity=severity,
            detail=f"{len(group)} {name} finding(s): " + ", ".join(titles[:4]) + ("…" if len(titles) > 4 else ""),
            value=len(group),
            evidence=[
                {
                    "label": f["title"],
                    "file": f["file"],
                    "line": f["line"],
                    "snippet": f["snippet"],
                    "why": f["why"],
                    "fix": f["fix"],
                    "rule": f["rule"],
                }
                for f in group[:12]
            ],
        )
        if name in {"critical", "high"}:
            worst = group[0]
            cat.recommend(
                f"security.fix_{name}",
                f"Remediate the {len(group)} {name} security finding(s)",
                why=worst["why"],
                how=worst["fix"],
                effort="low" if len(group) <= 3 else "medium",
                recovers=points,
                confidence=0.75,
                files=sorted({f["file"] for f in group})[:8],
                signal=f"security.{name}",
            )

    if not findings and totals.get("files"):
        cat.credit(
            "security.clean",
            "No security smells detected",
            6,
            detail=f"{totals.get('files')} source file(s) were scanned against {len(_security_rule_ids())} detectors with no hits.",
        )
    if not totals.get("files"):
        cat.confidence = 0.3
        cat.unmeasured.append("No scannable source files were found.")
    return cat


def _security_rule_ids() -> list[str]:
    from app.engine.quality import SECURITY_RULES

    return [rule.id for rule in SECURITY_RULES]


def _score_testing(metrics: dict[str, Any], signals: dict[str, Any]) -> Category:
    cat = Category("testing")
    cat.confidence = 0.65
    totals = signals.get("totals") or {}
    infra = signals.get("infra") or {}
    source_files = int(totals.get("source_files") or 0)
    test_files = int(totals.get("test_files") or 0)
    source_loc = int(totals.get("source_loc") or 0)
    test_loc = int(totals.get("test_loc") or 0)
    assertions = int(totals.get("assertions") or 0)
    untested = signals.get("untested_modules") or []
    module_count = int(signals.get("module_count") or 0)

    file_ratio = _ratio(test_files, source_files)
    loc_ratio = _ratio(test_loc, source_loc)

    cat.metrics = {
        "test_files": test_files,
        "source_files": source_files,
        "test_file_ratio": round(file_ratio, 4),
        "test_loc": test_loc,
        "source_loc": source_loc,
        "test_loc_ratio": round(loc_ratio, 4),
        "assertions": assertions,
        "assertions_per_test_file": round(_ratio(assertions, test_files), 1),
        "modules": module_count,
        "untested_modules": len(untested),
        "ci_configured": bool(infra.get("ci")),
        "coverage_configured": bool(infra.get("coverage_config")),
    }

    if source_files == 0:
        cat.confidence = 0.25
        cat.unmeasured.append("No source files were available to compare tests against.")
        return cat

    if test_files == 0:
        cat.deduct(
            "testing.none",
            "No automated tests found",
            50,
            severity="critical",
            detail="No file matched a test naming or directory convention, so every change is verified by hand.",
            value=0,
        )
        cat.recommend(
            "testing.bootstrap",
            "Establish an automated test suite",
            why="Without tests there is no safety net; every refactor is a gamble and regressions reach users.",
            how="Add a test runner, then cover the highest-risk modules first - start with the ones that change most often.",
            effort="high",
            recovers=50,
            confidence=0.9,
            signal="testing.none",
        )
    else:
        target = 0.25
        if file_ratio < target:
            points = cat.deduct(
                "testing.coverage_breadth",
                "Test suite is thin relative to the codebase",
                min((target - file_ratio) / target * 40, 40),
                severity="high" if file_ratio < 0.1 else "medium",
                detail=f"{test_files} test file(s) for {source_files} source file(s) ({_pct(file_ratio)}); the target is {_pct(target)}.",
                value=round(file_ratio, 4),
            )
            cat.recommend(
                "testing.expand",
                "Expand test coverage on the least-covered modules",
                why="Modules with no tests are the ones where defects survive the longest.",
                how="Add characterisation tests to the untested modules listed in the evidence, starting with the busiest.",
                effort="medium",
                recovers=points,
                files=untested[:8],
                signal="testing.coverage_breadth",
            )
        else:
            cat.credit("testing.breadth", "Healthy test-to-source ratio", 4, detail=_pct(file_ratio), value=round(file_ratio, 4))

        if loc_ratio < 0.2:
            cat.deduct(
                "testing.depth",
                "Tests are shallow",
                min((0.2 - loc_ratio) / 0.2 * 16, 16),
                severity="medium",
                detail=f"Test code is {_pct(loc_ratio)} of production code; healthy suites usually sit above 20%.",
                value=round(loc_ratio, 4),
            )
        per_file = _ratio(assertions, test_files)
        if per_file < 3:
            cat.deduct(
                "testing.assertions",
                "Few assertions per test file",
                min((3 - per_file) * 3, 9),
                severity="low",
                detail=f"An average of {round(per_file, 1)} assertion(s) per test file suggests smoke tests rather than verification.",
                value=round(per_file, 1),
            )

    if module_count and untested:
        cat.deduct(
            "testing.untested_modules",
            "Modules without any test",
            min(_ratio(len(untested), module_count) * 24, 18),
            severity="medium",
            detail=f"{len(untested)} of {module_count} module(s) have no matching test file.",
            value=len(untested),
            evidence=[{"label": name} for name in untested[:12]],
        )

    if not infra.get("ci"):
        cat.deduct(
            "testing.ci",
            "No continuous integration pipeline",
            12,
            severity="medium",
            detail="No CI workflow file was found, so nothing runs the suite automatically on every change.",
        )
        cat.recommend(
            "testing.add_ci",
            "Add a CI pipeline that runs the suite",
            why="Tests that are not run automatically stop being run at all.",
            how="Add a workflow that installs dependencies and runs the test command on every push and pull request.",
            effort="low",
            recovers=12,
            confidence=0.9,
            signal="testing.ci",
        )
    else:
        cat.credit("testing.ci_present", "Continuous integration configured", 3, detail="A CI workflow was detected.")

    if not infra.get("coverage_config"):
        cat.deduct(
            "testing.coverage_tooling",
            "No coverage measurement configured",
            5,
            severity="low",
            detail="No coverage configuration was found, so untested code paths stay invisible.",
        )
    return cat


def _score_documentation(metrics: dict[str, Any], signals: dict[str, Any]) -> Category:
    cat = Category("documentation")
    docs = signals.get("project_docs") or {}
    symbols = signals.get("symbol_docs") or {}
    totals = signals.get("totals") or {}
    coverage = float(symbols.get("coverage") or 0.0)
    public = int(symbols.get("public_symbols") or 0)
    documented_modules = int(totals.get("documented_modules") or 0)
    source_files = int(totals.get("source_files") or 0)
    comment_ratio = _ratio(int(totals.get("comment_lines") or 0), int(totals.get("code_lines") or 0))

    cat.metrics = {
        "public_symbols": public,
        "documented_symbols": symbols.get("documented_symbols", 0),
        "symbol_coverage": round(coverage, 4),
        "module_doc_coverage": round(_ratio(documented_modules, source_files), 4),
        "comment_ratio": round(comment_ratio, 4),
        "readme": bool(docs.get("readme")),
        "license": bool(docs.get("license")),
        "contributing": bool(docs.get("contributing")),
        "doc_pages": docs.get("doc_pages", 0),
        "doc_words": docs.get("doc_words", 0),
    }

    if public:
        target = 0.7
        if coverage < target:
            points = cat.deduct(
                "docs.symbol_coverage",
                "Public API is largely undocumented",
                min((target - coverage) / target * 40, 40),
                severity="high" if coverage < 0.25 else "medium",
                detail=f"{symbols.get('documented_symbols', 0)} of {public} public symbols ({_pct(coverage)}) carry a doc comment.",
                value=round(coverage, 4),
                evidence=[
                    {"label": u["name"], "file": u.get("file", ""), "line": u.get("line", 0), "kind": u.get("kind", "")}
                    for u in (symbols.get("undocumented") or [])[:12]
                ],
            )
            cat.recommend(
                "docs.document_api",
                "Document the public API surface",
                why="Undocumented public symbols force every consumer to read the implementation to learn the contract.",
                how="Add a one-paragraph doc comment stating purpose, parameters, return value and failure modes.",
                effort="low",
                recovers=points,
                files=[u.get("file", "") for u in (symbols.get("undocumented") or [])[:6] if u.get("file")],
                signal="docs.symbol_coverage",
            )
        else:
            cat.credit("docs.api", "Public API is documented", 5, detail=_pct(coverage), value=round(coverage, 4))
    else:
        cat.confidence = 0.5
        cat.unmeasured.append("No public symbols were detected to measure doc coverage against.")

    if not docs.get("readme"):
        cat.deduct(
            "docs.readme",
            "No README",
            20,
            severity="high",
            detail="A new contributor has no entry point explaining what this project is or how to run it.",
        )
        cat.recommend(
            "docs.add_readme",
            "Write a README",
            why="The README is the first and often only document a new engineer reads.",
            how="Cover purpose, architecture in one diagram, how to run, how to test and how to deploy.",
            effort="low",
            recovers=20,
            confidence=0.95,
            signal="docs.readme",
        )
    else:
        cat.credit("docs.readme_present", "README present", 3)

    if not docs.get("doc_pages"):
        cat.deduct(
            "docs.pages",
            "No written documentation pages",
            10,
            severity="medium",
            detail="No markdown documentation was found beyond source comments.",
        )
    elif int(docs.get("doc_words") or 0) < 400:
        cat.deduct(
            "docs.depth",
            "Documentation is very brief",
            5,
            severity="low",
            detail=f"Only {docs.get('doc_words')} words of prose documentation exist across {docs.get('doc_pages')} page(s).",
            value=docs.get("doc_words"),
        )

    if source_files:
        module_doc_ratio = _ratio(documented_modules, source_files)
        if module_doc_ratio < 0.4:
            cat.deduct(
                "docs.module_headers",
                "Most files have no header documentation",
                min((0.4 - module_doc_ratio) / 0.4 * 12, 12),
                severity="low",
                detail=f"{documented_modules} of {source_files} file(s) ({_pct(module_doc_ratio)}) open with a module doc comment.",
                value=round(module_doc_ratio, 4),
            )
    if comment_ratio < 0.05:
        cat.deduct(
            "docs.comments",
            "Very low comment density",
            6,
            severity="low",
            detail=f"Comments make up {_pct(comment_ratio)} of code lines.",
            value=round(comment_ratio, 4),
        )
    if not docs.get("license"):
        cat.deduct("docs.license", "No licence file", 4, severity="low", detail="Reuse terms are undefined.")
    return cat


def _score_maintainability(metrics: dict[str, Any], signals: dict[str, Any], history: dict[str, Any]) -> Category:
    cat = Category("maintainability")
    gods = metrics.get("god_classes") or []
    coupling = metrics.get("coupling") or []
    totals = signals.get("totals") or {}
    largest = signals.get("largest_files") or []
    hotspots = (history or {}).get("hotspots") or []
    source_files = int(totals.get("source_files") or 0)
    source_loc = int(totals.get("source_loc") or 0)
    avg_file = _ratio(source_loc, source_files)
    unstable = [c for c in coupling if float(c.get("instability") or 0) > 0.85 and int(c.get("fan_in") or 0) >= 3]
    heavy = [c for c in coupling if int(c.get("coupling") or 0) >= 15]

    cat.metrics = {
        "god_classes": len(gods),
        "average_file_loc": round(avg_file, 1),
        "source_files": source_files,
        "source_loc": source_loc,
        "highly_coupled_modules": len(heavy),
        "unstable_dependencies": len(unstable),
        "hotspots": len(hotspots),
    }

    if gods:
        points = cat.deduct(
            "maint.god_classes",
            "God classes",
            min(len(gods) * 4.0, 24),
            severity="high" if len(gods) > 3 else "medium",
            detail=f"{len(gods)} type(s) concentrate far too many methods or dependencies.",
            value=len(gods),
            evidence=[
                {
                    "label": g.get("name", ""),
                    "file": g.get("file", ""),
                    "value": g.get("methods", 0),
                    "detail": f"{g.get('methods', 0)} methods, {g.get('dependencies', 0)} dependencies",
                }
                for g in gods[:12]
            ],
        )
        cat.recommend(
            "maint.split_god_classes",
            "Split the god classes by responsibility",
            why="A type that does everything cannot be understood, tested or changed in isolation.",
            how="Group the methods by the data they touch and extract each group into a collaborator with a single reason to change.",
            effort="high",
            recovers=points,
            files=[g.get("file", "") for g in gods[:6] if g.get("file")],
            signal="maint.god_classes",
        )

    if heavy:
        cat.deduct(
            "maint.coupling",
            "Highly coupled modules",
            min(len(heavy) * 2.0, 14),
            severity="medium",
            detail=f"{len(heavy)} module(s) have a combined fan-in plus fan-out of 15 or more.",
            value=len(heavy),
            evidence=[
                {
                    "label": c.get("module", ""),
                    "value": c.get("coupling", 0),
                    "detail": f"fan-in {c.get('fan_in', 0)}, fan-out {c.get('fan_out', 0)}, instability {c.get('instability', 0)}",
                }
                for c in heavy[:12]
            ],
        )
    if unstable:
        cat.deduct(
            "maint.instability",
            "Depended-upon modules are unstable",
            min(len(unstable) * 2.0, 10),
            severity="medium",
            detail=f"{len(unstable)} module(s) are depended upon while themselves depending on nearly everything else.",
            value=len(unstable),
            evidence=[{"label": c.get("module", ""), "value": c.get("instability", 0)} for c in unstable[:12]],
        )

    if avg_file > 250:
        cat.deduct(
            "maint.file_size",
            "Average file is large",
            min((avg_file - 250) / 25, 10),
            severity="low",
            detail=f"The mean source file is {round(avg_file)} lines.",
            value=round(avg_file, 1),
            evidence=[{"label": f["file"], "file": f["file"], "value": f["loc"]} for f in largest[:8]],
        )
    elif source_files:
        cat.credit("maint.file_size_ok", "Files stay small", 3, detail=f"Mean file length {round(avg_file)} lines.")

    risky_hotspots = [h for h in hotspots if h.get("risk") == "high"]
    if risky_hotspots:
        points = cat.deduct(
            "maint.hotspots",
            "High-churn, multi-owner hotspots",
            min(len(risky_hotspots) * 2.5, 14),
            severity="high" if len(risky_hotspots) > 4 else "medium",
            detail=f"{len(risky_hotspots)} file(s) change constantly and are touched by many authors - the classic defect breeding ground.",
            value=len(risky_hotspots),
            evidence=[
                {
                    "label": h.get("path", ""),
                    "file": h.get("path", ""),
                    "value": h.get("changes", 0),
                    "detail": f"{h.get('changes', 0)} changes by {h.get('authors', 0)} author(s)",
                }
                for h in risky_hotspots[:12]
            ],
        )
        cat.recommend(
            "maint.stabilise_hotspots",
            "Stabilise the change hotspots",
            why="Files that change on every feature are where regressions concentrate.",
            how="Cover the hotspot with tests, then split it so unrelated features stop editing the same file.",
            effort="medium",
            recovers=points,
            files=[h.get("path", "") for h in risky_hotspots[:6]],
            signal="maint.hotspots",
        )
    elif not hotspots:
        cat.confidence = 0.75
        cat.unmeasured.append("No git history was available, so change-churn risk could not be measured.")
    return cat


def _score_performance(metrics: dict[str, Any], signals: dict[str, Any], graph_stats: dict[str, Any]) -> Category:
    cat = Category("performance")
    cat.confidence = 0.6
    findings = [f for f in (signals.get("findings") or []) if f["category"] == "performance"]
    high = [f for f in findings if f["severity"] == "high"]
    medium = [f for f in findings if f["severity"] == "medium"]
    low = [f for f in findings if f["severity"] == "low"]
    unindexed = signals.get("unindexed_foreign_keys") or []

    cat.metrics = {
        "total_findings": len(findings),
        "by_severity": {"high": len(high), "medium": len(medium), "low": len(low)},
        "affected_files": len({f["file"] for f in findings}),
        "unindexed_foreign_keys": len(unindexed),
    }

    for name, group, per, cap, severity in [
        ("high", high, 11.0, 36.0, "high"),
        ("medium", medium, 4.0, 24.0, "medium"),
        ("low", low, 1.5, 10.0, "low"),
    ]:
        if not group:
            continue
        titles = sorted({f["title"] for f in group})
        points = cat.deduct(
            f"perf.{name}",
            f"{name.capitalize()} performance findings",
            min(len(group) * per, cap),
            severity=severity,
            detail=f"{len(group)} finding(s): " + ", ".join(titles[:4]),
            value=len(group),
            evidence=[
                {
                    "label": f["title"],
                    "file": f["file"],
                    "line": f["line"],
                    "snippet": f["snippet"],
                    "why": f["why"],
                    "fix": f["fix"],
                    "rule": f["rule"],
                }
                for f in group[:12]
            ],
        )
        if name == "high":
            cat.recommend(
                "perf.fix_hot_paths",
                f"Fix the {len(group)} high-impact performance issue(s)",
                why=group[0]["why"],
                how=group[0]["fix"],
                effort="medium",
                recovers=points,
                confidence=0.7,
                files=sorted({f["file"] for f in group})[:8],
                signal="perf.high",
            )

    if unindexed:
        points = cat.deduct(
            "perf.missing_indexes",
            "Foreign keys without an index",
            min(len(unindexed) * 3.0, 15),
            severity="medium",
            detail=f"{len(unindexed)} foreign key column(s) have no supporting index, so joins fall back to full scans.",
            value=len(unindexed),
            evidence=[{"label": f"{u['table']}.{u['column']}", "detail": f"references {u.get('references', '')}"} for u in unindexed[:12]],
        )
        cat.recommend(
            "perf.add_indexes",
            "Index the foreign key columns",
            why="Unindexed foreign keys turn every join and cascading delete into a table scan.",
            how="Add an index on each foreign key column and verify with the query planner.",
            effort="low",
            recovers=points,
            signal="perf.missing_indexes",
        )

    if not findings and not unindexed:
        cat.credit("perf.clean", "No performance anti-patterns detected", 6, detail="Static detectors found no hot-path smells.")
        cat.unmeasured.append("Static analysis cannot replace profiling - measure real latency before optimising.")
    return cat


def _score_technical_debt(metrics: dict[str, Any], signals: dict[str, Any]) -> Category:
    cat = Category("technical_debt")
    totals = signals.get("totals") or {}
    markers = signals.get("debt_markers") or []
    marker_counts = signals.get("marker_counts") or {}
    findings = [f for f in (signals.get("findings") or []) if f["category"] == "debt"]
    loc = int(totals.get("loc") or 0)
    deprecated = int(totals.get("deprecated") or 0)
    per_kloc = _ratio(len(markers), loc / 1000) if loc else 0.0

    cat.metrics = {
        "markers": len(markers),
        "markers_per_kloc": round(per_kloc, 2),
        "marker_counts": marker_counts,
        "deprecated_references": deprecated,
        "debt_findings": len(findings),
        "total_loc": loc,
    }

    if markers:
        points = cat.deduct(
            "debt.markers",
            "Unresolved TODO / FIXME markers",
            min(per_kloc * 7, 30),
            severity="medium" if per_kloc > 2 else "low",
            detail=f"{len(markers)} marker(s) ({round(per_kloc, 1)} per 1000 lines): "
            + ", ".join(f"{k} x{v}" for k, v in sorted(marker_counts.items(), key=lambda kv: -kv[1])[:4]),
            value=len(markers),
            evidence=[
                {"label": f"{m['marker']}: {m['note'] or '(no note)'}", "file": m["file"], "line": m["line"]}
                for m in markers[:12]
            ],
        )
        cat.recommend(
            "debt.triage_markers",
            "Triage the TODO/FIXME backlog",
            why="Markers with no owner or ticket are invisible debt that nobody schedules.",
            how="Convert each marker into a tracked issue or delete it, and reject new ones without a ticket reference.",
            effort="low",
            recovers=points,
            files=sorted({m["file"] for m in markers})[:8],
            signal="debt.markers",
        )
    else:
        cat.credit("debt.no_markers", "No unresolved debt markers", 6, detail="No TODO, FIXME or HACK markers were found.")

    for name, per, cap, severity in [("high", 6.0, 24.0, "high"), ("medium", 2.5, 15.0, "medium"), ("low", 1.0, 8.0, "low")]:
        group = [f for f in findings if f["severity"] == name]
        if not group:
            continue
        titles = sorted({f["title"] for f in group})
        points = cat.deduct(
            f"debt.{name}",
            f"{name.capitalize()} maintainability defects",
            min(len(group) * per, cap),
            severity=severity,
            detail=f"{len(group)} occurrence(s): " + ", ".join(titles[:4]),
            value=len(group),
            evidence=[
                {
                    "label": f["title"],
                    "file": f["file"],
                    "line": f["line"],
                    "snippet": f["snippet"],
                    "why": f["why"],
                    "fix": f["fix"],
                    "rule": f["rule"],
                }
                for f in group[:12]
            ],
        )
        if name == "high":
            cat.recommend(
                "debt.fix_silent_failures",
                "Stop swallowing errors",
                why=group[0]["why"],
                how=group[0]["fix"],
                effort="low",
                recovers=points,
                files=sorted({f["file"] for f in group})[:8],
                signal="debt.high",
            )

    if deprecated:
        cat.deduct(
            "debt.deprecated",
            "References to deprecated APIs",
            min(deprecated * 1.5, 10),
            severity="low",
            detail=f"{deprecated} reference(s) to APIs marked deprecated.",
            value=deprecated,
        )
    return cat


# --------------------------------------------------------------------------- #
# Derived signals that need the graph
# --------------------------------------------------------------------------- #
def unindexed_foreign_keys(graph) -> list[dict[str, Any]]:
    from app.graph.model import NodeKind

    result: list[dict[str, Any]] = []
    for node in graph.by_kind(NodeKind.TABLE):
        foreign_keys = node.attributes.get("foreign_keys") or []
        indexes = node.attributes.get("indexes") or []
        indexed = {
            str(column).lower()
            for index in indexes
            if isinstance(index, dict)
            for column in (index.get("columns") or [])
        }
        primary = {
            str(column.get("name", "")).lower()
            for column in (node.attributes.get("columns") or [])
            if isinstance(column, dict) and column.get("primary_key")
        }
        for key in foreign_keys:
            if not isinstance(key, dict):
                continue
            column = str(key.get("column", "")).lower()
            if not column or column in indexed or column in primary:
                continue
            result.append(
                {
                    "table": node.name,
                    "column": key.get("column", ""),
                    "references": key.get("references_table", ""),
                }
            )
    return result[:40]


# --------------------------------------------------------------------------- #
# Roadmap and assembly
# --------------------------------------------------------------------------- #
def _priority_for(overall_gain: float, effort: str, severity_hint: float) -> str:
    if overall_gain >= 4 and effort == "low":
        return "critical"
    if overall_gain >= 3 or (overall_gain >= 1.5 and effort == "low"):
        return "high"
    if overall_gain >= 0.8:
        return "medium"
    return "low"


def _build_roadmap(categories: list[dict[str, Any]], weights: dict[str, float]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for category in categories:
        weight = weights.get(category["id"], 0.0)
        for rec in category["recommendations"]:
            gain = round(rec["category_gain"] * weight, 1)
            action = dict(rec)
            action.update(
                {
                    "category_label": category["label"],
                    "category_score": category["score"],
                    "overall_gain": gain,
                    "priority": _priority_for(gain, rec["effort"], category["score"]),
                    "roi": round(gain / (EFFORT_ORDER[rec["effort"]] + 1), 2),
                }
            )
            actions.append(action)

    actions.sort(key=lambda a: (-a["roi"], -a["overall_gain"]))
    for index, action in enumerate(actions, start=1):
        action["rank"] = index

    return {
        "quick_wins": [a for a in actions if a["effort"] == "low"][:8],
        "medium_term": [a for a in actions if a["effort"] == "medium"][:8],
        "long_term": [a for a in actions if a["effort"] == "high"][:8],
        "all": actions,
        "total_potential_gain": round(sum(a["overall_gain"] for a in actions), 1),
    }


def evaluate(
    graph,
    metrics: dict[str, Any],
    signals: dict[str, Any],
    *,
    history: dict[str, Any] | None = None,
    weights: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce the full scorecard. Never raises - a partial card beats no card."""
    active_weights = normalise_weights(weights)
    signals = dict(signals or {})
    signals.setdefault("unindexed_foreign_keys", unindexed_foreign_keys(graph))
    graph_stats = graph.stats() if hasattr(graph, "stats") else {}
    history = history or {}

    builders = {
        "architecture": lambda: _score_architecture(metrics, signals),
        "code_quality": lambda: _score_code_quality(metrics, signals),
        "security": lambda: _score_security(metrics, signals),
        "testing": lambda: _score_testing(metrics, signals),
        "documentation": lambda: _score_documentation(metrics, signals),
        "maintainability": lambda: _score_maintainability(metrics, signals, history),
        "performance": lambda: _score_performance(metrics, signals, graph_stats),
        "technical_debt": lambda: _score_technical_debt(metrics, signals),
    }

    categories: list[dict[str, Any]] = []
    for category_id in CATEGORY_ORDER:
        try:
            built = builders[category_id]().build(active_weights[category_id])
        except Exception:  # noqa: BLE001 - one broken category must not lose the report
            log.exception("scoring category %s failed", category_id)
            built = Category(category_id).build(active_weights[category_id])
            built["unmeasured"] = ["This category could not be evaluated for this analysis."]
            built["confidence"] = 0.0
        categories.append(built)

    overall_exact = sum(c["score_exact"] * active_weights[c["id"]] for c in categories)
    overall = int(round(_clamp(overall_exact)))
    roadmap = _build_roadmap(categories, active_weights)

    all_issues: list[dict[str, Any]] = []
    for category in categories:
        for issue in category["issues"]:
            all_issues.append(
                {
                    **issue,
                    "category": category["id"],
                    "category_label": category["label"],
                    "weighted_impact": round(abs(issue["impact"]) * active_weights[category["id"]], 2),
                }
            )
    all_issues.sort(key=lambda i: (-i["weighted_impact"], SEVERITY_WEIGHT.get(i["severity"], 9)))

    strengths = [
        {**s, "category": c["id"], "category_label": c["label"]}
        for c in categories
        for s in c["strengths"]
        if s["impact"] > 0
    ]
    strengths.sort(key=lambda s: -s["impact"])

    weakest = min(categories, key=lambda c: c["score_exact"])
    strongest = max(categories, key=lambda c: c["score_exact"])
    confidence = round(sum(c["confidence"] * active_weights[c["id"]] for c in categories), 2)

    return {
        "version": 2,
        "overall": overall,
        "overall_exact": round(overall_exact, 2),
        "max": 100,
        "grade": grade_for(overall),
        "band": band_for(overall),
        "confidence": confidence,
        "weights": active_weights,
        "categories": categories,
        "category_index": {c["id"]: c["score"] for c in categories},
        "roadmap": roadmap,
        "top_issues": all_issues[:12],
        "strengths": strengths[:10],
        "potential_score": int(round(_clamp(overall + roadmap["total_potential_gain"]))),
        "weakest_category": {"id": weakest["id"], "label": weakest["label"], "score": weakest["score"]},
        "strongest_category": {"id": strongest["id"], "label": strongest["label"], "score": strongest["score"]},
        "headline": _headline(overall, weakest, strongest),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _headline(overall: int, weakest: dict[str, Any], strongest: dict[str, Any]) -> str:
    if overall >= 90:
        state = "in excellent health"
    elif overall >= 75:
        state = "healthy with a few gaps"
    elif overall >= 60:
        state = "workable but carrying real risk"
    elif overall >= 40:
        state = "under strain"
    else:
        state = "in critical condition"
    return (
        f"This architecture scores {overall}/100 and is {state}. "
        f"{strongest['label']} is the strongest area ({strongest['score']}/100) "
        f"while {weakest['label']} needs the most work ({weakest['score']}/100)."
    )


# --------------------------------------------------------------------------- #
# Weight persistence
# --------------------------------------------------------------------------- #
def weights_path():
    from app.config import settings

    return settings.resolved_data_dir / "scoring_weights.json"


def load_weights() -> dict[str, float]:
    path = weights_path()
    try:
        if path.exists():
            return normalise_weights(json.loads(path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 - a corrupt file must not break analysis
        log.warning("could not read %s, falling back to default weights", path)
    return dict(DEFAULT_WEIGHTS)


def save_weights(raw: dict[str, Any] | None) -> dict[str, float]:
    weights = normalise_weights(raw)
    path = weights_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(weights, indent=2), encoding="utf-8")
    tmp.replace(path)
    return weights


def reset_weights() -> dict[str, float]:
    return save_weights(dict(DEFAULT_WEIGHTS))


def rescore(graph, metrics: dict[str, Any], signals: dict[str, Any], history: dict[str, Any] | None = None) -> dict[str, Any]:
    """Re-evaluate an existing analysis with the currently configured weights."""
    return evaluate(graph, metrics, signals, history=history, weights=load_weights())
