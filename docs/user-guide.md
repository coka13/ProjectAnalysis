# User guide

## 1. Add a project

**Projects → Browse…**, pick a folder, name it, *Create*. You can also point at a
git URL, which the app clones into its own data folder.

If `AAI_ALLOWED_LOCAL_ROOTS` is set, only folders under those roots can be added.

## 2. Analyse

Press **Analyse** (or `Mod + Enter`). Progress is live and the run can be
cancelled. An analysis walks the repository, extracts a knowledge graph, computes
metrics, scores the codebase and generates every applicable diagram.

Re-analysing keeps the previous run, which is what makes **Trends** and
**Compare** work.

## 3. Read the score

**Dashboard** gives you the number, the weakest categories and the highest-value
work. **Scorecard** shows all eight categories and, for each one, exactly what was
measured and which findings cost points. Click a piece of evidence to see the
offending lines.

The **potential score** is what the codebase would reach if the whole improvement
plan were completed.

## 4. Work the improvement plan

**Improvement plan** ranks every recommendation by points gained per unit of
effort and splits it into quick wins, medium-term and long-term work. Each entry
carries why it matters, how to do it and which files are involved.

## 5. Find the risky files

**Hotspots** draws a treemap sized by lines of code and coloured by risk, plus a
heatmap and a sortable, filterable table. Risk combines findings, churn and size,
so a large file that nobody touches ranks below a small one that everybody does.

## 6. Apply guided fixes

**Guided fixes** lists mechanical defects with a unified diff for each. Tick what
you want and press *Apply selected*; you will be asked to confirm, and only then
is anything written.

Commit or stash first - the app has no undo of its own. Full rules in
[guided-fixes.md](guided-fixes.md).

## 7. Explore the diagrams

**Diagrams** holds every generated diagram: architecture, component, class,
dependency, sequence, data flow, database/ER, deployment and state. You can also
generate one with explicit filters, or describe what you want in plain language.

In the viewer: wheel to zoom, drag to pan, and buttons for fit, centre, reset,
search and fullscreen. Export to PNG, SVG, PDF, Mermaid, PlantUML, Draw.io or JSON.

## 8. Read the repository's history

**History** shows commit activity over time, change hotspots, contributors,
temporal coupling (files that keep changing together) and a **commit graph** with
branches, merges and tags. The graph is virtualised, so a repository with
thousands of commits stays responsive.

## 9. Compare two analyses

**Compare** picks any two completed runs and reports what was added, removed and
changed - components, relationships, metrics and the score.

## 10. Make it yours

**Settings → Appearance**: theme, high contrast, chart palette (including a
colour-blind-safe one), text size and reduced motion.

**Settings → Scoring**: the eight category weights. Saving re-scores stored
analyses without re-parsing anything. *Reset* restores the defaults.

**Settings → AI provider**: optional. See [ai-configuration.md](ai-configuration.md).

## Language

The selector in the top bar switches between English and Hebrew instantly, and
the whole interface flips to right-to-left for Hebrew.

## Keyboard

`Mod + Shift + P` opens the command palette, which reaches every view, action and
score category. `Shift + ?` lists every shortcut. Full table in
[shortcuts.md](shortcuts.md).
