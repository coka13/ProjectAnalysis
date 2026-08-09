/**
 * Commit graph renderer.
 *
 * Draws the repository DAG the way a dedicated git client does: one lane per
 * concurrent line of development, continuous branch lines, and explicit merge
 * edges. Lane assignment happens in Python (app/history/commit_graph.py); this
 * file is purely presentation.
 *
 * Rows are virtualised. Edges are kept whenever their vertical span intersects
 * the viewport rather than only when an endpoint is visible - an edge that
 * crosses the whole screen must still be drawn, otherwise branch lines appear to
 * stop partway down the graph.
 */
(function () {
  'use strict';

  const AAI = window.AAI;
  const NS = 'http://www.w3.org/2000/svg';

  const ROW = 34;          // vertical distance between commits
  const LANE = 18;         // horizontal distance between lanes
  const LEFT = 18;         // gutter before the first lane
  const DOT = 4.5;
  const OVERSCAN = 8;      // rows rendered beyond the viewport

  const LANE_COLOURS = [
    '#4f9cf9', '#22c55e', '#f59e0b', '#a855f7', '#ec4899',
    '#14b8a6', '#ef4444', '#8b5cf6', '#06b6d4', '#84cc16',
  ];

  const laneColour = (lane) => LANE_COLOURS[lane % LANE_COLOURS.length];
  const laneX = (lane) => LEFT + lane * LANE;

  function el(tag, attrs) {
    const node = document.createElementNS(NS, tag);
    for (const key in attrs) {
      if (attrs[key] !== null && attrs[key] !== undefined) node.setAttribute(key, String(attrs[key]));
    }
    return node;
  }

  /**
   * Path from a commit down to one of its parents.
   *
   * Same lane is a straight line. A lane change is drawn as a curve that leaves
   * the commit vertically and arrives at the parent vertically, so merges read
   * clearly even when several land on the same row.
   */
  function edgePath(x1, y1, x2, y2) {
    if (x1 === x2) return `M ${x1} ${y1} L ${x2} ${y2}`;
    const bend = Math.min(ROW * 0.6, Math.abs(y2 - y1) * 0.5);
    return `M ${x1} ${y1} L ${x1} ${y2 - bend - (y2 - y1 > ROW ? ROW * 0.2 : 0)} C ${x1} ${y2 - bend / 2}, ${x2} ${y2 - bend}, ${x2} ${y2}`;
  }

  function render(host, payload, options) {
    const opt = options || {};
    const t = (AAI.i18n && AAI.i18n.t) || ((key) => key);
    const commits = (payload && payload.commits) || [];
    const rowOf = new Map();
    commits.forEach((commit, index) => rowOf.set(commit.sha, index));

    const lanes = Math.max(1, payload.lanes || 1);
    const gutter = LEFT + lanes * LANE + LEFT;
    const height = commits.length * ROW;

    const root = document.createElement('div');
    root.className = 'gitgraph';

    const scroller = document.createElement('div');
    scroller.className = 'gitgraph-scroll';
    scroller.tabIndex = 0;
    scroller.setAttribute('role', 'list');
    scroller.setAttribute('aria-label', t('gitgraph.title'));

    const canvas = document.createElement('div');
    canvas.className = 'gitgraph-canvas';
    canvas.style.height = `${height}px`;
    canvas.style.setProperty('--gitgraph-gutter', `${gutter}px`);

    const svg = el('svg', { class: 'gitgraph-svg', width: gutter, height, 'aria-hidden': 'true' });
    svg.style.width = `${gutter}px`;
    svg.style.height = `${height}px`;

    const rows = document.createElement('div');
    rows.className = 'gitgraph-rows';

    canvas.append(svg, rows);
    scroller.append(canvas);
    root.append(scroller);

    /** Build every edge once; each carries the row span it covers. */
    const edges = [];
    commits.forEach((commit, index) => {
      (commit.links || []).forEach((link) => {
        const parentRow = rowOf.get(link.parent);
        const from = { x: laneX(link.from), y: index * ROW + ROW / 2 };
        // A parent outside the loaded window still gets a stub so the branch
        // visibly continues past the end of the graph instead of stopping dead.
        const targetRow = parentRow === undefined ? commits.length : parentRow;
        const to = { x: laneX(link.to), y: targetRow * ROW + ROW / 2 };
        edges.push({
          top: Math.min(from.y, to.y),
          bottom: Math.max(from.y, to.y),
          d: edgePath(from.x, from.y, to.x, to.y),
          colour: laneColour(link.to),
          dangling: parentRow === undefined,
        });
      });
    });

    let painted = { first: -1, last: -1 };

    function paint() {
      const top = scroller.scrollTop;
      const view = scroller.clientHeight || 600;
      const first = Math.max(0, Math.floor(top / ROW) - OVERSCAN);
      const last = Math.min(commits.length - 1, Math.ceil((top + view) / ROW) + OVERSCAN);
      if (first === painted.first && last === painted.last) return;
      painted = { first, last };

      const viewTop = first * ROW;
      const viewBottom = (last + 1) * ROW;

      while (svg.firstChild) svg.removeChild(svg.firstChild);
      edges.forEach((edge) => {
        // Span intersection, not endpoint visibility: a long edge crossing the
        // viewport has neither endpoint on screen but must still be drawn.
        if (edge.bottom < viewTop || edge.top > viewBottom) return;
        svg.append(el('path', {
          d: edge.d,
          class: `gitgraph-edge${edge.dangling ? ' dangling' : ''}`,
          stroke: edge.colour,
          fill: 'none',
        }));
      });

      rows.textContent = '';
      for (let i = first; i <= last; i += 1) {
        const commit = commits[i];
        const y = i * ROW;
        svg.append(el('circle', {
          cx: laneX(commit.lane),
          cy: y + ROW / 2,
          r: commit.parents.length > 1 ? DOT + 1.5 : DOT,
          class: `gitgraph-dot${commit.parents.length > 1 ? ' merge' : ''}`,
          fill: laneColour(commit.lane),
        }));

        const row = document.createElement('div');
        row.className = 'gitgraph-row';
        row.style.transform = `translateY(${y}px)`;
        row.setAttribute('role', 'listitem');
        row.tabIndex = -1;
        row.dataset.sha = commit.sha;

        const refs = document.createElement('span');
        refs.className = 'gitgraph-refs';
        (commit.refs || []).forEach((ref) => {
          const tag = document.createElement('span');
          tag.className = `gitgraph-ref ${ref.kind}`;
          tag.textContent = ref.name;
          refs.append(tag);
        });

        const subject = document.createElement('span');
        subject.className = 'gitgraph-subject truncate';
        subject.textContent = commit.subject;
        subject.title = commit.subject;

        const meta = document.createElement('span');
        meta.className = 'gitgraph-meta';
        meta.textContent = commit.author;

        const sha = document.createElement('span');
        sha.className = 'gitgraph-sha mono';
        sha.textContent = commit.short;

        row.append(refs, subject, meta, sha);
        if (typeof opt.onSelect === 'function') {
          row.classList.add('clickable');
          row.addEventListener('click', () => opt.onSelect(commit));
          row.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            opt.onSelect(commit);
          });
        }
        rows.append(row);
      }
    }

    scroller.addEventListener('scroll', () => requestAnimationFrame(paint), { passive: true });
    paint();

    // The first paint happens before the element is laid out, so clientHeight is
    // 0 and only the overscan rows appear. Repaint once the real size is known.
    let observer = null;
    if (typeof ResizeObserver === 'function') {
      observer = new ResizeObserver(() => { painted = { first: -1, last: -1 }; paint(); });
      observer.observe(scroller);
    }

    host.append(root);
    return {
      node: root,
      repaint: () => { painted = { first: -1, last: -1 }; paint(); },
      destroy: () => { if (observer) observer.disconnect(); root.remove(); },
    };
  }

  AAI.gitgraph = { render, laneColour, edgePath, ROW, LANE, LEFT };
})();
