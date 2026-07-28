# Connect Pulse — funder-facing live service-delivery telemetry

**Date:** 2026-07-28
**Status:** Design approved, pending spec review
**Prototype:** artifact `12b63ed7-5f9d-4c5f-bbbb-33269e70536e` (night-map variant, 27,501 real visits baked in)

## Purpose

A visually striking, funder-facing display of service delivery flowing into Connect —
cost, verified services, reach, and the offline-sync story — that feels live.

Three delivery modes off one build:

- **Kiosk** — always-on wall display, auto-cycling, nobody driving it.
- **Presenter** — you open it in a meeting and steer it.
- **Link** — a URL a funder opens alone, at any hour, with no narration.

Connect prod has no push feed. "Live" is achieved by polling the export API on an
`id` cursor and streaming the results to open viewers, plus a labelled replay mode
for when there is no traffic.

## Measured baseline (2026-07-28, via the `ace` token)

These numbers drove the design and are recorded so later work can tell drift from
change.

| Quantity | Value |
|---|---|
| Scope visible to one Dimagi-staff token | 15 orgs · 108 programmes · 494 opps |
| Lifetime visits | 1,647,855 |
| Active opps / genuinely live opps | 64 / ~14 |
| Visits in last 24h | ~1,300 (peak 350/hr ≈ 6/min) |
| Live window | ~06:00–17:00 UTC (≈ 02:00–13:00 US Eastern) |
| Visits carrying GPS | 95.3% |
| Outcome mix | approved 82.5 · over_limit 14.3 · pending 1.9 · rejected 1.0 · duplicate 0.3 |
| Flagged for human review | 6.1% (reviewer agreed on 79%) |
| Sync lag (field→server) | median 9 min · p90 2.8h · max 176h |
| Paid to worker per approved service | $2.40 blended; $0.36–$30.04 by programme |

**Timezone constraint.** Traffic follows Nigerian/Kenyan working hours. A 9–11am ET
funder meeting is live; a 2pm ET meeting sees nothing arriving. Replay is not a
nice-to-have, it is the primary mode for half the working day.

## Export API characteristics

Verified against `dimagi/commcare-connect` `commcare_connect/data_export/`.

**Keyset pagination is a change feed.** `IdKeysetPagination` accepts `last_id`,
`page_size` (max 5000), `cursor_order`. So:

- `?cursor_order=reverse&page_size=1` — cheap freshness probe (~0.45s/opp)
- `?last_id=<max_seen>` — returns only rows created since the last poll

**Cost per endpoint** (measured, opp 765):

| Endpoint | Bytes/row | Notes |
|---|---|---|
| `user_visits` | 16,119 | **99% is `form_json`**; no field selection, no date filter |
| `completed_works` | 561 | clean |
| `user_data` | 637 | clean |
| `payment` / `invoice` | small | clean |
| *what Pulse actually stores* | 381 | 2.4% of the wire bytes |

A full 1.65M-visit backfill would move **~26.5 GB to store ~628 MB**. Steady-state
tailing is ~20 MB/day, which is irrelevant. **The cost is entirely in backfill depth.**

`opp_org_program_list` returns all 494 opps *with lifetime `visit_count`* in one
request — headline scale numbers are effectively free.

**Upstream ask (separate, optional):** a `?fields=` param or slim serializer on
`user_visits` in `dimagi/commcare-connect` would cut ingest ~40×. Not a dependency;
Pulse works without it.

## Architecture

New app `connect_labs/pulse/`. Three layers, where **layer 2 is the contract** —
no card ever talks to Connect.

### Layer 1 · Ingest

Two speeds, justified by the byte costs above.

**Cheap tier** — all 494 opps, every ~5 min. `opp_org_program_list` (counts, scope),
`completed_works`, `user_data`, `payment`, `invoice`. Feeds every scalar, money and
worker card. Costs almost nothing.

**Expensive tier** — `user_visits`, tailed by `last_id`, only for opps on the map.
Adaptive tiering by recency of last visit:

| Tier | Condition | Poll interval |
|---|---|---|
| hot | visit < 6h ago | 60s |
| warm | < 7d | 15 min |
| cold | < 90d | daily |
| dormant | older | weekly |

Today: ~14 hot, ~15 warm. Steady state ≈ 10 requests/min.

`PulseCursor(opportunity_id, endpoint, last_id, last_seen_at, tier)` holds cursor
state. Backfill is a separate one-shot job from tailing, so a slow backfill never
blocks the live tail.

**Auth.** Runs as a designated Django user via
`connect_labs.labs.connect_tokens.get_valid_access_token(user)`, which already
handles refresh (its docstring anticipates exactly this). **Decision: Jonathan's own
account.** Two consequences that must be handled, not assumed:

1. His membership scope is **unverified** — all measurements above used the `ace`
   token. First implementation step is to compare his visible opp set against the
   `ace` baseline and record the difference.
2. Refresh tokens have an absolute lifetime. If he does not log into labs for a long
   stretch, ingest stops. Must fail loudly (surfaced status on the page + log), never
   silently serve stale data as live.

**Backfill depth: last 90 days**, extendable. Covers every currently-live programme,
~30–40 min to first useful screen, deep history backfills later.

### Layer 2 · Common data model

