# MBW Auditing V4 — Design Document

> Status: Implementation complete (Goal 1). Server-side pipeline architecture implemented.

---

## 1. Overview

MBW Auditing V4 is a workflow template for the bi-weekly FLW audit cycle. Every two weeks, a PM triggers a new audit run, reviews ~98 active FLWs against a set of audit metrics, triggers OCS Audit Bot tasks for flagged FLWs, monitors improvement over ~7 days, then assigns a final performance category and concludes the run.

**Files:**
- `connect_labs/workflow/templates/mbw_auditing_v4.py` — Python template (DEFINITION, PIPELINE_SCHEMAS, TEMPLATE export)
- `connect_labs/workflow/templates/mbw_auditing_v4_render.js` — JSX render code (~1,000 lines)

---

## 2. Architecture

### 2.1 Backend: New `mbw_auditing_v4` Job Handler (server-side pipeline)

V4 uses a new `mbw_auditing_v4` job handler in `connect_labs/workflow/job_handlers/mbw_auditing_v4.py`. Unlike the old `mbw_monitoring` handler, it fetches all pipeline data **server-side** using `PipelineDataAccess.execute_pipeline_from_schema()` — the browser sends only a minimal job config (no 73MB pipeline data round-trip).

The browser sends:
```
job_type: "mbw_auditing_v4"
active_usernames: [list of all worker usernames]
flw_names: { username → display_name }
flw_statuses: { username → {result, notes} }
opportunity_id: int
task_filters: { username → triggered_at_iso }  # Optional: Tab 2 only
```

The handler fetches all three pipeline schemas server-side, runs the analysis (reusing MBW Monitoring analysis functions), and returns per-FLW summaries directly:
```
flw_summaries: [{username, display_name, gs_score, followup_rate, pct_still_eligible,
                  ebf_pct, revisit_dist, meter_per_visit, dist_ratio, minute_per_visit,
                  num_mothers, num_mothers_eligible}]
```

All 8 metrics are computed server-side (no client-side metric assembly). The render code only merges `last_active` from the `workers` prop. Progress streams via SSE (`actions.streamJobProgress`).

### 2.2 Pipelines (3 sources, identical to MBW Monitoring V2)

| Alias | Source | Purpose |
|---|---|---|
| `visits` | CommCare Connect CSV | Per-visit data: GPS, breastfeeding status, case linking |
| `registrations` | CCHQ `Register Mother` forms | Mother metadata: expected visits, names, connect user ID |
| `gs_forms` | CCHQ `Gold Standard Visit Checklist` forms | GS scores: assessor name, date, score per mother case |

All three use `terminal_stage: "visit_level"` to preserve visit-level rows (not aggregated). The job handler does all aggregation server-side.

### 2.3 Render Code Architecture

The render code follows a clean single-function pattern (per workflow template requirements):
- `function WorkflowUI(...)` with `var` declarations only (no `const`/`let`)
- Auto-runs job on load when pipelines are ready
- State management via `React.useState` pairs
- Persists worker results via `actions.saveWorkerResult`
- Persists previous metrics via `onUpdateState({ previous_metrics: {...} })`

### 2.4 State Stored in `instance.state`

| Key | Type | Purpose |
|---|---|---|
| `worker_results` | `{username → {result, notes}}` | Performance categories and notes per FLW |
| `task_states` | `{username → {task_id, status}}` | Task status per FLW |
| `previous_metrics` | `{username → {metric_key → value}}` | Baseline for change arrows, saved on run conclusion |
| `analysis_complete` | bool | Whether the job has run at least once |
| `analysis_ts` | ISO timestamp | When the last analysis ran |

---

## 3. Tabs

### Tab 1: Per FLW Audit Report
One row per FLW. Columns: FLW name + username, Last Active, # Mothers (eligible in brackets), 8 metric columns, Flag, Category, Notes, Task.

Filter bar: All / Red Flags / All Flagged / Has Task. Search box. Refresh Data button (re-runs job).

Rows sorted by flag severity descending by default (red → yellow → none).

### Tab 2: Improvement Within Audit
Same table, filtered to FLWs with any flag or open task. Change arrows compare to `previous_metrics` (same baseline as Tab 1, not a per-task baseline — see §4.2 for this design decision).

