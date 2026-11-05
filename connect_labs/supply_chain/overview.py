"""What the Overview says about the programme as a whole, beyond the buying table.

Three questions a programme manager opens Overview to answer, each worked out
from rows that already exist (nothing new is stored):

  next_step           where the programme stands before a tender exists and
                      after orders are placed, as one line.
  distribution_overview
                      which partners are low, which field workers run dry this
                      week, and the open stock issues.
  buying_model_label  how an order is being bought, once the order records it.

Two of these are written to light up when another package lands, and say so
rather than guessing:

  * Stock issues (`open_issues`) read `connect_labs.supply_chain.issues` if it
    exists. Until then the Overview says issues are not yet recorded, which is
    different from "there are none".
  * A tender's "needed by" date and an order's buying model are read from the
    record when it carries them (`needed_by`, `start_by`, `buying_model`).
    Without them the line is simply shorter.
"""

from __future__ import annotations

import importlib
from datetime import date
from decimal import Decimal, InvalidOperation

from connect_labs.supply_chain.stock.services.belief import is_below_minimum

# How many days ahead counts as "this week" when a field worker's stock runs out.
DRY_WITHIN_DAYS = 7

# Orders in these statuses are not yet placed, or are finished.
_ORDER_NOT_PLACED = ("draft", "cancelled")

# What each buying model is called. The field is added by another package; an
# unknown value is shown humanised rather than dropped.
BUYING_MODEL_LABELS = {
    "programme_buys": "We buy and ship to the partner",
    "partner_buys": "The partner buys on our quote and invoices us",
}


def buying_model_label(contract: dict) -> str:
    """How this order is bought, or "" while the order does not record it yet."""
    value = (contract or {}).get("buying_model") or ""
    if not value:
        return ""
    return BUYING_MODEL_LABELS.get(value, str(value).replace("_", " ").capitalize())


# ---------------------------------------------------------------- next step


def _month(day: date) -> str:
    return day.strftime("%b %Y")


