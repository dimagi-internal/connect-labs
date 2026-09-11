# RUTF Procurement in Connect Labs — design

**Status:** approved design, not yet implemented. Supersedes the sourcing half of the
2026-09-09 supply pivot; see "Relationship to prior work".

**Date:** 2026-09-11

## 1. Why

The Connect-RUTF programme buys ready-to-use therapeutic food, and will shortly buy a
wider integrated community case management (ICCM) basket. Today that procurement runs in
a spreadsheet, and the spreadsheet shows precisely where it hurts.

Round 1 was 500 cartons, ordered. Round 2 (February) is 2,000 cartons, re-quotes sent
9 Sep 2026 and all pending. Nine suppliers identified, four contacted, three quoted —
**and the three quotes are not comparable to each other**:

- One quoted per carton, with freight itemised separately, and never stated how many
  sachets a carton holds — so a per-sachet figure cannot be derived.
- One quoted per sachet, for a quantity 33% larger than the round, with freight
  unspecified.
- One quoted per carton but excluded import duties, which are material because the
  goods are manufactured in a Nigerian free economic zone.

The landed-cost column therefore performs arithmetic that cannot be audited, and the
reasons it cannot be audited live in a free-text notes column. Meanwhile suppliers
mostly do not reply at all — the order volume is a rounding error next to a UNICEF
contract — and those who do will not quote without a confirmed delivery point, because
freight dominates the price.

The three standing asks from the procurement lead are: standardise the request for quote
so every reply comes back per carton of 150 sachets; confirm shelf life against the
project timeline; and gather costing data across commodities so that supplying a
treatment can be compared against referring the patient on evidence rather than
instinct.

This design builds the tool that does those three things, and does them in a way that an
API client and an AI agent can drive as well as a person.

## 2. What this is not

Out of scope for v1, deliberately:

- The two-stage EOI → qualification → RFP → per-lot bid scoring machinery in
  `connect_labs/supply/`. That models competitive humanitarian tendering; this models
  chasing nine manufacturers for a quote. Different problem.
- EPCIS event ingestion, flow maps, funder-facing views, facilities as supply nodes.
- Multi-currency FX beyond a stored rate per quote.
- Payment and invoice execution. Local lead organisations (LLOs) transact; we record
  what they paid.
- Attachment binary storage (see Open questions).

## 3. Relationship to prior work

**`connect_labs/supply/`** (the OES satellite site) stays exactly where it is, as a
design reference. Three principles are carried over: derived values are never typed in,
the sachet → carton → course unit ladder, and one module computes a given figure so
every screen agrees. Nothing is imported from it — it has zero cross-app imports by
design and cannot reach real opportunities, workers or visits.

**The 2026-09-09 pivot** aimed at consumption and stock on hand derived from visits.
That remains the plan, as phase 2 (§14). This spec is phase 1, upstream of it, because
that is where the work is currently being done by hand.

**`connect_labs/solicitations/`** is the closest existing pattern and the scoping model
is borrowed from it. It is not reused directly: its respondents are Connect-authenticated
LLO organisations submitting against a published solicitation, whereas suppliers here are
external companies that reply to email, if at all. When suppliers do get Connect accounts
(a stated future direction), the binding is `supplier.connect_organization_id`, not a
second identity model.

## 4. Scoping

A new app, `connect_labs/procurement/`, on the house `data_access.py` + `LabsRecord`
pattern. Three tiers, because the entities have genuinely different lifetimes:

| Tier | Scope | Record types |
|---|---|---|
| Reference | `organization_id` | `commodity`, `supplier` |
| Procurement | `program_id` (experiment = program_id) | `round`, `outreach`, `quote`, `award`, `purchase` |
| Fulfilment (phase 2) | `opportunity_id` | `supply_event` |

Reference data is **organization-scoped, not `public=True`**. A supplier is the same
supplier across every programme, and "a carton is 150 sachets of 92 g" is a fact rather
than a programme's opinion — but `public=True` in LabsRecord terms means world-readable,
and these records hold supplier contact names, direct emails, phone numbers and
commercial pricing. Organisation scope gives the reuse without publishing a price list.