### Tab 3: Summary by Performance Band
4 summary cards (Eligible, Requires Improvement, Suspension, Uncategorized) + detail table. Reads from current `workerResults` in UI state. **Manual refresh only** — not reactive to category changes in Tab 1.

### Tab 4: Guide
Static content. Metric definitions, flag thresholds, performance category definitions, workflow overview.

---

## 4. Design Decisions — Review Required

These are areas where the requirements were not prescriptive and a specific implementation choice was made. Review each to confirm or adjust.

---

### 4.1 Flag Thresholds — RESOLVED

| Flag | Condition | Type |
|---|---|---|
| GS Score | < 50% | 🔴 Red |
| Follow-up Rate | < 50% | 🔴 Red |
| Follow-up Rate | 50–79% | 🟡 Yellow |
| % Still Eligible | < 50% | 🔴 Red |
| % Still Eligible | < 85% | 🟡 Yellow |
| EBF % | ≤ 30% OR > 95% | 🟡 Yellow |
| GPS Dist Ratio | < 1.0 | 🟡 Yellow |
| Any metric worsened | > 10% change since last run | 🟡 Yellow |

All confirmed by user on 2026-05-12.

---

### 4.2 Tab 2 "Since Task Triggered" — IMPLEMENTED

**Approach:** A "Compute Post-Task Metrics" button in Tab 2 triggers a second job call. Pipeline visit rows are filtered client-side to only rows after each FLW's task trigger date, then the `mbw_monitoring` job runs for flagged FLWs only. Results show metrics computed exclusively from post-task data, with change arrows comparing post-task vs. current-run values.

**Performance note:** This is a second full job execution, running on a subset of rows (flagged FLWs, post-task visits only). Typically ~5–15 flagged FLWs and fewer rows = faster than the full run, but still ~15–30 seconds. Triggered on demand only (button click).

**Task trigger date storage:** When the PM clicks "Trigger Task" for a FLW, `taskStates[username].triggered_at` is saved to `instance.state.task_states`. This persists across page loads.

**GPS metric limitation:** GPS distance metrics in Tab 2 are based only on post-task visits, which may be sparse if the FLW hasn't submitted many forms yet. This is by design — the point is to see post-task behavior.

---

### 4.3 GS Score Uses "Highest Value" — CONFIRMED

**Decision made:** GS Score per FLW = `Math.max(all_gs_scores_for_this_flw)`.

**Requirements said:** "GS Score (based on highest value)" — this is explicit in the requirements doc. The V2 dashboard used oldest/first value by default.

**Potential issue:** The GS score assessment is done by a supervisor filling a form per FLW in CommCare. If a supervisor accidentally submits an inflated score, using max could mask poor performance. Using "most recent" would be more operationally meaningful.

**Question:** Confirm that "highest value" is the intended behavior, or should it be "most recent"?

---

### 4.4 % Still Eligible Calculation — FIXED

**Previous bug (V2):** Denominator only checked `eligible_full_intervention_bonus=1`. Missing the `anc_visit_completion` condition.

**V4 fix:** The `aggregate_mother_metrics` function in `followup_analysis.py` sets `eligible: is_eligible` where `is_eligible = eligible_full_intervention_bonus == "1"` only. It also stores `anc_completion_date` in the drilldown row.

The render code now filters:
```javascript
var eligMothers = drilldown.filter(function(m) { return m.eligible && m.anc_completion_date; });
```

This adds the `anc_completion_date` check client-side. `anc_completion_date` is set from ANC Visit form rows in `_extract_per_mother_fields` and passed through `aggregate_mother_metrics`.

---

### 4.5 ⚠️ "Worsened" Flag for `meter_per_visit` and `minute_per_visit`

**Decision made:** For metrics where `higherBetter: null` (meter_per_visit, minute_per_visit), no worsening check is applied — these are treated as neutral metrics.

**Reasoning:** Visit duration and GPS travel distance don't have a clear "good" direction in isolation. Low meter/visit could mean clustering (bad) or dense caseloads (fine). High minute/visit could mean thorough visits (good) or inefficiency (bad).

**Question:** Should worsening on these two metrics trigger a yellow flag? If so, which direction is "worse"?

