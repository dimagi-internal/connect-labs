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

from datetime import date

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
    "contract_cost_unconfirmed": "Get the price details still missing, so the total cost can be worked out",
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
        return label.split(" — ")[0]
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


def _expiring(check: dict, about: str, today: date):
    """(what, ask, about, days) for a batch near or past its expiry date.

    Said with the same words and the same age as the Overview's issues panel (`expiry_words`): a batch
    still in date is "Expiring soon" and has no age, only a date to act by; only a past date is "Expired",
    aged from the day it expired. (The age used to run from the start of the warning period.)
    """
    from connect_labs.supply_chain.stock.services.expiry import expiry_words

    facts = check.get("facts") or {}
    expiry = facts.get("expiry")
    if not expiry:
        return "Stock expiring soon or already expired", "Check the batch's expiry date", about, check.get("days_open")
    said = expiry_words(date.fromisoformat(expiry), today)
    quantity = facts.get("quantity") or "the stock"
    if said["expired"]:
        ask = f"Move or write off the {quantity} that expired: take it off the shelf so it is not handed out"
        return said["label"], ask, f"{about} · {said['when']}", said["days"]
    ask = f"Use the {quantity} first, or move it to where it will be used, before it expires"
    return said["label"], ask, f"{about} · {said['when']}", None


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
    (a place carries `name` and `managed_by`).
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
        ask = ask_for(check["kind"], check["audience"], who, what)
        about = (check.get("subject") or {}).get("label") or ""
        days = check.get("days_open")
        if check["kind"] == "stock_expiring":
            what, ask, about, days = _expiring(check, about, today)
        items.append(
            {
                "audience": check["audience"],
                "who": who,
                "what": what,
                "ask": ask,
                "about": about,
                "days": days,
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
                "audience": "internal",
                "who": who,
                "what": "On the road with no arrival date",
                "ask": f"Ask {who} when the stock will arrive" if who else "Find out when the stock will arrive",
                "about": f"{origin.get('name', '')} → {destination.get('name', '')} · "
                f"{consignment['quantity']} {consignment['quantity_unit']}",
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
                "audience": "supplier",
                "who": who,
                "what": "Owed, with no arrival date promised",
                "ask": f"Ask {who or 'the supplier'} for an arrival date",
                "about": order.get("reference") or f"Order #{order['contract_id']}",
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


def grouped(items: list[dict]) -> list[dict]:
    """The items by audience, oldest first, empty groups left out.

    An item with no age sorts last: not knowing how long it has waited is not
    the same as being new.
    """
    out = []
    known = {audience for audience, _ in CHASE_GROUPS}
    for audience, label in CHASE_GROUPS:
        group = sorted(
            # An item of an audience with no section of its own is still shown, under "Ours to answer".
            (i for i in items if i["audience"] == audience or (audience == "internal" and i["audience"] not in known)),
            key=lambda i: -(i["days"] if i["days"] is not None else -1),
        )
        if group:
            out.append({"audience": audience, "label": label, "items": group})
    return out
