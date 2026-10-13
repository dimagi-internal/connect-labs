/**
 * What a WORKFLOW's render code reaches for that an opaque-origin frame does not
 * have, put back as bridge requests -- so render code written for the run page
 * runs unchanged in the sandbox. Spec: page-sandbox-design.md, "Phase 2".
 *
 * None of this is a security boundary. The frame's opaque origin and CSP are: its
 * own `fetch` to labs carries no session and is blocked by `connect-src` anyway.
 * These shims only turn what render code used to do directly into requests the
 * HOST decides on (./routes.ts for fetch, the navigate allowlist for links):
 *
 * - `fetch(<labs path>)`        -> bridge `fetch` (other hosts: the browser's own,
 *                                  which the CSP mostly refuses)
 * - `<img src="<labs path>">`   -> the bytes fetched through the bridge, shown as a
 *                                  `blob:` URL (the only images img-src allows)
 * - `EventSource`               -> a stream the HOST reads (bridge `streamOpen`),
 *                                  checked against the same route table as fetch
 * - `FormData` bodies           -> sent url-encoded (files are refused)
 * - `document.cookie`           -> "" (reading it throws in an opaque origin)
 * - `confirm()` / `prompt()`    -> No / null, and a host notice saying so (where the
 *                                  call sits in an async function or an event
 *                                  handler, ./rewrite.ts compiled it to the host's
 *                                  own question instead)
 * - `alert(text)`               -> bridge `notify`, drawn by the host as text
 * - `window.open(url)`          -> bridge `navigate` in a new tab
 * - `history.pushState/replace` -> bridge `setLocation` on the host's own URL
 * - `navigator.clipboard`       -> bridge `copyText`
 * - a click on `<a download>`   -> bridge `download` with the blob's bytes
 *
 * `window.location` cannot be shimmed at run time (the Navigation API that could
 * cancel a navigation is disabled in an opaque-origin document, and the property
 * cannot be redefined). ./rewrite.ts compiles every reference to it into
 * `__labsSandbox.location` (`sandboxGlobals` below) instead.
 */

import type { Bridge } from './frame';
import type { StreamMessage } from './protocol';

export interface FetchAnswer {
  status: number;
  statusText: string;
  headers: Record<string, string>;
  body: ArrayBuffer;
}

/** The frame's own URL is the shell's on labs' host: a labs path resolves there. */
export function labsUrl(input: string, base: string): URL | null {
  try {
    const u = new URL(input, base);
    const b = new URL(base);
    return u.origin === b.origin ? u : null;
  } catch {
    return null;
  }
}

async function bodyOf(
  body: unknown,
  headers: Record<string, string>,
): Promise<string | ArrayBuffer | undefined> {
  if (body === undefined || body === null) return undefined;
  if (typeof body === 'string') return body;
  if (body instanceof URLSearchParams) {
    if (!headers['content-type']) {
      headers['content-type'] =
        'application/x-www-form-urlencoded;charset=UTF-8';
    }
    return body.toString();
  }
  if (body instanceof ArrayBuffer) return body;
  if (ArrayBuffer.isView(body)) {
    const v = body as ArrayBufferView;
    return v.buffer.slice(
      v.byteOffset,
      v.byteOffset + v.byteLength,
    ) as ArrayBuffer;
  }
  if (typeof Blob !== 'undefined' && body instanceof Blob) {
    return body.arrayBuffer();
  }
  // A form of plain fields (a CSRF token among them, which the host drops: it adds
  // its own) goes as the url-encoded form it is equivalent to. A file is refused:
  // there is no upload route.
  if (typeof FormData !== 'undefined' && body instanceof FormData) {
    const params = new URLSearchParams();
    body.forEach((value, key) => {
      if (typeof value !== 'string') {
        throw new TypeError('In the sandbox a form may not carry a file');
      }
      params.append(key, value);
    });
    headers['content-type'] = 'application/x-www-form-urlencoded;charset=UTF-8';
    return params.toString();
  }
  throw new TypeError(
    'In the sandbox a request body must be text, a form, URLSearchParams or bytes',
  );
}

function headerRecord(h: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!h) return out;
  if (typeof Headers !== 'undefined' && h instanceof Headers) {
    h.forEach((v, k) => {
      out[k.toLowerCase()] = v;
    });
    return out;
  }
  const entries: [string, unknown][] = Array.isArray(h)
    ? (h as [string, unknown][])
    : Object.entries(h as Record<string, unknown>);
  for (const [k, v] of entries) out[String(k).toLowerCase()] = String(v);
  return out;
}

