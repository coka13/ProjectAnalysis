/* DOM helper tests. These run in the real webview against the real document. */
(function () {
  'use strict';

  const { suite, test, assert, withStage } = window.AAITest;
  const { el, append, card } = window.AAI.dom;

  suite('dom.append', () => {
    /**
     * Regression: views build optional blocks as `condition ? node : null` and
     * hand the result straight to a parent node. `el()` drops those nulls, but
     * the native append() stringifies them, so every hidden block printed the
     * word "null" on the page - most visibly right under the category dial in
     * the score explorer.
     */
    test('an absent child leaves no text behind', () => {
      withStage((stage) => {
        const host = el('div');
        append(host, el('b', {}, 'kept'), null, undefined, false);
        stage.append(host);
        assert.equal(host.textContent, 'kept', 'nothing but the real child should render');
        assert.equal(host.childNodes.length, 1, 'no placeholder nodes should be created');
      });
    });

    test('the same call still renders every real child in order', () => {
      const host = el('div');
      append(host, el('span', {}, 'a'), null, el('span', {}, 'b'), 'c');
      assert.equal(host.textContent, 'abc', 'order and content must survive the filter');
    });

    test('el() and append() agree about what an empty child is', () => {
      const built = el('div', {}, el('i', {}, 'x'), null, false);
      const appended = append(el('div'), el('i', {}, 'x'), null, false);
      assert.equal(built.innerHTML, appended.innerHTML, 'the two paths must not diverge');
    });

    test('a card with no optional body renders no stray text', () => {
      const node = card('Title', { icon: 'info' }, el('p', {}, 'body'), null);
      assert.equal(/null|undefined/.test(node.textContent), false, node.textContent);
    });
  });
})();
