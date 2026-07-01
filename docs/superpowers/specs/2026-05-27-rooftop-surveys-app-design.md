# Rooftop Surveys — New Labs App Design

**Status:** Proposal — no code yet. This document captures the full picture; implementation will land in staged PRs.
**Author:** Claude (with jjackson)
**Why connect-labs and not ACE:** The work currently happens in two places — a hand-run R pipeline that produces sampling and daily monitoring CSVs, and an ACE-side opportunity (`rooftop-surveys`) that wants to turn that into a turnkey Connect offering. jjackson's direction (2026-05-27): "we want to build this all into the product as a new app in connect-labs." So instead of growing ACE skills or `connect-labs` MCP atoms in isolation, we land a first-class Labs app — `rooftop_surveys/` — that owns the data model, the UI, the workflow templates, and the MCP tool surface. ACE consumes it just like any other Labs capability.
**Related:**
- ACE opp: `ACE/rooftop-surveys/` (concept note + source materials live there).
- Existing precedents in this repo: `coverage/` (external data ingest pattern), `custom_analysis/` (per-program dashboards), `workflow/templates/mbw_monitoring_v3.py` (live monitoring), `workflow/templates/performance_review.py` (saved-runs lifecycle), `workflow/templates/program_admin_report.py` (multi-opp rollup).

---

## 1. Goal

Turn the Rooftop Sampling methodology — currently a hand-run R + analyst loop, demonstrated in the Fall 2025 CCC-CHC pilot — into a turnkey Labs offering that any program admin can operate end-to-end without a statistician in the room:

1. **Pick an area** (intervention polygon + optional comparison polygon, by admin boundary or hand-drawn).
2. **Generate a sampling frame** (Google Open Buildings + filtering + k-means PSU clustering + PPS systematic sampling within strata) — produces an opp-scoped pin assignment per FLW.
3. **Push pins to a Connect opportunity** so FLWs see them in their app, navigate, and submit short surveys.
4. **Watch live monitoring** as visits land — per-target status, per-FLW productivity, per-cluster coverage, GPS adherence, fallback reasons, time-to-completion, intervention-vs-comparison comparison.
5. **Apply QC** — validation-cascade filters, back-check sampling, audio audit queue, FLW behavioral anomaly detection.
6. **Export** a de-identified analyst-ready dataset and a summary report.

The R pipeline currently delivers ~steps 2 and 4. Steps 1, 3, 5, 6 are net-new product surface.

## 2. Out of scope (v1)

- **A statistical-design wizard.** Power calculations, design-effect tuning, sample-size recommendation given a target MDE — these are CLI/notebook tools today and stay that way for v1. The app accepts the design (number of clusters, households-per-cluster, primary+alternate count) as input, not output.
- **Multi-country admin boundary library.** v1 ships with Nigeria ward / LGA boundaries from GADM cached in the repo. Other countries on demand.
- **Custom QC anomaly authoring.** v1 ships a small library of opinionated FLW-side detectors (gender skew, time clustering, duration outliers, GPS-stationarity). Users opt in; they don't write new ones.
- **Real-time map (live-streaming pin updates).** v1 dashboards refresh per scheduled pipeline run (every 15-60 minutes), not per-submission websocket push.
- **Re-randomization / re-stratification mid-survey.** Once pins are pushed, the assignment is frozen for the run. Re-sampling means creating a new run.
- **A SurveyCTO/ODK-style form builder.** The survey instrument is authored in the existing CommCare app for the opp; this app does not own form design.

## 3. Source materials (deep-scan summary)

Everything below is grounded in:

