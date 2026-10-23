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
  NAVIGATE_DENIED_PARAMS,
  NAVIGATE_PATTERNS,
  NAVIGATE_PREFIXES,
  NOT_ON_A_PAGE,
  PHASE1_METHODS,
  RATE,
  SHELL_PATH,
  STORAGE_AREAS,
  STREAM,
  UPDATE,
  VIEWPORT,
  WORKFLOW_METHODS,
  WORKFLOW_NAVIGATE_DENIED,
  WORKFLOW_NAVIGATE_PATTERNS,
  WORKFLOW_NAVIGATE_PREFIXES,
  type BridgeRequest,
  type BridgeResponse,
  type FramePayload,
  type HostViewport,
  type StorageArea,
  type StorageBatch,
  type StorageSeed,
  type StreamMessage,
} from './protocol';
import {
  RouteError,
  checkFetch,
  cleanHeaders,
  type CheckedRequest,
  type RouteContext,
} from './routes';

/** 'page' / 'workflow': what the frame draws (a page has no run). */
export type SandboxMode = 'page' | 'workflow';
type NavigateSet = SandboxMode | 'full';

export class BridgeError extends Error {}

// ---------------------------------------------------------------------------
// Pure checks
// ---------------------------------------------------------------------------

/**
 * Where a navigate request may go: a labs page on the allowlist, on labs' own
 * origin and scheme, with no credentials in the URL. Returns the URL to use (the
 * parsed `href`, never the string the frame sent), or throws.
 */
export function checkNavigate(
  url: unknown,
  labsOrigin: string,
  mode: NavigateSet = 'page',
): string {
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
  let allowed =
    NAVIGATE_PREFIXES.some((p) => path.startsWith(p)) ||
    NAVIGATE_PATTERNS.some((re) => re.test(path));
  // `full`: a host that answers the full method set (a workflow, or a page since
  // pages became as definable as workflows) may also lead to these.
  if (!allowed && mode !== 'page') {
    allowed =
      (WORKFLOW_NAVIGATE_PREFIXES.some((p) => path.startsWith(p)) ||
        WORKFLOW_NAVIGATE_PATTERNS.some((re) => re.test(path))) &&
      !WORKFLOW_NAVIGATE_DENIED.some((p) => path.startsWith(p));
  }
  if (!allowed) {
    throw new BridgeError(
      'navigate: that labs address cannot be opened from a page',
    );
  }
  const params = Array.from(parsed.searchParams.keys()).map((k) =>
    k.toLowerCase(),
  );
  // A sign-in start ("Authorize CommCare") comes back to where the person was: its
  // `next` is allowed when it is itself a labs address this check would open (with
  // no second hop of its own), so it can lead nowhere a link could not.
  const signIn =
    mode !== 'page' && SIGN_IN_STARTS.some((p) => path.startsWith(p));
  for (const k of params) {
    if (!NAVIGATE_DENIED_PARAMS.includes(k)) continue;
    if (signIn && k === 'next' && nextIsLabsPage(parsed, labsOrigin, mode)) {
      continue;
    }
    throw new BridgeError(
      'navigate: that labs address cannot be opened from a page',
    );
  }
  return parsed.href;
}

/** Sign-in starts whose `next` may name where to come back to. */
export const SIGN_IN_STARTS = [
  '/labs/ocs/initiate/',
  '/labs/commcare/initiate/',
];

