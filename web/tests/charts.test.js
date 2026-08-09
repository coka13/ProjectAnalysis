/* Chart rendering tests. These run in the real webview, so SVG geometry is real. */
(function () {
  'use strict';

  const { suite, test, assert, withStage } = window.AAITest;
  const charts = window.AAI.charts;

  suite('charts.line', () => {
    /**
     * Regression: the draw-in animation used to set strokeDasharray to a guess
     * (points.length * 60). When the guess was shorter than the real path the
     * dash pattern repeated, so the line rendered as dash-gap-dash and stayed
     * that way - it visibly stopped partway across the chart.
     */
    test('dash length matches the real path length', () => {
      withStage((stage) => {
        const chart = charts.line({
          series: [{ name: 'commits', values: [4, 40, 9] }],
          labels: ['2024-01', '2024-02', '2024-03'],
        });
        stage.append(chart.node);
        const path = chart.svg.querySelector('.series-line');
        assert.ok(path, 'a series line should be rendered');

        const real = path.getTotalLength();
        assert.ok(real > 0, 'the path should have measurable length');

        const dash = parseFloat(path.style.strokeDasharray);
        assert.ok(dash > 0, 'the draw-in animation should set a dash length');
        // A guessed 3 * 60 = 180 against a real length of ~700 is the bug.
        assert.close(dash, real, 1, 'dash length must equal the real path length');
      });
    });

    test('few points over a wide plot still draw a continuous line', () => {
      withStage((stage) => {
        const chart = charts.line({
          series: [{ name: 'score', values: [10, 90] }],
          labels: ['a', 'b'],
          width: 900,
        });
        stage.append(chart.node);
        const path = chart.svg.querySelector('.series-line');
        const dash = parseFloat(path.style.strokeDasharray);
        // Two points 60px apart by the old formula, ~830px in reality.
        assert.ok(dash > 500, `dash length should span the plot, got ${dash}`);
      });
    });

    test('the dash is cleared once the animation finishes', async () => {
      await withStage(async (stage) => {
        const chart = charts.line({
          series: [{ name: 'score', values: [1, 5, 3, 8] }],
          labels: ['a', 'b', 'c', 'd'],
        });
        stage.append(chart.node);
        const path = chart.svg.querySelector('.series-line');
        path.dispatchEvent(new Event('transitionend'));
        assert.equal(path.style.strokeDasharray, '', 'dash should be removed so the stroke is solid');
        assert.equal(path.style.strokeDashoffset, '', 'dash offset should be removed');
      });
    });

    test('a single point does not break rendering', () => {
      withStage((stage) => {
        const chart = charts.line({ series: [{ name: 'one', values: [7] }], labels: ['a'] });
        stage.append(chart.node);
        assert.ok(chart.svg.querySelector('.series-line'), 'a one-point series still renders a path');
      });
    });

    test('gaps in a series do not break the line', () => {
      withStage((stage) => {
        const chart = charts.line({
          series: [{ name: 'sparse', values: [1, null, 3, undefined, 5] }],
          labels: ['a', 'b', 'c', 'd', 'e'],
        });
        stage.append(chart.node);
        const path = chart.svg.querySelector('.series-line');
        const dash = parseFloat(path.style.strokeDasharray);
        assert.close(dash, path.getTotalLength(), 1, 'dash length must match even with gaps');
      });
    });
  });

  suite('charts.gauge', () => {
    test('a low score still draws a visible arc', () => {
      withStage((stage) => {
        const chart = charts.gauge({ value: 0, max: 100 });
        stage.append(chart.node || chart);
        const svg = (chart.node || chart).querySelector('svg');
        assert.ok(svg, 'gauge renders an svg');
      });
    });
  });

  suite('icons inside charts', () => {
    /**
     * Regression: `.chart svg { width: 100% }` also matched icons placed inside
     * a chart container, stretching an 18px glyph across the whole panel.
     */
    test('an icon in a chart container keeps its own size', () => {
      withStage((stage) => {
        const holder = document.createElement('div');
        holder.className = 'chart';
        const icon = window.AAI.dom.icon('info');
        holder.append(icon);
        stage.append(holder);
        const width = icon.getBoundingClientRect().width;
        assert.ok(width > 0 && width <= 64, `icon should stay small, measured ${width}px`);
      });
    });
  });

  /* ------------------------------------------------------------- labels */

  /** Every <text> in the chart, with its box and the canvas it must stay in. */
  function labelBoxes(chart) {
    const svg = chart.svg;
    const view = svg.viewBox.baseVal;
    return Array.prototype.map.call(svg.querySelectorAll('text'), (node) => ({
      text: node.textContent,
      box: node.getBBox(),
      view,
    }));
  }

  function assertInsideCanvas(chart, message) {
    labelBoxes(chart).forEach((entry) => {
      const { box, view } = entry;
      if (!box.width) return;
      const inside =
        box.x >= view.x - 0.5 &&
        box.y >= view.y - 0.5 &&
        box.x + box.width <= view.x + view.width + 0.5 &&
        box.y + box.height <= view.y + view.height + 0.5;
      assert.ok(
        inside,
        `${message}: "${entry.text}" at [${box.x.toFixed(1)}, ${box.y.toFixed(1)}, ` +
          `${box.width.toFixed(1)}, ${box.height.toFixed(1)}] escapes viewBox ` +
          `[${view.x}, ${view.y}, ${view.width}, ${view.height}]`,
      );
    });
  }

  const LONG_CATEGORIES = [
    'Architecture', 'Code quality', 'Security', 'Testing',
    'Documentation', 'Maintainability', 'Performance', 'Technical debt',
  ];
  const HEBREW_CATEGORIES = [
    'ארכיטקטורה', 'איכות קוד', 'אבטחה', 'בדיקות',
    'תיעוד', 'תחזוקתיות', 'ביצועים', 'חוב טכני',
  ];

  suite('charts.radar labels', () => {
    /**
     * Regression: axis labels were cut at 15 characters, so the Category
     * Balance chart displayed "Maintainabilit…". A category the reader cannot
     * name is worse than no chart at all.
     */
    test('a long category name is never shortened', () => {
      withStage((stage) => {
        const chart = charts.radar({
          axes: LONG_CATEGORIES,
          series: [{ name: 'score', values: [80, 70, 60, 50, 40, 30, 20, 10] }],
        });
        stage.append(chart.node);
        const rendered = Array.prototype.map
          .call(chart.svg.querySelectorAll('text.axis-label'), (n) => n.textContent.replace(/\s+/g, ' '))
          .join('|');
        assert.ok(rendered.indexOf('…') < 0, `no label may be elided, got ${rendered}`);
        LONG_CATEGORIES.forEach((name) => {
          assert.ok(
            rendered.indexOf(name.replace(/\s+/g, ' ')) >= 0,
            `"${name}" should appear in full, got ${rendered}`,
          );
        });
      });
    });

    test('every label carries the full name for hovering', () => {
      withStage((stage) => {
        const chart = charts.radar({
          axes: LONG_CATEGORIES,
          series: [{ name: 'score', values: [80, 70, 60, 50, 40, 30, 20, 10] }],
        });
        stage.append(chart.node);
        const labels = chart.svg.querySelectorAll('text.axis-label');
        assert.equal(labels.length, LONG_CATEGORIES.length, 'one label per axis');
      });
    });

    /**
     * The chart scales with its container, so a label inside the viewBox is
     * safe at any window size, DPI or zoom. Escaping the viewBox is the only
     * way a label can actually be lost, which makes this the whole guarantee.
     */
    test('an over-long single word still fits after the canvas is refitted', () => {
      withStage((stage) => {
        const chart = charts.radar({
          axes: ['Supercalifragilistic', 'B', 'C', 'D'],
          series: [{ name: 'score', values: [80, 70, 60, 50] }],
        });
        stage.append(chart.node);
        chart.refit();
        assertInsideCanvas(chart, 'radar');
      });
    });

    test('Hebrew category names fit and are not reversed out of the canvas', () => {
      withStage((stage) => {
        stage.dir = 'rtl';
        const chart = charts.radar({
          axes: HEBREW_CATEGORIES,
          series: [{ name: 'ציון', values: [80, 70, 60, 50, 40, 30, 20, 10] }],
        });
        stage.append(chart.node);
        chart.refit();
        assertInsideCanvas(chart, 'radar (he)');
      });
    });
  });

  suite('chart labels in an RTL document', () => {
    /**
     * Regression: in a Hebrew UI the SVG inherited direction:rtl, which flips
     * what `text-anchor: start` means. Labels then ran backwards off the left
     * edge - a treemap label was measured at x = -22 inside a viewBox that
     * starts at 0, so the filename was simply invisible.
     */
    test('a treemap label stays on the canvas when the page is RTL', () => {
      withStage((stage) => {
        stage.dir = 'rtl';
        const chart = charts.treemap({
          items: [
            { label: 'CI.yml', value: 60 },
            { label: 'app/main.py', value: 30 },
            { label: 'README.md', value: 10 },
          ],
          width: 600,
          height: 300,
        });
        stage.append(chart.node);
        assertInsideCanvas(chart, 'treemap (rtl)');
      });
    });

    test('the chart canvas pins itself to ltr regardless of the page', () => {
      withStage((stage) => {
        stage.dir = 'rtl';
        const chart = charts.bars({ items: [{ label: 'מודול', value: 5 }] });
        stage.append(chart.node);
        assert.equal(
          getComputedStyle(chart.svg).direction,
          'ltr',
          'chart geometry must not be mirrored by the document direction',
        );
      });
    });
  });

  suite('charts.bars labels', () => {
    test('a long row label is not shortened', () => {
      withStage((stage) => {
        const label = 'a/very/long/module/path/that/keeps/going/on.py';
        const chart = charts.bars({ items: [{ label, value: 10 }] });
        stage.append(chart.node);
        chart.refit();
        const text = chart.svg.querySelector('text.axis-label').textContent;
        assert.equal(text, label, 'the row label should be printed in full');
      });
    });
  });
})();
