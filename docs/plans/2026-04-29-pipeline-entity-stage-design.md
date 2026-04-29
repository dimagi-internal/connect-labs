# Pipeline: Entity Stage

**Date:** 2026-04-29
**Status:** Draft
**Prerequisite for:** [Workflow Runs & Snapshots](2026-04-29-workflow-run-snapshots-design.md)

## Problem

The pipeline framework supports two terminal stages today (`labs/analysis/config.py:25`):

```python
class CacheStage(Enum):
    VISIT_LEVEL = "visit_level"   # one row per visit
    AGGREGATED  = "aggregated"    # one row per FLW (GROUP BY username)
```

Several dashboards need a third stage: per-entity (per-child, per-beneficiary, per-case) aggregation. `AnalysisPipelineConfig` already has `linking_field` (default `entity_id`), explicitly intended for this — but no stage acts on it. As a workaround, three templates each implement entity aggregation in JS over flat visit rows:

- `kmc_longitudinal.py:261` — `groupVisitsByChild(visitRows)` reduces visits to children
- `kmc_project_metrics.py:269` — its own copy of `groupVisitsByChild`
- `sam_followup.py:273` — another copy

Each then computes per-entity properties: pick `first` non-null demographic across rows, walk to find `last` weight, sum visit counts, etc. — exactly what the SQL-side aggregation vocabulary already does for FLW stage.

Doing this in JS forces every dashboard to ship the entire flat visit table to the browser, re-derive children on every mount, and own its own (slightly different) aggregation logic. Three reasons to fix it now:

1. Performance — push aggregation to SQL where it already runs for FLW stage. Browser receives entity rows, not visit rows.
2. Correctness — single Python implementation replaces three JS copies that drift.
3. Unblocks the snapshot framework — `build_snapshot` becomes a thin KPI/weekly summary on top of entity rows instead of having to re-implement the JS shaping.

## Design

### Architecture

The entity stage parallels the existing FLW stage exactly. Same aggregation vocabulary, same caching pattern, same query-builder shape — just `GROUP BY <linking_field>` instead of `GROUP BY username`.

```
Raw visits  ─┬─▶  ComputedVisitCache (one row per visit)        VISIT_LEVEL
             │
             ├─▶  ComputedFLWCache    (GROUP BY username)        AGGREGATED
             │
             └─▶  ComputedEntityCache (GROUP BY linking_field)   ENTITY  ← new
```

The three are siblings. A pipeline picks one as its `terminal_stage` and that's what the cache fills. The choice is per-pipeline, declared in the schema, not per-run.

### Schema additions

**`CacheStage.ENTITY`** — new enum value:

```python
class CacheStage(Enum):
    VISIT_LEVEL = "visit_level"
    AGGREGATED  = "aggregated"
    ENTITY      = "entity"   # new
```

**`AnalysisPipelineConfig`** — `linking_field` is already there. Two clarifications:

- When `terminal_stage == ENTITY`, `linking_field` must be the name of a `FieldComputation` declared in `fields` *or* one of the base raw-visit columns (`entity_id`). The query builder uses its path expression as the GROUP BY column.
- `fields` and `histograms` define what's aggregated per entity, with the same `aggregation` vocabulary as FLW stage (`count`, `sum`, `avg`, `min`, `max`, `first`, `last`, `count_distinct`, `list`).

No new dataclass needed — the existing `FieldComputation` carries everything.

### Storage

New model `ComputedEntityCache` paralleling `ComputedFLWCache`:

```python
class ComputedEntityCache(models.Model):
    # Cache metadata (identical to ComputedFLWCache)
    opportunity_id = models.IntegerField(db_index=True)
    config_hash    = models.CharField(max_length=32, db_index=True)
    visit_count    = models.IntegerField()
    expires_at     = models.DateTimeField(db_index=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    # Entity identification
    entity_id   = models.CharField(max_length=255, db_index=True)
    entity_name = models.CharField(max_length=500, blank=True)
    username    = models.CharField(max_length=255, db_index=True)
        # representative FLW — typically `first(username)` per entity.
        # For entities served by multiple FLWs over time, callers should use `list`
        # via a separate computed field rather than relying on this column.

    # Aggregated data
    aggregated_fields = models.JSONField(default=dict)

    # Standard counters (parallels FLW)
    total_visits     = models.IntegerField(default=0)
    first_visit_date = models.DateField(null=True, blank=True)
    last_visit_date  = models.DateField(null=True, blank=True)

    class Meta:
        app_label = "labs"
        db_table  = "labs_computed_entity_cache"
        indexes = [
            models.Index(fields=["opportunity_id", "config_hash", "visit_count"]),
            models.Index(fields=["opportunity_id", "config_hash", "username"]),
        ]
```

