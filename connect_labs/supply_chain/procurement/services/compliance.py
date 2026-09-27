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

# How a requirement reads to a person. The stored operator is for the check;
# nobody buying test kits should have to read ">=".
OPERATOR_WORDS = {
    "<=": "at most",
    ">=": "at least",
    "==": "exactly",
    "<": "less than",
    ">": "more than",
}
_FIELD_WORDS = {"max": "maximum", "min": "minimum"}

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


def requirement_label(field, unit="") -> str:
    """A stored field name as words: `range_max_mg_per_l` with unit mg/L is
    "Range maximum" -- the unit is said once, beside the number, not twice."""
    words = (field or "").lower().split("_")
    unit_words = (unit or "").lower().replace("/", " per ").split()
    if unit_words and words[-len(unit_words) :] == unit_words and len(words) > len(unit_words):
        words = words[: -len(unit_words)]
    return " ".join(_FIELD_WORDS.get(word, word) for word in words if word).capitalize()


# The unit a figure's name ends in, as it is written beside a number. A
# figure called `range_max_mg_per_l` is stated in mg/L; saying so lets the
# requirement editor fill the unit rather than ask for it.
_UNIT_SUFFIXES = (
    ("mg_per_l", "mg/L"),
    ("ppm", "ppm"),
    ("months", "months"),
    ("days", "days"),
    ("pct", "%"),
    ("percent", "%"),
    ("mg", "mg"),
    ("kg", "kg"),
    ("ml", "mL"),
    ("g", "g"),
)


def _split_unit(field) -> tuple[str, str]:
    """(the figure's name without its unit, the unit): ("range_max", "mg/L")."""
    name = (field or "").lower()
    for suffix, unit in _UNIT_SUFFIXES:
        if name.endswith(f"_{suffix}"):
            return name[: -len(suffix) - 1], unit
    return name, ""


def figure_name(field) -> str:
    """A figure's name without the unit it ends in: `moisture_pct` -> "moisture"."""
    return _split_unit(field)[0]


def figure_unit(field) -> str:
    """The unit a figure's name says it is stated in: `range_max_mg_per_l` -> "mg/L", else ""."""
    return _split_unit(field)[1]


def figure_label(field) -> str:
    """A figure's name as a person picks it from a list: "Range maximum (mg/L)", "Tests per kit"."""
    name, unit = _split_unit(field)
    label = requirement_label(name)
    return f"{label} ({unit})" if unit else label


def requirement_text(requirement: dict) -> str:
    """One requirement as a sentence fragment: "Range maximum at least 2.0 mg/L"."""
    operator = requirement.get("operator")
    unit = requirement.get("unit", "")
    words = OPERATOR_WORDS.get(operator, operator or "")
    return f"{requirement_label(requirement.get('field'), unit)} {words} {requirement.get('value')} {unit}".strip()


# How each operator reads after a requirement's name on a listing: "Sachets
# per carton: 150 (exact)", "Shelf life: at least 18 months".
_LINE_WORDS = {">=": "at least {}", "<=": "at most {}", ">": "more than {}", "<": "less than {}", "==": "{} (exact)"}


def requirement_line(requirement: dict) -> str:
    """One requirement as a line of a request: "Sachets per carton: 150 (exact)"."""
    unit = requirement.get("unit") or ""
    figure = f"{requirement.get('value')} {unit}".strip()
    template = _LINE_WORDS.get(requirement.get("operator"))
    said = template.format(figure) if template else f"{requirement.get('operator') or ''} {figure}".strip()
    return f"{requirement_label(figure_name(requirement.get('field')), unit)}: {said}"


def _as_decimal(raw):
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


# Requirement names that ask for a figure the quote and the trade item also
# carry in their own columns. A supplier who wrote "150 sachets per carton" on
# the quote has answered "sachets per carton", however the figure was typed in.
SHELF_LIFE_FIELDS = frozenset({"shelf_life_months", "shelf_life"})


def pack_count_fields(commodity) -> frozenset:
    """The requirement names that mean "base units per pack" for this commodity:
    `sachets_per_carton` for RUTF, and the generic `units_per_pack`."""
    names = {"base_per_pack", "units_per_pack"}
    base = (getattr(commodity, "base_unit", "") or "").lower()
    pack = (getattr(commodity, "pack_unit", "") or "").lower()
    if base and pack:
        names |= {f"{base}s_per_{pack}", f"{base}_per_{pack}"}
    return frozenset(names)


def is_pack_count_field(field, commodity) -> bool:
    return (field or "").lower() in pack_count_fields(commodity)


def is_shelf_life_field(field) -> bool:
    return (field or "").lower() in SHELF_LIFE_FIELDS


def _item_column(field, item, commodity):
    """A requirement the trade item's own columns answer (units per pack, shelf life)."""
    if item is None:
        return None
    if is_pack_count_field(field, commodity):
        return item.base_per_pack
    if is_shelf_life_field(field):
        return item.shelf_life_months
    return None


def _quote_column(field, quote, commodity):
    """A requirement the quote's own columns answer.

    Units per pack counts only when the quote says it was stated on the quote:
    a figure left behind by some other source is not the supplier's statement.
    """
    if is_pack_count_field(field, commodity):
        if getattr(quote, "pack_spec_source", "") == "stated_on_quote":
            return quote.base_per_pack_stated
        return None
    if is_shelf_life_field(field):
        return quote.shelf_life_months_stated
    return None


