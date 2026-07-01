# KMC FLW Flag Report Template — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a workflow template that computes 8 FLW performance flags from CommCare data and enables targeted audit creation with AI review for selected FLWs.

**Architecture:** Two-pipeline template (`kmc_flw_flags.py`). Pipeline #1 (`flw_flags`, aggregated) computes per-FLW metrics via SQL GROUP BY. Pipeline #2 (`weight_series`, visit-level) provides raw weight data for client-side consecutive-pair analysis. React renders KPI cards + sortable flag table with checkboxes. Audit creation reuses the existing `actions.createAudit()` flow.

**Tech Stack:** Python (pipeline config), PostgreSQL (SQL aggregation), React/JSX (RENDER_CODE via Babel), Playwright (E2E testing)

**Design doc:** `docs/plans/2026-03-07-kmc-flw-flags-design.md`

---

## Task 1: Add `count_distinct` aggregation to pipeline query builder

The pipeline needs `COUNT(DISTINCT value)` for counting unique beneficiary cases per FLW. Currently `count_unique` falls through to `MIN()`.

**Files:**
- Modify: `connect_labs/labs/analysis/backends/sql/query_builder.py:167-193`
- Test: `connect_labs/labs/tests/test_query_builder.py` (create)

**Step 1: Write failing test**

```python
# connect_labs/labs/tests/test_query_builder.py
import pytest

from connect_labs.labs.analysis.backends.sql.query_builder import _aggregation_to_sql


class TestAggregationToSQL:
    def test_count_distinct(self):
        result = _aggregation_to_sql("count_distinct", "beneficiary_case_id", "total_cases")
        assert "COUNT(DISTINCT" in result
        assert "beneficiary_case_id" in result

    def test_count(self):
        result = _aggregation_to_sql("count", "visit_id", "total_visits")
        assert result == "COUNT(visit_id)"

    def test_first_uses_subquery(self):
        result = _aggregation_to_sql("first", "weight", "first_weight")
        assert "ORDER BY visit_date ASC" in result
        assert "LIMIT 1" in result

    def test_unknown_falls_to_min(self):
        result = _aggregation_to_sql("bogus", "val", "field")
        assert result == "MIN(val)"
```

**Step 2: Run test to verify it fails**

Run: `pytest connect_labs/labs/tests/test_query_builder.py -v --ds=config.settings.local -o "addopts="`
Expected: `test_count_distinct` FAILS (count_unique falls through to MIN)

**Step 3: Implement count_distinct**

In `query_builder.py`, add before the `else: return MIN` fallback (around line 190):

```python
    elif agg == "count_distinct" or agg == "count_unique":
        return f"COUNT(DISTINCT {value_expr})"
```

Also add `"last"` while we're here (mirror of "first" with DESC):

```python
    elif agg == "last":
        return f"""(
            SELECT sub.val FROM (
                SELECT {value_expr} as val, visit_date
                FROM labs_raw_visit_cache sub
                WHERE sub.opportunity_id = labs_raw_visit_cache.opportunity_id
                  AND sub.username = labs_raw_visit_cache.username
                  AND {value_expr} IS NOT NULL
                ORDER BY visit_date DESC
                LIMIT 1
            ) sub
        )"""
```

**Step 4: Run tests to verify they pass**

Run: `pytest connect_labs/labs/tests/test_query_builder.py -v --ds=config.settings.local -o "addopts="`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add connect_labs/labs/tests/test_query_builder.py connect_labs/labs/analysis/backends/sql/query_builder.py
git commit -m "feat: add count_distinct and last aggregation types to pipeline query builder"
```

---

## Task 2: Add per-field filter support for conditional aggregation

Several flag metrics require conditional counting (e.g., count distinct cases WHERE child_alive='no'). Add an optional `filter` on FieldComputation that generates PostgreSQL `FILTER (WHERE ...)` clauses.

**Files:**
- Modify: `connect_labs/labs/analysis/config.py:62-145` (FieldComputation)
- Modify: `connect_labs/labs/analysis/backends/sql/query_builder.py:167-193`
- Modify: `connect_labs/workflow/data_access.py:1709-1721` (_schema_to_config field parsing)
- Test: `connect_labs/labs/tests/test_query_builder.py` (extend)

**Step 1: Write failing test**

Add to `test_query_builder.py`:

```python
from connect_labs.labs.analysis.config import FieldComputation


class TestFilteredAggregation:
    def test_count_distinct_with_filter(self):
        """COUNT(DISTINCT case_id) FILTER (WHERE child_alive = 'no')"""
        field = FieldComputation(
            name="deaths",
            path="form.kmc_beneficiary_case_id",
            aggregation="count_distinct",
            filter_path="form.child_alive",
            filter_value="no",
        )
        result = _aggregation_to_sql(
            field.aggregation,
            "COALESCE(form_json->'form'->>'kmc_beneficiary_case_id', '')",
            field.name,
            filter_path=field.filter_path,
            filter_value=field.filter_value,
        )
        assert "FILTER" in result
        assert "child_alive" in result

    def test_count_without_filter(self):
        result = _aggregation_to_sql("count", "val", "field")
        assert "FILTER" not in result
```

**Step 2: Run test to verify it fails**

Run: `pytest connect_labs/labs/tests/test_query_builder.py::TestFilteredAggregation -v --ds=config.settings.local -o "addopts="`
Expected: FAIL — FieldComputation doesn't accept `filter_path`

**Step 3: Add filter fields to FieldComputation**

In `config.py`, add to FieldComputation class (after `extractor` field, around line 115):

```python
    filter_path: str = ""       # Optional: path for FILTER (WHERE ...) clause
    filter_value: str = ""      # Optional: value to compare against in filter
