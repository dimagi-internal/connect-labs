# Audit Logging (HIPAA-bar)

Labs is not formally a HIPAA-covered system, but it displays health-program
data and we hold it to the HIPAA bar. This document is both the technical
reference for the `audit_trail` app and the written policy that
§164.308(a)(1)(ii)(D) (information system activity review) expects.

## What gets logged

Every event carries the ASTM E2147 / NIST SP 800-66 field set: **who** (user
FK + username/email snapshot), **what** (action + resource type + opaque
resource id + row count), **when** (UTC), **where from** (IP, user agent,
request id, path), **scope** (opportunity/program/organization ids), and
**outcome** (success/failure + HTTP status).

| Event | Trigger point |
| --- | --- |
| `list` / `read` / `create` / `update` / `delete` | The five `LabsRecordAPIClient` methods — covers every LabsRecord touch, production HTTP and labs-only synthetic alike (synthetic tagged `labs_only=true`) |
| `export` | `ExportAPIClient.paginate` — the bulk PHI path (visit form JSON, worker identities); one event per crawl, counting rows actually **transferred** (not the size of the dataset available — a partial read counts only what it received). A caller that samples rather than exports everything passes `partial_ok=True`, and its early stop is recorded as a **success** tagged `metadata.terminated = "early"`; an undeclared mid-stream teardown (client disconnect, timeout) stays a failure. See below. |
| `page_view` | Every authenticated HTML page render (middleware; htmx partials excluded) — makes a user's session fully reconstructable, including pages that touch no data. Hidden by default on the dashboard |
| `login` / `logout` / `login_failed` | Django auth signals (OAuth callback calls `auth.login`) |
| `access_denied` | Any 403 response (middleware) — repeated 403s against one scope are the classic snooping signature |
| `review` | The "Mark reviewed" action on the dashboard — reviews are themselves audit events |
| `canary` | Every 30 min from Celery beat — proves the pipeline is alive |

MCP tool calls are additionally logged to `MCPAuditLog` (tool-level); their
data touches flow through the same client choke points and land here too,
attributed via the PAT user with `source="mcp"`.

**Session reconstruction.** Every event carries the request's `path` and its
`query_string` — with **free-text parameter values redacted**
(`service.FREE_TEXT_PARAMS`: `q`, `search`, `notes`, ... — a search box may
contain typed PHI content, whereas `?username=`/`?entity_id=`/`?status=` are
identifiers and are kept verbatim). Filter the dashboard by username with
"Include page views" checked to replay a user's exact click-path — full URLs
interleaved with what each page actually read, exported, or changed. This
same redaction applies to what the Umami tracker sends (`beforeSend` hook),
so both stores hold the identical identifiers-not-content data class.

**Partial exports are not failures.** Several callers *sample* the visit
stream rather than export it — the audit-creation wizard's question-discovery
endpoints read a couple of hundred rows, stop as soon as the field set goes
stable, and abandon the page generator. Python signals that abandonment with
`GeneratorExit`, which is byte-for-byte what a genuine mid-download teardown
raises, so intent has to come from the caller: `paginate(partial_ok=True)`
means "stopping early is expected here," and the event is recorded as a
success carrying `metadata.terminated = "early"` plus the row count actually
transferred. Without the flag the same teardown is still a failure. Read the
distinction the boring way: a `failure` on an `export` means a bulk read
someone was attempting did not complete. Sampling never appears there —
which is what makes the failure column worth alarming on.

**Never log PHI content.** Events carry opaque identifiers only — no names,
form answers, or free text. This applies to `metadata` too. The audit log
itself is PHI-adjacent (it reveals who was served where), so it gets the same
protection as data: Dimagi-staff-only UI, read-only admin.

## Storage architecture

1. **Hot — Postgres `labs_audit_event`.** Append-only at the DB level: a
   `pgtrigger.Protect` trigger rejects UPDATE/DELETE. Serves the dashboard
   and "who touched X" queries.
2. **Warm — CloudWatch.** Each event is also emitted as a pure-JSON line on
   the `connect_labs.audit_trail.stream` logger → container stdout →
   `/ecs/labs-jj-web` / `/ecs/labs-jj-worker`. Independent copy (survives DB
   write failures) and the substrate for metric-filter alarms.
3. **Cold — S3 archive, 6-year retention.** Nightly Celery task
   (`archive_audit_events`) writes the previous UTC day as
   `audit-events/YYYY/MM/DD.jsonl.gz` plus a `.sha256` digest to the
   `AUDIT_TRAIL_ARCHIVE_BUCKET` bucket, which has **Object Lock (compliance
   mode, 6 years)** — nobody, including root, can delete or rewrite an
   archived day before 2032+. The digest is the batch tamper-evidence seal
   (we deliberately skip per-row hash chains — the Object-Locked digest gives
   equivalent evidence without serializing writes).

