"""Every derived exception in the domain, as structured facts.

The product derives. It does not recommend (design doc section 22). The
distinction this module rests on:

  a **derivation** is a deterministic function of records -- this quote is
  missing a pack specification; this award has no contract; this point is
  under its own minimum. Those are facts, and they belong here.

  a **recommendation** is a judgement about what to do, in what order, and
  in what words. That belongs to a client -- an agent on top of this surface
  can do it better than a hardcoded list could, for any programme, in any
  language, and can change its mind without a deploy.

So this returns exceptions in a deterministic but deliberately *non-semantic*
order (by kind, then by subject id). There is no priority field, no severity,
no "you should", and no drafted message. A caller that wants a worklist sorts
one; nothing here pretends to know which of a stockout and an unevidenced
duty relief matters more today, because that depends on things the database
does not contain.

`audience` is the one piece of routing that IS a fact: a missing pack
specification can only be answered by the supplier, and a missing ration
table can only be answered by us. Handing a supplier a question about our own
configuration wastes their time and ours.
"""

from datetime import date

from django.db.models import Count, Q

from connect_labs.supply_chain.fulfilment.services.landed import landed_total
from connect_labs.supply_chain.fulfilment.services.match import three_way_match
from connect_labs.supply_chain.models import Award, Commodity, Contract, Item, Movement, Round, Shipment, Supplier
from connect_labs.supply_chain.procurement.services.comparison import compare_round
from connect_labs.supply_chain.procurement.services.compliance import spec_verdict
from connect_labs.supply_chain.stock.services import network
from connect_labs.supply_chain.values import Unconfirmed

# Every kind this module can produce. Declared so a client can enumerate what
# it may receive without waiting to encounter one, and so a new kind cannot be
# added without appearing in the contract.
KINDS = (
    "quote_not_comparable",
    "round_awaiting_response",
    "supplier_never_approached",
    "award_not_contracted",
    "contract_reference_unknown",
    "duty_relief_unevidenced",
    "contract_cost_unconfirmed",
    "shipment_stalled",
    "shipment_without_certificate",
    "invoice_over_billed",
    "stock_stockout",
    "stock_below_minimum",
    "stock_never_reported",
    "stock_unconfirmed",
    "commodity_course_undefined",
    "item_fails_specification",
)


def _exception(kind, *, subject_type, subject_id, label, audience, facts=None, since=None, as_of=None):
    """One exception. `days_open` is computed so a client need not know today."""
    reference = as_of or date.today()
    days_open = (reference - since).days if since else None
    return {
        "kind": kind,
        "subject": {"type": subject_type, "id": subject_id, "label": label},
        "audience": audience,
        "facts": facts or {},
        "since": since.isoformat() if since else None,
        "days_open": days_open,
    }


def _sourcing(access, as_of):
    out = []

    for supplier in Supplier.objects.filter(scope_key=access.scope_key).annotate(invitations=Count("outreach")):
        if supplier.invitations == 0 and supplier.status in ("identified", ""):
            out.append(
                _exception(
                    "supplier_never_approached",
                    subject_type="supplier",
                    subject_id=supplier.pk,
                    label=supplier.name,
                    audience="internal",
                    facts={"status": supplier.status},
                    since=supplier.created_at.date(),
                    as_of=as_of,
                )
            )

    for round_ in Round.objects.filter(program_id=access.program_id, status="open").prefetch_related(
        "outreach__supplier"
    ):
        for invitation in round_.outreach.all():
            if not invitation.responded:
                out.append(
                    _exception(
                        "round_awaiting_response",
                        subject_type="outreach",
                        subject_id=invitation.pk,
                        label=f"{invitation.supplier.name} — {round_.label}",
                        audience="supplier",
                        facts={
                            "round_id": round_.pk,
                            "supplier_id": invitation.supplier_id,
                            "channel": invitation.channel,
                        },
                        since=invitation.sent_on,
                        as_of=as_of,
                    )
                )

        for line in round_.lines or []:
            slug = line.get("commodity_slug")
            commodity = access.get_commodity(slug) if slug else None
            if commodity is None:
                continue
            quotes = [q for q in access.list_quotes(round_id=round_.pk) if q.commodity_id == commodity.pk]
            if not quotes:
                continue
            comparison = compare_round(
                round_,
                commodity,
                quotes,
                {s.pk: s for s in access.list_suppliers()},
                items_by_id=access.items_by_id(),
            )
            for row in comparison.blocked:
                out.append(
                    _exception(
                        "quote_not_comparable",
                        subject_type="quote",
                        subject_id=row.quote_id,
                        label=f"{row.supplier_name} — {commodity.name}",
                        # A quote blocked only on facts WE have not supplied
                        # is our problem; one blocked on the supplier's terms
                        # is theirs. Reported per question below.
                        # Each missing fact carries its own audience below;
                        # the exception's is the one that predominates, so a
                        # caller filtering on it is not misled about who can
                        # answer.
                        audience=(
                            "internal"
                            if row.questions and all(q.audience == "internal" for q in row.questions)
                            else "supplier"
                        ),
                        facts={
                            "round_id": round_.pk,
                            "supplier_id": row.supplier_id,
                            "missing": [
                                {"key": q.key, "question": q.question, "audience": q.audience} for q in row.questions
                            ],
                        },
                        as_of=as_of,
                    )
                )

    return out


