// @vitest-environment happy-dom
// @vitest-environment-options {"settings": {"disableIframePageLoading": true}}
// Sizing: the frame grows to its content and its window is the host's
// (./viewport.ts, host.ts createSandboxHost). Spec:
// docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Sizing".
import { describe, it, expect, vi, afterEach } from 'vitest';
import { createRequire } from 'module';
import {
  canLift,
  createDispatcher,
  createSandboxHost,
  frameGutters,
  liftFor,
  revealTarget,
  measureViewport,
} from './host';
import { RUNAWAY_GROWTHS, connectBridge, reportHeight } from './frame';
import { INIT, LIMITS, VIEWPORT } from './protocol';
import {
  installViewport,
  rewriteViewportUnits,
  sliceOf,
  stickyShift,
} from './viewport';

const require = createRequire(import.meta.url);
const hostViewportPlugin = require('../../../webpack/postcss-host-viewport.js');
const postcss = require('postcss');

const LABS = 'https://labs.connect.dimagi.com';

/** A host window 900 tall, the frame 3000 tall, scrolled `scrollY` with its top
 * `frameTop` from the window's top and labs' 64px header over it. */
function vp(over = {}) {
  return {
    width: 1440,
    height: 900,
    scrollX: 0,
    scrollY: 0,
    frameTop: 144,
    frameHeight: 3000,
    insetTop: 64,
    gutterLeft: 0,
    gutterRight: 0,
    clipLeft: -1,
    clipRight: -1,
    liftTop: -1,
    ...over,
  };
}

describe('viewport units are the host window’s', () => {
  it('rewrites vh, dvh, svh and lvh, negative and fractional, and nothing else', () => {
    expect(rewriteViewportUnits('100vh')).toBe(
      'calc(var(--labs-host-vh) * 100)',
    );
    expect(rewriteViewportUnits('calc(100dvh - 4rem)')).toBe(
      'calc(calc(var(--labs-host-vh) * 100) - 4rem)',
    );
    expect(rewriteViewportUnits('translateY(-50svh) 0.5lvh')).toBe(
      'translateY(calc(var(--labs-host-vh) * -50)) calc(var(--labs-host-vh) * 0.5)',
    );
    for (const same of ['10vw', '100%', 'var(--labs-host-vh)', '12px']) {
      expect(rewriteViewportUnits(same)).toBe(same);
    }
  });

  it('the stylesheet plugin keeps 100vh on a labs page and reads the variable in the frame', () => {
    const out = postcss([hostViewportPlugin]).process(
      '.h-screen{height:100vh}.x{min-height:calc(100dvh - 4rem);width:50vw}@media (min-width:640px){.y{max-height:80vh}}',
      { from: undefined },
    ).css;
    expect(out).toContain('height:calc(var(--labs-host-vh, 1vh) * 100)');
    expect(out).toContain(
      'min-height:calc(calc(var(--labs-host-vh, 1dvh) * 100) - 4rem)',
    );
    expect(out).toContain('max-height:calc(var(--labs-host-vh, 1vh) * 80)');
    expect(out).toContain('width:50vw');
    // Running twice changes nothing.
    expect(
      postcss([hostViewportPlugin]).process(out, { from: undefined }).css,
    ).toBe(out);
  });
});

describe('what the person sees of the frame (the slice)', () => {
  it('starts at the frame’s top, or below labs’ header once scrolled past', () => {
    expect(sliceOf(vp())).toEqual({ top: 0, height: 756 });
    expect(sliceOf(vp({ frameTop: -1000 }))).toEqual({
      top: 1064,
      height: 836,
    });
  });

  it('ends where the frame ends when it is shorter than the window', () => {
    expect(sliceOf(vp({ frameHeight: 300 }))).toEqual({ top: 0, height: 300 });
    expect(sliceOf(vp({ frameTop: -2900, frameHeight: 3000 }))).toEqual({
      top: 2964,
      height: 36,
    });
  });
});

