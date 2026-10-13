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
 * - `EventSource`               -> absent, so render code takes its plain-fetch path
 * - `document.cookie`           -> "" (reading it throws in an opaque origin)
 * - `alert(text)`               -> bridge `notify`, drawn by the host as text
 * - `window.open(url)`          -> bridge `navigate` in a new tab
 * - `history.pushState/replace` -> bridge `setLocation` on the host's own URL
 * - `navigator.clipboard`       -> bridge `copyText`
 * - a click on `<a download>`   -> bridge `download` with the blob's bytes
 *
 * Not shimmed: `window.location = ...`. The Navigation API that could cancel it is
 * disabled in an opaque-origin document, so the frame navigates, the host's
 * frame-src refuses the destination, and the host stops the frame ("tried to
 * leave"). Render code uses `actions.navigate(url)` instead.
 */

import type { Bridge } from './frame';

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
  throw new TypeError(
    'In the sandbox a request body must be text, URLSearchParams or bytes',
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
// The rest of the window
// ---------------------------------------------------------------------------

export function installWorkflowShims(
  win: Window & typeof globalThis,
  bridge: Bridge,
  pageUrl: () => string,
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
  // Render code that streams prefers EventSource and falls back to a plain fetch
  // when there is none; the frame has no network to labs to stream over.
  define(win, 'EventSource', undefined);

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
