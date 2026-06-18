# High-fidelity synthetic generator + clone KMC opps into one labs-only program

**Date:** 2026-06-18
**Status:** Design — pending review
**Author:** Jonathan Jackson (with Claude)

## 1. Goal

Two-part, in priority order:

**Part A — upgrade the core synthetic generator to high fidelity.** The data will be used
to **test an AI data-analytics system**, so the synthetic data must be statistically close
to real at the *joint* level, not just per-field marginals. Stay **fully synthetic** (zero
real records, no PII) but reproduce categorical distributions, inter-field correlations,
missing-data patterns, and temporal shape.

**Part B — clone the 11 `Dimagi-KMC` opps** (523, 524, 675, 874, 938, 1234, 1236, 1487,
1488, 1739, 1790) into labs-only synthetic opps grouped under **one shared program**
"KMC (Synthetic)", each serving a copy of the real app structure + high-fidelity synthetic
data produced by the upgraded generator.

This is **Layer A only** — export fixtures (GDrive), no `LabsLocalRecord` / workflow seeding.

## 2. Why fidelity is the core problem (evidence)

Audit of the current profiler/generator (`generator/fixtures/`): it is **faithful at the
marginal level but naive about the joint distribution** — exactly the structure an analytics
system exists to find.

| Property | Today | Target |
|---|---|---|
| Numeric field mean/std | FAITHFUL (`profiler._profile_field_distributions`) | keep (becomes copula margins) |
| Per-FLW volume, approval/flag rates, date range, schema coverage | FAITHFUL | keep |
| **Categorical field values** | INVENTED — random choice (`fields._default_for_kind`) | profile real frequencies |
| **Inter-field correlations** | NOT REPRODUCED — independent `rng.gauss` per field | Gaussian copula |
| **Missing/blank patterns** | NOT REPRODUCED — fills every field | per-field null rate |
| **Day-of-week / hour-of-day** | uniform / fixed (engine hardcodes 11:00/12:00) | profiled histograms |
| **Per-week trend** | flat (`profiler` hardcodes `progression="flat"`) | profiled weekly curve |
| **Flag reasons** | generic hardcoded list (`status.py`) | profiled distribution |

Testing an analytics system on marginal-only data yields *false confidence* (it runs,
per-field stats look right) while it cannot surface — and you cannot validate — the
multivariate relationships that are the point of analytics. Libraries confirmed available:
**numpy 2.4.3, scipy 1.17.1, pandas 3.0.1**.

## 3. Scope boundary

Layer A (export fixtures → GDrive), fully synthetic. No `LabsLocalRecord`, no workflow/
audit/task seeding, ensure/env system untouched. No de-identified real data (rejected in
favor of fully-synthetic). No runnable CCZ clones (app-structure JSON only).

---

## 4. Part A — generator fidelity upgrade

The upgrade is additive and back-compatible: new manifest fields are optional, old manifests
still validate, the generator falls back to current behavior when a richer stat is absent.

### 4.1 Profiler (`generator/fixtures/profiler.py`)

Use the real `app_structure` (now available in the clone pipeline, §5.1) to classify each
schema path as numeric / categorical / binary / date / image, instead of guessing by
sampling. Then extract:

- **Categorical frequencies** — per categorical path, `Counter` of observed values → `{value: rate}`.
- **Per-field null/presence rate** — fraction of visits where the path is present & non-empty.
- **Numeric margins** — keep mean/std (also min/max + a few empirical quantiles for non-normal margins).
- **Correlation matrix** — build a per-visit matrix over "analytic" fields (numeric +
  ordinal-encoded categoricals) with sufficient coverage; compute **Spearman** rank
  correlation (pandas); project to nearest PSD via eigenvalue clipping (numpy `eigh`). Store
  the ordered field list + matrix + per-field margin handles.
- **Temporal histograms** — day-of-week (7 weights) and hour-of-day (24 weights) from real `visit_date`/timestamps.
- **Per-week volume curve** — real visits-per-week series → populate `progression` (replaces hardcoded "flat").
- **Flag-reason distribution** — if `flag_reason` present, histogram (global, optionally per-FLW).
- Keep existing per-FLW archetype/cadence extraction.

### 4.2 Manifest schema (`generator/fixtures/manifest.py`)

Additive, optional fields:

