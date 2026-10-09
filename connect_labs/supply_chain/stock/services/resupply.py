"""Consumption rate, cover, and what to send -- the standard supply figures.

Every one of these returns `Unconfirmed` rather than a number when its inputs
are not there, and the two that matter most are:

  - a consumption rate averaged over fewer than `MINIMUM_WINDOW_DAYS` days is
    refused, and every rate says how many days it rests on, so an early one
    reads as early. A week is enough for a field worker's daily rate; the
    figure is days of stock, not months, so a short basis is not stretched
    across a month it never saw;
  - months of stock is computed on on-hand alone. In-transit stock is real
    but it is not cover (design doc section 19.1).

`min_months_of_stock` / `max_months_of_stock` live on the supply point, so
the policy is data rather than a constant in here.
"""

from datetime import date, timedelta
from decimal import Decimal

from connect_labs.supply_chain.models import Movement
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.values import NotForecast, Quantity, Unconfirmed, decimal_string, unconfirmed

DAYS_PER_MONTH = Decimal("30")
# Below this there is no rate. Was 30 until 2026-10-08 (owner's call: a worker's stock
# is planned in days, and a month's wait hid every new worker's figures).
MINIMUM_WINDOW_DAYS = 7
# How far back a rate looks, by the kind of point (`window_for`). One pace per
# point on every page: until 2026-10-08 the Stock review used 14 days and every
# other page 90, so one worker had two run-out dates under one label (#2342). A
# field worker's stock is planned in days and a manager acting this week wants
# their recent pace; a store's demand is lumpy -- a monthly collection, a
# partner's monthly report -- and 14 days of it is either nothing or a spike.
WORKER_WINDOW_DAYS = 14
STORE_WINDOW_DAYS = 90

# What an operation's `window_days` says it is, wherever one takes it.
WINDOW_DAYS_HELP = (
    "Days the rate looks back over. Omit it for the standard every page uses: "
    "a field worker's last 14 days, a store's last 90."
)

DURABLE = NotForecast("durable — not forecast: it is held and moved, never consumed")


# Said when a point has never dispensed anything. A constant so the stock page
# can recognise it and say it once rather than in every forecast column.
NO_CONSUMPTION_YET = "nothing has been dispensed from here yet, so there is no consumption rate"
# Said when there was demand here once, but none inside the window: a worker who
# ran out weeks ago and so has given nothing out since.
NOTHING_IN_WINDOW = "no {basis} recorded in the window, so there is no rate to project"


def _is_durable(item) -> bool:
    return item is not None and getattr(item, "stock_class", "consumable") == "durable"


def window_for(supply_point, window_days=None) -> int:
    """The days a point's rate looks back: the caller's window when it names one, else its kind's."""
    if window_days is not None:
        return window_days
    return WORKER_WINDOW_DAYS if supply_point.kind == "user_held" else STORE_WINDOW_DAYS


def average_monthly_consumption(program_id, supply_point, item=None, as_of=None, window_days=None):
    """Consumption per 30 days at this point, over a stated window.

    The window is part of the answer, not a hidden parameter: two AMCs over
    different windows are different figures and should never be compared as
    if they were the same one. Callers get `amc_window_days` back alongside.
    With no window named, the point's kind picks it (`window_for`).
    """
    return _rate_and_days(program_id, supply_point, item, as_of, window_days)[0]


def _rate_and_days(program_id, supply_point, item, as_of, window_days):
    """(average_monthly_consumption, the days it rests on -- `observed`, None with no rate to rest on)."""
    if _is_durable(item):
        return DURABLE, None
    too_short = window_too_short(window_days)
    if too_short is not None:
        return too_short, None

    # No as-of date means "as of today", not "with no end": an open end left
    # the window with no start either, so all history was summed and divided
    # by the window's days -- a store with a long history read far busier than
    # it is, and a short one read idler.
    end = as_of or date.today()
    window_days = window_for(supply_point, window_days)
    start = window_start(end, window_days)
    basis = demand_basis(program_id, supply_point, item=item, as_of=end)
    # Only demand on or before the end: on a past day, dispensing that had not
    # happened yet must not set the rate (or its earliest day).
    demand = _demand(program_id, supply_point, basis, item=item).as_of(end)
    earliest = demand.order_by("occurred_on").values_list("occurred_on", flat=True).first()
    by_unit = {unit[0]: total for unit, total in demand.between(start, end)._totals(["quantity_unit"]).items()}
    rate = rate_from(ledger.collapse(by_unit, item, None), earliest, end, window_days, basis)
    return rate, observed(earliest, end, window_days)


