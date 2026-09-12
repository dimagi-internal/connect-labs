# The labs supply domain, and RUTF procurement — design

**Status:** approved design, not yet implemented.
**Date:** 2026-09-11 (revised same day twice: see "Clients, not callers", and
section 2 on the `/supply/` domain).

## 0. In plain terms

Buying therapeutic food for the Connect-RUTF programme currently runs in a
spreadsheet, and the hard part is not tracking it — it is that **the quotes cannot
be compared to each other.** One supplier quotes per carton without saying how many
sachets a carton holds. Another quotes per sachet, for a third more product than was
asked for, and does not say whether freight is included. A third excludes import
duties that are material because the goods come out of a Nigerian free trade zone.
So the "total landed cost" column does arithmetic nobody can check, and the reasons
it cannot be checked sit in a free-text notes column beside it.

This builds a procurement app in Connect Labs that does four things:

1. **Keeps the pipeline** — who was asked, when, who replied, who is overdue.
2. **Writes the request** — generates a quote request that asks for exactly the facts
   needed to make the answer comparable.
3. **Refuses to fake a comparison** — it computes cost per sachet, per carton, per
   course and per child treated, and where a supplier did not give it enough to do
   that honestly, it says so and tells you what to go ask. Its main output is the
   next email you need to send.
4. **Remembers what things actually cost** — what LLOs really paid, per commodity,
   per course, so that "treat or refer" can be argued from measured cost.

Underneath all four sits a **master item list**: every type of stock we can buy or
track, from RUTF to amoxicillin to infant scales, each with its pack configuration, its
GS1 identifiers and its required specification. That list is what makes two suppliers
comparable, what makes a specification checkable, and what phase-2 stock will be counted
in.

Everything is typed in by hand to begin with. But every single thing the web pages
can do is also an API call and an MCP tool, so an AI agent can do it too — which is
how quotes will mostly arrive in practice: forward the supplier's email to an agent
and let it do the entering.

It is built to extend from RUTF to the wider ICCM basket — vitamin A, amoxicillin,
antimalarials, rapid diagnostic tests, dewormers, MUAC tapes, scales — without a
second app.

It lives at **`/supply/`**, because procurement is one sub-component of the supply
domain and tracking and distribution follow it. The OES demo site, which held that
address, moves to `/oes/`.

## 1. Why now

Round 1 was 500 cartons, ordered. Round 2 (February) is 2,000 cartons, re-quotes
sent 9 Sep 2026, all pending. Nine suppliers identified, four contacted, three
quoted, and the three are not comparable for the reasons above.

Suppliers mostly do not reply at all — the order volume is a rounding error next to
a UNICEF contract — and those who do will not quote without a confirmed delivery
point, because freight dominates the price.

The three standing asks from the procurement lead: standardise the quote request so
every reply comes back per carton of 150 sachets; confirm shelf life against the
project timeline; and gather costing data across commodities so supplying a treatment
can be compared to referring the patient, on evidence.

## 2. Scope, and the shape of the domain

A new app `connect_labs/supply_chain/` mounted at **`/supply/`**. Procurement is its
first sub-component, at `/supply/procurement/`; **tracking** and **distribution** are
named slots beside it, built later. The shared plumbing — the unit ladder, the
commodity and supplier registries, the operation registry, the HTTP and MCP adapters —
sits at the app root; each sub-component owns only its own services, operations and
screens. That is the seam that makes the second sub-component cheap.

The API is **domain-level, not per sub-component**: one endpoint dispatches the whole
registry, so a tracking operation added later needs no routing work.

`/supply/` is currently the OES demo satellite. It moves to `/oes/` — URL prefix,
namespace, its own tests, and the four live walkthrough recipes. Its Python package
stays `connect_labs/supply/` and its Django app label stays `supply`: renaming the
package would rewrite ~16k lines of internal imports and renaming the label would
orphan its migration history, both for an app slated for retirement. Historical
artifacts that record a rendered run — the DDD run reports, narrative locks, the
2026-07-25 design doc — are **not** rewritten; editing them would falsify history.

**In:** commodity catalogue, supplier registry, quote rounds, outreach tracking,
quote capture, honest normalisation, spec compliance, award with rationale, recorded
purchases, cost library. Web UI, HTTP API and MCP tools over all of it.