Indexes mirror FLW. The `(opp, config, username)` index supports KMC's "all children for this FLW" lookup, which is the dominant query.

### Query builder

New function `build_entity_aggregation_query` paralleling `build_flw_aggregation_query` (`labs/analysis/backends/sql/query_builder.py:291`). Differences:

- `GROUP BY <linking_field_expr>, opportunity_id` instead of `GROUP BY username`.
- The linking-field expression comes from the same `_paths_to_coalesce_sql` machinery used for any other field — declarative paths, with fallbacks.
- Standard counters: `COUNT(*) as total_visits`, `MIN(visit_date)`, `MAX(visit_date)` — same as FLW. The status/flagged counters from FLW (`approved_visits`, etc.) are dropped at entity stage; an entity isn't approved-vs-rejected, individual visits are. Templates that need those at entity level declare them as custom `FieldComputation` fields.
- The correlated `first`/`last` subqueries (`query_builder.py:184-215`) currently join on `username`. They get a sibling form that joins on `<linking_field>`. To keep this clean, refactor `_aggregation_to_sql` to take a `group_column_expr` parameter and emit the correlated subquery's WHERE clause from that — one code path serving both stages.

### Cache manager

`SQLCacheManager` (`labs/analysis/backends/sql/cache.py:55`) gets a parallel set of methods:

- `has_valid_entity_cache(expected_visit_count, tolerance_pct=100) -> bool`
- `store_entity_results(entity_data: list[dict], visit_count: int)`
- `get_entity_results_queryset()`

All three are direct copies of the FLW versions with `ComputedEntityCache` substituted. `invalidate_all` and `delete_config_cache` extend to delete entity rows.

### Pipeline orchestration

`labs/analysis/pipeline.py` already branches on `terminal_stage`. Add the `ENTITY` case wherever `AGGREGATED` is checked. Concretely:

- Cache validity check (`pipeline.py:208`): when `terminal_stage == ENTITY`, check `has_valid_entity_cache`.
- Result type (`pipeline.py:290`): a new `EntityAnalysisResult` paralleling `FLWAnalysisResult`. Fields: `entity_id`, `entity_name`, `username`, `total_visits`, `first_visit_date`, `last_visit_date`, `custom_fields` (mirrors FLW's pattern from `backends/sql/backend.py:335`).
- SSE progress strings (`pipeline.py:372`): `"FLW" / "visit" / "entity"`.
- Storage write path: when `terminal_stage == ENTITY`, run `build_entity_aggregation_query`, fetch rows, call `store_entity_results`.

### Workflow integration (read side)

Workflows already consume pipelines via the `pipelines` prop, populated by `WorkflowDataAccess.get_pipeline_data` (called in two places: server-side via `run_workflow_job` when `server_fetch_pipelines=True`, and the FE pipeline endpoint). The path that fetches rows needs to know which cache table to read from based on the pipeline's `terminal_stage`. Today it implicitly reads from FLW or visit cache; for entity-stage pipelines it reads from `ComputedEntityCache` and returns rows in the same `{rows, fields}` shape.

The FE prop shape is unchanged — `pipelines.<alias>.rows` is just a list of objects. KMC render code stops calling `groupVisitsByChild` and reads the entity rows directly.

## What this does not do

- **No new aggregation vocabulary.** Same `count/sum/avg/first/last/min/max/count_distinct/list` as today. If a template needs something we can't express, that's a separate feature.
- **No multi-entity composition.** A pipeline picks one terminal stage. KMC FLW Flags that consume both `flw_flags` (FLW-level) and `weight_series` (visit-level) keep doing that — they're two pipelines, not one with two outputs.
- **No automatic linking-field discovery.** Templates declare `linking_field` explicitly in the schema. Same convention as today.

## Migration path for existing templates

Each KMC/SAM template migration is a self-contained change after the framework lands:

1. **`kmc_longitudinal.py`** — set `terminal_stage=ENTITY`, `linking_field="beneficiary_case_id"` (already declared). Promote each demographic field used by `groupVisitsByChild` from a base path to a `FieldComputation` with the right aggregation (`first` for demographics, `last` for current weight, `last` for `kmc_status`). Drop `groupVisitsByChild` and `findFirst` from the JS. Fields like `weightGain`, `isOverdue`, `reachedThreshold`, `avgWeightGainPerWeek` move to `build_snapshot` (the layer 2 step) — these are derived properties, not aggregations.
2. **`kmc_project_metrics.py`** — same pipeline as above; in fact this template should *share* the entity-level pipeline with `kmc_longitudinal` rather than redefining it. Project metrics are then just KPIs computed in `build_snapshot` over the same entity rows.
3. **`sam_followup.py`** — same pattern, `linking_field="beneficiary_case_id"`, demographics as `FieldComputation`s, MUAC color resolution as `last`. The image-filmstrip and audit-creation halves of this template are unaffected (they're action-shaped per the snapshot doc).
4. **`kmc_flw_flags.py`** — already FLW-stage, doesn't need entity. Stays as-is. The lift here is moving its per-FLW JS computation into `build_snapshot` — orthogonal to this design.

## Deliverables

1. **Schema** (`labs/analysis/config.py`): add `CacheStage.ENTITY`. Document the `linking_field` requirement at entity stage.
2. **Model + migration** (`labs/analysis/backends/sql/models.py`, `labs/migrations/`): `ComputedEntityCache` + `0xxx_computed_entity_cache.py`.
3. **Query builder** (`labs/analysis/backends/sql/query_builder.py`): `build_entity_aggregation_query`. Refactor `_aggregation_to_sql` so `first`/`last` subqueries take a configurable group column. Tests in `tests/test_query_builder.py`.
4. **Cache manager** (`labs/analysis/backends/sql/cache.py`): `has_valid_entity_cache`, `store_entity_results`, `get_entity_results_queryset`. Extend `invalidate_all` and `delete_config_cache`.
5. **Orchestration** (`labs/analysis/pipeline.py`): `EntityAnalysisResult` plus `terminal_stage=ENTITY` branches.
6. **Workflow read path** (`workflow/data_access.py` `get_pipeline_data` and the labs analysis pipeline endpoint): return entity rows when the pipeline's `terminal_stage == ENTITY`.
7. **Tests** — entity-stage parity tests against KMC fixtures; verify `groupVisitsByChild`-equivalent output matches the SQL aggregation. This is the gate for swapping the templates over.
8. **Docs** — `WORKFLOW_REFERENCE.md` gains an "Entity stage" section showing schema declaration and a worked example.

Estimated effort: 4–6 days for the framework. Per-template lift is then an independent change per template.

## Test plan

The strategy is **side-by-side, not in-place**. Existing templates (`kmc_longitudinal`, `kmc_project_metrics`, `sam_followup`) are untouched. Each gets a `_v2` sibling that uses entity stage. Both versions live in the registry simultaneously and are compared against the same opportunity data until parity is signed off; only then is v1 deleted.

### Layer 1: Framework unit tests

`labs/analysis/backends/sql/tests/`

- **Entity-stage GROUP BY parity with FLW.** Same fixture, same fields, same aggregations, swap `terminal_stage` and `linking_field=username`. Output rows must match FLW-stage output row-for-row. Locks in the refactored `_aggregation_to_sql` doesn't break FLW.
- **`first`/`last` tiebreaker test.** Two visits with the same `visit_date` for the same entity, different `visit_id`. Confirm the row with the smaller `visit_id` wins for `first`, larger for `last`. Run for both stages.
- **Histogram parity.** Synthetic numeric field, fixed bins, 50 entities. Confirm bin counts match a hand-computed expected per entity.
- **NULL `entity_id` handling.** Visits where the linking-field path doesn't extract collapse into one GROUP BY row (or zero, depending on how we handle it — confirm the chosen behavior is stable across rebuilds).
- **Cache-rebuild idempotence.** Run aggregation twice; confirm row count and contents identical, no orphan rows.

### Layer 2: Per-template parity harness

A pytest module `labs/analysis/tests/test_entity_parity.py` that:

1. Targets **opportunity 874** (KMC PIPN, ~11k visits) as the single fixture. It is the only opp running all three KMC templates (`kmc_longitudinal`, `kmc_project_metrics`, `kmc_flw_flags`) plus where `audit_with_ai_review` is wired up; opportunity 879 (~547 visits) is the single fixture for `sam_followup`. No synthetic data — the parity guarantee is "the migration produces identical output on the opps these templates are actually run on."
2. For each `_v2` template:
   - **v1 path:** runs the existing pipeline (visit-stage cache), then applies a **Python port of `groupVisitsByChild`** + the per-template KPI computations. The Python port is single-purpose test code — it exists only to validate parity, gets deleted along with v1.
   - **v2 path:** runs the entity-stage pipeline, reads `ComputedEntityCache` rows.
3. Canonicalizes both outputs into the same shape (sort by `entity_id`, normalize date formats, coerce empty string → null).
4. Diffs field-by-field with explicit tolerance rules:
   - Floats: equal within `1e-6` for raw values, `1e-2` for derived ratios (e.g. `avg_weight_gain_per_week`).
   - Dates: ISO date strings only; reject datetime-vs-date drift.
   - Nulls: treat `None`, `""`, missing key as equivalent.
   - List fields (`visits`): order by `visit_id`, then deep-equal.
5. Reports the first N divergences per opp with a readable diff, not a stack trace.

This harness is the gate. Until it passes for opp 874 (and 879 for `sam_followup`), v2 is not promoted.

### Layer 3: Manual UI parity

The two templates show up in the workflow list as `KMC Longitudinal` and `KMC Longitudinal (v2)`. A reviewer creates a run on the same opportunity for each, eyeballs:

- KPI card numbers
- Table row counts and ordering
- Chart shapes
- Filters and search behavior
- Per-child drill-down (sam_followup specifically — photos, audit creation)

Captured as a checklist in the migration PR, not a formal test.

### Promotion criterion (per template)

A `_v2` template promotes to replace v1 when **all three** are true:

1. Layer-2 parity harness passes on every opp in the fixture list with no unexplained diffs.
2. Layer-3 manual sign-off recorded in the PR (one reviewer who's used the v1 dashboard before).
3. The template has been the default for at least one full run cycle on a real opp without complaint.

After promotion: delete v1 template file, delete the Python port of `groupVisitsByChild` from the test harness, drop `_v2` from the v2 template's name (rename file + update registrations).

### What we explicitly are *not* testing

- Performance regressions. We expect entity stage to be faster than JS shaping; if it's somehow slower for a specific opp, that's worth knowing but doesn't block parity sign-off.
- Render-code visual changes. Charts may render slightly differently because data flows through fewer transformations; visual diffs aren't blockers if KPIs match.
- Backwards compatibility of saved runs. v1 runs frozen before this work continue to render via v1 code. Once v1 is deleted, those runs become legacy (per the snapshot doc's migration plan); they don't need to render under v2.

## Decisions

1. **Uniqueness — match the FLW pattern, no DB constraint.** `ComputedEntityCache` rows are kept de-facto unique by the same `DELETE all rows for (opp, config_hash) + bulk_create` transaction that `ComputedFLWCache` uses. No `UNIQUE(opp_id, config_hash, entity_id)` constraint, primarily to avoid surprising behavior around NULL/empty `entity_id` values from visits where the linking-field path didn't extract. Add the constraint later if duplicate-row bugs ever appear that the existing tests don't catch.
2. **`linking_field` is a single string for v1.** No composite-key support. None of the current templates need it; string concatenation is an easy escape hatch if a future template does.
3. **Histograms work at entity stage by reuse.** `_build_histogram_fields` doesn't reference `username` directly, so it should compose with the new GROUP BY for free. One parity test locks it in; no special-case code.
4. **`first`/`last` tiebreaker is `visit_id ASC` at both stages.** The `_aggregation_to_sql` refactor that parameterizes the group column also makes the tiebreaker a single shared decision instead of two. Entity stage inherits the same ordering FLW stage uses today.
5. **Stale-cache cleanup is handled by TTL.** When a template flips `terminal_stage` from `VISIT_LEVEL` or `AGGREGATED` to `ENTITY`, its old cache rows have a different effective shape and roll off via the existing TTL within a day or two. No special migration cleanup pass.
