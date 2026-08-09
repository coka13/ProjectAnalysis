"""Deterministic code-fix proposals.

The platform never edits a repository on its own. This module only *proposes*
changes: every proposal carries the problem, the root cause, a unified diff and
a digest of the file it was computed against. Applying a proposal is a separate,
explicit call that re-verifies the digest first, so a stale proposal can never
overwrite work the user did in the meantime.

Every rule here is a pure text transformation with a mechanical justification -
there is no model in the loop and no network access, which is what makes the
output reproducible and safe to diff. Rules that cannot be fixed mechanically
(``shell=True``, hardcoded secrets) are still reported, but with
``auto_fixable`` false and no diff, because a wrong "fix" is worse than none.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ingest.walker import walk

log = logging.getLogger("aai.fixes")

MAX_FILE_BYTES = 400_000
DEFAULT_LIMIT = 200


class FixError(RuntimeError):
    """Raised when a proposal cannot be applied safely."""


# --------------------------------------------------------------------------- #
# Rule definitions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Rule:
    """A single mechanical repair.

    ``transform`` receives the whole file text and returns the repaired text
    plus the 1-based line numbers it touched. Returning the input unchanged
    means "nothing to do here".
    """

    id: str
    title: str
    problem: str
    root_cause: str
    impact: str
    severity: str
    effort: str
    confidence: float
    languages: frozenset[str]
    transform: Callable[[str], tuple[str, list[int]]] | None = None
    detect: Callable[[str], list[int]] | None = None
    # Formatting-only rules drown out the findings that actually matter, so they
    # are held back unless the caller explicitly asks for them.
    cosmetic: bool = False
    # Ordered instructions for the rules that cannot be repaired mechanically.
    steps: tuple[str, ...] = ()

    @property
    def auto_fixable(self) -> bool:
        return self.transform is not None


def _line_numbers(text: str, pattern: re.Pattern[str]) -> list[int]:
    return [index for index, line in enumerate(text.splitlines(), start=1) if pattern.search(line)]


def _substitute(
    pattern: re.Pattern[str], replacement: str | Callable[[re.Match[str]], str]
) -> Callable[[str], tuple[str, list[int]]]:
    """Build a line-wise substitution transform.

    Line-wise rather than whole-text so the reported line numbers are exact and
    a single unfixable line never silently swallows the rest of the file.
    """

    def transform(text: str) -> tuple[str, list[int]]:
        touched: list[int] = []
        out: list[str] = []
        for index, line in enumerate(text.splitlines(keepends=True), start=1):
            new_line = pattern.sub(replacement, line)
            if new_line != line:
                touched.append(index)
            out.append(new_line)
        return "".join(out), touched

    return transform


# `except:` with nothing after it swallows KeyboardInterrupt and SystemExit.
_BARE_EXCEPT = re.compile(r"(?<![\w.])except\s*:")
# `x == None` works by accident; identity is the defined comparison for None.
_EQ_NONE = re.compile(r"(?<![=!<>])==\s*None\b")
_NE_NONE = re.compile(r"!=\s*None\b")
# yaml.load without a Loader constructs arbitrary Python objects.
_YAML_LOAD = re.compile(r"\byaml\.load\s*\(")
_DEBUGGER = re.compile(r"^\s*debugger\s*;?\s*$")
_TRAILING_WS = re.compile(r"[ \t]+(?=\r?\n|$)")
_SHELL_TRUE = re.compile(r"\bshell\s*=\s*True\b")
_HTML_SINK = re.compile(r"\.innerHTML\s*=\s*(?![\'\"`]\s*[\'\"`])")

PY = frozenset({"python"})
JS = frozenset({"javascript", "typescript"})
ANY = frozenset()


def _fix_none_identity(text: str) -> tuple[str, list[int]]:
    first, touched_a = _substitute(_NE_NONE, "is not None")(text)
    second, touched_b = _substitute(_EQ_NONE, "is None")(first)
    return second, sorted(set(touched_a) | set(touched_b))


def _fix_debugger(text: str) -> tuple[str, list[int]]:
    touched: list[int] = []
    out: list[str] = []
    for index, line in enumerate(text.splitlines(keepends=True), start=1):
        if _DEBUGGER.match(line):
            touched.append(index)
            continue
        out.append(line)
    return "".join(out), touched


def _fix_final_newline(text: str) -> tuple[str, list[int]]:
    if not text or text.endswith("\n"):
        return text, []
    return text + "\n", [len(text.splitlines())]


# --------------------------------------------------------------------------- #
# Additional mechanical repairs
#
# Each of these is either a construct Python/JS has already removed - so the
# code is broken, not merely unfashionable - or a rewrite that is provably
# equivalent. Anything that could change behaviour stays advisory.
# --------------------------------------------------------------------------- #

# Removed in Python 3.12. The replacement name has always been an exact alias.
_UNITTEST_ALIASES = {
    "assertEquals": "assertEqual",
    "assertNotEquals": "assertNotEqual",
    "assertAlmostEquals": "assertAlmostEqual",
    "assertNotAlmostEquals": "assertNotAlmostEqual",
    "assertRegexpMatches": "assertRegex",
    "assertNotRegexpMatches": "assertNotRegex",
    "assertRaisesRegexp": "assertRaisesRegex",
    "failUnless": "assertTrue",
    "failIf": "assertFalse",
    "failUnlessEqual": "assertEqual",
    "failIfEqual": "assertNotEqual",
    "failUnlessRaises": "assertRaises",
}
# `self.assertEquals(...)`. The lookbehind excludes word characters only - these
# aliases are always reached through `self.`, so excluding a leading dot as well
# would mean the rule never matched anything.
_UNITTEST_ALIAS = re.compile(r"(?<!\w)(" + "|".join(sorted(_UNITTEST_ALIASES)) + r")(?=\s*\()")

# `except ValueError, exc:` - Python 2 syntax that will not even parse on 3.
_PY2_EXCEPT = re.compile(r"^(\s*except\s+[\w.()\[\], ]+?)\s*,\s*([A-Za-z_]\w*)\s*:")

# `new Array()` / `new Object()` with no arguments.
_JS_NEW_WRAPPER = re.compile(r"\bnew\s+(Array|Object)\s*\(\s*\)")

# `typeof x == 'string'`. typeof always yields a string, so against a string
# literal `==` and `===` cannot differ - unlike loose equality in general.
_TYPEOF_LOOSE = re.compile(r"(typeof\s+[^=!\n]+?)(==|!=)(?!=)(\s*(['\"])[a-z]+\4)")

# A backslash escape Python does not recognise. Since 3.12 these raise a
# SyntaxWarning and are scheduled to become a SyntaxError.
_VALID_ESCAPES = set("\n\\'\"abfnrtv01234567xNuU \t")
_STRING_LITERAL = re.compile(r"(?<![\w\"'])([rbuf]{0,2})(\"\"\"|'''|\"|')((?:\\.|(?!\2).)*)\2", re.S)


def _fix_unittest_alias(text: str) -> tuple[str, list[int]]:
    return _substitute(_UNITTEST_ALIAS, lambda m: _UNITTEST_ALIASES[m.group(1)])(text)


def _fix_py2_except(text: str) -> tuple[str, list[int]]:
    return _substitute(_PY2_EXCEPT, r"\1 as \2:")(text)


def _fix_js_new_wrapper(text: str) -> tuple[str, list[int]]:
    return _substitute(_JS_NEW_WRAPPER, lambda m: "[]" if m.group(1) == "Array" else "{}")(text)


def _fix_typeof_equality(text: str) -> tuple[str, list[int]]:
    return _substitute(_TYPEOF_LOOSE, lambda m: f"{m.group(1)}{m.group(2)}={m.group(3)}")(text)


def _invalid_escapes(body: str) -> bool:
    """Whether every backslash in ``body`` starts an escape Python rejects.

    All-or-nothing on purpose: adding an `r` prefix to a literal that also
    contains a real escape such as `\\n` would change the string's value, so
    such a literal is left alone.
    """
    found = False
    index = 0
    while index < len(body) - 1:
        if body[index] == "\\":
            if body[index + 1] in _VALID_ESCAPES:
                return False
            found = True
            index += 2
            continue
        index += 1
    return found


def _code_end(line: str) -> int:
    """Index where a Python `#` comment starts, or the length of the line.

    Quote-aware so a `#` inside a string literal is not mistaken for a comment.
    """
    quote = ""
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#":
            return index
        index += 1
    return len(line)


def _fix_invalid_escape(text: str) -> tuple[str, list[int]]:
    touched: list[int] = []
    out: list[str] = []
    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        changed = False

        def promote(match: re.Match[str]) -> str:
            nonlocal changed
            prefix, quote, body = match.group(1), match.group(2), match.group(3)
            if "r" in prefix.lower() or not _invalid_escapes(body):
                return match.group(0)
            changed = True
            return f"r{prefix}{quote}{body}{quote}"

        # Only the code part is rewritten; a regex quoted inside a comment is
        # prose, and editing prose is not this rule's job.
        end = _code_end(line)
        new_line = _STRING_LITERAL.sub(promote, line[:end]) + line[end:]
        if changed:
            touched.append(number)
        out.append(new_line)
    return "".join(out), touched


_TRAILING_BLANKS = re.compile(r"(\r?\n)(?:[ \t]*\r?\n)+\Z")


def _fix_trailing_blank_lines(text: str) -> tuple[str, list[int]]:
    updated = _TRAILING_BLANKS.sub(r"\1", text)
    if updated == text:
        return text, []
    return updated, [len(updated.splitlines()) + 1]


# --------------------------------------------------------------------------- #
# Deeper detectors
#
# These are the defects worth a developer's attention: latent bugs, injection
# sinks and constructs that quietly change behaviour. Almost none of them can be
# rewritten mechanically without risking a semantic change, so they are reported
# with an explicit repair procedure instead of a diff. A wrong patch is worse
# than a clear explanation.
# --------------------------------------------------------------------------- #
_MUTABLE_DEFAULT = re.compile(r"\bdef\s+\w+\s*\([^)]*=\s*(\[\s*\]|\{\s*\}|set\(\)|list\(\)|dict\(\))")
_EXCEPT_HEAD = re.compile(r"^\s*except\b.*:\s*(#.*)?$")
_EVAL_EXEC = re.compile(r"(?<![\w.])(eval|exec)\s*\(")
_PICKLE_LOAD = re.compile(r"\bpickle\.loads?\s*\(")
_OS_SYSTEM = re.compile(r"\bos\.(system|popen)\s*\(")
_WEAK_HASH = re.compile(r"\bhashlib\.(md5|sha1)\s*\(")
_STAR_IMPORT = re.compile(r"^\s*from\s+[\w.]+\s+import\s+\*")
_UTCNOW = re.compile(r"\bdatetime\.utcnow\s*\(\s*\)")
_LAMBDA_ASSIGN = re.compile(r"^\s*[A-Za-z_]\w*\s*(:[^=]+)?=\s*lambda\b")
_BARE_OPEN = re.compile(r"^\s*[A-Za-z_]\w*\s*=\s*open\s*\(")
_REQUESTS_CALL = re.compile(r"\brequests\.(get|post|put|patch|delete|head|options|request)\s*\(")
_SECRET_ASSIGN = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|private[_-]?key)\b"
    r"\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"
)
_SECRET_PLACEHOLDERS = ("your", "xxx", "changeme", "example", "placeholder", "todo", "dummy", "insert")
_SQL_KEYWORD = re.compile(r"(?i)\b(select\s+.+\s+from|insert\s+into|update\s+\w+\s+set|delete\s+from)\b")
_SQL_INTERPOLATION = re.compile(r"""(?i)(execute\w*\s*\(\s*f[\"'])|([\"']\s*(\+|%)\s*\w)|(\.format\s*\()""")
_CONFLICT_MARKER = re.compile(r"^(<{7}|>{7})[ \t]")
_JS_COMMENT = re.compile(r"//.*$")
_JS_STRING = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`\n]*`")
_LOOSE_EQ = re.compile(r"(?<![=!<>])==(?!=)|(?<!!)!=(?!=)")
_VAR_DECL = re.compile(r"(?<![\w.$])var\s+[A-Za-z_$]")
_DOCUMENT_WRITE = re.compile(r"\bdocument\.write(ln)?\s*\(")
_TIMER_STRING = re.compile(r"\b(setTimeout|setInterval)\s*\(\s*[\"'`]")


def _detect(pattern: re.Pattern[str]) -> Callable[[str], list[int]]:
    return lambda text: _line_numbers(text, pattern)


def _detect_except_pass(text: str) -> list[int]:
    """`except ...:` whose entire body is `pass` (or `...`)."""
    lines = text.splitlines()
    hits: list[int] = []
    for index, line in enumerate(lines):
        if not _EXCEPT_HEAD.match(line):
            continue
        for follow in lines[index + 1 :]:
            stripped = follow.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped in {"pass", "..."}:
                hits.append(index + 1)
            break
    return hits


def _call_arguments(text: str, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    """(line number, argument text) for each call matched by ``pattern``.

    The arguments are collected by counting brackets rather than reading to the
    end of the line, so a call broken across several lines is still judged as a
    whole. Brackets inside string literals are not tracked; the worst case is a
    missed finding, never a wrong rewrite, because these rules only report.
    """
    found: list[tuple[int, str]] = []
    for match in pattern.finditer(text):
        open_paren = text.index("(", match.end() - 1)
        depth = 0
        for index in range(open_paren, len(text)):
            char = text[index]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
                if depth == 0:
                    found.append((text.count("\n", 0, match.start()) + 1, text[open_paren + 1 : index]))
                    break
    return found


def _detect_requests_without_timeout(text: str) -> list[int]:
    return [line for line, args in _call_arguments(text, _REQUESTS_CALL) if "timeout" not in args]


def _detect_hardcoded_secret(text: str) -> list[int]:
    hits: list[int] = []
    for index, line in enumerate(text.splitlines(), start=1):
        match = _SECRET_ASSIGN.search(line)
        if not match:
            continue
        value = match.group(2)
        lowered = value.lower()
        # Templates, placeholders and environment lookups are not secrets.
        if any(token in lowered for token in _SECRET_PLACEHOLDERS):
            continue
        if any(char in value for char in "{}<>$") or len(set(value)) <= 2:
            continue
        hits.append(index)
    return hits


def _detect_sql_interpolation(text: str) -> list[int]:
    return [
        index
        for index, line in enumerate(text.splitlines(), start=1)
        if _SQL_KEYWORD.search(line) and _SQL_INTERPOLATION.search(line)
    ]


def _js_code(line: str) -> str:
    """A line with its comments and string bodies removed."""
    return _JS_STRING.sub("''", _JS_COMMENT.sub("", line))


def _detect_js(pattern: re.Pattern[str]) -> Callable[[str], list[int]]:
    def detect(text: str) -> list[int]:
        return [
            index for index, line in enumerate(text.splitlines(), start=1) if pattern.search(_js_code(line))
        ]

    return detect


RULES: tuple[Rule, ...] = (
    Rule(
        id="bare-except",
        title="Bare `except:` catches control-flow exceptions",
        problem="`except:` with no exception type also catches KeyboardInterrupt and SystemExit.",
        root_cause="A catch-all was used to silence an unknown error instead of naming the expected one.",
        impact="The process becomes impossible to interrupt and real failures are hidden.",
        severity="high",
        effort="S",
        confidence=0.95,
        languages=PY,
        transform=_substitute(_BARE_EXCEPT, "except Exception:"),
    ),
    Rule(
        id="none-identity",
        title="`== None` instead of `is None`",
        problem="Equality with None invokes `__eq__`, which a class can override.",
        root_cause="Habit carried over from languages where `==` is identity.",
        impact="Comparisons can return the wrong answer for objects with a custom `__eq__`.",
        severity="low",
        effort="S",
        confidence=0.97,
        languages=PY,
        transform=_fix_none_identity,
    ),
    Rule(
        id="yaml-unsafe-load",
        title="`yaml.load` without a safe loader",
        problem="`yaml.load` can instantiate arbitrary Python objects from the document.",
        root_cause="The default loader is unsafe and the call site never passed `Loader=`.",
        impact="A malicious YAML file becomes remote code execution.",
        severity="critical",
        effort="S",
        confidence=0.9,
        languages=PY,
        transform=_substitute(_YAML_LOAD, "yaml.safe_load("),
    ),
    Rule(
        id="debugger-statement",
        title="`debugger` statement left in shipped code",
        problem="A `debugger` statement halts execution whenever devtools are open.",
        root_cause="Debugging aid that was never removed.",
        impact="The application appears to freeze for anyone with devtools open.",
        severity="medium",
        effort="S",
        confidence=0.99,
        languages=JS,
        transform=_fix_debugger,
    ),
    Rule(
        id="trailing-whitespace",
        title="Trailing whitespace",
        problem="Lines end with spaces or tabs.",
        root_cause="Editors that do not trim on save.",
        impact="Every future edit to these lines produces noisy, unreviewable diffs.",
        severity="low",
        effort="S",
        confidence=1.0,
        languages=ANY,
        transform=_substitute(_TRAILING_WS, ""),
        cosmetic=True,
    ),
    Rule(
        id="missing-final-newline",
        title="File does not end with a newline",
        problem="The last line has no terminating newline.",
        root_cause="POSIX text-file convention not enforced by the editor.",
        impact="Diffs show the final line as changed whenever anything is appended.",
        severity="low",
        effort="S",
        confidence=1.0,
        languages=ANY,
        transform=_fix_final_newline,
        cosmetic=True,
    ),
    # -- mechanical repairs for constructs the language already removed ----- #
    Rule(
        id="py2-except-syntax",
        title="Python 2 `except X, e:` syntax",
        problem="The handler separates the exception type and the name with a comma.",
        root_cause="Python 2 code that was never converted.",
        impact="The file does not parse on Python 3 at all, so nothing that imports it can run.",
        severity="critical",
        effort="S",
        confidence=1.0,
        languages=PY,
        transform=_fix_py2_except,
    ),
    Rule(
        id="deprecated-unittest-alias",
        title="Deprecated `unittest` method alias",
        problem="Names such as `assertEquals` and `failUnless` are old aliases of the "
        "current assertion methods.",
        root_cause="They were the documented spelling for years and still work on older "
        "interpreters, so they survive in long-lived test suites.",
        impact="Every one of these aliases was removed in Python 3.12. The tests stop "
        "collecting with an AttributeError the moment the interpreter is upgraded.",
        severity="high",
        effort="S",
        confidence=1.0,
        languages=PY,
        transform=_fix_unittest_alias,
    ),
    Rule(
        id="invalid-escape-sequence",
        title="Unknown escape sequence in a plain string",
        problem="A string contains a backslash sequence Python does not recognise, such as "
        "`\\d` in a regular expression written without an `r` prefix.",
        root_cause="Regular expressions and Windows paths are written as ordinary strings, "
        "where the backslash is already meaningful.",
        impact="Python 3.12 raises a SyntaxWarning for these and they are scheduled to become "
        "a SyntaxError. The value is also not what it looks like, so the pattern can quietly "
        "fail to match.",
        severity="medium",
        effort="S",
        confidence=0.9,
        languages=PY,
        # Only promoted when every escape in the literal is invalid, so the
        # rewrite cannot change the string's value.
        transform=_fix_invalid_escape,
    ),
    Rule(
        id="typeof-loose-equality",
        title="`typeof` compared with `==`",
        problem="A `typeof` result is compared to a string literal using loose equality.",
        root_cause="Habit. Loose equality is shorter and appears to work here.",
        impact="It does work here - `typeof` always returns a string - but it trains the "
        "pattern and hides the genuinely unsafe `==` comparisons elsewhere in the file "
        "from any reviewer scanning for them.",
        severity="low",
        effort="S",
        confidence=1.0,
        languages=JS,
        # Provably equivalent: both operands are already strings, so coercion
        # cannot occur. This is the one slice of loose equality safe to rewrite.
        transform=_fix_typeof_equality,
    ),
    Rule(
        id="js-wrapper-constructor",
        title="`new Array()` or `new Object()`",
        problem="An empty array or object is built with a constructor call.",
        root_cause="Java or C# habit carried into JavaScript.",
        impact="`Array` and `Object` are ordinary globals that any earlier script can "
        "reassign, and `new Array(3)` means something entirely different from `[3]`, so the "
        "construct is a trap the next time an argument is added.",
        severity="low",
        effort="S",
        confidence=0.95,
        languages=JS,
        transform=_fix_js_new_wrapper,
    ),
    Rule(
        id="trailing-blank-lines",
        title="Blank lines at end of file",
        problem="The file ends with one or more empty lines.",
        root_cause="Accumulated by editors and merges.",
        impact="Every append to the file produces a diff that also touches the blank lines.",
        severity="low",
        effort="S",
        confidence=1.0,
        languages=ANY,
        transform=_fix_trailing_blank_lines,
        cosmetic=True,
    ),
    # -- reported but never auto-fixed ------------------------------------- #
    Rule(
        id="subprocess-shell",
        title="`shell=True` passed to subprocess",
        problem="The command string is handed to a shell, so any interpolated value becomes code.",
        root_cause="A shell feature (globbing, pipes) was needed, or the call was copied from a snippet.",
        impact="Command injection wherever the command contains untrusted input.",
        severity="critical",
        effort="M",
        confidence=0.8,
        languages=PY,
        detect=lambda text: _line_numbers(text, _SHELL_TRUE),
        steps=(
            "Pass the command as a list of arguments and drop `shell=True`.",
            "Where a shell feature was needed - a pipe, a glob - do it in Python instead: "
            "`glob.glob` for expansion, or chain two `subprocess` calls for a pipe.",
            "If the shell is genuinely required, quote every interpolated value with "
            "`shlex.quote`.",
        ),
    ),
    Rule(
        id="innerhtml-sink",
        title="Assignment to `innerHTML`",
        problem="`innerHTML` parses its input as markup, so any interpolated value can inject script.",
        root_cause="String concatenation used to build DOM instead of element creation.",
        impact="Cross-site scripting if any part of the string is user or repository controlled.",
        severity="high",
        effort="M",
        confidence=0.6,
        languages=JS,
        detect=lambda text: _line_numbers(text, _HTML_SINK),
        steps=(
            "Replace the assignment with `textContent` if the value is plain text.",
            "If markup is genuinely required, build the nodes with `createElement` and "
            "`append`, so the browser never parses attacker-controlled text as HTML.",
            "If the markup comes from a template, sanitise it at the boundary instead of "
            "at every sink.",
        ),
    ),
    # -- latent bugs -------------------------------------------------------- #
    Rule(
        id="mutable-default-arg",
        title="Mutable default argument",
        problem="A list, dict or set literal in a parameter default is created once, when the "
        "function is defined, and then shared by every call that omits the argument.",
        root_cause="The default is read as 'a fresh empty container', but Python evaluates it "
        "at definition time.",
        impact="State leaks between unrelated calls: the second caller sees the first caller's "
        "data. The resulting bugs are intermittent and very hard to trace.",
        severity="high",
        effort="S",
        confidence=0.9,
        languages=PY,
        detect=_detect(_MUTABLE_DEFAULT),
        steps=(
            "Change the default to `None`.",
            "Inside the function, replace it with a fresh container: `if items is None: items = []`.",
            "Check the callers - any that relied on the accumulating default are already buggy "
            "and need an explicit argument.",
        ),
    ),
    Rule(
        id="except-pass",
        title="Exception caught and silently discarded",
        problem="The entire body of the `except` block is `pass`, so the failure leaves no trace.",
        root_cause="An error was noisy in development and was silenced instead of handled.",
        impact="Faults become invisible. The system reports success while doing nothing, and the "
        "eventual bug report points at a symptom far from the cause.",
        severity="high",
        effort="S",
        confidence=0.9,
        languages=PY,
        detect=_detect_except_pass,
        steps=(
            "Narrow the `except` to the exception you actually expect.",
            "Log it with `log.warning(...)` or `log.exception(...)` including enough context to "
            "identify the input.",
            "If ignoring really is correct, say so in a comment - a reader cannot tell a "
            "deliberate no-op from an oversight.",
        ),
    ),
    Rule(
        id="star-import",
        title="Wildcard import",
        problem="`from module import *` binds every public name, including ones added later.",
        root_cause="Convenience during prototyping that was never tightened up.",
        impact="Names collide silently, static analysis cannot resolve references, and an "
        "upstream release can shadow a local symbol without any code change here.",
        severity="medium",
        effort="S",
        confidence=0.95,
        languages=PY,
        detect=_detect(_STAR_IMPORT),
        steps=(
            "List the names the module actually uses and import them explicitly.",
            "If the list is long, import the module itself and qualify the uses.",
        ),
    ),
    Rule(
        id="lambda-assignment",
        title="Lambda bound to a name",
        problem="A lambda is assigned to a name where a `def` was meant.",
        root_cause="Written as a one-liner and then grew.",
        impact="Tracebacks show `<lambda>` instead of the function name, the function cannot "
        "carry a docstring, and type checkers infer less about it.",
        severity="low",
        effort="S",
        confidence=0.9,
        languages=PY,
        detect=_detect(_LAMBDA_ASSIGN),
        steps=("Rewrite as a `def` with the same name, parameters and body.",),
    ),
    Rule(
        id="unclosed-file",
        title="File opened without a context manager",
        problem="`open(...)` is assigned to a variable rather than used in a `with` block.",
        root_cause="The `close()` call is expected to happen later in the function.",
        impact="If anything raises in between, the handle stays open. On Windows the file then "
        "cannot be deleted or replaced until the process exits.",
        severity="medium",
        effort="S",
        confidence=0.75,
        languages=PY,
        detect=_detect(_BARE_OPEN),
        steps=(
            "Wrap the use in `with open(...) as handle:` and indent the body.",
            "Delete the matching `close()` call - the context manager does it, including on the "
            "exception path.",
        ),
    ),
    Rule(
        id="naive-utcnow",
        title="`datetime.utcnow()` returns a naive timestamp",
        problem="`utcnow()` produces a datetime with no timezone attached, even though the value "
        "is UTC.",
        root_cause="It reads like the obvious way to get the current UTC time.",
        impact="The value compares incorrectly against aware datetimes and is silently "
        "reinterpreted as local time by most serialisers. It is also deprecated from Python 3.12.",
        severity="medium",
        effort="S",
        confidence=0.95,
        languages=PY,
        detect=_detect(_UTCNOW),
        steps=(
            "Use `datetime.now(datetime.timezone.utc)`.",
            "Add the `timezone` import if it is missing.",
            "Check anything that compares or stores the result - mixing naive and aware "
            "datetimes raises `TypeError`.",
        ),
    ),
    Rule(
        id="request-without-timeout",
        title="HTTP request with no timeout",
        problem="A `requests` call is made without a `timeout` argument.",
        root_cause="`requests` has no default timeout, which is easy to miss.",
        impact="A silent peer holds the connection open forever. The calling thread, worker or "
        "request handler is blocked with no recovery path.",
        severity="medium",
        effort="S",
        confidence=0.85,
        languages=PY,
        detect=_detect_requests_without_timeout,
        steps=(
            "Add an explicit `timeout=` to the call.",
            "Use a separate connect and read timeout - `timeout=(3.05, 30)` - when the two "
            "budgets differ.",
            "Handle `requests.Timeout` at the call site so the caller sees a clear failure.",
        ),
    ),
    Rule(
        id="loose-equality",
        title="Loose equality comparison",
        problem="`==` and `!=` coerce their operands before comparing.",
        root_cause="Habit; the strict operators are three characters longer.",
        impact="`0 == '', null == undefined` and `'1' == 1` are all true, so guards pass for "
        "values they were written to reject.",
        severity="medium",
        effort="S",
        confidence=0.7,
        languages=JS,
        detect=_detect_js(_LOOSE_EQ),
        steps=(
            "Replace with `===` and `!==`.",
            "Where the coercion was deliberate - usually a `== null` null-or-undefined check - "
            "make it explicit and leave a comment.",
        ),
    ),
    Rule(
        id="var-declaration",
        title="`var` declaration",
        problem="`var` is function-scoped and hoisted, unlike `let` and `const`.",
        root_cause="Pre-ES6 code, or a snippet copied from an old answer.",
        impact="The binding leaks out of the block it was written in, and a `var` inside a loop "
        "is shared by every closure created in that loop.",
        severity="low",
        effort="S",
        confidence=0.9,
        languages=JS,
        detect=_detect_js(_VAR_DECL),
        steps=(
            "Use `const` if the binding is never reassigned, `let` otherwise.",
            "Re-test any closure created inside a loop - with `let` each iteration gets its own "
            "binding, which is usually the intended behaviour but is a real change.",
        ),
    ),
    # -- security ----------------------------------------------------------- #
    Rule(
        id="dynamic-eval",
        title="Dynamic code execution",
        problem="`eval` or `exec` executes a string as code.",
        root_cause="Used to reach a name or build behaviour that is not known until runtime.",
        impact="Any value that reaches the string becomes executable code with the full "
        "privileges of the process.",
        severity="critical",
        effort="M",
        confidence=0.8,
        languages=PY | JS,
        detect=_detect(_EVAL_EXEC),
        steps=(
            "Replace name lookup with a dictionary that maps the allowed keys to the allowed "
            "callables.",
            "Use `json.loads` (or `JSON.parse`) if the string is data rather than code.",
            "If the code really is dynamic, it must not be reachable from any external input.",
        ),
    ),
    Rule(
        id="unsafe-deserialisation",
        title="`pickle` used to load data",
        problem="`pickle.load`/`loads` reconstructs arbitrary Python objects from the stream.",
        root_cause="Pickle is the quickest way to round-trip a Python object.",
        impact="Loading an untrusted pickle executes whatever the author put in it. This is "
        "remote code execution, not a parsing bug.",
        severity="critical",
        effort="M",
        confidence=0.85,
        languages=PY,
        detect=_detect(_PICKLE_LOAD),
        steps=(
            "Use JSON, MessagePack or an explicit schema for anything that crosses a trust "
            "boundary.",
            "If pickle is unavoidable, sign the payload and verify the signature before "
            "unpickling.",
        ),
    ),
    Rule(
        id="os-command",
        title="Command executed through the shell",
        problem="`os.system` and `os.popen` pass their argument to a shell.",
        root_cause="The shortest way to run an external command.",
        impact="Every shell metacharacter in the string is interpreted, so any interpolated "
        "value becomes command injection. Neither call reports failures usefully.",
        severity="critical",
        effort="M",
        confidence=0.9,
        languages=PY,
        detect=_detect(_OS_SYSTEM),
        steps=(
            "Use `subprocess.run([...], check=True)` with the command as a list of arguments.",
            "Leave `shell=False` (the default) so no shell is involved at all.",
            "Validate any path or identifier that comes from outside before it reaches the "
            "argument list.",
        ),
    ),
    Rule(
        id="weak-hash",
        title="MD5 or SHA-1 used for hashing",
        problem="`hashlib.md5` and `hashlib.sha1` are both broken against collision attacks.",
        root_cause="They are the first names in the module and are fast.",
        impact="Two different inputs can be made to hash identically, which defeats any "
        "signature, integrity check or deduplication built on them.",
        severity="high",
        effort="S",
        confidence=0.7,
        languages=PY,
        detect=_detect(_WEAK_HASH),
        steps=(
            "Use `hashlib.sha256` for integrity and content addressing.",
            "Use `hashlib.scrypt` or `bcrypt` for passwords - a plain hash of any strength is "
            "the wrong tool there.",
            "If the hash is only a non-security cache key, pass `usedforsecurity=False` to make "
            "that explicit.",
        ),
    ),
    Rule(
        id="sql-string-building",
        title="SQL assembled by string interpolation",
        problem="A SQL statement is built with an f-string, `%` or `.format()`.",
        root_cause="Parameter binding feels heavier than interpolation for a quick query.",
        impact="Any interpolated value can close the literal and append its own SQL. This is the "
        "single most exploited web vulnerability.",
        severity="critical",
        effort="M",
        confidence=0.7,
        languages=PY,
        detect=_detect_sql_interpolation,
        steps=(
            "Move every value into bound parameters: `cursor.execute(sql, (value,))`.",
            "Identifiers cannot be bound - validate table and column names against a fixed "
            "allow-list instead.",
            "Where an ORM is already in use, express the query through it rather than raw SQL.",
        ),
    ),
    Rule(
        id="hardcoded-secret",
        title="Credential embedded in source",
        problem="A password, API key or token is assigned a literal string value.",
        root_cause="A working value was pasted in to get something running and never moved out.",
        impact="The secret is in the version-control history forever. Rotating it later does not "
        "remove it from any existing clone.",
        severity="critical",
        effort="M",
        confidence=0.6,
        languages=ANY,
        detect=_detect_hardcoded_secret,
        steps=(
            "Treat the value as compromised and rotate it now.",
            "Read it from the environment or a secret store at runtime.",
            "Remove it from the git history, not just from the working tree.",
        ),
    ),
    Rule(
        id="document-write",
        title="`document.write` used to build the page",
        problem="`document.write` after load replaces the whole document.",
        root_cause="A pattern from before DOM APIs were widely available.",
        impact="It blocks the parser, wipes the current page when called late, and parses its "
        "argument as markup, so it is also an injection sink.",
        severity="high",
        effort="M",
        confidence=0.85,
        languages=JS,
        detect=_detect_js(_DOCUMENT_WRITE),
        steps=(
            "Create the nodes and insert them with `append` or `replaceChildren`.",
            "For text, set `textContent` so nothing is parsed as markup.",
        ),
    ),
    Rule(
        id="timer-string-body",
        title="Timer callback given as a string",
        problem="`setTimeout`/`setInterval` was passed a string instead of a function.",
        root_cause="An old idiom that still works.",
        impact="The string is evaluated as code in the global scope - identical in risk to "
        "`eval`, and it cannot see any local variable it appears to reference.",
        severity="high",
        effort="S",
        confidence=0.9,
        languages=JS,
        detect=_detect_js(_TIMER_STRING),
        steps=("Pass a function: `setTimeout(() => doWork(), 100)`.",),
    ),
    Rule(
        id="merge-conflict-marker",
        title="Unresolved merge-conflict marker",
        problem="The file still contains `<<<<<<<` or `>>>>>>>` markers from a merge.",
        root_cause="A conflict was committed before it was resolved.",
        impact="The file is not valid source. Anything that imports it fails at parse time.",
        severity="critical",
        effort="S",
        confidence=1.0,
        languages=ANY,
        detect=_detect(_CONFLICT_MARKER),
        steps=(
            "Open the file and choose the correct side of each conflict.",
            "Delete all three marker lines.",
            "Re-run the tests - a conflict resolved by eye is rarely right the first time.",
        ),
    ),
)

RULES_BY_ID = {rule.id: rule for rule in RULES}


# --------------------------------------------------------------------------- #
# Proposal generation
# --------------------------------------------------------------------------- #
@dataclass
class _Candidate:
    rule: Rule
    relative: str
    original: str
    updated: str
    lines: list[int] = field(default_factory=list)


def digest(text: str) -> str:
    """Content digest a proposal is pinned to."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_source(path: Path) -> str | None:
    """Read a file verbatim, or None if it cannot be round-tripped safely.

    ``newline=""`` disables universal-newline translation so a CRLF file is not
    silently rewritten to LF when a fix is applied, and strict decoding means we
    never propose an edit to a file we would have to lossily re-encode.
    """
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def _applies(rule: Rule, language: str) -> bool:
    return not rule.languages or language in rule.languages


def _unified(relative: str, before: str, after: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{relative}",
        tofile=f"b/{relative}",
        n=3,
    )
    return "".join(diff)


def _proposal(candidate: _Candidate, original_digest: str) -> dict[str, Any]:
    rule = candidate.rule
    return {
        "id": f"{rule.id}::{candidate.relative}",
        "rule": rule.id,
        "kind": "code",
        "title": rule.title,
        "problem": rule.problem,
        "root_cause": rule.root_cause,
        "impact": rule.impact,
        "severity": rule.severity,
        "effort": rule.effort,
        "confidence": rule.confidence,
        "cosmetic": rule.cosmetic,
        "file": candidate.relative,
        "files": [candidate.relative],
        "lines": candidate.lines[:50],
        "occurrences": len(candidate.lines),
        "auto_fixable": rule.auto_fixable,
        # A rule that cannot be applied mechanically still has to leave the
        # reader with something to do, so it carries the repair procedure.
        "steps": list(rule.steps),
        "diff": _unified(candidate.relative, candidate.original, candidate.updated) if rule.auto_fixable else "",
        "digest": original_digest,
        "source": "static",
    }


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_SEVERITY_ORDER = SEVERITY_ORDER  # retained for existing callers


# --------------------------------------------------------------------------- #
# Structural proposals
#
# Text rules can only see one file at a time, so the most valuable work in a
# codebase - breaking a dependency cycle, splitting an oversized type, covering
# an untested module - is invisible to them. These proposals come from the
# analysis that has already run: they carry no diff, because no diff would be
# honest, but they name the files, quantify the problem and give an ordered
# procedure. They are ranked above the mechanical rules for exactly that reason.
# --------------------------------------------------------------------------- #
def _structural(
    rule: str,
    index: int,
    *,
    title: str,
    problem: str,
    root_cause: str,
    impact: str,
    severity: str,
    effort: str,
    steps: list[str],
    files: list[str],
    occurrences: int = 1,
    confidence: float = 0.85,
) -> dict[str, Any]:
    clean = [f for f in dict.fromkeys(files) if f][:12]
    return {
        "id": f"structure:{rule}:{index}",
        "rule": rule,
        "kind": "structural",
        "title": title,
        "problem": problem,
        "root_cause": root_cause,
        "impact": impact,
        "severity": severity,
        "effort": effort,
        "confidence": confidence,
        "cosmetic": False,
        "file": clean[0] if clean else "",
        "files": clean,
        "lines": [],
        "occurrences": occurrences,
        "auto_fixable": False,
        "steps": steps,
        "diff": "",
        "digest": "",
        "source": "static",
    }


def structural_proposals(metrics: dict[str, Any] | None, *, limit: int = 25) -> list[dict[str, Any]]:
    """Cross-file improvements derived from the analysis metrics."""
    metrics = metrics or {}
    signals = metrics.get("signals") or {}
    out: list[dict[str, Any]] = []

    for index, cycle in enumerate((metrics.get("cycles") or [])[:5]):
        modules = [str(m) for m in (cycle.get("modules") or [])]
        if not modules:
            continue
        loop = " → ".join(modules + modules[:1])
        out.append(
            _structural(
                "dependency-cycle",
                index,
                title=f"Break the dependency cycle through {modules[0]}",
                problem=f"{len(modules)} modules import each other in a loop: {loop}.",
                root_cause="A module needed one name from a module that already depended on it, "
                "and the import was added in place rather than the shared piece being moved out.",
                impact="None of these modules can be built, tested, released or reused on its "
                "own, and a change to any one of them can propagate back to itself.",
                severity="high",
                effort="L",
                confidence=0.95,
                files=modules,
                occurrences=len(modules),
                steps=[
                    f"Pick the weakest edge in the loop - usually the one importing a single "
                    f"name. Start with {modules[-1]} → {modules[0]}.",
                    "Decide which of the two names is the more stable abstraction; that one "
                    "belongs in the lower module.",
                    "Either move the shared type into a third module both may depend on, or "
                    "declare the interface in the lower module and implement it in the higher one.",
                    "Re-run the analysis: the cycle count is the check that the edge is gone.",
                ],
            )
        )

    for index, god in enumerate((metrics.get("god_classes") or [])[:5]):
        methods = god.get("methods", 0)
        out.append(
            _structural(
                "god-class",
                index,
                title=f"Split {god.get('name', 'the type')} into focused units",
                problem=f"{god.get('name', 'This type')} has {methods} methods, "
                f"{god.get('properties', 0)} fields and {god.get('dependencies', 0)} outgoing "
                "dependencies.",
                root_cause="Each new requirement was added as another method on the type that "
                "already had the data, so unrelated responsibilities accumulated in one place.",
                impact="The type cannot be tested in isolation, it is a constant source of merge "
                "conflicts, and every consumer depends on far more behaviour than it uses.",
                severity="high" if methods >= 30 else "medium",
                effort="L",
                confidence=0.8,
                files=[god.get("file", "")],
                steps=[
                    "Group the methods by which fields they read and write. Groups that share no "
                    "state are separate responsibilities.",
                    "Extract the largest group into its own class, moving the fields it owns "
                    "along with it.",
                    "Keep the original type as a thin facade that delegates, so no caller breaks "
                    "in the same commit.",
                    "Move the callers over one at a time, then delete the delegating methods.",
                ],
            )
        )

    violations = metrics.get("layering_violations") or []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for violation in violations:
        grouped.setdefault((violation.get("from_layer", ""), violation.get("to_layer", "")), []).append(violation)
    for index, ((from_layer, to_layer), items) in enumerate(list(grouped.items())[:4]):
        out.append(
            _structural(
                "layering-violation",
                index,
                title=f"Invert {len(items)} dependencies from {from_layer} into {to_layer}",
                problem=f"{len(items)} references point from the {from_layer} layer up into the "
                f"{to_layer} layer, against the intended direction.",
                root_cause="The lower layer needed a concrete type that happened to live higher "
                "up, and the import was taken directly instead of an interface being introduced.",
                impact=f"The {from_layer} layer can no longer be built, tested or deployed "
                f"without {to_layer}, which is the one guarantee layering exists to provide.",
                severity="high",
                effort="M",
                confidence=0.85,
                files=[item.get("file", "") for item in items],
                occurrences=len(items),
                steps=[
                    f"Define the interface the {from_layer} layer needs, inside the {from_layer} "
                    "layer.",
                    f"Implement it in {to_layer} and inject the implementation at composition time.",
                    "Replace the direct references with the interface; the call still happens at "
                    "runtime, but the compile-time dependency now points the right way.",
                ],
            )
        )

    offenders = ((signals.get("complexity") or {}).get("offenders") or [])[:6]
    for index, offender in enumerate(offenders):
        score = offender.get("complexity", 0)
        out.append(
            _structural(
                "complex-function",
                index,
                title=f"Reduce the branching in {offender.get('name', 'this function')}",
                problem=f"Cyclomatic complexity is {score}, meaning at least {score} independent "
                "paths through the function.",
                root_cause="Conditions were added one at a time; each was small, and no single "
                "change looked large enough to justify restructuring.",
                impact=f"Covering it properly needs {score} test cases, so in practice it is "
                "under-tested and every edit risks a path nobody exercises.",
                severity="high" if score >= 21 else "medium",
                effort="M",
                confidence=0.9,
                files=[offender.get("file", "")],
                steps=[
                    "Return early for the guard conditions at the top; that alone usually removes "
                    "a level of nesting.",
                    "Extract each self-contained block into a named function - the name documents "
                    "the branch that led to it.",
                    "Replace long if/elif chains that switch on a value with a lookup table.",
                    "Write the tests against the extracted functions, where the paths are few "
                    "enough to enumerate.",
                ],
            )
        )

    untested = signals.get("untested_modules") or []
    if untested:
        out.append(
            _structural(
                "untested-module",
                0,
                title=f"Add a first test for {len(untested)} untested modules",
                problem=f"{len(untested)} modules have no test file referencing them: "
                + ", ".join(str(m) for m in untested[:6])
                + ("…" if len(untested) > 6 else ""),
                root_cause="Tests were written for the parts that were hard to get right, and "
                "the rest was verified by hand and never revisited.",
                impact="Any change to these modules is unverified. They are also the modules a "
                "refactor cannot safely touch, so they tend to calcify.",
                severity="high",
                effort="M",
                confidence=0.75,
                files=[str(m) for m in untested],
                occurrences=len(untested),
                steps=[
                    "Start with the module with the most callers - it has the highest return per "
                    "test.",
                    "Write one test for the main path first; it is the regression net that makes "
                    "everything after it safe.",
                    "Add a test for each error branch the module handles explicitly.",
                    "Only then chase coverage percentages.",
                ],
            )
        )

    largest = [f for f in (signals.get("largest_files") or []) if (f.get("loc") or 0) >= 600][:4]
    for index, item in enumerate(largest):
        out.append(
            _structural(
                "oversized-file",
                index,
                title=f"Split {item.get('file', 'this file')} ({item.get('loc', 0)} lines)",
                problem=f"The file is {item.get('loc', 0)} lines long.",
                root_cause="Related code was appended over time because the file was already the "
                "obvious place for it.",
                impact="It cannot be reviewed in one sitting, it serialises anyone working in "
                "the area behind merge conflicts, and its true responsibilities are hidden.",
                severity="medium",
                effort="M",
                confidence=0.7,
                files=[item.get("file", "")],
                steps=[
                    "Read the top-level definitions and write down the two or three subjects the "
                    "file covers.",
                    "Move the smallest subject out first, keeping the public names re-exported "
                    "from the original module.",
                    "Repeat until every file has one subject, then remove the re-exports.",
                ],
            )
        )

    undocumented = (signals.get("symbol_docs") or {}).get("undocumented") or []
    if len(undocumented) >= 5:
        out.append(
            _structural(
                "undocumented-api",
                0,
                title=f"Document {len(undocumented)} public symbols",
                problem=f"{len(undocumented)} public classes and functions have no docstring.",
                root_cause="The names were self-explanatory to whoever wrote them.",
                impact="The contract - what is accepted, what is returned, what is raised - "
                "exists only in the implementation, so every caller has to read it.",
                severity="low",
                effort="S",
                confidence=0.9,
                files=[str(sym.get("file", "")) for sym in undocumented],
                occurrences=len(undocumented),
                steps=[
                    "Document the entry points first - the symbols other modules import.",
                    "State what the function does and what it raises. Repeating the signature "
                    "adds nothing.",
                    "Where a docstring is hard to write, that is usually the sign the function "
                    "does two things.",
                ],
            )
        )

    out.sort(key=lambda p: (SEVERITY_ORDER.get(p["severity"], 9), p["rule"]))
    return out[:limit]


def propose(
    root: Path,
    *,
    limit: int = DEFAULT_LIMIT,
    rules: list[str] | None = None,
    include_cosmetic: bool = False,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan ``root`` and return every fix proposal, most severe first.

    Formatting-only rules are excluded unless ``include_cosmetic`` is set: a list
    headed by two hundred trailing-whitespace hits buries the injection sink
    further down it. When ``metrics`` from a completed analysis is supplied, the
    cross-file structural work is merged in and ranked above the text rules.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        return {"available": False, "reason": "path is not a directory", "proposals": []}

    requested = set(rules or [])
    selected = [
        r
        for r in RULES
        if (not requested or r.id in requested) and (include_cosmetic or not r.cosmetic or r.id in requested)
    ]
    proposals: list[dict[str, Any]] = []
    scanned = 0

    for source in walk(root):
        if source.size > MAX_FILE_BYTES:
            continue
        applicable = [r for r in selected if _applies(r, source.language)]
        if not applicable:
            continue
        text = read_source(source.path)
        if not text:
            continue
        scanned += 1
        original_digest = digest(text)

        for rule in applicable:
            if rule.transform is not None:
                updated, lines = rule.transform(text)
                if updated == text:
                    continue
                proposals.append(
                    _proposal(_Candidate(rule, source.relative_path, text, updated, lines), original_digest)
                )
            elif rule.detect is not None:
                lines = rule.detect(text)
                if not lines:
                    continue
                proposals.append(
                    _proposal(_Candidate(rule, source.relative_path, text, text, lines), original_digest)
                )
            if len(proposals) >= limit:
                break
        if len(proposals) >= limit:
            break

    proposals.sort(key=lambda p: (SEVERITY_ORDER.get(p["severity"], 9), p["file"], p["rule"]))
    structural = structural_proposals(metrics) if not requested else []
    # Structural work leads: it is the expensive, high-return end of the list,
    # and it is exactly what a per-line scanner can never surface.
    merged = structural + proposals
    return {
        "available": True,
        "mode": "static",
        "proposals": merged[:limit],
        "count": len(merged[:limit]),
        "structural_count": len(structural),
        "truncated": len(merged) > limit,
        "files_scanned": scanned,
        "cosmetic_included": include_cosmetic,
        "rules": [
            {
                "id": r.id,
                "title": r.title,
                "severity": r.severity,
                "auto_fixable": r.auto_fixable,
                "cosmetic": r.cosmetic,
            }
            for r in RULES
        ],
        "source": "static",
    }


# --------------------------------------------------------------------------- #
# Applying (explicit, never automatic)
# --------------------------------------------------------------------------- #
def _resolve_inside(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root``, refusing anything that escapes it."""
    if not relative or Path(relative).is_absolute():
        raise FixError("invalid file path")
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise FixError("file path escapes the project root") from exc
    return target


def apply(root: Path, selections: list[dict[str, Any]], *, confirm: bool = False) -> dict[str, Any]:
    """Apply the given proposals.

    ``confirm`` must be True. Each selection needs ``rule``, ``file`` and the
    ``digest`` the proposal was computed against; a mismatch aborts that file
    so a stale review pane can never clobber newer edits.
    """
    if not confirm:
        raise FixError("fixes must be confirmed explicitly before they are applied")

    root = Path(root).resolve()
    results: list[dict[str, Any]] = []
    changed = 0

    # Group by file so several rules on one file produce a single write.
    by_file: dict[str, list[dict[str, Any]]] = {}
    for selection in selections or []:
        by_file.setdefault(str(selection.get("file") or ""), []).append(selection)

    for relative, group in sorted(by_file.items()):
        try:
            target = _resolve_inside(root, relative)
            if not target.is_file():
                raise FixError("file no longer exists")
            text = read_source(target)
            if text is None:
                raise FixError("file is not valid UTF-8 text")
            current = digest(text)
            expected = {str(s.get("digest") or "") for s in group}
            if expected and current not in expected:
                raise FixError("file changed since the fix was proposed")

            applied: list[str] = []
            updated = text
            ai_ids = [str(s.get("ai_fix_id") or "") for s in group if s.get("ai_fix_id")]
            if ai_ids:
                # A model patch is a whole-file replacement, so it cannot be
                # composed with the line-wise rules. Refusing is better than
                # silently applying one and dropping the other.
                if len(ai_ids) > 1 or len(group) > 1:
                    raise FixError("apply the AI fix for this file on its own")
                patch = _AI_PATCHES.get(ai_ids[0])
                if patch is None:
                    raise FixError("this AI fix has expired - regenerate the proposals")
                if patch["file"] != relative or patch["digest"] != current:
                    raise FixError("file changed since the fix was proposed")
                updated = patch["text"]
                applied.append(ai_ids[0])
            for selection in group:
                if selection.get("ai_fix_id"):
                    continue
                rule = RULES_BY_ID.get(str(selection.get("rule") or ""))
                if rule is None or rule.transform is None:
                    raise FixError(f"rule {selection.get('rule')!r} cannot be applied automatically")
                candidate, _lines = rule.transform(updated)
                if candidate != updated:
                    updated = candidate
                    applied.append(rule.id)

            if updated != text:
                with target.open("w", encoding="utf-8", newline="") as handle:
                    handle.write(updated)
                changed += 1
            results.append({"file": relative, "ok": True, "applied": applied, "digest": digest(updated)})
        except (FixError, OSError, UnicodeDecodeError) as exc:
            log.warning("cannot apply fixes to %s: %s", relative, exc)
            results.append({"file": relative, "ok": False, "error": str(exc), "applied": []})

    return {
        "applied_files": changed,
        "results": results,
        "failed": [r for r in results if not r["ok"]],
    }


def preview(root: Path, relative: str, rule_ids: list[str]) -> dict[str, Any]:
    """Recompute a diff for one file against its current contents."""
    root = Path(root).resolve()
    target = _resolve_inside(root, relative)
    if not target.is_file():
        raise FixError("file no longer exists")
    text = read_source(target)
    if text is None:
        raise FixError("file is not valid UTF-8 text")
    updated = text
    for rule_id in rule_ids:
        rule = RULES_BY_ID.get(rule_id)
        if rule is None or rule.transform is None:
            continue
        updated, _lines = rule.transform(updated)
    return {
        "file": relative,
        "diff": _unified(relative, text, updated),
        "digest": digest(text),
        "changed": updated != text,
    }


# --------------------------------------------------------------------------- #
# AI-assisted mode
#
# The deterministic engine above is the floor, not the ceiling: it can only fix
# what a regular expression can describe. When a provider is configured, the
# findings that no rule can repair are sent for a real patch.
#
# Three properties are preserved no matter what the model returns:
#   * the model is asked for source text, never for a diff - the diff is
#     computed here with difflib, so it always describes the actual change;
#   * the model only sees a bounded excerpt, so it cannot rewrite parts of the
#     file it was never shown;
#   * nothing is written. The patch is held server-side against the digest of
#     the file it was computed from, and applying it is still an explicit,
#     confirmed call that re-verifies that digest.
# --------------------------------------------------------------------------- #
AI_WINDOW = 24
AI_MAX_CANDIDATES = 6
_AI_CACHE_LIMIT = 200
_AI_PATCHES: dict[str, dict[str, Any]] = {}
_FENCE = re.compile(r"^\s*```[\w-]*\n|\n?```\s*$")


def _remember_patch(patch_id: str, payload: dict[str, Any]) -> None:
    """Hold a generated patch server-side, keyed by proposal id.

    The alternative - round-tripping the replacement text through the browser -
    would mean trusting client-supplied file content at apply time. Keeping it
    here means the only thing the UI can send back is an identifier.
    """
    if len(_AI_PATCHES) >= _AI_CACHE_LIMIT:
        for stale in list(_AI_PATCHES)[: _AI_CACHE_LIMIT // 2]:
            _AI_PATCHES.pop(stale, None)
    _AI_PATCHES[patch_id] = payload


def ai_candidates(proposals: list[dict[str, Any]], *, limit: int = AI_MAX_CANDIDATES) -> list[dict[str, Any]]:
    """Proposals worth spending a model call on: real defects with no mechanical fix."""
    ranked = [
        p
        for p in proposals
        if p.get("kind") == "code" and not p.get("auto_fixable") and p.get("file") and p.get("lines")
    ]
    ranked.sort(key=lambda p: (SEVERITY_ORDER.get(p.get("severity", "low"), 9), p["file"]))
    return ranked[:limit]


def _window(text: str, line: int) -> tuple[int, int, str]:
    lines = text.splitlines(keepends=True)
    start = max(0, line - 1 - AI_WINDOW // 2)
    end = min(len(lines), start + AI_WINDOW)
    return start, end, "".join(lines[start:end])


def _clean_replacement(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        # A blank or whitespace-only reply would splice the excerpt out of the
        # file entirely, which is the worst possible reading of "no change".
        return ""
    return _FENCE.sub("", value.strip("\n"))


async def enrich_with_ai(
    root: Path,
    proposals: list[dict[str, Any]],
    provider: Any,
    *,
    limit: int = AI_MAX_CANDIDATES,
    language: str = "en",
) -> dict[str, Any]:
    """Attach model-authored patches to the proposals no rule can fix."""
    from app.ai import prompts  # imported lazily: the static path must not need it
    from app.ai.provider import AIProviderError, parse_json_response

    root = Path(root).resolve()
    patched: dict[str, dict[str, Any]] = {}
    attempted = failed = 0

    for candidate in ai_candidates(proposals, limit=limit):
        relative = str(candidate["file"])
        try:
            target = _resolve_inside(root, relative)
        except FixError:
            continue
        text = read_source(target) if target.is_file() else None
        if not text:
            continue
        current = digest(text)
        if current != candidate.get("digest"):
            # The file moved on since the scan; a patch against stale content
            # would be rejected at apply time anyway.
            continue

        start, end, excerpt = _window(text, int(candidate["lines"][0]))
        attempted += 1
        try:
            raw = await provider.chat(
                prompts.code_fix(candidate, relative, excerpt, start + 1, language), json_mode=True
            )
        except AIProviderError as exc:
            log.warning("AI fix for %s failed: %s", relative, exc)
            failed += 1
            continue

        parsed = parse_json_response(raw) or {}
        replacement = _clean_replacement(parsed.get("replacement"))
        if not replacement or replacement.strip() == excerpt.strip():
            failed += 1
            continue
        # A replacement wildly longer than the excerpt means the model wandered
        # off; splicing it would corrupt the file.
        if len(replacement.splitlines()) > max(len(excerpt.splitlines()) * 3, 20):
            log.warning("AI fix for %s discarded: replacement is implausibly long", relative)
            failed += 1
            continue

        lines = text.splitlines(keepends=True)
        if not replacement.endswith("\n") and end < len(lines):
            replacement += "\n"
        updated = "".join(lines[:start]) + replacement + "".join(lines[end:])
        if updated == text:
            failed += 1
            continue

        patch_id = f"ai:{candidate['id']}"
        _remember_patch(patch_id, {"file": relative, "digest": current, "text": updated})
        patched[candidate["id"]] = {
            "ai_fix_id": patch_id,
            "auto_fixable": True,
            "source": "ai",
            "diff": _unified(relative, text, updated),
            "ai_diagnosis": str(parsed.get("diagnosis") or ""),
            "ai_explanation": str(parsed.get("explanation") or ""),
            "ai_risk": str(parsed.get("risk") or ""),
            "ai_confidence": _confidence(parsed.get("confidence")),
        }

    merged = [dict(p, **patched.get(p.get("id", ""), {})) for p in proposals]
    return {
        "proposals": merged,
        "ai_attempted": attempted,
        "ai_patched": len(patched),
        "ai_failed": failed,
    }


def _confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(float(value), 1.0)), 2)
    except (TypeError, ValueError):
        return 0.0