describe('a dialog covers labs’ header and nav, as on the run page', () => {
  it('lifted, the slice is the whole window', () => {
    // Lifted from the page's top: the frame starts 144px above the window here.
    expect(
      sliceOf(vp({ frameTop: -144, frameHeight: 3144, liftTop: 144 })),
    ).toEqual({ top: 144, height: 900 });
    // Without the lift, the header still hides its top.
    expect(sliceOf(vp({ frameTop: -1000 }))).toEqual({
      top: 1064,
      height: 836,
    });
  });

  it('is lifted from the top of the page to the window’s edges, wherever it is now', () => {
    expect(liftFor({ top: 144, left: 64, right: 1424 }, null, 0, 1440)).toEqual(
      { top: 144, left: 64, right: 16 },
    );
    // Scrolled 500: the page's top is 500 further up.
    expect(
      liftFor({ top: -356, left: 64, right: 1424 }, null, 500, 1440),
    ).toEqual({ top: 144, left: 64, right: 16 });
    // Measured while lifted: the same lift, not a bigger one.
    const lift = { top: 144, left: 64, right: 16 };
    expect(
      liftFor({ top: -500, left: 0, right: 1440 }, lift, 500, 1440),
    ).toEqual(lift);
  });

  it('is not lifted where a box around it would keep it under the header', () => {
    const outer = document.createElement('div');
    const iframe = document.createElement('iframe');
    outer.appendChild(iframe);
    document.body.appendChild(outer);
    expect(canLift(iframe, window)).toBe(true);
    outer.style.transform = 'translateX(1px)';
    expect(canLift(iframe, window)).toBe(false);
    outer.style.transform = '';
    outer.style.overflowY = 'auto';
    expect(canLift(iframe, window)).toBe(false);
    outer.style.overflowY = '';
    outer.style.position = 'relative';
    outer.style.zIndex = '1';
    expect(canLift(iframe, window)).toBe(false);
    outer.remove();
  });

  it('only for a dialog the person just opened; closing needs nothing', async () => {
    const overlay = vi.fn();
    let active = false;
    const d = createDispatcher(
      {
        querySupply: vi.fn(),
        queryWorkflow: vi.fn(),
        navigate: vi.fn(),
        shareSelection: vi.fn(),
        resize: vi.fn(),
        overlay,
        scroll: vi.fn(),
        reveal: vi.fn(),
        copyText: vi.fn(),
        confirm: vi.fn(),
        download: vi.fn(),
        reportError: vi.fn(),
      },
      { labsOrigin: LABS, hasActivation: () => active, push: () => undefined },
    );
    const ask = (open) => d({ id: 1, method: 'overlay', params: { open } });
    expect((await ask(true)).result).toBe(false);
    expect(overlay).not.toHaveBeenCalled();
    active = true;
    expect((await ask(true)).result).toBe(true);
    expect(overlay).toHaveBeenLastCalledWith(true);
    active = false;
    expect((await ask(false)).result).toBe(true);
    expect(overlay).toHaveBeenLastCalledWith(false);
  });
});

describe('sticky elements stick below labs’ header, within their box', () => {
  const slice = { top: 1000, height: 800 };
  it('a top-sticky one is pushed down to the line, and no further than its box', () => {
    const top = { top: 0, bottom: null };
    expect(
      stickyShift(
        { top: 1200, height: 40 },
        { top: 0, bottom: 5000 },
        slice,
        top,
      ),
    ).toBe(0);
    expect(
      stickyShift(
        { top: 600, height: 40 },
        { top: 0, bottom: 5000 },
        slice,
        top,
      ),
    ).toBe(400);
    // Its table ends at 1020: it rides up with the table's end.
    expect(
      stickyShift(
        { top: 600, height: 40 },
        { top: 0, bottom: 1020 },
        slice,
        top,
      ),
    ).toBe(380);
    expect(
      stickyShift({ top: 600, height: 40 }, { top: 0, bottom: 5000 }, slice, {
        top: 32,
        bottom: null,
      }),
    ).toBe(432);
  });

  it('a bottom-sticky one is pulled up to the window’s bottom', () => {
    const bottom = { top: null, bottom: 0 };
    expect(
      stickyShift(
        { top: 2500, height: 50 },
        { top: 0, bottom: 5000 },
        slice,
        bottom,
      ),
    ).toBe(-750);
    expect(
      stickyShift(
        { top: 1500, height: 50 },
        { top: 0, bottom: 5000 },
        slice,
        bottom,
      ),
    ).toBe(0);
  });
});

