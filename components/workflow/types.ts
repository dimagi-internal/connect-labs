/**
 * TypeScript types for Workflow components.
 *
 * These types define the contract between Django and React for workflow rendering.
 * Workflows can reference Pipelines as data sources - pipeline data is passed
 * via the `pipelines` prop.
 */

// =============================================================================
// Core Props - What every workflow component receives
// =============================================================================

/**
 * Props passed to every workflow component.
 * This is the main contract between the system and workflow render code.
 */
export interface WorkflowProps {
  /** The workflow definition (structure defined by workflow creator) */
  definition: WorkflowDefinition;

  /** The current workflow instance with state */
  instance: WorkflowInstance;

  /** Workers in this opportunity */
  workers: WorkerData[];

  /** Data from pipeline sources (keyed by alias) */
  pipelines: Record<string, PipelineResult>;

  /** Data from supply sources (keyed by alias; workflow/supply_sources.py) */
  supply?: Record<string, SupplyResult>;

  /** Helper functions for generating URLs to other Labs features */
  links: LinkHelpers;

  /** Action handlers for programmatic operations (create tasks, OCS, etc.) */
  actions: ActionHandlers;

  /** Callback to update workflow instance state */
  onUpdateState: (newState: Record<string, unknown>) => Promise<void>;

  /**
   * Run view — abstracts snapshot-vs-live data reads so render code is the
   * same whether the run is in_progress or completed. Always read run data
   * via this helper rather than the bare `workers`/`pipelines`/`instance`
   * props. See WORKFLOW_REFERENCE.md §"Saved-runs templates".
   */
  view: RunView;
}

/**
 * Helper for reading run data without branching on status.
 *
 * - When `instance.status === 'in_progress'`: returns the live workers,
 *   pipelines, and state passed in via props.
 * - When `instance.status === 'completed'`: returns the workers, pipelines,
 *   and state captured at completion time, derived from `instance.snapshot`.
 *
 * `complete()` is the canonical way to mark a run completed from render
 * code — it confirms with the user, atomically builds + persists the
 * snapshot, and reloads the page so the runner re-mounts in completed mode.
 */
export interface RunView {
  /** Workers — live while in_progress, snapshot-frozen while completed. */
  workers: WorkerData[];

  /** Pipelines (keyed by alias) — live or snapshot-frozen. */
  pipelines: Record<string, PipelineResult>;

  /** Supply sources (keyed by alias) — live or snapshot-frozen. */
  supply?: Record<string, SupplyResult>;

  /** State (working area). Live while in_progress; frozen while completed. */
  state: WorkflowState;

  /** True iff `instance.status === 'completed'`. */
  isCompleted: boolean;

  /** ISO timestamp data is "as of" — completed_at when completed, else now. */
  asOf: string | null;

  /**
   * Mark the run completed. Returns true on success, false on user cancel
   * or server error (errors are surfaced via window.alert for now).
   */
  complete(opts?: { confirm?: string }): Promise<boolean>;

  /**
   * Tell the embedded agent what the page now shows: the worker keys in view
   * (`<opportunity_id>::<username>`) and what is drilled into. A no-op unless
   * the workflow shares its runs (config.agent.share). See
   * workflow/agent_sharing.py.
   */
  /** The actions this workflow offers, for its buttons: pass a `key` to
   * `actions.runAction`. Empty when it declares none. */
  workflowActions?: WorkflowActionSpec[];

  shareSelection?(selection: {
    visible_ids?: string[];
    drilled?: Record<string, unknown>;
  }): void;

  /**
   * Flags raised against this run, newest first. Always queried live from
   * the Flag records (not snapshot-frozen). A Flag is a finding derived
   * from the metrics (`source: 'auto'`) or appended by a human
   * (`source: 'manual'`). Multiple Flags can exist for the same (run, flw).
   */
  flags: Flag[];

  /**
   * Convenience: return all Flags for `username`. Empty array if none.
   * Render code uses this to decide what pills to show in the Flag column.
   */
  flagsFor(username: string): Flag[];

