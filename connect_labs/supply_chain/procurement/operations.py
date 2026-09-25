"""Procurement's capabilities, registered into the shared supply registry.

Handlers take a SupplyDataAccess first and return JSON-serialisable dicts.
"""

from django.db.models import Q

from connect_labs.supply_chain import records
from connect_labs.supply_chain.operations import (
    _OUTREACH_DATA,
    _OUTREACH_DATA_CREATE,
    _QUOTE_DATA_CORRECTION,
    _QUOTE_DATA_CREATE,
    _TENDER_DATA,
    ID,
    _data_with,
    figure,
    obj,
    record,
    register_operation,
)
from connect_labs.supply_chain.procurement.services.comparison import COURSE_FIGURES, compare_tender
from connect_labs.supply_chain.procurement.services.compliance import check_compliance
from connect_labs.supply_chain.procurement.services.pricing import compute_figures
from connect_labs.supply_chain.procurement.services.questions import missing_facts
from connect_labs.supply_chain.procurement.services.render import render_followup, render_initial_request
from connect_labs.supply_chain.procurement.services.supply_base import supply_base, wire

# ---- tenders and outreach ----------------------------------------------


@register_operation(
    name="tender_list",
    summary="List quote tenders for this programme with their status and lines.",
    input_schema=obj({}),
)
def tender_list(access):
    return [record(r) for r in access.list_tenders()]


@register_operation(
    name="tender_get",
    summary="Fetch one tender by id, with its commodity lines, delivery places and whether it accepts collection.",
    input_schema=obj({"tender_id": ID}, required=("tender_id",)),
)
def tender_get(access, tender_id):
    tender = access.get_tender(tender_id)
    return record(tender) if tender else None


@register_operation(
    name="tender_create",
    summary=(
        "Create a tender (a request for quotes) in draft. Needs lines (commodity_slug, "
        "quantity, quantity_unit), and delivery_points (one or more places) or "
        "pickup_accepted=true, before it can be opened."
    ),
    input_schema=obj({"data": _TENDER_DATA}, required=("data",)),
    is_write=True,
)
def tender_create(access, data):
    return record(access.create_tender(data))


@register_operation(
    name="tender_update",
    summary="Update a tender's label, lines, delivery places, collection, deadline or notes.",
    input_schema=obj({"tender_id": ID, "data": _TENDER_DATA}, required=("tender_id", "data")),
    is_write=True,
)
def tender_update(access, tender_id, data):
    return record(access.update_tender(tender_id, data))


@register_operation(
    name="tender_open",
    summary=(
        "Open a tender for quotes. Refused unless the tender names at least one "
        "delivery place or accepts collection from the supplier, because suppliers "
        "will not quote without knowing where the goods go."
    ),
    input_schema=obj({"tender_id": ID}, required=("tender_id",)),
    is_write=True,
)
def tender_open(access, tender_id):
    return record(access.open_tender(tender_id))


@register_operation(
    name="tender_invite_org",
    summary=(
        "Put a supplier organisation on a tender's invited list, so it can see and bid on the tender "
        "while it is restricted. Only organisations registered as suppliers can be invited."
    ),
    input_schema=obj({"tender_id": ID, "org_id": ID}, required=("tender_id", "org_id")),
    is_write=True,
)
def tender_invite_org(access, tender_id, org_id):
    return record(access.invite_org_to_tender(tender_id, org_id))


@register_operation(
    name="tender_uninvite_org",
    summary="Take an organisation off a tender's invited list. A restricted tender disappears for it.",
    input_schema=obj({"tender_id": ID, "org_id": ID}, required=("tender_id", "org_id")),
    is_write=True,
)
def tender_uninvite_org(access, tender_id, org_id):
    return record(access.uninvite_org_from_tender(tender_id, org_id))


@register_operation(
    name="tender_close",
    summary="Close a tender to further quotes.",
    input_schema=obj({"tender_id": ID}, required=("tender_id",)),
    is_write=True,
)
def tender_close(access, tender_id):
    return record(access.close_tender(tender_id))


