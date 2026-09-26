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

  it("never reaches for a caller's object", () => {
    // The original bug: a helper read `store`, which was threaded into
    // openPartner/openWorker rather than held at module scope, so it threw at
    // render time into the catch that reports "Could not load this partner".
    //
    // `store` is gone — the module now declares its dependency as `host` — and
    // this keeps it gone, because reintroducing the pattern reintroduces the
    // silent failure.
    const leaks = moduleFunctions(SRC)
      .filter((f) => /\bstore\b/.test(f.body))
      .map((f) => f.name);
    expect(leaks).toEqual([]);
  });

  it('has working defaults, so a page that forgets to configure degrades', () => {
    // The window is opened by more than one page now. A host that has to be
    // configured before the module is safe to load would fail as an exception
    // inside the same swallowing catch.
    const flat = SRC.replace(/\s+/g, '');
    expect(flat).toContain('labels:()=>({})');
    expect(flat).toMatch(/urlFor\(path,params\)\{/);
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
    // Gesture drag (gestures.js) moves a window through this, not the stack.
    expect(typeof win.PulseWindows.moveBy).toBe('function');
    win.PulseWindows.moveBy(10, 10); // nothing open: a no-op, not a throw
    // Gesture dial: nothing open means nothing to step.
    expect(win.PulseWindows.stepOpportunity(1)).toBe(false);
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

describe('windows.js shareable state', () => {
  it('exposes what is open, so the address bar can describe it', () => {
    expect(SRC).toMatch(/state:\s*\(\)\s*=>/);
    expect(SRC).toMatch(/onChange\(fn\)/);
  });

  it('reports nothing open before anything is opened', () => {
    const cards = fs.readFileSync(path.join(here, 'cards.js'), 'utf8');
    const doc = {
      addEventListener() {},
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
    // Null rather than an empty object: the URL writer omits absent fields, and
    // an object of nulls would still read as "something is open".
    expect(win.PulseWindows.state()).toBeNull();
  });

  it('accepts a preselected opportunity, so a link can land on one engagement', () => {
    const flat = SRC.replace(/\s+/g, '');
    expect(flat).toContain('functionopenPartner(slug,preselectOpp)');
    expect(flat).toContain('selectedOpp=preselectOpp||null');
  });

  it('clears the whole open-state when the base window closes', () => {
    // Closing the partner must not leave a worker or opportunity in the URL.
    // Whitespace-normalised: these assert intent, and a reformat is not a
    // regression — an earlier version of this test failed only because
    // prettier moved the assignment onto its own line.
    const flat = SRC.replace(/\s+/g, ' ');
    expect(flat).toMatch(
      /if \(depth === 0\) openState = \{ partner: null, opportunity: null, worker: null \};/,
    );
  });
});

describe('windows.js opportunity dial', () => {
  /* A partner window run for real against a do-nothing DOM and a stub API,
     so the steps are asserted by what the window actually asks for. */
  async function partnerWindow(oppIds) {
    const cards = fs.readFileSync(path.join(here, 'cards.js'), 'utf8');
    const el = () => ({
      style: {},
      dataset: {},
      classList: { add() {}, remove() {} },
      setAttribute() {},
      toggleAttribute() {},
      addEventListener() {},
      appendChild() {},
      remove() {},
      focus() {},
      querySelector: () => el(),
      querySelectorAll: () => [],
    });
    const doc = {
      addEventListener() {},
      createElement: el,
      querySelector: () => null,
      querySelectorAll: () => [],
      body: el(),
    };
    const asked = [];
    const win = {
      document: doc,
      fetch: async (url) => {
        asked.push(new URL(url, 'http://x').searchParams.get('opportunity'));
        return {
          ok: true,
          json: async () => ({
            partner: { slug: 'p', name: 'P', named: true },
            opportunities: oppIds.map((id) => ({ id, name: 'o' + id })),
            workers: [],
          }),
        };
      },
      setInterval: () => 0,
      clearInterval() {},
    };
    new Function('window', 'document', cards)(win, doc);
    new Function(
      'window',
      'document',
      'fetch',
      'setInterval',
      'clearInterval',
      SRC,
    )(win, doc, win.fetch, win.setInterval, win.clearInterval);
    const tick = () => new Promise((r) => setTimeout(r, 0));
    win.PulseWindows.openPartner('p');
    await tick();
    const step = async (d) => {
      const moved = win.PulseWindows.stepOpportunity(d);
      await tick();
      return moved;
    };
    return { step, asked };
  }

  it('steps all -> each opportunity -> all, wrapping both ways', async () => {
    const { step, asked } = await partnerWindow([11, 22, 33]);
    for (const d of [1, 1, 1, 1, -1, -1]) expect(await step(d)).toBe(true);
    // The first request is the window opening on all of them.
    expect(asked).toEqual([null, '11', '22', '33', null, '33', '22']);
  });

  it('has nothing to step through for a one-opportunity partner', async () => {
    const { step, asked } = await partnerWindow([11]);
    expect(await step(1)).toBe(false);
    expect(asked).toEqual([null]);
  });

  it('lets only the latest partner request paint', () => {
    // A dial steps faster than the API answers; without this a late reply
    // for the previous opportunity repaints over the selected one.
    const flat = SRC.replace(/\s+/g, ' ');
    expect(flat).toContain('const seq = ++loadSeq;');
    expect(flat).toMatch(/if \(seq !== loadSeq\) return; win\.last = d;/);
  });
});