describe('scrolling the host as scrollIntoView would have', () => {
  it('start, end, center and nearest, against the whole window as the browser does', () => {
    const v = vp({ scrollY: 500, frameTop: -356 }); // frame's top at page y 144
    // An element at frame y 2000..2040 is at page y 2144..2184.
    expect(revealTarget(v, 2000, 2040, 'start')).toBe(2144);
    expect(revealTarget(v, 2000, 2040, 'end')).toBe(2184 - 900);
    expect(revealTarget(v, 2000, 2040, 'center')).toBe(2164 - 450);
    expect(revealTarget(v, 2000, 2040, 'nearest')).toBe(2184 - 900);
    expect(revealTarget(v, 500, 540, 'nearest')).toBeNull();
    expect(revealTarget(v, 100, 140, 'nearest')).toBe(244);
    // The page's scroll-padding-top, when it has one.
    expect(revealTarget(v, 2000, 2040, 'start', 64)).toBe(2080);
    expect(revealTarget(v, 0, 10, 'start')).toBe(144);
  });
});

describe('the host tells the frame about its window', () => {
  function drawn(mode) {
    const container = document.createElement('div');
    document.body.appendChild(container);
    return createSandboxHost({
      container,
      labsOrigin: LABS,
      payload: { a: 1 },
      handlers: {
        querySupply: vi.fn(),
        queryWorkflow: vi.fn(),
        navigate: vi.fn(),
        shareSelection: vi.fn(),
        copyText: vi.fn(),
        confirm: vi.fn(),
        download: vi.fn(),
        reportError: vi.fn(),
      },
      mode,
      onLeave: () => {},
      hasActivation: () => true,
    });
  }
  function connect(host) {
    const frameWindow = { postMessage: vi.fn() };
    Object.defineProperty(host.iframe, 'contentWindow', {
      value: frameWindow,
      configurable: true,
    });
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'labs-sandbox:hello' },
        origin: 'null',
        source: frameWindow,
      }),
    );
    const [message, , ports] = frameWindow.postMessage.mock.calls[0];
    return { message, port: ports[0] };
  }

  it.each(['page', 'workflow'])(
    'a %s grows to its content (one sizing path, no fill-the-window mode)',
    async (mode) => {
      const host = drawn(mode);
      expect(host.iframe.style.height).toBe(`${LIMITS.minHeight * 4}px`);
      expect(host.iframe.getAttribute('scrolling')).toBe('no');
      const { port } = connect(host);
      port.postMessage({ id: 1, method: 'resize', params: { height: 4321 } });
      await new Promise((r) => setTimeout(r, 30));
      expect(host.iframe.style.height).toBe('4321px');
      host.destroy();
    },
  );

  it('INIT carries the window, and a scroll sends it again', async () => {
    const host = drawn('workflow');
    const { message, port } = connect(host);
    expect(message.type).toBe(INIT);
    expect(message.viewport).toMatchObject({
      width: window.innerWidth,
      height: window.innerHeight,
      insetTop: 0,
    });
    const got = [];
    port.onmessage = (e) => got.push(e.data);
    // Nothing moved: nothing sent.
    window.dispatchEvent(new Event('scroll'));
    await new Promise((r) => setTimeout(r, 20));
    expect(got).toEqual([]);
    const spy = vi
      .spyOn(host.iframe, 'getBoundingClientRect')
      .mockReturnValue({ top: -700, height: 3000 });
    window.dispatchEvent(new Event('scroll'));
    await new Promise((r) => setTimeout(r, 20));
    expect(got).toHaveLength(1);
    expect(got[0].type).toBe(VIEWPORT);
    expect(got[0].viewport).toMatchObject({
      frameTop: -700,
      frameHeight: 3000,
    });
    spy.mockRestore();
    host.destroy();
  });

  it('a dialog lifts the frame over the window, and closing it puts it back', async () => {
    const host = drawn('page');
    const { port } = connect(host);
    const got = [];
    port.onmessage = (e) => got.push(e.data);
    const ask = (id, method, params) => {
      port.postMessage({ id, method, params });
      return new Promise((r) => setTimeout(r, 30));
    };
    await ask(1, 'resize', { height: 2000 });
    // The frame sits 144px down the page, under a 64px header.
    let lifted = false;
    host.iframe.getBoundingClientRect = () =>
      lifted
        ? { top: 0, left: 0, right: 1024, height: 2144 }
        : { top: 144, left: 0, right: 1024, height: 2000 };
    await ask(2, 'overlay', { open: true });
    lifted = true;
    expect(host.iframe.style.position).toBe('relative');
    expect(host.iframe.style.top).toBe('-144px');
    expect(host.iframe.style.zIndex).toBe('50');
    // The page around it is laid out as before: the extra height is taken back.
    expect(host.iframe.style.height).toBe('2144px');
    expect(host.iframe.style.marginBottom).toBe('-144px');
    const view = got.filter((m) => m.type === VIEWPORT).at(-1).viewport;
    expect(view.liftTop).toBe(144);
    // A new height while lifted keeps the lift.
    await ask(3, 'resize', { height: 2500 });
    expect(host.iframe.style.height).toBe('2644px');
    await ask(4, 'overlay', { open: false });
    lifted = false;
    expect(host.iframe.style.position).toBe('');
    expect(host.iframe.style.top).toBe('');
    expect(host.iframe.style.zIndex).toBe('');
    expect(host.iframe.style.marginBottom).toBe('');
    expect(host.iframe.style.height).toBe('2500px');
    expect(got.filter((m) => m.type === VIEWPORT).at(-1).viewport.liftTop).toBe(
      -1,
    );
    host.destroy();
  });

  it('measures the header that is fixed over the page', () => {
    const header = document.createElement('header');
    header.style.position = 'fixed';
    document.body.appendChild(header);
    header.getBoundingClientRect = () => ({ top: 0, bottom: 64, height: 64 });
    const iframe = document.createElement('iframe');
    iframe.getBoundingClientRect = () => ({ top: 144, height: 500 });
    const v = measureViewport(window, iframe, 64);
    expect(v).toMatchObject({
      frameTop: 144,
      frameHeight: 500,
      insetTop: 64,
      gutterLeft: 0,
      gutterRight: 0,
    });
    header.remove();
  });

  it('reaches into the page’s margins, never over a column beside it', () => {
    // body (0..1440) > col (80..1440, padded) > main (96..1424) > box (112..1392)
    const at = (el, left, right, top = 100, height = 500) => {
      el.getBoundingClientRect = () => ({
        left,
        right,
        top,
        bottom: top + height,
        width: right - left,
        height,
      });
      return el;
    };
    const col = at(document.createElement('div'), 80, 1440, 0, 2000);
    const main = at(document.createElement('main'), 96, 1424, 0, 2000);
    const crumbs = at(document.createElement('nav'), 96, 1424, 40, 30);
    const box = at(document.createElement('div'), 112, 1392);
    main.append(crumbs, box);
    col.appendChild(main);
    document.body.appendChild(col);
    Object.defineProperty(document.documentElement, 'clientWidth', {
      value: 1440,
      configurable: true,
    });
    const bodyRect = document.body.getBoundingClientRect;
    document.body.getBoundingClientRect = () => ({
      left: 0,
      right: 1440,
      top: 0,
      bottom: 2000,
      width: 1440,
      height: 2000,
    });
    expect(frameGutters(box, window)).toEqual({
      left: 112,
      right: 48,
      clipLeft: -1,
      clipRight: -1,
    });
    // A column beside it stops the reach at the box that holds both.
    const side = at(document.createElement('aside'), 1100, 1424, 100, 300);
    main.appendChild(side);
    box.getBoundingClientRect = () => ({
      left: 112,
      right: 1090,
      top: 100,
      bottom: 600,
      width: 978,
      height: 500,
    });
    expect(frameGutters(box, window)).toMatchObject({ left: 0, right: 0 });
    // A fixed or absolute sibling is drawn over the page, not beside it.
    side.style.position = 'fixed';
    expect(frameGutters(box, window).left).toBe(112);
    // A box that clips does not stop it (fixed elements escaped it on the page), but
    // content is clipped where it clipped.
    side.remove();
    main.style.overflowX = 'clip';
    expect(frameGutters(box, window)).toEqual({
      left: 112,
      right: 350,
      clipLeft: 16,
      clipRight: 334,
    });
    col.remove();
    document.body.getBoundingClientRect = bodyRect;
    delete document.documentElement.clientWidth;
  });

  it('scroll and reveal are answered, uncounted, and checked', async () => {
    const h = {
      resize: vi.fn(),
      scroll: vi.fn(),
      reveal: vi.fn(),
    };
    const d = createDispatcher(h, {
      labsOrigin: LABS,
      hasActivation: () => false,
    });
    // More than a page's whole budget (60 per 10s), none refused.
    for (let i = 0; i < 100; i++) {
      const out = await d({ id: i, method: 'scroll', params: { top: i } });
      expect(out.error).toBeUndefined();
    }
    expect(h.scroll).toHaveBeenLastCalledWith(99, false);
    await d({
      id: 'r',
      method: 'reveal',
      params: { top: 5, bottom: 1, block: 'center', smooth: true },
    });
    expect(h.reveal).toHaveBeenCalledWith(5, 5, 'center', true);
    await d({
      id: 'r2',
      method: 'reveal',
      params: { top: 5, bottom: 9, block: 'sideways' },
    });
    expect(h.reveal).toHaveBeenLastCalledWith(5, 9, 'nearest', false);
    expect(
      (await d({ id: 'x', method: 'scroll', params: { top: 'a' } })).error,
    ).toMatch(/number/);
    expect(
      (await d({ id: 'y', method: 'reveal', params: { top: 1 } })).error,
    ).toMatch(/numbers/);
    expect(
      await d({ id: 'z', method: 'scroll', params: { top: -40 } }),
    ).toEqual({ id: 'z', result: true });
    expect(h.scroll).toHaveBeenLastCalledWith(0, false);
  });

  it('says once when a frame is taller than it may be drawn', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const h = { resize: vi.fn() };
    const d = createDispatcher(h, {
      labsOrigin: LABS,
      hasActivation: () => true,
    });
    await d({ id: 1, method: 'resize', params: { height: 1e9 } });
    await d({ id: 2, method: 'resize', params: { height: 1e9 } });
    expect(h.resize).toHaveBeenCalledWith(LIMITS.maxHeight);
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });
});

