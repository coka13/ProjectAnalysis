"""Source-level signal extraction for the scoring engine.

The language analyzers build the structural knowledge graph. This module makes a
second, cheap pass over the same :class:`SourceFile` objects (their text is already
cached, so nothing is read twice) and extracts the *textual* signals that structure
alone cannot provide: tests, documentation, security smells, performance smells and
technical debt markers.

Everything produced here is evidence-bearing: every finding carries a file, a line,
a snippet and a severity so the UI can show the user exactly why a score moved.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.graph.model import KnowledgeGraph, NodeKind
from app.ingest.walker import SourceFile

CODE_LANGUAGES = {
    "python", "javascript", "typescript", "java", "csharp",
    "go", "rust", "c", "cpp", "shell", "powershell",
}

DOC_LANGUAGES = {"markdown"}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

MAX_FINDINGS_PER_RULE = 12
MAX_LINE_LENGTH = 120
LARGE_FILE_LOC = 600
HUGE_FILE_LOC = 1000

# Comment syntax per language, used for the comment/doc ratio.
LINE_COMMENT = {
    "python": ("#",),
    "shell": ("#",),
    "powershell": ("#",),
    "yaml": ("#",),
    "javascript": ("//",),
    "typescript": ("//",),
    "java": ("//",),
    "csharp": ("//",),
    "go": ("//",),
    "rust": ("//",),
    "c": ("//",),
    "cpp": ("//",),
    "sql": ("--",),
}

DEBT_MARKER = re.compile(r"(?<![A-Za-z])(TODO|FIXME|HACK|XXX|WORKAROUND|TEMPORARY|KLUDGE)\b[:\s-]*(.{0,90})", re.I)
DEPRECATED_MARKER = re.compile(r"@?\bdeprecated\b", re.I)

TEST_PATH_HINT = re.compile(r"(^|/)(tests?|__tests__|spec|specs|testing|e2e|it|integration[-_]tests?)(/|$)", re.I)
TEST_FILE_HINT = re.compile(r"(^|[._-])(test|tests|spec|specs)([._-]|$)|(^|/)test_[^/]+$|_test\.[a-z]+$|Tests?\.(cs|java)$", re.I)
ASSERTION_HINT = re.compile(
    r"\b(assert\w*|expect|assertThat|assertEquals|verify|require)\s*[\(\s]|"
    r"\.should\b|\bAssert\.\w+\(|\bt\.(Errorf|Fatalf)\("
)

CI_HINT = re.compile(r"(^|/)(\.github/workflows/|\.gitlab-ci\.yml|azure-pipelines\.yml|Jenkinsfile|\.circleci/|bitbucket-pipelines\.yml|\.travis\.yml)", re.I)
COVERAGE_HINT = re.compile(r"(^|/)(\.coveragerc|codecov\.ya?ml|jest\.config\.[jt]s|vitest\.config\.[jt]s|karma\.conf\.js)$", re.I)
CONTAINER_HINT = re.compile(r"(^|/)(dockerfile|docker-compose\.ya?ml|compose\.ya?ml)$", re.I)

README_HINT = re.compile(r"^(readme|README)(\.[a-z]+)?$", re.I)
DOC_DIR_HINT = re.compile(r"(^|/)(docs?|documentation|adr|rfcs?|architecture)(/|$)", re.I)
LICENSE_HINT = re.compile(r"^(license|licence|copying)(\.[a-z]+)?$", re.I)
CONTRIB_HINT = re.compile(r"^(contributing|code_of_conduct|changelog|security)(\.[a-z]+)?$", re.I)

PY_MODULE_DOC = re.compile(r'^\s*(?:#[^\n]*\n|\s*\n)*\s*(?:"""|\'\'\')')
JSDOC = re.compile(r"/\*\*")
XMLDOC = re.compile(r"^\s*///", re.M)
GODOC = re.compile(r"^\s*//\s*(?:Package|[A-Z]\w+)\s", re.M)


@dataclass
class Rule:
    """A single textual detector."""

    id: str
    category: str          # security | performance | debt | quality
    severity: str
    title: str
    pattern: re.Pattern[str]
    why: str
    fix: str
    languages: set[str] = field(default_factory=set)   # empty == all code languages
    ignore: re.Pattern[str] | None = None

    def applies(self, language: str) -> bool:
        return not self.languages or language in self.languages