**Out of v1:** the two-stage EOI → qualification → RFP → per-lot bid scoring in
`connect_labs/supply/` (that models competitive tendering; this models chasing nine
manufacturers for a reply). EPCIS ingestion, flow maps, funder views, facilities as
supply nodes. Payment execution — LLOs transact; we record what they paid.

## 3. Relationship to prior work

**`connect_labs/supply/`** (the OES satellite site, now served at `/oes/`) stays as a
design reference and is expected to be retired once this ships.
Three principles carry over: derived values are never typed in, the
sachet → carton → course unit ladder, and one module computes a given figure so every
screen agrees. Nothing is imported — it has zero cross-app imports by design and
cannot reach real opportunities, workers or visits.

**The 2026-09-09 pivot** aimed at consumption and stock on hand derived from visits.
That is phase 2 (section 13). This spec is phase 1, upstream of it, because that is
where the work is being done by hand today.

**`connect_labs/solicitations/`** supplies the scoping pattern. It is not reused: its
respondents are Connect-authenticated LLOs answering a published solicitation, where
suppliers here are external companies answering email, if at all. When suppliers do
get Connect accounts, the binding is `supplier.connect_organization_id` — an existing
nullable field, not a second identity model.

## 4. Scoping

`connect_labs/supply_chain/`, on the house `data_access.py` + `LabsRecord` pattern.
Three tiers, because these entities have genuinely different lifetimes:

| Tier | Scope | Record types |
|---|---|---|
| Reference | `organization_id` | `commodity`, `item`, `supplier` |
| Procurement | `program_id` (experiment = program_id) | `round`, `outreach`, `quote`, `award`, `purchase` |
| Fulfilment (phase 2) | `opportunity_id` | `supply_event` |

Reference data is **organization-scoped, not `public=True`**. A supplier is the same
supplier across every programme and a carton is 150 sachets regardless of programme —
but `public=True` means world-readable in LabsRecord terms, and these records hold
supplier contact names, direct emails, phone numbers and commercial pricing.

Procurement is program-scoped because one programme's LLOs buy for several
opportunities, and a cost library is only useful if it compares across them. Phase-2
fulfilment is opportunity-scoped because visits belong to an opportunity.

Scope resolves from `request.labs_context` like every other labs app, so no
programme identifier is hard-coded anywhere.

**Scope-tier selection (resolved 2026-09-11 from the code, not from a probe).**
`LabsRecordAPIClient.__init__` coerces unconditionally — `int(organization_id)` — so a
non-numeric organisation identifier raises `ValueError` out of the constructor. It is a
loud failure, not the silent unscoped write the earlier draft feared, and the
`isinstance(..., int)` guard later in `create_record()` is therefore unreachable dead
defence.

What actually arrives: `connect_labs/labs/context.py` sets
`labs_context["organization_id"]` from the request, then replaces it with `org["id"]`
on validation (line 230). For a real Connect organisation that is numeric. For a
**labs-only synthetic organisation it is a slug** — `context.py` constructs those org
entries itself (line 103) and says so at line 208. Synthetic opportunities are how this
app is developed and demoed, so "org-scoped reference data" would crash in exactly the
environment we build in.

So `SupplyDataAccess` picks the reference tier in **one place**, on the shape of the
identifier:

| `organization_id` | Reference tier | Consequence |
|---|---|---|
| numeric | `organization_id` scope | one registry shared across the org's programmes — the intended behaviour |
| non-numeric (slug) or absent | `program_id` scope, `experiment = "supply:reference"` | registry is per-programme; correct but not shared |

Every reference record stores `reference_scope: "organization" | "program"` so a later
migration can find the program-scoped ones and lift them. Choosing the tier anywhere
other than `SupplyDataAccess.__init__` is a bug.

The probe command survives as **confirmation, not a gate**: it establishes what a real
Connect organisation's `id` actually looks like end to end, which the code can only
suggest. Run it when a token is to hand and record the result here:

```bash
make manage CMD="supply_scope_probe --organization-id <int> --token $TOKEN"
```

## 5. Data model

`LabsRecord`s reached through `ProcurementDataAccess`, following
`connect_labs/tasks/data_access.py` for shape and `solicitations/data_access.py` for
program scoping — including its labs-only routing rule: a synthetic opportunity
exposes itself as a program whose `program_id == opportunity_id`, and
`LabsRecordAPIClient` keys local routing on `opportunity_id`, so both must be passed.

### 5.1 `commodity` (reference)

