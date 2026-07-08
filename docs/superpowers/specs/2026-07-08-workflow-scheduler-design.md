# Lightweight Workflow Scheduler — Design

**Date:** 2026-07-08
**Status:** Approved design (pre-implementation)

## Goal

Let a schedulable workflow run itself on a recurring cadence with no user logged
in, reusing the "default run" already baked into the workflow. A workflow is
schedulable iff its template sets `supports_default_run: True` and implements a
`run_default` hook — the only workflows with a headless entry point. For every
other workflow the scheduling control simply does not appear.

Two user-facing surfaces:

1. **Schedule from the workflow list screen** — per-row control to enable / edit
   / remove a schedule, plus a visible indicator showing which workflows are
   scheduled and at what cadence. Not exposed inside a run.
2. **Manage all schedules** — a "Scheduled Workflows" card + page in Labs
   Explorer (`/labs/explorer/`), the existing backend-tools hub, listing every
   schedule with disable and delete controls.

## Background (what already exists — do not rebuild)

- **Default run:** `run_default_for_definition(definition, *, access_token, request=None, **kwargs)`
  in `connect_labs/workflow/templates/__init__.py` dispatches to the template's
  `run_default` hook (resolved via `definition.template_type` →
  `data.config.templateType`). Templates supporting it today:
  `program_audit_creator` and `weekly_dual_track_audit`. The hooks are
  **idempotent per window** (they reuse one run per window), so a repeated fire
  never double-creates.
- **Celery beat:** `django_celery_beat` is installed and
  `CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"`
  (`config/settings/base.py`). Established pattern: seed `PeriodicTask` (+
  `CrontabSchedule`) via a data migration (e.g.
  `opportunity/migrations/0105_purge_stale_opportunities_periodic_task.py`).
- **Unattended auth (the crux, already solved):**
  `connect_labs/labs/connect_tokens.py::get_valid_access_token(user)` returns a
  fresh Connect access token for a user, auto-refreshing from the persisted
  `UserConnectToken.refresh_token`. Its docstring explicitly names "future
  background jobs" as the caller. If the refresh token is dead it raises
  `ConnectReLoginRequired`. **We store only the owning user on a schedule — never
  a token.**
- **Workflow storage:** workflows are LabsRecords over an HTTP API (no local
  table). Runs are created via `WorkflowDataAccess.create_run(...)` which requires
  exactly one of `opportunity_id` / `program_id`.
- **Workflow list UI:** `WorkflowListView` (`connect_labs/workflow/views.py`),
  template `connect_labs/templates/workflow/list.html`, builds per-definition
  rows via `_build_workflow_row`; scoped opp-vs-program by `request.labs_context`.
- **Labs Explorer hub:** `/labs/explorer/` (`connect_labs/labs/explorer/`),
  landing template `connect_labs/templates/labs/explorer/index.html` (card grid),
  already hosts a Task Manager, Cache Manager, etc.

## Architecture decision

**Chosen: a `WorkflowSchedule` model + one seeded "tick" PeriodicTask.**

A single static `PeriodicTask` (seeded via migration) runs every 15 minutes,
scans `WorkflowSchedule` for due rows, and dispatches each. All schedule CRUD is
plain ORM.

Rejected alternative: one native `PeriodicTask` + `CrontabSchedule` per schedule.
`PeriodicTask` has nowhere to record last-run status/error, "which workflows are
scheduled" degrades to a task-name filter + kwargs parse, and orphaned
`CrontabSchedule` rows must be reaped. The model+ticker approach is lighter in
moving parts (one seeded task, not N dynamic ones), yields a clean model to power
the admin list, and makes the auth-expired state trivial to surface. 15-minute
granularity is immaterial for daily/weekly/monthly cadences.

## Data model

New Django model `WorkflowSchedule` in the `workflow` app (this is a real local
table — workflow *definitions* remain remote LabsRecords; the schedule only
references a definition by id).

| Field            | Type                                        | Notes |
|------------------|---------------------------------------------|-------|
| `definition_id`  | `IntegerField`                              | Workflow definition (LabsRecord id) |
| `opportunity_id` | `IntegerField(null=True)`                   | Exactly one of opp/program set (mirrors `create_run` XOR) |
| `program_id`     | `IntegerField(null=True)`                   | " |
| `owner`          | `ForeignKey(User, on_delete=CASCADE)`       | Whose token runs it |
| `cadence`        | `CharField(choices=daily/weekdays/weekly/monthly)` | `weekdays` = Mon–Fri |
| `hour`           | `PositiveSmallIntegerField` (0–23)          | Hour of day (UTC — see Open Questions) |
| `day_of_week`    | `PositiveSmallIntegerField(null=True)` (0–6)| Weekly only |
| `day_of_month`   | `PositiveSmallIntegerField(null=True)` (1–28)| Monthly only (cap at 28 to avoid missing months) |
| `enabled`        | `BooleanField(default=True)`                | |
| `next_run_at`    | `DateTimeField`                             | Computed on save + after each fire |
| `last_run_at`    | `DateTimeField(null=True)`                  | |
| `last_status`    | `CharField(choices=ok/failed/auth_expired/running, null=True)` | |
| `last_error`     | `TextField(blank=True)`                     | |
| `created_at`     | `DateTimeField(auto_now_add=True)`          | |

