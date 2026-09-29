# Stock from visits: the synthetic RUTF world

`manage.py supply_seed_stock_from_visits [--reset]` builds a labs-only
opportunity (its own programme, id >= 10,000, labelled "Stock from visits:
synthetic RUTF (invented)") whose visits are shaped like the released RUTF
deliver app's submissions, and reads them into the supply ledger week by
week. Invented names only; this repository is public. The paths and answer
strings are the app's *structure*; no submission was read or copied.

## What it shows

Design `docs/superpowers/specs/2026-09-28-supply-stock-from-visits-design.md`
§7, as resolved from the released app (the plan's "Resolved from the released
app" section, which overrides the spec's §9 table):

- A central store, a partner store under it, and 20 workers (`worker-acacia`
  … `worker-tamarind`) resupplied from the partner: every product in week 0,
  RUTF again in week 4.
- Three visits per worker per week (Tuesday, Thursday, Saturday); the first
  each week is a **Screening**, the rest are **Visit Form**s. A **Stock
  Management** form follows each delivery.
- **RUTF** (stated): on Screening, `form.screening_outcome.rutf_stock_deduction`
  alone -- it already is the total (the ration, or 1 for an appetite test
  alone). On Visit Form, `form.rutf_dispensing.rutf_sachets_dispensed` plus
  `form.var.appetite_test_stock_deduction` (a whole sachet, only when no
  ration was given). The phone's own balance (`form.var.new_stock_balance`,
  `form.stock_balance.sachets_remaining`) and receipts
  (`form.current_stock.*`) are recorded as counts beside the ledger.
- **Amoxicillin DT** (estimated): a `value_map` on the three `dosage_pneumonia`
  questions with the app's exact strings -- including the missing space in
  `"2 tablets every 12 hours(total 20 tablets)"` -- plus presumptive
  amoxicillin (`form.visit_1.presumptive_amoxicillin_given = yes`, 10 tablets).
- **Vitamin A** (estimated), as two items: 100,000 IU when the 6-11 month
  dose question was answered, 200,000 IU for either older band; given only
  when `va_delivered` contains `child_fine`.
- **mRDT** (estimated): one per answered `mrdt_result`, `invalid` included.
- **Off, deliberately**: AL and paracetamol (no dose field / mixed units),
  ORS and zinc (the app says both 4 sachets and 2 co-packs), albendazole (not
  in §7's story). No rule reads the child's age: it is a case property, never
  in the form.

The story: most workers' app balances reconcile with the ledger;
`worker-kapok` runs out; `worker-marula` reports a receipt of 100 that no
store recorded, so her count sits 100 above the ledger; one of `worker-neem`'s
week-2 visits is rejected in week 4 and reversed (her phone still counts those
14 sachets as given, so she reads 14 below); `worker-sapele` never answers
the RUTF question (`no_answer` on every visit).

Every write goes through the operations, dated with `seed_overrides`: set-up
a week before the first visit, each distribution on its Monday, and each
week's read on its Sunday evening (`visit_consumption_ingest` with `until`
that Sunday). So `?as_of=` any day in the eight weeks shows that day's page,
and the reversal appears from week 4's Sunday.

Where to look: `/supply/network/`, `/supply/workers/`, one worker's page,
and `?as_of=` any day in the eight weeks.

## Stand-ins

Marked `STAND-IN` in `PATHS`: the Vitamin A paths. The addendum elides them
(`…vita_group.va_delivered`, `…prepare_vita_dosage.va_eligible_dose_*`); the
seeder puts them where the same app keeps albendazole
(`form.chc_commodities.…` on Screening, `form.visit_2_or_greater.…` on Visit
Form). The dose questions' own answer (`"OK"`) is a stand-in too; the rule
only needs them answered. `child_unwell` is an invented "not given" value.
Before copying the Vitamin A rules to opportunity 2230, read the released
app with `get_opportunity_apps` and replace each stand-in.

## Running it

Locally (needs Drive credentials and `LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID`
in `.env`, because the fixtures are uploaded as for any synthetic opp):

    make manage CMD="supply_seed_stock_from_visits"
    make manage CMD="supply_seed_stock_from_visits --reset"

On labs, through the web task (see the command's docstring for the full
`aws ecs execute-command` line). It prints the opportunity id it registered
(the next free labs-only id, fixed thereafter: re-runs find it by label) and a
per-week reader summary.

A second run without `--reset` changes nothing. `--reset` purges this
programme's supply data and history (allowed only for a registered labs-only
programme) and seeds it again with a fresh fixture folder, dated from today.