```

No changes needed to `__post_init__` validation — these are optional.

**Step 4: Update _aggregation_to_sql signature**

In `query_builder.py`, update the function signature and add FILTER clause generation:

```python
def _aggregation_to_sql(
    agg: str,
    value_expr: str,
    field_name: str,
    filter_path: str = "",
    filter_value: str = "",
) -> str:
    """Convert aggregation type to SQL aggregate function."""
    # Build the base aggregation
    if agg == "count":
        base = f"COUNT({value_expr})"
    elif agg == "sum":
        base = f"SUM({value_expr})"
    elif agg == "avg":
        base = f"AVG({value_expr})"
    elif agg == "count_distinct" or agg == "count_unique":
        base = f"COUNT(DISTINCT {value_expr})"
    elif agg == "first":
        # Subquery — filters not applicable
        return f"""(
            SELECT sub.val FROM (
                SELECT {value_expr} as val, visit_date
                FROM labs_raw_visit_cache sub
                WHERE sub.opportunity_id = labs_raw_visit_cache.opportunity_id
                  AND sub.username = labs_raw_visit_cache.username
                  AND {value_expr} IS NOT NULL
                ORDER BY visit_date ASC
                LIMIT 1
            ) sub
        )"""
    elif agg == "last":
        return f"""(
            SELECT sub.val FROM (
                SELECT {value_expr} as val, visit_date
                FROM labs_raw_visit_cache sub
                WHERE sub.opportunity_id = labs_raw_visit_cache.opportunity_id
                  AND sub.username = labs_raw_visit_cache.username
                  AND {value_expr} IS NOT NULL
                ORDER BY visit_date DESC
                LIMIT 1
            ) sub
        )"""
    elif agg == "list":
        base = f"ARRAY_AGG({value_expr}) FILTER (WHERE {value_expr} IS NOT NULL)"
        # list already has its own FILTER, so skip adding another
        return base
    elif agg == "min":
        base = f"MIN({value_expr})"
    elif agg == "max":
        base = f"MAX({value_expr})"
    else:
        base = f"MIN({value_expr})"

    # Apply optional FILTER clause
    if filter_path and filter_value:
        filter_sql = _jsonb_path_to_sql(filter_path)
        base = f"{base} FILTER (WHERE {filter_sql} = '{filter_value}')"

    return base
```

**Step 5: Update callers of _aggregation_to_sql**

In `query_builder.py`, find where `_aggregation_to_sql` is called (in `build_flw_aggregation_query` or similar) and pass the filter params from the FieldComputation:

```python
# Where fields are iterated to build SELECT expressions:
agg_sql = _aggregation_to_sql(
    field.aggregation,
    value_expr,
    field.name,
    filter_path=field.filter_path,
    filter_value=field.filter_value,
)
```

**Step 6: Update _schema_to_config in data_access.py**

In `workflow/data_access.py` around line 1709-1721, add filter_path and filter_value to the FieldComputation constructor:

```python
fields.append(
    FieldComputation(
        name=field_def["name"],
        path=field_def.get("path", ""),
        paths=field_def.get("paths"),
        aggregation=field_def.get("aggregation", "first"),
        transform=get_transform(field_def.get("transform")),
        description=field_def.get("description", ""),
        default=field_def.get("default"),
        filter_path=field_def.get("filter_path", ""),
        filter_value=field_def.get("filter_value", ""),
    )
)
```

**Step 7: Run tests**

Run: `pytest connect_labs/labs/tests/test_query_builder.py -v --ds=config.settings.local -o "addopts="`
Expected: ALL PASS

**Step 8: Commit**

```bash
git add connect_labs/labs/analysis/config.py connect_labs/labs/analysis/backends/sql/query_builder.py connect_labs/workflow/data_access.py connect_labs/labs/tests/test_query_builder.py
git commit -m "feat: add per-field filter support for conditional SQL aggregation"
```

---

## Task 3: Create template skeleton with PIPELINE_SCHEMAS

Create the template file with pipeline definitions and basic structure.

**Files:**
- Create: `connect_labs/workflow/templates/kmc_flw_flags.py`

**Step 1: Create the template file**

```python
# connect_labs/workflow/templates/kmc_flw_flags.py
"""
KMC FLW Flag Report — identifies FLWs with concerning performance patterns
across 8 flags in three domains: case management, danger signs, weight tracking.
Enables targeted audit creation with AI review for selected FLWs.

Reference: KMC_FLW_Flag_Report_Full.pdf (2026-03-07)
Design: docs/plans/2026-03-07-kmc-flw-flags-design.md
"""

# ---------------------------------------------------------------------------
# Pipeline Schemas
# ---------------------------------------------------------------------------