function nextIsLabsPage(
  parsed: URL,
  labsOrigin: string,
  mode: NavigateSet,
): boolean {
  const all = parsed.searchParams.getAll('next');
  if (all.length !== 1) return false;
  const next = all[0];
  // A path on labs, never a scheme-relative or absolute address.
  if (!next.startsWith('/') || next.startsWith('//')) return false;
  try {
    const back = checkNavigate(next, labsOrigin, mode);
    return !SIGN_IN_STARTS.some((p) => new URL(back).pathname.startsWith(p));
  } catch {
    return false;
  }
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
// Workflow fetches (phase 2)
// ---------------------------------------------------------------------------

/** What the frame gets back for a fetch: enough to build a Response. */
// ---------------------------------------------------------------------------
// Storage: what render code keeps in localStorage / sessionStorage
// ---------------------------------------------------------------------------

/** Every key a sandboxed page or workflow keeps on the host starts with this. */
export const STORAGE_PREFIX = 'labs-sandbox-storage:';

/** A `storage` request, checked: the area, and only strings, within the limits. */
export function checkStorageBatch(
  params: Record<string, unknown>,
): StorageBatch {
  const area = params.area as StorageArea;
  if (!STORAGE_AREAS.includes(area)) {
    throw new BridgeError('storage: area must be local or session');
  }
  const key = (k: unknown) => {
    if (typeof k !== 'string' || k.length > LIMITS.storageKeyLength) {
      throw new BridgeError('storage: a key must be a short string');
    }
    return k;
  };
  // No prototype: `__proto__` is a key like any other.
  const set: Record<string, string> = Object.create(null);
  if (params.set !== undefined) {
    if (!isPlainObject(params.set)) {
      throw new BridgeError('storage: set must be an object');
    }
    for (const [k, v] of Object.entries(params.set)) {
      if (typeof v !== 'string') {
        throw new BridgeError('storage: a value must be a string');
      }
      set[key(k)] = v;
    }
  }
  const remove: string[] = [];
  if (params.remove !== undefined) {
    if (
      !Array.isArray(params.remove) ||
      params.remove.length > LIMITS.storageKeys
    ) {
      throw new BridgeError('storage: remove must be a list of keys');
    }
    for (const k of params.remove) remove.push(key(k));
  }
  return { area, clear: params.clear === true, set, remove };
}

function storedBytes(entries: Iterable<[string, string]>): number {
  let n = 0;
  for (const [k, v] of entries) n += k.length + v.length;
  return n;
}

/**
 * The host's own localStorage / sessionStorage, as ONE page or workflow sees it:
 * only keys under `labs-sandbox-storage:<scope>:`, with that prefix taken off. The
 * scope is the host's (the definition id it drew), never the frame's say, so a
 * frame reads and writes its own keys and nothing else of labs'. With no scope, or
 * where the host's storage throws, nothing is kept: the frame's in-memory copy is
 * all there is, as before.
 */
export function scopedStorage(win: Window, scope: string | null) {
  const prefix = scope ? `${STORAGE_PREFIX}${scope}:` : null;
  const areaOf = (name: StorageArea): Storage | null => {
    if (!prefix) return null;
    try {
      return name === 'local' ? win.localStorage : win.sessionStorage;
    } catch {
      return null;
    }
  };
  const entries = (s: Storage, start: string): [string, string][] => {
    const out: [string, string][] = [];
    for (let i = 0; i < s.length; i += 1) {
      const k = s.key(i);
      if (k === null || !k.startsWith(start)) continue;
      out.push([k, s.getItem(k) ?? '']);
    }
    return out;
  };
  return {
    seed(): StorageSeed {
      const seed: StorageSeed = {
        local: Object.create(null),
        session: Object.create(null),
      };
      for (const name of STORAGE_AREAS) {
        const s = areaOf(name);
        if (!s) continue;
        try {
          for (const [k, v] of entries(s, prefix!)) {
            seed[name][k.slice(prefix!.length)] = v;
          }
        } catch {
          // unreadable: start empty
        }
      }
      return seed;
    },
    apply(batch: StorageBatch): void {
      const s = areaOf(batch.area);
      if (!s) return;
      const own = new Map(
        entries(s, prefix!).map(([k, v]) => [k.slice(prefix!.length), v]),
      );
      const drop = batch.clear ? Array.from(own.keys()) : [];
      drop.push(...batch.remove);
      // Every limit is checked on the result before anything is written or
      // removed: a refused batch leaves what was kept as it was.
      for (const k of drop) own.delete(k);
      for (const [k, v] of Object.entries(batch.set)) own.set(k, v);
      if (own.size > LIMITS.storageKeys) {
        throw new BridgeError('storage: too many keys for one page');
      }
      const mine = storedBytes(own);
      if (mine > LIMITS.storageBytes) {
        throw new BridgeError('storage: too much stored for one page');
      }
      const others = storedBytes(
        entries(s, STORAGE_PREFIX).filter(([k]) => !k.startsWith(prefix!)),
      );
      if (others + mine > LIMITS.storageTotalBytes) {
        throw new BridgeError('storage: no room left in this browser');
      }
      try {
        for (const k of drop) s.removeItem(prefix + k);
        for (const [k, v] of Object.entries(batch.set))
          s.setItem(prefix + k, v);
      } catch {
        throw new BridgeError('storage: the browser has no room');
      }
    },
  };
}

export interface FetchAnswer {
  status: number;
  statusText: string;
  headers: Record<string, string>;
  body: ArrayBuffer;
}

/** The request body a workflow may send: text or bytes, size-capped. */
export function checkBody(body: unknown): string | Uint8Array | undefined {
  if (body === undefined || body === null) return undefined;
  if (typeof body === 'string') {
    if (body.length > LIMITS.fetchBodyBytes) {
      throw new BridgeError('fetch: that request is too large');
    }
    return body;
  }
  let bytes: Uint8Array | null = null;
  if (body instanceof Uint8Array) bytes = body;
  else if (body instanceof ArrayBuffer) bytes = new Uint8Array(body);
  if (!bytes) throw new BridgeError('fetch: a body must be text or bytes');
  if (bytes.byteLength > LIMITS.fetchBodyBytes) {
    throw new BridgeError('fetch: that request is too large');
  }
  return bytes;
}

/**
 * A response's body, refused as soon as it passes `max` bytes: read in chunks, so an
 * answer too large for a workflow never sits whole in the host page's memory.
 */
export async function readCapped(
  response: Response,
  max: number,
): Promise<ArrayBuffer> {
  const tooLarge = () =>
    new BridgeError('fetch: the answer is too large for a workflow');
  const declared = Number(response.headers.get('content-length'));
  if (Number.isFinite(declared) && declared > max) {
    response.body?.cancel().catch(() => undefined);
    throw tooLarge();
  }
  const reader = response.body?.getReader();
  if (!reader) {
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength > max) throw tooLarge();
    return buffer;
  }
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > max) {
      reader.cancel().catch(() => undefined);
      throw tooLarge();
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.byteLength;
  }
  return out.buffer;
}

/**
 * Make a checked request as the viewer, from THIS page: labs' own CSRF token, the
 * session cookie, no redirect followed off labs. Response headers are cut down to
 * the few render code reads.
 */
export async function proxyFetch(
  checked: CheckedRequest,
  init: { headers?: unknown; body?: unknown },
  csrfToken: string,
  fetchImpl: typeof fetch = fetch,
  /** Where a read was redirected to must pass the same check (routes.ts). */
  recheck?: (url: string) => CheckedRequest,
): Promise<FetchAnswer> {
  const headers = cleanHeaders(init.headers);
  // Which definition this request is made for: the server binds the runs it reads
  // to the ones that definition declares (workflow/declared_reads.py). Set here,
  // after the frame's own headers are cut to content-type and accept, so the frame
  // can neither set nor drop it.
  headers['x-labs-sandbox-for'] = checked.sandboxFor || 'none';
  if (checked.sandboxScope)
    headers['x-labs-sandbox-scope'] = checked.sandboxScope;
  const unsafe = !['GET', 'HEAD'].includes(checked.method);
  if (unsafe) headers['x-csrftoken'] = csrfToken;
  // A route that checked its body sends what it checked (canonical JSON).
  let body = unsafe
    ? checked.body !== undefined
      ? checked.body
      : checkBody(init.body)
    : undefined;
  // A body the route checked is sent as the JSON it was checked as, and says so: a
  // form content type would let Django read fields out of it the check never saw.
  if (unsafe && checked.body !== undefined) {
    headers['content-type'] = 'application/json';
  }
  // A form the frame built carries the dummy token it read off its own DOM; Django
  // reads a form's token before the header, so it is dropped and the header (this
  // page's own token) decides.
  if (
    typeof body === 'string' &&
    checked.body === undefined &&
    (headers['content-type'] || '')
      .toLowerCase()
      .startsWith('application/x-www-form-urlencoded')
  ) {
    const form = new URLSearchParams(body);
    if (form.has('csrfmiddlewaretoken')) {
      form.delete('csrfmiddlewaretoken');
      body = form.toString();
    }
  }
  const response = await fetchImpl(checked.href, {
    method: checked.method,
    headers,
    body: body as BodyInit | undefined,
    credentials: 'same-origin',
    // A read follows labs' own redirects (the context middleware adds the
    // session's opportunity to a bare URL), and where it lands is checked again
    // below. A write never follows one: a 307 would repeat it somewhere else.
    redirect: unsafe ? 'manual' : 'follow',
  });
  if (response.redirected) {
    let landed: CheckedRequest | null = null;
    try {
      landed = recheck ? recheck(response.url) : null;
    } catch {
      landed = null;
    }
    if (!landed || landed.gate !== 'read') {
      throw new BridgeError(
        'fetch: that address redirected somewhere a workflow cannot reach',
      );
    }
  }
  const buffer = await readCapped(response, LIMITS.fetchResultBytes);
  const out: Record<string, string> = {};
  for (const name of ['content-type', 'content-disposition', 'retry-after']) {
    const v = response.headers.get(name);
    if (v) out[name] = v;
  }
  return {
    status: response.type === 'opaqueredirect' ? 302 : response.status,
    statusText: response.statusText,
    headers: out,
    body: buffer,
  };
}