@register_operation(
    name="request_render",
    summary=(
        "Render the quote-request text for one supplier on one tender. Asks "
        "for exactly the facts needed to make the reply comparable."
    ),
    input_schema=obj(
        {"tender_id": ID, "supplier_id": ID, "commodity_slug": {"type": "string"}},
        required=("tender_id", "supplier_id", "commodity_slug"),
    ),
)
def request_render(access, tender_id, supplier_id, commodity_slug):
    tender = access.get_tender(tender_id)
    supplier = access.get_supplier(supplier_id)
    commodity = access.get_commodity(commodity_slug)
    return {"text": render_initial_request(commodity, tender, supplier)}


@register_operation(
    name="followup_render",
    summary="Render a follow-up email for a quote, asking only for the facts still missing before it can be compared.",
    input_schema=obj({"quote_id": ID}, required=("quote_id",)),
)
def followup_render(access, quote_id):
    quote = access.get_quote(quote_id)
    tender = access.get_tender(quote.tender_id)
    commodity = access.get_commodity(quote.commodity_slug)
    supplier = access.get_supplier(quote.supplier_id)
    item = access.get_item(quote.item_id) if quote.item_id else None
    return {"text": render_followup(quote, commodity, tender, supplier, item=item)}


@register_operation(
    name="outreach_list",
    summary="List outreach rows — who was asked, when, and whether they replied.",
    input_schema=obj({"tender_id": ID}),
)
def outreach_list(access, tender_id=None):
    return [record(o) for o in access.list_outreach(tender_id=tender_id)]


@register_operation(
    name="outreach_log",
    summary="Record that a quote request was sent to a supplier on a tender.",
    input_schema=obj({"data": _OUTREACH_DATA_CREATE}, required=("data",)),
    is_write=True,
)
def outreach_log(access, data):
    return record(access.create_outreach(data))


@register_operation(
    name="outreach_update",
    summary="Update an outreach row — typically to record that a supplier responded, and how.",
    input_schema=obj({"outreach_id": ID, "data": _OUTREACH_DATA}, required=("outreach_id", "data")),
    is_write=True,
)
def outreach_update(access, outreach_id, data):
    return record(access.update_outreach(outreach_id, data))


@register_operation(
    name="outreach_delete",
    summary=(
        "Delete an invitation recorded in error, with a reason. Unlike quote_void this removes the "
        "row: a quote is a supplier's stated fact worth keeping once superseded, an invitation we "
        "never sent is not. The reason is recorded in the write log."
    ),
    input_schema=obj(
        {"outreach_id": ID, "reason": {"type": "string", "minLength": 1}},
        required=("outreach_id", "reason"),
    ),
    is_write=True,
)
def outreach_delete(access, outreach_id, reason):
    return access.delete_outreach(outreach_id)


# ---- quotes ------------------------------------------------------------


@register_operation(
    name="quote_list",
    summary="List quotes, optionally for one tender. Includes voided and superseded versions.",
    input_schema=obj({"tender_id": ID}),
)
def quote_list(access, tender_id=None):
    return [record(q) for q in access.list_quotes(tender_id=tender_id)]


@register_operation(
    name="quote_get",
    summary=(
        "Fetch one quote with its as-quoted figures and basis flags, plus "
        "the derived figures and what is still missing."
    ),
    input_schema=obj({"quote_id": ID}, required=("quote_id",)),
)
def quote_get(access, quote_id):
    quote = access.get_quote(quote_id)
    if quote is None:
        return None
    tender = access.get_tender(quote.tender_id)
    commodity = access.get_commodity(quote.commodity_slug)
    item = access.get_item(quote.item_id) if quote.item_id else None
    figures = compute_figures(quote, commodity, tender, item=item).as_dict()
    missing = missing_facts(quote, commodity, tender, item=item)
    # The comparison's rule, applied to one quote: a category with no course
    # (a consumable, a dispenser, a test kit) has no per-course figure to be
    # unconfirmed and no treatment protocol for us to enter. Showing them here
    # after the comparison stopped reported the same absence as a gap on one
    # screen and not on the other.
    if not records.course_applies_to_category(commodity.category):
        figures = {key: value for key, value in figures.items() if key not in COURSE_FIGURES}
        missing = [f for f in missing if f.key != "course_definition"]
    return {
        "quote": record(quote),
        "item": record(item) if item else None,
        "figures": {key: figure(value) for key, value in figures.items()},
        "compliance": [
            {
                "field": r.field,
                "outcome": r.outcome,
                "message": r.message,
                "spec_origin": r.spec_origin,
                "claim_conflict": r.claim_conflict,
            }
            for r in check_compliance(quote, commodity, item=item)
        ],
        "missing": [{"key": f.key, "question": f.question, "audience": f.audience} for f in missing],
    }


