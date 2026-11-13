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
    subtitle="From where Connect works to what it buys.",
    groups=(
        Group(
            "Overview",
            (
                Item("Programs", "marketplace:home", public=True),
                Item("Partners", "marketplace:network", owns=("/labs/marketplace/org/",)),
            ),
        ),
        Group(
            "Solicitations",
            (
                # One page for every call: cards or a table, open and past. A round's own
                # analysis page (signed in) sits under it.
                Item(
                    "Calls for partners",
                    "solicitations:public_list",
                    owns=("/labs/marketplace/rounds/",),
                    public=True,
                ),
                Item("Manage my calls", "solicitations:manage_list", owns=("/solicitations/o/",)),
            ),
        ),
        Group(
            "Network",
            (
                # The menu opens the night map, not the picker and link manager at the section's
                # root (Jonathan, 2026-11-09); the item still lights on that root page.
                Item("Live delivery", "pulse:index", owns=("/labs/pulse/opp/",), link=("pulse:display", "nightmap")),
                Item("Network growth", "pulse:network"),
                Item("Connectivity", "pulse:connectivity"),
                Item("Cost data", "pulse:costs"),
                Item("Donor reports", "pulse:report_list"),
            ),
        ),
        Group(
            "Cost-Effectiveness",
            (
                # What the indicators say, then the models, then the models applied to what was delivered, then
                # the models applied to a market that has not been served yet (Jonathan, 2026-11-12).
                Item("Health indicators", "targeting:index"),
                Item("CEA models", "cea:models", owns=("/labs/cea/models/",)),
                Item("Implementations", "cea:implementation", owns=("/labs/cea/implementation/",)),
                Item("Market size", "cea:market", owns=("/labs/cea/market/",)),
            ),
        ),
        Group(
            "Suppliers",
            (Item("Supplier marketplace", "supply_chain:market", public=True),),
        ),
    ),
    # Public, token-scoped and kiosk pages: no menu, and nothing that leads a visitor out of them.
    never=("/labs/pulse/p/", "/labs/pulse/r/", "/labs/pulse/v/", "/labs/pulse/api/"),
    sidebar_url_name="marketplace:home",
)


def marketplace_rail_context(request) -> dict:
    """Context processor: the marketplace rail on a page it lists. A visitor who is not
    signed in gets it too, with only the pages they can open."""
    if is_present_mode(request):
        return {}
    menu = build(MARKETPLACE_MENU, request)
    return {"expandable_rail_menu": menu} if menu else {}
