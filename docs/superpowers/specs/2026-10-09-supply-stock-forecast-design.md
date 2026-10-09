# Supply stock forecast: service delivery meets stock

**Status:** design, for Jonathan's review (2026-10-09). Nothing built yet.

## What it is

A workflow that joins Connect's service-delivery data (children enrolled, their visits,
the sachets each one has received) to the supply chain's stock (on hand at each worker
and store) and runs both forward. For every worker, every store and the programme it
answers one question: **does the stock where it is cover the children already in
treatment plus the children we expect to enrol, and if not, when does it run dry?**

It is exploratory, not a decision tool: no orders, no writes. Its value is showing the
relationship between two datasets nobody sees together today.

```
need(point, week) = committed to open cases + projected new enrolments
                    (both in sachets, both phased by how a course is really delivered)
cover(point)      = on hand at the point (+ the store above it, + inbound, as you roll up)
already given out = the history the forecast continues from, and what tells each open
                    case how much of its course is left
```

## What the data supports today (opportunity 10113, regenerated from 2230 on 2026-10-09)

Measured with the SQL explorer, all visits 15 Sep to 7 Oct:

| Week of a child's treatment | Visits | Sachets per visit | Days since last |
|---|---|---|---|
| Screening (enrolment) | 209 | 5.2 | |
| First Visit Form, same day | 178 | 6.0 | 0.2 |
| Follow-up 1 | 177 | 13.1 | 8.1 |
| Follow-up 2 | 39 | 12.4 | 7.0 |
| Follow-up 3 | 3 | 14.7 | 7.7 |

