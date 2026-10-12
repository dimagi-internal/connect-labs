/**
 * The host side of the page sandbox: the trusted labs page that draws a page's
 * frame, passes it its data, and answers a short allowlist of requests from it, as
 * the viewer, with the checks labs already makes.
 * Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md.
 *
 * Nothing the frame sends is trusted. Every request is validated (method, types,
 * sizes, rate); anything that leaves the frame -- a navigation, the clipboard, a
 * download -- needs the person's own click (user activation on THIS page).
 */

import {
  DOWNLOAD_TYPES,
  HELLO,
  INIT,
  LIMITS,
  NAVIGATE_DENIED,
  NAVIGATE_PATTERNS,
  NAVIGATE_PREFIXES,
  NOT_ON_A_PAGE,
  PHASE1_METHODS,
  PHASE2_METHODS,
  SHELL_PATH,
  UPDATE,
  type BridgeRequest,
  type BridgeResponse,
  type FramePayload,
} from './protocol';

export class BridgeError extends Error {}

// ---------------------------------------------------------------------------
// Pure checks
// ---------------------------------------------------------------------------

/**
 * Where a navigate request may go: a labs page on the allowlist, on labs' own
 * origin and scheme, with no credentials in the URL. Returns the URL to use (the
 * parsed `href`, never the string the frame sent), or throws.
 */
export function checkNavigate(url: unknown, labsOrigin: string): string {
  if (typeof url !== 'string' || !url || url.length > LIMITS.urlLength) {
    throw new BridgeError('navigate: url must be a short string');
  }
  // Control characters and backslashes are how a URL parser is talked into a
  // different host than the one a reader sees; a labs link never has them.
  if (/[\u0000-\u001f\u007f\\]/.test(url)) {
    throw new BridgeError('navigate: that address is not a labs page');
  }
  let parsed: URL;
  let base: URL;
  try {
    base = new URL(labsOrigin);
    parsed = new URL(url, base.origin);
  } catch {
    throw new BridgeError('navigate: that address is not a labs page');
  }
  if (
    parsed.origin !== base.origin ||
    parsed.protocol !== base.protocol ||
    parsed.username ||
    parsed.password
  ) {
    throw new BridgeError('navigate: only labs pages can be opened');
  }
  const path = parsed.pathname;
  if (NAVIGATE_DENIED.some((p) => path.startsWith(p))) {
    throw new BridgeError(
      'navigate: that labs address cannot be opened from a page',
    );
  }
  const allowed =
    NAVIGATE_PREFIXES.some((p) => path.startsWith(p)) ||
    NAVIGATE_PATTERNS.some((re) => re.test(path));
  if (!allowed) {
    throw new BridgeError(
      'navigate: that labs address cannot be opened from a page',
    );
  }
  return parsed.href;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return (
    typeof v === 'object' &&
    v !== null &&
    Object.getPrototypeOf(v) === Object.prototype
  );
}

/**
 * The selection a page shares with the agent, rebuilt by the host: strings only,
 * count and length capped. The frame names what is on screen; it never touches the
 * page state's filters, path, resource or backing tool.
 */
export function sanitizeSelection(params: unknown): {
  visible_ids: string[];
  drilled: Record<string, string | number | boolean | null>;
} {
  const p = isPlainObject(params) ? params : {};
  const ids = Array.isArray(p.visible_ids) ? p.visible_ids : [];
  const visible_ids = ids
    .filter((i) => typeof i === 'string' || typeof i === 'number')
    .map((i) => String(i).slice(0, LIMITS.idLength))
    .slice(0, LIMITS.visibleIds);
  const drilled: Record<string, string | number | boolean | null> = {};
  if (isPlainObject(p.drilled)) {
    for (const [k, v] of Object.entries(p.drilled).slice(
      0,
      LIMITS.drilledKeys,
    )) {
      const key = String(k).slice(0, 64);
      if (v === null) drilled[key] = null;
      else if (typeof v === 'boolean') drilled[key] = v;
      else if (typeof v === 'number' && Number.isFinite(v)) drilled[key] = v;
      else if (typeof v === 'string')
        drilled[key] = v.slice(0, LIMITS.drilledValueLength);
    }
  }
  return { visible_ids, drilled };
}