The unit ladder and the specification. Generic from day one — this is what lets the
ICCM basket run through the same machinery as RUTF.

- `name`, `slug`, `category` (therapeutic_food | micronutrient | antibiotic |
  antimalarial | diagnostic | anthelmintic | equipment | consumable)
- `base_unit` (sachet | tablet | test | unit), `pack_unit` (carton | box | case),
  `base_per_pack`, `base_unit_grams`
- `course_definition`: `{base_units_per_day, days_per_course, base_units_per_course,
  source}` — the programme's treatment protocol, entered once. **No default.** Cost
  per course and cost per child treated are unavailable until it is set, and say so
  (section 6). "Roughly 150 sachets is one child's worth" is not a number a
  normalizer can divide by, and inventing a clinical figure is worse than reporting
  that it is missing.
- `spec_requirements`: list of `{field, operator, value, unit, rationale}` — e.g. for
  an infant scale, `{minimum_graduation_g, "<=", 20, "g", "100 g increments cannot
  record infant weight change"}`
- `shelf_life_months_minimum`

### 5.2 `supplier` (reference)

- `name`, `type` (manufacturer | distributor | trader), `country`, `city`,
  `origin_note` (free-zone status and the like, which drives duty treatment)
- `contacts`: list of `{name, role, email, phone, verified, verified_source}`
- `connect_organization_id` — nullable; the future account binding
- `qualifications`: list of `{scheme, reference, expires_on, evidence_ref}` (e.g.
  UNICEF or WHO prequalification). Informational in v1; no gating.
- `status` (identified | contacted | responsive | quoting | awarded | declined |
  unusable), `status_reason`
- `source` (section 11)

Mutable — a phone number changes. Nothing commercial depends on the live profile;
commercial terms live on the quote.

### 5.3 `item` (reference) — the master item list

A **trade item**: the specific, branded, pack-configured thing that is actually counted,
scanned and shipped. `commodity` is a type ("RUTF"); an `item` is
"Northwind RUTF 92 g, 150 per carton". Three reasons this is a separate record and not a
few more fields on `commodity`:

1. **Two suppliers' RUTF differ in pack configuration** — 144 per carton against 150.
   That difference is invisible at commodity level and silently corrupts every
   per-sachet comparison, which is the defect already present in the spreadsheet this
   app replaces.
2. **Stock cannot be counted per commodity.** You count items, in batches. Phase-2
   tracking has nothing to hang off without this layer.
3. **A specification is a property of an item, not a category.** "Scale" has no
   graduation; that scale does.

Fields:

- `sku` (slug, unique within the organisation), `name`, `commodity_slug`
- `manufacturer`, `brand`. Who *sells* it is not stored — that is derivable from quotes,
  and a distributor list would go stale immediately.
- packaging ladder: `base_unit`, `base_per_pack`, `pack_unit`, `pack_per_case`,
  `base_unit_grams`, `gross_weight_kg`
- GS1: `gtin_base`, `gtin_pack`, `gtin_case` — one per packaging level, each optional and
  check-digit validated on write (section 5.9)
- external references: `unicef_material_no`, `unspsc`, `atc`, `gpc_brick`
- `spec_attributes`: `{field: value}` — the item's actual specification, checked against
  its commodity's `spec_requirements`. This is durable fact; a quote's `stated_spec` is
  one supplier's claim. Both exist, and the item's value wins when the quote names a
  confirmed item.
- `shelf_life_months`, `status` (active | discontinued), `source`

**Batches are not master data.** Lot number, expiry, production date and serial are
properties of a consignment, not of an item — one item has many batches in a warehouse
at once. They land in phase 2 on the fulfilment records, named after the GS1 Application
Identifiers so a scanned barcode maps straight onto them.

### 5.4 `round` (procurement)

One quote-request cycle. Holds everything a supplier must be told to be able to quote.

- `label`, `status` (draft | open | closed | awarded)
- `lines`: list of `{commodity_slug, quantity, quantity_unit}`
- `delivery_point`: `{name, city, country, incoterm_requested}` — **required before a
  round leaves draft**, because suppliers will not quote without it
- `response_deadline`, `reminder_interval_days`
- `notes_to_supplier` — standing preamble, including why the volume is small
- `shelf_life_months_minimum` (defaults from commodity, overridable)

### 5.5 `outreach` (procurement)

One row per supplier per round — the pipeline the spreadsheet's left half is doing.