def _iso(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def partners_set_up(points, contracts, exclude_org_ids=()) -> tuple[int, int]:
    """(set up, partners) -- the organisations that buy or hold stock here, other than the one running it.

    A partner is known here by what it does in the programme: it is the buyer
    of an order, or it runs a place that holds stock. It is "set up" once it
    runs at least one active place of its own (a supplier's site does not
    count: that is where goods leave from, not where a partner keeps them).
    An organisation that has done neither is not yet visible to this count.
    """
    exclude = set(exclude_org_ids)
    holders = {
        p["managed_by_org_id"]
        for p in points
        if p.get("managed_by_org_id") and p.get("status") == "active" and p.get("kind") != "supplier_site"
    }
    buyers = {c["buyer_org_id"] for c in contracts if c.get("buyer_org_id")}
    partners = (holders | buyers) - exclude
    return len(partners & holders), len(partners)


def next_step(
    *, tenders, contracts, points, exclude_org_ids=(), today: date | None = None, tender_replies=None
) -> dict:
    """The programme's next step as {"line", "segments", "next"}.

    "Partners: 3 of 5 set up · Tender: not created · Needed by Feb 2027, start by early Dec".
    `segments` are the parts of the line, `next` is the one thing to do now.

    A tender that is collecting quotes comes first even when orders exist: a
    re-quote opened after the first order is the live work, and "every order is
    received" would send the reader to the wrong page. `tender_replies` is
    {tender id: (suppliers asked, suppliers silent)}, for the sentence.
    """
    today = today or date.today()
    set_up, partners = partners_set_up(points, contracts, exclude_org_ids)
    segments, nxt = [], ""

    if partners:
        segments.append(f"Partners: {set_up} of {partners} set up")
    placed = [c for c in contracts if c.get("status") not in _ORDER_NOT_PLACED]
    received = [c for c in placed if c.get("status") in ("received", "closed")]
    live = [t for t in tenders if t.get("status") != "cancelled"]
    # The tender in play: the newest one.
    tender = max(live, key=lambda t: t["id"]) if live else None

    # A tender split by partner is not ordered until every partner has: "4 of 5 partners have ordered".
    split_set = None
    if tender is not None and tender.get("partner_splits"):
        from connect_labs.supply_chain.procurement.services.splits import order_set

        split_set = order_set(tender, [c for c in contracts if c.get("tender_id") == tender["id"]])

    collecting = max((t for t in live if t.get("status") == "open"), key=lambda t: t["id"], default=None)

    if placed:
        text = f"Orders: {len(placed)} placed"
        if split_set and split_set["text"]:
            text = f"Orders: {split_set['text']}"
        if received:
            text += f" · {len(received)} received"
        segments.append(text)
        nxt = (
            "Record each delivery as it arrives"
            if len(received) < len(placed)
            else "Every order is received: check the stock and the payments"
        )
        if split_set and split_set["waiting_partners"]:
            nxt = "Place an order for " + ", ".join(split_set["waiting_partners"])
        elif partners and set_up < partners:
            nxt = "Set up the partners who have no warehouse yet, then record each delivery"
        if collecting is not None:
            name = collecting.get("label") or "The open tender"
            asked, silent, *rest = (tender_replies or {}).get(collecting["id"], (0, 0))
            not_asked = rest[0] if rest else 0
            segments.append(f"{name}: collecting quotes")
            if asked and (silent or not_asked):
                nxt = f"{name}: {asked - silent - not_asked} of {asked} suppliers answered"
                if silent:
                    nxt += f"; chase {'the other ' if not not_asked else ''}{silent}"
                if not_asked:
                    nxt += f"; {not_asked} not yet written to (send them the request)"
            else:
                nxt = f"{name}: chase the suppliers who have not replied"
    elif tender is None:
        segments.append("Tender: not created")
        nxt = "Create a tender to ask suppliers for quotes"
        if partners and set_up < partners:
            nxt = "Set up the remaining partners, then create a tender"
    else:
        status = tender.get("status")
        words = {
            "draft": ("Tender: drafted, not sent", "Send the tender to suppliers"),
            "open": ("Tender: collecting quotes", "Chase the suppliers who have not replied"),
            "closed": ("Tender: comparing quotes", "Compare the quotes and award the tender"),
            "awarded": ("Tender: awarded, no orders yet", "Place an order for each partner"),
        }.get(status, (f"Tender: {str(status or 'in progress').replace('_', ' ')}", ""))
        segments.append(words[0])
        nxt = words[1]

    # A needed-by date, when the tender records one (added separately).
    needed = _iso((tender or {}).get("needed_by"))
    if needed and not placed:
        text = f"Needed by {_month(needed)}"
        start = _iso(tender.get("start_by"))
        if start:
            text += f", start by {_start_words(start)}"
            if start < today and (tender or {}).get("status") in (None, "draft"):
                text += " (late to start)"
        segments.append(text)
    return {"line": " · ".join(segments), "segments": segments, "next": nxt}


def _start_words(day: date) -> str:
    """ "early Dec", "mid Dec" or "late Dec": a start date is a season, not a day."""
    part = "early" if day.day <= 10 else "mid" if day.day <= 20 else "late"
    return f"{part} {day.strftime('%b')}"


# ------------------------------------------------------------ distribution


def _days(value) -> Decimal | None:
    """A days figure from the wire (a decimal string), or None when it cannot be told."""
    if value is None or isinstance(value, dict):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _partner_of(point, by_id):
    """The organisation that runs this place: its own, else the nearest above it that names one."""
    seen = set()
    while point is not None and point["id"] not in seen:
        if point.get("managed_by_org_id"):
            return point["managed_by_org_id"]
        seen.add(point["id"])
        point = by_id.get(point.get("parent_supply_point_id"))
    return None


def open_issues(program_id) -> dict:
    """HOOK: the open stock issues, from the Issues package when it is installed.

    Issues (missing, damaged, expired stock) is another package. It lights up
    here by providing `connect_labs.supply_chain.issues.open_issues(program_id)`
    returning a list of {"title", "partner", "status", "days_open", "href"}.
    Until it exists, `available` is False and the Overview says issues are not
    recorded yet, which is not the same statement as "no issues".
    """
    try:
        module = importlib.import_module("connect_labs.supply_chain.issues")
    except ImportError:
        return {"available": False, "items": []}
    reader = getattr(module, "open_issues", None)
    if reader is None:
        return {"available": False, "items": []}
    return {"available": True, "items": list(reader(program_id))}


def distribution_overview(*, program_id, stock_points, points, org_names, worker_href=None) -> dict:
    """Which partners are low, which field workers run dry this week, and the open issues.

    `stock_points` are network_stock rows, `points` supply_point_list rows,
    `org_names` {org id: name}. Counts are of places, never summed quantities:
    a carton and a sachet are not added together.
    """
    by_id = {p["id"]: p for p in points}
    partners: dict = {}
    dry = []
    for row in stock_points:
        if row.get("kind") == "supplier_site":
            continue
        point = by_id.get(row["supply_point_id"])
        org_id = _partner_of(point, by_id) if point else None
        entry = partners.setdefault(
            org_id, {"org_id": org_id, "name": org_names.get(org_id, "No partner assigned"), "places": 0, "low": 0}
        )
        entry["places"] += 1
        low = is_below_minimum(row.get("status"))
        entry["low"] += int(low)
        if row.get("kind") == "user_held":
            days = _days(row.get("days_to_stockout"))
            out_now = row.get("status") in ("stockout", "negative")
            if out_now or (days is not None and days <= DRY_WITHIN_DAYS):
                dry.append(
                    {
                        "name": row["name"],
                        "partner": entry["name"],
                        "days": None if out_now else int(days),
                        "href": worker_href(row) if worker_href else "",
                    }
                )
    dry.sort(key=lambda d: (d["days"] is not None, d["days"] or 0, d["name"]))
    low_partners = sorted((p for p in partners.values() if p["low"]), key=lambda p: (-p["low"], p["name"]))
    return {
        "has_network": bool(partners),
        "partners": sorted(partners.values(), key=lambda p: p["name"]),
        "low_partners": low_partners,
        "dry_this_week": dry,
        "issues": open_issues(program_id),
    }