- **The concept note** (`Connect for Continuous Program Monitoring`) — the funder-facing pitch.
- **CCC Better Jobs and Orgs: Rooftop Sampling** — internal scoping doc, LDVP framing, two GPT-authored case studies (sample-size walkthroughs).
- **RooftopSampling_Briefs** (90K-char retrospective) — the Fall 2025 CCC-CHC pilot writeup, Nigeria, 5 wards in Sokoto / Gombe / Borno, 996 surveys, 1,846 children U5, LLO partners Solina + PPFN. **Key empirical numbers:** Google Open Buildings + per-ward roof-confidence threshold 0.60–0.75; tiny-roof filter <9 m² (with neighbor-exception); large-roof filter >330 m²; k-means k ∈ [45, 125] → final ~25 clusters; 8 primary + 8 alternates per cluster; 15m GPS-accuracy gate AND 15m of-target gate; 75% overall navigation success (range 56–98% by ward); dominant failure mode is "human" (operator-asserted "I believe I have arrived") not "physical barrier" (5%).
- **R script catalogue:** three sub-folders that map cleanly onto setup / monitoring / wrap-up:
  - `Clustering and Randomizing/` — `clustering_pipeline_10-14-25_gwange_combined.R` (k-means + min-merge + PPS + strata), `ward_density_analysis_script.R` (k tuning + stratum thresholds).
  - `Daily Data Processessing for Monitoring/` — `cleaning.R`, `derive_status.R`, `main_processing.R` (+ a `_restored` sibling), `time_to_completion.R`, `utils_io.R`, `write_outputs.R`. **This is the operational core that turns into a labs pipeline + workflow template.**
  - `Analysis/` — `1. Data_Cleaning.R`, `2. Sampling_Quality_Analysis.R`, `3. Data_Analysis.R` (3 scripts too large to base64-decode in one shot; their behavior is partially documented in the RooftopSampling_Briefs validation-cascade section).
- Two PPTX decks (training slides + a pitch deck) — not load-bearing for this design.
- One inaccessible Google Doc ("Example: Potential Offering") — flagged.

## 4. Concept-note delta — what we're actually shipping

Cross-checking the public concept note against what exists today:

| Concept-note promise | Status today | What this app needs to ship |
|---|---|---|
| "Define an intervention area and a comparison area" | Hand-drawn in QGIS, fed as shapefile to R | UI + atom: pick admin boundary OR hand-draw polygon |
| "Geospatial analysis of household structures to sample…" | `clustering_pipeline_...R` | Python port + UI flow |
| "Each FLW provided with a unique set of households" | Hand-merged CSV pushed to Connect | Atom that writes assignment to Connect opp |
| "Photos / list-select / audio / GPS / timestamp" | Built into the CommCare form for the opp | (no labs change — instrument lives in HQ) |
| "Sample of completed surveys auto-flagged for QC" | None — pilot did post-hoc filter cascade in `Sampling_Quality_Analysis.R` | New `rooftop_qc_*` family |
| "Audio audits / back-checks" | None | New `rooftop_qc_*` family |
| "Real-time dashboard, intervention vs comparison" | R writes dated CSVs to a folder; analyst reads them | Saved-runs workflow template + map UI |
| "Statistical models flag FLW behavior (gender skew)" | Aspirational — not in R pipeline. Borrows credibility from `kmc_flw_flags` and the briefs' validation cascade | Small library of FLW-side detectors |
| "Pay only for verified work" | Concept; not wired in this codebase | Out of scope for v1 — the verification flags this app produces *enable* this on the Connect side, but the pay-gate wiring is a separate effort |
| "Final outputs: de-identified dataset + summary report" | Manual export | Atom + UI |

## 5. Why this is its own app, not bolted into `coverage/` or `custom_analysis/`

Two existing apps were candidates:

- **`coverage/`** does ingest building/delivery-unit data from CommCare HQ and renders maps. It's the closest geospatial precedent in the repo. But coverage is about *verifying delivery happened in the right places after the fact* — it ingests delivery-unit boundaries and joins them with submitted visits. Rooftop surveys is about *producing the sampling frame in the first place* — generating pins before any visits exist. Different lifecycle, different inputs, different consumers. Bolting one onto the other would mean every coverage user pays the import cost of geo-sampling code that doesn't apply to them.
- **`custom_analysis/`** holds per-program dashboards (CHC, KMC, MBW, RUTF). A natural-feeling home for "rooftop dashboards." But custom_analysis sub-apps are pure read views — they don't own *write* surfaces like "create sampling frame," "push pins to Connect," "trigger a back-check," "register a validation cascade." Rooftop has both read and write surfaces; pinning the write half onto a read-only convention is a structural mismatch.

