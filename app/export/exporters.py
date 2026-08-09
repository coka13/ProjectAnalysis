"""Export diagrams to Mermaid, PlantUML, Markdown, HTML, Draw.io and JSON."""

from __future__ import annotations

import html
import json
import xml.etree.ElementTree as ET
from typing import Any

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs"

EXPORT_FORMATS = ["mermaid", "plantuml", "markdown", "html", "drawio", "json"]

MIME_TYPES = {
    "mermaid": "text/plain; charset=utf-8",
    "plantuml": "text/plain; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "drawio": "application/xml; charset=utf-8",
    "json": "application/json; charset=utf-8",
}

FILE_EXTENSIONS = {
    "mermaid": "mmd",
    "plantuml": "puml",
    "markdown": "md",
    "html": "html",
    "drawio": "drawio",
    "json": "json",
}

_LABELS = {
    "en": {
        "purpose": "Purpose",
        "description": "Description",
        "components": "Key components",
        "patterns": "Detected patterns",
        "risks": "Risks",
        "improvements": "Improvement suggestions",
        "notes": "Notes",
        "source": "Source",
        "diagram": "Diagram",
    },
    "he": {
        "purpose": "מטרה",
        "description": "תיאור",
        "components": "רכיבים מרכזיים",
        "patterns": "תבניות שזוהו",
        "risks": "סיכונים",
        "improvements": "המלצות לשיפור",
        "notes": "הערות",
        "source": "מקור",
        "diagram": "תרשים",
    },
}


def _label(language: str, key: str) -> str:
    return _LABELS.get(language, _LABELS["en"]).get(key, key)


def export(diagram: dict[str, Any], fmt: str, *, language: str = "en") -> str:
    if fmt == "mermaid":
        return diagram.get("mermaid", "")
    if fmt == "plantuml":
        return diagram.get("plantuml") or _plantuml_placeholder(diagram)
    if fmt == "markdown":
        return to_markdown(diagram, language=language)
    if fmt == "html":
        return to_html(diagram, language=language)
    if fmt == "drawio":
        return to_drawio(diagram)
    if fmt == "json":
        return json.dumps(diagram, ensure_ascii=False, indent=2)
    raise ValueError(f"Unsupported export format: {fmt}")


def _plantuml_placeholder(diagram: dict[str, Any]) -> str:
    return "\n".join(
        [
            "@startuml",
            f"title {diagram.get('title', 'Diagram')}",
            "note as N1",
            "  A native PlantUML rendering is not available for this diagram kind.",
            "  Use the Mermaid or Draw.io export instead.",
            "end note",
            "@enduml",
        ]
    )


# --------------------------------------------------------------------------- #
def to_markdown(diagram: dict[str, Any], *, language: str = "en") -> str:
    explanation = (diagram.get("explanation") or {}).get(language) or (diagram.get("explanation") or {}).get("en") or {}
    lines = [f"# {diagram.get('title', 'Diagram')}", ""]

    if explanation.get("purpose"):
        lines += [f"**{_label(language, 'purpose')}:** {explanation['purpose']}", ""]
    if explanation.get("description"):
        lines += [f"## {_label(language, 'description')}", "", explanation["description"], ""]

    lines += [f"## {_label(language, 'diagram')}", "", "```mermaid", diagram.get("mermaid", ""), "```", ""]

    components = explanation.get("key_components") or []
    if components:
        lines += [f"## {_label(language, 'components')}", ""]
        for item in components:
            lines.append(f"- **{item.get('name', '')}** - {item.get('role', '')}")
        lines.append("")

    patterns = explanation.get("patterns") or []
    if patterns:
        lines += [f"## {_label(language, 'patterns')}", ""]
        for item in patterns:
            lines.append(f"- **{item.get('pattern', '')}** - {item.get('evidence', '')}")
        lines.append("")

    risks = explanation.get("risks") or []
    if risks:
        lines += [f"## {_label(language, 'risks')}", ""]
        for item in risks:
            lines.append(f"- `{item.get('severity', 'low')}` {item.get('issue', '')}")
        lines.append("")

    improvements = explanation.get("improvements") or []
    if improvements:
        lines += [f"## {_label(language, 'improvements')}", ""]
        for index, item in enumerate(improvements, start=1):
            lines.append(f"{index}. {item}")
        lines.append("")

    notes = diagram.get("notes") or []
    if notes:
        lines += [f"## {_label(language, 'notes')}", ""]
        lines += [f"- {note}" for note in notes]
        lines.append("")

    return "\n".join(lines)