  /**
   * Persist any auto-computed flags that aren't already on the run. The
   * framework dedups by (workflow_run_id, flw_id, flag_key) so calling
   * this on every render is safe — only the first call per (run, flw,
   * flag_key) actually POSTs. Returns the list of newly-created flags.
   *
   * Render code should call this from a React.useEffect on mount, passing
   * the flags computed from the current row data.
   */
  ensureAutoFlags(
    computed: Array<{
      flw_id: string;
      flag_key: string;
      flag_label?: string;
      evidence?: Record<string, unknown>;
    }>,
  ): Promise<Flag[]>;

  /**
   * Audits created against this run. Live-queried from AuditSession
   * records by `labs_record_id == workflow_run_id`; not snapshot-frozen
   * (audits live their own lifecycle and may transition status after
   * the run completes). Render code uses {@link auditsFor} to know
   * whether a per-row "Create Audit" affordance should swap to a
   * "View audit" link to the existing artifact.
   */
  audits: Audit[];

  /**
   * Convenience: return all Audits for `username`. Empty array if none.
   */
  auditsFor(username: string): Audit[];

  /**
   * Tasks created against this run. Same live-query philosophy as audits.
   */
  tasks: Task[];

  /**
   * Convenience: return all Tasks for `username`. Empty array if none.
   */
  tasksFor(username: string): Task[];
}

/**
 * A Flag is a finding attached to one FLW within one workflow run. Flags
 * are typically computed from the metrics by render code on mount and
 * persisted via `view.ensureAutoFlags(...)`. They do not carry audit/task
 * linkage — actions create audit/task records separately.
 */
export interface Flag {
  id: number;
  flw_id: string;
  flag_key: string;
  flag_label: string;
  evidence: Record<string, unknown>;
  source: 'auto' | 'manual';
  flagged_at: string | null;
  flagged_by: string | null;
}

/**
 * An AuditSession created against a workflow run, as seen from the
 * runner's `view.audits` array. Mirrors the per-FLW shape PAR's
 * build_snapshot uses so a template can read both surfaces with the
 * same field names. The link to the run is by `labs_record_id ==
 * workflow_run_id` server-side; render code doesn't need to know that.
 */
export interface Audit {
  id: number;
  flw_id: string;
  status: string;
  overall_result: string | null;
  pass_count: number;
  fail_count: number;
  pending_count: number;
}

/**
 * A Task created against a workflow run, as seen from the runner's
 * `view.tasks` array. The link to the run is by `data.workflow_run_id`
 * server-side. `official_action` reflects the resolution chosen when
 * the task was closed (e.g. "satisfactory", "warned", "suspended").
 */
export interface Task {
  id: number;
  flw_id: string;
  status: string;
  title: string;
  priority: string;
  official_action: string | null;
}

// =============================================================================
// Pipeline Data Types
// =============================================================================

/**
 * One supply source over the workflow's opportunities (workflow/supply_sources.py).
 * `rows` are tagged with opportunity_id (null for a programme-wide row) and
 * program_id; `rollup` is computed on the server (counts summed, rates recomputed).
 */
export interface SupplyResult {
  rows: Array<Record<string, unknown>>;
  rollup: Record<string, unknown>;
  metadata: {
    source?: string;
    scope?: 'opportunity' | 'program';
    item?: string | null;
    as_of?: string | null;
    opportunity_ids?: number[];
    per_opp?: Record<
      string,
      { row_count: number; program_id?: number; error?: string }
    >;
    error?: string;
  };
}

/** Arguments to actions.querySupply. */
export interface SupplyQuery {
  /** Further arguments the source takes (e.g. supply_point_id for worker_stock_get). */
  args?: Record<string, unknown>;
  /** Narrow to one of the workflow's opportunities. */
  opportunity_id?: number;
  /** Read a past day (YYYY-MM-DD). */
  as_of?: string;
}

/**
 * Result from a pipeline execution.
 * Workflows reference pipelines as data sources and receive this structure.
 */
export interface PipelineResult {
  /** Array of data rows from the pipeline */
  rows: PipelineRow[];

  /** Metadata about the pipeline execution */
  metadata: PipelineMetadata;
}

/**
 * A single row from a pipeline result.
 * Structure varies based on pipeline schema and terminal_stage.
 */
export interface PipelineRow {
  /** Username (always present) */
  username: string;

  /** Visit date (for visit_level stage) */
  visit_date?: string;

  /** Visit status */
  status?: string;

  /** Entity ID (for linked visits) */
  entity_id?: string;