- `round_id`, `supplier_id`, `contact_email_used`
- `sent_on`, `channel` (manual | api | mcp | ses), `request_text_rendered`
- `responded`, `responded_on`, `response_kind` (quote | declined | needs_info | no_reply)
- derived: `reminder_due_on`, `days_waiting`
- `source`

### 5.6 `quote` (procurement) — the important one

**Stores exactly what the supplier said, in the unit they said it, plus the basis
flags. Nothing derived is stored.**

- `round_id`, `supplier_id`, `commodity_slug`
- `as_quoted_amount`, `as_quoted_currency`, `as_quoted_unit`
  (per_base_unit | per_pack | per_lot_total | per_metric_tonne)
- `quantity_basis`, `quantity_basis_unit` — what the quote actually covers. A quote
  for 667 cartons is not a quote for a 500-carton round and its lot total must never
  sit in a column beside one.
- `item_id` — nullable; the trade item this quote is for, when known
- `pack_spec_source` — `stated_on_quote | trade_item_confirmed | not_stated`
- `base_per_pack_stated`, `base_unit_grams_stated` — populated when the source is
  `stated_on_quote`
- `freight_basis` (included | excluded | not_specified), `freight_amount`
- `duties_basis` (included | excluded | not_specified), `duties_amount`, `duties_note`
- `incoterm`, `delivery_point_quoted`
- `shelf_life_months_stated`, `production_or_expiry_date_stated`
- `moq`, `moq_unit`, `lead_time_days`, `validity_until`
- `stated_spec`: `{field: value}` for compliance checking
- `fx_rate_to_usd`, `fx_rate_as_of`
- `document`: `{s3_key, filename, checksum, extracted_text}` — the pro-forma invoice
  is the evidence; it is stored, not just referenced
- `version`, `supersedes_quote_id`, `superseded_by_quote_id`, `correction_reason`
- `voided`, `void_reason`
- `source`

Every basis flag defaults to `not_specified`, which is the honest reading of a quote
that was silent. That default is load-bearing: a plausible guess is the commonest
failure for a person in a hurry and for a model reading an invoice, so the truthful
answer has to be the cheapest one to record.

### 5.7 `award` (procurement)

- `round_id`, `quote_id`, `decided_on`, `decided_by`, `rationale` (required)
- `comparison_snapshot` — the normalised comparison **frozen as it stood at the moment
  of decision**, so the reason a supplier was chosen stays reconstructible after later
  quotes land. Mirrors the OES app's frozen-submission rule.

### 5.8 `purchase` (procurement)

What an LLO actually paid, which is frequently not what was quoted.

- `round_id`, `supplier_id`, `commodity_slug`, `llo_organization_id` or `llo_name`
- `quantity`, `quantity_unit`, `amount_paid`, `currency`, `paid_on`
- `freight_paid`, `duties_paid`, `other_costs`: list of `{label, amount}`
- `document`, `source`

Quoted prices are an intention; paid prices are a fact. The cost library is built on
these.

### 5.9 GS1, and what we take from it

Humanitarian supply chains already have identifier standards, and UNICEF Supply Division
barcodes to them. Inventing our own keys would make every later integration a mapping
exercise, so we adopt the identifier layer now — it is nearly free — and defer the
machinery.

**Taken now, as optional fields:**

| Standard | Where it lands |
|---|---|
| **GTIN** — trade item identity, one per packaging level | `item.gtin_base` / `gtin_pack` / `gtin_case`. The natural master-list key. |
| **GLN** — locations and parties | `supplier.gln`; phase-2 supply nodes |
| **GPC brick** — product classification | `commodity.gpc_brick`, `item.gpc_brick` |
| **Application Identifier vocabulary** | field *names*: `01` GTIN, `00` SSCC, `10` batch/lot, `17` expiry, `11` production date, `21` serial, `414` location GLN. Naming our fields after the AIs means a scanned GS1-128 or DataMatrix maps 1:1 with no translation layer. |
| **GS1 Digital Link** | already implemented in the code being ported |

Non-GS1 references carried on `item` for the same reason: `unspsc`, `atc`, and
**`unicef_material_no`** — that last one specifically because UNICEF is the reference
price for RUTF, and holding their material number is what lets the cost library say
"our landed cost is X% over the UNICEF published price", which is a far stronger claim
than three quotes compared against each other.