def window_start(end, window_days):
    """The first day of a window of `window_days` days ending on `end`, both days in it.

    `observed` counts days inclusively, so the window must too: "end minus 14
    days" held 15 days of dispensing and divided them by 14 -- 7% high at
    two weeks (1% at the 90 days it was written for).
    """
    return end - timedelta(days=window_days - 1)


def window_too_short(window_days):
    """Why a window cannot carry a monthly rate, or None when it can. Asked before any movement is read.

    None (the point's own window, `window_for`) is never too short.
    """
    if window_days is not None and window_days < MINIMUM_WINDOW_DAYS:
        return unconfirmed(
            f"a {window_days}-day window is too short to average a month of consumption "
            f"(at least {MINIMUM_WINDOW_DAYS} days are needed)"
        )
    return None


def observed(earliest, end, window_days):
    """How many days of the window the rate is averaged over: the window, or less when demand began inside it.

    None when nothing has been recorded yet. Fewer than MINIMUM_WINDOW_DAYS and
    there is no rate (`rate_from`), which a page can say as a field -- "28 d of
    30" -- instead of as the reason's sentence.
    """
    if earliest is None:
        return None
    return min(window_days, (end - earliest).days + 1)


def estimate_from(earliest, end, window_days):
    """The first day a monthly rate can be estimated, while too few days of demand stand behind one; else None.

    The day the recorded days reach MINIMUM_WINDOW_DAYS, counting the first
    day of demand as one. None with no demand at all, with a rate already,
    and with a window that could never hold enough days (`window_too_short`).
    """
    if earliest is None or window_too_short(window_days) is not None:
        return None
    if observed(earliest, end, window_days) >= MINIMUM_WINDOW_DAYS:
        return None
    return earliest + timedelta(days=MINIMUM_WINDOW_DAYS - 1)


def per_day_so_far(total, earliest, end):
    """Demand per day over the days recorded so far, or None.

    Not a rate to plan on -- there are too few days for that (`rate_from`) --
    but what those days show: the total over the days since the first.
    """
    if earliest is None or not isinstance(total, Quantity):
        return None
    days = (end - earliest).days + 1
    return Quantity((total.amount / Decimal(days)).quantize(ledger.QUANTITY_SCALE), total.unit)


def rate_from(total, earliest, end, window_days, basis):
    """A monthly rate from a window's total and the earliest demand ever recorded.

    The one rule, shared by one point's plan and by the grouped figures in
    belief.py, so a worker's cover on the Workers page and on the resupply
    plan cannot disagree.
    """
    if earliest is None:
        return unconfirmed(NO_CONSUMPTION_YET)
    observed_days = observed(earliest, end, window_days)
    if observed_days < MINIMUM_WINDOW_DAYS:
        what = "dispensing" if basis == CONSUMPTION else "releases"
        return unconfirmed(
            f"only {observed_days} days of {what} have been recorded here; "
            f"at least {MINIMUM_WINDOW_DAYS} are needed before a daily rate means anything"
        )
    if isinstance(total, Unconfirmed):
        return total
    if total.amount == 0:
        return unconfirmed(NOTHING_IN_WINDOW.format(basis=basis))
    # Quantized for the same reason conversions are: a rate carried to 27
    # digits is false precision on a figure derived from counted cartons.
    rate = (total.amount / Decimal(observed_days) * DAYS_PER_MONTH).quantize(ledger.QUANTITY_SCALE)
    return Quantity(rate, total.unit)


CONSUMPTION = "consumption"
RELEASES = "releases"

# What leaves a store for another supply point: the demand on a store that
# does not dispense to anyone itself, such as a distributor's warehouse whose
# whole outflow is partners collecting.
RELEASE_KINDS = ("issue", "transfer", "distribution")


def demand_basis(program_id, supply_point, item=None, as_of=None) -> str:
    """What a point's rate is averaged from, stated rather than assumed.

    A point that dispenses is rated on what it dispenses -- that is demand.
    A point that never has is rated on what it releases to other points, which
    is the demand placed on it; without this a warehouse had no rate at all,
    and so no months of stock and no reorder figure, however busy it was.

    On a past day (`as_of`) only what had happened by then decides it.
    """
    dispensed = (
        Movement.objects.for_program(program_id)
        .as_of(as_of)
        .filter(kind="consumption", from_supply_point=supply_point)
    )
    if item is not None:
        dispensed = dispensed.filter(item=item)
    return CONSUMPTION if dispensed.exists() else RELEASES


