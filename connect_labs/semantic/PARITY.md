# KMC semantic layer — what is proven, and what is not

## The change

`kmc_programme_metrics` derives Layer 2 (case properties) and aggregates Layer 3
(indicators) **in the browser**, in JavaScript, over one row per baby carrying a
weights array plus one row per visit for the weight series. That forces the
in-memory shape: indicators cannot be pushed into SQL because none of the
properties they reference exist in the database to `GROUP BY`.

This package moves both layers into SQL, driven by declarative definitions:

|                         | before                                        | after                                       |
| ----------------------- | --------------------------------------------- | ------------------------------------------- |
| Layer 1 paths → columns | pipeline schema (SQL)                         | unchanged                                   |
| Layer 2 properties      | 26 derived in JS per baby                     | `properties.yml` → SQL columns              |
| Layer 3 indicators      | `var IND` closures, evaluated per scope in JS | `indicators.yml` (Cube syntax) → `GROUP BY` |
| browser receives        | ~8.7k case rows + ~35k visit rows             | aggregates                                  |

The existing workflow is **untouched**.

## Why Cube syntax, given nothing runs Cube

The registry uses Cube's measure notation restricted to Cube's real measure types
(`count`, `count_distinct`, `count_distinct_approx`, `sum`, `avg`, `min`, `max`,
`number`). No extensions — `test_rejects_a_type_outside_cube` enforces it.

Scout (the sibling Dimagi product) is putting its semantic layer on Cube on
`codex/semantic-model-work`, and its pipeline settles the layering question:
raw JSONB → its own generated dbt SQL → typed tables → Cube. Cube never
normalises there, and its `SemanticField.MeasureType` has no `first`/`last` even
after a migration named `expand_measure_types`. So shaping stays below the
semantic layer, and we do not invent dialect a Cube tool would reject.

The notation is what makes these definitions readable by a second engine
(a local Python analysis over the same programme) instead of being trapped in
render code that only a browser can execute.

## What is proven

`connect_labs/semantic/tests/` — 11 tests, all green.

- The registry compiles: every `{CUBE}.col` resolves to a real property or
  aggregate (`validate`), every `{measure}` reference resolves, and every measure
  type is a real Cube type.
- Every indicator carries its denominator measure — the workbook's "no bare
  numbers" rule, enforced structurally rather than by convention.
- **The SQL executes against real Postgres and equals the JavaScript.**
  `test_parity.py` runs the compiled statement and compares 14 indicators against
  a faithful port of the render code over the same fixture:

```
C01 8.0000  C02 7.0000  C05 8.0000  C07 83.3333  C08 83.3333
C09 66.6667 C10 0.0000  C13 5.1945  C14 16.6667  C15 14.2857
C20 14.2857 C24 2.5714  C28 14.2857 C31 72.2222
14 indicators compared, 0 mismatches
```

- **The parity test is mutation-verified.** Changing `ELIG_DAYS` from 28 to 30 in
  `properties.yml` makes C14 and C15 diverge and the test fails. An earlier
  version of the fixture did _not_ catch that mutation — no case straddled the
  gate — so `b8` (last visit at day 29) was added specifically to make the test
  load-bearing. Without it "SQL matches JS" was a weaker claim than it read.

## Two definition bugs this surfaced

1. **C31 denominator.** The render code sums `n_weight_readings` (raw pipeline
   count) and counts round values over the **raw** weights array, while
   `d.n_weights` elsewhere is day-collapsed. Defining the property once as
   day-collapsed would have silently changed the rounding rate's denominator.
   Now computed before the day collapse, as `n_weight_readings` /
   `n_weights_round_100_raw`, and covered by the parity test.

2. **C17 median is the upper median.** `mv[Math.floor(mv.length/2)]` on a
   0-indexed sorted array returns the upper of the two middle values for even n —
   not `PERCENTILE_CONT`, and not the conventional median. The SQL reproduces the
   JS exactly so the engines agree, but the choice itself is worth revisiting.

## Performance

Calling the compiler once per scope re-runs the whole Layer 1 extraction each
time. `compile_rollup_sql` collapses every scope into ONE pass over `props` via
`GROUPING SETS`. Measured on opportunity 10042 (608 babies, 2,691 visits), three
runs, warm cache:

|                           | run 1  | run 2  | run 3  |
| ------------------------- | ------ | ------ | ------ |
| per-scope, 4 queries      | 3.626s | 4.734s | 3.555s |
| single pass, all 4 scopes | 0.892s | 0.882s | 0.874s |
| speedup                   | 4.07x  | 5.37x  | 4.07x  |

The single pass costs about the same as ONE per-scope query, which is the point:
the extraction happens once. `test_rollup_equals_per_scope_queries` pins that the
collapse does not move any number, and the real-data check re-ran through
`compile_rollup_sql` at 21 indicators, 0 mismatches.

An earlier measurement of ~28s per scope was a cold read taken just after the
visit cache was written; warm and repeated it is consistently sub-second. Neither
that figure nor an earlier 2.25s single run should have been generalised from.

## What is NOT proven

- **Not yet run against opportunity 10042's real data.** Everything above is a
  fixture. There is no path to execute arbitrary SQL against the labs database
  without deploying, so real-data verification comes after this merges.
- **No render layer yet.** This proves the compute; the new workflow template that
  consumes these aggregates is the next step.
- **`visit_sql` is wired by contract, not yet by execution** — the compiler takes
  the pipeline's own `visit_extraction_sql` as its inner query, verified by reading
  `pipeline_sql` for pipeline 5108, but the two have not yet run joined together.
- Six workbook indicators (C03, C04, C18, C22, C29, C30) remain uncomputed for the
  same reasons as in the existing dashboard; C32/C33 need thresholds that are TBD.
