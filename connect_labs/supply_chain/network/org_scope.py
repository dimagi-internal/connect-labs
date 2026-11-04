"""Which organisations matter to one programme, for the Organisations page's default list.

Organisations are one registry for all of labs, so the full list is every programme's. The page opens on the
ones this programme actually uses and offers the rest behind a "Show all" link. This only narrows what is
listed: no organisation is hidden from the places that need one (a buyer, a place's operator), and a
search or "Show all" reaches every row.

An organisation matters here when it is any of:
  - a partner with a plan (PartnerPlan), or one that runs a place (SupplyPoint.managed_by_org);
  - a buyer on an order (Contract.buyer_org);
  - a supplier of this programme (Supplier.org);
  - named on a tender: invited to it, or in one of its per-partner splits.
"""

from connect_labs.supply_chain.models import Contract, PartnerPlan, Supplier, SupplyPoint, Tender, scope_key


def org_ids_in_programme(program_id) -> set:
    ids = set()
    ids.update(PartnerPlan.objects.filter(program_id=program_id).values_list("org_id", flat=True))
    ids.update(
        SupplyPoint.objects.filter(program_id=program_id, managed_by_org__isnull=False).values_list(
            "managed_by_org_id", flat=True
        )
    )
    ids.update(
        Contract.objects.filter(program_id=program_id, buyer_org__isnull=False).values_list("buyer_org_id", flat=True)
    )
    ids.update(Supplier.objects.filter(scope_key=scope_key(program_id=program_id)).values_list("org_id", flat=True))
    for tender in Tender.objects.filter(program_id=program_id).prefetch_related("invited_orgs"):
        ids.update(org.pk for org in tender.invited_orgs.all())
        ids.update(s["org_id"] for s in tender.partner_splits or [] if s.get("org_id"))
    return ids


def narrow(orgs: list, program_id, *, show_all: bool, query: str = "") -> tuple[list, int]:
    """(the organisations to list, how many there are in all). `orgs` are `org_list` records."""
    total = len(orgs)
    if not show_all and program_id is not None:  # no programme chosen: nothing to narrow by
        mine = org_ids_in_programme(program_id)
        orgs = [o for o in orgs if o["id"] in mine]
    query = (query or "").strip().lower()
    if query:
        orgs = [
            o
            for o in orgs
            if query in (o.get("name") or "").lower()
            or query in (o.get("slug") or "").lower()
            or any(query in (a or "").lower() for a in o.get("aliases") or [])
        ]
    return orgs, total