def to_html(diagram: dict[str, Any], *, language: str = "en") -> str:
    direction = "rtl" if language == "he" else "ltr"
    explanation = (diagram.get("explanation") or {}).get(language) or (diagram.get("explanation") or {}).get("en") or {}
    escaped_title = html.escape(str(diagram.get("title", "Diagram")))
    mermaid_source = html.escape(diagram.get("mermaid", ""))

    def section(title: str, items: list[str]) -> str:
        if not items:
            return ""
        rendered = "".join(f"<li>{html.escape(item)}</li>" for item in items)
        return f"<section><h2>{html.escape(title)}</h2><ul>{rendered}</ul></section>"

    risks = [f"[{item.get('severity', 'low')}] {item.get('issue', '')}" for item in explanation.get("risks") or []]
    components = [f"{item.get('name', '')} - {item.get('role', '')}" for item in explanation.get("key_components") or []]
    patterns = [f"{item.get('pattern', '')} - {item.get('evidence', '')}" for item in explanation.get("patterns") or []]

    return f"""<!DOCTYPE html>
<html lang="{html.escape(language)}" dir="{direction}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escaped_title}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; padding:2rem; background:#0f1319; color:#e6edf5;
         font-family:'Segoe UI',system-ui,'Noto Sans Hebrew',sans-serif; line-height:1.6; }}
  h1 {{ font-size:1.6rem; margin-top:0; }}
  h2 {{ font-size:1.1rem; color:#9fb8d4; border-bottom:1px solid #24303d; padding-bottom:.35rem; }}
  .diagram {{ background:#151b23; border:1px solid #24303d; border-radius:12px; padding:1.25rem; overflow:auto; }}
  ul {{ padding-inline-start:1.25rem; }}
  footer {{ margin-top:2rem; color:#6f8296; font-size:.85rem; }}
</style>
</head>
<body>
<h1>{escaped_title}</h1>
<p>{html.escape(str(explanation.get("purpose", "")))}</p>
<div class="diagram"><pre class="mermaid">{mermaid_source}</pre></div>
<p>{html.escape(str(explanation.get("description", "")))}</p>
{section(_label(language, "components"), components)}
{section(_label(language, "patterns"), patterns)}
{section(_label(language, "risks"), risks)}
{section(_label(language, "improvements"), list(explanation.get("improvements") or []))}
{section(_label(language, "notes"), list(diagram.get("notes") or []))}
<footer>ProjectAnalysis</footer>
<script type="module">
  import mermaid from '{MERMAID_CDN}';
  mermaid.initialize({{ startOnLoad: true, theme: 'dark', securityLevel: 'strict' }});
</script>
</body>
</html>"""