  /** Entity name */
  entity_name?: string;

  /** Computed fields from pipeline schema (visit_level) */
  computed?: Record<string, unknown>;

  /** Total visits (for aggregated stage) */
  total_visits?: number;

  /** Approved visits (for aggregated stage) */
  approved_visits?: number;

  /** Pending visits (for aggregated stage) */
  pending_visits?: number;

  /** Rejected visits (for aggregated stage) */
  rejected_visits?: number;

  /** Flagged visits (for aggregated stage) */
  flagged_visits?: number;

  /** First visit date (for aggregated stage) */
  first_visit_date?: string;

  /** Last visit date (for aggregated stage) */
  last_visit_date?: string;

  /** Custom aggregated fields (for aggregated stage) */
  custom_fields?: Record<string, unknown>;

  /** Additional fields */
  [key: string]: unknown;
}

/**
 * Metadata about a pipeline execution.
 */
/**
 * A raw-visit refetch that came back suspiciously smaller than what was
 * already cached, and got rejected in favor of the previous (larger,
 * still-good) data rather than silently overwriting it — see
 * SQLBackend.stream_raw_visits / fetch_raw_visits in
 * connect_labs/labs/analysis/backends/sql/backend.py. This is the shape the
 * backend actually attaches at a single opportunity's scope (`execute_pipeline`
 * in connect_labs/workflow/data_access.py, and each entry of `per_opp` below)
 * — no `opportunity_id`, since at that nesting level it's implied by context.
 */
export interface RawFetchAnomalyDetail {
  previous_count: number;
  attempted_count: number;
  threshold_pct: number;
}

/**
 * The flat, alias-level aggregation of RawFetchAnomalyDetail — see
 * PipelineDataStreamView.stream_data in connect_labs/workflow/views.py, which
 * is the only place `opportunity_id` gets added (spread onto each per_opp
 * entry when building `raw_fetch_anomalies`). `opportunity_id` is a string
 * because it's a JSON object key server-side (per_opp), coerced to string by
 * serialization.
 */
export interface RawFetchAnomaly extends RawFetchAnomalyDetail {
  opportunity_id: string;
}

export interface PipelineMetadata {
  /** Number of rows returned */
  row_count: number;

  /** Whether the data came from cache */
  from_cache: boolean;

  /** Name of the pipeline */
  pipeline_name: string;

  /** Terminal stage: visit_level or aggregated */
  terminal_stage: 'visit_level' | 'aggregated';

  /** Error message if execution failed */
  error?: string;

  /**
   * Set when this pipeline's own fetch (non-multi-opp path — see
   * PipelineDataAccess.execute_pipeline in connect_labs/workflow/data_access.py)
   * hit the raw-fetch shrink guard. Multi-opp pipelines surface this via
   * `raw_fetch_anomalies` / `per_opp` below instead.
   */
  raw_fetch_anomaly?: RawFetchAnomalyDetail;

  /**
   * CommCare HQ auth error, aggregated up from a per-opp failure in a
   * multi-opp workflow so a single check
   * (pipelines[alias].metadata.auth_error) catches it regardless of which
   * opportunity failed — see PipelineDataStreamView.stream_data in
   * connect_labs/workflow/views.py.
   */
  auth_error?: string;
  auth_error_domain?: string;
  auth_authorize_url?: string;

  /** Opportunities this pipeline executed against (multi-opp workflows). */
  opportunity_ids?: number[];

  /**
   * Per-opportunity execution detail, keyed by opportunity_id (a string —
   * JSON object keys are always strings). Only present for pipelines
   * fetched via the multi-opp SSE path.
   */
  per_opp?: Record<
    string,
    {
      row_count?: number;
      from_cache?: boolean;
      error?: string;
      auth_error?: string;
      auth_error_domain?: string;
      raw_fetch_anomaly?: RawFetchAnomalyDetail;
    }
  >;

  /**
   * Same aggregation as auth_error, above, for the raw-fetch shrink guard —
   * a flat list so a generic UI check doesn't need to dig into `per_opp`
   * itself. Empty/absent means no anomaly on any opportunity this pipeline
   * covers.
   */
  raw_fetch_anomalies?: RawFetchAnomaly[];
}

// =============================================================================
// Workflow Definition - Schema defined by creator (flexible)
// =============================================================================

