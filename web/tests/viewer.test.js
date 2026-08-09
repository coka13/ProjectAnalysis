/**
 * Diagram viewer placement, in both reading directions.
 *
 * The viewer centres a diagram arithmetically - offset.x = (stageWidth -
 * width * scale) / 2 applied as a translate() with transform-origin at 0 0 -
 * which is only correct if the untransformed SVG starts at the stage's left
 * edge. It does in English. In Hebrew the document is direction:rtl, and a
 * block box with an explicit width (which is exactly what sizeToViewBox
 * produces) is laid out flush against the RIGHT edge instead, so the same
 * translate pushed every diagram narrower than the stage off to one side.
 *
 * These tests run the real DiagramViewer against the real stylesheet and
 * compare measured pixels, because that layout rule is a property of the
 * rendering engine and cannot be asserted from the source alone.
 */
(function () {
  'use strict';

  const { suite, test, assert, withStage } = window.AAITest;

  /** A stand-in for a mermaid render: a viewBox-bearing SVG of a known size. */
  const DIAGRAM = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 120">'
    + '<rect x="0" y="0" width="240" height="120" fill="#888"></rect></svg>';

  const WIDE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2400 1200">'
    + '<rect x="0" y="0" width="2400" height="1200" fill="#888"></rect></svg>';

  /**
   * Build a viewer inside a hidden stage under the given document direction and
   * return measurements. The transition is disabled first: the transform is
   * read back immediately and an in-flight animation would report a position
   * the diagram is only passing through.
   */
  function place(dir, markup, act) {
    const html = document.documentElement;
    const previous = html.getAttribute('dir');
    html.setAttribute('dir', dir);
    try {
      return withStage((host) => {
        const mount = document.createElement('div');
        host.append(mount);
        const viewer = new window.AAI.viewer.DiagramViewer(mount);
        viewer.canvas.style.transition = 'none';
        viewer.canvas.innerHTML = markup || DIAGRAM;
        const node = viewer.canvas.querySelector('svg');
        viewer.sizeToViewBox(node);
        node.style.direction = 'ltr';
        (act || ((v) => v.fit()))(viewer);
        const stage = viewer.stage.getBoundingClientRect();
        const box = node.getBoundingClientRect();
        return {
          stage,
          box,
          scale: viewer.scale,
          offset: { x: viewer.offset.x, y: viewer.offset.y },
          dx: (box.left + box.width / 2) - (stage.left + stage.width / 2),
          dy: (box.top + box.height / 2) - (stage.top + stage.height / 2),
        };
      });
    } finally {
      if (previous === null) html.removeAttribute('dir');
      else html.setAttribute('dir', previous);
    }
  }

  suite('diagram viewer centring', () => {
    test('fit centres a small diagram in English', () => {
      const seen = place('ltr');
      assert.close(seen.dx, 0, 1.5, 'the diagram must sit on the stage centre line');
      assert.close(seen.dy, 0, 1.5, 'the diagram must sit on the stage middle');
    });

    test('fit centres a small diagram in Hebrew', () => {
      const seen = place('rtl');
      assert.close(seen.dx, 0, 1.5, 'RTL must not push the diagram off centre');
      assert.close(seen.dy, 0, 1.5, 'RTL must not push the diagram off the middle');
    });

    test('both directions place the diagram identically', () => {
      const ltr = place('ltr');
      const rtl = place('rtl');
      assert.close(rtl.dx, ltr.dx, 0.75, 'the reading direction must not move the diagram');
      assert.close(rtl.scale, ltr.scale, 0.001, 'the reading direction must not change the zoom');
    });

    test('a fitted diagram never spills past the stage in Hebrew', () => {
      [DIAGRAM, WIDE].forEach((markup) => {
        const seen = place('rtl', markup);
        assert.ok(seen.box.left >= seen.stage.left - 1, 'the diagram must not spill past the start edge');
        assert.ok(seen.box.right <= seen.stage.right + 1, 'the diagram must not spill past the end edge');
        assert.ok(seen.box.top >= seen.stage.top - 1, 'the diagram must not spill above the stage');
        assert.ok(seen.box.bottom <= seen.stage.bottom + 1, 'the diagram must not spill below the stage');
      });
    });

    test('centre recentres without changing the zoom in Hebrew', () => {
      const seen = place('rtl', DIAGRAM, (viewer) => {
        viewer.scale = 1.5;
        viewer.offset = { x: -400, y: -80 };
        viewer.apply();
        viewer.center();
      });
      assert.close(seen.scale, 1.5, 0.001, 'centre must leave the zoom alone');
      assert.close(seen.dx, 0, 1.5, 'centre must put the diagram back on the centre line');
      assert.close(seen.dy, 0, 1.5, 'centre must put the diagram back on the middle');
    });

    test('reset returns to 1:1 and centred in Hebrew', () => {
      const seen = place('rtl', DIAGRAM, (viewer) => {
        viewer.scale = 3;
        viewer.offset = { x: 500, y: 500 };
        viewer.apply();
        viewer.reset();
      });
      assert.close(seen.scale, 1, 0.001, 'reset must return to 1:1');
      assert.close(seen.box.width, 240, 1.5, 'reset must show the diagram at its intrinsic width');
      assert.close(seen.dx, 0, 1.5, 'reset must centre the diagram');
    });

    test('a large diagram is scaled down to fit rather than cropped', () => {
      const seen = place('rtl', WIDE);
      assert.ok(seen.scale < 1, 'an oversized diagram must be scaled down');
      assert.ok(seen.box.width <= seen.stage.width, 'the scaled diagram must fit the stage width');
      assert.ok(seen.box.height <= seen.stage.height, 'the scaled diagram must fit the stage height');
    });

    test('the canvas is pinned to LTR so the transform frame matches', () => {
      const seen = place('rtl', DIAGRAM, (viewer) => {
        viewer.scale = 1;
        viewer.offset = { x: 0, y: 0 };
        viewer.apply();
        assert.equal(
          getComputedStyle(viewer.canvas).direction,
          'ltr',
          'the viewer canvas must stay LTR so offset 0 means the stage start edge',
        );
      });
      assert.close(seen.box.left - seen.stage.left, 0, 1, 'offset 0 must place the diagram at the stage start edge');
    });
  });
})();
