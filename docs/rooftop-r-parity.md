# Rooftop Surveys — R-pipeline parity / completeness

Answers "did we recreate everything from the original R code, on the labs side?"
Maps each capability of the original R pipeline (Clustering+Randomizing, Daily
Monitoring, Analysis) to its status in the labs `rooftop_surveys` app.

**Framing:** the labs app rides Connect microplanning (Connect owns FLW
assignment, mobile delivery, visit status, the inaccessibility/substitution
flow). So we only (re)build in labs what Connect does **not** do: the
building-level sampling design, and the rooftop-specific monitoring/QC analytics.

## CREATE — sampling design (`sampling/`)

| R capability | Status | Where |
|---|---|---|
| Confidence ≥ 0.7 filter | ✅ built | `filters.py` (applied in the Overture query) |
| Tiny-roof <9m² drop unless ≥2 neighbors within 12m | ✅ built | `filters.py` |
| Large-roof >330m² drop | ✅ built | `filters.py` |
| k-means + merge clusters <16 into nearest centroid | ✅ built | `cluster.py` |
| radius95 compactness | ✅ built | `cluster.py` |
| `distance_to_visit` + per-cluster coverage (pct_50/75/le_400) | ✅ built (needs a `reference_point`) | `cluster.py` |
| High/Medium/Low stratification (exact R thresholds) | ✅ built; falls back to single "Low" pool w/o a reference point (the pilot baseline) | `cluster.py` |
| PPS-systematic PSU selection (∝ building count) | ✅ built | `sample.py` |
| Within-PSU spatial thinning, 8 primary + 8 alternate ≥15m apart | ✅ built | `sample.py` |
| Design inclusion weights (P_psu, Pi, weight=1/Pi) | ✅ built (primaries) | `sample.py` |
| Building footprints source | ✅ built — Overture via DuckDB/S3 (the R consumed a precomputed CSV; connect-gis used GEE/Overture) | `footprints.py` |
| Admin-boundary area picker (global) | ✅ built — Overture divisions (region/county/locality) | `boundaries.py` |
| Stratified **allocation** (Medium FieldTest/Pilot split, Backup High PSU) | ⬜ not yet — selection draws target_clusters from one pool | follow-up |
| `ward_density` k-tuning + adaptive threshold-derivation grids | ⬜ not yet — a design-time tuning tool; can stay offline | follow-up |
| KML / My-Maps exports | ➖ N/A — replaced by push-to-Connect (work areas) + the setup map |

## MONITOR — daily analytics (`monitoring/`) — none of this is in Connect

| R capability (derive_status.R etc.) | Status | Where |
|---|---|---|
| 15m GPS gate `reached_le15` | ✅ built | `derive.py` |
| `believed_reached` operator override | ✅ built | `derive.py` |
| `cannot_reach` barrier, `proceed_when_believed` (GPS issue) | ✅ built | `derive.py` |
| `completed`, `revisit_required`, `attempt_n` | ✅ built | `derive.py` |
| Per-cluster GPS accuracy/issue/barrier/target-occupied/completion rates | ✅ built | `rollups.py` |
| Believed-at-pin distance bands (16-25/26-50/>50) | ✅ built | `rollups.py` |
| What-was-found (inhabited/empty/nonresidential/uninhabited) | ✅ built | `rollups.py` |
| Fallback/substitution usage | ✅ built | `rollups.py` |
| Per-FLW-per-day productivity (`build_enum_daily`) | ✅ built | `rollups.py` |
| Time-to-completion bins (<10/10-15/15-20/20-30/>30) | ✅ built | `duration.py` |
| GPS-issue review report (believed & >25m, w/ screenshot link) | ✅ built | `gps_issue.py` |
| Field→canonical mapping (cleaning.R apply_mapping) | ✅ built (configurable field_map) | `normalize.py` |
| Intervention-vs-comparison split | ✅ built | `pipeline.py` (by_arm) |
| Live ingest from Connect export API (`/user_visits/`) | ⬜ wiring pending — pipeline is source-injected + tested; needs a real opp w/ rooftop visits to validate the field_map | follow-up |
| Per-target status table (`build_targets_status` first-vs-revisit detail) | ◐ partial — core flags built; the full per-target table is a follow-up | follow-up |

## ANALYZE / QC (`qc/`) — not in Connect

| R capability (Data_Analysis.R / Sampling_Quality_Analysis.R) | Status | Where |
|---|---|---|
| Validation cascade (older-services 8+ / confidence / phone → visit_validated) | ✅ built | `qc/cascade.py` |
| Per-ward reported→validated drop report | ✅ built | `qc/cascade.py` |
| older-services violations from member-level data | ✅ built | `qc/cascade.py` |
| Sampling-quality QC (Google confidence/area × outcome χ², coordinate-upload mismatch, free-text auto-coder) | ⬜ not yet — one-shot analytic | follow-up |
| De-identified analyst export | ⬜ not yet | follow-up |

## Bottom line

Create + monitor + the headline QC cascade are reproduced faithfully and unit-
tested (42 tests pinning exact thresholds/rates). Remaining follow-ups are
refinements (stratified allocation, ward-density tuning, the per-target detail
table, sampling-quality χ²/text-coding, de-id export) and the live ingest
wiring (gated on a real rooftop opp with submissions). None block the core
"generate a frame and monitor it" loop.