Procurement rounds are program-scoped because one programme's LLOs procure for several
opportunities, and the cost library is only useful if it compares across them.

Phase 2 fulfilment is opportunity-scoped because visits belong to an opportunity.

## 5. Data model

Records are `LabsRecord`s reached through `ProcurementDataAccess`, following
`connect_labs/tasks/data_access.py` for shape and `connect_labs/solicitations/data_access.py`
for the program-scope handling (including its labs-only routing note: a synthetic
opportunity exposes itself as a program whose `program_id == opportunity_id`, and
`LabsRecordAPIClient` keys local routing on `opportunity_id`, so both must be passed).

### 5.1 `commodity` (reference)

The unit ladder and the spec requirements. Generic from day one — this is what lets the
ICCM basket run through the same machinery as RUTF.

- `name`, `slug`, `category` (therapeutic_food | micronutrient | antibiotic | antimalarial
  | diagnostic | anthelmintic | equipment | consumable)
- `base_unit` (sachet | tablet | test | unit), `pack_unit` (carton | box | case),
  `base_per_pack` (e.g. 150), `base_unit_grams` (e.g. 92)
- `course_definition`: `{base_units_per_day, days_per_course, base_units_per_course}` —
  **a stored, editable parameter, never a constant.** For RUTF this is 2 sachets/day and
  a course of roughly 150 sachets, but "roughly one child's worth" is not a number a
  normalizer can divide by, so the programme states it explicitly and every derived
  cost-per-course cites the `course_definition` version it used.
- `spec_requirements`: a list of `{field, operator, value, unit, rationale}` — e.g. for an
  infant scale, `{minimum_graduation_g, "<=", 20, "g", "100 g increments cannot record
  infant weight change"}`. Checked, not prose (§8).
- `shelf_life_months_minimum` — the acceptance threshold the RFQ asks against.

### 5.2 `supplier` (reference)

- `name`, `type` (manufacturer | distributor | trader), `country`, `city`,
  `origin_note` (e.g. free economic zone status, which drives duty treatment)
- `contacts`: list of `{name, role, email, phone, verified, verified_source}`
- `connect_organization_id` — nullable, the future account binding
- `qualifications`: list of `{scheme, reference, expires_on, evidence_ref}` — e.g. UNICEF
  or WHO prequalification. Informational in v1; no gating logic.
- `status` (identified | contacted | responsive | quoting | awarded | declined | unusable)
  and `status_reason`
- `source` (§10)

### 5.3 `round` (procurement)

One request-for-quote cycle. Holds everything a supplier must be told in order to be able
to quote at all.

- `label` (e.g. "Round 2 — February"), `status` (draft | open | closed | awarded)
- `lines`: list of `{commodity_slug, quantity, quantity_unit}` — e.g. 2,000 cartons
- `delivery_point`: `{name, city, country, incoterm_requested}`. **Required before a round
  can leave draft**, because suppliers will not quote without it.
- `required_response_fields` — derived from the commodity, not hand-listed (§7)
- `response_deadline`, `reminder_interval_days`
- `notes_to_supplier` — the standing preamble, including why the volume is small
- `shelf_life_months_minimum` (defaults from commodity, overridable per round)

### 5.4 `outreach` (procurement)

One row per supplier per round — the pipeline view that the spreadsheet's left half is
doing today.

- `round_id`, `supplier_id`, `contact_email_used`
- `sent_on`, `channel` (manual_email | api | mcp | ses), `rfq_text_rendered`
- `responded` (bool), `responded_on`, `response_kind` (quote | declined | needs_info | no_reply)
- `reminder_due_on` (derived), `days_waiting` (derived)
- `source`

### 5.5 `quote` (procurement) — the important one

**Stores exactly what the supplier said, in the unit they said it, plus the basis flags.
Nothing derived is stored here.**

