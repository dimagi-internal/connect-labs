"""Money, the honest-absence type, and the unit ladder.

Pure Python by design: these are the primitives every pricing rule is built
from, and keeping Django out of here keeps the rules cheap to test.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

# The scale money is stored at (models.MONEY), applied to DERIVED figures on
# their way out. Dividing a price by a pack size is the commonest derivation
# here, and unquantized a $50 carton at 150 to the carton reports
# 0.33333333333333333333333333 USD per sachet -- 26 digits off a two-decimal
# input, asserting a precision nobody has.
#
# Rounded at the boundary rather than at the division, because a figure here
# is often the input to the next one: quantizing the per-sachet price made
# cost per course 49.9950 instead of 50.00, since per course is per sachet
# times the ration. Internal arithmetic keeps full precision; only the
# published number is rounded, and at four decimal places the resolution is a
# hundredth of a cent -- finer than any procurement decision, and the same
# scale the column would hold.
MONEY_SCALE = Decimal("0.0001")


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = "USD"


@dataclass(frozen=True)
class Quantity:
    """An amount of something, carrying the unit it was stated in.

    The unit travels with the number because the domain's central failure is
    a quantity that got added to another quantity in a different unit. A
    bare Decimal invites exactly that; a Quantity cannot be summed with a
    mismatched one without someone deciding how, which is the decision this
    type exists to force into the open.
    """

    amount: Decimal
    # None where a zero balance's unit is genuinely unknown: no movements to
    # read one from, no item stating a pack unit, and no caller asking for
    # one. Naming a unit there would invent a fact.
    unit: str | None


@dataclass(frozen=True)
class Unconfirmed:
    """A figure that cannot be computed honestly, and why.

    The reasons are user-facing: they become the questions sent back to the
    supplier, so they name the missing fact rather than the failed calculation.
    """

    reasons: tuple[str, ...]


@dataclass(frozen=True)
class NotCosted:
    """A cost that does not exist, as distinct from one nobody has supplied.

    Goods donated in kind, or paid for inside a setup fee, have no price and
    never will. `Unconfirmed` would say a fact is missing and send somebody to
    chase it; this says there is nothing to chase, and why. It is never
    summed and never ranked -- a derivation that meets one does not become a
    number, it carries the statement through.
    """

    reason: str


@dataclass(frozen=True)
class NotForecast:
    """A consumption figure that does not apply, rather than one that is missing.

    A dispenser is held and moved but never consumed, so its "months of
    stock" is not unknown -- it is not a quantity at all. Reporting it as
    `Unconfirmed` would send somebody looking for consumption data that will
    never exist; reporting a number would invent one.
    """

    reason: str


Derived = Money | Unconfirmed
DerivedQuantity = Quantity | Unconfirmed


def unconfirmed(*reasons: str) -> Unconfirmed:
    return Unconfirmed(reasons=tuple(reasons))


def decimal_string(value) -> str:
    """A Decimal written for the wire, without the storage scale showing.

    The single source for how a number is spelled in JSON, because there are
    two producers -- `to_wire` for derived figures and `serializers` for
    stored columns -- and a quantity that reads "288" from one and "288.0000"
    from the other is the same value described two ways. A consumer comparing
    them is then wrong for a reason it cannot see.

    Always a string, never a float: a float is how a monetary amount silently
    loses the precision the input schemas refuse to accept in the first place.
    """
    if not isinstance(value, Decimal):
        return str(value)
    trimmed = value.normalize()
    if trimmed == trimmed.to_integral_value():
        # normalize() renders whole numbers in exponent form (2E+2).
        trimmed = trimmed.quantize(Decimal("1"))
    return str(trimmed)


def to_wire(value: Derived | DerivedQuantity) -> dict:
    """The one JSON wire shape for a derived figure.

    {"amount", "currency"} for a Money, {"amount", "unit"} for a Quantity,
    {"unconfirmed": [...]} for an Unconfirmed -- the contract every consumer reads via cell.amount /
    cell.unconfirmed in a template. operations.figure() and
    Comparison.to_snapshot()'s cell() used to each define these same four
    lines separately; single-sourced here so the two producers cannot drift
    apart on what a figure looks like on the wire.
    """
    if isinstance(value, Money):
        return {"amount": decimal_string(value.amount.quantize(MONEY_SCALE)), "currency": value.currency}
    if isinstance(value, Quantity):
        return {"amount": decimal_string(value.amount), "unit": value.unit}
    if isinstance(value, NotCosted):
        return {"not_costed": value.reason}
    if isinstance(value, NotForecast):
        return {"not_forecast": value.reason}
    return {"unconfirmed": list(value.reasons)}


def merge(*items: Derived) -> Unconfirmed | None:
    """Combine the reasons from any unconfirmed inputs, preserving order.

    Returns None when every input is confirmed, so callers read as:

        blocked = merge(rate, pack_spec)
        if blocked:
            return blocked
    """
    reasons: list[str] = []
    for item in items:
        if isinstance(item, Unconfirmed):
            for reason in item.reasons:
                if reason not in reasons:
                    reasons.append(reason)
    return Unconfirmed(reasons=tuple(reasons)) if reasons else None


_ConfirmedT = TypeVar("_ConfirmedT")


def confirmed(value: _ConfirmedT) -> _ConfirmedT:
    """Mark an already-confirmed plain value as fit to pass into merge().

    merge() only ever inspects its arguments for `Unconfirmed`; every other value
    — an int, a Decimal, a domain value a caller has already checked — is silently
    treated as confirmed and contributes no reasons. That means this is the
    identity function at runtime. It exists so a call site can write
    `merge(rate, confirmed(pack_spec))` and say what it means, instead of a
    `pack_spec if isinstance(pack_spec, int) else Money(Decimal("0"))` guard whose
    only job was to hand merge() a throwaway stand-in.
    """
    return value


def _require_positive(value, name: str) -> None:
    """The one positivity guard both unit-ladder conversions below need --
    each used to carry its own copy of this raise (finding 21)."""
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def packs_to_base_units(packs: Decimal, base_per_pack: int) -> Decimal:
    _require_positive(base_per_pack, "base_per_pack")
    return packs * Decimal(base_per_pack)


def metric_tonnes_to_base_units(tonnes: Decimal, base_unit_grams: int) -> Decimal:
    _require_positive(base_unit_grams, "base_unit_grams")
    grams = tonnes * Decimal("1000000")
    return (grams / Decimal(base_unit_grams)).to_integral_value(rounding="ROUND_DOWN")


def _plural_unit(unit: str, count) -> str:
    """Naive English pluralisation for a unit noun (carton, sachet, tonne, ...).

    Handles count == 1 so a single-carton round reads "carton", not "1
    cartons". No unit in this domain's vocabulary takes an irregular plural.
    Private: composed into quantity_phrase() below rather than called on its
    own, so a quantity's digits and its unit noun cannot drift apart into two
    separately-formatted pieces.
    """
    return unit if count == 1 else f"{unit}s"


def _format_quantity(quantity) -> str:
    """A quantity's digits written the way commercial correspondence writes
    them, e.g. 2000 -> "2,000". Private for the same reason as _plural_unit."""
    return f"{quantity:,}"


def destination_phrase(delivery_point: dict | None) -> str:
    """A round's delivery point as one piece of prose: name, city, country.

    Single-sourced for the same reason quantity_phrase() is (see its own
    docstring): render.py's _destination() and questions.py's _context()
    each built this from the same delivery_point dict at their own call
    site, and their comments both claimed to apply the same preference
    (human-typed country name over the bare ISO code) -- but render.py also
    included delivery_point.name and questions.py did not, so one RFQ read
    "delivered to Central store, Kano, Nigeria" in its opening line and then
    "freight to Kano, Nigeria" three times in the questions that followed.
    """
    point = delivery_point or {}
    country = point.get("country_name") or point.get("country")
    parts = [point.get("name"), point.get("city"), country]
    text = ", ".join(part for part in parts if part)
    return text or "the delivery point"


def quantity_phrase(count, unit: str) -> str:
    """A quantity and its unit as one piece of prose: quantity_phrase(2000,
    "carton") -> "2,000 cartons"; quantity_phrase(1, "carton") -> "1 carton".

    The single source for how a quantity is written in a sentence. An earlier
    version had render.py and questions.py each call plural_unit() and
    format_quantity() separately at their own call sites — two coordinated
    calls instead of one, which is exactly the shape that let the digits and
    the unit noun be formatted consistently with each other but drift out of
    step between the two messages (one showing "2,000", the other "2000").
    Composing both here means there is nothing left for a caller to get
    inconsistent.
    """
    return f"{_format_quantity(count)} {_plural_unit(unit, count)}"
