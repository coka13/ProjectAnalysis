"""Prompt construction for architecture insight tasks.

All prompts instruct the model to keep code identifiers (class, function, API and
file names) untranslated so Hebrew output stays actionable for engineers.
"""

from __future__ import annotations

import json
from typing import Any

from app.graph.model import EdgeKind, KnowledgeGraph, NodeKind

LANGUAGE_NAME = {"en": "English", "he": "Hebrew"}

IDENTIFIER_RULE = (
    "Never translate or alter code identifiers: class names, interface names, function and method names, "
    "module names, API routes, table names, environment variable names and file paths must appear exactly "
    "as given, in Latin characters."
)

BASE_SYSTEM = (
    "You are a principal software architect performing a rigorous review of a real codebase. "
    "You reason only from the structured evidence provided; you never invent components, files or metrics. "
    "If evidence is insufficient, you say so explicitly. Be concise, specific and actionable."
)


def language_directive(language: str) -> str:
    name = LANGUAGE_NAME.get(language, "English")
    directive = f"Write all prose in {name}."
    if language == "he":
        directive += " Use natural, professional Hebrew suitable for a right-to-left document. " + IDENTIFIER_RULE
    else:
        directive += " " + IDENTIFIER_RULE
    return directive


def system_message(language: str) -> dict[str, str]:
    return {"role": "system", "content": f"{BASE_SYSTEM}\n{language_directive(language)}"}


# --------------------------------------------------------------------------- #
# Context builders
# --------------------------------------------------------------------------- #
def graph_summary(graph: KnowledgeGraph, metrics: dict[str, Any] | None = None, *, limit: int = 40) -> str:
    metrics = metrics or (graph.meta.get("metrics") if graph.meta else {}) or {}
    stats = graph.stats()

    components = sorted(
        graph.by_kind(NodeKind.COMPONENT),
        key=lambda node: -(node.attributes.get("file_count") or 0),
    )[:limit]
    component_lines = [
        f"- {node.name} (layer={node.attributes.get('layer', 'unassigned')}, "
        f"files={node.attributes.get('file_count', 0)}, "
        f"languages={','.join((node.attributes.get('languages') or {}).keys()) or 'n/a'})"
        for node in components
    ]

    key_types = sorted(
        graph.by_kind(NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ABSTRACT_CLASS),
        key=lambda node: -graph.degree(node.id),
    )[:limit]
    type_lines = [
        f"- {node.name} [{node.kind}] module={node.module} stereotype={node.attributes.get('stereotype', '-')} "
        f"methods={len(node.attributes.get('methods') or [])} degree={graph.degree(node.id)}"
        for node in key_types
    ]

    endpoints = graph.by_kind(NodeKind.API_ENDPOINT)[:20]
    stores = graph.by_kind(NodeKind.DATABASE, NodeKind.TABLE, NodeKind.DATA_STORE)[:20]
    queues = graph.by_kind(NodeKind.QUEUE)[:10]
    externals = graph.by_kind(NodeKind.EXTERNAL_API)[:10]
    containers = graph.by_kind(NodeKind.CONTAINER)[:15]

    sections = [
        "PROJECT STATISTICS",
        json.dumps(
            {
                "files_by_language": stats.get("files_by_language", {}),
                "nodes_by_kind": stats.get("nodes_by_kind", {}),
                "edges_by_kind": stats.get("edges_by_kind", {}),
            },
            ensure_ascii=False,
        ),
        "",
        "COMPONENTS",
        *(component_lines or ["- none detected"]),
        "",
        "KEY TYPES",
        *(type_lines or ["- none detected"]),
        "",
        "INTEGRATION SURFACE",
        f"- API endpoints: {', '.join(n.name for n in endpoints) or 'none'}",
        f"- Data stores: {', '.join(n.name for n in stores) or 'none'}",
        f"- Message queues: {', '.join(n.name for n in queues) or 'none'}",
        f"- External systems: {', '.join(n.name for n in externals) or 'none'}",
        f"- Containers/workloads: {', '.join(n.name for n in containers) or 'none'}",
        "",
        "ARCHITECTURE METRICS",
        json.dumps(
            {
                "score": metrics.get("score"),
                "layers": metrics.get("layers"),
                "cycles": [c["modules"] for c in (metrics.get("cycles") or [])[:6]],
                "top_coupling": (metrics.get("coupling") or [])[:6],
                "god_classes": [g["name"] for g in (metrics.get("god_classes") or [])[:6]],
                "layering_violations": (metrics.get("layering_violations") or [])[:6],
                "detected_patterns": metrics.get("patterns"),
                "abstraction_ratio": metrics.get("abstraction_ratio"),
            },
            ensure_ascii=False,
        ),
    ]
    return "\n".join(sections)