def _catalogue(access, as_of):
    out = []
    for commodity in Commodity.objects.filter(scope_key=access.scope_key):
        if not (commodity.course_definition or {}).get("base_units_per_course"):
            out.append(
                _exception(
                    "commodity_course_undefined",
                    subject_type="commodity",
                    subject_id=commodity.pk,
                    label=commodity.name,
                    # Nobody outside can answer this. It is our ration table.
                    audience="internal",
                    facts={"blocks": ["cost_per_course", "cost_per_child", "courses_from_quantity"]},
                    as_of=as_of,
                )
            )

    for item in Item.objects.filter(scope_key=access.scope_key).select_related("commodity"):
        verdict = spec_verdict(item.spec_attributes, item.commodity.spec_requirements)
        if "fail" in verdict.lower():
            out.append(
                _exception(
                    "item_fails_specification",
                    subject_type="item",
                    subject_id=item.pk,
                    label=f"{item.name} ({item.sku})",
                    # The item does not meet the commodity's own requirement,
                    # so the decision is ours: buy a different item, or change
                    # the requirement. Not a question for the manufacturer.
                    audience="internal",
                    facts={
                        "verdict": verdict,
                        "requirements": item.commodity.spec_requirements,
                        "stated": item.spec_attributes,
                    },
                    as_of=as_of,
                )
            )
    return out


