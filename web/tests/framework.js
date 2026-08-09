/**
 * Minimal test framework for the browser-side code.
 *
 * The UI is deliberately built from plain <script> tags with no bundler, and this
 * machine has neither node nor network access, so tests run inside the real
 * webview instead of a DOM shim. That is a feature rather than a compromise: the
 * defects worth catching here (SVG path length, getBBox, resolved styles) only
 * reproduce in a real rendering engine.
 */
(function () {
  'use strict';

  const suites = [];
  let current = null;

  function suite(name, body) {
    current = { name, tests: [] };
    suites.push(current);
    body();
    current = null;
  }

  function test(name, body) {
    if (!current) throw new Error(`test("${name}") declared outside a suite`);
    current.tests.push({ name, body });
  }

  function fail(message, detail) {
    const error = new Error(detail ? `${message}: ${detail}` : message);
    error.assertion = true;
    throw error;
  }

  const assert = {
    ok(value, message) {
      if (!value) fail(message || 'expected a truthy value', String(value));
    },
    equal(actual, expected, message) {
      if (actual !== expected) {
        fail(message || 'values differ', `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
      }
    },
    close(actual, expected, tolerance, message) {
      if (typeof actual !== 'number' || !isFinite(actual)) {
        fail(message || 'expected a finite number', String(actual));
      }
      if (Math.abs(actual - expected) > tolerance) {
        fail(message || 'value out of tolerance', `expected ${expected} ±${tolerance}, got ${actual}`);
      }
    },
    match(text, pattern, message) {
      if (!pattern.test(String(text))) {
        fail(message || 'text did not match', `${pattern} against ${JSON.stringify(String(text))}`);
      }
    },
    throws(body, message) {
      try {
        body();
      } catch (err) {
        return;
      }
      fail(message || 'expected the call to throw');
    },
  };

  /**
   * Run a body that needs a laid-out element: SVG geometry is 0 until it is in
   * the document. An async body keeps its stage until it settles, otherwise the
   * element would be detached the moment the body first awaited and every
   * measurement after that would silently read back as zero.
   */
  function withStage(body) {
    const stage = document.createElement('div');
    stage.style.cssText = 'position:fixed;inset:0 auto auto 0;width:900px;height:400px;visibility:hidden';
    document.body.append(stage);
    let result;
    try {
      result = body(stage);
    } catch (error) {
      stage.remove();
      throw error;
    }
    if (result && typeof result.then === 'function') {
      return result.then(
        (value) => { stage.remove(); return value; },
        (error) => { stage.remove(); throw error; },
      );
    }
    stage.remove();
    return result;
  }

  async function run() {
    const results = [];
    for (const s of suites) {
      for (const t of s.tests) {
        const entry = { suite: s.name, name: t.name, passed: true, error: null };
        try {
          await t.body();
        } catch (error) {
          entry.passed = false;
          entry.error = String((error && error.stack) || error);
        }
        results.push(entry);
      }
    }
    return results;
  }

  window.AAITest = { suite, test, assert, withStage, run };
})();
