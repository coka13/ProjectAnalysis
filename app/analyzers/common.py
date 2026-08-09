"""Shared helpers for brace-language regex analyzers."""

from __future__ import annotations

import re
from dataclasses import dataclass

VISIBILITY_KEYWORDS = ("public", "private", "protected", "internal")

METHOD_RE = re.compile(
    r"(?P<modifiers>(?:\b(?:public|private|protected|internal|static|final|abstract|virtual|override|async|extern|unsafe|sealed|partial)\b\s+)*)"
    r"(?P<ret>[A-Za-z_][\w<>,\[\]\.\?\* ]*?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^;{)]*)\)\s*(?:const\s*)?(?:->[^{;]+)?(?:\{|;)",
    re.MULTILINE,
)

FIELD_RE = re.compile(
    r"(?P<modifiers>(?:\b(?:public|private|protected|internal|static|final|readonly|const|volatile|mutable)\b\s+)+)"
    r"(?P<type>[A-Za-z_][\w<>,\[\]\.\?\*]*)\s+(?P<name>[A-Za-z_]\w*)\s*(?:=[^;]+)?;",
    re.MULTILINE,
)

CONTROL_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "foreach", "using", "lock", "do", "else"}


@dataclass
class Block:
    name: str
    keyword: str
    header: str
    body: str
    start_line: int
    groups: dict[str, str]


def matching_block(text: str, open_index: int) -> tuple[int, str]:
    """Return (end_index, body) for the brace block starting at ``open_index``."""
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index, text[open_index + 1 : index]
    return len(text), text[open_index + 1 :]


def iter_blocks(text: str, pattern: re.Pattern[str]) -> list[Block]:
    """Find declarations matching ``pattern`` and capture their brace body."""
    blocks: list[Block] = []
    for match in pattern.finditer(text):
        open_index = text.find("{", match.end() - 1)
        if open_index == -1:
            continue
        # Guard against picking up a brace far away (forward declaration).
        if open_index - match.end() > 200:
            continue
        _, body = matching_block(text, open_index)
        groups = {k: (v or "").strip() for k, v in match.groupdict().items()}
        blocks.append(
            Block(
                name=groups.get("name", ""),
                keyword=groups.get("keyword", ""),
                header=match.group(0),
                body=body,
                start_line=text.count("\n", 0, match.start()) + 1,
                groups=groups,
            )
        )
    return blocks


def visibility_of(modifiers: str, default: str = "public") -> str:
    lowered = modifiers.lower()
    for keyword in VISIBILITY_KEYWORDS:
        if re.search(rf"\b{keyword}\b", lowered):
            return "package" if keyword == "internal" else keyword
    return default


def parse_params(raw: str) -> list[dict]:
    params: list[dict] = []
    depth = 0
    current = ""
    for char in raw:
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth -= 1
        if char == "," and depth == 0:
            params.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        params.append(current)
    result: list[dict] = []
    for chunk in params:
        tokens = [t for t in re.split(r"\s+", chunk.strip().replace("*", "* ")) if t and t not in {"final", "const", "in", "out", "ref", "params"}]
        if not tokens:
            continue
        if len(tokens) == 1:
            result.append({"name": tokens[0], "type": ""})
        else:
            result.append({"name": tokens[-1].strip("&*"), "type": " ".join(tokens[:-1])})
    return result


def extract_methods(body: str, *, default_visibility: str = "public", limit: int = 40) -> list[dict]:
    methods: list[dict] = []
    seen: set[str] = set()
    for match in METHOD_RE.finditer(body):
        name = match.group("name")
        if name in CONTROL_KEYWORDS or name in seen:
            continue
        modifiers = match.group("modifiers") or ""
        return_type = (match.group("ret") or "").strip()
        if return_type in CONTROL_KEYWORDS or not return_type:
            continue
        seen.add(name)
        methods.append(
            {
                "name": name,
                "returns": return_type.split()[-1],
                "params": parse_params(match.group("params") or ""),
                "visibility": visibility_of(modifiers, default_visibility),
                "is_static": "static" in modifiers,
                "is_abstract": "abstract" in modifiers,
                "is_async": "async" in modifiers,
            }
        )
        if len(methods) >= limit:
            break
    return methods


def extract_fields(body: str, *, default_visibility: str = "private", limit: int = 40) -> list[dict]:
    fields: list[dict] = []
    seen: set[str] = set()
    for match in FIELD_RE.finditer(body):
        name = match.group("name")
        if name in seen:
            continue
        seen.add(name)
        fields.append(
            {
                "name": name,
                "type": match.group("type"),
                "visibility": visibility_of(match.group("modifiers") or "", default_visibility),
            }
        )
        if len(fields) >= limit:
            break
    return fields


def split_type_list(raw: str) -> list[str]:
    """Split an inheritance clause into individual type names."""
    if not raw:
        return []
    cleaned = re.sub(r"\bwhere\b.*$", "", raw, flags=re.DOTALL)
    parts: list[str] = []
    depth = 0
    current = ""
    for char in cleaned:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current)
    result = []
    for part in parts:
        name = part.strip()
        name = re.sub(r"^(public|private|protected|virtual)\s+", "", name)
        name = name.split("<")[0].strip()
        if name and name[0].isalpha() or name.startswith("_"):
            result.append(name.split(".")[-1])
    return result


CALL_RE = re.compile(r"(?:(?P<recv>[A-Za-z_]\w*)\s*(?:\.|->|::))?(?P<name>[A-Za-z_]\w*)\s*\(")


def extract_calls(body: str, *, limit: int = 60) -> list[tuple[str, str]]:
    """Return (receiver, callee) pairs found in a body."""
    calls: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in CALL_RE.finditer(body):
        name = match.group("name")
        if name in CONTROL_KEYWORDS or len(name) < 3:
            continue
        pair = (match.group("recv") or "", name)
        if pair in seen:
            continue
        seen.add(pair)
        calls.append(pair)
        if len(calls) >= limit:
            break
    return calls
