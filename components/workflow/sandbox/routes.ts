/**
 * What a sandboxed WORKFLOW's code may ask labs for, through the host.
 * Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Phase 2".
 *
 * A workflow's render code used to call `fetch('/audit/api/...')` on labs' own
 * origin, with the viewer's session. In the sandbox it has no session and no
 * network to labs at all; its `fetch` to a labs path becomes a bridge request the
 * host answers ONLY if the request matches a route below. This table is the whole
 * of what workflow code can make labs do as the viewer, so it is the thing to
 * review:
 *
 * - `read`: the endpoint only reads. The server checks the viewer's access as it
 *   always has. Routes whose path names an opportunity, a program, this workflow
 *   or this run are BOUND: the id must be one the workflow itself covers.
 * - `own`: a write to this run's or this workflow's own record (its state, its
 *   flags, its name). Bound to this run / definition, no question asked: render
 *   code makes these as the person works (and some from effects).
 * - `effect`: a write that reaches past this run -- a task, an audit, a coaching
 *   message to a worker, a background job, completing or deleting the run. The
 *   host asks the person first, in its own words (never the page's), and a yes
 *   holds for that kind of write for a short while, so one click that creates a
 *   batch is one question.
 *
 * Everything else is refused: the token page, OAuth, the MCP, admin, workflow
 * definitions and render code, sharing, deleting workflows, and a workflow's
 * declared actions (those go through `actions.runAction`, which the host
 * previews and confirms itself).
 */

export type Gate = 'read' | 'own' | 'effect';

export interface Route {
  /** Methods this route answers. */
  methods: readonly string[];
  /** Matched against the URL's pathname. Named groups bind ids (see `bindings`). */
  path: RegExp;
  gate: Gate;
  /** For `effect`: how the host names it when it asks ("create a task"). */
  label?: string;
  /**
   * When the request's body decides how far the write reaches: return the gate
   * for this body (or null to keep `gate`). Read from the body the host is about
   * to send, so the frame cannot claim one thing and send another.
   */
  bodyGate?: (body: unknown) => Gate | null;
  /** Ids a request names in its BODY, bound like path ids: throws when one is not
   * this workflow's. */
  bodyCheck?: (body: unknown, ctx: RouteContext) => void;
}

function parseBody(body: unknown): unknown {
  if (typeof body !== 'string') return body;
  try {
    return JSON.parse(body);
  } catch {
    return null;
  }
}

/** `{"opportunities": [ids]}`: every one must be this workflow's. */
function opportunitiesInBody(body: unknown, ctx: RouteContext): void {
  const parsed = parseBody(body) as { opportunities?: unknown } | null;
  const opps =
    parsed && Array.isArray(parsed.opportunities) ? parsed.opportunities : [];
  for (const o of opps) {
    if (!ctx.opportunityIds.includes(Number(o))) {
      throw new RouteError(
        `fetch: that request names opportunity ${String(o).slice(0, 20)}, which this workflow does not cover`,
      );
    }
  }
}

/**
 * Background jobs that only compute into THIS run's own state (a rollup, a
 * plausibility check). Every other job type creates things past the run (audits
 * across opportunities, coaching) and is an `effect`.
 * See workflow/job_handlers/.
 */
export const COMPUTE_ONLY_JOBS = [
  'audit_par_rollup',
  'muac_plausibility_compute',
  'program_admin_rollup',
];

function jobGate(body: unknown): Gate | null {
  const parsed = parseBody(body);
  const jobType =
    parsed && typeof parsed === 'object'
      ? ((parsed as { job_config?: { job_type?: unknown } }).job_config || {})
          .job_type
      : undefined;
  return typeof jobType === 'string' && COMPUTE_ONLY_JOBS.includes(jobType)
    ? 'own'
    : null;
}

/** What the host knows about the workflow it is drawing: the ids a route may name. */
export interface RouteContext {
  labsOrigin: string;
  definitionId: number | null;
  runId: number | null;
  opportunityIds: number[];
  programId: number | null;
}

export interface CheckedRequest {
  href: string;
  method: string;
  gate: Gate;
  /** The route's kind, the key a yes is remembered under. */
  kind: string;
  label: string;
}

const ID = '(\\d+)';
const RUN = '(?<run>\\d+)';
const DEF = '(?<def>\\d+)';
const OPP = '(?<opp>\\d+)';
const PROG = '(?<prog>\\d+)';
const TOKEN = '([A-Za-z0-9_.:-]+)';
/** A visit image's blob id, URL-encoded by render code: anything but a slash. */
const BLOB = '([^/]+)';
const GET = ['GET', 'HEAD'] as const;
const POST = ['POST'] as const;

function p(source: string): RegExp {
  return new RegExp('^' + source + '$');
}

