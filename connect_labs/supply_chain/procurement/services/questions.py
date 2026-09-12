"""What a quote is still missing — the single source for every question we ask.

Read in three directions (design doc section 7):
  initial_request_facts -> the RFQ for a round, where nothing is known yet
  missing_facts         -> the follow-up to a supplier who answered partly
  missing_facts         -> the outstanding-questions panel on the comparison

Because the same function decides all three, standardising the request and
normalising the reply cannot drift apart: add a field to the quote record and
the next request asks for it; drop a question from the request and it shows
up as a systematic Unconfirmed.

Every question below is product copy addressed to an external supplier — it
must read as a fact they can actually supply. Some Unconfirmed reasons from
pricing.py name a fact that is *not* a supplier's to give (the programme's own
treatment protocol, a round misconfigured with no line for a commodity, an
invalid as-quoted-unit value someone typed into our own records). Those are
deliberately left unmapped in `_REASON_QUESTIONS`: an unmatched reason is
silently dropped rather than turned into a question nobody outside could
possibly answer. See the note above `_REASON_QUESTIONS` for the full list.
"""

from dataclasses import dataclass

from connect_labs.supply_chain.models import CommodityRecord, QuoteRecord, RoundRecord
from connect_labs.supply_chain.procurement.services.compliance import NOT_STATED, check_compliance
from connect_labs.supply_chain.procurement.services.pricing import compute_figures
from connect_labs.supply_chain.values import Unconfirmed


@dataclass(frozen=True)
class MissingFact:
    key: str
    question: str


# Maps a fragment of a pricing Unconfirmed reason to the question that
# resolves it. Ordered: the first matching rule wins, so more specific
# fragments come before more general ones ("quantity basis" before "round
# is", both of which can appear in the same reason string).
#
# Three of pricing.py's Unconfirmed reasons are intentionally NOT mapped here,
# because none names a fact a supplier could ever supply:
#   - "no course definition set for ..." — the treatment protocol behind a
#     per-course cost is programme data, decided internally.
#   - "this round has no line for ..." — a round misconfiguration, not
#     something asking the supplier could fix.
#   - "as-quoted unit ... is not recognised" — an invalid enum value in our
#     own record of the quote, not a fact the supplier failed to state.
# Every other Unconfirmed reason pricing.py can produce is matched below;
# see test_questions.py for the check that pins the case Task 4 added
# (per-metric-tonne unit weight) so a real gap doesn't silently disappear.
_REASON_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "pack spec",
        "pack_spec",
        "How many {base_unit}s are in one {pack_unit}, and what is the weight of each {base_unit}?",
    ),
    (
        "unit weight",
        "unit_weight",
        "What is the weight of one {base_unit}, in grams?",
    ),
    (
        "freight",
        "freight_basis",
        "Does the price include freight to {destination}? If not, what is the freight charge?",
    ),
    (
        "duties",
        "duties_basis",
        "Does the price include import duties and taxes at {destination}? If not, what are they?",
    ),
    (
        "exchange rate",
        "fx_rate",
        "Can you confirm the price in USD, or the exchange rate the quote assumes?",
    ),
    (
        "quantity basis",
        "quantity_basis",
        "What quantity does this price cover?",
    ),
    (
        "round is",
        "quantity_basis",
        "Can you quote for {quantity} {quantity_unit} specifically?",
    ),
    (
        "no amount recorded",
        "amount",
        "What is the price per {pack_unit}?",
    ),
)

_QUESTION_BY_KEY: dict[str, str] = {key: template for _, key, template in _REASON_QUESTIONS}

# Facts no derived figure ever blocks on, so pricing/compliance never flag
# their absence — asked only in the initial request, never re-asked in a
# missing_facts follow-up.
_ALWAYS_ASKED: tuple[tuple[str, str], ...] = (
    (
        "shelf_life",
        "What is the shelf life from date of manufacture, and the production date "
        "of the batch you would supply? We need at least {shelf_life} months.",
    ),
    ("moq", "What is your minimum order quantity?"),
    ("lead_time", "How many days from order to delivery at {destination}?"),
    ("validity", "How long is this quotation valid?"),
)

_OPERATOR_PHRASES = {
    "<=": "no more than",
    ">=": "at least",
    "==": "exactly",
    "<": "less than",
    ">": "more than",
}


