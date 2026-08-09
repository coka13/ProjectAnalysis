# ProjectAnalysis

A local desktop app that reads any codebase on your machine, builds a knowledge
graph of it, and produces architecture diagrams, metrics, and (optionally)
AI-written explanations.

No server, no login, and no telemetry. The UI, database, and diagram renderer
run fully offline. Remote Git clones and an optional AI provider are the only
features that talk to the network, and only when you use them.

---

## Features

- **Connect** to a local folder or a git repo (cloned for you)
- **Analyse** Python, JavaScript/TypeScript, Java, C#, Go, Rust, C/C++, plus
  Docker, Compose, Kubernetes, and SQL
- **Knowledge graph** of modules, classes, functions, routes, tables, services,
  and their relationships
- **9 diagram types**: architecture, component, class, dependency, sequence,
  data flow, database/ER, deployment, and state
- **Architecture metrics**: cycles, god classes, coupling, layering, patterns
- **Score out of 100** across eight weighted categories — every deduction links
  back to file and line
- **Git history**: hotspots, ownership, temporal coupling, commit graph
- **Guided fixes** with unified diffs you review before applying
- **Compare** two analyses for architectural drift
- **English and Hebrew** (full RTL)
- **Export** to PNG, SVG, PDF, Mermaid, PlantUML, Markdown, HTML, Draw.io, JSON

---

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-webview.txt   # or requirements-native.txt
.\.venv\Scripts\python -m app                              # WebView2 UI
# .\.venv\Scripts\python -m app.ui                       # native UI (no WebView2)
```

A desktop window opens. Nothing is served over HTTP.

| | WebView2 | Native |
|---|---|---|
| Run | `python -m app` | `python -m app.ui` |
| Script | `projectanalysis` | `projectanalysis-native` |
| Needs | Edge WebView2 Runtime | PySide6 only |
| Spec | `ProjectAnalysis.spec` | `ProjectAnalysisNative.spec` |

Prefer **native** when the machine has no WebView2, or the WebView window opens
black/empty. Both builds share the same analysis, scoring, diagram, and export
code — only the window differs. See [docs/](docs/README.md) for full guides.

---

## Requirements

- Python 3.10+
- `git` on PATH (for repos and history)
- **WebView2 build**: Microsoft Edge WebView2 Runtime (Windows 10/11 usually
  already have it)
- **Native build**: nothing beyond PySide6

---

## Install

```powershell
python -m venv .venv

# WebView2 interface
.\.venv\Scripts\pip install -r requirements-webview.txt

# Native interface (no WebView2) — may be installed alongside
.\.venv\Scripts\pip install -r requirements-native.txt
```

On macOS / Linux, use `./.venv/bin/pip` instead of `.\.venv\Scripts\pip`.

---

## Run

```powershell
.\.venv\Scripts\python -m app        # WebView2
.\.venv\Scripts\python -m app.ui     # native
```

---

## Build a Windows executable

```powershell
.\.venv\Scripts\pip install pyinstaller

.\.venv\Scripts\python tools\build_exe.py --target webview   # default
.\.venv\Scripts\python tools\build_exe.py --target native
.\.venv\Scripts\python tools\build_exe.py --target both
```

Output lives under `dist\ProjectAnalysis\` or `dist\ProjectAnalysisNative\`.
Ship the whole folder (exe + `_internal`); the exe will not run alone.

Version, name, and icon come from `app/branding.py`. Regenerate the icon with:

```powershell
.\.venv\Scripts\python tools\make_icon.py
```

`tools/build_exe.py --resources-only` refreshes assets and the version resource
without running PyInstaller.

<details>
<summary>If pip fails with <code>CERTIFICATE_VERIFY_FAILED</code></summary>

You are likely behind a proxy that re-signs TLS. Export the Windows trust store
and point pip at it:

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

</details>

---

## Move to another computer

Copy the source folder, then create a **fresh** venv on that machine (a copied
`.venv` keeps absolute paths and will not work):

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-native.txt
.\.venv\Scripts\python -m app.ui
```

### Black or empty window (WebView2)

1. Use the native build: `python -m app.ui`
2. Install the Edge WebView2 Runtime
3. Bundle a Fixed Version Runtime into `webview2/` at the repo root (ignored by
   git; ~290 MB). The launcher and `ProjectAnalysis.spec` pick it up when present.

