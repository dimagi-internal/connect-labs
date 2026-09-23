# Supply: what three real procurements need that the domain does not yet do

**Status:** gap analysis, approved for build.
**Date:** 2026-09-23.
**Source:** the requirements interview, three tabs (CHC procurement, IPTSc, Chlorine),
answered by the programme team.
**Reads against:** `2026-09-11-rutf-procurement-design.md` (the domain as built).

## 0. The three procurements

| Use case | Goods | How it ran |
|---|---|---|
| **CHC** | ORS/zinc co-packs (500k), vitamin A (400k tablets), dewormer (250k tablets). Three orders in 2025, one in 2026. | EHA Clinics offered product options; we chose, ordered and paid EHA. EHA paid the manufacturers, received and inspected the goods, held them in its warehouse, and released them to LLOs, who collected. EHA kept a live stock-movement sheet and we reordered off it. |
| **IPTSc** | 700 three-day treatment packets, one order. | Same chain as CHC, except that EHA could not source enough. The LLO (SCDI) bought the shortfall locally from a vendor it already knew. |
| **Chlorine** | Chlorine solution (3 L jerry cans), dispensers, test kits. | Three different chains. **Chlorine** comes free from Evidence Action (EvAc) and is three months late, so we are asking EHA for NAFDAC-registered chlorine as a stop-gap, and GiveWell must approve using it. **Dispensers** are imported by EvAc. We chase the import documents, pay the customs fees, and EHA clears them, checks them on arrival, stores them and releases them. **Test kits** are specified by Aquaya, sourced through EHA, and Aquaya confirms the product before we buy. |

Two answers came back from every tab. Every status update is asked for by email or
WhatsApp. And the thing that would have helped is alerts, both on stock movements and
on low stock.

## 1. What already fits

The domain was designed from the RUTF procurement, so it already covers most of the chain.
Each of the following is used as built, with no change:

- **Options from one supplier.** A round line can carry several quotes from EHA, one per
  item, and `round_compare` sets them side by side.
- **Buyer of record.** When we pay EHA, the buyer is `programme_org`. When SCDI buys
  locally, it is `partner_org` (§17).
- **Order status.** `contract.status` records whether an order is placed, confirmed,
  part received or received.
- **Paying.** The invoice → payment path, and `contract_match` to check what was
  ordered, delivered and billed against each other.
- **Receiving and inspection.** A receipt line records `quantity_accepted` and
  `quantity_rejected` with a `rejection_reason`. That covers opened-and-inspected goods
  and dispensers that arrive broken.
- **Customs as a shipment status.** A shipment moves through `at_customs` and `cleared`.
- **Stock EHA holds for us.** This is a supply point with `managed_by_org` set to EHA. LLOs
  collecting is a `transfer` to the LLO's point, and handing stock to a worker is a
  distribution.
- **Stock that EHA reports.** A `stock_count` of kind `physical_count` with
  `source=supplier_reported`.
- **Reordering.** `resupply_plan` and `stock_position`, and the `stock_below_minimum`
  and `stock_stockout` checks.
- **Specifications.** Chlorine concentration and test-kit reagent counts are
  `spec_requirements` and `spec_attributes`, and the `item_fails_specification` check
  tests them.
- **Pack sizes.** A 3 L jerry can is `base_unit=L`, `pack_unit=jerry_can`,
  `base_per_pack=3`. A test kit is `pack_unit=kit`, `base_unit=test`, `base_per_pack=50`.

## 2. Gaps

Ranked by how many of the three procurements need each one.

### G1. Kits: one SKU that bundles several products. *(CHC, IPTSc, Chlorine)*

An ORS/zinc co-pack holds 2 ORS sachets and 10 zinc tablets. An IPTSc packet is a
three-day course. A test kit comes with reagent packs, and a dispenser comes with spare
parts. EHA prices per SKU, meaning per co-pack. An `Item` belongs to exactly one
`Commodity`, so the model cannot say that a co-pack contains two products. Two
consequences follow. A co-pack cannot be checked against the specification for its
zinc. And two suppliers' "co-packs" cannot be shown to hold the same contents.

