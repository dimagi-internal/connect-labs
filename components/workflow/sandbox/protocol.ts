/**
 * The page sandbox bridge: what the host (the trusted labs page) and the frame (the
 * page's code, in an opaque-origin iframe) say to each other.
 * Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "The bridge".
 *
 *   frame -> parent (window):  {type: HELLO}                     until INIT arrives
 *   host  -> frame  (window):  {type: INIT, payload} + one MessagePort
 *   host  -> frame  (port):    {type: UPDATE, payload}
 *   frame -> host   (port):    {id, method, params}  ->  {id, result} | {id, error}
 *
 * After INIT everything rides the port; window messages are ignored.
 */

export const HELLO = 'labs-sandbox:hello';
export const INIT = 'labs-sandbox:init';
export const UPDATE = 'labs-sandbox:update';

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

/** Methods a page will get with workflows (phase 2): rejected with a clear message. */
export const PHASE2_METHODS = [
  'queryPipelineRows',
  'runAction',
  'startJob',
  'createTask',
  'fetchImage',
  'jobStatus',
] as const;

export const NOT_ON_A_PAGE = 'not available on a page yet';

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
/** Never, whatever the lists above say. */
export const NAVIGATE_DENIED = ['/o/', '/accounts/', '/labs/mcp/', '/admin/'];

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

/** What INIT and UPDATE carry: the page's props as plain data. */
export interface FramePayload {
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
}
