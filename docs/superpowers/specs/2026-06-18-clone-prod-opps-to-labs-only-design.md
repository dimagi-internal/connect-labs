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

**Each opp is profiled and generated independently from its own real data** — there is no
shared "KMC" profile. Opp 523's correlations/categoricals/temporal shape come from opp 523's
real visits, opp 1790's from opp 1790's, and so on. The *only* thing shared across the 11 is
the program grouping (`program_id` + name).

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

## 3.1 Two-phase architecture: profile (safe-mode) → generate (unsafe-mode)

The pipeline splits into two phases with a persisted **per-opp profile bundle** as the
hand-off, so the prod-touching "get what we need" work can run under restricted permissions
("safe mode") and the heavy generation can run separately in full mode ("unsafe mode")
**without touching prod again**.

**Phase 1 — Profile (prod-touching, minimal).** For each opp: fetch the real exports
(opportunity detail, `user_visits`, `user_data`) + `app_structure` with the caller's OAuth
token, compute the enriched profile, and write a **self-contained profile bundle** at
`<out_dir>/<source_opp_id>/`:
- `manifest.yaml` — aggregate stats only (personas, cohorts, categorical dists, numeric margins, **correlation matrix**, null rates, temporal histograms, flag-reason dist).
- `app_structure.json` — the real `{learn_app, deliver_app}` form schema (program config, not PII).
- `opportunity.json` — opp metadata scrubbed to non-PII fields (no FLW/beneficiary rows).

Raw prod rows are held only transiently in memory; the bundle persists **only aggregate
stats + program config** — this is the privacy boundary. Phase 1 generates nothing and
uploads nothing.

**Phase 2 — Generate (offline, zero prod calls).** For each opp: read its bundle, run the
upgraded generator (copula etc.), produce the 6 export fixtures, upload to GDrive, register
the labs-only opp under the shared program. Re-runnable freely; this is where the bulk
compute happens.

**Hard requirement:** the bundle must be **self-contained**. Today
`generate_from_manifest` re-fetches opportunity detail + form schema from prod mid-generation
— we change generation to read those from the bundle so **Phase 2 needs no token and no
network**. This is what makes the safe-mode → unsafe-mode handoff clean.

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

New `generator/fixtures/fidelity.py` + tool `synthetic_fidelity_report(bundle_dir)`:
after generation, compare **synthetic vs. the bundle's profile** and emit a scorecard —
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

### 5.4 Clone service (`synthetic/clone_from_prod.py`) — split on the phase boundary

**Phase 1 (prod-touching):**
- `profile_opp_to_bundle(source_opp_id, *, oauth_token, out_dir) -> Path` — fetch real prod (detail, visits, users, **app_structure**) → enriched profile → write `manifest.yaml` + `app_structure.json` + scrubbed `opportunity.json` into `out_dir/<source_opp_id>/`. No generation, no DB, no GDrive. Returns the bundle dir.
- `profile_opps_bulk(source_ids, *, oauth_token, out_dir) -> list[Path]` — loop, isolating per-opp failures.

**Phase 2 (offline, no prod):**
- `generate_opp_from_bundle(bundle_dir, *, program_id, program_name, org_name, source_opp_id, label=None, fresh=False) -> CloneResult` — read bundle → upgraded generator → upload 6 fixtures (incl. `app_structure.json`) → `register_labs_only_opp(cloned_from=source_opp_id)` → refresh `visit_count`. Skips if already cloned unless `fresh`. **Makes no prod calls.**
- `generate_opps_bulk(bundle_root, *, program_name="KMC (Synthetic)", org_name="Dimagi-KMC (Synthetic)", fresh=False) -> BulkResult` — `allocate_shared_program_id()` → loop bundles, isolating per-opp failures.

`context.py` already collapses opps sharing a `program_id` into one program (context.py:73-77) — "one program, 11 opps" needs no further change.

### 5.5 Entry points (split along the phase boundary)
- **Phase 1 (safe-mode, the only prod-touching tools):** `synthetic_profile_opp(source_opportunity_id, out_dir)` and `synthetic_profile_opps_bulk(source_opportunity_ids, out_dir)` — run as the authenticated user (token has access to all 11). Output: profile bundles on disk.
- **Phase 2 (unsafe-mode, offline):** `synthetic_generate_opp(bundle_dir, program_id?, …)` and `synthetic_generate_opps_bulk(bundle_root, program_name?, org_name?, fresh?)`.
- `synthetic_fidelity_report(bundle_dir)` — compares generated fixtures vs. the bundle's profile (offline).
- Optional `manage.py` equivalents per phase for a CI path.

---

## 6. Phasing (one combined plan)

**Decision: one combined plan** covering Part A then Part B, sequenced internally so the
fidelity work lands and is proven before the clone layer consumes it:

1. Part A — profiler + manifest + **copula (numeric + ordinal-encoded categorical)** + generation + fidelity report; proven against a real opp profile via the fidelity report.
2. Part B — bundle write/read + app_structure capture + shared `register_labs_only_opp` helper + `cloned_from` + two-phase clone service + MCP tools + run the 11 under one program.

Internal interface between the two halves: the upgraded generator + manifest. The **runtime
split is profile (Phase 1, prod) vs. generate (Phase 2, offline)** per §3.1 — the build
sequences so Phase 2's generator (the bulk of Part A) is done and tested before the thin
Phase 1 fetch + bundle writer wraps it.

## 7. Idempotency, cross-cutting, testing

- **Idempotency:** clone keyed on `cloned_from_opportunity_id`; re-run skips unless `fresh`; helper never clobbers grouping/folder.
- **Auth/PII (and the safe/unsafe boundary):** only **Phase 1** reads prod (caller's OAuth `export` scope; user has access to all 11). It persists **only** the profile bundle — aggregate stats (distributions, a correlation matrix, histograms = not row-level data) + program config (app_structure, scrubbed opp metadata). **Phase 2 makes zero prod calls** and runs entirely off the bundle, so generation can run in full/unsafe mode with no token. No real records ever leave prod.
- **Determinism:** all draws seeded (`random_seed`); copula uses a seeded numpy `Generator` so runs are reproducible.
- **Naming:** `[Synthetic] <real name>`, `program_name="KMC (Synthetic)"`, `org_name="Dimagi-KMC (Synthetic)"`.
- **Testing:**
  - Profiler: categorical frequencies, null rates, Spearman matrix (PSD-projected), temporal histograms extracted from a fixture export.
  - Copula: generated sample reproduces target marginals (KS) AND target correlation (Frobenius within tolerance); PSD projection handles non-PSD pairwise input.
  - Missing data: synthetic null rate ≈ profiled null rate.
  - Categorical: synthetic category frequencies ≈ profiled.
  - Fidelity report: returns expected metrics on a known synthetic-vs-bundle pair.
  - **Bundle / phase split:** Phase 1 writes a self-contained bundle (`manifest.yaml` + `app_structure.json` + scrubbed `opportunity.json`); **Phase 2 generates from the bundle with the prod-fetch helper patched to raise** — proving zero prod calls in generation.
  - **Per-opp independence:** two different source profiles produce different manifests / correlation matrices (no shared state leaks across opps in a bulk run).
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
| `generator/fixtures/engine.py` | hour/minute from histogram; **accept app_structure + opportunity detail as inputs (from the bundle), not fetched** — so Phase 2 needs no prod |
| `generator/fixtures/fidelity.py` | **NEW** — synthetic-vs-bundle scorecard |
| `generator/io/uploader.py` | write `app_structure.json`; register via shared helper |
| `synthetic/bundle.py` | **NEW** — write/read the per-opp profile bundle (`manifest.yaml` + `app_structure.json` + scrubbed `opportunity.json`); the safe/unsafe handoff format |
| `synthetic/dump.py` | fetch + upload `app_structure.json` |
| `synthetic/provisioning.py` | **NEW** — `register_labs_only_opp`, `allocate_shared_program_id` |
| `synthetic/models.py` (+ migration) | `cloned_from_opportunity_id` |
| `synthetic/clone_from_prod.py` | **NEW** — Phase 1: `profile_opp_to_bundle`, `profile_opps_bulk`; Phase 2: `generate_opp_from_bundle`, `generate_opps_bulk` |
| `mcp/tools/synthetic.py` | Phase 1: `synthetic_profile_opp`, `synthetic_profile_opps_bulk`; Phase 2: `synthetic_generate_opp`, `synthetic_generate_opps_bulk`, `synthetic_fidelity_report`; refactor create/clone onto the helper |
| `labs/management/commands/synthetic_profile_opps.py`, `synthetic_generate_opps.py` | **NEW** (optional) — one command per phase |
| tests | as in §7 |

## 9. Decisions

- **One combined plan** (Part A then Part B), not split. *(decided)*
- **Correlation scope: full Gaussian copula over numeric + ordinal-encoded categorical fields.** *(decided)*
- **Geography: deferred** — leave manual/empty for now (revisit only if the analytics system is spatial). *(decided)*
- **Entry points:** MCP tools are the primary/required surface; the management command is an optional add for a CI path (can be dropped during implementation if not needed).

## 10. Out of scope

- Layer B (`LabsLocalRecord`/workflow/audit/task) seeding — ensure/env untouched.
- De-identified or verbatim real data — fully synthetic only.
- Runnable CCZ/HQ app clones — app-structure JSON only.
- Geography-from-GPS and image-content fidelity (deferred; §9.3).
