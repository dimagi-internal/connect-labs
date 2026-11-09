"""The chase list: what is stuck, who can unblock it, and what to ask them.

One source for every screen that shows it. The Map's "Chase list" tab, the
Overview and the portfolio page all read `chase_items`; the Map receives the
result inside its payload (`program["chase"]`) and draws it without working
anything out itself, so the three cannot drift apart.

Items are grouped by AUDIENCE (who can unblock them) and sorted oldest first.
An age is a fact on the record. It is not a ranking of what matters, which the
database cannot know (see views.DomainHomeView).

Each item carries `ask`: the sentence a programme manager can act on ("Ask
DABS to confirm they received payment"). A check kind without a sentence here
falls back to its label, so a new check kind shows up in the list before
anyone words it.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from connect_labs.supply_chain.naming import store_name
from connect_labs.supply_chain.values import day_text

# Who each audience is, when the check does not name a person or company.
# "internal" has no one to name, so a sentence that reaches it with no subject
# says "whoever has it" rather than leaving a gap ("Check the count with : ...").
_AUDIENCE_NOUN = {"supplier": "the supplier", "partner": "the partner", "internal": "whoever has it"}

# Checks about a place or a worker's stock: the subject's label IS the name of
# the person to ask (a supply point such as "flw_007"), and no supplier is named.
_NAMED_BY_SUBJECT = {"stock_never_reported", "stock_variance", "stock_negative"}

# What each kind of check needs, written for someone who is not a supply
# specialist. `{who}` is the supplier or partner it names, or the audience's
# plain noun when it names none. Kinds with no sentence use their label.
ASK = {
    "quote_not_comparable": "Ask {who} for the facts missing from their quote",
    "contract_cost_unconfirmed": "Ask {who} for the price details still missing, so the total cost can be worked out",
    "contract_reference_unknown": "Record the purchase order number",
    "duty_relief_unevidenced": "Get proof that the duty exemption was granted",
    "shipment_without_certificate": "Ask {who} for the batch certificate",
    "shipment_documents_outstanding": "Ask {who} for the shipping documents still missing",
    "shipment_delivered_unevidenced": "Ask {who} for proof that it was delivered",
    "charge_paid_unevidenced": "Ask {who} for proof of delivery for the carrier we paid",
    "shipment_quantity_unaccounted": "Ask {who} where the cartons that have not arrived are",
    "shipment_overdue": "Ask {who} when the shipment will arrive",
    "contract_delivery_overdue": "Ask {who} when the order will arrive",
    "consignment_overdue": "Ask {who} to confirm when the stock arrives",
    "payment_unconfirmed": "Ask {who} to confirm they received payment",
    "award_awaiting_approval": "Get the award approved",
    "award_not_contracted": "Place the order for the award",
    "invoice_over_billed": "Ask {who} to correct the invoice: it bills for more than arrived",
    "invoice_above_contract": "Ask {who} to correct the invoice: it is above the agreed price",
    "commitment_open": "Send {who} the answer we owe",
    "stock_never_reported": "Ask {who} to report their stock",
    "stock_variance": "Check the count with {who}: it disagrees with the records",
    "stock_negative": "Check with {who}: more was given out than was ever received",
}

# What each kind says about itself, in the words a programme manager would use (Sophie, round 2, "Plain words").
# `{who}` is the person or company the row is about; the row's own facts fill the rest in `_say`.
SAYS = {
    "payment_unconfirmed": "{who} hasn't confirmed they received payment",
    "shipment_without_certificate": "No batch certificate on file",
    "stock_variance": "{who}'s count is below the records",
    "stock_negative": "{who} gave out more than was ever received",
    "stock_never_reported": "{who} has never reported their stock",
}

# Hover explanations for the words a non-specialist will not know.
HINTS = {
    "batch certificate": (
        "The manufacturer's quality test result for one batch of product: what was made, when it expires, "
        "and that it passed. We ask for one with every delivery."
    ),
}

# FLW counts that differ from the records are the partner's supervisor's to check, not ours.
_COUNT_GAPS = ("stock_variance", "stock_negative", "stock_never_reported")
# Hand-written codes (DEMO-SHP-NSCH-001, PO-9): never the name of someone to ask.
_CODE = re.compile(r"^[A-Z0-9]+(?:[-_/][A-Z0-9]+)+$|^[A-Z]*\d[\w-]*$")


def _looks_like_code(text: str) -> bool:
    text = (text or "").strip()
    return bool(text) and " " not in text and bool(_CODE.match(text))


def hint_html(text: str, term: str):
    """`text` with the first `term` in it underlined and explained on hover (HINTS), as safe HTML."""
    from django.utils.html import escape, format_html
    from django.utils.safestring import mark_safe

    start = text.lower().find(term.lower()) if term else -1
    if start < 0 or term.lower() not in HINTS:
        return escape(text)
    return format_html(
        '{}<abbr title="{}" class="cursor-help underline decoration-dotted">{}</abbr>{}',
        mark_safe(escape(text[:start])),
        HINTS[term.lower()],
        text[start : start + len(term)],
        mark_safe(escape(text[start + len(term) :])),
    )


def _num(value) -> str:
    """ "1288.4000" -> "1,288.4", "4E+1" -> "40": a figure as a person writes it."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    if number == number.to_integral_value():
        return f"{int(number):,}"
    return f"{number.normalize():,f}"


