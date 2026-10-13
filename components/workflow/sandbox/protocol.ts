/**
 * The page sandbox bridge: what the host (the trusted labs page) and the frame (the
 * page's code, in an opaque-origin iframe) say to each other.
 * Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "The bridge".
 *
 *   frame -> parent (window):  {type: HELLO}                     until INIT arrives
 *   host  -> frame  (window):  {type: INIT, payload} + one MessagePort
 *   host  -> frame  (port):    {type: UPDATE, payload}
 *   frame -> host   (port):    {id, method, params}  ->  {id, result} | {id, error}
 *   host  -> frame  (port):    {type: STREAM, stream, kind, ...}  (an EventSource the
 *                              host reads for the frame: `streamOpen` / `streamClose`)
 *
 * After INIT everything rides the port; window messages are ignored.
 */

/** The frame document's path on labs (workflow/sandbox.py SHELL_PATH). Fixed here, never
 * read from the page: the host draws only this. */
export const SHELL_PATH = '/labs/sandbox/render/';

export const HELLO = 'labs-sandbox:hello';
export const INIT = 'labs-sandbox:init';
export const UPDATE = 'labs-sandbox:update';
export const STREAM = 'labs-sandbox:stream';

/** One thing that happened on a stream the host reads for the frame. */
export interface StreamMessage {
  type?: typeof STREAM;
  stream: string;
  /** `open` once; `event` per server-sent event; `end` or `error` last. */
  kind: 'open' | 'event' | 'end' | 'error';
  name?: string;
  data?: string;
  lastEventId?: string;
  message?: string;
}

/** Methods the host answers on a page (phase 1). Anything else is an error. */
export const PHASE1_METHODS = [
  'querySupply',
  'queryWorkflow',
  'navigate',
  'shareSelection',
  'resize',
  'copyText',
  'confirm',
  'download',
  'reportError',
] as const;
export type BridgeMethod = (typeof PHASE1_METHODS)[number];

/**
 * Methods the host answers for a WORKFLOW drawn in the frame (phase 2), on top of
 * the page ones. A page asking for any of these is told they are not for pages.
 *
 * - `fetch`: a labs request, checked against ./routes.ts (the whole of what a
 *   workflow's code can make labs do as the viewer) and made by the host.
 * - `queryPipelineRows`: server-side rows of this workflow's own pipeline.
 * - `updateState`: this run's state (onUpdateState).
 * - `runAction`: one of this workflow's declared actions, previewed and confirmed
 *   by the host's own dialog (ActionDialog), never by the page.
 * - `completeRun`: view.complete() -- the host asks, completes, reloads.
 * - `setLocation`: history.pushState/replaceState on the host's URL (query and
 *   hash only, same path).
 * - `notify`: what alert() used to say, drawn by the host as text.
 * - `streamOpen` / `streamClose`: an EventSource, read by the host for a GET the
 *   route table allows as a read; its events come back as STREAM messages.
 */
export const WORKFLOW_METHODS = [
  'fetch',
  'queryPipelineRows',
  'updateState',
  'runAction',
  'completeRun',
  'setLocation',
  'notify',
  'streamOpen',
  'streamClose',
] as const;

/** Kept for the page tests: what a page is refused. */
export const PHASE2_METHODS = WORKFLOW_METHODS;

export const NOT_ON_A_PAGE = 'not available on a page';

export const LIMITS = {
  /** Serialised params of one request. */
  paramsBytes: 64 * 1024,
  /** A query's serialised answer. */
  resultBytes: 4 * 1024 * 1024,
  /** Requests per rolling window, and the window. */
  rateCount: 60,
  rateWindowMs: 10_000,
  /** Queries in flight at once. */
  inFlightQueries: 6,
  aliasLength: 100,
  urlLength: 2048,
  visibleIds: 400,
  idLength: 200,
  drilledKeys: 20,
  drilledValueLength: 200,
  copyTextLength: 100_000,
  confirmTextLength: 2_000,
  errorLength: 2_000,
  downloadBytes: 10 * 1024 * 1024,
  filenameLength: 120,
  minHeight: 80,
  maxHeight: 50_000,
  /** A workflow fetch: its request body, and the answer the host hands back. */
  fetchBodyBytes: 4 * 1024 * 1024,
  fetchResultBytes: 96 * 1024 * 1024,
  /** A workflow's own params (runAction's workers, a state update). */
  workflowParamsBytes: 4 * 1024 * 1024,
  /** How long a yes to an `effect` write holds for that kind of write. */
  effectGrantMs: 2 * 60 * 1000,
  /** ...and for at most this many writes. */
  effectGrantWrites: 200,
  messageLength: 2_000,
  /** Streams (EventSource) the host reads for one frame at once. */
  streams: 6,
  /** What one stream may deliver, in all (a pipeline's rows ride the last event). */
  streamBytes: 96 * 1024 * 1024,
  /** Streams opened per minute: each holds server work open, and closing one does
   * not stop that work at once. */
  streamOpensPerMinute: 20,
  /** Requests that start server-side work (a job, a footprints fetch), per minute. */
  jobStartsPerMinute: 30,
};