def _context(commodity: CommodityRecord, round_: RoundRecord) -> dict:
    destination = round_.delivery_point or {}
    where = ", ".join(part for part in (destination.get("city"), destination.get("country")) if part)
    quantity = round_.quantity_for(commodity.slug)
    return {
        "base_unit": commodity.base_unit or "unit",
        "pack_unit": commodity.pack_unit or "pack",
        "commodity": commodity.name or commodity.slug,
        "destination": where or "the delivery point",
        "quantity": quantity[0] if quantity else "",
        "quantity_unit": quantity[1] if quantity else "",
        "shelf_life": round_.shelf_life_months_minimum or commodity.shelf_life_months_minimum or "",
    }


def _fact(key: str, template: str, context: dict) -> MissingFact:
    return MissingFact(key=key, question=template.format(**context))


def _spec_fact(field: str, requirement: dict) -> MissingFact:
    """The one question text for an unstated spec requirement.

    Shared by missing_facts (a specific quote left it unanswered) and
    initial_request_facts (nobody has answered anything yet) so the wording
    of a spec question is written exactly once, not duplicated between them.
    """
    phrase = _OPERATOR_PHRASES.get(requirement.get("operator"), requirement.get("operator"))
    amount = f"{requirement.get('value')} {requirement.get('unit') or ''}".strip()
    return MissingFact(
        key=f"spec:{field}",
        question=(
            f"What is the {field.replace('_', ' ')} of the item you would supply? " f"We require {phrase} {amount}."
        ),
    )


def missing_facts(
    quote: QuoteRecord,
    commodity: CommodityRecord,
    round_: RoundRecord,
    item=None,
) -> list[MissingFact]:
    """Every fact still needed before this quote could be compared honestly.

    Derived from exactly two sources — pricing's Unconfirmed reasons (a
    figure could not be derived) and compliance's not_stated results (a
    requirement was never addressed) — deduplicated by key so a fact that
    blocks several figures at once is still named once.

    `item` is threaded through to pricing and compliance rather than
    consulted here: a supplier who confirmed a trade item has already
    answered the pack-spec question, so no question is generated for it.
    """
    context = _context(commodity, round_)
    facts: list[MissingFact] = []
    seen: set[str] = set()

    figures = compute_figures(quote, commodity, round_, item=item)
    reasons: list[str] = []
    for figure in figures.as_dict().values():
        if isinstance(figure, Unconfirmed):
            reasons.extend(figure.reasons)

    for reason in reasons:
        lowered = reason.lower()
        for fragment, key, template in _REASON_QUESTIONS:
            if fragment in lowered:
                if key not in seen:
                    seen.add(key)
                    facts.append(_fact(key, template, context))
                break

    for result in check_compliance(quote, commodity, item=item):
        if result.outcome == NOT_STATED:
            key = f"spec:{result.field}"
            if key not in seen:
                seen.add(key)
                facts.append(_spec_fact(result.field, result.requirement))

    return facts


def initial_request_facts(
    commodity: CommodityRecord,
    round_: RoundRecord,
) -> list[MissingFact]:
    """Everything a supplier must answer for a round, before any quote exists.

    Shares its question text with missing_facts (via _QUESTION_BY_KEY and
    _spec_fact) so the two directions of the same schema cannot drift, and
    adds the facts (shelf life, MOQ, lead time, validity) that no derived
    figure ever blocks on — those go out only here, never in a follow-up.
    """
    context = _context(commodity, round_)
    seen: set[str] = set()
    facts: list[MissingFact] = []

    for key, template in (
        ("amount", _QUESTION_BY_KEY["amount"]),
        ("pack_spec", _QUESTION_BY_KEY["pack_spec"]),
        ("quantity_basis", "Can you quote for {quantity} {quantity_unit}?"),
        ("freight_basis", _QUESTION_BY_KEY["freight_basis"]),
        ("duties_basis", _QUESTION_BY_KEY["duties_basis"]),
    ):
        seen.add(key)
        facts.append(_fact(key, template, context))

    for key, template in _ALWAYS_ASKED:
        if key not in seen:
            seen.add(key)
            facts.append(_fact(key, template, context))

    for requirement in commodity.spec_requirements:
        field = requirement.get("field")
        key = f"spec:{field}"
        if key not in seen:
            seen.add(key)
            facts.append(_spec_fact(field, requirement))

    return facts
