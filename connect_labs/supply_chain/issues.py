"""The Overview's reader for open stock issues (the hook `overview.open_issues` looks for).

`open_issues(program_id)` returns one row per issue still being dealt with: what it is, which partner
runs the place, its status, how long it has been open and where to follow it up.
"""

from datetime import date

from django.urls import reverse

from connect_labs.supply_chain.models import StockIssue
from connect_labs.supply_chain.naming import store_name
from connect_labs.supply_chain.stock.services import posting
from connect_labs.supply_chain.stock.services.expiry import expiry_words
from connect_labs.supply_chain.stock.services.issues import NO_PARTNER, partner_by_point
from connect_labs.supply_chain.values import plain_number

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
        kind, when, days_open = issue.get_kind_display().capitalize(), "", (today - issue.occurred_on).days
        if issue.kind in ("expired", "expiring"):
            # The same words and the same age as the chase list says for this batch (expiry_words).
            expiry = posting.batch_expiry(issue)
            if expiry is not None:
                said = expiry_words(expiry, today)
                kind, when, days_open = said["label"], said["when"], None
            elif issue.kind == "expiring":
                kind = "Expiring soon"
        out.append(
            {
                "title": (
                    f"{kind}: {plain_number(issue.quantity)} {issue.quantity_unit} "
                    f"of {issue.commodity.name} at "
                    f"{store_name(issue.supply_point.name, issue.supply_point.kind, names.get(org_id))}"
                ),
                "partner": names.get(org_id, NO_PARTNER) if org_id else NO_PARTNER,
                "status": _STATUS.get(issue.status, issue.status),
                "days_open": days_open,
                "when": when,
                # A warning: still in date, nothing has come off stock.
                "warning": issue.kind == "expiring",
                "kind": issue.kind,
                "href": f"{base}?partner={org_id}" if org_id else base,
            }
        )
    return out