function alias(params: Record<string, unknown>): string {
  const a = params.alias;
  if (typeof a !== 'string' || !a || a.length > LIMITS.aliasLength) {
    throw new BridgeError('alias must be a short string');
  }
  return a;
}

function bytesOf(value: unknown): number {
  try {
    return new TextEncoder().encode(JSON.stringify(value) ?? '').length;
  } catch {
    return Infinity;
  }
}

/** A download, as the host will save it: allowlisted extension, a type of labs' choosing. */
export function checkDownload(params: Record<string, unknown>): {
  filename: string;
  mime: string;
  bytes: Uint8Array;
} {
  const raw = typeof params.filename === 'string' ? params.filename : '';
  const filename = raw
    .replace(/[\u0000-\u001f\u007f/\\:*?"<>|]/g, '_')
    .slice(0, LIMITS.filenameLength);
  const ext = (filename.split('.').pop() || '').toLowerCase();
  if (!filename || !(ext in DOWNLOAD_TYPES) || !filename.includes('.')) {
    throw new BridgeError(
      `download: a filename ending ${Object.keys(DOWNLOAD_TYPES)
        .map((e) => '.' + e)
        .join(', ')}`,
    );
  }
  let bytes: Uint8Array;
  const b = params.bytes;
  if (typeof b === 'string') bytes = new TextEncoder().encode(b);
  else if (b instanceof Uint8Array) bytes = b;
  else if (b instanceof ArrayBuffer) bytes = new Uint8Array(b);
  else throw new BridgeError('download: bytes must be a string or bytes');
  if (bytes.byteLength > LIMITS.downloadBytes) {
    throw new BridgeError('download: that file is too large');
  }
  return { filename, mime: DOWNLOAD_TYPES[ext], bytes };
}

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------

export interface HostHandlers {
  querySupply(alias: string, args: Record<string, unknown>): Promise<unknown>;
  queryWorkflow(alias: string): Promise<unknown>;
  navigate(href: string, newTab: boolean): void;
  shareSelection(selection: ReturnType<typeof sanitizeSelection>): void;
  resize(height: number): void;
  copyText(text: string): Promise<void>;
  confirm(text: string): Promise<boolean>;
  download(file: { filename: string; mime: string; bytes: Uint8Array }): void;
  reportError(message: string): void;
}

export interface DispatchOptions {
  labsOrigin: string;
  /** User activation on the HOST page when the request arrives. */
  hasActivation: () => boolean;
  now?: () => number;
}

/** Answer one frame request at a time: `(request) => response`, never throwing. */
export function createDispatcher(
  handlers: HostHandlers,
  options: DispatchOptions,
): (request: unknown) => Promise<BridgeResponse | null> {
  const now = options.now || (() => Date.now());
  const recent: number[] = [];
  let inFlight = 0;

  function needsActivation(method: string) {
    if (!options.hasActivation()) {
      throw new BridgeError(`${method} needs a click on the page first`);
    }
  }

  async function run(method: string, params: Record<string, unknown>) {
    switch (method) {
      case 'querySupply':
      case 'queryWorkflow': {
        if (inFlight >= LIMITS.inFlightQueries) {
          throw new BridgeError('too many queries at once');
        }
        const a = alias(params);
        // The query is always THIS page's: the frame cannot name a definition, an
        // opportunity or a programme. Only `args` (a plain object) passes through.
        const args = isPlainObject(params.args) ? params.args : {};
        inFlight += 1;
        try {
          const result =
            method === 'querySupply'
              ? await handlers.querySupply(a, args)
              : await handlers.queryWorkflow(a);
          if (bytesOf(result) > LIMITS.resultBytes) {
            throw new BridgeError(
              `${method}: the answer is too large for a page`,
            );
          }
          return JSON.parse(JSON.stringify(result ?? null));
        } finally {
          inFlight -= 1;
        }
      }
      case 'navigate': {
        needsActivation('navigate');
        const href = checkNavigate(params.url, options.labsOrigin);
        handlers.navigate(href, params.newTab === true);
        return true;
      }
      case 'shareSelection':
        handlers.shareSelection(sanitizeSelection(params));
        return true;
      case 'resize': {
        const h = Number(params.height);
        if (!Number.isFinite(h))
          throw new BridgeError('resize: height must be a number');
        const clamped = Math.round(
          Math.min(LIMITS.maxHeight, Math.max(LIMITS.minHeight, h)),
        );
        handlers.resize(clamped);
        return clamped;
      }
      case 'copyText': {
        needsActivation('copyText');
        const text = params.text;
        if (typeof text !== 'string' || text.length > LIMITS.copyTextLength) {
          throw new BridgeError(
            'copyText: text must be a string, not too long',
          );
        }
        await handlers.copyText(text);
        return true;
      }
      case 'confirm': {
        const text = params.text;
        if (typeof text !== 'string' || !text) {
          throw new BridgeError('confirm: text must be a string');
        }
        return Boolean(
          await handlers.confirm(text.slice(0, LIMITS.confirmTextLength)),
        );
      }
      case 'download': {
        needsActivation('download');
        handlers.download(checkDownload(params));
        return true;
      }
      case 'reportError': {
        const message =
          typeof params.message === 'string' ? params.message : '';
        handlers.reportError(
          message.slice(0, LIMITS.errorLength) || 'The page failed to draw.',
        );
        return true;
      }
      default:
        throw new BridgeError(
          `unknown method ${JSON.stringify(method).slice(0, 80)}`,
        );
    }
  }

  return async (request: unknown) => {
    if (!isPlainObject(request)) return null;
    const { id, method } = request as unknown as BridgeRequest;
    if (typeof id !== 'number' && typeof id !== 'string') return null;
    try {
      if (typeof method !== 'string')
        throw new BridgeError('method must be a string');
      if ((PHASE2_METHODS as readonly string[]).includes(method)) {
        throw new BridgeError(`${method} is ${NOT_ON_A_PAGE}`);
      }
      if (!(PHASE1_METHODS as readonly string[]).includes(method)) {
        throw new BridgeError(
          `unknown method ${JSON.stringify(method).slice(0, 80)}`,
        );
      }
      const t = now();
      while (recent.length && recent[0] < t - LIMITS.rateWindowMs)
        recent.shift();
      // Resizes come with every layout change; they are not counted against the page.
      if (method !== 'resize') {
        if (recent.length >= LIMITS.rateCount) {
          throw new BridgeError(
            'too many requests from this page; wait a moment',
          );
        }
        recent.push(t);
      }
      const params = request.params === undefined ? {} : request.params;
      if (
        !isPlainObject(params) ||
        // A download's bytes are sized by checkDownload, against its own cap.
        (method !== 'download' && bytesOf(params) > LIMITS.paramsBytes)
      ) {
        throw new BridgeError('params must be a small plain object');
      }
      return { id, result: await run(method, params) };
    } catch (e) {
      const message =
        e instanceof BridgeError
          ? e.message
          : `${String(request.method)} failed: ${e instanceof Error ? e.message : String(e)}`;
      return { id, error: message.slice(0, LIMITS.errorLength) };
    }
  };
}

// ---------------------------------------------------------------------------
// The handshake
// ---------------------------------------------------------------------------

/**
 * Whether a window message is the frame's hello: from THIS iframe's window, from an
 * opaque origin, and only once.
 */
export function isFrameHello(
  event: Pick<MessageEvent, 'source' | 'origin' | 'data'>,
  frameWindow: unknown,
): boolean {
  return (
    !!frameWindow &&
    event.source === frameWindow &&
    event.origin === 'null' &&
    isPlainObject(event.data) &&
    (event.data as { type?: unknown }).type === HELLO
  );
}

export interface SandboxHostOptions {
  container: HTMLElement;
  labsOrigin: string;
  payload: FramePayload;
  handlers: Omit<HostHandlers, 'resize'>;
  /** The frame navigated: it tried to leave. Called once; the host has stopped. */
  onLeave: () => void;
  hasActivation?: () => boolean;
  win?: Window;
}

export interface SandboxHost {
  iframe: HTMLIFrameElement;
  update(payload: FramePayload): void;
  destroy(): void;
}

function defaultActivation(win: Window): () => boolean {
  return () => {
    const ua = (
      win.navigator as Navigator & {
        userActivation?: { isActive: boolean };
      }
    ).userActivation;
    // No API, no activation: fail closed.
    return !!ua && ua.isActive === true;
  };
}

/** Draw the frame and answer it. The listener is attached BEFORE src is set. */
export function createSandboxHost(options: SandboxHostOptions): SandboxHost {
  const win = options.win || window;
  const doc = options.container.ownerDocument;
  const iframe = doc.createElement('iframe');
  iframe.id = 'page-sandbox';
  // allow-scripts only: no same-origin, forms, popups, modals, downloads or top
  // navigation. The shell's own CSP `sandbox` says the same, so either alone holds.
  iframe.setAttribute('sandbox', 'allow-scripts');
  iframe.setAttribute('referrerpolicy', 'strict-origin');
  iframe.setAttribute('title', 'Page');
  iframe.style.width = '100%';
  iframe.style.border = '0';
  iframe.style.display = 'block';
  iframe.style.height = `${LIMITS.minHeight * 4}px`;

  let payload = options.payload;
  let port: MessagePort | null = null;
  let greeted = false;
  let loads = 0;
  let stopped = false;

  const dispatch = createDispatcher(
    {
      ...options.handlers,
      resize: (h) => {
        iframe.style.height = `${h}px`;
      },
    },
    {
      labsOrigin: options.labsOrigin,
      hasActivation: options.hasActivation || defaultActivation(win),
    },
  );

  function stop() {
    stopped = true;
    if (port) port.close();
    port = null;
    win.removeEventListener('message', onMessage);
  }

  function onMessage(event: MessageEvent) {
    if (stopped || greeted) return;
    if (!isFrameHello(event, iframe.contentWindow)) return;
    greeted = true;
    const channel = new MessageChannel();
    port = channel.port1;
    port.onmessage = async (e: MessageEvent) => {
      const response = await dispatch(e.data);
      if (response && port) port.postMessage(response);
    };
    // '*': an opaque origin cannot be named. The frame-src policy on this page keeps
    // the iframe on the shell, and a second load stops the bridge before anything else.
    iframe.contentWindow?.postMessage({ type: INIT, payload }, '*', [
      channel.port2,
    ]);
  }

  function onLoad() {
    loads += 1;
    if (loads > 1 && !stopped) {
      // The frame navigated itself: the page is not redrawn there.
      stop();
      iframe.remove();
      options.onLeave();
    }
  }

  win.addEventListener('message', onMessage);
  iframe.addEventListener('load', onLoad);
  // The shell is always labs' own fixed path, whatever the page data says.
  iframe.src = SHELL_PATH;
  options.container.appendChild(iframe);

  return {
    iframe,
    update(next: FramePayload) {
      payload = next;
      if (port && !stopped) port.postMessage({ type: UPDATE, payload: next });
    },
    destroy() {
      stop();
      iframe.removeEventListener('load', onLoad);
      iframe.remove();
    },
  };
}