def _plural(count, noun: str) -> str:
    return f"{_num(count)} {noun}{'' if Decimal(str(count)) == 1 else 's'}"


CHASE_GROUPS = (
    ("supplier", "Suppliers to chase"),
    ("partner", "Partners to chase"),
    ("internal", "Ours to answer"),
)


def _who(check: dict) -> str:
    """Whom to call: the supplier the check names, else the name its label leads with."""
    named = ((check.get("facts") or {}).get("supplier") or {}).get("name") or ""
    if named:
        return named
    label = (check.get("subject") or {}).get("label") or ""
    if check.get("kind") in _NAMED_BY_SUBJECT:
        return label
    if check.get("kind") == "commitment_open":
        return ((check.get("facts") or {}).get("owed_to") or {}).get("name") or ""
    if check.get("audience") == "supplier" and " — " in label[1:]:
        # "DABS — RUTF" leads with the company; "DEMO-SHP-001 — RUTF — DABS" leads with a code and ends with it.
        parts = [part.strip() for part in label.split(" — ")]
        return parts[-1] if _looks_like_code(parts[0]) else parts[0]
    return ""


def ask_for(kind: str, audience: str, who: str, label: str) -> str:
    """What this item needs, as one sentence."""
    template = ASK.get(kind)
    if template is None:
        return label
    return " ".join(template.format(who=who or _AUDIENCE_NOUN.get(audience, "")).split())


def _days_since(iso: str | None, today: date) -> int | None:
    if not iso:
        return None
    return (today - date.fromisoformat(iso)).days


def _about(label: str, who: str) -> str:
    """The subject's label without any record code and without the name already given as `who`."""
    parts = [part.strip() for part in (label or "").split(" — ")]
    kept = [part for part in parts if part and not _looks_like_code(part) and part != who]
    return " · ".join(kept)


def _gap(check: dict) -> tuple[Decimal, str, str] | None:
    """(size, unit, "below" or "above") of a count that disagrees with the records, or None."""
    facts = check.get("facts") or {}
    if check.get("kind") != "stock_variance" or not facts.get("reconcilable", True):
        return None
    try:
        variance = Decimal(str(facts["variance"]))
    except (KeyError, InvalidOperation, ValueError):
        return None
    if variance == 0:
        return None
    return abs(variance), facts.get("unit") or "", "below" if variance < 0 else "above"


def _money(facts: dict) -> str:
    if facts.get("amount") in (None, ""):
        return ""
    return f"{facts.get('currency') or ''} {_num(facts['amount'])}".strip()


def _duty_topics(check: dict) -> list[str]:
    """What a supplier is being asked for, from the reasons a cost cannot be worked out: duty, VAT, freight, price."""
    text = " ".join((check.get("facts") or {}).get("reasons") or []).lower()
    topics = []
    if "duty" in text:
        topics.append("duty")
    if "vat" in text:
        topics.append("VAT")
    if "freight" in text:
        topics.append("freight")
    if "unit price" in text or "included" in text or "quantity" in text:
        topics.append("price")
    return topics