export const ROUTES: readonly Route[] = [
  // --- Reads: workflow runs, history, indicators -------------------------------
  { methods: GET, gate: 'read', path: p(`/labs/workflow/api/run/${ID}/`) },
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/run/${ID}/snapshot/(preview/)?`),
  },
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/${ID}/runs/history/`),
  },
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/${ID}/(indicator-definitions|semantic)/`),
  },
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/run/${RUN}/flags/list/`),
  },
  // This workflow's own pipelines, rows and sources: bound to this definition.
  {
    methods: GET,
    gate: 'read',
    path: p(
      `/labs/workflow/api/${DEF}/(pipeline-rows|pipeline-rows/stream|case-states|pipeline-data|supply-data|workflow-data)/`,
    ),
  },
  {
    methods: POST,
    gate: 'read',
    path: p(
      `/labs/workflow/api/${DEF}/(pipeline-query|supply-query|workflow-query)/`,
    ),
  },
  { methods: GET, gate: 'read', path: p(`/labs/workflow/api/auth-status/`) },
  {
    methods: GET,
    gate: 'read',
    path: p(
      `/labs/workflow/api/(workers|open-tasks|worker-tasks|chatbot-status|prev-categories|open-run-state|run-category-history|flw-audit-report-history|flw-daily-indicator-history|flw-daily-summary-history)/`,
    ),
  },
  {
    methods: POST,
    gate: 'read',
    path: p(`/labs/workflow/api/opportunity-flws/`),
    bodyCheck: opportunitiesInBody,
  },
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/ocs/(status|bots)/`),
  },
  // Job progress (the poll endpoint; the stream one answers once, at the end).
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/job/${TOKEN}/(status/|status\\.json)`),
  },
  // Visit images: bound to this workflow's opportunities.
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/image/${OPP}/${BLOB}/`),
  },
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/${OPP}/visit-images/`),
  },
  { methods: GET, gate: 'read', path: p(`/audit/image/${OPP}/${BLOB}/`) },
  // --- Reads: audits, tasks, benchmarks, boundaries -----------------------------
  {
    methods: GET,
    gate: 'read',
    path: p(
      `/audit/api/opportunity/${OPP}/(image-questions|field-questions|deliver-unit-types|sessions-summary)/`,
    ),
  },
  {
    methods: GET,
    gate: 'read',
    path: p(`/audit/api/program/${PROG}/sessions-summary/`),
  },
  { methods: GET, gate: 'read', path: p(`/audit/api/scope-context/`) },
  { methods: GET, gate: 'read', path: p(`/audit/api/ai-agents/`) },
  {
    methods: GET,
    gate: 'read',
    path: p(`/audit/api/workflow/${ID}/sessions/`),
  },
  { methods: GET, gate: 'read', path: p(`/audit/api/${ID}/bulk-data/`) },
  { methods: POST, gate: 'read', path: p(`/audit/api/audit/preview/`) },
  {
    methods: GET,
    gate: 'read',
    path: p(`/audit/api/audit/task/${TOKEN}/(status|stream)/`),
  },
  { methods: GET, gate: 'read', path: p(`/tasks/api/${ID}/`) },
  {
    methods: GET,
    gate: 'read',
    path: p(`/tasks/${ID}/ai/(sessions|transcript)/`),
  },
  { methods: GET, gate: 'read', path: p(`/labs/benchmarks/api/${OPP}/`) },
  {
    methods: GET,
    gate: 'read',
    path: p(`/microplans/boundaries/(viewport|countries)/`),
  },
  {
    methods: GET,
    gate: 'read',
    path: p(
      `/microplans/${OPP}/boundaries/(areas|geometry|population_by_name)/`,
    ),
  },

  // --- Own: this run's and this workflow's own records --------------------------
  {
    methods: POST,
    gate: 'own',
    path: p(
      `/labs/workflow/api/run/${RUN}/(state|worker-result|flags|rename|reconcile-generation)/`,
    ),
  },
  {
    methods: POST,
    gate: 'own',
    path: p(`/labs/workflow/api/${DEF}/audit-batch-config/`),
  },
  {
    methods: POST,
    gate: 'own',
    path: p(`/labs/workflow/api/job/${TOKEN}/cancel/`),
  },

  // --- Effect: past this run; the host asks first --------------------------------
  {
    methods: POST,
    gate: 'effect',
    path: p(`/labs/workflow/api/run/${RUN}/job/start/`),
    label: 'start a background job that creates audits or other records',
    bodyGate: jobGate,
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/labs/workflow/api/run/${RUN}/complete/`),
    label: 'complete this run (it is saved and cannot change after)',
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/labs/workflow/api/run/${RUN}/delete/`),
    label: 'delete this run',
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/labs/workflow/api/run/${RUN}/(manager-audit|manager-coaching)/`),
    label: 'add a demo audit or coaching conversation to this run',
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/tasks/api/single-create/`),
    label: 'create tasks',
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/tasks/api/${ID}/(update|comment)/`),
    label: 'update tasks',
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/tasks/${ID}/ai/initiate/`),
    label: 'start an AI coaching conversation with a worker',
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/tasks/${ID}/ai/save-transcript/`),
    label: 'save a coaching transcript to a task',
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/audit/api/audit/(create|create-async)/`),
    label: 'create audits',
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/audit/api/audit/task/${TOKEN}/cancel/`),
    label: 'cancel an audit being created',
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/audit/api/${ID}/delete/`),
    label: 'delete audits',
  },
];

