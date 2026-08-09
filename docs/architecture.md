# Architecture

A single-process desktop application. There is no server, no port and no network
requirement.

```
app/__main__.py          starts the pywebview window
  desktop/window.py      native window, state persistence, menu
  desktop/bridge.py      every JS-callable endpoint (@endpoint -> {ok, data|error})
  services/              orchestration used by the bridge
  engine/pipeline.py     the analysis stages
  ingest/                source resolution (local folder / git clone) and file walking
  analyzers/             per-language extraction into the knowledge graph
  graph/                 the graph model, metrics and the 0-100 scoring
  diagrams/              diagram generators, one module per kind
  history/               git history analysis and the commit DAG
  ai/                    provider client, prompts, deterministic fallbacks, fix rules
  export/                exporters (PNG, SVG, PDF, Mermaid, PlantUML, Markdown, ...)
  compare/               architecture diff between two analyses
web/                     the UI: plain scripts, no bundler
tools/                   static audits and the JS test harness runner
```

## The bridge

`app/desktop/bridge.py` is the only surface the UI can reach. Each method is
wrapped by `@endpoint`, which:

- runs the call and returns `{"ok": true, "data": ...}`,
- turns a `BridgeError` into a user-facing `{"ok": false, "error": ...}`,
- turns anything else into `{"ok": false, "error": "ClassName: message"}` and logs it.

Long operations are dispatched to worker threads through `app/core/jobs.py` and
report progress back through the job manager, so the UI thread never blocks.

## The frontend

The UI is loaded from `file://`, which blocks ES modules and `fetch`. Everything
is therefore a plain `<script>` loaded in dependency order from
[web/index.html](../web/index.html), attaching to the single `window.AAI`
namespace:

| Script | Responsibility |
| --- | --- |
| `i18n/en.js`, `i18n/he.js` | translation tables |
| `js/i18n.js` | `t()`, language switching, `applyStatic()` |
| `js/dom.js` | element helpers, icons, modals, cards, tables, toasts |
| `js/api.js` | one function per bridge endpoint |
| `js/palette.js` | command palette |
| `js/charts.js` | hand-drawn SVG charts |
| `js/gitgraph.js` | virtualised commit DAG renderer |
| `js/score.js` | scorecard widgets |
| `js/viewer.js` | diagram viewer (zoom, pan, search, fullscreen, export) |
| `js/app.js` | the shell, routing and every view |

Adding a script means adding it to `index.html` **before** `app.js`.

## Styling rules

`web/css/app.css` is a token-based system. Two rules matter:

- **Logical properties only.** `inset-inline-start`, `padding-inline`,
  `margin-block` - never `left`/`right`. This is what makes the Hebrew RTL layout
  work without a second stylesheet.
- **Themable through attributes**, not classes: `[data-theme]`, `[data-contrast]`,
  `[data-palette]`, `[data-motion]` and the `--ui-scale` variable. Anything the
  Appearance settings can toggle must be consumed by a CSS rule; `tools/audit_ui.py`
  checks that.
- **Two deliberate exceptions to the RTL flip.** A diff pane and a chart canvas
  both pin themselves to `direction: ltr`. Diff text is source code, and chart
  geometry is computed left to right - inheriting `rtl` reverses what
  `text-anchor: start` means and throws labels off the canvas. Chart text still
  carries `unicode-bidi: plaintext`, so a Hebrew label reads right to left inside
  a left-to-right canvas.

## Charts

Every chart is hand-drawn SVG in `js/charts.js`. The rule the code is built
around is that a label is never shortened: `wrapLabel()` breaks a long name on
word boundaries, `textBlock()` emits one `<tspan>` per line plus a `<title>` with
the full text, and `fitContent()` then grows the `viewBox` to the union of the
nominal size and the real `getBBox()`.

That last step is what makes the guarantee testable. Because the SVG scales with
its container, anything inside the viewBox survives any window size, DPI or zoom,
so "can this label be clipped?" reduces to "is its box inside the viewBox?" - one
measurement, asserted in `web/tests/charts.test.js`. `fitContent()` re-runs on
`document.fonts.ready` (text measured against a fallback font reports the wrong
width) and returns itself as `chart.refit()` for callers that cannot wait for an
animation frame.

The single exception is a treemap tile, which is a hard physical bound: a name
too long for its tile is shortened there and carried in full as a tooltip.

## Branding and the release build

`app/branding.py` is the only place the product name, version, author, copyright
and icon path are written down. The window title, the About screen, the taskbar
identity and the executable's Windows version resource all read from it, so they
cannot drift apart. `tools/make_icon.py` draws `assets/appicon.ico` from the same
shape the interface renders in its header, and `tools/build_exe.py` regenerates
both that icon and the version resource before invoking PyInstaller.

That shape is `ICON_PATHS.appmark` — three connected nodes — and it is reserved
for the product. It used to be `layers`, which is also the Diagrams navigation
entry, the Architecture score category, the instability card and the sidebar
toggle; the toggle sits immediately beside the brand plate, so the top bar
opened showing the same glyph twice, once in the muted button colour and once
in white on the blue plate. An identity mark has to be a shape nothing else in
the interface uses. The path exists in three places that must stay in step —
`web/js/dom.js`, the inline favicon in `web/index.html` and `_glyph_shapes` in
`tools/make_icon.py` — and `tools/audit_ui.py` compares a segment of it across
all three.

## Adding a view

1. Write `viewX(host)` in `js/app.js`.
2. Register it in the `VIEWS` map and add an entry to `NAV`.
3. Add `nav.x` and any other keys to **both** `i18n/en.js` and `i18n/he.js`.
4. Run `tools/audit_i18n.py` to confirm the two tables still match.

## Adding an endpoint

1. Add an `@endpoint` method to `Bridge`.
2. Add the matching one-liner to `web/js/api.js`.
3. Cover the Python side in `tests/`. The bridge itself is covered by
   `tests/test_bridge.py`.

## Determinism

Every AI-backed capability has a static fallback in `app/ai/insights.py`, and the
fix engine in `app/ai/fixes.py` has no AI path at all. The application is fully
functional - in both languages - with no provider configured.
