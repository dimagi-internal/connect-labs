"""The marketplace's menu: everything about where Connect works, who does it, and what it buys.

One expandable rail (`labs/rail_menu.py`) across the marketplace, targeting, solicitations,
Pulse and the supplier marketplace, so a person moves between them without the Labs sidebar.
Headings and items agreed with Jonathan, 2026-11-06. A page joins the menu by being listed
here.
"""

from __future__ import annotations

from connect_labs.labs.presentation import is_present_mode
from connect_labs.labs.rail_menu import Group, Item, Menu, build

MARKETPLACE_MENU = Menu(
    key="marketplace",
    title="Marketplace",
    subtitle="Every kind of work on Connect: where it goes, who does it, and what it buys.",
    groups=(
        Group(
            "Overview",
            (
                Item("Programs", "marketplace:home"),
                Item("Partners", "marketplace:network", owns=("/labs/marketplace/org/",)),
            ),
        ),
        Group(
            "Targeting",
            (
                # Deep dives (Malaria PMC today, more per programme area later) are
                # reached from the Targeting page, not listed here; Targeting stays lit
                # on them because their URLs sit under its own.
                Item("Targeting", "targeting:index"),
            ),
        ),
        Group(
            "Solicitations",
            (
                Item("Calls for partners", "solicitations:public_list"),
                Item("Expressions of interest", "marketplace:rounds"),
                Item("Manage my calls", "solicitations:manage_list", owns=("/solicitations/o/",)),
            ),
        ),
        Group(
            "Network",
            (
                Item("Live delivery", "pulse:index", owns=("/labs/pulse/opp/",)),
                Item("Network growth", "pulse:network"),
                Item("Connectivity", "pulse:connectivity"),
            ),
        ),
        Group(
            "Cost-Effectiveness",
            (
                Item("Cost-effectiveness ranking", "pulse:cea"),
                Item("Cost data", "pulse:costs"),
                Item("Donor reports", "pulse:report_list"),
            ),
        ),
        Group(
            "Suppliers",
            (Item("Supplier marketplace", "supply_chain:market"),),
        ),
    ),
    # Public, token-scoped and kiosk pages: no menu, and nothing that leads a visitor out of them.
    never=("/labs/pulse/p/", "/labs/pulse/r/", "/labs/pulse/v/", "/labs/pulse/api/"),
    sidebar_url_name="marketplace:home",
)


def marketplace_rail_context(request) -> dict:
    """Context processor: the marketplace rail for a signed-in person on a page it lists."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or is_present_mode(request):
        return {}
    menu = build(MARKETPLACE_MENU, request)
    return {"expandable_rail_menu": menu} if menu else {}
