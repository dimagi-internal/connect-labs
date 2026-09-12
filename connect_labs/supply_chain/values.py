"""Money, the honest-absence type, and the unit ladder.

Pure Python by design: these are the primitives every pricing rule is built
from, and keeping Django out of here keeps the rules cheap to test.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = "USD"


@dataclass(frozen=True)
class Unconfirmed:
    """A figure that cannot be computed honestly, and why.

    The reasons are user-facing: they become the questions sent back to the
    supplier, so they name the missing fact rather than the failed calculation.
    """

    reasons: tuple[str, ...]


Derived = Money | Unconfirmed


def unconfirmed(*reasons: str) -> Unconfirmed:
    return Unconfirmed(reasons=tuple(reasons))


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


def packs_to_base_units(packs: Decimal, base_per_pack: int) -> Decimal:
    if base_per_pack <= 0:
        raise ValueError("base_per_pack must be positive")
    return packs * Decimal(base_per_pack)


def metric_tonnes_to_base_units(tonnes: Decimal, base_unit_grams: int) -> Decimal:
    if base_unit_grams <= 0:
        raise ValueError("base_unit_grams must be positive")
    grams = tonnes * Decimal("1000000")
    return (grams / Decimal(base_unit_grams)).to_integral_value(rounding="ROUND_DOWN")


def plural_unit(unit: str, count) -> str:
    """Naive English pluralisation for a unit noun (carton, sachet, tonne, ...).

    One shared rule so a quantity's unit cannot be pluralised two different
    ways in the same message — procurement's render.py and questions.py both
    call this rather than each appending an "s" of its own. Handles count == 1
    so a single-carton round reads "carton", not "1 cartons". No unit in this
    domain's vocabulary takes an irregular plural.
    """
    return unit if count == 1 else f"{unit}s"


def format_quantity(quantity: Decimal) -> str:
    """A quantity written the way commercial correspondence writes it, e.g.
    2000 -> "2,000"."""
    return f"{quantity:,}"
