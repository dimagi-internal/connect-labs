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
 *   or this run are BOUND: the id must be one the workflow itself covers. Reads of
 *   ANOTHER workflow's runs are bound to the workflows this one DECLARES
 *   (workflow/declared_reads.py): a definition named in the path or the query here,
 *   and a run by its definition on the server, which knows the request is the
 *   frame's from the `X-Labs-Sandbox-For` header the host stamps on it.
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
  /** Matched against the URL's pathname. Named groups bind ids (see `bound`). */
  path: RegExp;
  gate: Gate;
  /** For `effect`: how the host names it when it asks ("create a task"). */
  label?: string;
  /**
   * When the request's body decides how far the write reaches: return the gate
   * for this body (or null to keep `gate`).
   */
  bodyGate?: (body: Record<string, unknown>, ctx: RouteContext) => Gate | null;
  /** Ids a request names in its BODY, bound like path ids: throws when one is not
   * this workflow's. */
  bodyCheck?: (body: Record<string, unknown>, ctx: RouteContext) => void;
  /**
   * An effect on ONE existing record (delete this audit, update this task, message
   * this worker): asked every time, naming the record; a yes is never reused.
   */
  perRecord?: boolean;
  /** The path's token must be a job this host started (or the run's own). */
  ownJob?: boolean;
  /** With `perRecord`: a yes holds for THAT record a while (a screen that saves one
   * audit's results again and again), never for another. */
  recordGrant?: boolean;
  /** The answer starts a job whose token later routes name (`ownJob`). */
  startsJob?: boolean;
  /** A search across everything the viewer has: the host cuts the answer's
   * `opportunities` to this workflow's own. */
  filterOpportunities?: boolean;
  /** The answer lists pipelines (`{pipelines: [{id}]}`) the viewer may attach: the
   * host remembers their ids, which `pipe` then binds to. */
  remembersPipelines?: boolean;
  /** Query parameters this route alone binds, beyond the ones every route does
   * (SCOPE_PARAMS, BOUND_PARAMS): `{session_id: 'run'}` where a view reads a run
   * by another name. */
  boundQuery?: Record<string, string>;
  /** For `effect`: what the host's question says, in labs' words, built from the
   * CHECKED body (for a write whose reach the body decides). Replaces `label`. */
  describe?: (body: Record<string, unknown>, ctx: RouteContext) => string;
}

/**
 * A body a route inspects must be a JSON object, parsed HERE; the host then sends
 * `JSON.stringify` of what it checked, never the frame's own bytes, so the server
 * reads exactly what was checked (bytes, a byte-order mark or UTF-16 would
 * otherwise be read one way by this check and another by Django).
 */
function jsonObjectBody(body: unknown): Record<string, unknown> {
  if (body === undefined || body === null || body === '') return {};
  if (typeof body !== 'string') {
    throw new RouteError('fetch: this request takes a JSON body, as text');
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    throw new RouteError('fetch: this request takes a JSON body');
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new RouteError('fetch: this request takes a JSON object');
  }
  return parsed as Record<string, unknown>;
}

function refuseOpportunity(o: unknown): never {
  throw new RouteError(
    `fetch: that request names opportunity ${String(o).slice(0, 20)}, which this workflow does not cover`,
  );
}

/** `{"opportunities": [ids or {id}]}`: every one must be this workflow's. */
function opportunitiesInBody(
  body: Record<string, unknown>,
  ctx: RouteContext,
): void {
  const opps = body.opportunities;
  if (opps === undefined || opps === null) return;
  if (!Array.isArray(opps)) refuseOpportunity(opps);
  for (const o of opps as unknown[]) {
    const id =
      o && typeof o === 'object' ? (o as { id?: unknown }).id : (o as unknown);
    if (!ctx.opportunityIds.includes(Number(id))) refuseOpportunity(id);
  }
}

/** `{"per_opp": {"<opp id>": ...}}` (audit batch config). */
function perOppInBody(body: Record<string, unknown>, ctx: RouteContext): void {
  const per = body.per_opp;
  if (per === undefined || per === null) return;
  if (typeof per !== 'object' || Array.isArray(per)) refuseOpportunity(per);
  for (const k of Object.keys(per as object)) {
    if (!ctx.opportunityIds.includes(Number(k))) refuseOpportunity(k);
  }
}

