"""Per-partner first, with a programme roll-up: the one place a page learns which partner it is showing.

A partner's own page is the same page filtered by `?partner=<organisation id>`, the way the Issues
page does it (stock/issue_views.py). No new access model: who may SEE a programme is unchanged
(`org_access`, the running organisation, `runner/`); this only narrows what a page lists. A place
belongs to the partner that runs it, else the nearest one above it in the network
(`stock.services.issues.partner_by_point`).
"""

from connect_labs.supply_chain.stock.services.issues import partner_by_point


def partner_param(request):
    value = request.GET.get("partner") or ""
    return int(value) if value.isdigit() else None


def point_ids_of(program_id, org_id) -> set:
    """Every supply point `org_id` runs, including the field workers below its places."""
    return {pk for pk, owner in partner_by_point(program_id).items() if owner == org_id}


def switcher(request, program_id, names=None) -> dict:
    """What the 'Partner: All | A | B' strip needs: the partners that run something, and the selected one.

    Each link keeps the rest of the page's query (the product chosen, a past day) and swaps only `partner`.
    """
    from connect_labs.labs.models import LabsOrg

    selected = partner_param(request)

    owners = {org for org in partner_by_point(program_id).values() if org}
    if selected:
        owners.add(selected)
    if names is None:
        names = dict(LabsOrg.objects.filter(pk__in=owners).values_list("pk", "name"))
    listed = sorted(({"org_id": pk, "name": names.get(pk) or f"Organisation {pk}"} for pk in owners), key=_by_name)

    def link(org_id):
        query = request.GET.copy()
        query.pop("partner", None)
        if org_id:
            query["partner"] = str(org_id)
        encoded = query.urlencode()
        return f"{request.path}?{encoded}" if encoded else request.path

    for row in listed:
        row["href"] = link(row["org_id"])
    return {
        "partner_options": listed,
        "partner_id": selected,
        "partner_name": next((p["name"] for p in listed if p["org_id"] == selected), None),
        "partner_all_href": link(None),
    }


def _by_name(row):
    return row["name"].lower()