PIPELINE_SCHEMAS = [
    # Pipeline 1: FLW-level aggregated metrics
    {
        "alias": "flw_flags",
        "name": "FLW Flag Metrics",
        "description": "Per-FLW aggregated metrics for flag computation",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "aggregated",
            "fields": [
                # --- Case management metrics ---
                {
                    "name": "total_cases",
                    "paths": ["form.kmc_beneficiary_case_id", "form.case.@case_id"],
                    "aggregation": "count_distinct",
                    "description": "Total unique beneficiary cases",
                },
                {
                    "name": "closed_cases",
                    "paths": ["form.kmc_beneficiary_case_id", "form.case.@case_id"],
                    "aggregation": "count_distinct",
                    "filter_path": "form.case_close_condition",
                    "filter_value": "closed",
                    "description": "Unique cases that have been closed",
                },
                {
                    "name": "deaths",
                    "paths": ["form.kmc_beneficiary_case_id", "form.case.@case_id"],
                    "aggregation": "count_distinct",
                    "filter_path": "form.child_alive",
                    "filter_value": "no",
                    "description": "Unique cases where child died",
                },
                {
                    "name": "total_visits",
                    "path": "form.grp_kmc_visit.visit_number",
                    "aggregation": "count",
                    "description": "Total visit form submissions",
                },
                # --- Danger sign metrics ---
                {
                    "name": "danger_visit_count",
                    "path": "form.danger_signs_checklist.danger_sign_positive",
                    "aggregation": "count",
                    "description": "Total visits with danger sign assessment",
                },
                {
                    "name": "danger_positive_count",
                    "path": "form.danger_signs_checklist.danger_sign_positive",
                    "aggregation": "count",
                    "filter_path": "form.danger_signs_checklist.danger_sign_positive",
                    "filter_value": "yes",
                    "description": "Visits where danger sign was positive",
                },
                # --- Enrollment timing ---
                {
                    "name": "reg_date",
                    "paths": ["form.reg_date", "form.grp_kmc_beneficiary.reg_date"],
                    "aggregation": "first",
                    "description": "Registration date (for enrollment timing)",
                },
                {
                    "name": "discharge_date",
                    "path": "form.hosp_lbl.date_hospital_discharge",
                    "aggregation": "first",
                    "description": "Hospital discharge date",
                },
            ],
        },
    },
    # Pipeline 2: Visit-level weight data for consecutive pair analysis
    {
        "alias": "weight_series",
        "name": "Weight Series",
        "description": "Per-visit weight measurements for weight flag computation",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "linking_field": "beneficiary_case_id",
            "fields": [
                {
                    "name": "beneficiary_case_id",
                    "paths": ["form.kmc_beneficiary_case_id", "form.case.@case_id"],
                    "aggregation": "first",
                },
                {
                    "name": "visit_date",
                    "paths": ["form.grp_kmc_visit.visit_date"],
                    "aggregation": "first",
                    "transform": "date",
                },
                {
                    "name": "weight",
                    "paths": [
                        "form.anthropometric.child_weight_visit",
                        "form.child_details.birth_weight_reg.child_weight_reg",
                    ],
                    "aggregation": "first",
                    "transform": "float",
                },
                {
                    "name": "visit_number",
                    "paths": ["form.grp_kmc_visit.visit_number"],
                    "aggregation": "first",
                    "transform": "int",
                },
            ],
        },
    },
]


# ---------------------------------------------------------------------------
# Definition
# ---------------------------------------------------------------------------

DEFINITION = {
    "name": "KMC FLW Flag Report",
    "description": "Identifies FLWs with concerning performance patterns across case management, danger signs, and weight tracking. Select flagged FLWs to create targeted audits with AI review.",
    "version": 1,
    "templateType": "kmc_flw_flags",
    "statuses": [
        {"id": "pending", "label": "Pending Review", "color": "gray"},
        {"id": "audits_created", "label": "Audits Created", "color": "green"},
    ],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
    },
    "pipeline_sources": [],
}


# ---------------------------------------------------------------------------
# Render Code (React JSX)
# ---------------------------------------------------------------------------

RENDER_CODE = """
// PLACEHOLDER — implemented in Task 4-7
function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    return React.createElement('div', {className: 'p-4'},
        React.createElement('p', null, 'KMC FLW Flag Report — loading...')
    );
}
"""


# ---------------------------------------------------------------------------
# Template Export
# ---------------------------------------------------------------------------

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

**Step 2: Verify template auto-discovery**

Run Python to check the template is discovered:

```bash
python -c "from connect_labs.workflow.templates import list_templates; print([t['key'] for t in list_templates()])"
```

Expected: List includes `'kmc_flw_flags'`

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_flw_flags.py
git commit -m "feat: add kmc_flw_flags template skeleton with pipeline schemas"
```

---

## Task 4: Implement RENDER_CODE — data processing and flag computation

Write the core data processing functions that merge both pipeline results and compute all 8 flags.

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_flw_flags.py` (RENDER_CODE)

**Step 1: Replace RENDER_CODE placeholder**

Replace the RENDER_CODE string with the full JSX implementation. The code below shows the complete data processing section. The UI sections follow in Tasks 5-7.

