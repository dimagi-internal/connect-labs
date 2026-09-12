"""What a quote is still missing — the single source for every question we ask.

Read in three directions (design doc section 7):
  initial_request_facts -> the RFQ for a round, where nothing is known yet
  missing_facts         -> the follow-up to a supplier who answered partly
  missing_facts         -> the outstanding-questions panel on the comparison

Because the same function decides all three, standardising the request and
normalising the reply cannot drift apart: add a field to the quote record and
the next request asks for it; drop a question from the request and it shows
up as a systematic Unconfirmed.

Two classes of fact are missing, and both matter:
  - comparability facts — price, unit, pack spec, quantity basis, freight,
    duties, FX rate, unit weight. Without these `pricing.py` cannot derive a
    number at all.
  - acceptance facts — shelf life, MOQ, lead time, quote validity. These
    block nothing in `pricing.py`, but they decide whether an otherwise
    fully-priced offer is actually usable, and a supplier silent on them is
    exactly who needs chasing in a follow-up.

Every fact also has an `audience`: most are questions for the supplier, but
a few name something only we can fix (our own missing programme data, a
misconfigured round, a bad enum value in our own record of a quote). Those
still get named — nothing is ever silently dropped — but tagged
`audience="internal"` so nothing addressed to us ever gets emailed to a
supplier by mistake. `render_followup` (services/render.py) filters on
`audience == "supplier"` before composing the email; the comparison screen
can show both.
"""

import logging
from dataclasses import dataclass

from connect_labs.supply_chain.models import CommodityRecord, QuoteRecord, RoundRecord
from connect_labs.supply_chain.procurement.services.compliance import NOT_STATED, check_compliance
from connect_labs.supply_chain.procurement.services.pricing import compute_figures
from connect_labs.supply_chain.values import Unconfirmed

logger = logging.getLogger(__name__)

SUPPLIER = "supplier"
INTERNAL = "internal"


@dataclass(frozen=True)
class MissingFact:
    key: str
    question: str
    audience: str = SUPPLIER


# Maps a fragment of a pricing Unconfirmed reason to (key, question, audience).
# Ordered: the first matching rule wins, so more specific fragments come
# before more general ones ("quantity basis" before "round is", both of which
# can appear in the same reason string).
#
# Every Unconfirmed reason pricing.py can produce is matched below — see
# test_questions.py's completeness check, which walks a set of deliberately
# broken quotes and asserts every reason they produce matches a fragment
# here. A reason that named a real gap but matched nothing would silently
# vanish, which is the exact failure this module exists to prevent — so
# three of the rows below are `audience="internal"` rather than omitted:
# the fact they name is real, it just is not the supplier's to give.
_REASON_QUESTIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "pack spec",
        "pack_spec",
        "How many {base_unit}s are in one {pack_unit}, and what is the weight of each {base_unit}?",
        SUPPLIER,
    ),
    (
        "unit weight",
        "unit_weight",
        "What is the weight of one {base_unit}, in grams?",
        SUPPLIER,
    ),
    (
        "freight",
        "freight_basis",
        "Does the price include freight to {destination}? If not, what is the freight charge?",
        SUPPLIER,
    ),
    (
        "duties",
        "duties_basis",
        "Does the price include import duties and taxes at {destination}? If not, what are they?",
        SUPPLIER,
    ),
    (
        "exchange rate",
        "fx_rate",
        "Can you confirm the price in USD, or the exchange rate the quote assumes?",
        SUPPLIER,
    ),
    (
        "course definition",
        "course_definition",
        "Enter the treatment protocol for {commodity} ({base_unit}s per day and days per course) "
        "in the commodity record — a per-course cost cannot be computed until then.",
        INTERNAL,
    ),
    (
        "quantity basis",
        "quantity_basis",
        "What quantity does this price cover?",
        SUPPLIER,
    ),
    (
        "round is",
        "quantity_basis",
        "Can you quote for {quantity} {quantity_unit} specifically?",
        SUPPLIER,
    ),
    (
        "no amount recorded",
        "amount",
        "What is the price per {pack_unit}?",
        SUPPLIER,
    ),
    (
        "no line for",
        "round_configuration",
        "This round has no line for {commodity} — add one before requesting or comparing quotes.",
        INTERNAL,
    ),
    (
        "as-quoted unit",
        "as_quoted_unit",
        "The as-quoted unit recorded on this quote is not one of the recognised bases — " "correct the quote record.",
        INTERNAL,
    ),
)

