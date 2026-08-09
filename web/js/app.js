/**
 * Application shell.
 *
 * Structure: a persistent chrome (topbar + sidebar) is mounted once, and only
 * the main region is re-rendered when the route or the selection changes. This
 * keeps form focus, chart animations and scroll position stable.
 *
 * Everything runs offline against the pywebview bridge - there is no network.
 */
(function () {
  const AAI = window.AAI;
  const i18n = AAI.i18n;
  const { t } = i18n;
  const {
    el, append, clear, toast, modal, confirmDialog, field, setFieldError, validate,
    badgeFor, fmtDate, list, spinner, icon, card, emptyState, skeleton, tabs,
    progress, drawer, bandClass, deltaLabel,
  } = AAI.dom;
  const { DiagramViewer, refreshTheme } = AAI.viewer;
  const charts = AAI.charts;
  const gitgraph = AAI.gitgraph;
  const palette = AAI.palette;
  const score = AAI.score;
  const api = AAI.api;

  /* ===================================================================== */
  /* preferences                                                            */
  /* ===================================================================== */

  const PREF_KEYS = {
    theme: 'aai.theme',
    contrast: 'aai.contrast',
    palette: 'aai.palette',
    scale: 'aai.scale',
    motion: 'aai.motion',
    sidebar: 'aai.sidebar',
    view: 'aai.view',
    project: 'aai.project',
  };

  function pref(key, fallback) {
    try {
      const value = localStorage.getItem(PREF_KEYS[key]);
      return value === null ? fallback : value;
    } catch {
      return fallback;
    }
  }

  function setPref(key, value) {
    try { localStorage.setItem(PREF_KEYS[key], String(value)); } catch { /* ignore */ }
  }

  function applyPreferences() {
    const root = document.documentElement;
    root.dataset.theme = pref('theme', 'dark');
    root.dataset.contrast = pref('contrast', 'normal');
    root.dataset.palette = pref('palette', 'default');
    root.dataset.motion = pref('motion', 'full');
    root.style.setProperty('--ui-scale', pref('scale', '1'));
  }

  /* ===================================================================== */
  /* state                                                                  */
  /* ===================================================================== */

  const state = {
    info: null,
    projects: [],
    projectId: null,
    analyses: [],
    analysisId: null,
    diagrams: [],
    diagramId: null,
    view: pref('view', 'dashboard'),
    poller: null,
    polling: null,        // analysis id the poller is watching
    starting: false,      // an analysis_start request is in flight
    scorecard: null,      // { scorecard, project, analysis, stats }
    scoreFor: null,       // analysis id the scorecard belongs to
    trend: null,
    trendFor: null,
    files: null,
    filesFor: null,
    fixes: null,          // last Guided Fixes payload
    fixesKey: null,       // analysis|language|cosmetic key the payload belongs to
    fixesCosmetic: false, // remembered "include formatting" toggle
    settingsTab: 'appearance',
  };

  const DIAGRAM_KINDS = [
    'architecture', 'component', 'class', 'dependency',
    'sequence', 'dataflow', 'database', 'deployment', 'state',
  ];

  const NAV = [
    {
      group: 'nav.groupOverview',
      items: [{ id: 'dashboard', icon: 'dashboard', label: 'nav.dashboard' }],
    },
    {
      group: 'nav.groupWorkspace',
      items: [
        { id: 'projects', icon: 'folder', label: 'nav.projects' },
        { id: 'analyses', icon: 'play', label: 'nav.analyses' },
      ],
    },
    {
      group: 'nav.groupQuality',
      items: [
        { id: 'scorecard', icon: 'gauge', label: 'nav.scorecard', needsScore: true },
        { id: 'roadmap', icon: 'sparkle', label: 'nav.roadmap', needsScore: true },
        { id: 'hotspots', icon: 'alert', label: 'nav.hotspots', needsAnalysis: true },
        { id: 'fixes', icon: 'wrench', label: 'nav.fixes', needsAnalysis: true },
        { id: 'trends', icon: 'chart', label: 'nav.trends' },
      ],
    },
    {
      group: 'nav.groupVisual',
      items: [{ id: 'diagrams', icon: 'layers', label: 'nav.diagrams', needsAnalysis: true }],
    },
    {
      group: 'nav.groupIntel',
      items: [
        { id: 'insights', icon: 'report', label: 'nav.insights', needsAnalysis: true },
        { id: 'history', icon: 'history', label: 'nav.history', needsAnalysis: true },
        { id: 'compare', icon: 'compare', label: 'nav.compare' },
      ],
    },
    {
      group: 'nav.groupSystem',
      items: [
        { id: 'settings', icon: 'settings', label: 'nav.settings' },
        { id: 'about', icon: 'info', label: 'nav.about' },
      ],
    },
  ];

  const ALL_NAV = NAV.reduce((acc, group) => acc.concat(group.items), []);
  const navItem = (id) => ALL_NAV.find((item) => item.id === id) || ALL_NAV[0];

  const currentProject = () => state.projects.find((p) => p.id === state.projectId) || null;
  const currentAnalysis = () => state.analyses.find((a) => a.id === state.analysisId) || null;
  const currentDiagram = () => state.diagrams.find((d) => d.id === state.diagramId) || null;
  const analysisReady = () => {
    const run = currentAnalysis();
    return !!(run && run.status === 'succeeded');
  };

  function handle(error) {
    toast(String((error && error.message) || error), 'error');
  }

  /**
   * Wrap an async click handler so the button disables itself and shows a
   * spinner for the duration. Every long-running action in the shell goes
   * through this, which is what stops double submits and dead-looking clicks.
   */
  function busy(handler) {
    return async (event) => {
      const button = event.currentTarget;
      if (!button || button.disabled) return;
      button.disabled = true;
      button.classList.add('busy');
      button.setAttribute('aria-busy', 'true');
      try {
        await handler(event);
      } finally {
        button.disabled = false;
        button.classList.remove('busy');
        button.removeAttribute('aria-busy');
      }
    };
  }

  /* ===================================================================== */
  /* data loading                                                           */
  /* ===================================================================== */

  async function loadProjects() {
    state.projects = await api.projects();
    if (!state.projects.some((p) => p.id === state.projectId)) {
      const remembered = pref('project', '');
      const match = state.projects.find((p) => p.id === remembered);
      state.projectId = (match && match.id) || (state.projects[0] ? state.projects[0].id : null);
    }
    await loadAnalyses();
  }

  async function loadAnalyses() {
    state.analyses = state.projectId ? await api.analyses(state.projectId) : [];
    // A poller belonging to a run that is no longer on screen (the user
    // switched project) would keep ticking and eventually navigate away.
    if (state.polling && !state.analyses.some((a) => a.id === state.polling)) stopPolling();
    if (!state.analyses.some((a) => a.id === state.analysisId)) {
      const done = state.analyses.find((a) => a.status === 'succeeded');
      state.analysisId = (done && done.id) || (state.analyses[0] && state.analyses[0].id) || null;
    }
    invalidateScore();
    await loadDiagrams();
  }

  async function loadDiagrams() {
    state.diagrams = analysisReady() ? await api.diagrams(state.analysisId) : [];
    if (!state.diagrams.some((d) => d.id === state.diagramId)) {
      state.diagramId = state.diagrams[0] ? state.diagrams[0].id : null;
    }
  }

  function invalidateScore() {
    state.scorecard = null;
    state.scoreFor = null;
    state.files = null;
    state.filesFor = null;
    state.trend = null;
    state.trendFor = null;
    invalidateFixes();
  }

  function invalidateFixes() {
    state.fixes = null;
    state.fixesKey = null;
  }

  function fixesCacheKey(includeCosmetic) {
    return `${state.analysisId}|${i18n.language()}|${includeCosmetic ? 1 : 0}`;
  }

  /** Fetch (and memoise) the scorecard for the selected analysis. */
  async function ensureScorecard() {
    if (!analysisReady()) return null;
    if (state.scorecard && state.scoreFor === state.analysisId) return state.scorecard;
    const payload = await api.scoreCard(state.analysisId);
    state.scorecard = payload;
    state.scoreFor = state.analysisId;
    return payload;
  }

  async function ensureTrend() {
    if (state.trend && state.trendFor === state.projectId) return state.trend;
    const payload = await api.scoreTrend({ project_id: state.projectId });
    state.trend = payload;
    state.trendFor = state.projectId;
    return payload;
  }

  async function ensureFiles() {
    if (!analysisReady()) return null;
    if (state.files && state.filesFor === state.analysisId) return state.files;
    const payload = await api.scoreFiles(state.analysisId, 600);
    state.files = payload;
    state.filesFor = state.analysisId;
    return payload;
  }

  /* ===================================================================== */
  /* chrome                                                                 */
  /* ===================================================================== */

  const ui = {};
  const root = document.getElementById('root');

  function mount() {
    clear(root);

    ui.breadcrumbs = el('nav', { class: 'breadcrumbs', 'aria-label': t('a11y.breadcrumb'), 'data-i18n-aria': 'a11y.breadcrumb' });
    ui.projectPicker = el('div', { class: 'inline' });
    ui.runButton = el(
      'button',
      { class: 'btn primary sm', onclick: () => startAnalysis() },
      icon('play', { size: 14 }), el('span', { 'data-i18n': 'analysis.start' }, t('analysis.start')),
    );
    ui.omnibox = el(
      'button',
      {
        class: 'omnibox',
        type: 'button',
        onclick: () => palette.show(),
        'aria-label': t('palette.open'),
        'data-i18n-aria': 'palette.open',
      },
      icon('search', { size: 15 }),
      el('span', { class: 'grow ph truncate', 'data-i18n': 'palette.placeholder' }, t('palette.placeholder')),
      el('kbd', { class: 'kbd' }, isMac() ? '⌘K' : 'Ctrl K'),
    );

    ui.topbar = el(
      'header',
      { class: 'topbar' },
      el(
        'button',
        {
          class: 'btn icon ghost',
          type: 'button',
          'aria-label': t('a11y.toggleSidebar'),
          'data-i18n-aria': 'a11y.toggleSidebar',
          title: t('a11y.toggleSidebar'),
          'data-i18n-title': 'a11y.toggleSidebar',
          onclick: () => toggleSidebar(),
        },
        // A panel, not the product mark: this button used to carry the very
        // same glyph as the brand plate immediately to its right, so the top
        // bar opened showing one icon twice - once grey, once white.
        icon('sidebar', { size: 16 }),
      ),
      el(
        'div',
        { class: 'brand' },
        el('span', { class: 'mark' }, appMark(16)),
        el(
          'div',
          { class: 'titles' },
          el('strong', { 'data-i18n': 'app.title' }, t('app.title')),
          // The top bar has a fixed budget, so the tagline is the one line that
          // may be shortened. It carries its own full text as a tooltip.
          el('span', { class: 'sub', 'data-i18n': 'app.tagline', title: t('app.tagline'), 'data-i18n-title': 'app.tagline' }, t('app.tagline')),
        ),
      ),
      el('span', { class: 'grow' }),
      ui.omnibox,
      ui.projectPicker,
      ui.runButton,
      themeButton(),
      el(
        'button',
        {
          class: 'btn icon ghost',
          type: 'button',
          'aria-label': t('shortcuts.title'),
          'data-i18n-aria': 'shortcuts.title',
          title: t('shortcuts.title'),
          'data-i18n-title': 'shortcuts.title',
          onclick: () => palette.help(),
        },
        icon('info', { size: 16 }),
      ),
    );

    ui.nav = el('nav', { class: 'sidebar', 'aria-label': t('a11y.primaryNav'), 'data-i18n-aria': 'a11y.primaryNav' });
    ui.content = el('main', { class: 'content', id: 'main', tabindex: '-1' });
    ui.main = el('div', { class: 'main' }, ui.breadcrumbs, ui.content);
    ui.shell = el('div', { class: 'shell' }, ui.nav, ui.main);
    root.append(ui.topbar, ui.shell);

    if (pref('sidebar', 'open') === 'collapsed') ui.nav.classList.add('collapsed');
    buildNav();
    registerCommands();
    registerShortcuts();
  }

  function isMac() {
    return /mac/i.test(navigator.platform || '');
  }

  function themeButton() {
    const button = el('button', {
      class: 'btn icon ghost',
      type: 'button',
      'aria-label': t('common.theme'),
      'data-i18n-aria': 'common.theme',
    });
    const paint = () => {
      clear(button).append(document.documentElement.dataset.theme === 'dark' ? '☾' : '☀');
    };
    button.onclick = () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      setPref('theme', next);
      applyPreferences();
      refreshTheme();
      paint();
      renderView();
    };
    paint();
    return button;
  }

  function toggleSidebar() {
    const collapsed = ui.nav.classList.toggle('collapsed');
    setPref('sidebar', collapsed ? 'collapsed' : 'open');
  }

  function buildNav() {
    // The language listener is registered before the shell exists, so a
    // language change that lands during boot would otherwise clear() an
    // undefined node and abort the rest of the start-up.
    if (!ui.nav) return;
    clear(ui.nav);
    NAV.forEach((group) => {
      ui.nav.append(el('div', { class: 'nav-group-title' }, t(group.group)));
      group.items.forEach((item) => {
        const locked = (item.needsAnalysis || item.needsScore) && !analysisReady();
        ui.nav.append(
          el(
            'button',
            {
              class: `nav-item ${state.view === item.id ? 'active' : ''} ${locked ? 'locked' : ''}`,
              type: 'button',
              'aria-current': state.view === item.id ? 'page' : null,
              title: t(item.label),
              onclick: () => navigate(item.id),
            },
            icon(item.icon, { size: 16 }),
            el('span', { class: 'label' }, t(item.label)),
            locked ? icon('info', { size: 12, class: 'dim' }) : null,
          ),
        );
      });
    });
  }

  function buildChrome() {
    if (!ui.breadcrumbs) return;
    // breadcrumbs: project / analysis / view
    clear(ui.breadcrumbs);
    const project = currentProject();
    const run = currentAnalysis();
    const crumbs = [];
    if (project) crumbs.push({ label: project.name, go: () => navigate('projects') });
    if (run) {
      crumbs.push({
        label: `${run.ref || '—'} ${(run.commit_sha || '').slice(0, 7)}`.trim(),
        go: () => navigate('analyses'),
      });
    }
    crumbs.push({ label: t(navItem(state.view).label) });
    crumbs.forEach((crumb, index) => {
      if (index) ui.breadcrumbs.append(el('span', { class: 'sep' }, '/'));
      ui.breadcrumbs.append(
        crumb.go
          ? el('button', { class: 'crumb', type: 'button', onclick: crumb.go }, crumb.label)
          : el('span', { class: 'crumb current', 'aria-current': 'page' }, crumb.label),
      );
    });

    // project + analysis pickers
    clear(ui.projectPicker);
    if (state.projects.length) {
      ui.projectPicker.append(
        el(
          'select',
          {
            class: 'compact',
            'aria-label': t('nav.projects'),
            onchange: async (event) => {
              state.projectId = event.target.value;
              setPref('project', state.projectId);
              state.analysisId = null;
              try { await loadAnalyses(); } catch (error) { handle(error); }
              refresh();
            },
          },
          ...state.projects.map((p) => el('option', { value: p.id, selected: p.id === state.projectId }, p.name)),
        ),
      );
    }
    const succeeded = state.analyses.filter((a) => a.status === 'succeeded');
    if (succeeded.length) {
      ui.projectPicker.append(
        el(
          'select',
          {
            class: 'compact',
            'aria-label': t('nav.analyses'),
            onchange: async (event) => {
              state.analysisId = event.target.value;
              invalidateScore();
              try { await loadDiagrams(); } catch (error) { handle(error); }
              refresh();
            },
          },
          ...succeeded.map((a) =>
            el(
              'option',
              { value: a.id, selected: a.id === state.analysisId },
              `${a.ref || '—'} · ${fmtDate(a.created_at)}`,
            ),
          ),
        ),
      );
    }
    setRunBusy(state.starting);
  }

  function navigate(view) {
    state.view = view;
    setPref('view', view);
    refresh();
    ui.content.scrollTop = 0;
  }

  function refresh() {
    buildNav();
    buildChrome();
    renderView();
  }

  /** Standard page header used by every view. */
  function pageHead(title, subtitle, actions) {
    return el(
      'div',
      { class: 'view-head' },
      el('div', { class: 'titles' }, el('h1', {}, title), subtitle ? el('p', { class: 'sub' }, subtitle) : null),
      el('div', { class: 'inline' }, ...(actions || [])),
    );
  }

  /**
   * Placeholder for a failed load. Always offers a retry so a transient
   * backend hiccup never leaves the user staring at a dead panel.
   */
  function errorState(error, title) {
    return emptyState({
      icon: 'alert',
      title: title || t('common.error'),
      body: String((error && error.message) || error || ''),
      action: el(
        'button',
        { class: 'btn primary', onclick: () => renderView() },
        icon('refresh', { size: 14 }),
        t('common.retry'),
      ),
    });
  }

  function needsAnalysisState(host) {
    host.append(
      emptyState({
        icon: 'play',
        title: t('empty.noAnalysis'),
        body: t('empty.noAnalysisHint'),
        action: state.projectId
          ? el('button', { class: 'btn primary', onclick: () => startAnalysis() }, t('analysis.start'))
          : el('button', { class: 'btn primary', onclick: () => navigate('projects') }, t('project.create')),
      }),
    );
  }

  const VIEWS = {
    dashboard: viewDashboard,
    projects: viewProjects,
    analyses: viewAnalyses,
    scorecard: viewScorecard,
    roadmap: viewRoadmap,
    hotspots: viewHotspots,
    fixes: viewFixes,
    trends: viewTrends,
    diagrams: viewDiagrams,
    insights: viewInsights,
    history: viewHistory,
    compare: viewCompare,
    settings: viewSettings,
    about: viewAbout,
  };

  /** Live commit-graph handle, torn down whenever the view is replaced. */
  let activeGraph = null;

  function releaseGraph() {
    if (activeGraph) {
      activeGraph.destroy();
      activeGraph = null;
    }
  }

  function renderView() {
    releaseGraph();
    clear(ui.content);
    (VIEWS[state.view] || viewDashboard)(ui.content);
    i18n.applyStatic(root);
  }

  /* ===================================================================== */
  /* view: dashboard                                                        */
  /* ===================================================================== */

  function viewDashboard(host) {
    host.append(
      pageHead(t('dash.title'), t('dash.subtitle'), [
        el('button', { class: 'btn', onclick: () => navigate('scorecard') }, t('dash.openScorecard')),
      ]),
    );

    if (!state.projects.length) {
      host.append(
        emptyState({
          icon: 'folder',
          title: t('empty.noProject'),
          body: t('empty.noProjectHint'),
          action: el('button', { class: 'btn primary', onclick: () => navigate('projects') }, t('project.create')),
        }),
      );
      return;
    }
    if (!analysisReady()) {
      needsAnalysisState(host);
      host.append(recentRuns());
      return;
    }

    const board = el('div', { class: 'stack-lg' }, skeleton('block'), skeleton('line', 3));
    host.append(board);

    Promise.all([ensureScorecard(), ensureTrend().catch(() => null)])
      .then(([payload, trend]) => {
        const scorecard = payload.scorecard;
        const points = (trend && trend.points ? trend.points : []).map((p) => p.overall);
        clear(board).append(
          score.hero(scorecard, { trend: points }),
          el(
            'div',
            { class: 'grid grid-2' },
            score.radarPanel(scorecard, (id) => score.openExplorer(state.analysisId, id)),
            statsPanel(payload.stats),
          ),
          score.categoryCards(scorecard, (id) => score.openExplorer(state.analysisId, id)),
          el(
            'div',
            { class: 'grid grid-2' },
            score.topIssues(scorecard, (id) => score.openExplorer(state.analysisId, id)),
            quickWinsPreview(scorecard),
          ),
          recentRuns(),
        );
        i18n.applyStatic(board);
      })
      .catch((error) => {
        clear(board).append(
          emptyState({
            icon: 'alert',
            title: t('score.unavailable'),
            body: String(error.message || error),
            action: el('button', { class: 'btn', onclick: () => startAnalysis() }, t('analysis.start')),
          }),
        );
      });
  }

  function statsPanel(stats) {
    const languages = Object.entries(stats.languages || {}).map(([name, count]) => ({ label: name, value: count }));
    const donut = charts.donut({
      items: languages,
      size: 190,
      center: String(stats.files_analyzed || 0),
      centerSub: t('analysis.files'),
    });
    return card(t('dash.composition'), { icon: 'code' },
      el(
        'div',
        { class: 'inline wrap', style: 'gap:20px' },
        donut.node,
        el(
          'div',
          { class: 'grow' },
          el('div', { class: 'kpi-grid' },
            el('div', { class: 'kpi-tile' }, el('b', {}, String(stats.node_count || 0)), el('span', {}, t('analysis.nodes'))),
            el('div', { class: 'kpi-tile' }, el('b', {}, String(stats.edge_count || 0)), el('span', {}, t('analysis.edges'))),
            el('div', { class: 'kpi-tile' }, el('b', {}, `${Math.round(stats.duration_seconds || 0)}s`), el('span', {}, t('analysis.duration')))),
          el('div', { class: 'legend', style: 'margin-top:12px' },
            ...languages.slice(0, 8).map((item, index) =>
              el('span', {},
                el('i', { style: `background:${charts.PALETTE[index % charts.PALETTE.length]}` }),
                `${item.label} · ${item.value}`))),
        ),
      ));
  }

  function quickWinsPreview(scorecard) {
    const wins = (scorecard.roadmap && scorecard.roadmap.quick_wins) || [];
    return card(t('score.quickWins'), {
      icon: 'sparkle',
      actions: [el('button', { class: 'btn sm ghost', onclick: () => navigate('roadmap') }, t('common.viewAll'))],
    },
      wins.length
        ? el('div', { class: 'stack' }, ...wins.slice(0, 3).map(score.actionRow))
        : emptyState({ icon: 'check', title: t('score.noQuickWins') }));
  }

  function recentRuns() {
    const rows = state.analyses.slice(0, 5);
    return card(t('dash.recentRuns'), {
      icon: 'history',
      actions: [el('button', { class: 'btn sm ghost', onclick: () => navigate('analyses') }, t('common.viewAll'))],
    },
      rows.length
        ? el('div', { class: 'stack-sm' }, ...rows.map((run) =>
            el(
              'button',
              {
                type: 'button',
                class: `list-item ${run.id === state.analysisId ? 'active' : ''}`,
                style: 'width:100%;text-align:start',
                onclick: async () => {
                  state.analysisId = run.id;
                  invalidateScore();
                  try { await loadDiagrams(); } catch (error) { handle(error); }
                  refresh();
                },
              },
              el('span', { class: `badge ${badgeFor(run.status)}` }, t(`status.${run.status}`)),
              el('div', { class: 'main' },
                el('div', { class: 'mono small' }, `${run.ref || '—'} ${(run.commit_sha || '').slice(0, 8)}`),
                el('div', { class: 'small muted' }, fmtDate(run.created_at))),
              run.status === 'running' ? progress(run.progress, false) : null,
            )))
        : emptyState({ icon: 'inbox', title: t('analysis.empty') }));
  }

  /* ===================================================================== */
  /* view: projects                                                         */
  /* ===================================================================== */

  function viewProjects(host) {
    host.append(
      pageHead(t('nav.projects'), t('project.subtitle'), [
        el('button', { class: 'btn primary', onclick: () => projectDialog() }, icon('plus', { size: 14 }), t('project.create')),
      ]),
    );

    if (!state.projects.length) {
      host.append(
        emptyState({
          icon: 'folder',
          title: t('empty.noProject'),
          body: t('empty.noProjectHint'),
          action: el('button', { class: 'btn primary', onclick: () => projectDialog() }, t('project.create')),
        }),
      );
      return;
    }

    host.append(
      el(
        'div',
        { class: 'card-grid' },
        ...state.projects.map((project) => {
          const latest = project.latest_analysis;
          return el(
            'article',
            { class: `card project-card ${project.id === state.projectId ? 'selected' : ''}` },
            el('div', { class: 'inline' },
              icon('folder', { size: 16 }),
              el('h3', { class: 'grow truncate' }, project.name),
              latest ? el('span', { class: `badge ${badgeFor(latest.status)}` }, t(`status.${latest.status}`)) : null),
            el('p', { class: 'small muted mono truncate', title: project.source_location },
              `${project.source_kind === 'git' ? t('project.sourceGit') : t('project.sourceLocal')}: ${project.source_location}`),
            latest
              ? el('p', { class: 'small muted' }, `${t('project.lastRun')}: ${fmtDate(latest.created_at)}`)
              : el('p', { class: 'small dim' }, t('project.neverRun')),
            el(
              'div',
              { class: 'inline', style: 'margin-top:auto;padding-top:12px' },
              el(
                'button',
                {
                  class: 'btn sm primary',
                  onclick: async () => {
                    state.projectId = project.id;
                    setPref('project', project.id);
                    await startAnalysis();
                  },
                },
                icon('play', { size: 13 }), t('project.analyze'),
              ),
              el(
                'button',
                {
                  class: 'btn sm',
                  onclick: async () => {
                    state.projectId = project.id;
                    setPref('project', project.id);
                    state.analysisId = null;
                    try { await loadAnalyses(); } catch (error) { handle(error); }
                    navigate('dashboard');
                  },
                },
                t('project.open'),
              ),
              el('span', { class: 'grow' }),
              el(
                'button',
                {
                  class: 'btn sm icon danger ghost',
                  'aria-label': t('common.delete'),
                  onclick: () => confirmDialog(t('project.deleteConfirm'), async () => {
                    try {
                      await api.deleteProject(project.id);
                      if (state.projectId === project.id) state.projectId = null;
                      await loadProjects();
                      refresh();
                    } catch (error) { handle(error); }
                  }),
                },
                icon('trash', { size: 14 }),
              ),
            ),
          );
        }),
      ),
    );
  }

  function projectDialog() {
    const name = el('input', { type: 'text', autofocus: true, maxlength: '160' });
    const gitHint = el('p', { class: 'small muted span-2', hidden: true }, t('project.gitNetworkHint'));
    const kind = el(
      'select',
      {
        onchange: () => {
          const local = kind.value === 'local';
          browse.style.display = local ? '' : 'none';
          location.placeholder = local ? 'C:\\path\\to\\project' : 'https://github.com/owner/repo.git';
          gitHint.hidden = local;
          setFieldError(location, '');
        },
      },
      el('option', { value: 'local' }, t('project.sourceLocal')),
      el('option', { value: 'git' }, t('project.sourceGit')),
    );
    const location = el('input', { type: 'text', placeholder: 'C:\\path\\to\\project', required: true });
    const browse = el(
      'button',
      {
        class: 'btn',
        type: 'button',
        onclick: async () => {
          try {
            const picked = await api.pickFolder();
            if (!picked.path) return;
            location.value = picked.path;
            setFieldError(location, '');
            if (!name.value.trim()) {
              name.value = picked.path.split(/[\\/]/).filter(Boolean).pop() || '';
              setFieldError(name, '');
            }
          } catch (error) { handle(error); }
        },
      },
      t('project.browse'),
    );
    const ref = el('input', { type: 'text', placeholder: 'main' });
    const exclude = el('input', { type: 'text', placeholder: 'tests/**, docs/**' });

    // Clear a validation message as soon as the user starts correcting it.
    [name, location].forEach((input) =>
      input.addEventListener('input', () => setFieldError(input, '')));

    modal(
      t('project.create'),
      el(
        'div',
        { class: 'form-grid' },
        field(t('project.name'), name, { required: true }),
        field(t('project.sourceKind'), kind),
        gitHint,
        el(
          'div',
          { class: 'field span-2' },
          el('label', {}, t('project.location'), el('span', { class: 'req', 'aria-hidden': 'true' }, '*')),
          el('div', { class: 'input-row' }, location, browse),
        ),
        field(`${t('project.ref')} (${t('common.optional')})`, ref),
        field(t('project.exclude'), exclude, { hint: t('project.excludeHint') }),
      ),
      [
        { label: t('common.cancel'), onClick: (close) => close() },
        {
          label: t('common.create'),
          primary: true,
          onClick: async (close) => {
            const ok = validate([
              { input: name, message: t('common.required') },
              { input: location, message: t('common.required') },
            ]);
            if (!ok) return undefined;
            try {
              const created = await api.createProject({
                name: name.value.trim(),
                source_kind: kind.value,
                source_location: location.value.trim(),
                default_ref: ref.value.trim(),
                exclude_globs: exclude.value.split(',').map((s) => s.trim()).filter(Boolean),
              });
              close();
              state.projectId = created.id;
              setPref('project', created.id);
              await loadProjects();
              refresh();
              toast(t('common.saved'), 'success');
            } catch (error) {
              setFieldError(location, String((error && error.message) || error));
            }
            return undefined;
          },
        },
      ],
    );
  }

  /* ===================================================================== */
  /* view: analyses                                                         */
  /* ===================================================================== */

  async function startAnalysis() {
    if (!state.projectId) {
      navigate('projects');
      return;
    }
    // Starting an analysis is not idempotent, so the request has to be
    // single-flighted: the button stays live during the round trip and an
    // impatient double click would otherwise queue a second full run.
    if (state.starting) return;
    state.starting = true;
    setRunBusy(true);
    try {
      const run = await api.startAnalysis({
        project_id: state.projectId,
        generate_diagrams: true,
        include_history: true,
      });
      state.analysisId = run.id;
      invalidateScore();
      state.view = 'analyses';
      setPref('view', 'analyses');
      await loadAnalyses();
      refresh();
      pollAnalysis(run.id);
    } catch (error) {
      handle(error);
    } finally {
      state.starting = false;
      setRunBusy(false);
    }
  }

  function setRunBusy(busy) {
    if (!ui.runButton) return;
    ui.runButton.disabled = busy || !state.projectId;
    ui.runButton.classList.toggle('busy', busy);
  }

  function stopPolling() {
    if (state.poller) clearInterval(state.poller);
    state.poller = null;
    state.polling = null;
  }

  function pollAnalysis(id) {
    stopPolling();
    state.polling = id;
    // Snapshot of what the analyses view last rendered, so a poll tick that
    // brings no news does not tear down and rebuild the DOM (which would drop
    // hover, focus and restart the progress animation).
    let signature = '';
    state.poller = setInterval(async () => {
      try {
        const run = await api.analysis(id);
        const index = state.analyses.findIndex((a) => a.id === id);
        if (index >= 0) state.analyses[index] = run;
        else state.analyses.unshift(run);
        if (['succeeded', 'failed', 'cancelled'].includes(run.status)) {
          stopPolling();
          invalidateScore();
          await loadDiagrams();
          toast(t(`status.${run.status}`), run.status === 'succeeded' ? 'success' : 'error');
          if (run.status === 'succeeded') navigate('dashboard');
          else refresh();
          return;
        }
        const next = `${run.status}|${run.stage || ''}|${Math.round((run.progress || 0) * 100)}`;
        if (state.view === 'analyses' && next !== signature) {
          signature = next;
          renderView();
        }
      } catch (error) {
        stopPolling();
        handle(error);
      }
    }, 1200);
  }

  function viewAnalyses(host) {
    host.append(
      pageHead(t('nav.analyses'), t('analysis.subtitle'), [
        el('button', { class: 'btn primary', onclick: () => startAnalysis(), disabled: !state.projectId || state.starting },
          icon('play', { size: 14 }), t('analysis.start')),
      ]),
    );

    if (!state.projectId) {
      host.append(emptyState({ icon: 'folder', title: t('empty.noProject'), body: t('empty.noProjectHint') }));
      return;
    }

    const selectRun = async (run) => {
      state.analysisId = run.id;
      invalidateScore();
      try { await loadDiagrams(); } catch (error) { handle(error); }
      refresh();
    };

    host.append(
      card(null, {},
        list(
          state.analyses,
          (run) =>
            el(
              'div',
              {
                // A row is a real control: it needs a role, a tab stop and
                // Enter/Space handling, otherwise the whole analyses list is
                // unreachable without a mouse.
                class: `list-item run ${run.id === state.analysisId ? 'active' : ''}`,
                role: 'button',
                tabindex: '0',
                'aria-pressed': run.id === state.analysisId ? 'true' : 'false',
                'aria-label': `${t(`status.${run.status}`)} ${run.ref || ''} ${fmtDate(run.created_at)}`,
                onclick: () => selectRun(run),
                onkeydown: (event) => {
                  if (event.target !== event.currentTarget) return;
                  if (event.key !== 'Enter' && event.key !== ' ') return;
                  event.preventDefault();
                  selectRun(run);
                },
              },
              el(
                'div',
                { class: 'main' },
                el('div', { class: 'inline' },
                  el('span', { class: `badge ${badgeFor(run.status)}` }, t(`status.${run.status}`)),
                  el('span', { class: 'mono small' }, run.ref || '—'),
                  el('span', { class: 'mono small dim' }, (run.commit_sha || '').slice(0, 8)),
                  el('span', { class: 'small muted' }, fmtDate(run.created_at))),
                run.status === 'running' || run.status === 'pending'
                  ? el('div', { style: 'margin-top:8px' },
                      progress(run.progress || 0, run.status === 'pending'),
                      el('div', { class: 'small muted', style: 'margin-top:4px' }, run.stage || ''))
                  : null,
                run.error ? el('div', { class: 'small', style: 'color:var(--danger)' }, run.error) : null,
              ),
              run.status === 'running' || run.status === 'pending'
                ? el(
                    'button',
                    {
                      class: 'btn sm danger',
                      onclick: busy(async (event) => {
                        event.stopPropagation();
                        try { await api.cancelAnalysis(run.id); } catch (error) { handle(error); }
                      }),
                    },
                    t('analysis.cancel'),
                  )
                : el(
                    'button',
                    {
                      class: 'btn sm icon danger ghost',
                      'aria-label': t('common.delete'),
                      onclick: (event) => {
                        event.stopPropagation();
                        confirmDialog(t('analysis.delete'), async () => {
                          try {
                            await api.deleteAnalysis(run.id);
                            if (state.analysisId === run.id) state.analysisId = null;
                            await loadAnalyses();
                            refresh();
                          } catch (error) { handle(error); }
                        });
                      },
                    },
                    icon('trash', { size: 14 }),
                  ),
            ),
          t('analysis.empty'),
        )),
    );

    const active = state.analyses.find((a) => a.status === 'running' || a.status === 'pending');
    if (active && !state.poller) pollAnalysis(active.id);
  }

  /* ===================================================================== */
  /* view: scorecard                                                        */
  /* ===================================================================== */

  function viewScorecard(host) {
    host.append(
      pageHead(t('nav.scorecard'), t('score.subtitle'), [
        el('button', { class: 'btn', onclick: () => weightsDialog() }, icon('settings', { size: 14 }), t('score.weights')),
        el('button', { class: 'btn', onclick: busy(() => exportScoreReport()) }, icon('download', { size: 14 }), t('score.exportReport')),
      ]),
    );
    if (!analysisReady()) { needsAnalysisState(host); return; }

    const body = el('div', { class: 'stack-lg' }, skeleton('block'), skeleton('line', 4));
    host.append(body);

    ensureScorecard()
      .then((payload) => {
        const scorecard = payload.scorecard;
        clear(body).append(
          score.hero(scorecard, {}),
          score.categoryCards(scorecard, (id) => score.openExplorer(state.analysisId, id)),
          el('div', { class: 'grid grid-2' }, score.radarPanel(scorecard, (id) => score.openExplorer(state.analysisId, id)), score.contributionPanel(scorecard)),
          el('div', { class: 'grid grid-2' }, score.topIssues(scorecard, (id) => score.openExplorer(state.analysisId, id)), score.strengths(scorecard)),
          methodologyCard(scorecard),
        );
        i18n.applyStatic(body);
      })
      .catch((error) => {
        clear(body).append(errorState(error, t('score.unavailable')));
      });
  }

  function methodologyCard(scorecard) {
    return card(t('score.method'), { icon: 'info' },
      el('p', { class: 'small muted' }, t('score.methodBody')),
      el(
        'table',
        {},
        el('thead', {}, el('tr', {},
          el('th', {}, t('score.category')),
          el('th', {}, t('score.weight')),
          el('th', {}, t('score.score')),
          el('th', {}, t('score.contribution')),
          el('th', {}, t('score.confidence')))),
        el('tbody', {}, ...scorecard.categories.map((category) =>
          el('tr', {},
            el('td', {}, score.catLabel(category.id)),
            el('td', { class: 'tabular' }, `${category.weight_pct}%`),
            el('td', { class: 'tabular' }, `${category.score} (${category.grade})`),
            el('td', { class: 'tabular' }, String(category.contribution)),
            el('td', { class: 'tabular' }, `${Math.round(category.confidence * 100)}%`)))),
        el('tfoot', {}, el('tr', {},
          el('td', {}, t('score.overallTitle')),
          el('td', { class: 'tabular' }, '100%'),
          el('td', { class: 'tabular' }, `${scorecard.overall} (${scorecard.grade})`),
          el('td', { class: 'tabular' }, String(scorecard.overall_exact)),
          el('td', { class: 'tabular' }, `${Math.round(scorecard.confidence * 100)}%`))),
      ));
  }

  function weightsDialog() {
    modal(t('score.weights'), score.weightsEditor(async () => {
      try {
        const result = await api.scoreRecompute(state.analysisId);
        state.scorecard = Object.assign({}, state.scorecard, { scorecard: result.scorecard });
        renderView();
      } catch (error) { handle(error); }
    }), [{ label: t('common.close'), primary: true, onClick: (close) => close() }]);
  }

  async function exportScoreReport() {
    try {
      const payload = await ensureScorecard();
      const report = buildScoreMarkdown(payload);
      const saved = await api.saveText(`architecture-score-${payload.analysis.id.slice(0, 8)}.md`, report);
      if (saved.saved) toast(t('common.savedTo', { path: saved.path }), 'success');
    } catch (error) { handle(error); }
  }

  function buildScoreMarkdown(payload) {
    const card = payload.scorecard;
    const lines = [
      `# ${payload.project.name} — ${t('score.overallTitle')}`,
      '',
      `**${card.overall}/100 (${card.grade})** — ${card.headline}`,
      '',
      `- ${t('score.potential')}: ${card.potential_score}`,
      `- ${t('score.confidence')}: ${Math.round(card.confidence * 100)}%`,
      `- ${t('analysis.files')}: ${payload.stats.files_analyzed}`,
      '',
      `## ${t('score.category')}`,
      '',
      `| ${t('score.category')} | ${t('score.score')} | ${t('score.weight')} | ${t('score.issues')} |`,
      '| --- | ---: | ---: | ---: |',
    ];
    card.categories.forEach((category) => {
      lines.push(`| ${score.catLabel(category.id)} | ${category.score} (${category.grade}) | ${category.weight_pct}% | ${category.issue_count} |`);
    });
    lines.push('', `## ${t('score.roadmap')}`, '');
    ((card.roadmap && card.roadmap.all) || []).forEach((action) => {
      lines.push(`### ${action.rank}. ${action.title} (+${action.overall_gain})`);
      lines.push('', `- ${t('score.category')}: ${score.catLabel(action.category)}`);
      lines.push(`- ${t('score.effort')}: ${action.effort}`);
      lines.push(`- ${t('score.whyItMatters')}: ${action.why}`);
      lines.push(`- ${t('score.howToFix')}: ${action.how}`);
      if ((action.files || []).length) lines.push(`- ${t('score.files')}: ${action.files.join(', ')}`);
      lines.push('');
    });
    return lines.join('\n');
  }

  /* ===================================================================== */
  /* view: roadmap                                                          */
  /* ===================================================================== */

  function viewRoadmap(host) {
    host.append(pageHead(t('nav.roadmap'), t('score.roadmapSubtitle'), []));
    if (!analysisReady()) { needsAnalysisState(host); return; }

    const body = el('div', { class: 'stack-lg' }, skeleton('line', 6));
    host.append(body);

    ensureScorecard()
      .then((payload) => {
        const scorecard = payload.scorecard;
        const plan = scorecard.roadmap || {};
        const projection = el(
          'div',
          { class: 'chart-frame' },
          el('div', { class: 'chart-head' },
            el('h4', {}, t('score.projection')),
            el('span', { class: 'small dim' }, t('score.projectionHint'))),
          el(
            'div',
            { class: 'mag-list projection-list' },
            magnitudeRow({
              label: t('score.current'),
              value: scorecard.overall,
              max: 100,
              display: `${scorecard.overall} / 100`,
              color: charts.scoreColor(scorecard.overall),
            }, 0),
            magnitudeRow({
              label: t('score.afterQuickWins'),
              value: Math.min(100, Math.round(scorecard.overall + sumGain(plan.quick_wins))),
              max: 100,
              display: `${Math.min(100, Math.round(scorecard.overall + sumGain(plan.quick_wins)))} / 100`,
              color: 'var(--info)',
            }, 1),
            magnitudeRow({
              label: t('score.afterAll'),
              value: scorecard.potential_score,
              max: 100,
              display: `${scorecard.potential_score} / 100`,
              color: 'var(--ok)',
            }, 2),
          ),
        );
        clear(body).append(
          card(null, {}, projection),
          score.roadmap(scorecard),
        );
        i18n.applyStatic(body);
      })
      .catch((error) => {
        clear(body).append(errorState(error, t('score.unavailable')));
      });
  }

  function sumGain(items) {
    return (items || []).reduce((total, item) => total + (item.overall_gain || 0), 0);
  }

  /* ===================================================================== */
  /* view: hotspots                                                         */
  /* ===================================================================== */

  function viewHotspots(host) {
    host.append(pageHead(t('nav.hotspots'), t('hotspots.subtitle'), []));
    if (!analysisReady()) { needsAnalysisState(host); return; }

    const body = el('div', { class: 'stack-lg' }, skeleton('block'), skeleton('line', 5));
    host.append(body);

    ensureFiles()
      .then((payload) => {
        const files = payload.files || [];
        if (!files.length) {
          clear(body).append(emptyState({ icon: 'inbox', title: t('hotspots.empty') }));
          return;
        }
        const maxRisk = Math.max(...files.map((f) => f.risk)) || 1;
        const colorFor = (item) => {
          const intensity = Math.min(1, (item.risk || 0) / maxRisk);
          return `color-mix(in srgb, var(--danger) ${Math.round(intensity * 88)}%, var(--info))`;
        };
        const map = charts.treemap({
          items: files.slice(0, 120).map((file) => ({
            label: file.file,
            short: file.file.split('/').pop(),
            value: file.loc,
            risk: file.risk,
            hint: `${t('hotspots.risk')} ${file.risk} · ${file.loc} ${t('hotspots.loc')}`,
          })),
          width: 900,
          height: 380,
          colorFor,
          onSelect: (item) => fileDrawer(files.find((f) => f.file === item.label)),
        });
        const heat = charts.heatmap({
          cells: files.slice(0, 240).map((file) => ({
            label: file.file,
            value: file.risk,
            hint: `${file.findings} ${t('hotspots.findings')} · ${file.changes} ${t('hotspots.changes')}`,
          })),
          width: 900,
          onSelect: (cell) => fileDrawer(files.find((f) => f.file === cell.label)),
        });

        clear(body).append(
          el('div', { class: 'kpi-grid' },
            el('div', { class: 'kpi-tile' }, el('b', {}, String(payload.total_files)), el('span', {}, t('analysis.files'))),
            el('div', { class: 'kpi-tile' }, el('b', {}, charts.fmt(payload.total_loc)), el('span', {}, t('hotspots.loc'))),
            el('div', { class: 'kpi-tile' }, el('b', {}, String(files.filter((f) => f.risk_findings > 0).length)), el('span', {}, t('hotspots.risky'))),
            el('div', { class: 'kpi-tile' }, el('b', {}, String(files.filter((f) => f.is_test).length)), el('span', {}, t('hotspots.tests')))),
          card(null, {}, charts.panel(t('hotspots.treemap'), map, { subtitle: t('hotspots.treemapHint') })),
          card(null, {}, charts.panel(t('hotspots.heatmap'), heat, { subtitle: t('hotspots.heatmapHint') })),
          hotspotTable(files),
        );
        i18n.applyStatic(body);
      })
      .catch((error) => {
        clear(body).append(errorState(error));
      });
  }

  function hotspotTable(files) {
    const COLUMNS = [
      { key: 'file', label: t('hotspots.file'), text: true },
      { key: 'risk', label: t('hotspots.risk') },
      { key: 'loc', label: t('hotspots.loc') },
      { key: 'findings', label: t('hotspots.findings') },
      { key: 'debt_markers', label: t('hotspots.debt') },
      { key: 'changes', label: t('hotspots.changes') },
    ];
    const PAGE = 40;

    let sortKey = 'risk';
    let ascending = false;
    let query = '';
    let limit = PAGE;

    const host = el('div');
    const filter = el('input', {
      type: 'search',
      class: 'compact',
      'aria-label': t('hotspots.filter'),
      'data-i18n-placeholder': 'hotspots.filter',
      placeholder: t('hotspots.filter'),
    });
    const summary = el('span', { class: 'small muted', 'aria-live': 'polite' });

    let filterTimer = 0;
    filter.addEventListener('input', () => {
      clearTimeout(filterTimer);
      filterTimer = setTimeout(() => { query = filter.value.trim().toLowerCase(); limit = PAGE; draw(); }, 120);
    });

    const compare = (a, b) => {
      const left = a[sortKey];
      const right = b[sortKey];
      const result = typeof left === 'string' || typeof right === 'string'
        ? String(left || '').localeCompare(String(right || ''))
        : (left || 0) - (right || 0);
      return ascending ? result : -result;
    };

    function draw() {
      const matched = query ? files.filter((file) => file.file.toLowerCase().includes(query)) : files;
      const sorted = matched.slice().sort(compare);
      const rows = sorted.slice(0, limit);
      summary.textContent = t('hotspots.showing', { n: rows.length, total: matched.length });

      const header = (column) =>
        el(
          'th',
          {
            class: 'sortable',
            'aria-sort': sortKey === column.key ? (ascending ? 'ascending' : 'descending') : 'none',
          },
          el(
            'button',
            {
              type: 'button',
              class: 'th-sort',
              // Re-sorting on the same column flips the direction, which is
              // what every desktop grid does.
              onclick: () => {
                if (sortKey === column.key) ascending = !ascending;
                else { sortKey = column.key; ascending = !!column.text; }
                draw();
              },
            },
            column.label,
          ),
        );

      append(
        clear(host),
        el('div', { class: 'inline', style: 'margin-block-end:12px' }, filter, el('span', { class: 'grow' }), summary),
        rows.length
          ? el(
              'div',
              { class: 'table-wrap' },
              el(
                'table',
                {},
                el('thead', {}, el('tr', {}, ...COLUMNS.map(header))),
                el('tbody', {}, ...rows.map((file) =>
                  el(
                    'tr',
                    {
                      class: 'clickable',
                      tabindex: '0',
                      onclick: () => fileDrawer(file),
                      onkeydown: (event) => {
                        if (event.key !== 'Enter' && event.key !== ' ') return;
                        event.preventDefault();
                        fileDrawer(file);
                      },
                    },
                    el('td', { class: 'mono small truncate', title: file.file }, file.file),
                    el('td', { class: 'tabular' }, el('span', { class: `badge ${file.risk > 20 ? 'err' : file.risk > 10 ? 'warn' : ''}` }, String(file.risk))),
                    el('td', { class: 'tabular' }, String(file.loc)),
                    el('td', { class: 'tabular' }, String(file.findings)),
                    el('td', { class: 'tabular' }, String(file.debt_markers)),
                    el('td', { class: 'tabular' }, String(file.changes)),
                  ))),
              ),
            )
          : emptyState({ icon: 'search', title: t('common.noMatches') }),
        rows.length < matched.length
          ? el('div', { class: 'center', style: 'margin-block-start:12px' },
              el('button', { class: 'btn sm', onclick: () => { limit += PAGE; draw(); } }, t('common.showMore')))
          : null,
      );
      i18n.applyStatic(host);
    }
    draw();
    return card(t('hotspots.table'), { icon: 'file' }, host);
  }

  function fileDrawer(file) {
    if (!file) return;
    const view = drawer(file.file);
    const detailHost = el('div', { class: 'stack' }, skeleton('line', 4));
    view.body.append(
      el('div', { class: 'kpi-grid' },
        el('div', { class: 'kpi-tile' }, el('b', {}, String(file.risk)), el('span', {}, t('hotspots.risk'))),
        el('div', { class: 'kpi-tile' }, el('b', {}, String(file.loc)), el('span', {}, t('hotspots.loc'))),
        el('div', { class: 'kpi-tile' }, el('b', {}, String(file.findings)), el('span', {}, t('hotspots.findings'))),
        el('div', { class: 'kpi-tile' }, el('b', {}, String(file.changes)), el('span', {}, t('hotspots.changes')))),
      el('table', {}, el('tbody', {},
        row(t('hotspots.module'), file.module || '—'),
        row(t('hotspots.language'), file.language || '—'),
        row(t('hotspots.comments'), String(file.comment_lines)),
        row(t('hotspots.isTest'), file.is_test ? t('common.yes') : t('common.no')),
        row(t('hotspots.riskFindings'), String(file.risk_findings)),
        row(t('hotspots.debt'), String(file.debt_markers)),
        row(t('hotspots.authors'), String(file.authors || 0)),
        row(t('hotspots.lastChanged'), file.last_changed ? fmtDate(file.last_changed) : '—'))),
      detailHost,
    );

    // The counters above say the file is risky; they do not say why. The scan
    // already recorded a rule, a reason, a fix and a snippet for each finding,
    // so they are fetched and shown rather than left on the server.
    api
      .scoreFileDetail(state.analysisId, file.file)
      .then((detail) => {
        if (!detailHost.isConnected) return;
        clear(detailHost).append(...fileDetailSections(detail));
        i18n.applyStatic(detailHost);
      })
      .catch((error) => {
        if (!detailHost.isConnected) return;
        clear(detailHost).append(el('p', { class: 'sub' }, String(error && error.message ? error.message : error)));
      });

    function row(label, value) {
      return el('tr', {}, el('td', { class: 'muted' }, label), el('td', {}, value));
    }
  }

  function findingEntry(finding) {
    const severity = String(finding.severity || 'info').toLowerCase();
    return el(
      'details',
      { class: 'finding' },
      el('summary', {},
        el('span', { class: `badge ${SEVERITY_TONE[severity] || 'info'}` }, t(`score.priority.${severity}`)),
        el('span', { class: 'grow' }, finding.title || finding.rule || ''),
        finding.line ? el('span', { class: 'mono sub' }, `${t('score.line')} ${finding.line}`) : null),
      finding.why ? el('p', { class: 'sub' }, `${t('hotspots.why')}: ${finding.why}`) : null,
      finding.fix ? el('p', { class: 'sub' }, `${t('hotspots.fix')}: ${finding.fix}`) : null,
      finding.snippet ? el('pre', { class: 'snippet-block' }, el('code', {}, finding.snippet)) : null,
      el('div', { class: 'inline sub' },
        finding.rule ? el('span', { class: 'badge outline mono' }, finding.rule) : null,
        finding.category ? el('span', { class: 'badge outline' }, finding.category) : null),
    );
  }

  function detailSection(title, count, children) {
    if (!children.length) return null;
    return el('section', { class: 'drawer-section' },
      el('h4', {}, `${title} (${count})`),
      el('div', { class: 'stack-sm' }, ...children));
  }

  function fileDetailSections(detail) {
    const sections = [];
    sections.push(
      detailSection(t('hotspots.findingsDetail'), (detail.findings || []).length,
        (detail.findings || []).map(findingEntry)),
    );
    if (detail.findings_truncated) {
      sections.push(el('p', { class: 'sub' },
        t('hotspots.findingsTruncated', { shown: (detail.findings || []).length, total: detail.findings_reported })));
    }
    sections.push(
      detailSection(t('hotspots.debtDetail'), (detail.debt_markers || []).length,
        (detail.debt_markers || []).map((marker) =>
          el('div', { class: 'evidence' },
            el('div', { class: 'grow' },
              el('span', { class: 'badge outline' }, marker.marker || ''),
              el('span', {}, ` ${marker.note || ''}`)),
            marker.line ? el('span', { class: 'mono sub' }, String(marker.line)) : null))),
      detailSection(t('hotspots.complexDetail'), (detail.complex_functions || []).length,
        (detail.complex_functions || []).map((fn) =>
          el('div', { class: 'evidence' },
            el('div', { class: 'grow mono truncate' }, fn.name || ''),
            el('span', { class: 'badge warn' }, `${t('hotspots.complexity')} ${fn.complexity}`),
            fn.line ? el('span', { class: 'mono sub' }, String(fn.line)) : null))),
      detailSection(t('hotspots.wideSignatures'), (detail.wide_signatures || []).length,
        (detail.wide_signatures || []).map((fn) =>
          el('div', { class: 'evidence' },
            el('div', { class: 'grow mono truncate' }, fn.name || ''),
            el('span', { class: 'badge outline' }, t('hotspots.params', { n: fn.params })))),
      ),
      detailSection(t('hotspots.undocumented'), (detail.undocumented || []).length,
        (detail.undocumented || []).map((sym) =>
          el('div', { class: 'evidence' },
            el('div', { class: 'grow mono truncate' }, sym.name || ''),
            el('span', { class: 'badge outline' }, sym.kind || ''))),
      ),
      detailSection(t('hotspots.symbols'), (detail.symbols || []).length,
        (detail.symbols || []).map((sym) =>
          el('div', { class: 'evidence' },
            el('div', { class: 'grow mono truncate' }, sym.name || ''),
            el('span', { class: 'badge outline' }, sym.kind || ''),
            sym.complexity ? el('span', { class: 'badge outline' }, String(sym.complexity)) : null,
            el('span', { class: `badge ${sym.documented ? 'ok' : 'outline'}` },
              sym.documented ? t('hotspots.documented') : t('hotspots.undocumentedShort')))),
      ),
    );
    const present = sections.filter(Boolean);
    return present.length
      ? present
      : [el('p', { class: 'sub' }, t('hotspots.noDetail'))];
  }

  /* ===================================================================== */
  /* view: trends                                                           */
  /* ===================================================================== */

  function viewTrends(host) {
    host.append(pageHead(t('nav.trends'), t('trends.subtitle'), []));
    if (!state.projectId) {
      host.append(emptyState({ icon: 'folder', title: t('empty.noProject') }));
      return;
    }
    const body = el('div', { class: 'stack-lg' }, skeleton('block'));
    host.append(body);

    ensureTrend()
      .then((trend) => {
        clear(body).append(
          score.trendCharts(trend, (index) => {
            const point = trend.points[index];
            if (!point) return;
            state.analysisId = point.analysis_id;
            invalidateScore();
            navigate('scorecard');
          }),
          trendTable(trend),
        );
        i18n.applyStatic(body);
      })
      .catch((error) => {
        clear(body).append(errorState(error));
      });
  }

  function trendTable(trend) {
    const points = (trend.points || []).slice().reverse();
    if (!points.length) return el('span');
    return card(t('trends.runs'), { icon: 'history' },
      el(
        'table',
        {},
        el('thead', {}, el('tr', {},
          el('th', {}, t('trends.when')),
          el('th', {}, t('project.ref')),
          el('th', {}, t('score.score')),
          el('th', {}, t('analysis.files')),
          el('th', {}, ''))),
        el('tbody', {}, ...points.map((point) =>
          el('tr', {},
            el('td', { class: 'small' }, fmtDate(point.at)),
            el('td', { class: 'mono small' }, `${point.ref || '—'} ${point.commit}`),
            el('td', {}, el('span', { class: `badge ${bandClass(point.overall)}` }, `${point.overall} ${point.grade}`)),
            el('td', { class: 'tabular' }, String(point.files)),
            el('td', {}, el('button', {
              class: 'btn sm ghost',
              onclick: async () => {
                state.analysisId = point.analysis_id;
                invalidateScore();
                try { await loadDiagrams(); } catch (error) { handle(error); }
                navigate('scorecard');
              },
            }, t('common.open')))))),
      ));
  }

  /* ===================================================================== */
  /* view: diagrams                                                         */
  /* ===================================================================== */

  function viewDiagrams(host) {
    host.append(
      pageHead(t('nav.diagrams'), t('diagram.subtitle'), [
        el('button', { class: 'btn', onclick: () => generateDialog() }, icon('plus', { size: 14 }), t('diagram.generate')),
        el('button', { class: 'btn', onclick: () => askDialog() }, icon('sparkle', { size: 14 }), t('ai.ask')),
        el('button', { class: 'btn', onclick: busy(() => exportBundle()) }, icon('download', { size: 14 }), t('export.package')),
      ]),
    );
    if (!analysisReady()) { needsAnalysisState(host); return; }

    host.append(
      el(
        'div',
        { class: 'split' },
        el(
          'aside',
          { class: 'panel diagram-list' },
          el('h4', {}, t('nav.diagrams')),
          list(
            state.diagrams,
            (diagram) =>
              el(
                'button',
                {
                  type: 'button',
                  class: `list-item ${diagram.id === state.diagramId ? 'active' : ''}`,
                  style: 'width:100%;text-align:start',
                  onclick: () => { state.diagramId = diagram.id; renderView(); },
                },
                icon('layers', { size: 14 }),
                el('div', { class: 'main' },
                  el('div', { class: 'truncate' }, diagram.title),
                  el('div', { class: 'small muted' }, t(`diagram.${diagram.kind}`))),
                el('span', { class: `badge ${badgeFor(diagram.approval_state)}` }, t(`approval.${diagram.approval_state}`)),
              ),
            t('diagram.empty'),
          ),
        ),
        diagramPanel(),
      ),
    );
  }

  function generateDialog() {
    const kind = el('select', {}, ...DIAGRAM_KINDS.map((k) => el('option', { value: k }, t(`diagram.${k}`))));
    const detail = el('select', {}, ...['executive', 'standard', 'detailed'].map((d) =>
      el('option', { value: d, selected: d === 'standard' }, t(`diagram.detail.${d}`))));
    const maxNodes = el('input', { type: 'number', value: 60, min: 3, max: 400 });
    const focus = el('input', { type: 'text' });
    const external = el('input', { type: 'checkbox' });

    modal(
      t('diagram.generate'),
      el('div', { class: 'stack' },
        el('div', { class: 'row' }, field(t('diagram.kind'), kind), field(t('diagram.detail'), detail)),
        el('div', { class: 'row' }, field(t('diagram.maxNodes'), maxNodes), field(t('diagram.focus'), focus)),
        el('label', { class: 'check' }, external, el('span', {}, t('diagram.includeExternal')))),
      [
        { label: t('common.cancel'), onClick: (close) => close() },
        {
          label: t('diagram.generate'),
          primary: true,
          onClick: async (close) => {
            try {
              const created = await api.generateDiagram({
                analysis_id: state.analysisId,
                kind: kind.value,
                filters: {
                  detail: detail.value,
                  max_nodes: Number(maxNodes.value) || 60,
                  focus: focus.value.trim(),
                  include_external: external.checked,
                  scope: 'project',
                },
              });
              close();
              await loadDiagrams();
              state.diagramId = created.id;
              renderView();
            } catch (error) { handle(error); }
          },
        },
      ],
    );
  }

  function askDialog() {
    const prompt = el('input', { type: 'text', 'data-i18n-placeholder': 'ai.askPlaceholder', autofocus: true });
    prompt.addEventListener('input', () => setFieldError(prompt, ''));
    modal(
      t('ai.ask'),
      el('div', { class: 'stack' }, el('p', { class: 'small muted' }, t('ai.askHint')), el('div', { class: 'field' }, prompt)),
      [
        { label: t('common.cancel'), onClick: (close) => close() },
        {
          label: t('ai.generate'),
          primary: true,
          onClick: async (close) => {
            if (!validate([{ input: prompt, message: t('common.required') }])) return undefined;
            try {
              const result = await api.askForDiagram({
                analysis_id: state.analysisId,
                prompt: prompt.value.trim(),
                language: i18n.language(),
              });
              close();
              await loadDiagrams();
              state.diagramId = result.diagram.id;
              renderView();
              showGeneratedDiagram(result);
            } catch (error) {
              setFieldError(prompt, String((error && error.message) || error));
            }
            return undefined;
          },
        },
      ],
    );
  }

  async function exportBundle() {
    try {
      const bundle = await api.exportBundle({ analysis_id: state.analysisId, language: i18n.language() });
      const saved = await api.saveText(bundle.filename, bundle.content);
      if (saved.saved) toast(t('common.savedTo', { path: saved.path }), 'success');
    } catch (error) { handle(error); }
  }

  function showGeneratedDiagram(result) {
    const box = el('div', { style: 'min-height:420px' });
    const spec = result.spec || {};
    modal(
      spec.title || t('ai.generate'),
      el('div', {},
        el('p', { class: 'small muted' },
          `${t('ai.interpretation')}: ${t(`diagram.${result.diagram.kind}`)} — ${spec.reasoning || ''}`),
        box),
      [{ label: t('common.close'), primary: true, onClick: (close) => { close(); renderView(); } }],
    );
    new DiagramViewer(box).render(result.diagram);
    i18n.applyStatic(box.parentElement);
  }

  function diagramPanel() {
    const diagram = currentDiagram();
    if (!diagram) {
      return el('div', { class: 'panel' }, emptyState({
        icon: 'layers',
        title: t('diagram.empty'),
        body: t('diagram.emptyHint'),
        action: el('button', { class: 'btn primary', onclick: () => generateDialog() }, t('diagram.generate')),
      }));
    }

    const stage = el('div', { class: 'stage' });
    const insightBox = el('div');
    const panel = el(
      'div',
      { class: 'panel diagram-panel' },
      el(
        'div',
        { class: 'inline', style: 'margin-bottom:12px' },
        el('h3', { class: 'grow truncate', style: 'margin:0' }, diagram.title),
        approvalSelect(diagram),
        el(
          'button',
          {
            class: 'btn sm',
            onclick: async (event) => {
              const button = event.currentTarget;
              button.disabled = true;
              clear(insightBox).append(el('p', { class: 'muted small' }, t('ai.thinking')), spinner());
              try {
                const result = await api.explain({ diagram_id: diagram.id, language: i18n.language() });
                clear(insightBox).append(renderExplanation(result));
              } catch (error) { handle(error); clear(insightBox); } finally { button.disabled = false; }
            },
          },
          icon('sparkle', { size: 13 }), t('ai.explain'),
        ),
        exportMenu(diagram),
      ),
      stage,
      (diagram.payload && diagram.payload.notes || []).length
        ? el('div', { class: 'legend' }, ...diagram.payload.notes.map((note) => el('span', {}, note)))
        : null,
      insightBox,
      el('details', {}, el('summary', { class: 'small muted' }, t('diagram.source')), el('pre', {}, diagram.mermaid)),
      commentsSection(diagram),
      versionsSection(diagram),
    );

    queueMicrotask(() => {
      new DiagramViewer(stage).render(diagram);
      i18n.applyStatic(stage);
    });
    return panel;
  }

  function approvalSelect(diagram) {
    return el(
      'select',
      {
        class: 'compact',
        'aria-label': t('diagram.approval'),
        onchange: async (event) => {
          try {
            await api.setApproval(diagram.id, event.target.value);
            diagram.approval_state = event.target.value;
            toast(t('common.saved'), 'success');
            renderView();
          } catch (error) { handle(error); }
        },
      },
      ...['draft', 'in_review', 'approved', 'rejected'].map((s) =>
        el('option', { value: s, selected: s === diagram.approval_state }, t(`approval.${s}`))),
    );
  }

  function exportMenu(diagram) {
    const formats = (state.info && state.info.export_formats) || ['mermaid', 'plantuml', 'markdown', 'html', 'drawio', 'json'];
    const select = el(
      'select',
      { class: 'compact', 'aria-label': t('export.title') },
      el('option', { value: '' }, t('export.title')),
      ...formats.map((f) => el('option', { value: f }, t(`export.${f}`))),
    );
    select.addEventListener('change', async () => {
      const format = select.value;
      select.value = '';
      if (!format) return;
      try {
        const result = await api.exportDiagram({ diagram_id: diagram.id, format, language: i18n.language() });
        const saved = await api.saveText(result.filename, result.content);
        if (saved.saved) toast(t('common.savedTo', { path: saved.path }), 'success');
      } catch (error) { handle(error); }
    });
    return select;
  }

  function commentsSection(diagram) {
    const box = el('div');
    const input = el('input', { type: 'text', 'data-i18n-placeholder': 'diagram.addComment' });
    const reload = async () => {
      try {
        const comments = await api.comments(diagram.id);
        clear(box).append(
          list(
            comments,
            (comment) =>
              el(
                'div',
                { class: 'list-item' },
                el('div', { class: 'main' },
                  el('div', { class: 'small' }, comment.body),
                  el('div', { class: 'small muted' }, fmtDate(comment.created_at))),
                el(
                  'div',
                  { class: 'inline' },
                  el('button', {
                    class: `btn sm ${comment.resolved ? '' : 'primary'}`,
                    onclick: async () => { try { await api.toggleComment(comment.id); reload(); } catch (error) { handle(error); } },
                  }, comment.resolved ? t('common.back') : t('diagram.resolve')),
                  el('button', {
                    class: 'btn sm icon danger ghost',
                    'aria-label': t('common.delete'),
                    onclick: async () => { try { await api.deleteComment(comment.id); reload(); } catch (error) { handle(error); } },
                  }, icon('trash', { size: 13 })),
                ),
              ),
            t('common.none'),
          ),
        );
      } catch (error) { handle(error); }
    };
    reload();

    const submit = async () => {
      const body = input.value.trim();
      if (!setFieldError(input, body ? '' : t('common.required'))) return;
      try {
        await api.addComment(diagram.id, body);
        input.value = '';
        reload();
      } catch (error) { handle(error); }
    };
    input.addEventListener('input', () => setFieldError(input, ''));
    input.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      submit();
    });

    return el(
      'details',
      { ontoggle: () => i18n.applyStatic(box) },
      el('summary', { class: 'small muted' }, t('diagram.comments')),
      box,
      el(
        'div',
        { class: 'row', style: 'margin-top:8px' },
        el('div', { class: 'field grow' }, input),
        el('button', { class: 'btn', onclick: busy(submit) }, t('common.save')),
      ),
    );
  }

  function versionsSection(diagram) {
    const box = el('div');
    const reload = async () => {
      try {
        const versions = await api.versions(diagram.id);
        clear(box).append(
          list(
            versions,
            (version) =>
              el(
                'div',
                { class: 'list-item' },
                el('div', { class: 'main' },
                  el('strong', {}, `v${version.version}`),
                  el('div', { class: 'small muted' }, `${version.note || ''} ${fmtDate(version.created_at)}`)),
                el('button', {
                  class: 'btn sm',
                  onclick: async () => {
                    try { await api.restoreVersion(version.id); await loadDiagrams(); renderView(); }
                    catch (error) { handle(error); }
                  },
                }, t('diagram.restore')),
              ),
            t('common.none'),
          ),
        );
      } catch (error) { handle(error); }
    };
    reload();
    return el('details', {}, el('summary', { class: 'small muted' }, t('diagram.versions')), box);
  }

  /* ===================================================================== */
  /* view: AI insights                                                      */
  /* ===================================================================== */

  function viewInsights(host) {
    host.append(
      pageHead(t('nav.insights'), t('ai.subtitle'), [
        el('button', { class: 'btn primary', onclick: () => runInsight(api.review, renderReview) }, icon('sparkle', { size: 14 }), t('ai.review')),
        el('button', { class: 'btn', onclick: () => runInsight(api.refactor, renderRefactor) }, t('ai.refactor')),
      ]),
    );
    if (!analysisReady()) { needsAnalysisState(host); return; }

    const output = el('div', { class: 'stack-lg' });
    const structure = el('div', { class: 'stack-lg' }, skeleton('line', 5));
    host.append(output, structure);

    api
      .metrics(state.analysisId)
      .then(({ metrics }) => { clear(structure).append(metricsDetail(metrics)); })
      .catch((error) => { clear(structure); handle(error); });

    function runInsight(fn, renderer) {
      clear(output).append(card(null, {}, el('p', { class: 'muted small' }, t('ai.thinking')), spinner()));
      fn({ analysis_id: state.analysisId, language: i18n.language() })
        .then((result) => clear(output).append(renderer(result)))
        .catch((error) => { handle(error); clear(output); });
    }
  }

  function describe(item) {
    if (typeof item === 'string') return item;
    // `issue`, `pattern` and `summary` matter: the deterministic builders emit
    // those, so without them a risk collapsed to a bare "(high)" and a pattern
    // fell through to raw JSON.
    const headline = item.title || item.issue || item.pattern || item.name || item.summary || '';
    const evidence = typeof item.evidence === 'string' ? item.evidence : '';
    const body = item.detail || item.description || evidence || (item.title ? item.issue : '') || item.why || '';
    const severity = item.severity_label || item.severity || '';
    return [headline, body === headline ? '' : body, severity ? `(${severity})` : '']
      .filter(Boolean).join(' — ');
  }

  // Risks are rendered as cards rather than bullets. A severity word on its own
  // is not actionable, so each card states what was measured, why it matters,
  // what it will cost to address and what to do - with the evidence behind it.
  function riskCard(item) {
    if (typeof item === 'string') {
      return el('div', { class: 'panel' }, el('p', { class: 'small' }, item));
    }
    const severity = String(item.severity || '').toLowerCase();
    const label = item.severity_label || (severity ? severity : '');
    const headline = item.title || item.issue || item.name || '';
    const detail = item.title ? (item.issue || item.detail || item.description || '') : '';
    const evidence = (Array.isArray(item.evidence) ? item.evidence : []).filter(Boolean);
    return el(
      'div',
      { class: 'panel' },
      el('div', { class: 'inline' },
        label ? el('span', { class: `badge ${SEVERITY_TONE[severity] || 'outline'}` }, String(label)) : null,
        headline ? el('strong', {}, headline) : null),
      detail ? el('p', { class: 'small' }, detail) : null,
      item.why ? el('p', { class: 'small muted' }, `${t('ai.whyItMatters')}: ${item.why}`) : null,
      item.remediation ? el('p', { class: 'small' }, `${t('ai.remediation')}: ${item.remediation}`) : null,
      el('div', { class: 'inline small muted' },
        item.impact ? el('span', { class: 'badge outline' }, `${t('ai.impact')}: ${item.impact}`) : null,
        item.effort ? el('span', { class: 'badge outline' }, `${t('ai.effort')}: ${item.effort}`) : null,
        item.location ? el('span', { class: 'badge outline mono' }, item.location) : null),
      evidence.length
        ? el('details', {},
            el('summary', { class: 'small' }, t('ai.evidence')),
            el('ul', { class: 'small muted' }, ...evidence.map((line) => el('li', {}, String(line)))))
        : null,
    );
  }

  function riskSection(title, items) {
    if (!items || !items.length) return null;
    return el('div', {}, el('h4', {}, title),
      el('div', { class: 'stack' }, ...items.map(riskCard)));
  }

  function describeComponent(item) {
    if (typeof item === 'string') return item;
    return [item.name, item.role].filter(Boolean).join(' — ');
  }

  function section(title, body) {
    if (!body) return null;
    return el('div', {}, el('h4', {}, title), el('p', {}, String(body)));
  }

  function bulletSection(title, items) {
    if (!items || !items.length) return null;
    // Model output is not guaranteed to be a list of strings, so fall back to
    // the shared formatter rather than dumping raw JSON at the user.
    const lines = items.map((item) => describe(item) || JSON.stringify(item)).filter(Boolean);
    if (!lines.length) return null;
    return el('div', {}, el('h4', {}, title),
      el('ul', {}, ...lines.map((line) => el('li', {}, line))));
  }

  function insightSourceNotice(result) {
    if (!result || result.source === 'ai') return null;
    if (result.warning) {
      return el('div', { class: 'notice warn', role: 'note' },
        icon('alert', { size: 16 }),
        el('p', {}, t('ai.fallbackFailed', { error: result.warning })));
    }
    return el('p', { class: 'small muted' }, t('ai.fallbackNotice'));
  }

  function renderExplanation(result) {
    return el(
      'div',
      { style: 'margin-top:12px' },
      insightSourceNotice(result),
      section(t('ai.purpose'), result.purpose),
      section(t('ai.description'), result.description),
      bulletSection(t('ai.keyComponents'), (result.key_components || []).map(describeComponent)),
      bulletSection(t('ai.patterns'), result.patterns),
      riskSection(t('ai.risks'), result.risks || []),
      bulletSection(t('ai.improvements'), result.improvements),
    );
  }

  function renderReview(result) {
    return card(t('ai.review'), { icon: 'report' },
      insightSourceNotice(result),
      section(t('compare.summary'), result.summary),
      bulletSection(t('ai.strengths'), result.strengths),
      bulletSection(t('ai.issues'), (result.issues || []).map(describe)),
      bulletSection(t('ai.recommendations'), (result.recommendations || []).map(describe)));
  }

  function renderRefactor(result) {
    return card(t('ai.refactor'), { icon: 'wrench' },
      insightSourceNotice(result),
      section(t('compare.summary'), result.summary),
      el('div', { class: 'stack' }, ...(result.suggestions || []).map((item) =>
        el('div', { class: 'panel' },
          el('strong', {}, item.title || item.name || ''),
          el('p', { class: 'small' }, item.detail || item.description || ''),
          el('div', { class: 'inline small muted' },
            item.impact ? el('span', { class: 'badge outline' }, `${t('ai.impact')}: ${item.impact}`) : null,
            item.effort ? el('span', { class: 'badge outline' }, `${t('ai.effort')}: ${item.effort}`) : null),
          item.rationale ? el('p', { class: 'small muted' }, `${t('ai.rationale')}: ${item.rationale}`) : null))));
  }

  /* ------------------------------------------------- architecture metrics */

  const METRIC_BADGE = { good: 'ok', watch: 'warn', bad: 'err', neutral: 'info' };

  function verdictLabel(tone) {
    if (tone === 'good') return t('metrics.verdictGood');
    if (tone === 'watch') return t('metrics.verdictWatch');
    if (tone === 'bad') return t('metrics.verdictBad');
    return t('metrics.verdictNeutral');
  }

  /**
   * A metric that explains itself.
   *
   * A bare count ("Dependency cycles: 3") only means something to a reader who
   * already knows the vocabulary, so every tile carries its definition, a
   * verdict, and a sentence about what this particular value says.
   */
  function metricTile(spec) {
    return el(
      'div',
      { class: 'metric-tile' },
      el('div', { class: 'metric-head' },
        el('b', {}, String(spec.value)),
        el('span', { class: `badge ${METRIC_BADGE[spec.tone] || 'info'}` }, verdictLabel(spec.tone))),
      el('span', { class: 'metric-name' }, spec.label),
      el('p', { class: 'metric-reading' }, spec.reading),
      el('p', { class: 'metric-hint' }, spec.hint),
    );
  }

  /**
   * Place a 0..1 value on a labelled track.
   *
   * "0.62" is meaningless on its own; against both ends of the scale it reads
   * at a glance.
   */
  function ratioScale(value, lowLabel, highLabel) {
    const ratio = Math.max(0, Math.min(1, Number(value) || 0));
    return el(
      'div',
      { class: 'scale' },
      el('div', { class: 'scale-track' },
        el('div', { class: 'scale-fill', style: `width:${(ratio * 100).toFixed(1)}%` }),
        el('div', { class: 'scale-pin', style: `inset-inline-start:${(ratio * 100).toFixed(1)}%` },
          el('span', { class: 'scale-value' }, ratio.toFixed(2)))),
      el('div', { class: 'scale-ends' },
        el('span', {}, lowLabel),
        el('span', {}, highLabel)),
    );
  }

  /* ---------------------------------------------------- findings rendering */

  /**
   * A findings group with a heading, a count and a reason to care.
   *
   * The body is a diagram rather than a bullet list: a dependency cycle is a
   * shape, and a list of arrows written as text makes the reader rebuild that
   * shape in their head.
   */
  function findingGroup(spec) {
    if (!spec.body) return null;
    return el(
      'section',
      { class: 'finding-group' },
      el('header', { class: 'finding-head' },
        icon(spec.icon, { size: 16 }),
        el('h4', { class: 'grow' }, spec.title),
        el('span', { class: `badge ${spec.tone || 'info'}` }, String(spec.count))),
      el('p', { class: 'small muted' }, spec.why),
      spec.body,
    );
  }

  /**
   * One dependency cycle, drawn as the loop it actually is.
   *
   * The first module is repeated at the end behind a return arrow, because that
   * is the whole point of a cycle and a plain "a -> b -> c" list hides it.
   */
  function cycleChain(modules, index) {
    const parts = [];
    modules.forEach((name, position) => {
      if (position) parts.push(el('span', { class: 'chain-arrow', 'aria-hidden': 'true' }, '→'));
      parts.push(el('span', { class: 'chain-node' }, name));
    });
    parts.push(el('span', { class: 'chain-arrow back', 'aria-hidden': 'true' }, '↩'));
    parts.push(el('span', { class: 'chain-node repeat' }, modules[0]));
    return el(
      'div',
      {
        class: 'chain',
        style: `--i:${index}`,
        'aria-label': t('metrics.cycleAria', { chain: modules.join(' → '), first: modules[0] }),
      },
      ...parts,
    );
  }

  /** A labelled bar whose length is the value relative to the worst case. */
  function magnitudeRow(spec, index) {
    const share = Math.max(4, Math.round((spec.value / (spec.max || 1)) * 100));
    const fillStyle = `--mag:${share}%${spec.color ? `;background:${spec.color}` : ''}`;
    return el(
      'div',
      { class: 'mag-row', style: `--i:${index}` },
      el('span', { class: 'mag-label truncate', title: spec.label }, spec.label),
      el('span', { class: 'mag-track' },
        el('span', { class: `mag-fill tone-${spec.tone || 'watch'}`, style: fillStyle })),
      el('span', { class: 'mag-value' }, spec.display),
    );
  }

  /** A dependency that runs against the intended direction. */
  function violationRow(source, target, index) {
    return el(
      'div',
      { class: 'chain wrong', style: `--i:${index}` },
      el('span', { class: 'chain-node' }, source),
      el('span', { class: 'chain-arrow bad', 'aria-hidden': 'true' }, '↯'),
      el('span', { class: 'chain-node' }, target),
    );
  }

  function metricsDetail(metrics) {
    const cycles = metrics.cycles || [];
    const gods = metrics.god_classes || [];
    const coupling = (metrics.coupling || []).slice(0, 12);
    const violations = metrics.layering_violations || [];
    const modules = Number(metrics.module_count) || 0;
    const abstraction = Math.round((Number(metrics.abstraction_ratio) || 0) * 100);
    const average = Number(metrics.average_instability) || 0;

    const instability = charts.bars({
      items: coupling.map((row) => ({
        label: row.module || row.name,
        value: Number(row.instability) || 0,
        hint: t('metrics.fanHint', { inbound: row.fan_in ?? 0, outbound: row.fan_out ?? 0 }),
      })),
      max: 1,
      width: 700,
    });

    // Three plain readings instead of one number the reader has to interpret.
    const averageTone = average <= 0.35 ? 'good' : average <= 0.65 ? 'neutral' : 'watch';
    const averageReading = average <= 0.35
      ? t('metrics.instabilityStable')
      : average <= 0.65
        ? t('metrics.instabilityBalanced')
        : t('metrics.instabilityVolatile');

    return el(
      'div',
      { class: 'stack-lg' },
      card(t('metrics.title'), { icon: 'chart' },
        el('p', { class: 'muted' }, t('metrics.intro')),
        el('div', { class: 'metric-grid' },
          metricTile({
            value: modules || '—',
            label: t('metrics.modules'),
            tone: 'neutral',
            reading: t('metrics.modulesReading', { count: modules }),
            hint: t('metrics.modulesHint'),
          }),
          metricTile({
            value: cycles.length,
            label: t('metrics.cycles'),
            tone: cycles.length ? 'bad' : 'good',
            reading: cycles.length ? t('metrics.cyclesBad', { count: cycles.length }) : t('metrics.cyclesGood'),
            hint: t('metrics.cyclesHint'),
          }),
          metricTile({
            value: gods.length,
            label: t('metrics.godClasses'),
            tone: gods.length ? 'watch' : 'good',
            reading: gods.length ? t('metrics.godClassesBad', { count: gods.length }) : t('metrics.godClassesGood'),
            hint: t('metrics.godClassesHint'),
          }),
          metricTile({
            value: violations.length,
            label: t('metrics.layering'),
            tone: violations.length ? 'watch' : 'good',
            reading: violations.length ? t('metrics.layeringBad', { count: violations.length }) : t('metrics.layeringGood'),
            hint: t('metrics.layeringHint'),
          }),
          metricTile({
            value: `${abstraction}%`,
            label: t('metrics.abstraction'),
            tone: abstraction >= 15 ? 'good' : 'neutral',
            reading: t('metrics.abstractionReading', { percent: abstraction }),
            hint: t('metrics.abstractionHint'),
          }))),
      card(t('metrics.instability'), { icon: 'layers' },
        el('p', {}, t('metrics.instabilityWhat')),
        ratioScale(average, t('metrics.scaleStable'), t('metrics.scaleUnstable')),
        el('p', { class: `metric-reading tone-${averageTone}` }, averageReading),
        el('p', { class: 'small muted' }, t('metrics.instabilityUse')),
        coupling.length
          ? charts.panel(t('metrics.byModule'), instability, { subtitle: t('metrics.byModuleHint') })
          : null),
      cycles.length || gods.length || violations.length
        ? card(t('metrics.findings'), { icon: 'alert' },
            el('p', { class: 'muted' }, t('metrics.findingsIntro')),
            el(
              'div',
              { class: 'finding-stack' },
              findingGroup({
                icon: 'refresh',
                tone: 'err',
                title: t('metrics.cycles'),
                why: t('metrics.cyclesWhy'),
                count: cycles.length,
                body: cycles.length
                  ? el('div', { class: 'chain-list' },
                      ...cycles.map((c, index) => cycleChain(c.modules || [], index)))
                  : null,
              }),
              findingGroup({
                icon: 'layers',
                tone: 'warn',
                title: t('metrics.godClasses'),
                why: t('metrics.godClassesWhy'),
                count: gods.length,
                body: gods.length
                  ? el('div', { class: 'mag-list' },
                      ...(() => {
                        const ranked = gods
                          .slice()
                          .sort((a, b) => (Number(b.methods) || 0) - (Number(a.methods) || 0));
                        const worst = Number(ranked[0].methods) || 1;
                        return ranked.map((g, index) => magnitudeRow({
                          label: g.name || g.id,
                          value: Number(g.methods) || 0,
                          max: worst,
                          tone: 'watch',
                          display: t('metrics.godClassLine', {
                            methods: g.methods ?? '?',
                            deps: g.dependencies ?? '?',
                          }),
                        }, index));
                      })())
                  : null,
              }),
              findingGroup({
                icon: 'compare',
                tone: 'warn',
                title: t('metrics.layering'),
                why: t('metrics.layeringWhy'),
                count: violations.length,
                body: violations.length
                  ? el('div', { class: 'chain-list' },
                      ...violations.map((v, index) =>
                        violationRow(v.source || v.from, v.target || v.to, index)))
                  : null,
              }),
            ))
        : card(t('metrics.findings'), { icon: 'check' },
            el('div', { class: 'all-clear' },
              icon('check', { size: 28 }),
              el('p', {}, t('metrics.allClear')))),
    );
  }

  /* ===================================================================== */
  /* view: guided fixes                                                     */
  /* ===================================================================== */

  const SEVERITY_RANK = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
  const SEVERITY_TONE = { critical: 'err', high: 'err', medium: 'warn', low: 'info', info: 'info' };

  /**
   * Render a unified diff.
   *
   * Every line goes in via textContent, never innerHTML - the content is source
   * code from the scanned repository and must never be parsed as markup.
   */
  function diffBlock(diff) {
    const pre = el('pre', { class: 'diff', tabindex: '0' });
    String(diff || '')
      .split('\n')
      .forEach((line) => {
        let kind = 'ctx';
        if (line.startsWith('+++') || line.startsWith('---')) kind = 'meta';
        else if (line.startsWith('@@')) kind = 'hunk';
        else if (line.startsWith('+')) kind = 'add';
        else if (line.startsWith('-')) kind = 'del';
        const row = el('span', { class: `diff-line ${kind}` });
        row.textContent = line || ' ';
        pre.append(row);
      });
    return pre;
  }

  function viewFixes(host) {
    const refreshBtn = el(
      'button',
      {
        class: 'btn',
        type: 'button',
        onclick: () => load(true),
      },
      icon('refresh', { size: 14 }),
      t('common.refresh'),
    );
    host.append(pageHead(t('nav.fixes'), t('fixes.subtitle'), [refreshBtn]));
    if (!analysisReady()) { needsAnalysisState(host); return; }

    const body = el('div', { class: 'stack-lg' });
    host.append(body);

    // Selection lives outside the render so filtering never loses ticks.
    const selected = new Set();
    let proposals = [];
    let includeCosmetic = !!state.fixesCosmetic;
    let payload = null;

    function load(force) {
      state.fixesCosmetic = includeCosmetic;
      const key = fixesCacheKey(includeCosmetic);
      if (!force && state.fixes && state.fixesKey === key) {
        payload = state.fixes;
        proposals = (payload && payload.proposals) || [];
        selected.clear();
        paint(payload);
        return;
      }

      clear(body).append(skeleton('block'), skeleton('line', 5));
      refreshBtn.disabled = true;
      refreshBtn.setAttribute('aria-busy', 'true');
      api
        .fixProposals(state.analysisId, 200, { includeCosmetic, language: i18n.language() })
        .then((result) => {
          if (!body.isConnected) return;
          state.fixes = result;
          state.fixesKey = key;
          payload = result;
          proposals = (result && result.proposals) || [];
          selected.clear();
          paint(result);
        })
        .catch((error) => {
          if (!body.isConnected) return;
          clear(body).append(errorState(error, t('nav.fixes')));
        })
        .finally(() => {
          refreshBtn.disabled = false;
          refreshBtn.removeAttribute('aria-busy');
        });
    }

    function paint(nextPayload) {
      payload = nextPayload;
      const autoFixable = proposals.filter((p) => p.auto_fixable);
      const advisory = proposals.filter((p) => !p.auto_fixable);
      const structural = proposals.filter((p) => p.kind === 'structural');
      const files = new Set(proposals.map((p) => p.file).filter(Boolean));
      const aiOn = !!(payload && payload.ai_available);
      const patched = (payload && payload.ai_patched) || 0;
      const attempted = (payload && payload.ai_attempted) || 0;
      const failed = (payload && payload.ai_failed) || 0;
      const modeCopy = aiOn
        ? (attempted
          ? t('fixes.modeAiStats', { n: patched, attempted, failed })
          : t('fixes.modeAi', { n: patched }))
        : t('fixes.modeOffline');

      clear(body);
      // `el()` drops null children, but this is the native DOM method, which
      // stringifies them - a null argument here renders the word "null" on the
      // page. Optional blocks are therefore collected and filtered, never passed
      // as `condition ? node : null`.
      const notices = [
        // The safety contract is the headline, not a footnote.
        el('div', { class: 'notice info', role: 'note' }, icon('info', { size: 16 }), el('p', {}, t('fixes.manualOnly'))),
        // Which of the two modes produced this list, and what the other one
        // would add - otherwise a short list looks like a broken feature.
        el('div', { class: `notice ${aiOn ? 'ok' : ''}`, role: 'note' },
          icon(aiOn ? 'sparkle' : 'shield', { size: 16 }),
          el('p', {}, modeCopy)),
        payload && payload.ai_error
          ? el('div', { class: 'notice warn', role: 'note' }, icon('alert', { size: 16 }),
              el('p', {}, t('fixes.aiFailed', { error: payload.ai_error })))
          : null,
      ].filter(Boolean);
      body.append(
        ...notices,
        el('div', { class: 'kpi-grid' },
          el('div', { class: 'kpi-tile' }, el('b', {}, String(proposals.length)), el('span', {}, t('fixes.total'))),
          el('div', { class: 'kpi-tile' }, el('b', {}, String(structural.length)), el('span', {}, t('fixes.structural'))),
          el('div', { class: 'kpi-tile' }, el('b', {}, String(autoFixable.length)), el('span', {}, t('fixes.autoFixable'))),
          el('div', { class: 'kpi-tile' }, el('b', {}, String(advisory.length)), el('span', {}, t('fixes.advisory'))),
          el('div', { class: 'kpi-tile' }, el('b', {}, String(files.size)), el('span', {}, t('fixes.files')))),
      );

      if (!proposals.length) {
        body.append(emptyState({ icon: 'check', title: t('fixes.clean'), body: t('fixes.cleanHint') }));
        i18n.applyStatic(body);
        return;
      }

      const listHost = el('div', { class: 'stack' });
      const counter = el('span', { class: 'sub' }, '');
      const applyBtn = el(
        'button',
        { class: 'btn primary', onclick: () => confirmApply() },
        icon('check', { size: 14 }),
        t('fixes.apply'),
      );

      function syncBar() {
        counter.textContent = t('fixes.selectedCount', { n: selected.size });
        applyBtn.disabled = selected.size === 0;
      }

      const severityFilter = el(
        'select',
        { class: 'compact', 'aria-label': t('fixes.severity'), onchange: () => renderList() },
        el('option', { value: '' }, t('fixes.allSeverities')),
        ...['critical', 'high', 'medium', 'low'].map((s) => el('option', { value: s }, t(`score.priority.${s}`))),
      );
      const onlyFixable = el('input', { type: 'checkbox', onchange: () => renderList() });
      // Formatting rules are off by default: two hundred whitespace hits bury
      // the injection sink further down the list. Opting in reloads, because the
      // backend never generated them in the first place.
      const cosmeticToggle = el('input', {
        type: 'checkbox',
        checked: includeCosmetic,
        onchange: (ev) => { includeCosmetic = ev.currentTarget.checked; load(false); },
      });

      function renderList() {
        const sev = severityFilter.value;
        const visible = proposals.filter(
          (p) => (!sev || p.severity === sev) && (!onlyFixable.checked || p.auto_fixable),
        );
        clear(listHost);
        if (!visible.length) {
          listHost.append(emptyState({ icon: 'inbox', title: t('fixes.noMatches') }));
          return;
        }
        visible.forEach((proposal) => listHost.append(proposalCard(proposal)));
        i18n.applyStatic(listHost);
      }

      function proposalCard(proposal) {
        const tone = SEVERITY_TONE[proposal.severity] || 'info';
        const structural = proposal.kind === 'structural';
        const box = proposal.auto_fixable
          ? el('input', {
              type: 'checkbox',
              'aria-label': `${t('fixes.select')}: ${proposal.title}`,
              onchange: (ev) => {
                if (ev.currentTarget.checked) selected.add(proposal.id);
                else selected.delete(proposal.id);
                syncBar();
              },
            })
          : null;
        if (box) box.checked = selected.has(proposal.id);

        const details = el(
          'details',
          { class: 'fix-diff' },
          el('summary', {}, t('fixes.showDiff')),
          proposal.auto_fixable
            ? diffBlock(proposal.diff)
            : el('p', { class: 'sub' }, structural ? t('fixes.noDiffStructural') : t('fixes.noDiff')),
        );

        // A finding with no diff still has to leave the reader with something to
        // do, so the repair procedure is shown in full rather than hidden away.
        const steps = (proposal.steps || []).length
          ? el('div', { class: 'fix-steps' },
              el('h5', {}, t('fixes.howTo')),
              el('ol', {}, ...proposal.steps.map((step) => el('li', {}, String(step)))))
          : null;

        const targets = structural && (proposal.files || []).length
          ? el('div', { class: 'fix-steps' },
              el('h5', {}, t('fixes.affected')),
              el('ul', { class: 'mono' }, ...proposal.files.map((f) => el('li', {}, String(f)))))
          : null;

        const aiNotes = proposal.source === 'ai'
          ? el('div', { class: 'fix-steps ai' },
              el('h5', {}, t('fixes.aiPatch')),
              proposal.ai_diagnosis ? el('p', { class: 'sub' }, proposal.ai_diagnosis) : null,
              proposal.ai_explanation ? el('p', { class: 'sub' }, proposal.ai_explanation) : null,
              proposal.ai_risk
                ? el('p', { class: 'sub' }, `${t('fixes.reviewFirst')}: ${proposal.ai_risk}`)
                : null)
          : null;

        return el(
          'article',
          { class: `fix-card ${proposal.auto_fixable ? '' : 'advisory'}${structural ? ' structural' : ''}` },
          el('header', { class: 'fix-head' },
            box,
            el('div', { class: 'fix-title' },
              el('h4', {}, proposal.title),
              el('p', { class: 'sub mono' },
                structural
                  ? t('fixes.filesAffected', { n: (proposal.files || []).length })
                  : `${proposal.file}${proposal.lines.length ? `:${proposal.lines[0]}` : ''}`)),
            el('div', { class: 'inline' },
              structural ? el('span', { class: 'badge outline' }, t('fixes.structuralTag')) : null,
              proposal.source === 'ai' ? el('span', { class: 'badge info' }, t('fixes.aiTag')) : null,
              el('span', { class: `badge ${tone}` }, t(`score.priority.${proposal.severity}`)),
              el('span', { class: 'badge' }, `${t('score.effort')} ${proposal.effort}`),
              el('span', { class: 'badge' }, `${t('fixes.confidence')} ${Math.round(proposal.confidence * 100)}%`))),
          el('dl', { class: 'fix-facts' },
            el('dt', {}, t('fixes.problem')), el('dd', {}, proposal.problem),
            el('dt', {}, t('fixes.rootCause')), el('dd', {}, proposal.root_cause),
            el('dt', {}, t('fixes.impact')), el('dd', {}, proposal.impact),
            el('dt', {}, t('fixes.occurrences')), el('dd', {}, String(proposal.occurrences))),
          steps,
          targets,
          aiNotes,
          details,
        );
      }

      function confirmApply() {
        const chosen = proposals.filter((p) => selected.has(p.id));
        const fileCount = new Set(chosen.map((p) => p.file)).size;
        confirmDialog(
          t('fixes.confirmBody', { n: chosen.length, files: fileCount }),
          () => {
            applyBtn.disabled = true;
            applyBtn.setAttribute('aria-busy', 'true');
            api
              .applyFixes(
                state.analysisId,
                chosen.map((p) => ({
                  file: p.file,
                  rule: p.rule,
                  digest: p.digest,
                  // Present only on model-authored patches; the replacement text
                  // itself never leaves the backend.
                  ai_fix_id: p.ai_fix_id || undefined,
                })),
              )
              .then((result) => {
                const failed = (result && result.failed) || [];
                if (failed.length) {
                  toast(t('fixes.partial', { n: result.applied_files, failed: failed.length }), 'warn');
                } else {
                  toast(t('fixes.applied', { n: result.applied_files }), 'ok');
                }
                invalidateFixes();
                load(true);
              })
              .catch((error) => {
                applyBtn.removeAttribute('aria-busy');
                applyBtn.disabled = false;
                toast(String(error && error.message ? error.message : error), 'err');
              });
          },
          { title: t('fixes.apply'), confirmLabel: t('fixes.applyConfirm'), danger: true },
        );
      }

      body.append(
        el('div', { class: 'fix-toolbar' },
          el('label', { class: 'inline' }, t('fixes.severity'), severityFilter),
          el('label', { class: 'inline' }, onlyFixable, t('fixes.onlyFixable')),
          el('label', { class: 'inline' }, cosmeticToggle, t('fixes.includeCosmetic')),
          el('span', { class: 'fix-toolbar-gap' }),
          counter,
          el('button', {
            class: 'btn',
            onclick: () => {
              proposals.filter((p) => p.auto_fixable).forEach((p) => selected.add(p.id));
              renderList();
              syncBar();
            },
          }, t('fixes.selectAll')),
          el('button', {
            class: 'btn',
            onclick: () => { selected.clear(); renderList(); syncBar(); },
          }, t('fixes.clearSelection')),
          applyBtn),
        listHost,
      );

      if (payload && payload.truncated) {
        body.append(el('p', { class: 'sub' }, t('fixes.truncated', { n: proposals.length })));
      }
      renderList();
      syncBar();
      i18n.applyStatic(body);
    }

    load(false);
  }

  /* ===================================================================== */
  /* view: git history                                                      */
  /* ===================================================================== */

  /**
   * Commit DAG panel. Loads independently of the activity charts so a slow or
   * unavailable graph never blocks the rest of the history view.
   */
  function commitGraphCard() {
    const slot = el('div', {}, skeleton('block'), skeleton('line', 3));
    const meta = el('p', { class: 'sub' }, '');
    const shell = card(t('gitgraph.title'), { icon: 'history' }, el('p', { class: 'sub' }, t('gitgraph.subtitle')), meta, slot);

    let limit = 300;

    function load() {
      api
        .commitGraph(state.analysisId, limit)
        .then((payload) => {
          // The view may have been replaced while the bridge call was in
          // flight; rendering now would leak a ResizeObserver on a dead node.
          if (!shell.isConnected) return;
          releaseGraph();
          clear(slot);
          if (!payload || !payload.available || !payload.commits || !payload.commits.length) {
            meta.textContent = '';
            slot.append(emptyState({
              // A git failure and an empty repository look identical unless the
              // backend's reason is shown, so a timeout must not read as "no commits".
              icon: payload && payload.failed ? 'alert' : 'history',
              title: payload && payload.failed ? t('history.failed') : t('gitgraph.empty'),
              body: historyReason(payload),
              action: payload && payload.failed
                ? el('button', { class: 'btn', onclick: () => { clear(slot).append(skeleton('block')); load(); } },
                    icon('refresh', { size: 14 }), t('common.retry'))
                : null,
            }));
            return;
          }
          meta.textContent = payload.truncated
            ? t('gitgraph.truncated', { n: payload.count })
            : t('gitgraph.showing', { n: payload.count });
          activeGraph = gitgraph.render(slot, payload);
          if (payload.truncated) {
            slot.append(
              el(
                'button',
                {
                  class: 'btn',
                  onclick: (ev) => {
                    ev.currentTarget.disabled = true;
                    limit = Math.min(limit * 2, 2000);
                    load();
                  },
                },
                icon('refresh', { size: 14 }),
                t('gitgraph.loadMore'),
              ),
            );
          }
          i18n.applyStatic(shell);
        })
        .catch((error) => {
          if (!shell.isConnected) return;
          releaseGraph();
          meta.textContent = '';
          clear(slot).append(errorState(error, t('gitgraph.title')));
        });
    }

    load();
    return shell;
  }

  /**
   * The three busiest committers, on a podium that rises into place.
   *
   * The blocks are sized relative to the leader and animated by a keyframe that
   * starts at zero height, so the ranking is readable before a single number is
   * read. The animation is declared in CSS, which means the reduced-motion rule
   * (OS setting or Appearance -> Motion) collapses it automatically.
   */
  function contributorPodium(contributors) {
    const ranked = contributors.filter((person) => Number(person.commits) > 0).slice(0, 3);
    if (!ranked.length) return null;

    const total = contributors.reduce((sum, person) => sum + (Number(person.commits) || 0), 0) || 1;
    const leader = Number(ranked[0].commits) || 1;
    // Silver on the left, gold in the middle, bronze on the right.
    const layout = [1, 0, 2].filter((index) => ranked[index]);

    const slots = layout.map((index) => {
      const person = ranked[index];
      const commits = Number(person.commits) || 0;
      const height = 44 + Math.round((commits / leader) * 96);
      const share = Math.round((commits / total) * 100);
      const name = person.author || person.name || '—';
      return el(
        'div',
        { class: 'podium-slot', role: 'listitem' },
        el('div', { class: 'podium-who' },
          el('span', { class: 'podium-name', title: name }, name),
          el('span', { class: 'podium-commits' }, t('history.commitCount', { count: commits })),
          el('span', { class: 'podium-share' }, t('history.share', { percent: share }))),
        el('div', {
          class: `podium-step rank-${index + 1}`,
          style: `--podium-height:${height}px;animation-delay:${120 * (index + 1)}ms`,
        }, el('span', { class: 'podium-rank' }, String(index + 1))),
      );
    });

    return el('div', { class: 'podium', role: 'list', 'aria-label': t('history.podium') }, ...slots);
  }

  /**
   * The sentence to show when history is unavailable.
   *
   * The backend sends `reason_key` for the states it can name (not a
   * repository, no commits yet, folder missing) and a raw English `reason` for
   * whatever git itself said. Preferring the key is what makes this page speak
   * Hebrew; a genuine git error still falls through to git's own words, which
   * are more useful untranslated than paraphrased.
   */
  function historyReason(payload) {
    if (!payload) return t('history.unavailableHint');
    if (payload.reason_key) {
      const translated = t(payload.reason_key);
      if (translated && translated !== payload.reason_key) {
        // Git's own words are the diagnostic half of the message, so they are
        // appended rather than replaced by the translated lead.
        return payload.detail ? `${translated} ${payload.detail}` : translated;
      }
    }
    return payload.reason || t('history.unavailableHint');
  }

  function viewHistory(host) {
    host.append(pageHead(t('nav.history'), t('history.subtitle'), []));
    if (!analysisReady()) { needsAnalysisState(host); return; }

    const body = el('div', { class: 'stack-lg' }, skeleton('block'), skeleton('line', 4));
    host.append(body);
    host.append(commitGraphCard());

    api
      .history(state.analysisId)
      .then((history) => {
        if (!history || !history.commit_count) {
          clear(body).append(emptyState({
            icon: history && history.failed ? 'alert' : 'history',
            title: history && history.failed ? t('history.failed') : t('history.unavailable'),
            body: historyReason(history),
            action: history && history.failed
              ? el('button', { class: 'btn', onclick: () => renderView() }, icon('refresh', { size: 14 }), t('common.retry'))
              : null,
          }));
          return;
        }
        const months = Object.entries(history.activity_by_month || {});
        const activity = months.length
          ? charts.line({
              series: [{ name: t('history.commits'), values: months.map(([, count]) => count), color: 'var(--accent)' }],
              labels: months.map(([month]) => month),
              width: 900,
              height: 240,
            })
          : null;
        const contributors = charts.bars({
          items: (history.top_contributors || []).slice(0, 10).map((c) => ({ label: c.author || c.name, value: c.commits })),
          width: 640,
        });
        const podium = contributorPodium(history.top_contributors || []);

        append(
          clear(body),
          el('div', { class: 'kpi-grid' },
            el('div', { class: 'kpi-tile' }, el('b', {}, String(history.commit_count)), el('span', {}, t('history.commits'))),
            el('div', { class: 'kpi-tile' }, el('b', {}, String(history.contributors ?? (history.top_contributors || []).length)), el('span', {}, t('history.contributors'))),
            el('div', { class: 'kpi-tile' }, el('b', {}, String((history.hotspots || []).length)), el('span', {}, t('history.hotspots'))),
            el('div', { class: 'kpi-tile' }, el('b', {}, String((history.temporal_coupling || []).length)), el('span', {}, t('history.coupling')))),
          activity ? card(t('history.activity'), { icon: 'chart' }, charts.panel(t('history.commits'), activity, {})) : null,
          podium
            ? card(t('history.podium'), { icon: 'sparkle' },
                el('p', { class: 'small muted' }, t('history.podiumHint')),
                podium)
            : null,
          el(
            'div',
            { class: 'grid grid-2' },
            card(t('history.contributors'), { icon: 'folder' }, charts.panel(t('history.commits'), contributors, {})),
            card(t('history.hotspots'), { icon: 'alert' },
              el('table', {},
                el('thead', {}, el('tr', {},
                  el('th', {}, t('hotspots.file')),
                  el('th', {}, t('history.changes')),
                  el('th', {}, t('history.owner')),
                  el('th', {}, t('history.riskLevel')))),
                el('tbody', {}, ...(history.hotspots || []).slice(0, 20).map((hot) =>
                  el('tr', {},
                    el('td', { class: 'mono small truncate' }, hot.path || hot.name),
                    el('td', { class: 'tabular' }, String(hot.changes ?? hot.change_count ?? '')),
                    el('td', { class: 'small' }, hot.primary_owner || '—'),
                    el('td', {}, el('span', {
                      class: `badge ${hot.risk === 'high' ? 'err' : hot.risk === 'medium' ? 'warn' : ''}`,
                    }, hot.risk || '—')))))))),
          card(t('history.signals'), { icon: 'info' },
            riskSection(t('history.risks'), history.risks || []),
            bulletSection(t('history.coupling'), (history.temporal_coupling || []).map((c) =>
              `${(c.files || [c.a, c.b]).join(' ↔ ')} (${c.together ?? c.count ?? c.score})`))),
        );
        i18n.applyStatic(body);
      })
      .catch((error) => {
        clear(body).append(errorState(error));
      });
  }

  /* ===================================================================== */
  /* view: compare                                                          */
  /* ===================================================================== */

  function viewCompare(host) {
    host.append(pageHead(t('compare.title'), t('compare.subtitle'), []));
    const done = state.analyses.filter((a) => a.status === 'succeeded');
    if (done.length < 2) {
      host.append(emptyState({ icon: 'compare', title: t('compare.need'), body: t('compare.needHint') }));
      return;
    }
    const option = (a) =>
      el('option', { value: a.id }, `${a.ref || '—'} ${(a.commit_sha || '').slice(0, 8)} · ${fmtDate(a.created_at)}`);
    const base = el('select', {}, ...done.map(option));
    const head = el('select', {}, ...done.map(option));
    head.value = done[0].id;
    base.value = done[1].id;
    const output = el('div', { class: 'stack-lg' });

    host.append(
      card(null, {},
        el('div', { class: 'row' },
          field(t('compare.base'), base),
          field(t('compare.head'), head),
          el('div', { class: 'field' }, el('label', {}, '\u00a0'),
            el('button', {
              class: 'btn primary',
              onclick: async () => {
                clear(output).append(card(null, {}, el('p', { class: 'muted small' }, t('common.loading')), spinner()));
                try {
                  const result = await api.compare({
                    base_analysis_id: base.value,
                    head_analysis_id: head.value,
                    language: i18n.language(),
                  });
                  clear(output).append(renderDiff(result));
                  await appendScoreDiff(output, base.value, head.value);
                } catch (error) { handle(error); clear(output); }
              },
            }, t('compare.run'))))),
      output,
    );
  }

  async function appendScoreDiff(host, baseId, headId) {
    try {
      const [a, b] = await Promise.all([api.scoreCard(baseId), api.scoreCard(headId)]);
      const before = a.scorecard;
      const after = b.scorecard;
      host.append(
        card(t('compare.scoreDelta'), { icon: 'gauge' },
          el('div', { class: 'inline', style: 'gap:24px;margin-bottom:12px' },
            el('div', { class: 'inline' },
              el('span', { class: `badge ${bandClass(before.overall)}` }, `${before.overall} ${before.grade}`),
              icon('arrow', { size: 16 }),
              el('span', { class: `badge ${bandClass(after.overall)}` }, `${after.overall} ${after.grade}`)),
            deltaLabel(after.overall - before.overall, { suffix: ' pts' })),
          el('table', {},
            el('thead', {}, el('tr', {},
              el('th', {}, t('score.category')),
              el('th', {}, t('compare.base')),
              el('th', {}, t('compare.head')),
              el('th', {}, t('compare.delta')))),
            el('tbody', {}, ...after.categories.map((category) => {
              const previous = before.category_index[category.id] ?? 0;
              return el('tr', {},
                el('td', {}, score.catLabel(category.id)),
                el('td', { class: 'tabular' }, String(previous)),
                el('td', { class: 'tabular' }, String(category.score)),
                el('td', {}, deltaLabel(category.score - previous)));
            })))),
      );
    } catch {
      /* one of the runs predates the scorecard - the structural diff still stands */
    }
  }

  function renderDiff(result) {
    const diff = result.diff || {};
    const narrative = result.narrative || {};
    const stage = el('div', { class: 'stage' });
    const wrapper = card(t('compare.title'), { icon: 'compare' },
      el('div', { class: 'kpi-grid' },
        el('div', { class: 'kpi-tile' }, el('b', {}, String((diff.added_nodes || []).length)), el('span', {}, t('compare.added'))),
        el('div', { class: 'kpi-tile' }, el('b', {}, String((diff.removed_nodes || []).length)), el('span', {}, t('compare.removed'))),
        el('div', { class: 'kpi-tile' }, el('b', {}, String((diff.changed_nodes || []).length)), el('span', {}, t('compare.changed'))),
        el('div', { class: 'kpi-tile' }, el('b', {}, String(diff.impact || '—')), el('span', {}, t('compare.impact')))),
      el('p', {}, narrative.summary || diff.summary || ''),
      bulletSection(t('compare.highlights'), narrative.highlights || diff.highlights),
      riskSection(t('ai.risks'), narrative.risks || diff.risks || []),
      bulletSection(t('ai.recommendations'), (narrative.recommendations || []).map(describe)),
      diff.metrics
        ? el('div', {}, el('h4', {}, t('compare.metricDelta')),
            el('table', {}, el('tbody', {}, ...Object.entries(diff.metrics).map(([key, value]) =>
              el('tr', {}, el('td', { class: 'mono small' }, key), el('td', {}, formatDelta(value)))))))
        : null,
      stage);
    if (diff.mermaid) {
      queueMicrotask(() => {
        new DiagramViewer(stage).render({ kind: 'compare', title: t('compare.title'), mermaid: diff.mermaid });
        i18n.applyStatic(stage);
      });
    }
    return wrapper;
  }

  function formatDelta(value) {
    if (value && typeof value === 'object') {
      const delta = value.delta ?? value.head - value.base;
      const shown = typeof delta === 'number' ? delta.toFixed(2) : delta;
      return `${value.base} → ${value.head} (${delta > 0 ? '+' : ''}${shown})`;
    }
    return String(value);
  }

  /* ===================================================================== */
  /* view: settings                                                         */
  /* ===================================================================== */

  function viewSettings(host) {
    host.append(pageHead(t('nav.settings'), t('settings.subtitle'), []));
    const panel = el('div', { class: 'settings-layout' });
    const tabHost = el('div', { class: 'settings-tabs' });
    const body = el('div', { class: 'settings-body', role: 'tabpanel' });

    const items = () => [
      { id: 'appearance', label: t('settings.appearance') },
      { id: 'provider', label: t('provider.title') },
      { id: 'scoring', label: t('score.weights') },
      { id: 'storage', label: t('settings.storage') },
    ];
    const draw = () => {
      const tab = state.settingsTab || 'appearance';
      clear(tabHost).append(tabs(items(), tab, (next) => {
        state.settingsTab = next;
        draw();
      }));
      clear(body);
      if (tab === 'appearance') body.append(appearanceSettings(draw));
      else if (tab === 'provider') body.append(providerSettings());
      else if (tab === 'storage') body.append(storageSettings());
      else body.append(card(t('score.weights'), { icon: 'gauge' }, score.weightsEditor(() => {
        if (analysisReady()) api.scoreRecompute(state.analysisId).then(() => invalidateScore()).catch(() => {});
      })));
      i18n.applyStatic(body);
    };
    draw();
    host.append(append(panel, tabHost, body));
  }

  /** Where the app keeps its files. Operational detail, so it lives in
   *  Settings rather than on the About page. */
  function storageSettings() {
    const host = el('div', {}, skeleton('line', 3));
    api
      .settings()
      .then((info) => {
        append(
          clear(host),
          card(t('settings.storage'), { icon: 'folder' },
            el('dl', { class: 'about-facts' },
              aboutFact(t('settings.dataDir'), info.data_dir, true),
              aboutFact(t('settings.database'), info.database, true))),
        );
      })
      .catch((error) => { clear(host); handle(error); });
    return host;
  }

  function appearanceSettings(redraw) {
    const choice = (key, options, current) =>
      el('div', { class: 'chips', role: 'group' }, ...options.map((option) =>
        el('button', {
          type: 'button',
          class: `chip ${current === option.value ? 'on' : ''}`,
          'aria-pressed': current === option.value ? 'true' : 'false',
          onclick: () => {
            setPref(key, option.value);
            applyPreferences();
            refreshTheme();
            // Stay on this settings tab; only rebuild the panel.
            if (typeof redraw === 'function') redraw();
            else renderView();
          },
        }, option.label)));

    return card(t('settings.appearance'), { icon: 'settings' },
      el('div', { class: 'settings-grid' },
        el('div', { class: 'field' },
          el('label', {}, t('common.theme')),
          choice('theme', [
            { value: 'dark', label: t('settings.dark') },
            { value: 'light', label: t('settings.light') },
          ], pref('theme', 'dark'))),
        el('div', { class: 'field' },
          el('label', {}, t('settings.contrast')),
          choice('contrast', [
            { value: 'normal', label: t('settings.normal') },
            { value: 'high', label: t('settings.high') },
          ], pref('contrast', 'normal'))),
        el('div', { class: 'field' },
          el('label', {}, t('settings.palette')),
          choice('palette', [
            { value: 'default', label: t('settings.paletteDefault') },
            { value: 'cb', label: t('settings.paletteCb') },
          ], pref('palette', 'default'))),
        el('div', { class: 'field' },
          el('label', {}, t('settings.motion')),
          choice('motion', [
            { value: 'full', label: t('settings.motionFull') },
            { value: 'reduced', label: t('settings.motionReduced') },
          ], pref('motion', 'full'))),
        el('div', { class: 'field' },
          el('label', {}, t('settings.textSize')),
          choice('scale', [
            { value: '0.9', label: 'A-' },
            { value: '1', label: 'A' },
            { value: '1.12', label: 'A+' },
            { value: '1.25', label: 'A++' },
          ], pref('scale', '1'))),
        el('div', { class: 'field' },
          el('label', {}, t('common.language')),
          el('div', { class: 'chips', role: 'group' }, ...i18n.SUPPORTED.map((language) =>
            el('button', {
              type: 'button',
              class: `chip ${i18n.language() === language.code ? 'on' : ''}`,
              'aria-pressed': i18n.language() === language.code ? 'true' : 'false',
              onclick: () => i18n.setLanguage(language.code),
            }, language.label)))),
        el('div', { class: 'field span-2' },
          el('label', {}, t('shortcuts.title')),
          el('button', { class: 'btn', onclick: () => palette.help() }, t('shortcuts.show'))),
      ));
  }

  function providerSettings() {
    const host = el('div', {}, skeleton('line', 6));

    const draw = async () => {
      let config = {};
      try { config = (await api.provider()) || {}; } catch (error) { handle(error); }

      const baseUrl = el('input', {
        type: 'url',
        value: config.base_url || '',
        placeholder: 'http://localhost:11434/v1',
      });
      const model = el('input', { type: 'text', value: config.model || '' });
      const apiKey = el('input', { type: 'password', placeholder: config.api_key_masked || '' });
      const headers = el('textarea', {}, JSON.stringify(config.headers || {}, null, 2));
      const temperature = el('input', { type: 'number', step: '0.05', min: 0, max: 2, value: config.temperature ?? 0.2 });
      const maxTokens = el('input', { type: 'number', min: 64, max: 200000, value: config.max_tokens ?? 2048 });
      const timeout = el('input', { type: 'number', min: 5, max: 1800, value: config.timeout_seconds ?? 120 });
      const retries = el('input', { type: 'number', min: 0, max: 10, value: config.max_retries ?? 3 });
      const streaming = el('input', { type: 'checkbox', checked: config.streaming ?? true });
      const result = el('span');

      const payload = () => {
        let parsedHeaders = {};
        try { parsedHeaders = JSON.parse(headers.value || '{}'); }
        catch { throw new Error('Custom headers must be valid JSON'); }
        return {
          name: config.name || 'default',
          base_url: baseUrl.value.trim(),
          model: model.value.trim(),
          api_key: apiKey.value,
          headers: parsedHeaders,
          temperature: Number(temperature.value),
          max_tokens: Number(maxTokens.value),
          timeout_seconds: Number(timeout.value),
          max_retries: Number(retries.value),
          streaming: streaming.checked,
        };
      };

      clear(host).append(
        card(t('provider.title'), { icon: 'sparkle' },
          el(
            'div',
            { class: 'stack-lg' },
            el('p', { class: 'muted small' }, t('provider.hint')),
            config.source === 'environment' ? el('p', { class: 'small muted mono' }, '.env') : null,
            el('div', { class: 'form-grid' },
              el('div', { class: 'field span-2' },
                el('label', {}, t('provider.baseUrl')),
                baseUrl),
              field(t('provider.model'), model),
              field(t('provider.apiKey'), apiKey, { hint: t('provider.apiKeyKeep') }),
              el('div', { class: 'field span-2' },
                el('label', {}, t('provider.headers')),
                headers)),
            el(
              'div',
              { class: 'panel settings-subpanel' },
              el('div', { class: 'form-grid' },
                field(t('provider.temperature'), temperature),
                field(t('provider.maxTokens'), maxTokens),
                field(t('provider.timeout'), timeout),
                field(t('provider.retries'), retries)),
              el('label', { class: 'check' }, streaming, el('span', {}, t('provider.streaming'))),
              el('div', { class: 'inline settings-actions' },
                el('button', {
                  class: 'btn primary',
                  onclick: async () => {
                    try { await api.saveProvider(payload()); toast(t('common.saved'), 'success'); draw(); }
                    catch (error) { handle(error); }
                  },
                }, t('common.save')),
                el('button', {
                  class: 'btn',
                  onclick: async (event) => {
                    const button = event.currentTarget;
                    button.disabled = true;
                    clear(result).append(spinner());
                    try {
                      const probe = await api.testProvider();
                      clear(result).append(el('span', { class: `badge ${probe.ok ? 'ok' : 'err'}` },
                        probe.ok ? t('provider.ok', { ms: probe.latency_ms }) : probe.error || t('common.error')));
                    } catch (error) { handle(error); clear(result); } finally { button.disabled = false; }
                  },
                }, t('provider.test')),
                el('button', {
                  class: 'btn danger',
                  onclick: () => confirmDialog(t('provider.clear'), async () => {
                    try { await api.clearProvider(); toast(t('common.saved'), 'success'); draw(); }
                    catch (error) { handle(error); }
                  }),
                }, t('provider.clear')),
                result),
            ),
          ),
        ),
      );
      i18n.applyStatic(host);
    };

    draw();
    return host;
  }

  /* ===================================================================== */
  /* view: about                                                            */
  /* ===================================================================== */

  function viewAbout(host) {
    host.append(pageHead(t('about.title'), t('about.local'), []));
    const info = state.info || {};
    const version = info.version || '';
    const build = info.build || '';
    // Nothing here needs the bridge: every value already arrived with the
    // health call at start-up. The page used to fetch the settings summary
    // purely to print the data folder and the database path, which are
    // operational details that belong in Settings, not on an About page.
    host.append(
      el('div', { class: 'about-page' },
        el('div', { class: 'about-hero' },
          el('div', { class: 'about-mark' }, appMark(56)),
          el('div', { class: 'about-id' },
            el('h2', {}, info.product || t('app.title')),
            el('p', { class: 'about-tagline' }, t('about.local')),
            el('div', { class: 'about-badges' },
              version ? el('span', { class: 'badge outline' }, `${t('about.version')} ${version}`) : null,
              build ? el('span', { class: 'badge outline mono' }, `${t('about.build')} ${build}`) : null))),
        el('dl', { class: 'about-facts' },
          aboutFact(t('about.version'), version, true),
          aboutFact(t('about.build'), build, true),
          // The author's name is a proper noun, so it is deliberately not translated.
          aboutFact(t('about.createdBy'), info.author || 'Daniel Uralsky'),
          aboutFact(t('about.copyright'), info.copyright || '')),
        el('p', { class: 'about-foot' }, t('about.offline'))),
    );
  }

  /** One row of the About fact list. Monospace only where the value is data. */
  function aboutFact(label, value, mono) {
    if (!value) return null;
    return el('div', { class: 'about-fact' },
      el('dt', {}, label),
      el('dd', { class: mono ? 'mono' : null }, value));
  }

  /** The application mark, drawn from the same path as assets/appicon.ico.
   *
   * `appmark` exists only for this: the mark used to be `layers`, which is
   * also the Diagrams navigation entry, the Architecture category and the
   * instability card, so the product had no shape of its own.
   */
  function appMark(size) {
    return icon('appmark', { size: size, weight: 1.6 });
  }

  /* ===================================================================== */
  /* commands + shortcuts                                                   */
  /* ===================================================================== */

  function registerCommands() {
    palette.register(() =>
      ALL_NAV.map((item) => ({
        id: `go.${item.id}`,
        label: t(item.label),
        hint: t('palette.goTo'),
        group: t('palette.navigation'),
        run: () => navigate(item.id),
      })),
    );

    palette.register(() => [
      { id: 'act.analyze', label: t('analysis.start'), group: t('palette.actions'), run: () => startAnalysis() },
      { id: 'act.newProject', label: t('project.create'), group: t('palette.actions'), run: () => projectDialog() },
      { id: 'act.generate', label: t('diagram.generate'), group: t('palette.actions'), run: () => { if (analysisReady()) { navigate('diagrams'); generateDialog(); } } },
      { id: 'act.ask', label: t('ai.ask'), group: t('palette.actions'), run: () => { if (analysisReady()) askDialog(); } },
      { id: 'act.weights', label: t('score.weights'), group: t('palette.actions'), run: () => weightsDialog() },
      { id: 'act.report', label: t('score.exportReport'), group: t('palette.actions'), run: () => exportScoreReport() },
      { id: 'act.theme', label: t('common.theme'), group: t('palette.actions'), run: () => {
        setPref('theme', document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
        applyPreferences(); refreshTheme(); renderView();
      } },
      { id: 'act.sidebar', label: t('a11y.toggleSidebar'), group: t('palette.actions'), run: () => toggleSidebar() },
    ]);

    palette.register(() =>
      state.projects.map((project) => ({
        id: `project.${project.id}`,
        label: project.name,
        hint: project.source_location,
        group: t('nav.projects'),
        run: async () => {
          state.projectId = project.id;
          setPref('project', project.id);
          state.analysisId = null;
          try { await loadAnalyses(); } catch (error) { handle(error); }
          navigate('dashboard');
        },
      })),
    );

    palette.register(() => {
      if (!state.scorecard) return [];
      return state.scorecard.scorecard.categories.map((category) => ({
        id: `score.${category.id}`,
        label: `${score.catLabel(category.id)} — ${category.score}/100`,
        hint: t('score.openExplorer'),
        group: t('nav.scorecard'),
        run: () => score.openExplorer(state.analysisId, category.id),
      }));
    });
  }

  function registerShortcuts() {
    palette.bind('mod+shift+p', t('palette.open'), () => palette.show(), t('palette.navigation'));
    palette.bind('mod+Enter', t('analysis.start'), () => startAnalysis(), t('palette.actions'));
    palette.bind('mod+1', t('nav.dashboard'), () => navigate('dashboard'), t('palette.navigation'));
    palette.bind('mod+2', t('nav.scorecard'), () => navigate('scorecard'), t('palette.navigation'));
    palette.bind('mod+3', t('nav.roadmap'), () => navigate('roadmap'), t('palette.navigation'));
    palette.bind('mod+4', t('nav.diagrams'), () => navigate('diagrams'), t('palette.navigation'));
    palette.bind('mod+b', t('a11y.toggleSidebar'), () => toggleSidebar(), t('palette.actions'));
    palette.bind('shift+?', t('shortcuts.title'), () => palette.help(), t('palette.actions'));
  }

  /* ===================================================================== */
  /* bootstrap                                                              */
  /* ===================================================================== */

  async function start() {
    applyPreferences();
    i18n.init();
    i18n.onLanguageChange(() => {
      buildNav();
      buildChrome();
      renderView();
    });

    clear(root).append(
      el('div', { class: 'boot' }, el('div', { class: 'panel' }, spinner(), ' ', t('common.loading'))),
    );

    state.info = await api.health();
    if (Array.isArray(state.info.diagram_kinds) && state.info.diagram_kinds.length) {
      DIAGRAM_KINDS.length = 0;
      DIAGRAM_KINDS.push(...state.info.diagram_kinds);
    }
    await loadProjects();
    mount();
    refresh();
  }

  start().catch((error) => {
    clear(root).append(
      el('div', { class: 'boot' },
        el('div', { class: 'panel' },
          el('h3', {}, 'ProjectAnalysis'),
          el('p', { class: 'small' }, String((error && error.message) || error)))),
    );
  });
})();
