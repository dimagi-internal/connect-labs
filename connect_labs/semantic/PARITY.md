# KMC semantic layer — parity with the existing dashboard

## The result

Against the existing `kmc_programme_metrics` dashboard's own frozen run (5218),
all 11 opportunities, `as_of` pinned to that run's snapshot time:

| scope       | rows | indicator checks | mismatches |
| ----------- | ---- | ---------------- | ---------- |
| programme   | 1    | 22               | **0**      |
| opportunity | 11   | 242              | **0**      |
| llo         | 6    | 132              | **0**      |
| flw         | 241  | 5,302            | **0**      |
| **total**   |      | **5,698**        | **0**      |

Structure matches too: 8,718 cases · 35,453 visits · 11 opps · 6 LLOs · 241 FLWs.
Value AND denominator, exact, at every scope. One `GROUPING SETS` query, ~12s.

The render is **unchanged**: the existing template already short-circuits every
scope memo on `frozen`, so this is the same UI with the numbers computed in SQL.

## What the comparison caught

Every one of these was a defect in THIS project, and each was found by a
different mechanism. None would have been caught by the others.

**Guessed constants** — `WMIN` was 400; the render uses **250**. These are preterm
babies, so a 400g floor silently discarded real low-birth-weight readings.
`SWING` was 0.25; the render uses **0.3**. Those two alone moved the whole growth
chain (C07–C13) from wrong to exact. They were invented, not read.

**Layer 1 was paraphrased** — the hand-written extraction carried 3 of 10
danger-sign paths, 3 of 6 referral, 3 of 4 kmc-hours. Exactly those indicators
(C19, C20, C23) disagreed. `layer1.build_visit_sql` now generates it from the
pipeline's own schema, and 6 tests pin that no path can be dropped again.

**The baby key was wrong** — grouping on `baby_case_id` alone merged the 829 case
ids that appear in more than one opportunity: 7,889 cases instead of 8,718,
changing every denominator. The key is `(opportunity, case)`.

**The visit cache is partitioned by pipeline** — the same visit is cached once per
pipeline that has fetched it, so an un-deduped read double-counted.

**The growth window was anchored on the wrong event** — the render measures a
weighing's age from the first VISIT; this measured from the first WEIGHING. Only
matters for babies whose first visit carried no weight, which no fixture had.

**Input availability was missing** — an indicator whose input the scope never
records must read n/a, not 0. Invisible at programme level; 268 of 5,302 per-FLW
checks disagreed until it was ported.

**`as_of` was unpinned** — comparing a live 28-day eligibility gate against a
two-day-old snapshot is a bug in the test, not the code.

## Where each guard now lives

- `test_every_declared_scope_actually_executes` — compiling is not the bar; `llo`
  compiled cleanly for weeks and failed at execution.
- `test_rollup_equals_per_scope_queries` — the `GROUPING SETS` collapse moves no number.
- `test_layer1.py` — every extraction path survives; dedup and widening are pinned.
- `test_gates.py` — input availability and credibility, including that both fail
  OPEN on a missing gate: blanking a real indicator on our own wiring error is
  the worse failure.
- `test_parity.py` — mutation-verified: changing `ELIG_DAYS`, restoring the `llo`
  whitelist, or making suppression a no-op each turns the suite red.

## Performance

`compile_rollup_sql` returns every scope from ONE pass over `props` via
`GROUPING SETS`. Measured on opportunity 10042 (608 babies), warm, 3 runs:
per-scope 3.626 / 4.734 / 3.555s versus single-pass 0.892 / 0.882 / 0.874s —
**4.1–5.4x**, with the single pass costing about what one scope cost.

Two earlier figures should not have been generalised from: a ~28s-per-scope
measurement was a cold read taken right after the cache was written, and a 2.25s
single run was one warm sample.

## Still open

- No server-side endpoint compiles and runs the registry per request; the run
  carries computed results. That needs a labs deploy.
- C18/C22 stay uncomputed pending the workbook's completion-gate definition.
- C17 reproduces the render's UPPER median (`mv[floor(n/2)]`) deliberately, for
  parity. Whether that is the right statistic is a question for the workbook.