def _resolve(field, quote: Quote, item, commodity=None):
    """(value, origin, conflict) for one requirement field.

    The item's specification sheet wins over the supplier's claim: one is a
    durable property of a product, the other is what somebody wrote in an email.
    A disagreement between them is not swallowed — it is reported, because a
    supplier claiming a specification the sheet contradicts is worth a look.

    Each side is read from its specification first and then from its own
    columns: a quote that states 150 sachets per carton, or a shelf life, has
    answered the requirement that asks for it, and "not stated" beside a
    stated figure is the contradiction this exists to prevent.
    """
    item_value = item.spec_attributes.get(field) if item is not None else None
    if item_value is None:
        item_value = _item_column(field, item, commodity)
    quote_value = quote.stated_spec.get(field)
    if quote_value is None:
        quote_value = _quote_column(field, quote, commodity)

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
        raw_value, origin, conflict = _resolve(field, quote, item, commodity)
        stated = _as_decimal(raw_value)

        if stated is None:
            results.append(
                RequirementResult(
                    field=field,
                    outcome=NOT_STATED,
                    requirement=requirement,
                    stated_value=raw_value,
                    message=f"{requirement_label(field, unit)} not stated; it must be "
                    f"{OPERATOR_WORDS[operator]} {required} {unit}".strip(),
                    spec_origin=origin,
                )
            )
            continue

        suffix = {"item": " (per the item specification)", "quote": " (stated on the quote)"}.get(origin, "")
        if conflict:
            suffix += "; the quote claims a different value"

        if OPERATORS[operator](stated, required):
            results.append(
                RequirementResult(
                    field=field,
                    outcome=PASS,
                    requirement=requirement,
                    stated_value=stated,
                    message=(
                        f"{requirement_label(field, unit)} {stated} {unit} meets "
                        f"{OPERATOR_WORDS[operator]} {required} {unit}".strip() + suffix
                    ),
                    spec_origin=origin,
                    claim_conflict=conflict,
                )
            )
        else:
            rationale = requirement.get("rationale", "")
            message = (
                f"{requirement_label(field, unit)} {stated} {unit} fails: it must be "
                f"{OPERATOR_WORDS[operator]} {required} {unit}".strip() + suffix
            )
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
    return _summarise(_outcomes(item_spec_attributes, spec_requirements))


def failing_requirements(spec_attributes: dict | None, spec_requirements: list[dict]) -> list[str]:
    """Each requirement an item's own specification fails, as a sentence.

    "Range maximum 1.5 mg/L; it must be at least 2.0 mg/L". The checks page
    said "1 of 3 fail" and then printed every requirement as raw data, so the
    reader had to find the failing one themselves.
    """
    attributes = spec_attributes or {}
    out = []
    for requirement in spec_requirements or []:
        operator = requirement.get("operator")
        if operator not in OPERATORS:
            continue
        required = _as_decimal(requirement.get("value"))
        stated = _as_decimal(attributes.get(requirement.get("field")))
        if stated is None or OPERATORS[operator](stated, required):
            continue
        unit = requirement.get("unit", "")
        sentence = (
            f"{requirement_label(requirement.get('field'), unit)} {stated} {unit}; it must be "
            f"{OPERATOR_WORDS[operator]} {required} {unit}".strip()
        )
        if requirement.get("rationale"):
            sentence = f"{sentence} — {requirement['rationale']}"
        out.append(sentence)
    return out


def _outcomes(spec_attributes: dict | None, spec_requirements: list[dict]) -> list[str]:
    """pass / fail / not_stated for each requirement, in order."""
    attributes = spec_attributes or {}
    outcomes: list[str] = []
    for requirement in spec_requirements or []:
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
    return outcomes


def kit_spec_verdict(
    item_spec_attributes: dict | None,
    spec_requirements: list[dict],
    components: list[dict] | None,
    requirements_by_slug: dict[str, list[dict]],
) -> dict:
    """The verdict for a trade item that may be a kit, part by part.

    A co-pack is checked against the co-pack's own requirements AND each
    component against its own product's: the zinc inside is held to the zinc
    specification, because that is the product a child actually takes. The
    component's stated figures travel on the component
    (`spec_attributes`), not on the kit, so "zinc_mg" on a kit that holds
    two zinc formulations cannot be ambiguous.

    Returns {"verdict": <summary over every part>, "components": [{
    commodity_slug, verdict}]}. The summary uses `spec_verdict`'s wording, so
    a kit and an ordinary item read the same way on the same page. An item
    with no components gets an empty list and its ordinary verdict.
    """
    outcomes = _outcomes(item_spec_attributes, spec_requirements)
    parts = []
    for component in components or []:
        slug = component.get("commodity_slug")
        requirements = requirements_by_slug.get(slug) or []
        component_outcomes = _outcomes(component.get("spec_attributes"), requirements)
        outcomes.extend(component_outcomes)
        parts.append(
            {
                "commodity_slug": slug,
                "verdict": _summarise(component_outcomes) if requirements else "No requirements",
            }
        )
    return {"verdict": _summarise(outcomes) if outcomes else "No requirements", "components": parts}


def _summarise(outcomes: list[str]) -> str:
    if FAIL in outcomes:
        return f"{outcomes.count(FAIL)} of {len(outcomes)} fail"
    if NOT_STATED in outcomes:
        return f"{outcomes.count(NOT_STATED)} of {len(outcomes)} not stated"
    return f"Meets all {len(outcomes)}"