A new app `rooftop_surveys/` is the right call. It can borrow `coverage`'s OAuth-to-HQ pattern (for fetching building footprints from BigQuery and pushing case data to a CommCare domain) and `custom_analysis`'s sub-app dashboard pattern (for the per-opp views), without forcing either to absorb the other's surface.

## 6. New app layout

```
connect_labs/rooftop_surveys/
├── __init__.py
├── apps.py
├── urls.py
├── views.py                  # area picker, frame review, run launcher, dashboards
├── data_access.py            # wraps LabsRecordAPIClient for rooftop_* record types
├── models.py                 # LocalLabsRecord subclasses (proxy models, @property)
├── api_views.py              # JSON endpoints for the React/htmx UI
├── mcp_tools.py              # MCP atom registrations (geo_*, rooftop_*, qc_*)
├── sampling/                 # the Python port of Stage A (R: Clustering and Randomizing/)
│   ├── __init__.py
│   ├── boundaries.py         # admin polygons (GADM Nigeria seed; plug-in for others)
│   ├── footprints.py         # Google Open Buildings via BigQuery (read-only)
│   ├── filters.py            # roof-area + confidence + neighbor-exception
│   ├── cluster.py            # k-means + min-cluster-merge + stratification
│   ├── sample.py             # PPS systematic sampling within strata + primary/alternate split
│   └── assign.py             # round-robin/load-balanced pin → FLW assignment
├── monitoring/               # the Python port of Stage B (R: Daily Data Processessing/)
│   ├── __init__.py
│   ├── ingest.py             # pull CC submissions (proxied via Labs export API)
│   ├── normalize.py          # schema mapping (R: cleaning.R, utils_io.R)
│   ├── derive.py             # per-attempt flags (R: derive_status.R)
│   ├── rollups.py            # per-target / per-FLW / per-cluster (R: derive_status.R cont.)
│   ├── duration.py           # time-to-completion bins (R: time_to_completion.R)
│   └── outputs.py            # writes LabsRecords + optional Drive CSV mirror
├── qc/                       # net-new
│   ├── __init__.py
│   ├── cascade.py            # generic N-filter validation engine (R: Sampling_Quality_Analysis.R)
│   ├── flw_anomaly.py        # gender skew, time clustering, duration outliers, GPS stationarity
│   ├── back_check.py         # sampling + Connect-side case push
│   └── audio_audit.py        # sampling + reviewer queue
├── tasks.py                  # Celery: scheduled monitoring run, footprint fetch
├── tests/
│   ├── fixtures/             # the Gwange/Tsaki ward extracts + a synthetic submissions CSV
│   ├── test_sampling_parity.py    # parity vs R outputs from the pilot
│   ├── test_monitoring_parity.py  # parity vs R outputs from the pilot
│   ├── test_qc.py
│   └── test_api.py
├── templates/
│   └── rooftop_surveys/      # htmx + alpine + mapbox panels
│       ├── area_picker.html
│       ├── frame_review.html
│       ├── run_dashboard.html
│       └── flw_panel.html
└── README.md
```

Plus three new files under `workflow/templates/`:

```
connect_labs/workflow/templates/
├── rooftop_monitoring.py            # the workflow that wraps Stage B + dashboard for funder/LLO viewers
├── rooftop_monitoring_render.js
└── rooftop_back_check.py            # the workflow that drives the back-check loop (Stage C)
```

No top-level `data_loader.py` (à la `coverage/`) is needed — Open Buildings is fetched on-demand inside `sampling/footprints.py`, cached per opp via `rooftop_psu` records, and never re-fetched in normal operation.

## 7. Data model — LabsRecord types

All persistence goes through `LabsRecordAPIClient` (no Django ORM writes). Per record-type conventions in CLAUDE.md, every record has `experiment = <opportunity_id>` to scope by opp and a `type` discriminator.

