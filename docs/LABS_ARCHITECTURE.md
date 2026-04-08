# Labs Architecture

Most production apps have been removed from this codebase. Only the 7 labs apps plus minimal retained apps (for models/migrations) remain. See [CLAUDE.md](../CLAUDE.md) for the full app map.

## System Overview

```
Browser
  │
  ▼
Labs Django (labs.connect.dimagi.com / localhost:8000)
  │
  ├── OAuth session auth ──────────► Connect Production (connect.dimagi.com)
  │                                    ├── /o/authorize/ (OAuth flow)
  │                                    ├── /export/labs_record/ (CRUD API)
  │                                    └── /export/opportunity/<id>/... (v2 paginated JSON)
  │
  ├── CommCare HQ OAuth ───────────► CommCare HQ (commcarehq.org)
  │                                    └── Case API v2 (coverage app only)
  │
  ├── OCS OAuth ───────────────────► Open Chat Studio (openchatstudio.com)
  │                                    └── Bot API (tasks + workflow apps)
  │
  ├── Celery tasks ────────────────► Redis (localhost:6379)
  │
  └── Local cache only ────────────► PostgreSQL (localhost:5432)
                                       └── RawVisitCache, ComputedVisitCache, ComputedFLWCache
```

**Labs never writes domain data to the local database.** All persistent data lives in production via the LabsRecord API. The local PostgreSQL is used only for Django sessions, the analysis cache, and admin boundary GIS data.

## Data Flow: LabsRecord CRUD

This is the primary data flow for audit, tasks, workflow, and solicitations.

```
1. User action in browser (e.g., create audit session)
       │
       ▼
2. Django view receives request
   ├── request.user          → LabsUser (transient, from session OAuth)
   ├── request.labs_context  → {opportunity_id, program_id, organization_id}
   └── request.session["labs_oauth"]["access_token"] → OAuth Bearer token
       │
       ▼
3. View creates AppDataAccess(request=request)
   └── DataAccess extracts token + context from request
       │
       ▼
4. DataAccess calls LabsRecordAPIClient methods
   ├── client.create_record(experiment="audit", type="AuditSession", data={...})
   ├── client.get_records(experiment="audit", type="AuditSession")
   ├── client.update_record(record_id=123, data={...})
   └── client.delete_record(record_id=123)
       │
       ▼
5. LabsRecordAPIClient makes HTTP request to production
   └── POST/GET/DELETE https://connect.dimagi.com/export/labs_record/
       │
       ▼
6. Production validates OAuth token (scope: "export")
   └── Returns JSON response
       │
       ▼
7. Client deserializes to LocalLabsRecord (or proxy model subclass)
   └── View renders template with list of proxy model instances
```

## Data Flow: Visit Data Analysis

Used by audit (visit filtering) and workflow (pipeline data).

```
1. View requests analysis data (e.g., FLW visit metrics)
       │
       ▼
2. AnalysisPipeline(request) initialized
   └── SQL backend (PostgreSQL)
       │
       ▼
3. Pipeline fetches data from source
   ├── connect_json: GET /export/opportunity/<id>/user_visits/ (v2 paginated JSON)
   └── cchq_forms: CommCare HQ Form API (via CommCareDataAccess)
       │
       ▼
4. SQL backend caches raw data
   └── RawVisitCache table in local PostgreSQL
       │
       ▼
5. SQL backend applies computations (field computations, histograms, aggregations)
       │
       ▼
6. SQL backend caches computed results
   └── ComputedVisitCache / ComputedFLWCache tables
       │
       ▼
7. Returns result objects (FLWRow, VisitRow with custom_fields)
```

## Cross-App Dependency Matrix