**Build.** Add `Item.components`: a list of `{commodity, quantity, base_unit}`. The kit
item itself still belongs to a primary commodity. Stock is counted in kit SKUs and never
broken apart in the ledger. The components serve three uses:
(a) the specification check covers each component;
(b) `round_compare` puts two kit quotes in the same comparable group only when their
components match, and otherwise marks them `Unconfirmed` with the reason "kit
composition differs";
(c) the course derivation can count a kit as one course.

### G2. Goods that are not bought. *(Chlorine; also CHC and IPTSc items paid from a setup fee)*

EvAc supplies chlorine and dispensers in kind. LLOs bought MUAC strips and service cards
locally, and the cost came out of the setup fee. Today every contract is treated as a
priced purchase. So an in-kind contract raises `contract_cost_unconfirmed` for ever, and
cost data that was never going to exist is reported as missing.

**Build.** Add `Contract.consideration` with three values: `priced` (the default),
`in_kind` or `bundled`. When the value is not `priced`, no unit price or invoice is
expected. Landed cost reports "not purchased (in kind)" or "bundled in setup fee"
instead of `Unconfirmed`, and the cost library leaves the contract out and names why. An
in-kind contract still has a supplier (EvAc as the donor), a quantity, promised dates,
shipments and receipts, so the whole physical chain works unchanged.

### G3. Late deliveries. *(Chlorine, where EvAc is three months late; implicit everywhere)*

No check fires when a promised delivery date passes. `Shipment.expected_on` and
`Contract.promised_lead_time_days` are both stored, and nothing reads them.

**Build.** Add two derived checks:
- `shipment_overdue`: `expected_on` has passed and the shipment has not been received.
- `contract_delivery_overdue`: the contract was signed or placed more than the lead time
  ago and it is not fully received.

Both carry `days_late`, `expected_on` and the supplier. As §22 requires, they report the
lateness and make no recommendation.

### G4. Import clearance: documents and third-party charges. *(Chlorine dispensers)*

The dispensers need three things the model does not have.

- **Import document types.** Add `airway_bill`, `bill_of_lading`, `packing_list`,
  `commercial_invoice`, `import_permit`, `customs_declaration` and
  `product_registration` (NAFDAC) to `DOCUMENT_KINDS`.
- **Required documents, and who owes each one.** Add `Shipment.required_documents`: a
  list of `{kind, owed_by_org}`. A derived check, `shipment_documents_outstanding`,
  names each required document not yet attached and the organisation that owes it. This
  is what "follow up with EvAc when needed" means as data.
- **Charges paid to someone other than the supplier.** Customs fees, a clearing agent
  and transport from the port are paid by us to a courier or to customs, not to EvAc.
  **Build** a `Charge` record on a shipment: `kind` (customs_duty, customs_fee,
  clearing, inland_freight, storage, other), `payee_org`, amount, currency, `paid_on`,
  source, with an optional document attached. Landed cost for the contract adds the
  charges on its shipments, itemised.

### G5. Approvals before an award. *(Chlorine: Aquaya confirms test kits; GiveWell approves the stop-gap chlorine)*

The approver is a third party who is not the person deciding the award. Today
`decided_by` and `rationale` are the only fields that record a decision.

**Build.** Add an `AwardApproval` record:
- the award;
- `approver_org`;
- `role`: technical, funder or regulatory;
- `status`: requested, approved or declined;
- `requested_on` and `decided_on`;
- a note and an optional document.

A derived check, `award_awaiting_approval`, reports each approval still pending and how
long it has been waiting. `contract_create` against an award that has a declined or
pending approval is refused, and the refusal names the approval. That refusal is a
statement of fact, not a recommendation. A contract may not rest on an unapproved
award, just as a duty relief may not be claimed without evidence.

### G6. Alerts. *(the answer to question 7 on all three tabs)*

"Real-time alerts on stock movements and low stock alerts." For chlorine, that means low
stock is the signal we pass to EvAc.