def _describe(check: dict, who: str, place: dict, today: date) -> dict:
    """How one check reads on the list: audience, the next action, the plain statement, the amount, a hover.

    Routing (round 2): a count gap or missing duty and VAT is not "ours". The first goes to the partner that runs
    the place (its supervisor), the second to the supplier. Everything else keeps the audience the check carries.
    """
    kind = check["kind"]
    facts = check.get("facts") or {}
    audience = check["audience"]
    partner = place.get("managed_by") or ""
    subject = (check.get("subject") or {}).get("label") or ""
    out = {"audience": audience, "who": who, "hint_term": ""}

    if kind in _COUNT_GAPS and partner:
        gap = _gap(check)
        out["audience"], out["who"] = "partner", partner
        if kind == "stock_variance":
            out["ask"] = f"Ask {partner}'s supervisor to check {subject}'s count"
            out["what"] = (
                f"{subject}'s count is {_plural(gap[0], gap[1] or 'unit')} {gap[2]} the records"
                if gap
                else f"{subject}'s count cannot be compared with the records"
            )
            out["amount"] = f"{_plural(gap[0], gap[1] or 'unit')} {gap[2]}" if gap else ""
        elif kind == "stock_negative":
            out["ask"] = f"Ask {partner}'s supervisor how {subject} gave out more than they received"
            out["what"] = f"{subject} has given out more than was ever received"
            out["amount"] = ""
        else:
            out["ask"] = f"Ask {partner}'s supervisor to get {subject} to report their stock"
            out["what"] = f"{subject} has never reported their stock"
            out["amount"] = ""
        out["about"] = subject
        out["gap"] = gap
        return out

    if kind == "contract_cost_unconfirmed":
        topics = _duty_topics(check)
        if topics:
            out["audience"] = "supplier"
            # Its label ("DABS \u2014 RUTF") leads with the company, which `_who` only reads for a supplier's check.
            who = who or _who({**check, "audience": "supplier"})
            out["who"] = who
            noun = " and ".join(topics) if topics != ["price"] else "price"
            out["ask"] = f"Ask {who or 'the supplier'} for the {noun} details still missing, for the total cost"
            out["what"] = f"The {noun} details are missing, so the total cost cannot be worked out"
            out["about"] = _about(subject, who)
            return out

    if kind == "stock_expiring" and facts.get("expiry"):
        from connect_labs.supply_chain.stock.services.expiry import expiry_words

        # One rule for "Expired" and "Expiring soon" on every page (expiry_words): a batch in date is a warning
        # with a date to act by and no age; only a past date is expired, aged from the day it expired.
        said = expiry_words(date.fromisoformat(facts["expiry"]), today)
        quantity = facts.get("quantity") or ""
        number, _, unit = quantity.partition(" ")
        shown = _plural(number, unit) if number and unit else (quantity or "the stock")
        store = store_name(place.get("name") or subject.split(" \u2014 ")[0], place.get("kind"), partner)
        when = day_text(date.fromisoformat(facts["expiry"]))
        if said["expired"]:
            out["ask"] = f"{shown} at {store} expired {when}: take them off the shelf"
            out["days"], out["age_text"] = said["days"], ""
        else:
            out["ask"] = f"{shown} at {store} expire {when}: issue these first"
            out["days"], out["age_text"] = None, f"expires {when}"
        out["what"] = said["label"]
        out["amount"] = shown
        batch = f"batch {facts['batch']}" if facts.get("batch") else ""
        out["about"] = " \u00b7 ".join(part for part in (store, batch, said["when"]) if part)
        if partner:
            out["audience"], out["who"] = "partner", partner
        return out

    if kind == "shipment_without_certificate":
        tender = facts.get("tender") or ""
        delivery = f"the {tender} delivery" if tender else "this delivery"
        out["ask"] = f"Ask {who or 'the supplier'} for the batch certificate for {delivery}"
        out["what"] = SAYS[kind]
        out["hint_term"] = "batch certificate"
        out["about"] = _about(subject, who)
        return out

    if kind == "payment_unconfirmed":
        out["ask"] = ask_for(kind, audience, who, subject)
        out["what"] = SAYS[kind].format(who=who or "The supplier")
        out["amount"] = _money(facts)
        out["about"] = _about(subject, who)
        return out

    out["ask"] = ask_for(kind, audience, who, subject)
    out["about"] = _about(subject, who) if audience == "supplier" else subject
    return out


