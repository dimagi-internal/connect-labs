// @vitest-environment happy-dom
// @vitest-environment-options {"settings": {"disableIframePageLoading": true}}
// The page sandbox's bridge (host.ts) and frame runtime (frame.tsx).
// Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md.
import { describe, it, expect, vi } from 'vitest';
import {
  checkNavigate,
  createDispatcher,
  createSandboxHost,
  isFrameHello,
  sanitizeSelection,
} from './host';
import {
  MemoryStorage,
  connectBridge,
  installMemoryStorage,
  interceptAnchorClicks,
  pageActions,
} from './frame';
import { HELLO, INIT } from './protocol';

const LABS = 'https://labs.connect.dimagi.com';

function handlers(overrides = {}) {
  return {
    querySupply: vi.fn(async () => ({ rows: [{ n: 1 }] })),
    queryWorkflow: vi.fn(async () => ({ latest_run: null })),
    navigate: vi.fn(),
    shareSelection: vi.fn(),
    resize: vi.fn(),
    copyText: vi.fn(async () => undefined),
    confirm: vi.fn(async () => true),
    download: vi.fn(),
    reportError: vi.fn(),
    ...overrides,
  };
}

function dispatcher(h = handlers(), activation = true) {
  return createDispatcher(h, {
    labsOrigin: LABS,
    hasActivation: () => activation,
  });
}

describe('the host refuses what a page may not ask for', () => {
  it('an unknown method gets an error and reaches no handler', async () => {
    const h = handlers();
    const out = await dispatcher(h)({ id: 1, method: 'fetch', params: {} });
    expect(out).toEqual({ id: 1, error: 'unknown method "fetch"' });
    expect(Object.values(h).every((fn) => fn.mock.calls.length === 0)).toBe(
      true,
    );
  });

  it('a workflow-only method says it is not on a page yet', async () => {
    const out = await dispatcher()({ id: 2, method: 'runAction', params: {} });
    expect(out.error).toBe('runAction is not available on a page yet');
  });

  it('a message that is not a request is dropped', async () => {
    expect(await dispatcher()('labs-sandbox:hello')).toBeNull();
    expect(await dispatcher()({ method: 'navigate' })).toBeNull();
  });

  it('params that are not a plain object are refused', async () => {
    const out = await dispatcher()({ id: 3, method: 'resize', params: [400] });
    expect(out.error).toMatch(/plain object/);
  });

  it('a page asking too often is slowed', async () => {
    const d = dispatcher();
    const answers = [];
    for (let i = 0; i < 61; i += 1) {
      answers.push(
        await d({
          id: i,
          method: 'shareSelection',
          params: { visible_ids: [] },
        }),
      );
    }
    expect(answers[59].result).toBe(true);
    expect(answers[60].error).toMatch(/too many requests/);
  });
});

describe('navigate', () => {
  const refused = [
    '//evil.com',
    '/\\evil.com',
    '\t//evil',
    'javascript:alert(1)',
    'https://labs.connect.dimagi.com.evil.com/labs/p/',
    'https://labs.connect.dimagi.com@evil.com/labs/p/',
    'http://labs.connect.dimagi.com/labs/p/org/acme/',
    'https://user:pw@labs.connect.dimagi.com/labs/p/',
    '/o/authorize/?client_id=x',
    '/labs/p/../../o/authorize/',
    '/labs/mcp/tokens/create/',
    '/accounts/logout/',
    '/admin/',
    '/labs/workflow/12/edit/',
    'data:text/html,hi',
  ];
  for (const url of refused) {
    it(`refuses ${JSON.stringify(url)}`, () => {
      expect(() => checkNavigate(url, LABS)).toThrow();
    });
  }

  it('opens labs pages on the allowlist, by the parsed address', () => {
    expect(checkNavigate('/labs/p/org/acme/', LABS)).toBe(
      `${LABS}/labs/p/org/acme/`,
    );
    expect(checkNavigate(`${LABS}/supply/?program_id=25`, LABS)).toBe(
      `${LABS}/supply/?program_id=25`,
    );
    expect(
      checkNavigate('/labs/workflow/99/run/?program_id=25&run_id=3', LABS),
    ).toBe(`${LABS}/labs/workflow/99/run/?program_id=25&run_id=3`);
    expect(checkNavigate('/labs/workflow/?program_id=25', LABS)).toBe(
      `${LABS}/labs/workflow/?program_id=25`,
    );
  });

  it('needs the person’s click on the host', async () => {
    const h = handlers();
    const out = await dispatcher(
      h,
      false,
    )({
      id: 1,
      method: 'navigate',
      params: { url: '/labs/p/org/acme/' },
    });
    expect(out.error).toMatch(/needs a click/);
    expect(h.navigate).not.toHaveBeenCalled();
  });

  it('a clicked labs link navigates, in a new tab when asked', async () => {
    const h = handlers();
    await dispatcher(h)({
      id: 1,
      method: 'navigate',
      params: { url: '/labs/p/programme/25/', newTab: true },
    });
    expect(h.navigate).toHaveBeenCalledWith(
      `${LABS}/labs/p/programme/25/`,
      true,
    );
  });

  it('a refused link reaches no handler', async () => {
    const h = handlers();
    const out = await dispatcher(h)({
      id: 1,
      method: 'navigate',
      params: { url: '//evil.com' },
    });
    expect(out.error).toMatch(/only labs pages/);
    expect(h.navigate).not.toHaveBeenCalled();
  });
});

