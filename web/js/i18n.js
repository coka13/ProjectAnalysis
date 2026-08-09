/**
 * Runtime localisation with LTR/RTL switching.
 * Bundles are preloaded as plain scripts into window.AAI_I18N - no fetch(),
 * because file:// documents cannot make network requests.
 */
(function () {
  const AAI = (window.AAI = window.AAI || {});
  const bundles = window.AAI_I18N || {};
  const RTL_LANGUAGES = new Set(['he', 'ar', 'fa', 'ur']);
  const listeners = new Set();

  const SUPPORTED = [
    { code: 'en', label: 'English' },
    { code: 'he', label: 'עברית' },
  ];

  let current = 'en';
  try {
    const stored = localStorage.getItem('aai.language');
    if (stored && bundles[stored]) current = stored;
  } catch {
    /* localStorage can be unavailable for file:// in some engines */
  }

  const language = () => current;
  const isRTL = (lang) => RTL_LANGUAGES.has(lang || current);
  const direction = (lang) => (isRTL(lang) ? 'rtl' : 'ltr');

  /** Translate a key, interpolating {placeholders}. Falls back to English, then the key. */
  function t(key, params) {
    const bundle = bundles[current] || {};
    const value = key in bundle ? bundle[key] : (bundles.en && bundles.en[key]) || key;
    if (!params) return value;
    return String(value).replace(/\{(\w+)\}/g, (match, name) =>
      name in params ? String(params[name]) : match,
    );
  }

  function setLanguage(lang) {
    if (!bundles[lang]) return;
    current = lang;
    try {
      localStorage.setItem('aai.language', lang);
    } catch { /* ignore */ }
    document.documentElement.lang = lang;
    document.documentElement.dir = direction(lang);
    applyStatic();
    listeners.forEach((fn) => fn(lang));
  }

  function onLanguageChange(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  /** Apply translations to any element carrying data-i18n attributes. */
  function applyStatic(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach((node) => {
      node.textContent = t(node.dataset.i18n);
    });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach((node) => {
      node.placeholder = t(node.dataset.i18nPlaceholder);
    });
    scope.querySelectorAll('[data-i18n-title]').forEach((node) => {
      node.title = t(node.dataset.i18nTitle);
    });
    // Screen reader names have to follow the language too. A control whose
    // visible label is Hebrew but whose aria-label is still English is
    // announced in the wrong language and cannot be found by voice.
    scope.querySelectorAll('[data-i18n-aria]').forEach((node) => {
      node.setAttribute('aria-label', t(node.dataset.i18nAria));
    });
  }

  function init() {
    document.documentElement.lang = current;
    document.documentElement.dir = direction(current);
  }

  AAI.i18n = { SUPPORTED, language, isRTL, direction, t, setLanguage, onLanguageChange, applyStatic, init };
})();
