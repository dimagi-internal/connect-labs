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
    ("supply_chain:suppliers", "Suppliers"),
    ("supply_chain:procurement_round_board", "Sourcing"),
    ("supply_chain:orders", "Orders"),
    ("supply_chain:network", "Network"),
    ("supply_chain:stock", "Stock"),
    ("supply_chain:distribution", "Distribution"),
    # After the chain, because they cut across it: who is told when something
    # in it changes, and how a supplier records its own part.
    ("supply_chain:alerts", "Alerts"),
    ("supply_chain:update_links", "Update links"),
)

# Pages that belong under a tab without being it, so the tab still reads as
# current when you are one level in.
TAB_FOR_VIEW = {
    "supply_chain:procurement_round_detail": "supply_chain:procurement_round_board",
    "supply_chain:procurement_comparison": "supply_chain:procurement_round_board",
    "supply_chain:procurement_quote_entry": "supply_chain:procurement_round_board",
    # A quote's own page had no entry, so landing on it un-highlighted every
    # tab and the nav read as though you had left the domain.
    "supply_chain:procurement_quote_detail": "supply_chain:procurement_round_board",
    "supply_chain:award_detail": "supply_chain:procurement_round_board",
    "supply_chain:product_detail": "supply_chain:catalogue",
    "supply_chain:item_detail": "supply_chain:catalogue",
    "supply_chain:supplier_detail": "supply_chain:suppliers",
    "supply_chain:order_detail": "supply_chain:orders",
    "supply_chain:shipment_detail": "supply_chain:orders",
    # Organisations are infrastructure rather than a daily destination, so they
    # read as part of Suppliers rather than taking a tab of their own.
    "supply_chain:organisations": "supply_chain:suppliers",
    # The checks are the overview's second panel, read in full.
    "supply_chain:checks": "supply_chain:home",
    "supply_chain:alert_create": "supply_chain:alerts",
    "supply_chain:alert_edit": "supply_chain:alerts",
    "supply_chain:update_link_issue": "supply_chain:update_links",
    "supply_chain:update_link_revoke": "supply_chain:update_links",
    # The movements behind a balance are the stock page read one level down.
    "supply_chain:movements": "supply_chain:stock",
    # Everything below was missing, so each of these screens un-highlighted the
    # whole nav -- the same fault the quote page above was fixed for. The
    # approval-request screen is the one a walkthrough caught; the rest were
    # found by closing the set (see tests/test_navigation_tabs.py), which is
    # what stops the next screen re-opening the hole.
    "supply_chain:product_create": "supply_chain:catalogue",
    "supply_chain:product_edit": "supply_chain:catalogue",
    "supply_chain:item_create": "supply_chain:catalogue",
    "supply_chain:item_edit": "supply_chain:catalogue",
    "supply_chain:supplier_create": "supply_chain:suppliers",
    "supply_chain:supplier_edit": "supply_chain:suppliers",
    "supply_chain:org_create": "supply_chain:suppliers",
    "supply_chain:org_edit": "supply_chain:suppliers",
    "supply_chain:org_merge": "supply_chain:suppliers",
    "supply_chain:procurement_round_create": "supply_chain:procurement_round_board",
    "supply_chain:procurement_round_edit": "supply_chain:procurement_round_board",
    "supply_chain:procurement_round_open": "supply_chain:procurement_round_board",
    "supply_chain:procurement_round_close": "supply_chain:procurement_round_board",
    "supply_chain:procurement_outreach_log": "supply_chain:procurement_round_board",
    "supply_chain:procurement_outreach_reply": "supply_chain:procurement_round_board",
    "supply_chain:procurement_outreach_delete": "supply_chain:procurement_round_board",
    "supply_chain:procurement_quote_void": "supply_chain:procurement_round_board",
    "supply_chain:procurement_quote_correct": "supply_chain:procurement_round_board",
    "supply_chain:quote_document_attach": "supply_chain:procurement_round_board",
    # Asking for an approval, attaching its letter and answering it are all
    # read from the award, which sits under Sourcing.
    "supply_chain:approval_request": "supply_chain:procurement_round_board",
    "supply_chain:approval_document_attach": "supply_chain:procurement_round_board",
    "supply_chain:approval_decide": "supply_chain:procurement_round_board",
    # Placing the order is the first Orders screen, not the last Sourcing one.
    "supply_chain:contract_create": "supply_chain:orders",
    "supply_chain:contract_edit": "supply_chain:orders",
    "supply_chain:invoice_record": "supply_chain:orders",
    "supply_chain:invoice_edit": "supply_chain:orders",
    "supply_chain:charge_record": "supply_chain:orders",
    "supply_chain:payment_record": "supply_chain:orders",
    "supply_chain:payment_confirm": "supply_chain:orders",
    "supply_chain:document_attach": "supply_chain:orders",
    "supply_chain:shipment_record": "supply_chain:orders",
    "supply_chain:shipment_status": "supply_chain:orders",
    "supply_chain:shipment_document_attach": "supply_chain:orders",
    "supply_chain:shipment_require_document": "supply_chain:orders",
    "supply_chain:shipment_unrequire_document": "supply_chain:orders",
    "supply_chain:receipt_record": "supply_chain:orders",
    "supply_chain:supply_point_create": "supply_chain:network",
    "supply_chain:supply_point_edit": "supply_chain:network",
    "supply_chain:movement_record": "supply_chain:stock",
    "supply_chain:stock_count_record": "supply_chain:stock",
    "supply_chain:distribution_record": "supply_chain:distribution",
    "supply_chain:alert_check_now": "supply_chain:alerts",
    "supply_chain:alert_delete": "supply_chain:alerts",
}

# Addresses that render no supply nav at all, so there is no tab to keep
# current. Held as a named set rather than left out, so the test that closes
# the set above can tell "deliberately has no nav" from "forgotten".
VIEWS_WITHOUT_TABS = frozenset(
    {
        # JSON, not a page.
        "supply_chain:api_operations",
        "supply_chain:api_operation",
        # Streams a stored file back.
        "supply_chain:document_open",
        # A supplier's own login-free page: deliberately no programme chrome,
        # because the person reading it is not in the programme.
        "supply_chain:update_link_public",
        # Local sign-in shim, mounted only under DEBUG.
        "supply_chain:dev_login",
        # The portfolio spans programmes, and every tab above reverses to a
        # programme-scoped page. Highlighting one of them there would say
        # something untrue about where you are, and mapping the portfolio
        # under "Overview" would be worse -- the Overview is one programme.
        # So it renders no supply nav and offers its way back a row at a time,
        # each into its own programme's Overview.
        "supply_chain:portfolio",
    }
)


def supply_tabs(request) -> list[dict]:
    current = request.resolver_match.view_name if request.resolver_match else ""
    current = TAB_FOR_VIEW.get(current, current)
    return [{"url": reverse(name), "label": label, "active": name == current} for name, label in SUPPLY_TABS]
