"""Money, the honest-absence type, and the unit ladder.

Pure Python by design: these are the primitives every pricing rule is built
from, and keeping Django out of here keeps the rules cheap to test.
"""

from dataclasses import dataclass
from decimal import Decimal


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


def packs_to_base_units(packs: Decimal, base_per_pack: int) -> Decimal:
    if base_per_pack <= 0:
        raise ValueError("base_per_pack must be positive")
    return packs * Decimal(base_per_pack)


def metric_tonnes_to_base_units(tonnes: Decimal, base_unit_grams: int) -> Decimal:
    if base_unit_grams <= 0:
        raise ValueError("base_unit_grams must be positive")
    grams = tonnes * Decimal("1000000")
    return (grams / Decimal(base_unit_grams)).to_integral_value(rounding="ROUND_DOWN")
