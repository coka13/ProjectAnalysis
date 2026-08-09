"""Behavioural diagrams: sequence flows and state machines."""

from __future__ import annotations

from typing import Any

from app.diagrams.base import DiagramFilters, DiagramResult, EmptyDiagramError, escape_label, safe_id
from app.graph.model import EdgeKind, KnowledgeGraph, Node, NodeKind

MAX_DEPTH = 7
MAX_PARTICIPANTS = 9

TERMINAL_TOKENS = {"completed", "complete", "done", "closed", "failed", "error", "cancelled", "canceled",
                   "rejected", "expired", "archived", "deleted", "finished", "succeeded"}
INITIAL_TOKENS = {"new", "created", "draft", "pending", "initial", "init", "idle", "queued", "unknown", "none"}


# --------------------------------------------------------------------------- #
# Sequence
# --------------------------------------------------------------------------- #
def _participant_for(graph: KnowledgeGraph, node: Node) -> Node:
    """Return the owning class/component for a callable node."""
    if node.kind in {NodeKind.METHOD, NodeKind.FUNCTION}:
        for edge in graph.in_edges(node.id, [EdgeKind.CONTAINS]):
            owner = graph.nodes.get(edge.source)
            if owner and owner.kind in {NodeKind.CLASS, NodeKind.ABSTRACT_CLASS, NodeKind.STRUCT, NodeKind.INTERFACE}:
                return owner
        component_id = f"component:{(node.module or '').split('/')[0]}"
        component = graph.nodes.get(component_id)
        if component:
            return component
    return node


def _pick_entry_points(graph: KnowledgeGraph, filters: DiagramFilters) -> list[Node]:
    endpoints = graph.by_kind(NodeKind.API_ENDPOINT)
    if filters.focus:
        needle = filters.focus.lower()
        focused = [
            node
            for node in endpoints
            if needle in node.name.lower() or needle in (node.file or "").lower() or needle in (node.module or "").lower()
        ]
        if focused:
            return sorted(focused, key=lambda n: -graph.degree(n.id))[:3]
        callables = [
            node
            for node in graph.by_kind(NodeKind.FUNCTION, NodeKind.METHOD)
            if needle in node.name.lower() or needle in (node.qualified_name or "").lower()
        ]
        if callables:
            return sorted(callables, key=lambda n: -len(graph.out_edges(n.id, [EdgeKind.CALLS])))[:3]
    if endpoints:
        return sorted(endpoints, key=lambda n: -graph.degree(n.id))[:3]
    callables = [
        node
        for node in graph.by_kind(NodeKind.FUNCTION, NodeKind.METHOD)
        if len(graph.out_edges(node.id, [EdgeKind.CALLS])) >= 2
    ]
    if not callables:
        raise EmptyDiagramError("No API endpoints or call chains were detected for a sequence view.")
    return sorted(callables, key=lambda n: -len(graph.out_edges(n.id, [EdgeKind.CALLS])))[:2]


def _walk(graph: KnowledgeGraph, start: Node, depth_limit: int) -> list[tuple[Node, Node, str]]:
    """Depth-first traversal of the call chain, returning (caller, callee, label)."""
    interactions: list[tuple[Node, Node, str]] = []
    visited: set[str] = {start.id}
    stack: list[tuple[Node, int]] = [(start, 0)]
    while stack:
        current, depth = stack.pop()
        if depth >= depth_limit:
            continue
        outgoing = graph.out_edges(
            current.id, [EdgeKind.EXPOSES, EdgeKind.CALLS, EdgeKind.USES, EdgeKind.READS, EdgeKind.WRITES]
        )
        outgoing.sort(key=lambda edge: -edge.weight)
        for edge in outgoing[:4]:
            target = graph.nodes.get(edge.target)
            if not target or target.id in visited:
                continue
            visited.add(target.id)
            label = {
                EdgeKind.EXPOSES: "handle request",
                EdgeKind.READS: "query",
                EdgeKind.WRITES: "persist",
                EdgeKind.USES: "use",
            }.get(edge.kind, target.name)
            interactions.append((current, target, label))
            stack.append((target, depth + 1))
        if len(interactions) > 40:
            break
    return interactions


