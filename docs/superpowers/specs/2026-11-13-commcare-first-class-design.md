# CommCare HQ as a first-class data source — design

Status: proposed, 2026-11-13. Plan: `docs/superpowers/plans/2026-11-13-commcare-first-class.md`.

## What we want

1. Labs treats a **CommCare project space (an HQ domain)** as something you can work in
   without Connect: its forms and cases, kept fresh by **incremental sync**, queryable,
   and usable by pipelines.
2. A person can **choose to persist** a project's data and its pipeline results, so they are
   not rebuilt on a one-hour TTL.
3. A person with **no Connect login** signs in with **CommCare HQ OAuth**, and sees only the
   parts of labs that make sense without Connect.
4. A project space can keep its data in a **customer-hosted database** instead of the labs one.

## What is true today (read from the code, 2026-11-13)

- Labs already reads HQ: `labs/integrations/commcare/api_client.py` (`CommCareDataAccess`),
  data source types `cchq_forms` and `cchq_cases`, with a per-user HQ OAuth token
  (`UserCCHQToken`, headless refresh in `cchq_tokens.py`). Both types hang off a Connect
  opportunity: the domain comes from the opportunity's `cc_domain`, and rows are cached
  under an opportunity id.
- **No incremental sync for HQ.** Every cache miss is a full walk, or a
  `form_lookback_days` window. The only delta logic in labs is Connect's
  `user_visits` export (`backend.py::_try_delta_refresh`), a cursor on a numeric row id
  that finds new rows but never status changes. Visit ids from HQ are UUIDs, so
  `get_raw_delta_anchor` returns nothing for them.
- **Nothing persists.** `RawVisitCache` and the computed caches are generational
  (delete + insert) with a 1 h TTL; `ensure_visit_cache` holds a slot at most 180 min, on
  purpose, because expiry is how Connect status changes are picked up.
- **Login is Connect-only.** `LabsOAuthSessionMiddleware` logs out any authenticated user
  with no `session["labs_oauth"]`. `/campaign/` has its own HQ login, but it is a satellite
  and the first labs request logs that user out. `labs.access.scopes` (`Caller`, `holdings`,
  `may_use`) knows only organisation, programme and opportunity.
- **No customer-database support.** `multidb` is a vestigial replication router; nothing
  reads a second database. There is no encrypted-field mechanism; `UserCCHQToken` stores
  tokens in plaintext.

### HQ API facts this design rests on (checked against `dimagi/commcare-hq` docs, 2026-11-13)

- Forms `GET /a/<d>/api/form/v1/`: `indexed_on_start`/`indexed_on_end`
  ("forms that have had data modified since"), `order_by=indexed_on`, `include_archived`,
  `limit` max **1000** (labs sends 2500 today). The list doc's sample omits it, but each form
  object carries `indexed_on` (`indexed_on = fields.CharField(attribute='inserted_at')` in
  `corehq/apps/api/resources/v0_4.py`; commcare-export reads it to page).
- Cases `GET /a/<d>/api/case/v2/`: `indexed_on.gte|gt|lte|lt`, `server_last_modified.*`,
  `last_modified.*`, `closed`, `case_type`, **cursor** pagination, `limit` max 5000.
  `indexed_on` is "subject to change without notice from regular maintenance operations",
  so a delta window must overlap and dedupe.
- Neither API reports hard deletes. Archiving a form and closing a case do re-index it, so
  both are visible to a delta; a purge is not.

## Design

### Concepts