_QUESTION_BY_KEY: dict[str, str] = {key: template for _, key, template, _audience in _REASON_QUESTIONS}

# Acceptance facts: nothing in pricing.py or compliance.py ever blocks a
# figure on these, so they are checked directly against the quote itself
# rather than discovered as a pricing/compliance side effect. Each fires
# independently — a quote can state its price and pack spec perfectly and
# still be missing all four of these.
_ACCEPTANCE_CHECKS: tuple[tuple[str, str], ...] = (
    ("shelf_life", "shelf_life_months_stated"),
    ("lead_time", "lead_time_days"),
    ("validity", "validity_until"),
)

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

_ALWAYS_ASKED_BY_KEY: dict[str, str] = dict(_ALWAYS_ASKED)


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


def _fact(key: str, template: str, context: dict, audience: str = SUPPLIER) -> MissingFact:
    return MissingFact(key=key, question=template.format(**context), audience=audience)


def _spec_fact(field_name: str, requirement: dict) -> MissingFact:
    """The one question text for an unstated spec requirement.

    Shared by missing_facts (a specific quote left it unanswered) and
    initial_request_facts (nobody has answered anything yet) so the wording
    of a spec question is written exactly once, not duplicated between them.
    """
    operator_phrases = {
        "<=": "no more than",
        ">=": "at least",
        "==": "exactly",
        "<": "less than",
        ">": "more than",
    }
    phrase = operator_phrases.get(requirement.get("operator"), requirement.get("operator"))
    amount = f"{requirement.get('value')} {requirement.get('unit') or ''}".strip()
    return MissingFact(
        key=f"spec:{field_name}",
        question=(
            f"What is the {field_name.replace('_', ' ')} of the item you would supply? "
            f"We require {phrase} {amount}."
        ),
    )


def missing_facts(
    quote: QuoteRecord,
    commodity: CommodityRecord,
    round_: RoundRecord,
    item=None,
) -> list[MissingFact]:
    """Every fact still needed before this quote could be compared honestly.

    Three sources, deduplicated by key so a fact that blocks several figures
    at once is still named once:
      - pricing's Unconfirmed reasons — a figure could not be derived;
      - compliance's not_stated results — a requirement was never addressed;
      - the acceptance facts (shelf life, MOQ, lead time, validity) checked
        directly, since no derived figure depends on them.

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
        for fragment, key, template, audience in _REASON_QUESTIONS:
            if fragment in lowered:
                if key not in seen:
                    seen.add(key)
                    facts.append(_fact(key, template, context, audience=audience))
                break
        else:
            # A reason pricing.py can produce that names no fact in the table
            # above: a real gap that would otherwise vanish with nobody ever
            # told. This should be unreachable — test_questions.py's
            # completeness check walks every reason pricing.py can currently
            # produce — so reaching it means a new Unconfirmed() call was
            # added to pricing.py without a matching row here.
            logger.warning("questions.missing_facts: unmapped Unconfirmed reason: %r", reason)

    for result in check_compliance(quote, commodity, item=item):
        if result.outcome == NOT_STATED:
            key = f"spec:{result.field}"
            if key not in seen:
                seen.add(key)
                facts.append(_spec_fact(result.field, result.requirement))

    for key, attr in _ACCEPTANCE_CHECKS:
        if key not in seen and getattr(quote, attr) is None:
            seen.add(key)
            facts.append(_fact(key, _ALWAYS_ASKED_BY_KEY[key], context))

    if "moq" not in seen and (quote.moq is None or quote.moq_unit is None):
        seen.add("moq")
        facts.append(_fact("moq", _ALWAYS_ASKED_BY_KEY["moq"], context))

    return facts


def initial_request_facts(
    commodity: CommodityRecord,
    round_: RoundRecord,
) -> list[MissingFact]:
    """Everything a supplier must answer for a round, before any quote exists.

    Shares its question text with missing_facts (via _QUESTION_BY_KEY and
    _spec_fact) so the two directions of the same schema cannot drift, and
    always includes the acceptance facts (shelf life, MOQ, lead time,
    validity) up front.
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
        req_field = requirement.get("field")
        key = f"spec:{req_field}"
        if key not in seen:
            seen.add(key)
            facts.append(_spec_fact(req_field, requirement))

    return facts