describe('the frame’s window is the host’s', () => {
  let controller;
  let root;
  afterEach(() => {
    controller?.destroy();
    controller = null;
    root?.remove();
  });
  function install() {
    root = document.createElement('div');
    root.id = 'sandbox-root';
    document.body.appendChild(root);
    const bridge = { request: vi.fn(async () => true) };
    controller = installViewport(window, root, bridge);
    return bridge;
  }

  it('says when a dialog opens and closes, and keeps the content where it was once lifted', async () => {
    // This test DOM lays nothing out: the dialog is as tall as the window.
    const native = Element.prototype.getBoundingClientRect;
    const rect = vi
      .spyOn(Element.prototype, 'getBoundingClientRect')
      .mockImplementation(function () {
        return this.dataset && this.dataset.dialog
          ? new DOMRect(0, 0, 1440, 900)
          : native.call(this);
      });
    const bridge = install();
    controller.apply(vp());
    const dialog = document.createElement('div');
    dialog.style.position = 'fixed';
    dialog.className = 'fixed';
    dialog.dataset.dialog = '1';
    document.body.appendChild(dialog);
    await new Promise((r) => setTimeout(r, 50));
    expect(bridge.request).toHaveBeenCalledWith('overlay', { open: true });
    // The frame 144px down the page, lifted to its top: the content is moved down by
    // as much, and the slice is the window.
    controller.apply(vp({ frameTop: 0, frameHeight: 3144, liftTop: 144 }));
    const style = document.documentElement.style;
    expect(style.getPropertyValue('--labs-lift-top')).toBe('144px');
    expect(style.getPropertyValue('--labs-slice-top')).toBe('0px');
    expect(style.getPropertyValue('--labs-slice-height')).toBe('900px');
    // Below the header: nothing of the content is under it.
    expect(style.getPropertyValue('--labs-band-top')).toBe('0px');
    // Scrolled 600 with it open: the top 520px of the content is under the header.
    controller.apply(
      vp({ scrollY: 600, frameTop: -600, frameHeight: 3144, liftTop: 144 }),
    );
    expect(style.getPropertyValue('--labs-band-top')).toBe('520px');
    expect(style.getPropertyValue('--labs-root-clip')).toBe('none');
    // Where the content clips, a clip there would clip the dialog too: none.
    controller.apply(
      vp({
        scrollY: 600,
        frameTop: -600,
        frameHeight: 3144,
        liftTop: 144,
        clipLeft: 16,
      }),
    );
    expect(style.getPropertyValue('--labs-band-top')).toBe('0px');
    expect(style.getPropertyValue('--labs-root-clip')).toBe(
      'inset(-100000px -100000px -100000px -16px)',
    );
    dialog.remove();
    await new Promise((r) => setTimeout(r, 50));
    expect(bridge.request).toHaveBeenLastCalledWith('overlay', { open: false });
    controller.destroy();
    controller = null;
    rect.mockRestore();
  });

  it('innerHeight is the host window’s, and 100vh reads it', () => {
    install();
    controller.apply(vp({ height: 777 }));
    expect(window.innerHeight).toBe(777);
    expect(
      document.documentElement.style.getPropertyValue('--labs-host-vh'),
    ).toBe('7.77px');
    expect(
      document.documentElement.style.getPropertyValue('--labs-slice-top'),
    ).toBe('0px');
    expect(
      document.documentElement.style.getPropertyValue('--labs-root-clip'),
    ).toBe('none');
    controller.apply(vp({ frameTop: -1000, clipLeft: 16, gutterLeft: 32 }));
    expect(
      document.documentElement.style.getPropertyValue('--labs-root-clip'),
    ).toBe('inset(-100000px -100000px -100000px -16px)');
    expect(
      document.documentElement.style.getPropertyValue('--labs-gutter-left'),
    ).toBe('32px');
    expect(
      document.documentElement.style.getPropertyValue('--labs-slice-top'),
    ).toBe('1064px');
    expect(
      document.documentElement.style.getPropertyValue('--labs-slice-height'),
    ).toBe('836px');
  });

  it('client coordinates are the slice’s, and scrollY puts them on the host page', () => {
    install();
    const el = document.createElement('div');
    root.appendChild(el);
    controller.apply(vp({ scrollY: 1144, frameTop: -1000 }));
    const r = el.getBoundingClientRect();
    expect(r.top).toBe(-1064);
    // rect + scrollY = the element's place on the host page (scrollY + frameTop + 0).
    expect(r.top + window.scrollY).toBe(1144 - 1000);
    expect(document.elementFromPoint(5, 10)).toBeDefined();
  });

  it('a pointer event’s clientY is the slice’s, its pageY the host page’s', () => {
    // A browser keeps clientY as a getter on MouseEvent.prototype, which is what is
    // patched; this test DOM keeps it on each event, so stand in a browser's.
    class BrowserMouseEvent {
      constructor(y) {
        this._y = y;
      }
      get clientY() {
        return this._y;
      }
      get y() {
        return this._y;
      }
      get pageY() {
        return this._y;
      }
    }
    const real = window.MouseEvent;
    Object.defineProperty(window, 'MouseEvent', {
      value: BrowserMouseEvent,
      configurable: true,
      writable: true,
    });
    try {
      install();
      controller.apply(vp({ scrollY: 1144, frameTop: -1000 }));
      const e = new BrowserMouseEvent(1100);
      expect(e.clientY).toBe(1100 - 1064);
      expect(e.y).toBe(1100 - 1064);
      expect(e.pageY).toBe(1100 + 1144 - 1000);
    } finally {
      controller.destroy();
      controller = null;
      Object.defineProperty(window, 'MouseEvent', {
        value: real,
        configurable: true,
        writable: true,
      });
    }
    expect(new BrowserMouseEvent(1100).clientY).toBe(1100);
  });

  it('window.scrollTo and scrollBy scroll the host; scrollIntoView reveals there', () => {
    const bridge = install();
    controller.apply(vp({ scrollY: 300, frameTop: -156 }));
    window.scrollTo(0, 450);
    expect(bridge.request).toHaveBeenLastCalledWith('scroll', {
      top: 450,
      smooth: false,
    });
    window.scrollTo({ top: 10, behavior: 'smooth' });
    expect(bridge.request).toHaveBeenLastCalledWith('scroll', {
      top: 10,
      smooth: true,
    });
    window.scrollBy(0, 100);
    // scrollY is where the slice starts on the host page: 300 + 64 (under the header).
    expect(window.scrollY).toBe(364);
    expect(bridge.request).toHaveBeenLastCalledWith('scroll', {
      top: 464,
      smooth: false,
    });
    const el = document.createElement('div');
    root.appendChild(el);
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    expect(bridge.request).toHaveBeenLastCalledWith('reveal', {
      top: 0,
      bottom: 0,
      block: 'center',
      smooth: true,
    });
    el.scrollIntoView(false);
    expect(bridge.request.mock.calls.at(-1)[1].block).toBe('end');
  });

  it('focus reveals the field on the host, unless preventScroll', () => {
    const bridge = install();
    controller.apply(vp());
    const input = document.createElement('input');
    root.appendChild(input);
    input.focus();
    expect(bridge.request).toHaveBeenLastCalledWith(
      'reveal',
      expect.objectContaining({ block: 'nearest' }),
    );
    input.blur();
    bridge.request.mockClear();
    input.focus({ preventScroll: true });
    expect(bridge.request).not.toHaveBeenCalled();
  });

  it('Space pages the host, as it paged the run page; not when meant for a control', () => {
    const bridge = install();
    controller.apply(vp({ scrollY: 1000, frameTop: -856 }));
    const space = (target, shiftKey = false) => {
      const e = new KeyboardEvent('keydown', {
        key: ' ',
        shiftKey,
        bubbles: true,
        cancelable: true,
      });
      target.dispatchEvent(e);
      return e.defaultPrevented;
    };
    expect(space(document.body)).toBe(true);
    expect(bridge.request).toHaveBeenLastCalledWith('scroll', {
      top: 1860,
      smooth: false,
    });
    const cell = document.createElement('td');
    root.appendChild(cell);
    space(cell, true);
    expect(bridge.request).toHaveBeenLastCalledWith('scroll', {
      top: 140,
      smooth: false,
    });
    bridge.request.mockClear();
    const button = document.createElement('button');
    root.appendChild(button);
    expect(space(button)).toBe(false);
    expect(bridge.request).not.toHaveBeenCalled();
  });

  it('a scroll on the host is a scroll event in the frame; a new window size a resize', () => {
    install();
    controller.apply(vp());
    const scrolled = vi.fn();
    const resized = vi.fn();
    window.addEventListener('scroll', scrolled);
    window.addEventListener('resize', resized);
    controller.apply(vp({ scrollY: 10, frameTop: 134 }));
    controller.apply(vp({ scrollY: 10, frameTop: 134, height: 700 }));
    expect(scrolled).toHaveBeenCalledTimes(1);
    expect(resized).toHaveBeenCalledTimes(1);
    window.removeEventListener('scroll', scrolled);
    window.removeEventListener('resize', resized);
  });

  it('inline styles and <style> elements in vh are rewritten as they land', async () => {
    install();
    controller.apply(vp());
    const box = document.createElement('div');
    box.style.height = '50vh';
    root.appendChild(box);
    const style = document.createElement('style');
    style.textContent = '.tall{min-height:100dvh}';
    root.appendChild(style);
    await new Promise((r) => setTimeout(r, 0));
    expect(box.style.height).toBe('calc(var(--labs-host-vh) * 50)');
    expect(style.textContent).toBe(
      '.tall{min-height:calc(var(--labs-host-vh) * 100)}',
    );
    box.style.maxHeight = '80vh';
    await new Promise((r) => setTimeout(r, 0));
    expect(box.style.maxHeight).toBe('calc(var(--labs-host-vh) * 80)');
  });

  it('destroy puts the window back', () => {
    install();
    controller.apply(vp({ height: 321 }));
    expect(window.innerHeight).toBe(321);
    controller.destroy();
    controller = null;
    expect(window.innerHeight).not.toBe(321);
  });
});

