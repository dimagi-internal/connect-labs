# Workflow App

Configurable workflow engine with data-driven definitions, dynamic React UIs (render code), pipeline data extraction, and async job processing. The most complex labs app.

Workflows are defined as JSON definitions with associated React component code (render code) stored as LabsRecords. Pipelines provide data extraction from production CSV streams with configurable field mappings and aggregations.

**Full authoring guide:** [WORKFLOW_REFERENCE.md](WORKFLOW_REFERENCE.md) — template anatomy, pipeline schemas, render code contract, actions API, common patterns, building from external specs.

## Key Files

| File                        | Purpose                                                                                      |
| --------------------------- | -------------------------------------------------------------------------------------------- |
| `data_access.py`            | `WorkflowDataAccess`, `PipelineDataAccess` — both extend `BaseDataAccess`                    |
| `views.py`                  | ~40 views: workflow CRUD, run management, pipeline editing, SSE streaming, sharing           |
| `tasks.py`                  | `run_workflow_job` Celery task with pluggable job handler registry                           |
| `urls.py`                   | URL routing under `/workflow/`                                                               |
| `templates/`                | Workflow template definitions (Python files auto-discovered by registry)                     |
| `templates/mbw_monitoring/` | MBW monitoring dashboard — see [DOCUMENTATION.md](templates/mbw_monitoring/DOCUMENTATION.md) |

## Data Model

**Workflow records** (experiment=`"workflow"`):

| Type                    | Proxy Model                 | Purpose                                                 |
| ----------------------- | --------------------------- | ------------------------------------------------------- |
| `workflow_definition`   | `WorkflowDefinitionRecord`  | Name, statuses, config, template_type, pipeline_sources |
| `workflow_render_code`  | `WorkflowRenderCodeRecord`  | React component JSX (linked to definition)              |
| `workflow_run`          | `WorkflowRunRecord`         | Execution instance with state, period, status           |
| `workflow_chat_history` | `WorkflowChatHistoryRecord` | AI conversation messages                                |

**Pipeline records** (experiment=`"pipeline"`):

| Type                    | Proxy Model                 | Purpose                                    |
| ----------------------- | --------------------------- | ------------------------------------------ |
| `pipeline_definition`   | `PipelineDefinitionRecord`  | Schema with fields, aggregations, grouping |
| `pipeline_render_code`  | `PipelineRenderCodeRecord`  | Visualization component                    |
| `pipeline_chat_history` | `PipelineChatHistoryRecord` | AI conversation messages                   |

## Key Patterns

**Render Code:** React components stored as strings in LabsRecords, rendered dynamically in the workflow runner. Components receive `{definition, instance, workers, pipelines, links, actions, onUpdateState}` as props. See [WORKFLOW_REFERENCE.md](WORKFLOW_REFERENCE.md) for the full prop API.

**Pipeline Execution:** Schemas define field extraction from production CSV data. `PipelineDataAccess.execute_pipeline()` converts schema → `AnalysisPipelineConfig` → runs via `AnalysisPipeline` → returns `{rows, metadata}`.

**State Management:** Workflow runs use PATCH-based state merging (`update_run_state`) to avoid race conditions. State is stored in `run.data.state`.

**Job System:** `run_workflow_job` is a multi-stage Celery task. Job handlers are registered via `@register_job_handler(job_type)` decorator. Current handlers: `scale_validation`, `pipeline_only`.

**Templates:** Python files in `workflow/templates/` export a `TEMPLATE` dict. Auto-discovered by the registry in `templates/__init__.py`.

## Cross-App Connections

- **Depends on:** `labs/` (BaseDataAccess, AnalysisPipeline), `audit/` (creates audits from workflow actions), `tasks/` (creates tasks from workflow actions)
- **Used by:** `ai/` (agents modify definitions and render code)

Audit and task imports are **lazy** (inside functions in views.py) to avoid circular dependencies.

## Testing

```bash
pytest commcare_connect/workflow/
```

Mock `LabsRecordAPIClient`. Pipeline tests may need mock CSV data.