@register_operation(
    name="quote_record",
    summary=(
        "Record a quote as the supplier stated it, with the quantity_basis the price covers. Do NOT "
        "compute anything: leave freight_basis and duties_basis not_specified, and pack_spec_source "
        "not_stated, where the supplier was silent. Name an item_id with "
        "pack_spec_source=trade_item_confirmed when they identify a known trade item."
    ),
    input_schema=obj({"data": _QUOTE_DATA_CREATE}, required=("data",)),
    is_write=True,
)
def quote_record(access, data):
    return record(access.create_quote(data))


@register_operation(
    name="quote_correct",
    summary=(
        "Correct a quote by writing a new version that supersedes it. Needs "
        "a reason. The original stays readable so past comparisons remain "
        "reproducible."
    ),
    input_schema=obj(
        {"quote_id": ID, "data": _QUOTE_DATA_CORRECTION, "reason": {"type": "string", "minLength": 1}},
        required=("quote_id", "data", "reason"),
    ),
    is_write=True,
)
def quote_correct(access, quote_id, data, reason):
    return record(access.supersede_quote(quote_id, data, reason))


@register_operation(
    name="quote_void",
    summary=(
        "Void a quote with a reason — a duplicate, a mis-entry, or a "
        "withdrawn offer. It stays readable and drops out of comparisons. "
        "This is how a caller cleans up after itself."
    ),
    input_schema=obj(
        {"quote_id": ID, "reason": {"type": "string", "minLength": 1}},
        required=("quote_id", "reason"),
    ),
    is_write=True,
)
def quote_void(access, quote_id, reason):
    return record(access.void_quote(quote_id, reason))


@register_operation(
    name="tender_compare",
    summary=(
        "Compare every live quote on a tender for one commodity. Columns report "
        "rankable=false where any candidate is unconfirmed, with blocked_by "
        "naming the suppliers responsible — the honest answer to 'who is "
        "cheapest' is often 'not yet, ask these questions'."
    ),
    input_schema=obj(
        {"tender_id": ID, "commodity_slug": {"type": "string"}},
        required=("tender_id", "commodity_slug"),
    ),
)
def tender_compare(access, tender_id, commodity_slug):
    tender = access.get_tender(tender_id)
    commodity = access.get_commodity(commodity_slug)
    quotes = [q for q in access.list_quotes(tender_id=tender_id) if q.commodity_slug == commodity_slug]
    suppliers = {s.id: s for s in access.list_suppliers()}
    return compare_tender(tender, commodity, quotes, suppliers, items_by_id=access.items_by_id()).to_snapshot()


@register_operation(
    name="tender_outstanding_questions",
    summary="Every outstanding question on a tender, grouped by supplier — the follow-up worklist.",
    input_schema=obj(
        {"tender_id": ID, "commodity_slug": {"type": "string"}},
        required=("tender_id", "commodity_slug"),
    ),
)
def tender_outstanding_questions(access, tender_id, commodity_slug):
    snapshot = tender_compare(access, tender_id, commodity_slug)
    return [
        {"supplier_name": row["supplier_name"], "quote_id": row["quote_id"], "questions": row["questions"]}
        for row in snapshot["all_rows"]
        if row["questions"]
    ]


