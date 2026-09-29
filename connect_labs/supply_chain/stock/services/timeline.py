"""A worker's stock day by day, and the chart drawn from it (design 2026-09-28 §6.3).

Issued steps up, dispensed steps down, reported counts are points, and the
ledger line runs between them. Everything is in the item's single unit.

The chart is inline SVG because no chart library is loaded on supply pages,
and an SVG renders inside the as-of rewind with the rest of the page. It is
built from numbers and ISO dates only; the one piece of text from data, the
unit, is escaped. Its text and axis take `currentColor`, so they follow the
card they sit on; the series colours are mid-tone, legible on a light or a
dark card, and each series is also told apart by direction (up, down) or
shape (a count is a ringed point), never by colour alone. The `<title>` and
`<desc>` say in words what the chart draws, and the page carries the same
figures as a table.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Q
from django.utils.html import escape

from connect_labs.supply_chain import records
from connect_labs.supply_chain.models import Movement, StockCount
from connect_labs.supply_chain.stock.services import belief, ledger
from connect_labs.supply_chain.values import Quantity, day_text, quantity_digits, unit_noun

ZERO = Decimal("0")

# Mid-tone, so each reads against white and against a near-black card alike.
ISSUED_COLOUR = "#16a34a"
DISPENSED_COLOUR = "#ea580c"
OTHER_COLOUR = "#6b7280"
LEDGER_COLOUR = "#6b7280"
COUNT_COLOUR = "#3b82f6"


def worker_timeline(program_id, point, item, *, on_date=None) -> dict:
    """{"unit", "days": [...], "counts": [...], "unconverted": [...]} for one worker and item.

    A day is {"on", "before", "issued", "dispensed", "other", "balance"}, all
    strings. A reversal (a rejected visit's consumption put back) takes that
    day's dispensing back; it is never an issue. `other` is what moved for
    any other reason: an adjustment, stock sent on, a loss.
    """
    unit = belief.unit_of(item)
    moves = (
        Movement.objects.for_program(program_id)
        .as_of(on_date)
        .filter(item=item)
        .filter(Q(to_supply_point=point) | Q(from_supply_point=point))
        .order_by("occurred_on", "id")
        .values("occurred_on", "kind", "quantity", "quantity_unit", "to_supply_point_id", "from_supply_point_id")
    )
    days: dict = {}
    unconverted = []
    for m in moves:
        if m["to_supply_point_id"] == m["from_supply_point_id"]:
            continue  # a transfer to itself moves nothing
        converted = ledger.convert(m["quantity"], m["quantity_unit"], unit, item)
        if not isinstance(converted, Quantity):
            unconverted.append({"on": m["occurred_on"].isoformat(), "reasons": list(converted.reasons)})
            continue
        amount = converted.amount
        day = days.setdefault(m["occurred_on"], {"issued": ZERO, "dispensed": ZERO, "other": ZERO})
        inbound = m["to_supply_point_id"] == point.pk
        if m["kind"] == "consumption":
            day["dispensed"] += -amount if inbound else amount
        elif inbound and m["kind"] in belief.ISSUE_KINDS:
            day["issued"] += amount
        else:
            day["other"] += amount if inbound else -amount

    balance = ZERO
    out = []
    for on in sorted(days):
        day = days[on]
        before = balance
        balance = balance + day["issued"] - day["dispensed"] + day["other"]
        out.append(
            {
                "on": on.isoformat(),
                "before": str(before),
                "issued": str(day["issued"]),
                "dispensed": str(day["dispensed"]),
                "other": str(day["other"]),
                "balance": str(balance),
            }
        )

    counts = StockCount.objects.filter(
        program_id=program_id, supply_point=point, item=item, kind__in=records.ON_HAND_COUNT_KINDS
    )
    if on_date is not None:
        counts = counts.filter(counted_on__lte=on_date)
    count_rows = []
    for count in counts.order_by("counted_on", "id"):
        converted = ledger.convert(count.quantity, count.quantity_unit, unit, item)
        if isinstance(converted, Quantity):
            count_rows.append({"on": count.counted_on.isoformat(), "quantity": str(converted.amount), "kind": count.kind})
        else:
            unconverted.append({"on": count.counted_on.isoformat(), "reasons": list(converted.reasons)})
    return {"unit": unit, "days": out, "counts": count_rows, "unconverted": unconverted}


def _amount(value, unit) -> str:
    """ "14 sachets": the number by the one quantity rule, the unit pluralised on it, escaped."""
    return escape(f"{quantity_digits(value)} {unit_noun(unit, value)}".strip())


def _segment(x1, y1, x2, y2, kind, colour, title="", width=3, dash=""):
    inner = f"<title>{title}</title>" if title else ""
    dashed = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{colour}" '
        f'stroke-width="{width}" stroke-linecap="round"{dashed} data-kind="{kind}">{inner}</line>'
    )


_STEPS = (("issued", ISSUED_COLOUR, 1), ("dispensed", DISPENSED_COLOUR, -1), ("other", OTHER_COLOUR, 1))


def _description(days, counts, unit) -> str:
    """The chart in one paragraph, for anyone who cannot see it."""
    dates = [d["on"] for d in days] + [c["on"] for c in counts]
    issued = sum((Decimal(d["issued"]) for d in days), ZERO)
    dispensed = sum((Decimal(d["dispensed"]) for d in days), ZERO)
    parts = [f"From {day_text(min(dates))} to {day_text(max(dates))}:"]
    if days:
        parts.append(
            f"{_amount(issued, unit)} issued and {_amount(dispensed, unit)} dispensed; "
            f"the ledger ends at {_amount(Decimal(days[-1]['balance']), unit)}."
        )
    if counts:
        last = counts[-1]
        parts.append(
            f"{len(counts)} count{'' if len(counts) == 1 else 's'} reported, the last "
            f"{_amount(Decimal(last['quantity']), unit)} on {day_text(last['on'])}."
        )
    else:
        parts.append("No count reported.")
    return " ".join(parts)


def timeline_svg(line: dict, *, width: int = 720, height: int = 220) -> str:
    """The timeline as inline SVG, or "" when there is nothing to draw."""
    days = [{**d, "on": date.fromisoformat(d["on"])} for d in line.get("days") or []]
    counts = [{**c, "on": date.fromisoformat(c["on"])} for c in line.get("counts") or []]
    if not days and not counts:
        return ""
    unit = line.get("unit") or ""
    plural = escape(unit_noun(unit, 2))
    dates = [d["on"] for d in days] + [c["on"] for c in counts]
    first, last = min(dates), max(dates)
    span = max((last - first).days, 1)
    levels = [Decimal(d[k]) for d in days for k in ("before", "balance")] + [Decimal(c["quantity"]) for c in counts]
    top = max([*levels, Decimal(1)])
    bottom = min([*levels, ZERO])
    pad = 28

    def x(on):
        return pad + (on - first).days / span * (width - 2 * pad)

    def y(value):
        return pad + float(top - Decimal(value)) / float(top - bottom) * (height - 2 * pad)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="worker-timeline-title worker-timeline-desc" class="w-full h-auto" '
        f'data-testid="worker-timeline">',
        f'<title id="worker-timeline-title">Stock held, in {plural}, day by day</title>',
        f'<desc id="worker-timeline-desc">{_description(days, counts, unit)}</desc>',
        f'<g stroke-opacity="0.35">{_segment(pad, y(ZERO), width - pad, y(ZERO), "axis", "currentColor", width=1)}</g>',
    ]
    previous = None
    for d in days:
        at = x(d["on"])
        level = Decimal(d["before"])
        if previous is not None:
            parts.append(_segment(previous, y(level), at, y(level), "ledger", LEDGER_COLOUR, width=1.5, dash="4 3"))
        for key, colour, sign in _STEPS:
            amount = Decimal(d[key])
            if amount == 0:
                continue
            after = level + sign * amount
            verb = {"issued": "issued", "dispensed": "dispensed", "other": "moved"}[key]
            title = f"{day_text(d['on'])}: {_amount(abs(amount), unit)} {verb}"
            parts.append(_segment(at, y(level), at, y(after), key, colour, title))
            level = after
        previous = at
    if days:
        end = Decimal(days[-1]["balance"])
        parts.append(_segment(previous, y(end), x(last), y(end), "ledger", LEDGER_COLOUR, width=1.5, dash="4 3"))
    for c in counts:
        parts.append(
            f'<circle cx="{x(c["on"]):.1f}" cy="{y(c["quantity"]):.1f}" r="5" fill="{COUNT_COLOUR}" '
            f'stroke="currentColor" stroke-width="1.5" data-kind="count">'
            f"<title>{day_text(c['on'])}: counted {_amount(Decimal(c['quantity']), unit)}</title></circle>"
        )
    text = 'font-size="11" fill="currentColor"'
    parts.append(f'<text x="{pad}" y="{height - 8}" {text}>{day_text(first)}</text>')
    parts.append(f'<text x="{width - pad}" y="{height - 8}" {text} text-anchor="end">{day_text(last)}</text>')
    parts.append(f'<text x="{pad}" y="{pad - 10}" {text}>{_amount(top, unit)}</text>')
    parts.append("</svg>")
    return "".join(parts)
