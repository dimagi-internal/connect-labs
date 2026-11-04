"""The Overview's reader for open stock issues (the hook `overview.open_issues` looks for).

`open_issues(program_id)` returns one row per issue still being dealt with: what it is, which partner
runs the place, its status, how long it has been open and where to follow it up.
"""

from datetime import date

from django.urls import reverse

from connect_labs.supply_chain.models import StockIssue
from connect_labs.supply_chain.stock.services.issues import NO_PARTNER, partner_by_point

_STATUS = {"open": "not yet looked at", "following_up": "being followed up"}


def open_issues(program_id) -> list[dict]:
    from connect_labs.labs.models import LabsOrg

    owner = partner_by_point(program_id)
    rows = list(
        StockIssue.objects.filter(program_id=program_id)
        .exclude(status="resolved")
        .select_related("supply_point", "commodity")
        .order_by("occurred_on", "pk")
    )
    names = dict(LabsOrg.objects.filter(pk__in={o for o in owner.values() if o}).values_list("pk", "name"))
    today = date.today()
    out = []
    for issue in rows:
        org_id = owner.get(issue.supply_point_id)
        base = reverse("supply_chain:issues")
        out.append(
            {
                "title": (
                    f"{issue.get_kind_display().capitalize()}: {issue.quantity.normalize()} {issue.quantity_unit} "
                    f"of {issue.commodity.name} at {issue.supply_point.name}"
                ),
                "partner": names.get(org_id, NO_PARTNER) if org_id else NO_PARTNER,
                "status": _STATUS.get(issue.status, issue.status),
                "days_open": (today - issue.occurred_on).days,
                "href": f"{base}?partner={org_id}" if org_id else base,
            }
        )
    return out