@register_operation(
    name="award_create",
    summary=(
        "Award a tender to a quote. Requires a rationale and freezes the "
        "comparison as it stood at the moment of decision. Once every line of a draft or open tender "
        "has an award, the tender's status becomes awarded; a closed tender is left as it is."
    ),
    input_schema=obj(
        {
            "tender_id": ID,
            "quote_id": ID,
            "rationale": {"type": "string", "minLength": 1},
            "decided_by": {"type": "string"},
            # The day the decision was made, which is often not the day it
            # is recorded -- the meeting was in July, the entry is today. It
            # defaults to today, and may not be a day that has not come.
            "decided_on": {"type": "string", "format": "date"},
        },
        required=("tender_id", "quote_id", "rationale"),
    ),
    is_write=True,
)
def award_create(access, tender_id, quote_id, rationale, decided_by=None, decided_on=None):
    """The decision of record — validate at least as hard as every other write.

    Every other write operation reference-checks what it points at
    (_require_tender, _require_commodity); award_create is the one that
    freezes a comparison_snapshot into a permanent record, so a bad
    reference here is a permanent record of the wrong thing. Three checks
    a tender-trip through tender_compare would not itself catch:
      - the quote exists at all (a bad id would otherwise crash inside
        tender_compare on `quote.commodity_slug`, a confusing AttributeError
        instead of a 400 naming the missing quote);
      - the quote belongs to THIS tender -- without this, tender 1 could be
        awarded to a quote that only ever quoted on tender 2, and the frozen
        snapshot (built from tender_id's own comparison) would never contain
        the quote it claims to have chosen;
      - the quote is live -- a voided or superseded quote's row is already
        filtered out of compare_tender's snapshot by `_is_live`, so awarding
        one would freeze a comparison that does not even list the "winner".
    """
    quote = access.get_quote(quote_id)
    if quote is None:
        raise ValueError(f"quote {quote_id} not found")
    if quote.tender_id != tender_id:
        raise ValueError(
            f"quote {quote_id} belongs to tender {quote.tender_id}, not tender {tender_id} — "
            "a quote can only be awarded on the tender it was quoted for"
        )
    if quote.voided:
        raise ValueError(f"quote {quote_id} is voided and cannot be awarded")
    if quote.superseded_by_quote_id:
        raise ValueError(
            f"quote {quote_id} has been superseded by quote {quote.superseded_by_quote_id} — "
            "award the current version instead"
        )
    decided = _decided_on(decided_on)
    snapshot = tender_compare(access, tender_id, quote.commodity_slug)
    return record(
        access.create_award(
            {
                "tender_id": tender_id,
                "quote_id": quote_id,
                "rationale": rationale,
                "decided_by": decided_by,
                "decided_on": decided,
                "comparison_snapshot": snapshot,
            }
        )
    )


def _decided_on(value):
    """A parsed decision date: today when not given, never in the future."""
    from datetime import date

    if not value:
        return date.today()
    decided = date.fromisoformat(str(value))
    if decided > date.today():
        raise ValueError(f"an award cannot be dated {decided.isoformat()}: that is in the future")
    return decided


@register_operation(
    name="award_list",
    summary="List awards for this programme, each with its frozen comparison snapshot and rationale.",
    input_schema=obj({"tender_id": ID}),
)
def award_list(access, tender_id=None):
    return [record(a) for a in access.list_awards(tender_id=tender_id)]


@register_operation(
    name="commodity_supply_base",
    summary=(
        "Who we think can supply a commodity, and the record each belief rests on: contracted, "
        "awarded, quoted, invited, or merely named as the manufacturer of a trade item. Derived and "
        "dated, never stored. Pass item_id to narrow to one trade item."
    ),
    input_schema=obj(
        {"commodity_slug": {"type": "string"}, "item_id": ID},
        required=("commodity_slug",),
    ),
)
def commodity_supply_base(access, commodity_slug, item_id=None):
    claims = supply_base(
        commodity_slug=commodity_slug,
        item_id=item_id,
        suppliers=access.list_suppliers(),
        quotes=access.list_quotes(),
        awards=access.list_awards(),
        contracts=access.list_contracts(),
        outreach=access.list_outreach(),
        tenders=access.list_tenders(),
        items=access.list_items(),
        declared=_declared(access, commodity_slug),
    )
    return [wire(claim) for claim in claims]


