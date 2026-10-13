/**
 * The frame's window, made to be the HOST's (spec: "Sizing: the frame grows to its
 * content"). The frame is as tall as what it draws and never scrolls -- the page
 * around it does -- so its own window is the whole document, not what the person
 * sees. Render code was written against the run page's window; this makes the
 * frame's say what that window said:
 *
 * - `100vh` (and dvh, svh, lvh; Tailwind's h-screen, min-h-screen...) is the host
 *   window's height: `--labs-host-vh` (tailwind/postcss-host-viewport.js does the
 *   stylesheet; inline styles and <style> elements are rewritten here as they land).
 *   Never the frame's own, which is the content: content sized by it grew without end.
 * - `position: fixed` is placed within the part of the frame the person can see (the
 *   SLICE): the frame's <body> is moved to the slice and is the fixed elements'
 *   containing block (a transform), and the content is moved back by as much (the
 *   shell's stylesheet, workflow/sandbox_frame.html). A dialog is centred where the
 *   person is looking, a toast sits at the bottom of the window.
 * - So that code placing things itself (a tooltip at an element's rect, a map's
 *   pointer) agrees, client coordinates are the slice's: `getBoundingClientRect`,
 *   `clientY` and `elementFromPoint` are moved by the slice's top, `scrollY` is the
 *   host's plus as much, and `innerHeight` is the host window's.
 * - `position: sticky` sticks to the top of the slice (below labs' header), as the
 *   frame has no scrolling of its own for it to stick to.
 * - `scrollTo`, `scrollIntoView` and focusing a field scroll the HOST.
 *
 * Horizontally the frame reaches past its column into the page's empty margins
 * (host.ts frameGutters) and keeps the content in the column (`--labs-gutter-left`,
 * `--labs-gutter-right`): what overflowed the column still shows, a fixed dialog is
 * centred in the window, and `vw`, `innerWidth` and media queries are, as on the run
 * page, nearly the window's.
 */

import type { HostViewport } from './protocol';

export const VH_VAR = '--labs-host-vh';
export const SLICE_TOP_VAR = '--labs-slice-top';
export const SLICE_HEIGHT_VAR = '--labs-slice-height';
export const GUTTER_LEFT_VAR = '--labs-gutter-left';
export const GUTTER_RIGHT_VAR = '--labs-gutter-right';
export const CLIP_LEFT_VAR = '--labs-clip-left';
export const CLIP_RIGHT_VAR = '--labs-clip-right';
/** Far enough to be no clip at all. */
const UNCLIPPED = 100000;

const VH_RE = /(-?(?:\d+\.?\d*|\.\d+))(?:d|s|l)?vh\b/g;

/** `70vh` -> `calc(var(--labs-host-vh) * 70)`: the host window's, not the frame's. */
export function rewriteViewportUnits(value: string): string {
  return value.replace(VH_RE, (_m, n) => `calc(var(${VH_VAR}) * ${n})`);
}

function hasViewportUnits(value: string | null | undefined): boolean {
  if (!value) return false;
  VH_RE.lastIndex = 0;
  const yes = VH_RE.test(value);
  VH_RE.lastIndex = 0;
  return yes;
}

/** The part of the frame the person can see, in the frame document's coordinates. */
export interface Slice {
  top: number;
  height: number;
}

export function sliceOf(vp: HostViewport): Slice {
  const clamp = (n: number) => Math.min(vp.frameHeight, Math.max(0, n));
  const top = clamp(vp.insetTop - vp.frameTop);
  const bottom = clamp(vp.height - vp.frameTop);
  return {
    top: Math.round(top),
    height: Math.round(Math.max(0, bottom - top)),
  };
}

/** A sticky element's shift, given where it would be and what it sticks within. */
export function stickyShift(
  natural: { top: number; height: number },
  container: { top: number; bottom: number },
  slice: Slice,
  offset: { top: number | null; bottom: number | null },
): number {
  if (offset.top !== null) {
    const line = slice.top + offset.top;
    if (natural.top >= line) return 0;
    const room = container.bottom - natural.top - natural.height;
    return Math.max(0, Math.min(line - natural.top, room));
  }
  if (offset.bottom !== null) {
    const line = slice.top + slice.height - offset.bottom;
    const over = natural.top + natural.height - line;
    if (over <= 0) return 0;
    return -Math.max(0, Math.min(over, natural.top - container.top));
  }
  return 0;
}