def chase_items(
    *,
    program_id: int,
    program_name: str,
    checks: list[dict],
    consignments: list[dict],
    orders: list[dict],
    suppliers: dict,
    places: dict,
    check_labels: dict,
    today: date | None = None,
    alert_failures: list[dict] = (),
    tenders: list[dict] = (),
    contracts: list[dict] = (),
    issues: list[dict] = (),
) -> list[dict]:
    """Every open thing in one programme that someone has to chase.

    `checks` are the wire form the map builds (`_check_wire`), `consignments`
    and `orders` the map's own lists, `suppliers` and `places` are keyed by id
    (a place carries `name`, `kind` and `managed_by`, the partner that runs it).
    """
    today = today or date.today()
    items = []

    def label_of(kind):
        return check_labels.get(kind) or kind.replace("_", " ")

    for check in checks:
        # Audiences the list has no section for ("ours": a check that is ours to chase) belong to "Ours to
        # answer": the header counts every item, and none may fall out of the groups unseen.
        if check["audience"] not in dict(CHASE_GROUPS):
            check = {**check, "audience": "internal"}
        who = _who(check)
        what = label_of(check["kind"])
        subject = check.get("subject") or {}
        place = places.get(subject.get("id")) if subject.get("type") == "supply_point" else None
        described = _describe(check, who, place or {}, today)
        items.append(
            {
                "kind": check["kind"],
                "audience": described["audience"],
                "who": described["who"],
                "what": described.get("what") or what,
                "ask": described["ask"],
                "about": described.get("about", (check.get("subject") or {}).get("label") or ""),
                "amount": described.get("amount", ""),
                "hint_term": described.get("hint_term", ""),
                "gap": described.get("gap"),
                "days": described["days"] if "days" in described else check.get("days_open"),
                "age_text": described.get("age_text", ""),
                "href": check.get("href") or "",
                "program": program_name,
                "program_id": program_id,
            }
        )

    for consignment in consignments:
        # A late one is already a check above; this adds the one no check can
        # see -- dispatched with no arrival date given.
        if consignment.get("expected_on"):
            continue
        origin = places.get(consignment["from_supply_point_id"]) or {}
        destination = places.get(consignment["to_supply_point_id"]) or {}
        who = origin.get("managed_by") or ""
        items.append(
            {
                "kind": "consignment_no_date",
                "audience": "internal",
                "who": who,
                "what": "On the road with no arrival date",
                "ask": f"Ask {who} when the stock will arrive" if who else "Find out when the stock will arrive",
                "about": f"{store_name(origin.get('name', ''), origin.get('kind'), origin.get('managed_by'))} → "
                f"{store_name(destination.get('name', ''), destination.get('kind'), destination.get('managed_by'))}",
                "amount": _plural(consignment["quantity"], consignment["quantity_unit"]),
                "days": _days_since(consignment.get("dispatched_on"), today),
                "href": consignment.get("receive_url") or "",
                "program": program_name,
                "program_id": program_id,
            }
        )

    for order in orders:
        if order.get("expected_on"):
            continue  # a late order is already a check above
        supplier = suppliers.get(order["supplier_id"]) or {}
        who = supplier.get("name") or ""
        items.append(
            {
                "kind": "order_no_date",
                "audience": "supplier",
                "who": who,
                "what": "Owed, with no arrival date promised",
                "ask": f"Ask {who or 'the supplier'} for an arrival date",
                "about": order.get("reference") or f"Order #{order['contract_id']}",
                "amount": "",
                "days": None,
                "href": order.get("url") or "",
                "program": program_name,
                "program_id": program_id,
            }
        )
    items += _partner_items(tenders, contracts, issues, program_name, program_id)
    # An alert that could not send (a sender's connection expired, no chatbot, no address) sits in the alert
    # log otherwise: `alert_failures` is its own source (alerts.service.failed_alert_items), so wording and
    # routing of the rest can change without losing it.
    items.extend(alert_failures)
    return items