| App | Depends on | Used by | Shared via |
|-----|-----------|---------|------------|
| `labs/` | (none — foundation) | All apps | `LabsRecordAPIClient`, `LocalLabsRecord`, middleware, `AnalysisPipeline` |
| `audit/` | `labs/` | `workflow/`, `tasks/` (ref only) | `AuditDataAccess`, `AuditSessionRecord` |
| `tasks/` | `labs/` | `workflow/` | `TaskDataAccess`, `TaskRecord` |
| `workflow/` | `labs/`, `audit/`, `tasks/` | `ai/` | `WorkflowDataAccess`, `PipelineDataAccess` |
| `ai/` | `workflow/`, `solicitations/` | (called from UI) | Agent functions via SSE |
| `solicitations/` | `labs/` | `ai/` | `SolicitationsDataAccess` |
| `coverage/` | CommCare HQ OAuth | (standalone) | `CoverageDataAccess` |

**Import direction:**
- Workflow imports `AuditDataAccess` and `TaskDataAccess` to create audits/tasks from workflow actions
- AI agents import `WorkflowDataAccess` and `SolicitationsDataAccess` to modify data via tool calls
- Tasks store `audit_session_id` as a reference but don't import audit code
- Coverage is fully standalone (uses CommCare HQ, not Connect production)

## LabsRecord Data Model

Every record stored via the API has these fields:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | int | Auto-incrementing, assigned by production |
| `experiment` | str | App namespace: `"audit"`, `"tasks"`, `"workflow"`, `"pipeline"`, `"solicitations"` |
| `type` | str | Record type within experiment: `"AuditSession"`, `"Task"`, `"workflow_definition"`, etc. |
| `data` | JSON | All domain-specific fields (the actual content) |
| `username` | str | Primary user identifier (NOT user_id) |
| `opportunity_id` | int | Scoping: which opportunity this belongs to |
| `organization_id` | int | Scoping: which organization |
| `program_id` | int | Scoping: which program |
| `labs_record_id` | int | Parent reference (e.g., response → solicitation, run → definition) |
| `public` | bool | When True, record is queryable without scope matching |

**Proxy models** add typed `@property` accessors over the `data` JSON field. For example, `TaskRecord.status` returns `self.data.get("status")`.

## Frontend Architecture

Labs uses three frontend approaches depending on complexity:

| Approach | When to use | Examples |
|----------|-------------|---------|
| **React/TypeScript** | Complex interactive components | Workflow runner, workflow editor (@xyflow/react), AI chat |
| **Alpine.js** | Lightweight interactivity on Django templates | Toggle buttons, dropdowns, inline editing |
| **HTMX** | Progressive enhancement, partial page updates | Form submissions, live search, table filtering |

**Build system:** Webpack 5 with TypeScript transpilation
- `npm run dev` — development build
- `npm run dev-watch` — watch mode (rebuilds on file change)
- `npm run build` — production build

**Styling:** TailwindCSS v4 with PostCSS. Utility-first CSS classes.

**SSE streaming** is used extensively for long-running operations:
- Audit creation progress
- AI agent responses
- Pipeline data loading
- Coverage map GeoJSON loading

## Decision Tree: Building a New Feature

```
What data do you need?
│
├── Domain data (records you create/manage)
│   └── Use LabsRecordAPIClient via data_access.py
│       ├── Define proxy model (subclass LocalLabsRecord)
│       ├── Create DataAccess class wrapping LabsRecordAPIClient
│       └── Use experiment="your_app", type="YourType"
│
├── Production visit/user data (for analysis)
│   └── Use AnalysisPipeline
│       └── It handles fetching, caching, and computation transparently
│
├── Production opportunity/organization metadata
│   └── Direct HTTP: GET /export/opp_org_program_list/
│       └── Or GET /export/opportunity/<id>/ for details
│
├── CommCare HQ case data
│   └── CommCare HQ OAuth + Case API v2 (see coverage/ app)
│
├── AI agent integration
│   └── Add agent in ai/agents/, register tools, use AIStreamView for SSE
│
└── Async processing (long-running tasks)
    └── Celery task in {app}/tasks.py, SSE streaming for progress updates
```

## Related Documentation

- [LABS_GUIDE.md](../commcare_connect/labs/LABS_GUIDE.md) — OAuth setup, API client patterns, proxy model patterns
- [AGENTS.md](../.claude/AGENTS.md) — Per-app details and common mistakes
- [Workflow Templates SKILL](../.claude/skills/workflow-templates/SKILL.md) — Workflow template creation patterns
