"""The invoices sheet, for an agent: make or adopt it, import it, read the reconstructed invoices.

What Dimagi paid for Connect opportunities outside Connect is kept as invoices on one sheet
(``pulse.ledger``), built outside labs from a filtered Intacct pull; labs keeps no accounting line.
These tools are its API, the way ``marketplace_import`` is the directory's (their names predate the
sheet holding invoices only):

* ``costs_ledger_sheet_create`` makes the sheet, or adopts one a person made, and shares it.
* ``costs_ledger_import`` replaces labs' reconstructed invoices with its Invoices tab.
* ``costs_reconstructed_invoices`` reads them back.

They say what was paid to whom, so like ``cea_opportunities`` they are for full tokens only (in
none of ``token_scopes``' restricted sets), and the two that write are for Dimagi members and
platform support.
"""

from __future__ import annotations

from typing import Any

from ..tool_registry import MCPToolError, register
from .marketplace import _may_import

MAX_INVOICES = 500


def _refuse_unless_dimagi(user) -> None:
    if not _may_import(user):
        raise MCPToolError(
            "PERMISSION_DENIED", "Only Dimagi members and platform support may change the costs ledger."
        )


@register(
    name="costs_ledger_sheet_create",
    description=(
        'Make the invoices sheet ("Connect invoices to add") in the LLO Directory\'s Drive folder with its two '
        "tabs and header rows: Invoices (one row per reconstructed invoice: opportunity, organisation, kind, "
        "amount, dates, a description naming only the organisation or supplier and bill numbers, whether it "
        "counts), which labs imports, and Per opportunity, the summary people keep. `share_with` gets edit "
        "access. Making a second is refused with its URL. To use a sheet someone made (or when Google refuses "
        "the service account a new file, CANNOT_CREATE), give the service account edit access and pass its id "
        "as `spreadsheet_id`: labs adopts it in place of the one it used before, adding whichever tabs are "
        "missing or empty and never clearing one with rows. Never put accounting ledger lines in it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "share_with": {"type": "array", "items": {"type": "string", "format": "email"}, "maxItems": 20},
            "spreadsheet_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{20,}$"},
        },
        "required": ["share_with"],
        "additionalProperties": False,
    },
    is_write=True,
)
def costs_ledger_sheet_create(user, share_with: list[str], spreadsheet_id: str | None = None) -> dict[str, Any]:
    from connect_labs.pulse import ledger

    _refuse_unless_dimagi(user)
    try:
        return ledger.create_sheet([e.strip() for e in share_with if e.strip()], spreadsheet_id=spreadsheet_id)
    except ledger.ImportFailed as e:
        raise MCPToolError(e.code, str(e)) from e


@register(
    name="costs_ledger_import",
    description=(
        "Replace labs' reconstructed invoices with the invoices sheet's Invoices tab. They count in every "
        "labs cost figure (cost per service, cost-effectiveness) unless their row says count = no. Returns "
        "the counts, the totals by kind and every row it could not read or place (`problems`). `dry_run` "
        "reads and reports without writing. Labs writes nothing back to the sheet. Only Dimagi members and "
        "platform support may run it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "dry_run": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    is_write=True,
    long_running=True,
)
def costs_ledger_import(user, dry_run: bool = False) -> dict[str, Any]:
    from connect_labs.effectiveness.implementation.reach import people_reached
    from connect_labs.pulse import ledger

    _refuse_unless_dimagi(user)
    try:
        return ledger.run_import(people_reached=people_reached, dry_run=dry_run)
    except ledger.ImportFailed as e:
        raise MCPToolError(e.code, str(e)) from e


def _invoice(inv) -> dict[str, Any]:
    return {
        "invoice_number": inv.invoice_number,
        "opportunity_id": inv.opportunity_id,
        "kind": inv.kind,
        "amount_usd": float(inv.amount_usd),
        "date": inv.date.isoformat() if inv.date else None,
        "start_date": inv.start_date.isoformat() if inv.start_date else None,
        "end_date": inv.end_date.isoformat() if inv.end_date else None,
        "title": inv.title,
        "description": inv.description,
        "service_delivery": inv.service_delivery,
        "counted": inv.counted,
        "excluded_reason": inv.excluded_reason,
        "pushed_invoice_number": inv.pushed_invoice_number or None,
        "basis": inv.basis,
    }


@register(
    name="costs_reconstructed_invoices",
    description=(
        "The reconstructed invoices: per opportunity, the entry Connect would hold had every payment for it "
        "gone through Connect. `llo_payment` = paid to the opportunity's organisation beyond what Connect "
        "recorded (a Connect custom invoice, service_delivery false); `direct_cost` = bought directly for "
        "the programme (Connect has no object for it yet). Each carries Connect's invoice fields (number, "
        "amount, dates, title, description), whether it counts and what was pushed to Connect. "
        "Select by `opportunity_ids`, `program_id` or `org_slug`, "
        "or none for all."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_ids": {"type": "array", "items": {"type": "integer"}},
            "program_id": {"type": "integer"},
            "org_slug": {"type": "string"},
        },
        "additionalProperties": False,
    },
)
def costs_reconstructed_invoices(user, opportunity_ids=None, program_id=None, org_slug=None) -> dict[str, Any]:
    from connect_labs.pulse import ledger
    from connect_labs.pulse.models import PulseOpportunity, PulseReconstructedInvoice

    qs = PulseReconstructedInvoice.objects.all()
    if opportunity_ids:
        qs = qs.filter(opportunity_id__in=opportunity_ids)
    if program_id is not None or org_slug:
        opps = PulseOpportunity.objects.filter(is_test=False)
        if program_id is not None:
            opps = opps.filter(program_id=program_id)
        if org_slug:
            opps = opps.filter(org_slug=org_slug)
        qs = qs.filter(opportunity_id__in=list(opps.values_list("opportunity_id", flat=True)))
    rows = list(qs[: MAX_INVOICES + 1])
    if len(rows) > MAX_INVOICES:
        raise MCPToolError("BAD_REQUEST", f"More than {MAX_INVOICES} invoices match; narrow the selection.")
    sid = ledger.sheet_id()
    return {
        "sheet": ledger.sheet_url(sid) if sid else None,
        "count": len(rows),
        "counted_usd": round(sum(float(r.amount_usd) for r in rows if r.counted), 2),
        "invoices": [_invoice(r) for r in rows],
    }
