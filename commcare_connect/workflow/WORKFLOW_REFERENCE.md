# Workflow Engine Reference

This is the single source of truth for building workflow templates in CommCare Connect Labs. It is consumed by Claude Code (via the workflow-templates skill), the in-product AI agent (loaded at module init), and developers reading documentation. A workflow template is a self-contained Python file that declares a data pipeline schema, a workflow definition, and a React render function. The pipeline engine extracts, transforms, and aggregates data from CommCare form submissions. The render code receives that data as props and displays it using React with Tailwind CSS.

---

## 1. Template Anatomy

Each template is a single `.py` file in `commcare_connect/workflow/templates/`. Files are auto-discovered by `__init__.py` via `pkgutil.iter_modules` -- any module in that directory that exports a `TEMPLATE` dict will be registered. Modules starting with `_` or named `base` are skipped.

### Required Exports

| Export        | Type   | Description                                              |
| ------------- | ------ | -------------------------------------------------------- |
| `DEFINITION`  | `dict` | Workflow definition: name, description, statuses, config |
| `RENDER_CODE` | `str`  | JSX string defining the `WorkflowUI` function component  |
| `TEMPLATE`    | `dict` | Registry entry that bundles everything together          |

### Optional Exports

| Export             | Type         | Description                                                     |
| ------------------ | ------------ | --------------------------------------------------------------- |
| `PIPELINE_SCHEMA`  | `dict`       | Single pipeline schema (simple templates)                       |
| `PIPELINE_SCHEMAS` | `list[dict]` | Multiple pipeline schemas with aliases (multi-source templates) |

The `TEMPLATE` dict itself also accepts one optional key:

| Key         | Type   | Default | Description                                                                                                |
| ----------- | ------ | ------- | ---------------------------------------------------------------------------------------------------------- |
| `multi_opp` | `bool` | `False` | Opt in to multi-opportunity support. See [§8 Multi-opportunity workflows](#8-multi-opportunity-workflows). |

### Minimal Example (Single Pipeline)

```python
"""My Workflow Template."""

PIPELINE_SCHEMA = {
    "name": "Worker Metrics",
    "description": "Aggregated metrics per worker",
    "version": 1,
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [
        {
            "name": "visit_count",
            "path": "form.meta.instanceID",
            "aggregation": "count",
            "description": "Total form submissions",
        },
        {
            "name": "last_visit_date",
            "path": "form.meta.timeEnd",
            "aggregation": "last",
            "description": "Most recent submission date",
        },
    ],
    "histograms": [],
    "filters": {},
}

DEFINITION = {
    "name": "My Workflow",
    "description": "Review worker performance",
    "version": 1,
    "templateType": "my_workflow",
    "statuses": [
        {"id": "pending", "label": "Pending", "color": "gray"},
        {"id": "confirmed", "label": "Confirmed", "color": "green"},
    ],
    "config": {
        "showSummaryCards": True,
        "showFilters": True,
    },
    "pipeline_sources": [],  # Populated at creation time
}

RENDER_CODE = """function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    var workerStates = instance.state?.worker_states || {};

    return (
        <div className="space-y-4">
            <h1 className="text-2xl font-bold">{definition.name}</h1>
            <p className="text-gray-600">{definition.description}</p>
            <div className="text-sm text-gray-500">{workers.length} workers</div>
        </div>
    );
}"""

TEMPLATE = {
    "key": "my_workflow",
    "name": "My Workflow",
    "description": "Review worker performance",
    "icon": "fa-clipboard-check",
    "color": "green",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
}
```

### Multi-Pipeline Example

Use `PIPELINE_SCHEMAS` (plural) when your template needs data from multiple sources. Each schema gets an `alias` used to access it in render code as `pipelines.<alias>`.

```python
PIPELINE_SCHEMAS = [
    {
        "alias": "visits",
        "name": "Visit Data",
        "description": "Per-visit data from Connect CSV",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "linking_field": "beneficiary_case_id",
            "fields": [
                {"name": "beneficiary_case_id", "path": "form.case.@case_id", "aggregation": "first"},
                {"name": "weight", "path": "form.weight", "aggregation": "first", "transform": "float"},
            ],
        },
    },
    {
        "alias": "registrations",
        "name": "Registration Forms",
        "description": "Registration data from CommCare HQ",
        "schema": {
            "data_source": {
                "type": "cchq_forms",
                "form_name": "Register Mother",
                "app_id_source": "opportunity",
            },
            "grouping_key": "case_id",
            "terminal_stage": "visit_level",
            "fields": [
                {"name": "mother_name", "path": "form.mother_name", "aggregation": "first"},
            ],
        },
    },
]

TEMPLATE = {
    "key": "my_multi_pipeline",
    "name": "Multi-Source Workflow",
    "description": "Uses multiple data sources",
    "icon": "fa-chart-line",
    "color": "blue",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,  # Note: plural key
}
```

---

## 2. Pipeline Schema Deep-Dive

A pipeline schema defines how raw form submission data is extracted, transformed, and aggregated. When using `PIPELINE_SCHEMAS` (multi-source), each entry wraps a schema with metadata:

```python
{
    "alias": "visits",                    # Key for accessing data: pipelines.visits
    "name": "Display Name",              # Human-readable name
    "description": "What this provides", # Shown in pipeline editor UI
    "schema": { ... }                    # The actual schema (documented below)
}
```

### Full Schema Structure

```python
{
    "data_source": {
        "type": "connect_csv",            # or "cchq_forms"
        "form_name": "Register Mother",   # cchq_forms only: form name for xmlns lookup
        "app_id_source": "opportunity",   # cchq_forms only: derive app_id from opportunity
        "app_id": "",                     # cchq_forms only: explicit app ID
        "gs_app_id": "",                  # cchq_forms only: Gold Standard supervisor app ID
    },
    "grouping_key": "username",           # "username", "entity_id", "case_id", or "deliver_unit_id"
    "terminal_stage": "visit_level",      # "visit_level" or "aggregated"
    "linking_field": "beneficiary_case_id",  # Optional: links visits to a logical entity
    "fields": [ ... ],                    # Field definitions (see below)
    "histograms": [ ... ],               # Optional histogram computations
    "filters": {},                        # Optional global filters, e.g. {"status": ["approved"]}
}
```

### Schema Fields Reference

#### `data_source`

| Property        | Values          | Description                                                                                                                   |
| --------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `type`          | `"connect_csv"` | Fetch from Connect production paginated JSON export (default). Token name predates the v2 migration; most templates use this. |
| `type`          | `"cchq_forms"`  | Fetch from CommCare HQ Form API. Requires `form_name` or `app_id`.                                                            |
| `form_name`     | string          | (cchq_forms only) Human-readable form name, e.g., `"Register Mother"`. Used for xmlns discovery.                              |
| `app_id_source` | `"opportunity"` | (cchq_forms only) Derive the CommCare app ID from opportunity metadata.                                                       |
| `app_id`        | string          | (cchq_forms only) Explicit CommCare application ID.                                                                           |
| `gs_app_id`     | string          | (cchq_forms only) Explicit Gold Standard supervisor app ID.                                                                   |

#### `grouping_key`

How visits are grouped before aggregation. Determines the primary key of output rows.

| Value               | Description                                 |
| ------------------- | ------------------------------------------- |
| `"username"`        | Group by FLW username. Most common.         |
| `"entity_id"`       | Group by Connect entity ID.                 |
| `"case_id"`         | Group by CommCare case ID (for cchq_forms). |
| `"deliver_unit_id"` | Group by delivery unit.                     |

#### `terminal_stage`

Controls what the pipeline outputs and how custom fields are structured in the row.

| Value           | Output                                                                                                                                                                                                                                    | Row shape                                                             |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `"visit_level"` | One row per visit. Custom fields in row's `computed` dict (flattened to top-level in JSON).                                                                                                                                               | `{ username, visit_date, entity_id, weight, height, ... }`            |
| `"aggregated"`  | One row per FLW (`GROUP BY username`). Custom fields in row's `custom_fields` dict (flattened to top-level in JSON). Standard counters: total_visits, approved/pending/rejected/flagged, first/last_visit_date.                           | `{ username, total_visits, approved_visits, avg_weight, ... }`        |
| `"entity"`      | One row per entity (`GROUP BY linking_field`). Custom fields in row's `custom_fields` dict (flattened to top-level in JSON). Standard counters: total_visits, first/last_visit_date, plus a representative `username` (first per entity). | `{ entity_id, entity_name, username, total_visits, child_name, ... }` |

**Important:** In the JSON response sent to the frontend, `computed` (visit_level) and `custom_fields` (aggregated, entity) are **flattened** into the top-level row object. So in render code, you access fields directly as `row.weight`, `row.visit_count`, etc. — not as `row.computed.weight` or `row.custom_fields.visit_count`.

**Entity stage** is for analyses whose unit of interest is a tracked thing — a beneficiary case, a child, a household — rather than the worker who served them. The pipeline groups raw visits by `linking_field` and applies the same aggregation vocabulary (`first/last/sum/avg/count/...`) used at FLW stage. The status/flagged counters are dropped because they're visit-level facts; templates that need approved-counts at entity level declare them as custom `FieldComputation`s with `filter_path/filter_value`.

#### `linking_field`

Identifies the column used by the entity stage (and as a hint for visit-level dashboards). Resolution:

1. If the value matches a base column on `labs_raw_visit_cache` (`entity_id`, `username`, `deliver_unit_id`, etc.) — that column is used directly.
2. Otherwise the value must match the `name` of a `FieldComputation` declared in `fields`. The pipeline takes that field's `paths` and builds the GROUP BY expression from the JSONB extraction.

Required when `terminal_stage = "entity"`. Default is `"entity_id"`.

Example: In KMC tracking, each visit has a `beneficiary_case_id` that identifies the child. Setting `"linking_field": "beneficiary_case_id"` along with a corresponding `FieldComputation` named `beneficiary_case_id` allows the entity stage to emit one row per child:

```python
PIPELINE_SCHEMAS = [{
    "alias": "children",
    "name": "Children with KMC follow-up",
    "schema": {
        "data_source": {"type": "connect_csv"},
        "grouping_key": "username",                       # bookkeeping; entity stage uses linking_field
        "terminal_stage": "entity",
        "linking_field": "beneficiary_case_id",
        "fields": [
            {
                "name": "beneficiary_case_id",
                "paths": ["form.case.@case_id", "form.kmc_beneficiary_case_id"],
                "aggregation": "first",
            },
            # Demographics — picked from the earliest visit (first by visit_date, then visit_id)
            {"name": "child_name",   "path": "form.grp_kmc_beneficiary.child_name",   "aggregation": "first"},
            {"name": "mother_name",  "path": "form.grp_beneficiary_details.mother_name", "aggregation": "first"},
            {"name": "child_dob",    "path": "form.grp_beneficiary_details.child_dob",   "aggregation": "first"},
            # Most recent values — picked from the latest visit
            {"name": "current_weight", "path": "form.weight", "aggregation": "last"},
            {"name": "kmc_status",     "path": "form.kmc_status", "aggregation": "last"},
        ],
    },
}]
```

Output rows shape:

```json
{
  "entity_id": "case-uuid-123",
  "entity_name": "...",
  "username": "alice",
  "total_visits": 4,
  "first_visit_date": "2026-03-01",
  "last_visit_date": "2026-04-10",
  "child_name": "Asha",
  "mother_name": "Priya",
  "child_dob": "2026-02-15",
  "current_weight": "3.2",
  "kmc_status": "ongoing"
}
```

**`first` / `last` semantics at entity stage.** For each entity group, pick the value from the visit with the earliest (`first`) or latest (`last`) `visit_date`. Ties on `visit_date` are broken by `visit_id` (ASC for `first`, DESC for `last`) — so demographics from the registration visit and current values from the most recent visit are deterministic.

### Field Definition Reference

```python
{
    "name": "field_name",             # REQUIRED. Name used in output rows.
    "path": "form.xpath.to.value",    # Dot-notated JSON path into form submission.
    "paths": ["form.path1", "form.path2"],  # Alternative: fallback paths (tried in order).
    "aggregation": "first",           # REQUIRED. How values are combined.
    "transform": "float",             # Optional. Value transformation before aggregation.
    "filter_path": "form.field",      # Optional. Only include rows where this path...
    "filter_value": "yes",            # ...equals this value.
    "description": "Human label",     # Optional. Documentation only.
    "default": null,                  # Optional. Default value if extraction yields null.
}
```

**`path` vs `paths`:** Use `path` (singular) when the field is always at the same JSON path. Use `paths` (plural) when the same data might be at different paths in different form versions. The engine tries each path in order and uses the first non-null value (COALESCE behavior).

```python
# Single path
{"name": "weight", "path": "form.anthropometric.child_weight", "aggregation": "first"}

# Multiple fallback paths
{
    "name": "weight",
    "paths": [
        "form.anthropometric.child_weight_visit",   # Visit form
        "form.child_details.birth_weight_reg.child_weight_reg",  # Registration form
    ],
    "aggregation": "first",
}
```

### Aggregation Types

| Aggregation      | Description                                | Output type   |
| ---------------- | ------------------------------------------ | ------------- |
| `first`          | First non-null value (chronological order) | same as input |
| `last`           | Last non-null value (chronological order)  | same as input |
| `count`          | Count of non-null values                   | int           |
| `count_unique`   | Count of distinct non-null values          | int           |
| `count_distinct` | Alias for `count_unique`                   | int           |
| `sum`            | Sum of numeric values                      | float         |
| `avg`            | Average of numeric values                  | float         |
| `min`            | Minimum value                              | same as input |
| `max`            | Maximum value                              | same as input |
| `list`           | Collect all values into a list             | list          |
| `median`         | Interpolated 50th percentile               | float         |
| `mode`           | Most frequent non-null value               | same as input |
| `mode_share`     | Share (0..1) of rows equal to the mode     | float         |

### Transform Types

Transforms are applied to raw extracted values **before** aggregation.

| Transform   | Description                                                              |
| ----------- | ------------------------------------------------------------------------ |
| `"float"`   | Parse to float. Returns `None` if not a valid number.                    |
| `"int"`     | Parse to int (via float). Returns `None` if not valid.                   |
| `"kg_to_g"` | Multiply by 1000 (kilogram to gram conversion). Validates numeric first. |
| `"date"`    | Date parsing. Handled by the pipeline date processing.                   |
| `"string"`  | Convert to string.                                                       |
| _(omit)_    | No transform; raw string value is used.                                  |

### Conditional Field Extraction (`filter_path` / `filter_value`)

Only count or aggregate rows where a specific field matches a value. Useful for computing conditional metrics.

```python
# Count visits where danger signs were positive
{
    "name": "danger_positive_count",
    "path": "form.danger_signs_checklist.danger_sign_positive",
    "aggregation": "count",
    "filter_path": "form.danger_signs_checklist.danger_sign_positive",
    "filter_value": "yes",
}

# Count distinct cases where child is not alive
{
    "name": "deaths",
    "paths": ["form.kmc_beneficiary_case_id", "form.case.@case_id"],
    "aggregation": "count_distinct",
    "filter_path": "form.child_alive",
    "filter_value": "no",
}
```

### Histogram Computations

Histograms bin numeric values into ranges and produce count fields for each bin plus summary statistics.

```python
{
    "name": "muac_distribution",
    "path": "form.case.update.soliciter_muac_cm",
    "paths": ["form.case.update.soliciter_muac_cm", "form.subcase_0.case.update.soliciter_muac"],
    "lower_bound": 9.5,
    "upper_bound": 21.5,
    "num_bins": 12,
    "bin_name_prefix": "muac",
    "transform": null,
    "description": "MUAC measurement distribution",
    "include_out_of_range": true,  # Count values outside bounds in first/last bin
}
```

Produces fields like `muac_9_5_10_5_visits`, `muac_10_5_11_5_visits`, etc.

---

## 3. Discovering Field Paths

Field paths map form questions to their JSON submission structure. Getting these right is critical -- wrong paths produce empty data.

### MCP Server (Claude Code)

The CommCare MCP server provides tools to discover exact JSON paths:

1. **Get opportunity apps:**

   ```
   get_opportunity_apps(opportunity_id=874) -> { cc_domain, learn_app_id, deliver_app_id }
   ```

2. **Get app structure:**

   ```
   get_app_structure(domain, app_id) -> modules, forms, xmlns
   ```

3. **Get form JSON paths (key tool):**
   ```
   get_form_json_paths(xmlns, domain, app_id) -> [
       { json_path: "form.weight", type: "Int", label: "Weight (grams)" },
       { json_path: "form.child_info.birth_weight", type: "Decimal", label: "Birth Weight" },
       ...
   ]
   ```

Use `json_path` values directly in pipeline schema field definitions.

### Manual Path Construction

CommCare form questions map to JSON paths following these rules:

- Top-level question `weight` becomes `form.weight`
- Question inside group `anthropometric` becomes `form.anthropometric.weight`
- Nested groups: `form.group1.group2.question_id`
- Case properties: `form.case.update.property_name`
- Case ID: `form.case.@case_id`

### Common Meta Paths

| Path                          | Description                            |
| ----------------------------- | -------------------------------------- |
| `form.meta.timeEnd`           | Submission timestamp                   |
| `form.meta.instanceID`        | Unique form submission ID              |
| `form.meta.location.#text`    | GPS coordinates (lat lon alt accuracy) |
| `form.meta.appVersion`        | CommCare app version string            |
| `form.meta.app_build_version` | App build version number               |
| `form.case.@case_id`          | CommCare case ID                       |
| `form.case.update.*`          | Case property updates                  |
| `form.@name`                  | Form name                              |
| `metadata.location`           | Alternative GPS location path          |

---

## 4. Render Code Contract

The render code is a JSX string that defines a React function component. It is transpiled at runtime by Babel standalone and evaluated in the browser.

### Function Signature

```javascript
function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState })
```

### Constraints

- The function **must** be named `WorkflowUI` (not a variable assignment)
- Use `var` for all variable declarations (`const` and `let` work in modern browsers but `var` is the safest choice for Babel standalone + eval)
- No imports -- only `React` is available as a global
- CDN libraries available via `window`: Chart.js 4.4.0 (`window.Chart`), chartjs-adapter-date-fns 3.0.0, Leaflet 1.9.4 (`window.L`)
- Tailwind CSS classes are available for styling
- All React hooks are accessed via `React.useState`, `React.useEffect`, `React.useMemo`, `React.useRef`, `React.useCallback`

### Props Reference

| Prop            | Type                             | Description                                                                          |
| --------------- | -------------------------------- | ------------------------------------------------------------------------------------ |
| `definition`    | `WorkflowDefinition`             | Workflow config: `name`, `description`, `statuses[]`, `config`, `pipeline_sources[]` |
| `instance`      | `WorkflowInstance`               | Current run: `id`, `definition_id`, `opportunity_id`, `status`, `state`              |
| `workers`       | `WorkerData[]`                   | Workers: `username`, `name`, `visit_count`, `last_active`, `phone_number`            |
| `pipelines`     | `Record<string, PipelineResult>` | Pipeline data keyed by alias                                                         |
| `links`         | `LinkHelpers`                    | URL builders: `links.auditUrl(params)`, `links.taskUrl(params)`                      |
| `actions`       | `ActionHandlers`                 | Action methods (see Section 5)                                                       |
| `onUpdateState` | `(newState) => Promise<void>`    | Merge-save instance state                                                            |

### Pipeline Data Access

Pipeline data is keyed by alias. Each pipeline result has `rows` (array) and `metadata` (object).

```javascript
// Access visit-level pipeline
var visitData = pipelines?.visits?.rows || [];
// Each row (visit_level): { username, visit_date, entity_id, weight, height, ... }

// Access aggregated pipeline
var metrics = pipelines?.metrics?.rows || [];
// Each row (aggregated): { username, total_visits, approved_visits, avg_weight, ... }

// Check metadata
var rowCount = pipelines?.visits?.metadata?.row_count || 0;
var fromCache = pipelines?.visits?.metadata?.from_cache;
var pipelineName = pipelines?.visits?.metadata?.pipeline_name;
```

**Custom fields are flattened into the top-level row object.** Access them directly:

```javascript
// Correct -- fields are at top level
var weight = row.weight;
var caseId = row.beneficiary_case_id;

// Wrong -- these nested paths do not exist in frontend JSON
var weight = row.computed.weight; // NO
var count = row.custom_fields.count; // NO
```

### Built-in Row Fields

Visit-level rows always include: `username`, `visit_date`, `entity_id`, `entity_name`.

Aggregated rows always include: `username`, `total_visits`, `approved_visits`, `pending_visits`, `rejected_visits`, `flagged_visits`, `first_visit_date`, `last_visit_date`.

All custom fields from your schema are flattened in alongside these.

### State Management

Instance state persists across page loads. `onUpdateState` performs a merge (not a replace).

```javascript
// Save state (merges with existing state)
await onUpdateState({
  worker_states: {
    ...workerStates,
    [username]: { status: 'reviewed', notes: 'Looks good' },
  },
});

// Read state
var workerStates = instance.state?.worker_states || {};
var periodStart = instance.state?.period_start;
```

### Link Helpers

```javascript
// Generate audit creation URL
var auditLink = links.auditUrl({ username: worker.username, count: 5 });
var auditLink = links.auditUrl({
  usernames: 'user1,user2',
  count: 10,
  audit_type: 'random',
  start_date: '2026-01-01',
  end_date: '2026-03-01',
  title: 'Weekly Review',
  tag: 'performance',
  auto_create: true,
});

// Generate task creation URL
var taskLink = links.taskUrl({
  username: worker.username,
  title: 'Follow up on missed visits',
  description: 'Worker has 3 missed visits this week',
  priority: 'high',
});
```

---

## 5. Actions API

All action methods are available on the `actions` prop. They make API calls to the Labs backend.

### Task Management

```javascript
// Create a task programmatically
var result = await actions.createTask({
  username: 'worker123', // Required
  title: 'Follow up needed', // Required
  description: '...', // Optional
  priority: 'medium', // Optional: "low" | "medium" | "high"
  flw_name: 'Worker Name', // Optional: display name
});
// Returns: { success: boolean, task_id?: number, error?: string }

// Open task creation form in new tab
actions.openTaskCreator({
  username: 'worker123',
  title: 'Follow up needed',
  description: '...',
  priority: 'high',
  workflow_instance_id: instance.id,
});
// Returns: void (opens new browser tab)

// Get task details
var task = await actions.getTaskDetail(taskId);
// Returns: task object or { success: false, error: string }

// Update a task
var updated = await actions.updateTask(taskId, {
  status: 'completed',
  notes: 'Done',
});
// Returns: updated task object or { success: false, error: string }
```

### Audit Creation

```javascript
// Create an audit asynchronously (returns task_id for progress tracking)
var result = await actions.createAudit({
  opportunities: [{ id: 874, name: 'My Opportunity' }],
  criteria: { count: 5, audit_type: 'random' },
  visit_ids: [1, 2, 3], // Optional: pre-selected visit IDs
  flw_visit_ids: { user1: [1, 2] }, // Optional: per-FLW visit IDs
  template_overrides: { start_date: '...' }, // Optional: override template values
  workflow_run_id: instance.id, // Optional: link to workflow run
  ai_agent_id: 'agent_name', // Optional: run AI review after creation
});
// Returns: { success: boolean, task_id?: string, error?: string }

// Poll audit status
var status = await actions.getAuditStatus(taskId);
// Returns: { status, message?, current_stage?, total_stages?, stage_name?,
//            processed?, total?, result?, error? }

// Stream audit progress via SSE (real-time updates)
var cleanup = actions.streamAuditProgress(
  taskId,
  function onProgress(data) {
    // data: { status, message?, current_stage?, total_stages?, stage_name?, processed?, total? }
  },
  function onComplete(result) {
    // result: { success?, template_id?, sessions?, total_visits?, total_images?, error? }
  },
  function onError(error) {
    // error: string
  },
);
// Returns: cleanup function. Call cleanup() to close the SSE connection.

// Cancel a running audit
var result = await actions.cancelAudit(taskId);
// Returns: { success: boolean, error?: string }
```

### Job Management

Jobs are long-running backend computations (e.g., MBW monitoring analysis).

```javascript
// Start a job
var result = await actions.startJob(instance.id, {
  job_type: 'mbw_monitoring',
  params: {
    /* job-specific parameters */
  },
  records: [
    /* optional data records */
  ],
});
// Returns: { success: boolean, task_id?: string, error?: string }

// Stream job progress via SSE
var cleanup = actions.streamJobProgress(
  taskId,
  function onProgress(data) {
    // data: { status, current_stage?, total_stages?, stage_name?, processed?, total?, message? }
  },
  function onItemResult(item) {
    // item: individual result row for real-time updates
  },
  function onComplete(results) {
    // results: full computation results
  },
  function onError(error) {
    // error: string
  },
  function onCancelled() {
    // Job was cancelled
  },
);
// Returns: cleanup function

// Cancel a running job
var result = await actions.cancelJob(taskId, instance.id);
// Returns: { success: boolean, error?: string }

// Delete a workflow run and all its results
var result = await actions.deleteRun(instance.id);
// Returns: { success: boolean, error?: string }
```

### OCS (Open Chat Studio) Integration

```javascript
// Check if OCS is connected
var status = await actions.checkOCSStatus();
// Returns: { connected: boolean, login_url?: string, error?: string }

// List available OCS bots
var bots = await actions.listOCSBots();
// Returns: { success: boolean, bots?: [{ id, name, version? }], needs_oauth?: boolean, error?: string }

// Create a task and initiate OCS session in one call
var result = await actions.createTaskWithOCS({
  username: 'worker123',
  title: 'AI Outreach',
  ocs: {
    experiment: 'bot_experiment_id',
    prompt_text: 'Hello, this is a follow-up...',
  },
});
// Returns: { success, task_id?, error?, ocs?: { success, message?, error? } }

// Initiate OCS session on existing task
var result = await actions.initiateOCSSession(taskId, {
  identifier: 'worker123',
  experiment: 'bot_experiment_id',
  prompt_text: '...',
  platform: 'commcare_connect', // Optional, default
  start_new_session: true, // Optional, default true
});
// Returns: { success: boolean, message?: string, error?: string }
```

### MBW-Specific Actions

```javascript
// Save a worker assessment result
var result = await actions.saveWorkerResult(instance.id, {
  username: 'worker123',
  result: 'eligible_for_renewal', // or "probation" | "suspended" | null
  notes: 'Good performance',
});
// Returns: { success, worker_results?, progress?: { percentage, assessed, total }, error? }

// Complete the workflow run
var result = await actions.completeRun(instance.id, {
  overall_result: 'completed',
  notes: 'All workers reviewed',
});
// Returns: { success, status?, overall_result?, error? }
```

### AI Transcript Actions

```javascript
// Get AI conversation transcript for a task
var transcript = await actions.getAITranscript(taskId, sessionId, refresh);
// Returns: transcript object

// List AI sessions for a task
var sessions = await actions.getAISessions(taskId);
// Returns: sessions list

// Save AI transcript data
var result = await actions.saveAITranscript(taskId, data);
// Returns: result object
```

---

## 6. Common UI Patterns

Reusable code snippets for common workflow UI elements. All examples use `var` declarations and access React via the global.

### KPI Summary Cards

```javascript
var stats = React.useMemo(
  function () {
    var total = workers.length;
    var reviewed = workers.filter(function (w) {
      return workerStates[w.username]?.status !== 'pending';
    }).length;
    return { total: total, reviewed: reviewed };
  },
  [workers, workerStates],
);

return (
  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
    <div className="bg-white p-4 rounded-lg shadow-sm">
      <div className="text-3xl font-bold text-gray-900">{stats.total}</div>
      <div className="text-gray-600">Total Workers</div>
    </div>
    <div className="bg-green-50 p-4 rounded-lg shadow-sm border border-green-200">
      <div className="text-3xl font-bold text-green-700">{stats.reviewed}</div>
      <div className="text-gray-600">Reviewed</div>
    </div>
  </div>
);
```

### Status Badge Color Map

```javascript
var colorMap = {
  gray: 'bg-gray-100 text-gray-800',
  green: 'bg-green-100 text-green-800',
  yellow: 'bg-yellow-100 text-yellow-800',
  blue: 'bg-blue-100 text-blue-800',
  red: 'bg-red-100 text-red-800',
  purple: 'bg-purple-100 text-purple-800',
  orange: 'bg-orange-100 text-orange-800',
  pink: 'bg-pink-100 text-pink-800',
};

var getStatusColor = function (statusId) {
  var status = definition.statuses.find(function (s) {
    return s.id === statusId;
  });
  return colorMap[status?.color] || colorMap.gray;
};
```

### SSE Pipeline Data Loading

Pipeline data is typically loaded automatically by the workflow runner and passed via the `pipelines` prop. However, for streaming large datasets or custom loading, use `window.WORKFLOW_API_ENDPOINTS`:

```javascript
var _loading = React.useState(true);
var loading = _loading[0];
var setLoading = _loading[1];
var _data = React.useState([]);
var data = _data[0];
var setData = _data[1];

React.useEffect(function () {
  var url = window.WORKFLOW_API_ENDPOINTS?.streamPipelineData;
  if (!url) {
    setLoading(false);
    return;
  }
  var es = new EventSource(url);
  es.onmessage = function (e) {
    var msg = JSON.parse(e.data);
    if (msg.complete) {
      setData(msg.data.pipelines?.visits?.rows || []);
      setLoading(false);
      es.close();
    }
  };
  return function () {
    es.close();
  };
}, []);
```

### Chart.js (window.Chart)

```javascript
var chartRef = React.useRef(null);
var chartInstance = React.useRef(null);

React.useEffect(
  function () {
    if (!chartRef.current || !window.Chart) return;
    if (chartInstance.current) chartInstance.current.destroy();

    chartInstance.current = new window.Chart(chartRef.current, {
      type: 'line',
      data: {
        labels: dates,
        datasets: [
          {
            data: values,
            label: 'Weight (g)',
            borderColor: '#3b82f6',
            tension: 0.1,
          },
        ],
      },
      options: { responsive: true, maintainAspectRatio: false },
    });
    return function () {
      if (chartInstance.current) chartInstance.current.destroy();
    };
  },
  [dates, values],
);

// In JSX:
// <div style={{height: '300px'}}><canvas ref={chartRef}></canvas></div>
```

### Leaflet Map (window.L)

```javascript
var mapRef = React.useRef(null);
var mapInstance = React.useRef(null);

React.useEffect(
  function () {
    if (!mapRef.current || !window.L || mapInstance.current) return;
    mapInstance.current = window.L.map(mapRef.current).setView([lat, lng], 13);
    window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap',
    }).addTo(mapInstance.current);
    window.L.marker([lat, lng]).addTo(mapInstance.current);
    return function () {
      if (mapInstance.current) {
        mapInstance.current.remove();
        mapInstance.current = null;
      }
    };
  },
  [lat, lng],
);

// In JSX:
// <div ref={mapRef} style={{height: '300px', width: '100%'}}></div>
```

### Sortable Table with Filters

```javascript
var _sortBy = React.useState('name');
var sortBy = _sortBy[0];
var setSortBy = _sortBy[1];
var _filterStatus = React.useState('all');
var filterStatus = _filterStatus[0];
var setFilterStatus = _filterStatus[1];

var displayWorkers = React.useMemo(
  function () {
    var filtered = workers;
    if (filterStatus !== 'all') {
      filtered = workers.filter(function (w) {
        return (workerStates[w.username]?.status || 'pending') === filterStatus;
      });
    }
    return filtered.slice().sort(function (a, b) {
      if (sortBy === 'name')
        return (a.name || a.username).localeCompare(b.name || b.username);
      if (sortBy === 'visits') return b.visit_count - a.visit_count;
      return 0;
    });
  },
  [workers, workerStates, filterStatus, sortBy],
);
```

### Destructuring State Hook (var-compatible)

Since `const [x, setX] = React.useState(...)` requires `const`, use this pattern:

```javascript
var _state = React.useState(initialValue);
var myValue = _state[0];
var setMyValue = _state[1];
```

---

## 7. Building from External Specs

Process for turning an indicator document or monitoring framework into a workflow template.

### Step 1: Analyze the Source Document

Identify:

- **Indicators** -- what data points are tracked (counts, rates, averages)
- **Grouping** -- per-worker, per-beneficiary, per-facility
- **Time dimension** -- single snapshot vs. longitudinal tracking
- **Visualization needs** -- tables, charts, maps, KPI cards

### Step 2: Map Indicators to CommCare Form Fields

- Use MCP `get_form_json_paths` (Claude Code) or manually inspect CommCare HQ
- Each indicator typically maps to one pipeline field with an aggregation
- Example: "% of visits with danger signs" requires a `count` field with `filter_path`/`filter_value` and a total `count` field

### Step 3: Choose terminal_stage

| Need                                             | terminal_stage                          | Example                                                        |
| ------------------------------------------------ | --------------------------------------- | -------------------------------------------------------------- |
| Per-visit detail (timelines, individual records) | `visit_level`                           | KMC child timeline                                             |
| Per-worker summaries (scorecards, rankings)      | `aggregated`                            | Performance review                                             |
| Both                                             | Use two pipelines with different stages | KMC FLW flags (aggregated metrics + visit-level weight series) |

### Step 4: Write PIPELINE_SCHEMAS

Map each indicator to a field with the correct path, aggregation, and transform. Use `linking_field` when grouping visits by beneficiary/case.

### Step 5: Design RENDER_CODE

Match visualization to indicator type:

- Counts/rates -> KPI cards
- Per-worker metrics -> sortable tables
- Time series -> Chart.js line/bar charts
- Geographic data -> Leaflet maps
- Per-entity longitudinal data -> drill-down views (list -> detail)

### Step 6: Validate

- Test with `?edit=true` URL parameter to see the pipeline editor
- Check browser console for Babel transpilation errors
- Verify pipeline data rows are non-empty

### Common Indicator-to-Field Mappings

| Indicator Type             | Pipeline Field Pattern                                                                                                                            |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Count of visits            | `{ "name": "visit_count", "path": "form.meta.instanceID", "aggregation": "count" }`                                                               |
| Last visit date            | `{ "name": "last_visit", "path": "form.meta.timeEnd", "aggregation": "last" }`                                                                    |
| Average numeric            | `{ "name": "avg_weight", "path": "form.weight", "aggregation": "avg", "transform": "float" }`                                                     |
| Yes/No rate numerator      | `{ "name": "yes_count", "path": "form.field", "aggregation": "count", "filter_path": "form.field", "filter_value": "yes" }`                       |
| Unique entities            | `{ "name": "unique_cases", "path": "form.case.@case_id", "aggregation": "count_unique" }`                                                         |
| Weight in grams (from kg)  | `{ "name": "weight_g", "path": "form.weight_kg", "aggregation": "last", "transform": "kg_to_g" }`                                                 |
| GPS location               | `{ "name": "gps", "path": "form.meta.location.#text", "aggregation": "first" }`                                                                   |
| Distinct cases with filter | `{ "name": "deaths", "paths": ["form.case.@case_id"], "aggregation": "count_distinct", "filter_path": "form.child_alive", "filter_value": "no" }` |

---

## Validation Checklist

Before deploying a new template:

- [ ] Template `key` is unique (check existing templates in `__init__.py`)
- [ ] All field `path`/`paths` values verified via MCP or manual CommCare inspection
- [ ] `terminal_stage` matches your data access pattern (visit_level for per-visit, aggregated for per-group)
- [ ] `linking_field` set if doing visit_level with entity grouping by a computed field
- [ ] `data_source.type` is correct: `connect_csv` for Connect data, `cchq_forms` for HQ forms
- [ ] `RENDER_CODE` uses `var` declarations (not `const`/`let`) for maximum compatibility
- [ ] `RENDER_CODE` function is named `WorkflowUI` (not a variable assignment)
- [ ] `RENDER_CODE` accesses custom fields at row top-level (e.g., `row.weight`, not `row.computed.weight`)
- [ ] React hooks accessed via `React.useState`, `React.useEffect`, etc. (no imports)
- [ ] `TEMPLATE` dict has all required keys: `key`, `name`, `description`, `icon`, `color`, `definition`, `render_code`
- [ ] Pipeline schema linked via `pipeline_schema` (single) or `pipeline_schemas` (plural, with aliases)
- [ ] Test with `?edit=true` -- pipeline data is non-empty
- [ ] Check browser console for Babel transpilation errors

---

## 8. Multi-opportunity workflows

By default, every workflow run is scoped to a single opportunity. Templates can opt in to **multi-opportunity** execution, where one workflow merges data from several opportunities and presents opportunity-tagged rows to its render code.

### When to use

Use multi-opp for workflows that need a cross-opportunity view — e.g. a program-wide performance review, a shared worker roster, a network-manager dashboard across 2-5 opps. Single-opp remains the default for anything where the render code assumes data comes from one opportunity.

### Enabling a template

Set `multi_opp: True` on the `TEMPLATE` dict:

```python
TEMPLATE = {
    "key": "my_template",
    "name": "My Template",
    "multi_opp": True,        # <-- opt in
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
}
```

The registry surfaces this flag via `list_templates()`; the flag is also persisted into `definition.data.config.multi_opp` when a workflow is created, so the runtime can gate UI and API behaviour on it.

### What changes at create time

For a `multi_opp` template, the workflow list page shows a **Multi-opp** badge on the template card, and clicking it opens an opportunity multi-select. The submitted `opportunity_ids` are validated server-side against the user's `user_opportunities` and stored on the definition as `data.opportunity_ids`. The workflow's "primary opp" (the opp whose context was active when the user hit Create) remains the record owner for permission/scoping purposes — it is independent of `opportunity_ids` and may or may not be a member.

### What changes at runtime

Both the non-SSE path (`WorkflowDataAccess.get_pipeline_data`) and the SSE streamer (`PipelineDataStreamView`) iterate `definition.opportunity_ids or [primary_opp_id]`. For each pipeline source, they execute the pipeline once per opp, tag each returned row with `opportunity_id`, and concatenate. `get_workers` is called per-opp and the returned dicts are likewise tagged. Per-opp failures are isolated and recorded in metadata rather than aborting the whole stream.

### Render-code contract

Render code for a multi-opp template receives the same props as a single-opp template, with these additions:

- `instance.opportunity_ids: number[]` — full opp set for this run.
- `instance.opportunity_id: number` — primary opp (unchanged semantics).
- `workers[i].opportunity_id: number` — each worker is tagged with its source opp.
- `pipelines[alias].rows[i].opportunity_id: number` — each row is tagged.
- `pipelines[alias].metadata.opportunity_ids: number[]` — list of opps that contributed.
- `pipelines[alias].metadata.per_opp: { [opp_id_as_string: string]: { row_count, from_cache, error? } }` — per-opp metadata. **Keys are strings.** Python's `json.dumps` coerces integer dict keys to strings, so the shape the browser sees uses strings. Access via `metadata.per_opp[String(oppId)]`.

The engine does not deduplicate rows across opps. Single-opp templates receive `opportunity_ids = [primary]` and every row tagged with the same opp, so no code changes are required — legacy behaviour is preserved.

### Editing the opp set

Multi-opp workflow run pages show an "Opportunities: N selected [Edit]" control. Editing posts to `POST /labs/workflow/api/<definition_id>/opportunity-ids/` (view: `UpdateOpportunityIdsView`). The endpoint validates each submitted ID against `user_opportunities`, rejects empty lists, rejects updates against non-multi-opp workflows, and updates `definition.data.opportunity_ids`. The page reloads after save so pipeline data re-streams against the new opp set. The primary opp cannot be changed through this endpoint.

### Reference implementation

`commcare_connect/workflow/templates/performance_review.py` is the canonical multi-opp template. Its table includes an **Opp** column rendering `worker.opportunity_id`. See also `docs/superpowers/specs/2026-04-17-multi-opp-workflows-design.md` for the design notes that led to the current contract.

## 9. Saved-runs templates

Some templates produce a periodic review whose value depends on what was true _at the moment the user finished it_ — a weekly performance review, a cohort QA pass, an audit batch. Reopening the run later should show the same workers and decisions even if the live data has shifted. The saved-runs framework provides this with two states (`in_progress | completed`), a `view` helper that abstracts snapshot-vs-live reads, and a single completion verb.

Other templates are action-shaped — their value lives in the artifacts they produce (audit sessions, tasks, OCS conversations), each persisted in its own model. They opt out of saved runs and never "complete" at the run level.

### Lifecycle

```
   Start Run                     view.complete()
       │                                │
       ▼                                ▼
  ┌───────────┐                  ┌───────────┐
  │in_progress│ ───────────────▶ │ completed │
  └───────────┘                  └───────────┘
       │                                │
       │ delete                         │ Re-run = new in_progress run
       ▼                                ▼
     (gone)                       (completed run preserved as history)
```

- **`in_progress`** — mutable. State writes go to `run.data.state`. No snapshot exists.
- **`completed`** — immutable. The completion call builds the snapshot, persists it, flips status, and stamps `completed_at` in a single LabsRecord write. The render reads from `instance.snapshot` via `view.X`; live pipelines/workers are not consulted.

There is no `failed` state. If snapshot assembly raises, the run stays `in_progress` and the user can retry. There is no `abandoned` state either — abandoned runs are indistinguishable from in-progress, so the only terminal transition is the user explicitly marking complete.

### Opting in

Run-shaped templates declare:

```python
TEMPLATE = {
    "key": "performance_review",
    "supports_saved_runs": True,           # opts in to the lifecycle
    "snapshot_inputs": {                   # optional, see below
        "pipelines": ["visits"],
        "workers": True,
        "state_keys": ["worker_states", "notes"],
    },
    "snapshot_schema": SNAPSHOT_SCHEMA,    # optional, documents the shape
    ...
}
```

Action-shaped templates omit `supports_saved_runs` (or set it `False`). They never get the completion endpoint wired up, never appear with a "Mark Run Complete" button, and the run-picker shows them differently.

### Declaring the snapshot

**Default path: `snapshot_inputs` (the manifest).** Declares what the framework's default hook captures — listed pipelines, the worker list, listed state keys. Anything not listed is not captured. Render code recomputes derived values (summary cards, sorts, filters) at render time from this captured data.

```python
"snapshot_inputs": {
    "pipelines":  ["visits", "registrations"],   # alias allow-list (None = all, [] = none)
    "workers":    True,                          # default True
    "state_keys": ["worker_states", "notes"],    # state allow-list (None = all of state)
}
```

If a declared pipeline alias isn't present at completion (because the workflow definition's pipeline_sources changed), the framework logs a warning and skips it. Almost every saved-runs template should land here — it requires no Python and produces a snapshot whose shape mirrors what `view.X` exposes while in_progress, so render code is identical in both modes.

