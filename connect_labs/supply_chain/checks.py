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

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q

from connect_labs.supply_chain import records
from connect_labs.supply_chain.fulfilment.services.landed import landed_total
from connect_labs.supply_chain.fulfilment.services.match import three_way_match
from connect_labs.supply_chain.models import (
    Award,
    AwardApproval,
    Commodity,
    Contract,
    Item,
    Movement,
    Round,
    Shipment,
)
from connect_labs.supply_chain.procurement.services.comparison import compare_round
from connect_labs.supply_chain.procurement.services.compliance import kit_spec_verdict
from connect_labs.supply_chain.stock.services import network, soh
from connect_labs.supply_chain.values import Quantity, Unconfirmed, decimal_string

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
    "shipment_documents_outstanding": "missing",
    "award_awaiting_approval": "missing",
    "payment_unconfirmed": "missing",
    # conflict -- two records disagree
    "award_not_contracted": "conflict",
    "invoice_over_billed": "conflict",
    "stock_variance": "conflict",
    "stock_negative": "conflict",
    # threshold -- a derived figure crossed a bound stored in the data
    "stock_stockout": "threshold",
    "stock_below_minimum": "threshold",
    "item_fails_specification": "threshold",
    # The bound is a date the record itself holds: the shipment's expected
    # date, or the contract's signature plus its promised lead time.
    "shipment_overdue": "threshold",
    "contract_delivery_overdue": "threshold",
}

KINDS = tuple(KIND_CATEGORIES)

# How long after a payment its payee's confirmation is a missing fact rather
# than simply not yet arrived. This is the one number in this module that is
# not read from a row, and it is deliberately not a threshold on a figure:
# it is the grace between "we sent it" and "they should have said so", the
# same allowance every check that dates from `since` makes implicitly. A
# payment made yesterday is not a missing confirmation, and reporting it as
# one would put every settlement on the list the day it happened.
PAYMENT_CONFIRMATION_GRACE_DAYS = 14


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
            quotes_by_id = {q.pk: q for q in quotes}
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
                        # How long the question has gone unanswered. Nullable
                        # on the quote, and left None rather than defaulted
                        # when the sheet gave no date -- an age of 0 would
                        # read as "arrived today".
                        since=quotes_by_id[row.quote_id].received_on,
                        as_of=as_of,
                    )
                )
    return out


# Which categories have a course lives in records.py, beside the rest of the
# domain's vocabulary, so the comparison can ask it too without importing
# this module (which imports the comparison). Re-exported under its old name
# because the catalogue page imports it from here.
course_applies_to_category = records.course_applies_to_category


def _course_applies(commodity) -> bool:
    return course_applies_to_category(commodity.category)


def courses_carried_by_kits(items) -> set:
    """Products whose course a kit already states, so a ration table adds nothing.

    `items` are trade items as dicts with `commodity_slug`, `components` and
    `one_course_is` -- the wire shape, so the catalogue pages can ask with the
    item list they already hold and cannot disagree with this feed.

    A kit whose item says it IS one course carries the course for its own
    product -- when every trade item of that product says so -- and for the
    products inside it that are never bought as items of their own: the
    tablets inside a three-day packet are not stocked, priced or dispensed
    apart from the packet. A part that is also sold loose keeps needing its
    ration table, because that loose item has no course to borrow.
    """
    items = list(items)
    loose = {item["commodity_slug"] for item in items if not item.get("components")}
    by_product: dict[str, list] = {}
    for item in items:
        by_product.setdefault(item["commodity_slug"], []).append(item)
    carried = {slug for slug, own in by_product.items() if all(item.get("one_course_is") for item in own)}
    for item in items:
        if item.get("one_course_is"):
            carried |= {c["commodity_slug"] for c in item.get("components") or [] if c["commodity_slug"] not in loose}
    return carried


