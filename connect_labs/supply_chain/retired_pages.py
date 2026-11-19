"""The pages round 3's menu replaced, sent to the page that carries their job.

Network, Stock and Workers are Stock by LLO; Distribution and Plan are This week. Each old address still answers,
with a redirect, so a saved link or an email already sent keeps working; an LLO named in `?partner=` lands on that
LLO's page.
Only a plain GET is redirected (a form posting to one of these addresses is untouched), and not for a program that
chose the compact layout, whose menu still has them. Pages that remain are the records and working pages the new ones
link to: a movement, Where it went, the map, a handout form, an FLW's own page.
"""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

SUPPLY_PREFIX = "/supply/"

# old view name -> (list page, the same LLO's page)
RETIRED = {
    "supply_chain:network": ("supply_chain:stock_by_llo", "supply_chain:llo_stock"),
    "supply_chain:stock": ("supply_chain:stock_by_llo", "supply_chain:llo_stock"),
    "supply_chain:workers": ("supply_chain:stock_by_llo", "supply_chain:llo_stock"),
    "supply_chain:plan_stores": ("supply_chain:stock_by_llo", "supply_chain:llo_stock"),
    "supply_chain:distribution": ("supply_chain:this_week", "supply_chain:llo_week"),
    "supply_chain:plan": ("supply_chain:this_week", "supply_chain:llo_week"),
}

# What the new pages do not take: the old pages' own filters.
DROPPED = ("partner", "item_id", "commodity")


class RetiredPagesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not getattr(settings, "SUPPLY_RETIRED_REDIRECTS", True):
            return None
        if request.method not in ("GET", "HEAD") or not request.path.startswith(SUPPLY_PREFIX):
            return None
        if not getattr(getattr(request, "user", None), "is_authenticated", False):
            return None
        match = getattr(request, "resolver_match", None)
        target = RETIRED.get(match.view_name) if match is not None else None
        if target is None:
            return None
        from connect_labs.supply_chain.navigation import compact_layout

        if compact_layout(request):
            return None
        partner = request.GET.get("partner") or ""
        if partner.isdigit() and target[1] != target[0]:
            path = reverse(target[1], args=[int(partner)])
        else:
            path = reverse(target[0])
        query = request.GET.copy()
        for key in DROPPED:
            query.pop(key, None)
        return redirect(f"{path}?{query.urlencode()}" if query else path)
