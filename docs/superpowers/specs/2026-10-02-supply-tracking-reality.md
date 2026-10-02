# Supply: the record tracks reality

**Status:** shipped with this change, 2026-10-02.
**Why:** Sophie's email rehearsal (2026-10-01, `docs/sophie-email-rehearsal`
branch) recorded twelve messy supplier emails through the `supply_chain_*`
operations. The lead story is not the price comparison. In round 1 Sophie got
one clear quote out of everyone she asked, so the record must hold who was
asked, who replied and when, what each said, what is still missing, what we
owe them, what arrived and was paid, and when. Side-by-side price display is
deliberately not built here.

Each ruling says what, why, and what it costs. Finding numbers are the
rehearsal's.

## 1. An undeclared field is refused, not dropped (finding 7)

**What.** Every write schema's `data` object is closed
(`additionalProperties: false`). A key it does not declare is refused before
anything is written, and the message names each unknown key, says "Nothing was
recorded", and lists the fields the operation takes. The real fields the
rehearsal needed are declared: on a quote `received_on`, `validity_until`,
`incoterm`, `quantity_basis_unit`, `moq`/`moq_unit`, `payment_terms` (as stated),
`supplier_reference`, `notes`, `stated_spec`; on outreach `responded_on`,
`notes`; on an invoice `unit_price`, `freight_amount`; on tenders, contracts,
items and products the real columns that had never been declared. Objects
stored whole as JSON (a tender line, a delivery place, a kit component, a
specification requirement, a required document) stay open, because an extra
key there is kept, not lost.

**Why refusal, not a warning.** A warning rides on an OK: the write has
happened without the fact, the AI has to notice a side field, and the
operation's replay key now holds the wrong payload. A refusal costs one retry,
and the message carries the correct name, so the retry is right. Refusing also
caught real junk in our own callers (an organisation `kind`, a document
`reference`, a contract `quote_id`, an invoice `number`), all of which had been
silently discarded.

**Cost.** Seeders that replay Drive documents can carry keys an operation does
not take. They now pass payloads through `operations.declared_only`, which
strips them and prints what it dropped (oes-demo and CHC seeders). A re-seed
of an already seeded demo after this deploy will not replay calls whose payload
changed; reset a demo by its guarded purge, as now.

## 2. A forwarded copy is not a second live quote (finding 3)

**What.** `quote_record` refuses a quote when the same supplier already has a
live quote for the same product, trade item and delivery option on the tender
**and** it states the same offer by any one mark: the supplier's own reference
(`supplier_reference`, case-insensitive), the price as quoted (amount, unit,
currency), or the day it was received. The refusal (`SuspectedDuplicate`)
names the quote on file with its price, date, reference and the source excerpt
it was recorded from, and says what to do: nothing for a copy, `quote_correct`
for a revision, or `distinct_from_quote_ids=[…]` for a genuine second offer.

**Why these marks.** The replay key (Message-ID plus exact payload) cannot
catch an inline forward (no original Message-ID) or a second reading that
differs in one field. Refusing every second quote from a supplier was tried
and broke ordinary data (one distributor quoting two prices); a guard that
fires on normal offers gets overridden by reflex. Any one shared mark catches
both rehearsal cases.

**Cost.** A copy whose price was misread and which carries no reference or
received date still gets through. The refusal's evidence list is one extra
query per suspected rival.

## 3. Real dates are domain fields; history keeps recorded time (finding 6)

**What.** A quote's `received_on` and an outreach's `responded_on` are the
email's own dates, declared in the schemas, asked on the quote and reply
forms, and shown on the tender page (the outreach table's "Replied" column now
shows the date). `Revision.recorded_at` stays recorded time only.

**Why.** Jonathan rejected a second "known on" clock. These are facts about
the world, like `sent_on` and `expected_on`, not about our history.

**Cost.** The overview's "last change" still says when we learned of a change
(by design), so a batch forwarded on a Friday still reads as Friday there.

## 4. An invoice above the contract is a check (finding 4)

