# ProjectAnalysis

A **local desktop application** that reads any codebase on your computer, builds a
knowledge graph of it, and generates professional architecture diagrams, metrics
and (optionally AI-written) explanations.

There is no server, no login and no network dependency. Everything - the database,
the diagram renderer and your AI credentials - lives on your machine.

---

## What it does

- **Connects to a codebase**: a local folder, or a git repository it clones for you.
- **Analyses 8 languages** plus infrastructure and SQL: Python, JavaScript/TypeScript,
  Java, C#, Go, Rust, C/C++, plus Dockerfiles, Compose, Kubernetes and SQL schemas.
- **Builds a knowledge graph** of modules, classes, functions, routes, tables,
  services and the relationships between them.
- **Generates 9 diagram types**: architecture, component, class, dependency,
  sequence, data flow, database/ER, deployment and state.
- **Computes architecture metrics**: dependency cycles, god classes, coupling and
  instability, layering violations, abstraction ratio and detected patterns.
- **Scores the codebase out of 100** across eight weighted categories, and shows
  its work: every point deducted links back to the file and line that caused it.
- **Analyses git history**: hotspots, ownership, temporal coupling and risk, plus a
  virtualised commit graph showing branches, merges and tags.
- **Proposes deterministic fixes**: mechanical defects (bare `except:`, unsafe
  `yaml.load`, stray `debugger;`) come with a unified diff you review and apply
  yourself. Nothing is ever written without an explicit confirmation.
- **Compares two analyses** to show architectural drift over time.
- **Explains everything in English or Hebrew**, with full right-to-left support.
- **Exports** to PNG, SVG, PDF, Mermaid, PlantUML, Markdown, HTML, Draw.io and JSON.

AI is entirely optional. Without a provider configured, every insight falls back to
deterministic static analysis, so the app is fully useful offline.

---

## Two ways to run it

The same application ships with two interchangeable interfaces. They share every
line of analysis, scoring, diagram and export code — only the window differs.

| | **WebView2 build** | **Native build** |
|---|---|---|
| Run with | `python -m app` | `python -m app.ui` |
| Console script | `projectanalysis` | `projectanalysis-native` |
| Interface | HTML/CSS/JS in `web/`, hosted by pywebview | Qt widgets in `app/ui/` |
| Needs Edge WebView2 | **Yes** (or a bundled runtime) | **No** |
| Extra dependency | `pywebview` | `PySide6` |
| PyInstaller spec | `ProjectAnalysis.spec` | `ProjectAnalysisNative.spec` |
| Window state file | `window-state.json` | `window-state-native.json` |
| Log file | `app.log` | `app-native.log` |

Both may be installed at once. They keep separate window geometry and log files,
so running one never disturbs the other.

**Which should I use?** The WebView2 build is the original and remains the visual
reference. Choose the native build when the target machine has no Edge WebView2
Runtime, cannot install one, or shows a black window because of it.

### Why there are two

Everything below the window — `app/analyzers`, `app/graph`, `app/diagrams`,
`app/engine`, `app/services` and the `Api` in `app/desktop/bridge.py` — is shared.
`Api` is deliberately free of any UI toolkit: the only calls that need a window
are the file dialogs, and those sit behind a small `FileDialogHost` protocol that
each front end implements. A change to the analysis or scoring reaches both
interfaces at once.

---

## Requirements

- Python 3.10 or newer
- `git` on the PATH, if you want to analyse git repositories or history
- **For the WebView2 build only**: the Microsoft Edge WebView2 Runtime on Windows,
  or the system WebKit webview on macOS/Linux. Windows 11 and current Windows 10
  builds already have it; older or locked-down images may not.
- **For the native build**: nothing beyond PySide6. It has no browser engine.

## Install

Install the shared core plus the interface you want:

```powershell
python -m venv .venv

# WebView2 build
.\.venv\Scripts\pip install -r requirements-webview.txt

# Native build (no WebView2)
.\.venv\Scripts\pip install -r requirements-native.txt
```

On macOS / Linux, use `./.venv/bin/pip` in place of `.\.venv\Scripts\pip`.

## Run

```powershell
.\.venv\Scripts\python -m app        # WebView2 interface
.\.venv\Scripts\python -m app.ui     # native interface
```

A desktop window opens. Nothing is served over HTTP and no port is opened.

