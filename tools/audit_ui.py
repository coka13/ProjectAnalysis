"""Static UI audit: check that behaviours the JS relies on are actually present.

Each check is a (label, predicate) pair over the raw source text. This is a
cheap regression net for the class of defect where JS toggles a class or a
preference that no CSS rule ever consumes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

CSS = (WEB / "css" / "app.css").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
WINDOW_PY = (ROOT / "app" / "desktop" / "window.py").read_text(encoding="utf-8")
BRANDING_PY = (ROOT / "app" / "branding.py").read_text(encoding="utf-8")
SPEC = (ROOT / "ProjectAnalysis.spec").read_text(encoding="utf-8")
MAKE_ICON = (ROOT / "tools" / "make_icon.py").read_text(encoding="utf-8")
JS = {p.name: p.read_text(encoding="utf-8") for p in (WEB / "js").glob("*.js")}
ALL_JS = "\n".join(JS.values())
SOURCE_PY = (ROOT / "app" / "ingest" / "source.py").read_text(encoding="utf-8")
BRIDGE_PY = (ROOT / "app" / "desktop" / "bridge.py").read_text(encoding="utf-8")
HISTORY_PY = (ROOT / "app" / "history" / "git_history.py").read_text(encoding="utf-8")

# The closing edge of the product mark: short, unique, and present in every
# copy of the path, so a change in one place and not the others is caught.
MARK_SEGMENT = "M8.2 18h7.6"


def css_has(selector: str) -> bool:
    return selector in CSS


COMMENT = re.compile(r"//[^\n]*")


def _split_top_level(text: str) -> list[str]:
    """Split an argument list on the commas that are not nested in brackets."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def appends_a_null() -> list[str]:
    """Arguments to the native append() that can be null.

    `el()` drops null children, but the native DOM method stringifies them, so
    `node.append(cond ? child : null)` writes the literal word "null" onto the
    page whenever the condition is false. The two look identical in the source,
    which is exactly why this needs a machine to check it.
    """
    offenders: list[str] = []
    for name, source in JS.items():
        text = COMMENT.sub("", source)
        for match in re.finditer(r"\.append\(", text):
            depth, index = 1, match.end()
            while index < len(text) and depth:
                if text[index] in "([{":
                    depth += 1
                elif text[index] in ")]}":
                    depth -= 1
                index += 1
            for argument in _split_top_level(text[match.end() : index - 1]):
                trimmed = argument.strip()
                if trimmed in {"null", "undefined"} or re.search(r":\s*(null|undefined)$", trimmed):
                    offenders.append(f"{name}: {' '.join(trimmed.split())[:80]}")
    return offenders


NULL_APPENDS = appends_a_null()

# The recovery scripts in index.html run on engines that cannot parse the
# application, so they may not use anything the application relies on. String
# literals are stripped first: the engine probe deliberately hands modern syntax
# to `new Function` as text, which is exactly how it detects an old engine.
BOOT_SCRIPTS = re.sub(
    r"'[^'\n]*'|\"[^\"\n]*\"",
    "''",
    "".join(re.findall(r"<script>(.*?)</script>", HTML, re.S)),
)
MODERN_SYNTAX = re.compile(r"=>|`|\bconst\b|\blet\b|\basync\b|\.\.\.|\?\?|\?\.")

# Every --f-* token must be expressed in a unit that follows the root font size,
# otherwise the Text size preference scales `html` and nothing else moves.
ABSOLUTE_TYPE_SCALE = [
    f"{name}: {value}"
    for name, value in re.findall(r"(--f-[\w-]+)\s*:\s*([^;]+);", CSS)
    if "rem" not in value and "em" not in value and "%" not in value
]

# A width animation with only a `from` keyframe resolves its end state to the
# width the element already has, so a bar sized by --mag never appears to grow.
def _keyframes_body(name: str) -> str:
    """The text of a @keyframes block, brace-matched past its inner steps."""
    start = CSS.find(f"@keyframes {name}")
    if start < 0:
        return ""
    open_brace = CSS.find("{", start)
    depth, index = 1, open_brace + 1
    while index < len(CSS) and depth:
        if CSS[index] == "{":
            depth += 1
        elif CSS[index] == "}":
            depth -= 1
        index += 1
    return CSS[open_brace + 1 : index - 1]