def _offers_for(access, commodity_slug):
    """Every marketplace offering that matches this program's product, with how it matched."""
    from connect_labs.supply_chain.market.service import offering_match_kind
    from connect_labs.supply_chain.models import SupplierOffering

    commodity = access.get_commodity(commodity_slug)
    if commodity is None:
        return []
    items = [i for i in access.list_items() if i.commodity_id == commodity.pk]
    gtins = {g for i in items for g in (i.gtin_base, i.gtin_pack, i.gtin_case) if g}
    # Narrowed in the database to the offerings that could match at all, so a
    # product page does not read every offering on the marketplace.
    candidates = Q(gtin__in=gtins) if gtins else Q(pk__in=[])
    if commodity.category:
        candidates |= Q(category=commodity.category)
    if commodity.unicef_material_number:
        candidates |= Q(unicef_material_number=commodity.unicef_material_number)
    found = []
    for offering in SupplierOffering.objects.filter(candidates).select_related("profile__org"):
        match = offering_match_kind(offering, commodity, items)
        if match is not None:
            found.append((offering, match))
    return found


def _declared(access, commodity_slug):
    by_org = {s.org_id: s.pk for s in access.list_suppliers()}
    return [
        (by_org[offering.profile.org_id], offering, match)
        for offering, match in _offers_for(access, commodity_slug)
        if offering.profile.org_id in by_org
    ]


@register_operation(
    name="commodity_market_offers",
    summary=(
        "Companies on the supplier marketplace that say they sell this product and are NOT yet this "
        "program's suppliers -- who to approach next. Their own claim, dated, unverified; `match` says "
        "whether it matched exactly (UNICEF number or GTIN) or only by kind of product."
    ),
    input_schema=obj({"commodity_slug": {"type": "string"}}, required=("commodity_slug",)),
)
def commodity_market_offers(access, commodity_slug):
    ours = {s.org_id for s in access.list_suppliers()}
    return [
        {
            "org_id": offering.profile.org_id,
            "org_name": offering.profile.org.name,
            "country": offering.profile.org.country,
            "product_name": offering.product_name,
            "match": match,
            "certifications": offering.certifications,
            "updated_on": offering.updated_at.date().isoformat() if offering.updated_at else None,
        }
        for offering, match in _offers_for(access, commodity_slug)
        if offering.profile.org_id not in ours
    ]


# Purchases used to live here, as "what an LLO actually paid". They are gone:
# a commitment (Contract), a bill (Invoice) and a settlement (Payment) are
# three different facts with three different dates, and collapsing them into
# one row meant the system could not answer "what have we committed but not
# paid". See fulfilment/operations.py.


@register_operation(
    name="tracker_import",
    summary=(
        "Import a procurement tracker from a Google Sheet: suppliers, tenders, invitations and quotes. "
        "Records what the sheet STATES and refuses what it DERIVES — `refused` lists what it would "
        "not guess at, and is as much the point as `imported`. Idempotent on tender labels and "
        "supplier names. The sheet must be shared with the Drive service account. Use dry_run first."
    ),
    input_schema=obj(
        {
            "spreadsheet_id": {"type": "string"},
            "commodity_slug": {"type": "string"},
            "ensure_commodity": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
        }
    ),
    is_write=True,
    internal=True,
)
def tracker_import(access, spreadsheet_id=None, commodity_slug="rutf", ensure_commodity=False, dry_run=False):
    from connect_labs.supply_chain.procurement.services import tracker_import as service

    return service.import_tracker(
        access,
        spreadsheet_id=spreadsheet_id or service.SPREADSHEET_ID,
        commodity_slug=commodity_slug,
        ensure_commodity=ensure_commodity,
        dry_run=dry_run,
    )


# ---- approvals ----------------------------------------------------------
#
# Somebody other than the decider has to agree before an award becomes an
# order: a technical partner confirming the product, a funder approving a use
# of funds, a regulator. contract_create refuses an award with one pending or
# declined, and names it.

_DATE = {"type": "string", "format": "date"}