def _catalogue(access, as_of):
    out = []
    carried = courses_carried_by_kits(
        {"commodity_slug": slug, "components": components, "one_course_is": one_course_is}
        for slug, components, one_course_is in Item.objects.filter(scope_key=access.scope_key).values_list(
            "commodity__slug", "components", "one_course_is"
        )
    )
    for commodity in Commodity.objects.filter(scope_key=access.scope_key):
        if commodity.slug in carried:
            continue
        if _course_applies(commodity) and not (commodity.course_definition or {}).get("base_units_per_course"):
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

    requirements_by_slug = {
        slug: requirements
        for slug, requirements in Commodity.objects.filter(scope_key=access.scope_key).values_list(
            "slug", "spec_requirements"
        )
    }
    for item in Item.objects.filter(scope_key=access.scope_key).select_related("commodity"):
        # A kit is checked part by part: the zinc inside a co-pack is held to
        # the zinc specification, not to the co-pack's.
        checked = kit_spec_verdict(
            item.spec_attributes, item.commodity.spec_requirements, item.components, requirements_by_slug
        )
        verdict = checked["verdict"]
        if "fail" in verdict.lower():
            facts = {
                "verdict": verdict,
                "requirements": item.commodity.spec_requirements,
                "stated": item.spec_attributes,
            }
            if item.is_kit:
                facts["components"] = checked["components"]
                # Which unit those parts fill: "in each kit", "in each co-pack".
                facts["components_in_each"] = item.components_unit
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
                    facts=facts,
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

    # An approval asked for and not yet given. The fact nobody has supplied is
    # the approver's answer; how long it has been outstanding is the age.
    # Only pending ones: a declined approval is an answer, and the refusal it
    # causes lives on contract_create, where it bites.
    pending = AwardApproval.objects.filter(
        award__round__program_id=access.program_id, status="requested"
    ).select_related("approver_org", "award__supplier", "award__commodity")
    for approval in pending:
        award = approval.award
        out.append(
            _check(
                "award_awaiting_approval",
                subject_type="award",
                subject_id=award.pk,
                label=f"{award.supplier.name} — {award.commodity.name}",
                audience="internal",
                facts={
                    "approval_id": approval.pk,
                    "approver": {"id": approval.approver_org_id, "name": approval.approver_org.name},
                    "role": approval.role,
                    "requested_on": approval.requested_on.isoformat(),
                    "round_id": award.round_id,
                },
                since=approval.requested_on,
                as_of=as_of,
            )
        )

    for contract in Contract.objects.filter(program_id=access.program_id).select_related(
        "commodity", "supplier", "buyer_org"
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
        late = _contract_lateness(contract, match, as_of)
        if late is not None:
            out.append(late)
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
                filter=Q(documents__kind__in=records.CERTIFICATE_KINDS),
            )
        )
        .filter(certificates=0)
        # A consignment that declares its own required documents says what it
        # needs; a certificate belongs on that list when it is needed. Asking
        # for one on top kept a "missing document" on the checks list after the
        # consignment's own checklist read nothing outstanding.
        .filter(required_documents=[])
        .select_related("contract__supplier", "contract__commodity", "contract__item")
    )
    out += _late_shipments(access, as_of)
    out += _unconfirmed_payments(access, as_of)
    out += _outstanding_documents(access, as_of)

    for shipment in uncertified:
        out.append(
            _check(
                "shipment_without_certificate",
                subject_type="shipment",
                subject_id=shipment.pk,
                label=_shipment_label(shipment),
                audience="supplier",
                facts={"status": shipment.status},
                since=shipment.dispatched_on,
                as_of=as_of,
            )
        )
    return out


def _supplier_fact(supplier) -> dict:
    return {"id": supplier.pk, "name": supplier.name}


