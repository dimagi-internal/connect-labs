"""Where each partner's order of a split tender stands, in words.

Sophie Feintuch's round-2 walk of the demo, "Split tender page": the page said "done" though one
partner had not ordered and another's delivery was in transit. This reads the orders the way the
Overview's buying table does (`standing._order_rows`), so the two pages use one vocabulary
("In transit · expected 20 Nov", "Received"), and counts the set: "Ordered 2 of 3 · Delivered 1 of 3".
"""

from __future__ import annotations

from datetime import date

from connect_labs.supply_chain.values import quantity_phrase


def order_words(program_id, contract_ids, today=None) -> dict:
    """{contract_id: (words, delivered)}: each order's step name as the Overview writes it.

    One status for an order wherever it is shown: the award page, the tender page and the Overview
    all read this. Quiet when there is nothing to read.
    """
    ids = [i for i in contract_ids if i]
    if not ids:
        return {}
    from connect_labs.supply_chain.standing import _order_rows

    rows = _order_rows(program_id, today or date.today(), None, None, only=ids, keep_done=True)
    return {r.contract_id: (r.label, r.stage_index == 6) for r in rows}


def place_name(tender, split: dict) -> str:
    """The delivery place of a split by its name ("North Star warehouse, Kano"), never its key ("ns-wh")."""
    key = split.get("delivery_point_key")
    for point in tender.delivery_points or []:
        if isinstance(point, dict) and point.get("key") == key:
            name = ", ".join(x for x in (point.get("name"), point.get("city")) if x)
            if name:
                return name
    if split.get("supply_point_id"):
        from connect_labs.supply_chain.models import SupplyPoint

        found = SupplyPoint.objects.filter(pk=split["supply_point_id"]).values_list("name", flat=True).first()
        if found:
            return found
    return str(key or "").replace("-", " ").replace("_", " ").strip().capitalize()


def buying_words(model: str) -> str:
    """How the partner's share is bought, in the words a programme manager uses."""
    return {
        "programme_buys": "We buy and ship to the partner",
        "partner_buys": "The partner buys, on our negotiated quote",
    }.get(model or "", (model or "").replace("_", " ").capitalize())


def split_progress(tender, today=None) -> dict | None:
    """The split tender's partners with their orders, and the count of the set. None for an ordinary tender.

    {"partners": [{org_name, quantity (phrase), place, buying, ordered, reference, contract_id, url,
                   status, delivered}],
     "ordered", "delivered", "total", "waiting": [names], "words", "to_do": ["Savanna to order", ...]}
    """
    splits = [s for s in tender.partner_splits or [] if isinstance(s, dict)]
    if not splits:
        return None
    from django.urls import reverse

    from connect_labs.supply_chain.models import Contract

    contracts = list(Contract.objects.filter(tender=tender).exclude(status="cancelled").order_by("pk"))
    by_org = {}
    for c in contracts:
        by_org.setdefault(c.partner_org_id or c.buyer_org_id, c)
    states = order_words(tender.program_id, [c.pk for c in by_org.values()], today)
    partners = []
    for split in splits:
        contract = by_org.get(split.get("org_id"))
        status, delivered = states.get(contract.pk, ("", False)) if contract else ("", False)
        partners.append(
            {
                "org_id": split.get("org_id"),
                "org_name": split.get("org_name") or "",
                "quantity": (
                    quantity_phrase(split.get("quantity"), split.get("quantity_unit"))
                    if split.get("quantity") not in (None, "")
                    else ""
                ),
                "place": place_name(tender, split),
                "buying": buying_words(split.get("buying_model")),
                "receiver": split.get("receiver_name") or "",
                "receiver_contact": split.get("receiver_contact") or "",
                "ordered": contract is not None,
                "reference": (contract.reference or f"Order {contract.pk}") if contract else "",
                "contract_id": contract.pk if contract else None,
                "url": reverse("supply_chain:order_detail", args=[contract.pk]) if contract else "",
                "status": status or ("Not ordered yet" if contract is None else "Ordered"),
                "delivered": delivered,
            }
        )
    ordered = sum(1 for p in partners if p["ordered"])
    delivered = sum(1 for p in partners if p["delivered"])
    total = len(partners)
    short = [p["org_name"] for p in partners if not p["ordered"]]
    return {
        "partners": partners,
        "ordered": ordered,
        "delivered": delivered,
        "total": total,
        "waiting": [p["org_name"] for p in partners if not p["ordered"]],
        "words": f"Ordered {ordered} of {total} · Delivered {delivered} of {total}",
        "to_do": [f"{name} to order" for name in short],
    }