/** Footprints for at most a few drawn areas per request (each is a cold fetch on the
 * shared background worker). */
export const MAX_FOOTPRINT_AREAS = 5;
function fewAreas(body: Record<string, unknown>): void {
  const areas = body.areas;
  if (!Array.isArray(areas) || areas.length > MAX_FOOTPRINT_AREAS) {
    throw new RouteError(
      `fetch: building footprints for at most ${MAX_FOOTPRINT_AREAS} areas at a time`,
    );
  }
}

/** Requires `run_id` in the body, and that it is THIS run (a write a route makes
 * to the run it names). */
function thisRunRequired(
  body: Record<string, unknown>,
  ctx: RouteContext,
): void {
  if (ctx.runId === null || Number(body.run_id) !== ctx.runId) {
    throw new RouteError(
      'fetch: that request names a run this workflow does not cover',
    );
  }
  if (
    body.opportunity_id !== undefined &&
    body.opportunity_id !== null &&
    body.opportunity_id !== '' &&
    !ctx.opportunityIds.includes(Number(body.opportunity_id))
  ) {
    refuseOpportunity(body.opportunity_id);
  }
}

/** A pipeline alias, as the definition stores it: a short slug. */
const ALIAS_RE = /^[A-Za-z0-9_-]{1,64}$/;
function aliasIn(body: Record<string, unknown>): string {
  const a = body.alias;
  if (typeof a !== 'string' || !ALIAS_RE.test(a)) {
    throw new RouteError('fetch: a pipeline alias is a short name');
  }
  return a;
}

/** Attaching a pipeline to THIS workflow: one the viewer was offered (the host saw it
 * in an available-pipelines answer) or one the workflow already has. */
function attachBody(body: Record<string, unknown>, ctx: RouteContext): void {
  aliasIn(body);
  const id = Number(body.pipeline_id);
  if (!Number.isSafeInteger(id) || !(ctx.pipelineIds || []).includes(id)) {
    throw new RouteError(
      'fetch: that pipeline was not offered to this workflow',
    );
  }
  if (
    body.load !== undefined &&
    body.load !== null &&
    (typeof body.load !== 'string' || body.load.length > 32)
  ) {
    throw new RouteError('fetch: load is a short name');
  }
  for (const k of Object.keys(body)) {
    if (!['pipeline_id', 'alias', 'load'].includes(k)) {
      throw new RouteError(
        `fetch: ${k.slice(0, 40)} is not part of that change`,
      );
    }
  }
}

function detachBody(body: Record<string, unknown>): void {
  aliasIn(body);
  for (const k of Object.keys(body)) {
    if (k !== 'alias') {
      throw new RouteError(
        `fetch: ${k.slice(0, 40)} is not part of that change`,
      );
    }
  }
}