def generate_sequence(graph: KnowledgeGraph, filters: DiagramFilters) -> DiagramResult:
    entries = _pick_entry_points(graph, filters)
    entry = entries[0]
    depth_limit = {"executive": 3, "standard": 5, "detailed": MAX_DEPTH}.get(filters.detail, 5)
    interactions = _walk(graph, entry, depth_limit)
    if not interactions:
        raise EmptyDiagramError("The selected flow has no resolvable downstream calls.")

    participants: dict[str, Node] = {}
    actor = graph.nodes.get("actor:user")
    if actor:
        participants[actor.id] = actor

    ordered_pairs: list[tuple[Node, Node, str]] = []
    for caller, callee, label in interactions:
        caller_participant = _participant_for(graph, caller)
        callee_participant = _participant_for(graph, callee)
        if caller_participant.id == callee_participant.id:
            continue
        for participant in (caller_participant, callee_participant):
            if participant.id not in participants and len(participants) < MAX_PARTICIPANTS:
                participants[participant.id] = participant
        if caller_participant.id in participants and callee_participant.id in participants:
            ordered_pairs.append((caller_participant, callee_participant, label or callee.name))

    if entry.id not in participants and len(participants) < MAX_PARTICIPANTS:
        participants[entry.id] = entry

    aliases = {node_id: safe_id(node_id) for node_id in participants}
    lines = ["sequenceDiagram", "  autonumber"]
    for node in participants.values():
        keyword = "actor" if node.kind == NodeKind.ACTOR else "participant"
        lines.append(f'  {keyword} {aliases[node.id]} as {escape_label(node.name, 28)}')

    if actor and entry.id in participants and actor.id != entry.id:
        lines.append(f"  {aliases[actor.id]}->>+{aliases[entry.id]}: {escape_label(entry.name, 34)}")

    payload_steps: list[dict[str, Any]] = []
    for index, (caller, callee, label) in enumerate(ordered_pairs[:24], start=1):
        arrow = "->>+" if callee.kind not in {NodeKind.TABLE, NodeKind.DATABASE} else "->>"
        lines.append(f"  {aliases[caller.id]}{arrow}{aliases[callee.id]}: {escape_label(label, 34)}")
        if callee.kind in {NodeKind.TABLE, NodeKind.DATABASE, NodeKind.DATA_STORE}:
            lines.append(f"  {aliases[callee.id]}-->>{aliases[caller.id]}: result set")
        else:
            lines.append(f"  {aliases[callee.id]}-->>-{aliases[caller.id]}: response")
        payload_steps.append(
            {"step": index, "from": caller.id, "to": callee.id, "label": label, "kind": callee.kind}
        )

    if actor and entry.id in participants and actor.id != entry.id:
        lines.append(f"  {aliases[entry.id]}-->>-{aliases[actor.id]}: response")

    title = f"Sequence: {entry.name}"
    notes = [f"Flow derived from {entry.kind.replace('_', ' ')} '{entry.name}'."]
    if len(entries) > 1:
        notes.append(f"{len(entries) - 1} additional entry point(s) available: " + ", ".join(e.name for e in entries[1:]))

    plantuml_lines = ["@startuml", "skinparam backgroundColor transparent", "autonumber"]
    for node in participants.values():
        keyword = "actor" if node.kind == NodeKind.ACTOR else "participant"
        plantuml_lines.append(f'{keyword} "{escape_label(node.name, 28)}" as {aliases[node.id]}')
    for caller, callee, label in ordered_pairs[:24]:
        plantuml_lines.append(f"{aliases[caller.id]} -> {aliases[callee.id]} : {escape_label(label, 34)}")
        plantuml_lines.append(f"{aliases[callee.id]} --> {aliases[caller.id]}")
    plantuml_lines.append("@enduml")

    return DiagramResult(
        kind="sequence",
        title=title,
        mermaid="\n".join(lines),
        plantuml="\n".join(plantuml_lines),
        payload={
            "entry": {"id": entry.id, "name": entry.name, "kind": entry.kind, "file": entry.file},
            "participants": [
                {"id": node.id, "alias": aliases[node.id], "name": node.name, "kind": node.kind, "file": node.file}
                for node in participants.values()
            ],
            "steps": payload_steps,
            "alternatives": [{"id": node.id, "name": node.name} for node in entries[1:]],
            "legend": [{"key": "->>", "label": "synchronous call"}, {"key": "-->>", "label": "return"}],
        },
        scope=filters.to_dict(),
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------------- #
def _transition_label(source: str, target: str) -> str:
    lowered = target.lower()
    if lowered in {"failed", "error"}:
        return "on error"
    if lowered in {"cancelled", "canceled"}:
        return "cancel"
    if lowered in {"approved"}:
        return "approve"
    if lowered in {"rejected"}:
        return "reject"
    return f"{source.lower()} → {lowered}".split(" → ")[1][:18]


def generate_state(graph: KnowledgeGraph, filters: DiagramFilters) -> DiagramResult:
    machines = [node for node in graph.by_kind(NodeKind.ENUM) if node.attributes.get("is_state_machine")]
    if filters.focus:
        needle = filters.focus.lower()
        focused = [node for node in machines if needle in node.name.lower()]
        machines = focused or machines
    if not machines:
        machines = [node for node in graph.by_kind(NodeKind.ENUM) if len(node.attributes.get("members") or []) >= 3]
    if not machines:
        raise EmptyDiagramError("No state machines, status enums or lifecycle types were detected.")

    machine = max(machines, key=lambda node: len(node.attributes.get("members") or []))
    states = [str(state) for state in (machine.attributes.get("states") or machine.attributes.get("members") or [])][:14]
    if len(states) < 2:
        raise EmptyDiagramError("The detected state type does not define enough states.")

    initial = next((s for s in states if s.lower() in INITIAL_TOKENS), states[0])
    terminals = [s for s in states if s.lower() in TERMINAL_TOKENS]
    intermediate = [s for s in states if s != initial and s not in terminals]

    lines = ["stateDiagram-v2", "  direction LR", f"  [*] --> {safe_id(initial)}"]
    aliases = {state: safe_id(state) for state in states}
    for state in states:
        lines.append(f'  {aliases[state]} : {escape_label(state.replace("_", " ").title(), 26)}')

    transitions: list[dict[str, str]] = []
    chain = [initial, *intermediate]
    for index in range(len(chain) - 1):
        source, target = chain[index], chain[index + 1]
        label = _transition_label(source, target)
        lines.append(f"  {aliases[source]} --> {aliases[target]} : {escape_label(label, 20)}")
        transitions.append({"from": source, "to": target, "event": label})

    last_active = chain[-1] if chain else initial
    for terminal in terminals:
        source = last_active if last_active != terminal else initial
        label = _transition_label(source, terminal)
        lines.append(f"  {aliases[source]} --> {aliases[terminal]} : {escape_label(label, 20)}")
        lines.append(f"  {aliases[terminal]} --> [*]")
        transitions.append({"from": source, "to": terminal, "event": label})
    if not terminals:
        lines.append(f"  {aliases[last_active]} --> [*]")

    other_machines = [node.name for node in machines if node.id != machine.id][:6]
    notes = [
        f"Lifecycle inferred from '{machine.name}' in {machine.file or 'the codebase'}.",
        "Transitions are inferred from state ordering and naming conventions; verify against the implementation.",
    ]
    if other_machines:
        notes.append("Other state types detected: " + ", ".join(other_machines) + ".")

    plantuml = "\n".join(
        [
            "@startuml",
            "skinparam backgroundColor transparent",
            "hide empty description",
            f"[*] --> {safe_id(initial)}",
            *[f"{safe_id(t['from'])} --> {safe_id(t['to'])} : {escape_label(t['event'], 20)}" for t in transitions],
            *[f"{safe_id(terminal)} --> [*]" for terminal in terminals],
            "@enduml",
        ]
    )

    return DiagramResult(
        kind="state",
        title=f"State Machine: {machine.name}",
        mermaid="\n".join(lines),
        plantuml=plantuml,
        payload={
            "machine": {"id": machine.id, "name": machine.name, "file": machine.file, "line": machine.line},
            "states": [
                {"name": state, "alias": aliases[state], "terminal": state in terminals, "initial": state == initial}
                for state in states
            ],
            "transitions": transitions,
            "alternatives": [{"id": node.id, "name": node.name} for node in machines if node.id != machine.id][:10],
            "legend": [{"key": "[*]", "label": "start / end"}],
        },
        scope=filters.to_dict(),
        notes=notes,
    )