**What.** New check `invoice_above_contract` (conflict, audience supplier):
the invoice's `unit_price` above the contract's (same currency and unit), its
`freight_amount` above the contract's, or the sum of live invoices above the
agreed goods plus freight. Each comparison is made only where both sides state
the figure; a per-carton price is never compared with a per-sachet one. The
order page shows it in a banner.

**Cost.** A contract whose freight is unconfirmed gets no total comparison
(only the unit price and freight lines).

## 5. Whose move it is (finding 8)

**What.** `fulfilment/services/holds.py` reads what an order is held on that
we owe: a document a not-yet-received shipment requires, not on file, owed by
the buyer's organisation when we (or an agency for us) are the buyer of
record, and our open promises on the order. When there is one, the overview's
"waiting on" reads `us: …`, the late-ETA flag says "held on us", and
`contract_delivery_overdue` / `shipment_overdue` carry `waiting_on: "us"`,
`held_on_us`, and audience `internal` instead of `supplier`. The order page
leads with "Waiting on us" and says the next move is ours. Exposed as the read
operation `contract_holds`.

**Why.** It is a fact in the records, read once, so the screens cannot
disagree. A document owed by a partner buyer stays the partner's.

**Cost.** No record says when a document hold began, so it shows no "since";
we do not invent one from the recorded time.

## 6. Advance payments (finding 9)

**What.** `Payment.contract` is new and required; `Payment.invoice` is now
optional. `payment_record` takes `contract_id` (an advance on a pro-forma) or
`invoice_id`. `invoice_record`/`invoice_update` take
`acknowledges_payment_ids`: each payment the invoice says it received ("less
advance received") is matched to it and counts as confirmed by the payee on
the invoice's issue date, so `payment_unconfirmed` stops firing. The match and
the overview count every payment on the order. Screen: "Record an advance
payment" on the order page, and a tick-list of unmatched payments on the
invoice form.

**Why.** The supplier's own invoice acknowledging the money is the payee's
confirmation, in writing, dated. An invoice may not acknowledge a payment on
another order or one matched to another invoice.

**Cost.** One migration pair (backfill from the invoice, then not-null).

## 7. What we owe them (finding 12)

**What.** New model `Commitment` (kind `question` or `promise`, owed to an
organisation, on a tender or an order, raised on a date, resolved with what was
said). Operations `commitment_record`, `commitment_resolve`, `commitment_list`,
with screens on the tender and order pages. Open ones put `us: answers to
Northgate Commodities (3 questions since 11 Jul)` first in the tender's
"waiting on", feed an order's holds (ruling 5), raise `commitment_open`
(internal), and `tender_drafts_render` drafts a `reply` per counterparty
listing their open questions, at any stage, since an answer owed does not
lapse with an award.

**Cost.** The AI must record each question; a free-text note on outreach no
longer counts as one.

## 8. Partial updates keep who told us (finding 11)

**What.** `shipment_update` and `invoice_update` need neither `contract_id`
nor `source`. Both are still accepted when they repeat the record's own
values; a different value is refused by name, never applied and never
dropped. `recorded_by_org_id` is not taken on an update, so provenance stamping
no longer touches one. The shipment status form no longer asks "How do you
know?". New source `forwarder_reported` for a freight forwarder or clearing
agent.

**Why.** The record's `source` is who first told us; who told us of a change
is the call's own provenance, kept in its history.

## 9. The timeline names the actual sender (finding 10)

**What.** `source` gains an optional `sender` ("Crescent Freight &
Clearing"), stored as `OperationCall.source_sender`; the timeline uses it
first. Without it, a created record's sender is read from the record (a
quote's supplier; a shipment, invoice or receipt the supplier reported); an
update with no stated sender names nobody rather than the record's first
teller; a carrier is never the sender. The order page's invoice "Told by" now
says whose word it is ("Dimagi, for Harmattan (they told us)").

## Not done here

Side-by-side prices (deprioritised), duty and exchange rate set once by us
(findings 2, 18), re-quote versus correction (13), approximate dates and
indicative freight (14), expired quotes (15), attachments without bytes (16),
WhatsApp conventions (17), accruing demurrage (19), and catalogue noise (20).