/** A job is cancelled only on THIS run. */
function thisRunInBody(body: Record<string, unknown>, ctx: RouteContext): void {
  if (
    body.run_id !== undefined &&
    body.run_id !== null &&
    Number(body.run_id) !== ctx.runId
  ) {
    throw new RouteError(
      'fetch: that request names a run this workflow does not cover',
    );
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

/** A job's own scope ids are bound like any other; then its type decides the gate. */
function jobGate(
  body: Record<string, unknown>,
  ctx: RouteContext,
): Gate | null {
  const config =
    body.job_config && typeof body.job_config === 'object'
      ? (body.job_config as Record<string, unknown>)
      : {};
  if (
    config.opportunity_id !== undefined &&
    config.opportunity_id !== null &&
    !ctx.opportunityIds.includes(Number(config.opportunity_id))
  ) {
    refuseOpportunity(config.opportunity_id);
  }
  // Every job handler acts on job_config.run_id: it must be THIS run (the server
  // also refuses one that is not the run in the URL).
  if (
    config.run_id !== undefined &&
    config.run_id !== null &&
    (ctx.runId === null || Number(config.run_id) !== ctx.runId)
  ) {
    throw new RouteError(
      'fetch: that job names a run this workflow does not cover',
    );
  }
  if (
    config.program_id !== undefined &&
    config.program_id !== null &&
    !ctx.programIds.includes(Number(config.program_id))
  ) {
    throw new RouteError(
      'fetch: that job names a program this workflow does not cover',
    );
  }
  const jobType = config.job_type;
  return typeof jobType === 'string' && COMPUTE_ONLY_JOBS.includes(jobType)
    ? 'own'
    : null;
}

/** What the host knows about what it is drawing: the ids a route may name. */
export interface RouteContext {
  labsOrigin: string;
  definitionId: number | null;
  /** null on a page (it has no run) and in edit mode (run 0). */
  runId: number | null;
  opportunityIds: number[];
  programIds: number[];
  /** The organisation in view (a page's scope), by slug. */
  organizationKey: string | null;
  /** The definitions whose runs this one declares it reads (itself included):
   * `workflow_sources`, a companion's report, watched sources, `read_workflows`.
   * From the server (declared_reads.readable_definition_ids). */
  readableDefinitionIds: number[];
  /** Pipelines this workflow may name: its own `pipeline_sources`, and those an
   * available-pipelines answer offered (the host adds to it). */
  pipelineIds?: number[];
  /** Their names, as that answer gave them (for the host's question). */
  pipelineNames?: Record<string, string>;
  /** Jobs this run is running or this host started: the only ones it may cancel.
   * The host adds to it as it starts jobs. */
  jobIds: string[];
  /** Added to a write that names no scope, so it lands in THIS workflow's and not
   * in whatever the viewer's session last pointed at. */
  defaultScope: { name: 'opportunity_id' | 'program_id'; value: string } | null;
}

export interface CheckedRequest {
  href: string;
  method: string;
  gate: Gate;
  /** The route's kind, the key a yes is remembered under. */
  kind: string;
  label: string;
  /** The body to send, when the route checked one (its canonical JSON). */
  body?: string;
  /** Asked every time, naming `recordId`. */
  perRecord?: boolean;
  recordId?: string;
  /** A yes holds a while for `recordId` alone. */
  recordGrant?: boolean;
  /** The host cuts the answer's `opportunities` to this workflow's. */
  filterOpportunities?: boolean;
  /** A job start: the host remembers the job it started. */
  startsJob?: boolean;
  /** The host remembers the pipelines the answer lists. */
  remembersPipelines?: boolean;
  /** Labs' own words for the question, when the route builds them from the body. */
  question?: string;
  /** Stamped on the request as `X-Labs-Sandbox-For`: the definition it is made for,
   * so the server binds the runs it reads to the ones that definition declares. */
  sandboxFor: string;
  /** Stamped as `X-Labs-Sandbox-Scope`: where that definition lives
   * (`program_id=25`), so the server reads it there whatever the session holds. */
  sandboxScope: string;
}

const ID = '(\\d+)';
const RUN = '(?<run>\\d+)';
/** ANOTHER run, written as an effect: the server binds it to a definition this
 * workflow declares (declared_reads.py). */
const XRUN = '(?<xrun>\\d+)';
/** A pipeline this workflow has or was offered (`pipelineIds`). */
const PIPE = '(?<pipe>\\d+)';
const DEF = '(?<def>\\d+)';
const OPP = '(?<opp>\\d+)';
/** Another definition whose runs are read: one this workflow declares. */
const REL = '(?<rel>\\d+)';
const PROG = '(?<prog>\\d+)';
const TOKEN = '([A-Za-z0-9_.:-]+)';
/** A job's token, bound to the jobs this run has (`ownJob`). */
const JOB = '(?<job>[A-Za-z0-9_.:-]+)';
/** The one existing record an effect acts on: named when the host asks. */
const REC = '(?<rec>\\d+)';
const RECTOKEN = '(?<rec>[A-Za-z0-9_.:-]+)';
/** A visit image's blob id, URL-encoded by render code: anything but a slash. */
const BLOB = '([^/]+)';
const GET = ['GET', 'HEAD'] as const;
const POST = ['POST'] as const;

function p(source: string): RegExp {
  return new RegExp('^' + source + '$');
}

export const ROUTES: readonly Route[] = [
  // --- Reads: workflow runs, history, indicators -------------------------------
  // A run by id: the server binds it to a definition this workflow declares
  // (declared_reads.py, from the X-Labs-Sandbox-For header the host stamps).
  { methods: GET, gate: 'read', path: p(`/labs/workflow/api/run/${ID}/`) },
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/run/${ID}/snapshot/(preview/)?`),
  },
  // Another workflow's history and indicators: one this workflow declares.
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/${REL}/runs/history/`),
  },
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/${REL}/(indicator-definitions|semantic)/`),
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
      `/labs/workflow/api/${DEF}/(pipeline-rows|pipeline-rows/stream|case-states|pipeline-data|pipeline-data/stream|supply-data|workflow-data|schedule)/`,
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
  // The audit wizard's opportunity picker: answered with THIS workflow's only.
  {
    methods: GET,
    gate: 'read',
    path: p(`/audit/api/opportunities/search/`),
    filterOpportunities: true,
  },
  { methods: GET, gate: 'read', path: p(`/audit/api/ai-agents/`) },
  {
    methods: GET,
    gate: 'read',
    path: p(`/audit/api/workflow/${ID}/sessions/`),
  },
  { methods: GET, gate: 'read', path: p(`/audit/api/${ID}/bulk-data/`) },
  {
    methods: POST,
    gate: 'read',
    path: p(`/audit/api/audit/preview/`),
    bodyCheck: opportunitiesInBody,
  },
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
  // Building footprints for an area drawn on a map: computed on the server from
  // public data (writes nothing of the viewer's), then polled -- only a poll this
  // host started. A few areas at a time; starts are budgeted by the host.
  {
    methods: POST,
    gate: 'read',
    path: p(`/microplans/${OPP}/preview_footprints/`),
    startsJob: true,
    bodyCheck: fewAreas,
  },
  {
    methods: GET,
    gate: 'read',
    path: p(`/microplans/preview_status/${JOB}/`),
    ownJob: true,
  },

  // The pipelines the viewer may attach (their own and shared ones: names and ids,
  // no rows), and one pipeline's definition -- only one this workflow has or was
  // just offered.
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/available-pipelines/`),
    remembersPipelines: true,
  },
  {
    methods: GET,
    gate: 'read',
    path: p(`/labs/workflow/api/pipeline/${PIPE}/`),
  },
  // The workflow list, as a sandboxed request gets it from the server: only the run
  // links of the workflows this one declares (views.WorkflowListView, sandboxed),
  // never the list page itself. Old dashboards find run ids there.
  { methods: GET, gate: 'read', path: p(`/labs/workflow/`) },
  // The deprecated MBW monitoring dashboards' own endpoints
  // (workflow/templates/mbw_monitoring/urls.py): reads of this workflow's
  // opportunity (bound by the query) and its own run (`run_id`, bound).
  {
    methods: POST,
    gate: 'read',
    path: p(`/custom_analysis/mbw_monitoring/api/opportunity-flws/`),
    bodyCheck: opportunitiesInBody,
  },
  {
    methods: GET,
    gate: 'read',
    path: p(
      `/custom_analysis/mbw_monitoring/(stream|api/oauth-status|api/snapshot)/`,
    ),
    // These views read the run as `run_id` OR `session_id`: both are this run.
    boundQuery: { session_id: 'run' },
  },
  {
    methods: GET,
    gate: 'read',
    path: p(`/custom_analysis/mbw_monitoring/api/gps/([^/]+)/`),
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
    bodyCheck: perOppInBody,
  },
  // The MBW dashboard's snapshot of THIS run (its body names the run).
  {
    methods: POST,
    gate: 'own',
    path: p(`/custom_analysis/mbw_monitoring/api/save-snapshot/`),
    bodyCheck: thisRunRequired,
  },
  {
    methods: POST,
    gate: 'own',
    path: p(`/labs/workflow/api/job/${JOB}/cancel/`),
    ownJob: true,
    bodyCheck: thisRunInBody,
  },

  // --- Effect: past this run; the host asks first --------------------------------
  {
    methods: POST,
    gate: 'effect',
    path: p(`/labs/workflow/api/run/${RUN}/job/start/`),
    label: 'start a background job that creates audits or other records',
    bodyGate: jobGate,
  },
  // Another workflow's run's state (a control room marking the runs it watches):
  // asked first; the server takes it only for a run of a definition this one
  // declares (declared_reads.py). This run's own state is the `own` route above.
  {
    methods: POST,
    gate: 'effect',
    path: p(`/labs/workflow/api/run/${XRUN}/state/`),
    label: 'change the saved state of run',
    // Asked naming the run; a yes holds a while for THAT run only.
    perRecord: true,
    recordGrant: true,
  },
  // This workflow's own data sources: asked every time, naming the change.
  {
    methods: POST,
    gate: 'effect',
    path: p(`/labs/workflow/api/${DEF}/pipeline-sources/add/`),
    label: 'change its own data sources',
    perRecord: true,
    bodyCheck: attachBody,
    describe: (body, ctx) => {
      const id = Number(body.pipeline_id);
      const name = (ctx.pipelineNames || {})[String(id)];
      const named = name
        ? ` "${name
            .replace(/["\s]+/g, ' ')
            .trim()
            .slice(0, 80)}"`
        : '';
      return `change its own data sources: attach pipeline #${id}${named} as "${String(body.alias)}"`;
    },
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/labs/workflow/api/${DEF}/pipeline-sources/remove/`),
    label: 'change its own data sources',
    perRecord: true,
    bodyCheck: detachBody,
    describe: (body) =>
      `change its own data sources: detach the pipeline "${String(body.alias)}"`,
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/labs/workflow/api/run/${RUN}/complete/`),
    label: 'complete this run (it is saved and cannot change after)',
    perRecord: true,
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/labs/workflow/api/run/${RUN}/delete/`),
    label: 'delete this run',
    perRecord: true,
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
    path: p(`/tasks/api/${REC}/(update|comment)/`),
    label: 'update task',
    perRecord: true,
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/tasks/${REC}/ai/initiate/`),
    label: 'start an AI coaching conversation with the worker on task',
    perRecord: true,
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/tasks/${REC}/ai/save-transcript/`),
    label: 'save a coaching transcript to task',
    perRecord: true,
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/audit/api/audit/(create|create-async)/`),
    label: 'create audits',
    bodyCheck: opportunitiesInBody,
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/audit/api/audit/task/${RECTOKEN}/cancel/`),
    label: 'cancel the audit being created by job',
    perRecord: true,
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/audit/api/${REC}/delete/`),
    label: 'delete audit',
    perRecord: true,
  },
  {
    methods: POST,
    gate: 'effect',
    path: p(`/audit/api/${REC}/save/`),
    label: 'save review results to audit',
    perRecord: true,
    recordGrant: true,
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
const SCOPE_PARAMS = {
  opportunity_id: 'opp',
  program_id: 'prog',
  organization_id: 'org',
} as const;

/** Other query parameters bound like a path id (the history endpoints name the
 * workflow they read with `?definition_id=`). */
const BOUND_PARAMS = { definition_id: 'rel', run_id: 'run' } as const;

export class RouteError extends Error {}

function bound(name: string, value: string, ctx: RouteContext): boolean {
  if (name === 'rec') return true; // named in the question, checked by the server
  if (name === 'job') return ctx.jobIds.includes(value);
  if (name === 'org')
    return !!ctx.organizationKey && value === ctx.organizationKey;
  const n = Number(value);
  if (!/^\d+$/.test(value) || !Number.isSafeInteger(n)) return false;
  switch (name) {
    case 'run':
      return ctx.runId !== null && ctx.runId > 0 && n === ctx.runId;
    case 'xrun':
      // Another run: the server binds it to a declared definition.
      return n > 0 && n !== ctx.runId;
    case 'pipe':
      return (ctx.pipelineIds || []).includes(n);
    case 'def':
      return ctx.definitionId !== null && n === ctx.definitionId;
    case 'rel':
      return (
        (ctx.definitionId !== null && n === ctx.definitionId) ||
        (ctx.readableDefinitionIds || []).includes(n)
      );
    case 'opp':
      return ctx.opportunityIds.includes(n);
    case 'prog':
      return ctx.programIds.includes(n);
    default:
      return false;
  }
}

const NOUNS: Record<string, string> = {
  opp: 'an opportunity',
  prog: 'a program',
  org: 'an organization',
  run: 'a run',
  def: 'a workflow',
  rel: 'a workflow',
  xrun: 'a run',
  pipe: 'a pipeline',
  job: 'a job',
};

/**
 * Whether a request from the frame may be made, and as what. Returns the URL to
 * use (always the parsed, absolute labs URL -- never the string the frame sent) and,
 * when the route reads its body, the body to send; or throws a RouteError saying
 * why not.
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
      'fetch: this code can only reach labs, and only through the page around it',
    );
  }
  const path = parsed.pathname;
  if (FETCH_DENIED.some((d) => path.startsWith(d))) {
    throw new RouteError(`fetch: ${path} cannot be reached from here`);
  }
  // The first route the request satisfies: its path, its method and every id it
  // binds. (This run's state is an `own` write; another run's is an `effect`.)
  const candidates = ROUTES.filter((r) => r.path.test(path));
  if (!candidates.length) {
    throw new RouteError(`fetch: ${path} cannot be reached from here`);
  }
  const byMethod = candidates.filter((r) => r.methods.includes(m));
  if (!byMethod.length) {
    throw new RouteError(`fetch: ${m} ${path} cannot be made from here`);
  }
  let route: Route | null = null;
  let groups: Record<string, string> = {};
  let unbound = '';
  for (const r of byMethod) {
    const g = r.path.exec(path)?.groups || {};
    const miss = Object.entries(g).find(
      ([name, value]) => value !== undefined && !bound(name, value, ctx),
    );
    if (!miss) {
      route = r;
      groups = g;
      break;
    }
    if (!unbound) unbound = miss[0];
  }
  if (!route) {
    throw new RouteError(
      `fetch: ${path} names ${NOUNS[unbound] || 'something'} this workflow does not cover`,
    );
  }
  // The scope a request names in its query is bound on EVERY request, reads
  // included: the context middleware both answers in that scope and saves it to
  // the viewer's session, so an unbound one would read another scope and move the
  // viewer's other tabs into it.
  for (const [param, name] of Object.entries({
    ...SCOPE_PARAMS,
    ...BOUND_PARAMS,
    ...(route.boundQuery || {}),
  })) {
    for (const value of parsed.searchParams.getAll(param)) {
      if (!bound(name, value, ctx)) {
        throw new RouteError(
          `fetch: ${path} names ${param}=${value.slice(0, 20)}, which this workflow does not cover`,
        );
      }
    }
  }
  if (route.ownJob && !groups.job) {
    throw new RouteError("fetch: that job is not this run's");
  }
  let canonical: string | undefined;
  let gate: Gate = route.gate;
  let question: string | undefined;
  if (route.bodyCheck || route.bodyGate || route.describe) {
    const obj = jsonObjectBody(body);
    if (route.bodyCheck) route.bodyCheck(obj, ctx);
    gate = (route.bodyGate && route.bodyGate(obj, ctx)) || route.gate;
    if (route.describe) question = route.describe(obj, ctx);
    canonical = JSON.stringify(obj);
  }
  // A write that names no scope lands in THIS workflow's, not in whatever the
  // viewer's session last pointed at.
  if (
    gate !== 'read' &&
    ctx.defaultScope &&
    !Object.keys(SCOPE_PARAMS).some((k) => parsed.searchParams.has(k))
  ) {
    parsed.searchParams.set(ctx.defaultScope.name, ctx.defaultScope.value);
  }
  return {
    href: parsed.href,
    method: m,
    gate,
    kind: route.path.source,
    label: route.label || '',
    body: canonical,
    perRecord: route.perRecord === true,
    recordId: groups.rec ?? groups.xrun,
    recordGrant: route.recordGrant === true,
    filterOpportunities: route.filterOpportunities === true,
    startsJob: route.startsJob === true || /\/job\/start\/$/.test(path),
    remembersPipelines: route.remembersPipelines === true,
    question,
    sandboxFor:
      ctx.definitionId !== null && Number.isSafeInteger(ctx.definitionId)
        ? String(ctx.definitionId)
        : 'none',
    sandboxScope: ctx.defaultScope
      ? `${ctx.defaultScope.name}=${ctx.defaultScope.value}`
      : '',
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
