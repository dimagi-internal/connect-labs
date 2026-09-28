# Supply: stock we believe each worker holds, from their submitted visits

Status: design agreed in conversation 2026-09-28; §9 mapped from the real app the same day.
Builds on `2026-09-11-rutf-procurement-design.md` Part 2 (§18 a worker is a
supply point, §19.2 consumption is derived, §20 counts and overrides, §22 the
product derives and does not recommend).

## 1. Why

A programme hands stock down a chain — central store, partner store, the
frontline worker — and the worker gives it out at visits. Today the ledger can
hold all of that, and a worker already *is* a supply point, but nothing turns a
visit into stock leaving the worker's bag. Every `consumption` movement in the
repo is typed by a seeder. So "how much RUTF does each worker have left?" has
no honest answer, and neither does anything built on it: months of cover,
days to stock-out, resupply.

§19.2 decided the answer in September — *consumption is derived, not
entered; nobody keys a consumption report* — and left it unbuilt. This builds
it, and shows the result at every level down to the worker.

**Synthetic only** until the product owner says otherwise. Nothing here reads
or writes a real programme's supply data (program 263 included) until then.

## 2. Decisions taken

| Decision | Choice | Reason |
|---|---|---|
| Where visit consumption lives | `consumption` movements in the existing ledger | Balance, AMC, months of stock, resupply, the network roll-up and as-of pick it up unchanged |
| When a visit counts | **On arrival, whatever its approval status.** A visit later `rejected` (or flagged `duplicate`) posts a reversal | The sachets left the bag whether or not the visit is paid. Owner's call, 2026-09-28 |
| How much of a figure is unapproved | Shown beside every worker figure, never folded in | The early count is only safe if its uncertainty is visible |
| A visit with no answer in the mapped field | Unknown, counted and shown — never zero | A missing answer is not "dispensed nothing" |
| Form has no quantity field at all | A fixed ration per visit type, labelled **estimated** everywhere it appears | Better an honest estimate than no figure; never passed off as stated |
| Mapping from form to stock | A per-opportunity record editable without a deploy | Forms differ per opportunity and change; a deploy per field rename is wrong |
| Worker supply points | Created automatically from the workers who submit | Hand-made points never keep up with a real roster |
| Ranking workers | None. Facts only: days to stock-out, variance, days since checked | §22 |

## 3. Model

### 3.1 `DispensingRule` (new)

One per (program, opportunity, item). A real iCCM deliver app gives out many
commodities from one visit (§9), so an opportunity carries a rule per item,
not one rule. Fields:

- `program_id`, `opportunity_id` (integers, as everywhere in supply)
- `item` (FK) — what is dispensed; its pack spec converts base units to packs
- `lines` — what a visit gives out of this item. Each line is one of:
  - **stated**: `{path, unit}` — read a number from the visit's `form_json`
    (`extract_json_path_multi` fallbacks allowed), e.g.
    `form.rutf_dispensing.rutf_sachets_dispensed`;
  - **protocol**: `{given_path, given_values, quantity | by_age, unit}` —
    the form records only *that* it was given (`ors_given = yes`,
    `va_delivered = child_fine`), so the quantity comes from the protocol,
    optionally banded by the child's age in months read from `age_path`.
  A visit's movement for the item is the sum of its lines, so the appetite
  test's third of a sachet and the ration given the same visit are one
  consumption.
- `resupply_point` (FK `SupplyPoint`) — the store auto-created worker points
  hang from
- `active_from` — visits before this date are not read (so turning a rule on
  mid-programme does not invent history the ledger never saw)

A movement made only from stated lines is **stated**; one with any protocol
line is **estimated**, and says so wherever it appears.