| `type`                    | `data` payload (sketch)                                                                          | Lifecycle |
|---------------------------|--------------------------------------------------------------------------------------------------|-----------|
| `rooftop_area`            | `{intervention_polygon: GeoJSON, comparison_polygon: GeoJSON\|null, admin_boundary_ref?: str}`   | One per opp run; immutable after frame generation |
| `rooftop_psu`             | `{cluster_id, ward, centroid: [lat,lon], building_count, occupancy_stratum, polygon: GeoJSON, source_arm: "intervention"\|"comparison"}` | Created in batch on frame generation; immutable |
| `rooftop_pin`             | `{pin_id, cluster_id, building_id, target_lat, target_lon, order_in_cluster, kind: "primary"\|"alternate"}` | Created in batch; immutable |
| `rooftop_assignment`      | `{pin_id, flw_username, assigned_at, opportunity_case_id?: str}`                                  | Created on push-to-Connect; mutable on re-assignment |
| `rooftop_visit_status`    | (derived; written by monitoring pipeline) `{pin_id, attempts: [...], latest_status, reached_le15, believed_reached, completed, revisit_required, fallback_reason}` | Upserted per monitoring run |
| `rooftop_flw_daily`       | `{flw_username, date, targets_visited, targets_completed, gps_issues, barrier_rate, median_duration_min}` | One per FLW per day |
| `rooftop_cluster_rollup`  | `{cluster_id, date, completion_rate, gps_accuracy_rate, fallback_success_rate, occupancy_observed}` | One per cluster per day |
| `rooftop_qc_finding`      | `{kind: "gender_skew"\|"time_clustering"\|"duration_outlier"\|"gps_stationary"\|"cascade_drop", subject: "flw"\|"visit"\|"cluster", subject_id, evidence: dict, severity: "info"\|"warn"\|"alert"}` | Append-only |
| `rooftop_back_check`      | `{primary_pin_id, back_check_pin_id, back_check_flw, status: "open"\|"completed"\|"discrepant", concordance?: dict}` | Updated through the back_check workflow |
| `rooftop_audio_audit`     | `{visit_submission_id, sample_reason, reviewer, decision, notes}`                                | Updated through review action |
| `rooftop_export`          | `{exported_at, scope, file_url, sha256, deidentified: bool}`                                     | Append-only audit trail |

Proxy models in `models.py` expose `@property` getters for each field. No `.save()` — writes go through `data_access.py` → API client.

### 7.1 Why not lean on `workflow_run` snapshot for all of this?

Tempting, because `workflow/templates/performance_review.py` already gives us a saved-runs lifecycle. But the rooftop monitoring loop has read patterns that a single saved snapshot doesn't cover well:

- **Cross-opp queries** ("show me all FLWs with gender_skew this quarter") — record-level type plus `experiment=<opp>` lookup makes this a single API call. A snapshot-only model forces a walk of every saved run.
- **Idempotent upsert per-pin** — the daily pipeline updates `rooftop_visit_status` for the same `pin_id` repeatedly. Embedding that in a workflow snapshot makes the snapshot a moving target.
- **Decoupling the monitoring stream from the human-review surface.** Stage B should run on a schedule whether or not a human opens a workflow. A separate record stream + a workflow template that *reads* those records keeps those concerns clean.

So: records are the source of truth; the workflow template is a configured view over them.

## 8. Stage A — Setup (sampling)

### 8.1 User flow

1. User navigates to `/rooftop-surveys/<opportunity_id>/setup/`.
2. **Area picker** (Mapbox): pick a country, pick admin boundaries from a dropdown OR draw freehand. Choose intervention + optional comparison. Drag-to-edit supported.
3. **Frame config:** target clusters (default 25), households per cluster (default 8 primary + 8 alternate), occupancy strata (default High/Medium/Low computed from local density), roof-confidence threshold (default per-ward auto, override allowed).
4. **Preview:** map shows building footprints, candidate cluster centroids, sampled pins. Diagnostics: building count, density distribution, recommended k.
5. **Commit:** persists `rooftop_area`, `rooftop_psu` × N, `rooftop_pin` × N×k.
6. **Push to Connect:** opens a confirm dialog showing FLW count + pin-per-FLW math (e.g., "200 pins / 20 FLWs = 10 pins each"). Confirm → creates the Connect-side cases (via the existing Labs → CCHQ pathway) and writes `rooftop_assignment` records.

### 8.2 Library shape