/** A `fetch` that sends labs requests over the bridge and anything else natively. */
export function makeSandboxFetch(
  bridge: Bridge,
  base: () => string,
  nativeFetch: typeof fetch,
): typeof fetch {
  return async function sandboxFetch(
    input: RequestInfo | URL,
    init: RequestInit = {},
  ): Promise<Response> {
    let url: string;
    let method = init.method;
    let headers = headerRecord(init.headers);
    let body: unknown = init.body;
    if (typeof Request !== 'undefined' && input instanceof Request) {
      url = input.url;
      method = method || input.method;
      headers = { ...headerRecord(input.headers), ...headers };
      if (body === undefined && !['GET', 'HEAD'].includes(input.method)) {
        body = await input.arrayBuffer();
      }
    } else {
      url = String(input);
    }
    const target = labsUrl(url, base());
    if (!target) return nativeFetch(input as RequestInfo, init);
    const answer = (await bridge.request('fetch', {
      url: target.pathname + target.search,
      method: (method || 'GET').toUpperCase(),
      headers,
      body: await bodyOf(body, headers),
    })) as FetchAnswer;
    const nullBody = [101, 204, 205, 304].includes(answer.status);
    return new Response(nullBody ? null : answer.body, {
      status: answer.status < 200 || answer.status > 599 ? 502 : answer.status,
      statusText: answer.statusText,
      headers: answer.headers,
    });
  } as typeof fetch;
}

// ---------------------------------------------------------------------------
// Images
// ---------------------------------------------------------------------------

/**
 * Show labs-hosted images (visit photos) by fetching their bytes through the
 * bridge: the frame's img-src allows no labs address, only `blob:`. Returns a
 * stop function.
 */
export function proxyLabsImages(
  doc: Document,
  sandboxFetch: typeof fetch,
  base: () => string,
  concurrency = 6,
): () => void {
  const cache = new Map<string, Promise<string>>();
  const queue: (() => void)[] = [];
  let active = 0;
  const win = doc.defaultView as Window & typeof globalThis;

  function schedule<T>(job: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const start = () => {
        active += 1;
        job()
          .then(resolve, reject)
          .finally(() => {
            active -= 1;
            const next = queue.shift();
            if (next) next();
          });
      };
      if (active < concurrency) start();
      else queue.push(start);
    });
  }

  function blobUrlFor(href: string): Promise<string> {
    let hit = cache.get(href);
    if (!hit) {
      hit = schedule(async () => {
        const r = await sandboxFetch(href);
        if (!r.ok) throw new Error(`image ${r.status}`);
        const blob = await r.blob();
        return win.URL.createObjectURL(blob);
      });
      cache.set(href, hit);
      // A failure is not remembered: the next draw may try again.
      hit.catch(() => cache.delete(href));
    }
    return hit;
  }

  function adopt(img: HTMLImageElement) {
    const raw = img.getAttribute('src');
    if (!raw || raw.startsWith('blob:') || raw.startsWith('data:')) return;
    const target = labsUrl(raw, base());
    if (!target) return;
    const href = target.pathname + target.search;
    img.setAttribute('data-labs-src', raw);
    // Nothing to load until the bytes arrive (and nothing for the CSP to refuse).
    img.removeAttribute('src');
    blobUrlFor(href).then(
      (blobUrl) => {
        // Still the image it was when we started (React may have moved on).
        if (img.getAttribute('data-labs-src') === raw) img.src = blobUrl;
      },
      () => {
        if (img.getAttribute('data-labs-src') === raw) {
          img.dispatchEvent(new Event('error'));
        }
      },
    );
  }

  function scan(node: Node) {
    if (node.nodeType !== 1) return;
    const el = node as Element;
    if (el.tagName === 'IMG') adopt(el as HTMLImageElement);
    el.querySelectorAll?.('img[src]').forEach((i) =>
      adopt(i as HTMLImageElement),
    );
  }

  const observer = new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === 'attributes' && r.target.nodeName === 'IMG') {
        adopt(r.target as HTMLImageElement);
      } else {
        r.addedNodes.forEach(scan);
      }
    }
  });
  observer.observe(doc.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['src'],
  });
  scan(doc.documentElement);
  return () => observer.disconnect();
}

// ---------------------------------------------------------------------------
// Downloads: `a.download` + a blob/data URL, the usual "Export CSV"
// ---------------------------------------------------------------------------