- About 2 sachets a day after the first week, so a 150-sachet course lasts about 11 weeks.
- Enrolment by week: 57, 90, 59 (the source's first three weeks).
- 178 of 180 followed children trace to their own enrolling Screening.
- **No child has finished treatment yet** (true of the source too), so course length and
  exit rates cannot be measured yet. The forecast falls back to the protocol for those
  and says so.

## Approach

Three options were considered:

1. **Compute in the render code.** Add a visits pipeline beside the supply sources and do
   the arithmetic in JavaScript. This is fast to iterate, but the forecast then exists only
   on one page. Hal, the MCP and tests cannot read it, and the "numbers agree" check has
   no source of truth.
2. **A semantic-layer registry for RUTF.** This is right for indicators. A forecast,
   though, is a projection over supply state, which the layer does not model.
3. **A supply read operation (recommended).** `stock/services/forecast.py` computes the
   forecast server-side. It is exposed as the read operation `stock_forecast` (so it is
   also the MCP tool `supply_chain_stock_forecast`) and as a workflow supply source
   `stock_forecast`. A new template, `supply_stock_forecast`, only draws.
   - It follows the same pattern as `worker_stock`: one definition, readable by people,
     agents and tests.
   - It runs as the viewer, with the supply pages' access rules.
   - It takes `as_of`.

## Components

### 1. Case rule: telling the forecast what a "case" is
A child's case is read from the visits, per opportunity. It lives beside the dispensing
rule that already says where the sachets are, as a `cases` block on `DispensingRule`:

```json
"cases": {
  "enrol":   {"form_xmlns": "<Screening>", "path": "form.screening_outcome.rutf_enrollment", "equals": "yes"},
  "outcome": {"path": "form.case_state.outcome_value", "open": ["enrolled"],
              "exit": ["recovered", "deceased", "non_response", "lost_for_follow_up", "visit_referred"]},
  "lost_after_days": 21
}
```

- Forms are matched by xmlns, not by name: the real app's form is named `"Screening "`,
  with a trailing space.
- The case is the visit's `entity_id`. The visit reader gains `entity_id`; `WorkerVisit`
  keeps the rule's answers as today.
- The block is optional. A programme without it gets the stock-only forecast (pace ×
  days, as Stock review does now) and a chip saying cases are not configured.
- The clone seed (`demo/clone_supply.py`) writes this block for 10113.

### 2. Forecast service (`stock/services/forecast.py`)
The inputs are the program, item, opportunity or opportunities, anchor date (`as_of`, or
the last visit date if the visits stop earlier, shown as "data to 7 Oct"), horizon
(default 8 weeks) and an enrolment scenario multiplier (default 1.0).

For each worker:
- **Given out**: weekly sachets dispensed from the ledger over the last 6 weeks. These
  are the same figures `worker_stock` uses, so they agree by construction.
- **Open cases**: children enrolled, with no exit outcome, and seen within
  `lost_after_days`.
  - For each: sachets received so far (the sum over that child's visits) and the
    remaining course, `max(course - received, 0)`.
- **Course size**, in order:
  1. measured from exited-as-recovered cases, once there are at least `K_MIN` of them;
  2. otherwise the commodity's `course_definition.base_units_per_course`;
  3. otherwise 150.

  The result carries which one was used (`measured` / `protocol` / `default`).
- **Delivery profile**: sachets in week k of treatment, measured from the visits (the
  table above), with the protocol's ~2 a day where too few cases reach week k.
  - **Committed**: each open case's remaining course laid onto the coming weeks along
    that profile.
  - **Projected new enrolments**: the worker's weekly enrolment rate (mean of the last 3
    full weeks, × the scenario). Each future cohort is laid onto the weeks after its
    enrolment along the same profile, until its course is used up.
- **On hand** comes from `worker_stock` at the anchor. **Runs dry** is the first week
  where cumulative need exceeds it.

Rolling up:
- **Store**: its subtree's need beyond what each worker under it holds, against the
  store's own on hand from `network_tree`. That gives the store's runs-dry week.
- **Programme**: the same, plus inbound shipments by their expected arrival
  (`shipment_list`). Inbound counts only at the programme level, the way `resupply`
  already refuses to count in-transit stock as a point's cover.

Every figure comes back in sachets, with a `basis` naming where it came from. Rates are
never summed up the tree; they are recomputed from summed counts (the targeting rule).

### 3. Template `supply_stock_forecast` (multi-opp)
It declares `supply_sources: [{alias: "forecast", source: "stock_forecast", item: "rutf"}]`
and has no pipelines. The render draws:

1. **The programme line.** Weekly sachets: given out (past, solid), then committed and
   new enrolments stacked (future). The network's on-hand line crosses it at "runs dry
   week of X", with inbound shown as steps.
2. **Children in treatment** by enrolment week: how many are open and the sachets still
   owed to them. This is the service-delivery side made visible.
3. **Workers**: on hand, open cases, owed to open cases, projected new, and the
   runs-dry week, sorted soonest first. Each row expands to that worker's own line.
   Phone rows are cards, as in #2350.
4. **Stores**: the subtree version of the same table.
5. **Assumptions as chips, not prose**: "course 150 · protocol", "enrolment 69/wk ·
   last 3 weeks", "lost after 21 days", "data to 7 Oct". A scenario control sets
   enrolment from 50% to 150%.

It is pinnable as a supply tab (`supply_chain_view_pin`) beside Stock review and follows
the header's date.

## Errors and edge cases
- **No case rule**: stock-only forecast, with the chip "cases not configured".
- **Too few weeks of enrolment**: projected new enrolments are shown as "not enough
  history". It does not invent a rate.
- **Visits stop before the anchor**: anchor at the last visit and say so. It does not
  forecast from stale data as if it were today.
- **Unapproved visits**: they count toward cases and sachets the same way `worker_stock`
  counts them (its `unapproved` share), so the two never disagree.

## Testing
- Service unit tests on a small fixture:
  - committed laid along the profile;
  - a remaining course never below zero;
  - a lost case leaves the open count after `lost_after_days`;
  - the course-size fallback order;
  - a scenario multiplier of 0 leaves committed only;
  - the runs-dry week;
  - store and programme roll-up with inbound at the programme only.
- **Numbers agree**: the forecast's on hand and given out equal `worker_stock` and
  `stock_flow` for the same anchor, per worker. This is the test Hal's ledger would
  otherwise catch.
- The template renders with the source (a render test), including the phone layout.
- A live check on 10112/10113 after deploy, and Hal's first-use review on the new tab.

## Not in scope
- Ordering or redistribution actions; it writes nothing.
- Weight-based dosing: the forms carry `weight_kg` on some visits, but the protocol table
  is not in the system.
- Statistical models beyond a trailing mean. The scenario control is the honest answer to
  uncertainty here.
- Programmes other than RUTF until a second one has a case rule; nothing in the service
  is RUTF-specific.

## Build order
1. Case rule + `entity_id` on the visit reader + the clone seed writes it.
2. `forecast.py` and the `stock_forecast` operation and source, with tests.
3. The `supply_stock_forecast` template; pin it on 10112.
4. Deploy, check live, then Hal's first-use review of the tab.

## As built (2026-10-09: #2373, #2380, #2381)

Where the build differs from the design above:

- **Forms match by xmlns or name**, as dispensing lines already do. The clone of
  2230 carries no `@xmlns`, so matching by xmlns alone would have matched nothing.
  The `cases` block names its enrolment forms under `enrol.forms`, not `form_xmlns`.
- **The outcome block has `complete`**: the exits that mean a finished course,
  which the measured course size reads. It must be a subset of `exit`.
- **Stock is read on the day asked for** (today by default), the same way
  worker_stock reads it. Only the cases are read at the last visit (`anchor`,
  `data_to`), so a delivery made since the last visit counts.
- **A child's first forecast week owes only what it has not had yet** that week.
- **History covers 8 weeks**, not 6.
- **The page's stock line is the server's `programme.left_at_top`**: what the top
  stores hold once their workers' shortfalls are met. It reaches zero on the
  headline's run-dry date.
- **Not handled:** one anchor is shared by every opportunity in a programme.
  An opportunity whose visits stop syncing well before another's would read its
  children as lost.
