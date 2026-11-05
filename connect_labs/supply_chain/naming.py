"""How a place is named on screen: a store belongs to a local partner, so its name says so.

Round 2, "Name stores by partner everywhere": North Star's warehouse was just "Warehouse" on the Overview,
Issues, custodians, Plan and Where it went, so two partners' warehouses read the same. One helper, used by
every page, so they all say it the same way.

Only places that hold stock for a partner are prefixed. A field worker is already a person's name, a supplier's
site is the supplier's, and a place that is moving or at customs belongs to nobody yet.
"""

from __future__ import annotations

# Places whose name stands alone: a person, a supplier's site, a consignment on the road.
_UNPREFIXED_KINDS = ("user_held", "supplier_site", "in_transit", "customs")


def store_name(name: str, kind: str | None = None, partner: str | None = None) -> str:
    """ "North Star · Warehouse": the store's name led by the partner that runs it.

    Left alone when there is no partner, the place is not a store (see above), or the name already says it
    ("North Star warehouse").
    """
    name = (name or "").strip()
    partner = (partner or "").strip()
    if not name or not partner or kind in _UNPREFIXED_KINDS:
        return name
    if partner.lower() in name.lower():
        return name
    return f"{partner} · {name}"


def partner_of_point(point: dict, by_id: dict, partner_names: dict) -> str:
    """The name of the partner that runs this place: its own organisation, else the nearest above it with one.

    `point` and `by_id` are `supply_point_list` rows; `partner_names` maps an organisation id to its name.
    "" when nobody is named along the way.
    """
    seen = set()
    while point is not None and point["id"] not in seen:
        org_id = point.get("managed_by_org_id")
        if org_id:
            return partner_names.get(org_id, "")
        seen.add(point["id"])
        point = by_id.get(point.get("parent_supply_point_id"))
    return ""
