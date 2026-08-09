/**
 * Dependency-free SVG chart toolkit. Everything renders into a fixed viewBox
 * and scales with the container, so charts stay crisp on any DPI and never clip.
 *
 *   const c = AAI.charts.line({ series: [...], x: [...] });
 *   host.append(c.node);
 *
 * Every chart returns { node, svg, update?, destroy? } and is exposed as
 * window.AAI.charts.
 */
(function () {
  const AAI = (window.AAI = window.AAI || {});
  const NS = 'http://www.w3.org/2000/svg';

  const PALETTE = ['#4f9cf9', '#37d399', '#f5b544', '#f4676a', '#a78bfa', '#22d3ee', '#fb923c', '#f472b6'];
  const BAND_COLOR = {
    excellent: 'var(--ok)',
    good: '#86c440',
    fair: 'var(--warn)',
    poor: '#f08b3c',
    critical: 'var(--danger)',
  };

  function bandOf(score) {
    if (score >= 90) return 'excellent';
    if (score >= 75) return 'good';
    if (score >= 60) return 'fair';
    if (score >= 40) return 'poor';
    return 'critical';
  }
  const scoreColor = (score) => BAND_COLOR[bandOf(score)];

  function s(tag, attrs, ...children) {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'text') node.textContent = String(value);
      else if (key.startsWith('on') && typeof value === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else node.setAttribute(key, String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child);
    }
    return node;
  }

  function frame(width, height, className) {
    const node = document.createElement('div');
    node.className = `chart ${className || ''}`;
    const svg = s('svg', {
      viewBox: `0 0 ${width} ${height}`,
      preserveAspectRatio: 'xMidYMid meet',
      role: 'img',
      focusable: 'false',
    });
    node.append(svg);
    return { node, svg };
  }

  /** Shared hover tooltip. Positions itself from the target's client rect. */
  function tooltip(node) {
    const tip = document.createElement('div');
    tip.className = 'chart-tip';
    node.append(tip);
    let raf = 0;
    return {
      show(target, html) {
        tip.innerHTML = html;
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
          const host = node.getBoundingClientRect();
          const box = target.getBoundingClientRect();
          const x = box.left + box.width / 2 - host.left;
          const y = box.top - host.top;
          // Clamp against the tooltip's real width rather than a guess, so a
          // long label near either edge of the chart is never cut off.
          const half = tip.offsetWidth / 2 + 4;
          tip.style.left = `${Math.max(half, Math.min(x, Math.max(half, host.width - half)))}px`;
          // Flip below the target when there is not enough room above it.
          const above = y - tip.offsetHeight - 10;
          if (above < 0) {
            tip.classList.add('below');
            tip.style.top = `${y + box.height + 10}px`;
          } else {
            tip.classList.remove('below');
            tip.style.top = `${y}px`;
          }
          tip.classList.add('on');
        });
      },
      hide() {
        cancelAnimationFrame(raf);
        tip.classList.remove('on');
      },
    };
  }

  const esc = (value) =>
    String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');

  const fmt = (value) => {
    if (typeof value !== 'number' || !isFinite(value)) return String(value ?? '');
    if (Math.abs(value) >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
    if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(1)}k`;
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  };

  /* ------------------------------------------------------------ label text */
  /*
   * Chart labels are never shortened. Truncating a category name to
   * "Maintainabilit…" makes the chart unreadable in exactly the case the
   * reader needs it most, and a truncated Hebrew label can lose the word
   * entirely. Long text is wrapped onto more lines instead, and the viewBox
   * is grown afterwards so the extra lines cannot fall outside the canvas.
   */

  /** Break a label into lines on word boundaries. Never drops characters. */
  function wrapLabel(text, maxChars) {
    const words = String(text === null || text === undefined ? '' : text).split(/\s+/).filter(Boolean);
    if (!words.length) return [''];
    const limit = Math.max(4, maxChars || 14);
    const lines = [];
    let current = '';
    for (const word of words) {
      if (!current) current = word;
      else if (current.length + 1 + word.length <= limit) current += ` ${word}`;
      else {
        lines.push(current);
        current = word;
      }
    }
    lines.push(current);
    // A single word longer than the limit stays whole: splitting mid-word is
    // worse than a wide line, and fitContent() makes room for it either way.
    return lines;
  }

  /** A <text> holding one <tspan> per line, plus a native tooltip. */
  function textBlock(lines, attrs, lineHeight, fullText) {
    const node = s('text', attrs);
    lines.forEach((line, index) => {
      const span = s('tspan', { x: attrs.x, dy: index === 0 ? 0 : lineHeight });
      span.textContent = line;
      node.append(span);
    });
    if (fullText !== undefined && fullText !== null && String(fullText) !== lines.join(' ')) {
      const title = s('title');
      title.textContent = String(fullText);
      node.append(title);
    }
    return node;
  }

  /**
   * Grow the viewBox until every drawn child sits inside it.
   *
   * The chart scales with its container, so anything inside the viewBox is
   * safe at any window size, DPI or zoom level. That reduces "can the label be
   * clipped?" to a question about the viewBox alone, which is measurable and
   * therefore testable.
   *
   * Getting the fit to actually *run* is the hard part. A chart is built
   * before it is attached - the view constructs it, wraps it in a panel and
   * only then appends the result - so a fit scheduled with requestAnimationFrame
   * alone finds a detached node, returns, and never tries again. That is how a
   * category name ends up outside the canvas even though the maths is right.
   * Four independent triggers cover it:
   *
   *   - a microtask, which drains at the end of the task that built *and*
   *     appended the chart, so the common path is fixed before the first paint
   *     and without depending on frames being produced at all;
   *   - an animation frame, for a chart appended in a later task;
   *   - document.fonts.ready, because text measured against a fallback font
   *     reports the wrong width;
   *   - a ResizeObserver, which fires when the element first gets a box and
   *     again whenever the container, the zoom level or the display scaling
   *     changes.
   *
   * Returns the fit routine itself so a caller can settle the chart by hand.
   */
  function fitContent(svg, base, padding) {
    const pad = padding === undefined ? 8 : padding;
    const apply = () => {
      if (!svg.isConnected) return false;
      let box;
      try {
        box = svg.getBBox();
      } catch (error) {
        return false; // not rendered yet; another trigger will cover it
      }
      if (!box || !box.width || !box.height) return false;
      const minX = Math.min(0, box.x - pad);
      const minY = Math.min(0, box.y - pad);
      const maxX = Math.max(base.width, box.x + box.width + pad);
      const maxY = Math.max(base.height, box.y + box.height + pad);
      const next = `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;
      // Writing the same value back would resize the element again and the
      // ResizeObserver below would call straight back in, so the comparison is
      // what makes the observer terminate.
      if (svg.getAttribute('viewBox') !== next) svg.setAttribute('viewBox', next);
      return true;
    };

    Promise.resolve().then(apply);
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(apply);
    if (document.fonts && document.fonts.ready && typeof document.fonts.ready.then === 'function') {
      document.fonts.ready.then(apply).catch(() => {});
    }
    if (typeof ResizeObserver === 'function') {
      // The observer's only strong reference is to this svg, and the only
      // reference to the observer is this closure, so the pair becomes
      // unreachable together when the chart is discarded.
      const observer = new ResizeObserver(() => { apply(); });
      observer.observe(svg);
    }
    return apply;
  }

  /** "Nice" axis ticks: at most `count` round values covering [min, max]. */
  function ticks(min, max, count) {
    if (min === max) return [min];
    const span = max - min;
    const raw = span / Math.max(1, count);
    const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((m) => m >= raw) || magnitude * 10;
    const start = Math.ceil(min / step) * step;
    const out = [];
    for (let value = start; value <= max + step * 0.001; value += step) {
      out.push(Math.round(value * 1000) / 1000);
    }
    return out;
  }

  /** True when the user asked for reduced motion, via the OS or the in-app setting. */
  function reducedMotion() {
    if (document.documentElement.dataset.motion === 'reduced') return true;
    return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  /** Exact length of a straight-segment path through `points`. */
  function polylineLength(points) {
    let total = 0;
    for (let i = 1; i < points.length; i += 1) {
      total += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
    }
    return total;
  }

  /**
   * Animate a line drawing itself in.
   *
   * The dash length must be the path's REAL length. A guess that falls short
   * makes the dash pattern repeat, so the line renders as dash-gap-dash and
   * stays that way after the animation settles - the line visibly stops partway
   * through the chart. The dash is therefore cleared once the draw-in finishes,
   * leaving a plain solid stroke behind.
   */
  function drawIn(path, length, index) {
    if (!length || !isFinite(length) || reducedMotion()) return;
    const done = () => {
      path.style.strokeDasharray = '';
      path.style.strokeDashoffset = '';
      path.style.transition = '';
    };
    path.style.strokeDasharray = String(length);
    path.style.strokeDashoffset = String(length);
    path.style.transition = `stroke-dashoffset 700ms ${120 + index * 90}ms cubic-bezier(0.16,1,0.3,1)`;
    path.addEventListener('transitionend', done, { once: true });
    requestAnimationFrame(() => { path.style.strokeDashoffset = '0'; });
  }

  /* ------------------------------------------------------------ gauge/dial */
  function gauge(options) {
    const opt = Object.assign({ value: 0, max: 100, size: 220, thickness: 16, label: '', sublabel: '' }, options);
    const size = opt.size;
    const r = (size - opt.thickness) / 2 - 6;
    const cx = size / 2;
    const cy = size / 2;
    const start = -220;
    const sweep = 260;
    const pct = Math.max(0, Math.min(opt.value / opt.max, 1));
    const colour = opt.color || scoreColor((opt.value / opt.max) * 100);

    const arc = (fromDeg, toDeg) => {
      const p = (deg) => {
        const rad = (deg * Math.PI) / 180;
        return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
      };
      const [x1, y1] = p(fromDeg);
      const [x2, y2] = p(toDeg);
      const large = Math.abs(toDeg - fromDeg) > 180 ? 1 : 0;
      return `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`;
    };

    const { node, svg } = frame(size, size, 'chart-gauge');
    svg.append(
      s('path', {
        d: arc(start, start + sweep),
        fill: 'none',
        stroke: 'var(--muted-soft)',
        'stroke-width': opt.thickness,
        'stroke-linecap': 'round',
      }),
    );
    const value = s('path', {
      // `start + sweep * pct` can legitimately evaluate to 0 (at pct ≈ 0.846),
      // so the end angle must be computed before any fallback is considered -
      // `a + b || c` would silently draw an empty arc for that one score.
      d: arc(start, pct > 0 ? start + sweep * pct : start + 0.01),
      fill: 'none',
      stroke: colour,
      'stroke-width': opt.thickness,
      'stroke-linecap': 'round',
    });
    const length = Math.PI * r * (sweep / 180) * pct;
    value.style.strokeDasharray = `${length} ${length}`;
    value.style.strokeDashoffset = String(length);
    value.style.transition = 'stroke-dashoffset 900ms cubic-bezier(0.16,1,0.3,1)';
    svg.append(value);
    requestAnimationFrame(() => { value.style.strokeDashoffset = '0'; });

    // band ticks at 40 / 60 / 75 / 90
    [40, 60, 75, 90].forEach((mark) => {
      const deg = start + sweep * (mark / 100);
      const rad = (deg * Math.PI) / 180;
      const inner = r - opt.thickness / 2 - 3;
      const outer = r + opt.thickness / 2 + 3;
      svg.append(
        s('line', {
          x1: cx + inner * Math.cos(rad),
          y1: cy + inner * Math.sin(rad),
          x2: cx + outer * Math.cos(rad),
          y2: cy + outer * Math.sin(rad),
          stroke: 'var(--line)',
          'stroke-width': 1,
        }),
      );
    });

    const overlay = document.createElement('div');
    overlay.className = 'score-dial';
    overlay.style.position = 'absolute';
    overlay.style.inset = '0';
    overlay.innerHTML =
      `<div class="value"><b style="color:${colour}">${esc(Math.round(opt.value))}</b>` +
      `<span class="of">${esc(opt.label || `/ ${opt.max}`)}</span>` +
      (opt.sublabel ? `<span class="grade" style="color:${colour}">${esc(opt.sublabel)}</span>` : '') +
      '</div>';
    node.style.position = 'relative';
    node.append(overlay);
    return { node, svg };
  }

  /* -------------------------------------------------------------- sparkline */
  function sparkline(values, options) {
    const opt = Object.assign({ width: 120, height: 30, color: 'var(--accent)' }, options);
    const { node, svg } = frame(opt.width, opt.height, 'chart-spark');
    const data = (values || []).filter((v) => typeof v === 'number');
    if (data.length < 2) {
      svg.append(s('line', { x1: 0, y1: opt.height / 2, x2: opt.width, y2: opt.height / 2, stroke: 'var(--line)' }));
      return { node, svg };
    }
    const min = Math.min(...data);
    const max = Math.max(...data);
    const span = max - min || 1;
    const step = opt.width / (data.length - 1);
    const points = data.map((v, i) => [i * step, opt.height - 2 - ((v - min) / span) * (opt.height - 4)]);
    svg.append(
      s('path', {
        d: `M ${points.map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' L ')}`,
        class: 'series-line',
        stroke: opt.color,
      }),
      s('circle', { cx: points[points.length - 1][0], cy: points[points.length - 1][1], r: 2.5, fill: opt.color }),
    );
    return { node, svg };
  }

  /* ------------------------------------------------------------------ line */
  function line(options) {
    const opt = Object.assign(
      {
        series: [],
        labels: [],
        width: 760,
        height: 300,
        yMin: null,
        yMax: null,
        area: true,
        yFormat: fmt,
        valueSuffix: '',
      },
      options,
    );
    const pad = { top: 18, right: 20, bottom: 40, left: 48 };
    const { node, svg } = frame(opt.width, opt.height, 'chart-line');
    const tip = tooltip(node);
    const plotW = opt.width - pad.left - pad.right;
    const plotH = opt.height - pad.top - pad.bottom;
    const hidden = new Set();
    // Assigned once fitContent has run below. draw() re-runs whenever a legend
    // entry is toggled, and the new series can widen the y axis enough to push
    // its labels past the left edge, so every redraw has to refit - but through
    // the same closure, otherwise each toggle would register another
    // ResizeObserver on the same element.
    let refit = null;

    const draw = () => {
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      const active = opt.series.filter((series) => !hidden.has(series.name));
      const all = active.flatMap((series) => series.values.filter((v) => typeof v === 'number'));
      const min = opt.yMin !== null ? opt.yMin : Math.min(...(all.length ? all : [0]));
      const max = opt.yMax !== null ? opt.yMax : Math.max(...(all.length ? all : [1]));
      const span = max - min || 1;
      const count = Math.max(1, opt.labels.length - 1);
      const x = (i) => pad.left + (count ? (i / count) * plotW : plotW / 2);
      const y = (v) => pad.top + plotH - ((v - min) / span) * plotH;

      const axis = s('g', { class: 'axis' });
      ticks(min, max, 5).forEach((value) => {
        const yy = y(value);
        axis.append(
          s('line', { x1: pad.left, y1: yy, x2: pad.left + plotW, y2: yy, class: 'gridline' }),
          s('text', { x: pad.left - 8, y: yy + 3.5, 'text-anchor': 'end', text: opt.yFormat(value) }),
        );
      });
      const every = Math.max(1, Math.ceil(opt.labels.length / 8));
      const lastIndex = opt.labels.length - 1;
      opt.labels.forEach((label, i) => {
        const onGrid = i % every === 0;
        // The final tick is always meaningful, but drawing it right next to the
        // previous one produces overlapping text - so only keep it when there
        // is at least half a step of clearance.
        const isCrowdedLast = i === lastIndex && !onGrid && (lastIndex % every) < every / 2;
        if ((!onGrid && i !== lastIndex) || isCrowdedLast) return;
        axis.append(
          s('text', {
            x: x(i),
            y: opt.height - pad.bottom + 18,
            'text-anchor': i === 0 ? 'start' : i === lastIndex ? 'end' : 'middle',
            text: label,
          }),
        );
      });
      axis.append(
        s('line', { x1: pad.left, y1: pad.top + plotH, x2: pad.left + plotW, y2: pad.top + plotH, class: 'gridline' }),
      );
      svg.append(axis);

      active.forEach((series, index) => {
        const colour = series.color || PALETTE[opt.series.indexOf(series) % PALETTE.length];
        const points = series.values
          .map((value, i) => (typeof value === 'number' ? [x(i), y(value), value, i] : null))
          .filter(Boolean);
        if (!points.length) return;
        const path = `M ${points.map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' L ')}`;
        if (opt.area) {
          svg.append(
            s('path', {
              d: `${path} L ${points[points.length - 1][0].toFixed(1)} ${pad.top + plotH} L ${points[0][0].toFixed(1)} ${pad.top + plotH} Z`,
              fill: colour,
              class: 'series-area',
            }),
          );
        }
        const stroke = s('path', { d: path, class: 'series-line', stroke: colour });
        svg.append(stroke);
        drawIn(stroke, polylineLength(points), index);

        points.forEach((p) => {
          const dot = s('circle', { cx: p[0], cy: p[1], r: points.length > 40 ? 0 : 3.5, fill: colour, class: 'point' });
          const hit = s('circle', { cx: p[0], cy: p[1], r: 11, fill: 'transparent', class: 'point' });
          hit.addEventListener('mouseenter', () => {
            dot.setAttribute('r', '5.5');
            tip.show(hit,
              `<b>${esc(opt.labels[p[3]] ?? '')}</b><div class="r"><span><i class="sw" style="background:${colour};display:inline-block"></i> ${esc(series.name)}</span><b>${esc(fmt(p[2]))}${esc(opt.valueSuffix)}</b></div>`);
          });
          hit.addEventListener('mouseleave', () => {
            dot.setAttribute('r', points.length > 40 ? '0' : '3.5');
            tip.hide();
          });
          if (typeof opt.onPoint === 'function') {
            hit.style.cursor = 'pointer';
            hit.addEventListener('click', () => opt.onPoint(p[3], series));
          }
          svg.append(dot, hit);
        });
      });
      if (refit) refit();
    };

    draw();
    // The y axis labels are anchored at the end, 8px left of the plot area, so
    // a wide formatted value (a byte count, a six figure line total) reaches
    // past x=0 and used to be cut off by the viewBox.
    refit = fitContent(svg, { width: opt.width, height: opt.height });
    const legend = opt.series.length > 1 ? buildLegend(opt.series, hidden, draw) : null;
    const wrap = document.createElement('div');
    wrap.append(node);
    if (legend) wrap.append(legend);
    return { node: wrap, chart: node, svg, redraw: draw, refit };
  }

  function buildLegend(series, hidden, redraw) {
    const legend = document.createElement('div');
    legend.className = 'legend';
    series.forEach((item, index) => {
      const colour = item.color || PALETTE[index % PALETTE.length];
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'legend-item';
      button.innerHTML = `<i class="sw" style="background:${colour}"></i>${esc(item.name)}`;
      button.addEventListener('click', () => {
        if (hidden.has(item.name)) hidden.delete(item.name);
        else hidden.add(item.name);
        button.classList.toggle('off', hidden.has(item.name));
        redraw();
      });
      legend.append(button);
    });
    return legend;
  }

  /* ------------------------------------------------------------------- bar */
  function bars(options) {
    const opt = Object.assign(
      { items: [], width: 760, horizontal: true, max: null, showValue: true, height: null, color: null, unit: '' },
      options,
    );
    const items = opt.items || [];
    const rowH = 26;
    const pad = opt.horizontal
      ? { top: 8, right: 56, bottom: 8, left: Math.min(220, Math.max(90, ...items.map((i) => String(i.label).length * 6.2))) }
      : { top: 16, right: 12, bottom: 46, left: 44 };
    const height = opt.height || (opt.horizontal ? pad.top + pad.bottom + items.length * rowH : 260);
    const { node, svg } = frame(opt.width, Math.max(height, 60), 'chart-bars');
    const tip = tooltip(node);
    if (!items.length) {
      node.innerHTML = '';
      const empty = document.createElement('div');
      empty.className = 'chart-empty';
      empty.textContent = opt.emptyText || '—';
      node.append(empty);
      return { node, svg };
    }
    const max = opt.max || Math.max(...items.map((i) => Math.abs(i.value) || 0)) || 1;

    if (opt.horizontal) {
      const plotW = opt.width - pad.left - pad.right;
      items.forEach((item, index) => {
        const y = pad.top + index * rowH;
        const w = Math.max(2, (Math.abs(item.value) / max) * plotW);
        const colour = item.color || opt.color || PALETTE[index % PALETTE.length];
        svg.append(
          textBlock(
            [String(item.label)],
            { x: pad.left - 10, y: y + rowH / 2 + 4, 'text-anchor': 'end', class: 'label axis-label' },
            0,
            item.label,
          ),
          s('rect', { x: pad.left, y: y + 5, width: plotW, height: rowH - 12, rx: 3, fill: 'var(--muted-soft)' }),
        );
        const rect = s('rect', { x: pad.left, y: y + 5, width: 0, height: rowH - 12, rx: 3, fill: colour, class: 'bar' });
        rect.style.transition = `width 650ms ${index * 35}ms cubic-bezier(0.16,1,0.3,1)`;
        svg.append(rect);
        requestAnimationFrame(() => rect.setAttribute('width', String(w)));
        if (opt.showValue) {
          svg.append(
            s('text', {
              x: pad.left + plotW + 8,
              y: y + rowH / 2 + 4,
              class: 'value-label',
              text: `${fmt(item.value)}${opt.unit}`,
            }),
          );
        }
        rect.addEventListener('mouseenter', () =>
          tip.show(rect, `<b>${esc(item.label)}</b><div class="r"><span>${esc(item.hint || '')}</span><b>${esc(fmt(item.value))}${esc(opt.unit)}</b></div>`),
        );
        rect.addEventListener('mouseleave', tip.hide);
        if (typeof opt.onSelect === 'function') {
          rect.style.cursor = 'pointer';
          rect.addEventListener('click', () => opt.onSelect(item));
        }
      });
      const refit = fitContent(svg, { width: opt.width, height: Math.max(height, 60) });
      return { node, svg, refit };
    }

    const plotW = opt.width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const slot = plotW / items.length;
    const axis = s('g', { class: 'axis' });
    ticks(0, max, 4).forEach((value) => {
      const yy = pad.top + plotH - (value / max) * plotH;
      axis.append(
        s('line', { x1: pad.left, y1: yy, x2: pad.left + plotW, y2: yy, class: 'gridline' }),
        s('text', { x: pad.left - 8, y: yy + 3.5, 'text-anchor': 'end', text: fmt(value) }),
      );
    });
    svg.append(axis);
    items.forEach((item, index) => {
      const w = Math.min(46, slot * 0.62);
      const x = pad.left + slot * index + (slot - w) / 2;
      const h = Math.max(2, (Math.abs(item.value) / max) * plotH);
      const colour = item.color || opt.color || PALETTE[index % PALETTE.length];
      const rect = s('rect', { x, y: pad.top + plotH, width: w, height: 0, rx: 4, fill: colour, class: 'bar' });
      rect.style.transition = `height 620ms ${index * 45}ms cubic-bezier(0.16,1,0.3,1), y 620ms ${index * 45}ms cubic-bezier(0.16,1,0.3,1)`;
      svg.append(rect);
      requestAnimationFrame(() => {
        rect.setAttribute('height', String(h));
        rect.setAttribute('y', String(pad.top + plotH - h));
      });
      const label = String(item.label);
      // Rotated rather than shortened. The viewBox is grown below, so a long
      // name simply leans further out instead of losing its tail.
      const anchorY = height - pad.bottom + 16;
      svg.append(
        textBlock(
          [label],
          {
            x: x + w / 2,
            y: anchorY,
            'text-anchor': label.length > 8 ? 'end' : 'middle',
            class: 'label axis-label',
            transform: label.length > 8 ? `rotate(-35 ${x + w / 2} ${anchorY})` : null,
          },
          0,
          label,
        ),
      );
      rect.addEventListener('mouseenter', () =>
        tip.show(rect, `<b>${esc(item.label)}</b><div class="r"><span>${esc(item.hint || '')}</span><b>${esc(fmt(item.value))}${esc(opt.unit)}</b></div>`),
      );
      rect.addEventListener('mouseleave', tip.hide);
      if (typeof opt.onSelect === 'function') {
        rect.style.cursor = 'pointer';
        rect.addEventListener('click', () => opt.onSelect(item));
      }
    });
    const refit = fitContent(svg, { width: opt.width, height: Math.max(height, 60) });
    return { node, svg, refit };
  }

  /* ----------------------------------------------------------------- donut */
  function donut(options) {
    const opt = Object.assign({ items: [], size: 200, thickness: 26, center: '', centerSub: '' }, options);
    const items = (opt.items || []).filter((i) => (i.value || 0) > 0);
    const total = items.reduce((sum, i) => sum + i.value, 0) || 1;
    const { node, svg } = frame(opt.size, opt.size, 'chart-donut');
    const tip = tooltip(node);
    const cx = opt.size / 2;
    const cy = opt.size / 2;
    const r = (opt.size - opt.thickness) / 2 - 4;
    let angle = -90;

    items.forEach((item, index) => {
      const portion = item.value / total;
      const sweep = portion * 360;
      const colour = item.color || PALETTE[index % PALETTE.length];
      const p = (deg) => {
        const rad = (deg * Math.PI) / 180;
        return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
      };
      const [x1, y1] = p(angle);
      const [x2, y2] = p(angle + Math.min(sweep, 359.9));
      const arc = s('path', {
        d: `M ${x1} ${y1} A ${r} ${r} 0 ${sweep > 180 ? 1 : 0} 1 ${x2} ${y2}`,
        fill: 'none',
        stroke: colour,
        'stroke-width': opt.thickness,
        class: 'bar',
      });
      arc.addEventListener('mouseenter', () => {
        arc.setAttribute('stroke-width', String(opt.thickness + 5));
        tip.show(arc, `<b>${esc(item.label)}</b><div class="r"><span>${Math.round(portion * 100)}%</span><b>${esc(fmt(item.value))}</b></div>`);
      });
      arc.addEventListener('mouseleave', () => {
        arc.setAttribute('stroke-width', String(opt.thickness));
        tip.hide();
      });
      svg.append(arc);
      angle += sweep;
    });

    if (opt.center) {
      svg.append(
        s('text', { x: cx, y: cy + 2, 'text-anchor': 'middle', class: 'value-label', 'font-size': 22, text: opt.center }),
      );
      if (opt.centerSub) {
        svg.append(s('text', { x: cx, y: cy + 20, 'text-anchor': 'middle', class: 'label', text: opt.centerSub }));
      }
    }
    // The centre caption is free text - a formatted total or a translated
    // subtitle - and at 22px it outgrows the hole in the ring long before it
    // outgrows the chart, so let the viewBox follow it.
    const refit = fitContent(svg, { width: opt.size, height: opt.size });

    const wrap = document.createElement('div');
    wrap.append(node);
    if (opt.legend !== false) {
      const legend = document.createElement('div');
      legend.className = 'legend';
      items.forEach((item, index) => {
        const colour = item.color || PALETTE[index % PALETTE.length];
        const span = document.createElement('span');
        span.className = 'legend-item';
        span.innerHTML = `<i class="sw" style="background:${colour}"></i>${esc(item.label)} <b>${esc(fmt(item.value))}</b>`;
        legend.append(span);
      });
      wrap.append(legend);
    }
    return { node: wrap, chart: node, svg, refit };
  }

  /* ----------------------------------------------------------------- radar */
  function radar(options) {
    const opt = Object.assign({ axes: [], series: [], size: 320, max: 100 }, options);
    const LINE_H = 12;
    const n = opt.axes.length || 1;
    // Narrower blocks when there are many axes: a tall thin label costs arc
    // length, a wide one costs the width its neighbours need.
    const wrapAt = Math.max(8, Math.round(16 - n / 4));
    const lineSets = opt.axes.map((label) => wrapLabel(label, wrapAt));
    const tallest = lineSets.reduce((most, lines) => Math.max(most, lines.length), 1);

    // Labels sit on a ring of radius r * LABEL_RING. Neighbours are 2*PI/n
    // apart, so the arc between them is 2*PI*ring/n and it has to be at least
    // as long as a label block is tall or the two will overlap. Solving for r
    // is what keeps thirty categories readable instead of stacked on top of
    // one another - and for the usual eight it changes nothing.
    const LABEL_RING = 1.16;
    const blockHeight = tallest * LINE_H + 8;
    const needed = (n * blockHeight) / (2 * Math.PI * LABEL_RING);
    const r = Math.max(opt.size / 2 - 46, needed);
    const size = Math.max(opt.size, (r + 46) * 2);

    const { node, svg } = frame(size, size, 'chart-radar');
    const tip = tooltip(node);
    const cx = size / 2;
    const cy = size / 2;
    const at = (index, value) => {
      const angle = (Math.PI * 2 * index) / n - Math.PI / 2;
      const radius = (Math.max(0, Math.min(value, opt.max)) / opt.max) * r;
      return [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)];
    };

    [0.25, 0.5, 0.75, 1].forEach((step) => {
      const points = opt.axes.map((_, i) => at(i, opt.max * step).join(',')).join(' ');
      svg.append(s('polygon', { points, fill: 'none', stroke: 'var(--grid-line)', 'stroke-width': 1 }));
    });
    opt.axes.forEach((label, i) => {
      const [x, y] = at(i, opt.max);
      svg.append(s('line', { x1: cx, y1: cy, x2: x, y2: y, stroke: 'var(--grid-line)' }));
      const [lx, ly] = at(i, opt.max * LABEL_RING);
      const anchor = lx > cx + 6 ? 'start' : lx < cx - 6 ? 'end' : 'middle';
      const lines = lineSets[i];
      // Centre the block on its spoke so a wrapped label grows outwards
      // symmetrically instead of drifting across the chart body.
      const top = ly + 4 - ((lines.length - 1) * LINE_H) / 2;
      const text = textBlock(
        lines,
        { x: lx, y: top, 'text-anchor': anchor, class: 'label axis-label' },
        LINE_H,
        label,
      );
      const score = opt.series.length ? opt.series[0].values[i] : null;
      text.addEventListener('mouseenter', () =>
        tip.show(
          text,
          score === null || score === undefined
            ? `<b>${esc(label)}</b>`
            : `<b>${esc(label)}</b><div class="r"><span>${esc(opt.series[0].name)}</span><b>${esc(score)}/${opt.max}</b></div>`,
        ),
      );
      text.addEventListener('mouseleave', tip.hide);
      if (typeof opt.onAxis === 'function') {
        text.style.cursor = 'pointer';
        text.addEventListener('click', () => opt.onAxis(i));
      }
      svg.append(text);
    });

    opt.series.forEach((series, index) => {
      const colour = series.color || PALETTE[index % PALETTE.length];
      const points = series.values.map((value, i) => at(i, value));
      const polygon = s('polygon', {
        points: points.map((p) => p.join(',')).join(' '),
        fill: colour,
        'fill-opacity': index === 0 ? 0.2 : 0.1,
        stroke: colour,
        'stroke-width': 2,
        'stroke-linejoin': 'round',
      });
      polygon.style.transformOrigin = `${cx}px ${cy}px`;
      polygon.style.transform = 'scale(0.2)';
      polygon.style.transition = `transform 700ms ${index * 120}ms cubic-bezier(0.16,1,0.3,1)`;
      svg.append(polygon);
      requestAnimationFrame(() => { polygon.style.transform = 'scale(1)'; });
      points.forEach((p, i) => {
        const dot = s('circle', { cx: p[0], cy: p[1], r: 4, fill: colour, class: 'point' });
        dot.addEventListener('mouseenter', () =>
          tip.show(dot, `<b>${esc(opt.axes[i])}</b><div class="r"><span>${esc(series.name)}</span><b>${esc(series.values[i])}/${opt.max}</b></div>`),
        );
        dot.addEventListener('mouseleave', tip.hide);
        if (typeof opt.onAxis === 'function') {
          dot.style.cursor = 'pointer';
          dot.addEventListener('click', () => opt.onAxis(i));
        }
        svg.append(dot);
      });
    });
    const refit = fitContent(svg, { width: size, height: size });
    return { node, svg, refit };
  }

  /* --------------------------------------------------------------- heatmap */
  function heatmap(options) {
    const opt = Object.assign(
      { cells: [], width: 760, cell: 26, gap: 3, colorFor: null, emptyText: '—' },
      options,
    );
    const cells = opt.cells || [];
    if (!cells.length) {
      const node = document.createElement('div');
      node.className = 'chart';
      const empty = document.createElement('div');
      empty.className = 'chart-empty';
      empty.textContent = opt.emptyText;
      node.append(empty);
      return { node };
    }
    const step = opt.cell + opt.gap;
    const cols = Math.max(1, Math.floor((opt.width - 4) / step));
    const rows = Math.ceil(cells.length / cols);
    const { node, svg } = frame(opt.width, rows * step + 6, 'chart-heatmap');
    const tip = tooltip(node);
    const max = Math.max(...cells.map((c) => c.value || 0)) || 1;

    cells.forEach((cell, index) => {
      const column = index % cols;
      const row = Math.floor(index / cols);
      const intensity = Math.max(0.08, (cell.value || 0) / max);
      const colour = opt.colorFor ? opt.colorFor(cell, intensity) : `color-mix(in srgb, var(--danger) ${Math.round(intensity * 100)}%, var(--surface-3))`;
      const rect = s('rect', {
        x: 2 + column * step,
        y: 2 + row * step,
        width: opt.cell,
        height: opt.cell,
        rx: 3,
        fill: colour,
        class: 'cell',
      });
      rect.style.opacity = '0';
      rect.style.transition = `opacity 400ms ${Math.min(index * 6, 600)}ms`;
      requestAnimationFrame(() => { rect.style.opacity = '1'; });
      rect.addEventListener('mouseenter', () =>
        tip.show(rect, `<b>${esc(cell.label)}</b>${cell.hint ? `<div class="r"><span>${esc(cell.hint)}</span><b>${esc(fmt(cell.value))}</b></div>` : `<div class="r"><b>${esc(fmt(cell.value))}</b></div>`}`),
      );
      rect.addEventListener('mouseleave', tip.hide);
      if (typeof opt.onSelect === 'function') {
        rect.style.cursor = 'pointer';
        rect.addEventListener('click', () => opt.onSelect(cell));
      }
      svg.append(rect);
    });
    return { node, svg };
  }

  /* --------------------------------------------------------------- treemap */
  function treemap(options) {
    const opt = Object.assign({ items: [], width: 760, height: 320, colorFor: null }, options);
    const items = (opt.items || []).filter((i) => (i.value || 0) > 0).slice(0, 120);
    if (!items.length) {
      const node = document.createElement('div');
      node.className = 'chart';
      const empty = document.createElement('div');
      empty.className = 'chart-empty';
      empty.textContent = opt.emptyText || '—';
      node.append(empty);
      return { node };
    }
    const { node, svg } = frame(opt.width, opt.height, 'chart-treemap');
    const tip = tooltip(node);
    const total = items.reduce((sum, i) => sum + i.value, 0);

    // squarified-lite: slice-and-dice alternating direction keeps aspect sane
    const layout = (list, x, y, w, h, horizontal) => {
      if (!list.length) return;
      if (list.length === 1) {
        emit(list[0], x, y, w, h);
        return;
      }
      const sum = list.reduce((acc, i) => acc + i.value, 0);
      let half = 0;
      let index = 0;
      while (index < list.length - 1 && half + list[index].value < sum / 2) {
        half += list[index].value;
        index += 1;
      }
      const first = list.slice(0, index || 1);
      const rest = list.slice(index || 1);
      const share = first.reduce((acc, i) => acc + i.value, 0) / sum;
      if (horizontal) {
        layout(first, x, y, w * share, h, !horizontal);
        layout(rest, x + w * share, y, w * (1 - share), h, !horizontal);
      } else {
        layout(first, x, y, w, h * share, !horizontal);
        layout(rest, x, y + h * share, w, h * (1 - share), !horizontal);
      }
    };

    const emit = (item, x, y, w, h) => {
      if (w < 1 || h < 1) return;
      const colour = opt.colorFor ? opt.colorFor(item) : PALETTE[0];
      const rect = s('rect', {
        x: x + 1,
        y: y + 1,
        width: Math.max(1, w - 2),
        height: Math.max(1, h - 2),
        rx: 3,
        fill: colour,
        stroke: 'var(--bg)',
        'stroke-width': 1,
        class: 'cell',
      });
      rect.addEventListener('mouseenter', () =>
        tip.show(rect, `<b>${esc(item.label)}</b><div class="r"><span>${esc(item.hint || '')}</span><b>${esc(fmt(item.value))}</b></div>`),
      );
      rect.addEventListener('mouseleave', tip.hide);
      if (typeof opt.onSelect === 'function') {
        rect.style.cursor = 'pointer';
        rect.addEventListener('click', () => opt.onSelect(item));
      }
      svg.append(rect);
      if (w > 62 && h > 22) {
        const text = String(item.short || item.label);
        const room = Math.max(1, Math.floor((w - 14) / 5.6));
        // A treemap label is bounded by its own tile, so this is the one place
        // shortening is unavoidable. The full name stays reachable through the
        // hover tooltip and the native title below.
        svg.append(
          textBlock(
            [text.length > room ? `${text.slice(0, room - 1)}…` : text],
            { x: x + 7, y: y + 15, class: 'label treemap-label', 'font-size': 10 },
            0,
            item.label,
          ),
        );
      }
    };

    items.sort((a, b) => b.value - a.value);
    layout(items, 0, 0, opt.width, opt.height, true);
    return { node, svg, total };
  }

  /* ------------------------------------------------------------- stack bar */
  function stack(options) {
    const opt = Object.assign({ groups: [], keys: [], colors: {}, width: 760, height: 260, unit: '' }, options);
    const pad = { top: 16, right: 14, bottom: 44, left: 44 };
    const { node, svg } = frame(opt.width, opt.height, 'chart-stack');
    const tip = tooltip(node);
    const plotW = opt.width - pad.left - pad.right;
    const plotH = opt.height - pad.top - pad.bottom;
    const totals = opt.groups.map((g) => opt.keys.reduce((sum, key) => sum + (g.values[key] || 0), 0));
    const max = Math.max(...totals, 1);
    const slot = plotW / Math.max(1, opt.groups.length);

    const axis = s('g', { class: 'axis' });
    ticks(0, max, 4).forEach((value) => {
      const yy = pad.top + plotH - (value / max) * plotH;
      axis.append(
        s('line', { x1: pad.left, y1: yy, x2: pad.left + plotW, y2: yy, class: 'gridline' }),
        s('text', { x: pad.left - 8, y: yy + 3.5, 'text-anchor': 'end', text: fmt(value) }),
      );
    });
    svg.append(axis);

    opt.groups.forEach((group, index) => {
      const w = Math.min(44, slot * 0.6);
      const x = pad.left + slot * index + (slot - w) / 2;
      let cursor = pad.top + plotH;
      opt.keys.forEach((key, k) => {
        const value = group.values[key] || 0;
        if (!value) return;
        const h = (value / max) * plotH;
        cursor -= h;
        const colour = opt.colors[key] || PALETTE[k % PALETTE.length];
        const rect = s('rect', { x, y: cursor, width: w, height: h, fill: colour, class: 'bar' });
        rect.addEventListener('mouseenter', () =>
          tip.show(rect, `<b>${esc(group.label)}</b><div class="r"><span><i class="sw" style="background:${colour};display:inline-block"></i> ${esc(key)}</span><b>${esc(fmt(value))}${esc(opt.unit)}</b></div>`),
        );
        rect.addEventListener('mouseleave', tip.hide);
        svg.append(rect);
      });
      const label = String(group.label);
      const anchorY = opt.height - pad.bottom + 16;
      svg.append(
        textBlock(
          [label],
          {
            x: x + w / 2,
            y: anchorY,
            'text-anchor': label.length > 8 ? 'end' : 'middle',
            class: 'label axis-label',
            transform: label.length > 8 ? `rotate(-35 ${x + w / 2} ${anchorY})` : null,
          },
          0,
          label,
        ),
      );
    });
    fitContent(svg, { width: opt.width, height: opt.height });

    const wrap = document.createElement('div');
    wrap.append(node);
    const legend = document.createElement('div');
    legend.className = 'legend';
    opt.keys.forEach((key, k) => {
      const span = document.createElement('span');
      span.className = 'legend-item';
      span.innerHTML = `<i class="sw" style="background:${opt.colors[key] || PALETTE[k % PALETTE.length]}"></i>${esc(key)}`;
      legend.append(span);
    });
    wrap.append(legend);
    return { node: wrap, chart: node, svg };
  }

  /* ------------------------------------------------------------ containers */
  /** Wrap a chart in a titled frame with an export button. */
  function panel(title, chart, options) {
    const opt = options || {};
    const wrap = document.createElement('div');
    wrap.className = 'chart-frame';
    const head = document.createElement('div');
    head.className = 'chart-head';
    head.innerHTML = `<h4>${esc(title)}</h4>`;
    if (opt.subtitle) {
      const sub = document.createElement('span');
      sub.className = 'small dim';
      sub.textContent = opt.subtitle;
      head.append(sub);
    }
    if (opt.actions) opt.actions.forEach((action) => head.append(action));
    wrap.append(head, chart.node || chart);
    return wrap;
  }

  /** Serialise a chart's <svg> with the resolved theme colours baked in. */
  function toSVGString(svgEl) {
    const clone = svgEl.cloneNode(true);
    const computed = getComputedStyle(document.documentElement);
    const resolve = (value) =>
      String(value || '').replace(/var\((--[a-z0-9-]+)[^)]*\)/gi, (m, name) => computed.getPropertyValue(name).trim() || '#888');
    clone.querySelectorAll('*').forEach((node) => {
      ['fill', 'stroke'].forEach((attribute) => {
        const value = node.getAttribute(attribute);
        if (value && value.includes('var(')) node.setAttribute(attribute, resolve(value));
      });
    });
    clone.setAttribute('xmlns', NS);
    const box = clone.getAttribute('viewBox').split(/\s+/);
    clone.setAttribute('width', box[2]);
    clone.setAttribute('height', box[3]);
    const bg = computed.getPropertyValue('--surface').trim() || '#111';
    return `<svg xmlns="${NS}" width="${box[2]}" height="${box[3]}" viewBox="${clone.getAttribute('viewBox')}"><rect width="100%" height="100%" fill="${bg}"/>${clone.innerHTML}</svg>`;
  }

  AAI.charts = {
    PALETTE,
    bandOf,
    scoreColor,
    svg: s,
    gauge,
    line,
    bars,
    donut,
    radar,
    heatmap,
    treemap,
    stack,
    sparkline,
    panel,
    ticks,
    fmt,
    toSVGString,
  };
})();