@register_operation(
    name="approval_request",
    summary=(
        "Record that an award needs a third party's agreement before it can be ordered: approver_org_id "
        "(an organisation from org_list) in the role technical, funder or regulatory. It starts as "
        "requested. While any approval on an award is requested or declined, contract_create against "
        "that award is refused. Attach the approver's letter with document_attach and approval_id."
    ),
    input_schema=obj(
        {
            "data": _data_with(
                ("award_id", "approver_org_id", "role"),
                award_id=ID,
                approver_org_id=ID,
                role={"enum": list(records.APPROVAL_ROLES)},
                requested_on=_DATE,
                note={"type": "string"},
                # A document already on file that the approval rests on -- a
                # product registration under a regulatory approval.
                rests_on_document_id=ID,
            )
        },
        required=("data",),
    ),
    is_write=True,
)
def approval_request(access, data):
    return record(access.request_approval(data))


@register_operation(
    name="approval_decide",
    summary=(
        "Record the approver's answer: approved or declined, on decided_on (today if omitted). A decision "
        "is final on its row; a reversal is a new approval_request, so the first answer stays on record."
    ),
    input_schema=obj(
        {
            "approval_id": ID,
            "status": {"enum": ["approved", "declined"]},
            "decided_on": _DATE,
            "note": {"type": "string"},
            "rests_on_document_id": ID,
            # Set only by the approver's own update link (update_links/service.py),
            # which runs with no user. It makes the answer the approver's word.
            "via_update_link_id": ID,
        },
        required=("approval_id", "status"),
    ),
    is_write=True,
)
def approval_decide(
    access, approval_id, status, decided_on=None, note=None, rests_on_document_id=None, via_update_link_id=None
):
    source, recorded_by = None, None
    if via_update_link_id is not None:
        # Refused for anyone signed in: a programme member recording the
        # answer is `we_recorded`, and letting them stamp it as the approver's
        # own word would undo the one thing the approver's link establishes.
        if getattr(access, "user", None) is not None or getattr(access, "request", None) is not None:
            raise ValueError("an answer is the approver's own word only through the approver's own link")
        from connect_labs.supply_chain.update_links.models import UpdateLink
        from connect_labs.supply_chain.update_links.service import scope_for

        link = UpdateLink.objects.filter(pk=via_update_link_id, program_id=access.program_id).first()
        # The link's scope as it stands now: named approvals on a listed link,
        # every approval asked of its organisation on one that follows it.
        if link is None or not link.is_usable or not scope_for(link).approvals.filter(pk=approval_id).exists():
            raise ValueError(f"update link {via_update_link_id} does not cover approval {approval_id}")
        source, recorded_by = "partner_reported", link.org_id
    return record(
        access.decide_approval(
            approval_id,
            status,
            decided_on=decided_on,
            note=note,
            rests_on_document_id=rests_on_document_id,
            source=source,
            recorded_by_org_id=recorded_by,
        )
    )


@register_operation(
    name="approval_list",
    summary="List approvals on this programme's awards, optionally for one award or in one status.",
    input_schema=obj({"award_id": ID, "status": {"enum": list(records.APPROVAL_STATUSES)}}),
)
def approval_list(access, award_id=None, status=None):
    return [record(a) for a in access.list_approvals(award_id=award_id, status=status)]


# ---- supplier performance ----------------------------------------------
#
# What a supplier promised against what they did. The domain stored both and
# compared them nowhere, so the supplier directory was a list of names.


@register_operation(
    name="supplier_performance",
    summary=(
        "On-time and in-full delivery per supplier for this programme, from what was promised "
        "(signed_on plus promised_lead_time_days) against what arrived. Orders with no promised "
        "lead time, and orders still on their way, are EXCLUDED from the rates and counted "
        "separately (no_promise, not_yet_due) -- scoring an unpromised order as punctual would "
        "reward never committing to a date. Every rate comes with the count behind it, and a rate "
        "out of no measurable orders is null rather than zero. In full means accepted: goods "
        "refused on arrival were not delivered. There is deliberately no single blended score."
    ),
    input_schema=obj({"supplier_id": ID}),
)
def supplier_performance(access, supplier_id=None):
    from connect_labs.supply_chain.procurement.services.performance import supplier_performance as service

    return service(access, supplier_id=supplier_id)