def _fulfilment(access, as_of):
    out = []

    contracted_awards = set(
        Contract.objects.filter(program_id=access.program_id, award__isnull=False).values_list("award_id", flat=True)
    )
    for award in Award.objects.filter(round__program_id=access.program_id).select_related("supplier", "commodity"):
        if award.pk not in contracted_awards:
            out.append(
                _exception(
                    "award_not_contracted",
                    subject_type="award",
                    subject_id=award.pk,
                    label=f"{award.supplier.name} — {award.commodity.name}",
                    audience="internal",
                    facts={"round_id": award.round_id, "provisional": award.provisional},
                    since=award.decided_on,
                    as_of=as_of,
                )
            )

    for contract in Contract.objects.filter(program_id=access.program_id).select_related(
        "commodity", "supplier", "buyer_party"
    ):
        if contract.status not in ("cancelled", "closed") and not contract.reference:
            out.append(
                _exception(
                    "contract_reference_unknown",
                    subject_type="contract",
                    subject_id=contract.pk,
                    label=f"{contract.supplier.name} — {contract.commodity.name}",
                    # If the partner raised the order, only the partner holds
                    # the reference.
                    audience="partner" if contract.buyer_of_record == "partner_org" else "internal",
                    facts={"buyer_of_record": contract.buyer_of_record, "status": contract.status},
                    since=contract.signed_on,
                    as_of=as_of,
                )
            )

        if contract.duty_relief_claimed and not contract.duty_relief_document_id:
            out.append(
                _exception(
                    "duty_relief_unevidenced",
                    subject_type="contract",
                    subject_id=contract.pk,
                    label=f"{contract.supplier.name} — {contract.commodity.name}",
                    audience="partner" if contract.buyer_of_record == "partner_org" else "internal",
                    facts={"buyer_of_record": contract.buyer_of_record},
                    since=contract.signed_on,
                    as_of=as_of,
                )
            )

        costed = landed_total(contract)
        if isinstance(costed["landed_total"], Unconfirmed):
            out.append(
                _exception(
                    "contract_cost_unconfirmed",
                    subject_type="contract",
                    subject_id=contract.pk,
                    label=f"{contract.supplier.name} — {contract.commodity.name}",
                    audience="internal",
                    facts={
                        "buyer_of_record": contract.buyer_of_record,
                        "reasons": list(costed["landed_total"].reasons),
                    },
                    as_of=as_of,
                )
            )

        match = three_way_match(contract)
        if match["status"] == "over_invoiced":
            out.append(
                _exception(
                    "invoice_over_billed",
                    subject_type="contract",
                    subject_id=contract.pk,
                    label=f"{contract.supplier.name} — {contract.commodity.name}",
                    audience="supplier",
                    facts={
                        "over_invoiced": str(match["over_invoiced"].amount),
                        "unit": match["over_invoiced"].unit,
                    },
                    as_of=as_of,
                )
            )

    stalled = Shipment.objects.filter(
        contract__program_id=access.program_id, status__in=("in_transit", "at_customs")
    ).select_related("contract__supplier")
    for shipment in stalled:
        out.append(
            _exception(
                "shipment_stalled",
                subject_type="shipment",
                subject_id=shipment.pk,
                label=f"{shipment.reference or shipment.pk} — {shipment.contract.supplier.name}",
                audience="partner",
                facts={"status": shipment.status, "contract_id": shipment.contract_id},
                since=shipment.dispatched_on,
                as_of=as_of,
            )
        )

    uncertified = (
        Shipment.objects.filter(contract__program_id=access.program_id)
        .annotate(
            certificates=Count(
                "documents",
                filter=Q(documents__kind__in=("certificate_of_analysis", "certificate_of_conformity")),
            )
        )
        .filter(certificates=0)
        .select_related("contract__supplier")
    )
    for shipment in uncertified:
        out.append(
            _exception(
                "shipment_without_certificate",
                subject_type="shipment",
                subject_id=shipment.pk,
                label=f"{shipment.reference or shipment.pk} — {shipment.contract.supplier.name}",
                audience="supplier",
                facts={"status": shipment.status},
                since=shipment.dispatched_on,
                as_of=as_of,
            )
        )
    return out


def _stock(access, as_of, opportunity_id=None):
    out = []
    rows = network.network_stock(access.program_id, opportunity_id=opportunity_id)
    for row in rows:
        subject = dict(
            subject_type="supply_point",
            subject_id=row["supply_point_id"],
            label=row["name"],
            audience="internal",
        )
        if row["status"] == "stockout":
            out.append(_exception("stock_stockout", **subject, facts={"kind": row["kind"]}, as_of=as_of))
        elif row["status"] == "below_min":
            out.append(
                _exception(
                    "stock_below_minimum",
                    **subject,
                    facts={
                        "months_of_stock": str(row["months_of_stock"]),
                        "min_months_of_stock": str(row["min_months_of_stock"]),
                    },
                    as_of=as_of,
                )
            )
        if isinstance(row["on_hand"], Unconfirmed):
            out.append(
                _exception(
                    "stock_unconfirmed",
                    **subject,
                    facts={"reasons": list(row["on_hand"].reasons)},
                    as_of=as_of,
                )
            )
        if row["kind"] == "user_held" and row["reported"] is None:
            has_movements = (
                Movement.objects.for_program(access.program_id)
                .filter(Q(to_supply_point_id=row["supply_point_id"]) | Q(from_supply_point_id=row["supply_point_id"]))
                .exists()
            )
            out.append(
                _exception(
                    "stock_never_reported",
                    **subject,
                    facts={"connect_username": row["connect_username"], "holds_stock": has_movements},
                    as_of=as_of,
                )
            )
    return out


def list_exceptions(access, *, opportunity_id=None, kinds=None, as_of=None) -> list[dict]:
    """Every derived exception in this programme, unranked.

    Sorted by kind then subject id: deterministic, so a diff between two
    calls is meaningful, and deliberately not by importance, because nothing
    here knows what matters today.
    """
    found = _sourcing(access, as_of) + _catalogue(access, as_of) + _fulfilment(access, as_of)
    found += _stock(access, as_of, opportunity_id=opportunity_id)
    if kinds:
        wanted = set(kinds)
        found = [item for item in found if item["kind"] in wanted]
    return sorted(found, key=lambda item: (item["kind"], item["subject"]["id"] or 0))