/** Remember the Blob behind each object URL, so a download link can be read back. */
export function trackObjectUrls(
  win: Window & typeof globalThis,
): Map<string, Blob> {
  const blobs = new Map<string, Blob>();
  const original = win.URL.createObjectURL.bind(win.URL);
  const revoke = win.URL.revokeObjectURL.bind(win.URL);
  win.URL.createObjectURL = (obj: Blob | MediaSource) => {
    const url = original(obj);
    if (obj instanceof Blob) blobs.set(url, obj);
    return url;
  };
  win.URL.revokeObjectURL = (url: string) => {
    // Kept a moment: render code often revokes straight after click().
    setTimeout(() => blobs.delete(url), 30_000);
    revoke(url);
  };
  return blobs;
}

function dataUrlBytes(
  href: string,
): { bytes: Uint8Array; mime: string } | null {
  const m = /^data:([^;,]*)(;base64)?,(.*)$/s.exec(href);
  if (!m) return null;
  const mime = m[1] || 'text/plain';
  if (m[2]) {
    const bin = atob(m[3]);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return { bytes, mime };
  }
  return { bytes: new TextEncoder().encode(decodeURIComponent(m[3])), mime };
}

/** The bytes behind a download link, or null when it is not a blob/data link. */
export async function downloadBytes(
  href: string,
  blobs: Map<string, Blob>,
): Promise<Uint8Array | null> {
  if (href.startsWith('blob:')) {
    const blob = blobs.get(href);
    return blob ? new Uint8Array(await blob.arrayBuffer()) : null;
  }
  if (href.startsWith('data:')) return dataUrlBytes(href)?.bytes ?? null;
  return null;
}

// ---------------------------------------------------------------------------
// The address, and confirm(): what ./rewrite.ts compiles render code to reach
// ---------------------------------------------------------------------------

/** The host page's query and hash as the frame has set them (history, a hash). */
export interface AddressState {
  search: string;
  hash: string;
}

/** `resolveHref` from ./frame, here to keep this module free of a cycle. */
function resolveAgainst(raw: string, base: string): string {
  try {
    return new URL(raw, base).href;
  } catch {
    return '';
  }
}

/**
 * What render code compiled for the frame reaches as `__labsSandbox` (a parameter
 * of its wrapper, renderCode.tsx): `location`, read off the HOST page's address and
 * turning every change into a request the host checks, and `confirm`, the host's
 * own question.
 */
export function sandboxGlobals(
  bridge: Bridge,
  pageUrl: () => string,
  address: AddressState,
): {
  location: Record<string, unknown>;
  confirm: (text?: unknown) => Promise<boolean>;
} {
  const current = (): URL => {
    let u: URL;
    try {
      u = new URL(pageUrl());
    } catch {
      u = new URL('about:blank');
    }
    u.search = address.search;
    u.hash = address.hash;
    return u;
  };
  const go = (url: unknown) => {
    const href = resolveAgainst(String(url), current().href) || String(url);
    bridge
      .request('navigate', { url: href, newTab: false })
      .catch((e) => console.warn('[workflow] navigation refused:', e.message));
  };
  const withPart = (part: 'pathname' | 'search', value: unknown) => {
    const u = current();
    u[part] = String(value);
    go(u.href);
  };
  const setHash = (value: unknown) => {
    const raw = String(value ?? '');
    address.hash =
      raw === '' || raw === '#' ? '' : raw.startsWith('#') ? raw : '#' + raw;
    bridge
      .request('setLocation', {
        search: address.search,
        hash: address.hash,
        push: true,
      })
      .catch((e) =>
        console.warn('[workflow] address change refused:', e.message),
      );
  };
  const read = (part: keyof URL) => () => String(current()[part]);
  const location: Record<string, unknown> = {};
  Object.defineProperties(location, {
    href: { get: read('href'), set: go, enumerable: true },
    pathname: {
      get: read('pathname'),
      set: (v: unknown) => withPart('pathname', v),
      enumerable: true,
    },
    search: {
      get: read('search'),
      set: (v: unknown) => withPart('search', v),
      enumerable: true,
    },
    hash: { get: read('hash'), set: setHash, enumerable: true },
    origin: { get: read('origin'), enumerable: true },
    protocol: { get: read('protocol'), enumerable: true },
    host: { get: read('host'), enumerable: true },
    hostname: { get: read('hostname'), enumerable: true },
    port: { get: read('port'), enumerable: true },
    assign: { value: go },
    replace: { value: go },
    // The address without its hash: navigating to the same address WITH one is a
    // same-page jump, not a reload.
    reload: {
      value: () => {
        const u = current();
        u.hash = '';
        go(u.href);
      },
    },
    toString: { value: () => current().href },
    valueOf: { value: () => current().href },
  });
  const holder = {
    confirm: (text?: unknown) =>
      bridge
        .request('confirm', {
          text: String(text ?? '').slice(0, 2_000) || 'Are you sure?',
        })
        .then(Boolean, () => false),
  } as {
    location: Record<string, unknown>;
    confirm: (text?: unknown) => Promise<boolean>;
  };
  // `__labsSandbox.location = url` (from `window.location = url`) navigates.
  Object.defineProperty(holder, 'location', {
    get: () => location,
    set: go,
    enumerable: true,
  });
  return holder;
}