/**
 * Workflow definition stored in LabsRecord.
 * The structure is flexible - creators define what fields they need.
 */
export interface WorkflowDefinition {
  /** Unique identifier */
  id?: number;

  /** Display name */
  name: string;

  /** Description of what this workflow does */
  description: string;

  /** Version number for tracking changes */
  version?: number;

  /** Status options for workers (optional, workflow-defined) */
  statuses?: StatusConfig[];

  /** Configuration options */
  config?: WorkflowConfig;

  /** Pipeline data sources */
  pipeline_sources?: PipelineSource[];
  /** Supply sources (workflow/supply_sources.py): [{alias, source, item?, load?}]. */
  supply_sources?: Array<{
    alias: string;
    source: string;
    item?: string;
    load?: 'eager' | 'on_demand';
  }>;

  /** Whether this workflow is shared with others */
  is_shared?: boolean;

  /** Sharing scope: program, organization, or global */
  shared_scope?: 'program' | 'organization' | 'global';

  /** Additional fields defined by the workflow creator */
  [key: string]: unknown;
}

/**
 * Configuration options for a workflow.
 */
export interface WorkflowConfig {
  /** Show summary cards at top */
  showSummaryCards?: boolean;

  /** Show filter controls */
  showFilters?: boolean;

  /** Additional config options */
  [key: string]: unknown;
}

/**
 * Reference to a pipeline as a data source.
 */
export interface PipelineSource {
  /** ID of the pipeline to fetch data from */
  pipeline_id: number;

  /** Alias used to access the data in render code */
  alias: string;
}

/**
 * Status configuration for worker states.
 */
export interface StatusConfig {
  /** Unique identifier for the status */
  id: string;

  /** Display label */
  label: string;

  /** Color for UI rendering (gray, green, yellow, blue, red, etc.) */
  color: string;
}

// =============================================================================
// Workflow Instance - Running workflow with state
// =============================================================================

/**
 * Workflow instance stored in LabsRecord.
 * Represents a specific execution of a workflow for an opportunity.
 */
export interface WorkflowInstance {
  /** Unique identifier */
  id: number;

  /** Reference to the workflow definition */
  definition_id: number;

  /** Opportunity this instance is for */
  opportunity_id: number;

  /** Current status. `in_progress` is mutable, `completed` is immutable. */
  status: 'in_progress' | 'completed';

  /** Flexible state object - structure defined by the workflow */
  state: WorkflowState;

  /** ISO timestamp the run was completed (null while in_progress). */
  completed_at?: string | null;

  /**
   * Saved snapshot — null while in_progress, populated on completion.
   * Render code reads this via the `useRunView` helper, not directly.
   */
  snapshot?: Record<string, unknown> | null;
}

/**
 * Flexible state object for workflow instance.
 * Structure is defined by the workflow creator.
 */
export interface WorkflowState {
  /** Period start date (ISO format) */
  period_start?: string;

  /** Period end date (ISO format) */
  period_end?: string;

  /** Per-worker state (keyed by username) */
  worker_states?: Record<string, WorkerState>;

  /** Additional state fields defined by the workflow */
  [key: string]: unknown;
}

/**
 * State for a single worker within a workflow.
 * Structure is flexible based on workflow needs.
 */
export interface WorkerState {
  /** Current status (from definition.statuses) */
  status?: string;

  /** Notes about this worker */
  notes?: string;

  /** Reference to audit created from this workflow */
  audit_id?: number;

  /** Reference to task created from this workflow */
  task_id?: number;

  /** Additional fields defined by the workflow */
  [key: string]: unknown;
}

// =============================================================================
// Worker Data - From Connect API
// =============================================================================

/**
 * Worker data fetched from Connect API.
 */
export interface WorkerData {
  /** Unique username (primary identifier) */
  username: string;

  /** Display name */
  name: string;

  /** Total visit count */
  visit_count: number;

  /** Last active date (ISO format) or null */
  last_active: string | null;

  /** Phone number (if available) */
  phone_number?: string;

  /** Email (if available) */
  email?: string;

  /** Approved visits count */
  approved_visits?: number;

  /** Flagged visits count */
  flagged_visits?: number;

  /** Rejected visits count */
  rejected_visits?: number;

