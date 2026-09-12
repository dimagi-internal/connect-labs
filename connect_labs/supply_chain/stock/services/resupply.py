"""Consumption rate, cover, and what to send -- the standard supply figures.

Every one of these returns `Unconfirmed` rather than a number when its inputs
are not there, and the two that matter most are:

  - an average monthly consumption computed over a window shorter than
    `MINIMUM_WINDOW_DAYS` is refused. A fortnight of dispensing extrapolated
    to a month is how a supply chain talks itself into a stockout, in both
    directions;
  - months of stock is computed on on-hand alone. In-transit stock is real
    but it is not cover (design doc section 19.1).

`min_months_of_stock` / `max_months_of_stock` live on the supply point, so
the policy is data rather than a constant in here.
"""

from datetime import timedelta
from decimal import Decimal

from connect_labs.supply_chain.models import Movement
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.values import Quantity, Unconfirmed, decimal_string, unconfirmed

DAYS_PER_MONTH = Decimal("30")
MINIMUM_WINDOW_DAYS = 30
DEFAULT_WINDOW_DAYS = 90


def average_monthly_consumption(program_id, supply_point, item=None, as_of=None, window_days=DEFAULT_WINDOW_DAYS):
    """Consumption per 30 days at this point, over a stated window.

    The window is part of the answer, not a hidden parameter: two AMCs over
    different windows are different figures and should never be compared as
    if they were the same one. Callers get `amc_window_days` back alongside.
    """
    if window_days < MINIMUM_WINDOW_DAYS:
        return unconfirmed(
            f"a {window_days}-day window is too short to average a month of consumption "
            f"(at least {MINIMUM_WINDOW_DAYS} days are needed)"
        )

    end = as_of
    start = (end - timedelta(days=window_days)) if end else None
    consumed = (
        Movement.objects.for_program(program_id)
        .filter(kind="consumption", from_supply_point=supply_point)
        .between(start, end)
    )
    if item is not None:
        consumed = consumed.filter(item=item)

    first = Movement.objects.for_program(program_id).filter(kind="consumption", from_supply_point=supply_point)
    if item is not None:
        first = first.filter(item=item)
    earliest = first.order_by("occurred_on").values_list("occurred_on", flat=True).first()
    if earliest is None:
        return unconfirmed("nothing has been dispensed from here yet, so there is no consumption rate")

    observed_days = window_days
    if end is not None:
        observed_days = min(window_days, (end - earliest).days + 1)
    if observed_days < MINIMUM_WINDOW_DAYS:
        return unconfirmed(
            f"only {observed_days} days of dispensing have been recorded here; "
            f"at least {MINIMUM_WINDOW_DAYS} are needed before a monthly rate means anything"
        )

    total = ledger.collapse(consumed.consumption_by_unit(), item, None)
    if isinstance(total, Unconfirmed):
        return total
    if total.amount == 0:
        return unconfirmed("no consumption recorded in the window, so there is no rate to project")
    # Quantized for the same reason conversions are: a rate carried to 27
    # digits is false precision on a figure derived from counted cartons.
    rate = (total.amount / Decimal(observed_days) * DAYS_PER_MONTH).quantize(ledger.QUANTITY_SCALE)
    return Quantity(rate, total.unit)


def _ratio(numerator: Quantity, denominator: Quantity, item):
    """numerator / denominator, restating units first. Unconfirmed if it cannot."""
    restated = ledger.convert(denominator.amount, denominator.unit, numerator.unit, item)
    if isinstance(restated, Unconfirmed):
        return restated
    if restated.amount == 0:
        return unconfirmed("the consumption rate is zero, so cover cannot be expressed in months")
    return numerator.amount / restated.amount


def plan(program_id, supply_point, item=None, as_of=None, window_days=DEFAULT_WINDOW_DAYS) -> dict:
    """Everything a resupply decision needs, each figure honest about itself.

    `status` is a classification, not advice: it says where this point sits
    against its own min/max band. Choosing what to do about it -- and in what
    order across a network -- is deliberately not done here (design doc
    section 22).
    """
    on_hand = ledger.balance(program_id, supply_point, item=item, on_date=as_of)
    amc = average_monthly_consumption(program_id, supply_point, item=item, as_of=as_of, window_days=window_days)

    def blocked_on(reason):
        """Every dependent figure carries the same reason, rather than going blank.

        A blank cell invites the reader to supply their own number; the
        reason names the fact that is missing and who could supply it.
        """
        return {
            "on_hand": on_hand,
            "amc": amc,
            "amc_window_days": window_days,
            "months_of_stock": reason,
            "days_to_stockout": reason,
            "reorder_point": reason,
            "resupply_quantity": reason,
            "status": "unknown",
            "min_months_of_stock": supply_point.min_months_of_stock,
            "max_months_of_stock": supply_point.max_months_of_stock,
        }

    if isinstance(on_hand, Unconfirmed):
        return blocked_on(on_hand)
    if on_hand.amount < 0:
        # More has left this point than ever arrived, so cover and a resupply
        # quantity are both meaningless: sending stock does not fix a
        # movement nobody recorded, and a number here would invite exactly
        # that. Refuse, and name what to do first.
        return blocked_on(
            unconfirmed(
                f"{decimal_string(on_hand.amount)} {on_hand.unit} is a negative balance -- more has "
                "left here than ever arrived. Find the unrecorded movement before planning a "
                "resupply; sending more stock would not fix it."
            )
        )
    if isinstance(amc, Unconfirmed):
        return blocked_on(amc)

    months = _ratio(on_hand, amc, item)
    if isinstance(months, Unconfirmed):
        return blocked_on(months)

    minimum = supply_point.min_months_of_stock
    maximum = supply_point.max_months_of_stock

    status = "ok"
    if on_hand.amount < 0:
        # More has left this point than ever arrived. Not a level to
        # replenish -- a movement nobody recorded. checks_list reports it as
        # a conflict; the status says so too rather than calling it a
        # stockout, which would invite a resupply that fixes nothing.
        status = "negative"
    elif on_hand.amount == 0:
        status = "stockout"
    elif minimum is not None and months < minimum:
        status = "below_min"
    elif maximum is not None and months > maximum:
        status = "overstocked"

    reorder_point = None
    if maximum is not None:
        # Target the top of the band, less what is already here. On-order is
        # not netted off here: in-transit is not cover, and a consignment
        # stuck at a border has already proved that.
        target = ledger.convert(amc.amount * maximum, amc.unit, on_hand.unit, item)
        if isinstance(target, Quantity):
            shortfall = target.amount - on_hand.amount
            resupply = Quantity(max(shortfall, Decimal("0")), on_hand.unit)
        else:
            resupply = target
    else:
        resupply = unconfirmed(
            f"{supply_point.name} has no maximum months of stock set, so there is no target to resupply to"
        )

    if minimum is not None:
        # The level at which cover reaches the bottom of the band -- i.e.
        # order now. Expressed in the on-hand unit so it is directly
        # comparable with the figure beside it on a screen.
        reorder_point = ledger.convert(amc.amount * minimum, amc.unit, on_hand.unit, item)

    return {
        "on_hand": on_hand,
        "amc": amc,
        "amc_window_days": window_days,
        "months_of_stock": months,
        "days_to_stockout": months * DAYS_PER_MONTH,
        "reorder_point": reorder_point,
        "resupply_quantity": resupply,
        "status": status,
        "min_months_of_stock": minimum,
        "max_months_of_stock": maximum,
    }
