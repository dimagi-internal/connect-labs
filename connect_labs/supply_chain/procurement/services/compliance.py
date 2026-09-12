"""Check a quote's stated specification against the commodity's requirements.

`not_stated` is deliberately distinct from `fail`: a supplier who did not
answer has not failed, they have left a question open, and questions.py
turns that into the follow-up email.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from connect_labs.supply_chain.models import Commodity, Quote

OPERATORS = {
    "<=": lambda stated, required: stated <= required,
    ">=": lambda stated, required: stated >= required,
    "==": lambda stated, required: stated == required,
    "<": lambda stated, required: stated < required,
    ">": lambda stated, required: stated > required,
}

PASS = "pass"
FAIL = "fail"
NOT_STATED = "not_stated"


@dataclass(frozen=True)
class RequirementResult:
    field: str
    outcome: str
    requirement: dict
    stated_value: object
    message: str
    spec_origin: str = "none"
    claim_conflict: bool = False


def _as_decimal(raw):
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _resolve(field, quote: Quote, item):
    """(value, origin, conflict) for one requirement field.

    The item's specification sheet wins over the supplier's claim: one is a
    durable property of a product, the other is what somebody wrote in an email.
    A disagreement between them is not swallowed — it is reported, because a
    supplier claiming a specification the sheet contradicts is worth a look.
    """
    item_value = item.spec_attributes.get(field) if item is not None else None
    quote_value = quote.stated_spec.get(field)

    if item_value is not None:
        conflict = quote_value is not None and _as_decimal(quote_value) != _as_decimal(item_value)
        return item_value, "item", conflict
    if quote_value is not None:
        return quote_value, "quote", False
    return None, "none", False


def check_compliance(
    quote: Quote,
    commodity: Commodity,
    item=None,
) -> list[RequirementResult]:
    results: list[RequirementResult] = []

    for requirement in commodity.spec_requirements:
        field = requirement.get("field")
        operator = requirement.get("operator")
        if operator not in OPERATORS:
            raise ValueError(
                f"unknown spec operator {operator!r} on {commodity.slug}.{field}; "
                f"expected one of {sorted(OPERATORS)}"
            )

        unit = requirement.get("unit", "")
        required = _as_decimal(requirement.get("value"))
        raw_value, origin, conflict = _resolve(field, quote, item)
        stated = _as_decimal(raw_value)

        if stated is None:
            results.append(
                RequirementResult(
                    field=field,
                    outcome=NOT_STATED,
                    requirement=requirement,
                    stated_value=raw_value,
                    message=f"{field} not stated; requirement is {operator} {required} {unit}".strip(),
                    spec_origin=origin,
                )
            )
            continue

        suffix = " (per the item specification)" if origin == "item" else ""
        if conflict:
            suffix += "; the quote claims a different value"

        if OPERATORS[operator](stated, required):
            results.append(
                RequirementResult(
                    field=field,
                    outcome=PASS,
                    requirement=requirement,
                    stated_value=stated,
                    message=(f"{field} {stated} {unit} meets {operator} {required} {unit}".strip() + suffix),
                    spec_origin=origin,
                    claim_conflict=conflict,
                )
            )
        else:
            rationale = requirement.get("rationale", "")
            message = f"{field} {stated} {unit} fails {operator} {required} {unit}".strip() + suffix
            if rationale:
                message = f"{message} — {rationale}"
            results.append(
                RequirementResult(
                    field=field,
                    outcome=FAIL,
                    requirement=requirement,
                    stated_value=stated,
                    message=message,
                    spec_origin=origin,
                    claim_conflict=conflict,
                )
            )

    return results


def spec_verdict(item_spec_attributes: dict | None, spec_requirements: list[dict]) -> str:
    """A short pass/fail/not_stated summary for the item master screen's
    "spec verdict" column (design doc section 12, finding 15).

    No quote is in scope on that screen -- an item's own specification
    sheet is checked against the commodity's requirements directly, using
    the same OPERATORS this module already evaluates check_compliance's
    quote-vs-requirement comparisons with, so the pass/fail rule is written
    once. Unlike check_compliance, an unrecognised operator here is folded
    into "not stated" rather than raised: this renders a table cell on a
    read-only registry page, and a bad operator on one commodity's spec
    should not 500 the whole item list.
    """
    if not spec_requirements:
        return "No requirements"

    attributes = item_spec_attributes or {}
    outcomes: list[str] = []
    for requirement in spec_requirements:
        operator = requirement.get("operator")
        if operator not in OPERATORS:
            outcomes.append(NOT_STATED)
            continue
        required = _as_decimal(requirement.get("value"))
        stated = _as_decimal(attributes.get(requirement.get("field")))
        if stated is None:
            outcomes.append(NOT_STATED)
        elif OPERATORS[operator](stated, required):
            outcomes.append(PASS)
        else:
            outcomes.append(FAIL)

    if FAIL in outcomes:
        return f"{outcomes.count(FAIL)} of {len(outcomes)} fail"
    if NOT_STATED in outcomes:
        return f"{outcomes.count(NOT_STATED)} of {len(outcomes)} not stated"
    return f"Meets all {len(outcomes)}"