**Render contract: `snapshot_schema` (recommended companion).** Documents the keys render code expects to read off `instance.snapshot`. The framework can use this to drive completion-confirm copy ("save 12 workers, 8 review decisions"), and bumping `version` is how a template evolves its captured shape.

```python
SNAPSHOT_SCHEMA = {
    "version": 1,
    "keys": {
        "workers":             "FLW list at completion",
        "state.worker_states": "Per-FLW review decisions",
        "opportunity_ids":     "Opportunities the run covered",
    },
}
```

#### Escape hatch: `build_snapshot` hook

Use only when the snapshot needs a shape the manifest can't produce. Real reasons to reach for it:

1. **Compactness.** Raw pipelines are large and the dashboard only needs aggregates. A hook can roll rows into a compact summary instead of capturing them verbatim.
2. **Server-side context.** Something the hook has access to that the FE doesn't — a database lookup, a server-only timestamp, a roll-up across multiple opportunities.
3. **Shape divergence.** The captured snapshot needs to differ structurally from the inputs (rename keys, reorganize) for reasons other than (1) and (2).

If you don't need any of those, use `snapshot_inputs` instead — it's strictly simpler.

```python
def build_snapshot(*, pipelines, state, opportunity_id, workers, opportunity_ids, **_):
    """Hook signature. Returns whatever shape your render code expects under
    instance.snapshot. The hook owns the entire shape; snapshot_inputs is
    ignored when a hook is present. Must accept **_ for forward compatibility."""
    return {
        "schema_version": 1,
        "workers": workers,
        "state":   {"worker_states": state.get("worker_states", {})},
        # … computed aggregates that don't fit the manifest path …
    }
```

