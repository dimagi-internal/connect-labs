# KMC FLW Flag Report Template — Design

**Date:** 2026-03-07
**Status:** Approved
**Template key:** `kmc_flw_flags`

## Purpose

Replicate the KMC FLW Flag Report (PDF) as an interactive workflow template. Identifies FLWs with concerning performance patterns across 8 flags in three domains (case management, danger signs, weight tracking), then enables targeted audit creation with AI review for selected FLWs.

## Architecture

### Data Flow

```
CommCare Forms (Registration + Visit)
  ↓
Pipeline "flw_flags" (aggregated, GROUP BY username)
  → SQL: case counts, mortality, enrollment timing, danger sign rates
Pipeline "weight_series" (visit-level)
  → Per-visit: username, case_id, visit_date, weight
  ↓
React RENDER_CODE
  → Merges: flw_flags (pre-computed) + weight_series (client-side weight pair analysis)
  → Applies hardcoded thresholds → 8 boolean flags per FLW
  → Renders: KPI cards + sortable flag table + checkbox selection
  ↓
User selects flagged FLWs → "Create Audits"
  ↓
actions.createAudit() → per-FLW sessions, last week, weight image filter, scale_validation AI
```

### Why Two Pipelines

The `flw_flags` pipeline handles the 5 flags computable with standard SQL aggregation. Weight pair flags (wt_loss, wt_gain, wt_zero) require comparing consecutive visits per child (window functions), which the pipeline SQL builder doesn't yet support. `weight_series` provides raw visit-level weight data for client-side pair analysis.

## Pipeline #1: `flw_flags` (aggregated)

- **alias:** `flw_flags`
- **data_source:** `connect_csv`
- **grouping_key:** `username`
- **terminal_stage:** `aggregated`

### Fields

| Field | Aggregation | Source Path | Purpose |
|-------|-------------|-------------|---------|
| `total_cases` | count_distinct | `form.kmc_beneficiary_case_id` | Min case filter |
| `closed_cases` | filtered count_distinct | case_id WHERE case closed | Mortality denominator |
| `deaths` | filtered count_distinct | case_id WHERE child_alive='no' | Mortality numerator |
| `total_visits` | count | visit forms only | Visits flag |
| `avg_visits_per_case` | subquery | visits / distinct closed cases (non-mortality, last 50) | flag_visits |
| `pct_single_visit` | subquery | cases with exactly 1 visit / total cases | Display metric |
| `mortality_rate` | computed | deaths / closed_cases | flag_mort_low, flag_mort_high |
| `pct_8plus_days` | subquery | cases where reg_date - discharge_date >= 8 / total | flag_enroll |
| `danger_visit_count` | count | visit forms with danger_sign_positive field | flag_danger min threshold |
| `danger_positive_count` | filtered count | WHERE danger_sign_positive='yes' | flag_danger_high |
| `pct_danger_positive` | computed | danger_positive_count / danger_visit_count | flag_danger_high |

### Key CommCare Form Paths

**Registration form** (`58991FD0-F6A7-4DA2-8C74-AE4655A424A7`):
- `form.hosp_lbl.date_hospital_discharge` — Hospital discharge date
- `form.reg_date` — Registration date
- `form.case_close_condition` — Case close condition
- `form.child_alive` — Is baby alive (registration-time)

**Visit form** (`42DFAFE1-C3B5-4F11-A400-827DA369F2C9`):
- `form.anthropometric.child_weight_visit` — Weight in grams
- `form.danger_signs_checklist.danger_sign_positive` — Computed danger sign flag
- `form.child_alive` — Is baby alive (visit-time)
- `form.kmc_beneficiary_case_id` — Links visit to beneficiary case
- `form.grp_kmc_visit.visit_number` — Visit number
- `form.grp_kmc_visit.visit_date` — Visit date
- `form.grp_kmc_beneficiary.reg_date` — Registration date (from case)

## Pipeline #2: `weight_series` (visit-level)

- **alias:** `weight_series`
- **data_source:** `connect_csv`
- **grouping_key:** `username`
- **terminal_stage:** `visit_level`

### Fields

| Field | Path | Transform |
|-------|------|-----------|
| `username` | (built-in) | — |
| `beneficiary_case_id` | `form.kmc_beneficiary_case_id` | — |
| `visit_date` | `form.grp_kmc_visit.visit_date` | date |
| `weight` | `form.anthropometric.child_weight_visit` | float |

