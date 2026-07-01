# Plan-grounded synthetic survey generation

**Date:** 2026-06-12
**Status:** Approved (design), implementing
**Context:** Verified Monitoring DDD (stream ②), synthetic program `-10008`

## Problem

The Verified Monitoring demo's synthetic survey generator
(`scripts/walkthroughs/verified-monitoring/survey_sim.py`) scatters GPS points
**uniformly at random inside the ward polygon** (`_sample_in_geom` over a static
`wards_geojson`). It has no connection to the real sampled microplan work areas,
and no concept of **primary** (the household a surveyor was assigned to try first)
vs **alternate** (a ranked backup used when the primary is unreachable).

Meanwhile the sampled plans under program `-10008` are rich: each plan carries
~400 real building-**footprint polygons**, each tagged
`properties.sample_type ∈ {primary, alternate}`, with `cluster`,
`order_in_cluster`, `weight`, `stratum`, and a `centroid`. (Verified live on plan
3945: 282 primary / 120 alternate across 24 clusters.)

We want synthetic runs that are **representative and accurate** against those
plans: GPS captures sit on the real primary/alternate footprints, with a
configurable **primary rate** (share of completed surveys on the first-choice
unit) that we can **visualize** (map) and **measure** (scorecard).

## Approach (chosen: A — generic read tools + local assembly)

The generator runs as a local script with **MCP token auth**. Plan data + auth
live server-side. So the read path goes through new **generic** `connect_labs`
MCP tools; the VM-specific assembly stays in the demo script.

## Components

### 1. Generic generator library — `connect_labs/labs/survey_sim/`

Pure, no I/O, parallel to `survey_quality/`. The reusable "generate a
representative synthetic survey run for a correctly-parameterized plan" engine.

```
simulate_plan(work_areas, params, rng) -> list[record]
```

- `work_areas`: `[{wa_id, lon, lat, sample_type, cluster, order_in_cluster, arm}]`
  (centroid in GeoJSON `[lon, lat]` order, matching `plan._centroid`).
- Build per-cluster ranked **primary** slots + ranked **alternates**.
- One completed survey per primary slot: with probability `primary_rate` the
  surveyor completes the **primary**; otherwise substitutes the next-ranked
  **alternate** in the same cluster. The record carries the **visited** unit's
  `sample_type`, `cluster`, `wa_id`.
- **GPS capture = visited footprint centroid + offset** — `near` distribution
  when an in-spec draw (`gps_within_15m`) succeeds, `far` otherwise. This is the
  core fix: GPS now lands on the actual sampled household.
- Outcome (`vitamin_a_received` via coverage curve), eligibility, roof,
  duration, evidence: existing quality model, made plan-agnostic.
- `primary_rate` is **per-surveyor**: each surveyor draws
  `clamp(Normal(mean, variance), 0, 1)`; the **flagged surveyor** gets
  `flagged_mean` (heavy substitution is part of why they're flagged).
- Knows nothing about VM, rounds, dashboards, or survey_quality. Unit-tested.

`SimParams` (dataclass + `from_dict`): coverage curve, enumerators,
`primary_rate {mean, variance, flagged_mean, flagged_id}`,
`gps {within_15m, near_m, far_m}`, evidence, duration, eligibility.

Back-check generation and round/trend/scorecard assembly are **not** in this
library — they stay VM-side (the existing back-checks already key off each
primary's assigned location, which is now a real centroid).

### 2. `primary_rate` metric — `survey_quality`

```python
@register_metric("primary_rate", "Surveys on primary (first-choice) unit",
                 "survey_quality", threshold=85.0)
```

Share of primary records with `sample_type == "primary"`; `detail.by_surveyor`
so it rolls up per surveyor onto the scorecard like the other Layer-1 metrics.
Honest "computed from the records."

### 3. Generic microplans MCP read tools — `connect_labs/mcp/tools/microplans.py`

- `microplans_list_plans(program_id)` → `{plans:[{id,name,phase,...}],
  groups:[{group_id,name,kind,plan_ids,arm_for:{plan_id:arm}}]}`.
- `microplans_plan_work_areas(program_id, plan_id)` → compact
  `[{wa_id, lon, lat, sample_type, cluster, order_in_cluster, arm}]`.

Server-side via `ProgramPlanDataAccess` (works for labs-only `-opp` programs).
Registered in `tool_registry`. Tested against real postgres with synthetic
labs-only plans.

### 4. Rewire + visualize + narrative

- `regenerate.py`: discover `-10008` studies via `microplans_list_plans`, fetch
  each plan's work areas, call `simulate_plan` per plan (replacing
  random-in-ward), keep back-check/round/trend/scorecard assembly. New
  `primary_rate` knobs in `demo_config.json`.
- `verified_monitoring_render.js` (workflow 3699): scorecard gains a
  **primary-rate** column; scene-2 map styles survey pins by `sample_type`
  (primary filled / alternate hollow-ring) + legend entry.
- `docs/walkthroughs/verified-monitoring.yaml`: scene-2 (map) + scene-3
  (scorecard) narrative / `features` / `verify` mention primary vs alternate.

## Deploy / verify sequence

Generic lib + metric compute **locally** in `regenerate.py` (baked into
`instance.state`) → no deploy needed for those. The MCP read tools run on labs
prod, so the live `-10008` regen requires deploying them first. Render.js ships
via `push_render.py` (no deploy). Sequence: build+test → PR → merge → deploy →
regenerate against `-10008` → push render → verify live → re-judge.

Local integration test: create a labs-only program + sampled plans in the local
DB and run the whole pipeline end-to-end (no prod dependency).

## Out of scope

- Footprint materialization on the 133/3635 study-design plans (stream ③).
- Creating the R6 (Attakar×Gura) study — a sibling DDD owns it; the generator
  generates for whatever studies exist (5 today, 6 later).