The hook runs server-side at completion, in the same request that flips status — if it raises, the run stays `in_progress` and the user can retry.

### Reading run data: the `view` helper

Render code never reads `instance.snapshot` directly, never branches on `instance.status`, and never reads bare `workers`/`pipelines`/`state` props. It uses `view`:

```jsx
function WorkflowUI({
  definition,
  instance,
  links,
  actions,
  onUpdateState,
  view,
}) {
  const workers = view.workers; // live or snapshot, same shape
  const workerStates = view.state.worker_states ?? {};
  const isCompleted = view.isCompleted; // true when status == 'completed'
  const asOf = view.asOf; // completed_at, or null while in_progress

  // Mutations are no-ops once completed (and the BE rejects with 409 anyway):
  const handleChange = (username, status) => {
    if (isCompleted) return;
    onUpdateState({
      worker_states: { ...workerStates, [username]: { status } },
    });
  };

  // Mark run complete from a button:
  const handleComplete = () =>
    view.complete({
      confirm: 'Mark this run complete? Decisions are read-only after.',
    });
}
```

The contract: `view.workers`, `view.pipelines`, and `view.state` work identically whether the run is in_progress (live data) or completed (snapshot data). The template's `snapshot_inputs` (or hook) is what makes the round-trip safe — the snapshot's shape must match what `view` reads.

