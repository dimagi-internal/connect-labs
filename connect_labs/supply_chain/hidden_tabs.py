"""A built-in supply tab a programme has turned off in Settings says so, rather than 404.

`process_view`, so it runs after every middleware's `process_request` -- the labs
context (which programme) is settled by then -- and before the tab's own view does
any work. Pages one level under a tab (a worker's page under Workers) count as
the tab, the same mapping the header uses to highlight it.
"""

from django.shortcuts import render
from django.urls import reverse

SUPPLY_PREFIX = "/supply/"
# Scripts on supply pages fetch these; they are not tabs.
EXEMPT_PREFIXES = ("/supply/api/",)


class HiddenSupplyTabMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        path = request.path
        if not path.startswith(SUPPLY_PREFIX) or path.startswith(EXEMPT_PREFIXES):
            return None
        if not getattr(getattr(request, "user", None), "is_authenticated", False):
            return None
        match = getattr(request, "resolver_match", None)
        if match is None or not match.view_name.startswith("supply_chain:"):
            return None
        from connect_labs.supply_chain.navigation import SUPPLY_TABS, TAB_FOR_VIEW, hidden_tabs

        tab = TAB_FOR_VIEW.get(match.view_name, match.view_name)
        if tab not in hidden_tabs(request):
            return None
        from connect_labs.supply_chain.banner import program_line
        from connect_labs.supply_chain.navigation import supply_tabs

        program_id = (getattr(request, "labs_context", None) or {}).get("program_id")
        return render(
            request,
            "supply_chain/tab_hidden.html",
            {
                # The supply header, as every supply page has it.
                "supply_tabs": supply_tabs(request),
                "supply_program_line": program_line(request),
                "tab_label": dict(SUPPLY_TABS).get(tab, tab),
                "settings_url": (
                    reverse("labs:settings", args=["program", program_id]) + "#supply" if program_id else None
                ),
                "home_url": reverse("supply_chain:home") + (f"?program_id={program_id}" if program_id else ""),
            },
        )