- `CategoricalDistribution { kind: "categorical", values: {value: rate}, null_rate }`.
- `null_rate` on every `FieldDistribution`.
- Cohort-level `correlation { fields: [paths], matrix: [[...]], method: "spearman" }` (the copula spec).
- `temporal { day_of_week: [7], hour_of_day: [24] }`.
- `progression`: real per-week multipliers (knob already exists; now populated).
- `flag_reason_distribution` (cohort and/or per-persona).

### 4.3 Generation

- **Copula sampler** (new `generator/fixtures/copula.py`): given the correlation block +
  per-field margins, draw one correlated vector per visit — `z ~ N(0, Σ)` via Cholesky of
  the PSD matrix, `u = Φ(z)`, then map each component through its margin (numeric →
  inverse-CDF of the margin; categorical → cumulative-frequency threshold on `u`). Preserves
  both marginals and rank-correlation. `fields.fill_form_json` uses it for the correlated
  subset; uncorrelated/unprofiled fields fall back to current independent draws.
- **Categorical draws** — cumulative sampling from `CategoricalDistribution` (replaces random choice).
- **Missing data** — after sampling, omit each field with probability `null_rate`.
- **Temporal** — `timeline.py` picks weekday from `day_of_week` weights (not uniform); `engine.py` picks hour/minute from `hour_of_day` (not fixed).
- **Per-week volume** — apply `progression` curve to weekly counts.
- **Flag reasons** — `status.py` samples from `flag_reason_distribution`.

### 4.4 Fidelity report (so the data is trustworthy before testing)

New `generator/fixtures/fidelity.py` + tool `synthetic_fidelity_report(opportunity_id)`:
after generation, compare **synthetic vs. the real profile** and emit a scorecard —
per-field marginal divergence (KS for numeric, chi-square/TVD for categorical), correlation
matrix distance (Frobenius), null-rate deltas, temporal-histogram deltas. This lets you
*trust* the synthetic set before running your analytics system on it, and catches
regressions when the generator changes.

---

## 5. Part B — clone orchestration (delivery)

Thin layer that runs the upgraded generator over the 11 opps under one program.

### 5.1 Capture `app_structure.json` (core gap, also needed by the profiler)
`engine.generate()` retains the fetched `{learn_app, deliver_app}` wrapper; `uploader.py`
writes `app_structure.json`; `dump.py` likewise. Serving already works (`AppStructureView` +
`FixtureStore`). The profiler (§4.1) consumes the same wrapper for field typing.

### 5.2 Shared registration helper (DRY)
New `synthetic/provisioning.py`: `register_labs_only_opp(...)` — idempotent
`update_or_create` on `opportunity_id`, **only overwriting explicitly-passed keys** (never
clobbers an existing `gdrive_folder_id`/`program_id`); allocates the id when omitted; sets
`labs_only=True`; invalidates cache. Plus `allocate_shared_program_id()`. Refactor
`synthetic_create_labs_only` / `synthetic_clone_to_labs_only` onto it.

### 5.3 Model
`SyntheticOpportunity.cloned_from_opportunity_id` (nullable, indexed; + migration) for
idempotency + provenance.

### 5.4 Clone service (`synthetic/clone_from_prod.py`)
- `clone_opp_from_prod(source_opp_id, *, oauth_token, program_id, program_name, org_name, label=None, fresh=False)` → fetch real prod (detail, visits, users, **app_structure**) → profile (upgraded) → generate (upgraded) → upload 6 fixtures → `register_labs_only_opp(cloned_from=…)` → refresh `visit_count`. Skips if already cloned unless `fresh`.
- `clone_opps_bulk(source_ids, *, oauth_token, program_name="KMC (Synthetic)", org_name="Dimagi-KMC (Synthetic)", fresh=False)` → `allocate_shared_program_id()` → loop, isolating per-opp failures.

`context.py` already collapses opps sharing a `program_id` into one program (context.py:73-77) — "one program, 11 opps" needs no further change.

### 5.5 Entry points
Primary: MCP tools `synthetic_clone_from_prod`, `synthetic_clone_from_prod_bulk`,
`synthetic_fidelity_report` — run as the authenticated user (token already has access to all
11). Optional: `manage.py clone_prod_opps_to_labs_only` for a CI path.

---

## 6. Phasing / decomposition

Two cleanly separable sub-projects with a clear interface (the upgraded generator + manifest):

- **Plan 1 — generator fidelity upgrade (Part A).** Self-contained, valuable independent of
  the 11 opps; ships profiler + manifest + copula + generation + fidelity report, tested
  against a real opp profile. This is the substance.
- **Plan 2 — clone orchestration (Part B).** Thin once Plan 1 lands; app_structure capture +
  shared helper + `cloned_from` + service + MCP + run the 11.