### Marking a run completed

`view.complete({ confirm? })`:

1. Optionally shows the `confirm` dialog (skip the call on cancel).
2. POSTs to `apiEndpoints.completeRun`. The endpoint:
   - Refuses with **409** if the run is already completed.
   - Refuses with **400** if the workflow's template doesn't declare `supports_saved_runs`.
   - Calls `build_snapshot_for_template(...)` to produce the snapshot.
   - Atomically flips status to `completed`, stamps `completed_at`, persists the snapshot.
3. On 200 → reloads the page; the runner re-mounts in completed mode and reads from the snapshot.
4. On error → surfaces a `window.alert`; run stays `in_progress`.

Server-side write protection: while a run is `completed`, `update_state_api`, `save_worker_result_api`, and any other mutation endpoint return **409**. State is genuinely immutable, not just defensively read-only on the FE.

### Re-running

There is no "edit a completed run" path. The pattern is **re-run = new in_progress run**. The run picker's `Start Run` button creates a fresh run; the completed one stays in the history list. This matches what users actually want when they say "compare to last week."

### Action-shaped templates (opt-out)

`audit_with_ai_review`, `bulk_image_audit`, `ocs_outreach`, `sam_followup`, and `kmc_*` dashboards are action-shaped — their artifacts persist in their own models (audit sessions, tasks, child records). They don't declare `supports_saved_runs`. The runner doesn't show a complete button; the run picker labels them as working sessions rather than reviews.