```python
RENDER_CODE = r"""
function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {

    // ── Thresholds (matching KMC_FLW_Flag_Report_Full.pdf) ──────────
    const THRESHOLDS = {
        visits: 3.0,
        mort_low: 0.02,
        mort_high: 0.20,
        enroll: 0.35,
        danger_high: 0.30,
        danger_zero_min_visits: 30,
        wt_loss: 0.15,
        wt_gain: 60,
        wt_zero: 0.30,
    };
    const MIN_CASES = {
        visits: 10,
        mortality: 20,
        enroll: 10,
        danger: 20,
        danger_zero: 30,
        weight: 10,
    };

    // ── State ───────────────────────────────────────────────────────
    const [selectedWorkers, setSelectedWorkers] = React.useState({});
    const [selectAll, setSelectAll] = React.useState(false);
    const [filter, setFilter] = React.useState('all');
    const [sortKey, setSortKey] = React.useState('flag_count');
    const [sortAsc, setSortAsc] = React.useState(false);
    const [search, setSearch] = React.useState('');
    const [isRunning, setIsRunning] = React.useState(false);
    const [progress, setProgress] = React.useState(null);
    const [linkedSessions, setLinkedSessions] = React.useState([]);
    const cleanupRef = React.useRef(null);

    // ── Extract pipeline data ───────────────────────────────────────
    const flwRows = pipelines && pipelines.flw_flags ? (pipelines.flw_flags.rows || []) : [];
    const weightRows = pipelines && pipelines.weight_series ? (pipelines.weight_series.rows || []) : [];

    // ── Weight pair analysis (client-side) ──────────────────────────
    const computeWeightMetrics = React.useCallback((username, weightData) => {
        // Filter to this FLW's visits with valid weight
        const flwVisits = weightData
            .filter(v => v.username === username && v.weight && parseFloat(v.weight) > 0)
            .sort((a, b) => {
                const da = new Date(a.visit_date);
                const db = new Date(b.visit_date);
                return da - db;
            });

        // Group by beneficiary
        const byChild = {};
        flwVisits.forEach(v => {
            const cid = v.beneficiary_case_id;
            if (!cid) return;
            if (!byChild[cid]) byChild[cid] = [];
            byChild[cid].push(v);
        });

        let totalPairs = 0;
        let lossPairs = 0;
        let zeroPairs = 0;
        let totalDailyGain = 0;
        let gainPairCount = 0;

        Object.values(byChild).forEach(visits => {
            for (let i = 1; i < visits.length; i++) {
                const prev = parseFloat(visits[i - 1].weight);
                const curr = parseFloat(visits[i].weight);
                const prevDate = new Date(visits[i - 1].visit_date);
                const currDate = new Date(visits[i].visit_date);
                const daysDiff = (currDate - prevDate) / (1000 * 60 * 60 * 24);

                if (isNaN(prev) || isNaN(curr) || prev <= 0 || curr <= 0) continue;
                if (daysDiff <= 0) continue;

                totalPairs++;
                const weightDiff = curr - prev;

                if (weightDiff < 0) lossPairs++;
                if (weightDiff === 0) zeroPairs++;
                if (daysDiff > 0) {
                    totalDailyGain += weightDiff / daysDiff;
                    gainPairCount++;
                }
            }
        });

        return {
            total_weight_pairs: totalPairs,
            pct_wt_loss: totalPairs >= MIN_CASES.weight ? lossPairs / totalPairs : null,
            mean_daily_gain: gainPairCount >= MIN_CASES.weight ? totalDailyGain / gainPairCount : null,
            pct_wt_zero: totalPairs >= MIN_CASES.weight ? zeroPairs / totalPairs : null,
        };
    }, [weightRows]);

    // ── Merge pipeline data + compute flags ─────────────────────────
    const flwData = React.useMemo(() => {
        return flwRows.map(row => {
            const totalCases = parseInt(row.total_cases) || 0;
            const closedCases = parseInt(row.closed_cases) || 0;
            const deaths = parseInt(row.deaths) || 0;
            const totalVisits = parseInt(row.total_visits) || 0;
            const dangerVisitCount = parseInt(row.danger_visit_count) || 0;
            const dangerPositiveCount = parseInt(row.danger_positive_count) || 0;

            // Avg visits per closed case
            const avgVisits = closedCases > 0 ? totalVisits / closedCases : null;
            // Mortality rate
            const mortRate = closedCases > 0 ? deaths / closedCases : null;
            // Danger sign rate
            const pctDanger = dangerVisitCount > 0 ? dangerPositiveCount / dangerVisitCount : null;

            // Weight metrics (from visit-level pipeline)
            const wm = computeWeightMetrics(row.username, weightRows);

            // Enrollment timing — TODO: requires per-case reg_date vs discharge_date
            // For now compute from available data
            const pct8PlusDays = null; // Will be computed when enrollment pipeline is refined

            // ── Flag computation ────────────────────────────────────
            const excluded = totalCases < 20;
            const flags = {};

            // flag_visits: avg visits < 3.0 (min 10 closed cases)
            flags.visits = !excluded && closedCases >= MIN_CASES.visits && avgVisits !== null && avgVisits < THRESHOLDS.visits;
            // flag_mort_low: mortality < 2% (min 20 closed cases)
            flags.mort_low = !excluded && closedCases >= MIN_CASES.mortality && mortRate !== null && mortRate < THRESHOLDS.mort_low;
            // flag_mort_high: mortality > 20% (min 20 closed cases)
            flags.mort_high = !excluded && closedCases >= MIN_CASES.mortality && mortRate !== null && mortRate > THRESHOLDS.mort_high;
            // flag_enroll: >35% enrolled 8+ days after discharge (min 10 records)
            flags.enroll = !excluded && pct8PlusDays !== null && pct8PlusDays > THRESHOLDS.enroll;
            // flag_danger_high: >30% danger sign positive (min 20 visits)
            flags.danger_high = !excluded && dangerVisitCount >= MIN_CASES.danger && pctDanger !== null && pctDanger > THRESHOLDS.danger_high;
            // flag_danger_zero: zero danger signs across 30+ visits
            flags.danger_zero = !excluded && dangerVisitCount >= MIN_CASES.danger_zero && dangerPositiveCount === 0;
            // flag_wt_loss: >15% weight loss pairs (min 10 pairs)
            flags.wt_loss = !excluded && wm.pct_wt_loss !== null && wm.pct_wt_loss > THRESHOLDS.wt_loss;
            // flag_wt_gain: mean daily gain > 60 g/day (min 10 pairs)
            flags.wt_gain = !excluded && wm.mean_daily_gain !== null && wm.mean_daily_gain > THRESHOLDS.wt_gain;
            // flag_wt_zero: >30% zero change pairs (min 10 pairs)
            flags.wt_zero = !excluded && wm.pct_wt_zero !== null && wm.pct_wt_zero > THRESHOLDS.wt_zero;

            const flagCount = Object.values(flags).filter(Boolean).length;

            return {
                username: row.username,
                total_cases: totalCases,
                closed_cases: closedCases,
                deaths: deaths,
                total_visits: totalVisits,
                avg_visits: avgVisits,
                mort_rate: mortRate,
                pct_8plus_days: pct8PlusDays,
                pct_danger: pctDanger,
                danger_visit_count: dangerVisitCount,
                danger_positive_count: dangerPositiveCount,
                pct_wt_loss: wm.pct_wt_loss,
                mean_daily_gain: wm.mean_daily_gain,
                pct_wt_zero: wm.pct_wt_zero,
                total_weight_pairs: wm.total_weight_pairs,
                flags: flags,
                flag_count: flagCount,
                excluded: excluded,
            };
        });
    }, [flwRows, weightRows, computeWeightMetrics]);

    // ── Filter + sort ───────────────────────────────────────────────
    const filteredData = React.useMemo(() => {
        let data = flwData.filter(d => !d.excluded);
        if (filter === 'flagged') data = data.filter(d => d.flag_count > 0);
        if (filter === '2plus') data = data.filter(d => d.flag_count >= 2);
        if (search) {
            const q = search.toLowerCase();
            data = data.filter(d => d.username.toLowerCase().includes(q));
        }
        data.sort((a, b) => {
            const av = a[sortKey] ?? -Infinity;
            const bv = b[sortKey] ?? -Infinity;
            return sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
        });
        return data;
    }, [flwData, filter, search, sortKey, sortAsc]);

    // ── KPI summaries ───────────────────────────────────────────────
    const kpis = React.useMemo(() => {
        const analyzed = flwData.filter(d => !d.excluded);
        const excluded = flwData.filter(d => d.excluded);
        const flagged = analyzed.filter(d => d.flag_count >= 2);
        const totalCases = flwData.reduce((s, d) => s + d.total_cases, 0);
        return {
            analyzed: analyzed.length,
            excluded: excluded.length,
            flagged: flagged.length,
            totalCases: totalCases,
        };
    }, [flwData]);

    // ── Selection helpers ───────────────────────────────────────────
    const toggleWorker = (username) => {
        setSelectedWorkers(prev => ({ ...prev, [username]: !prev[username] }));
    };
    const handleSelectAll = () => {
        const newState = !selectAll;
        setSelectAll(newState);
        const newSelected = {};
        filteredData.forEach(d => { newSelected[d.username] = newState; });
        setSelectedWorkers(newSelected);
    };
    const selectedCount = Object.values(selectedWorkers).filter(Boolean).length;

    // ── Format helpers ──────────────────────────────────────────────
    const fmt = (val, type) => {
        if (val === null || val === undefined) return 'NE';
        if (type === 'pct') return (val * 100).toFixed(1) + '%';
        if (type === 'dec') return val.toFixed(2);
        if (type === 'gain') return val.toFixed(1);
        return String(val);
    };

    // ── Audit creation ──────────────────────────────────────────────
    const handleCreateAudits = async () => {
        const selectedUsernames = Object.entries(selectedWorkers)
            .filter(([_, selected]) => selected)
            .map(([username]) => username);

        if (selectedUsernames.length === 0) return;
        setIsRunning(true);
        setProgress({ status: 'starting' });

        try {
            const now = new Date();
            const dayOfWeek = now.getDay();
            const lastMonday = new Date(now);
            lastMonday.setDate(now.getDate() - dayOfWeek - 6);
            const lastSunday = new Date(lastMonday);
            lastSunday.setDate(lastMonday.getDate() + 6);

            const startDate = lastMonday.toISOString().split('T')[0];
            const endDate = lastSunday.toISOString().split('T')[0];

            const result = await actions.createAudit({
                opportunities: [{ id: instance.opportunity_id }],
                criteria: {
                    audit_type: 'date_range',
                    granularity: 'per_flw',
                    title: 'FLW Flag Audit - ' + startDate,
                    start_date: startDate,
                    end_date: endDate,
                    related_fields: [{
                        imagePath: 'anthropometric/upload_weight_image',
                        fieldPath: 'child_weight_visit',
                        label: 'Weight Reading',
                        filter_by_image: true,
                        filter_by_field: true,
                    }],
                    selected_flw_user_ids: selectedUsernames,
                },
                workflow_run_id: instance.id,
                ai_agent_id: 'scale_validation',
            });

            if (result && result.task_id) {
                const cleanup = actions.streamAuditProgress(
                    result.task_id,
                    (progressData) => { setProgress(progressData); },
                    (finalResult) => {
                        setIsRunning(false);
                        setProgress({ status: 'completed', ...finalResult });
                        onUpdateState({ status: 'audits_created' }).catch(() => {});
                        fetch('/audit/api/workflow/' + instance.id + '/sessions/')
                            .then(res => res.json())
                            .then(data => {
                                if (data.success && data.sessions) setLinkedSessions(data.sessions);
                            })
                            .catch(() => {});
                    },
                    (error) => {
                        setIsRunning(false);
                        setProgress({ status: 'failed', error });
                    }
                );
                cleanupRef.current = cleanup;
            }
        } catch (err) {
            setIsRunning(false);
            setProgress({ status: 'failed', error: err.message });
        }
    };

    // ── Load linked sessions on mount ───────────────────────────────
    React.useEffect(() => {
        if (instance && instance.id) {
            fetch('/audit/api/workflow/' + instance.id + '/sessions/')
                .then(res => res.json())
                .then(data => {
                    if (data.success && data.sessions) setLinkedSessions(data.sessions);
                })
                .catch(() => {});
        }
        return () => { if (cleanupRef.current) cleanupRef.current(); };
    }, [instance]);

    // ── Render ──────────────────────────────────────────────────────
    if (!pipelines || !pipelines.flw_flags) {
        return <div className="p-8 text-center text-gray-500">Loading pipeline data...</div>;
    }

    const handleSort = (key) => {
        if (sortKey === key) { setSortAsc(!sortAsc); }
        else { setSortKey(key); setSortAsc(false); }
    };

    const SortIcon = ({ col }) => {
        if (sortKey !== col) return <span className="text-gray-300 ml-1">&#8597;</span>;
        return <span className="text-blue-600 ml-1">{sortAsc ? '&#8593;' : '&#8595;'}</span>;
    };

    const FlagCell = ({ value, flagged, type }) => {
        const bg = flagged ? 'bg-red-100 text-red-800 font-semibold' : '';
        return <td className={'px-3 py-2 text-sm text-right ' + bg}>{fmt(value, type)}</td>;
    };

    return (
        <div className="space-y-6">
            {/* KPI Cards */}
            <div className="grid grid-cols-4 gap-4">
                <div className="bg-white rounded-lg border-l-4 border-blue-500 p-4 shadow-sm">
                    <div className="text-2xl font-bold text-blue-700">{kpis.analyzed}</div>
                    <div className="text-sm text-gray-500">FLWs Analyzed</div>
                </div>
                <div className="bg-white rounded-lg border-l-4 border-red-500 p-4 shadow-sm">
                    <div className="text-2xl font-bold text-red-700">{kpis.flagged}</div>
                    <div className="text-sm text-gray-500">With 2+ Flags</div>
                </div>
                <div className="bg-white rounded-lg border-l-4 border-gray-400 p-4 shadow-sm">
                    <div className="text-2xl font-bold text-gray-600">{kpis.excluded}</div>
                    <div className="text-sm text-gray-500">Excluded (&lt;20 cases)</div>
                </div>
                <div className="bg-white rounded-lg border-l-4 border-green-500 p-4 shadow-sm">
                    <div className="text-2xl font-bold text-green-700">{kpis.totalCases.toLocaleString()}</div>
                    <div className="text-sm text-gray-500">Total Cases</div>
                </div>
            </div>

            {/* Filter Bar */}
            <div className="flex items-center gap-3 bg-white rounded-lg p-3 shadow-sm">
                {['all', 'flagged', '2plus'].map(f => (
                    <button
                        key={f}
                        onClick={() => setFilter(f)}
                        className={'px-3 py-1.5 rounded-full text-sm font-medium ' +
                            (filter === f ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200')}
                    >
                        {f === 'all' ? 'All FLWs' : f === 'flagged' ? 'Any Flag' : '2+ Flags'}
                    </button>
                ))}
                <input
                    type="text"
                    placeholder="Search FLW..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    className="ml-auto px-3 py-1.5 border rounded-lg text-sm w-48"
                />
            </div>

            {/* Flag Table */}
            <div className="bg-white rounded-lg shadow-sm overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-3 py-3">
                                <input type="checkbox" checked={selectAll} onChange={handleSelectAll}
                                    disabled={isRunning} className="rounded border-gray-300" />
                            </th>
                            <th onClick={() => handleSort('username')} className="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer">
                                FLW<SortIcon col="username" />
                            </th>
                            <th onClick={() => handleSort('total_cases')} className="px-3 py-3 text-right text-xs font-medium text-gray-500 uppercase cursor-pointer">
                                Cases<SortIcon col="total_cases" />
                            </th>
                            <th onClick={() => handleSort('avg_visits')} className="px-3 py-3 text-right text-xs font-medium text-gray-500 uppercase cursor-pointer">
                                Avg Vis<SortIcon col="avg_visits" />
                            </th>
                            <th onClick={() => handleSort('mort_rate')} className="px-3 py-3 text-right text-xs font-medium text-gray-500 uppercase cursor-pointer">
                                Mort %<SortIcon col="mort_rate" />
                            </th>
                            <th onClick={() => handleSort('pct_8plus_days')} className="px-3 py-3 text-right text-xs font-medium text-gray-500 uppercase cursor-pointer">
                                8+ Days<SortIcon col="pct_8plus_days" />
                            </th>
                            <th onClick={() => handleSort('pct_danger')} className="px-3 py-3 text-right text-xs font-medium text-gray-500 uppercase cursor-pointer">
                                Danger<SortIcon col="pct_danger" />
                            </th>
                            <th onClick={() => handleSort('pct_wt_loss')} className="px-3 py-3 text-right text-xs font-medium text-gray-500 uppercase cursor-pointer">
                                Wt Loss<SortIcon col="pct_wt_loss" />
                            </th>
                            <th onClick={() => handleSort('mean_daily_gain')} className="px-3 py-3 text-right text-xs font-medium text-gray-500 uppercase cursor-pointer">
                                Gain<SortIcon col="mean_daily_gain" />
                            </th>
                            <th onClick={() => handleSort('pct_wt_zero')} className="px-3 py-3 text-right text-xs font-medium text-gray-500 uppercase cursor-pointer">
                                Wt Zero<SortIcon col="pct_wt_zero" />
                            </th>
                            <th onClick={() => handleSort('flag_count')} className="px-3 py-3 text-right text-xs font-medium text-gray-500 uppercase cursor-pointer">
                                Flags<SortIcon col="flag_count" />
                            </th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                        {filteredData.map(d => (
                            <tr key={d.username}
                                className={(selectedWorkers[d.username] ? 'bg-blue-50 ' : 'hover:bg-gray-50 ') +
                                    (d.flag_count >= 2 ? 'border-l-4 border-red-400' : d.flag_count === 1 ? 'border-l-4 border-orange-300' : '')}>
                                <td className="px-3 py-2">
                                    <input type="checkbox" checked={selectedWorkers[d.username] || false}
                                        onChange={() => toggleWorker(d.username)} disabled={isRunning}
                                        className="rounded border-gray-300" />
                                </td>
                                <td className="px-3 py-2 text-sm font-medium text-gray-900">{d.username}</td>
                                <td className="px-3 py-2 text-sm text-right text-gray-700">{d.total_cases}</td>
                                <FlagCell value={d.avg_visits} flagged={d.flags.visits} type="dec" />
                                <FlagCell value={d.mort_rate} flagged={d.flags.mort_low || d.flags.mort_high} type="pct" />
                                <FlagCell value={d.pct_8plus_days} flagged={d.flags.enroll} type="pct" />
                                <FlagCell value={d.pct_danger} flagged={d.flags.danger_high || d.flags.danger_zero} type="pct" />
                                <FlagCell value={d.pct_wt_loss} flagged={d.flags.wt_loss} type="pct" />
                                <FlagCell value={d.mean_daily_gain} flagged={d.flags.wt_gain} type="gain" />
                                <FlagCell value={d.pct_wt_zero} flagged={d.flags.wt_zero} type="pct" />
                                <td className={'px-3 py-2 text-sm text-right font-bold ' +
                                    (d.flag_count >= 2 ? 'text-red-700' : d.flag_count === 1 ? 'text-orange-600' : 'text-gray-400')}>
                                    {d.flag_count}
                                </td>
                            </tr>
                        ))}
                        {filteredData.length === 0 && (
                            <tr><td colSpan={11} className="px-6 py-8 text-center text-gray-400">No FLWs match the current filter</td></tr>
                        )}
                    </tbody>
                </table>
            </div>

            {/* Action Bar */}
            <div className="sticky bottom-0 bg-white border-t shadow-lg rounded-lg p-4 flex items-center justify-between">
                <div className="text-sm text-gray-600">
                    {selectedCount > 0 ? (
                        <span className="font-medium">{selectedCount} FLW{selectedCount !== 1 ? 's' : ''} selected</span>
                    ) : (
                        <span>Select FLWs to create audits</span>
                    )}
                </div>
                <div className="flex items-center gap-3">
                    {progress && progress.status === 'completed' && (
                        <span className="text-green-600 text-sm font-medium">Audits created successfully</span>
                    )}
                    {progress && progress.status === 'failed' && (
                        <span className="text-red-600 text-sm">{progress.error || 'Failed'}</span>
                    )}
                    {isRunning && progress && (
                        <span className="text-blue-600 text-sm">
                            {progress.stage_name || 'Processing...'}
                            {progress.processed && progress.total ? ` (${progress.processed}/${progress.total})` : ''}
                        </span>
                    )}
                    <button
                        onClick={handleCreateAudits}
                        disabled={selectedCount === 0 || isRunning}
                        className={'px-5 py-2.5 rounded-lg text-sm font-medium ' +
                            (selectedCount > 0 && !isRunning
                                ? 'bg-red-600 text-white hover:bg-red-700 shadow-sm'
                                : 'bg-gray-200 text-gray-400 cursor-not-allowed')}
                    >
                        {isRunning ? 'Creating Audits...' : 'Create Audits with AI Review (' + selectedCount + ')'}
                    </button>
                </div>
            </div>

            {/* Linked Audit Sessions */}
            {linkedSessions.length > 0 && (
                <div className="bg-white rounded-lg shadow-sm p-4">
                    <h3 className="text-sm font-semibold text-gray-700 mb-3">Linked Audit Sessions</h3>
                    <div className="space-y-2">
                        {linkedSessions.map(session => (
                            <a key={session.id} href={'/audit/' + session.id + '/bulk/'}
                                className="block p-3 rounded border hover:bg-gray-50 transition-colors">
                                <div className="flex justify-between">
                                    <span className="text-sm font-medium text-blue-600">{session.title || 'Audit Session'}</span>
                                    <span className={'text-xs px-2 py-0.5 rounded-full ' +
                                        (session.status === 'completed' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700')}>
                                        {session.status}
                                    </span>
                                </div>
                                {session.stats && (
                                    <div className="text-xs text-gray-500 mt-1">
                                        {session.stats.total_visits || 0} visits, {session.stats.total_assessments || 0} assessments
                                    </div>
                                )}
                            </a>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
"""
```

