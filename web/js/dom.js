/** Small DOM helpers shared by the views. Exposed as window.AAI.dom. */
(function () {
  const AAI = (window.AAI = window.AAI || {});

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key.startsWith('on') && typeof value === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key === 'html') node.innerHTML = value;
      else if (key in node && key !== 'list') node[key] = value;
      else node.setAttribute(key, value);
    }
    return append(node, ...children);
  }

  /**
   * Add children to an existing node, skipping the absent ones.
   *
   * The native append() turns a null argument into the text "null", so a block
   * written as `condition ? node : null` prints that word on the page whenever
   * the condition is false. Views must use this instead - `tools/audit_ui.py`
   * fails the build if a literal null reaches the native method.
   */
  function append(node, ...children) {
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }

  /* ------------------------------------------------------- overlay helpers */

  /** Elements that can hold keyboard focus, in document order. */
  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
    'select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

  function focusables(root) {
    return Array.from(root.querySelectorAll(FOCUSABLE)).filter(
      (node) => node.offsetParent !== null || node === document.activeElement,
    );
  }

  /** Keep Tab inside `panel` so an overlay never leaks focus to the page behind it. */
  function trapTab(panel, event) {
    if (event.key !== 'Tab') return;
    const items = focusables(panel);
    if (!items.length) {
      event.preventDefault();
      panel.focus();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  /**
   * Overlays (modals and drawers) are stacked. Only the topmost one reacts to
   * Escape, otherwise a single keypress would dismiss the whole stack.
   */
  const overlays = [];

  function pushOverlay(entry) {
    overlays.push(entry);
    document.body.classList.add('overlay-open');
    return () => {
      const index = overlays.indexOf(entry);
      if (index >= 0) overlays.splice(index, 1);
      if (!overlays.length) document.body.classList.remove('overlay-open');
    };
  }

  document.addEventListener(
    'keydown',
    (event) => {
      const top = overlays[overlays.length - 1];
      if (!top) return;
      if (event.key === 'Escape' && top.onEscape) {
        event.preventDefault();
        event.stopPropagation();
        top.onEscape();
      } else if (event.key === 'Tab') {
        trapTab(top.panel, event);
      }
    },
    true,
  );

  const MAX_TOASTS = 4;

  function toast(message, kind) {
    const host = document.getElementById('toasts');
    if (!host) return () => {};
    while (host.childElementCount >= MAX_TOASTS) host.firstElementChild.remove();

    const item = el('div', {
      class: `toast ${kind || ''}`,
      role: kind === 'error' ? 'alert' : 'status',
    });
    let timer = 0;
    const dismiss = () => {
      clearTimeout(timer);
      item.remove();
    };
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(dismiss, kind === 'error' ? 8000 : 4500);
    };
    item.append(
      el('span', { class: 'msg' }, String(message)),
      el(
        'button',
        {
          class: 'close',
          type: 'button',
          'aria-label': (AAI.i18n && AAI.i18n.t('common.close')) || 'Close',
          onclick: dismiss,
        },
        '×',
      ),
    );
    // Hovering a toast holds it open so long messages stay readable.
    item.addEventListener('mouseenter', () => clearTimeout(timer));
    item.addEventListener('mouseleave', arm);
    item.addEventListener('focusin', () => clearTimeout(timer));
    item.addEventListener('focusout', arm);
    host.append(item);
    arm();
    return dismiss;
  }

  let modalSeq = 0;

  /**
   * Modal dialog.
   *
   * Escape closes it, Enter activates the primary action (unless the caret is
   * in a textarea), Tab is trapped inside, focus is restored on close, and an
   * action whose `onClick` returns a promise puts the dialog in a busy state so
   * it cannot be submitted twice.
   */
  function modal(title, body, actions, options) {
    const opt = options || {};
    const titleId = `modal-title-${(modalSeq += 1)}`;
    const previous = document.activeElement;
    const backdrop = el('div', { class: 'modal-backdrop' });

    let releaseOverlay = null;
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      backdrop.remove();
      if (releaseOverlay) releaseOverlay();
      if (previous && previous.focus) previous.focus();
    };

    const buttons = (actions || []).map((action) => {
      const button = el(
        'button',
        {
          class: `btn ${action.primary ? 'primary' : ''} ${action.danger ? 'danger' : ''}`,
          type: 'button',
        },
        action.label,
      );
      button.addEventListener('click', () => {
        if (busy) return;
        const result = action.onClick(close);
        if (result && typeof result.then === 'function') {
          setBusy(true, button);
          result.finally(() => setBusy(false, button));
        }
      });
      button.dataset.primary = action.primary ? '1' : '';
      return button;
    });

    let busy = false;
    function setBusy(next, activeButton) {
      busy = next;
      buttons.forEach((button) => { button.disabled = next; });
      if (activeButton) activeButton.classList.toggle('busy', next);
      box.classList.toggle('is-busy', next);
    }

    const box = el(
      'div',
      {
        class: `modal ${opt.wide ? 'wide' : ''}`,
        role: 'dialog',
        'aria-modal': 'true',
        'aria-labelledby': titleId,
        tabindex: '-1',
      },
      el('h3', { id: titleId }, title),
      body,
      buttons.length ? el('div', { class: 'inline modal-actions' }, ...buttons) : null,
    );

    box.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || busy) return;
      const target = event.target;
      if (target && (target.tagName === 'TEXTAREA' || target.tagName === 'BUTTON')) return;
      const primary = buttons.find((button) => button.dataset.primary === '1');
      if (!primary || primary.disabled) return;
      event.preventDefault();
      primary.click();
    });

    // Only a press *and* release on the backdrop dismisses, so selecting text
    // inside the dialog and releasing outside it does not throw the work away.
    let pressedOnBackdrop = false;
    backdrop.addEventListener('mousedown', (event) => { pressedOnBackdrop = event.target === backdrop; });
    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop && pressedOnBackdrop && !busy) close();
      pressedOnBackdrop = false;
    });

    backdrop.append(box);
    document.body.append(backdrop);
    releaseOverlay = pushOverlay({ panel: box, onEscape: () => { if (!busy) close(); } });

    const first = focusables(box).find((node) => !buttons.includes(node)) || buttons[0] || box;
    first.focus();
    return close;
  }

  function confirmDialog(message, onYes, options) {
    const opt = options || {};
    const t = AAI.i18n.t;
    modal(opt.title || t('common.confirm'), el('p', {}, message), [
      { label: t('common.cancel'), onClick: (close) => close() },
      {
        label: opt.confirmLabel || t('common.yes'),
        primary: true,
        danger: opt.danger !== false,
        onClick: (close) => { close(); onYes(); },
      },
    ]);
  }

  function field(label, input, options) {
    const opt = options || {};
    const wrap = el(
      'div',
      { class: 'field' },
      el('label', {}, label, opt.required ? el('span', { class: 'req', 'aria-hidden': 'true' }, '*') : null),
      input,
      opt.hint ? el('span', { class: 'hint' }, opt.hint) : null,
    );
    if (opt.required && input) input.required = true;
    return wrap;
  }

  /**
   * Show or clear an inline validation message under a control. Returns false
   * when a message was shown, so callers can `if (!setFieldError(...)) return;`.
   */
  function setFieldError(input, message) {
    const wrap = input.closest('.field') || input.parentElement;
    if (!wrap) return !message;
    const existing = wrap.querySelector(':scope > .field-error');
    if (existing) existing.remove();
    if (!message) {
      input.removeAttribute('aria-invalid');
      return true;
    }
    input.setAttribute('aria-invalid', 'true');
    wrap.append(el('div', { class: 'field-error', role: 'alert' }, icon('alert', { size: 12 }), message));
    return false;
  }

  /** Validate a list of `{input, message, test}` rules; focuses the first failure. */
  function validate(rules) {
    let firstBad = null;
    rules.forEach((rule) => {
      const ok = rule.test ? rule.test() : String(rule.input.value || '').trim() !== '';
      setFieldError(rule.input, ok ? '' : rule.message);
      if (!ok && !firstBad) firstBad = rule.input;
    });
    if (firstBad) firstBad.focus();
    return !firstBad;
  }

  function badgeFor(status) {
    const map = {
      succeeded: 'ok', approved: 'ok', running: 'info', pending: 'info',
      in_review: 'warn', failed: 'err', rejected: 'err', cancelled: 'warn',
    };
    return map[status] || '';
  }

  function fmtDate(value) {
    if (!value) return '';
    try {
      return new Date(value).toLocaleString();
    } catch {
      return String(value);
    }
  }

  function list(items, renderer, emptyText) {
    if (!items || items.length === 0) return el('p', { class: 'muted small' }, emptyText);
    return el('div', {}, ...items.map(renderer));
  }

  function spinner() {
    return el('span', { class: 'spinner' });
  }

  /* --------------------------------------------------------------- icons */
  const ICON_PATHS = {
    dashboard: 'M3 3h7v8H3V3zm0 10h7v8H3v-8zm10 0h8v8h-8v-8zm0-10h8v6h-8V3z',
    folder: 'M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6z',
    report: 'M6 2h9l5 5v15H6V2zm8 1v5h5M9 12h8M9 16h8M9 8h3',
    layers: 'M12 3 3 8l9 5 9-5-9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5',
    // The product mark, and nothing else: three connected nodes, which is what
    // the application actually produces. It deliberately shares no shape with
    // `layers` or `sidebar` - those three used to be the same glyph, so the
    // brand plate and the sidebar button sat side by side in the top bar as
    // two copies of one mark in two different colours.
    appmark:
      'M9.4 6a2.6 2.6 0 1 0 5.2 0 2.6 2.6 0 1 0-5.2 0M3 18a2.6 2.6 0 1 0 5.2 0 2.6 2.6 0 1 0-5.2 0'
      + 'M15.8 18a2.6 2.6 0 1 0 5.2 0 2.6 2.6 0 1 0-5.2 0M10.8 8.3 6.8 15.7M13.2 8.3 17.2 15.7M8.2 18h7.6',
    // A panel with its rail, the shape every editor uses for this control.
    // Anything with stacked horizontal bars would read as a hamburger again.
    sidebar: 'M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM9.5 4v16',
    code: 'M9 6 4 12l5 6m6-12 5 6-5 6',
    shield: 'M12 3 5 6v5c0 4.6 3 8.5 7 10 4-1.5 7-5.4 7-10V6l-7-3z',
    beaker: 'M9 3v6L4 19a2 2 0 0 0 1.8 3h12.4A2 2 0 0 0 20 19l-5-10V3M8 3h8',
    book: 'M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5zm2 14h13',
    wrench: 'M21 4a6 6 0 0 1-8 8L5 20a2.8 2.8 0 0 1-4-4l8-8a6 6 0 0 1 8-8l-3.5 3.5 2 2L21 4z',
    gauge: 'M12 21a9 9 0 1 1 9-9M12 12l5-4',
    hourglass: 'M7 3h10M7 21h10M8 3c0 5 8 5 8 9s-8 4-8 9M16 3c0 5-8 5-8 9s8 4 8 9',
    chart: 'M4 20V10m5 10V4m5 16v-7m5 7V8',
    history: 'M3 12a9 9 0 1 0 3-6.7M3 4v5h5m4-1v5l4 2',
    compare: 'M9 3v18M15 3v18M3 8h6m6 8h6',
    // A cog drawn from exact polar coordinates about (12,12): a hub, a ring and
    // eight teeth at 45 degree intervals. The previous path was a mangled copy
    // of the Feather gear whose arcs reached x=1 and y=1, so it sat visibly
    // off-centre and lopsided. Geometry beats a hand-tweaked bezier here.
    settings:
      'M15.2 12a3.2 3.2 0 1 1-6.4 0 3.2 3.2 0 1 1 6.4 0M19 12a7 7 0 1 1-14 0 7 7 0 1 1 14 0'
      + 'M19 12h2.5M17 17l1.7 1.7M12 19v2.5M7 17l-1.7 1.7M5 12H2.5M7 7 5.3 5.3M12 5V2.5M17 7l1.7-1.7',
    sparkle: 'M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z',
    search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm10 2-4.3-4.3',
    check: 'm4 12 5 5L20 6',
    alert: 'M12 3 2 20h20L12 3zm0 6v5m0 3v.5',
    cross: 'M6 6l12 12M18 6 6 18',
    info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm0-13v.5m0 3.5v5',
    arrow: 'M5 12h14m-6-6 6 6-6 6',
    plus: 'M12 5v14M5 12h14',
    play: 'M6 4l14 8-14 8V4z',
    download: 'M12 3v12m0 0 4-4m-4 4-4-4M4 19h16',
    inbox: 'M4 13h5l1 3h4l1-3h5M4 13 6 5h12l2 8v6H4v-6z',
    file: 'M6 2h8l5 5v15H6V2zm8 1v5h5',
    trash: 'M4 7h16M9 7V5h6v2m-8 0 1 14h8l1-14',
    refresh: 'M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5',
  };

  function icon(name, options) {
    const opt = options || {};
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', opt.weight || '1.7');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    svg.setAttribute('class', `ico ${opt.class || ''}`);
    if (opt.size) {
      svg.style.width = `${opt.size}px`;
      svg.style.height = `${opt.size}px`;
    }
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', ICON_PATHS[name] || ICON_PATHS.info);
    svg.append(path);
    return svg;
  }

  /* ----------------------------------------------------------- containers */
  function card(title, options, ...children) {
    const opt = options || {};
    const head = title
      ? el(
          'div',
          { class: `card-head ${opt.plain ? 'plain' : ''}` },
          opt.icon ? icon(opt.icon, { size: 18 }) : null,
          el('h3', {}, title),
          ...(opt.actions || []),
        )
      : null;
    return el('section', { class: `card ${opt.class || ''}` }, head, ...children);
  }

  function emptyState(options) {
    const opt = options || {};
    return el(
      'div',
      { class: 'empty-state' },
      icon(opt.icon || 'inbox', { size: 40 }),
      el('h3', {}, opt.title || ''),
      opt.body ? el('p', {}, opt.body) : null,
      opt.action || null,
    );
  }

  function skeleton(kind, count) {
    const rows = [];
    for (let i = 0; i < (count || 1); i += 1) {
      rows.push(el('div', { class: `skeleton ${kind || 'line'}`, style: kind === 'line' ? `width:${70 + ((i * 13) % 30)}%` : '' }));
    }
    return el('div', { 'aria-busy': 'true' }, ...rows);
  }

  function tabs(items, active, onSelect) {
    const buttons = items.map((item) =>
      el(
        'button',
        {
          class: `tab ${item.id === active ? 'active' : ''}`,
          role: 'tab',
          type: 'button',
          tabindex: item.id === active ? '0' : '-1',
          'aria-selected': item.id === active ? 'true' : 'false',
          onclick: () => onSelect(item.id),
        },
        item.label,
      ),
    );
    // Roving tabindex: one tab stop for the group, arrows move between tabs.
    const strip = el(
      'div',
      {
        class: 'tabs',
        role: 'tablist',
        onkeydown: (event) => {
          const keys = { ArrowRight: 1, ArrowLeft: -1, Home: 'first', End: 'last' };
          const move = keys[event.key];
          if (move === undefined) return;
          event.preventDefault();
          const current = buttons.indexOf(document.activeElement);
          const rtl = document.documentElement.dir === 'rtl';
          let next;
          if (move === 'first') next = 0;
          else if (move === 'last') next = buttons.length - 1;
          else next = (current + (rtl ? -move : move) + buttons.length) % buttons.length;
          buttons[next].focus();
          onSelect(items[next].id);
        },
      },
      ...buttons,
    );
    return strip;
  }

  function progress(fraction, indeterminate) {
    return el(
      'div',
      { class: `progress ${indeterminate ? 'indeterminate' : ''}`, role: 'progressbar', 'aria-valuenow': Math.round((fraction || 0) * 100) },
      el('i', { style: `width:${Math.round((fraction || 0) * 100)}%` }),
    );
  }

  /** Right-hand slide-over used by the score explorer. Returns a close function. */
  function drawer(title, options) {
    const opt = options || {};
    const backdrop = el('div', { class: 'drawer-backdrop' });
    const body = el('div', { class: 'drawer-body' });
    const previous = document.activeElement;
    let releaseOverlay = null;
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      backdrop.remove();
      panel.remove();
      if (releaseOverlay) releaseOverlay();
      if (previous && previous.focus) previous.focus();
    };
    const closeBtn = el(
      'button',
      { class: 'btn icon ghost', type: 'button', 'aria-label': AAI.i18n.t('common.close'), onclick: close },
      icon('cross', { size: 16 }),
    );
    const panel = el(
      'aside',
      { class: 'drawer', role: 'dialog', 'aria-modal': 'true', 'aria-label': title, tabindex: '-1' },
      el('div', { class: 'drawer-head' }, el('h3', { class: 'grow truncate', title }, title), ...(opt.actions || []), closeBtn),
      body,
    );
    backdrop.addEventListener('click', close);
    document.body.append(backdrop, panel);
    releaseOverlay = pushOverlay({ panel, onEscape: close });
    panel.focus();
    return { body, close, panel };
  }

  function bandClass(score) {
    if (score >= 90) return 'band-excellent';
    if (score >= 75) return 'band-good';
    if (score >= 60) return 'band-fair';
    if (score >= 40) return 'band-poor';
    return 'band-critical';
  }

  function deltaLabel(value, options) {
    const opt = options || {};
    const rounded = Math.round((value || 0) * 10) / 10;
    const direction = rounded > 0 ? 'up' : rounded < 0 ? 'down' : 'flat';
    const invert = opt.invert && direction !== 'flat';
    const cls = invert ? (direction === 'up' ? 'down' : 'up') : direction;
    const arrow = direction === 'up' ? '▲' : direction === 'down' ? '▼' : '■';
    return el('span', { class: `delta ${cls}` }, `${arrow} ${rounded > 0 ? '+' : ''}${rounded}${opt.suffix || ''}`);
  }

  AAI.dom = {
    el, append, clear, toast, modal, confirmDialog, field, setFieldError, validate,
    badgeFor, fmtDate, list, spinner, icon, card, emptyState, skeleton, tabs,
    progress, drawer, bandClass, deltaLabel,
  };
})();