### Size budget

Snapshots live inside `LabsRecord.data` JSON. The framework warns at 1 MB per snapshot and logs an error at 5 MB — those are signals the template is capturing too much. Tighten with `snapshot_inputs` or move computed summaries into a `build_snapshot` hook.

### Reference implementation

`commcare_connect/workflow/templates/performance_review.py` is the canonical run-shaped template, and exemplifies the manifest path:

- `supports_saved_runs: True`.
- `snapshot_inputs` declaring `{pipelines: [], workers: True, state_keys: ["worker_states"]}` — captures workers + decisions, no pipelines.
- `SNAPSHOT_SCHEMA` documenting the shape for future readers.
- No `build_snapshot` hook — the render's summary cards (`Total / Reviewed / Pending / Confirmed`) are computed in JSX via `React.useMemo` from `view.workers` + `view.state.worker_states`, so they work identically in_progress and completed.
- Render code reads `view.workers`, `view.state.worker_states`, calls `view.complete(...)` from a "Mark Run Complete" button, and surfaces a completed banner with `view.asOf` when `view.isCompleted`.

## 10. Flags + actions catalog

A workflow template that produces a per-FLW report can declare a static
catalog of **Flags** (findings the report computes from data) and
**Actions** (operations the manager can initiate per row). The catalog
lives on `DEFINITION` so the contract is auditable from outside the
render code; the render code is still in charge of computing flag
presence and wiring up action handlers.