/** Never, whatever a route above says (belt and braces against a loose pattern). */
export const FETCH_DENIED = [
  '/o/',
  '/accounts/',
  '/admin/',
  '/labs/mcp/',
  '/mcp/',
  '/labs/sandbox/',
];

/** Query parameters that name a scope: when present, bound like a path id. */
const SCOPE_PARAMS = { opportunity_id: 'opp', program_id: 'prog' } as const;

export class RouteError extends Error {}

function bound(name: string, value: string, ctx: RouteContext): boolean {
  const n = Number(value);
  if (!Number.isSafeInteger(n)) return false;
  switch (name) {
    case 'run':
      return ctx.runId !== null && ctx.runId > 0 && n === ctx.runId;
    case 'def':
      return ctx.definitionId !== null && n === ctx.definitionId;
    case 'opp':
      return ctx.opportunityIds.includes(n);
    case 'prog':
      return ctx.programId !== null && n === ctx.programId;
    default:
      return false;
  }
}

/**
 * Whether a workflow's request may be made, and as what. Returns the URL to use
 * (always the parsed, absolute labs URL -- never the string the frame sent) or
 * throws a RouteError saying why not.
 */
export function checkFetch(
  url: unknown,
  method: unknown,
  ctx: RouteContext,
  body?: unknown,
): CheckedRequest {
  if (typeof url !== 'string' || !url || url.length > 4096) {
    throw new RouteError('fetch: the address must be a short string');
  }
  if (/[\u0000-\u001f\u007f\\]/.test(url)) {
    throw new RouteError('fetch: that address is not a labs address');
  }
  const m = (typeof method === 'string' ? method : 'GET').toUpperCase();
  let base: URL;
  let parsed: URL;
  try {
    base = new URL(ctx.labsOrigin);
    parsed = new URL(url, base.origin);
  } catch {
    throw new RouteError('fetch: that address is not a labs address');
  }
  if (
    parsed.origin !== base.origin ||
    parsed.protocol !== base.protocol ||
    parsed.username ||
    parsed.password
  ) {
    throw new RouteError(
      'fetch: a workflow can only reach labs, and only through the page around it',
    );
  }
  const path = parsed.pathname;
  if (FETCH_DENIED.some((d) => path.startsWith(d))) {
    throw new RouteError(`fetch: ${path} cannot be reached from a workflow`);
  }
  const route = ROUTES.find((r) => r.path.test(path));
  if (!route) {
    throw new RouteError(`fetch: ${path} cannot be reached from a workflow`);
  }
  if (!route.methods.includes(m)) {
    throw new RouteError(`fetch: ${m} ${path} cannot be made from a workflow`);
  }
  const groups = route.path.exec(path)?.groups || {};
  for (const [name, value] of Object.entries(groups)) {
    if (value !== undefined && !bound(name, value, ctx)) {
      throw new RouteError(
        `fetch: ${path} names ${name === 'opp' ? 'an opportunity' : name === 'prog' ? 'a program' : name === 'run' ? 'a run' : 'a workflow'} this workflow does not cover`,
      );
    }
  }
  // Writes may not name another scope in their query either.
  if (route.gate !== 'read') {
    for (const [param, name] of Object.entries(SCOPE_PARAMS)) {
      for (const value of parsed.searchParams.getAll(param)) {
        if (!bound(name, value, ctx)) {
          throw new RouteError(
            `fetch: ${path} names ${param}=${value.slice(0, 20)}, which this workflow does not cover`,
          );
        }
      }
    }
  }
  if (route.bodyCheck) route.bodyCheck(body, ctx);
  const gate = (route.bodyGate && route.bodyGate(body)) || route.gate;
  return {
    href: parsed.href,
    method: m,
    gate,
    kind: route.path.source,
    label: route.label || '',
  };
}

/** Request headers a workflow may set; the host adds its own CSRF token. */
export const PASS_HEADERS = ['content-type', 'accept'];

export function cleanHeaders(headers: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!headers || typeof headers !== 'object') return out;
  const entries: [string, unknown][] = Array.isArray(headers)
    ? (headers as [string, unknown][])
    : Object.entries(headers as Record<string, unknown>);
  for (const [k, v] of entries) {
    const key = String(k).toLowerCase();
    if (PASS_HEADERS.includes(key) && typeof v === 'string' && v.length < 200) {
      out[key] = v;
    }
  }
  return out;
}
