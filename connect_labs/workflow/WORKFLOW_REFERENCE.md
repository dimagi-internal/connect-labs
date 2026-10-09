# Workflow Engine Reference

This is the single source of truth for building workflow templates in Connect Labs. It is consumed by Claude Code (via the workflow-templates skill), the in-product AI agent (loaded at module init), and developers reading documentation. A workflow template is a self-contained Python file that declares a data pipeline schema, a workflow definition, and a React render function. The pipeline engine extracts, transforms, and aggregates data from CommCare form submissions. The render code receives that data as props and displays it using React with Tailwind CSS.

---

## 1. Template Anatomy

Each template is a single `.py` file in `connect_labs/workflow/templates/`. Files are auto-discovered by `__init__.py` via `pkgutil.iter_modules` -- any module in that directory that exports a `TEMPLATE` dict will be registered. Modules starting with `_` or named `base` are skipped.

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

The `TEMPLATE` dict itself also accepts these optional keys:

| Key          | Type         | Default | Description                                                                                                |
| ------------ | ------------ | ------- | ---------------------------------------------------------------------------------------------------------- |
| `multi_opp`  | `bool`       | `False` | Opt in to multi-opportunity support. See [§8 Multi-opportunity workflows](#8-multi-opportunity-workflows). |
| `companions` | `list[dict]` | `[]`    | Templates created alongside this one and cross-linked by config. See [Companions](#companions) below.      |

### Companions

A feature can be two workflows. The KMC programme report drills programme → LLO →
opportunity → worker, and a worker row opens the **KMC Worker Review** — a second
workflow, linked by configuration: the report's `config.flw_review` names the review
workflow and its long-lived run, the review's `config.source_workflow_id` names the
report. Creating the report from its template used to give you half the feature; the
review, its run and both config patches were a four-call runbook the "Create" button
could not follow, so a hand-created report had worker rows that were not links.

`companions` moves that runbook into the registry. Each entry is created right after
the primary — same ownership scope, same `opportunity_ids` — and the two are
cross-linked before `create_workflow_from_template` returns. The MCP tool and the
web view both go through that function, so one "Create" gives the whole feature.

```python
TEMPLATE = {
    "key": "kmc_programme_metrics",
    ...
    "companions": [
        {
            "template_key": "kmc_flw_review",        # a registered template
            "config_key": "flw_review",              # primary.config[key] = {"workflow_id", "run_id"?}
            "share_pipelines": True,                 # reuse the primary's pipeline records (one cache)
            "mint_run": True,                        # create one long-lived run; its id joins the link
            "back_reference": "source_workflow_id",  # companion.config[key] = the primary's id
        }
    ],
}
```

**Where a template appears in the picker** is not a `TEMPLATE` key. The Create
Workflow modal sections templates by what they produce, from one map,
`TEMPLATE_GROUP_OF` in `workflow/templates/__init__.py` (groups and their order in
`TEMPLATE_GROUPS`; the map's order is the order within a group). Every registered
template must be placed there — a test fails otherwise. A companion keeps its own
row, since it is designed to stand alone as well; its row says which template also
creates it, and the primary's row says what it brings.

Rules:

- **Validated before anything is created.** An unknown or deprecated companion
  template, an unknown key, or a cycle (A → B → A) raises `ValueError` up front, so a
  failure cannot leave a primary with no link and a companion with no owner.
- **`share_pipelines`** hands the companion the primary's `pipeline_sources` verbatim
  and creates none of its own — the companion's `pipeline_schemas` are ignored on
  this path (they still serve a stand-alone create).
- **`mint_run`** creates a run owned by the same opportunity / program as the
  workflows, dated today, and puts its id in the link. Use it for drill views the
  primary opens with `?run_id=`; leave it off for companions that mint their own runs.
- **`list_templates()`** exposes `companions` and `companion_of` (lists of template
  keys) so a creation surface can say "also creates X" on the primary and "with X" on
  the companion; the workflows page does both.
- The MCP `workflow_create_from_template` result carries `companions`, keyed by
  `config_key`, e.g. `{"flw_review": {"workflow_id": 5618, "run_id": 5620}}`.

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
        "type": "connect_csv",            # or "cchq_forms", "ocs_sessions", "connect_export", "cchq_cases", "gdrive"
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
| `type`          | `"gdrive"`      | Read CSV / Google Sheet / JSON files from Google Drive. See [Google Drive sources](#google-drive-sources).                    |

#### Google Drive sources

`"type": "gdrive"` reads tabular files from Drive with the server's service account — the way to
feed a workflow with outputs produced outside labs (an offline analysis pipeline, a tracker sheet).
Each row becomes one visit-shaped row; its cells are under `row.*` and the file it came from under
`file.*` (`file.name`, `file.id`, `file.modified`), so field paths read `"row.quality_score"`.

```python
"data_source": {
    "type": "gdrive",
    "folder_id": "11Xa7HWLWoxHFAhuAfKYNsBObhq1nA7U6",   # OR "file_id": "<one file>"
    "file_pattern": "answers_scored_*.csv",              # folder only: fnmatch over file names
    "username_column": "participant_id",                 # optional: becomes `username` (grouping_key)
    "date_column": "session_date",                       # optional: becomes `visit_date`
    "null_values": ["", "NA"],                           # optional: cells read as null (default [""])
    "columns": ["participant_id", "session_date", "quality_score"],  # optional: keep only these cells
}
```

- **Files:** CSV/TSV, a Google Sheet (first sheet, exported as CSV), or JSON (an array of objects,
  or an object holding one under `rows`/`data`). A folder's matching files are concatenated in name
  order; ≤ 100 files, ≤ 50 MB per file, ≤ 500,000 rows. CSV cells arrive as strings — use a
  `transform` (`int`, `float`) for numbers.
- **Keeping only some columns (`columns`):** a wide export where the pipelines read a handful of
  columns can list them; each row's `row.*` then holds exactly those cells (in that order) and
  every other cell is dropped as the file is read, so the cached copy is a fraction of the size.
  `file.*` is kept regardless. Omit `columns` to keep every column (the default, unchanged).
  Every `row.<col>` path the schema reads — field `path`/`paths`, `filter_path`/`filter_paths`,
  `conditional_paths`, `pre_aggregate_by`, histogram paths, `linking_field`, and path-shaped keys in
  `group_by`/`groupings`/`filters` — plus `username_column` and `date_column` must be listed, or
  the save (and any read) is refused with an error naming each missing column and what reads it,
  e.g. `data_source.columns does not list 1 column(s) the schema reads: 'qid' (read by fields[2].path 'row.qid')`.
  A listed column a file does not have reads null on that file's rows (and is logged), so one
  list can cover files with slightly different headers. `columns` is not part of the
  authorization stamp — it only narrows what is kept from the stamped target — so
  narrowing an authorized source needs no re-stamp.
- **Where the data must live:** only files and folders under a workflow-data root
  (`LABS_WORKFLOW_GDRIVE_ROOT_IDS`) can be read. Copy data a workflow should see into that tree;
  a source pointing anywhere else is refused when it is saved and again on every read (it is
  checked by walking the item's Drive parents, so moving a file out of the tree cuts it off). With
  no root configured, Drive sources are off.
- **Sharing:** the root is shared with the labs service account
  (`connect-labs-sa@connect-labs.iam.gserviceaccount.com`); anything copied under it is readable.
  Drive is read with a read-only token.
- **Authorization:** only Dimagi staff can point a pipeline at Drive. Setting or changing the
  target (`file_id`/`folder_id`/`file_pattern`) through `pipeline_update_schema` or the pipeline
  editor needs staff and stamps `data_source.authorization`, bound to that opportunity, that
  pipeline and that exact target (a stamp copied into another pipeline does not verify).
  `pipeline_create` stamps a new pipeline's source as it creates it. Re-saving an unchanged target
  keeps its stamp, so anyone may edit the fields of an authorized pipeline. A target stored some
  other way (e.g. a template sync) stays unstamped until a staff member passes
  `authorize_drive_source: true` to `pipeline_update_schema`. Never write `authorization` by hand.
- **Who can read it:** every read, fresh or cached, needs a valid stamp for the opportunity AND a
  caller (the Connect token the pipeline runs with) who is a member of that opportunity. Members
  can read every column of every matching file, including files added to the folder later, so
  point the source at a folder that holds only what that opportunity may see.
- **Scope limits:** an opportunity-stamped source is authorized per opportunity, so a multi-opp
  fan-out reads Drive only for the opportunity it was authorized for. For data that covers a whole
  program, stamp the source for the program instead (next section).
- **Freshness:** rows are cached for the pipeline's TTL (1 hour); a forced refresh re-reads Drive.
- **Several pipelines, one read:** pipelines on the same opportunity whose sources agree on
  `file_id`/`folder_id`, `file_pattern`, `null_values`, `username_column`, `date_column` and
  `columns` (as a set: order does not matter; omitted is its own value) share ONE raw copy of the
  rows; pipelines that list different columns each keep their own copy. The first to build reads
  Drive; the others build from that copy without touching Drive. So split a big folder into
  several small summary pipelines (an entity pipeline per question, per state, ...) rather than
  one pipeline that ships every row. Sharing the
  rows does not share the right to read them: each pipeline's own stamp and the caller's
  membership are checked on every read, before the shared copy is touched. A forced refresh on
  any of them re-reads Drive into the shared copy (once per page load, not once per pipeline).

##### Program-scoped Drive sources

For Drive files that cover a whole program — one interview export across every cohort, say — own
the pipeline **in the program** instead of in one of its opportunities. Its source is then stamped
for the program (`data_source.authorization.program_id`, signed; an opportunity stamp can never
pass for a program one or the reverse), and three things change:

- **Who can read it: the program's managing organization, only.** Every read — fresh, cached,
  shared raw copy, on-demand query, snapshot — needs a valid program stamp AND a caller who is a
  member of the organization that OWNS the program. Membership in one of the program's
  opportunities does **not** count: those are partner network organizations, and they must not see
  other cohorts' raw rows. Labs learns the owner from the caller's own Connect org tree
  (`/export/opp_org_program_list/`): its `programs` list holds only programs whose owning
  organization the caller belongs to, each naming that organization's slug, and both are checked.
  When the tree cannot be read the source is not read (fail closed).
- **Read once, not per opportunity.** In a program workflow that spans N opportunities, a program
  Drive pipeline is fetched and aggregated ONCE for the program, not once per opportunity — so an
  entity-stage `GROUP BY linking_field` or a visit-level pipeline is not multiplied N times. Its
  raw rows and computed caches live under the program's own cache key (a negative
  pseudo-opportunity id, `-program_id`), shared by every pipeline on the same Drive target, as
  above. This holds on page load (the stream), in `get_pipeline_data`, in run-completion snapshots
  and in `queryPipelineRows`.
- **Rows carry `opportunity_id: null`.** They belong to the program, not to one of its
  opportunities. `pipelines[alias].metadata` adds `program_id` and `read_once_for_program: true`,
  and `per_opp` has one entry keyed `"program:<id>"`. Split rows by cohort with a column of the
  file itself, not with `opportunity_id`.

How to build one through the labs MCP (Dimagi staff, the target under the workflow-data root):

```text
pipeline_create(program_id=121, name="Interview answers", schema={... "data_source": {"type": "gdrive", ...}})
pipeline_preview(program_id=121, pipeline_id=<id>)                 # read once for the program
pipeline_update_schema(program_id=121, pipeline_id=<id>, schema=..., expected_version=N)
workflow_create(program_id=121, name="Interviews dashboard")       # program-owned; opportunity_ids may stay []
workflow_add_pipeline_source(program_id=121, workflow_id=<wf>, pipeline_id=<id>, alias="answers",
                             load="on_demand")                     # home_scope {program_id: 121} is set for you
```

Render code then reads an on-demand program source with `actions.queryPipelineRows('answers', {...})`
and **no** `opportunity_id` — the page's program scope is enough. A program Drive pipeline may only
JOIN other program-scoped pipelines (their caches share the program's key).

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

#### `group_by` and `groupings` — composite keys and several summaries in one pipeline

`linking_field` groups by ONE value, and a pipeline produces ONE grouping. A summary
dashboard usually needs more: totals by questionnaire, by question, by state, by worker
type, and answer types by question × state. Before these keys existed that took one
pipeline per summary (and one per state, for the per-state breakdown), each a separate
pass over the same rows. Two entity-stage keys replace them:

- **`group_by: [...]`** — a composite key: one row per distinct combination of the listed
  fields. `group_by: ["state"]` is exactly `linking_field: "state"`.
- **`groupings: {name: {...}}`** — several named groupings computed from the same rows in
  ONE request. Each takes:
  - `group_by` (required): its key fields;
  - `filters` (optional): restricts the rows THIS grouping aggregates — same keys as the
    pipeline's entity-stage `filters` (a declared field, or `status` / `flagged` /
    `date_from` / `date_to`), applied on top of the pipeline's own `filters`;
  - `fields` (optional): which of the pipeline's `fields` / `histograms` it computes
    (default: all). The pipeline's `fields` are the shared aggregation vocabulary.

Declare one or the other, not both. `terminal_stage` may be omitted (these keys imply
`"entity"`); any other stage is refused. A key is a declared field (resolved like
`linking_field`, from its `path` / `paths` / `conditional_paths`, before any transform) or a
base column such as `username` or `status`.

```python
"schema": {
    "data_source": {"type": "gdrive", "folder_id": "...", "file_pattern": "typology_classifications_*.csv"},
    "fields": [
        {"name": "topic", "path": "row.topic", "aggregation": "first"},
        {"name": "qid", "path": "row.qid", "aggregation": "first"},
        {"name": "state", "path": "row.state", "aggregation": "first"},
        {"name": "flw_type", "path": "row.flw_type", "aggregation": "first"},
        {"name": "typology_id", "path": "row.typology_id", "aggregation": "first"},
        {"name": "typology_name", "path": "row.typology_name", "aggregation": "first"},
        {"name": "basis", "path": "row.classification_basis", "aggregation": "first"},
        {"name": "n", "path": "row.qid", "aggregation": "count"},
        {"name": "clean", "path": "row.qid", "aggregation": "count",
         "filter_path": "row.classification_basis", "filter_value": "answered_clean"},
        {"name": "interviews", "path": "row.session_id", "aggregation": "count_distinct"},
    ],
    "groupings": {
        "by_topic":    {"group_by": ["topic"]},
        "by_question": {"group_by": ["qid"]},
        "by_state":    {"group_by": ["state"]},
        "by_flw_type": {"group_by": ["flw_type"]},
        "types":       {"group_by": ["qid", "typology_id", "state"],
                        "filters": {"basis": ["answered_clean"]},
                        "fields": ["n", "typology_name"]},
    },
}
```

**Output rows.** Every grouping's rows arrive in ONE list, `props.pipelines[alias].rows`,
in declaration order, each tagged with the grouping it belongs to:

```json
{"grouping": "by_state", "entity_id": "by_state:Kebbi", "state": "Kebbi",
 "total_visits": 51234, "username": "...", "first_visit_date": "...", "last_visit_date": "...",
 "n": 51234, "clean": 40012, "interviews": 812, "topic": "...", "qid": "...", ...}
{"grouping": "types", "entity_id": "types:4.12|T1|Kebbi",
 "qid": "4.12", "typology_id": "T1", "state": "Kebbi", "total_visits": 37, "n": 37, "typology_name": "Workload"}
```

- `grouping` — the grouping's name; split with
  `rows.filter(r => r.grouping === "by_state")`. A field may not be named `grouping`.
- each key field is its own column holding the group's value (not that field's
  aggregate), so `r.state` is the state the row is about. A missing value groups as `null`.
- `entity_id` — a stable row key: `<grouping>:<key values joined by |>` (null reads as
  empty). A top-level composite `group_by` has no `grouping` column and an `entity_id`
  without the prefix (`4.12|T1|Kebbi`); a single key gives exactly the `linking_field` id.
- the standard entity columns (`total_visits`, a representative `username`, `entity_name`,
  `first_visit_date` / `last_visit_date`) as on any entity row, plus the grouping's fields.
  A grouping restricted by `fields` carries only those.

On-demand sources read the same rows with `actions.queryPipelineRows(alias, {filters:
{grouping: "types", state: "Kebbi"}})` — `grouping` and every key are queryable.

**One pass.** The JSON fields every grouping needs are extracted ONCE into a temporary table
(keys, values, each field's filter and each grouping's filter as typed columns); each
grouping is then a plain `GROUP BY` over it. `pipeline_preview` reports `metadata.rows_per_grouping`
(the sample is cut across groupings); `pipeline_sql` returns the whole script as
`sql.grouped_aggregation_sql`. Results are cached like any entity result (Drive caches follow
the files' fingerprints; a program-scoped Drive source is read once for the program).

**Not supported in a grouping:** `mode_share` / `dup_share` (they are per-FLW) and
`pre_aggregate_by` — leave such fields out of the grouping's `fields`. Saving a schema whose
keys, fields or filter keys name nothing declared is refused, as are grouping names that are
not lower case.

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
| `dup_share`      | Share (0..1) of rows whose value repeats   | float         |

> **FLW-grouping only:** `mode_share` and `dup_share` are compiled as correlated
> subqueries scoped to `(opportunity_id, username)`, so they work at FLW
> (`username`) grouping but are rejected at entity stage. Same restriction applies
> to `pre_aggregate_by`.

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

# The first T2 typology name per question (first / last / list honour the filter too)
{"name": "t2_name", "path": "row.typology_name", "aggregation": "first",
 "filter_path": "row.typology_id", "filter_value": "T2"}
```

`filter_path` / `filter_value` restrict ONE field's aggregate. To restrict every row a pipeline
aggregates, use the schema's `filters` (below) — at `terminal_stage: "entity"` they are applied
before the GROUP BY, e.g. `"filters": {"state": ["Kebbi"], "basis": ["answered_clean"]}` where
`state` and `basis` are declared fields.

Field names must be lower case (`[a-z_][a-z0-9_]*`): they become SQL column aliases, and Postgres
folds upper case away, so a field named `T0` used to read back null. Saving one is refused.

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
- CDN libraries available via `window`: Chart.js 4.4.0 (`window.Chart`), chartjs-adapter-date-fns 3.0.0, Leaflet 1.9.4 (`window.L`), Mapbox GL 3.13.0 (`window.mapboxgl`)
- **Shared map components** for drawing CommCare / microplan data on a Mapbox map: `window.ConnectMap` (boundaries, points, survey pins), `window.PlanLayers` (the canonical microplan plan layers — work areas, PSU hulls, sample pins, footprints), and `window.MicroplansMapPanel` (the docked Layers toggle panel). **Use these instead of hand-rolling layer paint or toggles** — they are the same definitions the plan editor uses, so a render draws a plan identically. See [§4a Shared map components](#4a-shared-map-components-connectmap--planlayers).
- **Shared report library — style every report with it.** `window.LabsReport` is the house look of the indicator reports (white `rounded-xl` cards, indigo accents, tabular numbers): page chrome, headline tiles, scorecards, charts, benchmarks, and scenario inputs. **Start a new render from these components, not from hand-written Tailwind** — a report built from them looks like every other report and picks up improvements on deploy. See [§4c Shared report library](#4c-shared-report-library-windowlabsreport).
- **Shared runner UI primitives** for panels reused across templates: `window.LabsAudit` (the per-FLW "Audit results by field worker" breakdown). **Call these instead of re-inlining the markup** — they're static, tested components editable in the repo (`connect_labs/static/js/`), so every consumer stays identical. See [§4b Shared runner UI primitives](#4b-shared-runner-ui-primitives-windowlabsaudit).
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

### 4a. Shared map components (ConnectMap & PlanLayers)

The render runtime loads two shared Mapbox-GL modules on `window`. They exist so a
workflow render draws CommCare / microplan data **the same way the rest of the app
does** — don't re-implement layer paint by hand; call these.

> How to discover what's available: this section is the canonical list. It's served
> verbatim by the `workflow_authoring_guide` MCP tool, so any AI authoring a template
> sees it alongside the rest of the contract. When a new shared component is added to
> the runtime (`templates/workflow/run.html`), it is documented here.

**`window.ConnectMap`** — generic CommCare map helpers (`static/maps/connect_map.js`):

| Method                                        | Purpose                                    |
| --------------------------------------------- | ------------------------------------------ |
| `createMap(el, opts)`                         | init a Mapbox map (`{center,zoom,style}`)  |
| `bounds(geojson)` / `fit(map, geojson, pad)`  | compute / fit viewport                     |
| `boundary(map, id, fc, opts)`                 | admin-boundary fill+line(+label) layers    |
| `points(map, id, fc, opts)`                   | dense point layer (e.g. delivery visits)   |
| `pins(map, id, fc, opts)`                     | survey pins coloured by a boolean property |
| `setSource(map, id, fc)` / `remove(map, ids)` | upsert source / tear down                  |

**`window.PlanLayers`** — the canonical **microplan plan layers** (`static/maps/plan_layers.js`).
These are the exact paint/source definitions the plan editor uses, so a render shows a
plan identically. Each drawer is idempotent (upsert source, add layers if absent) and
returns its layer ids; interactivity is the caller's job.

| Method                  | Draws                                    | Data shape (`opts.data` = FeatureCollection)                   |
| ----------------------- | ---------------------------------------- | -------------------------------------------------------------- |
| `workAreas(map, opts)`  | territory polygons (`wa-fill`/`wa-line`) | Polygon features w/ `{id, status, fill, outline}`              |
| `hulls(map, opts)`      | PSU / cluster hulls, two-arm             | Polygon features w/ `{arm: 'intervention'\|'comparison'}`      |
| `pins(map, opts)`       | sampled household pins                   | Point features w/ `{arm, sample_type: 'primary'\|'alternate'}` |
| `footprints(map, opts)` | building footprints                      | Polygon (fill) or Point (centroid dot) features                |
| `remove(map, ids)`      | tear down                                | —                                                              |

Common `opts`: `data` (FeatureCollection), `src` (source id), and overrides like
`armProp` / `colors` / `typeProp` so the same paint can run over differently-tagged data.
`footprints` additionally takes `armProp` (arm-colour instead of amber) and
`splitByType: true` (encode `typeProp`, default `sample_type`: **primary → solid fill,
alternate → dashed outline** — the polygon analogue of the pins' solid/hollow split),
plus `fillId` / `altLineId` / `dotsId` to namespace its layers.

**`window.MicroplansMapPanel`** — the docked **Layers panel** the plan editor uses
(`static/microplans/map_panel.js`): `create({ map, mount, tabs })` → `registerLayer({ id,
label, color, badge, meta, onToggle })`. Reuse it instead of hand-rolling layer toggles so
the selector chrome matches the plan UI. Pass `tabs: ['layers']` to drop the Inspect tab
when there's nothing to inspect. Each layer's `onToggle(on, handle)` drives your draw;
`handle.setMeta(text)` updates the row (e.g. a lazy-loaded count); `setEnabled(true, false)`
sets a default-on layer without firing the callback.

**Drawing a saved plan** (work areas + arm wards) from its JSON — fetch the plan API
(`/microplans/program/<program_id>/plan/<plan_id>/`, which returns `work_areas`,
`input_areas`, `psu_hulls`, `sampling_stats`) and hand each piece to `PlanLayers`:

```javascript
var map = window.ConnectMap.createMap(el, { center: [lng, lat], zoom: 10 });
fetch('/microplans/program/' + programId + '/plan/' + planId + '/')
  .then(function (r) {
    return r.json();
  })
  .then(function (plan) {
    // arm-coloured study wards from the plan's input_areas
    var wards = {
      type: 'FeatureCollection',
      features: (plan.input_areas || []).map(function (a) {
        return {
          type: 'Feature',
          geometry: a.geometry,
          properties: { arm: a.arm || 'intervention' },
        };
      }),
    };
    window.PlanLayers.hulls(map, { data: wards });
    // the sampled work-area footprints
    var was = {
      type: 'FeatureCollection',
      features: (plan.work_areas || []).map(function (w) {
        return {
          type: 'Feature',
          geometry: w.geometry,
          properties: {
            id: w.id,
            status: 'ACTIVE',
            fill: '#6366f1',
            outline: '#4338ca',
          },
        };
      }),
    };
    window.PlanLayers.workAreas(map, { data: was });
  });
```

### 4b. Shared runner UI primitives (window.LabsAudit)

Some panels are rendered **identically in more than one template** (and sometimes
in the `pages` cards surface too). Those live as **static, versioned,
unit-tested JS primitives** loaded on the runner page, and render code **calls**
them instead of re-inlining the markup. This is the same pattern as the shared
map components above — a small "runner UI kit" on `window.*`.

**These primitives ARE editable — you just edit the component source file in the
repo (in Claude Code, like everything else), and it ships on the next deploy.**
They are static (not live-editable via the MCP `update_render_code` path) on
purpose: that's what keeps every consumer byte-identical. **Do not copy a
primitive's markup back into a template's `render_code`** — extend the primitive
so all surfaces move together. When should something become a primitive? When it
is (a) rendered identically in 2+ places, (b) stable, and (c) worth a unit test.
One-off template layout stays in `render_code`.

**`window.LabsAudit`** — the **per-FLW audit breakdown** ("Audit results by field
worker": each field worker's MUAC + Other audit lines with pass/fail/pending/AI
counts, deep-linking to the bulk review screen). Source:
`connect_labs/static/js/labs_audit_breakdown.js` (tests:
`labs_audit_breakdown.test.js`). Consumed by `weekly_dual_track_audit` (the
opp-level run), `program_audit_creator` (each per-opp row expands to it), and the
`flw_audit_breakdown` pages card.

```javascript
// Render the breakdown (returns a React element; it self-manages collapse):
window.LabsAudit.renderFlwBreakdown(React, {
  sessions,            // array from the /audit/api/workflow/<run_id>/sessions/ endpoint
  oppNames,            // optional { "<opp_id>": "Display name" }
  workflowRunId,       // for the bulk-review deep links
  loading,             // optional: show a spinner instead of "no sessions"
  title,               // optional: null hides the "Audit results by field worker" header
});

// Lazy-fetch a run's sessions across one or more opps (merged, de-duped):
window.LabsAudit.fetchSessions(workflowRunId, [oppId1, oppId2]).then(function (sessions) { ... });
```

### 4c. Shared report library (window.LabsReport)

The component library behind the KMC and generic indicator reports, published on
`window.LabsReport` by the runner (source: `components/workflow/report/`, contract
tests: `report.test.js`). Any render — template-following, forked, or written live
through MCP — can call it. Same rules as §4b: components take plain data and
callbacks and never fetch; a shipped prop is never renamed (a breaking change is a
new name); `VERSION` rises with every addition, so a render can check
`R.VERSION >= n` before using something newer than the deployed bundle.

**If your render needs a look the library lacks, add it to the library** (with a
test) rather than styling it inline — that is how the styles stay reusable.

```javascript
var R = window.LabsReport;
```

| Need                                                                | Use                                                                                                   |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Page top: title, "as of", badges, actions                           | `R.ReportHeader`, `R.Pill`, `R.Button`                                                                |
| Sections                                                            | `R.Card` (`padded={false}` for tables), `R.SectionTitle`, `R.Tabs`                                    |
| Status / empty / error / loading                                    | `R.Notice` (`tone`: info, warn, error, muted; `onRetry`), `R.Loading`                                 |
| Headline indicator tiles (banded cells)                             | `R.HeadlineTiles`                                                                                     |
| Headline tiles for formatted or modelled figures (ranges, currency) | `R.StatTiles` (VERSION 4)                                                                             |
| Banded table cells, legend, sorting                                 | `R.ScoreCell`, `R.ScorecardLegend`, `R.useTableSort`                                                  |
| Trends and activity                                                 | `R.TrendCard`, `R.WeeklyActivityCard`                                                                 |
| Peer / organisation comparison                                      | `R.PeerCard`, `R.RankedBars`, `R.MiniRankBars`                                                        |
| Formatting and bands                                                | `R.fmtValue`, `R.nCount`, `R.dateLbl`, `R.BAND_CLS`, `R.BAND_TEXT`, `R.bandColour`                    |
| Assumption inputs the reader changes                                | `R.Field` (label + control + source hint), `R.NumberField`, `R.RangeField`, `R.Segmented` (VERSION 4) |
| A modelled low–high range against threshold lines                   | `R.RangeStrip`, `R.stripPos`, `R.rangeText` (VERSION 4)                                               |

`NumberField` / `RangeField` hand back the raw string typed, so a half-typed value
survives a render; parse it in the page. Pass `edited` to mark a value that differs
from its sourced default. Worked example of the scenario pieces: the KMC
cost-effectiveness explorer (workflow 6627 on the synthetic KMC set).

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

### On-demand pipeline sources and `actions.queryPipelineRows`

By default every pipeline source is streamed to the page on load: all its rows, for every
opportunity, into `pipelines[alias].rows`. For a pipeline too big for that — workflow 23765's
answers pipeline was 173k rows of free text, a 27 s load and ~435 MB of browser heap, to read one
question's answers at a time — mark the source **on demand**:

```text
workflow_add_pipeline_source(workflow_id=..., opportunity_id=..., pipeline_id=..., alias="answers",
                             load="on_demand")          # load="eager" switches it back
```

The source entry then carries `"load": "on_demand"` (re-pointing an alias without `load` keeps
it; `workflow_get` reports each source's `load`). The page no longer runs or ships it:
`pipelines.answers` is `{rows: [], metadata: {on_demand: true, ...}}`. (If an eager pipeline JOINs
it, it still runs to fill that join's cache, but its rows are still withheld.)

Render code asks for the rows it needs, filtered and paged **in SQL** on the server:

```javascript
var res = await actions.queryPipelineRows('answers', {
  filters: { qid: '4.12', state: ['Kebbi', 'Kano'] }, // value or any-of list; null = missing
  search: { text: 'drugs', fields: ['answer'] }, // case-insensitive substring; a bare
  // string searches every declared field
  order_by: ['-visit_date', 'state'], // '-' = descending
  limit: 50, // 1-500, default 100
  offset: 0,
  opportunity_id: 1251, // which opportunity, for a multi-opp workflow (else optional)
  onStatus: function (msg) {
    setStatus(msg);
  }, // progress while a cold cache warms
});
// res = { rows: [...same row shape as pipelines[alias].rows...], total: 133, limit: 50, offset: 0 }
```

- **Fields:** filters, search and order_by may name the pipeline's declared fields and the stage's
  base columns (visit: `id`, `username`, `visit_date`, `status`, `flagged`, `entity_id`,
  `entity_name`, ...; entity: `entity_id`, `username`, `total_visits`, `first_visit_date`, ...;
  aggregated: `username`, `total_visits`, `approved_visits`, ...). Anything else is refused with a
  400 naming the valid fields. JSON-field values compare as text (`3` matches a stored `3`,
  `true` a stored boolean); ordering a JSON field orders by its JSON value (numbers numerically).
- **Works on any terminal stage** (visit, entity, aggregated) — it reads that stage's cache.
- **Access:** the same gates as the page stream: signed in, the workflow readable in your scope and
  spanning the opportunity, the alias one of its sources, and for a Drive pipeline its own stamp
  plus your membership (for a program-scoped Drive pipeline: membership of the program's managing
  organization, and no `opportunity_id` is needed — see
  [Program-scoped Drive sources](#program-scoped-drive-sources)).
- **A cold cache** is filled the normal way (the pipeline runs as it would on page load), but in
  a background task so it cannot hit the 60 s request timeout. The endpoint answers 202
  `warming`, and the action polls until the rows are ready (up to `timeoutMs`, default 10 min),
  then resolves. It rejects with an `Error` on any failure (bad field, no access, the build
  failed).
- **Endpoint:** `POST /labs/workflow/api/<workflow_id>/pipeline-query/?opportunity_id=<scope>`
  with body `{alias, filters?, search?, order_by?, limit?, offset?, opportunity_id?}` →
  `{status: "ready", rows, total, limit, offset}` | 202 `{status: "warming"}` |
  `{status: "error", error}`.

Typical shape: a few small entity-stage summary pipelines (streamed — the overview) plus the big
visit-level pipeline on demand, read a question at a time when the user opens one.

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

// Stream audit progress (poll-first — see §11). Holds no connection by default;
// the SSE stream is the opt-in low-latency transport.
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
// Returns: cleanup function. Call cleanup() to stop polling / close the stream.

// Cancel a running audit
var result = await actions.cancelAudit(taskId);
// Returns: { success: boolean, error?: string }
```

### Job Management

Jobs are long-running backend computations (e.g., MBW monitoring analysis).
**Before wiring live progress, read [§11 Long-running job progress](#11-long-running-job-progress).**
`streamJobProgress` is now poll-first (holds no connection); the SSE stream is opt-in.

```javascript
// Start a job
var result = await actions.startJob(instance.id, {
  job_type: 'mbw_monitoring',
  params: {/* job-specific parameters */},
  records: [/* optional data records */],
});
// Returns: { success: boolean, task_id?: string, error?: string }

// Stream job progress (poll-first — see §11). The SSE stream is opt-in.
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
  platform: 'connect_labs', // Optional, default
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
- [ ] `data_source.type` is correct: `connect_csv` for Connect data, `cchq_forms` for HQ forms, `gdrive` for Drive files (saved by Dimagi staff)
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

`connect_labs/workflow/templates/performance_review.py` is the canonical multi-opp template. Its table includes an **Opp** column rendering `worker.opportunity_id`. See also `docs/superpowers/specs/2026-04-17-multi-opp-workflows-design.md` for the design notes that led to the current contract.

### Troubleshooting: "the workflow shows 0 rows and no error anywhere"

This exact symptom — every pipeline reports `row_count: 0`, `metadata.error` is empty, and the page just shows an empty-state message instead of an error banner — has three distinct root causes discovered while building a program-owned report (Ward Progress Tracker, 2026-07). Check them in this order; each produces an _identical_ symptom, so don't stop at the first plausible-sounding one without confirming.

1. **A reused pipeline is owned by an opportunity other than the one the runtime picked as its scope fallback.** Pipeline records are individually opportunity-owned regardless of which opportunity/program owns the _workflow_ referencing them (see [§2 `data_source`](#schema-fields-reference) — nothing about pipeline ownership is per-program). A program-owned workflow's live-render code path scopes its shared `PipelineDataAccess` to a single opportunity (`definition.opportunity_ids[0]`), then reuses it to look up every pipeline source's _definition_. If the pipeline you reused (the normal, recommended way to build a program-owned report — don't duplicate pipelines per opp) happens to be owned by a _different_ spanned opportunity, that lookup 404s — uniformly, for every opp, even the one that legitimately owns the pipeline. Fixed generally via `_resolve_pipeline_definition` (`connect_labs/workflow/views.py`), which retries the lookup across every opp in `opportunity_ids` before giving up. If you see "Pipeline `<id>` not found" in the SSE stream for every opportunity at once (not per-opp variation), this is almost certainly it — check that the fix is deployed before looking further.
2. **CommCare HQ OAuth expired or was denied, and the fetcher swallowed the failure instead of raising.** `CommCareDataAccess.fetch_cases`/`fetch_forms` normally retry once after a 401/403 (token refresh) and raise `CCHQAuthError` if that still fails — but any fetcher that _doesn't_ follow this pattern, or any non-auth HTTP/network error mid-pagination, can silently return a partial or empty list that looks like a legitimate result. If you're adding a new `cchq_*` fetcher, mirror `fetch_forms`'s retry-then-raise pattern exactly, and consider whether callers need `raise_on_http_error`-style strictness (default should tolerate partial results _only_ if there's a caller that already handles that gracefully — most report pipelines should NOT tolerate it, since a silent partial result is indistinguishable from "this domain genuinely has zero records").
3. **The SSE mixin silently drops error events — this is the one most likely to make (1) and (2) invisible even after they're fixed.** `AnalysisPipelineSSEMixin.stream_pipeline_events` (`connect_labs/labs/analysis/sse_streaming.py`) only recognizes `EVENT_STATUS`/`EVENT_DOWNLOAD`/`EVENT_RESULT`. When `AnalysisPipeline.stream_analysis`'s own top-level `except Exception` catches something and yields `(EVENT_ERROR, {...})`, that event matches none of the mixin's branches and is silently discarded — `self._pipeline_result` stays `None` (reported by every caller as `row_count: 0`), and `self._pipeline_from_cache` can even end up `True` as a pure side effect of an earlier, unrelated status message containing the word "cache". **If you've confirmed a real exception is being raised inside `stream_analysis` (e.g. by temporarily adding a `print`/log statement right where the exception is caught) but it never reaches the browser, this is why.** The fix is an opt-in `raise_on_error=True` flag on `stream_pipeline_events` (default `False` — five other Labs features share this mixin and have never handled a raised exception from it, so changing the default would be an unreviewed behavior change for all of them). `workflow/views.py`'s multi-opp per-opp execution loop passes `raise_on_error=True`; if you're calling this mixin from a new call site and want real errors instead of silent empty results, do the same — but verify your own call site already wraps the `yield from mixin.stream_pipeline_events(...)` call in a `try/except` first, since raising will now propagate there.

**Fastest way to tell these apart without deploying anything:** open `/labs/workflow/api/<definition_id>/pipeline-data/stream/?<opportunity_id=X|program_id=X>&refresh=1` directly in a browser tab (bypasses the frontend UI — the `refresh=1` query param forces a live fetch, skipping the pipeline cache, which is otherwise a 1-hour-TTL `PIPELINE_CACHE_TTL_HOURS` cache that would keep serving a stale prior result and make it look like nothing changed after a fix deploys). Read the raw SSE text directly — every `"message"` line is a real progress/error string from the pipeline, and the final `"complete": true` payload's `metadata.per_opp[oppId]` is where **multi-opp errors actually live** (not `metadata.error`/`metadata.auth_error` directly — those are the single-opp/whole-stream-level shape). A render-code error banner that only checks `waMeta.error`/`waMeta.auth_error` will miss a real per-opp error entirely for a multi-opp workflow — check `metadata.per_opp[oppId].error` too (see the `per_opp` shape documented in [§8 Render-code contract](#render-code-contract) above).

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

> **The workflow instance owns its manifest.** The template's `snapshot_inputs` is only the _starting value_: `create_workflow_from_template` stamps it onto the new definition (`data["snapshot_inputs"]`), and completion resolves the contract from the definition first (`resolve_snapshot_contract`), falling back to the template registry only for legacy instances and `build_snapshot`-hook templates. That means the snapshot captures what the workflow _is doing now_ — edit the instance manifest (via `workflow_update_definition`'s `snapshot_inputs` key) when a workflow grows new pipelines or state keys, instead of editing the repo template. Editing the repo template's manifest does **not** retroactively change already-stamped instances. A bespoke (non-template) workflow can opt into the whole saved-runs lifecycle just by setting `snapshot_inputs` on its definition. Pass `snapshot_inputs: null` to a `workflow_update_definition` patch to revert an instance to template-registry resolution.

> **Alias must match the created source.** The aliases you list in `snapshot_inputs.pipelines` (and read as `view.pipelines.<alias>` in render code) must equal the alias of the pipeline source `create_workflow_from_template` actually creates. For a single-pipeline template that source alias defaults to `"data"` — declare `"pipeline_alias": "<alias>"` on the `TEMPLATE` dict to override it. A mismatch is silent: live KPI cells render as dashes AND the completion snapshot filters down to an empty pipelines dict (see #464).

> **Period-scoped pipelines (`period_scoped: true`).** A saved run carries a `period_start`/`period_end`. By default an aggregated pipeline ignores them — its snapshot freezes the **all-time** per-FLW aggregate, so a recurring review's Week 1 and Week 2 snapshots render **identical** numbers (ace#764). Set `"period_scoped": true` on the **pipeline schema** of a recurring/periodic template so completion re-aggregates that pipeline to the run's half-open `[period_start, period_end)` visit-date window instead. The window is applied at read time over the pipeline's existing cache — no download, no recompute, nothing written back; the window is excluded from the cache hash, so one cache serves every period. Only the AGGREGATED (FLW) terminal stage is supported today. If the pipeline's raw-visit cache has been evicted, completion fails with the same "reload the dashboard" `PipelineCacheMiss` as the all-time read rather than freezing a wrong/empty slice. `llo_weekly_review` opts in; `performance_review` could too once validated.

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
   - Resolves the snapshot contract via `resolve_snapshot_contract` — the definition's own `snapshot_inputs` first, then the template registry (hook templates, legacy instances, name-match recovery). Refuses with **400** when nothing resolves.
   - Reads pipeline data **from the processed cache only**, scoped to the aliases the contract captures (`WorkflowDataAccess.get_cached_pipeline_data`). Completion **never executes a pipeline**: the snapshot's job is to freeze what the user was looking at, and a conclude-time re-execution both captured the wrong data and turned the button into a multi-minute batch job (102k visits ≈ 18 minutes + an OOM-killed worker). An empty `pipelines: []` manifest skips the read entirely; a cache miss refuses with **409** ("reload the run page, then conclude").
   - Calls `build_snapshot_for_contract(...)` to produce the snapshot, and self-heals the definition (stamps a name-recovered `templateType` and/or the template's manifest as the instance manifest) when it had to fall back to the registry. Refuses with **400** (`SnapshotTooLargeError`) if the snapshot would exceed the 5 MB hard cap.
   - Atomically flips status to `completed`, stamps `completed_at`, persists the snapshot.
3. On 200 → reloads the page; the runner re-mounts in completed mode and reads from the snapshot.
4. On error → surfaces a `window.alert`; run stays `in_progress`.

Server-side write protection: while a run is `completed`, `update_state_api`, `save_worker_result_api`, and any other mutation endpoint return **409**. State is genuinely immutable, not just defensively read-only on the FE.

### Re-running

There is no "edit a completed run" path. The pattern is **re-run = new in_progress run**. The run picker's `Start Run` button creates a fresh run; the completed one stays in the history list. This matches what users actually want when they say "compare to last week."

### Rebuilding history

A periodic report's trend draws one point per saved run, so a report created last week has one point and no line. `workflow/history_rebuild.py` writes the runs that report **would** have if it had been running on its cadence all along: one completed run per period, each computed as of that period's end, all graded against the definitions in force **now**.

Reach it through the MCP tools `workflow_rebuild_history` (writes) and `workflow_history_eligibility` (reads only). Both are generic — they resolve the definition's own snapshot contract, so any eligible workflow gets this without a line of template code.

Three properties are worth knowing before using it:

- **Rebuilt points can move, by design.** A saved run used to be evidence of what we said that week. A rebuilt one is what we would say _today_ about that week — later-syncing visits and current credibility judgements included. That is what you want while indicator definitions are still being settled (edit the registry, rebuild, and the whole series restates), and not what you want once they are settled. So rebuilding is an explicit operation a person invokes; nothing triggers it on a read.
- **Only periodic builders are eligible.** A builder that ignores `period_end` returns the same payload for every date, which renders as a flat line across real dates — indistinguishable from a programme that did not move, with no error anywhere. Eligibility is therefore a declaration, `PERIODIC_BUILDERS` in `snapshot_builders.py`, kept beside the builders and proved by `tests/test_periodic_builders.py` rather than asserted.
- **It only replaces its own output.** Every run it writes is stamped `state.generated_by = "history_rebuild"`, and only stamped runs are deleted. A run someone created and named by hand survives a rebuild of the same period. The new run is also built and completed _before_ the old one is deleted, so a failed build leaves the existing history intact — a transient duplicate costs nothing, because the trend keys its points by as-of date.

Cost is the real constraint: an as-of date invalidates the whole evaluation chain, so nothing amortises across points and each period is a full pass (order 10s on a cohort of ~9,000 cases). A year of weekly history is minutes. The pipeline cache must already be warm; a cold cache stops at the first period rather than failing every one identically. `dry_run: true` reports the period count and the plan without writing.

### Action-shaped templates (opt-out)

`audit_with_ai_review`, `bulk_image_audit`, `ocs_outreach`, `sam_followup`, and most `kmc_*` dashboards are action-shaped — their artifacts persist in their own models (audit sessions, tasks, child records). They don't declare `supports_saved_runs`. The runner doesn't show a complete button; the run picker labels them as working sessions rather than reviews.

`kmc_programme_metrics` is the exception among the KMC templates: it is a periodic report and does declare saved runs, via the `semantic_snapshot` builder. Its drill pages (`kmc_flw_review`, the case view) stay action-shaped and read the report run's snapshot through `&source_run=`, so the whole drill inherits one run's as-of rather than each page freezing its own.

### Size budget

Snapshots live inside `LabsRecord.data` JSON. The framework warns at 1 MB per snapshot and **rejects at 5 MB** (`SnapshotTooLargeError` → 400 from the complete endpoint; the run stays in*progress). Don't capture raw pipeline rows from large opps at all — have the render compute the derived per-row table it displays and freeze that into a state key in the same `onUpdateState` write that precedes `view.complete()` (see `mbw_auditing_v5`'s `concluded*\*` keys). The verbatim-capture failure mode is real: a 102k-visit opp produced a 112 MB snapshot and OOM-killed a web worker.

### Reference implementation

`connect_labs/workflow/templates/performance_review.py` is the canonical run-shaped template, and exemplifies the manifest path:

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

`connect_labs/workflow/templates/chc_nutrition_analysis.py` is the
canonical flags+actions template:

- `DEFINITION["flags"]` declares `sam_low`, `mam_low`, `gender_skew`.
- `DEFINITION["actions"]` declares `create_audit`, `send_task`.
- A render-local `FLAG_CATALOG` constant pairs each declared flag key
  with its `predicate(row)` and `evidence(row)` functions.
- The `React.useEffect` on `rows.length` computes flag presence per row
  and POSTs missing ones via `view.ensureAutoFlags`.
- Per-row `MenuButton` widgets render Create Audit / Send Task with
  flag-context-aware quick actions.

`connect_labs/workflow/templates/program_admin_report.py` is the
cross-opp rollup. Its `build_snapshot` reads flags + audits + tasks
independently per run (via `FlagsDataAccess.get_flags_for_run`,
`AuditDataAccess.get_sessions_by_workflow_run`, and
`TaskDataAccess.get_tasks_for_run`) and groups them into `flw_rows` for
the render layer.

## 11. Long-running job progress

Any workflow that kicks off a long backend job (audit creation, a program-level
fan-out, an analysis pass) needs to show a live progress bar. There is **one
sanctioned way** to do this. It is not the obvious one, and getting it wrong
costs real rework — a prior build spent a long session rediscovering the two
traps below.

### The one pattern

1. **The job persists progress to run state as it works.** Each tick writes
   `{status, processed, total, message}` into the run's `state.active_job` (job
   level) and/or `state.generation[<row_key>]` (per-row). Throttle the write to
   ~1.5s with `connect_labs.utils.throttle.throttled` — ticks fire hundreds of
   times but each persist is a rate-limited LabsRecord write.
2. **The render reads that persisted state.** The runner already refetches run
   state periodically and repaints from it, so persisted `generation` rows show
   their bars with no extra wiring. This — not a live event — is what actually
   paints per-row bars.
3. **For the job-level bar, poll the JSON status endpoint.** `streamJobProgress`
   / `streamAuditProgress` are **poll-first**: they hit a JSON status endpoint on
   an interval (`.../status.json` for jobs, `.../status/` for audits) via the
   shared `streamTaskProgress` client. No connection is held.

### Why not just stream it over SSE? (the two traps)

The web tier runs `gunicorn -k uvicorn.workers.UvicornWorker -w WEB_CONCURRENCY`
(default **3** workers). Django serves an SSE endpoint as a **sync generator that
holds a worker thread for the stream's entire lifetime** — and `BaseSSEStreamView`
spawns a **second** heartbeat producer thread per stream. So:

- **Trap 1 — concurrent streams starve the tier.** A handful of held streams
  (several users each running a job, or one page opening a stream per row)
  exhaust the small thread budget and the whole worker set wedges. This is a
  real multi-user ceiling, not a per-page cosmetic. **Polling holds nothing** —
  each poll is a fast Redis read that returns immediately, so N users scale fine.
- **Trap 2 — SSE `item_result` events don't reliably paint rows.** Per-row bars
  are painted by the periodic run-state refetch (step 2), not by the event
  stream. Emitting `item_result` alone leaves rows stuck at "running" with no
  bar. Persist to `generation`.

SSE is kept as an **opt-in** transport (`transport: 'sse'` on `streamTaskProgress`)
for the rare case where sub-second push latency matters and concurrency is known
to be low. Default to polling.

### Fanning out per-row progress from an in-process child job

When a parent job runs a child **eagerly in-process** (`child.apply(...)`) and
wants the child's fine-grained progress forwarded up to a parent-owned row, you
**cannot** pass a `progress_callback` closure through `.apply(kwargs=...)` —
Celery serializes the kwargs and chokes on a function (this silently produces
zero results). Use the in-process relay registry instead:

```python
from connect_labs.utils.progress_relays import register_relay, pop_relay

register_relay(run_id, lambda msg, processed=0, total=0: ...)  # BEFORE .apply()
try:
    child.apply(kwargs={...})          # child looks up the relay by run_id
finally:
    pop_relay(run_id)                  # always clean up
```

The child resolves its relay with `get_relay(workflow_run_id)`. This only works
because parent and child share a process (the eager case); across a real worker
boundary `get_relay` returns `None`, a safe no-op.

### Reusable primitives

| Primitive                                                    | Where                                         | Use                                                                                                                      |
| ------------------------------------------------------------ | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `throttled(fn, interval=1.5)`                                | `connect_labs/utils/throttle.py`              | Rate-limit the run-state persist (or any chatty side effect); `force=True` for the terminal write.                       |
| `register_relay` / `get_relay` / `pop_relay`                 | `connect_labs/utils/progress_relays.py`       | Forward child-job progress up without a Celery-serialized closure.                                                       |
| `build_task_progress(state, info)`                           | `connect_labs/labs/analysis/sse_streaming.py` | The single Celery-meta → UI-progress translation. Every SSE + poll endpoint goes through it — never re-derive the shape. |
| `streamTaskProgress({transport, statusUrl, streamUrl}, cbs)` | `connect_labs/static/js/task-progress.ts`     | Poll-first (default) or SSE progress client; `onItemResult` / `onCancelled` optional.                                    |
| `JobStatusAPIView`                                           | `connect_labs/workflow/views.py`              | JSON poll endpoint for a workflow job (`api/job/<task_id>/status.json`).                                                 |

## 12. Scheduling (recurring default runs)

Any workflow whose template supports a **default run** (`TEMPLATE["supports_default_run"] = True` + a callable `run_default` hook — see §"Default run") can be scheduled to run itself on a recurring cadence with no user logged in.

- **Model:** `connect_labs.labs.models.WorkflowSchedule` — one row per `(definition_id, scope, owner)`. Scope is `opportunity_id` XOR `program_id` (from `labs_context`). Cadences: `daily`, `weekdays` (Mon–Fri), `weekly` (+`day_of_week`), `monthly` (+`day_of_month` 1–28), each at a chosen `hour` (UTC). Next-fire math lives in the pure helper `connect_labs.workflow.schedules.compute_next_run`.
- **Identity / auth:** a schedule stores only its **owner**, never a token. At fire time the run mints a fresh Connect access token from the owner's persisted `UserConnectToken` via `connect_labs.labs.connect_tokens.get_valid_access_token(owner)`. If auth is permanently dead — any `ConnectTokenError` (no stored `UserConnectToken`, expired with no refresh token, or a dead refresh token / `ConnectReLoginRequired`) — the schedule is auto-disabled and marked `auth_expired` ("Needs re-login" in the UI). Transient network errors leave it enabled to retry next cadence.
- **Execution:** a single seeded `PeriodicTask` (django-celery-beat, every 15 min) runs `connect_labs.workflow.tasks.run_due_workflow_schedules`. For each enabled, due row it **claims** the row by advancing `next_run_at` to the next window **before** dispatch, via an optimistic conditional update (`.filter(pk=..., next_run_at=<current>).update(next_run_at=<next>)`), and only dispatches `run_scheduled_workflow.delay(schedule_id)` if the claim won (matched 1 row). This gives **at-most-once-per-window** dispatch even if a prior beat crashed after enqueueing or two ticks overlap — the loser's update matches 0 rows and skips. (Missing a run on a worker crash is acceptable; double-firing a non-idempotent hook is not.) `run_scheduled_workflow` then resolves the token, loads the definition, and calls `run_default_for_definition`; `run_default` hooks are additionally expected to be idempotent per window, but the claim is the primary guard against double-creation.
- **UIs:** enable/edit a schedule per-row from the **workflow list screen** (owner-scoped, self-service); manage/disable/delete **all** schedules from **Labs Admin → Scheduled Workflows** (`labs_admin:schedules`, Dimagi-gated, acts on any owner's schedule).

---

## 12. Reports that stay in step (followed templates, fan-out, warm-on-read)

When many workflows are meant to be **the same report over different scopes** — one KMC Opportunity Report per opportunity, say — every per-instance copy is something to keep in step. These mechanisms make an instance own as little as possible.

### Following the deployed template (`render_source`)

`render_source: {"template": "<key>"}` on a definition makes the page render the **deployed** template's code instead of its stored copy (`connect_labs/workflow/render_source.py`). A deploy reaches every following instance at once, and there is nothing to sync. Edits to a following instance's stored render are **refused (409)**, not silently ignored. Setting `render_source` to `null` forks: the template's current code becomes the stored copy. Only a null/absent `render_source` drifts from the repo, so check it before reaching for `workflow_sync_from_deployed_template` or `workflow_patch_render_code`. The only allowed value is the workflow's **own** template (`config.templateType`).

### Following a template WORKFLOW (`render_source: {workflow: ...}`) -- the no-deploy path

`render_source: {"workflow": <template workflow id>, "opportunity_id" | "program_id": <its scope>}` follows a **template workflow**: a workflow whose render code, config defaults and `snapshot_inputs` live as data in **LabsRecords** in the template's home scope (a program or an opportunity; logic in `connect_labs/workflow/template_workflows.py`). Three record types in experiment `workflow`: the template's own `workflow_definition` IS the template (`data.template_workflow`: template type, seed, the published pointer with a copy of that version's config/snapshot spec, the draft record id, the listed followers); `workflow_template_draft` (one editable draft, child of the definition, never public); `workflow_template_version` (one immutable record per publish, child of the definition, public exactly when the template is). Prefer it to `{"template": <key>}` for any report you expect to iterate on: a change is an edit and a publish, not a PR and a deploy.

- **Draft and published.** Edits go to the template's single draft (`workflow_template_update_draft`, optimistic on `draft_revision`). `workflow_template_publish` writes an immutable `workflow_template_version` record; followers render the published one on their next load. `workflow_template_rollback` publishes a COPY of an older version (`restores_version`), so history only grows.
- **Preview.** `?template_draft=1` on a follower's run page renders it with the draft -- render, config and snapshot spec -- for people with **write access to the template's scope** only -- the draft record is read in scope and is never public (`template_workflows.preview_drafts`, wrapped around `WorkflowRunView.get_context_data`); the page shows a draft banner. Everyone else, and every other request (the semantic/snapshot APIs the page calls), sees the published version. `workflow_template_preview` returns each follower's `draft_preview_url` and a diff.
- **Inheritance.** `WorkflowDataAccess.get_definition` / `list_definitions` return the follower's **effective** definition: the template's published `config` and `snapshot_inputs` (top-level keys) underneath the follower's own, which win. The raw record is `record.own_data`. Precedence for config: follower > template workflow > code template (`with_inherited_config_flags`, keyed on `templateType`).
- **Writes never bake inherited values in.** `WorkflowDataAccess.update_definition` (and the other definition writes in `data_access.py`) pass `strip_inherited` first: a follower key **equal** to the published template value is dropped. So a read-modify-write of the effective definition is safe, and `workflow_follow_template` reports which keys became inherited (`now_inherited`) and which are real overrides. Consequence: an override equal to the template value is not an override; it follows the template from the next write.
- **Permissions are the LabsRecord ACL -- there is no template-specific permission (#2236).** OWNERSHIP: write access to the home scope (a program: members of its managing organization; an opportunity: members of its organization, supervising organization or program organizations -- Connect's `data_export` checks scoped reads and writes with the same test) means edit the draft, preview, publish, roll back and change sharing. Every write goes out under the caller's own token, so Connect enforces it again. USE: anyone who can READ the template may follow it -- in its home scope, or from any program or opportunity once an owner shares it (`workflow_template_set_sharing public=true`, the same LabsRecord `public` flag `share_workflow` sets, applied to the definition and every version). A follower page reads the template in its home scope and falls back to the public record, the pattern of the registry fix in #2219 (#2216): a viewer who is not a member of the template's scope still renders a shared template. `template_scope` only places it in the template picker; it grants nothing. A follower must have the same `config.templateType`. A labs-only (synthetic) scope has no membership check, as for every other record there.
- **Followers list.** `data.template_workflow.followers` on the template is written by `workflow_follow_template` when the caller can write the template's scope; a follower outside it still follows (its `render_source` is the follow), it is just not listed. Previews report against the listed followers.
- **Data, but only the classes the page has.** The run page loads the compiled `tailwind.css`, so a template render can use only Tailwind classes that some deployed code already uses. A class that appears nowhere in the repo does nothing until a deploy carries it.
- **Code templates are seeds.** `workflow_template_create` seeds from a code template (`template_key`) or an existing workflow (`from_workflow`) and publishes v1. After that the code template's render plays no part for followers; its `templateType` still selects the Python-side contract (snapshot builder, hand-down, `supports_saved_runs`). The template's own definition record carries `is_template`, `template_scope`, `templateType` and the `template_workflow` metadata, and follows itself, so opening it shows the published render. `workflow_template_create` keeps the seed workflow's own scope unless you name another program or opportunity.
- **Edits to a follower's stored render are refused** (`RenderFollowsTemplateWorkflow`, 409 on the web save, `CONFLICT` on the MCP), as for a code-template follower. `workflow_follow_template` with `follow: false` forks: the published render becomes the stored copy and inherited values are written into the follower's own record, so the page does not change. Setting `render_source` to the `{workflow}` form through `workflow_update_definition` is refused -- use `workflow_follow_template`, which checks the template is readable and lists the follower.

### The way back: promoting a template workflow's fixes to the code template

A template workflow is a fork of its code template the moment it is seeded. Fixes published to it reach **its followers only**: every new programme is seeded from the code template in this repo, starts without them, and the same defects are found again (a Spark demo loop published 21 page fixes to three template workflows; a reseed for #2251 lost all of them, and the next loop re-found five of the same classes). So when a template's fixes are generic, promote them:

```bash
python tools/promote_template_workflow.py --template <id> --program <home program>   # or --opportunity <id>
```

It calls `workflow_template_export` (read-only; anyone who can read the template) for the published render beside **version 1 -- the verbatim seed** -- and runs `git merge-file <repo render> <seed> <published>`: whatever the code template gained after the seed is kept, only the template's changes are applied, and a clash is left as conflict markers. It prints every version note between the two, which is the PR body's changelog. `--version N` exports an earlier version, `--draft` the draft (editors), `--dry-run` reports without writing, `--from-json` merges a saved tool result (`include_code=true`) with no network. Two templates that share one render (the programme and opportunity reports share `indicator_report_render.js`) are merged one after the other; check the result for two helpers of the same name (the `kmc_render.test.mjs` duplicate-declaration check fails on it).

Then **read the diff, don't ship it whole.** Port what is generic; drop what fits one programme (a hard-coded noun, a guess from a flag's wording -- give the registry a key instead, as `display.visit_flags[].fields`/`description` replaced one); drop what a later version reverted (the export is net, but read the notes). A fixer working on data can only reuse Tailwind classes already deployed and cannot touch the shared library, so it often copies a library component into the render; when promoting, consider fixing the library instead.

The generic indicator renders are held to `connect_labs/workflow/templates/__tests__/indicator_render_guards.test.js`: mounted on a fixture snapshot, they must pass WCAG AA text contrast, show no table column that is the same on every row (indicator columns exempt), state the rule behind every Watch / Off-target colour they show, and never shade a chart gap across a visit -- the classes the demo judges kept re-finding. A promoted fix that breaks one fails `npm test`.

**The engine boundary.** A render reads only what the snapshot builder and the labs APIs provide. Layout, order, charts, labels and which config keys a render reads are template data. A new figure (e.g. a daily count `semantic_snapshot` does not compute yet) is an ENGINE change: code, PR, deploy. Ship the engine change first, then use the new field from the template's draft.

### Config resolved from the template on read

`templates.with_inherited_config_flags` fills in, at read time, every config key the template declares that the instance lacks. So a key added to a template later (`noPipelineStream`, `renderWhileLoading`, `warm_cache_on_read`) reaches instances created before it, with no migration. **Limit:** a key the instance already carries keeps the instance's value. That is deliberate (a per-instance override must survive), so changing the **value** of an existing key in the template does not reach existing instances; patch them with `workflow_update_definition`.

### Fan-out: one report per cohort member (`benchmarks_create_opp_reports`)

Creates one instance of a template (default `kmc_opp_report`) in each opportunity of a benchmark cohort that lacks one. It is idempotent, so re-running it after adding an opportunity to the cohort creates only the new one. Every instance:

- follows the deployed template (`render_source`);
- references the **source** report's pipeline records via `home_scope` rather than copying them — one pipeline read, one cache;
- binds the source report's **registry record**, so an indicator edit (`semantic_registry_update`) reaches all of them with no deploy.

Pass `source_workflow_id` (+ its scope) to get that sharing; without it each instance makes its own pipelines and registry, and the result says `shared: false`. A new instance has no run, and the page needs one: `workflow_create_run` per instance.

### Warm-on-read (`warm_cache_on_read`)

A report that fetches its figures from the semantic endpoint and never streams its pipelines (`noPipelineStream`) would otherwise depend on someone else filling the visit cache, which is held for 90 minutes. With `config.warm_cache_on_read: true`, the semantic endpoint (`api/<id>/semantic/`) fills any of the workflow's opportunities that have no live cached visits before evaluating, using the same `ensure_visit_cache` as the `workflow_ensure_visit_cache` MCP tool. It is best effort: a failure falls back to the `cold_cache` / `partial_cache` flags. It is opt-in, so a multi-opportunity report never turns a page load into a download of every opportunity. `kmc_opp_report` sets it. The live snapshot preview (`api/run/<id>/snapshot/preview/`) honours it too: a `cache_miss` warms the cache and builds once more, so an in-progress run of a report that streams no pipelines never tells its reader to open some other page first.

### Handing a saved run down (`hands_down_to_opportunity_reports` / `receives_hand_down`)

A programme report's saved run already graded every opportunity in it. When a run of a template with `hands_down_to_opportunity_reports` completes (web save, `workflow_save_snapshot`, or a finished history rebuild), a Celery task running as the saver cuts each opportunity's slice out of the snapshot and writes it as a **completed run** of that opportunity's report: any workflow whose template sets `receives_hand_down` and that names the programme report as its source (`config.source_workflow_id`, or a benchmark cohort containing its opportunity whose `source_workflow_id` is the programme report). The network manager reads only runs of their own report and never needs access to the programme report. See `connect_labs/workflow/hand_down.py`.

- A slice carries **nothing** of any other opportunity: rows, cases, series, credibility facts and the programme's pooled figures are removed, not hidden (`test_hand_down.py` pins it, including that every key the builder emits is either sliced or known safe).
- Idempotent per week: the same source run is written once; a newer save of the week replaces an older hand-down (new run completed before the old is deleted); a run the opportunity report saved itself is never touched.
- A run saved before the unified KMC indicator set (#2004) is skipped, not translated.
- Backfill with the `workflow_hand_down` MCP tool: no `run_id` walks the whole history in the background (one run per week, latest completion), a `run_id` hands one run down synchronously and returns the report.

### What still needs a person

- A **new opportunity** in the programme: add it to the cohort (`benchmarks_cohort_add_opportunities`), then re-run `benchmarks_create_opp_reports`.
- A **changed config value** in the template: patch existing instances (see the limit above).
- Benchmarks: see `connect_labs/benchmarks/README.md`. Publication follows the source report automatically once the cohort's `source_workflow_id` and `auto_publish_on_completion` are set.

---

## 13. Semantic registries (indicator definitions as data)

Use this section when a report's figures are **indicators**: a numerator over a denominator, graded against bands and a minimum denominator, at several levels (programme / LLO / opportunity / worker / month). Don't compute them in render code. Put the definitions in a **semantic registry** and let the engine compile them to SQL. The page then only displays and grades what comes back, and an indicator edit reaches every report bound to the registry without a deploy. The full user-facing guide is `user_docs/semantic-layer.md`; engine internals are in `connect_labs/semantic/`.

### The registry is general; KMC is one example

A registry is three documents. `properties_doc` holds the MODEL, the constants, the per-entity aggregates and the properties. `indicators_doc` holds the measures, suppression rules, `defaults` and `series`. `deployment` holds optional facts: `llo_map`, credibility `settings`, `app_asks`, `asks_as`. The model says what the engine counts:

```yaml
entity: {
    name: beneficiary,
    plural: beneficiaries,
    key: entity_id,
    cohort_date: first_visit,
    worker: last_visit, # optional: alphabetical (default) | first_visit | last_visit
  }
visit_columns: # derived per-visit columns added to Layer 1
  - { name: is_approved, word_match: { column: status, word: approved } }
  - { name: is_flagged, sql: 'COALESCE(flagged, FALSE)' }
  # window kinds: the case's previous visit (the value, or the GPS distance from it)
  - name: prev_date
    previous:
      { column: visit_date, partition_by: [entity_id], order_by: visit_date }
  - name: metres_moved
    distance_from_previous:
      { lat: lat, lon: lon, partition_by: [entity_id], order_by: visit_date }
pipelines:
  entity: visits # + extra_fields: {<column>: <pipeline alias>} -- same forms, another pipeline's paths
  lookups: # another SOURCE's own rows (CommCare HQ forms), joined per opportunity on a key
    reg:
      pipeline: registrations
      on: entity_id
      key: case_id
      fields: { eligible: eligible_flag }
      pick: latest
# weight_series: {...}               # optional per-entity reading series (KMC's weights); needs value_column
```

Two examples ship: `registry/visit_quality` (per beneficiary, generic Connect visit columns, series Q, no LLOs) and `registry/kmc` (per baby, weight series, LLOs, one family `KMC` with slug ids such as `mortality`). Copy the closer one. `indicators_doc.series` declares the family; in a registry declaring one, every indicator belongs to it whatever its id looks like. With several, an indicator names its family by `meta.series` or an id prefix.

### Tools

| Step                                | Tool                                                                                                                                                                                                                                                               |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Start a registry                    | `semantic_registry_create` (`seed_from: 'visit_quality'` or `'kmc'`, or pass documents). Needs its home scope: one of `organization_id` / `program_id` / `opportunity_id`. A create with none is refused.                                                          |
| Check before saving                 | `semantic_registry_validate`. It compiles at every scope the registry can have, and allow-lists every SQL fragment: no subqueries, no other tables, no comments, constants numeric.                                                                                |
| Add, replace or remove an indicator | `semantic_registry_update` with `upsert_measures` / `upsert_properties` / `upsert_aggregates` (by `name`; `insert_after` places new ones), `remove`, and `expected_version`. Validated against the merged registry. Send whole documents only for a rewrite.       |
| Change one indicator's display keys | `semantic_registry_set_indicator_meta`. The smallest edit there is: one meta key, nothing else resent.                                                                                                                                                             |
| Read the exact logic                | `semantic_registry_explain`. Pass indicator IDs; with none, you get the index.                                                                                                                                                                                     |
| Delete a registry                   | `semantic_registry_delete` with `registry_id` and the one home scope it lives in. Needs write access to that scope. Refused while any workflow's `registry_source` binds it (the refusal names them); the check covers every scope you hold plus shared workflows. |
| Bind a report                       | `workflow_update_definition` patch `registry_source: {registry_id: N}`, or `workflow_create_from_template(..., registry_source=...)`. Omitting it seeds a private copy.                                                                                            |

Registry writes only succeed from the record's **home scope** (the organisation, programme or opportunity it was created in). A shared record can be read and bound from anywhere.

### Render contract

```
GET /labs/workflow/api/<workflow_id>/semantic/?scopes=opportunity,flw[&series=<family>]
    [&as_of=YYYY-MM-DD] [&flw=<opportunity_id>::<username>] [&catalog_only=1] [&registry_id=<candidate>]
→ { rows: [{scope, opportunity_id?, username?, llo?, cohort_month?, case_id?, n_cases,
            <measure>, <measure>_numerator, <measure>_denominator, <measure>_suppressed?, anyrec_<input>?}],
    measures: <catalog: titles, units, directions, bands, min_denominator, inputs…>,
    deployment, cold_cache, partial_cache, opportunities_missing,
    lookups_missing: {<lookup>: [opportunity ids whose lookup pipeline has no cached rows]}, … }
```

- **Grade in the page, from `measures`**: minimum denominator, bands, n/a from `anyrec_*` and `inputs`, and not-credible from `_suppressed`. The SQL returns counts only. (Better: save runs with the `semantic_snapshot` builder, which grades server-side, and render the stored payload -- as every KMC report now does.)
- Show `cold_cache` / `partial_cache`. A cold cache reads as zeros, not as "no data".
- `scopes=case` returns one row per entity. Use it for drill-downs and for checking numbers.
- `registry_id=` evaluates a candidate registry without binding it.
- The definitions popup reads `/labs/workflow/api/<workflow_id>/indicator-definitions/` (`?format=json|md|sql`).
- Saved runs: `snapshot_inputs: {builder: semantic_snapshot, series, scopes, case_index: {pipeline, fields, date_fields}, credibility, …}` (see §9 and `kmc_programme_metrics.py`).

### Don'ts

- Don't hand-write Layer 1 SQL. It is generated from the pipeline named in `pipelines.entity`, and that's where form paths and their fallbacks live.
- Don't copy indicator logic into render code. That's the two-copies drift the semantic layer exists to end.
- Don't create a report from a template without `registry_source` when a registry for that indicator family already exists.

---

## 14. Workflow actions, and sharing a run with an agent

Two separate things. Neither one depends on the other.

### Workflow actions (`config.actions`)

An action is something a workflow lets you **do**, such as "Initiate AI coach". You define it once, and every entry point runs the same code (`connect_labs/workflow/actions.py`):

- **A button on the report.** Render code calls `actions.runAction(key, {workers: [{key}]})`. The runner previews the action in its own dialog and runs it only when the person confirms. `view.workflowActions` lists the declared actions so a render can draw their buttons (`indicator_report_render.js` does).
- **The labs MCP.** An agent calls `workflow_run_action`. That agent can be a person's own, signed in with a PAT, or canopy acting as the visitor on a page that shares its run.
- **A coaching send is a click, never the agent's call.** A `start_ocs_outreach` preview, as the agent gets it from `workflow_run_action`, carries no `confirm` (`sent_by: "click"`, and `next` says where the person clicks). `workflow_run_action` is an MCP Apps tool (SEP-1865; `_meta.ui.resourceUri = ui://labs/workflow-action-preview`, `connect_labs/mcp/ui/`): a host such as canopy renders its result as a View that previews again AS THE VIEWER through the app-only `workflow_action_preview_view` -- each worker's picture inline as a `data:` PNG, and that viewer's own token -- and commits through `workflow_run_action` on the viewer's click (Send to the worker; Send to me (QA test) with `deliver_to`, staff only; Not yet). The run page's button is the other way to send. Other action types keep the agent's two-call flow.

```python
"config": {
    "actions": [
        {
            "key": "initiate_ai_coach",       # the workflow's name for it
            "type": "start_ocs_outreach",     # which framework action it is (actions.ACTION_TYPES)
            "label": "Initiate AI coach",     # what its button says
            "defaults": {"bot": "<OCS bot id>", "prompt": "..."},
        }
    ]
}
```

- **Types are framework code, not render code.**
  - `create_task` makes one follow-up task per worker, attached to the run.
  - `start_ocs_outreach` makes that task plus an Open Chat Studio conversation. On synthetic opportunities it attaches a sample coaching conversation instead, and no message is sent. Two optional arguments:
    - `workers[].indicators` — the indicator keys (e.g. `["SF_P1", "SF_P3"]`, worst first) that worker is coached on. Stored on the new task as `task.data["coaching_indicators"]`, the denominator `/labs/workflow/api/chatbot-status/` reads for coaching progress.
    - `deliver_to` — a QA redirect: a ConnectID username that receives the conversation instead of the worker. The task is still the worker's; the OCS `session_data` (and the task's AI-session record) carry `qa_recipient` and `on_behalf_of`. On a synthetic opportunity it makes a **real** OCS call, so staff can QA the actual bot from a synthetic report. Dimagi staff only (`utils/dimagi_user.is_dimagi_user`; anyone else gets `forbidden`), one worker per run (several redirected workers would share one OCS participant), never a declaration default (`declaration_problems` refuses it), and part of the confirmed arguments, so the preview states it per worker (`sending_to`) and the token is bound to it.
  - **On an indicator report, each worker is briefed from the run's grading** (`workflow/coach_briefing.py`, reading `workflow/run_grading.py` -- the same cells `workflow_run_indicators` returns). A worker item with no `prompt` of its own gets a `BRIEFING (system text ...)` block naming the programme (the registry's `display.title`, else the workflow's name), the worker, and their red then yellow indicators in registry order (`meta.flw_applicable` not false only), each as `<label> [<ID>] — <numerator> of <denominator> (<pct>%), band <red|yellow>`; the action's own `prompt` is appended under `Programme team's note:`. That shape is a contract with the coaching bot (ACE `lib/coach-briefing.ts` `renderBriefing`). The topics become the item's `indicators` unless given. A worker with nothing red or yellow is left out and listed in the preview's `skipped` with the reason; if nobody is left, the preview has no `confirm`. The preview's `arguments` carry the composed briefings, so committing them never re-grades. On synthetic data the preview names the DECLARED bot and says (`synthetic_note`) that no message is sent; `qa_redirect: true` tells a page it may offer `deliver_to` (Dimagi staff, one worker). The report render offers the coaching button only to workers with something red or yellow.
  - **A briefing is never sent to the worker** (`tasks/ai_sessions.py`). OCS's `trigger_bot` `prompt_text` is written up by a generic LLM call outside the bot's pipeline, so a briefed conversation instead sends OCS `message_text` -- a fixed opening, verbatim: `Hello <first name>! This is a short, friendly check-in about how your work has been going. Is now a good time to talk for a few minutes?` (plain `Hello!` when the `Worker:` line looks like a username or code) -- and puts the briefing in `session_data.coach_briefing`, which the coaching bot reads as `{session_state.coach_briefing}`. The preview shows that line per worker as `opening`. Any non-briefing `prompt` keeps the `prompt_text` path.
  - **`include_image: true` (default false; a workflow's `defaults` may set it) attaches a picture of each briefed worker's own figures** (`workflow/coach_image.py`). The session state gains `coach_image_url` -- `LABS_PUBLIC_URL` + `/labs/coach-image/<signed token>/` -- and `coach_image_caption`, one sentence naming the topics (no numbers). The link is SIGNED, not stored: the worker's name and the figures of the briefing's own topics (read back from the briefing text, so after `fit_briefing` trimmed it) ride inside it, it expires after 7 days, and the PNG (1080 px wide and as tall as its content; per topic the label, the figure in bold as `31 of 73 · 42%`, and a bar of numerator over denominator in the band's colour; no targets) is drawn on each fetch. Open Chat Studio fetches it with `Authorization: Bearer <PAT>` where the PAT has the `coach-images` scope (mint one at `/labs/mcp/tokens/`, "Coaching pictures only"); any other token is refused, and the MCP server and export API refuse a `coach-images` token. A signed-in Labs user may also open the link in a browser (to see the picture before sending, e.g. in the canopy panel beside a run): the link carries the worker's `opportunity_id`, and the view opens it only for a live Labs sign-in that clears the same opportunity-access check the MCP tools use. A request with a Bearer header is always judged as the token's; a link with no `opportunity_id` (any issued before this) opens for the token only. Only a briefing gets a picture -- a worker whose `prompt` is free text gets none -- and the preview shows it per worker as `image: {url, caption}`.
  - A type is an `ActionType` in `ACTION_TYPES`: its argument schema plus an `execute(ctx)` that does ONE worker and writes what to record into `ctx.record`. A declaration's `type` is resolved by looking it up there, both to describe the action (`declared_actions`) and to run it (`execute`). To add a type, add one entry; nothing else dispatches on it.
  - A config entry with no known `type` is not offered. §10's catalog entries document render-code buttons; they are not actions.
- **Checked when it is saved.** `workflow_update_definition` refuses a `config.actions` with an unknown type, a duplicate or non-slug key, or `defaults` that don't fit the type's schema (`actions.declaration_problems`). A test holds every template's declaration to the same rule.
- **Preview, then commit.** Running an action takes two calls.
  - The **preview** returns exactly what would happen: the workers, the bot and the text. It also returns a single-use `confirm` token, bound to the person, the run, the action and those arguments.
  - The **commit** must carry that token. Change anything in between and it is refused.
  - `needs` lists what the preview is waiting for: `bot` (choose from `bot_choices`) or `connect_ocs` (the person connects OCS at `connect_url`).
- **It runs in the background, as the person.** Execution is the `execute_workflow_action` celery task. It uses the person's stored Connect token (`UserConnectToken`) and OCS token (`UserOCSToken`, saved when they connect OCS). No browser is needed.
  - Each task is filed in its **worker's** opportunity, which matters on program reports that span several.
  - `WorkflowActionExecution` records who ran the action, through which entry point (`page`, `mcp` or `canopy`), for which workers, and the outcome per worker.
- **Off by default, and never inherited from a template that says nothing.** Set it per workflow with `workflow_update_definition`.

### Sharing a run with the embedded agent (`config.agent.share`)

`{"agent": {"share": true}}` puts the canopy SDK's agent panel on the run page (`workflow/agent_sharing.py`). It is off by default and set per workflow.

- **What the agent is handed** is the selection, not the rows: the run, the scope it is filed under and the worker keys (`<opportunity_id>::<username>`).
- **When the report drills** into an organisation, opportunity or worker, render code calls `view.shareSelection({visible_ids, drilled})`, and the agent follows. That call is a no-op on a page that does not share.
- **The agent reads the run as the visitor.** `workflow_run_context` gives the indicators with their thresholds and the workflow's actions. `workflow_run_indicators(band="red")` gives the graded cells; bands come from the server's grading, `semantic/snapshot.py:band_of`. `workflow_indicator_explain` explains the bound registry.
- **The agent acts** through the same `workflow_run_action` as everyone else.
- **Canopy calls** to the `workflow_*` run tools are refused on a workflow that does not share. A person's own agent is not affected.

## 15. Supply sources (supply chain data, beside pipelines)

A workflow can read the supply chain's own figures — stock in workers' hands, the stores, orders, tenders — as **supply sources**, declared next to `pipeline_sources` (`connect_labs/workflow/supply_sources.py`):

```json
"supply_sources": [
  {"alias": "stock",  "source": "worker_stock",     "item": "rutf", "params": {"window_days": 14}},
  {"alias": "stores", "source": "network_stock",    "item": "rutf"},
  {"alias": "worker", "source": "worker_stock_get", "item": "rutf", "load": "on_demand"}
]
```

- **`source`** is one of the supply chain's READ operations, listed in `supply_sources.SOURCES` (worker stock, a worker's timeline, network stock, network tree, the flow, distributions, orders, shipments, tenders, suppliers, checks). A workflow never writes supply data through a source; supply writes become workflow actions (§14).
- **`item`** is a commodity slug or SKU, resolved per programme (ids differ between programmes). **`params`** are fixed settings the source accepts (`window_days` for the stock sources: how many recent days a pace is averaged over).
- **Scope** follows the workflow exactly as pipelines do: every opportunity in `opportunity_ids` (or the primary), across programmes if the list spans them. Each opportunity is resolved to its programme from the viewer's org data, and every call runs **as the viewer** through `SupplyDataAccess` — the same access rule as the supply pages. A source scoped to the opportunity (worker stock) runs once per opportunity; one scoped to the programme (stores, orders) runs once per programme, so a store is never counted twice.

**Render code** receives `supply.<alias>` (and `view.supply.<alias>`, which a completed run reads from its snapshot):

- `rows` — tagged with `opportunity_id` (a programme-wide row keeps its own, or `null` for a store) and `program_id`.
- `rollup` — computed on the server: counts summed, rates recomputed (worker stock: `workers`, `by_unit[unit].{on_hand, issued, dispensed, unapproved, estimated, unapproved_share}`, `runway` counts by band, `never_counted`, `no_answer_visits`).
- `metadata.per_opp[String(oppId)]` — `{row_count, program_id}` or `{error}`; one opportunity failing never blanks the rest.

`load: "on_demand"` sources are not loaded with the page; ask for them with `actions.querySupply(alias, {args, opportunity_id, as_of})`, e.g. `actions.querySupply('worker', {args: {supply_point_id: 7}, opportunity_id: 10113})`. `args` are the source's per-call arguments (`supply_point_id` for `worker_stock_get`); anything else is refused.

**Saved runs** freeze the aliases `snapshot_inputs.supply` names, loaded as of the run's `period_end`, under `snapshot.supply`. (A completion over the MCP has no viewer request and freezes no supply.)

**Editing without a deploy:** `workflow_update_definition` takes `supply_sources` (checked against `SOURCES`) and `snapshot_inputs.supply`.

**Reference template:** `supply_stock_review` — stock in field workers' hands across the workflow's opportunities: headline, runway (soonest out first), does it add up, a worker's day-by-day history on demand, and the stores behind them.

### Showing a workflow inside the supply pages

A programme's supply navigation can carry a workflow as a tab (`supply_chain/workflow_views/`):
`supply_chain_view_pin` with `{workflow_definition_id, label, replaces?, opportunity_id?}`
either stands in for a built-in tab (`replaces: "supply_chain:workers"`) or adds one.
The tab opens `/supply/views/<slug>/`: the same runner, inside the supply header, on the
workflow's newest open run (one is started for today if there is none), read as the viewer.
The page links to the workflow's editor and, when the pin replaced a tab, to that tab's own
page. A pin is a pointer: editing the workflow changes the tab. `supply_chain_view_unpin`
restores the built-in tab. The supply header's date (`?as_of=`) reaches the workflow on its
supply endpoints, so every source that can read a past day reads that one; the run itself,
and anything the workflow reads besides supply sources, stays live.
