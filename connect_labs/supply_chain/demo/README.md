# Stock from visits: the synthetic RUTF world

`manage.py supply_seed_stock_from_visits [--reset]` builds a labs-only
opportunity (its own programme, id >= 10,000, labelled "Stock from visits:
synthetic RUTF (invented)") whose visits are shaped like the released RUTF
deliver app's submissions, and reads them into the supply ledger week by
week. Invented names only; this repository is public. The paths and answer
strings are the app's _structure_; no submission was read or copied.

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
- **Vitamin A** (estimated), on the Screening form only (the Visit Form has
  no Vitamin A step), as two items. Given when
  `form.chc_commodities.vita_group.va_delivered` contains `child_fine`; the
  dose question answered (`prepared_dosage`) under
  `form.chc_commodities.vita_group.prepare_vita_dosage` says the strength:
  `va_eligible_dose_6mo_to_11mo` is one 100,000 IU capsule,
  `va_eligible_dose_1yr_2year` or `va_eligible_dose_2yr_5yr` one 200,000 IU
  capsule (two lines on that rule, because `requires_paths` needs every path
  it names).
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

## Paths and form identifiers

Every path and answer string in `PATHS`, `AMOX_DOSES` and `DOSE_PREPARED` is
the released app's own, read from its definition. The rules filter forms by
xmlns (`XMLNS`): Screening is
`http://openrosa.org/formdesigner/92026AF7-291B-4E25-A60B-82486FD0C799`,
Visit Form `http://openrosa.org/formdesigner/991BB731-417A-46D2-B1F5-1E4CD65C8D89`,
Stock Management `http://openrosa.org/formdesigner/5D7EBBAD-CB58-4238-BF27-368E344AD16E`.
Every fixture carries its form's `@xmlns` and `@name`. No rule filters on
Stock Management: the balance and receipt reports read every form a rule
sees.

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

# Organisations that run their own procurement (#2458)

`manage.py supply_seed_org_procurement {oes,llo} [--local-admin]` registers a
labs-only programme, **declares the organisation that runs it** before anything
else is written, and seeds its sourcing and orders through the ordinary
operations. Dimagi plays no part in either; its people see them read-only, and
platform support (Django staff) can act for them. `org_procurement.py` says why
each was chosen:

- **`oes`**: Operation End Starvation as a *pooled buyer*. It tenders RUTF to
  producers, compares landed cost, awards and orders as buyer of record, and
  delivers into the store its implementers draw from. This is the
  `clone_upstream` chain, bought by its running organisation.
- **`llo`**: an invented Kenyan community-health LLO, Kilele, buying its own
  chlorine tablets and ORS/zinc for a safe-water opportunity from local
  distributors. It is an implementer rather than a funder, buys small rather
  than pooled, from local distributors rather than international producers,
  in a different country and basket.

Each organisation is a labs-only row under a fixed key the seeder owns
(`labs-synthetic-demo-oes`, `labs-synthetic-demo-llo`). The seeder refuses an
opportunity with the same label that is filed under any other organisation.
Visibility is the organisation's own `.example` domain plus Dimagi's.

On labs, run it without `--local-admin`. The organisation's real admin is then
added by platform support (an `OrgMembership` granted for the organisation),
because a running organisation takes no invitations. Locally, `--local-admin`
also makes `dev-oes-admin` / `dev-kilele-admin` (a `.example` address with no
usable password; DEBUG only). `/supply/dev-login/?as=dev-oes-admin` signs in as
that admin, so you can walk Supply as someone who is not Dimagi.
