/**
 * Command palette (Ctrl/Cmd+K) and the global keyboard shortcut map.
 * Commands are contributed by the shell; the palette itself owns no state
 * beyond the current query and selection.
 */
(function () {
  const AAI = (window.AAI = window.AAI || {});
  const { el, clear } = AAI.dom;

  const providers = [];
  let open = null;

  /** A provider returns an array of {id, label, hint, group, run, keywords}. */
  function register(provider) {
    providers.push(provider);
    return () => {
      const index = providers.indexOf(provider);
      if (index >= 0) providers.splice(index, 1);
    };
  }

  function collect() {
    const out = [];
    providers.forEach((provider) => {
      try {
        const items = provider();
        if (Array.isArray(items)) out.push(...items);
      } catch {
        /* a broken provider must not break the palette */
      }
    });
    return out;
  }

  /** Fuzzy subsequence match with a light score: earlier + contiguous wins. */
  function match(query, text) {
    if (!query) return 0;
    const haystack = text.toLowerCase();
    const needle = query.toLowerCase();
    const direct = haystack.indexOf(needle);
    if (direct >= 0) return 1000 - direct;
    let index = 0;
    let score = 0;
    let last = -1;
    for (const character of needle) {
      const found = haystack.indexOf(character, index);
      if (found < 0) return -1;
      score += found === last + 1 ? 4 : 1;
      last = found;
      index = found + 1;
    }
    return score;
  }

  function show() {
    if (open) return;
    const previous = document.activeElement;
    const backdrop = el('div', { class: 'palette-backdrop' });
    const input = el('input', {
      type: 'text',
      autocomplete: 'off',
      spellcheck: false,
      'aria-label': AAI.i18n.t('palette.placeholder'),
      'data-i18n-placeholder': 'palette.placeholder',
      placeholder: AAI.i18n.t('palette.placeholder'),
    });
    const listBox = el('div', { class: 'palette-list', role: 'listbox' });
    const panel = el('div', { class: 'palette', role: 'dialog', 'aria-modal': 'true' }, input, listBox);
    backdrop.append(panel);

    let items = [];
    let selected = 0;

    const close = () => {
      backdrop.remove();
      document.removeEventListener('keydown', onKey, true);
      open = null;
      if (previous && previous.focus) previous.focus();
    };

    const runSelected = () => {
      const item = items[selected];
      if (!item) return;
      close();
      try { item.run(); } catch (error) { AAI.dom.toast(String(error && error.message ? error.message : error), 'error'); }
    };

    const draw = () => {
      const query = input.value.trim();
      items = collect()
        .map((item) => ({ item, score: query ? match(query, `${item.label} ${item.group || ''} ${item.keywords || ''}`) : 0 }))
        .filter((row) => row.score >= 0)
        .sort((a, b) => b.score - a.score)
        .map((row) => row.item)
        .slice(0, 40);
      if (selected >= items.length) selected = Math.max(0, items.length - 1);
      clear(listBox);
      rows = [];
      if (!items.length) {
        listBox.append(el('div', { class: 'palette-empty' }, AAI.i18n.t('palette.noResults')));
        return;
      }
      let group = null;
      const buttons = [];
      items.forEach((item, index) => {
        if (item.group && item.group !== group) {
          group = item.group;
          listBox.append(el('div', { class: 'palette-group' }, group));
        }
        const button = el(
          'button',
          {
            type: 'button',
            class: 'palette-item',
            role: 'option',
            // Moving the mouse must not rebuild the list: repainting dozens of
            // rows on every mousemove caused visible flicker and stole the
            // scroll position. Only the selection classes are touched.
            onmousemove: () => select(index, false),
            onclick: runSelected,
          },
          item.icon ? AAI.dom.icon(item.icon, { size: 15 }) : null,
          el('span', { class: 'label' }, item.label),
          item.hint ? el('span', { class: 'hint' }, item.hint) : null,
        );
        buttons.push(button);
        listBox.append(button);
      });
      rows = buttons;
      paintSelection(true);
    };

    let rows = [];

    function paintSelection(scroll) {
      rows.forEach((button, index) => {
        const on = index === selected;
        button.classList.toggle('sel', on);
        button.setAttribute('aria-selected', on ? 'true' : 'false');
        if (on && scroll) button.scrollIntoView({ block: 'nearest' });
      });
    }

    function select(index, scroll) {
      if (index === selected || index < 0 || index >= rows.length) return;
      selected = index;
      paintSelection(scroll);
    }

    const onKey = (event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
      else if (event.key === 'ArrowDown') { event.preventDefault(); select(Math.min(selected + 1, rows.length - 1), true); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); select(Math.max(selected - 1, 0), true); }
      else if (event.key === 'Home') { event.preventDefault(); select(0, true); }
      else if (event.key === 'End') { event.preventDefault(); select(rows.length - 1, true); }
      else if (event.key === 'Enter') { event.preventDefault(); runSelected(); }
      else if (event.key === 'Tab') { event.preventDefault(); }
    };

    input.addEventListener('input', () => { selected = 0; draw(); });
    backdrop.addEventListener('mousedown', (event) => { if (event.target === backdrop) close(); });
    document.addEventListener('keydown', onKey, true);
    document.body.append(backdrop);
    open = { close };
    draw();
    input.focus();
  }

  function toggle() {
    if (open) open.close();
    else show();
  }

  /* ------------------------------------------------------------ shortcuts */
  const shortcuts = [];

  function bind(combo, description, run, group) {
    shortcuts.push({ combo, description, run, group: group || 'general' });
  }

  function comboOf(event) {
    const parts = [];
    if (event.ctrlKey || event.metaKey) parts.push('mod');
    if (event.shiftKey) parts.push('shift');
    if (event.altKey) parts.push('alt');
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    parts.push(key);
    return parts.join('+');
  }

  const isTyping = (target) =>
    target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable);

  document.addEventListener('keydown', (event) => {
    const combo = comboOf(event);
    if (combo === 'mod+k') { event.preventDefault(); toggle(); return; }
    if (open) return;
    if (isTyping(event.target) && !combo.startsWith('mod')) return;
    const hit = shortcuts.find((item) => item.combo === combo);
    if (!hit) return;
    event.preventDefault();
    try { hit.run(); } catch (error) { AAI.dom.toast(String(error && error.message ? error.message : error), 'error'); }
  });

  function help() {
    const { t } = AAI.i18n;
    const rows = [{ combo: 'mod+k', description: t('palette.title'), group: 'general' }, ...shortcuts];
    const pretty = (combo) =>
      combo.split('+').map((part) => (part === 'mod' ? 'Ctrl' : part.charAt(0).toUpperCase() + part.slice(1))).join(' + ');
    AAI.dom.modal(
      t('shortcuts.title'),
      el(
        'div',
        { class: 'stack-sm' },
        ...rows.map((row) =>
          el(
            'div',
            { class: 'stat-row' },
            el('span', { class: 'k' }, row.description),
            el('span', {}, el('span', { class: 'kbd' }, pretty(row.combo))),
          ),
        ),
      ),
      [{ label: t('common.close'), primary: true, onClick: (close) => close() }],
    );
  }

  AAI.palette = { register, show, toggle, bind, help, shortcuts };
})();