Recommended order: Plan 1 first (with the fidelity report proving it), then Plan 2.

## 7. Idempotency, cross-cutting, testing

- **Idempotency:** clone keyed on `cloned_from_opportunity_id`; re-run skips unless `fresh`; helper never clobbers grouping/folder.
- **Auth/PII:** clone reads prod with the caller's OAuth `export` scope (user has access to all 11). Output is fully synthetic — no real records leave prod; only aggregate statistics (distributions, correlation matrix) cross into the manifest. *(Note: a correlation matrix + per-field histograms are aggregate stats, not row-level data.)*
- **Determinism:** all draws seeded (`random_seed`); copula uses a seeded numpy `Generator` so runs are reproducible.
- **Naming:** `[Synthetic] <real name>`, `program_name="KMC (Synthetic)"`, `org_name="Dimagi-KMC (Synthetic)"`.
- **Testing:**
  - Profiler: categorical frequencies, null rates, Spearman matrix (PSD-projected), temporal histograms extracted from a fixture export.
  - Copula: generated sample reproduces target marginals (KS) AND target correlation (Frobenius within tolerance); PSD projection handles non-PSD pairwise input.
  - Missing data: synthetic null rate ≈ profiled null rate.
  - Categorical: synthetic category frequencies ≈ profiled.
  - Fidelity report: returns expected metrics on a known synthetic-vs-real pair.
  - Clone: writes `app_structure.json`; `labs_only=True` row with shared `program_id` + `cloned_from`; re-run skips; bulk isolates one failure; picker collapses to one program (context.py test).
  - macOS pytest needs `GDAL_LIBRARY_PATH` / `GEOS_LIBRARY_PATH` exported.

## 8. File-by-file

| File | Change |
|---|---|
| `generator/fixtures/profiler.py` | categorical freqs, null rates, Spearman corr matrix (PSD), temporal histograms, weekly curve, flag-reason dist; use app_structure for field typing |
| `generator/fixtures/manifest.py` | `CategoricalDistribution`, `null_rate`, cohort `correlation`, `temporal`, populated `progression`, `flag_reason_distribution` (all optional) |
| `generator/fixtures/copula.py` | **NEW** — Gaussian copula sampler (numpy/scipy) |
| `generator/fixtures/fields.py` | copula-driven correlated draws; categorical sampling; null-rate omission |
| `generator/fixtures/status.py` | sample flag_reason from distribution |
| `generator/fixtures/timeline.py` | weekday from histogram; weekly progression curve |
| `generator/fixtures/engine.py` | hour/minute from histogram; retain + return app_structure |
| `generator/fixtures/fidelity.py` | **NEW** — synthetic-vs-real scorecard |
| `generator/io/uploader.py` | write `app_structure.json`; register via shared helper |
| `synthetic/dump.py` | fetch + upload `app_structure.json` |
| `synthetic/provisioning.py` | **NEW** — `register_labs_only_opp`, `allocate_shared_program_id` |
| `synthetic/models.py` (+ migration) | `cloned_from_opportunity_id` |
| `synthetic/clone_from_prod.py` | **NEW** — `clone_opp_from_prod`, `clone_opps_bulk` |
| `mcp/tools/synthetic.py` | `synthetic_clone_from_prod`, `synthetic_clone_from_prod_bulk`, `synthetic_fidelity_report`; refactor create/clone onto the helper |
| `labs/management/commands/clone_prod_opps_to_labs_only.py` | **NEW** (optional) |
| tests | as in §7 |

## 9. Open decisions (confirm at review)

1. **Split into Plan 1 (fidelity upgrade) + Plan 2 (clone), or one plan?** (Recommend split; build the fidelity upgrade first and prove it with the report.)
2. **Correlation scope** — copula over numeric + ordinal-encoded categoricals (recommended), or numeric-only first with categoricals independent? (Numeric-only is simpler but leaves the biggest categorical relationships unmodeled.)
3. **Geography** — leave manual/empty for now (deferred), or add basic GPS-density profiling? (Recommend defer unless the analytics system is spatial.)
4. MCP tools only, or also the management command?

## 10. Out of scope

- Layer B (`LabsLocalRecord`/workflow/audit/task) seeding — ensure/env untouched.
- De-identified or verbatim real data — fully synthetic only.
- Runnable CCZ/HQ app clones — app-structure JSON only.
- Geography-from-GPS and image-content fidelity (deferred; §9.3).
