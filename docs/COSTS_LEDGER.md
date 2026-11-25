# Costs paid outside Connect, as reconstructed invoices

Connect records what an opportunity paid per service and the custom invoices raised in it. Dimagi also pays
organisations outside Connect (setup, milestones, close-out) and buys supplies for a programme directly
(commodities, freight, storage). Those payments are in Dimagi's accounts (Intacct), not in Connect, so every labs
cost figure (cost per service, cost-effectiveness) was low by them. This is how labs brings them in, as the invoices
Connect would hold had every payment gone through it.

## Labs keeps no accounting line

An accounting ledger also holds staff pay, consultants, contractors and transfers between Dimagi's entities. None of
that may reach a shared place. So the invoices are built **outside labs**, on the machine that holds the Intacct login
(canopy projects `dimagi/intacct` and `connect/connect-invoicing`):

1. The Intacct skill's `vendor-payments` command pulls only vendor bills and payments (never journal entries) in the
   Connect departments, to vendors Intacct records as companies; a payment to a person is never fetched or written.
2. Each vendor is matched to a Connect organisation or supplier by its Intacct vendor id (a reviewed map, not names).
3. The build splits what an organisation was paid beyond what Connect recorded over the pair's closed opportunities by
   people reached, and a programme's direct costs over its closed opportunities, and writes one row per invoice.

Labs only ever sees the invoices.

## The sheet

One Google Sheet, **"Connect invoices to add"**, read by labs' service account (`connect_labs/pulse/ledger.py`). Labs
makes it with `costs_ledger_sheet_create`, or adopts one a person made (give the service account edit access, pass
`spreadsheet_id`; adopting replaces the sheet labs used before). Its id is kept in `PulseScalar` (`costs_ledger_sheet`).

| Tab | One row per | Read by labs |
|---|---|---|
| Invoices | reconstructed invoice: `opportunity_id`, `opportunity`, `org`, `kind` (`llo_payment` / `direct_cost`), `amount_usd`, `date`, `start_date`, `end_date`, `title`, `description` (names only the organisation or supplier and the bill numbers), `count` (yes / no), `exclude_reason` | yes |
| Per opportunity | opportunity: what Connect recorded, the invoices to add, the total after, cost per person before and after | no, it is for people |

Labs writes nothing back to the sheet.

## The import

`costs_ledger_import` (MCP, Dimagi members and platform support; `dry_run` to preview) or
`manage.py pulse_costs_ledger --import` replaces `PulseReconstructedInvoice` with the Invoices tab. A row it cannot
read, a second invoice of the same kind for an opportunity, or an opportunity labs does not know comes back in
`problems` and is left out. A row with `count` = no is kept but not counted, and needs an `exclude_reason`. Invoice
numbers (`LABS-…`) are stable per opportunity and kind, so a re-import keeps them and what was pushed to Connect.
People reached (`effectiveness.implementation.reach.people_reached`, passed in by the callers; Pulse never imports
effectiveness) are kept on each invoice for the page's cost per person.

## The data model

`PulseReconstructedInvoice`: per opportunity and kind, Connect's `PaymentInvoice` fields (`invoice_number` `LABS-…`,
`amount_usd`, `date`, `start_date`, `end_date`, `title`, `description`, `service_delivery` false), plus
`excluded_reason` and `pushed_invoice_number`. It counts in `costs.opportunity_costs` (`reconstructed_usd`, inside
`fixed_usd`) unless excluded, so cost per service, the cost-effectiveness ranking and `cea_opportunities` all include
it; `recorded_usd` is the figure without it. Read them with `costs_reconstructed_invoices` or on
`/labs/pulse/costs/reconstructed/` (CSV in Connect's invoice shape).

## Writing them back to Connect

| Kind | Connect object | Notes |
|---|---|---|
| `llo_payment` | `PaymentInvoice` with `service_delivery=False` (a custom invoice), `status=paid`, and the `Payment` to the opportunity's organisation that paying it creates (`invoice_pay`) | Connect has no API that creates invoices or payments; writing back needs one (the export API is read-only for invoices). |
| `direct_cost` | **None yet.** A `PaymentInvoice` is paid to the opportunity's organisation; a commodity was paid to a supplier. | Needs a Connect object for a programme or opportunity cost paid to a third party, or a decision to record it as a custom invoice with no payment. |

`pushed_invoice_number` records the Connect invoice once written, and survives re-imports.
