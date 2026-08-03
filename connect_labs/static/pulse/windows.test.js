/**
 * Drill-down window module.
 *
 * These guard a failure mode this module is unusually exposed to: every render
 * path runs inside a try/catch that replaces the window body with a polite
 * "Could not load this partner" note. A ReferenceError therefore ships looking
 * like a data problem, passes CI, passes lint, and survives a screenshot — the
 * window opens, the title is right, and only the body is wrong.
 *
 * That is exactly what happened: a module-level `opportunities()` helper read
 * `store`, which is not a module-level binding but a parameter threaded into
 * `openPartner`/`openWorker`. Every partner window rendered "Could not load
 * this partner (store is not defined)" where its opportunities should have
 * been.
 *
 * There is no DOM environment configured for vitest here, so these are source
 * and load-time checks rather than render tests. They catch the class of bug
 * that actually occurred without pulling in jsdom.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.join(here, 'windows.js'), 'utf8');

/** Module-level `function name(args) { ... }` declarations and their bodies. */
function moduleFunctions(src) {
  const out = [];
  const re = /^ {2}function (\w+)\(([^)]*)\)\s*\{/gm;
  let m;
  while ((m = re.exec(src))) {
    let depth = 1;
    let i = re.lastIndex;
    while (i < src.length && depth > 0) {
      if (src[i] === '{') depth += 1;
      else if (src[i] === '}') depth -= 1;
      i += 1;
    }
    out.push({ name: m[1], args: m[2], body: src.slice(re.lastIndex, i) });
  }
  return out;
}

describe('windows.js dependency scope', () => {
  it('finds the module-level helpers', () => {
    const names = moduleFunctions(SRC).map((f) => f.name);
    expect(names).toContain('opportunities');
    expect(names).toContain('openPartner');
  });

  it('never reaches for `store` without being handed it', () => {
    // `store` is threaded in from display.js, not held at module scope. A
    // helper that reaches for it throws at render time and is swallowed by the
    // window's own error handling.
    const leaks = moduleFunctions(SRC)
      .filter((f) => /\bstore\b/.test(f.body) && !/\bstore\b/.test(f.args))
      .map((f) => f.name);
    expect(leaks).toEqual([]);
  });
});

describe('windows.js module load', () => {
  function load() {
    const cards = fs.readFileSync(path.join(here, 'cards.js'), 'utf8');
    const listeners = [];
    // Both modules reference `document` as a bare global, not through the
    // `window` they are handed, so it has to be injected as its own binding.
    const doc = {
      addEventListener: (...a) => listeners.push(a),
      createElement: () => ({
        style: {},
        dataset: {},
        classList: { add() {} },
      }),
      querySelector: () => null,
      querySelectorAll: () => [],
    };
    const win = { document: doc };
    new Function('window', 'document', cards)(win, doc);
    new Function('window', 'document', SRC)(win, doc);
    return { win, listeners };
  }

  it('exports the drill-down surface display.js calls', () => {
    const { win } = load();
    expect(typeof win.PulseWindows.openPartner).toBe('function');
    expect(typeof win.PulseWindows.openWorker).toBe('function');
    expect(typeof win.PulseWindows.close).toBe('function');
    expect(typeof win.PulseWindows.isOpen).toBe('function');
  });

  it('opens closed, so the map tour is not suppressed before anything is shown', () => {
    const { win } = load();
    expect(win.PulseWindows.isOpen()).toBe(false);
  });

  it('registers the Escape handler that closes the top layer', () => {
    const { listeners } = load();
    expect(listeners.map((l) => l[0])).toContain('keydown');
  });

  it('reuses the card helpers rather than formatting money its own way', () => {
    // Windows and cards must never disagree about how a figure is written.
    expect(SRC).toMatch(/PulseCards\.helpers/);
  });
});