/** One server-sent event, as the frame's EventSource hands it to render code. */
export interface SseEvent {
  name: string;
  data: string;
  lastEventId: string;
}

/**
 * A `text/event-stream` parser (the HTML spec's, without `retry`): feed it text as
 * it arrives; it calls `onEvent` for each complete event.
 */
export function createSseParser(onEvent: (e: SseEvent) => void): {
  feed(text: string): void;
  end(): void;
} {
  let buffer = '';
  let data: string[] = [];
  let name = '';
  let lastEventId = '';
  let started = false;
  let scanned = 0;
  function line(raw: string) {
    if (raw === '') {
      if (data.length) {
        onEvent({
          name: name || 'message',
          data: data.join('\n'),
          lastEventId,
        });
      }
      data = [];
      name = '';
      return;
    }
    if (raw.startsWith(':')) return;
    const colon = raw.indexOf(':');
    const field = colon === -1 ? raw : raw.slice(0, colon);
    let value = colon === -1 ? '' : raw.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'data') data.push(value);
    else if (field === 'event') name = value;
    else if (field === 'id' && !value.includes('\u0000')) lastEventId = value;
  }
  return {
    feed(text: string) {
      buffer += text;
      if (!started && buffer.length) {
        started = true;
        if (buffer.charCodeAt(0) === 0xfeff) buffer = buffer.slice(1);
      }
      let m: RegExpExecArray | null;
      const re = /\r\n|\n|\r(?!$)/g;
      // Only what has not been looked at yet (less one, for a \r\n split across
      // chunks): a pipeline's rows can be one line of tens of megabytes arriving in
      // thousands of chunks, and rescanning it from the start each time is quadratic.
      re.lastIndex = Math.max(0, scanned - 1);
      let from = 0;
      while ((m = re.exec(buffer))) {
        line(buffer.slice(from, m.index));
        from = m.index + m[0].length;
      }
      buffer = buffer.slice(from);
      scanned = buffer.length;
    },
    end() {
      // An event the stream did not finish with a blank line is dropped (spec).
      buffer = '';
      data = [];
    },
  };
}

/**
 * Read a checked GET as a server-sent-event stream, as the viewer, from THIS page --
 * with the same headers a proxied fetch carries (a browser EventSource can set
 * none, which is why the host reads streams with fetch). Resolves when the stream
 * ends; rejects when it cannot be read.
 */
export async function proxyStream(
  checked: CheckedRequest,
  onEvent: (e: SseEvent) => void,
  onOpen: () => void,
  signal: AbortSignal,
  fetchImpl: typeof fetch = fetch,
  recheck?: (url: string) => CheckedRequest,
  maxBytes: number = LIMITS.streamBytes,
): Promise<void> {
  const headers: Record<string, string> = {
    accept: 'text/event-stream',
    'x-labs-sandbox-for': checked.sandboxFor || 'none',
  };
  if (checked.sandboxScope)
    headers['x-labs-sandbox-scope'] = checked.sandboxScope;
  const response = await fetchImpl(checked.href, {
    method: 'GET',
    headers,
    credentials: 'same-origin',
    redirect: 'follow',
    signal,
  });
  // Every refusal below lets go of the connection first.
  const refuse = (message: string): never => {
    response.body?.cancel().catch(() => undefined);
    throw new BridgeError(message);
  };
  if (response.redirected) {
    let landed: CheckedRequest | null = null;
    try {
      landed = recheck ? recheck(response.url) : null;
    } catch {
      landed = null;
    }
    if (!landed || landed.gate !== 'read') {
      refuse(
        'stream: that address redirected somewhere a workflow cannot reach',
      );
    }
  }
  if (!response.ok) refuse(`stream: HTTP ${response.status}`);
  const type = response.headers.get('content-type') || '';
  if (!type.startsWith('text/event-stream')) {
    refuse('stream: that address does not answer as a stream');
  }
  onOpen();
  const parser = createSseParser(onEvent);
  const decoder = new TextDecoder();
  let seen = 0;
  if (!response.body) {
    const text = await response.text();
    if (text.length > maxBytes) throw new BridgeError('stream: too large');
    parser.feed(text);
    parser.end();
    return;
  }
  const reader = response.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    seen += value.byteLength;
    if (seen > maxBytes) {
      reader.cancel().catch(() => undefined);
      throw new BridgeError('stream: the stream is too large for a workflow');
    }
    parser.feed(decoder.decode(value, { stream: true }));
  }
  parser.feed(decoder.decode());
  parser.end();
}

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------

export type RevealBlock = 'start' | 'center' | 'end' | 'nearest';
const REVEAL_BLOCKS: RevealBlock[] = ['start', 'center', 'end', 'nearest'];

export interface HostHandlers {
  querySupply(alias: string, args: Record<string, unknown>): Promise<unknown>;
  queryWorkflow(alias: string): Promise<unknown>;
  navigate(href: string, newTab: boolean): void;
  shareSelection(selection: ReturnType<typeof sanitizeSelection>): void;
  resize(height: number): void;
  /** Scroll the host window to `top` (host page coordinates). */
  scroll(top: number, smooth: boolean): void;
  /** Scroll the host so `top..bottom` (FRAME document coordinates) is in view. */
  reveal(
    top: number,
    bottom: number,
    block: RevealBlock,
    smooth: boolean,
  ): void;
  copyText(text: string): Promise<void>;
  confirm(text: string): Promise<boolean>;
  download(file: { filename: string; mime: string; bytes: Uint8Array }): void;
  reportError(message: string): void;
  /** Keep a batch of storage changes (scopedStorage). Without it, nothing is kept. */
  storage?(batch: StorageBatch): void;
}

/**
 * What the host does for a WORKFLOW (phase 2). Every one is labs' own code on
 * this page, bound to this workflow and run; nothing here takes an id from the
 * frame that the host has not checked.
 */
export interface WorkflowHandlers {
  /** A checked fetch, made as the viewer (proxyFetch). */
  fetch(
    checked: CheckedRequest,
    init: { headers?: unknown; body?: unknown },
  ): Promise<FetchAnswer>;
  queryPipelineRows(
    alias: string,
    query: Record<string, unknown>,
  ): Promise<unknown>;
  updateState(state: Record<string, unknown>): Promise<unknown>;
  /** One of this workflow's declared actions, through the host's own dialog. */
  runAction(
    key: string,
    args: Record<string, unknown>,
    options: { qaOnly: boolean },
  ): Promise<unknown>;
  /** view.complete(): the host asks (its words, then the page's), completes, reloads. */
  completeRun(pageText: string): Promise<boolean>;
  setLocation(search: string, hash: string, push: boolean): void;
  notify(message: string): void;
  /**
   * The host's own question, in its own words: "This workflow wants to <label>,
   * as you." Resolves true on the person's yes.
   */
  ask(text: string): Promise<boolean>;
  /** The action keys this workflow declares (runAction refuses any other). */
  actionKeys(): string[];
  /** Read a checked GET as a stream (proxyStream); resolves when it ends. */
  stream?(
    checked: CheckedRequest,
    onEvent: (e: SseEvent) => void,
    onOpen: () => void,
    signal: AbortSignal,
  ): Promise<void>;
}