Written through `dispensing_rule_upsert` / `_get` / `_list` operations, so it
is an MCP tool and a web form like every other supply write, attributed and
revisioned (#2075).

### 3.2 `Movement` additions

- `visit_id` (CharField, indexed). Two partial unique constraints make a
  re-run write nothing: at most one row per (`visit_id`, `item`) with
  `reverses` null (the consumption) and at most one with `reverses` set (its
  reversal).
- `reverses` (nullable FK to `Movement`). A reversal is a `consumption` row
  *into* the worker's point, pointing at the row it cancels.
  `consumption_by_unit()` and the resupply `demand_basis` net reversals out,
  so a rejected visit leaves neither the balance nor the monthly consumption
  inflated.
- `estimated` (bool) — true when any protocol line contributed. Carried up every
  aggregate as "N of this figure is estimated".

`source="connect_visit"` (already in `records.SOURCES`) on every row.

### 3.3 Supply points

`SupplyPoint.connect_user_id` is an `IntegerField`; the visit cache's
`user_id` is a Connect UUID string. Add `connect_user_uuid` (CharField,
indexed) and match on `(opportunity_id, connect_username)` first, UUID
second. Auto-created points: `kind=user_held`, parent = the rule's
`resupply_point`, slug `user-{opp}-{username}` (the existing ingest
convention).

### 3.4 What the worker's own app already says

The RUTF app keeps a running balance per worker on its user case and has a
**Stock Management** form (§9). Two things follow, and both reuse existing
records rather than inventing new ones:

- **Receipts the worker reports** (`sachets_received`, `date_received`) are a
  worker's account of a distribution. They are posted as a
  `StockCount`-style report of *receipt* against the worker's point — never as
  a ledger movement, because the ledger's inflow to a worker is the
  distribution the store recorded. Where the store recorded none, the gap is
  itself the finding ("worker reports 300 received on 3 Oct; no distribution
  recorded").
- **The app's computed balance** (`new_stock_balance` on each visit,
  `sachets_remaining` on Stock Management) is posted as a `self_reported`
  `StockCount` through the existing `stock_report_ingest` path. The variance
  between it and the ledger is then the existing `stock_on_hand` variance.

## 4. The visit reader

`visit_consumption_ingest` — an `internal=True` operation (like
`stock_report_ingest`) run by a Celery beat task for every active rule, and
by `manage.py supply_ingest_visit_consumption --opportunity N [--dry-run]`.

1. Read visits through `fetch_raw_visits` / `get_export_client` — never a
   hand-built export client — so a synthetic opportunity's fixtures are read
   exactly as a real one's would be.
2. For each visit on or after `active_from`:
   - resolve the worker's point (create it if missing);
   - for each active rule on the opportunity, sum the rule's lines from
     `form_json` (stated lines read the number; protocol lines apply the
     protocol quantity when the given-path says it was given);
   - post one `consumption` movement per item (`occurred_on` = visit date,
     `visit_id` set). Already posted → skip.
   - status `rejected` or `duplicate` and a consumption exists → post its
     reversal. Already reversed → skip.
   - no answer → no movement; counted in the report as `no_answer`.
3. Return a report: posted, reversed, skipped, `no_answer`, `unmatched`
   workers, unit refusals (a unit the item's pack spec cannot convert).

Everything goes through `call_operation`, so each run is one attributed
`OperationCall` and its movements carry Revisions.

**Fix alongside:** `stock/services/ingest.extract_rows` applies its path to
the top-level visit dict; real export and cache rows keep answers under
`form_json`, so the documented path would read nothing from a real export.
Point it at `form_json`, take the xform id (not the visit id) as
`form_submission_id`, and route `supply_ingest_stock_reports` through
`get_export_client` too.

## 5. What we believe, per worker

`stock/services/belief.py` returns, per worker point and item, as of a date:

| Figure | From |
|---|---|
| issued | `distribution` movements in |
| dispensed | `consumption` out, net of reversals |
| of which unapproved | dispensed on visits whose status is not `approved` |
| of which estimated | `estimated=true` movements |
| ledger on hand | the existing balance |
| last reported | latest `StockCount` (self-reported or physical) and its date |
| variance | ledger − reported at the count date (existing `stock_on_hand`) |
| days since checked | today − last count date |
| months of cover, days to stock-out | the existing `resupply.plan` |

Store levels are the same figures summed over their subtree — counts sum up
the hierarchy; cover and rates are recomputed at each level, never summed.

**Performance.** `network_stock` calls `resupply.plan` once per point. That is
fine for thirty stores and wrong for six hundred workers. `belief.py`
computes the whole subtree in grouped SQL (one pass per figure, grouped by
point and unit) and the stock page moves onto it.

## 6. Screens

All program-scoped, all honouring `?as_of=`.

1. **Network** (`/supply/network/`, reworked): the tree from central store
   to workers. Each node shows on hand, months of cover, its band, and when
   it was last checked; a store's row carries its subtree's totals and a
   count of workers below minimum. Expand a store to see its workers.
2. **Workers** (`/supply/workers/`, new): one row per worker — on hand,
   days to stock-out, variance, days since checked, share unapproved, share
   estimated. Sortable by any column; no default "worst first" ranking.
3. **Worker** (`/supply/workers/<point>/`, new): a timeline chart of
   issued (steps up), dispensed (steps down) and reported counts (points),
   with the ledger line between them; below it the visits and distributions
   behind each step, each visit linking to its form answer.
4. **Map**: worker points already plot; add on-hand as the marker's size and
   the band as its colour. Consumption stays undrawn as a flow (it leaves the
   network).

Every figure that rests on unapproved visits or estimates says so on the
figure itself, not in a footnote.

## 7. Synthetic world

A synthetic RUTF opportunity whose visit fixtures use **the real app's form
paths** (§9), so the rules written for it transfer to the real opportunity
unchanged, and a seeder that:

- sets rules for RUTF (stated) and for Vitamin A, amoxicillin, AL and mRDT
  (protocol, by age) — ORS and zinc stay off until §9's contradiction is
  settled;
- has workers file Stock Management forms (receipts and balances) and carry
  the app's running balance on each visit;
- issues stock to a partner store and from it to ~20 workers;
- runs the reader over ~8 weeks of visits;
- has most workers submit counts that reconcile, one that runs out, one
  whose count is well above the ledger (under-reported dispensing), one
  visit rejected after it was counted (the reversal shows);
- leaves one worker with visits that never answer the field (`no_answer`).

Invented names only; this repo is public.

## 8. Testing

- Reader: idempotent re-run; reversal on rejection and on `duplicate`;
  reversal idempotent; `no_answer` never posts; unmatched workers reported;
  `active_from` respected; unit conversion refusal; `protocol` rows marked
  estimated.
- Ledger: reversals net out of balance and AMC; the negative-quantity
  constraint still holds; one consumption and at most one reversal per visit.
- Belief: subtree sums equal the sum of children; cover recomputed per
  level; as-of reproduces a past date.
- Reader goes through `get_export_client` (synthetic routing test).
- `extract_rows` reads `form_json`; regression test with a real-shaped row.
- Grouped SQL: query count constant in the number of workers.
- Screens: as-of hides write controls; unapproved and estimated shares shown.

## 9. The real RUTF app (opportunity 2230), mapped 2026-09-28

Read from the released deliver app ("RUTF Deliver - NG - CBI - P1", built
2026-09-25) through `get_opportunity_apps`. App definition only; no
submissions were read. Paths below are `form_json` paths (`/data/x` in the
app is `form.x` in a submission). This is the programme's full iCCM basket,
not RUTF alone.

| Item | Form | Line | Kind |
|---|---|---|---|
| RUTF sachet | Screening | `form.visit_1.rutf_dispensing.rutf_sachets_dispensed` | stated |
| RUTF sachet | Visit Form | `form.rutf_dispensing.rutf_sachets_dispensed` | stated |
| RUTF sachet | both | appetite test, ⅓ sachet (`form.screening_outcome.rutf_stock_deduction`, `form.var.appetite_test_stock_deduction`) | stated (app-computed) |
| ORS sachet | Screening, Visit Form | `…ors_group.ors_given = yes`, CHC `…chc_commodities.ors_group`; 4 per child | protocol |
| Zinc tablet | same | with ORS; 10 per episode | protocol |
| Vitamin A capsule | Screening, Visit Form | `…vita_group.va_delivered = child_fine`; 100,000 IU at 6–11 months, 200,000 IU at 12–59 | protocol, by age |
| Albendazole | Screening, Visit Form | `…dw_group.dw_delivered = child_fine`; dose by age | protocol, by age |
| Amoxicillin DT | Screening, Visit Form | `form.visit_1.presumptive_amoxicillin_given`, pneumonia `…fast_breathing_treatment` (`dosage_pneumonia`); twice daily × 5 days, by age | protocol, by age |
| AL (antimalarial) | Screening, Visit Form | `…fever.mrdt_result = positive`; by age | protocol, by age |
| mRDT | Screening, Visit Form | one per `…mrdt_result` answered | protocol |
| Paracetamol | Screening, Visit Form | `…paracetamol_given = yes` (`paracetamol_dosage`) | protocol, by age |

Worker stock records in the same app:

- **Stock Management**: `form.current_stock.sachets_received`,
  `form.current_stock.date_received`, `form.stock_balance.sachets_remaining`
  (§3.4).
- **Every visit**: `form.var.new_stock_balance` — the app's own balance after
  the visit (§3.4).
- **Alert Low Stock** survey: the app already tells a worker to restock
  before visiting; the belief view should agree with it or say why not.

Two findings for the programme, not for this build:

- The same diarrhoea step tells the worker "hand 4 ORS sachets per child" in
  one place and "hand over 2 copacks per child" in another. The protocol line
  for ORS/zinc cannot be settled until the programme says which it is; until
  then the ORS and zinc rules stay off.
- Household adherence (`misused_rutf_amount`, `leftover_rutf_number`,
  `empty_sachets`, `packets_missing`) is stock *after* it left the worker. It
  is not worker stock and is out of scope here; it is a later "did it reach
  the child" measure.

Still open: whether the synthetic world should mirror this app's exact paths
(recommended: yes — a synthetic RUTF opportunity whose fixtures use these
paths, so the rules written for it transfer unchanged).

## 10. Out of scope

Any write to a real programme; syncing supply back to Connect; recommending
or ranking workers; batch/expiry tracking at worker level; household
adherence (what happened to stock after the worker gave it out).