```python
# connect_labs/rooftop_surveys/sampling/cluster.py

from dataclasses import dataclass

@dataclass(frozen=True)
class ClusterConfig:
    target_k: int = 25
    min_cluster_size: int = 50   # buildings; below this triggers merge
    max_iterations: int = 50
    stratify_by: str = "occupancy"   # "occupancy" | "density" | "none"
    stratum_breaks: tuple[float, float] = (0.33, 0.66)  # quantile cuts
    random_seed: int | None = None

def cluster_buildings(
    buildings: pd.DataFrame,       # columns: building_id, lat, lon, area_m2, confidence
    area_geom: shapely.Polygon,
    config: ClusterConfig,
) -> ClusterResult:
    """
    Equivalent of R clustering_pipeline.R. Returns:
      ClusterResult(psus: list[PSU], merged: list[(small_id, into_id)], k_used: int)
    Each PSU has cluster_id, centroid, building_ids[], stratum.
    """
```

### 8.3 Parity tests against R

`tests/fixtures/` contains the Fall 2025 pilot inputs (Gwange building extract, Tsaki extract) and the corresponding R outputs (`clusters.geojson`, `psu_mapping.csv`). The Python port is locked to within tolerance:

- Number of PSUs: exact match.
- Building → PSU assignment: ≥95% identical (k-means is stochastic; we fix the seed but tiny floating-point divergence across NumPy/scipy versions is allowed).
- PSU centroids: within 25m.
- Stratum assignment: exact match for High/Low; ±1 cluster slack on Medium boundaries.

The parity test is a CI gate. Adding new behavior is fine; silently changing existing behavior is not.

### 8.4 Open Buildings access

Google's Open Buildings dataset lives in BigQuery (`bigquery-public-data.geo_openstreetmap` + the Open Buildings tables). v1 reads it with a service-account credential stored in Labs settings. Per-opp building extracts are cached in `rooftop_psu`'s parent record so a re-run doesn't refetch.

## 9. Stage B — Daily monitoring

### 9.1 Pipeline structure

Celery beat fires `rooftop_surveys.tasks.run_daily_monitoring(opportunity_id)` on a schedule (default every 30 minutes; configurable per opp).

```
ingest (CC export → DataFrame)
  └→ normalize (schema mapping, parse GPS, parse timestamps)
      └→ derive (per-attempt flags: reached_le15, believed_reached, completed, fallback_reason)
          └→ rollups (per-target, per-FLW-per-day, per-cluster-per-day)
              ├→ outputs.upsert_records (writes rooftop_visit_status, rooftop_flw_daily, rooftop_cluster_rollup)
              └→ outputs.mirror_to_drive (optional, opt-in per opp; matches R "_latest" CSV behavior for analysts who still want files)
```

Each function in `monitoring/` is a pure dataframe-in / dataframe-out transformation. The pipeline is the composition; tests pin each stage's behavior independently.

### 9.2 The 15m gate (specific to this domain, worth calling out)

`derive.py` implements two distance checks that came out of the Nigeria pilot:

1. **`reached_le15`**: the form's submitted GPS is within 15m of the target pin AND submitted GPS accuracy ≤ 15m. Both conditions must hold; the briefs documented confusion when only one held.
2. **`believed_reached`**: an operator override flag captured in the form (FLW says "I'm at the door but GPS is bad"), gated on a CommCare-map screenshot being attached. Reported separately from `reached_le15` so funders can choose how strictly to filter.

The Nigeria briefs make a strong case for tracking `believed_reached` separately rather than rolling it into `reached_le15`: it's the dominant *correction* mechanism, not noise. v1 honors that distinction in the data model and in the dashboards.

### 9.3 Workflow template `rooftop_monitoring`

Modeled on `mbw_monitoring_v3.py`. Single-opp (multi-opp variant follows once we have one real opp running). `supports_saved_runs: True` — each saved run is a "frozen Wednesday morning view" the program admin can revisit.

```python
TEMPLATE = {
    "name": "Rooftop Monitoring",
    "slug": "rooftop_monitoring",
    "supports_saved_runs": True,
    "snapshot_inputs": {
        "pipelines": [],  # data is in LabsRecords, not workflow pipelines
        "workers": True,
        "state_keys": ["selected_arm", "selected_cluster"],
    },
    "data_loader": "connect_labs.rooftop_surveys.workflow_adapter.load_for_run",
}
```

