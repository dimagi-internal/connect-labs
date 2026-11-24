"""What each delivered opportunity cost, for an agent: the money behind ``cea_opportunities``' ``total_usd``.

``costs_opportunities`` reads the same function every cost figure in labs reads (``pulse.costs.opportunity_costs``)
and returns it un-summed: per-service pay (worker and organisation), Connect's invoices one by one with the basis
each USD figure was resolved on, the costs entered in labs (``PulseCostEntry``), and the payments Connect made to the
organisation (``PulseOrgPayment``). It is what reconciling Connect against an accounting system needs: each invoice's
own number (the key a ledger's bill carries) and what it was paid.

It says what was paid to whom per opportunity, so like ``cea_opportunities`` it is for full tokens only, never the
no-visit-data ones (it is in none of ``token_scopes``' restricted sets).
"""

from __future__ import annotations

from ..tool_registry import MCPToolError, register

MAX_OPPORTUNITIES = 300


def _money(value):
    return float(value) if value is not None else None


def _invoice(inv) -> dict:
    return {
        "invoice_number": inv.invoice_number,
        "date": inv.date.isoformat() if inv.date else None,
        "kind": "service_delivery" if inv.service_delivery else "fixed",
        "amount": _money(inv.amount),
        "amount_usd_connect": _money(inv.amount_usd_connect),
        "usd": _money(inv.usd),
        "usd_basis": inv.basis,
    }


def _payment(p) -> dict:
    return {
        "organization": p.organization,
        "invoice_id": p.invoice_id,
        "amount": _money(p.amount),
        "amount_usd": _money(p.amount_usd),
        "date_paid": p.date_paid.isoformat() if p.date_paid else None,
        "confirmed": p.confirmed,
        "payment_method": p.payment_method,
        "payment_operator": p.payment_operator,
    }


def _entry(e) -> dict:
    return {
        "kind": e.kind,
        "usd": _money(e.usd),
        "date": e.date.isoformat() if e.date else None,
        "reason": e.reason,
        "source": e.source,
        "engagement": e.group.slug if e.group_id else None,
    }


@register(
    name="costs_opportunities",
    description=(
        "What each real Connect opportunity cost, broken down: per-service pay accrued on approved work "
        "(`worker_usd`, `org_usd`), Connect's invoices one by one (`invoices`: number, date, kind = service_delivery "
        "or fixed, local amount, Connect's USD and the USD labs counts with its `usd_basis`), costs entered in labs "
        "for money that never passed through Connect (`cost_entries`), and Connect's payments to the organisation "
        "(`org_payments`: amount, date paid, confirmed, method, and Connect's invoice id). `total_usd` = per-service "
        "pay + fixed invoices + entered costs, the opportunity's own cost in `cea_opportunities` (before a cost-only "
        "opportunity's spend is pooled into the delivery it paid for). Service-delivery invoices bill the per-service "
        "pay already counted, so they are listed for reconciling, never added. Select with `opportunity_ids`, "
        "`program_id` or `org_slug` (at least one); `include` drops the lists you do not need. Use it to match "
        "Connect's invoices to an accounting system's bills: a bill's number is often Connect's `invoice_number`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": MAX_OPPORTUNITIES},
            "program_id": {"type": "integer"},
            "org_slug": {"type": "string"},
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": ["invoices", "cost_entries", "org_payments"]},
                "default": ["invoices", "cost_entries", "org_payments"],
            },
        },
        "additionalProperties": False,
    },
)
def costs_opportunities(user, *, opportunity_ids=None, program_id=None, org_slug=None, include=None):
    from connect_labs.pulse import costs
    from connect_labs.pulse.models import PulseCostEntry, PulseOpportunity, PulseOrgPayment

    if not opportunity_ids and program_id is None and not org_slug:
        raise MCPToolError("BAD_REQUEST", "Name `opportunity_ids`, `program_id` or `org_slug`.")
    include = set(include if include is not None else ("invoices", "cost_entries", "org_payments"))
    opps = PulseOpportunity.objects.filter(is_test=False)
    if opportunity_ids:
        opps = opps.filter(opportunity_id__in=opportunity_ids)
    if program_id is not None:
        opps = opps.filter(program_id=program_id)
    if org_slug:
        opps = opps.filter(org_slug=org_slug)
    opps = {o.opportunity_id: o for o in opps.order_by("opportunity_id")[: MAX_OPPORTUNITIES + 1]}
    if not opps:
        raise MCPToolError("NOT_FOUND", "No real (non-test) opportunity matches that selection.")
    if len(opps) > MAX_OPPORTUNITIES:
        raise MCPToolError("BAD_REQUEST", f"More than {MAX_OPPORTUNITIES} opportunities match; narrow the selection.")

    cost_of = costs.opportunity_costs(list(opps))
    entries: dict[int, list] = {}
    if "cost_entries" in include:
        # Entries against an engagement are apportioned inside opportunity_costs; listed here on the opportunity
        # only when entered against it, with the engagement's own entries named by slug on each member.
        for e in PulseCostEntry.objects.filter(opportunity_id__in=list(opps)).select_related("group"):
            entries.setdefault(e.opportunity_id, []).append(_entry(e))
        for opp in opps.values():
            if opp.group_id:
                for e in PulseCostEntry.objects.filter(group_id=opp.group_id).select_related("group"):
                    entries.setdefault(opp.opportunity_id, []).append(_entry(e))
    payments: dict[int, list] = {}
    if "org_payments" in include:
        for p in PulseOrgPayment.objects.filter(opportunity_id__in=list(opps)):
            payments.setdefault(p.opportunity_id, []).append(_payment(p))

    rows = []
    for oid, opp in opps.items():
        c = cost_of.get(oid) or costs.OppCosts(opportunity_id=oid)
        row = {
            "id": oid,
            "name": opp.name,
            "org_slug": opp.org_slug,
            "program_id": opp.program_id,
            "currency": opp.currency,
            "active": opp.is_active,
            "end_date": opp.end_date.isoformat() if opp.end_date else None,
            "invoices_synced_at": opp.invoices_synced_at.isoformat() if opp.invoices_synced_at else None,
            "approved_units": c.approved_units,
            "worker_usd": _money(c.worker_usd),
            "org_usd": _money(c.org_usd),
            "per_service_usd": _money(c.per_service_usd),
            "fixed_invoiced_usd": _money(c.fixed_invoiced_usd),
            "fixed_entered_usd": _money(c.fixed_entered_usd),
            "org_fee_entered_usd": _money(c.org_fee_entered_usd),
            "service_invoiced_usd": _money(c.service_invoiced_usd),
            "total_usd": _money(c.total_usd),
        }
        if "invoices" in include:
            row["invoices"] = [_invoice(i) for i in sorted(c.invoices, key=lambda i: (i.date is None, i.date))]
        if "cost_entries" in include:
            row["cost_entries"] = entries.get(oid, [])
        if "org_payments" in include:
            row["org_payments"] = payments.get(oid, [])
            row["org_paid_usd"] = round(sum(p["amount_usd"] or 0 for p in row["org_payments"]), 2)
        rows.append(row)
    return {
        "count": len(rows),
        "total_usd": round(sum(r["total_usd"] or 0 for r in rows), 2),
        "rows": rows,
    }
