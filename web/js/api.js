/**
 * Client for the Python bridge. Every call goes through window.pywebview.api,
 * which runs the matching method on a Python worker thread and resolves with
 * {ok, data} or {ok, error}. There is no HTTP anywhere in this application.
 */
(function () {
  const AAI = (window.AAI = window.AAI || {});

  class BridgeError extends Error {}

  let readyPromise = null;

  function ready() {
    if (window.pywebview && window.pywebview.api) return Promise.resolve();
    if (!readyPromise) {
      readyPromise = new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => reject(new BridgeError('The application backend did not start.')),
          20000,
        );
        window.addEventListener(
          'pywebviewready',
          () => {
            clearTimeout(timer);
            resolve();
          },
          { once: true },
        );
      });
    }
    return readyPromise;
  }

  async function call(name, payload) {
    await ready();
    const fn = window.pywebview.api[name];
    if (typeof fn !== 'function') throw new BridgeError(`Unknown backend method: ${name}`);
    const result = payload === undefined ? await fn() : await fn(payload);
    if (!result || result.ok !== true) {
      throw new BridgeError((result && result.error) || 'Unknown error');
    }
    return result.data;
  }

  /**
   * Read-only endpoints that are expensive and safe to share between callers.
   *
   * These shell out to git. Re-rendering a view - which is what switching the
   * language does - would otherwise fire a second `git log` while the first is
   * still running; the two compete for the same repository and one of them can
   * hit the git timeout, at which point history reports itself as unavailable.
   */
  const SHAREABLE = new Set([
    'analysis_history',
    'analysis_commit_graph',
    'analysis_metrics',
    'analysis_fix_proposals',
    'score_card',
  ]);

  /** In-flight shareable calls, keyed by endpoint + arguments. */
  const inFlight = new Map();

  /**
   * Run a call, joining onto an identical one that has not finished yet.
   *
   * Only the promise is shared, never a settled result, so this is a
   * concurrency guard and not a cache: a later call still re-reads the data.
   */
  function sharedCall(name, payload) {
    if (!SHAREABLE.has(name)) return call(name, payload);
    const key = `${name}:${JSON.stringify(payload === undefined ? null : payload)}`;
    const existing = inFlight.get(key);
    if (existing) return existing;
    const promise = call(name, payload).finally(() => {
      if (inFlight.get(key) === promise) inFlight.delete(key);
    });
    inFlight.set(key, promise);
    return promise;
  }

  /** Turn a Blob into base64 so binary data can cross the bridge as text. */
  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(new BridgeError('Could not read the generated file'));
      reader.readAsDataURL(blob);
    });
  }

  function bytesToBase64(bytes) {
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  AAI.BridgeError = BridgeError;
  AAI.api = {
    ready,
    call,
    blobToBase64,
    bytesToBase64,

    health: () => call('health'),
    settings: () => call('settings_summary'),

    pickFolder: () => call('pick_folder'),
    saveText: (filename, content) => call('save_file', { filename, content }),
    saveBinary: (filename, base64) => call('save_file', { filename, base64 }),

    projects: () => call('projects_list'),
    createProject: (payload) => call('project_create', payload),
    updateProject: (payload) => call('project_update', payload),
    deleteProject: (id) => call('project_delete', { project_id: id }),
    refs: (id) => call('project_refs', { project_id: id }),

    startAnalysis: (payload) => call('analysis_start', payload),
    analysis: (id) => call('analysis_status', { analysis_id: id }),
    cancelAnalysis: (id) => call('analysis_cancel', { analysis_id: id }),
    analyses: (projectId) => call('analyses_list', { project_id: projectId }),
    deleteAnalysis: (id) => call('analysis_delete', { analysis_id: id }),
    metrics: (id) => sharedCall('analysis_metrics', { analysis_id: id }),
    history: (id) => sharedCall('analysis_history', { analysis_id: id }),
    commitGraph: (id, limit) => sharedCall('analysis_commit_graph', { analysis_id: id, limit }),
    fixProposals: (id, limit, options) =>
      sharedCall('analysis_fix_proposals', {
        analysis_id: id,
        limit,
        language: (options && options.language) || undefined,
        include_cosmetic: !!(options && options.includeCosmetic),
        offline: !!(options && options.offline),
      }),
    fixPreview: (id, file, rules) => call('analysis_fix_preview', { analysis_id: id, file, rules }),
    // `confirm` is mandatory on the backend too - fixes are never auto-applied.
    applyFixes: (id, selections) => call('analysis_apply_fixes', { analysis_id: id, selections, confirm: true }),
    graph: (id, limit) => call('analysis_graph', { analysis_id: id, limit }),
    searchGraph: (id, query) => call('analysis_search', { analysis_id: id, query }),

    scoreCard: (id) => sharedCall('score_card', { analysis_id: id }),
    scoreCategory: (id, category) => call('score_category', { analysis_id: id, category }),
    scoreEvidence: (id, category, signal) => call('score_evidence', { analysis_id: id, category, signal }),
    scoreWeights: () => call('score_weights'),
    saveScoreWeights: (weights) => call('score_weights_save', { weights }),
    resetScoreWeights: () => call('score_weights_reset'),
    scoreRecompute: (id) => call('score_recompute', { analysis_id: id }),
    scoreTrend: (payload) => call('score_trend', payload),
    scoreFiles: (id, limit) => call('score_files', { analysis_id: id, limit }),
    scoreFileDetail: (id, file) => call('score_file_detail', { analysis_id: id, file }),

    diagramKinds: () => call('diagram_kinds'),
    diagrams: (analysisId) => call('diagrams_list', { analysis_id: analysisId }),
    diagram: (id) => call('diagram_get', { diagram_id: id }),
    generateDiagram: (payload) => call('diagram_generate', payload),
    updateDiagram: (payload) => call('diagram_update', payload),
    versions: (id) => call('diagram_versions', { diagram_id: id }),
    restoreVersion: (versionId) => call('diagram_restore', { version_id: versionId }),
    setApproval: (id, state) => call('diagram_approval', { diagram_id: id, state }),

    comments: (id) => call('comments_list', { diagram_id: id }),
    addComment: (id, body) => call('comment_add', { diagram_id: id, body }),
    toggleComment: (id) => call('comment_toggle', { comment_id: id }),
    deleteComment: (id) => call('comment_delete', { comment_id: id }),

    explain: (payload) => call('ai_explain', payload),
    review: (payload) => call('ai_review', payload),
    refactor: (payload) => call('ai_refactor', payload),
    askForDiagram: (payload) => call('ai_query', payload),
    translate: (payload) => call('ai_translate', payload),

    provider: () => call('provider_get'),
    saveProvider: (payload) => call('provider_save', payload),
    testProvider: () => call('provider_test'),
    clearProvider: () => call('provider_clear'),

    exportDiagram: (payload) => call('export_diagram', payload),
    exportBundle: (payload) => call('export_bundle', payload),
    compare: (payload) => call('compare_analyses', payload),
  };
})();