**Step 2: Verify template still loads**

```bash
python -c "from connect_labs.workflow.templates import get_template; t = get_template('kmc_flw_flags'); print('OK:', len(t['render_code']), 'chars')"
```

Expected: `OK: XXXX chars`

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_flw_flags.py
git commit -m "feat: implement KMC FLW flag report RENDER_CODE with full UI"
```

---

## Task 5: Add template to __init__.py exports

**Files:**
- Modify: `connect_labs/workflow/templates/__init__.py:218-231`

**Step 1: Add import and __all__ entry**

The template auto-discovers via `pkgutil`, but the explicit import in `__init__.py` ensures it's in `__all__`:

Add `kmc_flw_flags` to the import line (~line 218):
```python
from . import audit_with_ai_review, kmc_flw_flags, kmc_longitudinal, mbw_monitoring_v2, ocs_outreach, performance_review
```

Add to `__all__` (~line 227):
```python
    "kmc_flw_flags",
```

**Step 2: Verify**

```bash
python -c "from connect_labs.workflow.templates import list_templates; keys = [t['key'] for t in list_templates()]; print(keys); assert 'kmc_flw_flags' in keys"
```

Expected: List printed, assertion passes

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/__init__.py
git commit -m "feat: register kmc_flw_flags template in __init__.py exports"
```