### Flag = finding, not judgment

A `Flag` is a record persisted to the labs `Flag` LabsRecord. It carries:

```
flw_id, workflow_run_id, opportunity_id, flag_key, flag_label,
evidence (dict of metric values), source ('auto' | 'manual'),
flagged_at, flagged_by
```

Multiple flags can exist for the same `(run, flw)` — one record per
`flag_key`. Flags are not "decisions" — they don't approve or reject
anything. They just say "this metric crossed this threshold." The Flag
schema deliberately does **not** carry `audit_session_ids` or
`task_ids`; audits and tasks created in response are queried separately
by their own `workflow_run_id` linkage.

### Declaring the catalog

```python
DEFINITION = {
    # ...
    "flags": [
        {"key": "sam_low",     "label": "SAM rate suspiciously low", "auto": True},
        {"key": "gender_skew", "label": "Gender split outside 40-60%", "auto": True},
    ],
    "actions": [
        {"key": "create_audit", "label": "Create Audit"},
        {"key": "send_task",    "label": "Send Task"},
    ],
}
```

The catalog is documentation; the framework does not enforce the listed
keys. Render code is free to add flag keys via `view.ensureAutoFlags`
that aren't in the catalog (you'll just be auditing on the honor
system).

### Auto-applying flags on mount

