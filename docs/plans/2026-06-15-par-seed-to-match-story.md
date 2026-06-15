# PAR demo: seed data to match the story (DDD iteration 2)

**Goal:** flip the PAR walkthrough's concept verdict from FAIL (2/5) to converged by
making the seeded synthetic env match what the spec narrates. The render pipeline
already works (run `program-admin-report-2026-06-15-002`: 15/15 scenes, 0 required
failures). These are the concept-blocking, judge-identified gaps — all DATA/seed +
one template-render fix. No flow-logic changes.

## Changes (each maps to a judge finding)

1. **Missed week** (scenes 7 + 15 why_groundedness). One watched region must SKIP a
   run for one completed week so the grid renders a NO-RUN ("missed") card.
   - `weekly_runs.py`: allow a manifest-declared `missed_week_idxs` (per opp) — skip
     `create_backdated_workflow_run` for that (opp, week) and do NOT stamp
     `ctx.ids["run:{opp}:{monday}"]`.
   - `rollup.py` `_build_snapshot`: emit each source's `missed_week_idxs` into the
     snapshot so the template's `noRunCard()` path fires.
   - Manifest: declare the missed week on the *Southern* (incomplete) region, a week
     OTHER than the Jun-1 drill week (e.g. idx 1 / May 25) so the drill still works.

2. **SOP-MET region** (scene 15). Northern must genuinely clear the SOP → green
   "SOP MET" pill. Requires (a) the aggregate dedup fix (#5) so KPIs compute over one
   run/week, and (b) resolved-record reconciliation (#3) so Northern's audits/tasks
   read 100%.

3. **Reconcile resolved records** (the stale-reuse bug; underlies SOP-MET + a clean
   grid). `run_audits.py` and `tasks.py` reuse existing records by id and never
   upgrade status. On reuse, if the FLW's arc is resolved (`follow_up_outcome_week`
   set) but the existing audit is not `completed` / task not `closed`, rebuild it to
   the resolved archetype via `update_record`. (Mirror the create-path archetype
   choice already added in #575.)

4. **In-review audit mix** (scene 13). The incomplete-drill audit (ola, the
   `investigating` arc) must show a genuine mix (e.g. 2 Pass / 1 Fail / 2 Pending),
   not 5 Pending / 0 decided. Add a partially-decided archetype (e.g.
   `in_review_mixed`) in `archetypes.py` and select it in `run_audits.py` for the
   investigating-arc FLW instead of `pending_all_clean`.

5. **Aggregate dedup + "N/4 RUNS"** (scene 7/15 design_soundness, "12/4 RUNS").
   Template `program_admin_report.py` RENDER_CODE: `computeAggregate` / `aggregateCard`
   must dedup `source.runs` to one run per week (mirror `runForWeek`) before counting
   runs + KPIs, and surface the SOP threshold on the "BELOW"/"SOP MET" pill.
   (Edit the TEMPLATE FILE — the rollup ensurer re-pushes render code from it, so a
   live MCP edit would be overwritten.)

6. **Scene-14 task coherence**. The incomplete task's title/description must match its
   transcript (screening / household coverage), not "bad MUAC distribution". Edit the
   `ola_s` coaching arc / task seed text in the Southern manifest so all three fields
   describe one case.

## Acceptance

- `pytest commcare_connect/labs/synthetic/ensure/` green (update/extend tests for
  missed-week skip, reconciliation, in-review-mix archetype).
- After deploy + re-ensure: grid shows ≥1 NO-RUN cell, Northern aggregate = "SOP MET"
  (green), Southern = "BELOW", aggregate reads "N/4 RUNS" (N≈4 not 10-12), the
  incomplete audit shows a decided/undecided mix, the incomplete task's three fields
  agree.
- Re-render (record_video --manifest, labs rig) clean 15/15 → re-judge → concept ≥ 4
  / converged → `/canopy:ddd-upload`.

## Loop

labs PR (squash, "(#NNN)") → deploy from main → ECS COMPLETED → MCP
`synthetic_env_ensure env=program-admin-report` → render → dual-judge → upload.