**Deferred:** **GDSN**, the master-data exchange network. Suppliers who do not answer
email are not publishing to a data-sync network, and the attribute names above stay
GDSN/GDD-compatible so we could join later without a migration.

**Phase 2 uses EPCIS 2.0 + CBV** for the fulfilment event log — its what/where/when/why
vocabulary (`epcClass`, `bizStep`, `disposition`, `readPoint`, `bizLocation`) is the shape
that log wants, and inventing a parallel vocabulary would be a straight loss.

**Implementation note.** `connect_labs/supply/gs1.py` already implements the mod-10 check
digit, GTIN/GLN/SSCC construction, `is_valid()`, and Digital Link build/parse in 100
lines. It is **ported, not imported** — the OES app is being retired — and its
`SACHETS_PER_CARTON = 150` / `SACHET_GRAMS = 92` / `CARTONS_PER_CHILD_TREATED = 1`
constants are **dropped**: hardcoding the ladder is exactly what this design refuses, and
those values now live on `commodity.base_per_pack`, `base_unit_grams` and
`course_definition`.

The AI numbers above are recalled, not looked up. They must be checked against the GS1
General Specifications before any code encodes or parses a real barcode.

## 6. The one invariant

> **Every comparable number is derived, never stored. A derivation whose inputs are
> unconfirmed returns `Unconfirmed(reasons)` — never a number.**

The counterpart of the OES app's "stock is derived from events, never typed", and the
whole reason this beats a spreadsheet.

`services/pricing.py` computes, from a quote and its commodity:
`usd_per_base_unit`, `usd_per_pack_normalized`, `usd_per_course`,
`landed_total_as_quoted`, `landed_total_for_round_quantity`, `usd_per_child_treated`. Each returns a `Money` or
an `Unconfirmed`, whose reasons are specific because they become the questions sent
back to the supplier.

Rules:

1. A quote whose `pack_spec_source` is `not_stated` yields `Unconfirmed` for every
   per-base-unit and per-course figure. **The commodity's `base_per_pack` is not
   substituted** — that substitution is the exact error this exists to prevent.

   A `pack_spec_source` of `trade_item_confirmed` **is** a statement: a supplier who
   identifies the trade item being quoted has told us its pack configuration as surely
   as if they had typed it, and the figure derives from `item.base_per_pack`. What is
   forbidden is the *commodity* assumption about a pack nobody identified — never an
   identified item published one.

   **The same rule governs unit weight**, and for the same reason. A quote priced per
   metric tonne needs grams-per-base-unit to yield a per-base-unit figure, and that
   weight may come from the quote or from a confirmed item — never from the commodity
   catalogue. A 92 g catalogue assumption against a real 100 g sachet is an 8.7% error,
   the same order as 144 against 150 per carton, and it arrives wearing the same
   unearned authority. One helper resolves unit weight for every figure, so two numbers
   in the same result can never disagree about the same gram.

   This was originally written for pack spec alone. The asymmetry was found in review on
   2026-09-11, after it produced a landed total 8.7% high — reported as a confirmed
   figure — because two call sites resolved the same gram differently. An invariant that
   applies to one supplier-specific quantity and not its neighbour is worse than a strict
   one: nobody can remember which half is in force.
2. A quote whose `quantity_basis` differs from the round's quantity carries that
   basis, and the comparison will not place its total beside a conforming quote's.
3. `not_specified` freight or duties — or `excluded` with no amount — makes every
   landed figure `Unconfirmed`.

   Landed totals are computed from the price basis the supplier actually quoted, not by
   dividing to a per-unit figure and multiplying back up. Over fixed-precision decimal
   arithmetic that round trip does not reliably cancel: `50.00/150 × 300000` yields
   `99999.99999…`, not `100000.00`. A price and a quantity already in the same unit
   multiply exactly, and that is the path taken whenever the two units agree.
4. A missing `fx_rate_to_usd` on a non-USD quote makes every USD figure
   `Unconfirmed`. Currency is just another confirmable input.
5. A commodity with no `course_definition` makes per-course and per-child figures
   `Unconfirmed`. Configuration is held to the same standard as supplier data.
6. **The comparison partitions, and never ranks across the partition.** A quote that
   yields a full set of figures is *comparable* and is ranked. A quote with any
   `Unconfirmed` figure is *blocked*, never placed in the ranking, and surfaced as a
   worklist of the questions that would unblock it. Every comparison states
   "N of M comparable", names the blocked suppliers, and labels its leader
   **provisional**.