export interface DispatchOptions {
  labsOrigin: string;
  /** User activation on the HOST page when the request arrives. */
  hasActivation: () => boolean;
  now?: () => number;
  /**
   * What the frame draws. A page and a workflow get ONE capability set (Jonathan,
   * 2026-10-12: "pages should be completely definable like workflows are"); the
   * only difference is that a page has no run, so the run-only methods
   * (RUN_ONLY_METHODS) are refused for it, and every route that names a run is
   * refused by routes.ts because there is no run id to bind.
   */
  mode?: SandboxMode;
  /** The ids a fetch may name (routes.ts). With `workflow`, the full method set. */
  routeContext?: RouteContext;
  workflow?: WorkflowHandlers;
  /** The host page's query now (setLocation keeps its scope and run). */
  currentSearch?: () => string;
  /** Send a stream's messages to the frame (on the port). */
  push?: (message: StreamMessage) => void;
}

/** A dispatcher, and how to stop the streams it is reading. */
export type Dispatcher = ((
  request: unknown,
) => Promise<BridgeResponse | null>) & {
  closeStreams: () => void;
};

/** Address parameters the frame may keep but never change (setLocation). */
export const PINNED_ADDRESS_PARAMS = [
  'opportunity_id',
  'program_id',
  'organization_id',
  'owning_program_id',
  'run_id',
  'edit',
  'sandbox',
  'present',
  'template_draft',
];

/** Methods only a run answers: a page asking for one is told it has no run.
 * (`runAction` is not one: a page's declared actions act within its scope, through
 * the same host dialog -- workflow/actions.py `PageScope`.) */
export const RUN_ONLY_METHODS = ['updateState', 'completeRun'];

/** Reads that can run a pipeline or a registry on the server (tens of seconds cold). */
const COLD_READ_RE =
  /\/labs\/workflow\/api\/\d+\/(pipeline-rows|pipeline-rows\/stream|case-states|pipeline-data|pipeline-data\/stream|pipeline-query|semantic|workflow-data|workflow-query|supply-data|supply-query)\/$/;

function isColdRead(checked: CheckedRequest): boolean {
  try {
    return COLD_READ_RE.test(new URL(checked.href).pathname);
  } catch {
    return false;
  }
}

/** Methods outside the request budget (see the dispatcher). */
const UNCOUNTED_METHODS = ['resize', 'scroll', 'reveal', 'storage'];

