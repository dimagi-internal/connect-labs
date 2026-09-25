# KMC opportunity report rework — design

Status: agreed in conversation with Jonathan, 2026-09-25. Three pieces, one PR each,
built in order.

## Why

The KMC opportunity report (`kmc_opp_report`) is the page a network manager opens for
their own opportunity. Today it:

- recomputes everything on every open (no saved runs), and a cold visit cache turns
  that into a download from Connect, so it is slow;
- shows less than the programme report does for the same opportunity (no headline
  tiles, weekly activity, trends across saved reports, last visit, worker drill);
- is styled more plainly than the programme report, because the two renders share no
  code;
- draws peer trend lines that are wrong: they do not start together, finished
  opportunities appear to have run far longer than they did, and their lines run flat
  to the end of the axis.

The goal: the opportunity report is **the programme report's content for one
opportunity, for the network manager, plus anonymous benchmarks** — saved, fast, and
styled the same.

## Piece 1 — benchmark trends end when the figures stop moving

### Cause

- `benchmarks/publish.py::_history_observations` adds a point for every saved run of
  the source report after an opportunity's first activity. Nothing ends a line.
- Every saved run is computed as of its own date over all visits up to then, so an
  opportunity that stopped delivering repeats its settled figures in every later run:
  the flat line, and a tenure far longer than real delivery.
- The chart (`kmc_opp_report_render.js::PeerTrend`) spaces periods by position, not by
  week number, so W3, W8 and W52 sit at equal intervals and the stretch is hidden.

### When a figure stops moving

In the KMC registry the only thing that depends on the report date is
`days_since_first_visit` (`properties.yml`), through the maturity gates
`eligible_28d` (outcomes, loss to follow-up) and `eligible_42d` (growth, visits per
baby). `eligible_90d` exists but no indicator reads it. Everything else is a fact of
the visits. So once the most recently first-visited baby is 42 days past its first
visit, no later report can change any figure.

### Rule

An opportunity's trend line ends at
**latest case first-visit date + the settle window**, and the first saved run on or
after that date is its last point.

- **Settle window is derived from the registry, not hard-coded.** New
  `semantic/maturity.py::settle_after_days(props_doc, indicators_doc)`: find the
  properties whose SQL reads `:as_of`, close over everything derived from them, and
  take the largest constant any such property is compared against (`x >= :CONST`),
  counting only properties some indicator actually reaches. KMC today: 42. Adding an
  indicator on `eligible_90d` moves it to 90 with no code change. A registry with no
  date-dependent property settles at 0.
- **The anchor is template spec.** `snapshot_inputs.maturity_anchor` names the
  case-index date field the windows count from (KMC: `first_visit_date`). Without
  it, the anchor is the opportunity's last visit, which is always safe.
- **Recorded on the snapshot.** The semantic snapshot builder writes
  `meta.settles = {"after_days": N, "anchor": "<field>"}`, so a publication reads the
  rule the run was graded under. A snapshot saved before this falls back to the
  workflow's bound registry at publish time.
- **Computed at publish time**, per opportunity, from the newest snapshot's case
  index (`opportunity_ends`, beside `opportunity_starts`). An opportunity with no
  dated cases keeps the old behaviour (no end) and is logged.

### Chart

- x is placed by week number, from W0 to the largest week present, so every line
  starts at the left edge and gaps look like gaps.
- Axis labels: first, middle and last week of that range.

### Rollout

Deploy, then republish cohort 4 (prod, source 19778) and cohort 3 (synthetic, source
5456) with `benchmarks_publish`. No history rebuild needed: the cut is applied to the
existing history.

## Piece 2 — a shared report library

### What

A static component library, `window.LabsReport`, shipped in the workflow runner
bundle (built by webpack with JSX, like the rest of the bundle) and loaded by
`workflow/run.html` beside `ConnectMap`. Render code — template-following, forked, or
authored live through MCP — calls it at runtime. Changing the library needs a deploy;
using it does not.

Contents, lifted from the programme report (whose look becomes the library's look):

- page primitives: `Page`, `Card`, `SectionTitle`, `Badge`, `Callout`, `Tabs`;
- `ReportHeader` (title, "report of <date>", Final/Current badge, source label, save);
- `HeadlineTiles` (value, band, change against the previous saved report);
- `Scorecard` (banded indicator table over any scope rows, with the registry's own
  categories);
- `WorkerTable` (row per worker, a column per indicator, last visit, attention, row
  link);
- charts: `WeeklyActivity`, `TrendSmallMultiples` (across saved runs), `PeerBars`,
  `PeerTrend` (the week-number axis from piece 1);
- helpers: `grade`/`band` colours, value formatting by unit, `IndicatorGlossary`.

### Rules

- Components take plain data and callbacks; none fetch. The render owns data.
- Additive changes only once shipped: a prop is never renamed or repurposed. A
  breaking change is a new component name.
- Tests: jest tests beside the library for every component's contract.

### The programme report moves onto it

`kmc_programme_metrics_render.js` replaces its local copies with library calls. The
page must look and behave the same: checked by screenshot before/after on synthetic
workflow 5456 and by its existing tests.

## Piece 3 — the opportunity report, rebuilt

### Page

Built from the library. Order:

1. Header — opportunity name, "Week of <date>", where the figures came from
   ("from the programme report" / "saved here" / "live now"), Final badge, Save.
2. Headline tiles — the programme report's five, with week-on-week change.
3. Against its peers — benchmark bars and the corrected trend lines.
4. Activity by week, and indicator trends across saved reports.
5. Workers — every indicator, last visit, attention; each row opens that worker's
   review (`kmc_flw_review`) with their cases.
6. All indicators — definitions, collapsed.

### Saved runs

- The template turns on saved runs, graded by the same `semantic_snapshot` builder as
  the programme report, scoped to its one opportunity. A network manager can save a
  week without any programme report existing.
- **Push at save time.** When a programme report saves a run, a background task
  running as the person who saved it cuts each opportunity's slice out of the
  snapshot and writes it as a completed run of that opportunity's report, labelled
  as handed down from the programme report and its run. The network manager never
  reads the programme report. Same task machinery as benchmark auto-publish.
- Which opportunity report receives a slice: every `kmc_opp_report` whose definition
  names that programme report as its source (`source_workflow_id`, set by
  `benchmarks_create_opp_reports` and backfilled onto the 24 existing ones).
- Where it lives: an ordinary workflow run of the opportunity report. **Verify first**
  that the saver's token may write a run into each opportunity's scope on production;
  if not, fall back to a labs-owned table read under the same opportunity-access rule
  as benchmarks.
- History: one point per week; a later completion wins a week both sources hold.
- "Live now" stays available as a secondary view (the existing semantic endpoint with
  warm-on-read).

### Rollout

Deploy; backfill `source_workflow_id`; hand down the existing programme history to
each opportunity report (one pass over saved runs, no recomputation); check the page
on synthetic 5688 and prod.

## Out of scope

- Per-worker time series (none exist in the data).
- Changing any indicator definition.
- Moving `kmc_flw_review` onto the library (it can follow later).