As-quoted:
- `round_id`, `supplier_id`, `commodity_slug`
- `as_quoted_amount`, `as_quoted_currency`, `as_quoted_unit`
  (per_base_unit | per_pack | per_lot_total | per_metric_tonne)
- `quantity_basis`, `quantity_basis_unit` — what quantity the quote actually covers. A
  quote for 667 cartons is not a quote for the 500-carton round, and its lot total must
  never sit in a column beside one.
- `pack_spec_stated` (bool), `base_per_pack_stated`, `base_unit_grams_stated`
- `freight_basis` (included | excluded | not_specified), `freight_amount`
- `duties_basis` (included | excluded | not_specified), `duties_amount`, `duties_note`
- `incoterm`, `delivery_point_quoted`
- `shelf_life_months_stated`, `production_or_expiry_date_stated`
- `moq`, `moq_unit`, `lead_time_days`, `validity_until`
- `stated_spec`: `{field: value}` for spec checking (§8)
- `fx_rate_to_usd`, `fx_rate_as_of`

Integrity:
- `version` (int), `supersedes_quote_id` (nullable), `superseded_by_quote_id` (nullable),
  `correction_reason` — **quotes are immutable** (§11)
- `source` (§10)

Every basis flag defaults to `not_specified`. That default is load-bearing: the commonest
failure mode for both a person in a hurry and an agent reading a pro-forma invoice is to
supply a plausible value, and `not_specified` must be cheaper to record than a guess.

### 5.6 `award` (procurement)

- `round_id`, `quote_id`, `decided_on`, `decided_by`
- `rationale` — required, free text, the due-diligence record
- `comparison_snapshot` — the normalized comparison table **as it stood at the moment of
  decision**, frozen. Mirrors the OES app's rule that reviewers judge a frozen snapshot:
  the reason a supplier was chosen must remain reconstructible after later quotes land.

### 5.7 `purchase` (procurement)

What an LLO actually paid, which is frequently not the quote.

- `round_id`, `supplier_id`, `commodity_slug`, `llo_organization_id` or `llo_name`
- `quantity`, `quantity_unit`, `amount_paid`, `currency`, `paid_on`
- `freight_paid`, `duties_paid`, `other_costs`: list of `{label, amount}`
- `source_document_ref`, `source`

This is the input to the cost library (§13) and the honest basis for cost per child
treated — quoted prices are an intention, paid prices are a fact.

## 6. The derivation invariant

> **Every comparable number is derived, never stored. A derived number whose inputs are
> unconfirmed is returned as `Unconfirmed(reason)` — never as a number.**

This is the counterpart of the OES app's "stock is derived from events, never typed", and
it is the whole reason this beats a spreadsheet.

One module, `services/pricing.py`, computes from a `quote` plus its `commodity`:

- `usd_per_base_unit`
- `usd_per_pack_normalized` (per carton at the commodity's declared `base_per_pack`)
- `usd_per_course`
- `landed_total_for_round_quantity`
- `usd_per_child_treated`

Each returns either a `Money` or an `Unconfirmed(reasons: list[str])`. Reasons are
specific and actionable, because they become the questions sent back to the supplier:
"pack spec not stated on quote", "freight basis not specified", "quote covers 667 cartons,
round is 500", "duties excluded; free-zone origin".

Rules:

1. A quote that did not state its pack spec yields `Unconfirmed` for every per-base-unit
   and per-course figure. The commodity's `base_per_pack` is **not** substituted — that
   substitution is exactly the error being prevented.
2. A quote whose `quantity_basis` differs from the round's quantity yields a landed total
   marked with that basis, and the comparison refuses to place it in the same column as a
   conforming quote.
3. `freight_basis` or `duties_basis` of `not_specified` makes every landed figure
   `Unconfirmed`. `excluded` with no amount does the same.
4. **The comparison refuses to rank a column in which any candidate is `Unconfirmed`**,
   and instead emits the set of outstanding questions per supplier.

That fourth rule is the product. In the spreadsheet the blockers are prose in a notes
column and the comparison happens anyway; here the blockers are the primary output, and
the tool's answer to "who is cheapest" is "you cannot know yet, and here is the email to
send."

`services/pricing.py` is the only place any of these figures is computed. Screens, API
responses, MCP results, exports and the phase-2 reorder calculation all call it.

## 7. One schema, both directions

`services/rfq.py` renders a round into per-supplier request text, and it derives the
questions it asks from **the same field set `pricing.py` needs in order to avoid
returning `Unconfirmed`**. The RFQ asks for price per pack at a stated pack spec, the
incoterm and delivery point, explicit freight and duties treatment, shelf life against the
round's minimum, MOQ, lead time and quote validity — because those are exactly the
as-quoted fields in §5.5.

Consequence: **a compliant reply is automatically comparable.** Standardising the request
and normalising the answers stop being two disciplines to maintain and become one schema
read in two directions. A field added to `quote` surfaces in the next RFQ; a question
dropped from the RFQ shows up as a systematic `Unconfirmed` in the comparison.

Phase 1 renders the text for the procurement lead to send from her own mailbox — a
supplier who ignores an email will also ignore a portal, and the credibility is in the
sender. The tokenized self-serve form and SES send (`LABS_EMAIL_ENABLED`) attach to the
same `quote` record later with no schema change.

## 8. Spec compliance

`services/compliance.py` evaluates a quote's `stated_spec` against the commodity's
`spec_requirements`, returning per-requirement `pass | fail | not_stated` with the
rationale attached. An infant scale quoted at 100 g graduation fails
`minimum_graduation_g <= 20` and says why.

This generalises the equipment-verification concern beyond the RUTF case and is the reason
the ICCM basket needs no second app: vitamin A, amoxicillin, antimalarials, rapid
diagnostic tests, anthelmintics, MUAC tapes and scales differ in their
`spec_requirements`, not in their machinery.

`not_stated` is a distinct outcome from `fail`, and it feeds the RFQ's questions the same
way `Unconfirmed` does.

## 9. Three surfaces, one service layer

Everything is hand-entered to start, but every operation is available over HTTP and over
MCP from day one.

```
services/            the only place business rules live
  pricing.py         derivation + Unconfirmed
  rfq.py             round -> request text; required-field derivation
  compliance.py      stated_spec vs spec_requirements
  matching.py        supplier/commodity resolution, duplicate detection
  ingest.py          email-forward entry point (§10)
  costing.py         cost library aggregation
data_access.py       LabsRecord CRUD + scoping
views.py             Django pages  ─┐
api_views.py         JSON API      ─┼─ three thin adapters, no logic
mcp_tools.py         MCP tools     ─┘
```

**No business rule may live in an adapter.** `connect_labs/solicitations/` has
`mcp_tools.py` and `api_views.py` each calling `data_access` independently, which means a
rule added to one surface can silently miss the other. Here, an operation is declared once
in `services/operations.py` as a name, an input schema and a callable; the API router and
the MCP registry are both generated from that declaration.

A test asserts parity: every operation in the registry is reachable over HTTP and over
MCP, and the MCP input schema matches the API serializer. A surface that drifts fails the
suite rather than being discovered by an agent.

MCP tools, registered via `connect_labs/mcp/tool_registry.register` with `is_write=True`
on mutations (rate-limited and argument-logged to `MCPAuditLog`):

`procurement_commodity_list` / `_upsert`, `procurement_supplier_list` / `_get` / `_upsert`,
`procurement_round_list` / `_get` / `_create` / `_open` / `_close`,
`procurement_rfq_render`, `procurement_outreach_log` / `_list`,
`procurement_quote_record` / `_list` / `_get` / `_correct`,
`procurement_compare_round`, `procurement_award`,
`procurement_purchase_record`, `procurement_cost_library`,
`procurement_ingest_email`.

## 10. Email-forward ingestion

The expected primary write path in practice: forward a supplier's reply to
`ace@dimagi-ai.com` with "add this supplier and quote" or "update this". ACE's existing
`inbox-triage` routes the thread, then drives the MCP tools above. `procurement_ingest_email`
accepts the message (sender, message ID, date, subject, body text, attachment
descriptions plus extracted text) and returns a **plan** of writes, each carrying the
verbatim snippet it came from; the agent then commits them through the same per-entity
tools a person's form submission uses.

Two invariants make an AI-authored write trustworthy.

**The agent transcribes; it does not compute.** As-quoted fields accept only what the
message states. `procurement_quote_record` rejects a value in an as-quoted field that is
not present in the supplied evidence snippet, and every basis flag defaults to
`not_specified`. A model reading a pro-forma invoice will otherwise infer a pack spec
from general knowledge of RUTF — which is precisely the error §6 exists to catch, and it
would arrive wearing the confidence of a typed-in fact. Derived figures are never
accepted on input; they are only ever read back from `pricing.py`.

**Evidence or no write.** Every record carries `source`:
`{channel, message_id, thread_id, sender, received_at, document_ref, snippet, actor}`,
where `channel` is one of `manual | api | mcp | email_forward` and `snippet` is the
verbatim text the value was read from. Fields written without evidence on an
`email_forward` channel are refused. This promotes the spreadsheet's "rationale /
due-diligence note" column from prose to structure, and it is what makes an
agent-entered price defensible to a funder.

Behaviour on the update case:

- **Net-new** supplier or quote: written directly. Forwarding a quote should just work.
- **Ambiguous supplier**: `matching.py` returns ranked candidates (name, email domain,
  prior contacts) and the tool refuses to create until one is chosen or a new one is
  explicitly asserted. Silent duplication of a supplier is worse than a question.
- **A new price from a supplier already quoted**: a new `quote`, never an edit. A re-quote
  is a fact about February, not a correction of May.
- **A contradiction of an already-populated field**: surfaces a conflict for resolution
  rather than overwriting.
- **Idempotency**: keyed on `message_id` plus entity identity. Forwarding the same email
  twice is a no-op, and forwarding a thread that grew appends only what is new.

## 11. Provenance and immutability

`source` (§10) is on every record type, including hand-entered ones — the channel is then
`manual` and the actor is the user. Uniform provenance means "where did this number come
from" has one answer shape regardless of how it arrived.

Quotes are append-only. `procurement_quote_correct` writes a new version with
`supersedes_quote_id` and a required `correction_reason`; the superseded row stays
readable and is excluded from comparisons by default. `award.comparison_snapshot` freezes
the table that justified the decision. Together these make a past comparison
reproducible, which matters as soon as a comparison has been shown to a funder.

## 12. Screens

1. **Round board** — supplier × round: contacted, days waiting, responded, quote status,
   next action, reminder due. Replaces the spreadsheet's outreach half.
2. **Quote entry** — as-quoted fields with explicit basis selectors, `not_specified`
   preselected, live spec check and live `Unconfirmed` reasons as fields are filled, so
   the person entering sees immediately what the reply failed to say.
3. **Comparison** — normalized columns, `Unconfirmed` cells rendered as their reason,
   ranking suppressed where §6 rule 4 applies, an **outstanding questions** panel per
   supplier that renders straight into a follow-up email, and award with a required
   rationale.
4. **Registries** — commodity catalogue with spec requirements and course definition;
   supplier registry with contacts, status and qualifications.
5. **Cost library** — per commodity: quoted range, paid actuals, cost per course, cost per
   child treated, by LLO and over time, with provenance per figure and CSV export.

Django templates following the labs house style, with React only where the comparison
grid warrants it.

## 13. Cost library and the treat-versus-refer question

`services/costing.py` aggregates `purchase` rows across programmes and LLOs into per
commodity cost per course, each figure citing the purchases and the `course_definition`
version behind it. Quoted and paid figures are reported separately and never averaged
together.

This is the artifact behind the standing question of whether supplying a full ICCM
treatment basket beats referring the patient onward: it makes the supply side of that
comparison a measured number with provenance instead of an estimate. The referral side is
out of scope here.

## 14. Phase 2 seam

Phase 2 is the 2026-09-09 model: consumption and stock on hand. This spec does not build
it, but the phase-1 model is shaped so that it lands without rework.

- An `award` and its `purchase` imply an expected delivery. Despatch and receipt become
  opportunity-scoped `supply_event` rows in an append-only log; stock on hand, weeks of
  cover, stock-out date and expiry risk derive from that log, in one module, exactly as
  `pricing.py` is the one place for money.
- Consumption is intended to derive from the RUTF deliver form recording sachets
  dispensed per visit. **That is still a hypothesis** — it was asked in the 2026-09-09
  requirements doc and never answered — so phase 2 opens by verifying it against the real
  deliver app with `get_form_json_paths`, and the plan carries a fallback (periodic
  counts) if the field does not exist.
- The commodity catalogue and its unit ladder are shared, so a carton means the same thing
  on both sides.
- The loop closes when cover and expiry projections compute the next round's quantity.
  Round 3's number should be derived, not guessed — and at that point `round.lines`
  carries a provenance of its own.

Synthetic opportunity **10035** ("SAM Child Recovery (RUTF Follow-up) — Synthetic Demo",
40 visits) is the development and demo ground for phase 2, since it is labs-only and
carries no real patient data.

## 15. Getting the existing data in, and out

- A one-time importer reads the existing tracker export into `supplier`, `round`,
  `outreach` and `quote` records, running `pricing.py` over the result. Day one therefore
  shows the real pipeline and its real gaps rather than an empty app, and the import is
  the first real test of the normalizer.
- CSV export from every list view and from the cost library, so the tool is never a
  one-way door.

## 16. Testing

- `pricing.py` is the unit-test centre of gravity: each `Unconfirmed` reason gets a case,
  and the rule that a commodity's `base_per_pack` is never substituted for an unstated
  pack spec gets an explicit regression test.
- `compliance.py`: `pass` / `fail` / `not_stated` per operator, with the scale-graduation
  case as the worked example.
- `ingest.py`: golden email fixtures → expected write plans, including the refusals —
  inferred pack spec rejected, ambiguous supplier returns candidates, duplicate
  `message_id` is a no-op, contradiction raises a conflict.
- Surface parity test per §9.
- Immutability: a correction creates a version and leaves the original readable; an
  `award.comparison_snapshot` does not change when a later quote lands.

**This repository is public.** No supplier contact details, no real quoted or paid prices,
and no LLO-identifying data may appear in fixtures, tests or docs. Fixtures use invented
suppliers and prices that reproduce the *shapes* that matter — a per-sachet quote against
a per-carton one, a missing pack spec, unspecified freight, excluded duties, a quantity
basis larger than the round. Real data lives only in `LabsRecord`s.

## 17. Open questions

1. **The real Connect-RUTF `program_id` and owning organisation slug.** Needed to ground
   phase 1; the spec is written to be independent of the value.
2. **The ration table.** `course_definition` needs real numbers: sachets per day and days
   per course. "Roughly 150 sachets is one child's worth" is not divisible.
3. **Attachment storage.** v1 stores a document reference plus extracted text and the
   evidence snippet. Whether quote PDFs should also be stored as binaries (S3, alongside
   the audit archive) is deferred — it is additive to the `source` block.
4. **Whether the deliver form records sachets dispensed** — phase 2's load-bearing
   hypothesis, still unverified.

## 18. Phasing

- **1a** — commodity and supplier registries, rounds, outreach, quote capture,
  `pricing.py`, `compliance.py`, comparison and award. Three surfaces from the start.
- **1b** — email-forward ingestion, tracker import, cost library, exports.
- **1c** — SES send with tokenized supplier quote form; `connect_organization_id` binding
  when suppliers get Connect accounts.
- **2** — fulfilment: events, stock on hand, consumption from visits, computed reorder
  quantities.
