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
_AUDIENCE_NOUN = {"supplier": "the supplier", "partner": "the partner", "internal": ""}

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

AUDIENCES = (
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
        who = _who(check)
        what = label_of(check["kind"])
        items.append(
            {
                "audience": check["audience"],
                "who": who,
                "what": what,
                "ask": ask_for(check["kind"], check["audience"], who, what),
                "about": (check.get("subject") or {}).get("label") or "",
                "days": check.get("days_open"),
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
    return items


def grouped(items: list[dict]) -> list[dict]:
    """The items by audience, oldest first, empty groups left out.

    An item with no age sorts last: not knowing how long it has waited is not
    the same as being new.
    """
    out = []
    for audience, label in AUDIENCES:
        group = sorted(
            (i for i in items if i["audience"] == audience),
            key=lambda i: -(i["days"] if i["days"] is not None else -1),
        )
        if group:
            out.append({"audience": audience, "label": label, "items": group})
    return out