## Building a Windows executable

The release build is one command. It regenerates the application icon, writes a
Windows version resource from `app/branding.py`, and packages the application
with no console window:

```powershell
.\.venv\Scripts\pip install pyinstaller

.\.venv\Scripts\python tools\build_exe.py --target webview   # default
.\.venv\Scripts\python tools\build_exe.py --target native
.\.venv\Scripts\python tools\build_exe.py --target both
```

That produces `dist\ProjectAnalysis\ProjectAnalysis.exe` and/or
`dist\ProjectAnalysisNative\ProjectAnalysisNative.exe`, each next to an
`_internal` folder holding the interpreter, the UI and the icon. Ship the whole
folder — the executable will not start on its own. Neither needs Python on the
target machine.

The native build additionally needs **no Edge WebView2 Runtime**: its spec
excludes `webview`, `clr_loader` and every QtWebEngine module, so no browser
engine can reach the output.

The executable carries the icon, product name, file description, company and
version taken from `app/branding.py` — change the version in one place there and
the window, the About screen and the file properties dialog all follow.

The icon itself is generated, not stored as a binary blob you cannot edit:

```powershell
.\.venv\Scripts\python tools\make_icon.py
```

That writes `assets/appicon.ico` (every size from 16 to 256 px) and
`assets/appicon-256.png` from the same shape the interface draws in its header
and on the About screen, so the taskbar, the window, the shortcut and the app all
show one mark. That mark — three connected nodes — belongs to the product and to
nothing else; if you change it, change `ICON_PATHS.appmark` in `web/js/dom.js`,
the inline favicon in `web/index.html` and `_glyph_shapes` in `tools/make_icon.py`
together, which is what the UI audit checks. `tools/build_exe.py --resources-only`
regenerates those assets and the version resource without running PyInstaller.

If `pip install` fails with `CERTIFICATE_VERIFY_FAILED`, you are behind a proxy
that re-signs TLS traffic with a root certificate Windows trusts but pip does not
ship. Export the Windows trust store and point pip at it rather than disabling
verification:

```powershell
$pem = "$env:TEMP\win-ca-bundle.pem"
$sb = New-Object System.Text.StringBuilder
foreach ($store in 'Cert:\LocalMachine\Root','Cert:\CurrentUser\Root') {
  Get-ChildItem $store | ForEach-Object {
    [void]$sb.AppendLine('-----BEGIN CERTIFICATE-----')
    [void]$sb.AppendLine([Convert]::ToBase64String($_.RawData,'InsertLineBreaks'))
    [void]$sb.AppendLine('-----END CERTIFICATE-----')
  }
}
[IO.File]::WriteAllText($pem, $sb.ToString())
.\.venv\Scripts\pip install --cert $pem pyinstaller
```

## Moving it to another computer