def _partner_items(tenders, contracts, issues, program_name, program_id) -> list[dict]:
    """Things only a local partner (LLO) can do next: order its share of a split tender, report on a diversion.

    "Savanna hasn't ordered" is the partner's to do, not ours: the supplier is waiting on the partner, and so are
    we. An issue about stock taken or missing at a partner's place is followed up with that partner.
    """
    from connect_labs.supply_chain.procurement.services.splits import order_set, splits_for

    items = []
    for tender in tenders:
        if tender.get("status") != "awarded" or not splits_for(tender):
            continue
        mine = [c for c in contracts if c.get("tender_id") == tender.get("id")]
        waiting_orgs = set(order_set(tender, mine)["waiting_org_ids"])
        for split in splits_for(tender):
            if split["org_id"] not in waiting_orgs:
                continue
            quantity = f"{split.get('quantity')} {split.get('quantity_unit') or ''}".strip()
            items.append(
                {
                    "audience": "partner",
                    "who": split["org_name"],
                    "what": "Has not ordered yet",
                    "ask": f"Ask {split['org_name']} to place their order for {tender.get('label') or 'the tender'}",
                    "about": f"{tender.get('label') or 'Tender'} · {quantity}",
                    "days": None,
                    "href": "",
                    "program": program_name,
                    "program_id": program_id,
                }
            )
    for issue in issues:
        if issue.get("kind") not in ("taken", "missing") or not issue.get("partner"):
            continue
        if issue["partner"] == "no partner recorded":
            continue
        items.append(
            {
                "audience": "partner",
                "who": issue["partner"],
                "what": "Stock issue still open",
                "ask": f"Ask {issue['partner']} for an update: {issue['title']}",
                "about": issue.get("status") or "",
                "days": issue.get("days_open"),
                "href": issue.get("href") or "",
                "program": program_name,
                "program_id": program_id,
            }
        )
    return items


def _oldest_first(items: list[dict]) -> list[dict]:
    """An item with no age sorts last: not knowing how long it has waited is not the same as being new."""
    return sorted(items, key=lambda i: -(i["days"] if i["days"] is not None else -1))


def _merge_gaps(who: str, items: list[dict]) -> dict:
    """One row for a partner's FLW count gaps: "North Star: 5 FLWs 7-9 sachets below records"."""
    gaps = [i["gap"] for i in items if i.get("gap")]
    head = {**items[0], "days": max((i["days"] for i in items if i["days"] is not None), default=None)}
    names = [i["about"] for i in items]
    count = _plural(len(items), "FLW")
    head["parts"] = [
        {"text": i["what"], "href": i["href"], "days": i["days"], "amount": i.get("amount", "")} for i in items
    ]
    head["ask"] = f"Ask {who}'s supervisor to recount: {count} to check"
    head["about"] = ", ".join(names)
    if gaps:
        unit = gaps[0][1] or "unit"
        lo, hi = min(g[0] for g in gaps), max(g[0] for g in gaps)
        sizes = _num(lo) if lo == hi else f"{_num(lo)}\u2013{_num(hi)}"
        direction = gaps[0][2] if len({g[2] for g in gaps}) == 1 else "different from"
        noun = unit if hi == 1 else f"{unit}s"
        head["what"] = f"{who}: {count} {sizes} {noun} {direction} records"
        head["amount"] = f"{sizes} {noun} {direction}"
    else:
        head["what"] = f"{who}: {count} to check against the records"
        head["amount"] = ""
    head["href"] = ""
    return head


def _merge_per_contact(who: str, items: list[dict]) -> dict:
    """One row for everything to ask one supplier or partner, so a single message covers it."""
    ordered = _oldest_first(items)
    # Identical asks (three orders with prices missing) read once, with how many.
    seen: dict[str, dict] = {}
    for item in ordered:
        slot = seen.setdefault(item["ask"], {**item, "count": 0})
        slot["count"] += 1
    parts = list(seen.values())
    head = dict(ordered[0])
    head["days"] = max((i["days"] for i in items if i["days"] is not None), default=None)
    head["parts"] = [
        {
            "text": p["ask"] + (f" ({p['count']} orders)" if p["count"] > 1 else ""),
            "href": p["href"] if p["count"] == 1 else "",
            "days": p["days"],
            "amount": p.get("amount", ""),
            "hint_term": p.get("hint_term", ""),
        }
        for p in parts
    ]
    if len(parts) == 1:
        only = parts[0]
        head["ask"] = only["ask"] + (f" ({only['count']} orders)" if only["count"] > 1 else "")
        head["parts"] = []
        head["href"] = only["href"] if only["count"] == 1 else ""
        head["about"] = only["about"] if only["count"] == 1 else ""
    else:
        head["ask"] = f"Ask {who}: {len(parts)} things to cover in one message"
        head["what"] = f"{len(items)} open items with {who}"
        head["about"] = ""
        head["href"] = ""
        head["amount"] = ""
        head["hint_term"] = ""
    return head