def _demand(program_id, supply_point, basis, item=None):
    qs = Movement.objects.for_program(program_id).filter(from_supply_point=supply_point)
    if basis == CONSUMPTION:
        # A reversed visit never happened as far as the rate is concerned.
        qs = qs.filter(kind="consumption", reversal__isnull=True)
    else:
        qs = qs.filter(kind__in=RELEASE_KINDS, to_supply_point__isnull=False).exclude(to_supply_point=supply_point)
    if item is not None:
        qs = qs.filter(item=item)
    return qs


def _ratio(numerator: Quantity, denominator: Quantity, item):
    """numerator / denominator, restating units first. Unconfirmed if it cannot."""
    restated = ledger.convert(denominator.amount, denominator.unit, numerator.unit, item)
    if isinstance(restated, Unconfirmed):
        return restated
    if restated.amount == 0:
        return unconfirmed("the consumption rate is zero, so cover cannot be expressed in months")
    return numerator.amount / restated.amount


def restocked_from(supply_point) -> str:
    """ "supply_point" when another point in the programme sends to this one, else "supplier".

    The resupply quantity is the same arithmetic either way; what differs is
    who acts on it. The top of a network -- a distributor's central warehouse
    -- is restocked by an order to its supplier, not a transfer.
    """
    return "supply_point" if supply_point.parent_id else "supplier"


def plan(program_id, supply_point, item=None, as_of=None, window_days=None) -> dict:
    """Everything a resupply decision needs, each figure honest about itself.

    `status` is a classification, not advice: it says where this point sits
    against its own min/max band. Choosing what to do about it -- and in what
    order across a network -- is deliberately not done here (design doc
    section 22).
    """
    on_hand = ledger.balance(program_id, supply_point, item=item, on_date=as_of)
    basis = demand_basis(program_id, supply_point, item=item, as_of=as_of or date.today())
    amc, rate_days = _rate_and_days(program_id, supply_point, item, as_of, window_days)
    # The days the rate rests on, beside the window it looks back over: fewer when demand began inside it.
    return {**cover(on_hand, amc, basis, supply_point, item=item, window_days=window_days), "rate_days": rate_days}


def cover(on_hand, amc, basis, supply_point, item=None, window_days=None) -> dict:
    """Months of stock, days to stock-out, reorder point, resupply and status, from on-hand and a rate.

    Split from `plan` so belief.py can hand it figures it computed in grouped
    SQL for many points at once and still classify them by exactly this rule.
    `status` is a classification, not advice (design doc section 22).
    """
    window_days = window_for(supply_point, window_days)
    if _is_durable(item):
        # The balance is real -- "which site has which dispenser" -- and is
        # returned as it stands. Everything derived from consumption says why
        # it is not a number, and the status says the same, so no check reads
        # a durable point as a stockout or as below its band.
        return {
            "on_hand": on_hand,
            "amc": DURABLE,
            "amc_window_days": window_days,
            "amc_basis": basis,
            "months_of_stock": DURABLE,
            "days_to_stockout": DURABLE,
            "reorder_point": DURABLE,
            "resupply_quantity": DURABLE,
            "status": "durable",
            "min_months_of_stock": supply_point.min_months_of_stock,
            "max_months_of_stock": supply_point.max_months_of_stock,
        }

    def blocked_on(reason):
        """Every dependent figure carries the same reason, rather than going blank.

        A blank cell invites the reader to supply their own number; the
        reason names the fact that is missing and who could supply it.
        """
        return {
            "on_hand": on_hand,
            "amc": amc,
            "amc_window_days": window_days,
            "amc_basis": basis,
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
        blocked = blocked_on(amc)
        if on_hand.amount == 0 and NOTHING_IN_WINDOW.format(basis=basis) in amc.reasons:
            # Empty, and nothing given out lately because there was nothing to
            # give: that is a stock-out, not an unknown. A worker who ran out
            # three weeks ago has no 14-day rate, and must still read as out.
            blocked["status"] = "stockout"
        return blocked

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
        "amc_basis": basis,
        "months_of_stock": months,
        "days_to_stockout": months * DAYS_PER_MONTH,
        "reorder_point": reorder_point,
        "resupply_quantity": resupply,
        "status": status,
        "min_months_of_stock": minimum,
        "max_months_of_stock": maximum,
    }