Render code reads `view.workers` (FLWs assigned to the opp) and `view.rooftop` (a new domain-specific helper that loads the freshest `rooftop_visit_status` / `rooftop_flw_daily` / `rooftop_cluster_rollup` filtered by run completion timestamp). The dashboard panels:

- **Coverage strip** at the top: total pins / completed / in-progress / not-yet-attempted, split by intervention vs comparison arm.
- **Map panel** (Mapbox): every cluster colored by completion rate; click a cluster to drill into per-pin status.
- **FLW table:** sortable by completion %, GPS issue rate, median duration.
- **Quality strip:** % within-15m, % believed_reached, % requiring revisit, % fallback by reason.
- **Time-to-completion histogram:** 5 bins (<10, 10–15, 15–20, 20–30, >30 min).

### 9.4 Parity tests against R

Same shape as 8.3: feed the Fall 2025 submissions extract through both R and Python, diff the resulting `targets_status`, `enum_daily`, `cluster_rollup` CSVs. Tolerance: exact row counts; exact `completed` / `reached_le15` boolean assignments; ±1 row tolerance on edge-cases at exact 15.00m distances (different libraries handle the boundary differently); same total counts in time-to-completion bins.

## 10. Stage C — QC

Two of the four QC capabilities are "library + opt-in"; two need a small workflow each. v1 ships the cascade and the FLW-anomaly library; back-check and audio-audit workflows can land in a follow-up if needed.

### 10.1 `qc/cascade.py` — generic validation cascade

Direct port of the Sampling_Quality_Analysis.R three-filter pattern, generalized:

```python
@dataclass(frozen=True)
class FilterRule:
    name: str                   # "no_services_to_8plus"
    description: str            # human label
    predicate: Callable[[pd.Series], bool]
    severity: str = "drop"      # "drop" | "flag"

def apply_cascade(
    visits: pd.DataFrame,
    rules: list[FilterRule],
) -> CascadeResult:
    """
    Returns a CascadeResult with per-rule drop counts, per-rule example IDs,
    and a 'filtered' dataframe. Each rule is applied independently; ordering is
    cosmetic. Reuses the pilot's reporting shape so analysts familiar with the
    R output can read it without translation.
    """
```

Rules library lives in `qc/cascade.py`; users compose them in their per-opp config. The CCC-CHC pilot's three filters (no services to age 8+, enumerator confidence, phone-use recall) ship as named rules.

### 10.2 `qc/flw_anomaly.py` — FLW-side detectors

Each detector returns `list[QCFinding]`. v1 ships:

- **`gender_skew(visits, flw)`**: binomial test on female/male respondent split after ≥ N visits. Threshold defaults: N=30, p-value < 0.01 OR observed female_pct outside [0.35, 0.65]. The concept-note example.
- **`time_clustering(visits, flw)`**: detects FLWs whose visit timestamps cluster suspiciously (e.g., 20 visits in 90 minutes — physically implausible given the navigation reality from the briefs).
- **`duration_outlier(visits, flw)`**: detects FLWs whose median visit duration is <½ or >2× the program median.
- **`gps_stationary(visits, flw)`**: detects FLWs whose submitted GPS locations cluster within tens of meters across visits that should be at distinct pins.

Each detector is parameter-tunable. Findings land as `rooftop_qc_finding` records; the dashboard surfaces them per-FLW with severity badges.

### 10.3 `qc/back_check.py` — back-check sampling and assignment

Borrows the design of `workflow/templates/audit_with_ai_review.py`: a separate workflow template (`rooftop_back_check`) wraps it.

Flow:
1. Operator opens the back-check workflow for an opp.
2. Picks a sampling rate (default 5%) and stratification (default: stratify by FLW so every FLW gets back-checked equally).
3. v1: pins selected for back-check are pushed back into Connect as fresh cases assigned to a designated "back-check FLW" account. `rooftop_back_check` records track open status.
4. When the back-check survey lands, its responses are compared to the original survey on a small set of "must-match" fields (e.g., household composition, respondent gender, primary services received). Discrepancies flip `rooftop_back_check.status = "discrepant"`.

