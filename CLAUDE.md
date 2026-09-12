# Connect Labs

This is a **labs/rapid prototyping environment** for Connect. It operates entirely via API against the production Connect instance — there is no direct database access to production data.

## Always check freshness before starting non-trivial work

This is a fast-moving repo with multiple parallel worktrees and frequent merges to `main`. **Before designing or implementing any non-trivial feature, refactor, or rename — and especially anything touching workflows, runs, pipelines, tasks, or other actively-evolving areas — run:**

```bash
git fetch origin main
git log $(git merge-base HEAD origin/main)..origin/main --oneline
```

If `main` has commits the current branch doesn't, surface them to the user _before_ doing design work. Long-running branches based on a stale `main` produce silent rework — design discussions treat already-shipped code as missing, and merges later collide semantically (not just textually) with parallel work. The cost of a 5-second `git fetch` is much smaller than the cost of redoing a feature on the right base.

When in doubt — especially if the user mentions a recent feature ("we just built X", "the new Y") — assume `main` has moved and verify.

Most production apps have been removed from this codebase. The remaining non-labs apps (`opportunity`, `users`, `organization`, `program`) are kept only for their Django models and migrations (needed by foreign key references). Their tables are empty in this environment — do not query them expecting production data.

## Architecture at a Glance

- **OAuth + Django User** — OAuth login via production Connect creates/updates a Django User via `User.objects.update_or_create()`. OAuth tokens stored in `request.session["labs_oauth"]` for API calls. Org data (organizations, programs, opportunities) available via `get_org_data(request)` from `labs/context.py`, and in templates via `user_organizations`, `user_programs`, `user_opportunities` context variables.
- **All data via API** — `LabsRecordAPIClient` (`connect_labs/labs/integrations/connect/api_client.py`) makes HTTP calls to `/export/labs_record/` on production for all CRUD. See [Production API Reference](#production-api-reference) below for endpoint details. The production code lives in **`dimagi/commcare-connect`** at `commcare_connect/data_export/` (views, serializers, URLs). Use `gh api repos/dimagi/commcare-connect/contents/commcare_connect/data_export/views.py` to read it.
- **data_access.py pattern** — each app wraps `LabsRecordAPIClient` in a `data_access.py` class with domain-specific methods.
- **Proxy models** — `LocalLabsRecord` subclasses provide typed `@property` access to JSON data. They cannot be `.save()`d locally.
- **Context middleware** — `request.labs_context` provides `opportunity_id`, `program_id`, `organization_id` on every request.

## Production API Reference

The Labs Record API on production Connect (`/export/labs_record/`) is the single endpoint for all CRUD operations. Auth uses OAuth Bearer tokens with the `export` scope — this scope covers **both read and write** operations.

### LabsRecord Model (production side)

Fields: `id`, `experiment` (text), `type` (char), `data` (JSONField), `public` (bool), plus FK references to `user`, `organization`, `opportunity`, `program`, `labs_record` (self-referential parent).

### Endpoints

**GET** `/export/labs_record/` — List/filter records. Query params are passed directly to Django ORM `.filter()`:

- `type=solicitation` — filter by record type
- `experiment=<program_id>` — filter by experiment (typically program ID)
- `data__<field>=<value>` — JSONField lookups (e.g., `data__status=active`)
- `program_id=<id>` — scope by program (triggers membership permission check)
- `opportunity_id=<id>` — scope by opportunity (triggers access permission check)
- `organization_id=<id>` — scope by organization (triggers membership check)
- If none of the above scope params are provided, returns only `public=True` records

**POST** `/export/labs_record/` — Create or upsert records. Body is a JSON **list** of record objects:

```json
[{"experiment": "25", "type": "solicitation", "data": {...}, "program_id": 25, "public": true}]
```

Each item in the list can include `program_id`, `opportunity_id`, or `organization_id` to scope the write (each triggers a membership/access permission check). Include `id` to upsert an existing record. Include `username` to associate with a user.

**DELETE** `/export/labs_record/` — Delete records. Body is a JSON list with `id` fields:

```json
[{ "id": 123 }, { "id": 456 }]
```

### Permission Model

- **OAuth scope:** `export` — single scope for all read AND write operations
- **GET permissions:** If `program_id`, `opportunity_id`, or `organization_id` query param is present, the API checks the token's user has membership/access to that entity. Without these params, only `public=True` records are returned.
- **POST/DELETE permissions:** Each record in the payload is checked — any `program_id`, `opportunity_id`, or `organization_id` must belong to an entity the user has membership in. A 404 is returned if the user lacks access.
- **Common 404 cause:** Sending `program_id` in query params (GET) or payload (POST) when the authenticated user is not a member of the organization that owns that program.

### Synthetic / labs-only opportunities — DO NOT use the prod API or permissions

**This API is on production Connect (`connect.dimagi.com`), and it is NOT how you read or write a synthetic opp.** Two traps that produce a misleading `404`:

1. **Wrong host.** The labs*record API lives on `connect.dimagi.com`. Labs is a \_client* of it. Hitting `labs.connect.dimagi.com/export/labs_record/` 404s — that path isn't served on the labs host.
2. **Synthetic opps never touch this API at all.** An opportunity with `id ≥ 10_000` registered as a `SyntheticOpportunity` (`labs_only=True`) is dispatched **in-process** to `labs/synthetic/local_records_backend.py`, which does plain `LabsLocalRecord` Django ORM CRUD in the labs DB. **There are NO permission checks and NO HTTP** — `LabsRecordAPIClient` short-circuits to the local backend via `is_labs_only_opportunity_id()`. So a user-token + `curl` against the prod API is the wrong mechanism _and_ gives a permission-looking 404.

**To create/seed/iterate a synthetic opp's records (workflow definitions, runs, audits, tasks):** run **server-side, inside the labs app** — use the `connect_labs` MCP tools (they execute in-app → hit the local backend), the synthetic recipe/seeder (`scripts/walkthroughs/<demo>/`, `labs/synthetic/walkthrough_kit.py`), or a management command. **Never raw-HTTP a synthetic opp with a user token.** Permissions simply do not apply to labs-only opps — that's the whole point of the `LABS_ONLY_OPP_ID_FLOOR` (10,000) namespace. (Separately, GDrive-backed fixtures serve the `/export/...` _visit_ endpoints for synthetic opps — see `docs/SYNTHETIC_OPPS.md`; that path is for prod-export-shaped data, not LabsRecords.)

### Record Type Conventions

| App            | experiment       | type                    | Notes                 |
| -------------- | ---------------- | ----------------------- | --------------------- |
| Solicitations  | `program_id`     | `solicitation`          | Scoped by program     |
| Sol. Responses | `llo_entity_id`  | `solicitation_response` | Scoped by entity      |
| Sol. Reviews   | `llo_entity_id`  | `solicitation_review`   | Scoped by entity      |
| Audits         | `opportunity_id` | varies                  | Scoped by opportunity |
| Workflows      | `opportunity_id` | varies                  | Scoped by opportunity |

## App Map

### Labs Apps (Active Development)

| App                | Purpose                                                                                                                                                                                                                                | Key files                                                                           |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `labs/`            | Core infrastructure: OAuth, API client, middleware, analysis pipeline                                                                                                                                                                  | `integrations/connect/api_client.py`, `models.py`, `context.py`                     |
| `audit_trail/`     | HIPAA-bar access/change logging: append-only `AuditEvent`, choke-point instrumentation, review UI at `/labs/audit-trail/`, S3 Object-Lock archive. See `docs/AUDIT_LOGGING.md`                                                         | `service.py`, `middleware.py`, `tasks.py`, `views.py`                               |
| `audit/`           | Quality assurance review of FLW visits, HQ image questions, AI reviewers, visit clustering                                                                                                                                             | `data_access.py`, `ai_review.py`, `ai_review_config.py`, `visit_clustering.py`, `tasks.py`, `views.py` |
| `tasks/`           | Task management for FLW follow-ups                                                                                                                                                                                                     | `data_access.py` (simplest example of the pattern)                                  |
| `workflow/`        | Configurable workflow engine with React UIs and pipelines                                                                                                                                                                              | `data_access.py` (most complex), `templates/`                                       |
| `ai/`              | AI agent integration via pydantic-ai, SSE streaming                                                                                                                                                                                    | `agents/`, `views.py` (AIStreamView)                                                |
| `solicitations/`   | Solicitations with API views, forms, and MCP tools                                                                                                                                                                                     | `data_access.py`, `api_views.py`, `mcp_tools.py`, `forms.py`                        |
| `coverage/`        | Delivery unit mapping from CommCare HQ (separate OAuth)                                                                                                                                                                                | `data_access.py`, `data_loader.py`                                                  |
| `custom_analysis/` | Program-specific analysis dashboards (audit_of_audits, chc_nutrition, exports, kmc, rutf)                                                                                                                                              | Each sub-app has `data_access.py`, `views.py`, `urls.py`                            |
| `microplans/`      | Microplanning at `/microplans/`: sampling + coverage planning on admin boundaries/work areas, program-scoped plans, study groups, KPIs, Connect work-area export. See `docs/microplans-design.md` (historical north-star)              | `core/`, `sampling/`, `coverage/`, `monitoring/`, `qc/`, `models.py`, `views.py`    |
| `funder_dashboard/`| Funder-facing dashboard at `/funder/`: funds, allocations, charts/KPIs, AI fund-report agents                                                                                                                                          | `data_access.py`, `views.py`, `api_views.py`, `mcp_tools.py`                        |
| `campaign/`        | Standalone Campaign Utility Tool at `/campaign/` — has its **own CommCare OAuth** (session key `campaign_oauth`), not the labs session                                                                                                 | `api/`, `services/`, `auth/`, `middleware.py`                                       |
| `supply/`          | The OES (Operation End Starvation) demo satellite site, now at `/oes/` (moved off `/supply/` to free that address for `supply_chain/`) — second satellite site, own auth, zero labs imports. **Slated for retirement.** **Read [connect_labs/supply/README.md](connect_labs/supply/README.md) before touching it.**                     | see that README                                                                     |
| `supply_chain/`    | Core-labs supply domain at `/supply/` — sourcing, fulfilment, stock and distribution end to end. **The labs DB is its system of record** (real tables, real FKs, SQL aggregation for the stock ledger), unlike every other labs app: supply data originates here, carries no PII, and needs relational work. Sync back to Connect is deliberately deferred. A field worker is a supply point, so distributing to one reuses the ledger. See `docs/superpowers/specs/2026-09-11-rutf-procurement-design.md` (Part 2 covers buyer-of-record, provenance and stock). | `models.py`, `data_access.py`, `operations.py`, `stock/services/`, `scopes.py` |
| `pages/`           | Composable card landing-page "surfaces" at `/labs/p/<slug>`, authored via the `pages_*` MCP tools                                                                                                                                      | `data_access.py`, `providers/`, `views.py`                                          |
| `flags/`           | Flag-type `LocalLabsRecord`s — findings observed on FLWs during workflow runs; API mounted at `/labs/workflow/api/run/<id>/flags/`                                                                                                     | `models.py`, `data_access.py`                                                       |
| `mcp/`             | The labs remote MCP server (see [MCP Servers](#mcp-servers)): PAT auth, FastMCP ASGI app at `/mcp/`, tool registry + audit log                                                                                                          | `server.py`, `tool_registry.py`, `tools/`, `auth.py`                                |
| `pulse/`           | Funder-facing service-delivery telemetry at `/labs/pulse/` — wall display, donor reports, and the partner network at `/labs/pulse/network/`. Polls Connect's export API on a beat. See [Connect Pulse](#connect-pulse)                        | `ingest.py`, `api.py`, `network_api.py`, `partner_names.py`, `hq_location.py`       |
| `labs/synthetic/`  | Registry of "synthetic" opportunities that serve fixture JSON from GDrive instead of prod exports. CRUD UI at `/labs/synthetic/`, SSE-streamed dump flow, strict access scoping by `user_opportunities`. See `docs/SYNTHETIC_OPPS.md`. | `models.py`, `registry.py`, `fixture_store.py`, `gdrive.py`, `dump.py`, `client.py` |
| `mopup/`           | CHC mop-up at `/mopup/` — program-scoped runs that pick an opportunity/wards/dates, pull visits once, then re-evaluate cluster-aware coverage candidates against tunable thresholds. Hands off by calling microplans' own `create_plan()` and redirecting into its unmodified review page. Run state is a `LocalLabsRecord`, not a Django model.                                     | `core/` (`candidates.py`, `indicators.py`, `geometry.py`, `gaps.py`, `handoff.py`), `tasks.py`, `views.py` |
| `semantic/`        | SQL semantic layer — the indicator engine behind the KMC programme dashboard. YAML registry compiled to SQL and run in-process over the visit cache; the browser's JS indicator engine was **deleted**, so this is now the only source for the C/N series. Served at `workflow/api/<id>/semantic/`. See `connect_labs/semantic/PARITY.md`.                                          | `registry/kmc/*.yml`, `compiler.py`, `runtime.py`, `snapshot.py`, `gates.py`, `workflow_binding.py`, `validation.py` |
| `labs/indicators/` | Targeting at `/labs/targeting/` — population/burden primitives for deciding where to deploy: thresholded selection across Africa, reach + cost sizing, and the methodology behind every figure. **Counts sum up the hierarchy; rates must never be summed** — see its README before touching aggregation.                                                                            | `measures.py`, `resolve.py`, `boundaries.py`, `methods.py`, `defence.py`, `export.py`, `README.md` |

### Retained Non-Labs Apps (Models + Migrations Only)

| App             | Purpose                                                                                |
| --------------- | -------------------------------------------------------------------------------------- |
| `opportunity/`  | ORM models and migrations only — needed by FK references. No views, no business logic. |
| `users/`        | User model definitions and migrations                                                  |
| `organization/` | Organization model definitions and migrations                                          |
| `program/`      | Program model definitions and migrations                                               |
| `commcarehq/`   | Minimal — just `HQServer` model + migrations (needed by FKs)                           |

**Cross-app connections:** Workflow can create audits and tasks; flags hang off workflow runs. AI agents modify workflows and solicitations; funder-dashboard AI agents live in `ai/agents/`. `custom_analysis/audit_of_audits` reads audit and organization data. `mopup` reads CommCare/Connect data and writes into `microplans`; `semantic` is read by `workflow` — the live dashboard endpoint and the `semantic_snapshot` builder that freezes a saved run. Coverage and campaign are standalone.

## Prelogin marketing site

The public marketing site at `/` is the `prelogin` app. **This repo (labs) is its source of truth and staging environment** — edit `connect_labs/templates/prelogin/home.html`, `static/prelogin/`, and `prelogin/urls.py` directly (Django-native; no code-generation or import step), preview on `labs.connect.dimagi.com`, then promote to production (`dimagi/commcare-connect`). The old `dimagi-internal/connect-prelogin` upstream + `export-to-django.py` pipeline is **deprecated**.

To promote, the trigger is **"create a PR to push the prelogin changes to connect prod"** — copy the three `prelogin` dirs (`prelogin/`, `templates/prelogin/`, `static/prelogin/`) labs→prod and open a PR; leave each repo's `config/urls.py`/robots policy alone (labs stays `Disallow: /`, prod stays indexable). Full details: **[docs/prelogin-marketing-site.md](docs/prelogin-marketing-site.md)**.

## Workflow Engine

Templates are single Python files in `workflow/templates/` exporting DEFINITION (statuses, config), RENDER_CODE (React JSX string transpiled by Babel), and optionally PIPELINE_SCHEMAS (CommCare form field extraction). The registry auto-discovers them. Pipeline schemas map CommCare form JSON paths to extracted fields with aggregations and transforms. Render code receives `{definition, instance, workers, pipelines, links, actions, onUpdateState, view}` as props.

Templates can set `multi_opp: True` on their `TEMPLATE` dict to opt into multi-opportunity support. Multi-opp workflows store an `opportunity_ids` list on the definition, merge workers and pipeline rows across those opps at runtime, and tag every row/worker with its source `opportunity_id`. Single-opp workflows (default) are unchanged — they fall back to `[primary_opp_id]` with the same tagging shape. See [WORKFLOW_REFERENCE.md §8](connect_labs/workflow/WORKFLOW_REFERENCE.md#8-multi-opportunity-workflows) for the full contract.

Templates that produce a periodic review with a definite "moment of completion" can set `supports_saved_runs: True` to opt into the **in_progress | completed** run lifecycle. They declare what the snapshot captures via `snapshot_inputs` (a manifest of pipelines/workers/state_keys), render code reads run data via the `view` helper (`view.workers`, `view.pipelines.<alias>`, `view.state.<key>`, `view.isCompleted`, `view.asOf`), and triggers completion via `view.complete({confirm})`. The framework atomically builds the snapshot, flips status, stamps `completed_at`, and write-protects the run. Reference: `connect_labs/workflow/templates/performance_review.py`. Full contract: [WORKFLOW_REFERENCE.md §9](connect_labs/workflow/WORKFLOW_REFERENCE.md#9-saved-runs-templates).

A saved-runs template can be completed **server-side, with no browser**. Prefer the declarative route: `snapshot_inputs.builder` names a framework builder from `connect_labs/workflow/snapshot_builders.py` (today: `semantic_snapshot`) and the rest of the manifest is that builder's spec. Because the manifest rides on the *definition*, a computed snapshot is patchable via `workflow_update_definition` with no deploy — see `SNAPSHOT_INPUTS` in `connect_labs/workflow/templates/kmc_programme_metrics.py` for a worked example. The older per-template `build_snapshot` Python hook remains as an escape hatch (WORKFLOW_REFERENCE.md, "Escape hatch"), but writing one means a PR + deploy for every change; if you reach for it to grade a semantic registry, extend the builder spec instead. Either way the framework refuses to freeze an unstaged snapshot rather than freezing an empty one.

**Existing templates** (27; `*` = multi-opp): `audit_par`\*, `audit_with_ai_review`, `bulk_image_audit`, `chc_audit_history`\*, `chc_nutrition_analysis`, `flw_audit_trend_dashboard`\*, `flw_daily_indicator_report`\*, `flw_daily_indicator_table`\*, `flw_daily_summary_report`\*, `flw_weekly_audit_report`\*, `interviews_reporting_v2`, `jakusko_chlorine_dispenser`, `kmc_flw_flags`, `kmc_image_audit`\*, `kmc_longitudinal`, `kmc_programme_metrics`\*, `kmc_project_metrics`, `llo_weekly_review`, `mbw_auditing_v5`, `muac_picture_audit`\*, `ocs_outreach`, `performance_review`\*, `program_admin_report`\*, `program_audit_creator`\*, `sam_followup`, `verified_monitoring`, `weekly_dual_track_audit`\*

**Legacy — do NOT use as patterns:** the `mbw_monitoring` package (MBW v1: Python job handler + SSE + in-template React) is **deprecated** and retained only to keep a few pre-existing prod instances renderable. It's flagged `TEMPLATE["deprecated"] = True`, so it's hidden from `list_templates()` / the create menu and can't be instantiated anew (see `connect_labs/workflow/templates/mbw_monitoring/DEPRECATED.md`). The v2/v3 monitoring and v4 auditing templates were already removed. For any MBW or dashboard work, copy from **`mbw_auditing_v5`** (SQL-native, pipeline-pure, saved-runs) — never from `mbw_monitoring`.

Use the MCP server's `get_form_json_paths` tool to discover correct field paths when building pipeline schemas.

**Full reference:** [WORKFLOW_REFERENCE.md](connect_labs/workflow/WORKFLOW_REFERENCE.md)

## Connect Pulse

Delivery telemetry at `/labs/pulse/`, polled from Connect's export API. Four
things about it are expensive to rediscover.

**Partner identity lives in the LLO Directory, not this repo.** Connect publishes
partner *names* only for the orgs the polling account belongs to — a minority of
those that deliver. The rest arrive as a slug and are matched against the team's
directory sheet, loaded into `PulsePartner` by `pulse_partner_import` (daily on
beat, and runnable by hand). No partner name is written down in source; if the
board shows slugs instead of names, that import has not run.

Slugs no string rule can reach are resolved by a human on the directory's
"Connect Org Mapping" tab and carried as `PulsePartnerAlias`. `partner_names.py`
deliberately refuses to guess — a wrong parent name is worse than a visible slug.

**Two traps when dating anything from the spine.** `completed_works` cannot date
anything before **2025-01-14**: Connect bulk-created 81k rows at one instant that
day, so reading `created_ts` naively collapses every partner already active onto
a single date. And `field_ts` comes off a handset — one partner's earliest visit
claims 2010 — so the server-assigned `sync_ts` is what to trust, with a sanity
floor under it. `network_api.py` has both guards and tests pinning them.

**Partner identity is entitled, and fails closed.** The read API is otherwise
unauthenticated, so `_partner_names_allowed` requires a labs session or a token
minted to permit names. Any new endpoint carrying partner identity has to gate
the same way.

**The drill-down window is shared.** `PulseWindows.configure({urlFor, labels})`
declares its whole dependency; both the wall display and the network page open
it. Every render path sits inside a catch that reports "Could not load this
partner", so a missing binding ships looking like a data problem — see
`connect_labs/static/pulse/windows.test.js`.

## Deployment

Labs deploys to **AWS ECS Fargate** via `.github/workflows/deploy-labs.yml`.

- **Docker image:** Built from `Dockerfile`, pushed to ECR (`labs-jj-commcare-connect`)
- **Gunicorn config:** `docker/start` — serves the ASGI app under `config.uvicorn_worker.LabsUvicornWorker` (NOT gthread; the FastMCP server needs ASGI + lifespan), worker count via `WEB_CONCURRENCY` (default 3). Note the shape that shows up in CPU investigations: 3 worker processes against the task's **1 vCPU** (`deploy/task-definitions/web.json`, `cpu: 1024`)
- **ECS cluster:** `labs-jj-cluster` in `us-east-1`
- **Services:** `labs-jj-web` (web), `labs-jj-worker` (celery)
- **Concurrency valve:** `WEB_LIMIT_CONCURRENCY` (unset = off) bounds uvicorn's in-flight requests via `config/uvicorn_worker.py`. It exists because the web tier had no bound and overload exhausted RDS connection slots (#1152) — read that file before setting it
- **Infrastructure-as-code:** `infra/` holds CloudFormation stacks — `labs-monitoring.yml` (SNS + RDS/CPU/worker-kill alarms), `labs-access-logs.yml`, `labs-audit-analytics.yml`, `labs-email.yml`. Core RDS/ECS is still click-ops; import into a stack as needed. See `infra/README.md`
- **Env vars are wiped on deploy** unless pinned in `deploy/task-definitions/*.json` (see `deploy/task-definitions/README.md`)

**Deploy only from `main`.** The workflow has a hard `guard` job that refuses any `--ref` other than `refs/heads/main`. Land changes via PR + merge first, then trigger the deploy with `--ref main`. Branch deploys are not allowed: they make "what's on prod?" ambiguous and let unreviewed code into the labs environment.

```bash
# canonical deploy command — only main is accepted
gh workflow run deploy-labs.yml --repo dimagi-internal/connect-labs --ref main --field run_migrations=false
```

(Historical note: this repo used to be a fork of `dimagi/commcare-connect` whose labs branch was `labs-main`. That label is dead — the repo is its own thing now and `main` is the default branch. Any older doc that says `labs-main` now means `main`.)

## Pull Requests

Before creating any pull request, read `.github/PULL_REQUEST_TEMPLATE.md` and follow its structure exactly. Key sections:

- **`## Product Description`** — plain English, written for non-developer program staff. Describe what users will notice or be able to do differently. Leave blank only for pure infra/refactor changes with zero user-visible effect.
- **`## Technical Summary`** — links to tickets, design decisions, rationale.
- **`## Safety Assurance`** — how you tested it, what automated coverage exists, QA plan.

The `## Product Description` section drives automated documentation updates and the weekly changelog. PRs that skip it or use a different section name (e.g. `## Summary`) are invisible to that automation.

### `main` merges directly — the merge queue is OFF (since 2026-09-10)

`main` is protected by a repo ruleset: no direct pushes, no deletion, no non-fast-forward, `linter` + `pytest` required on the PR, **squash merges only** (the repository allows no other method). There is **no merge queue**: it re-ran the whole suite against the queued merge result and cost a median 4.3 minutes per merge, nearly always to confirm that two independently green PRs were also green together. Jonathan took that trade for iteration speed. Consequences:

- `gh pr merge <N> --squash` (or with no flag — squash is the only allowed method) **lands the commit immediately** once the PR's own checks are green. The branch does not have to be up to date with `main`.
- **`main` is tested after the fact.** CI runs the full suite on every push to `main`. If a merge that was green on its PR goes red on `main` (a semantic conflict with something that merged in between), that push run is the signal, within ~3 minutes. **Fix forward**: a follow-up PR, not a revert-and-wait.
- Confirm a merge actually landed before deploying: `git fetch origin main && git log origin/main --oneline -5`. "Deploy only from `main`" (above) depends on this.
- `ci.yml` keeps its `merge_group` trigger, inert, so the queue can be switched back on without the deadlock its comment describes.

## Git Worktrees and Virtualenv

This repo uses emdash which manages git worktrees. In a worktree, the virtualenv
lives in the **main repo** (commonly `~/emdash-projects/connect-labs/.venv`, but
the parent directory varies by machine — `~/emdash/repositories/connect-labs` is
also in use), NOT in the worktree directory. Pre-commit hooks will fail if the
virtualenv is not on PATH.

**Use the Makefile targets — they locate the main checkout by asking git
(`--git-common-dir`), so they work from any worktree on any layout:**

```bash
make commit                          # git commit with the venv on PATH
make test                            # pytest, with everything a worktree lacks
make test ARGS="connect_labs/audit -q"
```

Or do it by hand, substituting your own main-checkout path:

```bash
# Option 1: activate the main repo's venv
. ~/emdash-projects/connect-labs/.venv/bin/activate

# Option 2: prepend PATH inline for a single commit
PATH="$HOME/emdash-projects/connect-labs/.venv/bin:$PATH" git commit
```

### Why `make test` exists

A worktree is missing all three things pytest needs, and each fails in a way that
does not name the real cause:

| Missing | Symptom |
|---|---|
| the venv (lives in the main checkout) | `pytest: command not found`, or the system python with nothing installed |
| `.env` (untracked, so it does not exist here; settings read it from `BASE_DIR`, which is *this* worktree) | `ImproperlyConfigured` on whatever setting is read first |
| `GDAL_LIBRARY_PATH` / `GEOS_LIBRARY_PATH` on macOS (read by `config/settings/test.py` for Darwin, and **not** in `.env`) | `Set the GDAL_LIBRARY_PATH environment variable` |

`make test` resolves all three (linking `.env` from the main checkout rather than
copying it, so it cannot go stale) and then runs pytest exactly as CI does.

Note that `--reuse-db` is on by default (`pyproject.toml`), so a few tests that
depend on migration-seeded or fixture-created tables can fail locally while
passing in CI, which builds a fresh database. Before assuming a failure is yours,
reproduce it on a clean `origin/main` worktree.

Two more ways a red check is not what it looks like:

- **CI runs `pytest connect_labs/ -n auto` and nothing else.** Anything outside
  that path — `tools/tests/` in particular — can never go red in CI, so a test
  there passing is not evidence it runs (#1391, #1431). Run it yourself.
- **`main` can itself fail `pre-commit run --all-files`** (#1520). When it does,
  every open PR shows a red linter for files nobody on that PR touched. Check
  whether the failing files are yours before chasing it.

## Key Commands

```bash
inv up                              # Start docker services (postgres, redis)
npm ci && inv build-js              # Install JS deps and build frontend
inv build-js -w                     # Build with watch mode (rebuilds on change)
python manage.py runserver          # Django dev server (uses config.settings.local)
make test                           # Run tests (works from any worktree — preferred)
pytest                              # Run tests (needs venv + .env + GDAL vars already set)
pytest connect_labs/audit/      # Run tests for one app
celery -A config.celery_app worker -l info   # Celery worker (async audit creation, AI tasks)
pre-commit run --all-files          # Run linters/formatters
make commit                         # Git commit with correct venv PATH (works in worktrees)
make manage CMD="migrate"           # manage.py from any worktree (resolves venv + .env)
```

## Browser Verification — use `gstack browse` proactively

**You CAN drive a real browser against labs prod via `gstack browse`.** When you ship a UI or BE change to `labs.connect.dimagi.com`, do not stop at "I can't verify the logged-in path because I'm a bot." Use `gstack browse` to actually open the page, exercise the flow, and inspect the DOM/console. Reach for it whenever:

- A change touches the workflow runner, auth gate, OAuth flow, or any session-authenticated view.
- You just deployed and want to confirm the new bundle is loaded (find the bundle hash in the DOM, fetch it, grep for new strings).
- A user reports a UI bug — reproduce it in the browser before guessing.

What `gstack browse` gives you: the user is already logged into labs prod in the persistent browser session, so authenticated pages render fully. You can read DOM, see console errors, observe network activity, and verify the deployed code is what you think it is.

**Default to testing yourself before declaring "verification needs the user."** "I can't OAuth into CCHQ as a bot" is a real limit, but "I can't load the runner page" is not — that's gstack browse territory.

Note: `gstack` is often **not on the tool shell's `PATH`** even when it is installed. Invoke the binary directly — `~/.claude/skills/gstack/browse/dist/browse` — rather than concluding it is unavailable.

## Critical Warnings

- **DO NOT** query Django ORM models (`Opportunity`, `User`, `Organization`) expecting production data — those tables are empty. Use `LabsRecordAPIClient`.
- **DO NOT** use `config.settings.labs_aws` for local development. Use `config.settings.local` (the default). The `labs_aws` settings are only for the AWS deployment at `labs.connect.dimagi.com`.
- **DO NOT** call `.save()` on `LocalLabsRecord` — it raises `NotImplementedError`. Use `LabsRecordAPIClient` for persistence.
- **DO NOT** modify models in the retained non-labs apps (`opportunity/`, `organization/`, `program/`, `users/`). They exist only for migrations and FK references.
- **THIS REPO IS PUBLIC.** `dimagi-internal/connect-labs` is public on GitHub — every commit, branch, and PR body is world-readable, and history is not erasable by deleting a line later. So: no API keys or credentials in source (use Secrets Manager / `.env`, which is untracked); no colleagues' email addresses; no share tokens, PATs, or signed URLs; no patient-level or PII sample data in fixtures or test files. Two recent PRs exist solely to undo violations of this — treat a secret that reached a public commit as compromised and rotate it, don't just revert it.

## MCP Servers

Two MCP servers serve this project, split by product concern.

### `commcare_hq_mcp` (local stdio)

A local MCP server (`tools/commcare_hq_mcp/`) gives Claude access to CommCare HQ
application structure for building workflow pipeline schemas.

**Tools:** `get_opportunity_apps`, `list_apps`, `get_app_structure`,
`get_form_questions`, `get_form_json_paths`

**Key tool:** `get_form_json_paths` maps form questions to their exact JSON
submission paths (e.g., `form.anthropometric.child_weight_visit`) for use in
`PIPELINE_SCHEMAS` field definitions.

**Data safety:** HQ app-definition API only. No form submissions, case data,
user data, or patient-level information.

**Runs locally** as a stdio subprocess. Auth via CommCare API key (`.env`) and
Connect OAuth token (`~/.commcare-connect/token.json`).

### `connect_labs` (remote HTTP)

A remote MCP server hosted inside the labs Django app (`connect_labs/mcp/`)
at `https://labs.connect.dimagi.com/mcp/`. The protocol endpoint is a
FastMCP 3.x Streamable-HTTP ASGI app mounted in `config/asgi.py`; the catalog
registers **182 tools** (write tools are rate-limited and fully argument-logged
to `MCPAuditLog`) — 66 of them generated from the supply-chain
operation registry (`connect_labs/supply_chain/operations.py`), one tool per
operation, so the count moves whenever that registry does.

**Auth:** two ways in, both resolving to the same labs user (tools run as that
user, audit rows attribute to them):

- **Standard MCP sign-in (OAuth 2.1)** — for people. Any MCP client adds the URL
  and signs in through the browser; nothing is client-specific. The 401 names
  the protected-resource metadata, labs' own OAuth server (django-oauth-toolkit
  at `/o/`) issues the token, and clients self-register at `/o/register/`.
  Tokens carry only the `mcp` scope and MCP clients can hold no other, so an MCP
  sign-in never becomes a key to labs' other OAuth APIs. See
  `connect_labs/mcp/oauth.py`; discovery routes are in `config/asgi.py`.
- **Personal Access Tokens (PAT)** — for scripts and headless agents. Mint/rotate
  self-service at `/labs/mcp/tokens/` (the `labs-token-setup` skill automates
  this). The verifier tries a PAT first, then an OAuth token.

Labs was PAT-only until 2026-09-11, with OAuth discovery deliberately suppressed
(#431) because nothing stood behind it. It was changed so labs works like any
other remote MCP server. Do not reintroduce the suppression: a client's 401
must keep naming the metadata, or sign-in stops working for every client.

**Setup:** see `docs/MCP_SETUP.md`.

### MCP-powered skills

Seven repo skills (`.claude/skills/`) help Claude iterate on labs and operate the deployment:

- **`workflow-author`** — edit a live workflow instance via the `connect_labs` MCP (pull → edit JSX → push). **Use this for the common case.**
- **`pipeline-author`** — edit a pipeline schema via the `connect_labs` MCP with preview-then-save.
- **`workflow-templates`** — author new SEED templates in the repo. Only for the rare "ship a new starter in labs" case, not for editing existing workflows.
- **`pages-author`** — compose card landing-page surfaces at `/labs/p/<slug>` via the `pages_*` MCP tools.
- **`labs-token-setup`** — generate an MCP PAT and wire it into `~/.claude/mcp.json` seamlessly. Opens labs in the browser, user approves, Claude Code picks up the token automatically.
- **`deploy-labs`** — trigger the AWS deploy via GitHub Actions.
- **`aws-env-update`** — add/update env vars and secrets in the ECS task definitions.

The `connect_labs` remote MCP tool families: **targeting** (`targeting_*` — indicators/select/methodology/scenario/admin_levels/compare_criteria/research: where an indicator crosses a threshold across Africa, who lives there, what it would cost, and the workings), **workflows** (list/get/create/create_from_template/clone/update render code & definition/patch/add & remove_pipeline_source/update_opportunity_ids/set_template_flag/sync_from_template_file/**sync_from_deployed_template**/create_run/run_default/resume_dual_track_run/save_snapshot/delete), **pipelines** (list/get/update_schema/preview/sql/delete), **semantic registry** (`semantic_registry_*` — list/get/create/update/validate: the indicator sets behind the KMC dashboard, editable without a deploy), **synthetic data** (`synthetic_*` — envs, profile/generate, clone-from-prod, repoint, fidelity reports, local-record dump/count, image server), **microplans** (`microplans_*` — plans, transitions, work areas, bulk create + status, coverage param schema, study ensure/reset), **pages** (`pages_*`), **solicitations/responses** (incl. `award_response`), **reviews**, **funds** (incl. allocations), plus `campaign_build_national`, `custom_analysis_run`, `labs_context`, `program_admin_demo_seed`, `supply_demo_reseed`, `task_create_synthetic`, `get_sample_ids`, `get_opportunity_apps`, `list_templates`, and `workflow_authoring_guide`.

`workflow_sync_from_deployed_template` is the one worth knowing about: it syncs a live workflow from the template already running on the server, with no file upload — useful when you cannot get a local checkout in front of the instance you need to fix.

## Deeper Documentation

- **[LABS_GUIDE.md](connect_labs/labs/LABS_GUIDE.md)** — Detailed development patterns: OAuth setup, API client usage, proxy models, CLI scripts
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Code style, testing conventions, PR process, step-by-step guide for adding new features
- **[.claude/AGENTS.md](.claude/AGENTS.md)** — Full architecture reference: per-app details, API endpoints, data access patterns, common mistakes
- **[docs/LABS_ARCHITECTURE.md](docs/LABS_ARCHITECTURE.md)** — Architecture diagrams, data flow, cross-app dependency matrix. Caution: its "7 labs apps" count and "labs never writes domain data locally" claim predate the current app set and the labs-only synthetic backend
- **[docs/SAFE_MODE.md](docs/SAFE_MODE.md)** — `inv safe-claude`: locked-down Claude Code config for working near PII
- **[docs/WORKFLOW_EDITOR_QUICKSTART.md](docs/WORKFLOW_EDITOR_QUICKSTART.md)** — non-developer onboarding: mint a PAT, run safe-claude
- **[docs/DOCS_AUTOMATION.md](docs/DOCS_AUTOMATION.md)** — the automation that consumes PR `## Product Description` sections (mkdocs site, Confluence updater, weekly changelog)
- **[docs/synthetic-kmc-clone-runbook.md](docs/synthetic-kmc-clone-runbook.md)** — two-phase profile→generate runbook for cloning prod opps into labs-only synthetics
- **[docs/PERFORMANCE_RUNBOOK.md](docs/PERFORMANCE_RUNBOOK.md)** — **written for AI agents.** Labs slow, hanging, or 5xx-ing? Start with `python3 tools/perf_triage.py --hours 3`, which does steps 1–5 and prints a verdict
- **[docs/multi-site-auth.md](docs/multi-site-auth.md)** — the contract behind the `supply` / `campaign` satellite sites: one Django project, one user table, one session cookie, so **authentication is global but authorization is per-surface**. Read before adding a site or a permission check
- **[docs/OUTBOUND_EMAIL.md](docs/OUTBOUND_EMAIL.md)** — SES sending, live since 2026-07-29 behind `LABS_EMAIL_ENABLED`
- **[connect_labs/labs/indicators/README.md](connect_labs/labs/indicators/README.md)** — the targeting app: measures, the counts-sum/rates-never-sum rule, and how a figure is defended
- **[docs/targeting-data-acquisition.md](docs/targeting-data-acquisition.md)** — the acquisition register behind the targeting dataset (what is held, what was declined, and why)
- **[pr_guidelines.md](pr_guidelines.md)** — Pull request best practices
- **[docs/plans/](docs/plans/)**, **[docs/superpowers/specs/](docs/superpowers/specs/)**, **[docs/designs/](docs/designs/)** — Design documents and implementation plans for features built in this environment (most are point-in-time records; check each doc's status banner before treating it as current)
