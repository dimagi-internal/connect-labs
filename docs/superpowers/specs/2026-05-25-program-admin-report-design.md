# Program Admin Report — Design

**Status:** Approved 2026-05-25 (brainstorming) · Awaiting implementation plan
**Authors:** jjackson + Claude
**Related:** `commcare_connect/workflow/templates/program_admin_audit.py` (the existing SEED scaffold, which this design supersedes)

---

## 1. Goal

Give a program administrator a single artifact that answers "is each network manager (NM) doing the weekly SOP for each of my opportunities, and following through on the work they create?" The report rolls up multiple opportunities' worth of saved workflow runs across an admin-chosen window, surfaces three completion KPIs per opp, and lets the admin drill into any one (opp, week) cell to see per-FLW decisions, the audits the NM raised, and the tasks the NM raised — all with live links into the underlying records.

It must work for **2 opps with 10 users** *and* **10 opps with 50 users**. The per-opp surface stays compact; per-FLW detail only appears in a drill panel scoped to a single run.

The report is itself a saved-runs workflow template, so program admins can review past reports unchanged ("here's what was true the week of Nov 10").

## 2. Out of scope

- **Cross-opp FLW comparisons.** Every FLW-level view stays scoped to one opp at a time. No "show me all Aminas across the program" pivot.
- **Decision creation in the program report itself.** The report reads decisions made in watched workflows; it does not let the admin create new decisions.
- **Custom KPI authoring.** The three KPIs (FLW-decision %, audit-complete %, task-complete %) are hard-coded for v1. Custom KPI definitions are a follow-up.
- **Auto-discovery of watched workflows.** Admin pins `(opportunity_id, workflow_definition_id)` pairs explicitly.

## 3. The `Decision` concept (first-class object)

A `Decision` is a record of "a judgment the NM made about an FLW during a workflow run." It's distinct from a `Task` (which is an action item with its own lifecycle) and an `AuditSession` (which is a review-of-evidence with its own lifecycle). A Decision can *spawn* zero, one, or many of those — and that causal trail is the Decision's whole point.

### 3.1 Why first-class, not embedded in workflow state

The two alternatives both have problems:

- **Embedded in workflow state.** Decisions become unaddressable outside the workflow run that produced them. Any future report ("median time-to-resolve per opp", "all decisions about gender skew this quarter") has to walk every saved-run snapshot to find them. Bad fit for the user's stated direction of "this is going to power key workflows and more reports on top of it over time."
- **Reuse `Task`.** Forcing "no issues confirmed" into the Task shape pollutes Task semantics — Task means "thing to do," and a no-issues confirmation is a judgment, not a to-do. A task list mixing "pending action" with "explicit non-action" is confusing.

`Decision` as its own `LabsLocalRecord(type="decision")` follows the same idiom as `Task` and `AuditSession`, queryable independently, with a back-pointer to the run that produced it.

### 3.2 Schema

```python
{
    "workflow_run_id": int,            # back-pointer (queryable)
    "opportunity_id": int,             # scoping
    "flw_id": str,                     # subject FLW (username)
    "reason_key": str | None,          # stable id ("bad_muac_distribution"), null for no_issues
    "reason_label": str | None,        # human label, null for no_issues
    "decision_type": "no_issues" | "action_taken",
    "kpi_snapshot": dict,              # frozen evidence at decision time, e.g.
                                       # {"gender_female_pct": 0.226, "muac_dist_score": 0.41}
    "audit_session_ids": list[int],    # may be empty
    "task_ids": list[int],             # may be empty
    "notes": str | None,
    "decided_at": ISO datetime,
    "decided_by": str | None,          # username of NM
}
```

### 3.3 Status & resolution are NOT in the Decision

Task status (`investigating` / `flw_action_in_progress` / `flw_action_completed` / `review_needed` / `closed`) and audit-session state live on the task/audit records themselves and are queried live by id. The Decision only records what was caused; lifecycle state-of-truth lives on the entity that owns it. This avoids state duplication and means resolution updates don't require touching the Decision.

### 3.4 Multiple decisions per (run, flw) allowed

The user may make more than one judgment about the same FLW within a single run (e.g. "no issues on MUAC" + "noticed gender skew, escalating"). Each is a separate Decision row.

### 3.5 `DecisionsDataAccess` + API

Mirrors `TasksDataAccess`. New endpoints:

- `POST /labs/workflow/api/<workflow_run_id>/decisions/` — create a Decision
- `GET /labs/workflow/api/<workflow_run_id>/decisions/` — list for a run
- `GET /labs/decisions/?opportunity_id=&reason_key=&decided_after=` — cross-run queries

All writes go through `LabsRecordAPIClient` → production ACL → recorded. Same trust boundary as Tasks; no Django shortcut.

## 4. Watched-workflow contract (what CHC nutrition needs to gain)

A workflow opts into being readable by the program report by:

### 4.1 Setting `supports_saved_runs: True`

`chc_nutrition_analysis` is currently a live dashboard (no saved runs). It gains:
```python
TEMPLATE = {
    ...
    "supports_saved_runs": True,
    "snapshot_inputs": {
        "pipelines": ["nutrition_metrics"],
        "workers": True,
        "state_keys": [],
    },
}
```

### 4.2 Adding three per-FLW buttons to render code

Each FLW row in the live render gains three buttons:

- **"No issues"** — POST a Decision with `decision_type: "no_issues"`, no `reason_key`. Button label uses "No issues" (not "Confirmed Good" — we're not asserting goodness, just absence of issues; matters especially when this gets automated).
- **"Create task"** — POST a Decision with `decision_type: "action_taken"`, picks reason_key from a small dropdown (`bad_muac_distribution`, `gender_skew`), and spawns a Task via the existing task-creation flow with `data.workflow_run_id` set. The Decision records the new task_id.
- **"Create audit"** — same, spawns an audit session, records `audit_session_ids: [new_id]`.

All three are link/POST patterns, never Python direct calls.

### 4.3 Saved-run mode swaps create-buttons for view-links

When `view.isCompleted` is true and a Decision exists for the row, the render uses `view.decisionsFor(username)` (a new framework-supplied helper, render-time read of snapshot/live data) to swap:

| Live mode | Saved-run mode |
|---|---|
| `[Create audit]` button | `[View audit #46]` link |
| `[Create task]` button | `[View task #123]` link (one per task) |
| `[No issues]` button | `🟢 No issues` pill (status, not actionable) |
| (no decision on this row) | "(no decision)" muted placeholder |

`view.decisionsFor()` is purely a read accessor — no ACL concern. The View-links go to existing audit/task detail URLs; the buttons being absent in completed mode is enforced both client-side (omitted from render) and server-side (decision-create endpoint refuses writes against a completed run).

## 5. The new `program_admin_report` template

New file: `commcare_connect/workflow/templates/program_admin_report.py`. Replaces the existing `program_admin_audit.py` scaffold (which was created 2026-05-05 as an ACE Phase 6 SEED but its cross-workflow snapshot reader was never wired). **Action:** delete `program_admin_audit.py` and its test file as part of this work — it has no live workflow instances depending on it (confirmed: the SEED template was never instantiated against a real opportunity).

### 5.1 Template config

```python
TEMPLATE = {
    "key": "program_admin_report",
    "name": "Program Admin Report",
    "multi_opp": True,
    "supports_saved_runs": True,
    "snapshot_inputs": {
        "pipelines": [],
        "workers": False,
        "state_keys": ["watched_summary"],
    },
    "definition_config": {
        "watched_sources": [
            {"opportunity_id": 10001, "workflow_definition_id": 47},
            {"opportunity_id": 10002, "workflow_definition_id": 48},
        ],
        "window_start": None,    # set per-run when admin starts a run
        "window_end":   None,
    },
}
```

### 5.2 Window scoping (Approach C: explicit per-run)

Each program-report run takes explicit `window_start` and `window_end` inputs when the admin starts the run. These are frozen on the run record at creation time. The build-snapshot hook collects every completed watched run whose `completed_at` falls in `[window_start, window_end]`.

This supports both routine weekly use ("last week") and longitudinal analysis ("last 3 months") with a single mechanism, and the report is self-explanatory about its time scope.

### 5.3 Snapshot hook

A `build_snapshot` hook (server-side, runs at completion AND at every render in `in_progress` mode) produces:

```python
state["watched_summary"] = [
    {
        "opportunity_id": int,
        "workflow_definition_id": int,
        "opportunity_name": str,
        "network_manager_name": str,
        "flw_count": int,
        "expected_run_dow": "monday",
        "window_start": date, "window_end": date,
        "runs": [
            {
                "id": int,
                "completed_at": ISO datetime,
                "lateness_days": int,           # 0 if on the expected day
                "kpis": {
                    "flw_decision_pct": float,  # # FLWs reviewed / # FLW roster
                    "audits_completed_pct": float,
                    "tasks_completed_pct": float,
                },
                "decisions": [                  # frozen with resolved status at completion time
                    {
                        "id": int,
                        "flw_id": str,
                        "reason_key": str | None,
                        "reason_label": str | None,
                        "decision_type": "no_issues" | "action_taken",
                        "audit_outcomes": [{"id": int, "status": str, "pass_count": int, "fail_count": int, "pending_count": int}],
                        "task_outcomes":  [{"id": int, "status": str, "official_action": str | None, "closed_at": ISO | None}],
                    },
                    ...
                ],
                "workers": [...],                # FLW roster at run time
            },
            ...
        ],
        "aggregates": {
            "expected_runs": int,                # count of Mondays in window
            "actual_runs": int,
            "avg_flw_decision_pct": float,
            "avg_audits_completed_pct": float,
            "avg_tasks_completed_pct": float,
            "outcome_mix": {"satisfactory": int, "warned": int, "suspended": int, "none": int, "open": int},
        },
    },
    ...
]
```

The hook does the join: for each watched run's decisions, it looks up the current Task status (`status`, `resolution_details.official_action`, `events`) and AuditSession status, and embeds them in the snapshot. This is the only place those joins happen — the render code reads `state.watched_summary` and is offline-renderable.

**Why this differs from the watched-workflow snapshot policy.** §3.3 says Decisions don't store task/audit status — those live on the entity and are queried live. That applies to the *watched* workflow's snapshot. The *program report* must freeze resolved status at completion because the whole point of "open last week's program report" is to see what was true *then*, not now. So the watched-workflow snapshot stores ids only (live-read); the program-report snapshot stores ids + resolved status at completion (frozen). The two policies serve different historical needs.

### 5.4 Why a `build_snapshot` hook and not the manifest

The default `snapshot_inputs`-manifest path only captures verbatim state/pipelines/workers. It can't query Decision/Task/AuditSession records or compute rollups. The hook is the right tool because we're doing a cross-model join.

### 5.5 Cross-workflow snapshot reader

New helper in `commcare_connect/workflow/data_access.py`:

```python
def get_saved_runs_for_program_report(
    *,
    watched_sources: list[dict],
    window_start: datetime,
    window_end: datetime,
) -> list[dict]:
    """For each (opp_id, workflow_definition_id), return ALL completed runs
    whose completed_at falls in [window_start, window_end]."""
```

Called by the `build_snapshot` hook. Generic — usable by any future program-report-style template.

## 6. UI design (v7 — approved)

### 6.1 Grid view (default landing)

- Rows = watched opportunities
- Columns = weeks in window + one **window-aggregate** column pinned on the right
- Each weekly cell is a card showing:
  - Status pill: `✓ RAN` (Mon HH:MM) or `⚠ NO RUN`
  - Three mini KPI bars: FLW decisions %, Audits %, Tasks %
- Aggregate column card shows:
  - Run-compliance pill (`✓ SOP MET` / `⚠ BELOW SOP`)
  - Three rolled-up KPIs
  - Outcome mix bar (satisfactory / warned / suspended counts)

Grid width: ~200px per cell. 10 opps × 4 weeks + agg ≈ 1100px wide — fits standard screens.

### 6.2 Cell drilldown (inline expansion)

Clicking any cell in a row:
- Gives the clicked cell an indigo border + "SELECTED" tag
- Opens a full-width detail panel attached to the bottom of that row (visually like an accordion)
- Other rows unaffected; grid stays visible

The detail panel:
- **Header**: `Opp · Week · Date · Run #id · NM name · Completed timestamp (+ lateness)` and a primary `↗ Open the run` button (links to the actual saved workflow run page, where render code uses the saved-runs view-mode button transformations)
- **4-column FLW table**:
  - Col 1: FLW name + username
  - Col 2: **Decision** (badge: `✓ No issues` / `⚠ <reason_label>`)
  - Col 3: **Audits** — status pill + audit-id link + tiny pass/fail count (e.g. "5 photos · 2 ✓ · 2 ✗ · 1 pending"). No embedded images.
  - Col 4: **Tasks** — status pill + task-id link + age or close-time. No embedded status pipelines.
- **Sort order**: action FLWs first (soft amber background), no-issues rows after
- **Outcome footer**: rolls up across all audits and tasks in the run (e.g. "1 audit in review · 1 task satisfactory · 1 task in progress")

### 6.3 Density behavior

- 2 opps × 10 FLWs: detail panel shows all 10 rows fully expanded.
- 10 opps × 50 FLWs: detail panel collapses no-issues rows into a single "N FLWs · No issues [expand]" row by default; action rows always shown individually. (Threshold tunable; start at 20 FLWs.)

### 6.4 NO-RUN cell

When a watched workflow has no completed run in the cell's week: cell shows `⚠ NO RUN · SOP missed` with red border. Clicking still opens a (mostly empty) detail panel with a "Start a new run for this opp/week" link.

## 7. Synthetic generator additions

### 7.1 New manifest section (Approach A: per-FLW timeline)

```yaml
workflow_runs:
  - template_key: chc_nutrition_analysis
    cadence: weekly
    start_week: 1
    decisions_for:
      amina:
        - week: 1
          decision_type: action_taken
          reason_key: bad_muac_distribution
          spawn_audit: true
          spawn_task:
            title: "Address bad MUAC photo pattern — coaching"
            ocs_persona: gentle_coach
            resolution_week: 3
            official_action: warned
        - week: 3
          decision_type: action_taken
          reason_key: gender_skew
          spawn_task:
            title: "Visit gender split — coaching"
            resolution_week: null    # left open
      fatima:
        - week: 1
          decision_type: action_taken
          reason_key: gender_skew
          spawn_task:
            title: "Visit gender split — coaching"
            resolution_week: 2
            official_action: satisfactory
      # Default for any (flw, week) pair not explicitly listed: a no_issues
      # Decision is emitted for that FLW for that run. This way listed FLWs
      # override the default per-week, and the synthetic FLW-decision-%
      # naturally lands at 100% unless a manifest also sets `skip_decisions_for`.
program_admin_report_runs:
  - watched_sources:
      - {opportunity_id: 10001, workflow_definition_id: __auto__}
      - {opportunity_id: 10002, workflow_definition_id: __auto__}
    cadence: weekly
    windows:
      - {week: 1, span_days: 7}
      - {week: 2, span_days: 14}
      - {week: 3, span_days: 21}
```

### 7.2 New generator module

`commcare_connect/labs/synthetic/generator/workflows.py`:

- `build_workflow_definitions(...)` — emit `LabsLocalRecord(type="workflow_definition")` for each `template_key` referenced
- `build_workflow_runs(...)` — emit `LabsLocalRecord(type="workflow_run")` with `status="completed"`, `completed_at` for each cadence Monday
- For each run, emit `LabsLocalRecord(type="decision")` per FLW based on `decisions_for[flw_id]`
- For decisions that spawn audits/tasks, emit those records too with `data.workflow_run_id` pointing back
- Resolution-week semantics: `resolution_week=N` flips the task to `closed` with given `official_action` and `closed_at` = that Monday
- Program-report runs use the same mechanism; their `build_snapshot` runs against the fabricated watched data

### 7.3 Existing `synthetic_generate_from_manifest` response gets new fields

```json
{
  "workflow_definitions_created": int,
  "workflow_runs_created": int,
  "decisions_created": int,
  ...
}
```

## 8. Demo data shape

Two new labs-only synthetic opps (clones of opp 10000 with different FLW personas + dates). For each:

- 3 weekly chc_nutrition_analysis saved runs (Wk 1, 2, 3 Mondays)
- Each run records decisions per FLW per the manifest's per-FLW timeline
- Audits + tasks created where decisions called for them; some get resolved by later weeks

Plus 3 program-admin-report runs (one per week, increasing window each time) that show the report's longitudinal view.

The Southern Cluster opp deliberately has a missed Wk 2 + an open task to give the demo something visibly imperfect to draw the eye.

## 9. Implementation phases (high-level — detailed in writing-plans)

1. **`Decision` model + data access + API** — independent of any UI work
2. **CHC nutrition gains `supports_saved_runs` + the three buttons + `view.decisionsFor` framework helper**
3. **`program_admin_report` template + cross-workflow reader + `build_snapshot` hook**
4. **Synthetic generator: `workflows.py` module + new manifest section**
5. **Two new synthetic opps + program-report demo data**
6. **End-to-end verification: open grid, drill down, verify links work, freeze a run, reopen and confirm read-only**

## 10. Open follow-ups (not in v1)

- Custom KPI authoring per program
- Auto-discovery of watched workflows by template_key
- "Last N weeks" preset windows in the new-run dialog
- Per-FLW pivot ("show me Amina's decisions across all weeks") — would live in a separate FLW dossier report, not this one
- Email digest / scheduled program-report runs
- Comparison view: "this report vs. previous report"
