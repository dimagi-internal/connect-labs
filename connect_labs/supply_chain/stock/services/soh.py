"""Stock on hand: two answers, both kept.

A supply point has a *ledger balance* (what the movements say) and a *last
reported count* (what somebody says is actually there). These disagree
routinely, and the disagreement is the finding -- a system that silently
overwrites one with the other destroys the only signal it had.

So `stock_on_hand` returns both plus the variance, and says which basis a
consumer should plan on and why. The further down the network you go the more
the count matters: a worker's phone is not a warehouse system, and an
override is how reality gets in (see overrides in stock/services/counts.py).
"""

from connect_labs.supply_chain.models import StockCount
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.values import Quantity, Unconfirmed, unconfirmed


def last_count(program_id, supply_point, item=None, on_date=None):
    """The most recent count at this point, or None. Overrides do not win on
    kind -- only on recency, because an older override has been overtaken by
    a newer physical count as surely as by another override."""
    counts = StockCount.objects.filter(program_id=program_id, supply_point=supply_point)
    if item is not None:
        counts = counts.filter(item=item)
    if on_date:
        counts = counts.filter(counted_on__lte=on_date)
    return counts.order_by("-counted_on", "-id").first()


def stock_on_hand(program_id, supply_point, item=None, unit=None, on_date=None) -> dict:  # noqa: C901
    """{ledger, reported, variance, basis, as_of, reported_kind, reported_source}.

    `basis` names which figure a planner should use, and it is never a silent
    choice: `ledger` when no count exists, `count` when a count is more
    recent than the last movement it could have reflected, and
    `disagreement` when both exist and differ -- in which case the caller is
    told rather than handed one of them.
    """
    # Resolve the point's own item when the caller did not name one, so a
    # variance between a pack balance and a base-unit count is computed
    # rather than refused. See ledger.sole_item.
    item = item or ledger.sole_item(program_id, supply_point)
    balance = ledger.balance(program_id, supply_point, item=item, unit=unit, on_date=on_date)
    count = last_count(program_id, supply_point, item=item, on_date=on_date)

    if count is None:
        return {
            "ledger": balance,
            "reported": None,
            "variance": None,
            "basis": "ledger",
            "as_of": on_date,
            "reported_kind": None,
            "reported_source": None,
        }

    reported = Quantity(count.quantity, count.quantity_unit)
    variance = _variance(balance, reported, item)
    basis = "ledger"
    if isinstance(variance, Unconfirmed):
        basis = "disagreement"
    elif isinstance(variance, Quantity) and variance.amount != 0:
        basis = "disagreement"

    return {
        "ledger": balance,
        "reported": reported,
        "variance": variance,
        "basis": basis,
        "as_of": count.counted_on,
        "reported_kind": count.kind,
        "reported_source": count.source,
    }


def _variance(balance, reported: Quantity, item):
    """reported - ledger, in the ledger's unit, or Unconfirmed.

    This is where the carton/base-unit bridge fails when a supplier never
    stated the pack specification: the store counted packs, the worker
    counted sachets, and without the factor the two numbers cannot be
    subtracted. Returning a number anyway would be inventing the factor.
    """
    if isinstance(balance, Unconfirmed):
        return balance
    restated = ledger.convert(reported.amount, reported.unit, balance.unit, item)
    if isinstance(restated, Unconfirmed):
        return unconfirmed(
            f"the ledger is in {balance.unit}s and the count is in {reported.unit}s",
            *restated.reasons,
        )
    return Quantity(restated.amount - balance.amount, balance.unit)
