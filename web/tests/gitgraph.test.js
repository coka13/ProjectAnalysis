/**
 * Browser tests for the commit graph renderer and for the load integrity of the
 * application scripts.
 *
 * The failure this file exists to prevent is the one the user reported: branch
 * lines that disappear partway down the graph. That happens when virtualisation
 * decides visibility from an edge's endpoints instead of the rows it spans, so
 * a long edge crossing the viewport is dropped even though it is on screen.
 */
(function () {
  'use strict';

  const { suite, test, assert, withStage } = window.AAITest;

  function commit(sha, lane, parents, links) {
    return {
      sha,
      short: sha.slice(0, 7),
      lane,
      parents: parents || [],
      links: links || [],
      refs: [],
      subject: `subject ${sha}`,
      author: 'Test',
      date: '2024-01-01',
    };
  }

  /** A straight chain of `n` commits in lane 0. */
  function chain(n) {
    const commits = [];
    for (let i = 0; i < n; i += 1) {
      const sha = `c${String(i).padStart(3, '0')}`;
      const parent = i + 1 < n ? `c${String(i + 1).padStart(3, '0')}` : null;
      commits.push(commit(sha, 0, parent ? [parent] : [], parent ? [{ from: 0, to: 0, parent }] : []));
    }
    return { available: true, commits, lanes: 1, count: n, truncated: false };
  }

  suite('application scripts', () => {
    test('every script parses and evaluates', () => {
      assert.equal((window.__SCRIPT_ERRORS__ || []).join(' | '), '', 'a script failed to load');
    });

    test('every namespace the shell depends on is registered', () => {
      ['dom', 'i18n', 'api', 'charts', 'gitgraph', 'palette', 'viewer', 'score'].forEach((name) => {
        assert.ok(window.AAI[name], `AAI.${name} is missing`);
      });
    });
  });

  suite('gitgraph', () => {
    test('renders a row per visible commit', () => {
      withStage((stage) => {
        const handle = window.AAI.gitgraph.render(stage, chain(12));
        assert.equal(stage.querySelectorAll('.gitgraph-row').length, 12);
        handle.destroy();
      });
    });

    test('every commit is connected to its parent', () => {
      withStage((stage) => {
        const handle = window.AAI.gitgraph.render(stage, chain(10));
        // 10 commits, 9 links, all inside the viewport.
        assert.equal(stage.querySelectorAll('.gitgraph-edge').length, 9);
        handle.destroy();
      });
    });

    test('an edge spanning the whole viewport is still drawn', () => {
      withStage((stage) => {
        // One commit at the top, its parent far below the fold: neither endpoint
        // is on screen once we scroll into the middle, but the line crosses it.
        const commits = [];
        for (let i = 0; i < 200; i += 1) {
          commits.push(commit(`c${i}`, i === 0 ? 1 : 0, [], []));
        }
        commits[0].lane = 1;
        commits[0].parents = ['c199'];
        commits[0].links = [{ from: 1, to: 0, parent: 'c199' }];

        const handle = window.AAI.gitgraph.render(stage, {
          available: true, commits, lanes: 2, count: 200, truncated: false,
        });
        const scroller = stage.querySelector('.gitgraph-scroll');
        scroller.scrollTop = 100 * window.AAI.gitgraph.ROW;
        handle.repaint();
        assert.equal(stage.querySelectorAll('.gitgraph-edge').length, 1, 'the long edge was dropped');
        handle.destroy();
      });
    });

    test('a parent outside the window keeps a dangling stub', () => {
      withStage((stage) => {
        const payload = chain(5);
        payload.commits[4].parents = ['missing'];
        payload.commits[4].links = [{ from: 0, to: 0, parent: 'missing' }];
        const handle = window.AAI.gitgraph.render(stage, payload);
        assert.equal(stage.querySelectorAll('.gitgraph-edge.dangling').length, 1);
        handle.destroy();
      });
    });

    test('merge commits get a larger dot', () => {
      withStage((stage) => {
        const payload = chain(3);
        payload.commits[0].parents = ['c001', 'c002'];
        const handle = window.AAI.gitgraph.render(stage, payload);
        const dots = stage.querySelectorAll('.gitgraph-dot');
        assert.equal(dots.length, 3);
        assert.equal(stage.querySelectorAll('.gitgraph-dot.merge').length, 1);
        handle.destroy();
      });
    });

    test('long histories are virtualised, not fully rendered', () => {
      withStage((stage) => {
        const handle = window.AAI.gitgraph.render(stage, chain(4000));
        const rendered = stage.querySelectorAll('.gitgraph-row').length;
        assert.ok(rendered > 0, 'nothing was rendered');
        assert.ok(rendered < 400, `expected virtualisation, rendered ${rendered} rows`);
        handle.destroy();
      });
    });

    test('the canvas is tall enough to scroll through every commit', () => {
      withStage((stage) => {
        const handle = window.AAI.gitgraph.render(stage, chain(500));
        const canvas = stage.querySelector('.gitgraph-canvas');
        assert.equal(parseInt(canvas.style.height, 10), 500 * window.AAI.gitgraph.ROW);
        handle.destroy();
      });
    });

    test('destroy removes the graph from the page', () => {
      withStage((stage) => {
        const handle = window.AAI.gitgraph.render(stage, chain(5));
        handle.destroy();
        assert.equal(stage.querySelectorAll('.gitgraph').length, 0);
      });
    });

    test('an empty history renders without throwing', () => {
      withStage((stage) => {
        const handle = window.AAI.gitgraph.render(stage, { available: true, commits: [], lanes: 0 });
        assert.equal(stage.querySelectorAll('.gitgraph-row').length, 0);
        handle.destroy();
      });
    });

    test('lane colours are stable and wrap around', () => {
      const { laneColour } = window.AAI.gitgraph;
      assert.equal(laneColour(0), laneColour(0));
      assert.ok(laneColour(0) !== laneColour(1), 'adjacent lanes share a colour');
      assert.ok(laneColour(99), 'a high lane index produced no colour');
    });
  });
})();
