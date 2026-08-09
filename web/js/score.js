/**
 * The score experience: hero dial, category cards, the interactive score
 * explorer drawer, evidence views, the prioritised roadmap and score trends.
 *
 * Everything here reads a scorecard produced by app/graph/scoring.py. A score
 * is never shown as a bare number - it always carries its category, its band
 * and a path to the evidence behind it.
 */
(function () {
  const AAI = window.AAI;
  const { el, append, clear, icon, card, emptyState, skeleton, spinner, toast, bandClass, deltaLabel, drawer, tabs } = AAI.dom;
  const charts = AAI.charts;
  const api = AAI.api;

  const t = (key, params) => AAI.i18n.t(key, params);
  const catLabel = (id) => t(`score.cat.${id}`);
  const sevClass = (severity) => `sev-${severity || 'info'}`;

  const EFFORT_ORDER = { low: 0, medium: 1, high: 2 };

  function fileName(path) {
    return String(path || '').split('/').pop();
  }

  /* --------------------------------------------------------------- hero */
  function hero(scorecard, options) {
    const opt = options || {};
    const dial = charts.gauge({
      value: scorecard.overall,
      max: 100,
      size: 210,
      label: t('score.outOf', { max: 100 }),
      sublabel: `${t('score.grade')} ${scorecard.grade}`,
    });

    const potential = Math.max(0, scorecard.potential_score - scorecard.overall);
    const facts = el(
      'div',
      { class: 'stack' },
      el('h2', {}, t('score.overallTitle')),
      el('p', { class: 'muted' }, scorecard.headline),
      el(
        'div',
        { class: 'kpi-grid' },
        tile(`${scorecard.overall} / 100`, t('score.current'), bandClass(scorecard.overall)),
        tile(`+${Math.round(potential)}`, t('score.potential'), 'band-good'),
        tile(`${Math.round((scorecard.confidence || 0) * 100)}%`, t('score.confidence')),
        tile(String((scorecard.roadmap && scorecard.roadmap.all ? scorecard.roadmap.all.length : 0)), t('score.actions')),
      ),
      opt.trend && opt.trend.length > 1
        ? el(
            'div',
            { class: 'inline small muted' },
            t('score.since'),
            charts.sparkline(opt.trend, { width: 140, height: 28, color: 'var(--accent)' }).node,
            deltaLabel(opt.trend[opt.trend.length - 1] - opt.trend[0], { suffix: ' pts' }),
          )
        : null,
    );

    return card(null, { class: 'score-hero-card' },
      el('div', { class: 'score-hero' }, el('div', { class: 'score-dial' }, dial.node), facts));
  }

  function tile(value, label, cls) {
    return el('div', { class: `kpi-tile ${cls || ''}` },
      el('b', { style: cls ? 'color:var(--band)' : '' }, value), el('span', {}, label));
  }

  /* ------------------------------------------------------- category cards */
  function categoryCards(scorecard, onOpen, previous) {
    const before = previous || {};
    return el(
      'div',
      { class: 'score-cards' },
      ...scorecard.categories.map((category) => {
        const delta = before[category.id] === undefined ? null : category.score - before[category.id];
        return el(
          'button',
          {
            type: 'button',
            class: `score-card ${bandClass(category.score)}`,
            'aria-label': `${catLabel(category.id)} ${category.score} / 100`,
            onclick: () => onOpen(category.id),
          },
          el('div', { class: 'top' },
            icon(category.icon || 'chart', { size: 15 }),
            el('span', { class: 'name' }, catLabel(category.id)),
            el('span', { class: 'badge outline xs' }, category.grade)),
          el('div', { class: 'num' }, el('b', {}, String(category.score)), el('span', {}, '/ 100')),
          el('div', { class: 'meter' }, el('i', { style: `width:${category.score}%` })),
          el('div', { class: 'foot' },
            el('span', { class: 'grow' }, t('score.weightIs', { pct: category.weight_pct })),
            delta === null ? null : deltaLabel(delta),
            category.issue_count
              ? el('span', { class: 'badge err' }, t('score.issuesN', { n: category.issue_count }))
              : el('span', { class: 'badge ok' }, t('score.clean'))),
        );
      }),
    );
  }

  /* -------------------------------------------------------------- radar */
  function radarPanel(scorecard, onAxis) {
    const chart = charts.radar({
      axes: scorecard.categories.map((category) => catLabel(category.id)),
      series: [{ name: t('score.overallTitle'), values: scorecard.categories.map((c) => c.score), color: 'var(--accent)' }],
      size: 340,
      max: 100,
      onAxis: (index) => onAxis(scorecard.categories[index].id),
    });
    return card(
      t('score.balance'),
      { icon: 'layers' },
      el('p', { class: 'small muted' }, t('score.balanceHint')),
      el('div', { class: 'radar-wrap' }, chart.node),
    );
  }

  /* ------------------------------------------------------- weighting view */
  function contributionPanel(scorecard) {
    const chart = charts.bars({
      items: scorecard.categories
        .slice()
        .sort((a, b) => b.lost_points - a.lost_points)
        .map((category) => ({
          label: catLabel(category.id),
          value: Math.round(category.lost_points * 10) / 10,
          color: charts.scoreColor(category.score),
          hint: t('score.weightIs', { pct: category.weight_pct }),
        })),
      unit: ' pts',
      width: 640,
    });
    return card(
      t('score.lostPoints'),
      { icon: 'chart' },
      el('p', { class: 'small muted' }, t('score.lostPointsHint')),
      el('div', { class: 'chart-wrap' }, chart.node),
    );
  }

  /* ------------------------------------------------------------- signals */
  // A coloured dot and a bare number are not enough to act on: they say a signal
  // is "bad" without saying how bad, why, or what to do. Every row therefore
  // spells out the severity in words, shows the arithmetic behind the points,
  // and carries the matching remediation inline.
  function signalNote(signal) {
    const points = Math.abs(signal.impact);
    const overall = Math.abs(signal.overall_impact === undefined ? 0 : signal.overall_impact);
    if (!points) return t('score.pointsNeutral');
    const params = {
      points,
      category: signal.category_id ? catLabel(signal.category_id) : (signal.category_label || ''),
      weight: signal.weight_pct === undefined ? '' : signal.weight_pct,
      overall,
    };
    return signal.impact < 0 ? t('score.pointsCost', params) : t('score.pointsCredit', params);
  }

  function remediationBlock(rec, signal) {
    if (!rec) return null;
    const effort = rec.effort ? t(`score.effortLevel.${rec.effort}`) : '';
    return el(
      'details',
      { class: 'remediation' },
      el('summary', {}, `${t('score.howToFix')} — ${rec.title}`),
      rec.why ? el('div', { class: 'detail' }, `${t('score.whyItMatters')}: ${rec.why}`) : null,
      rec.how ? el('div', { class: 'detail' }, rec.how) : null,
      el('div', { class: 'inline small dim' },
        effort ? el('span', { class: 'badge outline' }, `${t('score.effort')}: ${effort}`) : null,
        rec.category_gain
          ? el('span', { class: 'badge outline' },
              t('score.fixGain', {
                points: rec.category_gain,
                category: signal.category_id ? catLabel(signal.category_id) : (signal.category_label || ''),
              }))
          : null,
        rec.confidence
          ? el('span', { class: 'badge outline' }, `${t('score.confidence')}: ${Math.round(rec.confidence * 100)}%`)
          : null),
      rec.files && rec.files.length
        ? el('div', { class: 'small dim' }, `${t('score.files')}: ${rec.files.join(', ')}`)
        : null,
    );
  }

  function signalRow(signal, context) {
    const negative = signal.impact < 0;
    const severity = String(signal.severity || 'info').toLowerCase();
    const body = el(
      'div',
      { class: 'body' },
      el(
        'div',
        { class: 'title' },
        el('span', { class: `severity-dot ${sevClass(signal.severity)}` }),
        el('span', { class: `badge ${sevClass(signal.severity)}` }, t(`score.sev.${severity}`)),
        signal.label,
        signal.value !== null && signal.value !== undefined && signal.value !== ''
          ? el('span', { class: 'badge outline' }, `${t('score.measured')}: ${signal.value}`)
          : null,
      ),
      signal.detail ? el('div', { class: 'detail' }, signal.detail) : null,
      el('div', { class: 'detail dim' }, signalNote(signal)),
      severity === 'info' ? null : el('div', { class: 'detail dim' }, t(`score.sevMeaning.${severity}`)),
      remediationBlock(signal.remediation, signal),
    );

    if (signal.evidence && signal.evidence.length) {
      body.append(
        el(
          'details',
          {},
          el('summary', {}, t('score.showEvidence', { n: signal.evidence.length })),
          evidenceList(signal.evidence, context),
        ),
      );
    }

    return el(
      'div',
      { class: `signal ${signal.status}` },
      body,
      el('span', { class: `impact ${negative ? 'neg' : 'pos'}` }, `${negative ? '' : '+'}${signal.impact}`),
    );
  }

  function evidenceList(evidence, context) {
    return el(
      'div',
      { class: 'evidence-list' },
      ...evidence.map((item) =>
        el(
          'div',
          {
            class: 'evidence',
            role: item.file ? 'button' : null,
            tabindex: item.file ? '0' : null,
            style: item.file ? 'cursor:pointer' : '',
            onclick: item.file && context ? () => openEvidence(item, context) : null,
            onkeydown: item.file && context
              ? (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openEvidence(item, context); } }
              : null,
          },
          el('div', { class: 'grow truncate' },
            el('div', {}, item.label),
            item.detail ? el('div', { class: 'small dim' }, item.detail) : null,
            item.snippet ? el('div', { class: 'snippet' }, item.snippet) : null),
          item.value !== undefined && item.value !== null && item.value !== ''
            ? el('span', { class: 'badge outline tabular' }, String(item.value))
            : null,
          item.file ? el('span', { class: 'loc' }, `${fileName(item.file)}${item.line ? `:${item.line}` : ''}`) : null,
        ),
      ),
    );
  }

  function openEvidence(item, context) {
    const view = drawer(item.label || t('score.evidence'));
    view.body.append(skeleton('line', 4));
    api
      .scoreEvidence(context.analysisId, context.categoryId, context.signalId)
      .then((result) => {
        const match = (result.evidence || []).find(
          (row) => row.file === item.file && (!item.line || row.line === item.line),
        ) || item;
        clear(view.body);
        append(
          view.body,
          el('div', { class: 'inline' },
            el('span', { class: 'badge outline mono' }, item.file || ''),
            item.line ? el('span', { class: 'badge outline' }, `${t('score.line')} ${item.line}`) : null),
          match.excerpt && match.excerpt.length
            ? el('div', { class: 'excerpt' }, ...match.excerpt.map((row) =>
                el('div', { class: `ln ${row.highlight ? 'hit' : ''}` },
                  el('span', { class: 'n' }, String(row.line)),
                  el('span', { class: 'code' }, row.text))))
            : item.snippet ? el('pre', {}, item.snippet) : null,
          item.why ? card(t('score.whyItMatters'), { icon: 'info', plain: true }, el('p', {}, item.why)) : null,
          item.fix ? card(t('score.howToFix'), { icon: 'wrench', plain: true }, el('p', {}, item.fix)) : null,
          item.rule ? el('p', { class: 'small dim mono' }, item.rule) : null,
        );
      })
      .catch(() => {
        clear(view.body);
        append(
          view.body,
          el('p', { class: 'muted' }, item.snippet || item.label),
          item.why ? el('p', {}, item.why) : null,
        );
      });
  }

  /* ----------------------------------------------------- recommendations */
  function actionRow(action, index) {
    return el(
      'div',
      { class: 'action' },
      el('div', { class: 'rank' }, String(action.rank || index + 1)),
      el(
        'div',
        {},
        el('h4', {}, action.title),
        el('p', { class: 'small muted' }, action.why),
        el('p', { class: 'small' }, el('strong', {}, `${t('score.howToFix')}: `), action.how),
        el(
          'div',
          { class: 'meta' },
          el('span', { class: 'badge outline' }, catLabel(action.category)),
          el('span', { class: `badge outline prio-${action.priority}` }, t(`score.priority.${action.priority}`)),
          el('span', { class: 'badge outline' }, `${t('score.effort')}: ${t(`score.effortLevel.${action.effort}`)}`),
          el('span', { class: 'badge outline' }, `${t('score.confidence')} ${Math.round(action.confidence * 100)}%`),
        ),
        (action.files || []).length
          ? el('div', { class: 'chips', style: 'margin-top:8px' },
              ...action.files.slice(0, 6).map((file) => el('span', { class: 'chip mono', title: file }, fileName(file))))
          : null,
      ),
      el('div', { class: 'gain' }, el('b', {}, `+${action.overall_gain}`), el('span', {}, t('score.points'))),
    );
  }

  function roadmap(scorecard) {
    const plan = scorecard.roadmap || {};
    const groups = [
      { id: 'quick_wins', label: t('score.quickWins'), hint: t('score.quickWinsHint'), items: plan.quick_wins || [] },
      { id: 'medium_term', label: t('score.mediumTerm'), hint: t('score.mediumTermHint'), items: plan.medium_term || [] },
      { id: 'long_term', label: t('score.longTerm'), hint: t('score.longTermHint'), items: plan.long_term || [] },
    ];
    const total = groups.reduce((sum, group) => sum + group.items.length, 0);
    if (!total) {
      return card(t('score.roadmap'), { icon: 'sparkle' },
        emptyState({ icon: 'check', title: t('score.noActions'), body: t('score.noActionsHint') }));
    }
    return card(t('score.roadmap'), {
      icon: 'sparkle',
      actions: [el('span', { class: 'badge ok' }, t('score.totalGain', { n: plan.total_potential_gain }))],
    },
      ...groups
        .filter((group) => group.items.length)
        .map((group) =>
          el(
            'div',
            { class: 'stack', style: 'margin-bottom:20px' },
            el('div', { class: 'inline' },
              el('h4', {}, group.label),
              el('span', { class: 'small dim' }, group.hint)),
            ...group.items.map(actionRow),
          ),
        ));
  }

  /* --------------------------------------------------------- top issues */
  function topIssues(scorecard, onOpen) {
    const issues = scorecard.top_issues || [];
    if (!issues.length) {
      return card(t('score.topRisks'), { icon: 'alert' },
        emptyState({ icon: 'check', title: t('score.noRisks') }));
    }
    return card(t('score.topRisks'), { icon: 'alert' },
      el('div', { class: 'stack-sm' },
        ...issues.map((issue) =>
          el(
            'button',
            {
              type: 'button',
              class: 'list-item',
              style: 'width:100%;text-align:start',
              onclick: () => onOpen(issue.category),
            },
            el('span', { class: `severity-dot ${sevClass(issue.severity)}` }),
            el('div', { class: 'main' },
              el('div', {}, issue.label),
              el('div', { class: 'small muted' }, issue.detail || '')),
            el('span', { class: 'badge outline' }, catLabel(issue.category)),
            el('span', { class: 'delta down' }, `${issue.impact}`),
          ))));
  }

  function strengths(scorecard) {
    const items = scorecard.strengths || [];
    if (!items.length) return null;
    return card(t('score.strengths'), { icon: 'check' },
      el('div', { class: 'stack-sm' },
        ...items.map((item) =>
          el('div', { class: 'signal pass' },
            el('div', { class: 'body' },
              el('div', { class: 'title' }, item.label),
              item.detail ? el('div', { class: 'detail' }, item.detail) : null),
            el('span', { class: 'impact pos' }, `+${item.impact}`)))));
  }

  /* --------------------------------------------------- explorer (drawer) */
  function openExplorer(analysisId, categoryId) {
    const view = drawer(catLabel(categoryId));
    view.body.append(skeleton('line', 3), skeleton('block'));
    api
      .scoreCategory(analysisId, categoryId)
      .then(({ category }) => {
        const context = { analysisId, categoryId };
        let tab = 'issues';
        const panelHost = el('div');
        const tabHost = el('div');

        const tabItems = () => [
          { id: 'issues', label: `${t('score.issues')} (${category.issues.length})` },
          { id: 'strengths', label: `${t('score.strengths')} (${category.strengths.length})` },
          { id: 'actions', label: `${t('score.actions')} (${category.recommendations.length})` },
          { id: 'metrics', label: t('score.metrics') },
        ];

        const drawTabs = () => {
          clear(tabHost).append(tabs(tabItems(), tab, (next) => { tab = next; drawTabs(); drawTab(); }));
        };

        const drawTab = () => {
          clear(panelHost);
          if (tab === 'issues') {
            panelHost.append(
              category.issues.length
                ? el('div', { class: 'stack-sm' },
                    ...category.issues.map((signal) => signalRow(signal, { ...context, signalId: signal.id })))
                : emptyState({ icon: 'check', title: t('score.noIssuesIn', { name: catLabel(categoryId) }) }),
            );
          } else if (tab === 'strengths') {
            panelHost.append(
              category.strengths.length
                ? el('div', { class: 'stack-sm' }, ...category.strengths.map((signal) => signalRow(signal, context)))
                : emptyState({ icon: 'inbox', title: t('score.noStrengths') }),
            );
          } else if (tab === 'actions') {
            panelHost.append(
              category.recommendations.length
                ? el('div', { class: 'stack' }, ...category.recommendations.map((rec, index) =>
                    actionRow({ ...rec, overall_gain: Math.round(rec.category_gain * category.weight * 10) / 10, priority: rec.priority || 'medium' }, index)))
                : emptyState({ icon: 'check', title: t('score.noActions') }),
            );
          } else {
            panelHost.append(metricsTable(category.metrics));
          }
        };

        clear(view.body);
        append(
          view.body,
          el(
            'div',
            { class: `card ${bandClass(category.score)}`, style: 'display:flex;gap:20px;align-items:center' },
            el('div', { style: 'min-width:120px' }, charts.gauge({
              value: category.score, max: 100, size: 128, thickness: 11,
              label: t('score.outOf', { max: 100 }), sublabel: category.grade,
            }).node),
            el('div', { class: 'grow' },
              el('p', {}, category.summary),
              el('div', { class: 'inline small muted', style: 'margin-top:8px' },
                el('span', { class: 'badge outline' }, t('score.weightIs', { pct: category.weight_pct })),
                el('span', { class: 'badge outline' }, t('score.contributes', { n: category.contribution })),
                el('span', { class: 'badge outline' }, `${t('score.confidence')} ${Math.round(category.confidence * 100)}%`))),
          ),
          (category.unmeasured || []).length
            ? el('div', { class: 'panel small muted' },
                icon('info', { size: 14 }), ' ', category.unmeasured.join(' '))
            : null,
          tabHost,
          panelHost,
        );

        drawTabs();
        drawTab();
      })
      .catch((error) => {
        clear(view.body).append(emptyState({ icon: 'alert', title: t('common.error'), body: String(error.message || error) }));
      });
  }

  function metricsTable(metrics) {
    const rows = Object.entries(metrics || {}).filter(([, value]) => typeof value !== 'object' || value === null);
    const nested = Object.entries(metrics || {}).filter(([, value]) => value && typeof value === 'object' && !Array.isArray(value));
    if (!rows.length && !nested.length) return emptyState({ icon: 'inbox', title: t('common.none') });
    return el(
      'div',
      { class: 'stack' },
      rows.length
        ? el('table', {}, el('tbody', {}, ...rows.map(([key, value]) =>
            el('tr', {}, el('td', { class: 'muted' }, humanise(key)), el('td', { class: 'tabular' }, String(value))))))
        : null,
      ...nested.map(([key, value]) =>
        el('div', {},
          el('h4', { style: 'margin-bottom:8px' }, humanise(key)),
          el('table', {}, el('tbody', {}, ...Object.entries(value).map(([k, v]) =>
            el('tr', {}, el('td', { class: 'muted' }, humanise(k)), el('td', { class: 'tabular' }, String(v)))))))),
    );
  }

  function humanise(key) {
    return String(key).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
  }

  /* --------------------------------------------------------------- trend */
  function trendCharts(trend, onPoint) {
    const points = trend.points || [];
    if (points.length < 2) {
      return card(t('score.history'), { icon: 'history' },
        emptyState({ icon: 'history', title: t('score.needTwoRuns'), body: t('score.needTwoRunsHint') }));
    }
    const labels = points.map((point) => (point.at || '').slice(0, 10));
    const overall = charts.line({
      series: [{ name: t('score.overallTitle'), values: points.map((p) => p.overall), color: 'var(--accent)' }],
      labels,
      yMin: 0,
      yMax: 100,
      width: 720,
      height: 260,
      onPoint,
    });
    const keys = ['security', 'code_quality', 'testing', 'technical_debt', 'architecture'];
    const perCategory = charts.line({
      series: keys.map((key, index) => ({
        name: catLabel(key),
        values: points.map((p) => (p.categories || {})[key] ?? null),
        color: charts.PALETTE[index % charts.PALETTE.length],
      })),
      labels,
      yMin: 0,
      yMax: 100,
      area: false,
      width: 720,
      height: 280,
    });
    const deltas = trend.deltas || {};
    return el(
      'div',
      { class: 'stack-lg' },
      card(t('score.history'), {
        icon: 'history',
        actions: deltas.overall === undefined ? [] : [deltaLabel(deltas.overall, { suffix: ' pts' })],
      }, charts.panel(t('score.overallTitle'), overall, { subtitle: t('score.historyHint') })),
      card(t('score.categoryTrends'), { icon: 'chart' },
        charts.panel(t('score.categoryTrends'), perCategory, { subtitle: t('score.legendHint') })),
    );
  }

  /* ---------------------------------------------------------- weights UI */
  function weightsEditor(onSaved) {
    const host = el('div', { class: 'stack' }, skeleton('line', 8));
    const reload = () => {
      api
        .scoreWeights()
        .then(({ weights, defaults, categories }) => {
          const inputs = {};
          const totalLabel = el('span', { class: 'badge' });
          const refreshTotal = () => {
            const sum = Object.values(inputs).reduce((acc, input) => acc + Number(input.value || 0), 0);
            totalLabel.textContent = t('score.weightTotal', { n: Math.round(sum * 100) });
            totalLabel.className = `badge ${Math.abs(sum - 1) < 0.005 ? 'ok' : 'warn'}`;
          };
          const rows = categories.map((category) => {
            const input = el('input', {
              type: 'range', min: '0', max: '0.4', step: '0.01',
              value: String(weights[category.id]),
              oninput: () => { readout.textContent = `${Math.round(input.value * 100)}%`; refreshTotal(); },
            });
            const readout = el('span', { class: 'badge outline tabular' },
              `${Math.round(weights[category.id] * 100)}%`);
            inputs[category.id] = input;
            return el('div', { class: 'weight-row' },
              el('span', { class: 'weight-name' }, catLabel(category.id)),
              el('div', { class: 'weight-slider' }, input),
              readout,
              el('span', { class: 'small dim weight-default' }, `${t('score.default')} ${Math.round(defaults[category.id] * 100)}%`));
          });
          refreshTotal();
          clear(host).append(
            el('p', { class: 'small muted' }, t('score.weightsHint')),
            el('div', { class: 'weight-list' }, ...rows),
            el('div', { class: 'inline settings-actions' },
              totalLabel,
              el('span', { class: 'grow' }),
              el('button', {
                class: 'btn',
                onclick: () => api.resetScoreWeights().then(() => { toast(t('common.saved'), 'success'); reload(); if (onSaved) onSaved(); }),
              }, t('score.resetWeights')),
              el('button', {
                class: 'btn primary',
                onclick: () => {
                  const payload = {};
                  Object.entries(inputs).forEach(([key, input]) => { payload[key] = Number(input.value); });
                  api.saveScoreWeights(payload).then(() => {
                    toast(t('score.weightsSaved'), 'success');
                    reload();
                    if (onSaved) onSaved();
                  }).catch((error) => toast(String(error.message || error), 'error'));
                },
              }, t('common.save'))),
          );
        })
        .catch((error) => clear(host).append(el('p', { class: 'muted' }, String(error.message || error))));
    };
    reload();
    return host;
  }

  AAI.score = {
    hero,
    categoryCards,
    radarPanel,
    contributionPanel,
    topIssues,
    strengths,
    roadmap,
    actionRow,
    signalRow,
    openExplorer,
    trendCharts,
    weightsEditor,
    catLabel,
    EFFORT_ORDER,
  };
})();