Logs: `%LOCALAPPDATA%\ProjectAnalysis\app.log` (WebView2) and `app-native.log`
(native). A healthy WebView2 start logs `document loaded`.

---

## Using it

1. **Projects** — Browse…, pick a folder, name it, Create
2. **Analyse** — progress is live; large repos can be cancelled
3. **Dashboard** — score, weak areas, highest-value fixes
4. **Scorecard** — eight categories with evidence at file:line
5. **Improvement plan** — ranked by points per unit of effort
6. **Hotspots** — treemap and heatmap of risky files
7. **Guided fixes** — review diffs, apply selected
   ([safety rules](docs/guided-fixes.md))
8. **Trends** — score movement across runs
9. **Diagrams** — auto-generated or filtered / described in plain language
10. **AI insights** — optional architecture review and suggestions
11. **Repository history** — hotspots, contributors, commit graph
12. **Compare** — architectural drift between two analyses
13. **Settings** — appearance, AI provider, score weights, storage path

Shortcuts: `Ctrl/Cmd+K` command palette · `Ctrl/Cmd+B` sidebar · `Shift+?` help.
See [docs/shortcuts.md](docs/shortcuts.md). Language (EN/HE) and appearance are
under Settings.

---

## How the score works

Overall score is a weighted average of eight categories (each starts at 100):

| Category | Default weight | Measures |
| --- | --- | --- |
| Architecture | 20% | cycles, layering, coupling, abstraction |
| Security | 20% | secrets, injection, weak crypto, unsafe deserialisation |
| Code quality | 15% | complexity, oversized files, duplication |
| Maintainability | 12% | god classes, instability, ownership risk |
| Testing | 12% | test ratio, assertion density, CI |
| Documentation | 8% | README, licence, module/symbol docs |
| Performance | 8% | queries in loops, unindexed FKs, fan-out |
| Technical debt | 5% | TODO/FIXME, deprecated APIs |

Each deduction has severity, points lost, why it matters, how to fix it, and
evidence. The scorecard also shows a **potential score** if the improvement plan
were fully applied. Weights live under **Settings → Score weights**
(`scoring_weights.json` in the data folder).

---

## Where your data lives

| Platform | Location |
| --- | --- |
| Windows | `%LOCALAPPDATA%\ProjectAnalysis` |
| macOS | `~/Library/Application Support/ProjectAnalysis` |
| Linux | `$XDG_DATA_HOME/ProjectAnalysis` (or `~/.local/share/...`) |

Contains `platform.sqlite3`, graphs, clones, cache, and `secret.key` (Fernet key
for the AI API key). Path is shown under **Settings → Storage**.

---

## Configuration (optional)

Copy `.env.example` to `.env`:

```ini
AAI_ALLOWED_LOCAL_ROOTS=C:\work,C:\src
AAI_DEBUG=true
```

In a packaged build, put `.env` next to the executable.

---

## Project layout

```
app/
  analyzers/   language plugins
  ingest/      source resolution, git, file walking
  engine/      analysis pipeline, enrichment, quality scanner
  graph/       knowledge graph, metrics, scorecard
  diagrams/    nine diagram generators
  history/     git history analysis
  ai/          OpenAI-compatible provider and insights
  export/      Mermaid, PlantUML, Markdown, HTML, Draw.io, JSON
  compare/     architecture diffing
  services/    orchestration
  desktop/     WebView2 window + shared Api
  ui/          native PySide6 interface
  core/        jobs and caching
  branding.py  name, version, icon
web/           WebView2 UI (plain scripts, no bundler)
assets/        generated application icon
tools/         icon, release build, audits
tests/
```

Both UIs call the same toolkit-agnostic `Api` in `app/desktop/bridge.py`. File
dialogs go through a small `FileDialogHost` protocol each front end implements.

---

## Tests

```powershell
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest

.\.venv\Scripts\python tools\audit_ui.py
.\.venv\Scripts\python tools\audit_i18n.py
.\.venv\Scripts\python tools\audit_ui_keys.py
.\.venv\Scripts\python tools\compare_ui.py --target both
```

More detail: [docs/testing.md](docs/testing.md) · full docs: [docs/](docs/README.md).