describe('what the host passes on', () => {
  it('queries are this page’s: no ids from the frame reach the query', async () => {
    const h = handlers();
    const out = await dispatcher(h)({
      id: 1,
      method: 'querySupply',
      params: {
        alias: 'stock',
        args: { supply_point_id: 7 },
        definition_id: 1,
        opportunity_id: 999,
        program_id: 998,
      },
    });
    expect(h.querySupply).toHaveBeenCalledWith('stock', { supply_point_id: 7 });
    expect(out.result).toEqual({ rows: [{ n: 1 }] });
  });

  it('the selection is rebuilt: the frame cannot set filters or the resource', async () => {
    const sel = sanitizeSelection({
      visible_ids: ['10::amina', 7, { evil: 1 }],
      drilled: { opportunity: 10, nested: { x: 1 }, name: 'Kano' },
      filters: { program_id: 1 },
      resource: 'labs-workflow://1',
      backing_tool: 'workflow_delete',
    });
    expect(sel).toEqual({
      visible_ids: ['10::amina', '7'],
      drilled: { opportunity: 10, name: 'Kano' },
    });
  });

  it('a download is limited to known file types, saved as labs’ type', async () => {
    const h = handlers();
    const bad = await dispatcher(h)({
      id: 1,
      method: 'download',
      params: { filename: 'page.html', mime: 'text/html', bytes: '<script>' },
    });
    expect(bad.error).toMatch(/filename ending/);
    await dispatcher(h)({
      id: 2,
      method: 'download',
      params: { filename: 'rows.csv', mime: 'text/html', bytes: 'a,b' },
    });
    expect(h.download.mock.calls[0][0].mime).toBe('text/csv');
  });

  it('resize is clamped', async () => {
    const h = handlers();
    await dispatcher(h)({ id: 1, method: 'resize', params: { height: 1e9 } });
    expect(h.resize).toHaveBeenCalledWith(50000);
  });
});

describe('the handshake', () => {
  const frameWindow = {};
  // happy-dom logs that it does not load iframe pages (disableIframePageLoading); harmless.
  it('accepts a hello only from the iframe, from an opaque origin', () => {
    const hello = { type: HELLO };
    expect(
      isFrameHello(
        { source: frameWindow, origin: 'null', data: hello },
        frameWindow,
      ),
    ).toBe(true);
    expect(
      isFrameHello({ source: {}, origin: 'null', data: hello }, frameWindow),
    ).toBe(false);
    expect(
      isFrameHello(
        { source: frameWindow, origin: LABS, data: hello },
        frameWindow,
      ),
    ).toBe(false);
    expect(
      isFrameHello(
        { source: frameWindow, origin: 'null', data: 'x' },
        frameWindow,
      ),
    ).toBe(false);
  });

  it('draws the frame sandboxed, with src set after its listener, and stops if it navigates', () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const onLeave = vi.fn();
    const host = createSandboxHost({
      container,
      labsOrigin: LABS,
      payload: {},
      handlers: handlers(),
      onLeave,
      hasActivation: () => true,
    });
    const iframe = container.querySelector('iframe');
    expect(iframe).toBe(host.iframe);
    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
    expect(iframe.getAttribute('referrerpolicy')).toBe('strict-origin');
    expect(iframe.getAttribute('src')).toBe('/labs/sandbox/render/');
    iframe.dispatchEvent(new Event('load'));
    expect(onLeave).not.toHaveBeenCalled();
    iframe.dispatchEvent(new Event('load'));
    expect(onLeave).toHaveBeenCalledTimes(1);
    expect(container.querySelector('iframe')).toBeNull();
    host.destroy();
  });

  it('ignores a hello from any window but the iframe’s', () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const host = createSandboxHost({
      container,
      labsOrigin: LABS,
      payload: {},
      handlers: handlers(),
      onLeave: () => {},
    });
    const posted = vi.fn();
    Object.defineProperty(host.iframe, 'contentWindow', {
      value: { postMessage: posted },
      configurable: true,
    });
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: HELLO },
        origin: 'null',
        source: window,
      }),
    );
    expect(posted).not.toHaveBeenCalled();
    host.destroy();
  });
});