MAG_GROW_HAS_TO = bool(re.search(r"\bto\s*\{", _keyframes_body("mag-grow")))

GIT_HISTORY_PY = (ROOT / "app" / "history" / "git_history.py").read_text(encoding="utf-8")

CHARTS_JS = JS["charts.js"]
# Comments are stripped before looking for an ellipsis, because the note
# explaining the defect naturally quotes the defect.
CHARTS_CODE = COMMENT.sub("", re.sub(r"/\*.*?\*/", "", CHARTS_JS, flags=re.S))


def _function_body(source: str, name: str) -> str:
    """The text of a top-level `function name(` block in a chart module."""
    start = source.find(f"function {name}(")
    if start < 0:
        return ""
    open_brace = source.find("{", start)
    depth, index = 1, open_brace + 1
    while index < len(source) and depth:
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
        index += 1
    return source[open_brace:index]


# A treemap tile is a hard physical bound, so a name that cannot fit inside one
# is shortened there and carried in full as a tooltip. Everywhere else the
# canvas grows to the text, so an ellipsis in any other chart is a defect: it
# is how the Category Balance radar came to print "Maintainabilit…".
TREEMAP_BODY = _function_body(CHARTS_CODE, "treemap")
TREEMAP_START = CHARTS_CODE.find(TREEMAP_BODY) if TREEMAP_BODY else -1
TREEMAP_END = TREEMAP_START + len(TREEMAP_BODY)
ELIDED_LABELS = [
    CHARTS_CODE[:hit].splitlines()[-1].strip()[:70]
    for hit in (m.start() for m in re.finditer("\u2026", CHARTS_CODE))
    if not (TREEMAP_START <= hit < TREEMAP_END)
]

# bars (horizontal and vertical), radar, the stacked bars, the line chart and
# the donut all draw text outside their nominal box and must therefore refit
# the canvas around it. The line chart's y axis labels hang off the left edge
# and the donut's centre caption outgrows the hole in the ring.
FITTED_CHARTS = len(re.findall(r"(?<!function )fitContent\(svg,", CHARTS_CODE))


def _method_body(source: str, name: str) -> str:
    """The text of one `def name(` method, up to the next method at its level."""
    start = source.find(f"    def {name}(")
    if start < 0:
        return ""
    nxt = source.find("\n    def ", start + 1)
    return source[start : nxt if nxt > 0 else len(source)]


# Listing branches legitimately resolves - for a remote there is nothing to
# list until it has been mirrored - so only the two history endpoints are held
# to the read-only path.
HISTORY_ENDPOINTS = _method_body(BRIDGE_PY, "analysis_history") + _method_body(
    BRIDGE_PY, "analysis_commit_graph"
)