  /** Additional fields from API */
  [key: string]: unknown;
}

// =============================================================================
// Link Helpers - Generate URLs to other Labs features
// =============================================================================

/**
 * Helper functions for generating URLs to other Labs features.
 * These allow workflow components to link to audits, tasks, etc.
 */
export interface LinkHelpers {
  /**
   * Generate URL to create an audit.
   */
  auditUrl(params: AuditUrlParams): string;

  /**
   * Generate URL to create a task.
   */
  taskUrl(params: TaskUrlParams): string;
}

/**
 * Parameters for audit URL generation.
 */
export interface AuditUrlParams {
  username?: string;
  usernames?: string;
  count?: number;
  audit_type?: string;
  granularity?: string;
  start_date?: string;
  end_date?: string;
  title?: string;
  tag?: string;
  auto_create?: boolean;
  [key: string]: unknown;
}

/**
 * Parameters for task URL generation.
 */
export interface TaskUrlParams {
  username?: string;
  title?: string;
  description?: string;
  coaching_prompt?: string;
  audit_session_id?: number;
  workflow_instance_id?: number;
  priority?: string;
  [key: string]: unknown;
}

// =============================================================================
// Action Handlers - For programmatic operations
// =============================================================================

/**
 * Action handlers available to workflow components.
 */
export interface ActionHandlers {
  /**
   * Run one of this workflow's declared actions (`view.workflowActions`) for some
   * workers -- e.g. "Initiate AI coach". The runner previews it, shows the person
   * exactly what will happen, and runs it only on their confirm. Resolves with the
   * finished run, or null if they cancelled. The same action an agent runs through
   * the labs MCP's `workflow_run_action`.
   */
  runAction?(
    key: string,
    args: { workers: WorkflowActionWorker[]; [key: string]: unknown },
    options?: WorkflowActionOptions,
  ): Promise<WorkflowActionExecution | null>;

  /**
   * Rows of ONE pipeline alias, filtered, searched, ordered and paged on the
   * server (in SQL) -- the read path for a `load: "on_demand"` source, which the
   * run page does not stream. Resolves `{rows, total}`; rejects with an Error
   * naming what was wrong (an unknown field, no access, a failed build). A cold
   * cache is filled in the background first; the promise waits for it (calling
   * `onStatus` with progress text) for up to `timeoutMs` (default 10 minutes).
   */
  queryPipelineRows?(
    alias: string,
    query?: PipelineRowsQuery,
  ): Promise<PipelineRowsQueryResult>;

  /** One supply source on demand (workflow/supply_sources.py), as the viewer. */
  querySupply?(alias: string, query?: SupplyQuery): Promise<SupplyResult>;

  createTask(params: CreateTaskParams): Promise<TaskResult>;
  checkOCSStatus(): Promise<OCSStatusResult>;
  listOCSBots(): Promise<OCSBotsResult>;
  initiateOCSSession(
    taskId: number,
    params: OCSSessionParams,
  ): Promise<OCSInitiateResult>;
  createTaskWithOCS(
    params: CreateTaskWithOCSParams,
  ): Promise<TaskWithOCSResult>;

  // Job Management Actions
  startJob(
    runId: number,
    jobConfig: Record<string, unknown>,
  ): Promise<StartJobResult>;
  cancelJob(taskId: string, runId?: number): Promise<CancelJobResult>;
  deleteRun(runId: number): Promise<DeleteRunResult>;
  streamJobProgress(
    taskId: string,
    onProgress: (data: JobProgressData) => void,
    onItemResult: (item: Record<string, unknown>) => void,
    onComplete: (results: Record<string, unknown>) => void,
    onError: (error: string) => void,
    onCancelled: () => void,
  ): () => void; // Returns cleanup function

  // Audit Creation Actions
  createAudit(config: CreateAuditConfig): Promise<CreateAuditResult>;
  getAuditStatus(taskId: string): Promise<AuditStatusResult>;
  streamAuditProgress(
    taskId: string,
    onProgress: (data: AuditProgressData) => void,
    onComplete: (result: AuditCreationResult) => void,
    onError: (error: string) => void,
  ): () => void; // Returns cleanup function
  cancelAudit(taskId: string): Promise<{ success: boolean; error?: string }>;