def _contract_lateness(contract, match, as_of):
    """A contract past its promised lead time and not yet fully received.

    The bound is the contract's own: signed on a date, with a lead time the
    supplier promised. A contract that states neither cannot be late, and is
    not guessed to be. Cancelled and closed are decisions that end the
    question, so they are left alone; a contract whose goods have all
    arrived is on time by definition, whatever its status says.
    """
    if contract.status in ("cancelled", "closed"):
        return None
    if contract.signed_on is None or contract.promised_lead_time_days is None:
        return None
    # A shortfall another order was placed to buy is not a late delivery:
    # nobody is waiting on this supplier for it any more. Lateness is read
    # from the outstanding quantity, not the match status: "over_invoiced"
    # overwrites the received status and says nothing about what arrived.
    if match.get("covered_by"):
        return None
    outstanding = match.get("outstanding")
    if outstanding is not None and not isinstance(outstanding, Unconfirmed) and outstanding.amount <= 0:
        return None
    expected_on = contract.signed_on + timedelta(days=contract.promised_lead_time_days)
    today = as_of or date.today()
    if expected_on >= today:
        return None
    facts = {
        "days_late": (today - expected_on).days,
        "expected_on": expected_on.isoformat(),
        "signed_on": contract.signed_on.isoformat(),
        "promised_lead_time_days": contract.promised_lead_time_days,
        "supplier": _supplier_fact(contract.supplier),
        "status": contract.status,
    }
    if outstanding is not None and not isinstance(outstanding, Unconfirmed):
        facts["outstanding"] = decimal_string(outstanding.amount)
        facts["unit"] = outstanding.unit
    return _check(
        "contract_delivery_overdue",
        subject_type="contract",
        subject_id=contract.pk,
        label=f"{contract.supplier.name} — {contract.commodity.name}",
        # The supplier is who knows where the goods are. Whether to chase,
        # wait or buy elsewhere is not a fact and is not said here.
        audience="supplier",
        facts=facts,
        since=expected_on,
        as_of=as_of,
    )


def _late_shipments(access, as_of):
    """Shipments past their expected date and not yet received.

    `shipment_stalled` was removed from this module because it had no time
    bound at all -- a consignment dispatched yesterday read the same as one
    held for seventy days. This one has the bound the record states: its own
    `expected_on`. A shipment with none cannot be late. Received means either
    status `delivered` or a goods received note against it, because a store
    that recorded the receipt and never moved the status has still received
    the goods. `lost` is its own ending, not lateness.
    """
    today = as_of or date.today()
    late = (
        Shipment.objects.filter(contract__program_id=access.program_id, expected_on__lt=today)
        .exclude(status__in=("delivered", "lost"))
        .filter(receipts__isnull=True)
        .select_related("contract__supplier", "contract__commodity", "contract__item")
        .distinct()
    )
    out = []
    for shipment in late:
        supplier = shipment.contract.supplier
        out.append(
            _check(
                "shipment_overdue",
                subject_type="shipment",
                subject_id=shipment.pk,
                label=_shipment_label(shipment),
                audience="supplier",
                facts={
                    "days_late": (today - shipment.expected_on).days,
                    "expected_on": shipment.expected_on.isoformat(),
                    "supplier": _supplier_fact(supplier),
                    "status": shipment.status,
                    "contract_id": shipment.contract_id,
                },
                since=shipment.expected_on,
                as_of=as_of,
            )
        )
    return out


def _unconfirmed_payments(access, as_of):
    """Payments the payee has not confirmed receiving, past the grace period.

    Aged from the payment date, so `days_open` is how long ago we paid. The
    supplier is the only one who can answer, and the answer is one date.
    """
    from connect_labs.supply_chain.models import Payment

    today = as_of or date.today()
    cutoff = today - timedelta(days=PAYMENT_CONFIRMATION_GRACE_DAYS)
    unconfirmed = Payment.objects.filter(
        invoice__contract__program_id=access.program_id,
        confirmed_by_payee_on__isnull=True,
        paid_on__lt=cutoff,
    ).select_related("invoice__contract__supplier")
    out = []
    for payment in unconfirmed:
        contract = payment.invoice.contract
        out.append(
            _check(
                "payment_unconfirmed",
                subject_type="payment",
                subject_id=payment.pk,
                label=f"{contract.supplier.name} — {payment.reference or payment.invoice.reference or payment.pk}",
                audience="supplier",
                facts={
                    "amount": decimal_string(payment.amount),
                    "currency": payment.currency,
                    "paid_on": payment.paid_on.isoformat(),
                    "invoice_id": payment.invoice_id,
                    "contract_id": contract.pk,
                    "supplier": _supplier_fact(contract.supplier),
                },
                since=payment.paid_on,
                as_of=as_of,
            )
        )
    return out


