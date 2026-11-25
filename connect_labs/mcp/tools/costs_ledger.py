"""The costs ledger, for an agent: make the sheet, import it, read the reconstructed invoices.

What Dimagi paid for Connect opportunities outside Connect lives on one sheet next to the LLO
Directory (``pulse.ledger``). These tools are its API, the way ``marketplace_import`` is the
directory's:

* ``costs_ledger_sheet_create`` makes the sheet in the directory's folder and shares it with the
  people and agents who will keep its Ledger tab.
* ``costs_ledger_import`` copies it into labs and rebuilds the reconstructed invoices: per
  opportunity, the entries Connect would hold had every payment gone through it.
* ``costs_reconstructed_invoices`` reads them back, each with the ledger lines it is made of.

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
        "Make the costs ledger sheet: one Google Sheet in the LLO Directory's Drive folder, owned by labs' "
        "service account, with the tabs people and agents keep (Ledger: one row per accounting line with its "
        "class and the Connect organisation and programme it paid for; Programmes: which Connect program ids "
        "each programme name covers; Exclusions: reconstructed invoices not to count) and their header rows. "
        "`share_with` gets edit access. There is one ledger: a second call is refused with its URL. If Google "
        "refuses the service account a new file there (CANNOT_CREATE), a person makes the sheet in that folder, "
        "gives the service account edit access, and passes its id as `spreadsheet_id`: labs adopts it, adding "
        "whichever of its tabs are missing or empty and never clearing one with rows."
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
        "Copy the costs ledger sheet into labs and rebuild the reconstructed invoices: for every closed "
        "programme-organisation pair, what the ledger paid the organisation beyond what Connect recorded, "
        "split over its opportunities by people reached; and every programme's direct costs (commodities, "
        "freight, storage), split over its closed opportunities the same way. They count in every labs cost "
        "figure (cost per service, cost-effectiveness) unless the Exclusions tab names them. Writes labs' "
        "own tabs back to the sheet (Labs Pairs, Labs Programmes, Labs Invoices, Labs Unattributed); never a "
        "tab a person keeps. Returns the counts, the total invoiced, what could not be placed and every row "
        "it could not read (`problems`). `dry_run` reads and reports without writing. Only Dimagi members "
        "and platform support may run it."
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


def _source(source) -> dict[str, Any]:
    line = source.line
    return {
        "line_key": line.line_key,
        "usd": float(source.usd),
        "date": line.date.isoformat() if line.date else None,
        "vendor": line.vendor,
        "document": line.document,
        "project": line.project,
        "class": line.line_class,
        "memo": line.memo[:300],
    }


def _invoice(inv, *, with_sources: bool) -> dict[str, Any]:
    out = {
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
    if with_sources:
        out["sources"] = [_source(s) for s in inv.sources.all()]
    return out


@register(
    name="costs_reconstructed_invoices",
    description=(
        "The reconstructed invoices: per opportunity, the entry Connect would hold had every payment for it "
        "gone through Connect. `llo_payment` = paid to the opportunity's organisation beyond what Connect "
        "recorded (a Connect custom invoice, service_delivery false); `direct_cost` = bought directly for "
        "the programme (Connect has no object for it yet). Each carries Connect's invoice fields (number, "
        "amount, dates, title, description), how it was built (`basis`), whether it counts, and with "
        "`sources` the ledger lines it is made of. Select by `opportunity_ids`, `program_id` or `org_slug`, "
        "or none for all."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_ids": {"type": "array", "items": {"type": "integer"}},
            "program_id": {"type": "integer"},
            "org_slug": {"type": "string"},
            "sources": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
)
def costs_reconstructed_invoices(
    user, opportunity_ids=None, program_id=None, org_slug=None, sources: bool = False
) -> dict[str, Any]:
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
    if sources:
        qs = qs.prefetch_related("sources__line")
    rows = list(qs[: MAX_INVOICES + 1])
    if len(rows) > MAX_INVOICES:
        raise MCPToolError("BAD_REQUEST", f"More than {MAX_INVOICES} invoices match; narrow the selection.")
    sid = ledger.sheet_id()
    return {
        "sheet": ledger.sheet_url(sid) if sid else None,
        "count": len(rows),
        "counted_usd": round(sum(float(r.amount_usd) for r in rows if r.counted), 2),
        "invoices": [_invoice(r, with_sources=sources) for r in rows],
    }