Constraint: unique on `(definition_id, opportunity_id, program_id, owner)` — one
schedule per (workflow, scope, owner). Re-enabling upserts the existing row.

`next_run_at` computation lives in a pure helper (`compute_next_run(schedule, from_dt)`)
so it is unit-testable in isolation.

## Execution flow

1. **Ticker** — seeded `PeriodicTask` → `run_due_workflow_schedules` (every 15
   min). Selects `enabled=True, next_run_at <= now`. For each: dispatch
   `run_scheduled_workflow.delay(schedule.id)` and set `next_run_at =
   compute_next_run(schedule, now)`. (Advance `next_run_at` at dispatch time, not
   in the worker, so a slow/failed run cannot cause a redispatch storm.)
2. **Worker** — `run_scheduled_workflow(schedule_id)`:
   - Load schedule (skip if gone/disabled).
   - `token = get_valid_access_token(schedule.owner)`.
   - Load the definition via `WorkflowDataAccess` scoped to the schedule's
     opp/program with that token.
   - Call `run_default_for_definition(definition, access_token=token, request=None)`.
   - Record `last_run_at=now`, `last_status="ok"`.
3. **Failure handling:**
   - `ConnectReLoginRequired` → `last_status="auth_expired"`, `enabled=False`
     (auto-disable — retrying a dead refresh token is pointless), `last_error` set.
   - Any other exception → `last_status="failed"`, `last_error` set (truncated),
     schedule stays enabled (transient failures retry next cadence).

## UI

### Workflow list screen (enable / edit / see status)

- Extend `_build_workflow_row` to include, per definition: `schedulable` (bool,
  from the template registry's `supports_default_run`) and `schedule` (the
  current `WorkflowSchedule` for this definition + context + user, or None).
- In `list.html`, for schedulable rows show:
  - If no schedule: a **"Schedule"** button opening a small inline form/modal
    (cadence select → daily / weekdays (Mon–Fri) / weekly / monthly; hour;
    day-of-week for weekly; day-of-month for monthly).
  - If scheduled: a badge like **"⏱ Weekly · Mon 06:00"** plus **Edit** and
    **Remove** actions. `auth_expired` renders as **"⚠ needs re-login"**.
- Scope (`opportunity_id` vs `program_id`) is taken from `request.labs_context`,
  matching how the list itself is scoped. Owner is `request.user`.

### Labs Explorer "Scheduled Workflows" page (manage all)

- New card in `connect_labs/templates/labs/explorer/index.html` (fa-clock icon),
  next to Task Manager.
- New route `/labs/explorer/schedules/` → `ScheduleListView`. Table columns:
  workflow name (resolved from the definition), scope (opp/program), owner,
  cadence, next run, last run + status. Row actions: **Disable/Enable** and
  **Delete**. Optional **Run now** (dispatch immediately) is a nice-to-have,
  deferred.

## Endpoints

Under the workflow app (`connect_labs/workflow/urls.py`):

- `POST api/<definition_id>/schedule/`  — create/update schedule (cadence, hour,
  day) for current context + user. Returns the schedule summary.
- `POST api/schedule/<schedule_id>/delete/` — delete.
- `POST api/schedule/<schedule_id>/toggle/` — enable/disable.

Explorer list page reads via the ORM directly (no API needed).

## Testing

- **Unit:** `compute_next_run` for each cadence (incl. `weekdays` skipping
  Sat/Sun — e.g. a Friday-afternoon fire rolls to Monday; month-end/day-of-month
  cap; and "already past today's hour" rolls to next period); ticker selects only
  `enabled=True, next_run_at<=now` and advances `next_run_at`;
  `run_scheduled_workflow` resolves token → calls `run_default_for_definition`
  (mocked) → records `ok`; `ConnectReLoginRequired` path sets `auth_expired` +
  disables; generic exception sets `failed` and leaves enabled.
- **View:** schedule create/update/delete/toggle endpoints (auth + scope); list
  screen renders the badge/button for schedulable rows only; Explorer schedules
  page renders rows. Follow the labs local-view test pattern (real Django view
  suite) where practical.

## Out of scope / deferred (YAGNI)

- Per-schedule window override (rely on `run_default`'s own default window).
- Raw-cron cadence (presets only).
- Multiple schedules per (workflow, scope, owner).
- Email/notification on run completion or failure.
- "Run now" button on the Explorer page (easy follow-up; not required v1).

## Open questions (resolve during implementation, non-blocking)

- **Timezone:** store/interpret `hour` as UTC for v1 (simplest, matches celery
  beat's UTC default). A per-schedule or per-user TZ is a later refinement.