Client-side computation in React:
1. Group by FLW → by child → sort by date
2. For each consecutive pair: compute weight_diff and days_diff
3. Aggregate per FLW: pct_wt_loss, mean_daily_gain, pct_wt_zero

## UI Layout

### KPI Cards (top row)

```
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  40 FLWs     │ │  11 Flagged  │ │  34 Excluded  │ │  3,471 Cases │
│  Analyzed    │ │  (2+ flags)  │ │  (<20 cases)  │ │  Total       │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
```

Cards use the same style as KMC longitudinal template (colored borders, bold numbers).

### Filter Bar

```
[All FLWs ▾] [Flagged Only] [2+ Flags] [Search: ___________]
```

### Flag Table

```
☑ │ FLW    │ Cases │ Avg Vis │ Mort%  │ 8+Days │ Danger │ Wt Loss │ Gain  │ Wt Zero │ Flags
──┼────────┼───────┼─────────┼────────┼────────┼────────┼─────────┼───────┼─────────┼──────
☑ │ 2935   │ 23    │ 2.65*   │ 0.0%*  │ NE     │ 0.0%*  │ NE      │ NE    │ NE      │ 3
☐ │ 2198   │ 38    │ 1.83*   │ 0.0%*  │ 80.0%* │ 10.0%  │ NE      │ NE    │ NE      │ 3
```

- Flagged cells: red/pink background (matching PDF)
- "NE" when min cases not met
- Sortable by any column (default: flags descending)
- Select-all checkbox in header
- Row highlight on selection

### Action Bar (sticky bottom)

```
┌─────────────────────────────────────────────────────────┐
│ 2 FLWs selected  │  [Create Audits with AI Review]     │
│                   │  Last week · Weight images · AI     │
└─────────────────────────────────────────────────────────┘
```

## Flag Thresholds (hardcoded)

```javascript
const THRESHOLDS = {
  visits: 3.0,           // avg visits < 3.0
  mort_low: 0.02,        // mortality < 2%
  mort_high: 0.20,       // mortality > 20%
  enroll: 0.35,          // 8+ days enrollment > 35%
  danger_high: 0.30,     // danger sign positive > 30%
  danger_zero_min: 30,   // zero across 30+ visits
  wt_loss: 0.15,         // weight loss pairs > 15%
  wt_gain: 60,           // daily gain > 60 g/day
  wt_zero: 0.30,         // zero change pairs > 30%
};

const MIN_CASES = {
  visits: 10,            // 10 closed cases
  mortality: 20,         // 20 closed cases
  enroll: 10,            // 10 enrollment records
  danger: 20,            // 20 followup visits
  danger_zero: 30,       // 30 followup visits
  weight: 10,            // 10 weight pairs
};
```

## Audit Creation

When user clicks "Create Audits with AI Review":

1. Calls `actions.createAudit()` with:
   - `opportunities: [{ id: instance.opportunity_id }]`
   - `criteria.audit_type: 'date_range'`
   - `criteria.granularity: 'per_flw'`
   - `criteria.start_date / end_date`: last Monday–Sunday
   - `criteria.related_fields`: weight image + reading (same as weekly audit)
   - `ai_agent_id: 'scale_validation'`
   - `workflow_run_id: instance.id`
   - `selected_flw_user_ids`: usernames from checked rows
2. Streams progress via `actions.streamAuditProgress()`
3. Shows linked audit sessions table below the flag table when complete

## Template Structure

Single file: `connect_labs/workflow/templates/kmc_flw_flags.py`

```python
TEMPLATE = {
    "key": "kmc_flw_flags",
    "name": "KMC FLW Flag Report",
    "description": "Identifies FLWs with concerning performance patterns. Select flagged FLWs to create targeted audits with AI review.",
    "icon": "fa-flag",
    "color": "red",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
```

## Pipeline Extensions Required

The current pipeline SQL builder may need extensions for:
1. `count_distinct` aggregation type (currently defaults to MIN)
2. Subqueries for per-case metrics aggregated to per-FLW level
3. Filtered count_distinct (cases WHERE condition)

These are additive changes to `query_builder.py` that don't break existing pipelines.

## Out of Scope

- Multi-opportunity support (use separate workflow runs per opportunity)
- Configurable thresholds UI (thresholds are constants, edit template to change)
- Historical flag trend tracking
- Export to PDF