# --------------------------------------------------------------------------- #
def to_drawio(diagram: dict[str, Any]) -> str:
    """Produce a draw.io / diagrams.net compatible mxGraph document."""
    payload = diagram.get("payload") or {}
    nodes = payload.get("nodes") or payload.get("entities") or payload.get("participants") or []
    edges = payload.get("edges") or payload.get("relations") or payload.get("steps") or []

    mxfile = ET.Element("mxfile", {"host": "projectanalysis", "type": "device"})
    page = ET.SubElement(mxfile, "diagram", {"name": str(diagram.get("title", "Diagram"))[:60], "id": "page-1"})
    model = ET.SubElement(
        page,
        "mxGraphModel",
        {"dx": "1200", "dy": "800", "grid": "1", "gridSize": "10", "page": "1", "pageWidth": "1600", "pageHeight": "1100"},
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

    id_map: dict[str, str] = {}
    columns = 4
    for index, node in enumerate(nodes[:200]):
        node_id = str(node.get("id") or node.get("name") or f"n{index}")
        cell_id = f"node-{index + 2}"
        id_map[node_id] = cell_id
        label = str(node.get("name") or node.get("entity") or node_id)
        style = _drawio_style(str(node.get("kind", "")))
        cell = ET.SubElement(
            root,
            "mxCell",
            {"id": cell_id, "value": label, "style": style, "vertex": "1", "parent": "1"},
        )
        ET.SubElement(
            cell,
            "mxGeometry",
            {
                "x": str(80 + (index % columns) * 260),
                "y": str(80 + (index // columns) * 140),
                "width": "200",
                "height": "70",
                "as": "geometry",
            },
        )

    for index, edge in enumerate(edges[:400]):
        source = str(edge.get("source") or edge.get("from") or "")
        target = str(edge.get("target") or edge.get("to") or "")
        if source not in id_map or target not in id_map:
            continue
        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": f"edge-{index}",
                "value": str(edge.get("label") or edge.get("kind") or ""),
                "style": "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;",
                "edge": "1",
                "parent": "1",
                "source": id_map[source],
                "target": id_map[target],
            },
        )
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(mxfile, encoding="unicode")


def _drawio_style(kind: str) -> str:
    base = "rounded=1;whiteSpace=wrap;html=1;fontSize=12;"
    styles = {
        "database": "shape=cylinder3;boundedLbl=1;backgroundOutline=1;fillColor=#f8cecc;strokeColor=#b85450;",
        "table": "shape=cylinder3;boundedLbl=1;backgroundOutline=1;fillColor=#f8cecc;strokeColor=#b85450;",
        "queue": "shape=parallelogram;fillColor=#ffe6cc;strokeColor=#d79b00;",
        "actor": "shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;",
        "external_api": "shape=hexagon;fillColor=#fff2cc;strokeColor=#d6b656;",
        "container": "shape=process;fillColor=#dae8fc;strokeColor=#6c8ebf;",
        "component": "shape=component;align=left;spacingLeft=36;fillColor=#dae8fc;strokeColor=#6c8ebf;",
        "api_endpoint": "shape=parallelogram;fillColor=#d5e8d4;strokeColor=#82b366;",
        "interface": "fillColor=#e1d5e7;strokeColor=#9673a6;dashed=1;",
    }
    return base + styles.get(kind, "fillColor=#eeeeee;strokeColor=#666666;")


def bundle_markdown(project_name: str, diagrams: list[dict[str, Any]], review: dict[str, Any], language: str) -> str:
    """Full documentation package: review + every diagram."""
    lines = [f"# {project_name} - Architecture Package", ""]
    if review:
        lines += [
            f"**Score:** {review.get('score', '-')} / 100 ({review.get('grade', '-')})",
            "",
            review.get("summary", ""),
            "",
        ]
        if review.get("strengths"):
            lines += ["## Strengths", ""] + [f"- {item}" for item in review["strengths"]] + [""]
        if review.get("issues"):
            lines += ["## Issues", ""] + [
                f"- `{item.get('severity', 'low')}` {item.get('issue', '')}" for item in review["issues"]
            ] + [""]
        if review.get("recommendations"):
            lines += ["## Recommendations", ""] + [
                f"{index}. **{item.get('title', '')}** - {item.get('detail', '')}"
                for index, item in enumerate(review["recommendations"], start=1)
            ] + [""]
    for diagram in diagrams:
        lines.append(to_markdown(diagram, language=language))
        lines.append("\n---\n")
    return "\n".join(lines)