/** Per-mode request budgets: a workflow loads images and rows a page never does. */
export const RATE = {
  page: { count: 60, windowMs: 10_000, inFlight: 6 },
  workflow: { count: 1_200, windowMs: 10_000, inFlight: 24 },
};

/** Extensions a page may download, and the type each is saved as (never the page's say). */
export const DOWNLOAD_TYPES: Record<string, string> = {
  csv: 'text/csv',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  json: 'application/json',
  png: 'image/png',
  pdf: 'application/pdf',
};

/** Where a click on a page may lead: labs pages that are pages, reports, supply... */
export const NAVIGATE_PREFIXES = [
  '/labs/p/',
  '/supply/',
  '/labs/settings/',
  '/labs/targeting/',
  '/labs/marketplace/',
  '/microplans/',
];
/** ...a workflow's run page, and the workflow list (page_blank links to it). */
export const NAVIGATE_PATTERNS = [
  /^\/labs\/workflow\/\d+\/run\/$/,
  /^\/labs\/workflow\/$/,
];
/** Where else a click in a WORKFLOW may lead: the audit, task and workflow screens
 * its rows link to, a visit photo, an indicator sheet, and the sign-in starts its
 * "Authorize" buttons name. Never an API address otherwise. */
export const WORKFLOW_NAVIGATE_PREFIXES = [
  '/audit/',
  '/tasks/',
  '/labs/ocs/initiate/',
  '/labs/commcare/initiate/',
  '/labs/workflow/api/image/',
];
export const WORKFLOW_NAVIGATE_PATTERNS = [
  /^\/labs\/workflow\/\d+\/$/,
  /^\/labs\/workflow\/\d+\/run\/$/,
  /^\/labs\/workflow\/run\/\d+\/$/,
  /^\/labs\/workflow\/api\/\d+\/indicator-definitions\/$/,
];
/** Inside the workflow prefixes above, still never these. */
export const WORKFLOW_NAVIGATE_DENIED = ['/audit/api/', '/tasks/api/'];
/** Never, whatever the lists above say. */
export const NAVIGATE_DENIED = ['/o/', '/accounts/', '/labs/mcp/', '/admin/'];
/** Query parameters a page's link may never carry: on the run page they once made a
 * GET write (`?sync=true&template=...` overwrote the render code). */
export const NAVIGATE_DENIED_PARAMS = [
  'sync',
  'template',
  // Where a sign-in start or any other view sends the person AFTER: a second hop
  // the allowlist never sees (e.g. on to an OAuth consent page).
  'next',
  'redirect',
  'redirect_to',
  'redirect_uri',
  'return',
  'return_to',
  'return_url',
];

export interface BridgeRequest {
  id: number | string;
  method: string;
  params?: unknown;
}

export interface BridgeResponse {
  id: number | string;
  result?: unknown;
  error?: string;
}

/** What INIT carries, and UPDATE carries any part of (only what changed): the
 * page's or workflow's props as plain data. */
export interface FramePayload {
  /** 'page' (phase 1) or 'workflow' (phase 2). A page is never handed workers,
   * pipelines or a run; a workflow is handed what its render reads today. */
  mode?: 'page' | 'workflow';
  renderCode: string;
  definition: Record<string, unknown>;
  instance: Record<string, unknown>;
  workers: unknown[];
  pipelines: Record<string, unknown>;
  supply: Record<string, unknown>;
  workflows: Record<string, unknown>;
  scope: unknown;
  config: Record<string, unknown>;
  links: { auditUrlBase: string; taskUrlBase: string };
  view: {
    state: Record<string, unknown>;
    isCompleted: boolean;
    asOf: string | null;
  };
  today: string;
  mapboxToken: string;
  /** The host page's own URL: what a page's relative links resolve against (the
   * frame's own URL is the shell's, where no relative link means anything). */
  pageUrl: string;
  /** Workflow mode only. */
  apiEndpoints?: Record<string, string | null>;
  flags?: unknown[];
  audits?: unknown[];
  tasks?: unknown[];
  workflowActions?: unknown[];
  isEditMode?: boolean;
  /** Workflow mode: the names of THIS workflow's opportunities, as render code
   * reads them from `#user-opportunities` on the run page (only these, never
   * every opportunity the viewer has). */
  userOpportunities?: { id: number; name: string }[];
  /** Set when the host is still streaming pipelines and the definition renders
   * while loading (config.renderWhileLoading). */
  pipelinesLoading?: boolean;
}