### 10.4 `qc/audio_audit.py` — audio audit queue

Per the concept note, "audio audits of recorded interviews." Audio attachments are accessible via the CommCare Connect API as part of the form submission. v1:

1. Random sample (configurable rate) of completed visits gets enqueued for audio review.
2. A reviewer UI plays the audio side-by-side with the form responses and assigns one of: `confirmed`, `flagged_quality`, `flagged_protocol`, `inconclusive`.
3. Findings flow into `rooftop_audio_audit` records and surface in the dashboard.

## 11. MCP tool surface

These atoms ride the existing `connect_labs` remote MCP. New `mcp_tools.py` in the app:

| Atom | What it does |
|---|---|
| `rooftop_define_area` | Persist intervention + comparison polygons for an opp |
| `rooftop_generate_frame` | Run sampling pipeline; persist `rooftop_psu` + `rooftop_pin` |
| `rooftop_assign_pins` | Assign generated pins to FLWs (round-robin / load-balanced / explicit) |
| `rooftop_push_to_connect` | Create CommCare cases on the opp's domain from `rooftop_assignment` |
| `rooftop_run_monitoring` | One-shot trigger of the daily pipeline (in addition to the celery schedule) |
| `rooftop_list_visit_status` | Read `rooftop_visit_status` filtered by opp / FLW / cluster / arm |
| `rooftop_list_flw_daily` | Read `rooftop_flw_daily` |
| `rooftop_list_cluster_rollup` | Read `rooftop_cluster_rollup` |
| `rooftop_apply_cascade` | Run the validation cascade with a named ruleset |
| `rooftop_run_flw_anomalies` | Run the FLW-anomaly detectors; persist findings |
| `rooftop_sample_back_checks` | Sample + assign back-check cases |
| `rooftop_sample_audio_audits` | Sample + enqueue audio audits |
| `rooftop_export_deidentified` | Strip PII, k-anonymize small cells, write a `rooftop_export` record + signed URL |

Each atom is a thin wrapper over a lib function — matches the lib-first / atom-second pattern. Tools land in PR sequence below; not all in PR 1.

## 12. UI surface (read by humans)

Three URL groups, all under `/rooftop-surveys/`:

- `/<opp_id>/setup/` — area picker → frame review → push-to-connect (Stage A)
- `/<opp_id>/dashboard/` — live monitoring view (Stage B); also reachable as a saved-run via the `rooftop_monitoring` workflow template
- `/<opp_id>/qc/` — three sub-views: cascade, FLW findings, back-check / audio-audit queues (Stage C)

Stack: htmx + alpine for control flow, mapbox-gl for maps, react islands for the dashboard table (matches `workflow/` precedent), Tailwind + shadcn primitives (already in deps).

## 13. Permissioning

OAuth + Django user, same as every other Labs app. Two access surfaces:

- **Opp scope:** all `rooftop_*` records carry `opportunity_id` in their write payload, which triggers the production-side access permission check. A user without access to opp X cannot read or write rooftop records for X. No new permission concept.
- **Program admin vs. funder vs. LLO scope:** v1 inherits the existing workflow-template gate model — a workflow template is visible per role configured at the template registration. We add `rooftop_monitoring` and `rooftop_back_check` to the same registration mechanism. Funders see the dashboard read-only; program admins see read + run; LLOs see their own FLWs only.

No new roles. No new auth flows.

## 14. Phased rollout

Per the lib-first / atom-second / small-staged-PRs convention from ACE memory, here's the sequence. **Each PR ships independently, with tests; only after PR 0 is merged does anyone consume this app for a real opp.**

