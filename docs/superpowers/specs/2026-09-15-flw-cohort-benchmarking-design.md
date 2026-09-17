# FLW cohort benchmarking — multi-dimensional buckets

**Status (2026-09-16): partly built — steps 2, 3 and part of 4 shipped; the
published `BenchmarkDistribution` object (steps 1, 5, 6) did not.** What ships
today is object **C** only: cohorting computed inside one opportunity's own
snapshot (`connect_labs/semantic/cohorts.py`, drawn by the `PeerCohorts` panel
in `kmc_programme_metrics_render.js`) — start-month, same-opportunity and
caseload-band cohorts, percentile rank, and binned distributions as a view.
Because nothing crosses an opportunity boundary, **R8 and the R1 amendment of
§4 were not needed and were not written**; `MIN_COHORT`/`MIN_BIN` in
`cohorts.py` are readability floors and its docstring says so plainly. Read §4
and §6 as the design for the day FLW histograms are actually published, not as
a description of anything running.

Supersedes the FLW section of
`2026-09-14-opportunity-benchmarking-design.md`, which deferred FLW-level
benchmarking on a disclosure argument this document reverses.

**Goal:** show a field worker where they sit against workers like them — by
percentile, by tenure, by activity level — without publishing anything about
any individual worker.

---

## 1. Why the earlier ruling was backwards

The opportunity-benchmarking design scoped FLWs to within-opportunity on the
grounds that one anonymous bar is one named health worker. That is true of the
object we actually built — `BenchmarkValue` is **one row per peer**, and with
`min_peers=5` a reader sees five rows each of which is one real organisation's
value. R1–R7 exist precisely because a row is a peer.

It is not true of a **histogram**. "37 workers sit between the 40th and 50th
percentile" is a count over a population; it names nothing and, above a floor,
cannot be inverted to an individual. Publishing bin counts over ~200 workers
discloses strictly less than publishing 11 per-opportunity rows does today.

So FLW-level benchmarking via buckets is **safer than what already ships** at
opportunity level via dots. The risk was never the level of the unit; it was
one-row-per-unit.

---

## 2. Three objects, deliberately separate

| | Unit | Crosses a boundary? | Identified? |
|---|---|---|---|
| **A. Peer dots** (exists) | an opportunity | yes, published | no — anonymised, R1–R7 |
| **B. Distribution bins** (new) | a count of FLWs | yes, published | no — never a row per person |
| **C. Colleague dots** (new, trivial) | an FLW | **no** | yes — the viewer's own opp |

C is not a disclosure object at all. It is the viewer's own snapshot data,
which they already hold and already see in the per-FLW indicator table; the
only new thing is drawing it as position instead of a table cell.

The page shows C over B: your colleagues as individual dots, laid on the
distribution of everyone. That is why "both" is coherent — the identified
population is local, and the only thing that crosses the boundary is a count.

**The subtraction risk is real and must be checked.** A viewer holds C exactly
and sees B; if their own opportunity is a large share of a thin cell, B − C
narrows the rest. §4's floors are what bound this, and they are why the
opportunity floor survives into the FLW rules.

---

## 3. Cohort membership stays a list; the slicing is a menu

`BenchmarkCohort` remains a static, granted list of opportunity ids. Who may
benchmark against whom stays an explicit grant — the ACL story does not change.

What becomes multi-dimensional is the **slicing within** the cohort, and it is
a fixed menu the publisher computes, never a filter the reader composes.
Arbitrary composition is how a population narrows to one person; a menu of
pre-computed, pre-suppressed histograms cannot.

Slice vocabulary (v1 ships `all` only; the rest are additive):

| slice_key | derivation | bands |
|---|---|---|
| `all` | — | one slice |
| `tenure:<band>` | `as_of − min(first_visit_date)` per username | `lt3m`, `3to12m`, `gte12m` |
| `activity:<band>` | cases per active month per username | `low`, `mid`, `high` (cohort terciles, frozen per publication) |
| `tenure:<b>/activity:<b>` | both | 9 cells |

Tercile cut-points are computed once per publication and **stored on the
publication**, so a later run cannot silently re-band history.

### The data is already captured

No new pipeline, no semantic-layer change, no new measure:

- **activity** — `byFLW[].n` (cases) is in the snapshot today; `case_index`
  carries `total_visits` per case.