---

### 4.6 ⚠️ Performance Category Labels

**Decision made:** Three categories:
- `eligible_for_renewal` → "Eligible for Renewal"
- `requires_improvement` → "Requires Improvement"
- `suspended` → "Suspension"

**Requirements said:** "Eligible for renewal / Requires improvement / Suspension" — the labels from the requirements doc were used directly.

**V2 used:** `eligible_for_renewal`, `probation`, `suspended`. The new `requires_improvement` category will be a new value in the system — confirm that this won't conflict with existing worker_results records from V2 if the same opportunity is used.

---

### 4.7 "Conclude Run" Gate — IMPLEMENTED

"Conclude Run" button is disabled when any FLW has an open task. The tooltip on the disabled button reads "Close all tasks before concluding."

When all tasks are resolved, the button becomes active. The PM closes tasks in the task management interface, then comes back to this UI and clicks "Mark Resolved" on each task cell. Once all tasks are marked resolved, conclude becomes available.

---

### 4.8 Filters: "Keep All Current V1 Filters"

**Decision made:** The requirements say "keep all current filters from V1 MBW dashboard," but V1's filter bar could not be inspected. The implemented filters are:
- **Flag filter:** All / Red Flags / All Flagged / Has Task (pill buttons)
- **Search:** FLW name/username text search

**What's possibly missing:** V1 may have had additional filters (e.g., by organization, supervisor, visit type, date range). Without seeing V1, these could not be replicated.

**Question:** What filters existed in V1 that should be preserved?

---

### 4.9 Monthly Visit Schedule — IMPLEMENTED

Replicates the same table from MBW Monitoring V2. Rows = visit types (ANC, Postnatal, Week 1, Month 1, Month 3, Month 6). Columns = months derived dynamically from the scheduled visit dates in `followup_data.flw_drilldown`. Cells show completed / total scheduled visits (with toggle to percentages or isolated counts). Color coding: green ≥80%, yellow 50–79%, red <50%.

---

## 5. What Was Deliberately NOT Built (Per Requirements)

- **Overview tab** — removed as instructed
- **GPS Analysis tab** — removed as instructed
- **Follow-up Rate tab** — removed as instructed
- **FLW selection step** — V2 required the PM to select active workers before running. V4 auto-runs for all workers simultaneously (no selection step)
- **GPS maps / Leaflet drill-downs** — not in V4 requirements
- **Per-mother drilldown view** — not in V4 requirements

---

## 6. Integration Points

| Concern | Mechanism |
|---|---|
| Job execution | `actions.startJob('mbw_monitoring', ...)` — reuses existing handler |
| Category persistence | `actions.saveWorkerResult(instance_id, {username, result, notes})` |
| Task creation | `actions.openTaskCreator({username, title, description, priority, workflow_instance_id})` |
| Run conclusion | `actions.completeRun(instance_id, {overall_result, notes})` |
| Metric snapshots | `onUpdateState({previous_metrics: {...}})` on conclusion |
| Job progress | `actions.streamJobProgress(task_id, onMessage, onCancel, onComplete, onError, onTimeout)` |

---

## 7. Open Questions Summary

**Resolved (2026-05-12):**
- ✅ All flag thresholds confirmed (fu_red=50, fu_yellow=80, elig_red=50, elig_yellow=85, ebf ≤30/>95, dist_ratio 1.0, worsened 10%)
- ✅ Tab 2 "since task triggered" — implemented as second job call with client-side date filtering
- ✅ GS Score uses highest value — confirmed
- ✅ % Still Eligible denominator — fixed to include `anc_completion_date` check
- ✅ Monthly Visit Schedule — implemented matching V2 structure
- ✅ Conclude gate — blocks on open tasks, tooltip explains, PM uses "Mark Resolved"

**Still open:**
1. **V1 filters** (§4.8) — what additional filters existed in V1 beyond flag filter and search?
2. **`requires_improvement` category** (§4.6) — any conflict with existing `probation` records in V2 instances if the same opportunity is reused?
3. **Worsened metric flag for neutral metrics** (§4.5) — should `meter_per_visit` and `minute_per_visit` ever trigger a worsening flag, and in which direction?