Render code computes flag presence per row and calls
`view.ensureAutoFlags(computed)` from a `React.useEffect`. The framework
dedups by `(workflow_run_id, flw_id, flag_key)` — calling it repeatedly
is safe.

```js
React.useEffect(
  function () {
    if (!view.ensureAutoFlags || view.isCompleted || !rows.length) return;
    var computed = [];
    rows.forEach(function (r) {
      if (samLow(r))
        computed.push({
          flw_id: r.username,
          flag_key: 'sam_low',
          flag_label: 'SAM low',
          evidence: { sam_pct: samPct(r) },
        });
      if (genderSkew(r))
        computed.push({
          flw_id: r.username,
          flag_key: 'gender_skew',
          flag_label: 'Gender skew',
          evidence: { female_pct: genderPct(r) },
        });
    });
    if (computed.length) view.ensureAutoFlags(computed);
  },
  [rows.length],
);
```

### Rendering the Flag column

```js
React.createElement(
  'td',
  null,
  (function () {
    var rowFlags = view.flagsFor(r.username); // returns array (possibly empty)
    if (!rowFlags.length) return '—';
    return rowFlags.map(function (f) {
      return pill(f.flag_label, 'amber');
    });
  })(),
);
```

### Per-row Action menus

Actions are **always** available, regardless of flag status. When a row
carries a relevant flag, the action's menu surfaces a flag-context-aware
quick action that pre-fills the audit filter or coaching prompt.

```js
React.createElement(MenuButton, {
  label: 'Create Audit',
  items: [
    {
      label: 'Audit 5 recent visits',
      onClick: function () {
        createAudit(r, { count: 5 });
      },
    },
    hasLowMUACFlag
      ? {
          label: 'Audit low-MUAC visits',
          highlight: true,
          onClick: function () {
            createAudit(r, { filter: 'low_muac' });
          },
        }
      : null,
  ].filter(Boolean),
});
```

### Reference implementation

`commcare_connect/workflow/templates/chc_nutrition_analysis.py` is the
canonical flags+actions template:

- `DEFINITION["flags"]` declares `sam_low`, `mam_low`, `gender_skew`.
- `DEFINITION["actions"]` declares `create_audit`, `send_task`.
- A render-local `FLAG_CATALOG` constant pairs each declared flag key
  with its `predicate(row)` and `evidence(row)` functions.
- The `React.useEffect` on `rows.length` computes flag presence per row
  and POSTs missing ones via `view.ensureAutoFlags`.
- Per-row `MenuButton` widgets render Create Audit / Send Task with
  flag-context-aware quick actions.

`commcare_connect/workflow/templates/program_admin_report.py` is the
cross-opp rollup. Its `build_snapshot` reads flags + audits + tasks
independently per run (via `FlagsDataAccess.get_flags_for_run`,
`AuditDataAccess.get_sessions_by_workflow_run`, and
`TaskDataAccess.get_tasks_for_run`) and groups them into `flw_rows` for
the render layer.