Copy the source folder, then **create a fresh virtual environment on that
machine** — a copied `.venv` records absolute paths from the machine that built
it and will not run elsewhere:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-native.txt
.\.venv\Scripts\python -m app.ui
```

### If the window opens black or empty

That is the WebView2 build failing to render. In order of effort:

1. **Use the native build** — `python -m app.ui`, or ship
   `dist\ProjectAnalysisNative`. It has no browser engine and cannot hit this.
2. **Install the Edge WebView2 Runtime** on the target machine.
3. **Bundle a runtime with the app.** Download the *WebView2 Fixed Version
   Runtime* (x64) from Microsoft, then extract it into `webview2/` at the
   repository root:

   ```powershell
   expand.exe Microsoft.WebView2.FixedVersionRuntime.<version>.x64.cab -F:* webview2
   ```

   The launcher finds `webview2/…/msedgewebview2.exe`, points
   `WEBVIEW2_BROWSER_EXECUTABLE_FOLDER` and pywebview's own
   `WEBVIEW2_RUNTIME_PATH` at it, and uses that copy regardless of what is
   installed. `ProjectAnalysis.spec` bundles the folder when it exists. It is
   about 290 MB and is **not committed** — `.gitignore` excludes `webview2/`
   and `*.cab`.

Both builds write a log to the data directory
(`%LOCALAPPDATA%\ProjectAnalysis\app.log` and `app-native.log`). A healthy
WebView2 start records the runtime in use, `document loaded` and a boot report;
if `document loaded` never appears, the page never rendered and the cause is the
engine rather than the interface.

An incomplete copy is the other cause: the whole `web` directory has to come
along, including `web/vendor`.

---

## Using it

1. **Projects** - click *Browse…*, pick a folder, give it a name and press *Create*.
2. Press **Analyse**. Progress is shown live; large repositories can be cancelled.
3. **Dashboard** - the score, the weakest areas and the highest-value fixes at a glance.
4. **Scorecard** - the eight categories, what each one measured and why it scored
   what it did. Open a category to read its findings, then click any piece of
   evidence to see the offending source lines.
5. **Improvement plan** - every recommendation ranked by points gained per unit of
   effort, split into quick wins, medium-term and long-term work.
6. **Hotspots** - a treemap and heatmap of the riskiest files.
7. **Guided fixes** - mechanical defects with a diff for each. Review, tick the
   ones you want and press *Apply selected*. See
   [docs/guided-fixes.md](docs/guided-fixes.md) for the safety rules.
8. **Trends** - how the score and each category moved across successive runs.
9. **Diagrams** - the analysis generates every applicable diagram automatically.
   You can also generate one with specific filters, or describe what you want in
   plain language ("show only the authentication architecture").
10. **AI insights** - architecture review, refactoring suggestions and detailed metrics.
11. **Repository history** - hotspots, contributors, temporal coupling and the
    commit graph, read straight from git.
12. **Compare** - pick two completed analyses to see what changed, score included.
13. **Settings** - appearance, the optional AI provider (Ollama, LM Studio, vLLM,
    OpenAI or any other OpenAI-compatible endpoint), the scoring weights, and
    where the data folder and database live.

Press `Ctrl/Cmd + K` for the command palette - it reaches every view, action and
score category from the keyboard. `Ctrl/Cmd + B` toggles the sidebar and
`Shift + ?` lists every shortcut. See [docs/shortcuts.md](docs/shortcuts.md).

Use the language selector to switch between English and Hebrew at any time; the
whole interface flips to RTL for Hebrew. Theme, contrast, chart palette, text
size and reduced motion are all under **Settings → Appearance**.

---

## How the score works

The overall number is a weighted average of eight category scores, each starting
at 100 and losing points to specific, named findings:

| Category | Default weight | Examples of what it measures |
| --- | --- | --- |
| Architecture | 20% | dependency cycles, layering violations, coupling, abstraction |
| Security | 20% | hardcoded secrets, injection risks, weak crypto, unsafe deserialisation |
| Code quality | 15% | complex functions, oversized files, long lines, duplication signals |
| Maintainability | 12% | god classes, instability, change hotspots, ownership risk |
| Testing | 12% | test-to-source ratio, assertion density, untested modules, CI config |
| Documentation | 8% | README, licence, module and symbol documentation |
| Performance | 8% | queries in loops, unindexed foreign keys, fan-out |
| Technical debt | 5% | TODO/FIXME markers, deprecated APIs |

Nothing is a black box: each deduction carries its severity, the points lost, the
reason it matters, how to fix it and the evidence behind it. The scorecard also
reports a **potential score** - what the codebase would reach if every
recommendation on the improvement plan were carried out.

Weights are yours to change under **Settings → Score weights**. Saving new weights
re-scores stored analyses without re-parsing anything, and the values live in
`scoring_weights.json` inside the data folder. *Reset* restores the defaults above.

---

## Where your data lives

| Platform | Location |
| --- | --- |
| Windows | `%LOCALAPPDATA%\ProjectAnalysis` |
| macOS | `~/Library/Application Support/ProjectAnalysis` |
| Linux | `$XDG_DATA_HOME/ProjectAnalysis` (or `~/.local/share/...`) |

That folder holds `platform.sqlite3`, the saved knowledge graphs, cloned
repositories, a cache, and `secret.key` - the Fernet key used to encrypt your AI
API key at rest. The exact path is shown under **Settings → Storage**.

To move or reset everything, just delete or copy that one folder.

---

## Configuration (optional)

Copy `.env.example` to `.env` if you want to override defaults - for example to
restrict which folders may be analysed:

```ini
AAI_ALLOWED_LOCAL_ROOTS=C:\work,C:\src
```

Set `AAI_DEBUG=true` to open the window with developer tools attached.

In a packaged build, put the `.env` next to `ProjectAnalysis.exe`. A shortcut
sets its own working directory, so that copy is read regardless of where the
application was launched from, and it takes precedence over one in the working
directory.

---

## Project layout

```
app/
  analyzers/   language plugins (one per language + infra + SQL)
  ingest/      source resolution, git clone/update, file walking
  engine/      the analysis pipeline, semantic enrichment and the quality scanner
  graph/       knowledge graph model, architecture metrics and the scorecard
  diagrams/    the nine diagram generators and their registry
  history/     git history analysis
  ai/          OpenAI-compatible provider, prompts and insights
  export/      Mermaid, PlantUML, Markdown, HTML, Draw.io, JSON exporters
  compare/     architecture diffing
  services/    analysis orchestration and AI provider resolution
  desktop/     the WebView2 window and the shared, toolkit-agnostic Api
  ui/          the native interface (PySide6)
    theme.py     design tokens and the Qt stylesheet built from them
    i18n.py      reads web/i18n/*.js, so both interfaces share one set of strings
    icons.py     reads ICON_PATHS from web/js/dom.js, so both share one icon set
    charts.py    gauge, donut, radar, bar and line, drawn with QPainter
    diagram.py   layered graph layout in a QGraphicsView, with zoom and pan
    score_band.py the overall-score block shared by the dashboard and scorecard
    palette.py   the command palette
    motion.py    animation timings ported from the CSS transitions
    workers.py   runs Api calls off the UI thread
    views/       one module per screen
  core/        background jobs and caching
  branding.py  name, version, author and icon - one source for the window,
               the About screen and the executable's version resource
web/
  index.html   the WebView2 UI, loaded from disk with file://
  css/         the design system: tokens, components and layouts
  js/          plain scripts (no bundler, no modules, no CDN)
               app.js views, score.js the scorecard, charts.js the SVG toolkit,
               palette.js the command palette, dom.js the element helpers
  i18n/        English and Hebrew string bundles, read by both interfaces
  tests/       the in-browser suite, run inside a real webview
  vendor/      a local copy of Mermaid
assets/        the generated application icon
tools/         the icon generator, the release build and the static audits
tests/
```

## Tests

```powershell
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest
```

Two audits guard the interfaces:

```powershell
.\.venv\Scripts\python tools\audit_ui.py        # the WebView2 UI
.\.venv\Scripts\python tools\audit_i18n.py      # every string key, both languages
.\.venv\Scripts\python tools\audit_ui_keys.py   # the native UI's keys and placeholders
```

To compare the two interfaces screen by screen, capture both and read the pairs
in `build/compare/`:

```powershell
.\.venv\Scripts\python tools\compare_ui.py --target both
```

See [docs/testing.md](docs/testing.md) for the static audits and the in-browser
JS suite. Full documentation lives in [docs/](docs/README.md).

## Architecture notes

`app/desktop/bridge.py` holds the whole application API — one method per
operation, each returning `{ok, data}` or `{ok, error}` so a backend failure can
never surface as a blank screen. It imports no UI toolkit: the only calls that
need a window are the two file dialogs, which sit behind a `FileDialogHost`
protocol that each front end implements. That is what lets the two interfaces
share everything else.

The WebView2 UI runs from `file://`, where browsers block ES modules and
`fetch()`, so it is deliberately built from plain `<script>` tags with everything
on `window.AAI`, crossing to Python through `window.pywebview.api`.

The native UI calls the same methods directly, from a thread pool. Results are
marshalled back through a `QObject` that lives on the UI thread — connecting a
worker signal straight to a plain function gives a *direct* connection, which
would run the callback on the worker thread and leave the widget unpainted.


Every chart is hand-drawn SVG rather than a charting library: gauges, radars,
lines, bars, donuts, treemaps and heatmaps all come from `web/js/charts.js`, which
keeps the app dependency-free, printable and legible at any zoom level. Labels are
never truncated: a chart wraps long names and then grows its own viewBox around
whatever it drew, so a category the reader cannot name is impossible by
construction rather than by careful sizing. The window chrome is mounted once and
only the content region re-renders, so navigating between views never restarts an
animation or steals focus.
#   P r o j e c t A n a l y s i s 
 
 #   P r o j e c t A n a l y s i s 
 
 