// ---------------------------------------------------------------------------
// EventSource: a stream the host reads, as the viewer, for a route it allows
// ---------------------------------------------------------------------------

/** The bridge, plus the stream messages the host pushes on the same port. */
export interface StreamingBridge extends Bridge {
  listen?(stream: string, onMessage: (m: StreamMessage) => void): () => void;
}

/**
 * `EventSource` in the frame. The frame has no network to labs, so the HOST opens
 * the stream (bridge `streamOpen`, checked against ./routes.ts as a GET, made with
 * the viewer's session and the same `X-Labs-Sandbox-*` headers as a fetch) and
 * pushes each event back over the port. One difference from a browser's: it does
 * not reconnect. When the stream ends or fails the source is CLOSED and `error`
 * fires, which is what render code that streams already handles (it closes, or
 * falls back to a plain fetch).
 */
export function makeEventSource(
  bridge: StreamingBridge,
  base: () => string,
): typeof EventSource {
  type Listener = EventListenerOrEventListenerObject;
  // Its own listener list, not EventTarget's: some EventTargets also call `on<type>`
  // themselves, and the handler would run twice.
  class SandboxEventSource {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSED = 2;
    readonly CONNECTING = 0;
    readonly OPEN = 1;
    readonly CLOSED = 2;
    readonly url: string;
    readonly withCredentials: boolean;
    readyState = 0;
    onopen: ((ev: Event) => unknown) | null = null;
    onmessage: ((ev: MessageEvent) => unknown) | null = null;
    onerror: ((ev: Event) => unknown) | null = null;
    private stream: string | null = null;
    private unlisten: (() => void) | null = null;
    private listeners = new Map<string, Listener[]>();

    constructor(url: string | URL, init?: { withCredentials?: boolean }) {
      const target = labsUrl(String(url), base());
      this.url = target ? target.href : String(url);
      this.withCredentials = Boolean(init?.withCredentials);
      if (!target || !bridge.listen) {
        // Another host: the frame's connect-src refuses it, as a browser's would.
        setTimeout(() => this.fail(), 0);
        return;
      }
      bridge
        .request('streamOpen', { url: target.pathname + target.search })
        .then(
          (answer) => {
            const id = (answer as { stream?: unknown })?.stream;
            if (typeof id !== 'string') return this.fail();
            if (this.readyState === 2) {
              bridge
                .request('streamClose', { stream: id })
                .catch(() => undefined);
              return;
            }
            this.stream = id;
            this.unlisten = bridge.listen!(id, (m) => this.receive(m));
          },
          (e) => {
            console.warn('[workflow] stream refused:', e.message);
            this.fail();
          },
        );
    }

    addEventListener(type: string, listener: Listener | null) {
      if (!listener) return;
      const list = this.listeners.get(type) || [];
      if (!list.includes(listener)) list.push(listener);
      this.listeners.set(type, list);
    }

    removeEventListener(type: string, listener: Listener | null) {
      const list = this.listeners.get(type) || [];
      this.listeners.set(
        type,
        list.filter((l) => l !== listener),
      );
    }

    dispatchEvent(event: Event): boolean {
      for (const l of [...(this.listeners.get(event.type) || [])]) {
        try {
          if (typeof l === 'function') l.call(this, event);
          else l.handleEvent(event);
        } catch (e) {
          setTimeout(() => {
            throw e;
          }, 0);
        }
      }
      return true;
    }

    private emit(event: Event) {
      this.dispatchEvent(event);
      const handler = (this as unknown as Record<string, unknown>)[
        'on' + event.type
      ];
      if (typeof handler !== 'function') return;
      try {
        handler.call(this, event);
      } catch (e) {
        // As a browser does: reported, and the stream goes on.
        setTimeout(() => {
          throw e;
        }, 0);
      }
    }

    private receive(m: StreamMessage) {
      if (this.readyState === 2) return;
      if (m.kind === 'open') {
        this.readyState = 1;
        this.emit(new Event('open'));
        return;
      }
      if (m.kind === 'event') {
        this.emit(
          new MessageEvent(m.name || 'message', {
            data: m.data ?? '',
            lastEventId: m.lastEventId ?? '',
            origin: new URL(this.url).origin,
          }),
        );
        return;
      }
      this.fail();
    }

    private fail() {
      if (this.readyState === 2) return;
      this.release();
      this.emit(new Event('error'));
    }

    private release() {
      this.readyState = 2;
      if (this.unlisten) this.unlisten();
      this.unlisten = null;
    }

    close() {
      if (this.readyState === 2) return;
      const id = this.stream;
      this.release();
      if (id)
        bridge.request('streamClose', { stream: id }).catch(() => undefined);
    }
  }
  return SandboxEventSource as unknown as typeof EventSource;
}

