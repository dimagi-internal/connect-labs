"""A worker's stock day by day, and the chart drawn from it (design 2026-09-28 §6.3).

Issued steps up, dispensed steps down, a reversal (a rejected visit's
dispensing put back) steps back up as a mark of its own, reported counts are
points, and the ledger line runs between them. Everything is in the item's
single unit.

The chart is inline SVG because no chart library is loaded on supply pages,
and an SVG renders inside the as-of rewind with the rest of the page. It is
built from numbers and ISO dates only; the one piece of text from data, the
unit, is escaped. Its text and axis take `currentColor`, so they follow the
card they sit on; the series colours are mid-tone, legible on a light or a
dark card, and each series is also told apart by direction (up, down) or
shape (a count is a ringed point), never by colour alone. The `<title>` and
`<desc>` say in words what the chart draws, and the page carries the same
figures as a table.

It has a scale: gridlines at round numbers labelled down the left, and dates
along the bottom. An SVG's text shrinks with it, so a phone gets its own
narrower drawing (`width`) rather than the desktop one squeezed to a thumbnail.
"""

import math
from datetime import date, timedelta
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
REVERSED_COLOUR = "#9333ea"
# A reversal usually lands on the day of the dispensing it undoes, so its step
# would sit on top of that one; it is drawn this far to the right, dashed, and
# capped with a ringed square, so it reads as its own step without colour.
REVERSAL_OFFSET = 5


