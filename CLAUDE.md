# CommCare Connect Labs

This is a **labs/rapid prototyping environment** for CommCare Connect. It operates entirely via API against the production CommCare Connect instance — there is no direct database access to production data.

Most production apps have been removed from this codebase. The remaining non-labs apps (`opportunity`, `users`, `organization`, `program`) are kept only for their Django models and migrations (needed by foreign key references). Their tables are empty in this environment — do not query them expecting production data.

## Architecture at a Glance

- **OAuth + Django User** — OAuth login via production Connect creates/updates a Django User via `User.objects.update_or_create()`. OAuth tokens stored in `request.session["labs_oauth"]` for API calls. Org data (organizations, programs, opportunities) available via `get_org_data(request)` from `labs/context.py`, and in templates via `user_organizations`, `user_programs`, `user_opportunities` context variables.
- **All data via API** — `LabsRecordAPIClient` (`commcare_connect/labs/integrations/connect/api_client.py`) makes HTTP calls to `/export/labs_record/` on production for all CRUD. The production data export API code lives in the **`dimagi/commcare-connect`** repo at `commcare_connect/data_export/` (views, serializers, URLs). Use `gh api repos/dimagi/commcare-connect/contents/commcare_connect/data_export/views.py` to read it. **Note:** The CSV export serializes Python dicts with `str()`, producing Python repr format (single quotes), not JSON — use `ast.literal_eval` as fallback when parsing.
- **data_access.py pattern** — each app wraps `LabsRecordAPIClient` in a `data_access.py` class with domain-specific methods.
- **Proxy models** — `LocalLabsRecord` subclasses provide typed `@property` access to JSON data. They cannot be `.save()`d locally.
- **Context middleware** — `request.labs_context` provides `opportunity_id`, `program_id`, `organization_id` on every request.

## App Map

### Labs Apps (Active Development)

| App              | Purpose                                                               | Key files                                                                        |
| ---------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `labs/`          | Core infrastructure: OAuth, API client, middleware, analysis pipeline | `integrations/connect/api_client.py`, `models.py`, `context.py` |
| `audit/`         | Quality assurance review of FLW visits, HQ image questions            | `data_access.py`, `ai_review.py`, `tasks.py`, `hq_app_utils.py`, `views.py`     |
| `tasks/`         | Task management for FLW follow-ups                                    | `data_access.py` (simplest example of the pattern)                               |
| `workflow/`      | Configurable workflow engine with React UIs and pipelines             | `data_access.py` (most complex), `templates/`                                    |
| `ai/`            | AI agent integration via pydantic-ai, SSE streaming                   | `agents/`, `views.py` (AIStreamView)                                             |
| `solicitations/` | RFP management (scoped by program, not opportunity)                   | `data_access.py`, `models.py`                                                    |
| `solicitations_new/` | Next-gen solicitations with API views, forms, and MCP tools       | `data_access.py`, `api_views.py`, `mcp_tools.py`, `forms.py`                    |
| `coverage/`      | Delivery unit mapping from CommCare HQ (separate OAuth)               | `data_access.py`, `data_loader.py`                                               |
| `custom_analysis/` | Program-specific analysis dashboards (audit_of_audits, chc_nutrition, kmc, mbw, rutf) | Each sub-app has `data_access.py`, `views.py`, `urls.py`     |

### Retained Non-Labs Apps (Models + Migrations Only)

| App              | Purpose                                                               |
| ---------------- | --------------------------------------------------------------------- |
| `opportunity/`   | ORM models and migrations only — needed by FK references. No views, no business logic. |
| `users/`         | User model definitions and migrations                                 |
| `organization/`  | Organization model definitions and migrations                         |
| `program/`       | Program model definitions and migrations                              |
| `commcarehq/`    | Minimal — just `HQServer` model + migrations (needed by FKs)          |

**Cross-app connections:** Workflow can create audits and tasks. AI agents modify workflows and solicitations. `custom_analysis/audit_of_audits` reads audit and organization data. Coverage is standalone.

## Workflow Engine

Templates are single Python files in `workflow/templates/` exporting DEFINITION (statuses, config), RENDER_CODE (React JSX string transpiled by Babel), and optionally PIPELINE_SCHEMAS (CommCare form field extraction). The registry auto-discovers them. Pipeline schemas map CommCare form JSON paths to extracted fields with aggregations and transforms. Render code receives `{definition, instance, workers, pipelines, links, actions, onUpdateState}` as props.