Rule 6 is the product. In the spreadsheet the blockers are prose and the comparison
happens anyway, silently. Here the ranking covers only what can be defended and carries
its own incompleteness on the same screen, so the answer to "who is cheapest" becomes
"Harmattan, provisionally — and three suppliers could still beat it; here are the emails
to send."

An earlier draft suppressed the ranking entirely whenever any candidate was unconfirmed.
That is more faithful to the letter and worse in practice: it withholds the comparison we
*can* defend along with the one we cannot, and the procurement lead does exactly two
things all week — decide, and chase. The partition serves both; total suppression serves
neither.

`pricing.py` is the only place any of these figures is computed — screens, API
responses, MCP results, exports and the phase-2 reorder calculation all call it.

## 7. One schema, three directions

`services/questions.py` derives, for any quote, the set of facts still missing before
`pricing.py` could answer honestly. That one function feeds three renderers:

- the **initial request** for a round (nothing is known yet, so it asks for
  everything: price per pack at a stated pack spec, incoterm and delivery point,
  explicit freight and duties treatment, shelf life against the round minimum, MOQ,
  lead time, validity),
- the **follow-up** to a supplier who answered incompletely (asks only what is
  missing),
- the **outstanding questions** panel on the comparison screen.

So standardising the request and normalising the replies are not two disciplines that
must be kept in step — they are one schema read in different directions. Add a field
to `quote` and the next request asks for it; drop a question and it shows up as a
systematic `Unconfirmed`.

**Two kinds of missing fact, and both get chased.** A *comparability* fact — price,
unit, pack spec, quantity basis, freight, duties, exchange rate, unit weight — is one
`pricing.py` needs before it will yield a number at all. An *acceptance* fact — shelf
life against the round's minimum, MOQ, lead time, quote validity — does not block the
arithmetic but decides whether the offer is usable. Confirming shelf life against the
project timeline is one of the programme's three standing asks, so a supplier who sends
a price and no shelf life is exactly who needs a follow-up. Both kinds appear in the
follow-up; neither is dropped merely because it did not happen to block a calculation.

**Some missing facts are ours, not the supplier's.** A `MissingFact` carries an
`audience`: `supplier` or `internal`. "How many sachets are in a carton" is a question
for a manufacturer; "nobody has entered the treatment protocol, so cost per course
cannot be reported" is a question for us. The follow-up renderer sends only the
supplier-facing ones; the comparison screen shows the internal ones as our own
outstanding work.

That field exists because the alternative — leaving an internal reason unmapped — means
a real missing fact nobody is ever told about, which is the same silent failure
`Unconfirmed` was introduced to abolish. **Every reason maps to a fact**; `audience`
decides only where it surfaces. It also has a concrete payoff today: the programme has
not yet supplied its ration table, so the comparison reads "cost per course unavailable:
nobody has entered the treatment protocol" rather than showing an empty column.

Phase 1 renders text to send from a human mailbox — a supplier who ignores email will
ignore a portal too, and the credibility is in the sender. SES send and a tokenized
supplier form attach to the same records later.

## 8. Spec compliance

`services/compliance.py` evaluates a specification against the commodity's
`spec_requirements`, returning `pass | fail | not_stated` per requirement with the
rationale attached. A scale quoted at 100 g graduation fails
`minimum_graduation_g <= 20` and says why.

It reads the specification from **the confirmed trade item first, the quote second**:
`item.spec_attributes` is durable fact about a product, where `quote.stated_spec` is one
supplier claim on one day. When a quote names a confirmed item, the item wins and the
quote value is kept only for comparison — a supplier claiming a graduation the item
sheet contradicts is itself worth seeing. When no item is named, the quote claim is all
there is.

`not_stated` is distinct from `fail` and feeds `questions.py` exactly as `Unconfirmed`
does. This is why the ICCM basket needs no second app: those commodities differ in
their `spec_requirements`, not in their machinery.

## 9. One domain, three equal clients

Everything is hand-entered to start, but there is no such thing as a capability the
web UI has and the API does not.

