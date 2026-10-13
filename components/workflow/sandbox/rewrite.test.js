// @vitest-environment happy-dom
// The compile-time rewrite the sandbox frame applies to render code (./rewrite.ts),
// run by the SAME Babel the frame loads (@babel/standalone 7.23.5, pinned in
// package.json to the version renderCode.tsx BABEL_URL names), and the runtime it
// compiles to (frameShims.ts sandboxGlobals, makeEventSource).
// Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Compile-time rewrites".
import { describe, it, expect, vi } from 'vitest';
import * as Babel from '@babel/standalone';
import { sandboxRewritePlugin, SANDBOX_GLOBAL } from './rewrite';
import { makeEventSource, sandboxGlobals } from './frameShims';
import { compileRenderCode } from '../renderCode';
import { BABEL_URL } from '../renderCode';

function rewrite(code) {
  return Babel.transform(code, {
    presets: ['react'],
    plugins: [sandboxRewritePlugin],
  }).code;
}

/** Compile `body` inside a function taking the sandbox helpers, and run it. */
function run(body, sb, extra = {}) {
  const names = Object.keys(extra);
  const out = Babel.transform(
    `(function(${SANDBOX_GLOBAL}, ${names.join(', ')}) { ${body} })`,
    { presets: ['react'], plugins: [sandboxRewritePlugin] },
  ).code;
  // eslint-disable-next-line no-eval
  return eval(out)(sb, ...names.map((n) => extra[n]));
}

describe('the pinned Babel is the one the frame loads', () => {
  it('same version as BABEL_URL', () => {
    expect(BABEL_URL).toContain(`@babel/standalone@${Babel.version}/`);
  });
});

describe('rewrite: the address', () => {
  it.each([
    ['window.location.href = url;', '__labsSandbox.location.href = url;'],
    ['window.location = url;', '__labsSandbox.location = url;'],
    ['location.href = url;', '__labsSandbox.location.href = url;'],
    ['location = url;', '__labsSandbox.location = url;'],
    ['document.location.href = url;', '__labsSandbox.location.href = url;'],
    ['top.location.href = url;', '__labsSandbox.location.href = url;'],
    ['window.top.location = url;', '__labsSandbox.location = url;'],
    [
      'window.parent.location.assign(url);',
      '__labsSandbox.location.assign(url);',
    ],
    ['location.replace(url);', '__labsSandbox.location.replace(url);'],
    ['window.location.reload();', '__labsSandbox.location.reload();'],
    ['location.reload(true);', '__labsSandbox.location.reload(true);'],
    ["window['location'].href = url;", '__labsSandbox.location.href = url;'],
    [
      'const q = new URLSearchParams(window.location.search);',
      'const q = new URLSearchParams(__labsSandbox.location.search);',
    ],
  ])('%s', (input, expected) => {
    expect(rewrite(input)).toContain(expected);
  });

  it('leaves a local named location (or window, or parent) alone', () => {
    const out = rewrite(`
      function a(location) { location.href = 'x'; }
      function b() { const location = { href: '' }; location.href = 'y'; }
      function c(parent) { parent.location = 'z'; }
      function d(window) { window.location.href = 'w'; }
      const { location: loc } = props;
    `);
    expect(out).not.toContain(SANDBOX_GLOBAL);
  });

  it('leaves properties and keys named location alone', () => {
    const out = rewrite(`
      const o = { location: 1 };
      row.location = 'Kano';
      const v = row.location;
      const el = <Map location="here" />;
    `);
    expect(out).not.toContain(SANDBOX_GLOBAL);
  });

  it('writes out a shorthand property it rewrites', () => {
    expect(rewrite('const o = { location };')).toContain(
      'location: __labsSandbox.location',
    );
  });

  it('does not touch strings', () => {
    expect(rewrite('const s = "window.location.href = x";')).not.toContain(
      SANDBOX_GLOBAL,
    );
  });
});