describe('the frame reports its content’s height', () => {
  let restore;
  afterEach(() => restore?.());
  function rootOf(height) {
    const root = document.createElement('div');
    const original = Element.prototype.getBoundingClientRect;
    const h = { value: height };
    Element.prototype.getBoundingClientRect = function () {
      return this === root ? { height: h.value, top: 0 } : original.call(this);
    };
    restore = () => {
      Element.prototype.getBoundingClientRect = original;
    };
    return { root, h };
  }

  it('exactly as tall as the content, and never less than an open dialog needs', () => {
    const { root, h } = rootOf(400.2);
    const sent = [];
    let min = 0;
    const r = reportHeight(root, (x) => sent.push(x), { minHeight: () => min });
    expect(sent).toEqual([400.2]);
    min = 836;
    r.measure();
    h.value = 300;
    r.measure();
    expect(sent).toEqual([400.2, 836]);
    min = 0;
    r.measure();
    expect(sent).toEqual([400.2, 836, 300]);
    r.disconnect();
  });

  it('stops following content that grows with the frame, and says so once', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { root, h } = rootOf(100);
    const sent = [];
    let t = 0;
    const r = reportHeight(root, (x) => sent.push(x), { now: () => t });
    for (let i = 0; i < RUNAWAY_GROWTHS + 20; i++) {
      t += 16;
      h.value += 100;
      r.measure();
    }
    const held = sent.at(-1);
    expect(held).toBeLessThan(100 + (RUNAWAY_GROWTHS + 20) * 100);
    expect(warn).toHaveBeenCalledTimes(1);
    // Shrinking is still followed.
    h.value = 50;
    r.measure();
    expect(sent.at(-1)).toBe(50);
    // A page that grows slowly (rows arriving) is followed all the way.
    const slow = rootOf(100);
    const sent2 = [];
    const r2 = reportHeight(slow.root, (x) => sent2.push(x), { now: () => t });
    for (let i = 0; i < RUNAWAY_GROWTHS + 20; i++) {
      t += 1000;
      slow.h.value += 100;
      r2.measure();
    }
    expect(sent2.at(-1)).toBe(100 + (RUNAWAY_GROWTHS + 20) * 100);
    warn.mockRestore();
    r.disconnect();
    r2.disconnect();
  });
});

