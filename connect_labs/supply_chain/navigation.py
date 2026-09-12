"""The domain's nav, declared once.

Its own module rather than a constant in views.py because the procurement
views deliberately do NOT inherit that module's `OperationBase` -- see their
docstring: importing it would make `call_operation` unpatchable from those
tests and let them run real operations against production. Nothing here
touches the domain, so it is safe for both view modules to import.

Ordered the way the work runs: what we can buy, how we buy it, what was
ordered, what is on hand, where it went.
"""

from django.urls import reverse

SUPPLY_TABS = (
    ("supply_chain:home", "Overview"),
    ("supply_chain:catalogue", "Catalogue"),
    ("supply_chain:procurement_round_board", "Sourcing"),
    ("supply_chain:orders", "Orders"),
    ("supply_chain:stock", "Stock"),
    ("supply_chain:distribution", "Distribution"),
)

# Pages that belong under a tab without being it, so the tab still reads as
# current when you are one level in.
TAB_FOR_VIEW = {
    "supply_chain:procurement_round_detail": "supply_chain:procurement_round_board",
    "supply_chain:procurement_comparison": "supply_chain:procurement_round_board",
    "supply_chain:procurement_quote_entry": "supply_chain:procurement_round_board",
    "supply_chain:procurement_registries": "supply_chain:catalogue",
    "supply_chain:order_detail": "supply_chain:orders",
}


def supply_tabs(request) -> list[dict]:
    current = request.resolver_match.view_name if request.resolver_match else ""
    current = TAB_FOR_VIEW.get(current, current)
    return [{"url": reverse(name), "label": label, "active": name == current} for name, label in SUPPLY_TABS]
