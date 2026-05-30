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
    saveWorkerResult?: string;
    completeRun?: string | null;
    getSnapshot?: string | null;
  };
  render_code?: string;
  is_edit_mode?: boolean;
}
