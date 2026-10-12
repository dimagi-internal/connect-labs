"""Where /supply/ opens for the programme in view.

Overview is about buying: tenders, orders, what the record cannot answer. It
holds no stock. A programme whose stock sits with its field workers is run
day to day from what those workers hold, so it opens on its workers' tab --
Stock review, where a workflow fills that tab (config.py) -- and keeps Overview
one tab away at /supply/overview/.

The `landing` setting overrides that either way, set by the organisation or the
programme (config.py). /supply/ is the front door; Overview's own tab and the
breadcrumbs that name it link to /supply/overview/, so going back to it never
bounces you onward.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.urls import reverse

from connect_labs.supply_chain.config import HOME, resolved_for, slug_of
from connect_labs.supply_chain.navigation import SUPPLY_TABS

# The built-in Workers page is what each worker holds; a programme that fills
# that tab with a workflow (its Stock review) opens on the workflow instead.
FIELD_LANDING = "supply_chain:workers"


def holds_stock_in_the_field(program_id: int) -> bool:
    """Whether any active supply point in the programme is a field worker's own holding.

    A worker is a supply point (`kind="user_held"`, models.SupplyPoint), and every
    way stock reaches the field makes one: a distribution to a worker, a
    dispensing rule reading visits, a worker's own count. A dispensing rule alone
    would miss a programme that hands workers stock without reading their visits.
    A positive balance would need the ledger summed on every visit to /supply/,
    and would send a programme back to Overview the day its workers ran dry --
    the day Stock review matters most.
    """
    from connect_labs.supply_chain.models import SupplyPoint

    return SupplyPoint.objects.filter(program_id=program_id, kind="user_held", status="active").exists()


def landing_url(request) -> str | None:
    """The address /supply/ should send this request to, or None to show Overview there."""
    program_id = (getattr(request, "labs_context", None) or {}).get("program_id")
    try:
        program_id = int(program_id)
    except (TypeError, ValueError):
        return None
    settings = resolved_for(request, program_id)
    key = settings.get("landing") or (FIELD_LANDING if holds_stock_in_the_field(program_id) else HOME)
    if key == HOME:
        return None
    tab = (settings.get("tabs") or {}).get(key) or {}
    if tab.get("hidden"):
        return None
    if tab.get("fill"):
        path = reverse("supply_chain:workflow_view", args=[slug_of(key, tab)])
    elif key in dict(SUPPLY_TABS):
        path = reverse(key)
    else:
        return None
    query = request.GET.copy()
    query["program_id"] = str(program_id)
    return f"{path}?{urlencode(sorted(query.lists()), doseq=True)}"