export interface ViewportBridge {
  request(method: string, params?: unknown): Promise<unknown>;
}

export interface ViewportController {
  /** The host's window changed (or arrived: INIT). */
  apply(vp: HostViewport): void;
  /** The least the frame may be: an open dialog needs the window below its top. */
  minHeight(): number;
  /** Called when minHeight may have changed (a dialog opened or closed). */
  onMinHeightChange(fn: () => void): void;
  destroy(): void;
}

type Win = Window & typeof globalThis;

function px(value: string): number | null {
  if (!value || value === 'auto') return null;
  const n = parseFloat(value);
  return Number.isFinite(n) ? n : null;
}

/**
 * Make the frame's window the host's. `apply` the INIT viewport before the first
 * draw, then every VIEWPORT the host sends.
 */
export function installViewport(
  win: Window,
  root: HTMLElement,
  bridge: ViewportBridge,
): ViewportController {
  const w = win as Win;
  const doc = w.document;
  const html = doc.documentElement;
  let vp: HostViewport | null = null;
  let slice: Slice = { top: 0, height: 0 };
  let overlayOpen = false;
  let minHeightListener: (() => void) | null = null;
  const cleanups: (() => void)[] = [];

  // --- coordinates ---------------------------------------------------------
  const ElementProto = w.Element.prototype;
  const nativeRect = ElementProto.getBoundingClientRect;
  const nativeRects = ElementProto.getClientRects;
  const offset = () => slice.top;
  const hostScrollY = () => (vp ? vp.scrollY + vp.frameTop + slice.top : 0);
  const shifted = (r: DOMRect) =>
    offset() ? new w.DOMRect(r.x, r.y - offset(), r.width, r.height) : r;
  const shiftedList = (list: DOMRectList) => {
    if (!offset()) return list;
    const out = Array.from(list).map(shifted) as unknown as DOMRectList & {
      item(i: number): DOMRect | null;
    };
    (out as unknown as { item: (i: number) => DOMRect | null }).item = (i) =>
      (out as unknown as DOMRect[])[i] || null;
    return out;
  };
  const patch = <T extends object>(
    obj: T,
    name: string,
    value: unknown,
    asGetter = false,
  ) => {
    const before = Object.getOwnPropertyDescriptor(obj, name);
    try {
      Object.defineProperty(
        obj,
        name,
        asGetter
          ? { get: value as () => unknown, configurable: true }
          : { value, configurable: true, writable: true },
      );
    } catch {
      return;
    }
    cleanups.push(() => {
      if (before) Object.defineProperty(obj, name, before);
      else delete (obj as Record<string, unknown>)[name];
    });
  };
  patch(ElementProto, 'getBoundingClientRect', function (this: Element) {
    return shifted(nativeRect.call(this));
  });
  patch(ElementProto, 'getClientRects', function (this: Element) {
    return shiftedList(nativeRects.call(this));
  });
  if (w.Range) {
    const rp = w.Range.prototype;
    const rRect = rp.getBoundingClientRect;
    const rRects = rp.getClientRects;
    patch(rp, 'getBoundingClientRect', function (this: Range) {
      return shifted(rRect.call(this));
    });
    patch(rp, 'getClientRects', function (this: Range) {
      return shiftedList(rRects.call(this));
    });
  }
  const eventY = (proto: object | undefined, names: string[]) => {
    if (!proto) return;
    const client = Object.getOwnPropertyDescriptor(proto, 'clientY')?.get;
    for (const name of names) {
      const d = Object.getOwnPropertyDescriptor(proto, name);
      if (!d || !d.get) continue;
      const get = d.get;
      patch(
        proto,
        name,
        name === 'pageY'
          ? function (this: object) {
              // The host page's: what the run page's pageY was.
              const y = (client || get).call(this) as number;
              return vp ? y + vp.scrollY + vp.frameTop : y;
            }
          : function (this: object) {
              return (get.call(this) as number) - offset();
            },
        true,
      );
    }
  };
  eventY(w.MouseEvent?.prototype, ['clientY', 'y', 'pageY']);
  eventY((w as unknown as { Touch?: { prototype: object } }).Touch?.prototype, [
    'clientY',
    'pageY',
  ]);
  for (const name of ['elementFromPoint', 'elementsFromPoint'] as const) {
    const fn = (doc as unknown as Record<string, unknown>)[name] as
      ((x: number, y: number) => unknown) | undefined;
    if (typeof fn !== 'function') continue;
    patch(doc, name, (x: number, y: number) =>
      fn.call(doc, x, Number(y) + offset()),
    );
  }
  for (const name of ['caretRangeFromPoint', 'caretPositionFromPoint']) {
    const fn = (doc as unknown as Record<string, unknown>)[name] as
      ((x: number, y: number, o?: unknown) => unknown) | undefined;
    if (typeof fn !== 'function') continue;
    patch(doc, name, (x: number, y: number, o?: unknown) =>
      fn.call(doc, x, Number(y) + offset(), o),
    );
  }
  const ownOrProto = (name: string) =>
    Object.getOwnPropertyDescriptor(w, name) ||
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(w), name);
  const nativeScrollY = ownOrProto('scrollY')?.get;
  const nativeInnerHeight = ownOrProto('innerHeight')?.get;
  patch(
    w,
    'innerHeight',
    () => (vp ? vp.height : nativeInnerHeight?.call(w)),
    true,
  );
  patch(w, 'scrollY', hostScrollY, true);
  patch(w, 'pageYOffset', hostScrollY, true);

  // --- scrolling: the host's ----------------------------------------------
  const nativeScrollTo = w.scrollTo.bind(w);
  const scrollHost = (top: number, smooth: boolean) => {
    if (!Number.isFinite(top)) return;
    bridge
      .request('scroll', { top: Math.max(0, top), smooth })
      .catch(() => undefined);
  };
  const scrollArgs = (a: unknown, b: unknown, relative: boolean) => {
    let top: number | undefined;
    let smooth = false;
    if (a && typeof a === 'object') {
      const o = a as { top?: unknown; behavior?: unknown };
      if (o.top === undefined) return;
      top = Number(o.top);
      smooth = o.behavior === 'smooth';
    } else {
      top = Number(b);
    }
    if (top === undefined || !Number.isFinite(top)) return;
    scrollHost(relative ? hostScrollY() + top : top, smooth);
  };
  patch(w, 'scrollTo', (a?: unknown, b?: unknown) => scrollArgs(a, b, false));
  patch(w, 'scroll', (a?: unknown, b?: unknown) => scrollArgs(a, b, false));
  patch(w, 'scrollBy', (a?: unknown, b?: unknown) => scrollArgs(a, b, true));
  for (const el of [html, doc.body]) {
    if (!el) continue;
    try {
      Object.defineProperty(el, 'scrollTop', {
        configurable: true,
        get: hostScrollY,
        set: (v: number) => scrollHost(Number(v), false),
      });
      cleanups.push(() => {
        delete (el as unknown as Record<string, unknown>).scrollTop;
      });
    } catch {
      // left native
    }
  }
  // The frame's own document never scrolls (the shell hides its overflow); a
  // browser that scrolls it anyway (focusing a field in a tall dialog) is put back.
  const onNativeScroll = () => {
    if (nativeScrollY && nativeScrollY.call(w)) nativeScrollTo(0, 0);
  };
  w.addEventListener('scroll', onNativeScroll, { passive: true });
  cleanups.push(() => w.removeEventListener('scroll', onNativeScroll));

  const fixedAncestor = (el: Element | null): Element | null => {
    for (let e = el; e && e !== doc.body; e = e.parentElement) {
      if (w.getComputedStyle(e).position === 'fixed') return e;
    }
    return null;
  };
  const reveal = (el: Element, block: string, smooth: boolean) => {
    if (!vp) return;
    const r = nativeRect.call(el);
    if (fixedAncestor(el)) {
      // In a dialog, which moves with the window: scrolling helps only while the
      // frame's top is below the header, where the dialog has less than the window.
      const below = r.bottom - (slice.top + slice.height);
      const room = vp.frameTop - vp.insetTop;
      if (below > 0 && room > 0)
        scrollHost(vp.scrollY + Math.min(room, below + 8), smooth);
      return;
    }
    const cs = w.getComputedStyle(el);
    const margin = (v: string) => parseFloat(v) || 0;
    bridge
      .request('reveal', {
        top: r.top - margin(cs.scrollMarginTop),
        bottom: r.bottom + margin(cs.scrollMarginBottom),
        block,
        smooth,
      })
      .catch(() => undefined);
  };
  const nativeIntoView = ElementProto.scrollIntoView;
  patch(
    ElementProto,
    'scrollIntoView',
    function (this: Element, arg?: boolean | ScrollIntoViewOptions) {
      // Scroll the element's own scrolling boxes (a list, a table) as before...
      try {
        nativeIntoView.call(this, arg as ScrollIntoViewOptions);
      } catch {
        // an argument this browser does not take
      }
      onNativeScroll();
      // ...and the page around the frame instead of the frame's.
      let block = 'start';
      let smooth = false;
      if (arg === false) block = 'end';
      else if (arg && typeof arg === 'object') {
        block = arg.block || 'start';
        smooth = arg.behavior === 'smooth';
      }
      reveal(this, block, smooth);
    },
  );
  // Focus scrolls the run page to the field (a Tab, a dialog's autofocus); not when
  // the code said preventScroll.
  let preventScroll = false;
  const HtmlProto = w.HTMLElement.prototype;
  const nativeFocus = HtmlProto.focus;
  patch(HtmlProto, 'focus', function (this: HTMLElement, o?: FocusOptions) {
    preventScroll = !!(o && o.preventScroll);
    try {
      nativeFocus.call(this, o);
    } finally {
      preventScroll = false;
    }
  });
  const onFocusIn = (e: FocusEvent) => {
    const t = e.target as Element | null;
    if (preventScroll || !t || t === doc.body || !(t instanceof w.Element))
      return;
    onNativeScroll();
    reveal(t, 'nearest', false);
  };
  doc.addEventListener('focusin', onFocusIn, true);
  cleanups.push(() => doc.removeEventListener('focusin', onFocusIn, true));

  // The keyboard pages the host as it paged the run page. PageDown, the arrows,
  // Home and End reach it from a frame that cannot scroll; Space does not in
  // Chromium or WebKit, so it is done here (by the browser's step, the window less
  // 40px), unless it was meant for a control or a box that scrolls itself.
  /** A box between `el` and the root that Space would page instead. */
  const pagesItself = (el: Element) => {
    for (let e: Element | null = el; e && e !== root; e = e.parentElement) {
      const y = w.getComputedStyle(e).overflowY;
      if ((y === 'auto' || y === 'scroll') && e.scrollHeight > e.clientHeight)
        return true;
    }
    return false;
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (
      e.key !== ' ' ||
      e.defaultPrevented ||
      e.ctrlKey ||
      e.metaKey ||
      e.altKey
    )
      return;
    if (!vp) return;
    const t = e.target as Element | null;
    if (
      t &&
      t !== doc.body &&
      t !== html &&
      typeof t.closest === 'function' &&
      (t.closest(
        'input, textarea, select, button, a[href], summary, [contenteditable], [role=button], [role=checkbox], [role=menuitem], [role=option], [role=tab]',
      ) ||
        pagesItself(t))
    )
      return;
    e.preventDefault();
    const step = Math.max(vp.height * 0.875, vp.height - 40);
    scrollHost(vp.scrollY + (e.shiftKey ? -step : step), false);
  };
  doc.addEventListener('keydown', onKeyDown);
  cleanups.push(() => doc.removeEventListener('keydown', onKeyDown));

  // --- viewport units in inline styles and <style> elements ---------------
  const rewriteStyleAttr = (el: Element) => {
    const style = (el as HTMLElement).style;
    if (!style || !hasViewportUnits(el.getAttribute('style'))) return;
    for (let i = 0; i < style.length; i++) {
      const name = style[i];
      const value = style.getPropertyValue(name);
      if (hasViewportUnits(value)) {
        style.setProperty(
          name,
          rewriteViewportUnits(value),
          style.getPropertyPriority(name),
        );
      }
    }
  };
  const rewriteStyleElement = (el: Element) => {
    const text = el.textContent || '';
    if (hasViewportUnits(text)) el.textContent = rewriteViewportUnits(text);
  };
  const rewriteTree = (node: Node) => {
    if (node.nodeType !== 1) return;
    const el = node as Element;
    if (el.tagName === 'STYLE') rewriteStyleElement(el);
    else if (el.hasAttribute('style')) rewriteStyleAttr(el);
    for (const s of Array.from(el.querySelectorAll('style')))
      rewriteStyleElement(s);
    for (const s of Array.from(el.querySelectorAll('[style*="vh"]')))
      rewriteStyleAttr(s);
  };

  // --- sticky and fixed: found as the page draws, placed as the window moves ---
  interface Sticky {
    key: string;
    top: number | null;
    bottom: number | null;
    shift: number;
  }
  const stickies = new Map<HTMLElement, Sticky>();
  const known = new WeakMap<Element, { key: string; sticky: Sticky | null }>();
  const scrollsItself = (el: Element) => {
    for (let e = el.parentElement; e && e !== root; e = e.parentElement) {
      const cs = w.getComputedStyle(e);
      const scroll = (v: string) => v !== 'visible' && v !== 'clip';
      if (scroll(cs.overflowX) || scroll(cs.overflowY)) return true;
    }
    return false;
  };
  const classify = (el: HTMLElement): Sticky | null => {
    const key = `${el.getAttribute('class') || ''}|${el.getAttribute('style') || ''}`;
    const seen = known.get(el);
    if (seen && seen.key === key) return seen.sticky;
    let sticky: Sticky | null = null;
    const cs = w.getComputedStyle(el);
    if (cs.position === 'sticky') {
      const top = px(cs.top);
      const bottom = top === null ? px(cs.bottom) : null;
      // Inside a box that scrolls, it sticks to that box, natively, as it did.
      if ((top !== null || bottom !== null) && !scrollsItself(el)) {
        sticky = { key, top, bottom, shift: stickies.get(el)?.shift || 0 };
      }
    }
    known.set(el, { key, sticky });
    return sticky;
  };
  const placeStickies = () => {
    if (!stickies.size) return;
    const reads: [HTMLElement, Sticky, number][] = [];
    for (const [el, s] of stickies) {
      if (!el.isConnected) {
        stickies.delete(el);
        continue;
      }
      const r = nativeRect.call(el);
      const box = el.closest('table') || el.parentElement || root;
      const c = nativeRect.call(box);
      const natural = { top: r.top - s.shift, height: r.height };
      const shift = Math.round(
        stickyShift(natural, { top: c.top, bottom: c.bottom }, slice, s),
      );
      reads.push([el, s, shift]);
    }
    for (const [el, s, shift] of reads) {
      if (shift === s.shift) continue;
      s.shift = shift;
      el.style.translate = shift ? `0px ${shift}px` : '';
    }
  };
  const isOverlay = (el: HTMLElement) => {
    if (!vp) return false;
    const r = nativeRect.call(el);
    return (
      r.height >= Math.max(40, 0.9 * slice.height) ||
      r.height >= 0.5 * vp.height
    );
  };
  const fixedEls = new Set<HTMLElement>();
  const scan = () => {
    const found = new Set<HTMLElement>();
    for (const el of Array.from(
      root.querySelectorAll<HTMLElement>(
        '[class*="sticky"], [style*="sticky"]',
      ),
    )) {
      const s = classify(el);
      if (s) {
        found.add(el);
        if (!stickies.has(el)) stickies.set(el, s);
        else {
          const cur = stickies.get(el)!;
          cur.top = s.top;
          cur.bottom = s.bottom;
        }
      }
    }
    for (const el of Array.from(stickies.keys())) {
      if (!found.has(el)) {
        el.style.translate = '';
        stickies.delete(el);
      }
    }
    fixedEls.clear();
    const candidates = new Set<HTMLElement>(
      Array.from(
        doc.body.querySelectorAll<HTMLElement>(
          '[class*="fixed"], [style*="fixed"]',
        ),
      ),
    );
    for (const el of Array.from(doc.body.children) as HTMLElement[])
      candidates.add(el);
    for (const el of candidates) {
      if (el === root || el.tagName === 'SCRIPT') continue;
      if (w.getComputedStyle(el).position === 'fixed') fixedEls.add(el);
    }
    placeStickies();
    checkOverlay();
  };
  const checkOverlay = () => {
    let open = false;
    for (const el of fixedEls) {
      if (el.isConnected && isOverlay(el)) {
        open = true;
        break;
      }
    }
    if (open !== overlayOpen) {
      overlayOpen = open;
      minHeightListener?.();
    }
  };
  let scanQueued = 0;
  const queueScan = () => {
    if (scanQueued) return;
    scanQueued = w.requestAnimationFrame(() => {
      scanQueued = 0;
      scan();
    });
  };
  const observer = new w.MutationObserver((records) => {
    let structural = false;
    for (const r of records) {
      if (r.type === 'attributes') {
        const el = r.target as HTMLElement;
        // Ours: the window's variables, a sticky element's shift.
        if (el === html || (r.attributeName === 'style' && stickies.has(el)))
          continue;
        if (r.attributeName === 'class') structural = true;
        else {
          rewriteStyleAttr(el);
          // A map moves its markers by style on every frame: only what can make
          // an element fixed, sticky or shown is worth a look.
          if (
            /position|display|fixed|sticky|inset/.test(
              el.getAttribute('style') || '',
            )
          )
            structural = true;
        }
      } else if (r.type === 'characterData') {
        const p = (r.target as Node).parentElement;
        if (p && p.tagName === 'STYLE') rewriteStyleElement(p);
      } else {
        r.addedNodes.forEach(rewriteTree);
        if (r.target && (r.target as Element).tagName === 'STYLE')
          rewriteStyleElement(r.target as Element);
        structural = true;
      }
    }
    if (structural) queueScan();
  });
  observer.observe(doc.documentElement, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: ['style', 'class'],
  });
  cleanups.push(() => observer.disconnect());
  const rootObserver =
    typeof w.ResizeObserver === 'function'
      ? new w.ResizeObserver(() => {
          placeStickies();
          checkOverlay();
        })
      : null;
  rootObserver?.observe(root);
  cleanups.push(() => rootObserver?.disconnect());

  return {
    apply(next: HostViewport) {
      const before = vp;
      vp = next;
      slice = sliceOf(next);
      html.style.setProperty(VH_VAR, `${next.height / 100}px`);
      html.style.setProperty(SLICE_TOP_VAR, `${slice.top}px`);
      html.style.setProperty(SLICE_HEIGHT_VAR, `${slice.height}px`);
      html.style.setProperty(GUTTER_LEFT_VAR, `${next.gutterLeft || 0}px`);
      html.style.setProperty(GUTTER_RIGHT_VAR, `${next.gutterRight || 0}px`);
      // Where the page clipped content that overflowed the column (an inset as far
      // out as that, or none).
      const clipAt = (n: number) => `${-(n >= 0 ? n : UNCLIPPED)}px`;
      html.style.setProperty(CLIP_LEFT_VAR, clipAt(next.clipLeft));
      html.style.setProperty(CLIP_RIGHT_VAR, clipAt(next.clipRight));
      placeStickies();
      checkOverlay();
      if (!before) return;
      if (
        before.scrollY !== next.scrollY ||
        before.frameTop !== next.frameTop
      ) {
        doc.dispatchEvent(new w.Event('scroll', { bubbles: true }));
      }
      if (before.height !== next.height || before.width !== next.width) {
        w.dispatchEvent(new w.Event('resize'));
      }
    },
    minHeight() {
      return overlayOpen && vp ? Math.max(0, vp.height - vp.insetTop) : 0;
    },
    onMinHeightChange(fn) {
      minHeightListener = fn;
    },
    destroy() {
      if (scanQueued) w.cancelAnimationFrame(scanQueued);
      for (const c of cleanups.reverse()) c();
    },
  };
}