| Concept | Meaning |
|---|---|
| **Project** (`hqdata.HQProject`) | One HQ domain on one HQ host. The unit of access, storage and sync. |
| **Source** (`HQSource`) | What is pulled from a project: forms (optionally one `app_id`/`xmlns`) or cases (optionally one `case_type`). Holds the sync cursor. |
| **Scope id** | The integer the existing caches are keyed by (`opportunity_id` column). A project's is `-(10**9 + project.id)`: negative, so it meets neither a Connect opportunity, a labs-only one (>= 10,000), nor a programme-scoped Drive source (`-program_id`, small). Same trick `gdrive_fetcher.program_cache_scope` uses. |
| **Storage mode** | `live` (default: fetch on demand, TTL'd, as today) or `persistent` (durable store, scheduled delta sync). |
| **Backend / store** | Where rows come from (`hq_api`) and where they live (`labs`, or `external`: a customer-hosted Postgres). Independent. |

### Storage: a durable store beside the cache, not inside it

`RawVisitCache` replaces a whole generation at a time and cannot take an upsert. So a
persistent project writes to **its own tables** with real upsert semantics:

- `HQForm(project, form_id UNIQUE per project, xmlns, app_id, build_id, received_on,
  server_modified_on, indexed_on, username, user_id, archived, form_json, deleted_at)`
- `HQCase(project, case_id UNIQUE per project, case_type, case_name, owner_id, closed,
  date_opened, last_modified, server_last_modified, indexed_on, properties, indices)`

Pipelines keep running through the existing machinery. A new data source type,
`hq_store`, streams rows out of those tables into the same visit-dict shape the CCHQ
fetchers already produce (`form_json` keeps its `form.*` paths; a case sits under
`form_json["case"]`), so `query_builder`, computed caches and every workflow render are
untouched. That derived `RawVisitCache` slot is local-DB-only to rebuild, so TTL on it is
cheap. "Persist the pipelines" is then: the durable store is the truth, and a project's
computed pipeline results are **held** (TTL extended each sync) rather than evicted.

A `live` project keeps today's behaviour, upgraded: its fetch uses the same delta engine
into `RawVisitCache` where a watermark is known.

### Incremental sync (the part labs does not do today)

`hqdata.sync.sync_source(source, mode)`:

- **delta** (default): walk ordered by `indexed_on` ascending from `watermark - 10 min`, upserting in
  batches (`bulk_create(update_conflicts=True)`). **The checkpoint is HQ's own `indexed_on` of the
  newest committed row, advanced with each batch**, so a run that dies resumes where it stopped
  and a first backfill that never finished resumes instead of restarting. This is the model
  commcare-export uses (`date_indexed` pagination mode, per-batch checkpoints with `final` and
  `last_doc_id`), the engine behind commcare-sync. Forms page by re-anchoring on the last row's
  `indexed_on` (`indexed_on_start`), not offset; a page that shares one `indexed_on` pages by
  offset until the anchor moves. Cases use HQ's cursor with `indexed_on.gte`. The 10 minute
  overlap is for HQ's asynchronous search index (a row can become visible after rows with a
  later `indexed_on`); anything older is the weekly reconcile's job.
- **full**: first sync, or on demand: walks everything with `include_archived=true`.
- **reconcile** (weekly): a full walk that also marks as deleted any stored row the walk
  did not see. This is the only way to learn a hard delete; it is why delta alone is never
  declared complete.
- Every run writes an `HQSyncRun` (mode, fetched, upserted, error). A failing source keeps
  its watermark, so the next run resumes; two runs of one source never overlap (Postgres
  advisory lock, as `single_flight.py` does).
- Runs as a Celery task. A beat entry every 5 minutes enqueues each persistent project
  whose `sync_interval_minutes` has elapsed.
- **Credential for a headless run**: the project's sync owner's `UserCCHQToken`. If the
  refresh fails, the project goes to `needs_reauth` and the page says so; nothing falls back
  to another person's token.

### Sign-in without Connect

- New routes `/hq/login/`, `/hq/login/initiate/`, `/hq/login/callback/` (HQ OAuth,
  `access_apis`, PKCE; the same HQ OAuth app as Campaign, one more registered redirect URI).
- User creation: username `cchq:<hq username>`, **blank `User.email`**. The HQ email lives on
  `HQIdentity`. Never merge by email and never derive `is_dimagi`: a CommCare-only identity
  is never staff, whatever address it claims (`utils/dimagi_user.py` keys on `User.email`,
  which stays empty). Linking to an existing Connect account is an explicit action by
  someone already signed in to Connect (the existing `/labs/commcare/` connect), never
  automatic.