describe('the bridge hands the frame the window', () => {
  it('INIT’s viewport reaches onInit, and VIEWPORT messages onViewport', () => {
    const listeners = [];
    const parent = { postMessage: vi.fn() };
    const win = {
      parent,
      addEventListener: (t, fn) => listeners.push(fn),
      removeEventListener: () => {},
    };
    const onInit = vi.fn();
    const onViewport = vi.fn();
    connectBridge(win, { onInit, onUpdate: vi.fn(), onViewport }, 10_000);
    const channel = new MessageChannel();
    listeners.forEach((fn) =>
      fn({
        source: parent,
        data: { type: INIT, payload: {}, viewport: vp() },
        ports: [channel.port2],
      }),
    );
    expect(onInit.mock.calls[0][2]).toEqual(vp());
    return new Promise((resolve) => {
      channel.port1.postMessage({ type: VIEWPORT, viewport: { bogus: 1 } });
      channel.port1.postMessage({
        type: VIEWPORT,
        viewport: vp({ scrollY: 9 }),
      });
      setTimeout(() => {
        expect(onViewport).toHaveBeenCalledTimes(1);
        expect(onViewport.mock.calls[0][0].scrollY).toBe(9);
        channel.port1.close();
        resolve();
      }, 20);
    });
  });
});