def _rx(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern, flags)


# --------------------------------------------------------------------------- #
# Detection rules
# --------------------------------------------------------------------------- #
PLACEHOLDER = _rx(
    r"(os\.environ|getenv|process\.env|System\.getenv|config\[|settings\.|\$\{|\{\{|<[a-z_]+>|"
    r"xxx|placeholder|example|changeme|your[-_]?|dummy|sample|redacted|\*{3,}|null|none|empty)",
    re.I,
)

SECURITY_RULES: list[Rule] = [
    Rule(
        id="sec.hardcoded_secret",
        category="security",
        severity="critical",
        title="Hard-coded credential",
        pattern=_rx(
            r"""(?i)\b(password|passwd|pwd|secret|secret_key|api[_-]?key|apikey|access[_-]?token|
                auth[_-]?token|private[_-]?key|client[_-]?secret|connection[_-]?string)\b\s*[:=]\s*
                ["'][^"'\n]{8,}["']""",
            re.X,
        ),
        ignore=PLACEHOLDER,
        why="A credential committed to source control is readable by everyone with repository access and survives in git history forever.",
        fix="Move the value to an environment variable or a secret manager and rotate the leaked credential.",
    ),
    Rule(
        id="sec.private_key",
        category="security",
        severity="critical",
        title="Private key material in the repository",
        pattern=_rx(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
        why="Anyone who can read the repository can impersonate this identity.",
        fix="Remove the key, purge it from history and issue a replacement key pair.",
    ),
    Rule(
        id="sec.cloud_key",
        category="security",
        severity="critical",
        title="Cloud provider access key",
        pattern=_rx(r"\b(AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{35}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{36})\b"),
        why="A live provider key grants direct access to billed infrastructure.",
        fix="Revoke the key immediately and load credentials from the provider's secret store.",
    ),
    Rule(
        id="sec.sql_injection",
        category="security",
        severity="critical",
        title="SQL built by string concatenation",
        pattern=_rx(
            r"""(?is)(execute|executemany|query|raw|rawQuery|createQuery|prepareStatement)\s*\(\s*
                (f?["'][^"']{0,200}\b(select|insert|update|delete|drop)\b[^"']{0,200}["']\s*(\+|%|\.format\(|,\s*\()|
                 ["'][^"']{0,200}\b(select|insert|update|delete)\b[^"']{0,200}\{)""",
            re.X,
        ),
        why="Interpolated SQL lets an attacker rewrite the query and read or destroy the whole database.",
        fix="Use parameterised queries (bind parameters) or an ORM expression instead of building strings.",
    ),
    Rule(
        id="sec.command_injection",
        category="security",
        severity="high",
        title="Shell command execution",
        pattern=_rx(
            r"(os\.system\s*\(|subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True|child_process\.exec\s*\(|"
            r"Runtime\.getRuntime\(\)\.exec\s*\(|Process\.Start\s*\(|exec\.Command\s*\(\s*[\"'](?:sh|bash|cmd)"
            r")"
        ),
        why="Passing user-controlled text to a shell allows arbitrary command execution on the host.",
        fix="Call the binary directly with an argument list and never enable shell interpolation.",
    ),
    Rule(
        id="sec.dynamic_eval",
        category="security",
        severity="high",
        title="Dynamic code evaluation",
        pattern=_rx(r"(?<![\w.])(eval|exec)\s*\(|new\s+Function\s*\(|setTimeout\s*\(\s*[\"']|Function\s*\(\s*[\"']"),
        why="Evaluating text as code turns any injected string into executable logic.",
        fix="Replace the evaluation with an explicit parser, a lookup table or a safe expression evaluator.",
    ),
    Rule(
        id="sec.unsafe_deserialization",
        category="security",
        severity="high",
        title="Unsafe deserialization",
        pattern=_rx(
            r"(pickle\.loads?|cPickle\.loads?|marshal\.loads|yaml\.load\s*\((?![^)]*Safe)|"
            r"ObjectInputStream|BinaryFormatter|JsonConvert\.DeserializeObject<object>|"
            r"unserialize\s*\()"
        ),
        why="Deserialising untrusted data can instantiate arbitrary objects and run code during load.",
        fix="Use a safe loader (yaml.safe_load, JSON schema validation) or sign the payload before trusting it.",
    ),
    Rule(
        id="sec.weak_crypto",
        category="security",
        severity="high",
        title="Weak or broken cryptography",
        pattern=_rx(
            r"(?i)\b(md5|sha1)\s*\(|MessageDigest\.getInstance\s*\(\s*[\"'](?:MD5|SHA-?1)[\"']|"
            r"hashlib\.(md5|sha1)\b|new\s+(MD5|SHA1)CryptoServiceProvider|\bDES(ede)?\b|\bRC4\b|"
            r"Cipher\.getInstance\s*\(\s*[\"'][^\"']*ECB"
        ),
        why="These algorithms are collision-prone or trivially reversible and are unacceptable for secrets or integrity.",
        fix="Use SHA-256/SHA-3 for digests, bcrypt/argon2 for passwords and AES-GCM for symmetric encryption.",
    ),
    Rule(
        id="sec.tls_disabled",
        category="security",
        severity="critical",
        title="Certificate validation disabled",
        pattern=_rx(
            r"(verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|"
            r"NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*[\"']?0|ServerCertificateValidationCallback\s*(\+)?=|"
            r"TrustAllCerts|CURLOPT_SSL_VERIFYPEER\s*,\s*(?:false|0))"
        ),
        why="Disabling certificate checks removes all protection against man-in-the-middle interception.",
        fix="Trust the proper CA bundle, or pin the certificate, instead of switching verification off.",
    ),
    Rule(
        id="sec.plaintext_transport",
        category="security",
        severity="medium",
        title="Plain-text HTTP endpoint",
        pattern=_rx(r"[\"']http://(?!localhost|127\.0\.0\.1|0\.0\.0\.0|\{|\$)[A-Za-z0-9.\-]+"),
        why="Traffic over http:// can be read and modified in transit.",
        fix="Switch the endpoint to https:// and reject downgrades.",
    ),
    Rule(
        id="sec.permissive_cors",
        category="security",
        severity="high",
        title="Wildcard CORS policy",
        pattern=_rx(
            r"(Access-Control-Allow-Origin[\"']?\s*[:,]\s*[\"']\*|allow_origins\s*=\s*\[\s*[\"']\*|"
            r"origin\s*:\s*[\"']\*|AllowAnyOrigin\s*\(\s*\))"
        ),
        why="Any website can call the API with the browser's ambient credentials.",
        fix="Enumerate the exact origins that are allowed to call the API.",
    ),
    Rule(
        id="sec.debug_enabled",
        category="security",
        severity="medium",
        title="Debug mode enabled in code",
        pattern=_rx(r"(debug\s*=\s*True|DEBUG\s*=\s*True|app\.run\([^)]*debug\s*=\s*True|UseDeveloperExceptionPage\s*\()"),
        why="Debug handlers leak stack traces, configuration and sometimes an interactive console.",
        fix="Drive the flag from configuration and keep it off outside local development.",
    ),
    Rule(
        id="sec.xss_sink",
        category="security",
        severity="high",
        title="Unescaped HTML sink",
        pattern=_rx(r"(innerHTML\s*=|outerHTML\s*=|document\.write\s*\(|dangerouslySetInnerHTML|v-html\s*=|\|\s*safe\b|Html\.Raw\s*\()"),
        languages={"javascript", "typescript", "python", "csharp", "java"},
        why="Writing unescaped text into the DOM is the classic cross-site scripting vector.",
        fix="Set textContent, or sanitise the value with a vetted HTML sanitiser before injecting it.",
    ),
    Rule(
        id="sec.weak_random",
        category="security",
        severity="medium",
        title="Predictable randomness for a secret",
        pattern=_rx(r"(?i)(token|secret|password|salt|nonce|session|otp|key)\s*=\s*[^;\n]*\b(Math\.random|random\.random|random\.randint|rand\.Int|new\s+Random)\s*\("),
        why="General-purpose PRNGs are seeded predictably, so generated secrets can be guessed.",
        fix="Use a cryptographically secure generator (secrets, crypto.randomBytes, SecureRandom).",
    ),
    Rule(
        id="sec.privileged_container",
        category="security",
        severity="high",
        title="Over-privileged container",
        pattern=_rx(r"(privileged\s*:\s*true|USER\s+root|runAsUser\s*:\s*0|--cap-add[= ]ALL|allowPrivilegeEscalation\s*:\s*true)"),
        languages={"docker", "yaml", "shell"},
        why="A privileged container escapes most of the isolation the runtime is supposed to provide.",
        fix="Run as a non-root user and grant only the individual capabilities the workload needs.",
    ),
]

PERFORMANCE_RULES: list[Rule] = [
    Rule(
        id="perf.select_star",
        category="performance",
        severity="medium",
        title="SELECT * query",
        pattern=_rx(r"(?i)select\s+\*\s+from\s+\w"),
        why="Fetching every column moves data the caller does not need and breaks when the schema changes.",
        fix="List the columns the query actually consumes.",
    ),
    Rule(
        id="perf.await_in_loop",
        category="performance",
        severity="medium",
        title="Sequential await inside a loop",
        pattern=_rx(r"(?m)^\s*(for|while)\b[^\n]*\n(?:[^\n]*\n){0,4}?[^\n]*\bawait\b"),
        languages={"javascript", "typescript", "python", "csharp"},
        why="Each iteration waits for the previous one, turning a parallel workload into a serial one.",
        fix="Collect the promises/tasks and await them together (Promise.all, asyncio.gather, Task.WhenAll).",
    ),
    Rule(
        id="perf.query_in_loop",
        category="performance",
        severity="high",
        title="Database query inside a loop (N+1)",
        pattern=_rx(
            r"(?mi)^\s*(for|foreach|while)\b[^\n]*\n(?:[^\n]*\n){0,4}?[^\n]*"
            r"(\.query\(|\.filter\(|\.findOne\(|\.findById\(|\.findMany\(|\.fetchone\(|"
            r"\.execute\(|session\.get\(|repository\.\w+\(|SELECT\s+\w)"
        ),
        why="One query per row multiplies latency by the size of the collection.",
        fix="Fetch the rows in a single query with a join, an IN clause or an eager-loading option.",
    ),
    Rule(
        id="perf.blocking_sleep",
        category="performance",
        severity="medium",
        title="Blocking sleep on an async path",
        pattern=_rx(r"(?m)^\s*(?:async\s+def|async\s+function)[^\n]*\n(?:[^\n]*\n){0,20}?[^\n]*\b(time\.sleep|Thread\.sleep|Thread\.Sleep)\s*\("),
        languages={"python", "javascript", "typescript", "csharp", "java"},
        why="A blocking sleep parks the whole event loop or thread-pool thread.",
        fix="Use the asynchronous sleep primitive (asyncio.sleep, await delay, Task.Delay).",
    ),
    Rule(
        id="perf.unbounded_fetch",
        category="performance",
        severity="low",
        title="Unbounded result set",
        pattern=_rx(r"(?i)(\.all\(\)|findAll\(\s*\)|\.ToList\(\)|fetchall\(\))(?![^\n]*\b(limit|take|top|slice)\b)"),
        why="Loading an entire table into memory does not survive production data volumes.",
        fix="Paginate the query or stream the rows.",
    ),
    Rule(
        id="perf.sync_io_in_request",
        category="performance",
        severity="low",
        title="Synchronous file I/O on a request path",
        pattern=_rx(r"(readFileSync|writeFileSync|existsSync)\s*\("),
        languages={"javascript", "typescript"},
        why="Synchronous I/O blocks the single Node.js event loop for every concurrent request.",
        fix="Use the promise-based fs API, or hoist the read to start-up.",
    ),
]

DEBT_RULES: list[Rule] = [
    Rule(
        id="debt.silent_failure",
        category="debt",
        severity="high",
        title="Silently swallowed exception",
        pattern=_rx(
            r"(except[^\n:]*:\s*\n\s*(pass|\.\.\.)\s*$|catch\s*\([^)]*\)\s*\{\s*\}|"
            r"catch\s*\([^)]*\)\s*\{\s*//[^\n]*\n\s*\}|rescue\s*\n\s*end)",
            re.M,
        ),
        why="Errors disappear without a trace, so failures surface later as corrupt data instead of an alert.",
        fix="Log the error with context, or let it propagate to a handler that can decide what to do.",
    ),
    Rule(
        id="debt.broad_except",
        category="debt",
        severity="medium",
        title="Catch-all exception handler",
        pattern=_rx(r"(except\s+(BaseException|Exception)?\s*:|catch\s*\(\s*(Exception|Throwable|System\.Exception)\s+\w+\s*\)|catch\s*\{)"),
        why="Catching everything hides programming errors alongside the failure you meant to handle.",
        fix="Catch the specific exception types this block knows how to recover from.",
    ),
    Rule(
        id="debt.magic_number",
        category="debt",
        severity="low",
        title="Unexplained magic number",
        pattern=_rx(r"(?<![\w.\"'])(?:if|while|return|=|<|>|\+|\-|\*)\s*(?:\w+\s*[<>=!]=?\s*)?\b\d{4,}\b(?!\s*[)\]]?\s*[;,]?\s*(?://|#))"),
        why="Numeric literals without a name are impossible to audit and drift out of sync between call sites.",
        fix="Promote the value to a named constant next to its documentation.",
    ),
    Rule(
        id="debt.console_logging",
        category="debt",
        severity="low",
        title="Ad-hoc console logging",
        pattern=_rx(r"(?<![\w.])(console\.(log|debug)|print\s*\(|System\.out\.println|fmt\.Println)\s*\("),
        languages={"javascript", "typescript", "java", "go"},
        why="Unstructured output bypasses log levels, correlation ids and shipping to the log platform.",
        fix="Route the message through the project logger at an explicit level.",
    ),
    Rule(
        id="debt.loose_typing",
        category="debt",
        severity="medium",
        title="Type system escape hatch",
        pattern=_rx(r"(:\s*any\b|as\s+any\b|@ts-ignore|@ts-nocheck|# type:\s*ignore|dynamic\s+\w+\s*=|Object\s+\w+\s*=\s*\()"),
        languages={"typescript", "python", "csharp", "java"},
        why="Every escape hatch removes a compile-time guarantee and hides real defects.",
        fix="Introduce the precise type, a generic, or a narrowing guard.",
    ),
]

ALL_RULES = SECURITY_RULES + PERFORMANCE_RULES + DEBT_RULES


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
def _is_test_file(relative_path: str) -> bool:
    name = relative_path.rsplit("/", 1)[-1]
    return bool(TEST_PATH_HINT.search(relative_path) or TEST_FILE_HINT.search(name))


def _line_stats(text: str, language: str) -> tuple[int, int, int]:
    """Return ``(code_lines, comment_lines, blank_lines)``."""
    prefixes = LINE_COMMENT.get(language, ("//", "#"))
    code = comment = blank = 0
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if in_block:
            comment += 1
            if "*/" in line or line.endswith('"""') or line.endswith("'''"):
                in_block = False
            continue
        if not line:
            blank += 1
        elif any(line.startswith(prefix) for prefix in prefixes):
            comment += 1
        elif line.startswith("/*") or line.startswith('"""') or line.startswith("'''"):
            comment += 1
            body = line[3:] if line[:3] in ('"""', "'''") else line[2:]
            terminator = line[:3] if line[:3] in ('"""', "'''") else "*/"
            if terminator not in body:
                in_block = True
        else:
            code += 1
    return code, comment, blank


def _has_module_doc(text: str, language: str) -> bool:
    if language == "python":
        return bool(PY_MODULE_DOC.match(text))
    if language in {"javascript", "typescript", "java"}:
        return bool(JSDOC.search(text[:2000]))
    if language == "csharp":
        return bool(XMLDOC.search(text[:2000]))
    if language == "go":
        return bool(GODOC.search(text[:600]))
    return False


def _snippet(line: str) -> str:
    trimmed = line.strip()
    return trimmed[:140] + ("…" if len(trimmed) > 140 else "")


def _scan_rules(source: SourceFile, text: str, lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    is_test = _is_test_file(source.relative_path)
    for rule in ALL_RULES:
        if not rule.applies(source.language):
            continue
        # Test fixtures legitimately contain fake secrets and demo SQL.
        if is_test and rule.category == "security":
            continue
        hits = 0
        for match in rule.pattern.finditer(text):
            fragment = match.group(0)
            if rule.ignore and rule.ignore.search(fragment):
                continue
            line_no = text.count("\n", 0, match.start()) + 1
            findings.append(
                {
                    "rule": rule.id,
                    "category": rule.category,
                    "severity": rule.severity,
                    "title": rule.title,
                    "why": rule.why,
                    "fix": rule.fix,
                    "file": source.relative_path,
                    "line": line_no,
                    "snippet": _snippet(lines[line_no - 1] if line_no <= len(lines) else fragment),
                }
            )
            hits += 1
            if hits >= MAX_FINDINGS_PER_RULE:
                break
    return findings


def _scan_debt_markers(source: SourceFile, lines: list[str]) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        match = DEBT_MARKER.search(line)
        if not match:
            continue
        markers.append(
            {
                "marker": match.group(1).upper(),
                "note": match.group(2).strip()[:90],
                "file": source.relative_path,
                "line": index,
            }
        )
        if len(markers) >= 40:
            break
    return markers


def scan(files: Iterable[SourceFile], graph: KnowledgeGraph) -> dict[str, Any]:
    """Extract textual signals and annotate FILE nodes in place."""
    findings: list[dict[str, Any]] = []
    debt_markers: list[dict[str, Any]] = []
    file_profiles: list[dict[str, Any]] = []

    totals = Counter()
    project_docs = {"readme": False, "license": False, "contributing": False, "doc_pages": 0, "doc_words": 0}
    infra = {"ci": False, "coverage_config": False, "containerised": False}
    largest: list[dict[str, Any]] = []

    for source in files:
        relative = source.relative_path
        name = relative.rsplit("/", 1)[-1]

        if CI_HINT.search(relative):
            infra["ci"] = True
        if COVERAGE_HINT.search(relative):
            infra["coverage_config"] = True
        if CONTAINER_HINT.search(relative) or source.infra_kind in {"docker", "compose", "helm"}:
            infra["containerised"] = True
        if README_HINT.match(name):
            project_docs["readme"] = True
        if LICENSE_HINT.match(name):
            project_docs["license"] = True
        if CONTRIB_HINT.match(name):
            project_docs["contributing"] = True

        if source.language in DOC_LANGUAGES:
            text = source.text()
            project_docs["doc_pages"] += 1
            project_docs["doc_words"] += len(text.split())
            continue

        if source.language not in CODE_LANGUAGES:
            continue

        text = source.text()
        if not text:
            continue
        lines = text.splitlines()
        loc = len(lines)
        code_lines, comment_lines, blank_lines = _line_stats(text, source.language)
        is_test = _is_test_file(relative)
        long_lines = sum(1 for line in lines if len(line) > MAX_LINE_LENGTH)
        assertions = len(ASSERTION_HINT.findall(text)) if is_test else 0

        file_findings = _scan_rules(source, text, lines)
        file_markers = _scan_debt_markers(source, lines)
        findings.extend(file_findings)
        debt_markers.extend(file_markers)

        totals["files"] += 1
        totals["loc"] += loc
        totals["code_lines"] += code_lines
        totals["comment_lines"] += comment_lines
        totals["blank_lines"] += blank_lines
        totals["long_lines"] += long_lines
        totals["deprecated"] += len(DEPRECATED_MARKER.findall(text))
        if is_test:
            totals["test_files"] += 1
            totals["test_loc"] += loc
            totals["assertions"] += assertions
        else:
            totals["source_files"] += 1
            totals["source_loc"] += loc
            if _has_module_doc(text, source.language):
                totals["documented_modules"] += 1
            if loc >= HUGE_FILE_LOC:
                totals["huge_files"] += 1
            elif loc >= LARGE_FILE_LOC:
                totals["large_files"] += 1

        profile = {
            "file": relative,
            "module": source.module,
            "language": source.language,
            "loc": loc,
            "code_lines": code_lines,
            "comment_lines": comment_lines,
            "is_test": is_test,
            "findings": len(file_findings),
            "markers": len(file_markers),
            "long_lines": long_lines,
        }
        file_profiles.append(profile)
        if not is_test:
            largest.append(profile)

        node = graph.nodes.get(f"file:{relative}")
        if node is not None:
            node.attributes.update(
                {
                    "loc": loc,
                    "code_lines": code_lines,
                    "comment_lines": comment_lines,
                    "blank_lines": blank_lines,
                    "is_test": is_test,
                    "long_lines": long_lines,
                    "debt_markers": len(file_markers),
                    "findings": len(file_findings),
                    "risk_findings": sum(1 for f in file_findings if f["severity"] in {"critical", "high"}),
                }
            )

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["file"], f["line"]))
    largest.sort(key=lambda p: p["loc"], reverse=True)

    tested_modules = {p["module"] for p in file_profiles if p["is_test"]}
    source_modules = {p["module"] for p in file_profiles if not p["is_test"]}
    untested = sorted(source_modules - {m.replace("/tests", "").replace("/test", "") for m in tested_modules})

    return {
        "totals": dict(totals),
        "project_docs": project_docs,
        "infra": infra,
        "findings": findings[:250],
        "finding_counts": _count_by(findings),
        "debt_markers": debt_markers[:200],
        "marker_counts": dict(Counter(m["marker"] for m in debt_markers)),
        "largest_files": largest[:15],
        "untested_modules": untested[:25],
        "module_count": len(source_modules),
        "tested_module_count": len(source_modules) - len(untested),
        "symbol_docs": _symbol_documentation(graph),
        "complexity": _complexity_profile(graph),
    }


