# Troubleshooting

## The window opens blank or white

The webview failed to load the UI.

- **Windows**: install the **WebView2 runtime**. It ships with Windows 10/11 but
  can be missing on stripped-down images.
- **Offline / locked-down machines**: ship the runtime *inside* the app instead of
  relying on the target having it. Download the **WebView2 Fixed Version Runtime**
  (x64) from the [Microsoft WebView2 download page](https://developer.microsoft.com/microsoft-edge/webview2/),
  extract it, and drop the extracted `Microsoft.WebView2.FixedVersionRuntime.<ver>.x64`
  folder (or its contents) into a `webview2/` folder at the repository root, then
  rebuild the EXE. The launcher detects `webview2/msedgewebview2.exe`, points
  `WEBVIEW2_BROWSER_EXECUTABLE_FOLDER` at it, and uses that runtime regardless of
  what is installed on the machine. Leave the folder out and the app falls back to
  the machine's own runtime, so nothing changes for normal installs.
- Run with `AAI_DEBUG=true` in `.env` to attach developer tools and read the
  console error.
- A blank screen after an update usually means a script was added to
  `web/index.html` *after* `js/app.js`, or not at all. Load order matters: the UI
  runs from `file://` with no module system, so every script must appear before
  the one that uses it.

## "This project is not a git repository"

History and the commit graph need a repository and `git` on the PATH. Check with
`git --version` in the same shell you launch the app from.

The folder does **not** have to be the top of the checkout. Detection asks git
itself (`git rev-parse --show-toplevel`), so a project pointed at `repo/backend`,
or at any folder inside a monorepo, still finds its history. It used to test for
a literal `.git` entry, which is why analysing a subdirectory left Repository
History permanently empty.

If the repository exists but has no commits yet, the page says so in your own
language instead of showing git's `fatal: your current branch 'master' does not
have any commits yet` next to a Retry button that could never succeed.

A "not a repository" answer is no longer cached, so a project that gains a
repository after its first analysis shows its history without being re-analysed.

## The commit graph is empty but history works

The graph keeps merge commits, history does not. An empty graph with a working
history view means `git log` returned nothing for the current ref - most often a
detached HEAD on an empty branch.

## Guided fixes says "Fixes can only be applied to a local project folder"

The project points at a remote git URL. The app analyses those in a throwaway
clone, so edits there would vanish on the next run. Clone the repository yourself
and add it as a **local folder** project.

## Guided fixes says "file changed since the fix was proposed"

Expected, and deliberate. Proposals are pinned to a digest of the file they were
computed from. Press *Apply* again - the view reloads after every apply - or
rescan to get fresh proposals.

## An analysis is slow or seems stuck

- Large repositories hit the file limit and log `file limit ... reached`.
  Narrow the scan with exclude globs on the project.
- Vendored folders (`node_modules`, `.venv`, `target`, `dist`) are skipped by
  default. If yours is named something else, exclude it explicitly.
- Analyses run on a worker thread and can be cancelled from the Analyses view.

## AI features return "static analysis" results

No provider is configured, or the configured one is unreachable. That is a
supported mode - every capability has a deterministic fallback. Check
**Settings → AI provider**; a failed call shows the provider error alongside the
fallback result rather than replacing it.

## Hebrew text looks left-aligned

The whole document flips via `dir="rtl"`, driven by the language selector. If a
single component stays LTR, it is almost always a CSS rule using a physical
property (`left`, `right`, `margin-left`) instead of a logical one
(`inset-inline-start`, `margin-inline-start`). Diff panes are the one deliberate
exception - they are forced LTR because a unified diff is column-oriented.

## Icons render huge

A regression of this exact shape happened once: `.chart svg { width: 100% }`
matched icons nested inside a chart container and stretched an 18px icon to the
panel width. The rule is now `.chart > svg:not(.ico)` and
`web/tests/charts.test.js` measures an icon inside a chart to keep it that way.

## Chart lines stop partway across the plot

Also a fixed regression: the draw-in animation used a guessed dash length, so the
dash pattern repeated and left the line permanently segmented. `charts.js` now
measures the polyline and clears the dash on `transitionend`. If you see it
again, check that `polylineLength()` is still being used instead of a constant.

## A chart label is cut short or missing

It should not be possible. Charts wrap long names rather than eliding them, then
call `fitContent()` to grow the viewBox around everything they drew, so a label is
safe at any window size, DPI or zoom. Two regressions are worth knowing about:

- Axis labels used to be cut at 15 characters, which printed the Category Balance
  category "Maintainability" as `Maintainabilit…`. If shortening reappears, look
  for a `slice()` in a label path; `tools/audit_ui.py` fails the build for an
  ellipsis anywhere in `charts.js` outside the treemap.
- In Hebrew the SVG inherited `direction: rtl`, which reverses what
  `text-anchor: start` means, and labels ran backwards off the left edge - a
  treemap filename was measured at x = -22 in a viewBox starting at 0. The canvas
  now pins itself with `.chart > svg:not(.ico) { direction: ltr }` while
  `unicode-bidi: plaintext` keeps each label reading in its own direction.

A treemap tile is the one exception: it is a hard physical bound, so a name too
long for its tile is shortened inside the tile and carried in full as a tooltip.

One more trap, fixed: `fitContent()` gave up when the SVG was not yet in the
document and never tried again. Every view builds its chart *before* attaching
it, so the fit silently never ran and only a manual `refit()` rescued it - which
no view calls. It now fires from a microtask, an animation frame, font loading
and a `ResizeObserver`, and `web/tests/labels.test.js` measures charts along the
real path (build, panel, append, never refit) to keep it honest.

## A diagram is off-centre in Hebrew

Fixed. The rendered diagram is a block box with an explicit pixel width, and
under `direction: rtl` the browser resolves the over-constrained margin equation
by ignoring `margin-left`, so the box sits flush against the **right** edge of
the canvas. The viewer centres arithmetically -
`offset.x = (stageWidth - width * scale) / 2`, applied as a `translate()` with
`transform-origin: 0 0` - which assumes the box starts at the left, so every
diagram narrower than the stage was pushed a further `stageWidth - width` pixels
sideways. Wide diagrams looked fine, which is why it only affected *some*.

`.viewer-stage, .viewer-canvas { direction: ltr }` gives the transform the same
frame of reference in both languages; the diagram's own labels still read
correctly through `unicode-bidi: plaintext`. `web/tests/viewer.test.js` measures
the rendered box against the stage centre line in both directions.

## Resetting everything

Delete the data folder shown under **Settings → Storage**. It holds the database,
stored graphs, clones, the cache and `secret.key`. Deleting it loses every stored
analysis and the encrypted AI key, and nothing else.