describe('rewrite: confirm()', () => {
  it('awaits the host in an async function', () => {
    const out = rewrite(`
      const del = async (s) => {
        if (!window.confirm('Delete?')) return;
        go();
      };
    `);
    expect(out).toContain(
      "if (!(await __labsSandbox.confirm('Delete?'))) return;",
    );
  });

  it('a bare confirm, in an async function declaration', () => {
    expect(rewrite('async function f() { if (confirm("x")) g(); }')).toContain(
      'if (await __labsSandbox.confirm("x")) g();',
    );
  });

  it('makes a JSX event handler async to await it', () => {
    const out = rewrite(
      '<button onClick={() => { if (confirm("Sure?")) del(); }}>x</button>',
    );
    expect(out).toContain('onClick: async () =>');
    expect(out).toContain('await __labsSandbox.confirm("Sure?")');
  });

  it('...and an on* property of a props object (hyperscript render code)', () => {
    const out = rewrite(`
      h("button", { onClick: function () {
        var ok = true;
        try { ok = window.confirm("Discard?"); } catch (e) { ok = true; }
        if (ok) discard();
      }}, "Discard");
    `);
    expect(out).toContain('onClick: async function');
    expect(out).toContain('ok = await __labsSandbox.confirm("Discard?")');
  });

  it('...and an addEventListener listener', () => {
    const out = rewrite(
      'el.addEventListener("click", function () { if (confirm("a")) b(); });',
    );
    expect(out).toContain('async function');
    expect(out).toContain('await __labsSandbox.confirm("a")');
  });

  it('leaves a handler that uses its event after the question (it would be too late)', () => {
    const out = rewrite(`
      <a href="/x/" onClick={(e) => { if (!confirm('Leave?')) e.preventDefault(); }}>x</a>;
      <a href="/y/" onClick={({ target }) => { if (confirm('Go?')) go(target); }}>y</a>;
    `);
    expect(out).not.toContain(SANDBOX_GLOBAL);
    // ...but an event used only BEFORE the question is fine.
    const ok = rewrite(
      '<b onClick={(e) => { e.stopPropagation(); if (confirm("Sure?")) del(); }} />',
    );
    expect(ok).toContain('await __labsSandbox.confirm("Sure?")');
  });

  it('leaves a confirm whose function returns synchronously (the frame answers No)', () => {
    const out = rewrite(`
      function check() { return confirm('ok?'); }
      const ask = (t) => Promise.resolve(window.confirm(t));
      items.forEach(function (i) { if (confirm(i)) drop(i); });
    `);
    expect(out).not.toContain(SANDBOX_GLOBAL);
    expect(out).not.toContain('async');
  });

  it('leaves a local confirm, and other objects’ confirm, alone', () => {
    const out = rewrite(`
      async function f(confirm) { if (confirm('x')) g(); }
      async function h() { if (dialog.confirm('x')) g(); if (view.confirm) {} }
    `);
    expect(out).not.toContain(SANDBOX_GLOBAL);
  });

  it('the compiled code waits for the answer, and follows it', async () => {
    const asked = [];
    let answer = false;
    const sb = {
      confirm: (t) => {
        asked.push(t);
        return Promise.resolve(answer);
      },
    };
    const done = [];
    const del = run(
      `return async function del(id) {
         if (!window.confirm('Delete ' + id + '?')) return 'kept';
         done.push(id);
         return 'deleted';
       };`,
      sb,
      { done },
    );
    expect(await del(1)).toBe('kept');
    answer = true;
    expect(await del(2)).toBe('deleted');
    expect(asked).toEqual(['Delete 1?', 'Delete 2?']);
    expect(done).toEqual([2]);
  });
});