**Build.** Add `AlertSubscription`:
- a programme;
- optionally a supply point or commodity;
- the check kinds and/or the movement kinds to watch;
- a recipient (a labs user, or an email address for an outside party such as EvAc);
- a cadence: immediate or daily digest.

A beat task runs `run_checks`, compares the result with what was last sent, and emails
only the checks that are **new**. That covers a new `stock_below_minimum`, a new
`shipment_overdue` and a new `shipment_documents_outstanding`. Movements go out as they
are recorded, when the subscription asks for them. The email states the derived fact
and links to the record. It gives no advice. Sending goes through the existing SES path
behind `LABS_EMAIL_ENABLED`.

### G7. The supplier records its own updates. *(all three: "all our updates need to be asked via email/WhatsApp")*

EHA reports stock through its own sheet, and every status update comes because someone
asked. §17.2 already decided the rule: a partner records through the same operations as
we do, with `recorded_by_org` set. What is missing is a way in for an organisation that
has no Connect programme membership.

**Build.** Add a **supplier update link**. A programme member issues it for one
organisation and a set of contracts and supply points. The link is a signed, expiring and
revocable token. Behind it is a small form with four actions:
- confirm the order;
- confirm the payment was received;
- record a shipment or receipt;
- record a stock count or release.

Each writes through the ordinary operation with `source=supplier_reported`. The link
cannot read anything outside the scope it was issued for.

### G8. Smaller gaps

- **Supplier confirms payment received** (CHC, IPTSc and test kits all list it). Add
  `Payment.confirmed_by_payee_on`. A payment older than N days without this date
  appears in `checks_list` as `payment_unconfirmed`.
- **Shortfall covered by another order** (IPTSc). Add `Contract.covers_shortfall_of`,
  pointing at another contract. `contract_match` on the short contract then reports the
  shortfall as covered by the named contract, instead of leaving it open.
- **Durable equipment** (dispensers). Add `Item.stock_class`: `consumable` (the
  default) or `durable`. Durable items are left out of AMC, months of stock and resupply,
  where a figure for them would be meaningless. They still move through the ledger, so
  "which site has which dispenser" is still a balance.

## 3. What this deliberately does not build

- **Ranked worklists or drafted chase emails.** §22 still holds. Alerts deliver derived
  facts, and an agent does the wording.
- **Syncing EHA's Google Sheet.** G7 replaces that sheet. Reading it would carry its
  errors in with it.
- **Paying anyone.** We record payments. We do not make them.

## 4. The walkthroughs

One DDD narrative per use case, so each procurement can be watched from start to
finish in the live `/supply/` screens. Each narrative has its own synthetic scope.

| Narrative | Arc | Gaps it exercises |
|---|---|---|
| `supply-chc-copacks` | EHA offers co-pack options, we compare them by composition, order and pay, and EHA confirms payment. EHA receives and inspects batches, and records them through its update link. An LLO collects. A low-stock alert fires and we reorder. | G1, G6, G7, G8 (payment) |
| `supply-iptsc-shortfall` | We order 700 packets from EHA and only 450 arrive. SCDI buys 250 locally, paid from its setup fee. The short order reads as covered. | G1, G2 (bundled), G8 (shortfall) |
| `supply-chlorine-stopgap` | EvAc's in-kind chlorine is 90 days late and the overdue check shows it. We ask EHA for NAFDAC-registered chlorine. The award waits on GiveWell, who approves it, and only then can the order be placed. A low-stock alert goes to EvAc. | G2, G3, G4 (registration document), G5, G6 |
| `supply-dispenser-import` | EvAc's dispensers are held at customs. The document checklist shows who owes what. We pay the customs and clearing fees and they appear in landed cost. On arrival two units are rejected as broken, and the rest go out to sites as durable equipment. | G3, G4, G8 (durable) |
| `supply-test-kits` | Aquaya specifies kits of 50 tests. EHA quotes two kits. Aquaya confirms one. We award it, order it, pay, and receive it into stock counted in tests. | G1, G5 |