  // MBW Monitoring Actions
  saveWorkerResult(
    runId: number,
    params: SaveWorkerResultParams,
  ): Promise<SaveWorkerResultResponse>;
  completeRun(
    runId: number,
    params?: CompleteRunParams,
  ): Promise<CompleteRunResponse>;
  openTaskCreator(params: TaskUrlParams): void;

  // Generic Task Management (reusable by any workflow template)
  getTaskDetail(taskId: number): Promise<Record<string, unknown>>;
  getAITranscript(
    taskId: number,
    sessionId?: string,
    refresh?: boolean,
  ): Promise<Record<string, unknown>>;
  getAISessions(taskId: number): Promise<Record<string, unknown>>;
  updateTask(
    taskId: number,
    data: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;
  saveAITranscript(
    taskId: number,
    data: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;
}

export interface PipelineRowsQuery {
  /** `{field: value}` or `{field: [v1, v2]}` (any of); null matches a missing value. */
  filters?: Record<
    string,
    string | number | boolean | null | Array<string | number | boolean | null>
  >;
  /** Case-insensitive substring match over `fields` (default: every declared field). */
  search?: string | { text: string; fields?: string[] };
  /** A field, `-field` for descending, or a list of them. */
  order_by?: string | string[];
  /** Page size, 1-500 (default 100). */
  limit?: number;
  offset?: number;
  /** Which opportunity of a multi-opp workflow to read. */
  opportunity_id?: number;
  onStatus?: (message: string) => void;
  timeoutMs?: number;
}

export interface PipelineRowsQueryResult {
  rows: Record<string, unknown>[];
  /** Rows matching the filters and search, before paging. */
  total: number;
  limit: number;
  offset: number;
}

export interface CreateTaskParams {
  username: string;
  title: string;
  description?: string;
  priority?: 'low' | 'medium' | 'high';
  flw_name?: string;
}

export interface TaskResult {
  success: boolean;
  task_id?: number;
  error?: string;
}

export interface OCSStatusResult {
  connected: boolean;
  login_url?: string;
  error?: string;
}

export interface OCSBotsResult {
  success: boolean;
  bots?: OCSBot[];
  needs_oauth?: boolean;
  error?: string;
}

export interface OCSBot {
  id: string;
  name: string;
  version?: number;
}

export interface OCSSessionParams {
  identifier: string;
  experiment: string;
  prompt_text: string;
  platform?: string;
  start_new_session?: boolean;
}

export interface OCSInitiateResult {
  success: boolean;
  message?: string;
  error?: string;
}

export interface CreateTaskWithOCSParams extends CreateTaskParams {
  ocs?: Omit<OCSSessionParams, 'identifier'>;
}

export interface TaskWithOCSResult extends TaskResult {
  ocs?: OCSInitiateResult;
}

// =============================================================================
// Job Management Types
// =============================================================================

/**
 * Result from starting a job.
 */
export interface StartJobResult {
  success: boolean;
  task_id?: string;
  run_id?: number;
  error?: string;
}

/**
 * Result from cancelling a job.
 */
export interface CancelJobResult {
  success: boolean;
  error?: string;
}

/**
 * Result from deleting a run.
 */
export interface DeleteRunResult {
  success: boolean;
  error?: string;
}

/**
 * Progress data from job execution.
 */
export interface JobProgressData {
  status: string;
  current_stage?: number;
  total_stages?: number;
  stage_name?: string;
  processed?: number;
  total?: number;
  message?: string;
}

/**
 * Configuration for starting a job.
 */
export interface JobConfig {
  job_type: string;
  params?: Record<string, unknown>;
  pipeline_source?: {
    pipeline_id?: number;
    alias?: string;
  };
  records?: Record<string, unknown>[];
}

// =============================================================================
// Audit Creation Types
// =============================================================================

/**
 * Configuration for creating an audit asynchronously.
 */
export interface CreateAuditConfig {
  /** Opportunities to audit (with id and optional name) */
  opportunities: Array<{ id: number; name?: string }>;

  /** Audit criteria */
  criteria: Record<string, unknown>;

  /** Pre-computed visit IDs (optional) */
  visit_ids?: number[];

  /** Pre-computed FLW to visit IDs mapping (optional) */
  flw_visit_ids?: Record<string, number[]>;

  /** Values to override in the template (e.g., date ranges from workflow) */
  template_overrides?: Record<string, unknown>;

  /** Workflow run ID if triggered from a workflow */
  workflow_run_id?: number;

  /** AI agent ID to run after audit creation (optional) */
  ai_agent_id?: string;
}

/**
 * Result from creating an audit.
 */
export interface CreateAuditResult {
  success: boolean;
  task_id?: string;
  error?: string;
}

/**
 * Result from getting audit task status.
 */
export interface AuditStatusResult {
  status: string;
  message?: string;
  current_stage?: number;
  total_stages?: number;
  stage_name?: string;
  processed?: number;
  total?: number;
  result?: AuditCreationResult;
  error?: string;
}

/**
 * Progress data from audit creation.
 */
export interface AuditProgressData {
  status: string;
  message?: string;
  current_stage?: number;
  total_stages?: number;
  stage_name?: string;
  processed?: number;
  total?: number;
}

/**
 * Final result from audit creation.
 */
export interface AuditCreationResult {
  success?: boolean;
  template_id?: number;
  sessions?: Array<{
    id: number;
    title: string;
    visits: number;
    images: number;
  }>;
  total_visits?: number;
  total_images?: number;
  error?: string;
}

/**
 * Active job state stored in workflow instance state.
 */
export interface ActiveJobState {
  job_id?: string;
  job_type?: string;
  status?: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  started_at?: string;
  completed_at?: string;
  failed_at?: string;
  cancelled_at?: string;
  cancelled_by?: string;
  current_stage?: number;
  total_stages?: number;
  stage_name?: string;
  processed?: number;
  total?: number;
  error?: string;
  pipeline_loaded?: boolean;
  pipeline_record_count?: number;
  summary?: {
    successful?: number;
    failed?: number;
  };
}

// =============================================================================
// MBW Monitoring Types
// =============================================================================

export interface SaveWorkerResultParams {
  username: string;
  result: 'eligible_for_renewal' | 'probation' | 'suspended' | null;
  notes?: string;
}

export interface SaveWorkerResultResponse {
  success: boolean;
  worker_results?: Record<
    string,
    {
      result: string | null;
      notes: string;
      assessed_by: number;
      assessed_at: string;
    }
  >;
  progress?: { percentage: number; assessed: number; total: number };
  error?: string;
}

/**
 * Body for POST /api/run/<id>/complete/. The endpoint takes no input —
 * the snapshot is built server-side from the template's hook and the
 * current pipelines/workers/state. Kept as an empty interface for
 * forward compatibility (e.g. future per-completion notes).
 */
export interface CompleteRunParams {}

export interface CompleteRunResponse {
  success: boolean;
  status?: 'completed';
  completed_at?: string;
  snapshot?: Record<string, unknown>;
  error?: string;
}

// =============================================================================
// API Response Types
// =============================================================================

export interface UpdateStateResponse {
  success: boolean;
  instance?: {
    id: number;
    state: WorkflowState;
  };
  error?: string;
}

export interface GetWorkersResponse {
  workers: WorkerData[];
  error?: string;
}

// =============================================================================
// Utility Types
// =============================================================================

export type WorkflowComponent = React.FC<WorkflowProps>;

/**
 * Data passed from Django template to React.
 */
/** One action a workflow offers (connect_labs/workflow/actions.py): its own
 * name for it, the framework action it is, and what its button says. */
export interface WorkflowActionSpec {
  key: string;
  type: string;
  label: string;
  description: string;
}

/** A worker named in an action's arguments, with any text of its own. */
export interface WorkflowActionWorker {
  key: string;
  prompt?: string;
  title?: string;
  description?: string;
  /** Coach this worker about ONE case (workflow/case_coaching.py). */
  case?: {
    id: string;
    story?: string;
    earlier?: { date: string; label: string; agreed?: string };
  };
}

/** How the run page's dialog offers an action (`actions.runAction`'s third argument). */
export interface WorkflowActionOptions {
  /** Offer only "Send to me (QA test)": the viewer's own PersonalID username as
   * `deliver_to`, and no send to the worker. */
  qaOnly?: boolean;
}

/** What running an action would do (POST .../actions/<key>/preview/). */
export interface WorkflowActionPreview {
  action: string;
  type: string;
  label: string;
  summary: string;
  workers: Array<{
    key: string;
    name: string;
    opportunity_id: number;
    prompt?: string;
    title?: string;
    /** Set on a QA redirect (`deliver_to`): where this worker's conversation goes. */
    sending_to?: string;
    /** A coaching briefing's fixed first message: what the worker receives first
     * (the briefing itself goes into the conversation's state, not to the worker). */
    opening?: string;
    /** A coaching briefing in plain words for the person confirming (display only;
     * `prompt` is the exact text the bot receives). */
    briefing?: {
      topics: Array<{
        label: string;
        figure: string;
        band: string;
        /** "off target" (red), "on watch" (yellow). */
        status: string;
      }>;
      note?: string;
    };
    /** Indicator keys the conversation covers (a coaching briefing's topics). */
    indicators?: string[];
    /** A case conversation: the case, its story and the facts behind it. */
    case?: {
      id: string;
      case?: string;
      topic?: string;
      story?: string;
      facts?: string;
    };
    /** The picture sent with the conversation: a signed Labs link and its caption. */
    image?: { url?: string; caption?: string };
  }>;
  /** Workers asked for but left out, and why (e.g. "nothing off target"). */
  skipped?: Array<{ key: string; name: string; reason: string }>;
  arguments: Record<string, unknown> & { workers: WorkflowActionWorker[] };
  /** What must be settled before it can be confirmed: `bot`, `connect_ocs`. */
  needs: string[];
  bot?: { id: string; name: string };
  bot_choices?: Array<{ id: string; name: string }>;
  unknown_bot?: string;
  connect_url?: string;
  synthetic?: boolean;
  /** On synthetic data: what happens instead of a real conversation. */
  synthetic_note?: string;
  /** The person may redirect this one conversation to themselves (`deliver_to`, Dimagi staff). */
  qa_redirect?: boolean;
  /** The QA recipient, when the preview is redirected. */
  deliver_to?: string;
  /** Present only when nothing is needed: the single-use token that runs it. */
  confirm?: string;
}

/** An action run (WorkflowActionExecution.as_dict()). */
export interface WorkflowActionExecution {
  id: number;
  action: string;
  type: string;
  status:
    'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed';
  via: string;
  run_id: number;
  progress: { total: number; done: number; ok: number; failed: number };
  results: Record<
    string,
    {
      status: 'ok' | 'failed';
      task_id?: number;
      session_id?: string | null;
      error?: string;
    }
  >;
  error: string;
}

export interface WorkflowDataFromDjango {
  definition: WorkflowDefinition;
  definition_id: number;
  opportunity_id?: number;
  instance: {
    id: number;
    definition_id: number;
    opportunity_id: number;
    status: string;
    state: WorkflowState;
    completed_at?: string | null;
    snapshot?: Record<string, unknown> | null;
  };
  workers: WorkerData[];
  pipeline_data?: Record<string, PipelineResult>;
  /** Flags raised against the current run. Optional — defaults to [] in
   * useRunView when the BE response omits it. */
  flags?: Flag[];
  /** Audits created against the current run (link is by
   * `labs_record_id == workflow_run_id` server-side). Optional —
   * defaults to [] in useRunView when the BE response omits it. */
  audits?: Audit[];
  /** Tasks created against the current run. Optional — defaults to []
   * in useRunView when the BE response omits it. */
  tasks?: Task[];
  links: {
    auditUrlBase: string;
    taskUrlBase: string;
  };
  apiEndpoints: {
    updateState: string | null;
    getWorkers: string;
    getPipelineData?: string;
    streamPipelineData?: string;
    /** POST endpoint behind actions.queryPipelineRows. */
    queryPipelineRows?: string;
    /** GET endpoint for the eager supply sources (the `supply` prop). */
    getSupplyData?: string;
    /** POST endpoint behind actions.querySupply. */
    querySupply?: string;
    saveWorkerResult?: string;
    completeRun?: string | null;
    getSnapshot?: string | null;
    /** Base URL of this run's workflow actions (workflow/actions.py). */
    actionBase?: string;
    actionExecutionBase?: string;
  };
  render_code?: string;
  is_edit_mode?: boolean;
  /** The actions this workflow offers (workflow/actions.py); absent in edit mode. */
  actions?: WorkflowActionSpec[];
}