def _outstanding_documents(access, as_of):
    """Each document a shipment requires and does not have, and who owes it.

    The requirement is the shipment's own list, so this is a gap in a row --
    "missing" -- and not a judgement about what a consignment ought to carry.
    A requirement is met by a document of that kind attached to the shipment
    itself: two consignments under one contract each need their own airway
    bill, so a contract-level document would satisfy the wrong one.
    """
    from connect_labs.labs.models import LabsOrg

    shipments = list(
        Shipment.objects.filter(contract__program_id=access.program_id)
        .exclude(required_documents=[])
        .select_related("contract__supplier", "contract__commodity", "contract__item")
        .prefetch_related("documents")
    )
    owed_by_ids = {
        entry.get("owed_by_org_id") for shipment in shipments for entry in shipment.required_documents or []
    }
    names = dict(LabsOrg.objects.filter(pk__in=owed_by_ids).values_list("pk", "name"))

    out = []
    for shipment in shipments:
        on_file = {document.kind for document in shipment.documents.all()}
        outstanding = [
            {
                "kind": entry["kind"],
                "owed_by": {"id": entry.get("owed_by_org_id"), "name": names.get(entry.get("owed_by_org_id"))},
            }
            for entry in shipment.required_documents or []
            if entry.get("kind") not in on_file
        ]
        if not outstanding:
            continue
        contract = shipment.contract
        out.append(
            _check(
                "shipment_documents_outstanding",
                subject_type="shipment",
                subject_id=shipment.pk,
                label=_shipment_label(shipment),
                # Who owes each is on each line; the check's own audience is
                # mapped to the domain's three -- the supplier's organisation,
                # the buying partner, or anybody else (whom we chase
                # ourselves) -- and is ours when more than one party owes.
                audience=_audience_for_documents(contract, outstanding),
                facts={
                    "outstanding": outstanding,
                    "required": len(shipment.required_documents),
                    "status": shipment.status,
                    "contract_id": contract.pk,
                },
                since=shipment.dispatched_on,
                as_of=as_of,
            )
        )
    return out


def _shipment_label(shipment) -> str:
    """Reference, what it carries, and who sent it -- so a check names the goods."""
    contract = shipment.contract
    what = contract.item.name if contract.item_id else contract.commodity.name
    return f"{shipment.reference or shipment.pk} — {what} — {contract.supplier.name}"


def _audience_for_documents(contract, outstanding) -> str:
    """Whose to answer, when several organisations may each owe a document.

    One owner: that owner's audience. Several: ours, because we are the ones
    chasing more than one party -- "only the supplier can answer" was taken
    from the first line alone while the next line named a clearing agent.
    """
    audiences = {_audience_for_org(contract, entry["owed_by"]["id"]) for entry in outstanding}
    return audiences.pop() if len(audiences) == 1 else "internal"


def _audience_for_org(contract, org_id):
    if org_id is not None and org_id == contract.supplier.org_id:
        return "supplier"
    if org_id is not None and org_id == contract.buyer_org_id and contract.buyer_of_record == "partner_org":
        return "partner"
    return "internal"


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
                        # At the stock page's precision: a figure derived from
                        # counted cartons carried to 28 places is false precision,
                        # and this is what the checks page and every alert print.
                        "months_of_stock": decimal_string(Decimal(row["months_of_stock"]).quantize(Decimal("0.01"))),
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
            # None when the count cannot be matched to the point's own item (a
            # count by commodity at a store holding several items): nothing to
            # compare, which is not a crash of the whole checks list.
            elif isinstance(variance, Quantity) and variance.amount != 0:
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