def worker_timeline(program_id, point, item, *, on_date=None) -> dict:
    """{"unit", "days": [...], "counts": [...], "unconverted": [...]} for one worker and item.

    A day is {"on", "before", "issued", "dispensed", "reversed", "other",
    "balance"}, all strings. `dispensed` is every visit's consumption that
    day; `reversed` is what came back because a visit was later rejected or
    marked a duplicate -- kept apart, never netted into `dispensed` and never
    an issue, so the chart can show the step back up. `other` is what moved
    for any other reason: an adjustment, stock sent on, a loss.
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
        day = days.setdefault(m["occurred_on"], {"issued": ZERO, "dispensed": ZERO, "reversed": ZERO, "other": ZERO})
        inbound = m["to_supply_point_id"] == point.pk
        if m["kind"] == "consumption":
            if inbound:
                day["reversed"] += amount  # only a reversal brings consumption back in
            else:
                day["dispensed"] += amount
        elif inbound and m["kind"] in belief.ISSUE_KINDS:
            day["issued"] += amount
        else:
            day["other"] += amount if inbound else -amount

    balance = ZERO
    out = []
    for on in sorted(days):
        day = days[on]
        before = balance
        balance = balance + day["issued"] - day["dispensed"] + day["reversed"] + day["other"]
        out.append(
            {
                "on": on.isoformat(),
                "before": str(before),
                "issued": str(day["issued"]),
                "dispensed": str(day["dispensed"]),
                "reversed": str(day["reversed"]),
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
            count_rows.append(
                {"on": count.counted_on.isoformat(), "quantity": str(converted.amount), "kind": count.kind}
            )
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


_STEPS = (
    ("issued", ISSUED_COLOUR, 1),
    ("dispensed", DISPENSED_COLOUR, -1),
    ("reversed", REVERSED_COLOUR, 1),
    ("other", OTHER_COLOUR, 1),
)
_VERBS = {"issued": "issued", "dispensed": "dispensed", "reversed": "put back", "other": "moved"}


def _description(days, counts, unit) -> str:
    """The chart in one paragraph, for anyone who cannot see it."""
    dates = [d["on"] for d in days] + [c["on"] for c in counts]
    issued = sum((Decimal(d["issued"]) for d in days), ZERO)
    dispensed = sum((Decimal(d["dispensed"]) for d in days), ZERO)
    reversed_ = sum((Decimal(d.get("reversed") or 0) for d in days), ZERO)
    parts = [f"From {day_text(min(dates))} to {day_text(max(dates))}:"]
    if days:
        parts.append(f"{_amount(issued, unit)} issued and {_amount(dispensed, unit)} dispensed")
        if reversed_:
            parts[-1] += f", of which {_amount(reversed_, unit)} was put back when visits were rejected"
        parts[-1] += f"; the ledger ends at {_amount(Decimal(days[-1]['balance']), unit)}."

    if counts:
        last = counts[-1]
        parts.append(
            f"{len(counts)} count{'' if len(counts) == 1 else 's'} reported, the last "
            f"{_amount(Decimal(last['quantity']), unit)} on {day_text(last['on'])}."
        )
    else:
        parts.append("No count reported.")
    return " ".join(parts)


def _step(span) -> Decimal:
    """A round gridline interval (1, 2 or 5 times a power of ten) giving about four lines over `span`."""
    raw = max(float(span) / 4, 1.0)
    power = 10 ** math.floor(math.log10(raw))
    for nice in (1, 2, 5, 10):
        if raw <= nice * power:
            return Decimal(nice * power)
    return Decimal(10 * power)


def _dates(first, last, count):
    """`count` evenly spaced days from first to last, without repeats."""
    span = (last - first).days
    if span == 0 or count < 2:
        return [first]
    picked = [first + timedelta(days=round(span * i / (count - 1))) for i in range(count)]
    return list(dict.fromkeys(picked))


def timeline_svg(
    line: dict, *, width: int = 720, height: int = 220, date_ticks: int = 5, font: int = 11, key: str = ""
) -> str:
    """The timeline as inline SVG, or "" when there is nothing to draw.

    `key` keeps the title and description ids unique when a page carries more than one drawing.
    """
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
    highest = max([*levels, Decimal(1)])
    lowest = min([*levels, ZERO])
    step = _step(highest - lowest)
    top = Decimal(math.ceil(highest / step)) * step
    bottom = Decimal(math.floor(lowest / step)) * step
    left, right, above, below = 44, 16, 22, 30

    def x(on):
        return left + (on - first).days / span * (width - left - right)

    def y(value):
        return above + float(top - Decimal(value)) / float(top - bottom) * (height - above - below)

    title_id, desc_id = f"worker-timeline-title{key}", f"worker-timeline-desc{key}"
    text = f'font-size="{font}" fill="currentColor"'
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="{title_id} {desc_id}" class="w-full h-auto" '
        f'data-testid="worker-timeline">',
        f'<title id="{title_id}">Stock held, in {plural}, day by day</title>',
        f'<desc id="{desc_id}">{_description(days, counts, unit)}</desc>',
    ]
    # The scale: a faint gridline at each round number, labelled on the left; zero a little firmer.
    grid = ['<g data-kind="scale">']
    tick = bottom
    while tick <= top:
        opacity = "0.35" if tick == 0 else "0.12"
        kind = "axis" if tick == 0 else "gridline"
        line = _segment(left, y(tick), width - right, y(tick), kind, "currentColor", width=1)
        grid.append(f'<g stroke-opacity="{opacity}">{line}</g>')
        grid.append(
            f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" {text} text-anchor="end" data-kind="tick">'
            f"{escape(quantity_digits(tick))}</text>"
        )
        tick += step
    grid.append(f'<text x="{left}" y="12" {text}>{plural}</text>')
    for on in _dates(first, last, date_ticks):
        at = x(on)
        # The first date reads from its tick, the last up to it, any between centred on it.
        anchor = "start" if on == first else "end" if on == last else "middle"
        mark = _segment(at, height - below, at, height - below + 4, "date-tick", "currentColor", width=1)
        grid.append(f'<g stroke-opacity="0.35">{mark}</g>')
        grid.append(
            f'<text x="{at:.1f}" y="{height - 8}" {text} text-anchor="{anchor}" data-kind="date">{day_text(on)}</text>'
        )
    grid.append("</g>")
    parts.extend(grid)
    previous = None
    for d in days:
        at = x(d["on"])
        level = Decimal(d["before"])
        if previous is not None:
            parts.append(_segment(previous, y(level), at, y(level), "ledger", LEDGER_COLOUR, width=1.5, dash="4 3"))
        for key, colour, sign in _STEPS:
            amount = Decimal(d.get(key) or 0)
            if amount == 0:
                continue
            after = level + sign * amount
            title = f"{day_text(d['on'])}: {_amount(abs(amount), unit)} {_VERBS[key]}"
            if key == "reversed":
                title += " (a visit rejected after it was counted)"
                step = at + REVERSAL_OFFSET
                parts.append(_segment(step, y(level), step, y(after), key, colour, title, dash="3 2"))
                parts.append(
                    f'<rect x="{step - 4:.1f}" y="{y(after) - 4:.1f}" width="8" height="8" fill="none" '
                    f'stroke="{colour}" stroke-width="2" data-kind="reversal-mark"><title>{title}</title></rect>'
                )
            else:
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
    parts.append("</svg>")
    return "".join(parts)
