/**
 * Interactive diagram viewer: Mermaid rendering plus zoom, pan, search and
 * PNG / SVG / PDF export. Mermaid is vendored locally so the whole thing works
 * with no network access. Files are written through the Python bridge, because
 * browser downloads are not available for a file:// document.
 */
(function () {
  const AAI = (window.AAI = window.AAI || {});
  const { t, direction } = AAI.i18n;
  const { toast } = AAI.dom;

  let initialised = false;
  let renderSeq = 0;

  /** Groups mermaid treats as a focusable "node" across every diagram kind. */
  const NODE_SELECTOR = 'g.node, g.classGroup, g.statediagram-state, g.entityBox, .er.entityBox, g.actor';
  /** Above this many nodes a diagram is dense enough to suggest drilling in. */
  const LARGE_DIAGRAM_NODES = 40;

  function theme() {
    return document.documentElement.dataset.theme === 'light' ? 'default' : 'dark';
  }

  function ensureMermaid() {
    if (!window.mermaid) {
      throw new Error('Mermaid failed to load (web/vendor/mermaid.min.js is required for offline use)');
    }
    window.mermaid.initialize({
      startOnLoad: false,
      theme: theme(),
      securityLevel: 'strict',
      fontFamily: 'Segoe UI, system-ui, Arial, sans-serif',
      flowchart: { curve: 'basis', nodeSpacing: 42, rankSpacing: 62, useMaxWidth: false, htmlLabels: false },
      sequence: { useMaxWidth: false, wrap: true, width: 170 },
      class: { useMaxWidth: false },
      er: { useMaxWidth: false },
      state: { useMaxWidth: false },
    });
    initialised = true;
  }

  function refreshTheme() {
    ensureMermaid();
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]),
    );
  }

  function fileName(diagram) {
    const base = (diagram && (diagram.title || diagram.kind)) || 'diagram';
    return (
      base
        .replace(/[^\w\u0590-\u05FF-]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 60) || 'diagram'
    );
  }

  async function saveBlob(blob, name) {
    const base64 = await AAI.api.blobToBase64(blob);
    const result = await AAI.api.saveBinary(name, base64);
    if (result.saved) toast(t('common.savedTo', { path: result.path }), 'success');
  }

  // ------------------------------------------------------------------ PDF
  function latin1(str) {
    const out = new Uint8Array(str.length);
    for (let i = 0; i < str.length; i += 1) out[i] = str.charCodeAt(i) & 0xff;
    return out;
  }

  /** Minimal single-page PDF wrapping a JPEG image - no external dependency. */
  function jpegToPdf(jpeg, pixelWidth, pixelHeight) {
    const pageWidth = Math.round((pixelWidth * 72) / 96);
    const pageHeight = Math.round((pixelHeight * 72) / 96);
    const chunks = [];
    const offsets = [];
    let cursor = 0;

    const push = (data) => {
      const bytes = typeof data === 'string' ? latin1(data) : data;
      chunks.push(bytes);
      cursor += bytes.length;
    };
    const object = (index, body, stream) => {
      offsets[index] = cursor;
      push(`${index} 0 obj\n${body}\n`);
      if (stream) {
        push('stream\n');
        push(stream);
        push('\nendstream\n');
      }
      push('endobj\n');
    };

    const content = `q ${pageWidth} 0 0 ${pageHeight} 0 0 cm /Im0 Do Q`;

    push('%PDF-1.4\n');
    object(1, '<< /Type /Catalog /Pages 2 0 R >>');
    object(2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>');
    object(
      3,
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidth} ${pageHeight}] ` +
        '/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>',
    );
    object(
      4,
      '<< /Type /XObject /Subtype /Image /Width ' +
        `${pixelWidth} /Height ${pixelHeight} /ColorSpace /DeviceRGB ` +
        `/BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>`,
      jpeg,
    );
    object(5, `<< /Length ${content.length} >>`, latin1(content));

    const xrefStart = cursor;
    let xref = 'xref\n0 6\n0000000000 65535 f \n';
    for (let i = 1; i <= 5; i += 1) {
      xref += `${String(offsets[i]).padStart(10, '0')} 00000 n \n`;
    }
    push(xref);
    push(`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xrefStart}\n%%EOF\n`);

    const out = new Uint8Array(cursor);
    let at = 0;
    for (const chunk of chunks) {
      out.set(chunk, at);
      at += chunk.length;
    }
    return out;
  }

  // --------------------------------------------------------------- viewer
  class DiagramViewer {
    constructor(container) {
      this.root = container;
      this.scale = 1;
      this.offset = { x: 0, y: 0 };
      this.userAdjusted = false;
      this.diagram = null;
      this.build();
    }

    build() {
      this.root.classList.add('viewer');
      this.root.innerHTML = `
        <div class="viewer-toolbar">
          <button class="btn sm" data-act="zoom-in" data-i18n-title="viewer.zoomIn">+</button>
          <button class="btn sm" data-act="zoom-out" data-i18n-title="viewer.zoomOut">−</button>
          <button class="btn sm" data-act="fit" data-i18n="viewer.fit"></button>
          <button class="btn sm" data-act="center" data-i18n="viewer.center"></button>
          <button class="btn sm" data-act="reset" data-i18n="viewer.reset"></button>
          <input type="search" data-role="search" data-i18n-placeholder="viewer.search" />
          <span class="small muted" data-role="matches"></span>
          <span class="small muted" data-role="hint"></span>
          <span class="grow"></span>
          <button class="btn sm" data-act="png">PNG</button>
          <button class="btn sm" data-act="svg">SVG</button>
          <button class="btn sm" data-act="pdf">PDF</button>
          <button class="btn sm" data-act="fullscreen" aria-pressed="false" data-i18n="viewer.fullscreen"></button>
        </div>
        <div class="viewer-stage" data-role="stage">
          <div class="viewer-canvas" data-role="canvas"></div>
        </div>`;

      this.stage = this.root.querySelector('[data-role="stage"]');
      this.canvas = this.root.querySelector('[data-role="canvas"]');
      this.searchInput = this.root.querySelector('[data-role="search"]');
      this.matchLabel = this.root.querySelector('[data-role="matches"]');
      this.hintLabel = this.root.querySelector('[data-role="hint"]');

      this.root.querySelector('.viewer-toolbar').addEventListener('click', (event) => {
        const target = event.target.closest('[data-act]');
        if (!target) return;
        const actions = {
          'zoom-in': () => this.zoom(1.2),
          'zoom-out': () => this.zoom(1 / 1.2),
          fit: () => this.fit(),
          center: () => this.center(),
          reset: () => this.reset(),
          png: () => this.exportImage('png'),
          svg: () => this.exportImage('svg'),
          pdf: () => this.exportPdf(),
          fullscreen: () => this.toggleFullscreen(),
        };
        const run = actions[target.dataset.act];
        if (run) run();
      });

      let searchTimer = null;
      this.searchInput.addEventListener('input', () => {
        clearTimeout(searchTimer);
        // Re-scanning every node on each keystroke is expensive on large
        // diagrams, so wait for a short pause in typing.
        searchTimer = setTimeout(() => this.search(this.searchInput.value.trim()), 140);
      });
      this.searchInput.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !this.searchInput.value) return;
        event.stopPropagation();
        clearTimeout(searchTimer);
        this.searchInput.value = '';
        this.search('');
      });

      this.stage.addEventListener(
        'wheel',
        (event) => {
          event.preventDefault();
          this.userAdjusted = true;
          const rect = this.stage.getBoundingClientRect();
          this.zoom(event.deltaY < 0 ? 1.12 : 1 / 1.12, {
            x: event.clientX - rect.left,
            y: event.clientY - rect.top,
          });
        },
        { passive: false },
      );

      let dragging = false;
      let dragMoved = false;
      let origin = null;
      let start = null;
      this.stage.addEventListener('pointerdown', (event) => {
        dragging = true;
        dragMoved = false;
        start = { x: event.clientX, y: event.clientY };
        origin = { x: event.clientX - this.offset.x, y: event.clientY - this.offset.y };
        this.userAdjusted = true;
        this.stage.classList.add('dragging');
        this.stage.setPointerCapture(event.pointerId);
      });
      this.stage.addEventListener('pointermove', (event) => {
        if (!dragging) return;
        if (!dragMoved && (Math.abs(event.clientX - start.x) > 4 || Math.abs(event.clientY - start.y) > 4)) {
          dragMoved = true;
        }
        this.offset = { x: event.clientX - origin.x, y: event.clientY - origin.y };
        this.apply();
      });
      const stop = (event) => {
        dragging = false;
        this.stage.classList.remove('dragging');
        if (event.pointerId !== undefined && this.stage.hasPointerCapture?.(event.pointerId)) {
          this.stage.releasePointerCapture(event.pointerId);
        }
      };
      this.stage.addEventListener('pointerup', stop);
      this.stage.addEventListener('pointercancel', stop);

      /** A plain click (not the tail of a drag) on a node zooms in on it. */
      this.stage.addEventListener('click', (event) => {
        if (dragMoved) { dragMoved = false; return; }
        const target = event.target.closest(NODE_SELECTOR);
        if (target) this.focusOn(target);
      });

      if (typeof ResizeObserver !== 'undefined') {
        this.resizeObserver = new ResizeObserver(() => {
          if (!this.userAdjusted) this.fit();
        });
        this.resizeObserver.observe(this.stage);
      }
    }

    async render(diagram) {
      if (!initialised) ensureMermaid();
      this.diagram = diagram;
      const id = `mmd-${(renderSeq += 1)}`;
      try {
        const { svg } = await window.mermaid.render(id, diagram.mermaid);
        this.canvas.innerHTML = svg;
        const node = this.canvas.querySelector('svg');
        if (node) {
          this.sizeToViewBox(node);
          node.style.direction = 'ltr';
          this.announceDensity(node);
        }
        this.userAdjusted = false;
        this.fit();
      } catch (error) {
        this.canvas.innerHTML = `<pre style="color:var(--danger)">${escapeHtml(
          String((error && error.message) || error),
        )}</pre>`;
        if (this.hintLabel) this.hintLabel.textContent = '';
      }
      this.search(this.searchInput.value.trim());
    }

    /**
     * Mermaid's SVG carries a viewBox in "diagram units"; pin the element's
     * own CSS box to those exact units so 1 unit == 1 CSS pixel before any
     * zoom transform is applied. Without this, fit()/zoom() (which always
     * measure the diagram through getBBox(), reported in viewBox units) end
     * up scaling against a rendered size the browser chose on its own - the
     * root cause of diagrams opening cropped, corner-anchored or at the
     * wrong zoom level.
     */
    sizeToViewBox(node) {
      const box = node.viewBox && node.viewBox.baseVal;
      const width = (box && box.width) || parseFloat(node.getAttribute('width')) || (node.getBBox && node.getBBox().width) || 1;
      const height = (box && box.height) || parseFloat(node.getAttribute('height')) || (node.getBBox && node.getBBox().height) || 1;
      node.removeAttribute('width');
      node.removeAttribute('height');
      node.style.width = `${width}px`;
      node.style.height = `${height}px`;
      node.style.display = 'block';
    }

    /** Surface a hint when a diagram is dense enough that drilling in helps. */
    announceDensity(svg) {
      if (!this.hintLabel) return;
      const count = svg.querySelectorAll(NODE_SELECTOR).length;
      this.hintLabel.textContent = count > LARGE_DIAGRAM_NODES ? t('viewer.large') : '';
    }

    /**
     * The size actually being transformed by fit()/reset()/center() is the
     * SVG's own CSS box (pinned to its viewBox in sizeToViewBox) - not its
     * getBBox() ink extents, which mermaid often pads a little smaller than
     * the viewBox. Using bbox here would centre/scale for a box slightly
     * different from the one on screen, letting the diagram spill past the
     * edge of the stage after "fit".
     */
    intrinsicSize(svg) {
      const width = parseFloat(svg.style.width) || (svg.getBBox && svg.getBBox().width) || svg.clientWidth || 1;
      const height = parseFloat(svg.style.height) || (svg.getBBox && svg.getBBox().height) || svg.clientHeight || 1;
      return { width, height };
    }

    apply() {
      this.canvas.style.transform = `translate(${this.offset.x}px, ${this.offset.y}px) scale(${this.scale})`;
    }

    zoom(factor, pivot) {
      const next = Math.min(6, Math.max(0.1, this.scale * factor));
      if (pivot) {
        const ratio = next / this.scale;
        this.offset = {
          x: pivot.x - (pivot.x - this.offset.x) * ratio,
          y: pivot.y - (pivot.y - this.offset.y) * ratio,
        };
      }
      this.scale = next;
      this.apply();
    }

    /** Recentre the diagram in the stage without changing the current zoom. */
    center() {
      const svg = this.canvas.querySelector('svg');
      if (!svg) return;
      const { width, height } = this.intrinsicSize(svg);
      const stageRect = this.stage.getBoundingClientRect();
      this.offset = {
        x: (stageRect.width - width * this.scale) / 2,
        y: (stageRect.height - height * this.scale) / 2,
      };
      this.apply();
    }

    /** Zoom in on a single node, centring it - a lightweight drill-down. */
    focusOn(target) {
      if (!target.getBBox) return;
      const box = target.getBBox();
      const stageRect = this.stage.getBoundingClientRect();
      this.scale = Math.min(2.5, Math.max(this.scale * 1.6, 1.5));
      this.offset = {
        x: stageRect.width / 2 - (box.x + box.width / 2) * this.scale,
        y: stageRect.height / 2 - (box.y + box.height / 2) * this.scale,
      };
      this.userAdjusted = true;
      this.apply();
    }

    reset() {
      const svg = this.canvas.querySelector('svg');
      this.scale = 1;
      if (svg) {
        const { width, height } = this.intrinsicSize(svg);
        const stageRect = this.stage.getBoundingClientRect();
        this.offset = {
          x: (stageRect.width - width) / 2,
          y: (stageRect.height - height) / 2,
        };
      } else {
        this.offset = { x: 0, y: 0 };
      }
      this.userAdjusted = false;
      this.apply();
    }

    fit() {
      const svg = this.canvas.querySelector('svg');
      if (!svg) return;
      const stageRect = this.stage.getBoundingClientRect();
      const { width, height } = this.intrinsicSize(svg);
      const scale = Math.min((stageRect.width - 36) / width, (stageRect.height - 36) / height, 2.5);
      this.scale = Math.max(0.12, scale || 1);
      this.offset = {
        x: (stageRect.width - width * this.scale) / 2,
        y: (stageRect.height - height * this.scale) / 2,
      };
      this.userAdjusted = false;
      this.apply();
    }

    /** Highlight matching nodes and dim the rest so large diagrams stay navigable. */
    search(term) {
      const svg = this.canvas.querySelector('svg');
      if (!svg) return;
      const nodes = svg.querySelectorAll(NODE_SELECTOR);
      nodes.forEach((node) => node.classList.remove('highlight', 'dimmed'));
      if (!term) {
        this.matchLabel.textContent = '';
        return;
      }
      const needle = term.toLowerCase();
      let matches = 0;
      nodes.forEach((node) => {
        const text = (node.textContent || '').toLowerCase();
        if (text.includes(needle)) {
          node.classList.add('highlight');
          matches += 1;
        } else {
          node.classList.add('dimmed');
        }
      });
      this.matchLabel.textContent = matches ? t('viewer.matches', { count: matches }) : t('viewer.noMatch');
    }

    toggleFullscreen() {
      const expanded = this.root.classList.toggle('fullscreen');
      const button = this.root.querySelector('[data-act="fullscreen"]');
      if (button) button.setAttribute('aria-pressed', String(expanded));

      if (this.escapeFullscreen) {
        document.removeEventListener('keydown', this.escapeFullscreen);
        this.escapeFullscreen = null;
      }
      if (expanded) {
        this.escapeFullscreen = (event) => {
          if (!document.contains(this.root)) {
            document.removeEventListener('keydown', this.escapeFullscreen);
            this.escapeFullscreen = null;
            return;
          }
          if (event.key !== 'Escape') return;
          event.preventDefault();
          this.toggleFullscreen();
        };
        document.addEventListener('keydown', this.escapeFullscreen);
      }

      this.userAdjusted = false;
      requestAnimationFrame(() => this.fit());
    }

    serialiseSvg() {
      const svg = this.canvas.querySelector('svg');
      if (!svg) return null;
      const clone = svg.cloneNode(true);
      clone.querySelectorAll('.highlight, .dimmed').forEach((node) =>
        node.classList.remove('highlight', 'dimmed'),
      );
      const box = svg.getBBox();
      clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
      clone.setAttribute('width', Math.ceil(box.width + 40));
      clone.setAttribute('height', Math.ceil(box.height + 40));
      clone.setAttribute('viewBox', `${box.x - 20} ${box.y - 20} ${box.width + 40} ${box.height + 40}`);
      const background = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      background.setAttribute('x', box.x - 20);
      background.setAttribute('y', box.y - 20);
      background.setAttribute('width', box.width + 40);
      background.setAttribute('height', box.height + 40);
      background.setAttribute('fill', getComputedStyle(document.body).backgroundColor);
      clone.insertBefore(background, clone.firstChild);
      return {
        markup: new XMLSerializer().serializeToString(clone),
        width: box.width + 40,
        height: box.height + 40,
      };
    }

    /** Rasterise the current SVG at the requested scale. */
    rasterise(serialised, scale, type, quality) {
      return new Promise((resolve) => {
        const image = new Image();
        image.onload = () => {
          const canvas = document.createElement('canvas');
          canvas.width = Math.ceil(serialised.width * scale);
          canvas.height = Math.ceil(serialised.height * scale);
          const ctx = canvas.getContext('2d');
          if (type === 'image/jpeg') {
            ctx.fillStyle = getComputedStyle(document.body).backgroundColor || '#ffffff';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
          }
          ctx.scale(scale, scale);
          ctx.drawImage(image, 0, 0);
          canvas.toBlob((blob) => resolve({ blob, width: canvas.width, height: canvas.height }), type, quality);
        };
        image.onerror = () => resolve(null);
        image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(serialised.markup)}`;
      });
    }

    async exportImage(format) {
      const serialised = this.serialiseSvg();
      if (!serialised) return;
      const name = fileName(this.diagram);
      if (format === 'svg') {
        await saveBlob(new Blob([serialised.markup], { type: 'image/svg+xml' }), `${name}.svg`);
        return;
      }
      const raster = await this.rasterise(serialised, 2, 'image/png');
      if (raster && raster.blob) await saveBlob(raster.blob, `${name}.png`);
    }

    async exportPdf() {
      const serialised = this.serialiseSvg();
      if (!serialised) return;
      const raster = await this.rasterise(serialised, 2, 'image/jpeg', 0.95);
      if (!raster || !raster.blob) return;
      const jpeg = new Uint8Array(await raster.blob.arrayBuffer());
      const pdf = jpegToPdf(jpeg, raster.width, raster.height);
      const result = await AAI.api.saveBinary(`${fileName(this.diagram)}.pdf`, AAI.api.bytesToBase64(pdf));
      if (result.saved) toast(t('common.savedTo', { path: result.path }), 'success');
    }
  }

  AAI.viewer = { DiagramViewer, refreshTheme, saveBlob, direction };
})();