---

## Task 6: Write E2E test

**Files:**
- Create: `connect_labs/workflow/tests/e2e/test_flw_flags_workflow.py`
- Reference: `connect_labs/workflow/tests/e2e/conftest.py` for fixtures

**Step 1: Write the E2E test**

```python
# connect_labs/workflow/tests/e2e/test_flw_flags_workflow.py
"""E2E test for KMC FLW Flag Report workflow template."""
import pytest
from playwright.sync_api import expect


@pytest.mark.e2e
class TestKMCFLWFlagsWorkflow:
    """Test the KMC FLW Flag Report workflow template end-to-end."""

    def test_flag_report_renders(self, auth_page, live_server_url, opportunity_id):
        """Create a workflow run and verify the flag report UI renders."""
        page = auth_page
        page.set_default_timeout(30000)

        # 1. Navigate to workflow list and create new run
        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("networkidle")

        # Click "New Workflow" or similar
        new_btn = page.locator("text=New Workflow").first
        if new_btn.is_visible():
            new_btn.click()
            page.wait_for_load_state("networkidle")

        # Select KMC FLW Flag Report template
        template_card = page.locator("text=KMC FLW Flag Report").first
        expect(template_card).to_be_visible(timeout=10000)
        template_card.click()

        # Confirm creation
        create_btn = page.locator("button:has-text('Create')").first
        if create_btn.is_visible():
            create_btn.click()

        page.wait_for_load_state("networkidle")

        # 2. Wait for pipeline data to load (can take up to 120s)
        page.set_default_timeout(120000)
        root = page.locator("#workflow-root")

        # Verify KPI cards render
        expect(root.locator("text=FLWs Analyzed")).to_be_visible(timeout=120000)
        expect(root.locator("text=Total Cases")).to_be_visible()

        # Verify filter bar renders
        expect(root.locator("text=All FLWs")).to_be_visible()
        expect(root.locator("text=Any Flag")).to_be_visible()
        expect(root.locator("text=2+ Flags")).to_be_visible()

        # Verify table renders with at least a header
        expect(root.locator("th:has-text('Avg Vis')")).to_be_visible()
        expect(root.locator("th:has-text('Flags')")).to_be_visible()

        # Verify action bar renders
        expect(root.locator("text=Create Audits with AI Review")).to_be_visible()

        # 3. Cleanup — delete the workflow run
        # Get run ID from URL
        url = page.url
        if '/run/' in url:
            run_id = url.split('/run/')[1].rstrip('/')
            csrf = page.evaluate("document.querySelector('[name=csrfmiddlewaretoken]')?.value || document.cookie.match(/csrftoken=([^;]+)/)?.[1] || ''")
            if csrf and run_id:
                page.request.delete(
                    f"{live_server_url}/labs/workflow/api/run/{run_id}/",
                    headers={"X-CSRFToken": csrf},
                )
```