def merge_rows(items: list[dict]) -> list[dict]:
    """Collapse items to one row per supplier or partner, so one email covers them (round 2, "Group items").

    FLW count gaps become one row per partner with the size of the gap; everything else for the same company
    becomes one row listing each ask. An item that names nobody is only merged with an identical one.
    """
    rows: list[dict] = []
    keyed: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for item in items:
        if item["audience"] == "partner" and item.get("gap") is not None and item["who"]:
            key = ("gaps", item["audience"], item["who"])
        elif item["who"] and item["audience"] in ("supplier", "partner"):
            key = ("who", item["audience"], item["who"])
        else:
            key = ("ask", item["audience"], item["ask"], item["who"])
        if key not in keyed:
            order.append(key)
        keyed.setdefault(key, []).append(item)
    for key in order:
        group = keyed[key]
        if key[0] == "gaps":
            rows.append(_merge_gaps(key[2], group))
        elif key[0] == "who" and (len(group) > 1):
            rows.append(_merge_per_contact(key[2], group))
        elif key[0] == "ask" and len(group) > 1:
            head = dict(group[0])
            head["ask"] = f"{head['ask']} ({len(group)} items)"
            head["days"] = max((i["days"] for i in group if i["days"] is not None), default=None)
            head["href"] = ""
            rows.append(head)
        else:
            rows.append(group[0])
    return rows


# What a run of the same kind of line is called once it is one line: (the heading, what each one is).
CLUSTERS = {
    "stock_expiring": ("Stock expiring soon or already expired", "lot"),
}
# A kind this often on one list is read as one thing with a count; fewer stay as the lines they are.
CLUSTER_FROM = 3


def _cluster_repeats(rows: list[dict]) -> list[dict]:
    """Rows of one kind, three or more, as one row with a count and the lines behind it.

    "12 jerrycans expire 4 Dec: issue these first" sixteen times is one finding sixteen times over;
    it reads once, "Stock expiring soon or already expired: 16 lots", with each lot a click away
    (the row's `parts`, which the chase list already folds). A row that already stands for several
    (a supplier's or partner's message, with `parts`) is never folded again, and the oldest
    stays first.
    """
    by_kind: dict[str, list[dict]] = {}
    for row in rows:
        if not row.get("parts") and row.get("kind"):
            by_kind.setdefault(row["kind"], []).append(row)
    folded = {kind for kind, same in by_kind.items() if len(same) >= CLUSTER_FROM}
    if not folded:
        return rows
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        kind = row.get("kind")
        if kind not in folded or row.get("parts"):
            out.append(row)
            continue
        if kind in seen:
            continue
        seen.add(kind)
        same = by_kind[kind]
        heading, noun = CLUSTERS.get(kind, (kind.replace("_", " ").capitalize(), "item"))
        head = dict(same[0])
        head["ask"] = f"{heading}: {_plural(len(same), noun)}"
        head["what"] = f"{_plural(len(same), noun)}, oldest first"
        head["about"] = ""
        head["href"] = ""
        head["amount"] = ""
        head["hint_term"] = ""
        head["days"] = max((i["days"] for i in same if i["days"] is not None), default=None)
        head["age_text"] = ""
        head["parts"] = [
            {"text": i["ask"], "href": i["href"], "days": i["days"], "amount": i.get("amount", "")} for i in same
        ]
        out.append(head)
    return out


def grouped(items: list[dict]) -> list[dict]:
    """The rows by audience, oldest first, empty groups left out, one row per supplier or partner.

    An item with no age sorts last: not knowing how long it has waited is not
    the same as being new.
    """
    out = []
    known = {audience for audience, _ in CHASE_GROUPS}
    for audience, label in CHASE_GROUPS:
        # An item of an audience with no section of its own is still shown, under "Ours to answer".
        mine = [
            i for i in items if i["audience"] == audience or (audience == "internal" and i["audience"] not in known)
        ]
        group = _cluster_repeats(_oldest_first(merge_rows(mine)))
        for row in group:
            # The template reads these on every row and every part; a missing key is an error there.
            for slot in (row, *row.get("parts", [])):
                slot.setdefault("hint_term", "")
                slot.setdefault("amount", "")
                slot.setdefault("age_text", "")
        if group:
            out.append({"audience": audience, "label": label, "items": group})
    return out