- Session marker `session["auth_kind"] = "commcare"` with `session["commcare_oauth"]`.
  `/hq/` joins `LABS_SATELLITE_URL_PREFIXES`; a new `HQOnlySurfaceMiddleware` then confines
  an `auth_kind=commcare` user to `/hq/`, logout, static and docs, and redirects every
  other path there. Fail closed: the allow-list is the surface, not a deny-list of Connect
  pages.
- A Connect user with a linked HQ token uses the same `/hq/` pages without a second login.

### Access

`hqdata.access.may_use_project(user, project)`:

1. an `HQProjectMember` row, verified within 24 h (`verified_at`); and
2. for a live read, an HQ token that passes `verify_hq_access(domain)`.

Membership is verified on HQ sign-in from `/api/v0.5/identity/` `domains`, and creating a
project requires `verify_hq_access` (Edit Data), the same probe labs uses today. Reading
from the durable store needs only (1), so a persistent project stays readable without a
live HQ call; a person removed from the HQ domain loses access at their next sign-in or
within 24 h, whichever first. The gate sits in the pipeline read path next to
`_check_drive_access`, so a cache hit is gated exactly like a fetch.

### Customer-hosted database: the project's STORE (owner decision, 2026-11-13)

The customer's database is where the project's data **lives and is queried**, not a place labs
reads from. This is commcare-sync's model (the organisation owns the database); the choice was
made between "store there", "read from it" and "both", and the first was picked.

Two independent settings on a project:

| | `backend` (where rows come from) | `store` (where rows live) |
|---|---|---|
| default | `hq_api` | `labs` (the labs database) |
| this feature | `hq_api` | `external` (a customer-hosted Postgres) |

(A `backend` that reads a customer's existing warehouse stays open; the sync engine already
takes any backend, so it would be a new class, not a rework.)

- **Control plane stays in labs, data plane moves.** `HQProject`, `HQSource` (watermark, lease),
  `HQSyncRun`, members and the connection record are labs rows. `HQForm` and `HQCase` rows, and
  anything derived from them, go to the store. Rows carry a plain `project_id`, not a foreign
  key, for exactly this reason.
- **`hqdata.stores.store_for(project)`** returns the project's store: its Django database alias
  (registered at runtime from the connection record), `ensure_schema()`, and `forms()` / `cases()`
  querysets. Everything that touches rows (`sync`, the pipeline reader, the MCP tools, the Data
  page, purge) goes through it; nothing imports `HQForm.objects` directly.
- **Writes are idempotent across two databases.** A batch is upserted to the store, then the
  checkpoint advances in labs. A crash between them leaves the checkpoint behind the rows, which
  the next walk re-writes harmlessly. If the labs checkpoint is ever lost, the store's own
  `max(indexed_on)` rebuilds it.
- **Provisioning.** Labs creates `labs_commcare_form` / `labs_commcare_case` (and the read view
  below) in the schema the customer names, with `schema_editor` on that connection. The role
  labs connects as needs create/insert/update/delete on that schema and nothing else; the
  test-connection step proves it can do exactly that and cannot read other schemas' tables.
- **Nothing from the project is kept in the labs database**, including derived caches. A pipeline
  over an external store does not stage rows into `labs_raw_visit_cache`; it runs its compiled SQL
  on the store through a `labs_raw_visit_cache` **view** that maps the form table onto the
  columns the query builder expects (`opportunity_id` = the project's scope id, `pipeline_id` =
  -1, `visit_id`, `username`, `visit_date`, `form_json`, ...), and its computed results are not
  cached in labs (computed on demand, or held in the store where the customer allows writes).
  Pipeline results returned to the person are not stored by labs.
