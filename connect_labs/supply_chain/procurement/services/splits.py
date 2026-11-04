"""A tender split by partner, and the set of orders that follows it.

Sophie Feintuch's RUTF feedback, "Split tenders by partner": a programme that sources on behalf of
partners names, per partner, a quantity, a delivery address (the partner's warehouse) and who
receives it. The supplier quotes per delivery or says the price holds for the split; the award and
orders follow the split, one order per partner. This revisits the earlier decision "one total
quantity per tender", for programmes that source for partners only: a tender with no split is
unchanged.

A split is stored on the tender (`Tender.partner_splits`) as a list of

    {key, org_id, org_name, commodity_slug, quantity, quantity_unit, delivery_point_key,
     receiver_name, receiver_contact, buying_model, supply_point_id}

and the tender's line quantity is the sum of its splits for that product.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.utils.text import slugify

from connect_labs.supply_chain import records
from connect_labs.supply_chain.values import quantity_digits, unit_noun


def _number(value, what):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"{what} {value!r} is not a number") from exc
    if number <= 0:
        raise ValueError(f"{what} must be more than zero")
    return number


def _plain(number: Decimal) -> str:
    """A decimal as plain digits: 2000, not 2E+3."""
    return format(number.normalize(), "f") if number != number.to_integral() else str(int(number))


def _text(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def apply_splits(access, splits, lines, delivery_points):
    """(splits, lines, delivery_points) with the splits checked and the lines and places made to agree.

    Each split names a partner organisation that exists, a product on the tender, a positive
    quantity and a delivery place (an existing place by `delivery_point_key`, or a new one as
    `delivery_point` {name, city, country}). One split per partner and product. The tender's line
    quantity becomes the total of its splits. An empty list removes the split.
    """
    lines = [dict(line) for line in lines or []]
    points = [dict(p) for p in delivery_points or []]
    if not splits:
        return [], lines, points
    slugs = [line.get("commodity_slug") for line in lines if line.get("commodity_slug")]
    out, seen = [], set()
    for raw in splits:
        org = access.get_org(raw.get("org_id")) if raw.get("org_id") is not None else None
        if org is None:
            raise ValueError(f"a split needs a partner organisation that exists (org_id {raw.get('org_id')!r})")
        slug = raw.get("commodity_slug") or (slugs[0] if len(slugs) == 1 else None)
        if slug not in slugs:
            raise ValueError(f"a split must name one of the tender's products ({', '.join(slugs) or 'none yet'})")
        if (org.pk, slug) in seen:
            raise ValueError(f"{org.name} is split twice for {slug}: one split per partner and product")
        seen.add((org.pk, slug))
        quantity = _number(raw.get("quantity"), f"{org.name}'s quantity")
        line = next(line for line in lines if line.get("commodity_slug") == slug)
        unit = _text(raw.get("quantity_unit")) or line.get("quantity_unit") or ""
        if line.get("quantity_unit") and unit != line["quantity_unit"]:
            raise ValueError(f"{org.name}'s quantity is in {unit} but the tender asks in {line['quantity_unit']}")
        key = _text(raw.get("delivery_point_key"))
        place = raw.get("delivery_point")
        if place and place.get("name"):
            key = key or slugify(f"{org.name}-{place['name']}")[:60]
            if not any(p.get("key") == key for p in points):
                points.append({"key": key, **{k: v for k, v in place.items() if v}})
        if key and not any(p.get("key") == key for p in points):
            raise ValueError(f"{org.name}'s delivery place {key!r} is not one of the tender's places")
        model = raw.get("buying_model") or "partner_buys"
        if model not in records.BUYING_MODELS:
            raise ValueError(f"buying_model must be one of {', '.join(records.BUYING_MODELS)}")
        out.append(
            {
                "key": f"{org.pk}-{slug}",
                "org_id": org.pk,
                "org_name": org.name,
                "commodity_slug": slug,
                "quantity": _plain(quantity),
                "quantity_unit": unit,
                "delivery_point_key": key,
                "receiver_name": _text(raw.get("receiver_name")),
                "receiver_contact": _text(raw.get("receiver_contact")),
                "buying_model": model,
                "supply_point_id": raw.get("supply_point_id"),
            }
        )
    for slug in {s["commodity_slug"] for s in out}:
        total = sum(Decimal(s["quantity"]) for s in out if s["commodity_slug"] == slug)
        for line in lines:
            if line.get("commodity_slug") == slug:
                line["quantity"] = _plain(total)
                line["quantity_unit"] = line.get("quantity_unit") or next(
                    s["quantity_unit"] for s in out if s["commodity_slug"] == slug
                )
    return out, lines, points


def splits_for(tender: dict, commodity_slug=None) -> list[dict]:
    return [
        s
        for s in (tender or {}).get("partner_splits") or []
        if commodity_slug is None or s.get("commodity_slug") == commodity_slug
    ]


def split_price_status(tender: dict, quote: dict) -> dict:
    """What a quote says about its price holding when the tender is bought as several purchases.

    {"purchases": how many the tender is split into, "stated": N the supplier said it holds for or None,
     "holds": True / False / None (None = not said), "note"}.
    Data, not a verdict: a supplier who has not said is "not said", never "no".
    """
    needed = len(splits_for(tender, quote.get("commodity_slug")))
    stated = quote.get("price_holds_for_purchases")
    if needed < 2:
        return {"purchases": needed, "stated": stated, "holds": None, "note": ""}
    if stated is None:
        return {
            "purchases": needed,
            "stated": None,
            "holds": None,
            "note": f"Not said whether the price holds if bought as {needed} purchases",
        }
    holds = stated >= needed
    return {
        "purchases": needed,
        "stated": stated,
        "holds": holds,
        "note": (
            f"Price holds if bought as {needed} purchases"
            if holds
            else f"Price holds for up to {stated} purchases; this tender needs {needed}"
        ),
    }


def order_set(tender: dict, contracts: list[dict], commodity_slug=None) -> dict:
    """The orders of one award as a set: "4 of 5 partners have ordered · 1,800 of 2,000 cartons".

    Works from the tender's splits when it has them, else from its single line quantity. An award is
    not done after its first order: `complete` is true only when every partner has ordered and the
    quantity ordered reaches the quantity sought.
    """
    splits = splits_for(tender, commodity_slug)
    lines = [
        line
        for line in (tender or {}).get("lines") or []
        if commodity_slug is None or line.get("commodity_slug") == commodity_slug
    ]
    live = [
        c
        for c in contracts
        if c.get("status") != "cancelled" and (commodity_slug is None or c.get("commodity_slug") == commodity_slug)
    ]
    unit = (splits[0].get("quantity_unit") if splits else None) or (lines[0].get("quantity_unit") if lines else "")
    if splits:
        total = sum(Decimal(str(s["quantity"])) for s in splits)
    else:
        total = sum((Decimal(str(line["quantity"])) for line in lines if line.get("quantity") not in (None, "")), 0)
    ordered = sum((Decimal(str(c["quantity"])) for c in live if c.get("quantity") not in (None, "")), Decimal(0))
    ordered_orgs = {c.get("partner_org_id") or c.get("buyer_org_id") for c in live}
    waiting = [s for s in splits if s["org_id"] not in ordered_orgs]
    partners_total = len(splits)
    partners_ordered = partners_total - len(waiting)
    complete = bool(live) and ordered >= total and not waiting if total else bool(live) and not waiting
    parts = []
    if splits:
        parts.append(f"{partners_ordered} of {partners_total} partners have ordered")
    if total:
        parts.append(f"{quantity_digits(ordered)} of {quantity_digits(total)} {unit_noun(unit, total)}".strip())
    return {
        "partners_total": partners_total or None,
        "partners_ordered": partners_ordered if splits else None,
        "waiting_partners": [s["org_name"] for s in waiting],
        "waiting_org_ids": [s["org_id"] for s in waiting],
        "quantity_total": str(total),
        "quantity_ordered": str(ordered),
        "unit": unit,
        "complete": complete,
        "text": " · ".join(parts),
    }
