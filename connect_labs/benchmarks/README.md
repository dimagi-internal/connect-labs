# Benchmarks

Anonymous peer figures for an opportunity: where it sits among comparable opportunities today, and how it got there. Drawn on the **KMC Opportunity Report** (`kmc_opp_report`) in its "Against its peers" section and read from `/labs/benchmarks/api/<opportunity_id>/`.

## The objects

- **Cohort** (`BenchmarkCohort`): a named set of opportunities that may be compared. **Membership is the read grant**: an opportunity sees the cohorts it belongs to and nothing else. It carries the disclosure settings and, optionally, the report it is published from (`source_workflow_id`).
- **Publication** (`BenchmarkPublication` + `BenchmarkValue`): one publish of a cohort from one completed run of the source report. The page reads the latest publication. A point value per indicator, plus a series per indicator (the trend).

## Where the numbers come from

- **Points** come from the published run's own snapshot.
- **Trends** come from the source report's **saved run history**: one point per saved run (the latest completion wins when a period has several). So the history has to exist before the trend can: rebuild it with `workflow_rebuild_history` first, then publish. Publishing first leaves an older opportunity with no line of its own.
- **The trend axis is tenure**: each opportunity's own weeks since its first activity, so week 1 is week 1 for everyone. It is never a calendar date.
- **A trend ends where its figures settle**: an opportunity's latest case anchor date (KMC: first visit) plus the longest maturity window any indicator waits on (KMC: 42 days, derived from the registry by `semantic/maturity.py`). Every later saved run only repeats the settled figures, and drawing them made a finished opportunity a flat line at a tenure it never reached. The rule rides on the snapshot as `meta.settles`; see `publish.py::opportunity_ends`.
- **Only rate-shaped indicators are published** (unit `%` or per-100). Counts, means and durations are withheld because the value would be the opportunity's size. This is a rule in `publish.py`, not a cohort setting.

## Disclosure settings (per cohort, `disclosure.py`)

| Setting                   | Meaning                                                                                   | Off                                   |
| ------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------- |
| `min_peers`               | Distinct contributing opportunities needed to publish a figure or a series period (R1/R5) | `1` (0 is refused by a DB constraint) |
| `min_denominator`         | A peer contributes only above this denominator (R2)                                       | `0`                                   |
| `require_complete_series` | Drop a peer's line if it has a hole or starts/ends inside the window (R6)                 | `false`                               |

Both KMC cohorts run with **all three off** (decided 2026-09-18: no competitive-leakage concern, no PII). The floors had been withholding exactly the early and late weeks the trends exist to show. A cohort that needs protection sets `min_peers ≥ 3` and a denominator floor. Change settings with `benchmarks_cohort_update`, then **republish**: settings apply to the next publication, not to past ones.

## Keeping it current

- **By hand:** `benchmarks_publish(cohort_id, workflow_id, run_id, …)`.
- **Automatically:** set the cohort's `source_workflow_id` and `auto_publish_on_completion` (`benchmarks_cohort_update`). Then saving a run of that workflow (the "Save this report" button or `workflow_save_snapshot`) republishes the cohort from that run, and a finished history rebuild republishes it from the newest run. Both happen in a Celery task (`tasks.py`), off the request, and a failed publish never affects the save. The logic is in `auto_publish.py`.

## The opportunity reports

`benchmarks_create_opp_reports(cohort_id, source_workflow_id=…)` creates one `kmc_opp_report` per cohort member that lacks one, all following the deployed template and sharing the source report's pipelines and registry. See `connect_labs/workflow/WORKFLOW_REFERENCE.md` §12 for how they stay in step and what still needs a person.

## Live cohorts (2026-09-18)

| Cohort                           | Org                         | Source report | Members                                                           |
| -------------------------------- | --------------------------- | ------------- | ----------------------------------------------------------------- |
| 4 — KMC programme peers          | `dimagi-kmc` (prod)         | 19778         | 523, 524, 675, 874, 938, 1234, 1236, 1487, 1488, 1739, 1790, 2166 |
| 3 — KMC programme peers (trends) | `labs-synthetic-dimagi-kmc` | 5456          | the 12 synthetic KMC opportunities                                |