describe('the frame runtime', () => {
  function click(el, init = {}) {
    const event = new MouseEvent('click', {
      bubbles: true,
      cancelable: true,
      ...init,
    });
    el.dispatchEvent(event);
    return event;
  }

  it('turns a click on a link into navigate, never a navigation of its own', () => {
    document.body.innerHTML =
      '<a id="a" href="/labs/p/org/acme/"><span id="inner">Acme</span></a>' +
      '<a id="b" href="/supply/" target="_blank">Supply</a>' +
      '<a id="c" href="#top">Top</a><a id="d" href="javascript:void(0)">js</a>';
    const navigate = vi.fn();
    const stop = interceptAnchorClicks(document, navigate);
    const e1 = click(document.getElementById('inner'));
    expect(e1.defaultPrevented).toBe(true);
    expect(navigate).toHaveBeenLastCalledWith(
      new URL('/labs/p/org/acme/', document.baseURI).href,
      false,
    );
    click(document.getElementById('b'));
    expect(navigate).toHaveBeenLastCalledWith(
      new URL('/supply/', document.baseURI).href,
      true,
    );
    click(document.getElementById('a'), { metaKey: true });
    expect(navigate.mock.calls.at(-1)[1]).toBe(true);
    const fragment = click(document.getElementById('c'));
    const js = click(document.getElementById('d'));
    expect(fragment.defaultPrevented).toBe(false);
    expect(js.defaultPrevented).toBe(true);
    expect(navigate).toHaveBeenCalledTimes(3);
    stop();
  });

  it('gives an opaque origin in-memory storage instead of a throw', () => {
    const win = {};
    Object.defineProperty(win, 'localStorage', {
      get() {
        throw new DOMException('denied', 'SecurityError');
      },
      configurable: true,
    });
    Object.defineProperty(win, 'sessionStorage', {
      get() {
        throw new DOMException('denied', 'SecurityError');
      },
      configurable: true,
    });
    installMemoryStorage(win);
    win.localStorage.setItem('k', 1);
    expect(win.localStorage.getItem('k')).toBe('1');
    expect(win.sessionStorage).toBeInstanceOf(MemoryStorage);
  });

  it('a workflow-only action rejects with a clear message, not a TypeError', async () => {
    const actions = pageActions({ request: vi.fn() });
    await expect(actions.createTask({})).rejects.toThrow(
      'createTask is not available on a page yet',
    );
  });

  it('takes INIT only from its parent, then answers over the port', async () => {
    const listeners = [];
    const parent = { postMessage: vi.fn() };
    const win = {
      parent,
      addEventListener: (t, fn) => listeners.push(fn),
      removeEventListener: () => {},
    };
    const onInit = vi.fn();
    const stop = connectBridge(win, { onInit, onUpdate: vi.fn() }, 10_000);
    expect(parent.postMessage).toHaveBeenCalledWith({ type: HELLO }, '*');
    const channel = new MessageChannel();
    listeners.forEach((fn) =>
      fn({
        source: {},
        data: { type: INIT, payload: {} },
        ports: [channel.port2],
      }),
    );
    expect(onInit).not.toHaveBeenCalled();
    listeners.forEach((fn) =>
      fn({
        source: parent,
        data: { type: INIT, payload: { a: 1 } },
        ports: [channel.port2],
      }),
    );
    expect(onInit).toHaveBeenCalledTimes(1);
    const bridge = onInit.mock.calls[0][1];
    channel.port1.onmessage = (e) =>
      channel.port1.postMessage({ id: e.data.id, result: e.data.method });
    await expect(bridge.request('resize', { height: 1 })).resolves.toBe(
      'resize',
    );
    stop();
    channel.port1.close();
  });
});