def diagram_context(diagram: dict[str, Any]) -> str:
    payload = diagram.get("payload") or {}
    trimmed = {
        "kind": diagram.get("kind"),
        "title": diagram.get("title"),
        "nodes": (payload.get("nodes") or payload.get("entities") or payload.get("participants") or [])[:60],
        "edges": (payload.get("edges") or payload.get("relations") or payload.get("steps") or [])[:80],
        "notes": diagram.get("notes") or [],
        "elided": payload.get("elided", 0),
    }
    return json.dumps(trimmed, ensure_ascii=False)[:12000]


# --------------------------------------------------------------------------- #
# Task prompts
# --------------------------------------------------------------------------- #
def explain_diagram(diagram: dict[str, Any], graph_context: str, language: str) -> list[dict[str, str]]:
    schema = {
        "purpose": "one sentence on what this diagram is for",
        "description": "2-4 sentences describing what the diagram shows",
        "key_components": [{"name": "Identifier", "role": "why it matters"}],
        "patterns": [{"pattern": "name", "evidence": "why you concluded this"}],
        "risks": [
            {
                "severity": "high|medium|low",
                "title": "short name for the problem",
                "issue": "what you observed, naming the specific elements",
                "why": "why this matters in practice, not a restatement of the issue",
                "remediation": "the concrete change that resolves it",
                "impact": "the quality attribute at stake",
                "effort": "how much work this is",
                "evidence": ["the specific facts this conclusion rests on"],
            }
        ],
        "improvements": ["concrete, actionable suggestion"],
    }
    user = (
        "Explain the following software architecture diagram for an engineering audience.\n\n"
        f"DIAGRAM DATA:\n{diagram_context(diagram)}\n\n"
        f"PROJECT CONTEXT:\n{graph_context}\n\n"
        "For every risk, state what you observed, why it matters and what to do about it. "
        "A severity word on its own is not useful to the reader.\n"
        "Respond with a single JSON object using exactly this schema (no markdown, no commentary):\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    return [system_message(language), {"role": "user", "content": user}]


def code_fix(
    finding: dict[str, Any],
    relative: str,
    excerpt: str,
    start_line: int,
    language: str,
) -> list[dict[str, str]]:
    """Ask for a repaired version of one excerpt.

    The model is asked for replacement source text, never for a diff: the diff is
    computed locally from what comes back, so a malformed or dishonest patch
    cannot be produced. The excerpt is bounded, so the model cannot rewrite parts
    of the file it was never shown.
    """
    schema = {
        "diagnosis": "what is actually wrong in this excerpt",
        "explanation": "why the replacement is correct and what behaviour changes",
        "replacement": "the full replacement text for the excerpt, verbatim",
        "confidence": 0.0,
        "risk": "what a reviewer must check before accepting this",
    }
    user = (
        f"A static analyser reported the following problem in {relative}.\n\n"
        f"RULE: {finding.get('rule', '')}\n"
        f"TITLE: {finding.get('title', '')}\n"
        f"PROBLEM: {finding.get('problem', '')}\n"
        f"WHY IT MATTERS: {finding.get('impact', '')}\n"
        f"REPORTED LINES: {finding.get('lines', [])}\n\n"
        f"EXCERPT (file lines {start_line}-{start_line + max(len(excerpt.splitlines()) - 1, 0)}):\n"
        "<<<EXCERPT\n"
        f"{excerpt}"
        "EXCERPT>>>\n\n"
        "Rewrite ONLY this excerpt so the problem is fixed.\n"
        "Rules you must follow:\n"
        "- Return the complete excerpt, not a fragment and not a diff.\n"
        "- Preserve the existing indentation style and the surrounding lines exactly.\n"
        "- Do not add imports unless the fix requires them and the excerpt contains the import block.\n"
        "- Do not reformat, rename or 'improve' anything unrelated to the reported problem.\n"
        "- No markdown fences, no line numbers, no commentary inside `replacement`.\n"
        "- If you cannot fix it safely from this excerpt alone, return an empty string for "
        "`replacement` and explain why in `diagnosis`.\n\n"
        "Respond with a single JSON object using exactly this schema (no markdown):\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    return [system_message(language), {"role": "user", "content": user}]


def architecture_review(graph_context: str, metrics: dict[str, Any], language: str) -> list[dict[str, str]]:
    schema = {
        "summary": "2-3 sentence executive summary",
        "score": 0,
        "strengths": ["..."],
        "issues": [{"severity": "high|medium|low", "area": "component or module", "issue": "...", "evidence": "..."}],
        "recommendations": [{"priority": 1, "title": "...", "detail": "...", "effort": "S|M|L"}],
        "quick_wins": ["..."],
    }
    user = (
        "Review this system's architecture and produce a professional assessment.\n\n"
        f"EVIDENCE:\n{graph_context}\n\n"
        f"COMPUTED METRICS:\n{json.dumps(metrics, ensure_ascii=False)[:6000]}\n\n"
        "The computed score is a static analysis result; keep your reported score within 10 points of it "
        "unless the evidence strongly contradicts it.\n"
        "Respond with a single JSON object using exactly this schema (no markdown):\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    return [system_message(language), {"role": "user", "content": user}]


def refactoring_plan(graph_context: str, findings: dict[str, Any], language: str) -> list[dict[str, str]]:
    schema = {
        "suggestions": [
            {
                "title": "...",
                "problem": "...",
                "approach": "...",
                "affected": ["module or class names"],
                "impact": "high|medium|low",
                "effort": "S|M|L",
                "steps": ["ordered migration steps"],
            }
        ],
        "target_architecture": "short description of the recommended end state",
    }
    user = (
        "Propose refactoring work for the following system.\n\n"
        f"EVIDENCE:\n{graph_context}\n\n"
        f"STATIC FINDINGS:\n{json.dumps(findings, ensure_ascii=False)[:6000]}\n\n"
        "Prioritise structural problems (cycles, god classes, layering violations, missing abstractions). "
        "Respond with a single JSON object using exactly this schema (no markdown):\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    return [system_message(language), {"role": "user", "content": user}]


def natural_language_query(prompt: str, available: dict[str, Any], language: str) -> list[dict[str, str]]:
    schema = {
        "kind": "one of: architecture, component, class, sequence, dataflow, deployment, dependency, database, state",
        "filters": {
            "scope": "project|module|package|selection",
            "modules": ["module names to include"],
            "nodes": ["specific node ids or names"],
            "languages": ["language filters"],
            "include_external": False,
            "max_nodes": 45,
            "detail": "executive|standard|detailed",
            "focus": "free text focus keyword",
        },
        "title": "a short human title for the resulting diagram",
        "reasoning": "one sentence explaining the choice",
    }
    user = (
        "Translate the user's diagram request into a diagram specification.\n\n"
        f"USER REQUEST: {prompt}\n\n"
        f"AVAILABLE MODULES AND COMPONENTS:\n{json.dumps(available, ensure_ascii=False)[:4000]}\n\n"
        "Pick the single most appropriate diagram kind and the narrowest filters that satisfy the request. "
        "Use 'executive' detail for requests aimed at executives or customers.\n"
        "Respond with a single JSON object using exactly this schema (no markdown):\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    return [system_message(language), {"role": "user", "content": user}]


def comparison(diff: dict[str, Any], language: str) -> list[dict[str, str]]:
    schema = {
        "summary": "2-3 sentences describing the architectural change",
        "impact": "high|medium|low",
        "highlights": ["most important changes"],
        "risks": ["risks introduced by these changes"],
        "recommendations": ["what to verify or do next"],
    }
    user = (
        "Two versions of the same system were analysed. Explain the architectural impact of the difference.\n\n"
        f"STRUCTURAL DIFF:\n{json.dumps(diff, ensure_ascii=False)[:8000]}\n\n"
        "Respond with a single JSON object using exactly this schema (no markdown):\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    return [system_message(language), {"role": "user", "content": user}]


def translation(text: str, target: str) -> list[dict[str, str]]:
    name = LANGUAGE_NAME.get(target, "English")
    return [
        {
            "role": "system",
            "content": (
                f"You are a technical translator. Translate the user's text into {name}. "
                f"{IDENTIFIER_RULE} Preserve markdown structure and line breaks. Output only the translation."
            ),
        },
        {"role": "user", "content": text},
    ]


def edge_kind_glossary() -> dict[str, str]:
    return {
        EdgeKind.INHERITS: "inheritance",
        EdgeKind.IMPLEMENTS: "interface implementation",
        EdgeKind.COMPOSES: "composition",
        EdgeKind.CALLS: "invocation",
        EdgeKind.DEPENDS_ON: "dependency",
        EdgeKind.COMMUNICATES_WITH: "network communication",
    }
