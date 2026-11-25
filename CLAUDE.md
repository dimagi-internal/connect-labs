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
| Solicitations (legacy) | `program_id` | `solicitation`, `solicitation_response`, `solicitation_review` | **No longer written.** Solicitations live in the labs DB (`solicitations/local_models.py`); the old prod records were ACE test output and were not imported |
| Audits         | `"audit"`        | `AuditSession`, `AuditCreationJob` | Scoped by `opportunity_id` |
| Tasks          | `"tasks"`        | `Task`                  | Scoped by `opportunity_id` |
| Workflows      | `"workflow"`     | `workflow_definition`, `workflow_render_code`, `workflow_run` (+ child `workflow_run_snapshot`), `workflow_chat_history`, `workflow_template_draft` / `workflow_template_version` (children of a template workflow's definition) | Scoped by `opportunity_id` or `program_id` |
| Pipelines      | `"pipeline"`     | `pipeline_definition`, `pipeline_render_code`, `pipeline_chat_history` | Scoped by `opportunity_id` |
| Semantic registries | `"semantic"` | `semantic_registry`    |                       |
| Flags          | `"flags"`        | `Flag`                  | Hang off a workflow run |

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
| `solicitations/`   | Calls for partners owned by an organisation (`owner_org`, a `LabsOrg`), in the labs DB alongside the marketplace's EOI rounds (`source` tells them apart; the marketplace reads directory rounds only). Who may do what is `access.py` alone (owner by grant, staff as operator, respondent); `data_access.py` is the single choke point every view, MCP tool and agent goes through. `solicitations_seed_demo` seeds an invented demo org. Where the work happens: a call offers its micro-plan's plans, or admin areas to work within (`service_area_limits`), or anywhere; owner and respondent pick on one map (`static/solicitations/service_area_picker.js`): whole countries (level 0, Mapbox country tiles) or admin areas at a level they choose (the microplans boundary layer, always on), resolved and checked server-side (`service_areas.py`). See `docs/superpowers/specs/2026-10-15-solicitations-owned-by-organisations-design.md` | `access.py`, `data_access.py`, `local_models.py`, `forms.py`, `service_areas.py`    |
| `microplans/`      | Microplanning at `/microplans/`: sampling + coverage planning on admin boundaries/work areas, program-scoped plans, study groups, KPIs, Connect work-area export. See `docs/microplans-design.md` (historical north-star). **Simple Microplan** (`/microplans/simple/`, `simple_views.py`, `static/microplans/simple_microplan*.js`): its in-page help popup renders `user_docs/simple-microplan.md` — change that guide in the same PR as any UI change (tests check every step has a help article). **A workspace program** (`labs.workspaces`, e.g. Auto-Connect 10120) uses Simple Microplan ONLY, at `/microplans/program/<id>/simple/` (plans carry `program_id` + `team`; NMs see their team's, only PMs delete; never in the global list): its CHC portfolio/creator pages and every WA Revisit route redirect there (`access.workspace_simple_microplan_url`), and its PM-only access page (`/microplans/program/<id>/workspace/`) has two lists: LLOs (`labs.WorkspaceLLO` — every member of that Connect org is an NM of it) and Users (`labs.WorkspaceUser` — any email, PM or NM of an LLO, wins over every other rule); who has had a role is noted in `labs.WorkspaceAccess`              **Buildings reached** (`coverage/`, page `/microplans/buildings-reached/`, tool `microplans_coverage_check`) takes its households from one of three sources, all reduced to the same row shapes (`check.points_sql` / `check.parse_served`): Connect visits through the Data query (`read.py`), an uploaded CSV (`upload.py`), or **a CommCare project's stored forms** (`commcare.py`: one `visit_level` pipeline whose fields are the spec's form paths, run through `hqdata.pipelines.rows_for_schema` as the signed-in member, then reduced; the page needs a Connect login AND a connected CommCare login, `/labs/commcare/initiate/`, and offers the projects CommCare lists for that person). **Facilities come from CommCare, not a typed list:** a form records its facility as an HQ location id, so the page (and the tool, when `settings.facilities` is omitted) reads the project's locations of the facility type with their coordinates live from CommCare's location API with the person's own token (`hqdata/locations.py`, `commcare.facility_list`); locations with no coordinates are counted and said, never drawn. | `core/`, `sampling/`, `coverage/`, `monitoring/`, `qc/`, `models.py`, `views.py`    |
| `funder_dashboard/`| Funder-facing dashboard at `/funder/`: funds, allocations, charts/KPIs, AI fund-report agents                                                                                                                                          | `data_access.py`, `views.py`, `api_views.py`, `mcp_tools.py`                        |
| `campaign/`        | Retired 2026-11-24: only `models.py` + `migrations/` remain, so the campaign tables (which reference users and workspaces) keep their data and cascades. Restore the tool from git history | `models.py` |
| `supply_chain/`    | Core-labs supply domain at `/supply/` — sourcing, fulfilment, stock and distribution end to end. **The labs DB is its system of record** (real tables, real FKs, SQL aggregation for the stock ledger), unlike every other labs app: supply data originates here, carries no PII, and needs relational work. Sync back to Connect is deliberately deferred. A field worker is a supply point, so distributing to one reuses the ledger. Stock from visits: per-opportunity `DispensingRule`s turn submitted visits into consumption on the ledger (`visit_consumption_ingest`), `stock/services/belief.py` rolls what each worker holds up the network in grouped SQL, and the results read at `/supply/network/` and `/supply/workers/`; see `docs/superpowers/specs/2026-09-28-supply-stock-from-visits-design.md`. **Settled product decisions (tender not round, tables not email, data not verdicts, stock in days...): `supply_chain/PRODUCT_DECISIONS.md` — read it before changing Supply's behaviour.** See `docs/superpowers/specs/2026-09-11-rutf-procurement-design.md` (Part 2 covers buyer-of-record, provenance and stock). **Every programme has one running organisation** (`ProgrammeRunner`, `supply_chain/runner/`): its members are the programme (they tender, award, pay, and record first-hand), Django staff act for it as platform support, and everyone else who can see it is a read-only partner; a programme's Connect manager declares it first (a synthetic labs-only programme has none, so a Dimagi member who can open it declares Dimagi), the running organisation's admins (or Connect members) change it, support always (`programme_runner_set`, `runner/declaring.py`); a declared runner's members hold the programme even where Connect does not list it to them, and Dimagi is one organisation that runs programmes, not a special case (#2458; `docs/superpowers/specs/2026-10-12-supply-running-organisation-design.md`). Every write is an attributed, idempotent `OperationCall`; record changes are `Revision`s; `?as_of=` renders a records page as it stood that evening, via a rolled-back rewind, and a stock page (Stock, Movements, Workers, Network, flow) live with what had happened by that day; see `supply_chain/history/as_of.py`. | `models.py`, `data_access.py`, `operations.py`, `stock/services/` (`belief.py`), `scopes.py` |
| `benchmarks/`      | Anonymous peer benchmarks across opportunities: cohorts (membership IS the read grant), per-cohort disclosure settings (currently all off for KMC), publications of point values plus tenure-axis trends from the source report's saved runs, auto-republish when that report saves a run. Read by `kmc_opp_report`. **Read [connect_labs/benchmarks/README.md](connect_labs/benchmarks/README.md) first.** | `models.py`, `publish.py`, `disclosure.py`, `auto_publish.py`, `mcp_tools.py`, `tasks.py` |
| `workers/`         | **Connect Workers** (Connect's own UI term for its mobile users; never "FLW" in new code): labs' **system of record** for each worker, keyed on the PersonalID username (= Connect `username`). `ConnectWorker`, `WorkerMembership` (one per opportunity, from `user_data`), `WorkerState` (lifecycle stage + facets), `WorkerEvent` (the timeline; Connect events written from snapshot diffs are `confidence=observed`), `WorkerFact` (append-only learned facts with provenance, program-scoped or global), `StaffNote`. Phone, suspension reasons and notes are `EncryptedTextField`. **Reading Connect goes through ONE facade**, `connect_facade/` (`get_source()`): `ConnectWorkerSource`'s methods mirror the endpoints asked of Connect (`docs/connect-asks.md`), `ExportFacade` works around each export-API gap inline (per-opportunity pulls filtered in labs, `since` emulated, synthetic keys for id-less rows, visits thin from Pulse's tail / the analysis cache and never `form_json`, `opportunity_access` probing), and `Capabilities` + `RoutedSource` let a future native source take over one method at a time -- when Connect ships an endpoint, only the facade changes. **Sync** (`sync.py`, `tasks.py`, `manage.py connect_worker_sync`): reads Connect as Pulse's poller unless `WORKERS_SYNC_USERNAME`; hourly opportunity refresh (`OpportunitySync.visible` IS a profile's coverage), cheap tier every 30 min per active opportunity (6 h when Pulse's visit tier is dormant, daily when inactive), all reads before one transaction, `WorkerSnapshot` diffs into events with dedupe keys, `state.py` stage rules + facets. OCS-mode tasks are linked to OCS sessions by participant + `connectTaskId` (`ocs_link.py`). **Page** `/labs/workers/<username>/` and MCP `worker_get` / `worker_sync`: only for a person who may access one of the worker's opportunities (`access.view_for`: labs' holdings, then Connect's `opportunity_access` with their own token), and only those opportunities; restricted content (suspension reasons, restricted events, private notes) on the page only, never in an MCP result or a brief. **Other apps use `workers/api.py`** (`get_or_sync_worker`, `worker_brief_data`, `stage_for`/`facets_for`/`states_for`, `timeline`, `record_event`, `ocs_sessions_for`). Canopy holds only an anchor + cache pointing here (`labs-worker://<username>`, canopy `sub` `personalid:<username>`). Messaging with workers lives in `messaging/`. Design + cross-repo contracts: `docs/superpowers/specs/2026-11-21-connect-worker-messaging-design.md`, `…-contracts.md` | `models.py`, `connect_facade/`, `sync.py`, `state.py`, `access.py`, `api.py` |
| `messaging/`       | **Labs Messaging**: every message labs exchanges with Connect Workers over **PersonalID messaging**. Labs is its own connect-id **message server** (replacing the OCS path): `create_channel` one channel per (worker, program) (`Address`, with consent history and the channel's AES-256-GCM key, served only to the worker's own phone after PersonalID's `userinfo` says whose token it is), `create_message` only (never `send_fcm`, which does not check channel ownership), idempotent message ids (`MESSAGE_ID_ALREADY_EXISTS` is success). connect-id's posts (delivery, consent, callback) are HMAC-checked (`X-MAC-DIGEST`), stored as `TransportEvent` (ciphertext only) and answered **exactly 200** at once, because connect-id never retries; Celery does the work. `Thread` (who handles it: agent, flow, human, none; `handlers/`), `Message` (text encrypted, every author, every one on the worker's timeline as a `WorkerEvent`). Rails live in `service.py` (`send` / `record_inbound`), so no handler skips them: `MESSAGING_ENABLED` (default off: endpoints 404, nothing sends) and the program's own switch (Settings `messaging.settings.enabled`, default off), consent and STOP close threads and block sends, the first message on a channel carries the AI disclosure and how to stop, bare URLs become Markdown links. Staff work at `/labs/messaging/` (the Inbox: threads of the programs and opportunities you hold, reply as staff, take over, hand back, close; `inbox.py`, `access.py`). **Playbooks** per program in Settings (`messaging.playbooks`: direction, trigger person|schedule|state_event|api, eligibility over `WorkerState`, handler agent|flow|human|none, verbatim opening, approval per_send|per_batch|auto_within_caps with `approved_by`, caps incl. quiet hours in the worker's time zone, exit, handoffs, tools, variants; versioned automatically). The **router** (`router.py`) starts outbound threads through eligibility, caps and approval (preview + single-use confirm; `auto_within_caps` for schedules, state events read off the timeline past a cursor, and the API) and routes inbound to the open thread, else the first matching inbound playbook (`priority`), else the Inbox, debounced per thread. **Handlers** (`handlers/`: `start`, `on_message`, `on_handoff_in`, `stop`; flows `welcome`, `verify_code`, `stock_low`, `task_due`, `payment_notice`; the `agent` kind is a stub handing to a person until the canopy bridge registers the real one with `handlers.register`). Workflow action `start_playbook`; MCP `messaging_*` (playbook list/get/set/dry_run, thread list/get, start, inbox_reply). Deploy secrets are NOT in the task definitions yet: see `messaging/README.md` | `service.py`, `router.py`, `playbooks.py`, `handlers/`, `transport/personalid.py`, `personalid_views.py`, `README.md` |
| `connect_web/`     | **Connect Web** at `/connect-web/`: a mobile-first web version of the Connect worker app. A labs user (Dimagi staff, or `CONNECT_WEB_ALLOWED_EMAILS`) checks out a **pool test worker** (`PoolWorker`, a +7426 number with an encrypted backup code) and labs' backend acts as that worker's phone: PersonalID recovery sign-in, Connect's mobile API (opportunities, start learning, claim behind our own learn-complete guard, delivery progress, payment confirmation) and per-domain HQ tokens, all server-side (`WorkerSession`, encrypted). `service.form_engine_context` is the seam the Formplayer form engine (separate track, pending) will call. **Read [connect_labs/connect_web/README.md](connect_labs/connect_web/README.md)** | `service.py`, `personalid.py`, `connect_api.py`, `hq.py`, `views.py` |
| `scope_config/`    | **Settings** at `/labs/settings/<scope>/<key>/`: how an organisation, programme, opportunity or person has set labs up. One config document per scope and namespace in the labs DB, layered defaults ← organisation ← programme ← opportunity ← user (an opportunity reads its **programme owner's** organisation layer), changed by members and Dimagi staff with attribution, history and undo (a labs-only organisation's layer by staff only: it is a bucket of every synthetic opp with that org name, across teams; a page owner or tab opportunity named in a layer must be one the writer may use); MCP `labs_config_*`. Apps declare namespaces (`supply`: which tabs Supply shows and which workflow fills one, replacing `SupplyWorkflowView`; `labs`: the `home` page a scope opens on). Config says what is shown and what is wired to what -- anything that changes a number stays domain data. See `docs/superpowers/specs/2026-10-09-labs-scope-config-design.md`. | `models.py`, `scopes.py`, `merge.py`, `service.py`, `namespaces.py`, `views.py` |
| `hqdata/`          | **CommCare projects** (an HQ domain) as a first-class source: `HQProject` (storage `live` or `persistent`), `HQSource` (forms or cases, optionally narrowed; carries the sync checkpoint), `HQForm` / `HQCase` rows with real upsert and a plain `project_id` (no foreign key: they live in the project's store), `HQSyncRun` history. **Incremental sync** (`sync.py`), modelled on commcare-export (the engine behind dimagi/commcare-sync): every walk is ordered by HQ's own `indexed_on`, the watermark is the newest committed row's `indexed_on` and moves per batch, so a failed run or an unfinished backfill resumes; a delta starts 10 min before it; a weekly reconcile marks purged rows (HQ never announces a purge). One lease per source (`HQSource.claim`). A project's cache scope id is `hqdata.scope.hq_scope_id` (negative, below `-10**9`, so it never meets an opportunity, a labs-only opportunity or a programme-scoped Drive source). **Sync is a durable job** (`jobs.py`): the long-running MCP tool `commcare_project_sync` (`mcp/jobs.py`: recorded in the labs DB, redelivered after a deploy, `job_status` / `job_cancel`), queued by `queue_sync` from a page, an MCP call or the beat `hqdata-sync-due-projects` (which skips a project already queued or running). Each job is time-boxed (35 min) and queues its own continuation, so a sync of any size is a chain of short jobs that always moves forward; a reconcile is resumable (`HQSource.reconcile_started_at` / `reconcile_cursor`, rows stamped with `synced_at`). It runs headless as the project's sync owner's `UserCCHQToken`. **Access** is one function, `access.may_use_project`: a member proven by HQ within 24 h (`refresh_memberships` from the person's own HQ identity; a domain no longer listed deletes the membership; labs never lets a person claim a domain; `create_project` needs HQ to say the person's token can read the domain). **Where a project's data lives** (`stores.py`): `HQProject.store` is `labs` or `external`, and everything that touches rows goes through `store_for(project)`; `HQForm` / `HQCase` carry a plain `project_id` (no foreign key) for that reason, while projects, sources, checkpoints and run history always stay in labs. An external store is a customer-hosted PostgreSQL (`HQExternalDatabase`: password `EncryptedTextField`, never shown again): `netguard.check_host` resolves the host and refuses loopback / link-local (cloud metadata) / private addresses (private only if staff tick `allow_private_network`), the connection is then made to the vetted address (`hostaddr`) with the certificate checked against the name, `external.check_connection` must pass every step before a project may move there (`store_switch.switch_store`; HQ is the source of truth, so a move re-reads rather than copies and purges the old store), and `store_schema.py` versions the tables labs creates there (forms, cases, and the four analysis caches). A pipeline over an external store runs its cache operations against that database: `redirect.default_connection_is` makes the thread's `default` connection the customer's for the length of one backend call (`RedirectedBackend`), so nothing staged or computed is written in labs. **Pipelines over a project**: data source type `hq_store` (`project_id`, `store_kind`, narrowing by `app_id` / `xmlns` / `case_type`) reads the project's stored rows in the shape `cchq_forms` / `cchq_cases` produce, so one schema runs against either; every read, cached or not, passes `pipeline_source.check_project_access` (`AnalysisPipeline._check_drive_access`; `_select_backend` then picks the labs or customer-database backend), the project's cache scope is cleared when a sync changes rows (`sync.invalidate_project_caches`) so its results are kept for weeks, not an hour, and a person with no Connect token runs one as `AnalysisPipeline(None, local_only_user=user)`, which refuses every other source. A saved pipeline is the engine's own pipeline record under the project's scope (`pipelines.save_pipeline` / `find_pipeline` / `list_pipelines`, over `pipeline_dao`, which unlike the workflow dao is allowed for a project in a customer's database because a definition holds no data); its `data_source` is stamped from the project and narrowing on every save AND every read (`pipelines.stamped_schema`), whatever the stored schema says. There is ONE kind of page source, `{pipeline_id, alias}`; `pipelines.pipeline_rows` is the one function that runs a project pipeline (store session, size ceiling, period window). Admin is `access.is_project_admin`. **One read path:** a page's data, live (`get_pipeline_data`) or frozen for a run (`get_cached_pipeline_data`), is read by the engine's data access, which for a project scope (`data_access.in_project_scope`) calls `workflows.read_sources`; `read_project_pipeline_data` is just the door that opens a dao and asks it. The page props are built from `page_mode`'s shared pieces (`closed_endpoints`, `scope_prop`, `run_instance`, `blank_instance`), pinned against a Connect page's by `test_page_mode.py`. **Sign in without Connect**: `/commcare/` (`web/`; it was `/hq/`, which now redirects) is a satellite (`LABS_SATELLITE_URL_PREFIXES`): CommCare HQ OAuth creates a `cchq:<host>:<username>` user with NO email, found by that name only and never by email, so a CommCare account cannot become a Connect or staff user; the existing labs boundary logs such a session out of every labs path, and `mcp/server.py` lets it into MCP with exactly one credential, a PAT of the `commcare` scope (minted at `/commcare/tokens/`), which `allowed_tools` confines to `token_scopes.COMMCARE_TOOLS` (the `commcare_*` tools in `hqdata/mcp_tools.py`, each gated by `may_use_project`; a test pins that the registered `commcare_*` tools and that list are the same). The redirect URI `/commcare/login/callback/` must be registered on the HQ OAuth app (the old `/hq/` one may stay registered; nothing uses it). The pages use the standard labs layout (sidebar, header, a CommCare sidebar item) for anyone with a Connect login and the bare layout only for a CommCare-only person (`views.render` / `access.is_commcare_only`). See `docs/multi-site-auth.md`. Secrets in this app use `utils/encrypted_fields.EncryptedTextField` (Fernet, `MultiFernet` over `LABS_FIELD_ENCRYPTION_KEYS`, refuses to save with no key where `LABS_FIELD_ENCRYPTION_REQUIRED`, which is on for AWS). **Workflows over a project** (`hqdata/workflows.py`, `web/workflow_views.py`): a project's workflow records (definitions, render code, pipeline definitions) are `LabsLocalRecord` rows scoped by `hq_scope_id(project.pk)` passed as the *opportunity* id, exactly like a labs-only opportunity's, so the workflow engine reads and writes them unchanged; `local_records_backend.is_labs_only_opportunity_id` accepts an hq scope when the project exists. The local backend checks nothing else (the scope is the tenant boundary), so `project_dao(user, project)` is the ONLY door: it runs `require_project` and refuses a project that keeps its data in a customer database (run snapshots would land in labs); and `BaseDataAccess` itself (`_open_hq_scope`) raises `PermissionError` for an hq scope unless the user is a verified member of that project right now (so no tool, task or view can open a project's records by naming its id; the shared `is_labs_only_opportunity_id` is deliberately NOT widened, the records client asks `is_hq_project_scope`). That DAO holds no Connect token (`hq_scope`), reads pipelines as `AnalysisPipeline(None, local_only_user=user)`, and pins any `hq_store` schema to the scope's own project (in `_schema_to_config`). **Why not a labs-only `SyntheticOpportunity` per project:** `is_accessible_to` passes every Dimagi user and fails open on empty `allowed_domains`, `save()` mints a `LabsOrg` and an `OrgMembership` for a person with no email, and `labs_context` would list it in every Connect user's picker; none of that has the 24 h revocable membership. Pages are served only at `/commcare/projects/<id>/workflows/<id>/` (GET, page kind only, sandboxed, repo-owned render code for the overview from `ensure_project_dashboard`), with every `apiEndpoints` entry `None` except `getPipelineData`, so nothing reaches `/labs/...`; `/commcare/` is context-free (`CONTEXT_FREE_PREFIXES`) and `/labs/sandbox/render/` is skipped by the labs session boundary so opening the frame does not sign a CommCare-only person out. **Authoring pages over a project** (`hqdata/mcp_workflow_tools.py`, `workflows.py` `create_page` / `update_page` / `delete_page`): `hq_workflow_{list,get,create,update,delete,data,authoring_guide}` in the `commcare` scope. A page is always the page kind (sandboxed, read-only, no runs); its `pipeline_sources` are `[{pipeline_id, alias}]` naming pipelines SAVED IN THAT PROJECT by `commcare_pipeline_save` (`clean_sources` refuses anything else, so a page cannot name another project's pipeline or a Connect source), read in `read_project_pipeline_data` through `pipelines.pipeline_rows` (every row; `run_pipeline` caps its display copy). Config is a whitelist of booleans (`PAGE_CONFIG_FLAGS`; `noPipelineStream` is NOT allowed: a page has no other way to fetch its data) merged onto the page's own. The overview labs maintains is marked `system` (found by that, not by its name), repaired by `ensure_project_dashboard`, and refused by `update_page`; edits are serialised per page by an advisory lock and re-read under it. Any member may change a page, only its author (`created_by`) or an admin deletes it (a `DeletedWorkflowBackup` is kept). Cases have no worker to group by: use `terminal_stage: visit_level` (one row per case) and group in render code (found live: a case pipeline grouped by `case_type` collapses to one row). Embedding works for any registered page id, authored or not. **Embedding for a CommCare-only viewer** (`embed/hq_kind.py`, `EmbedClient.commcare_host` / `vouches_for_membership`): the `commcare_workflow` kind (`{kind, id: page, project_id}`) is shown to people a CommCare client names as `cchq:<its host>:<username>` and no one else (the grant's `_commcare_person`; a CommCare client is never an identity authority, a Connect-style client never shows this kind, both enforced at registration AND at the grant). `needs_connect_token = False` skips every Connect token/org-data step; `still_allowed` re-runs `may_use_project` on EVERY request (labs' own 24 h record, not HQ: a vouched person removed in HQ stays visible until HQ stops asserting them and the session ends, so keep `session_seconds` short); the only route is `/embed/commcare/<project>/<page>/pipeline-data/` (`allowed`), and the frame's own session-start POST is the only write it may make (`writes`). Membership is labs' own record unless the client `vouches_for_membership` (staff-only: HQ's word creates a plain member of the ONE registered project on its own host, never promotes, never creates a project, and skips the can-read-forms probe every other path applies; steps 5-7 of a CommCare client's grant are one transaction so a refused or replayed assertion changes nothing). **Saved runs** (`workflows.py` `save_run` / `list_runs` / `get_run` / `delete_run`, tools `commcare_workflow_run_{save,list,get,delete}`, `?run=<id>` on the page, `POST …/workflows/<id>/runs/`): a run is the engine's own run record under the project scope (`dao.create_run` + `complete_run`), and its snapshot is built by the engine's own `snapshot_runtime.build_snapshot_for_run`: for a project scope (`data_access.hq_scope`) `get_cached_pipeline_data` delegates to `read_project_pipeline_data(aliases, window)` (so a project pipeline source works), a page with no registered template gets the default contract (every pipeline, no workers, no run state; a page's own `snapshot_inputs` still wins), and a `period_scoped` grouped pipeline is re-aggregated over the run's `[start, end)` window (`pipelines.pipeline_rows(window=)`); the payload also carries `captured_at` and `period`; an empty page, a failed pipeline or >12 MB is refused rather than saved; a run is looked up by page AND project so another page's run id is a 404; the author or an admin deletes. Opening a run passes the frozen rows as `pipeline_data` with no live endpoint. **Computed sources** (`hqdata/computed.py`, `computed_sources.py`): a page names `{computed, alias, params}` beside its pipelines and reads it as `pipelines.<alias>.rows`; each name is registered with one function run as the signed-in member over the project's store (`campaign_coverage`: counts per health centre and cycle joined to the plan, as `GROUP BY` / `COUNT(DISTINCT child)` aggregates in `microplans/coverage/campaign.py`, so it scales to millions of forms; `campaign_reach`: coverage against satellite population and buildings reached per district and cycle, reusing `microplans/coverage/compute.py`). A **slow** source never computes in a request: it answers from a kept result (by project, settings and last sync), or starts a Celery fill and raises `ComputedWarming`, which the data views answer as the 202 `warming` protocol of `workflow/cache_warm.py` (the sandbox host polls it), and new data shows the previous result marked `stale` while the new one is computed. `ensure_system_page` keeps a repo-owned page current by its `system` marker (the overview and **Campaign coverage**, `campaign_coverage.jsx`, opened from the project's Workflows page or `commcare_campaign_coverage_page`). Not yet: scheduled runs, an in-browser editor for pages, external-store projects. Design: `docs/superpowers/specs/2026-11-13-commcare-first-class-design.md` | `models.py`, `sync.py`, `backends/`, `scope.py`, `tasks.py` |
| `labsdata/`        | **Data** at `/labs/data/` (was the SQL explorer, `/labs/explorer/` redirects) and the read-only tools behind the **`labsdata` canopy agent** (the Scout experience inside labs): one validated SELECT over `visits` (every raw column + full `form_json`, scoped to opportunities the caller holds, read-only/time-boxed/empty `search_path`, and on AWS run on a Postgres login of its own, `labs_data_reader`, that row-level security confines to the statement's grants -- `reader_login.py`, migrations 0039/0040), whole visits by id, and `data_workflow_context` (a workflow's pipelines as a validated query over `visits`, to check a workflow against raw data). MCP `data_*` (`explorer_*` kept for scout-queries); canopy scope `data:read`. Which agents labs shows, and which may work with sensitive data, are ticks at **Labs Admin › Canopy agents** (`labs/canopy_settings.py`, Dimagi staff, history + restore), chosen from canopy's signed list of the agents it allows on labs (the site agents API, dimagi-canopy 0.14; none shown when canopy has said nothing for a day); each workflow chooses its own agent from those shown, and `canopy_panel.availability` is the one answer to "may this agent be offered here, and if not why". An agent through canopy sees production rows only when labs ticks "Sensitive data" AND canopy's per-call `Canopy-Execution` statement clears it (`canopy_panel.call_sensitive_data_clearance`). Labs publishes what it needs back at `/labs/canopy/site.json`. See `docs/superpowers/specs/2026-10-22-canopy-site-agents-api-design.md`. See `docs/superpowers/specs/2026-10-22-labsdata-agent-design.md`. `data_query` takes the agent's `display` (labels, formats, chart; `display.py`), kept in the kept result for the card. The Data page's **saved queries** (`models.SavedQuery`: the query, never rows, versioned per name; personal ones owner-only) are the one save path; the results card's Download CSV and Save open the Data page (`open_on_page_url`: SQL, opportunities, row cap) rather than serving rows themselves (docs/canopy-agent-panel.md "Results labs keeps"). **Data spaces** (`models.DataSpace`, `/labs/data/space/<id>/`): a frozen set of sources (opportunities; programs frozen to their opportunities; or one workflow) and the saved queries kept on it. **Every read is over a space**: `service` takes nothing else, and only `spaces.py` makes one -- `generate` (unsaved, from a selection, resolved as the caller for that call: the plain Data page and personal saved queries) or `open` (saved: every program and every frozen opportunity held now, else `not_found`; no membership table, nothing stored as a grant; then the space's `audience`, `holders`/`private`, which only narrows -- `spaces.AUDIENCES`; managers change sources/audience/managers, members run and save, all checked in `spaces.py`, never by an agent); `from_request` is the door both surfaces use, and `save` keeps a generated one. `tests/test_spaces_gate.py` fails if a view or `data_*` tool takes a `space_id` without the gate, or anything else resolves ids. Every `data_*` tool takes `space_id` and then refuses any other selection; changing a space's sources, audience or managers is a manager's own click only (no tool, no page action). **Saved conversations** (`models.SpaceConversation`) are pointers to canopy chat sessions begun on the space's page (session id + the saver's label, never a transcript or canopy's title), under the space's rules; opening one asks canopy to open it in the panel as the person. Opening a space loads its sources' uncached visits, as the person, once, without holding the page. A saved query's chart is drawn on the canvas by `static/labsdata/result_chart.js`, the results card's own chart module (the card cannot load scripts, so `mcp/ui/__init__.py` inlines it at the card's `/*@include:...*/` marker): change a chart in one place. The page's canvas is switched by page actions in `static/labsdata/data.js` (`page_showQuery`, `page_openItem`, `page_proposeSave`, `page_showVisit`), which return no rows (docs/canopy-agent-panel.md "Data spaces"; design `docs/superpowers/specs/2026-10-24-data-spaces-design.md`). **Dictionary and de-identified exports** (`/labs/data/space/<id>/exports/`, `dictionary.py`, `exports.py`, `lineage.py`, routes in `export_views.py`): a saved space's dictionary keys every field by form path + deliver unit with the app builds it was seen in; a person-run scan stores paths, counts and kinds, never values; a field is exported only once a manager reviewed it (drop unless allowed); an export refuses any field the dictionary does not know, applies `labs/deid` transforms (keyed pseudonyms, rounding, month, per-case date shift), checks its own output (rows, leak scan against the source's own identifiers, ranges, duplicates, orphans, small groups) and is downloadable only after a manager other than the requester signs it off, for 7 days. Design: `docs/superpowers/specs/2026-10-26-deidentified-exports-design.md`. | `engine.py`, `service.py`, `spaces.py`, `workflow_context.py`, `guidance.py`, `validator.py`, `display.py`, `models.py` |
| `embed/`           | **Other systems framing labs** (Connect prod first, any registered system later). An `EmbedClient` (Django admin, superusers) names its public keys (JWKS, asymmetric only), the origins that may frame labs, and the resources it may show. Its backend redeems an RFC 7523 assertion naming the viewer at `/embed/token/`, and its page frames `/embed/c/<client>/<kind>/<id>/`. A partitioned cookie then acts as that person, with their own Connect token, on the routes the resource's **kind** allows (`kinds.py`; `workflow` first, and pages are workflows) and nowhere else. It is read-only, draws once per token, and is never a labs login. Phase 1 trusts identity authorities only (Connect). See `docs/superpowers/specs/2026-10-23-embedding-labs-design.md` | `kinds.py`, `grant.py`, `sessions.py`, `middleware.py`, `framing.py` |
| `embed_example/`   | **The live reference embedder** at `/embed-example/`: a stand-in for another system that embeds labs, written to be read and copied. It imports nothing from `embed/`, holds its own Ed25519 key (`EMBED_EXAMPLE_PRIVATE_KEY`), publishes its JWKS, signs for the signed-in person only, calls `/embed/token/` over HTTP, and frames a report with both token handoffs. Registered by `manage.py embed_example_register` as an identity authority limited to **generated data** (`EmbedClient.generated_data_only`, enforced at token time). Not cross-origin until `EMBED_EXAMPLE_BASE_URL` points at a second hostname. | `embedder.py`, `views.py`, `config.py`, `templates/embed_example/page.html` |
| `flags/`           | Flag-type `LocalLabsRecord`s — findings observed on FLWs during workflow runs; API mounted at `/labs/workflow/api/run/<id>/flags/`                                                                                                     | `models.py`, `data_access.py`                                                       |
| `mcp/`             | The labs remote MCP server (see [MCP Servers](#mcp-servers)): PAT auth, FastMCP ASGI app at `/mcp/`, tool registry + audit log. Tools that produce rows keep them as `labs-result://` kept results (`KeptResult`, Labs' own MCP feature for any client) and return facts + a link, read back with `resources/read` (`kept_results.py`; docs/canopy-agent-panel.md "Results labs keeps") | `server.py`, `tool_registry.py`, `tools/`, `auth.py`, `kept_results.py`                |
| `pulse/`           | Funder-facing service-delivery telemetry at `/labs/pulse/` — wall display, donor reports, and the partner network at `/labs/pulse/network/`. Polls Connect's export API on a beat. See [Connect Pulse](#connect-pulse)                        | `ingest.py`, `api.py`, `network_api.py`, `partner_names.py`, `hq_location.py`       |
| `labs/deid/`       | What in a form identifies someone, shared by the synthetic profiler, the Data page's field index, coach charts and the de-identified export: the form walk (`fieldscan`), the field classifier and its default settings (`classify`, the profiler's rules), keyed transforms (`transforms`) and the leak scan (`leaks`). Pure functions, no models. | `fieldscan.py`, `classify.py`, `transforms.py`, `leaks.py` |
| `labs/synthetic/`  | Registry of "synthetic" opportunities that serve fixture JSON from GDrive instead of prod exports. Synthetic data is never a real case (every number, answer, id, name and date is generated): profile a real opp on the server (MCP `synthetic_profile_opp`) → saved profile → generate into a labs-only opp (`synthetic_generate_opp`). Labs never copies raw production exports out: the old dump-to-Drive was removed (#3122). Whether an opp's data IS generated is `provenance.is_generated` (backfill: `synthetic_mark_generated`). See `docs/SYNTHETIC_OPPS.md`. | `models.py`, `provenance.py`, `access.py`, `clone_from_prod.py`, `generator/`, `registry.py`, `gdrive.py`, `prod_export.py` |
| `mopup/`           | **User-facing name: "WA Revisit"** (at `/wa-revisit/` — old `/mopup/` URLs redirect there; the app/module/URL-namespace/JS-namespace internals still say "mopup", only displayed text and the path prefix changed) — program-scoped runs that pick an opportunity/wards/dates, pull visits once, then re-evaluate cluster-aware coverage candidates against tunable thresholds. Hands off by calling microplans' own `create_plan()` and redirecting into its unmodified review page. Run state is a `LocalLabsRecord`, not a Django model.                                     | `core/` (`candidates.py`, `indicators.py`, `geometry.py`, `gaps.py`, `handoff.py`), `tasks.py`, `views.py` |
| `marketplace/`     | **Org directory at `/labs/marketplace/`.** `LabsOrg` is the MASTER org registry — an org that answered an EOI and lost never reaches Connect, so this is strictly larger than Connect's org set. Satellites (`OrgProfile`, `OrgContact`, `OrgConnectSlug`) + EOI rounds/submissions (`solicitations.local_models`, `llo_entity` is a real FK) are fed from the LLO Directory sheet by `marketplace_import [--eoi] [--check-access]`. Pulse partner identity reads from here. Matching is exact-only; anything else waits for a human verdict on the sheet's `EOI Response Mapping` tab. Every import publishes a row-numbered data-quality report to the `Labs Findings` tab. The **canopy agent panel** rides the network and round pages — see [docs/canopy-agent-panel.md](docs/canopy-agent-panel.md). How rounds are ingested, versioned and reviewed: [docs/marketplace-round-review.md](docs/marketplace-round-review.md). | `models.py`, `directory.py`, `identity.py`, `eoi.py`, `matching.py`, `responses.py`, `quality.py`, `views.py` |
| `semantic/`        | SQL semantic layer — a general indicator engine: a YAML/record registry compiled to SQL and run in-process over the visit cache. Each registry declares its own **model** (`entity`, `visit_columns`, `pipelines`, optional `weight_series`, `defaults`, `series`) in `model.py` terms; KMC (`registry/kmc`, the only source for the KMC dashboards' indicators since the browser engine was deleted; one set of slug ids since #2004, Neal Lesh's compute spec rules throughout) and `registry/visit_quality` are two examples. `legacy.py` supplies KMC's model ONLY to records saved before the model existed. Every SQL fragment is allow-list checked on save and on compile. Served at `workflow/api/<id>/semantic/`. User docs: `user_docs/semantic-layer.md`; parity: `connect_labs/semantic/PARITY.md`. | `model.py`, `legacy.py`, `compiler.py`, `layer1.py`, `runtime.py`, `snapshot.py`, `validation.py`, `workflow_binding.py`, `registry/{kmc,visit_quality}/*.yml` |
| `labs/indicators/` | The indicators baseline at `/labs/indicators/` (Health indicators) — population/burden/coverage primitives: thresholded selection across Africa, reach counts, and the methodology behind every figure. A first-class system others build on; it imports nothing from effectiveness or Pulse. **Counts sum up the hierarchy; rates must never be summed** — see its README before touching aggregation. | `measures.py`, `resolve.py`, `boundaries.py`, `methods.py`, `defence.py`, `export.py`, `README.md` |
| `effectiveness/` | Cost-effectiveness at `/labs/cea/`: the Connect models (CHC, MBW, KC) run on the baseline — CEA models, Implementations (what delivered opportunities achieved), Market size, and the Workspace preview; all share one `Scenario` (`effectiveness/scenario.py`). Sizing can plan a reach to a target at territory grain, with Kangaroo Care's baseline risk scaled to local newborn mortality as a labelled Labs reading (`market.PLACE_RISK`; `docs/EFFECTIVENESS.md` § Where to work). The only layer that reads both the baseline and Pulse; the arithmetic is pure (`engine`, `valuation`, `result`, `explore`, `groups`, `market`, `implementation/whatif`); the baseline is read through its public `query` module. See `docs/EFFECTIVENESS.md`. | `engine.py`, `result.py`, `implementation/`, `market.py`, `people.py`, `views/`, `docs/EFFECTIVENESS.md` |

### Retained Non-Labs Apps (Models + Migrations Only)

| App             | Purpose                                                                                |
| --------------- | -------------------------------------------------------------------------------------- |
| `opportunity/`  | ORM models and migrations only — needed by FK references. No views, no business logic. |
| `users/`        | User model definitions and migrations                                                  |
| `organization/` | Organization model definitions and migrations                                          |
| `program/`      | Program model definitions and migrations                                               |
| `commcarehq/`   | Minimal — just `HQServer` model + migrations (needed by FKs)                           |

**Cross-app connections:** Workflow can create audits and tasks; flags hang off workflow runs. AI agents modify workflows and solicitations; funder-dashboard AI agents live in `ai/agents/`. `mopup` reads CommCare/Connect data and writes into `microplans`; `semantic` is read by `workflow` — the live dashboard endpoint and the `semantic_snapshot` builder that freezes a saved run.

## No marketing site here

**Labs no longer serves the marketing site.** It lives in its own repo, **[dimagi-internal/connect-marketing](https://github.com/dimagi-internal/connect-marketing)** — not here, and labs is no longer its staging environment. The `prelogin` app, its templates and its ~15 MB of assets were removed on 2026-10-27; nothing in labs should be edited to change the marketing site. (`/about/` is still an ungated `TemplateView`, so labs is not literally page-free — it is just not where marketing pages are authored.)

The site root (`/`) redirects to `/labs/overview/`, which is login-gated, so an anonymous visitor lands on `/labs/login/?next=/labs/overview/`. Every former marketing URL (`/platform`, `/portfolio/<slug>`, `/insights`, `/blog/<slug>`, `/contact/`, `/in-action/<slug>`) now 404s here, which is correct: labs is `Disallow: /` and not public-facing.

## Workflow Engine

Templates are single Python files in `workflow/templates/` exporting DEFINITION (statuses, config), RENDER_CODE (React JSX string transpiled by Babel), and optionally PIPELINE_SCHEMAS (CommCare form field extraction). The registry auto-discovers them. Pipeline schemas map CommCare form JSON paths to extracted fields with aggregations and transforms. Render code receives `{definition, instance, workers, pipelines, links, actions, onUpdateState, view}` as props.

Templates can set `multi_opp: True` on their `TEMPLATE` dict to opt into multi-opportunity support. Multi-opp workflows store an `opportunity_ids` list on the definition, merge workers and pipeline rows across those opps at runtime, and tag every row/worker with its source `opportunity_id`. Single-opp workflows (default) are unchanged — they fall back to `[primary_opp_id]` with the same tagging shape. See [WORKFLOW_REFERENCE.md §8](connect_labs/workflow/WORKFLOW_REFERENCE.md#8-multi-opportunity-workflows) for the full contract.

**Workflow actions** (`config.actions`, `workflow/actions.py`) are things a workflow lets you DO, e.g. "Initiate AI coach": one framework executor per type (`create_task`, `start_ocs_outreach`, `start_playbook` -- a labs Messaging conversation under a program playbook, `create_audit` -- an AI image audit per worker from a saved audit setup, `connect_labs/audit/setups.py`), run from a report button (`actions.runAction`) or the labs MCP (`workflow_run_action`) through the same preview → confirm-token → background execution, as the person, recorded in `WorkflowActionExecution`. OCS tokens are stored per user (`UserOCSToken`) so this needs no browser. Separately, a workflow can **share its runs with the canopy agent panel** (`config.agent.share`, `workflow/agent_sharing.py`). Both are off unless a workflow turns them on. See [WORKFLOW_REFERENCE.md §14](connect_labs/workflow/WORKFLOW_REFERENCE.md#14-workflow-actions-and-sharing-a-run-with-an-agent) and `docs/canopy-agent-panel.md`.

Templates that produce a periodic review with a definite "moment of completion" can set `supports_saved_runs: True` to opt into the **in_progress | completed** run lifecycle. They declare what the snapshot captures via `snapshot_inputs` (a manifest of pipelines/workers/state_keys), render code reads run data via the `view` helper (`view.workers`, `view.pipelines.<alias>`, `view.state.<key>`, `view.isCompleted`, `view.asOf`), and triggers completion via `view.complete({confirm})`. The framework atomically builds the snapshot, flips status, stamps `completed_at`, and write-protects the run. Reference: `connect_labs/workflow/templates/performance_review.py`. Full contract: [WORKFLOW_REFERENCE.md §9](connect_labs/workflow/WORKFLOW_REFERENCE.md#9-saved-runs-templates).

A saved-runs template can be completed **server-side, with no browser**. Prefer the declarative route: `snapshot_inputs.builder` names a framework builder from `connect_labs/workflow/snapshot_builders.py` (today: `semantic_snapshot`) and the rest of the manifest is that builder's spec. Because the manifest rides on the *definition*, a computed snapshot is patchable via `workflow_update_definition` with no deploy — see `SNAPSHOT_INPUTS` in `connect_labs/workflow/templates/kmc_programme_metrics.py` for a worked example. The older per-template `build_snapshot` Python hook remains as an escape hatch (WORKFLOW_REFERENCE.md, "Escape hatch"), but writing one means a PR + deploy for every change; if you reach for it to grade a semantic registry, extend the builder spec instead. Either way the framework refuses to freeze an unstaged snapshot rather than freezing an empty one.

**A run's stored snapshot is not what readers see.** `LabsRecordAPIClient` encodes `workflow_run` snapshots columnar on write (`workflow/run_codec.py`) and moves any that encode to 64 KB or more into a child `workflow_run_snapshot` record, leaving `snapshot_ref` + `snapshot_summary` on the run (`workflow/run_snapshot_store.py`); reads decode and lazily fetch, so `run.data["snapshot"]` looks unchanged. Anything that reads or writes run records around the client (raw HTTP, `LabsLocalRecord` ORM) sees the stored form. Trend readers should use `snapshot_state`, which reads the summary without fetching the child. A snapshot over 5 MB cannot be saved at all (#2194), which is why the programme report no longer stores case lists.

**Public links to a saved run** (`workflow/public_shares.py`, WORKFLOW_REFERENCE.md §9): Dimagi staff holding a run's ACL make an expiring link from its row on the workflow list; the run is copied into `PublicRunShare` in the labs DB (the copy is outside every ACL, so the only access check is at creation), drawn anonymously in the sandbox frame at `/labs/workflow/shared/<token>/`, and deleted when the link ends.

**Supply sources** (`workflow/supply_sources.py`, WORKFLOW_REFERENCE.md §15): a workflow reads the supply chain's own figures (worker stock, stores, orders, tenders...) beside its pipelines, declared as `supply_sources` and read as `supply.<alias>` -- `{rows, rollup, metadata}` -- or `actions.querySupply`. Every source is a supply READ operation, run as the viewer over the workflow's opportunities (across programmes if they span them); programme-wide sources run once per programme; rollups sum counts and recompute rates on the server. Writes become workflow actions. Reference: `supply_stock_review`. The `stock_forecast` source joins children in treatment (visits, via the dispensing rule's `cases` block) to the stock at every point and lays need forward week by week; drawn by `supply_stock_forecast`.

**Pages** (`workflow/page_mode.py`, WORKFLOW_REFERENCE.md §16): a page is a workflow definition with `kind: "page"` -- drawn by the same runner with **no run** (blank in-memory instance, run endpoints null). Every render (page or workflow) also gets `scope` (the org / programme / opportunity in view, with names and what is under it), `config` (the Settings namespaces it lists in `config_reads`) and `workflows` (other workflows' runs from `workflow_sources`, read as the viewer). Pages live at `/labs/p/<org|programme|opportunity>/<key>/[<slug>/]` (`workflow/page_views.py`); with no slug, the scope's `home` from Settings (`labs` namespace), which is what an organisation coming in lands on (`/labs/overview/?organization_id=<slug>` redirects there). Start one from `page_blank`; author it with the workflow-author loop. **A page's code runs sandboxed** (`workflow/sandbox.py`, `components/workflow/sandbox/`): an opaque-origin iframe with an enforced CSP, reaching labs only through a bridge the labs page answers as the viewer (`querySupply`, `queryWorkflow`, link clicks, `view.confirm`, `actions.download` ...); no `fetch`, no pipelines or workers. What a page may and may not do: WORKFLOW_REFERENCE.md §16. **Personal pages** (`workflow/personal_pages.py`, model `PersonalPage`, MCP `personal_page_*`): a page one person owns, at `/labs/p/user/<username>/<slug>/` (`/labs/p/me/` lists yours, makes one, sets sharing), shared with named people (username/email) and scopes (`program:<id>`, `opportunity:<id>`, `organization:<slug>`, members of which may open it). Access is decided from the row before the definition is read; only the owner and `is_staff` change it. Its records are `LabsLocalRecord` rows under `personal_scope_id(pk)` (negative, in (-10**9, -5*10**8]) opened only through `personal_dao` (`BaseDataAccess._open_personal_scope` re-checks and makes them read-only for viewers); never a labs context. It reads no data (every endpoint closed); `scope` is `{type: "user", key: <owner>}`.

**Existing templates** (43; `*` = multi-opp): `audit_par`\*, `audit_with_ai_review`, `bulk_image_audit`, `chc_audit_history`\*, `chc_nutrition_analysis`, `flw_audit_trend_dashboard`\*, `flw_daily_indicator_report`\*, `flw_daily_indicator_table`\*, `flw_daily_summary_report`\*, `flw_daily_summary_report_rutf`\*, `flw_weekly_audit_report`\*, `indicator_opp_report`, `indicator_programme_report`\*, `indicator_worker_review`\*, `interviews_reporting_v2`, `jakusko_chlorine_dispenser`, `kmc_flw_flags`, `kmc_flw_review`\*, `kmc_image_audit`\*, `kmc_longitudinal`, `kmc_opp_report`, `kmc_programme_metrics`\*, `kmc_project_metrics`, `llo_weekly_review`, `mbw_auditing_v5`, `mbw_auditing_v6`, `mbw_visit_verification`, `muac_picture_audit`\*, `muac_plausibility`\*, `ocs_outreach`, `page_blank`, `performance_review`\*, `photo_audit_report`, `program_admin_report`\*, `program_audit_creator`\*, `r2_revisit_analysis`\*, `rutf_ciff_kpis`, `sam_followup`, `supply_program_home`\* (a page), `supply_stock_forecast`\*, `supply_stock_review`\*, `verified_monitoring`, `weekly_dual_track_audit`\*

**The KMC report family** is the worked example of reports that stay in step. `kmc_programme_metrics` (the programme report, saved weekly runs, semantic-layer snapshot) drills into `kmc_flw_review` (its companion, one worker). `kmc_opp_report` is the programme report's content for one opportunity, for its network manager, plus anonymous peer benchmarks, fanned out one per opportunity by `benchmarks_create_opp_reports`; it has saved runs of its own and receives the programme report's saved weeks cut to its opportunity (`workflow/hand_down.py`). Both reports draw with the shared report library `window.LabsReport` (`components/workflow/report/`), which any render can use at runtime. Every instance follows the deployed template (`render_source`) -- or, faster, a **template workflow** (`render_source: {workflow: <id>, <scope>}`): render, config defaults and snapshot spec as data (LabsRecords in the template's program or opportunity, under that scope's LabsRecord ACL: write access edits and publishes, a read -- or a public share -- follows) with draft/preview/publish/rollback and no deploy (`workflow/template_workflows.py`, `workflow_template_*` / `workflow_follow_template` MCP tools, WORKFLOW_REFERENCE.md §12), shares the programme report's pipelines and live registry record, resolves config from the template on read, and fills its own visit cache when cold (`warm_cache_on_read`). **The generic indicator cascade** — `indicator_programme_report` (companion `indicator_worker_review`, hands down to `indicator_opp_report`) — is the same family for ANY registry: one render (`indicator_report_render.js`) for the programme and opportunity reports, no programme-specific code, every tile/target/label/noun/scorecard column from the registry's display contract (`semantic/display.py`, `user_docs/semantic-layer.md#how-a-report-reads-it-display`). Its snapshot spec names only the builder; `snapshot_builders.resolve_spec_defaults` derives scopes, case index, visit pipeline and credibility from the registry. Instances follow the template from creation (`follow_template`). Read [WORKFLOW_REFERENCE.md §12](connect_labs/workflow/WORKFLOW_REFERENCE.md#12-reports-that-stay-in-step-followed-templates-fan-out-warm-on-read) before adding a per-instance copy of anything, and [connect_labs/benchmarks/README.md](connect_labs/benchmarks/README.md) before publishing a benchmark (rebuild history first, then publish).

**For any MBW or dashboard work, copy from `mbw_auditing_v5`** (SQL-native, pipeline-pure, saved-runs). The MBW v1-v3 monitoring and v4 auditing templates have been removed.

Use the MCP server's `get_form_json_paths` tool to discover correct field paths when building pipeline schemas.

**Full reference:** [WORKFLOW_REFERENCE.md](connect_labs/workflow/WORKFLOW_REFERENCE.md)

## Connect Pulse

Delivery telemetry at `/labs/pulse/`, polled from Connect's export API. Four
things about it are expensive to rediscover.

**Partner identity lives in the LLO Directory, not this repo.** Connect publishes
partner *names* only for the orgs the polling account belongs to — a minority of
those that deliver. The rest arrive as a slug and are matched against the team's
directory sheet, loaded into the organisation registry (`labs.LabsOrg` +
`marketplace.OrgProfile`) by `marketplace_import` (daily on beat, on demand through
the `marketplace_import` MCP tool, or `make manage CMD="marketplace_import"`; all three
are `marketplace/importer.py::run_import`). No partner name is written down in source; if the
board shows slugs instead of names, that import has not run.

Slugs no string rule can reach are resolved by a human on the directory's
"Connect Org Mapping" tab and carried as `marketplace.OrgConnectSlug`. `partner_names.py`
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
- **Infrastructure-as-code:** `infra/` holds CloudFormation stacks — `labs-monitoring.yml` (SNS + RDS/CPU/worker-kill alarms), `labs-access-logs.yml`, `labs-audit-analytics.yml`, `labs-email.yml`, `labs-media.yml` (task-role grant for `media/*` uploads), `labs-tenant-monitoring.yml` (canopy-web/ace-web 5xx alarms), `labs-web-autoscaling.yml` (web tier scaling target + policies — change scaling there, never by hand), `labs-formplayer.yml` (Connect Web's form engine — patched upstream formplayer built from `formplayer/`; runbook `formplayer/DEPLOY.md`). Core RDS/ECS is still click-ops; import into a stack as needed. See `infra/README.md`
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

**A change to something another system calls** (today the embed endpoints, frame messages and errors in `connect_labs/embed/`) updates the developer docs in `user_docs/developers/` in the same PR. Those pages are hand-written and technical; the automated updater never touches them. See [docs/DOCS_AUTOMATION.md](docs/DOCS_AUTOMATION.md#two-audiences).

The `## Product Description` section drives automated documentation updates and the weekly changelog. PRs that skip it or use a different section name (e.g. `## Summary`) are invisible to that automation.

### `main` merges directly — the merge queue is OFF (since 2026-09-10)

`main` is protected by a repo ruleset: no direct pushes, no deletion, no non-fast-forward, `linter` + `pytest` required on the PR, **squash merges only** (the repository allows no other method). There is **no merge queue**: it re-ran the whole suite against the queued merge result and cost a median 4.3 minutes per merge, nearly always to confirm that two independently green PRs were also green together. Jonathan took that trade for iteration speed. Consequences:

- `gh pr merge <N> --squash` (or with no flag — squash is the only allowed method) **lands the commit immediately** once the PR's own checks are green. The branch does not have to be up to date with `main`.
- **`main` is tested after the fact.** CI runs the full suite on every push to `main`. If a merge that was green on its PR goes red on `main` (a semantic conflict with something that merged in between), that push run is the signal, within ~3 minutes. **Fix forward**: a follow-up PR, not a revert-and-wait.
- **Migration numbers race.** Two branches cut from the same `main` both add `00NN_*`; each is green alone and `main` goes red with conflicting leaf migrations once both land. Right before opening a migration PR, check `git fetch origin main && git ls-tree --name-only origin/main <app>/migrations/` and renumber if your number is taken.
- **If the required checks never appear, the branch is conflicting.** GitHub does not run `pull_request` workflows on an unmergeable branch, so a PR in conflict with `main` shows no `linter`/`pytest` at all and `gh pr merge` refuses in a way that reads like a permissions problem. Check `gh pr view <N> --json mergeable` first (`CONFLICTING`) and rebase, rather than re-pushing or recreating the PR.
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

- **CI runs `pytest connect_labs/ -n logical` and nothing else**, split over four
  runners (`--shard=1/4` … `4/4`, whole files per slice, balanced by the measured
  times in `connect_labs/testing/shard_durations.json`; refresh with
  `tools/refresh_shard_durations.py`); the required `pytest` check is green only
  when every `pytest (K/4)` slice is. A failure names its slice — reproduce it
  with the same `--shard` or just run the file. Anything
  outside that path — `tools/tests/` in particular — can never go red in CI, so a
  test there passing is not evidence it runs (#1391, #1431). Run it yourself.
- **`main` can itself fail `pre-commit run --all-files`** (#1520). When it does,
  every open PR shows a red linter for files nobody on that PR touched. Check
  whether the failing files are yours before chasing it.
- **`pytest-shifted-clock`** (nightly, or a PR labelled
  `clock-shift`) runs the suite with Python's clock moved 1-400 days forward
  (`pytest --clock-shift=+30d`). Red there means a test depends on the date;
  reproduce with `make test CLOCK_SHIFT=+<N>d` (the job summary gives N). Anchor
  fixture dates to today, or pass `as_of`; never `now()` / `CURRENT_DATE` in
  SQL — Postgres stays on the real clock.

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
registers **277 tools** as of 2026-11-18 (write tools are rate-limited and fully argument-logged
to `MCPAuditLog`) — 116 of them generated from the supply-chain
operation registry (`connect_labs/supply_chain/operations.py`), one tool per
operation, so the count moves whenever that registry does. The registry holds
120: four are `internal=True` (`catalogue_seed`, `tracker_import`,
`stock_report_ingest`, `visit_consumption_ingest`) and are deliberately kept OFF the MCP catalogue —
seeds, bulk imports and ingests are run by an engineer through a management
command, the way every other bootstrap in this repo is (`indicators_bootstrap`,
`load_indicators`, `seed_semantic_registry`). They stay
in the registry so their commands keep its schema validation and provenance
stamping. Those carry the
prefix **`supply_chain_`** (`connect_labs/supply_chain/mcp_tools.py`,
`TOOL_PREFIX`). They were `procurement_*` until 2026-09-15, which was wrong
for two-thirds of them — only sourcing is procurement, and the name stuck
through fulfilment, stock and distribution.

**Long-running tools** (minutes of work) declare `long_running=True` on `@register`
(`connect_labs/mcp/jobs.py`): after every gate passes, the call records an `MCPJob`
(labs DB) and queues it on the Celery worker, returning `{job_id, poll_with:
"job_status"}` -- or the result, if done within ~30s (`wait`). `job_status` /
`job_cancel` show a job only to its starter, and to a restricted call only jobs a
restricted call started; the job runs AS the caller with the call's restriction
(`restricted_job`) and re-checks `server.policy_denial` when it starts. A deploy does
not lose it (acks_late + requeue on cold shutdown + a DB lease; jobs.py says how), so the
handler must be safe to RERUN from the start. Work of an hour or more adds
`job_runner="standalone"` and runs on a Fargate task of its own (`labs/ecs_tasks.py`,
shared with synthetic jobs). Today: `workflow_preview_as_of`, `workflow_rebuild_history`.

**Auth:** two ways in for people and scripts (plus a third, below, for canopy acting as a visitor), each resolving to a labs user (tools run as that
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
  this). The verifier tries a PAT first, then an OAuth token. A PAT is
  **full** or **no-uservisit-data**. An agent that needs labs access for one job the
  person asked for uses `labs-token-setup --temporary` (one hour, no renewal, nothing
  registered), never a sign-in script of its own against `/o/register/`. An agent NOT on
  the person's computer (a cloud runner) cannot take that flow's `127.0.0.1` callback, so it
  asks with its own token instead (`mcp/agent_requests.py`, `request_agent_token.py`): the
  person opens `/labs/mcp/agent-requests/<ref>/` and approves with their PASSKEY through canopy
  (the same `full_access` sign-off, action key `labs.mcp_token.agent`, at either level), labs
  mints their one-hour token and returns it SEALED to the agent's X25519 key
  (`utils/sealed_output`), keeping only ciphertext, once. The request may carry
  `X-Canopy-Runner-Proof` (`canopy vault runner-proof`): labs asks canopy which approved runner
  signed it (`mcp/runner_check.py`, `canopy_sdk.runner_attestations`) and shows that to the
  person; no proof is "Not verified" (allowed for now), a proof canopy refuses is refused.
- **Both approval pages say the same thing** (`mcp/consent.py`): what can it do, for how
  long, what happens next. An app's self-chosen name is shown as a claim, never in the
  headline: it limits nothing.
- **Minting full access needs the person's passkey, through canopy** —
  creating or rotating a full PAT, the `labs-token-setup` page and a full `mcp`
  OAuth sign-in all ask canopy for a passkey approval (canopy's site sign-offs,
  `canopy_sdk.django.sign_offs`) and mint only after checking it
  (`mcp/full_access.py`; `create_token` refuses a full token without that
  proof). The person approves on the page they are on, in canopy's dialog
  (`{% canopy_sign_off %}`), and every outcome is a notice there; the page's
  word is never taken, `collect` reads canopy's state. Someone canopy cannot vouch for gets no-user-visit tokens only. Accounts
  ticked in the Django admin (`FullAccessSelfMint`, on for ACE) self-mint; using
  tokens, existing tokens and no-user-visit tokens are untouched.
- **"No user visit data"** — `/mcp/no_user_visit/` is the same MCP app mounted
  a second time (`config/asgi.py` marks each request; `server.endpoint_restricted`
  reads the mark). A request there, a no-uservisit-data PAT, or an OAuth token
  with the `mcp:no-uservisit-data` scope (which that endpoint's challenge asks
  for) is a restricted call -- all three resolve to the SAME tool set (pinned by
  `test_the_restricted_endpoint_and_the_restricted_token_grant_the_same_tools`).
  It never sees visit data but CAN edit definitions and trigger server-side
  computation (`DEFINITION_WRITE_TOOLS`: responses carry ids/versions/dates/counts
  only). It reaches only `mcp/token_scopes.py:RESTRICTED_TOOLS`,
  and the visit-reading ones among them (`GENERATED_ONLY_TOOLS`) run only when
  every opportunity the call reads is generated synthetic data
  (`mcp/visit_access.py` resolves the opps; `labs/synthetic/provenance.py` decides
  "generated"). Enforced by the same `ToolScopeMiddleware` gate as canopy's scopes,
  re-checked in `_run_registry_tool_inner`. Adding a tool to those lists means
  checking what it returns; a tool that reads visits needs a resolver.
- **Canopy acting as a visitor (off unless ticked at Labs Admin › Canopy agents)** — labs
  issues an ID-JAG for the person on a registered page, canopy redeems it at
  `/o/token/` (jwt-bearer + `private_key_jwt` + DPoP), and calls the MCP with
  `Authorization: DPoP`. Tools run as the visitor, limited to the scope's tools.
  The protocol is the canopy SDK (`canopy_sdk`, canopy-web `sdk/python`); labs
  owns only its registries in `connect_labs/labs/canopy.py`. See
  `docs/canopy-agent-panel.md`. With the grant on, canopy also runs a **live
  probe** every 30 min (`/labs/canopy/probe/`): a real ID-JAG for the dedicated
  service account `canopy:probe` (created by `mcp/migrations/0006`; no password,
  no staff bit, refused by the OAuth callback), redeemed and used to call
  `marketplace_rounds_list` — so a broken grant shows up in canopy before a
  visitor hits it. Never point the probe at a real person's account.

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
- **`pages-author`** — build a page (a workflow with no runs: an organisation's or programme's home at `/labs/p/<scope>/<key>/`); points at workflow-author's "Build a page".
- **`labs-token-setup`** — generate an MCP PAT and wire it into `~/.claude/mcp.json` seamlessly. Opens labs in the browser, user approves, Claude Code picks up the token automatically.
- **`deploy-labs`** — trigger the AWS deploy via GitHub Actions.
- **`aws-env-update`** — add/update env vars and secrets in the ECS task definitions.

The `connect_labs` remote MCP tool families: **CommCare projects** (`commcare_*` — project list/get/create/sync (a long-running job)/set_storage, forms and cases as stored, saved pipelines and their runs; the only tools a `commcare`-scope token reaches), **indicators** (`indicators_*` — list/select/methodology/admin_levels/compare_criteria/research/research_write: where an indicator crosses a threshold across Africa, who lives there, and the workings), **cost-effectiveness** (`cea_*` — model_run/opportunities/market_size: what a Connect model says a round of spend buys, what delivered opportunities achieved, and what reaching everyone in chosen places would cost), **template workflows** (`workflow_template_create/get/list/update_draft/preview/publish/rollback/set_owners`, `workflow_follow_template` — the no-deploy path for a report many workflows show), **workflows** (list/get/create/create_from_template/clone/update render code & definition/patch/add & remove_pipeline_source/update_opportunity_ids/set_template_flag/sync_from_template_file/**sync_from_deployed_template**/create_run/run_default/resume_dual_track_run/save_snapshot/preview_snapshot/preview_as_of/hand_down/ensure_visit_cache/delete), **saved-run history** (`workflow_history_runs`/`_history_eligibility`/`workflow_rebuild_history`/`workflow_prune_history`), **workflow runs** (`workflow_run_context`/`_run_indicators`/`_indicator_explain`/`_run_action`/`workflow_action_status` — read a run's grading, and run the workflow's own actions via preview → confirm; WORKFLOW_REFERENCE.md §14), **pipelines** (list/get/create/update_schema/preview/sql/set_shared/delete), **messaging** (`messaging_*` -- playbook list/get/set/dry_run, thread list/get, `messaging_start` preview then confirm, `messaging_inbox_reply`: labs' conversations with Connect Workers, as the person), **semantic registry** (`semantic_registry_*` — list/get/create/update/validate/explain/set_indicator_meta/delete: indicator registries for any programme (KMC and `visit_quality` ship as examples), editable without a deploy),
**benchmarks** (`benchmarks_*` — cohort_create/update/list/delete/add_opportunities, publish, create_opp_reports: anonymous peer figures and the per-opportunity reports that show them), **synthetic data** (`synthetic_*` — envs, profile/generate, clone-from-prod, repoint, fidelity reports, local-record dump/count, image server), **microplans** (`microplans_*` — plans, transitions, work areas, bulk create + status, coverage param schema, study ensure/reset, **`microplans_coverage_check`**: what share of the buildings around each facility did a campaign's households (a visit form's household GPS point, stored on the household) get within a set distance of, per cycle, with a data-quality account — `microplans/coverage/check.py` + `reached.py`, areas by `labs/admin_boundaries/placement.py`), **marketplace** (`marketplace_orgs_get`/`marketplace_rounds_list`, and per round `marketplace_round_responses`/`_scores`/`_versions`/`_version_archive`/`_version_delete`/`_summary_set`/`_criteria_set`: read a round's submissions, set and version its criteria, compare scores across versions — see [docs/marketplace-round-review.md](docs/marketplace-round-review.md)), **solicitations/responses** (incl. `award_response`), **reviews**, **funds** (incl. allocations), plus `labs_context`, `program_admin_demo_seed`, `task_create_synthetic`, `get_sample_ids`, `get_opportunity_apps`, `list_templates`, and `workflow_authoring_guide`.

`workflow_sync_from_deployed_template` is the one worth knowing about: it syncs a live workflow from the template already running on the server, with no file upload — useful when you cannot get a local checkout in front of the instance you need to fix.

## Deeper Documentation

- **[LABS_GUIDE.md](connect_labs/labs/LABS_GUIDE.md)** — Detailed development patterns: OAuth setup, API client usage, proxy models, CLI scripts
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Code style, testing conventions, PR process, step-by-step guide for adding new features
- **[.claude/AGENTS.md](.claude/AGENTS.md)** — Full architecture reference: per-app details, API endpoints, data access patterns, common mistakes
- **[docs/LABS_ARCHITECTURE.md](docs/LABS_ARCHITECTURE.md)** — data flows (LabsRecord CRUD, visit analysis), where data lives (prod API vs labs DB), LabsRecord fields, frontend stack, and a decision tree for new features
- **[docs/SAFE_MODE.md](docs/SAFE_MODE.md)** — `inv safe-claude`: locked-down Claude Code config for working near PII
- **[docs/WORKFLOW_EDITOR_QUICKSTART.md](docs/WORKFLOW_EDITOR_QUICKSTART.md)** — non-developer onboarding: mint a PAT, run safe-claude
- **[docs/DOCS_AUTOMATION.md](docs/DOCS_AUTOMATION.md)** — the automation that consumes PR `## Product Description` sections (mkdocs site, Confluence updater, weekly changelog)
- **[docs/synthetic-kmc-clone-runbook.md](docs/synthetic-kmc-clone-runbook.md)** — cohort clones from a YAML spec (`labs/synthetic/cohorts/`): pinned program, refreshed in place. For a one-off clone into a new program use `synthetic_clone_opp` (`docs/SYNTHETIC_OPPS.md`)
- **[docs/PERFORMANCE_RUNBOOK.md](docs/PERFORMANCE_RUNBOOK.md)** — **written for AI agents.** Labs slow, hanging, or 5xx-ing? Start with `python3 tools/perf_triage.py --hours 3`, which does steps 1–5 and prints a verdict
- **[docs/multi-site-auth.md](docs/multi-site-auth.md)** — the contract behind satellite sites (`/commcare/`): one Django project, one user table, one session cookie, so **authentication is global but authorization is per-surface**. Read before adding a site or a permission check
- **[docs/OUTBOUND_EMAIL.md](docs/OUTBOUND_EMAIL.md)** — SES sending, live since 2026-07-29 behind `LABS_EMAIL_ENABLED`
- **[docs/marketplace-round-review.md](docs/marketplace-round-review.md)** — marketplace EOI rounds end to end: the Google Forms intake (`marketplace_import --eoi`, the service-account share, a runbook for adding a round), criteria versions (`RubricVersion`, superseded AI reviews), the Scores/compare pages, review stances, and the round/review MCP tools
- **[docs/canopy-agent-panel.md](docs/canopy-agent-panel.md)** — the canopy agent panel on the marketplace pages: labs signs an Ed25519 assertion for the signed-in visitor, the widget hands the agent the SELECTION on screen (slugs, never rows) and it reads the rows back through `marketplace_orgs_get`. Three labs-specific traps: the CSRF cookie is `HttpOnly` so the token comes from the DOM; the include belongs in `inline_javascript` because `javascript` renders in `<head>` where there is no `document.body`; and the agent's tools run as the AGENT, not as the visitor
- **[connect_labs/labs/indicators/README.md](connect_labs/labs/indicators/README.md)** — the indicators app: measures, the counts-sum/rates-never-sum rule, and how a figure is defended
- **[docs/indicators-data-acquisition.md](docs/indicators-data-acquisition.md)** — the acquisition register behind the indicators dataset (what is held, what was declined, and why)
- **[docs/EFFECTIVENESS.md](docs/EFFECTIVENESS.md)** — the layers (baseline, Pulse, effectiveness), the effectiveness app, the pages and how the models stay current
- **[docs/ON_DEMAND_COMPUTE.md](docs/ON_DEMAND_COMPUTE.md)** — the pattern for running an expensive external model (a simulation) from Labs: content-addressed request, durable MCP job, on-demand instance, cached result; the retired EMOD runner is the worked example (tag `archive/pmc-emod`)
- **[pr_guidelines.md](pr_guidelines.md)** — Pull request best practices
- **[docs/plans/](docs/plans/)**, **[docs/superpowers/specs/](docs/superpowers/specs/)**, **[docs/designs/](docs/designs/)** — Design documents and implementation plans for features built in this environment (most are point-in-time records; check each doc's status banner before treating it as current)
