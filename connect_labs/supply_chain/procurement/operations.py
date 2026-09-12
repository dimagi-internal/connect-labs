"""Procurement's capabilities, registered into the shared supply registry.

Handlers take a SupplyDataAccess first and return JSON-serialisable dicts.
"""

from connect_labs.supply_chain.operations import (
    _OUTREACH_DATA,
    _OUTREACH_DATA_CREATE,
    _PURCHASE_DATA,
    _QUOTE_DATA,
    _QUOTE_DATA_CREATE,
    _ROUND_DATA,
    ID,
    figure,
    obj,
    record,
    register_operation,
)
from connect_labs.supply_chain.procurement.services.comparison import compare_round
from connect_labs.supply_chain.procurement.services.compliance import check_compliance
from connect_labs.supply_chain.procurement.services.pricing import compute_figures
from connect_labs.supply_chain.procurement.services.questions import missing_facts
from connect_labs.supply_chain.procurement.services.render import render_followup, render_initial_request

# ---- rounds and outreach ----------------------------------------------


@register_operation(
    name="round_list",
    summary="List quote rounds for this programme with their status and lines.",
    input_schema=obj({}),
)
def round_list(access):
    return [record(r) for r in access.list_rounds()]


@register_operation(
    name="round_get",
    summary="Fetch one round by id, with its commodity lines and delivery point.",
    input_schema=obj({"round_id": ID}, required=("round_id",)),
)
def round_get(access, round_id):
    round_ = access.get_round(round_id)
    return record(round_) if round_ else None


@register_operation(
    name="round_create",
    summary=(
        "Create a quote round in draft. Needs lines (commodity_slug, quantity, "
        "quantity_unit) and a delivery_point before it can be opened."
    ),
    input_schema=obj({"data": _ROUND_DATA}, required=("data",)),
    is_write=True,
)
def round_create(access, data):
    return record(access.create_round(data))


@register_operation(
    name="round_update",
    summary="Update a round's label, lines, delivery point, deadline or notes.",
    input_schema=obj({"round_id": ID, "data": _ROUND_DATA}, required=("round_id", "data")),
    is_write=True,
)
def round_update(access, round_id, data):
    return record(access.update_round(round_id, data))


@register_operation(
    name="round_open",
    summary=(
        "Open a round for quotes. Refused unless the round has a delivery "
        "point, because suppliers will not quote without knowing where the "
        "goods go."
    ),
    input_schema=obj({"round_id": ID}, required=("round_id",)),
    is_write=True,
)
def round_open(access, round_id):
    return record(access.open_round(round_id))


@register_operation(
    name="round_close",
    summary="Close a round to further quotes.",
    input_schema=obj({"round_id": ID}, required=("round_id",)),
    is_write=True,
)
def round_close(access, round_id):
    return record(access.close_round(round_id))


@register_operation(
    name="request_render",
    summary=(
        "Render the quote-request text for one supplier on one round. Asks "
        "for exactly the facts needed to make the reply comparable."
    ),
    input_schema=obj(
        {"round_id": ID, "supplier_id": ID, "commodity_slug": {"type": "string"}},
        required=("round_id", "supplier_id", "commodity_slug"),
    ),
)
def request_render(access, round_id, supplier_id, commodity_slug):
    round_ = access.get_round(round_id)
    supplier = access.get_supplier(supplier_id)
    commodity = access.get_commodity(commodity_slug)
    return {"text": render_initial_request(commodity, round_, supplier)}


@register_operation(
    name="followup_render",
    summary="Render a follow-up email for a quote, asking only for the facts still missing before it can be compared.",
    input_schema=obj({"quote_id": ID}, required=("quote_id",)),
)
def followup_render(access, quote_id):
    quote = access.get_quote(quote_id)
    round_ = access.get_round(quote.round_id)
    commodity = access.get_commodity(quote.commodity_slug)
    supplier = access.get_supplier(quote.supplier_id)
    item = access.get_item(quote.item_id) if quote.item_id else None
    return {"text": render_followup(quote, commodity, round_, supplier, item=item)}


@register_operation(
    name="outreach_list",
    summary="List outreach rows — who was asked, when, and whether they replied.",
    input_schema=obj({"round_id": ID}),
)
def outreach_list(access, round_id=None):
    return [record(o) for o in access.list_outreach(round_id=round_id)]


