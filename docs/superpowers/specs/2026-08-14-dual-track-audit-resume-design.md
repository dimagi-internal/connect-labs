# Resume/checkpoint capability for Weekly Dual-Track Audit jobs killed mid-batch

**Status:** Approved (design) — proceeding to implementation. User explicitly waived the
per-section approval gate and the written-spec review gate for this design; self-reviewed only.
**Date:** 2026-08-14

## Problem

`weekly_dual_track_audit`'s scheduled audit-creation job (`weekly_dual_track_audit_create`,
invoked via `run_workflow_job`) has no way to recover when the process running it dies partway
through a batch. PR #1136 (2026-07-xx) explicitly flagged this as out of scope: "This does not
add resume/checkpoint capability for a job that's genuinely killed mid-batch (e.g. by an ECS
deploy cutover) — that's a separate, larger piece of work." This spec is that work.

Three real production incidents surfaced this gap during one investigation:

- **Run 12709** (2026-08-11): failed instantly with "definition not found" — a *different* bug
  (opp-vs-program scoping), already fixed and deployed (PR #1163). Not a mid-batch kill, but
  the first thing that made this whole area worth examining.
- **Run 12931** (2026-08-13): died mid-batch with one opportunity's sessions created and the
  rest never attempted. `active_job.status` was `"failed"` with **no error message recorded** —
  a second, separate bug (the client wrote back the terminal status but not the reason), fixed
  and deployed (PR #1179).
- **Run 13364** (2026-08-14, live during this investigation): the real nightly 1am UTC fire.
  Started 01:05:52 UTC, heartbeat froze at 01:18:28 UTC after processing 470 of 1133 calls
  (41%). The freeze lines up exactly with an *unrelated* deploy's hard-cutover window
  (01:12:04–01:21:44 UTC, for PR #1178). Confirmed via `aws ecs stop-task` in
  `deploy-labs.yml`'s "Deploy services (hard cutover)" step: **every deploy unconditionally
  hard-kills every task on both `labs-jj-web` and `labs-jj-worker`, with no drain** — a
  deliberate tradeoff (a prior incident: 3 overlapping *graceful* rolling deploys left 0 healthy
  tasks behind the ALB → site-wide 503). The user manually stopped 13364 during this
  investigation once it was confirmed dead.

Once a run's `active_job` heartbeat goes stale, nothing corrects it automatically. A human has
to notice (via the error banner added in PR #1179), and even then, manually re-firing today
would duplicate every session that already succeeded — there is no way to know from the outside
which of the batch's calls already ran.

## Goals

- Detect a stale, incomplete `weekly_dual_track_audit` run automatically, without a human
  needing to open its page.
- Resume it without redoing already-successful work (no duplicate audit sessions).
- Bound the number of automatic attempts so a persistent (non-transient) failure surfaces
  clearly instead of retrying forever.

## Non-goals

- **Not every template.** Scoped to `weekly_dual_track_audit` only — the one template
  confirmed to hit this. Generalizing to every template on `run_workflow_job` would require
  auditing each existing job handler's idempotency first; several may not have the clean
  "checkpoint by existing side-effect" property this template does.
- **Not manually-created runs.** Only runs created by an enabled `WorkflowSchedule`'s fire are
  covered. The generic "+Create Run" button and ad-hoc `workflow_run_default` calls are not
  tracked by anything a sweep could revisit today, and a human is already at the keyboard for
  those (lower risk of going unnoticed).
- **Not finer-than-per-call checkpointing.** The checkpoint granularity is one (opportunity,
  track) call — a unit that either has a corresponding audit session or doesn't. If the process
  dies *mid-way through* a single call (partway through one session's image review), that
  session may be left partially reviewed and won't be revisited by this mechanism — it would
  need to be fixed by hand, using the per-tile Retry button or bulk "Run AI Review" action PR
  #1178 added. All three observed incidents (12931, 12783's early hiccup, 13364) died *between*
  calls, not mid-call, so per-call granularity matches the actual observed failure mode. Finer
  checkpointing is a non-trivial scope increase for a case not yet observed in practice.
- **Not decoupling from deploys.** Explored during this design (see below) and deliberately
  deferred as separate follow-on work, not folded into this spec.

## Relationship to PR #1178 and PR #1179

- **PR #1178** ("retry AI classifier gateway failures instead of dead-ending") added resilience
  *within* a single audit session's AI review: transient per-image HTTP failures now retry with
  backoff, and one extra per-image sweep runs before a session is marked complete. That's a
  different, lower layer — it helps when one HTTP call to the classifier gateway blips. It
  cannot help when the *entire process* running the batch is killed; there's no process left
  for a per-call retry to happen inside. This spec's resume mechanism operates one level up,
  and complements #1178: when a resumed call re-runs, it already gets #1178's per-image
  resilience for free.
- **PR #1179** (this same investigation) fixed the client from silently dropping the error
  message when a stale job got marked failed, and fixed the runs list to show the actually
  audited window instead of a frozen creation-time shell period. Neither adds resume — they
  make an already-dead run's state legible, which is a prerequisite for anyone (or anything)
  deciding whether to resume it.

## Why not decouple from deploys instead?

Investigated directly: `deploy-labs.yml`'s "Deploy services (hard cutover)" step calls
`aws ecs stop-task` unconditionally on every task in both `labs-jj-web` and `labs-jj-worker`,
then forces a new deployment — by design, to keep deploys fast and avoid a *slower* graceful
rolling update that caused a real outage previously (documented in the workflow's own
concurrency-group comment: three overlapping rolling deploys left 0 healthy tasks behind the
ALB on 2026-07-01). ECS's own graceful-shutdown window (`stopTimeout`) caps out around 120
seconds; observed dual-track batches take up to 2h42m (run 12783). There is no timeout setting
that reconciles "hard cutover in ~10s" with "let a multi-hour batch finish first." The only way
to make a scheduled batch genuinely immune to a deploy cutover is to run it as a standalone
one-off Fargate task (the same pattern migrations and the "Run Labs management command"
workflow already use) instead of inside the shared, deploy-cycled worker service — a real
infrastructure change (the app would need to launch ECS tasks via boto3 from inside Django,
handle launch failures, etc.), materially bigger and riskier than resume/checkpoint. Deliberately
scoped out of this spec; tracked as a separate future project if the team wants to pursue it.

## Architecture

### Making the handler idempotent by construction

In `weekly_dual_track_audit_create` (`connect_labs/workflow/job_handlers/weekly_dual_track_audit.py`),
before building the list of (opportunity, track) calls, fetch existing sessions for this
`run_id` via the existing `AuditDataAccess.get_sessions_by_workflow_run(run_id)` (already used
elsewhere for the same "what does this run already have" question — e.g. `build_snapshot`,
`reconcile_generation_api`). Build a set of `(opportunity_id, tag)` pairs that already have a
session, and skip those pairs when `build_track_audit_calls` would otherwise include them.

This makes *every* invocation of the handler safe to re-run for the same `run_id` — not a
special "resume mode," just how the handler always behaves now. A run that completes normally
on its first try pays one extra read (cheap, already-used call pattern) and skips nothing.

The handler's own summary counters (`successful`, `failed`, `sessions_created` in `last_batch`)
must reflect the run's *total* state after this invocation, not just what this invocation did —
recompute `sessions_created` from a final `get_sessions_by_workflow_run` count after the calls
loop, rather than tallying only the calls made in this specific invocation. Otherwise a resumed
run's `last_batch` would under-report, which the view-only summary (and any downstream consumer
of run state, e.g. the "FLW day-by-day perf report" this workflow feeds) would show incorrectly.

### Manual resume (the piece needed right now, for run 13364)

A new MCP tool, `workflow_resume_dual_track_run(run_id, opportunity_id | program_id)`:

1. Load the run and its definition (scoped by whichever owner kwarg is passed — same pattern
   as `workflow_run_default`).
2. Reject if `definition.template_type != "weekly_dual_track_audit"` — this tool only knows how
   to resume this one template.
3. Re-derive the same inputs the original fire used: `window_start`/`window_end` are already in
   `run.data.state` (written at `create_run` time, so they survive even a kill on the very first
   call — no need to recompute via `resolve_window`). Sampling/clustering overrides re-derive
   from the definition's pinned config via the existing `sample_overrides_for` /
   `clustering_overrides_for` — deterministic, same inputs produce the same overrides as the
   original fire.
4. Invoke `weekly_dual_track_audit_create` directly (via the existing `run_workflow_job` path,
   scoped to this run_id) — no new handler needed, because of the idempotency fix above.

This alone (without any sweep) is enough to safely finish run 13364 by hand today, and gives an
operator (or, later, the sweep) a single well-tested entry point for "resume this run."

### Detection: periodic sweep (Celery beat) — follow-on automation, not needed to unblock 13364

New task `sweep_stale_dual_track_runs`, registered in `CELERY_BEAT_SCHEDULE`
(`config/settings/base.py`) alongside the existing periodic jobs, ticking every 10 minutes.

Each tick:

1. For each `WorkflowSchedule.objects.filter(enabled=True)`: load its definition (scoped per the
   existing `_program_owner`-style opp/program resolution) and skip anything whose
   `template_type != "weekly_dual_track_audit"`.
2. `wda.list_runs(definition_id=sched.definition_id)`, take the most-recently-created run.
3. Read `state.active_job`. Eligible for resume if:
   - `status == "running"` and `active_job_age_seconds(active_job) > JOB_STALE_SECONDS` (reusing
     the *existing* helper/constant from `connect_labs/workflow/views.py` — one authoritative
     definition of staleness, not a second one that could drift from the UI's), **or**
   - `status == "failed"` and it hasn't exhausted its resume budget (below) — covers a run that
     died with a real exception too, not just a silent kill.
4. Check `state.get("resume_attempts", 0) < MAX_RESUME_ATTEMPTS` (constant, default 3). If
   exhausted, skip — already terminal, do not retry, do not reset.
5. If eligible: `dispatch resume for (run_id, owner_kwargs, attempt=resume_attempts + 1)`
   asynchronously (`.delay()`, not inline in the sweep task) — so one stuck run can't block the
   sweep's tick for every other schedule. Internally this calls the same resume path the manual
   MCP tool uses.

### Retry accounting

Stored on the run's own state (`state.resume_attempts`), not on `WorkflowSchedule` — it's a
property of one specific run's resume history, and a fresh run tomorrow starts with no
`resume_attempts` key at all (attempt 0). No new Django model or migration.

Once `resume_attempts >= MAX_RESUME_ATTEMPTS` and the run is still stale/incomplete, mark it
terminal: `active_job.status = "failed"`, `error` set to something explicit (e.g. "Exhausted 3
automatic resume attempts; still incomplete — manual investigation needed."), and
`active_job.resume_exhausted = true` so the sweep's own eligibility check skips it on every
future tick without a separate age-based cutoff.

## Data flow (concrete walkthrough, using run 13364's actual numbers)

1. 01:05:52 UTC — schedule fires, creates run 13364, `state = {window_start: "2026-08-13",
   window_end: "2026-08-13"}`.
2. Handler builds ~1133 calls, starts processing.
3. 01:12:04–01:21:44 UTC — unrelated deploy hard-cuts-over the worker. Process dies mid-batch;
   last heartbeat 01:18:28, ~470/1133 calls done.
4. `active_job.status` stays `"running"` forever — nothing writes to it again.
5. Manual resume (today, ahead of the sweep): operator calls
   `workflow_resume_dual_track_run(run_id=13364, program_id=217)`. Handler fetches existing
   sessions, finds ~470 (opportunity, track) pairs already covered, skips them, processes the
   remaining ~663.
6. [Once the sweep ships] the same sequence happens automatically: some tick after
   01:18:28 + 45min finds 13364 stale, dispatches the same resume path with `attempt=1`.
7. Handler finishes, writes `active_job.status = "completed"`, `last_batch.sessions_created`
   reflecting the *total* (pre-existing + newly created) session count.

## Error handling

- Sweep task: wrap each schedule's check in its own try/except, log and continue — matches the
  existing pattern in `run_due_workflow_schedules`. One broken schedule/run must never block the
  others.
- Resume path (manual tool or sweep-dispatched): inherits all of `run_workflow_job`'s existing
  error handling (per-call try/except inside the handler, outer except writing
  `status=failed, error=str(e)`). No new error paths needed.
- If `list_runs` or `get_run` fails (transient API error) during a sweep tick, skip that
  schedule this tick — picked up again next tick. Nothing gets written on a failed read, so
  there's no state-corruption risk from a flaky read.

## Testing

- Handler idempotency: given N calls and M pre-existing sessions matching M of the (opportunity,
  tag) pairs, exactly N−M calls get made; `last_batch.sessions_created` reflects the total.
- Manual resume tool: happy path (re-derives window/overrides correctly, skips done pairs,
  completes remaining); rejects non-`weekly_dual_track_audit` definitions; 404s on missing run.
- Sweep eligibility: mix of enabled/disabled schedules, `weekly_dual_track_audit` vs. other
  templates, fresh/stale/exhausted runs — exactly the right ones get a resume dispatched.
- Retry-budget exhaustion: attempt N+1 is not dispatched; run is marked `resume_exhausted` and
  left alone on every subsequent tick.

## Rollout

Shipping this itself requires a deploy — which, per the "why not decouple from deploys" section
above, could interrupt whatever's running at that exact moment (as one already did to 13364,
for an unrelated PR). No special deploy-time restriction is being added in this spec (that was
considered and explicitly deferred by the user in favor of shipping resume/checkpoint alone);
in practice, checking for an in-flight run first (the same read-only diagnostic pattern used
throughout this investigation) before triggering a deploy is a reasonable manual habit until
the sweep exists to self-heal any collision automatically.

**Implementation order:** ship the idempotency fix + manual resume tool first (unblocks 13364
today, and is the harder, more valuable half of this spec). The periodic sweep is a thin
automation layer on top of the same resume path, and can follow once the core is proven.
