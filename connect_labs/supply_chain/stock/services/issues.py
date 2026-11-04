"""What is open in the stock issues, in one place for every page and caller.

`open_issue_summary` is the one answer to "how many issues are open, and where":
the Overview, the Issues page and the `stock_issue_summary` operation all read
it, so the count on one cannot disagree with the list on another.

An issue belongs to the partner that runs the store it happened at. A store with
no organisation of its own takes the nearest one above it in the network (a
field worker's holding belongs to whoever runs the warehouse it is stocked from),
and an issue nobody runs is counted under "no partner recorded" rather than dropped.
"""

from collections import defaultdict
from datetime import date

from connect_labs.supply_chain.models import StockIssue, SupplyPoint

NO_PARTNER = "no partner recorded"


def partner_by_point(program_id) -> dict:
    """{supply point id: LabsOrg id or None}: who runs each point, inheriting from the point above it."""
    points = {
        p["id"]: p
        for p in SupplyPoint.objects.filter(program_id=program_id).values("id", "parent_id", "managed_by_org_id")
    }
    resolved: dict = {}

    def find(pk):
        seen = []
        while pk is not None and pk not in resolved:
            point = points.get(pk)
            if point is None:
                break
            if point["managed_by_org_id"] is not None:
                resolved[pk] = point["managed_by_org_id"]
                break
            seen.append(pk)
            pk = point["parent_id"]
        found = resolved.get(pk)
        for visited in seen:
            resolved[visited] = found
        return found

    return {pk: find(pk) for pk in points}


def open_issue_summary(program_id, *, opportunity_id=None, today=None) -> dict:
    """Counts of the issues still being dealt with, in total and per partner.

    `open` is not yet looked at, `following_up` is being chased; `total_open` is
    both. `oldest_days` is the age of the oldest unresolved issue (None when there
    are none). `by_partner` lists every partner with at least one, most first.
    """
    from connect_labs.labs.models import LabsOrg

    today = today or date.today()
    issues = StockIssue.objects.filter(program_id=program_id).exclude(status="resolved")
    if opportunity_id is not None:
        issues = issues.filter(opportunity_id=opportunity_id)
    issues = list(issues.select_related("supply_point"))
    owner = partner_by_point(program_id)

    by_status = defaultdict(int)
    by_kind = defaultdict(int)
    per_partner = defaultdict(lambda: {"open": 0, "following_up": 0})
    quantity = defaultdict(int)
    for issue in issues:
        by_status[issue.status] += 1
        by_kind[issue.kind] += 1
        per_partner[owner.get(issue.supply_point_id)][issue.status] += 1
        quantity[issue.quantity_unit] += issue.quantity

    names = dict(LabsOrg.objects.filter(pk__in=[k for k in per_partner if k is not None]).values_list("pk", "name"))
    partners = [
        {
            "org_id": org_id,
            "name": names.get(org_id, NO_PARTNER) if org_id is not None else NO_PARTNER,
            "total_open": counts["open"] + counts["following_up"],
            **counts,
        }
        for org_id, counts in per_partner.items()
    ]
    partners.sort(key=lambda row: (-row["total_open"], row["name"]))
    return {
        "total_open": len(issues),
        "open": by_status["open"],
        "following_up": by_status["following_up"],
        "oldest_days": max(((today - i.occurred_on).days for i in issues), default=None),
        "by_kind": dict(by_kind),
        "quantity_by_unit": {unit: str(total) for unit, total in quantity.items()},
        "by_partner": partners,
    }