**Existing templates:** `audit_with_ai_review`, `bulk_image_audit`, `kmc_flw_flags`, `kmc_longitudinal`, `kmc_project_metrics`, `mbw_monitoring_v2`, `ocs_outreach`, `performance_review`, `sam_followup`

Use the MCP server's `get_form_json_paths` tool to discover correct field paths when building pipeline schemas.

**Full reference:** [WORKFLOW_REFERENCE.md](commcare_connect/workflow/WORKFLOW_REFERENCE.md)

## Deployment

Labs deploys to **AWS ECS Fargate** via `.github/workflows/deploy-labs.yml`.

- **Docker image:** Built from `Dockerfile`, pushed to ECR (`labs-jj-commcare-connect`)
- **Gunicorn config:** `docker/start` — uses gthread workers, count set via `WEB_CONCURRENCY` env var (default 3)
- **ECS cluster:** `labs-jj-cluster` in `us-east-1`
- **Services:** `labs-jj-web` (web), `labs-jj-worker` (celery)

## Key Commands

```bash
inv up                              # Start docker services (postgres, redis)
npm ci && inv build-js              # Install JS deps and build frontend
inv build-js -w                     # Build with watch mode (rebuilds on change)
python manage.py runserver          # Django dev server (uses config.settings.local)
pytest                              # Run tests
pytest commcare_connect/audit/      # Run tests for one app
celery -A config.celery_app worker -l info   # Celery worker (async audit creation, AI tasks)
pre-commit run --all-files          # Run linters/formatters
```

## Critical Warnings

- **DO NOT** query Django ORM models (`Opportunity`, `User`, `Organization`) expecting production data — those tables are empty. Use `LabsRecordAPIClient`.
- **DO NOT** use `config.settings.labs_aws` for local development. Use `config.settings.local` (the default). The `labs_aws` settings are only for the AWS deployment at `labs.connect.dimagi.com`.
- **DO NOT** call `.save()` on `LocalLabsRecord` — it raises `NotImplementedError`. Use `LabsRecordAPIClient` for persistence.
- **DO NOT** modify models in the retained non-labs apps (`opportunity/`, `organization/`, `program/`, `users/`). They exist only for migrations and FK references.

## CommCare MCP Server

A local MCP server (`tools/commcare_mcp/`) gives Claude access to CommCare application structure for building workflow pipeline schemas.

**Tools:** `get_opportunity_apps`, `list_apps`, `get_app_structure`, `get_form_questions`, `get_form_json_paths`

**Key tool:** `get_form_json_paths` maps form questions to their exact JSON submission paths (e.g., `form.anthropometric.child_weight_visit`) for use in `PIPELINE_SCHEMAS` field definitions.

**Data safety:** The server calls only the CommCare HQ application definition API (`GET /a/{domain}/api/v0.5/application/`) — app schema metadata only. It does **not** access form submissions, case data, user data, or any patient-level information.

**Runs locally** as a stdio subprocess in Claude Code. Config in `.claude/mcp.json`. Auth via CommCare API key (`.env`) and Connect OAuth token (`~/.commcare-connect/token.json`).

**Source:** `server.py` (tool definitions), `hq_client.py` (HQ API), `connect_client.py` (Connect API), `extractors.py` (schema parsing)

## Deeper Documentation

- **[LABS_GUIDE.md](commcare_connect/labs/LABS_GUIDE.md)** — Detailed development patterns: OAuth setup, API client usage, proxy models, CLI scripts
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Code style, testing conventions, PR process, step-by-step guide for adding new features
- **[.claude/AGENTS.md](.claude/AGENTS.md)** — Full architecture reference: per-app details, API endpoints, data access patterns, common mistakes
- **[docs/LABS_ARCHITECTURE.md](docs/LABS_ARCHITECTURE.md)** — Architecture diagrams, data flow, cross-app dependency matrix, decision tree
- **[pr_guidelines.md](pr_guidelines.md)** — Pull request best practices
- **[docs/plans/](docs/plans/)** — Design documents and implementation plans for features built in this environment