```
connect_labs/supply_chain/
  records.py         LabsRecord type constants                     [shared]
  values.py          Money, Unconfirmed, the unit ladder           [shared]
  models.py          proxy models for every record type            [shared]
  data_access.py     LabsRecord CRUD + two-tier scoping            [shared]
  operations.py      the registry + the reference operations       [shared]
  views.py           domain shell    ─┐
  api_views.py       HTTP API        ─┼─ three adapters, no logic
  mcp_tools.py       MCP tools       ─┘
  procurement/       SUB-COMPONENT ONE
    operations.py    registers procurement_* into the registry
    views.py         the procurement screens
    services/        the rules — the only place they live
      pricing.py     derivation and Unconfirmed
      questions.py   what is still missing (section 7)
      render.py      request / follow-up text
      compliance.py  stated_spec vs spec_requirements
      costing.py     cost library aggregation            (phase 1b)
  tracking/          later
  distribution/      later
```

**The rule: no capability without an operation.** A Django view may not mutate state
except by calling one, and the HTTP API and the MCP registry are both loops over the
same registry — the API is a single dispatch endpoint rather than hand-written routes,
so parity is structural rather than maintained. Tests assert it: every operation is reachable over HTTP and over
MCP with matching schemas, and no view mutates a record outside an operation. A drifted
surface fails the suite instead of being discovered by an agent that cannot do
something a person can.

This is what makes a future in-app AI assistant unremarkable. It is a client of the
same operations as an external agent, with no privileged internal path — so it cannot
develop capabilities the API lacks, and building it requires no new surface.

Operations, all `is_write=True` ones rate-limited and argument-logged to `MCPAuditLog`
via `connect_labs/mcp/tool_registry.register`:

`commodity_list` / `_get` / `_upsert`, `supplier_list` (searchable) / `_get` /
`_create` / `_update`, `round_list` / `_get` / `_create` / `_update` / `_open` /
`_close`, `request_render`, `followup_render`, `outreach_log` / `_list` / `_update`,
`quote_record` / `_list` / `_get` / `_correct` / `_void`, `quote_questions`,
`round_compare`, `round_outstanding_questions`, `award_create`, `purchase_record` /
`_list`, `cost_library`.

MCP names are prefixed `procurement_`.

## 10. Clients, not callers

**Labs does not police its clients.** It offers clean reads and clean writes and
records what it is told. Deduplication, interpreting an email, deciding whether a
message has already been processed, and judging whether a supplier is one already on
file all belong to whoever is calling — an agent can read before it writes, and
`supplier_list` is searchable precisely so it can.

Concretely, and deliberately absent: no duplicate detection that refuses to create, no
conflict resolution, no idempotency keys, no email-parsing tool, and no validation that
tries to establish where a caller got a number from.

What labs does enforce is that a record is **well formed and internally coherent** —
a quote references a real round and commodity, amounts are positive, enum fields hold
enum values, a round has a delivery point before it opens. That is not policing a
caller; that is being a system of record.

The expected path in practice: forward a supplier's reply to an agent with "add this
supplier and quote", and it reads the registry, then writes. Nothing in labs knows or
cares that this happened by email.

## 11. Provenance, immutability, and cleaning up

`source` is on every record: `{channel, actor, reference, note}`, where `channel` is
`manual | api | mcp | ses`. Optional, conventional, never a gate. Hand entry records
`manual` and the user; an agent records what it was working from. Uniform provenance
means "where did this come from" has one answer shape regardless of route.

**Quotes are append-only.** `quote_correct` writes a new version carrying
`supersedes_quote_id` and a reason; the old version stays readable and drops out of
comparisons. A re-quote in February is a new quote, not an edit of May's — it is a
fact about February. This is domain integrity, not client management: overwriting
would make a past award's `comparison_snapshot` unreproducible, and a comparison
shown to a funder has to stay reconstructible.

Immutability also removes the need for conflict machinery — there are no conflicting
updates because there are no updates.

`quote_void(reason)` is how a client cleans up after itself: a duplicate, a
mis-transcription, a quote later withdrawn. Voided rows stay readable and leave
comparisons. That is the whole cleanup contract, and it is why labs needs no
duplicate prevention: duplicates are visible and removable by the client that made
them.

## 12. Screens

0. **Domain landing** (`/supply/`) — the sub-components and enough state to pick one.
   Tracking and distribution appear here as named-but-unavailable from day one, so the
   shape of the domain is visible rather than implied.
1. **Round board** — supplier × round: contacted, days waiting, responded, quote
   status, reminder due, next action.
2. **Quote entry** — as-quoted fields with explicit basis selectors, `not_specified`
   preselected, live compliance check and live `Unconfirmed` reasons as fields fill,
   so whoever is entering sees immediately what the reply failed to say.