| PR | Scope | Land before | Rough size |
|---|---|---|---|
| **PR 0** | This design doc | Any code | small |
| **PR 1** | App skeleton: `apps.py`, `urls.py`, empty `data_access.py` + `models.py`, fixtures dir | PR 2 | small |
| **PR 2** | `sampling/` library: boundaries, footprints, filters, cluster, sample, assign — pure Python, no UI, no MCP. Parity tests vs R fixtures | PR 3, 4 | medium |
| **PR 3** | `sampling/` UI: area picker + frame review + push-to-connect flow. No QC. No dashboard yet | PR 5 | medium |
| **PR 4** | `monitoring/` library: ingest → normalize → derive → rollups → outputs. Parity tests vs R fixtures | PR 5 | medium |
| **PR 5** | `rooftop_monitoring` workflow template + render code + the live dashboard URL. Schedule via celery beat | PR 6 | medium |
| **PR 6** | MCP tools for everything shipped so far (atoms wrap the libs from PRs 2 + 4 + the data_access surface) | PR 7 | small |
| **PR 7** | `qc/cascade.py` + `qc/flw_anomaly.py` + `/qc/` UI tab | PR 8 | medium |
| **PR 8** | `rooftop_back_check` workflow + `qc/back_check.py` | PR 9 | medium |
| **PR 9** | `qc/audio_audit.py` + audio review UI + `rooftop_audio_audit` records | post-launch | medium |
| **PR 10** | `rooftop_export_deidentified` (PII stripping + small-cell suppression) | post-launch | small |

After PR 6, the rooftop-surveys ACE opp can run end-to-end against this app — `/ace:run rooftop-surveys` consumes the MCP atoms instead of pretending the capability exists. PRs 7–10 land in parallel with the first opp's operation.

## 15. Open questions

1. **Building data licensing.** Google Open Buildings is CC-BY-4.0; Microsoft Building Footprints is ODbL. For redistributing extracted/sampled data downstream (de-identified dataset exports), are we OK with the attribution requirement? Probably yes — needs a one-line legal check.
2. **Survey instrument ownership.** The CommCare form is authored in HQ today. Do we want to ship a starter rooftop-survey form template (with VitA/ORS/MUAC/bednet style sections) alongside this app, or stay strictly out of form design? Recommendation: stay out for v1; ship one reference form as documentation but don't make form choice part of the app.
3. **Back-check FLW assignment.** Do back-check pins go to an independent FLW (separate Connect account), the same FLW with a delay, or a supervisor? v1 leans "independent FLW," but the Nigeria pilot didn't actually run back-checks — there's no precedent to defer to.
4. **Audio audit reviewer pool.** Same question — is this a separate Labs role, or does the program admin do it themselves? v1 assumes program admin until volume forces a split.
5. **Continuous vs episodic mode.** The concept note frames this as continuous monitoring; the Nigeria pilot was episodic. v1 supports both — but the "comparison area" framing only really applies in continuous mode. Episodic runs may just use intervention area. The area-picker UI should let users skip comparison without nagging.
6. **Cross-opp anomaly aggregation.** The concept note implies a funder can compare arms / wards / programs at a higher level than one opp. v1 does not. A follow-up "rooftop_program_report" workflow template (analogous to `program_admin_report`) would cover this.
7. **The inaccessible "Potential Offering" doc.** It may carry pricing / packaging detail that affects the export and back-check defaults. Worth chasing down before PR 1 lands.

## 16. Relationship to ACE

ACE's `rooftop-surveys` opportunity remains the authoring surface — the PDD, the work order, the synthetic data, the LLO onboarding flow, the QA evals. None of that moves into Labs. What changes: ACE skills stop describing aspirational "rooftop monitoring" behavior and start calling the concrete `rooftop_*` MCP atoms. Specifically:

- `idea-to-pdd` references the Labs `rooftop_surveys` app capability list when generating PDDs for monitoring-style opps.
- `pdd-to-deliver-app` / `pdd-to-learn-app` cite the rooftop reference form (once we publish one).
- A new skill — `rooftop-frame-setup` — runs at Phase 4 or 5 of the CRISPR-Connect lifecycle to call `rooftop_generate_frame` + `rooftop_push_to_connect`.
- `flw-data-review` reads from `rooftop_qc_finding` and `rooftop_flw_daily` instead of computing its own anomalies.
- `connect-opp-setup` reads from `rooftop_assignment` to know how many deliver-units the opp expects.

That work belongs in ACE PRs, after this Labs app is at least at PR 6 (MCP atoms wired).

---

**Reviewers' eye:** I'd appreciate a sanity check on the new-app justification in §5, the data model in §7, and the workflow-template choice in §9.3 (vs. just rendering a Django template directly). The rest can iterate in PR review.