describe('compileRenderCode with the sandbox options', () => {
  it('passes the helpers as a parameter, never a global', () => {
    window.Babel = Babel;
    const navigated = [];
    const sb = {
      location: {
        set href(v) {
          navigated.push(v);
        },
      },
    };
    const Component = compileRenderCode(
      `function WorkflowUI() { window.location.href = '/labs/workflow/'; return null; }`,
      { plugins: [sandboxRewritePlugin], params: { [SANDBOX_GLOBAL]: sb } },
    );
    expect(Component).toBeTruthy();
    Component({});
    expect(navigated).toEqual(['/labs/workflow/']);
    expect(window[SANDBOX_GLOBAL]).toBeUndefined();
  });

  it('without them, compiles exactly as before (the run page)', () => {
    window.Babel = Babel;
    const spy = vi.spyOn(Babel, 'transform');
    compileRenderCode('function WorkflowUI() { return null; }');
    expect(spy.mock.calls[0][1]).toEqual({ presets: ['react'] });
    spy.mockRestore();
  });
});

function fakeBridge() {
  const calls = [];
  const listeners = new Map();
  return {
    calls,
    listeners,
    request: vi.fn(async (method, params) => {
      calls.push([method, params]);
      if (method === 'streamOpen') return { stream: 's1' };
      if (method === 'confirm') return true;
      return true;
    }),
    listen(stream, fn) {
      listeners.set(stream, fn);
      return () => listeners.delete(stream);
    },
  };
}

describe('sandboxGlobals: the address render code is compiled to reach', () => {
  const PAGE =
    'https://labs.example/labs/workflow/12/run/?run_id=5&opportunity_id=3';

  it('reads the HOST page’s address, not the shell’s', () => {
    const b = fakeBridge();
    const { location } = sandboxGlobals(b, () => PAGE, {
      search: '?run_id=5&opportunity_id=3',
      hash: '',
    });
    expect(location.pathname).toBe('/labs/workflow/12/run/');
    expect(location.href).toBe(PAGE);
    expect(String(location)).toBe(PAGE);
    expect(new URLSearchParams(location.search).get('run_id')).toBe('5');
    expect(location.origin).toBe('https://labs.example');
  });

  it('every change of address is a navigate the host checks', async () => {
    const b = fakeBridge();
    const g = sandboxGlobals(b, () => PAGE, { search: '?run_id=5', hash: '' });
    g.location.href = '/audit/7/bulk/?x=1';
    g.location = '/labs/workflow/';
    g.location.assign('/tasks/3/edit/');
    g.location.replace('https://labs.example/labs/p/org/acme/');
    g.location.reload();
    g.location.search = '?run_id=6';
    await Promise.resolve();
    const navs = b.calls.filter(([m]) => m === 'navigate').map(([, p]) => p);
    expect(navs).toEqual([
      { url: 'https://labs.example/audit/7/bulk/?x=1', newTab: false },
      { url: 'https://labs.example/labs/workflow/', newTab: false },
      { url: 'https://labs.example/tasks/3/edit/', newTab: false },
      { url: 'https://labs.example/labs/p/org/acme/', newTab: false },
      {
        url: 'https://labs.example/labs/workflow/12/run/?run_id=5',
        newTab: false,
      },
      {
        url: 'https://labs.example/labs/workflow/12/run/?run_id=6',
        newTab: false,
      },
    ]);
  });

  it('a reload drops the hash (with one, the host would only jump)', async () => {
    const b = fakeBridge();
    const g = sandboxGlobals(b, () => PAGE, {
      search: '?run_id=5',
      hash: '#tab2',
    });
    g.location.reload();
    await Promise.resolve();
    expect(b.calls).toEqual([
      [
        'navigate',
        {
          url: 'https://labs.example/labs/workflow/12/run/?run_id=5',
          newTab: false,
        },
      ],
    ]);
  });

  it('a hash only moves the host’s hash', () => {
    const b = fakeBridge();
    const address = { search: '?a=1', hash: '' };
    const g = sandboxGlobals(b, () => PAGE, address);
    g.location.hash = 'tab2';
    expect(address.hash).toBe('#tab2');
    expect(g.location.hash).toBe('#tab2');
    expect(b.calls).toEqual([
      ['setLocation', { search: '?a=1', hash: '#tab2', push: true }],
    ]);
  });

  it('confirm is the host’s question; a refusal is a No', async () => {
    const b = fakeBridge();
    const g = sandboxGlobals(b, () => PAGE, { search: '', hash: '' });
    expect(await g.confirm('Sure?')).toBe(true);
    b.request.mockRejectedValueOnce(new Error('one at a time'));
    expect(await g.confirm('Again?')).toBe(false);
  });
});

