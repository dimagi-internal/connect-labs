"""The domain's checks, and what they found.

A fan-out of the derivations, so a client does not have to recompute them.
Each check is a deterministic function of the records, and falls into exactly
one of three categories -- a distinction that matters because it says what
kind of thing you are looking at and who, if anyone, can settle it:

  missing    a fact nobody has supplied. Structural: a null column, an
             absent document, a derivation that returned Unconfirmed.
  conflict   two records that disagree. Arithmetic: an invoice billed for
             more than arrived, a ledger and a stock count that differ.
  threshold  a derived figure that crossed a bound **stored in the data** --
             a supply point's own min/max band, a commodity's own
             specification. The number is yours, not ours.

None of the three is an opinion. That is the whole point, and it is why the
set is small and closed: these are properties of the schema, not judgements
about a programme.

**What is deliberately NOT here.** A state you can read off one table --
"this supplier has not replied", "this shipment is at customs" -- is not a
check. `outreach_list` and `shipment_list` already say so, and a second code
path producing the same fact implies a problem where there may be a shipment
that left yesterday. Every check below needs a derivation or a join; if a
single list call would answer it, it does not belong.

**And there is no learning here.** These detect gaps in a row. The exceptions
that actually matter in a live programme are patterns across rows and over
time -- this supplier is always three weeks late, this store's reported stock
is always half what we issued, cartons from this plant keep arriving short.
None of those can be enumerated in advance, and none of them need to be: the
operations already expose the history, and finding those patterns is a
client's job. The product's set is closed and cheap; a client's is open and
needs no deploy.

Nothing here ranks or words anything (design doc section 22). Prioritising is
a judgement about what matters today, which depends on things the database
does not contain.

`audience` is the one piece of routing that IS a fact. A missing pack
specification can only be answered by the supplier; an unset ration table
only by us; an unevidenced duty relief on a partner-bought contract only by
the partner. Handing a supplier a question about our own configuration wastes
their time and ours.
"""

from datetime import date

from django.db.models import Count, Q

from connect_labs.supply_chain.fulfilment.services.landed import landed_total
from connect_labs.supply_chain.fulfilment.services.match import three_way_match
from connect_labs.supply_chain.models import Award, Commodity, Contract, Item, Movement, Round, Shipment
from connect_labs.supply_chain.procurement.services.comparison import compare_round
from connect_labs.supply_chain.procurement.services.compliance import spec_verdict
from connect_labs.supply_chain.stock.services import network, soh
from connect_labs.supply_chain.values import Unconfirmed, decimal_string

# Every check this module can run, and its category. Declared so a client can
# enumerate what it may receive without waiting to encounter one, and so a new
# check cannot be added without appearing in the contract.
CATEGORIES = ("missing", "conflict", "threshold")

KIND_CATEGORIES = {
    # missing -- a fact nobody supplied
    "quote_not_comparable": "missing",
    "contract_cost_unconfirmed": "missing",
    "contract_reference_unknown": "missing",
    "duty_relief_unevidenced": "missing",
    "shipment_without_certificate": "missing",
    "commodity_course_undefined": "missing",
    "stock_unconfirmed": "missing",
    "stock_never_reported": "missing",
    # conflict -- two records disagree
    "award_not_contracted": "conflict",
    "invoice_over_billed": "conflict",
    "stock_variance": "conflict",
    "stock_negative": "conflict",
    # threshold -- a derived figure crossed a bound stored in the data
    "stock_stockout": "threshold",
    "stock_below_minimum": "threshold",
    "item_fails_specification": "threshold",
}

KINDS = tuple(KIND_CATEGORIES)


def _check(kind, *, subject_type, subject_id, label, audience, facts=None, since=None, as_of=None):
    """One finding. `days_open` is computed so a client need not know today."""
    reference = as_of or date.today()
    days_open = (reference - since).days if since else None
    return {
        "kind": kind,
        "category": KIND_CATEGORIES[kind],
        "subject": {"type": subject_type, "id": subject_id, "label": label},
        "audience": audience,
        "facts": facts or {},
        "since": since.isoformat() if since else None,
        "days_open": days_open,
    }


