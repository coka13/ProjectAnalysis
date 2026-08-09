/**
 * Label-visibility guarantees for the charts the app actually draws.
 *
 * The existing charts.test.js suites call `chart.refit()` by hand before
 * measuring. That proves the fitting maths is right, but it is not what the
 * application does: every view builds a chart, hands it to `charts.panel()`
 * and appends the result. Nothing calls `refit`. These tests therefore attach
 * the chart exactly the way a view does and then measure, which is the only
 * way to catch a label that is lost because the automatic fit never ran.
 */
(function () {
  const { suite, test, assert, withStage } = window.AAITest;
  const charts = window.AAI.charts;

  /**
   * Let the chart's own deferred fit run.
   *
   * Deliberately microtask-based rather than frame-based: the harness window is
   * hidden, and a hidden window produces no animation frames, so anything that
   * awaited one would hang forever and the whole suite would be reported as
   * skipped. Draining microtasks also matches the trigger the fix relies on for
   * the common path, which is exactly what these tests are here to prove.
   */
  function settle(turns) {
    let left = turns === undefined ? 6 : turns;
    let chain = Promise.resolve();
    while (left-- > 0) chain = chain.then(() => {});
    return chain;
  }

  /** Every <text> in the chart with its box and the canvas it must stay in. */
  function escapees(chart) {
    const svg = chart.svg;
    const view = svg.viewBox.baseVal;
    const out = [];
    Array.prototype.forEach.call(svg.querySelectorAll('text'), (node) => {
      const box = node.getBBox();
      if (!box.width || !box.height) return;
      const inside =
        box.x >= view.x - 0.5 &&
        box.y >= view.y - 0.5 &&
        box.x + box.width <= view.x + view.width + 0.5 &&
        box.y + box.height <= view.y + view.height + 0.5;
      if (!inside) {
        out.push(
          `"${node.textContent}" [${box.x.toFixed(1)}, ${box.y.toFixed(1)}, ` +
            `${box.width.toFixed(1)}, ${box.height.toFixed(1)}] outside ` +
            `[${view.x}, ${view.y}, ${view.width}, ${view.height}]`,
        );
      }
    });
    return out;
  }

  /** The words of one label, rejoined across the lines it wrapped onto. */
  function labelText(node) {
    const spans = node.querySelectorAll('tspan');
    const parts = spans.length
      ? Array.prototype.map.call(spans, (s) => s.textContent)
      : [node.textContent];
    return parts.join(' ').replace(/\s+/g, ' ').trim();
  }

  const EN = [
    'Architecture', 'Code quality', 'Security', 'Testing',
    'Documentation', 'Maintainability', 'Performance', 'Technical debt',
  ];
  const HE = [
    'ארכיטקטורה', 'איכות קוד', 'אבטחה', 'בדיקות',
    'תיעוד', 'תחזוקתיות', 'ביצועים', 'חוב טכני',
  ];
  const MIXED = [
    'ארכיטקטורה', 'Code quality', 'אבטחה', 'Testing',
    'תיעוד Docs', 'Maintainability', 'ביצועים', 'Technical debt',
  ];
  const scores = (n) => {
    const values = [];
    for (let i = 0; i < n; i += 1) values.push(10 + ((i * 37) % 90));
    return values;
  };

  /** Build the radar the way score.js does, attach it, let it settle. */
  async function mountRadar(stage, axes, options) {
    const chart = charts.radar(
      Object.assign({ axes, series: [{ name: 'score', values: scores(axes.length) }] }, options || {}),
    );
    // Exactly the production path: the chart goes inside a panel, and the
    // panel is what gets appended. Nothing calls refit().
    stage.append(charts.panel('Category balance', chart, { subtitle: 'hint' }));
    await settle(4);
    return chart;
  }

  suite('category balance labels are never lost', () => {
    /**
     * Regression: the automatic fit is scheduled with requestAnimationFrame and
     * gives up when the SVG is not yet in the document. A chart built before it
     * is attached therefore kept the un-grown viewBox and clipped its labels.
     * Only `refit()` rescued it, and no view calls that.
     */
    test('English names fit without anyone calling refit', () =>
      withStage(async (stage) => {
        const chart = await mountRadar(stage, EN);
        assert.equal(escapees(chart).join(' | '), '', 'every axis label must sit inside the canvas');
      }));

    test('Hebrew names fit in an RTL document', () =>
      withStage(async (stage) => {
        stage.dir = 'rtl';
        const chart = await mountRadar(stage, HE);
        assert.equal(escapees(chart).join(' | '), '', 'Hebrew axis labels must sit inside the canvas');
      }));

    test('mixed Hebrew and English names fit', () =>
      withStage(async (stage) => {
        stage.dir = 'rtl';
        const chart = await mountRadar(stage, MIXED);
        assert.equal(escapees(chart).join(' | '), '', 'mixed-direction labels must sit inside the canvas');
      }));

    test('a very long name is shown in full and still fits', () =>
      withStage(async (stage) => {
        const axes = EN.slice();
        axes[0] = 'Cross cutting infrastructure and deployment automation';
        const chart = await mountRadar(stage, axes);
        const labels = Array.prototype.map.call(chart.svg.querySelectorAll('text.axis-label'), labelText);
        const joined = labels.join('|');
        assert.ok(joined.indexOf('…') < 0, `no label may be elided, got ${joined}`);
        // Wrapping is allowed; losing a word is not. The label is compared
        // after its lines have been rejoined, so a name that wrapped onto four
        // lines still has to read back as the original.
        assert.ok(
          labels.indexOf(axes[0]) >= 0,
          `the long name must survive wrapping intact, got ${joined}`,
        );
        assert.equal(escapees(chart).join(' | '), '', 'the long label must sit inside the canvas');
      }));

    test('an unbreakable single word still fits', () =>
      withStage(async (stage) => {
        const chart = await mountRadar(stage, ['Supercalifragilisticexpialidocious', 'B', 'C', 'D']);
        assert.equal(escapees(chart).join(' | '), '', 'a long unbreakable word must sit inside the canvas');
      }));

    test('thirty categories still show every name inside the canvas', () =>
      withStage(async (stage) => {
        const axes = [];
        for (let i = 0; i < 30; i += 1) axes.push(`Category number ${i + 1}`);
        const chart = await mountRadar(stage, axes);
        assert.equal(
          chart.svg.querySelectorAll('text.axis-label').length,
          30,
          'every category keeps a label',
        );
        assert.equal(escapees(chart).join(' | '), '', 'no label may escape with many categories');
      }));

    /**
     * Two labels drawn on top of each other are as unreadable as one that was
     * clipped, so crowding is checked as well as containment.
     */
    test('labels do not overlap each other', () =>
      withStage(async (stage) => {
        const chart = await mountRadar(stage, EN);
        const boxes = Array.prototype.map.call(chart.svg.querySelectorAll('text.axis-label'), (n) => ({
          text: n.textContent,
          box: n.getBBox(),
        }));
        const clashes = [];
        for (let i = 0; i < boxes.length; i += 1) {
          for (let j = i + 1; j < boxes.length; j += 1) {
            const a = boxes[i].box;
            const b = boxes[j].box;
            const overlap =
              a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
            if (overlap) clashes.push(`${boxes[i].text} / ${boxes[j].text}`);
          }
        }
        assert.equal(clashes.join(' | '), '', 'axis labels must not sit on top of one another');
      }));

    /**
     * The window can be narrow, wide, or resized after the chart was drawn.
     * The canvas scales with its container, so the guarantee is that the
     * viewBox still holds every label once the container has changed size.
     * A hidden window delivers no ResizeObserver callbacks, so the refit is
     * invoked directly here; in the running app the observer does it.
     */
    test('labels stay inside the canvas after the container is resized', () =>
      withStage(async (stage) => {
        const chart = await mountRadar(stage, EN);
        for (const width of ['320px', '1200px', '240px', '760px']) {
          stage.style.width = width;
          chart.refit();
          await settle(2);
          assert.equal(escapees(chart).join(' | '), '', `labels must survive a resize to ${width}`);
        }
      }));
  });

  suite('bar chart labels are never lost', () => {
    test('long bar labels fit without anyone calling refit', () =>
      withStage(async (stage) => {
        const chart = charts.bars({
          items: [
            { label: 'Cross cutting infrastructure and deployment automation', value: 42 },
            { label: 'תשתיות חוצות מערכת ואוטומציית פריסה', value: 31 },
            { label: 'Short', value: 12 },
          ],
          width: 640,
        });
        stage.append(charts.panel('Contributions', chart, {}));
        await settle(4);
        assert.equal(escapees(chart).join(' | '), '', 'bar labels must sit inside the canvas');
      }));

    test('horizontal bar labels fit in an RTL document', () =>
      withStage(async (stage) => {
        stage.dir = 'rtl';
        const chart = charts.bars({
          items: [
            { label: 'ארכיטקטורה ותשתיות', value: 42 },
            { label: 'איכות קוד', value: 31 },
          ],
          width: 640,
          horizontal: true,
        });
        stage.append(charts.panel('איזון', chart, {}));
        await settle(4);
        assert.equal(escapees(chart).join(' | '), '', 'RTL bar labels must sit inside the canvas');
      }));
  });

  /**
   * A sweep over every chart type the app draws, in both reading directions,
   * with the kind of content that actually breaks layouts: wide formatted
   * numbers on the y axis, long group names under the x axis, and a free-text
   * caption in the middle of a donut. Each builder is exercised through the
   * production path - panel + append, never refit - so a chart type that was
   * simply never given a fit shows up here rather than in a screenshot.
   */
  suite('every chart keeps its text inside the frame', () => {
    const builders = {
      line: () =>
        charts.line({
          series: [
            { name: 'Lines of code', values: [1204567, 998321, 1310922, 1500004, 1422318] },
            { name: 'שורות קוד', values: [204567, 298321, 310922, 500004, 422318] },
          ],
          labels: ['2024-01', '2024-02', '2024-03', '2024-04', '2024-05'],
          width: 640,
        }),
      donut: () =>
        charts.donut({
          items: [
            { label: 'Python', value: 1200 },
            { label: 'JavaScript', value: 800 },
            { label: 'קבצי תצורה', value: 300 },
          ],
          center: '2,300,000',
          centerSub: 'סך הכול שורות קוד',
        }),
      stack: () =>
        charts.stack({
          keys: ['added', 'removed'],
          groups: [
            { label: 'Cross cutting infrastructure', values: { added: 900, removed: 400 } },
            { label: 'תשתיות חוצות מערכת', values: { added: 500, removed: 250 } },
            { label: 'ui', values: { added: 120, removed: 60 } },
          ],
          width: 640,
        }),
      radar: () =>
        charts.radar({ axes: MIXED, series: [{ name: 'score', values: scores(MIXED.length) }] }),
      bars: () =>
        charts.bars({
          items: [
            { label: 'Cross cutting infrastructure and deployment automation', value: 42 },
            { label: 'תשתיות חוצות מערכת', value: 31 },
          ],
          width: 640,
        }),
    };

    Object.keys(builders).forEach((name) => {
      ['ltr', 'rtl'].forEach((dir) => {
        test(`${name} in ${dir}`, () =>
          withStage(async (stage) => {
            stage.dir = dir;
            const chart = builders[name]();
            stage.append(charts.panel(`${name} panel`, chart, { subtitle: 'hint' }));
            await settle(4);
            assert.equal(
              escapees(chart).join(' | '),
              '',
              `${name} must keep every label inside its canvas in ${dir}`,
            );
          }));
      });
    });

    /**
     * A treemap tile is the one place a label genuinely cannot always be shown
     * in full, so the rule there is different: whenever a label is shortened,
     * the full text must remain reachable. Both the hover tooltip and a native
     * <title> provide that.
     */
    test('treemap tiles that shorten a label keep the full text reachable', () =>
      withStage(async (stage) => {
        const chart = charts.treemap({
          items: [
            { label: 'app/analyzers/jvm_dotnet_analyzer.py', value: 900 },
            { label: 'app/engine/pipeline.py', value: 500 },
            { label: 'web/js/charts.js', value: 260 },
            { label: 'תיקיית ניתוח ארוכה במיוחד', value: 140 },
          ],
          width: 640,
          height: 260,
        });
        stage.append(charts.panel('Hotspots', chart, {}));
        await settle(4);
        const labels = chart.svg.querySelectorAll('text.treemap-label');
        assert.ok(labels.length > 0, 'the treemap must draw some labels');
        Array.prototype.forEach.call(labels, (node) => {
          if (labelText(node).indexOf('…') < 0) return;
          const title = node.querySelector('title');
          assert.ok(title && title.textContent.length, 'a shortened tile label must carry its full text');
        });
      }));
  });
})();