describe('makeEventSource: a stream the host reads', () => {
  const base = () => 'https://labs.example/labs/sandbox/render/';
  const flush = () => new Promise((r) => setTimeout(r, 0));

  it('opens the labs path through the host and hands events to render code', async () => {
    const b = fakeBridge();
    const ES = makeEventSource(b, base);
    const es = new ES('/labs/workflow/api/42/pipeline-rows/stream/?alias=v');
    const got = [];
    es.onopen = () => got.push('open');
    es.onmessage = (e) => got.push(['message', e.data]);
    es.addEventListener('progress', (e) => got.push(['progress', e.data]));
    await flush();
    expect(b.calls[0]).toEqual([
      'streamOpen',
      { url: '/labs/workflow/api/42/pipeline-rows/stream/?alias=v' },
    ]);
    const push = b.listeners.get('s1');
    push({ stream: 's1', kind: 'open' });
    expect(es.readyState).toBe(ES.OPEN);
    push({ stream: 's1', kind: 'event', name: 'message', data: '{"a":1}' });
    push({ stream: 's1', kind: 'event', name: 'progress', data: '50%' });
    expect(got).toEqual(['open', ['message', '{"a":1}'], ['progress', '50%']]);
  });

  it('the end of the stream closes it and fires error; it never reconnects', async () => {
    const b = fakeBridge();
    const ES = makeEventSource(b, base);
    const es = new ES('/labs/workflow/api/job/abc/status/');
    const errors = vi.fn();
    es.onerror = errors;
    await flush();
    b.listeners.get('s1')({ stream: 's1', kind: 'end' });
    expect(es.readyState).toBe(ES.CLOSED);
    expect(errors).toHaveBeenCalledTimes(1);
    expect(b.calls.filter(([m]) => m === 'streamOpen')).toHaveLength(1);
  });

  it('close() tells the host and nothing more is delivered', async () => {
    const b = fakeBridge();
    const ES = makeEventSource(b, base);
    const es = new ES('/labs/workflow/api/job/abc/status/');
    const got = vi.fn();
    es.onmessage = got;
    await flush();
    const push = b.listeners.get('s1');
    es.close();
    push({ stream: 's1', kind: 'event', name: 'message', data: 'late' });
    expect(got).not.toHaveBeenCalled();
    expect(b.calls).toContainEqual(['streamClose', { stream: 's1' }]);
  });

  it('a refused stream fails as a browser’s would, so a fallback runs', async () => {
    const b = fakeBridge();
    b.request.mockRejectedValueOnce(new Error('fetch: /x/ cannot be reached'));
    const ES = makeEventSource(b, base);
    const es = new ES('/custom_analysis/x/stream/');
    const errors = vi.fn();
    es.onerror = errors;
    await flush();
    expect(errors).toHaveBeenCalledTimes(1);
    expect(es.readyState).toBe(2);
  });

  it('another host is never asked for', async () => {
    const b = fakeBridge();
    const ES = makeEventSource(b, base);
    const es = new ES('https://evil.example/stream');
    const errors = vi.fn();
    es.onerror = errors;
    await flush();
    expect(b.calls).toEqual([]);
    expect(errors).toHaveBeenCalledTimes(1);
  });
});