CHECKS: list[tuple[str, bool]] = [
    ("--ui-scale multiplied into a length", "calc(var(--ui-scale" in CSS),
    # Scaling `html` only reaches text that is sized relative to the root. A px
    # type scale silently ignores it, which is how Text size came to do nothing.
    (f"the type scale is relative, not px ({'; '.join(ABSOLUTE_TYPE_SCALE) or 'ok'})", not ABSOLUTE_TYPE_SCALE),
    ("[data-motion='reduced'] honoured", "[data-motion='reduced']" in CSS),
    ("[data-contrast='high'] honoured", "[data-contrast='high']" in CSS),
    ("[data-palette='cb'] honoured", "[data-palette='cb']" in CSS),
    (".viewer.fullscreen styled", css_has(".viewer.fullscreen")),
    ("viewer .dimmed styled", ".dimmed" in CSS),
    ("viewer .highlight styled", ".highlight" in CSS),
    ("viewer .dragging styled", ".dragging" in CSS),
    ("modal handles Escape", "onEscape" in JS["dom.js"]),
    ("modal traps Tab", "trapTab" in JS["dom.js"]),
    ("modal has aria-modal", "aria-modal" in JS["dom.js"]),
    ("toast is dismissible", "class: 'close'" in JS["dom.js"]),
    ("tabs support arrow keys", "ArrowRight" in JS["dom.js"]),
    ("inline field validation exists", "field-error" in JS["dom.js"] and "field-error" in CSS),
    ("sortable headers expose aria-sort", "aria-sort" in ALL_JS and "aria-sort" in CSS),
    ("appearance applied before first paint", "aai.theme" in HTML and "--ui-scale" in HTML),
    ("viewer search is debounced", "searchTimer" in JS["viewer.js"]),
    ("viewer fullscreen exits on Escape", "escapeFullscreen" in JS["viewer.js"]),
    ("failed loads offer a retry", "function errorState" in JS["app.js"]),
    ("busy buttons expose aria-busy", "aria-busy" in JS["app.js"]),
    # A guessed dash length leaves lines permanently segmented; it must be measured.
    ("line chart measures its dash length", "polylineLength" in JS["charts.js"]),
    ("line chart clears the dash after drawing in", "strokeDasharray = ''" in JS["charts.js"]),
    ("draw-in animation respects reduced motion", "reducedMotion" in JS["charts.js"]),
    # `.chart svg` used to match icons too and stretch them to the panel width.
    ("chart sizing does not capture icons", ".chart > svg:not(.ico)" in CSS),
    # Git graph: every layer of the feature has to be wired up or the panel is dead.
    ("git graph renderer registered", "AAI.gitgraph" in JS["gitgraph.js"]),
    ("git graph loaded before the shell", HTML.index("js/gitgraph.js") < HTML.index("js/app.js")),
    ("git graph reached from the history view", "commitGraphCard" in JS["app.js"]),
    ("git graph bound to a bridge call", "analysis_commit_graph" in JS["api.js"]),
    ("git graph edges and nodes styled", css_has(".gitgraph-edge") and css_has(".gitgraph-dot")),
    # Virtualising on endpoint visibility is exactly what makes long branch
    # lines vanish partway down the graph; the span has to be tested instead.
    ("git graph keeps edges that span the viewport", "bottom" in JS["gitgraph.js"] and "top" in JS["gitgraph.js"]),
    ("git graph repaints after layout", "ResizeObserver" in JS["gitgraph.js"]),
    # Guided fixes: the safety contract has to be visible in the code, not just the docs.
    ("fix review is reachable from the nav", "'fixes'" in JS["app.js"] and "nav.fixes" in JS["app.js"]),
    ("applying fixes always sends an explicit confirm", "confirm: true" in JS["api.js"]),
    ("fix review states that nothing is written automatically", "fixes.manualOnly" in JS["app.js"]),
    ("applying fixes asks for confirmation first", "confirmDialog(" in JS["app.js"]),
    # Diff text is repository source; rendering it as markup would be an XSS sink.
    ("diffs are rendered as text, never markup", "row.textContent = line" in JS["app.js"]),
    ("diff panes stay LTR in a Hebrew UI", "direction: ltr" in CSS),
    # A null handed to the native append() is printed as the word "null".
    (f"no null reaches a native append ({'; '.join(NULL_APPENDS) or 'none'})", not NULL_APPENDS),
    # A blank dark window is the worst possible failure report, so the page has
    # to be able to explain itself even when no application script survived.
    ("boot failures are captured", "__BOOT_ERRORS__" in HTML),
    ("an empty window explains itself", "getElementById('root')" in HTML and "WebView2" in HTML),
    ("the recovery scripts avoid syntax the failing engine rejects", not MODERN_SYNTAX.search(BOOT_SCRIPTS)),
    ("the launcher checks for the WebView2 runtime", "webview2_installed" in WINDOW_PY),
    # A bare count teaches nobody anything: every metric tile carries its own
    # definition, a verdict and a sentence about the value that was measured.
    ("architecture metrics explain themselves", css_has(".metric-tile") and "metricTile(" in JS["app.js"]),
    ("instability is placed on a labelled scale", css_has(".scale-track") and "ratioScale(" in JS["app.js"]),
    # Declaring the podium rise in CSS is what lets the reduced-motion rule
    # switch it off; a JS-driven animation would ignore that preference.
    ("the contributor podium animates from CSS", "@keyframes podium-rise" in CSS and "contributorPodium(" in JS["app.js"]),
    ("about names the author and the version", "Daniel Uralsky" in JS["app.js"] and "about.version" in JS["app.js"]),
    # Findings used to be a flat list of sentences. A dependency cycle is a
    # shape, so it gets drawn as one; sizes are compared with bars rather than
    # left as numbers the reader has to rank in their head.
    ("findings are drawn, not just listed", css_has(".chain-node") and "cycleChain(" in JS["app.js"]),
    ("findings compare sizes with bars", css_has(".mag-fill") and "magnitudeRow(" in JS["app.js"]),
    # An implicit `to` resolves to the width the element already has when the
    # animation starts, which for a bar sized by --mag is the final width, so
    # the bar would simply appear. The end keyframe has to be spelled out.
    ("the magnitude bar declares an explicit end", "@keyframes mag-grow" in CSS and MAG_GROW_HAS_TO),
    # Re-rendering a view (a language switch does exactly that) must not start
    # a second `git log` alongside the first; two of them race the timeout and
    # the history comes back empty.
    ("repeat reads share one in-flight call", "sharedCall(" in JS["api.js"] and "inFlight" in JS["api.js"]),
    ("history reads go through the shared call", "sharedCall('analysis_history'" in JS["api.js"]),
    # "No history" and "history could not be read" are different facts, and
    # only one of them is worth offering a retry for.
    ("an unreadable history says so", '"failed": True' in GIT_HISTORY_PY and "history.failed" in JS["app.js"]),
    # Chart labels: a category the reader cannot name is worse than no chart.
    # Truncation used to be the default, so the code must grow the canvas to
    # the text instead of cutting the text to the canvas.
    (f"no chart label is elided ({'; '.join(ELIDED_LABELS) or 'none'})", not ELIDED_LABELS),
    ("chart labels wrap instead of being cut", "function wrapLabel" in JS["charts.js"]),
    ("wrapped labels keep the full text for hovering", "function textBlock" in JS["charts.js"]),
    ("the canvas grows to fit its labels", "function fitContent" in JS["charts.js"]),
    (f"every labelled chart refits ({FITTED_CHARTS} of 6)", FITTED_CHARTS == 6),
    ("a chart can be refitted without waiting for a frame", "refit" in JS["charts.js"]),
    # Chart geometry is computed left to right, so a Hebrew document flipped
    # what `text-anchor: start` meant and pushed labels off the canvas.
    ("chart geometry ignores the document direction", ".chart > svg:not(.ico) { direction: ltr" in CSS),
    ("chart text still reads in its own direction", "unicode-bidi: plaintext" in CSS),
    # The shell is built once, so a language switch has to reach it through
    # markup; the title and tagline stayed English forever without this.
    ("the top bar follows the language", "'data-i18n': 'app.title'" in JS["app.js"]),
    # A control labelled in Hebrew but announced in English cannot be found by
    # voice and is read out in the wrong language.
    ("aria labels are translated too", "data-i18n-aria" in JS["i18n.js"] and "data-i18n-aria" in JS["app.js"]),
    # A language change can land before mount() finishes; the listener then
    # rebuilt parts of a shell that did not exist yet.
    ("the shell rebuild survives an early language switch", "if (!ui.nav) return;" in JS["app.js"]),
    # One icon everywhere. Without an explicit icon pywebview falls back to the
    # interpreter's own, which is how the window came to wear the Python logo.
    ("the application ships an icon", (ROOT / "assets" / "appicon.ico").exists()),
    ("the window is given that icon", "icon=str(icon)" in WINDOW_PY),
    ("the taskbar groups under the app, not python", "SetCurrentProcessExplicitAppUserModelID" in WINDOW_PY),
    ("the icon can be regenerated from source", (ROOT / "tools" / "make_icon.py").exists()),
    ("the header wears the same mark as the icon", "appMark(16)" in JS["app.js"]),
    # The mark used to be `layers`, which also labels Diagrams, the
    # Architecture category and the instability card - and, worst of all, the
    # sidebar button sitting right beside the brand plate, so the top bar
    # opened showing one glyph twice, once grey and once white.
    ("the product has a mark of its own", "appmark:" in JS["dom.js"]),
    ("the sidebar button does not reuse the product mark", "icon('sidebar'" in JS["app.js"]),
    ("no two top bar controls share a glyph", "icon('layers', { size: 16 })" not in JS["app.js"]),
    # Four copies of one path: the raster icon, the SVG mark, the favicon and
    # the About hero. A distinctive segment of the path keeps them honest.
    ("the favicon draws that same mark", MARK_SEGMENT in HTML and MARK_SEGMENT in JS["dom.js"]),
    ("the executable icon draws that same mark", "appmark" in MAKE_ICON),
    ("the page has a favicon", "rel=\"icon\"" in HTML or "rel='icon'" in HTML),
    # Branding is read from one module so the About page, the window title and
    # the executable's version resource cannot drift apart.
    ("branding has a single source", (ROOT / "app" / "branding.py").exists()),
    ("the release build is scripted", (ROOT / "tools" / "build_exe.py").exists() and (ROOT / "ProjectAnalysis.spec").exists()),
    # Frozen, a module's __file__ names a path inside an archive that was never
    # written to disk, so walking up from it finds no web/ or assets/ and the
    # packaged app opens on an empty window. sys._MEIPASS is the only reliable
    # answer, and it must be asked in exactly one place.
    ("data files are found through the bundle root", "_MEIPASS" in BRANDING_PY),
    ("only one module resolves the bundle root", "_MEIPASS" not in WINDOW_PY),
    ("the window reads its resources from branding", "branding.resource_root()" in WINDOW_PY),
    # The UI is loaded over file://, so it cannot be frozen into the archive.
    ("the build ships the UI and the icon", '"web"' in SPEC and '"assets"' in SPEC),
    ("the packaged app opens no console", "console=False" in SPEC),
    ("the executable carries icon and version metadata", "icon=" in SPEC and "version_info.txt" in SPEC),
    # A windowed build has no console, so a console subprocess flashes one.
    ("git is invoked without flashing a console", "creationflags" in BRANDING_PY),
    # About is an identity card; where the data lives is an operational detail.
    ("about shows the build it came from", "about.build" in JS["app.js"]),
    ("storage paths moved to settings", "storageSettings" in JS["app.js"] and "settings.dataDir" in JS["app.js"]),
    ("about no longer doubles as a file browser", "about.dataDir" not in JS["app.js"]),
    # --------------------------------------------------------- git history
    # Testing for `path/.git` only recognises the top of a checkout, so a
    # project opened at `repo/backend` reported "not a git repository" and left
    # Repository History permanently empty.
    ("a repository is found from any folder inside it", "--show-toplevel" in SOURCE_PY),
    ("an empty repository is recognised rather than reported as an error", "def has_commits" in SOURCE_PY),
    ("git subprocesses do not flash a console", "creationflags" in SOURCE_PY),
    ("git subprocesses never inherit a bad stdin", "stdin=subprocess.DEVNULL" in SOURCE_PY),
    # `resolve` clones remotes and force-checks-out local working trees.
    # Reading history must never do either, so the read path is separate.
    ("history uses the read-only locate, not resolve", "def locate" in SOURCE_PY),
    ("the history bridge does not take the write path", bool(HISTORY_ENDPOINTS) and "source_mod.resolve" not in HISTORY_ENDPOINTS),
    ("the history bridge locates the checkout read-only", "source_mod.locate(" in HISTORY_ENDPOINTS),
    # A stored "not a repository" answer used to be returned forever.
    ("a negative history result is not cached", 'stored.get("available")' in BRIDGE_PY),
    # Raw git text in a Hebrew window is worse than no text at all.
    ("history explains itself with a translatable key", "reason_key" in HISTORY_PY),
    ("the UI translates that key", "reason_key" in JS["app.js"] and "historyReason" in JS["app.js"]),
    # ------------------------------------------------------- chart labels
    # The automatic fit gave up when the SVG was not yet in the document, and
    # every view builds its chart before attaching it, so labels stayed
    # clipped unless something called refit() - and nothing does.
    ("the chart fit does not depend on an animation frame", "Promise.resolve().then(apply)" in JS["charts.js"]),
    ("the chart fit re-runs when the canvas resizes", "ResizeObserver" in JS["charts.js"]),
    ("the radar spaces its labels by how many there are", "LABEL_RING" in JS["charts.js"]),
    # A block box with an explicit width sits flush right under direction:rtl,
    # which put every diagram narrower than the stage off centre in Hebrew.
    ("the diagram canvas is pinned to LTR so centring matches", css_has(".viewer-canvas") and "direction: ltr" in CSS),
]


def main() -> int:
    failures = [label for label, ok in CHECKS if not ok]
    for label, ok in CHECKS:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if failures:
        print(f"\n{len(failures)} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