- **Safety.** The host is resolved and refused if loopback, link-local, metadata
  (169.254.169.254) or RFC 1918 unless staff set `allow_private_network` on the record (a
  customer reachable over a tunnel); `sslmode=verify-full` is required; labs sets
  `statement_timeout` and a row cap per fetch; identifiers it interpolates (schema name) are
  validated, never taken from free text. The password and connection string are Fernet-encrypted
  (`EncryptedTextField`, `MultiFernet` over `LABS_FIELD_ENCRYPTION_KEYS`, refusing to save with
  no key in production), never returned by an API or written to a log or the admin, and a
  connection in use by a project cannot be deleted (`PROTECT`, as commcare-sync does).
- **A person's SQL** (the Data page, `hq_query`) runs on the store through the same validated,
  read-only, time-boxed path as today, against the store's connection, as a role that can only
  read the two tables/views.
- Existing `UserCCHQToken` rows move to the same encrypted field in a follow-up migration.

### MCP

`hq_*` tools (project list/get/create, source config, sync now, sync status, persist
on/off, form/case query over the project's store, pipeline preview over a project) under a
new token scope `COMMCARE_ONLY`: an explicit allow-list like `RESTRICTED_TOOLS`, deny by
default, so no existing tool becomes reachable by a CommCare-only identity. HQ users mint
a PAT of that scope at `/hq/tokens/`.

## What we take from commcare-sync

`dimagi/commcare-sync` is a management UI over the CommCare Data Export Tool
(`commcare-export`): it does not sync anything itself, it schedules and supervises the tool.
Taken from it, and from the tool, on purpose:

- **Checkpointed, resumable walks on `indexed_on`**, as above (the tool's `date_indexed` mode).
- **One run record per attempt with an explicit lifecycle**: queued, started, completed, failed,
  skipped, timeout, plus `retry_of` and a reaper for runs a dead worker left `started`
  (commcare-sync `ExportRun`, migrations 0026-0028). Labs has the lease and run history now;
  the timeout status, `retry_of` and the reaper come with the scheduler work.
- **Connection strings stored as one Fernet-encrypted blob, `MultiFernet` over a list of keys**
  (`FERNET_KEYS`: encrypt with the first, decrypt with any, so keys rotate without downtime),
  with a data migration that encrypts what was stored in clear. Same shape as
  `EncryptedTextField` here.
- **A database cannot be deleted while a config uses it** (`Database.is_in_use`, `PROTECT`).
- **Config history** (django-reversion there; `django-pghistory`, already in labs, here) on
  projects, sources and connections, with the run recording the config version it ran under.
- **Kill the subprocess when the worker is cut off** (`finally: process.kill()`), the lesson for
  any external call a task makes.
- **HQ can serve the export definition**: `GET /a/<d>/api/det_export_instance/v1/` lists the
  exports a project already defined in HQ's Data Export Tool UI. A later phase can offer them as
  source presets instead of making a person pick an app and form.

Deliberately not taken: running `commcare-export` as a subprocess. It flattens forms into
per-question SQL tables from an Excel/JSON query, and auths with an API key. Labs' pipelines,
semantic layer and Data page read a form's whole JSON by path, and a CommCare-only user signs in
with OAuth, not a pasted key. So the engine is native and mirrors its mechanics.

## Not in scope

Workflows over a project (they are stored as Connect LabsRecords owned by an opportunity or
programme, which a CommCare-only user does not have); writing back to HQ; HQ user and
location data; UCR/exports/OData as sources; a second customer-DB engine (MySQL, SQL
Server).

## Risks

- `indexed_on` is mutable by HQ maintenance (a re-index moves it forward, so the row is simply
  seen again): idempotent upsert.
- Form v1 offset paging at depth: avoided by re-anchoring on `indexed_on`.
- The OAuth redirect URI must be registered on the HQ OAuth application. That is an HQ-side
  action outside this repo.
- A persistent store holds form data, which can be PHI. Same posture as
  `LabsLocalRecord` and `RawVisitCache` today, but longer-lived: the project page names the
  retention, the audit trail records access (`audit_trail`), and a project can be deleted,
  which deletes its rows.