def _count_by(findings: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, Counter] = {}
    for finding in findings:
        by_category.setdefault(finding["category"], Counter())[finding["severity"]] += 1
    return {category: dict(counter) for category, counter in by_category.items()}


def _symbol_documentation(graph: KnowledgeGraph) -> dict[str, Any]:
    """Docstring coverage for public classes and functions already in the graph."""
    documented = public = 0
    undocumented: list[dict[str, Any]] = []
    kinds = (NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ABSTRACT_CLASS, NodeKind.FUNCTION, NodeKind.METHOD)
    for node in graph.nodes.values():
        if node.kind not in kinds or node.external:
            continue
        if node.attributes.get("visibility") == "private" or node.name.startswith("_"):
            continue
        if node.attributes.get("is_test") or _is_test_file(node.file or ""):
            continue
        public += 1
        if str(node.attributes.get("docstring") or "").strip():
            documented += 1
        elif len(undocumented) < 25:
            undocumented.append(
                {"name": node.qualified_name or node.name, "kind": node.kind, "file": node.file, "line": node.line}
            )
    return {
        "public_symbols": public,
        "documented_symbols": documented,
        "coverage": round(documented / public, 4) if public else 0.0,
        "undocumented": undocumented,
    }


def _complexity_profile(graph: KnowledgeGraph) -> dict[str, Any]:
    """Cyclomatic complexity distribution over the functions that report it."""
    values: list[int] = []
    offenders: list[dict[str, Any]] = []
    wide_signatures: list[dict[str, Any]] = []
    for node in graph.nodes.values():
        if node.kind not in (NodeKind.FUNCTION, NodeKind.METHOD) or node.external:
            continue
        complexity = node.attributes.get("complexity")
        params = node.attributes.get("params") or []
        if isinstance(params, list) and len(params) >= 6:
            wide_signatures.append(
                {"name": node.qualified_name or node.name, "file": node.file, "line": node.line, "params": len(params)}
            )
        if not isinstance(complexity, int):
            continue
        values.append(complexity)
        if complexity >= 11:
            offenders.append(
                {
                    "name": node.qualified_name or node.name,
                    "file": node.file,
                    "line": node.line,
                    "complexity": complexity,
                    "severity": "high" if complexity >= 21 else "medium",
                }
            )
    offenders.sort(key=lambda o: o["complexity"], reverse=True)
    wide_signatures.sort(key=lambda o: o["params"], reverse=True)
    values.sort()
    measured = len(values)
    return {
        "measured_functions": measured,
        "average": round(sum(values) / measured, 2) if measured else 0.0,
        "median": values[measured // 2] if measured else 0,
        "p90": values[int(measured * 0.9)] if measured else 0,
        "max": values[-1] if measured else 0,
        "over_threshold": len(offenders),
        "offenders": offenders[:20],
        "wide_signatures": wide_signatures[:15],
        "distribution": _histogram(values),
    }


def _histogram(values: list[int]) -> list[dict[str, Any]]:
    buckets = [("1-5", 1, 5), ("6-10", 6, 10), ("11-20", 11, 20), ("21-50", 21, 50), ("50+", 51, 10**6)]
    return [
        {"label": label, "count": sum(1 for v in values if low <= v <= high)}
        for label, low, high in buckets
    ]