def _sourcing(access, as_of):
    """Quotes that cannot be compared, and why.

    "This supplier has not replied" used to be here and is gone: it is one
    boolean on one outreach row, which `outreach_list` already reports. A
    check that re-reads a single column adds a second path to the same fact
    and, by appearing in a list of findings, asserts that silence is a
    problem -- which on day one it is not.

    "This supplier was never approached" is gone for a different reason: it
    was a judgement about intent. A register of nine suppliers of whom four
    were ever meant to be contacted is a perfectly good register, and the
    database holds nothing that distinguishes that from an oversight.
    """
    out = []
    for round_ in Round.objects.filter(program_id=access.program_id, status="open"):
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
                    _check(
                        "quote_not_comparable",
                        subject_type="quote",
                        subject_id=row.quote_id,
                        label=f"{row.supplier_name} — {commodity.name}",
                        # Each missing fact carries its own audience below; the
                        # check's is the one that predominates, so a caller
                        # filtering on it is not misled about who can answer.
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
                _check(
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
                _check(
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
                _check(
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
                _check(
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
                _check(
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
                _check(
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
                _check(
                    "invoice_over_billed",
                    subject_type="contract",
                    subject_id=contract.pk,
                    label=f"{contract.supplier.name} — {contract.commodity.name}",
                    audience="supplier",
                    facts={
                        "over_invoiced": decimal_string(match["over_invoiced"].amount),
                        "unit": match["over_invoiced"].unit,
                    },
                    as_of=as_of,
                )
            )

    # A shipment sitting at customs used to be reported here as "stalled".
    # It is not a check: `shipment_list(status="at_customs")` says so already,
    # and "stalled" asserted a judgement the code never made -- there was no
    # time threshold at all, so a consignment dispatched yesterday read the
    # same as one held for seventy days. A client concludes "stalled" from the
    # status and the date; the product should not pretend to have decided.

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
            _check(
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
    # One fetch for the variance calls below, rather than one query per point.
    _point_by_id = {point.pk: point for point in access.list_supply_points(opportunity_id=opportunity_id)}
    for row in rows:
        subject = dict(
            subject_type="supply_point",
            subject_id=row["supply_point_id"],
            label=row["name"],
            audience="internal",
        )
        # A negative balance is not a stockout, it is an impossibility: more
        # has left this point than ever arrived. The commodity moved and the
        # movement was never recorded -- an informal transfer between
        # neighbours, or an issue note nobody wrote. Both are ordinary, and
        # both make every figure downstream unreliable until reconciled, so
        # this is a conflict to resolve rather than a level to replenish.
        if not isinstance(row["on_hand"], Unconfirmed) and row["on_hand"].amount < 0:
            out.append(
                _check(
                    "stock_negative",
                    **subject,
                    facts={
                        "balance": decimal_string(row["on_hand"].amount),
                        "unit": row["on_hand"].unit,
                        "kind": row["kind"],
                    },
                    as_of=as_of,
                )
            )
        elif row["status"] == "stockout":
            out.append(_check("stock_stockout", **subject, facts={"kind": row["kind"]}, as_of=as_of))
        elif row["status"] == "below_min":
            out.append(
                _check(
                    "stock_below_minimum",
                    **subject,
                    facts={
                        "months_of_stock": decimal_string(row["months_of_stock"]),
                        "min_months_of_stock": decimal_string(row["min_months_of_stock"]),
                    },
                    as_of=as_of,
                )
            )
        if isinstance(row["on_hand"], Unconfirmed):
            out.append(
                _check(
                    "stock_unconfirmed",
                    **subject,
                    facts={"reasons": list(row["on_hand"].reasons)},
                    as_of=as_of,
                )
            )
        # The disagreement the whole stock design exists to surface: the
        # store's ledger says one thing, the person standing next to the
        # cartons says another. Neither is automatically right, which is why
        # both are kept and the gap between them is a finding rather than
        # something one side silently wins.
        if row["reported"] is not None and not isinstance(row["on_hand"], Unconfirmed):
            variance = soh.stock_on_hand(access.program_id, _point_by_id[row["supply_point_id"]])["variance"]
            if isinstance(variance, Unconfirmed):
                out.append(
                    _check(
                        "stock_variance",
                        **subject,
                        facts={
                            "ledger": decimal_string(row["on_hand"].amount),
                            "ledger_unit": row["on_hand"].unit,
                            "reported": decimal_string(row["reported"].amount),
                            "reported_unit": row["reported"].unit,
                            "reconcilable": False,
                            "reasons": list(variance.reasons),
                        },
                        since=row["reported_on"],
                        as_of=as_of,
                    )
                )
            elif variance.amount != 0:
                out.append(
                    _check(
                        "stock_variance",
                        **subject,
                        facts={
                            "ledger": decimal_string(row["on_hand"].amount),
                            "reported": decimal_string(row["reported"].amount),
                            "variance": decimal_string(variance.amount),
                            "unit": variance.unit,
                            "reconcilable": True,
                            "reported_kind": row["reported_kind"],
                        },
                        since=row["reported_on"],
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
                _check(
                    "stock_never_reported",
                    **subject,
                    facts={"connect_username": row["connect_username"], "holds_stock": has_movements},
                    as_of=as_of,
                )
            )
    return out


def run_checks(access, *, opportunity_id=None, kinds=None, categories=None, as_of=None) -> list[dict]:
    """Run every check over this programme, unranked.

    Sorted by kind then subject id: deterministic, so a diff between two
    calls is meaningful, and deliberately not by importance, because nothing
    here knows what matters today.
    """
    found = _sourcing(access, as_of) + _catalogue(access, as_of) + _fulfilment(access, as_of)
    found += _stock(access, as_of, opportunity_id=opportunity_id)
    if kinds:
        wanted = set(kinds)
        found = [item for item in found if item["kind"] in wanted]
    if categories:
        wanted_categories = set(categories)
        found = [item for item in found if item["category"] in wanted_categories]
    return sorted(found, key=lambda item: (item["kind"], item["subject"]["id"] or 0))
