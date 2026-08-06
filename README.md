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

## Requirements

- Python 3.10 or newer
- Windows: the **Microsoft Edge WebView2 Runtime**. Windows 11 and current
  Windows 10 builds already have it; older or locked-down images may not. Without
  it the window falls back to the Internet Explorer engine, which cannot run this
  interface — see *Moving it to another computer* below.
- macOS / Linux: the system webview (WebKit) that pywebview uses
- `git` on the PATH, if you want to analyse git repositories or history

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

On macOS / Linux:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Run

```powershell
.\.venv\Scripts\python -m app
```

A native window opens. Nothing is served over HTTP and no port is opened.

## Building a Windows executable

The release build is one command. It regenerates the application icon, writes a
Windows version resource from `app/branding.py`, and packages the application
with no console window:

```powershell
.\.venv\Scripts\pip install pyinstaller
.\.venv\Scripts\python tools\build_exe.py
```

The result is `dist\ProjectAnalysis\ProjectAnalysis.exe` next to an `_internal`
folder holding the interpreter, the UI and the icon. Ship the whole
`dist\ProjectAnalysis` folder - the executable will not start on its own. It
needs no Python installation on the target machine, but it still needs the
**Microsoft Edge WebView2 Runtime**, and it will tell you so if it is missing.

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
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m app
```

If the window opens dark and empty, it will say why after a few seconds. The two
causes it reports are a missing WebView2 runtime (install it and start the
application again) and an incomplete copy — the whole `web` directory has to come
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

Press `Ctrl/Cmd + Shift + P` for the command palette - it reaches every view,
action and score category from the keyboard. `Ctrl/Cmd + B` toggles the sidebar
and `Shift + ?` lists every shortcut.

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

Weights are yours to change under **Settings → Scoring**. Saving new weights
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
  desktop/     the native window and the JavaScript <-> Python bridge
  core/        background jobs and caching
  branding.py  name, version, author and icon - one source for the window,
               the About screen and the executable's version resource
web/
  index.html   the UI, loaded from disk with file://
  css/         the design system: tokens, components and layouts
  js/          plain scripts (no bundler, no modules, no CDN)
               app.js views, score.js the scorecard, charts.js the SVG toolkit,
               palette.js the command palette, dom.js the element helpers
  i18n/        English and Hebrew string bundles
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

See [docs/testing.md](docs/testing.md) for the static audits and the in-browser
JS suite. Full documentation lives in [docs/](docs/README.md).

## Architecture notes

The UI runs inside a native webview loaded from `file://`. Browsers block ES
modules and `fetch()` for `file://` documents, so the front end is deliberately
built from plain `<script>` tags with everything exposed on `window.AAI`, and all
data crosses to Python through `window.pywebview.api`. Each bridge method runs on a
worker thread and always resolves to `{ok, data}` or `{ok, error}`, so a backend
failure surfaces as a toast instead of a blank screen.

Every chart is hand-drawn SVG rather than a charting library: gauges, radars,
lines, bars, donuts, treemaps and heatmaps all come from `web/js/charts.js`, which
keeps the app dependency-free, printable and legible at any zoom level. Labels are
never truncated: a chart wraps long names and then grows its own viewBox around
whatever it drew, so a category the reader cannot name is impossible by
construction rather than by careful sizing. The window chrome is mounted once and
only the content region re-renders, so navigating between views never restarts an
animation or steals focus.
#   P r o j e c t A n a l y s i s  
 