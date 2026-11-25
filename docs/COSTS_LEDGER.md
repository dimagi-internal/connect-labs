# The costs ledger and reconstructed invoices

Connect records what an opportunity paid per service and the custom invoices raised in it. Dimagi also pays
organisations outside Connect (setup, milestones, close-out) and buys supplies for a programme directly
(commodities, freight, storage). Those payments are in the accounting ledger (Intacct), not in Connect, so every
labs cost figure (cost per service, cost-effectiveness) was low by them. This is how labs brings them in, and the
shape they would be written back to Connect in.

## The sheet

One Google Sheet, **"Connect costs ledger (outside Connect)"**, in the LLO Directory's Drive folder and read by
the same labs service account (`connect_labs/pulse/ledger.py`). Made once by `costs_ledger_sheet_create` (or
`manage.py pulse_costs_ledger --create --share <email>`); its id is kept in `PulseScalar` (`costs_ledger_sheet`).

Kept by people and agents (labs never writes these):

| Tab | One row per | Columns |
|---|---|---|
| Ledger | accounting line (a bill or payment line) | `line_key` (unique, stable), `source`, `date`, `entity`, `vendor`, `document`, `memo`, `department`, `project`, `account`, `usd`, `class`, `org_slug`, `programme`, `match_rule` |
| Programmes | programme name used on the Ledger tab | `programme`, `connect_program_ids` (comma list), `notes` |
| Exclusions | reconstructed invoice not to count | `opportunity_id`, `kind`, `reason` |

`class` is one of `llo_service`, `llo_fixed`, `direct_cost`, `excluded` (staff, consultants, research partners,
intercompany), `unclear`. Whoever pulls the ledger from Intacct classifies each line and names the Connect
organisation (`org_slug`) and programme it paid for; labs does not guess. The Intacct skills on the canopy project
`dimagi/intacct` do this today from the Intacct web UI, and will from its API once sender credentials exist.

Written by labs on every import: **Labs Opportunities** (per opportunity: Connect recorded, invoices to add, implied total, change, cost per person reached before and after), **Labs Pairs**, **Labs Programmes**, **Labs Invoices**, **Labs Unattributed**.

## The import

`costs_ledger_import` (MCP, Dimagi members and platform support; `dry_run` to preview) or
`manage.py pulse_costs_ledger --import` reads the three tabs, mirrors the Ledger into `PulseLedgerLine`, rebuilds
`PulseReconstructedInvoice` (`connect_labs/pulse/reconstruct.py`, pure and tested on values), and writes labs' tabs
back. Rows it cannot read come back as `problems` and are left out. The rules:

1. A **pair** is a programme and an organisation; its opportunities are the organisation's opportunities in the
   Connect programmes the Programmes tab lists.
2. **What Connect missed** for a pair is what the ledger paid the organisation less what Connect and labs recorded
   (`OppCosts.recorded_usd`: per-service pay, custom invoices, `PulseCostEntry`). Only when every opportunity of
   the pair is closed; a negative gap is reported, never invoiced.
3. It is **split** over the pair's opportunities by people reached, else recorded cost, else equally.
4. **Direct costs** booked to a programme are split over all its opportunities the same way; only closed ones are
   invoiced, an active one's share is reported as held.
5. A line that **cannot be placed** is listed under Labs Unattributed, never spread.

People reached come from `effectiveness.implementation.reach.people_reached`, passed in by the callers
(Pulse never imports effectiveness).

## The data model

| Model | What it is |
|---|---|
| `PulseLedgerLine` | One accounting line, as the sheet lists it. Replaced on every import. |
| `PulseReconstructedInvoice` | Per opportunity and kind, the invoice Connect would hold had every payment gone through it. Connect's `PaymentInvoice` fields (`invoice_number` `LABS-…`, `amount_usd`, `date`, `start_date`, `end_date`, `title`, `description`, `service_delivery` false), plus `basis` (how it was built), `excluded_reason` and `pushed_invoice_number`. |
| `PulseReconstructedInvoiceSource` | How much of one ledger line makes up one invoice. |

A reconstructed invoice counts in `costs.opportunity_costs` (`reconstructed_usd`, inside `fixed_usd`) unless the
Exclusions tab names it, so cost per service, the cost-effectiveness ranking and `cea_opportunities` all include
it. `recorded_usd` is the figure without it. Read them with `costs_reconstructed_invoices` or on
`/labs/pulse/costs/reconstructed/` (CSV in Connect's invoice shape).

## Writing them back to Connect

What each kind would be in Connect:

| Kind | Connect object | Notes |
|---|---|---|
| `llo_payment` | `PaymentInvoice` with `service_delivery=False` (a custom invoice), `status=paid`, and the `Payment` to the opportunity's organisation that paying it creates (`invoice_pay`), dated from the ledger | Connect has no API that creates invoices or payments; writing back needs one (the export API is read-only for invoices). |
| `direct_cost` | **None yet.** A `PaymentInvoice` is paid to the opportunity's organisation; a commodity was paid to a supplier. | Needs a Connect object for a programme or opportunity cost paid to a third party, or a decision to record it as a custom invoice with no payment. |

`pushed_invoice_number` records the Connect invoice once written, and survives re-imports.