@register_operation(
    name="outreach_log",
    summary="Record that a quote request was sent to a supplier on a round.",
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


# ---- quotes ------------------------------------------------------------


@register_operation(
    name="quote_list",
    summary="List quotes, optionally for one round. Includes voided and superseded versions.",
    input_schema=obj({"round_id": ID}),
)
def quote_list(access, round_id=None):
    return [record(q) for q in access.list_quotes(round_id=round_id)]


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
    round_ = access.get_round(quote.round_id)
    commodity = access.get_commodity(quote.commodity_slug)
    item = access.get_item(quote.item_id) if quote.item_id else None
    figures = compute_figures(quote, commodity, round_, item=item)
    return {
        "quote": record(quote),
        "item": record(item) if item else None,
        "figures": {key: figure(value) for key, value in figures.as_dict().items()},
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
        "missing": [
            {"key": f.key, "question": f.question, "audience": f.audience}
            for f in missing_facts(quote, commodity, round_, item=item)
        ],
    }


@register_operation(
    name="quote_record",
    summary=(
        "Record a quote exactly as the supplier stated it. Give as_quoted_unit "
        "(per_base_unit | per_pack | per_lot_total | per_metric_tonne) and the "
        "quantity_basis the price covers. Do NOT compute anything: leave "
        "freight_basis and duties_basis as not_specified and pack_spec_source "
        "not_stated when the supplier was silent. Name an item_id with "
        "pack_spec_source=trade_item_confirmed when the supplier identifies a "
        "known trade item — that IS a statement of pack spec. Comparable "
        "figures are derived, "
        "and a guessed input produces a confident wrong answer."
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
        {"quote_id": ID, "data": _QUOTE_DATA, "reason": {"type": "string", "minLength": 1}},
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
    name="quote_questions",
    summary=(
        "The facts still missing before this quote could be compared "
        "honestly — the questions to send back to the supplier."
    ),
    input_schema=obj({"quote_id": ID}, required=("quote_id",)),
)
def quote_questions(access, quote_id):
    quote = access.get_quote(quote_id)
    round_ = access.get_round(quote.round_id)
    commodity = access.get_commodity(quote.commodity_slug)
    item = access.get_item(quote.item_id) if quote.item_id else None
    return [
        {"key": f.key, "question": f.question, "audience": f.audience}
        for f in missing_facts(quote, commodity, round_, item=item)
    ]


# ---- comparison and award ---------------------------------------------


@register_operation(
    name="round_compare",
    summary=(
        "Compare every live quote on a round for one commodity. Columns report "
        "rankable=false where any candidate is unconfirmed, with blocked_by "
        "naming the suppliers responsible — the honest answer to 'who is "
        "cheapest' is often 'not yet, ask these questions'."
    ),
    input_schema=obj(
        {"round_id": ID, "commodity_slug": {"type": "string"}},
        required=("round_id", "commodity_slug"),
    ),
)
def round_compare(access, round_id, commodity_slug):
    round_ = access.get_round(round_id)
    commodity = access.get_commodity(commodity_slug)
    quotes = [q for q in access.list_quotes(round_id=round_id) if q.commodity_slug == commodity_slug]
    suppliers = {s.id: s for s in access.list_suppliers()}
    return compare_round(round_, commodity, quotes, suppliers, items_by_id=access.items_by_id()).to_snapshot()


@register_operation(
    name="round_outstanding_questions",
    summary="Every outstanding question on a round, grouped by supplier — the follow-up worklist.",
    input_schema=obj(
        {"round_id": ID, "commodity_slug": {"type": "string"}},
        required=("round_id", "commodity_slug"),
    ),
)
def round_outstanding_questions(access, round_id, commodity_slug):
    snapshot = round_compare(access, round_id, commodity_slug)
    return [
        {"supplier_name": row["supplier_name"], "quote_id": row["quote_id"], "questions": row["questions"]}
        for row in snapshot["all_rows"]
        if row["questions"]
    ]


@register_operation(
    name="award_create",
    summary=(
        "Award a round to a quote. Requires a rationale and freezes the "
        "comparison as it stood at the moment of decision."
    ),
    input_schema=obj(
        {
            "round_id": ID,
            "quote_id": ID,
            "rationale": {"type": "string", "minLength": 1},
            "decided_by": {"type": "string"},
        },
        required=("round_id", "quote_id", "rationale"),
    ),
    is_write=True,
)
def award_create(access, round_id, quote_id, rationale, decided_by=None):
    """The decision of record — validate at least as hard as every other write.

    Every other write operation reference-checks what it points at
    (_require_round, _require_commodity); award_create is the one that
    freezes a comparison_snapshot into a permanent record, so a bad
    reference here is a permanent record of the wrong thing. Three checks
    a round-trip through round_compare would not itself catch:
      - the quote exists at all (a bad id would otherwise crash inside
        round_compare on `quote.commodity_slug`, a confusing AttributeError
        instead of a 400 naming the missing quote);
      - the quote belongs to THIS round -- without this, round 1 could be
        awarded to a quote that only ever quoted on round 2, and the frozen
        snapshot (built from round_id's own comparison) would never contain
        the quote it claims to have chosen;
      - the quote is live -- a voided or superseded quote's row is already
        filtered out of compare_round's snapshot by `_is_live`, so awarding
        one would freeze a comparison that does not even list the "winner".
    """
    quote = access.get_quote(quote_id)
    if quote is None:
        raise ValueError(f"quote {quote_id} not found")
    if quote.round_id != round_id:
        raise ValueError(
            f"quote {quote_id} belongs to round {quote.round_id}, not round {round_id} — "
            "a quote can only be awarded on the round it was quoted for"
        )
    if quote.voided:
        raise ValueError(f"quote {quote_id} is voided and cannot be awarded")
    if quote.superseded_by_quote_id:
        raise ValueError(
            f"quote {quote_id} has been superseded by quote {quote.superseded_by_quote_id} — "
            "award the current version instead"
        )
    snapshot = round_compare(access, round_id, quote.commodity_slug)
    return record(
        access.create_award(
            {
                "round_id": round_id,
                "quote_id": quote_id,
                "rationale": rationale,
                "decided_by": decided_by,
                "comparison_snapshot": snapshot,
            }
        )
    )


@register_operation(
    name="award_list",
    summary="List awards for this programme, each with its frozen comparison snapshot and rationale.",
    input_schema=obj({"round_id": ID}),
)
def award_list(access, round_id=None):
    return [record(a) for a in access.list_awards(round_id=round_id)]


# ---- purchases ---------------------------------------------------------


@register_operation(
    name="purchase_record",
    summary=(
        "Record what an LLO actually paid — the fact behind cost per course, " "as opposed to the quoted intention."
    ),
    input_schema=obj({"data": _PURCHASE_DATA}, required=("data",)),
    is_write=True,
)
def purchase_record(access, data):
    return record(access.create_purchase(data))


@register_operation(
    name="purchase_list",
    summary="List recorded purchases for this programme.",
    input_schema=obj({}),
)
def purchase_list(access):
    return [record(p) for p in access.list_purchases()]