PII is stripped **at ingest**, not at render, so it cannot leak by accident.
`entity_name` (real beneficiary names + phone numbers, e.g.
`"Sa,adatu Yakubu - 8037760312"`) and `user_data.name`/`phone` are **never** stored.
FLW `username` is already an opaque hash upstream and is what we display.

- **`PulseEvent`** — one row per service delivery:
  `connect_visit_id, opportunity_id, program_id, org_id, field_ts, sync_ts,
  lat, lon, country, status, flagged, flag_type, review_status, service_slug,
  worker_hash, usd_to_worker`
- **`PulseRollup`** — hourly aggregates per (opp, status): counts + USD sums, so no
  card scans raw events.
- **`PulseScalar`** — all-time figures, refreshed on the cheap tier.

Read API — the only surface cards know:

```
GET /labs/pulse/api/summary/                  scalars + rollups + scope + ingest health
GET /labs/pulse/api/events/?since=<cursor>    live tail
GET /labs/pulse/api/replay/?from=&to=         a bounded window for replay
```

### Layer 3 · Cards and layouts

**A card** declares a preferred grid size and renders from a shared store.
**A layout** is data — an ordered list of `{card, x, y, w, h}` stored as a LabsRecord —
so rearranging is an edit, not a deploy (and later an MCP call).

**`PulseStore`** is the reason this is a new app rather than a `pages` surface: one
clock and one subscription that every card reads. Live-vs-replay is a property of the
store, so switching modes moves every card together. Independent per-card fetching
(what `pages` does) would let the map and the ticker disagree about what time it is.

Why not reuse `pages`: it is a light-themed uniform grid
(`auto-fill, minmax(300px, 1fr)`, single card size, 148px min-height, indigo-on-white
link cards) with per-card lazy loading. Wrong theme, no card spans for a 3×2 map, and
no shared clock. Pulse cards **will** be registered as `pages` providers afterwards so
a single metric can drop onto a program hub.

### Card library v1

| Group | Cards |
|---|---|
| Pulse | night map · event ticker · live counter · 72h sparkline · daylight clock |
| Financial | funds flow (committed→accrued→paid→invoiced) · cumulative $ to workers · cost-per-service by programme · budget burn vs spend · payout timeline · $ by country |
| Trust | outcome partition · flag reasons · reviewer agreement · duplicates caught |
| Reach | country bars · programme leaderboard · worker count · service types |

## Live vs replay

Both are properties of `PulseStore`, exposed as `?mode=live|replay&speed=N`.

- **Live** — SSE/poll tail; badge is green and shows real arrival times.
- **Replay** — a bounded window played at 60–900×, badged amber and dated. Never
  presented as live.

**Replay is paced on `field_ts`, not `sync_ts`.** A worker who syncs 20 visits at 13:32
actually delivered them across the morning; replaying at field time is more truthful
than replaying arrival order, and it makes bursts read as steady work.

**Consequence to handle:** selecting a window by arrival time but pacing by field time
means the window spans wider than its nominal length (the prototype's "last 48h"
actually covered 07-19→07-28 because of the sync tail). **The real build selects the
window by `field_ts`** so the label means what it says.

## Correctness rules

Learned from prototype defects; these are requirements, not style notes.

1. **`status` is a partition; `flagged` is orthogonal.** A flagged visit can still be
   approved. Never render them as one funnel — the prototype's first build produced a
   funnel whose final stage was *wider* than the stage above it. Show outcomes as a
   partition and the review layer alongside.
2. **Payment coverage is uneven.** Opp 765 has payments, opp 1996 has none. The funds-flow
   card must distinguish *no data* from *zero paid*.
3. **Per-work ≈ per-service, but verify.** 99.1% of approved `completed_work` rows have
   `saved_approved_count = 1` ($2.424/work vs $2.400/count). Label precisely.
4. **Cross-check money.** Measured payout rates match each programme's `budget_per_visit`
   at market FX to within cents. Keep this as an ingest assertion — divergence means
   something upstream changed.
5. **No prose numbers.** Any figure in copy must be read from the same data the chart
   draws. The prototype briefly claimed "$0.39" beside a chart showing $0.41.
6. **Never plot below town scale.** Household GPS is real. Zoom is capped; the ambient
   layer is quantised to ~110m.

## Testing

- Ingest: cursor advance, no duplicate events on overlapping polls, PII fields absent
  from `PulseEvent` (assert by field list, so a future serializer change can't add one).
- Backfill/tail isolation: a running backfill does not stall the hot tier.
- Token failure: expired refresh surfaces as visible unhealthy state, not stale-as-live.
- Rollups reconcile against raw events.
- Replay determinism: same window + speed → same event order.
- Card contract: every card renders from a fixture payload with no network.

## Build order

Sequenced so there is something judgeable on screen before the long tail of cards.

1. Verify Jonathan's token scope vs the `ace` baseline; record the delta.
2. Ingest: models, cursors, cheap tier, `user_visits` tail, 90-day backfill.
3. Data model + read API + ingest health.
4. `PulseStore` + card/layout registry.
5. Night map (port the prototype) — proves the store end to end.
6. Mission control + financial view.
7. Iterate cards and layouts.

## Open questions

- Public link auth: unauthenticated token URL, or Dimagi SSO? Affects whether a funder
  can open it cold.
- Does "programmes" (108) or "opportunities" (494) read better to a funder as the unit
  of scale?
