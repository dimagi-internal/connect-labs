"""What the Stock page's cards say, worked out from the network read -- never stored.

THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is invented.

One card per supply point: its on-hand figure big, a days-of-cover bar against the minimum and
maximum this programme set for it, a status chip for trouble, and one plain sentence for why.
Everything here reads the same point row the table beneath it reads, so a card can never say
something the table does not. A figure that is unknown stays unknown: no bar is drawn from a
cover that was never worked out.
"""

from decimal import Decimal

from connect_labs.supply_chain.stock.services.resupply import DAYS_PER_MONTH
from connect_labs.supply_chain.templatetags import supply_chain_extras as fmt


def _number(value):
    """A derived figure's number (a cell, or a bare string), or None when there is none."""
    if isinstance(value, dict):
        value = value.get("amount")
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return number if number.is_finite() else None


def _days(point):
    """Whole days of cover still in hand, counted the way the table counts them; None if unknown."""
    days = _number(point.get("days_to_stockout"))
    if days is not None:
        return int(days)
    months = _number(point.get("months_of_stock"))
    if months is not None and not fmt.unconfirmed_reasons(point.get("months_of_stock")):
        return int(months * DAYS_PER_MONTH)
    return None


def state_of(point) -> str:
    """negative | stockout | below_min | durable | overstocked | ok | unknown -- what the card shows."""
    status = point.get("status")
    on_hand = _number(point.get("on_hand"))
    if status == "negative" or (on_hand is not None and on_hand < 0):
        return "negative"
    if status == "stockout" or point.get("stocked_out"):
        return "stockout"
    if status in ("below_min", "durable", "overstocked", "ok"):
        return status
    return "unknown"


def _band(point):
    low = _number(point.get("min_months_of_stock"))
    high = _number(point.get("max_months_of_stock"))
    if low is None or high is None:
        return None, None
    return int(low * DAYS_PER_MONTH), int(high * DAYS_PER_MONTH)


def cover_bar(point):
    """The bar's geometry as percentages of its own width, or None when no cover can be drawn.

    The scale reaches a little past the maximum (or past the cover itself if that is larger) so
    the maximum is never the edge of the track and an overstocked point visibly overshoots.
    """
    state = state_of(point)
    days = _days(point)
    if state in ("durable", "negative", "stockout") or days is None or point.get("no_consumption_yet"):
        return None
    low, high = _band(point)
    scale = max(days, high or 0, 1) * 1.2
    return {
        "days": days,
        "min_days": low,
        "max_days": high,
        "fill": round(min(days / scale, 1) * 100, 1),
        "min_at": round(low / scale * 100, 1) if low is not None else None,
        "max_at": round(high / scale * 100, 1) if high is not None else None,
    }


def _plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def _lead(point):
    """The big figure and its unit: the balance as the table's first cell leads with it."""
    on_hand = point.get("on_hand")
    base = point.get("on_hand_in_base")
    if point.get("counted_in_base") and base:
        main, side = base, on_hand
    else:
        main, side = on_hand, base
    text = fmt.figure_text(main)
    if not isinstance(main, dict) or main.get("amount") in (None, ""):
        return {"big": text, "unit": "", "side": "", "unconfirmed": True}
    number, _, unit = text.partition(" ")
    side_text = fmt.figure_text(side) if isinstance(side, dict) and side.get("amount") else ""
    return {"big": number, "unit": unit, "side": side_text, "unconfirmed": False}


def why(point, state, bar) -> str:
    """One plain sentence for the state the card is in."""
    days = bar["days"] if bar else None
    if state == "negative":
        return "The records show more has left here than ever arrived. A receipt may be missing, or a count is due."
    if state == "stockout":
        if point.get("no_consumption_yet"):
            return "Nothing left, and no use recorded yet, so there is no pace to plan on."
        return "Nothing left in stock."
    if state == "durable":
        return "Held, not used up, so there is no cover to measure."
    if point.get("no_consumption_yet"):
        return "No use recorded yet, so days of cover cannot be worked out."
    if days is None:
        reasons = fmt.unconfirmed_reasons(point.get("months_of_stock")) or fmt.unconfirmed_reasons(
            point.get("days_to_stockout")
        )
        return str(reasons[0]) if reasons else "Cover cannot be worked out yet."
    low, high = bar["min_days"], bar["max_days"]
    if state == "below_min":
        under = f"under its {low}-day minimum" if low is not None else "under its minimum"
        return f"{_plural(days, 'day')} of cover, {under}."
    if state == "overstocked":
        over = f"over its {high}-day maximum" if high is not None else "over its maximum"
        return f"{_plural(days, 'day')} of cover, {over}."
    if low is not None and high is not None:
        return f"{_plural(days, 'day')} of cover, inside its {low} to {high} day band."
    return f"{_plural(days, 'day')} of cover."


def _top_up(point) -> str:
    """ "18 jerrycans to reorder", or "" when nothing is owed to reach the maximum."""
    quantity = point.get("resupply_quantity")
    if not isinstance(quantity, dict) or quantity.get("amount") in (None, "", "0"):
        return ""
    verb = "reorder" if point.get("restocked_from") == "supplier" else "send"
    return f"{fmt.send_text(quantity)} to {verb}"


def build(points) -> list:
    """Every point's card, in the order given (the table's order)."""
    cards = []
    for point in points:
        state = state_of(point)
        bar = cover_bar(point)
        top_up = _top_up(point) if state != "durable" else ""
        cards.append(
            {
                "point": point,
                "state": state,
                "bar": bar,
                "lead": _lead(point),
                "why": why(point, state, bar),
                "top_up": top_up,
                "needs_order": bool(top_up) and point.get("restocked_from") == "supplier",
            }
        )
    return cards


def tiles(cards) -> dict:
    """The four counts across the top: what needs a person first."""
    return {
        "reorder": sum(1 for c in cards if c["needs_order"]),
        "empty": sum(1 for c in cards if c["state"] in ("negative", "stockout")),
        "below_min": sum(1 for c in cards if c["state"] == "below_min"),
        "fine": sum(1 for c in cards if c["state"] == "ok"),
    }