- **tenure** — `case_index` carries `username`, `first_visit_date`,
  `last_visit_date`, `opportunity_id`. Minimum `first_visit_date` per username
  is tenure. (`ComputedFLWCache` also holds `first_visit_date` per worker, but
  it is keyed by pipeline config, so the case index is the cleaner source.)

### FLW trendlines exist too, and are bounded by size, not availability

`compiler.py` defines a `flw_month` scope and the runtime computes it in the
same GROUPING SETS pass as `llo_month`/`opportunity_month` — it is free. The
snapshot simply does not project it: `monthlyByScope` is built by a loop over
`("llo:", "llo_month")` and `("opp:", "opportunity_month")` only.

So "no FLW trend" is a **snapshot projection gap, not a missing capability**.
The real constraint is the 5 MB snapshot cap against ~200 workers × months ×
indicators. Ship FLW trends for the drilled worker only, computed live, rather
than freezing every worker's series.

---

## 4. Bin suppression: R8, and a fix to R1

R1–R7 govern per-peer rows and do not cover counts. Two changes:

**R8 (new) — bin floors.** A histogram is published only if the slice has
≥ `min_cohort_flws` (default 50) contributing FLWs. Every non-empty published
bin holds ≥ `min_bin_count` (default 5) FLWs; thinner bins merge into a
neighbour until they clear, or the slice is withheld entirely. A bin count of
1–4 is never emitted. Tail bins merge outward, so the extremes widen rather
than disappearing.

**R1 (amended) — the peer unit is parameterised, and both floors hold.**
Today R1 is `len({o.opportunity_id for o in eligible}) < min_peers`. For an
FLW slice the natural unit is the FLW key — but switching the unit alone would
let a 60-worker slice drawn from 2 organisations publish, which is
organisation-level disclosure wearing an FLW costume. So an FLW slice requires
**both**: ≥ `min_cohort_flws` distinct FLWs **and** ≥ `min_peers` distinct
contributing opportunities. This is also what bounds the B − C subtraction in
§2.

### Cell sizing against the real cohort

~206 FLWs across the 12 KMC opportunities (~30 in the largest opportunity).

| slicing | cells | mean per cell | verdict |
|---|---|---|---|
| `all` | 1 | 206 | comfortable |
| 3 tenure | 3 | ~69 | fine |
| 3 activity | 3 | ~69 | fine |
| 3 × 3 | 9 | ~23 | **below the 50 floor — withheld under R8** |
| 5 × 5 | 25 | ~8 | never |

This is the finding that should drive the build order: **at 206 workers, the
two-dimensional cross does not clear its own floor.** Ship `all`, then the two
single-dimension slices, and treat `tenure × activity` as something the cohort
grows into — not as v1 scope. The floors are the product, not an obstacle to it.

---

## 5. Percentile rank vs distribution

Two answers from one published object:

- **"You are at the 68th percentile of 206 workers like you."** A rank — one
  number about the viewer's own worker, positioned against published bins.
  Computed client-side; nothing extra is published.
- **"37 workers sit in this band."** The distribution itself.

Both read off the same histogram, so adding the rank costs no new disclosure.

---

## 6. Storage

New model alongside `BenchmarkValue` (which is unchanged):

```
BenchmarkDistribution
  publication      FK -> BenchmarkPublication
  series           char
  indicator_id     char
  period           char, null       # null = point-in-time
  slice_key        char             # fixed vocabulary, §3
  bin_lo, bin_hi   float            # after R8 merging
  count            int              # >= min_bin_count
  n_flws           int              # slice total, for the rank denominator
  n_opportunities  int              # R1's second floor, recorded
```

No `opportunity_id`, no `peer_index`, no per-worker row — the absence is the
guarantee. `with_source()` provenance (which labs run produced it) attaches to
the publication, as it does for `BenchmarkValue`, and is never displayed.

---

## 7. Build order

1. `BenchmarkDistribution` + R8 + the R1 amendment, with the `all` slice only.
2. Colleague dots (object C) in the FLW report — own data, no publish path.
3. Percentile rank, read off (1).
4. `tenure:` and `activity:` slices, with cut-points frozen per publication.
5. `tenure × activity` only if a cohort clears the floors — not for KMC at 206.
6. FLW trendline for the drilled worker, live from the `flw_month` scope.
