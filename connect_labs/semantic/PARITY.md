# KMC semantic layer — how its numbers are checked

> **One indicator set since #2004 (2026-09).** The registry used to carry two
> families, the workbook's C-series (C01–C31) and the demo compute spec's N-series
> (N01–N15). They disagreed on when a baby counts as started and how growth is
> judged. Neal Lesh wrote both, and the compute spec was his later attempt to make
> them make sense. So its rules now apply everywhere, and the workbook indicators
> it never covered were moved onto the same rules. There are 24 indicators with
> plain-name ids. They are **working definitions**: expect them to change, and
> read each one's `note` for its caveats.

## The current guarantee

**The SQL equals an independent implementation of the rules.** `test_parity.py`
runs the same fixture babies through two paths: the compiled SQL against real
Postgres, and a hand-written Python port of the rules. The port is written from
the rules, not from the registry's SQL, because a port derived from the YAML would
prove only that the YAML agrees with itself. All 24 indicators must agree, value
and denominator.

The fixture puts one rule on a boundary per baby, so moving a rule moves a
number. Each of these was checked by breaking the rule and watching the suite go
red:

| Rule broken                                                     | Indicators that moved                                                  |
| --------------------------------------------------------------- | ---------------------------------------------------------------------- |
| started = 1+ follow-ups instead of 2+                           | started, cumulative, visits per case, lost by day 28, growth shares, … |
| growth maturity 28 days instead of 42                           | visits per case, all four growth shares                                |
| impossible step per kg of the earlier weight, not the pair mean | healthy, incomplete, mean growth rate, % impossible                    |
| "thin" ignoring the enrolment reading                           | healthy, incomplete, mean growth rate                                  |

**The spec's own figures.** On real data, % impossible weight changes matched the
spec's section 5 table to one decimal on all seven rows (verified 2026-09-11: PIPN
9.3, EHA 4.2 vs 4.3, NAMA 15.9 vs 15.7, GHI 3.1, Kikapu 42.5, BERI 12.2, All
10.0). This was only possible once the step rate was taken per kg of the pair
mean. The spec's prose does not say which weight; its numbers do.

**The unification moved no N-series number.** On prod programme report 19778, as
of 2026-09-20 (12 opportunities, 9,247 cases), the unified registry reproduced
every N-series programme figure exactly (all 15, value and denominator). The figures that
moved are the workbook's, where its rule was replaced. The before/after table is
in the #2004 PR.

**The golden file.** `tests/fixtures/kmc_rollup_golden.json` holds every scope's
rows (62 rows, nine scopes, gates on) over a fixture spread across three
opportunities, six workers, two LLOs and three cohort months. It was regenerated
deliberately for #2004, and only after `test_parity` passed. The on-disk registry
and the legacy record shape (no `series:` or `defaults:`, as records saved before
those sections existed) must both reproduce it and compile to the same SQL.

## Where each guard lives

- `test_parity.py`: SQL against the reference, every indicator, every growth
  branch.
- `test_every_declared_scope_actually_executes`: compiling is not the bar. `llo`
  compiled cleanly for weeks and failed at execution.
- `test_rollup_equals_per_scope_queries`: the `GROUPING SETS` collapse moves no
  number.
- `test_layer1.py`: every extraction path survives; dedup and widening are pinned.
- `test_gates.py`: input availability and credibility, including that both fail
  OPEN on a missing gate. Blanking a real indicator on our own wiring error is
  the worse failure.
- `test_engine_parity.py`: the engine is not KMC-shaped (entity, cohort date,
  visit markers, weight series, pipelines and denominator floor are the
  registry's MODEL, `model.py`), and the golden file pins that.
- `test_series_family.py`: an indicator's family is declared, not spelled into its
  id, so ids can be plain names.
- `test_visit_quality_registry.py`: a second, non-KMC registry
  (`registry/visit_quality`: beneficiaries, no weight series, no LLO map, family
  Q), executed against hand-computed numbers. It proves KMC is one example.

## History: parity with the browser dashboard (2026-08)

Before the semantic layer, `kmc_programme_metrics` computed its indicators in
the browser. The first job was to reproduce that engine exactly. Against its own
frozen run (5218, all 11 opportunities, `as_of` pinned to the snapshot time), the
C-series matched at every scope, value and denominator:

| scope       | rows | indicator checks | mismatches |
| ----------- | ---- | ---------------- | ---------- |
| programme   | 1    | 22               | **0**      |
| opportunity | 11   | 242              | **0**      |
| llo         | 6    | 132              | **0**      |
| flw         | 241  | 5,302            | **0**      |
| **total**   |      | **5,698**        | **0**      |

That engine has since been deleted, and #2004 replaced the workbook rules it
encoded, so this is a record, not a current guarantee. What the comparison caught
is still worth knowing. Every one was a defect in this project, and each was found
by a different mechanism:

**Guessed constants.** `WMIN` was 400; the render used **250**. These are preterm
babies, so a 400 g floor silently discarded real low-birth-weight readings.

**Layer 1 was paraphrased.** The hand-written extraction carried 3 of 10
danger-sign paths, 3 of 6 referral paths and 3 of 4 kmc-hours paths. Exactly those
indicators disagreed. `layer1.build_visit_sql` now generates the extraction from
the pipeline's own schema.

**The baby key was wrong.** Grouping on `baby_case_id` alone merged the 829 case
ids that appear in more than one opportunity: 7,889 cases instead of 8,718,
changing every denominator. The key is `(opportunity, case)`.

**The visit cache is partitioned by pipeline.** The same visit is cached once per
pipeline that has fetched it, so an un-deduped read double-counted.

**The growth window was anchored on the wrong event.** Maturity is measured from
the first VISIT, not the first weighing. This only matters for babies whose first
visit carried no weight, which no fixture had.

**Input availability was missing.** An indicator whose input the scope never
records must read n/a, not 0. This was invisible at programme level; 268 of 5,302
per-FLW checks disagreed until it was ported.

**`as_of` was unpinned.** Comparing a live 28-day eligibility gate against a
two-day-old snapshot is a bug in the test, not the code.

## Performance

`compile_rollup_sql` returns every scope from ONE pass over `props` via
`GROUPING SETS`. Measured on opportunity 10042 (608 babies), warm, 3 runs:
per-scope 3.626 / 4.734 / 3.555s versus single-pass 0.892 / 0.882 / 0.874s,
**4.1–5.4x** faster. The single pass costs about what one scope cost.

## Still open

- Completion (the workbook's C18/C22) waits on its definition. The 90-day gate
  (`eligible_90d`) and the `completion_recording_credible` settings are ready for
  it.
- Most bands are marked PROVISIONAL in their `bands_source`. Several were
  rescaled from the workbook, whose denominators differ, and several were derived
  from the spec's observed ranges.
