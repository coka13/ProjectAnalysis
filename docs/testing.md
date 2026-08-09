# Testing

```powershell
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```

## What the suites cover

| File | Covers |
| --- | --- |
| `tests/test_analysis.py` | the analysis pipeline end to end |
| `tests/test_bridge.py` | every bridge endpoint's contract |
| `tests/test_diagrams.py` | diagram generators |
| `tests/test_insights.py` | AI capabilities and their offline fallbacks |
| `tests/test_quality.py` | metrics and quality signals |
| `tests/test_scoring.py` | the 0-100 score, weights and the improvement plan |
| `tests/test_security.py` | secret encryption, path traversal, ref validation |
| `tests/test_git_history.py` | hotspots, ownership, temporal coupling, risk |
| `tests/test_commit_graph.py` | commit DAG lane assignment and edge continuity |
| `tests/test_fixes.py` | the fix catalogue and the apply safety rules |
| `tests/test_packaging.py` | what changes once the app is frozen into an EXE |
| `tests/test_ui_static.py` | runs the static audits below |
| `tests/test_js_browser.py` | the in-browser JS suite |

The git tests skip themselves if `git` is not on the PATH.

## Static audits

```powershell
.\.venv\Scripts\python tools\audit_ui.py     # JS behaviour <-> CSS rules
.\.venv\Scripts\python tools\audit_i18n.py   # en/he key parity
```

`audit_ui.py` is a cheap net for one specific defect class: JS that toggles a
class, attribute or preference that no CSS rule ever consumes, and features whose
wiring is split across several files. Each check is a `(label, predicate)` pair
over the raw source text - add one whenever you fix a bug of that shape.

## Browser tests

There is no npm, no bundler and no jsdom in this project. The JS suite runs in the
**real webview**, which is the only place where `SVGGeometryElement.getTotalLength()`
and `getComputedStyle()` behave the way they do for a user.

```
web/tests/framework.js     ~100 lines: suite/test/assert/withStage/run
web/tests/dom.test.js      icons, formatting and the small DOM helpers
web/tests/charts.test.js   chart geometry, with refit() called by hand
web/tests/labels.test.js   label visibility along the production path
web/tests/viewer.test.js   diagram centring in both reading directions
web/tests/gitgraph.test.js the commit graph
web/tests/harness.html     loads the app's real CSS and scripts, then reports
tools/js_harness.py        opens a hidden pywebview window and collects the JSON
tests/test_js_browser.py   runs the harness as a subprocess from pytest
```

`webview.start()` needs the main thread and can only run once per process, hence
the subprocess. `tests/test_js_browser.py` skips itself when no webview is
available, so the suite still runs on a headless machine.

To add a case, append to the suite it belongs to (or add a file and register it
in `harness.html`). Use `withStage()` when the assertion depends on layout - an
element that is never attached to the document has no geometry. `withStage()`
waits for an async body before removing the stage, so `await` inside it is safe.

The harness window is hidden, and a hidden Chromium window does not deliver
`requestAnimationFrame`. A test that awaits a frame therefore never finishes and
the whole harness times out with "the harness never reported back". Drain
microtasks instead - `labels.test.js` has a `settle()` helper that does exactly
that - or call `chart.refit()` directly.

### Testing the path the app actually takes

`charts.test.js` calls `chart.refit()` before measuring. That proves the fitting
maths, but it is not what a view does: views build a chart, pass it to
`charts.panel()` and append the result, and nothing ever calls `refit`. A whole
class of clipped-label bugs lived in that gap. `labels.test.js` therefore
attaches charts exactly the way a view does and measures without touching
`refit`, and `viewer.test.js` measures rendered pixels under both `dir=ltr` and
`dir=rtl`, because RTL block placement is a property of the engine that cannot
be asserted from the source.

## Linting

`pyproject.toml` configures `ruff` and `mypy`. Both are in
`requirements-dev.txt`:

```powershell
.\.venv\Scripts\ruff check app tests tools
.\.venv\Scripts\mypy
```