// ---------------------------------------------------------------------------
// The rest of the window
// ---------------------------------------------------------------------------

export function installWorkflowShims(
  win: Window & typeof globalThis,
  bridge: StreamingBridge,
  pageUrl: () => string,
  /** The host page's query and hash as the frame last set them (shared with the
   * `location` render code is compiled to reach, sandboxGlobals). */
  address: AddressState = { search: '', hash: '' },
): { sandboxFetch: typeof fetch; blobs: Map<string, Blob> } {
  const doc = win.document;
  const nativeFetch = win.fetch.bind(win);
  const base = () => win.location.href;
  const sandboxFetch = makeSandboxFetch(bridge, base, nativeFetch);
  const define = (obj: object, name: string, value: unknown) => {
    try {
      Object.defineProperty(obj, name, {
        value,
        configurable: true,
        writable: true,
      });
    } catch {
      // Not replaceable in this browser; render code touching it sees the original.
    }
  };

  define(win, 'fetch', sandboxFetch);
  // The frame has no network to labs to stream over: the host reads the stream.
  define(win, 'EventSource', makeEventSource(bridge, base));

  try {
    Object.defineProperty(win.Document.prototype, 'cookie', {
      configurable: true,
      get: () => '',
      set: () => undefined,
    });
  } catch {
    // ignore
  }

  define(win, 'alert', (message?: unknown) => {
    bridge
      .request('notify', { message: String(message ?? '') })
      .catch(() => undefined);
  });

  // A question code waits for synchronously cannot be the host's (it answers
  // later). ./rewrite.ts compiled the ones it could make wait; any left answer No
  // -- the safe answer, since whatever they guard does not happen -- and say so.
  const unanswerable = (kind: string) => (message?: unknown) => {
    bridge
      .request('notify', {
        message:
          `A question this code asked cannot be waited for in the sandbox, so it was answered ${kind}: ` +
          String(message ?? '').slice(0, 500),
      })
      .catch(() => undefined);
    return kind === 'No' ? false : null;
  };
  define(win, 'confirm', unanswerable('No'));
  define(win, 'prompt', unanswerable('with nothing'));

  define(win, 'open', (url?: string | URL) => {
    if (url) {
      bridge
        .request('navigate', { url: String(url), newTab: true })
        .catch((e) =>
          console.warn('[workflow] window.open refused:', e.message),
        );
    }
    return null;
  });

  // The page's address: query and hash only, on the HOST's URL. The frame's own
  // URL follows (same path), so `location.search` keeps reading what was set.
  for (const name of ['pushState', 'replaceState'] as const) {
    const original = win.history[name].bind(win.history);
    define(
      win.history,
      name,
      (state: unknown, title: string, url?: string | URL | null) => {
        if (url === undefined || url === null) {
          try {
            original(state, title);
          } catch {
            // ignore
          }
          return;
        }
        let target: URL;
        try {
          target = new URL(String(url), pageUrl());
        } catch {
          return;
        }
        address.search = target.search;
        address.hash = target.hash;
        bridge
          .request('setLocation', {
            search: target.search,
            hash: target.hash,
            push: name === 'pushState',
          })
          .catch((e) =>
            console.warn('[workflow] address change refused:', e.message),
          );
        try {
          original(
            state,
            title,
            win.location.pathname + target.search + target.hash,
          );
        } catch {
          // The browser refused it in an opaque origin; the host's URL still moved.
        }
      },
    );
  }

  const clipboard = {
    writeText: (text: string) =>
      bridge.request('copyText', { text: String(text) }).then(() => undefined),
    readText: () => Promise.reject(new Error('not available in a workflow')),
  };
  define(win.navigator, 'clipboard', clipboard);

  // Some render code reads the CSRF token from the DOM before a fetch. The host
  // replaces whatever is sent with its own, so this one carries nothing.
  if (!doc.querySelector('[name=csrfmiddlewaretoken]')) {
    const input = doc.createElement('input');
    input.type = 'hidden';
    input.name = 'csrfmiddlewaretoken';
    input.value = 'sandboxed';
    doc.body.appendChild(input);
  }

  const blobs = trackObjectUrls(win);

  return { sandboxFetch, blobs };
}