/** Answer one frame request at a time: `(request) => response`, never throwing. */
export function createDispatcher(
  handlers: HostHandlers,
  options: DispatchOptions,
): Dispatcher {
  const now = options.now || (() => Date.now());
  const mode: SandboxMode = options.mode || 'page';
  // The full method set is answered whenever the host gave the handlers for it.
  const full = !!(options.workflow && options.routeContext);
  const budget = RATE[full ? 'workflow' : 'page'];
  const noun = mode === 'workflow' ? 'workflow' : 'page';
  const recent: number[] = [];
  const grants = new Map<string, { until: number; left: number }>();
  const streams = new Map<string, AbortController>();
  const streamOpens: number[] = [];
  const jobStarts: number[] = [];
  const workflowJobStarts: number[] = [];
  const questionsAsked: number[] = [];
  let quietUntil = 0;
  let coldReads = 0;
  const storageWrites: number[] = [];
  let streamSeq = 0;
  let warnedTall = false;

  /** Within `limit` in the last minute, or refused: `recent` is this kind's log. */
  function perMinute(recent: number[], limit: number, what: string) {
    const t = now();
    while (recent.length && recent[0] < t - 60_000) recent.shift();
    if (recent.length >= limit) {
      throw new BridgeError(`too many ${what} from this page; wait a moment`);
    }
    recent.push(t);
  }
  let inFlight = 0;

  function needsActivation(method: string) {
    if (!options.hasActivation()) {
      throw new BridgeError(`${method} needs a click on the page first`);
    }
  }

  function workflow(): WorkflowHandlers {
    if (!full) throw new BridgeError('not available here');
    return options.workflow as WorkflowHandlers;
  }

  async function limited<T>(fn: () => Promise<T>): Promise<T> {
    if (inFlight >= budget.inFlight) {
      throw new BridgeError('too many requests at once');
    }
    inFlight += 1;
    try {
      return await fn();
    } finally {
      inFlight -= 1;
    }
  }

  /** An `effect` write: the host asks, and a yes holds for that kind a while. */
  /**
   * An `effect` write: the host asks first. A yes to a kind of write that creates
   * things holds for that kind a while, for a bounded number of writes; an effect on
   * ONE existing record (delete, update, message, complete) is asked every time,
   * naming the record.
   */
  async function allowEffect(checked: CheckedRequest) {
    const label = checked.label || 'make a change';
    // A yes to a kind of write; or, for a route that saves to one record again and
    // again (an audit's results), a yes to that kind of write on THAT record only.
    const grantKey = checked.recordGrant
      ? `${checked.kind}#${checked.recordId || ''}`
      : checked.kind;
    const remembered = !checked.perRecord || checked.recordGrant;
    if (remembered) {
      const grant = grants.get(grantKey);
      if (grant && grant.until > now() && grant.left > 0) {
        grant.left -= 1;
        return;
      }
    }
    const what = checked.question
      ? checked.question
      : checked.perRecord && checked.recordId
        ? `${label} ${checked.recordId}`
        : label;
    const yes = await askLabs(`This ${noun} wants to ${what}, as you.`);
    if (!yes) throw new BridgeError('not allowed: the person said no');
    if (remembered) {
      grants.set(grantKey, {
        until: now() + LIMITS.effectGrantMs,
        left: LIMITS.effectGrantWrites - 1,
      });
    }
  }

  /**
   * Labs' own question, budgeted: a page cannot bury the person in questions until
   * one is allowed. At most LIMITS.questionsPerMinute, and after a no, none for
   * LIMITS.quietAfterNoMs.
   */
  async function askLabs(text: string): Promise<boolean> {
    if (now() < quietUntil) {
      throw new BridgeError('not allowed: the person said no; wait a moment');
    }
    perMinute(questionsAsked, LIMITS.questionsPerMinute, 'questions');
    const yes = await workflow().ask(text);
    if (!yes) quietUntil = now() + LIMITS.quietAfterNoMs;
    return yes;
  }

  /** At most LIMITS.coldReadsInFlight of the reads that run a pipeline at once. */
  async function coldRead<T>(fn: () => Promise<T>): Promise<T> {
    if (coldReads >= LIMITS.coldReadsInFlight) {
      throw new BridgeError(
        'too many data reads at once; wait for one to finish',
      );
    }
    coldReads += 1;
    try {
      return await fn();
    } finally {
      coldReads -= 1;
    }
  }

  /** A job this host started may be cancelled by it (routes.ts `ownJob`). */
  function rememberJob(answer: FetchAnswer) {
    try {
      const data = JSON.parse(new TextDecoder().decode(answer.body));
      const id = data && (data.task_id || data.job_id);
      const ctx = options.routeContext as RouteContext;
      if (
        typeof id === 'string' &&
        /^[A-Za-z0-9_.:-]+$/.test(id) &&
        !ctx.jobIds.includes(id)
      ) {
        ctx.jobIds.push(id);
      }
    } catch {
      // not JSON: nothing started
    }
  }

  /** Pipelines an available-pipelines answer offered: the ones `pipe` binds to. */
  function rememberPipelines(answer: FetchAnswer) {
    try {
      const data = JSON.parse(new TextDecoder().decode(answer.body));
      const list = data && Array.isArray(data.pipelines) ? data.pipelines : [];
      const ctx = options.routeContext as RouteContext;
      const ids = ctx.pipelineIds || (ctx.pipelineIds = []);
      const names = ctx.pipelineNames || (ctx.pipelineNames = {});
      for (const p of list.slice(0, 2000)) {
        const id = Number(p && typeof p === 'object' ? p.id : NaN);
        if (!Number.isSafeInteger(id) || id <= 0) continue;
        if (!ids.includes(id)) ids.push(id);
        if (typeof p.name === 'string')
          names[String(id)] = p.name.slice(0, 200);
      }
    } catch {
      // not JSON: nothing offered
    }
  }

  /** A search across the viewer's opportunities, cut to THIS workflow's. */
  function onlyOurOpportunities(answer: FetchAnswer): FetchAnswer {
    const ours = (options.routeContext as RouteContext).opportunityIds;
    let data: unknown;
    try {
      data = JSON.parse(new TextDecoder().decode(answer.body));
    } catch {
      throw new BridgeError('fetch: that answer could not be read');
    }
    const list =
      data && typeof data === 'object'
        ? (data as { opportunities?: unknown }).opportunities
        : undefined;
    if (!Array.isArray(list)) {
      throw new BridgeError('fetch: that answer could not be read');
    }
    const kept = list.filter(
      (o) =>
        !!o &&
        typeof o === 'object' &&
        ours.includes(Number((o as { id?: unknown }).id)),
    );
    const body = new TextEncoder().encode(
      JSON.stringify({ ...(data as object), opportunities: kept }),
    );
    return { ...answer, body: body.buffer as ArrayBuffer };
  }

  /** Read a stream for the frame, pushing each event back on the port. */
  function openStream(checked: CheckedRequest): string {
    const w = workflow();
    if (!w.stream || !options.push) {
      throw new BridgeError('streams are not available here');
    }
    // A closed stream holds its place until the host's read of it has ended.
    if (streams.size >= LIMITS.streams) {
      throw new BridgeError('too many streams at once');
    }
    perMinute(streamOpens, LIMITS.streamOpensPerMinute, 'streams');
    streamSeq += 1;
    const id = `s${streamSeq}`;
    const controller = new AbortController();
    streams.set(id, controller);
    const push = (m: Omit<StreamMessage, 'stream' | 'type'>) => {
      if (streams.get(id) === controller && !controller.signal.aborted) {
        options.push!({ type: STREAM, stream: id, ...m });
      }
    };
    // After this request's answer (which names the stream) has been sent.
    Promise.resolve()
      .then(() =>
        w.stream!(
          checked,
          (e) =>
            push({
              kind: 'event',
              name: e.name,
              data: e.data,
              lastEventId: e.lastEventId,
            }),
          () => push({ kind: 'open' }),
          controller.signal,
        ),
      )
      .then(
        () => push({ kind: 'end' }),
        (e) => {
          if (!controller.signal.aborted) {
            const message = e instanceof Error ? e.message : String(e);
            console.warn(`[sandbox] stream ended: ${message}`);
            push({
              kind: 'error',
              message: message.slice(0, LIMITS.errorLength),
            });
          }
        },
      )
      .finally(() => {
        if (streams.get(id) === controller) streams.delete(id);
      });
    return id;
  }

  function closeStream(id: unknown) {
    if (typeof id !== 'string') return;
    const controller = streams.get(id);
    if (!controller) return;
    // Aborted, not forgotten: its slot is freed when the read settles (above).
    controller.abort();
  }

  async function navigateTo(href: string, newTab: boolean) {
    // A click inside the frame counts (activation reaches its ancestors). Without
    // one -- a navigation after an await -- the host asks, showing where it leads.
    if (!options.hasActivation()) {
      const path = new URL(href).pathname;
      const yes = await (full
        ? askLabs(`This ${noun} wants to open ${path}.`)
        : Promise.resolve(false));
      if (!yes)
        throw new BridgeError('navigate needs a click on the page first');
    }
    handlers.navigate(href, newTab);
  }

  async function run(method: string, params: Record<string, unknown>) {
    switch (method) {
      case 'querySupply':
      case 'queryWorkflow': {
        const a = alias(params);
        // The frame cannot name a definition or a source: the alias is one this page
        // declares. `args` (a plain object) passes through and may name an
        // opportunity, which the server checks against the opportunities the
        // workflow spans (supply_views.py).
        const args = isPlainObject(params.args) ? params.args : {};
        return limited(async () => {
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
        });
      }
      case 'navigate': {
        if (!full) needsActivation('navigate');
        const href = checkNavigate(
          params.url,
          options.labsOrigin,
          full ? 'full' : 'page',
        );
        await navigateTo(href, params.newTab === true);
        return true;
      }
      case 'shareSelection':
        handlers.shareSelection(sanitizeSelection(params));
        return true;
      case 'resize': {
        const h = Number(params.height);
        if (!Number.isFinite(h))
          throw new BridgeError('resize: height must be a number');
        // To the hundredth: a fractional height kept, so what follows the frame on
        // the host sits where it sat when the render was on the page itself.
        const clamped =
          Math.round(
            Math.min(LIMITS.maxHeight, Math.max(LIMITS.minHeight, h)) * 100,
          ) / 100;
        if (clamped < h && !warnedTall) {
          warnedTall = true;
          // Said once per frame, where whoever maintains the code can see it.
          console.warn(
            `[sandbox] the ${noun} is ${Math.round(h)}px tall; drawn ${clamped}px`,
          );
        }
        handlers.resize(clamped);
        return clamped;
      }
      case 'scroll': {
        const top = Number(params.top);
        if (!Number.isFinite(top))
          throw new BridgeError('scroll: top must be a number');
        handlers.scroll(Math.max(0, top), params.smooth === true);
        return true;
      }
      case 'reveal': {
        const top = Number(params.top);
        const bottom = Number(params.bottom);
        if (!Number.isFinite(top) || !Number.isFinite(bottom))
          throw new BridgeError('reveal: top and bottom must be numbers');
        const block = REVEAL_BLOCKS.includes(params.block as RevealBlock)
          ? (params.block as RevealBlock)
          : 'nearest';
        handlers.reveal(
          top,
          Math.max(top, bottom),
          block,
          params.smooth === true,
        );
        return true;
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
      case 'storage': {
        // Its own budget: storage is written as render code runs, and must never
        // crowd out the requests the page draws with.
        perMinute(
          storageWrites,
          LIMITS.storageWritesPerMinute,
          'storage writes',
        );
        if (bytesOf(params) > LIMITS.storageBytes + 64 * 1024) {
          throw new BridgeError('storage: too much in one change');
        }
        handlers.storage?.(checkStorageBatch(params));
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
      // --- Workflow mode --------------------------------------------------------
      case 'fetch': {
        const w = workflow();
        let checked: CheckedRequest;
        try {
          checked = checkFetch(
            params.url,
            params.method,
            options.routeContext as RouteContext,
            params.body,
          );
        } catch (e) {
          if (e instanceof RouteError) throw new BridgeError(e.message);
          throw e;
        }
        if (checked.startsJob) {
          perMinute(jobStarts, LIMITS.jobStartsPerMinute, 'background jobs');
          // A workflow job (a Celery task each) has a budget of its own, far below
          // the footprint polls': a compute job is started without a question.
          if (/\/job\/start\/$/.test(new URL(checked.href).pathname)) {
            perMinute(
              workflowJobStarts,
              LIMITS.workflowJobStartsPerMinute,
              'background jobs',
            );
          }
        }
        if (checked.gate === 'effect') {
          await allowEffect(checked);
        }
        const read = () =>
          w.fetch(checked, {
            headers: params.headers,
            body: params.body,
          });
        return limited(async () => {
          const answer = await (isColdRead(checked) ? coldRead(read) : read());
          if (checked.startsJob && answer.status < 300) rememberJob(answer);
          if (checked.remembersPipelines && answer.status < 300) {
            rememberPipelines(answer);
          }
          if (checked.filterOpportunities && answer.status < 300) {
            return onlyOurOpportunities(answer);
          }
          return answer;
        });
      }
      case 'queryPipelineRows': {
        const w = workflow();
        const a = alias(params);
        const query = isPlainObject(params.query) ? params.query : {};
        return limited(async () =>
          JSON.parse(
            JSON.stringify((await w.queryPipelineRows(a, query)) ?? null),
          ),
        );
      }
      case 'updateState': {
        const w = workflow();
        if (!isPlainObject(params.state)) {
          throw new BridgeError('updateState: state must be a plain object');
        }
        await w.updateState(params.state);
        return true;
      }
      case 'runAction': {
        const w = workflow();
        const key = params.key;
        if (
          typeof key !== 'string' ||
          !/^[A-Za-z0-9_-]{1,64}$/.test(key) ||
          !w.actionKeys().includes(key)
        ) {
          throw new BridgeError(
            `This workflow has no action ${JSON.stringify(String(key)).slice(0, 80)}.`,
          );
        }
        const args = isPlainObject(params.args) ? params.args : {};
        const opts = isPlainObject(params.options) ? params.options : {};
        const execution = await w.runAction(key, args, {
          qaOnly: opts.qaOnly === true,
        });
        return JSON.parse(JSON.stringify(execution ?? null));
      }
      case 'completeRun': {
        const w = workflow();
        const text =
          typeof params.confirm === 'string'
            ? params.confirm.slice(0, LIMITS.confirmTextLength)
            : '';
        return Boolean(await w.completeRun(text));
      }
      case 'setLocation': {
        const w = workflow();
        const search = typeof params.search === 'string' ? params.search : '';
        const hash = typeof params.hash === 'string' ? params.hash : '';
        if (search.length > LIMITS.urlLength || hash.length > 500) {
          throw new BridgeError('setLocation: too long');
        }
        if (search && !search.startsWith('?')) {
          throw new BridgeError('setLocation: search must start with ?');
        }
        if (hash && !hash.startsWith('#')) {
          throw new BridgeError('setLocation: hash must start with #');
        }
        const keys = Array.from(new URLSearchParams(search).keys()).map((k) =>
          k.toLowerCase(),
        );
        if (keys.some((k) => NAVIGATE_DENIED_PARAMS.includes(k))) {
          throw new BridgeError('setLocation: that address is not allowed');
        }
        // The page's scope and run are labs' to set, not the frame's: code this page
        // runs (the action dialog, a reload) reads them off the address.
        const next = new URLSearchParams(search);
        const current = new URLSearchParams(
          options.currentSearch ? options.currentSearch() : '',
        );
        for (const k of PINNED_ADDRESS_PARAMS) {
          if (
            next.getAll(k).join('\u0000') !== current.getAll(k).join('\u0000')
          ) {
            throw new BridgeError(
              `setLocation: ${k} cannot be changed from here`,
            );
          }
        }
        w.setLocation(search, hash, params.push === true);
        return true;
      }
      case 'streamOpen': {
        workflow();
        let checked: CheckedRequest;
        try {
          checked = checkFetch(
            params.url,
            'GET',
            options.routeContext as RouteContext,
          );
        } catch (e) {
          if (e instanceof RouteError) throw new BridgeError(e.message);
          throw e;
        }
        // A stream is a read: an address the table answers only as a write is not one.
        if (checked.gate !== 'read') {
          throw new BridgeError('stream: only a read can be streamed');
        }
        return { stream: openStream(checked) };
      }
      case 'streamClose': {
        workflow();
        closeStream(params.stream);
        return true;
      }
      case 'notify': {
        const w = workflow();
        const message =
          typeof params.message === 'string'
            ? params.message
            : String(params.message ?? '');
        w.notify(message.slice(0, LIMITS.messageLength));
        return true;
      }
      default:
        throw new BridgeError(
          `unknown method ${JSON.stringify(method).slice(0, 80)}`,
        );
    }
  }

  const dispatch = (async (request: unknown) => {
    if (!isPlainObject(request)) return null;
    const { id, method } = request as unknown as BridgeRequest;
    if (typeof id !== 'number' && typeof id !== 'string') return null;
    try {
      if (typeof method !== 'string')
        throw new BridgeError('method must be a string');
      const isWorkflowMethod = (WORKFLOW_METHODS as readonly string[]).includes(
        method,
      );
      if (isWorkflowMethod && !full) {
        throw new BridgeError(`${method} is ${NOT_ON_A_PAGE}`);
      }
      if (mode === 'page' && RUN_ONLY_METHODS.includes(method)) {
        throw new BridgeError(`${method} needs a run, and a page has none`);
      }
      if (
        !isWorkflowMethod &&
        !(PHASE1_METHODS as readonly string[]).includes(method)
      ) {
        throw new BridgeError(
          `unknown method ${JSON.stringify(method).slice(0, 80)}`,
        );
      }
      const t = now();
      while (recent.length && recent[0] < t - budget.windowMs) recent.shift();
      // Resizes come with every layout change, and scrolling only moves the page the
      // person is already on: neither is counted against the page.
      if (!UNCOUNTED_METHODS.includes(method)) {
        if (recent.length >= budget.count) {
          throw new BridgeError(
            'too many requests from this page; wait a moment',
          );
        }
        recent.push(t);
      }
      const params = request.params === undefined ? {} : request.params;
      const cap = full ? LIMITS.workflowParamsBytes : LIMITS.paramsBytes;
      if (
        !isPlainObject(params) ||
        // A download's bytes, a fetch's body and a storage change are sized
        // against their own caps.
        (method !== 'download' &&
          method !== 'fetch' &&
          method !== 'storage' &&
          bytesOf(params) > cap)
      ) {
        throw new BridgeError('params must be a small plain object');
      }
      if (method === 'fetch') {
        const { body, ...rest } = params as Record<string, unknown>;
        void body;
        if (bytesOf(rest) > LIMITS.paramsBytes) {
          throw new BridgeError('params must be a small plain object');
        }
      }
      return { id, result: await run(method, params) };
    } catch (e) {
      const message =
        e instanceof BridgeError
          ? e.message
          : `${String(request.method)} failed: ${e instanceof Error ? e.message : String(e)}`;
      if (full && e instanceof BridgeError) {
        // Said on the host page, where whoever maintains the code can see it.
        console.warn(`[sandbox] refused ${String(request.method)}: ${message}`);
      }
      return { id, error: message.slice(0, LIMITS.errorLength) };
    }
  }) as Dispatcher;
  dispatch.closeStreams = () => {
    for (const controller of streams.values()) controller.abort();
    streams.clear();
  };
  return dispatch;
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
  handlers: Omit<HostHandlers, 'resize' | 'scroll' | 'reveal' | 'storage'>;
  /** Whose storage the frame reads and writes (the definition id): its keys are
   * kept on this page's localStorage / sessionStorage under that name only. Null:
   * in memory only. */
  storageScope?: string | null;
  /** 'workflow' to answer the phase-2 methods (routeContext and workflow needed). */
  mode?: SandboxMode;
  routeContext?: RouteContext;
  workflow?: WorkflowHandlers;
  /** Appended to the shell's path so `location.search` in the frame reads the
   * host's query, as render code did before the frame (the shell ignores it). */
  search?: string;
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

// ---------------------------------------------------------------------------
// Sizing: the frame is as tall as what it draws, and the page around it scrolls
// ---------------------------------------------------------------------------

/**
 * The bottom of labs' fixed header (layouts/header.html) in the window, or 0: what
 * the person cannot see the frame through. A header taller than 40% of the window
 * is not a header.
 */
export function headerInset(doc: Document, win: Window): number {
  let inset = 0;
  for (const el of Array.from(doc.querySelectorAll('header'))) {
    const position = win.getComputedStyle(el).position;
    if (position !== 'fixed' && position !== 'sticky') continue;
    const r = el.getBoundingClientRect();
    if (r.height <= 0 || r.top > 1 || r.height > win.innerHeight * 0.4)
      continue;
    inset = Math.max(inset, r.bottom);
  }
  return Math.round(inset);
}

/** How far the frame reaches past its column, and how far content may overflow it. */
export interface Gutters {
  left: number;
  right: number;
  /** How far content may overflow the column, left and right, before a box on the
   * host clips it (`overflow`): -1 where nothing does. */
  clipLeft: number;
  clipRight: number;
}

/**
 * How far the frame may reach past the column it is drawn in (`container`), left and
 * right: out through every ancestor whose other in-flow children are above or below it,
 * never beside it, to the window's edges (where labs' fixed sidenav and header are drawn
 * over it, as over the page). On the run page, content that overflowed its column
 * showed in the page's margins, a fixed dialog was centred in the window and media
 * queries were the window's; a frame as wide as the column clipped the first and moved
 * the other two. A column beside it stops the reach. A box that clips (its overflow)
 * does not stop it -- fixed elements were never clipped by one -- but is passed on, so
 * the frame clips its content where the page did.
 */
export function frameGutters(container: HTMLElement, win: Window): Gutters {
  const doc = container.ownerDocument;
  const own = container.getBoundingClientRect();
  const top = own.top;
  const bottom = own.top + Math.max(1, own.height);
  let left = own.left;
  let right = own.right;
  let clip: { left: number; right: number } | null = null;
  let child: Element = container;
  walk: for (
    let a = container.parentElement;
    a && a !== doc.documentElement;
    child = a, a = a.parentElement
  ) {
    for (const sib of Array.from(a.children)) {
      if (sib === child) continue;
      const cs = win.getComputedStyle(sib);
      if (cs.position === 'fixed' || cs.position === 'absolute') continue;
      const r = sib.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      const besideIt =
        r.bottom > top &&
        r.top < bottom &&
        (r.right <= left + 1 || r.left >= right - 1);
      if (besideIt) break walk;
    }
    const r = a.getBoundingClientRect();
    left = Math.min(left, r.left);
    right = Math.max(right, r.right);
    const x = win.getComputedStyle(a).overflowX;
    if (!clip && a !== doc.body && x && x !== 'visible')
      clip = { left: r.left, right: r.right };
  }
  const width = doc.documentElement.clientWidth || win.innerWidth;
  return {
    left: Math.max(0, Math.round(own.left - Math.max(0, left))),
    right: Math.max(0, Math.round(Math.min(width, right) - own.right)),
    clipLeft: clip ? Math.max(0, Math.round(own.left - clip.left)) : -1,
    clipRight: clip ? Math.max(0, Math.round(clip.right - own.right)) : -1,
  };
}

/** The host window, and where the frame sits in it (protocol.ts HostViewport). */
export function measureViewport(
  win: Window,
  iframe: HTMLIFrameElement,
  insetTop: number,
  gutters: Gutters = { left: 0, right: 0, clipLeft: -1, clipRight: -1 },
): HostViewport {
  const r = iframe.getBoundingClientRect();
  return {
    width: win.innerWidth,
    height: win.innerHeight,
    scrollX: Math.round(win.scrollX),
    scrollY: Math.round(win.scrollY),
    frameTop: Math.round(r.top),
    frameHeight: Math.round(r.height),
    insetTop,
    gutterLeft: gutters.left,
    gutterRight: gutters.right,
    clipLeft: gutters.clipLeft,
    clipRight: gutters.clipRight,
  };
}

/**
 * Where the host window scrolls to show `top..bottom` of the frame's document, as
 * `scrollIntoView` would have scrolled the run page -- the browser's own rule, so
 * the window (less the page's `scroll-padding-top`, `padTop`), not the part below
 * labs' header: null when nothing need move (`nearest`, already in view).
 */
export function revealTarget(
  vp: HostViewport,
  top: number,
  bottom: number,
  block: RevealBlock,
  padTop = 0,
): number | null {
  const pageTop = vp.scrollY + vp.frameTop + top;
  const pageBottom = vp.scrollY + vp.frameTop + bottom;
  const visibleTop = vp.scrollY + padTop;
  const visibleBottom = vp.scrollY + vp.height;
  let target: number;
  if (block === 'start') target = pageTop - padTop;
  else if (block === 'end') target = pageBottom - vp.height;
  else if (block === 'center')
    target = (pageTop + pageBottom) / 2 - (padTop + vp.height) / 2;
  else {
    if (pageTop >= visibleTop && pageBottom <= visibleBottom) return null;
    const tall = pageBottom - pageTop > vp.height - padTop;
    target =
      tall || pageTop < visibleTop ? pageTop - padTop : pageBottom - vp.height;
  }
  return Math.max(0, Math.round(target));
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
  // As tall as what it draws (the frame reports it), for pages and workflows alike:
  // the page around it scrolls, never the frame. What render code sized by the window
  // (100vh, innerHeight, fixed dialogs, sticky headers) it sizes by THIS window, which
  // the host tells it about (sendViewport; the frame's side is ./viewport.ts).
  iframe.style.height = `${LIMITS.minHeight * 4}px`;
  iframe.setAttribute('scrolling', 'no');

  let payload = options.payload;
  let port: MessagePort | null = null;
  let greeted = false;
  let loads = 0;
  let stopped = false;
  let inset = 0;
  let gutters: Gutters = { left: 0, right: 0, clipLeft: -1, clipRight: -1 };
  let sentViewport = '';

  /** Reach into the page's margins (frameGutters); the frame keeps its content in
   * the column. */
  const placeHorizontally = () => {
    gutters = frameGutters(options.container, win);
    iframe.style.width = `calc(100% + ${gutters.left + gutters.right}px)`;
    iframe.style.marginLeft = `${-gutters.left}px`;
    iframe.style.marginRight = `${-gutters.right}px`;
  };
  const viewportNow = () => measureViewport(win, iframe, inset, gutters);
  /** Tell the frame about the window, when anything in it changed. */
  const sendViewport = () => {
    if (!port || stopped) return;
    const viewport = viewportNow();
    const key = JSON.stringify(viewport);
    if (key === sentViewport) return;
    sentViewport = key;
    port.postMessage({ type: VIEWPORT, viewport });
  };
  const onWindowResize = () => {
    inset = headerInset(doc, win);
    placeHorizontally();
    sendViewport();
  };
  // Content above the frame that changes height moves it without a scroll.
  let rafId = 0;
  const layoutObserver =
    typeof ResizeObserver === 'function'
      ? new ResizeObserver(() => {
          if (rafId) return;
          rafId = win.requestAnimationFrame(() => {
            rafId = 0;
            placeHorizontally();
            sendViewport();
          });
        })
      : null;

  const storage = scopedStorage(win, options.storageScope ?? null);
  const dispatch = createDispatcher(
    {
      ...options.handlers,
      storage: (batch) => storage.apply(batch),
      resize: (h) => {
        iframe.style.height = `${h}px`;
        sendViewport();
      },
      scroll: (top, smooth) =>
        win.scrollTo({ top, behavior: smooth ? 'smooth' : 'auto' }),
      reveal: (top, bottom, block, smooth) => {
        const pad = parseFloat(
          win.getComputedStyle(doc.documentElement).scrollPaddingTop,
        );
        const target = revealTarget(
          viewportNow(),
          top,
          bottom,
          block,
          Number.isFinite(pad) ? pad : 0,
        );
        if (target !== null)
          win.scrollTo({ top: target, behavior: smooth ? 'smooth' : 'auto' });
      },
    },
    {
      labsOrigin: options.labsOrigin,
      hasActivation: options.hasActivation || defaultActivation(win),
      mode: options.mode,
      routeContext: options.routeContext,
      workflow: options.workflow,
      currentSearch: () => win.location.search,
      push: (message) => {
        if (port && !stopped) port.postMessage(message);
      },
    },
  );

  function stop() {
    stopped = true;
    dispatch.closeStreams();
    if (port) port.close();
    port = null;
    win.removeEventListener('message', onMessage);
    win.removeEventListener('scroll', sendViewport);
    win.removeEventListener('resize', onWindowResize);
    layoutObserver?.disconnect();
    if (rafId) win.cancelAnimationFrame(rafId);
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
    inset = headerInset(doc, win);
    placeHorizontally();
    const viewport = viewportNow();
    sentViewport = JSON.stringify(viewport);
    iframe.contentWindow?.postMessage(
      { type: INIT, payload, viewport, storage: storage.seed() },
      '*',
      [channel.port2],
    );
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
  // The shell is always labs' own fixed path, whatever the page data says. A query
  // rides along only so the frame's `location.search` matches the page's.
  const search =
    typeof options.search === 'string' && /^\?[^#]*$/.test(options.search)
      ? options.search.slice(0, LIMITS.urlLength)
      : '';
  iframe.src = SHELL_PATH + search;
  options.container.appendChild(iframe);
  win.addEventListener('scroll', sendViewport, { passive: true });
  win.addEventListener('resize', onWindowResize);
  layoutObserver?.observe(doc.documentElement);

  return {
    iframe,
    update(next: FramePayload) {
      // Only what changed (by reference): a workflow's pipelines can be tens of
      // megabytes, and are cloned again on every send.
      const changed: Partial<FramePayload> = {};
      let any = false;
      for (const key of Object.keys(next) as (keyof FramePayload)[]) {
        if (next[key] !== payload[key]) {
          (changed as Record<string, unknown>)[key] = next[key];
          any = true;
        }
      }
      payload = next;
      if (any && port && !stopped)
        port.postMessage({ type: UPDATE, payload: changed });
    },
    destroy() {
      stop();
      iframe.removeEventListener('load', onLoad);
      iframe.remove();
    },
  };
}
