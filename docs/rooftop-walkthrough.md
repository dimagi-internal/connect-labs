# Rooftop Surveys — walkthrough

Companion narration for the screen recording (`rooftop-walkthrough.mp4`). The
video shows the one part with a UI today (the sampling setup flow); the rest of
the system — the sampling math, the monitoring analytics, the QC cascade — runs
behind that, so this doc narrates those too.

## The big idea (30 seconds)

Rooftop sampling = pick households to survey by **pre-pinning specific rooftops
on a satellite map**, then sending FLWs to exactly those GPS points (removing
their discretion over which houses get surveyed). It was a hand-run R + analyst
pipeline; we're turning it into a product.

Key architectural decision: **we ride Connect's first-class microplanning
feature** for everything FLW-facing (assignment, mobile delivery, visit
tracking, the "can't reach the pin" substitution flow). So this Labs app only
builds the two things Connect does *not* do: **(1) generate the sample** (the
geostatistical design) and **(2) the rooftop-specific monitoring/QC analytics**.

## What the video shows (the setup flow)

URL: `/rooftop-surveys/<opportunity_id>/setup/`

1. **Area picker** — a Mapbox satellite map (the same tooling Connect's
   microplanning uses) with a config panel pre-filled with the Nigeria-pilot
   defaults: 25 clusters, 8 primary + 8 alternate per cluster, confidence ≥ 0.7,
   roof area 9–330 m².
2. **Draw the intervention area** — the green polygon over Gwange/Maiduguri.
   (A comparison arm can be drawn too; you toggle Intervention/Comparison.)
3. **Preview frame** — this is the whole sampling engine firing live:
   building footprints are pulled from Overture Maps, filtered, clustered,
   PPS-selected, and thinned to pins. The map fills with **25 cluster hulls
   (amber)** and **~396 pins — red = primary targets, amber = 15m-substitute
   alternates** — and the panel shows per-arm stats (buildings, PSUs, primaries
   / alternates).
4. **Save frame** — persists the area + generated pins as opp-scoped records.
5. **Download Connect import CSV** — the frame as a microplanning work-area
   import file (one tiny work area per pin), ready to load onto FLW phones.

## Under the hood: the sampling engine (`sampling/`)

What actually happens between "Preview" and those pins (faithful port of the R
`clustering_pipeline`):

1. **Footprints** (`footprints.py`) — DuckDB queries Overture's global building
   Parquet on S3, bbox-pruned to your polygon, cached per area. (Buildings carry
   area + a Google-source confidence.)
2. **Filters** (`filters.py`) — drop low-confidence; drop tiny roofs <9 m²
   *unless* clustered (≥2 neighbors within 12 m — real compounds); drop big
   roofs >330 m² (markets/schools).
3. **Cluster** (`cluster.py`) — k-means → PSUs, merge clusters <16 buildings
   into the nearest. If you give a **reference point** (the facility being
   verified), it computes `distance_to_visit` and classifies each PSU
   **High/Medium/Low**; otherwise one "Low" pool (the pilot baseline).
4. **Select + sample** (`sample.py`) — PPS-systematic PSU selection (∝ building
   count), then spatial thinning inside each PSU to 8 primary + 8 alternate at
   **≥15 m apart**, and **design inclusion weights** (`1/Pi`) on primaries so
   downstream coverage estimates are unbiased.

Plus `boundaries.py`: pick a country's admin area (region → county → locality)
from Overture's global divisions theme to use as the area — works for any
country, no per-country config.

## Behind the scenes: monitoring (`monitoring/`) — no UI yet

Once FLWs start submitting visits, this computes the rooftop analytics Connect
can't (read its `/export/.../user_visits/` → `compute_monitoring()` → a
dashboard payload):

- **GPS adherence** — % within the 15 m gate (`reached_le15`); the "I believe
  I'm at the pin" override rate; the GPS-issue cases (believed but >15 m).
- **Per-cluster** completion / barrier / target-occupied rates, what was found
  at each pin (inhabited / empty / non-residential), and fallback-substitution
  usage.
- **Per-FLW-per-day** productivity, **time-to-completion** bins
  (<10/10-15/15-20/20-30/>30 min), and a **GPS-issue review report** (believed
  but >25 m, with the FLW's map screenshot) for spot-checking.
- **Intervention vs comparison** split throughout.

## Behind the scenes: QC (`qc/`)

The **validation cascade** — of the visits an FLW *reported*, how many survive
integrity filters: gave services to an 8+ member (services target under-5s),
low self-confidence, phone-not-used. Produces the per-ward
reported→validated drop report.

## How it plugs into Connect + what's left

The Save/CSV output → Connect microplanning work areas → FLW phones. That last
hop needs three things outside this app: the `microplanning` feature flag turned
on **for the specific opportunity**, a microplanning-aware deliver app, and the
work-area import (web upload today; a proposed `POST .../work_areas/` API would
automate it). See `docs/rooftop-e2e-runbook.md`.

Completeness vs the original R: `docs/rooftop-r-parity.md`.

## Run it yourself

```
inv up && python manage.py runserver            # needs MAPBOX_TOKEN in .env
open /rooftop-surveys/<opp_id>/setup/            # draw → Preview → Save → CSV
pytest commcare_connect/rooftop_surveys/         # 44 tests
```