3. **Comparison** — two sections, per rule 6. A banner states "N of M comparable" and
   names who is excluded. **Comparable** is a ranked table of normalised figures with
   the leader marked provisional and an award action. **Needs info** lists each blocked
   supplier with its outstanding questions and a copy-follow-up action. Award requires a
   rationale.
4. **Registries** — the **item master** (trade items: commodity, pack configuration,
   GTIN, spec verdict), which flags where two items under one commodity disagree on pack
   configuration; the commodity catalogue with specification requirements and course
   definition; and the supplier registry with contacts, status and qualifications.
5. **Cost library** — per commodity: quoted range, paid actuals, cost per course, cost
   per child treated, by LLO and over time, provenance per figure, CSV export.

Django templates in the labs house style; React only for the comparison grid.

## 13. Cost library, and phase 2

`services/costing.py` aggregates `purchase` rows across programmes and LLOs into cost
per course per commodity, each figure citing the purchases and the `course_definition`
behind it. Quoted and paid figures are reported separately and never averaged together.

That is the supply side of the treat-versus-refer question, measured rather than
estimated. The referral side is out of scope.

**Phase 2** is the 2026-09-09 model. Not built here, but the phase-1 model is shaped
so it lands without rework:

- An award and its purchase imply an expected delivery. Despatch and receipt become
  opportunity-scoped `supply_event` rows in an append-only log; stock on hand, weeks
  of cover, stock-out date and expiry risk derive from that log in one module, exactly
  as `pricing.py` is the one place for money.
- Consumption comes from a **pluggable source**: derived from the deliver form if it
  records sachets dispensed per visit, otherwise from periodic counts. Whether that
  field exists is an empirical question about the real app, not a design decision, so
  the design does not depend on the answer — phase 2 opens by checking with
  `get_form_json_paths` and selecting a source.
- The commodity catalogue and unit ladder are shared, so a carton means one thing on
  both sides.
- The loop closes when cover and expiry projections compute the next round's quantity,
  at which point `round.lines` carries a provenance of its own.

Synthetic opportunity **10035** ("SAM Child Recovery (RUTF Follow-up) — Synthetic
Demo", 40 visits) is the phase-2 development and demo ground: labs-only, no real
patient data.

## 14. Existing data, in and out

A one-time importer reads the current tracker export into `supplier`, `round`,
`outreach` and `quote` records and runs `pricing.py` over the result — so day one
shows the real pipeline and its real gaps rather than an empty app, and the import is
the first honest test of the normalizer. CSV export from every list view and from the
cost library, so the tool is never a one-way door.

## 15. Testing

- `pricing.py` is the centre of gravity: a case per `Unconfirmed` reason, and an
  explicit regression test that a commodity's `base_per_pack` is never substituted for
  an unstated pack spec.
- `questions.py`: the missing-fact set for a silent quote, a fully-specified quote
  (empty), and each partial case; and that `render.py` follow-ups ask only what is
  missing.
- `compliance.py`: `pass` / `fail` / `not_stated` per operator, with the scale
  graduation case worked.
- Surface parity per section 9: every operation reachable over HTTP and MCP with
  matching schemas; no view mutates outside an operation.
- Immutability: a correction creates a version and leaves the original readable; a
  void leaves comparisons; an `award.comparison_snapshot` does not change when a later
  quote lands.

**This repository is public.** No supplier contact details, no real quoted or paid
prices, no LLO-identifying data in fixtures, tests or docs. Fixtures use invented
suppliers reproducing the shapes that matter: a per-sachet quote against a per-carton
one, a missing pack spec, unspecified freight, excluded duties, a quantity basis over
the round, a non-USD quote with no rate. Real data lives only in `LabsRecord`s.

## 16. Phasing

- **1a** — registries (commodity, **item**, supplier), the ported `gs1.py` identifier
  helpers, rounds, outreach, quote capture, `pricing.py`, `questions.py`,
  `compliance.py`, the partitioned comparison, award. All three surfaces from the start.
- **1b** — document storage, tracker import, cost library, exports, follow-up
  rendering.
- **1c** — SES send with a tokenized supplier quote form; `connect_organization_id`
  binding as suppliers get Connect accounts.
- **2** — fulfilment: events, stock on hand, consumption from a selected source,
  computed reorder quantities.