**Step 2: Run E2E test**

```bash
pytest connect_labs/workflow/tests/e2e/test_flw_flags_workflow.py -v --ds=config.settings.local -o "addopts=" --opportunity-id=874
```

Expected: PASS (flag report renders with KPI cards, table, action bar)

**Step 3: Commit**

```bash
git add connect_labs/workflow/tests/e2e/test_flw_flags_workflow.py
git commit -m "test: add E2E test for KMC FLW Flag Report template"
```

---

## Task 7: Refine pipeline field paths with MCP verification

After the template is running, use the CommCare MCP server to verify that the pipeline field paths actually match the form JSON structure. Adjust any paths that return empty data.

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_flw_flags.py` (PIPELINE_SCHEMAS fields)

**Step 1: Run the template and check which fields return data**

Load the workflow in the browser, open DevTools Console, and check:
```javascript
// In browser console on the workflow page:
console.log('flw_flags:', window.__PIPELINE_DATA__?.flw_flags?.rows?.[0]);
console.log('weight_series:', window.__PIPELINE_DATA__?.weight_series?.rows?.slice(0, 3));
```

**Step 2: Use MCP tools to verify paths if fields are empty**

Use `get_form_json_paths` for the Registration form (`58991FD0-F6A7-4DA2-8C74-AE4655A424A7`) and Visit form (`42DFAFE1-C3B5-4F11-A400-827DA369F2C9`) on opportunity 874 to find the correct paths.

Key paths to verify:
- `form.case_close_condition` — may need `form.case_close_check` or `form.grp_kmc_beneficiary.kmc_status`
- `form.child_alive` — exists on both forms
- `form.danger_signs_checklist.danger_sign_positive` — verify exact casing/nesting
- `form.hosp_lbl.date_hospital_discharge` — registration form only

**Step 3: Update paths and re-test**

Adjust any incorrect paths in PIPELINE_SCHEMAS.

**Step 4: Commit**

```bash
git add connect_labs/workflow/templates/kmc_flw_flags.py
git commit -m "fix: correct pipeline field paths based on MCP verification"
```

---

## Summary

| Task | Description | Key Files |
|------|-------------|-----------|
| 1 | Add count_distinct aggregation | query_builder.py, test_query_builder.py |
| 2 | Add per-field filter support | config.py, query_builder.py, data_access.py |
| 3 | Template skeleton + PIPELINE_SCHEMAS | kmc_flw_flags.py |
| 4 | Full RENDER_CODE (flags, UI, audit) | kmc_flw_flags.py |
| 5 | Register in __init__.py | __init__.py |
| 6 | E2E test | test_flw_flags_workflow.py |
| 7 | Verify & fix pipeline paths | kmc_flw_flags.py |
