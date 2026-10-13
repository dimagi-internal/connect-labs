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
  UPDATE,
  WORKFLOW_METHODS,
  WORKFLOW_NAVIGATE_DENIED,
  WORKFLOW_NAVIGATE_PATTERNS,
  WORKFLOW_NAVIGATE_PREFIXES,
  type BridgeRequest,
  type BridgeResponse,
  type FramePayload,
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
  if (params.some((k) => NAVIGATE_DENIED_PARAMS.includes(k))) {
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
// Workflow fetches (phase 2)
// ---------------------------------------------------------------------------

/** What the frame gets back for a fetch: enough to build a Response. */
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
  const unsafe = !['GET', 'HEAD'].includes(checked.method);
  if (unsafe) headers['x-csrftoken'] = csrfToken;
  // A route that checked its body sends what it checked (canonical JSON).
  const body = unsafe
    ? checked.body !== undefined
      ? checked.body
      : checkBody(init.body)
    : undefined;
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
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength > LIMITS.fetchResultBytes) {
    throw new BridgeError('fetch: the answer is too large for a workflow');
  }
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
}

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

/** Methods only a run answers: a page asking for one is told it has no run. */
export const RUN_ONLY_METHODS = ['updateState', 'completeRun', 'runAction'];

/** Answer one frame request at a time: `(request) => response`, never throwing. */
export function createDispatcher(
  handlers: HostHandlers,
  options: DispatchOptions,
): (request: unknown) => Promise<BridgeResponse | null> {
  const now = options.now || (() => Date.now());
  const mode: SandboxMode = options.mode || 'page';
  // The full method set is answered whenever the host gave the handlers for it.
  const full = !!(options.workflow && options.routeContext);
  const budget = RATE[full ? 'workflow' : 'page'];
  const noun = mode === 'workflow' ? 'workflow' : 'page';
  const recent: number[] = [];
  const grants = new Map<string, { until: number; left: number }>();
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
    if (!checked.perRecord) {
      const grant = grants.get(checked.kind);
      if (grant && grant.until > now() && grant.left > 0) {
        grant.left -= 1;
        return;
      }
    }
    const what =
      checked.perRecord && checked.recordId
        ? `${label} ${checked.recordId}`
        : label;
    const yes = await workflow().ask(`This ${noun} wants to ${what}, as you.`);
    if (!yes) throw new BridgeError('not allowed: the person said no');
    if (!checked.perRecord) {
      grants.set(checked.kind, {
        until: now() + LIMITS.effectGrantMs,
        left: LIMITS.effectGrantWrites - 1,
      });
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

  async function navigateTo(href: string, newTab: boolean) {
    // A click inside the frame counts (activation reaches its ancestors). Without
    // one -- a navigation after an await -- the host asks, showing where it leads.
    if (!options.hasActivation()) {
      const path = new URL(href).pathname;
      const yes = await (full
        ? workflow().ask(`This ${noun} wants to open ${path}.`)
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
        // The query is always THIS page's: the frame cannot name a definition, an
        // opportunity or a programme. Only `args` (a plain object) passes through.
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
        if (checked.gate === 'effect') {
          await allowEffect(checked);
        }
        return limited(async () => {
          const answer = await w.fetch(checked, {
            headers: params.headers,
            body: params.body,
          });
          if (checked.startsJob && answer.status < 300) rememberJob(answer);
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
        if (typeof key !== 'string' || !w.actionKeys().includes(key)) {
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

  return async (request: unknown) => {
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
      // Resizes come with every layout change; they are not counted against the page.
      if (method !== 'resize') {
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
        // A download's bytes and a fetch's body are sized against their own caps.
        (method !== 'download' && method !== 'fetch' && bytesOf(params) > cap)
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
  /** 'workflow' to answer the phase-2 methods (routeContext and workflow needed). */
  mode?: SandboxMode;
  routeContext?: RouteContext;
  workflow?: WorkflowHandlers;
  /** Appended to the shell's path so `location.search` in the frame reads the
   * host's query, as render code did before the frame (the shell ignores it). */
  search?: string;
  /**
   * The frame is as tall as the window and scrolls inside, instead of growing to
   * its content (a workflow). Render code sizes things by the viewport
   * (`min-height: 100vh`, `fixed inset-0` dialogs): in a frame as tall as its
   * content, `100vh` IS the content, so it grew without end and a dialog was
   * centred somewhere off screen. A frame the size of the window keeps both meaning
   * what they meant on the run page.
   */
  fill?: boolean;
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
  iframe.style.height = options.fill ? '100vh' : `${LIMITS.minHeight * 4}px`;
  // Filling: the window below where the frame starts, so the whole frame (and a
  // dialog centred in it) is on screen with the page's header still above it.
  const fit = () => {
    const top = iframe.getBoundingClientRect().top + win.scrollY;
    iframe.style.height = `${Math.max(LIMITS.minHeight * 6, Math.round(win.innerHeight - top - 8))}px`;
  };

  let payload = options.payload;
  let port: MessagePort | null = null;
  let greeted = false;
  let loads = 0;
  let stopped = false;

  const dispatch = createDispatcher(
    {
      ...options.handlers,
      resize: (h) => {
        if (!options.fill) iframe.style.height = `${h}px`;
      },
    },
    {
      labsOrigin: options.labsOrigin,
      hasActivation: options.hasActivation || defaultActivation(win),
      mode: options.mode,
      routeContext: options.routeContext,
      workflow: options.workflow,
      currentSearch: () => win.location.search,
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
  // The shell is always labs' own fixed path, whatever the page data says. A query
  // rides along only so the frame's `location.search` matches the page's.
  const search =
    typeof options.search === 'string' && /^\?[^#]*$/.test(options.search)
      ? options.search.slice(0, LIMITS.urlLength)
      : '';
  iframe.src = SHELL_PATH + search;
  options.container.appendChild(iframe);
  if (options.fill) {
    fit();
    win.addEventListener('resize', fit);
  }

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
      win.removeEventListener('resize', fit);
      iframe.removeEventListener('load', onLoad);
      iframe.remove();
    },
  };
}