Retention: hot rows are pruneable after `AUDIT_TRAIL_HOT_RETENTION_DAYS`
(default 400) **only** for days whose archive object is verified present in
S3 (`prune_archived_events`, dry-run by default, uses `pgtrigger.ignore` —
the single sanctioned bypass of the append-only trigger).

## Review process (the part auditors actually ask about)

- **Cadence:** monthly, or after any security-relevant incident.
- **Reviewer:** a labs admin (Dimagi staff) — currently Jonathan Jackson.
- **Where:** `/labs/audit-trail/` — anomaly cards surface the exception
  reports: failed-login bursts, access-denied counts, data-op failures,
  export volume vs. 7-day baseline, off-hours activity (00–06 UTC), canary
  freshness, most-active users.
- **What to do:** scan the cards; drill into anything red/amber via filters;
  then click **Mark reviewed** with a note ("all clear" or findings). That
  records a `review` event — the review trail is itself append-only evidence
  the process runs.
- **Escalation:** a suspicious pattern (unexplained export spike, repeated
  `access_denied` from one user, off-hours bulk reads) goes to the labs owner
  and, if real-data exposure is possible, to Dimagi security.
- **Pipeline health is verified, not assumed:** the canary task alarms (ERROR
  log → Sentry) if no canary landed for 2 hours.

## Analytics store classification

The self-hosted Umami analytics DB holds the **same data class as the audit
log**: PHI-adjacent usage metadata — staff usernames (via `identify`), page
*paths* with opaque record ids, and feature-event names with resource types.
Two rules keep it that way:

- The tracker runs every URL through a `beforeSend` redaction hook
  (`labsAnalyticsBeforeSend`, mirroring `service.FREE_TEXT_PARAMS`):
  identifier params (`username`, `entity_id`, `status`, scope ids) pass
  through — they are the meaning of labs URLs — while typed free-text values
  (`q`, `search`, `notes`, ...) are replaced with `[redacted]` before
  anything leaves the browser.
- Server-side events carry `resource_type` + `labs_only` only.

Consequently, viewing analytics is viewing usage metadata, not PHI content —
but the store still gets audit-log-grade protection: first-party only, Umami
admin login (Secrets Manager) plus the Dimagi-admin-gated
`/labs/admin/analytics/` page. Staff reach the full Umami UI via the SSO
bridge (`/labs/admin/analytics/umami/`) — labs mints the admin JWT into
same-origin localStorage, so access rides labs OAuth, and both the summary
page and the bridge record `read` audit events on open.

## Operational notes

- `record()` and the middleware flush are best-effort by contract — a broken
  audit pipeline never blocks a user-facing action. DB insert failures still
  emit the CloudWatch JSON line.
- The middleware flushes **after** the view's `ATOMIC_REQUESTS` transaction,
  so events survive rolled-back requests and carry the final status code.
- Celery tasks get `source="celery"` context automatically (task_prerun
  signal), attributed to whoever enqueued them: `before_task_publish` stamps
  the acting user's id and username onto the message (`labs_actor_*` headers,
  never the email — Redis is not a place to persist personal data) and
  `task_prerun` reads them back. Beat- and script-published tasks have no
  publisher and stay unattributed; a task that knows better can still wrap
  work in `audit_context(user=...)`.
- The same context feeds **Sentry attribution**
  (`connect_labs/utils/sentry.py`, wired as `before_send`). Sentry's Django
  integration only attaches `request.user` under `send_default_pii=True`,
  which also ships cookies, bodies and client IPs — not acceptable this close
  to PHI — so labs attaches just the identity, from this contextvar. One
  definition of "who is acting" for both sinks: they cannot disagree.
  **User id and username only, never the email.** Sentry keys its Users count
  and per-user filtering off the id alone, so the email would only save a
  lookup, at the cost of staff contact details living in Sentry's store. Go
  from an id to a person via the labs admin, or pivot on `labs.request_id`
  into the audit trail — which holds the full record for the same action.
  Errors also carry `labs.source` / `labs.request_id` / `labs.path` tags, and
  `labs.opportunity_id` / `labs.program_id` where scoped, so a Sentry issue
  and the audit rows for the same action share a correlation id.
- Settings: `AUDIT_TRAIL_ARCHIVE_BUCKET` (unset ⇒ archive task no-ops),
  `AUDIT_TRAIL_HOT_RETENTION_DAYS` (default 400).
- Hygiene: never put identifiers in logged URLs or exception messages; Sentry
  receives INFO-level breadcrumbs, so app log lines must stay PHI-free too.
