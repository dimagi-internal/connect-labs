"""The domain's nav, declared once.

Its own module rather than a constant in views.py because the procurement
views deliberately do NOT inherit that module's `OperationBase` -- see their
docstring: importing it would make `call_operation` unpatchable from those
tests and let them run real operations against production. Nothing here
touches the domain, so it is safe for both view modules to import.

Grouped (TAB_GROUPS) the way the work divides: buying, distribution and stock, setup.
"""

from django.urls import reverse

# The tabs, grouped the way the work divides (Sophie's feedback, "Structure and
# wording across the site"): buying happens a few times a year and is run by the
# programme; distribution and stock is weekly and is partly the partners'; setup
# is shared by both. To add a tab, add one line to its group here -- the flat
# `SUPPLY_TABS` below, the grouped header, Settings' tab list and the hidden-tab
# check all follow. A tab a programme pins from Settings joins the group of the
# tab it sits after.
HOME = "supply_chain:home"
OVERVIEW = "supply_chain:overview"

TAB_GROUPS = (
    # Overview stands alone: no group name over it.
    ("", (("supply_chain:home", "Overview"), ("supply_chain:start_here", "Start here"))),
    (
        "Buying",
        (
            ("supply_chain:purchases", "Purchases"),
            ("supply_chain:suppliers", "Suppliers"),
        ),
    ),
    (
        "Distribution and stock",
        (
            ("supply_chain:network", "Network"),
            ("supply_chain:partners", "Partners"),
            ("supply_chain:programme_map", "Map"),
            ("supply_chain:distribution", "Distribution"),
            ("supply_chain:stock", "Stock"),
            ("supply_chain:workers", "Workers"),
            ("supply_chain:plan", "Plan"),
            ("supply_chain:flow", "Where it went"),
            ("supply_chain:issues", "Issues"),
        ),
    ),
    (
        "Setup",
        (
            ("supply_chain:catalogue", "Catalogue"),
            ("supply_chain:organisations", "Organisations"),
            ("supply_chain:alerts", "Alerts"),
            ("supply_chain:programme_team", "Team"),
            ("supply_chain:update_links", "Update links"),
        ),
    ),
)

SUPPLY_TABS = tuple(tab for _, tabs in TAB_GROUPS for tab in tabs)

# The two tabs Purchases replaced, as a programme may still have them switched off in Settings.
LEGACY_BUYING_TABS = {"supply_chain:procurement_tender_board", "supply_chain:orders"}


def canonical_tab(name):
    """A saved tab name as the tab it is now: Sourcing and Orders are Purchases."""
    return "supply_chain:purchases" if name in LEGACY_BUYING_TABS else name


GROUP_OF_TAB = {name: group for group, tabs in TAB_GROUPS for name, _ in tabs}

# One line per page: what it is for and what you do there, in plain words for a
# programme manager or partner staff member. Shown under the tabs on the tab's
# own page (not on the records one level below it).
PAGE_PURPOSE = {
    # One short line each: the page's own heading and content carry the rest (Jon, 2026-11-05:
    # "the entire UI is WAY too text heavy"). Keep each under ~12 words.
    "supply_chain:home": "What is open, and who to chase.",
    "supply_chain:start_here": "What each part is for, step by step.",
    "supply_chain:purchases": "Every purchase, from sizing to payment.",
    "supply_chain:suppliers": "The companies you may buy from.",
    "supply_chain:network": "Where stock is kept, and what each place holds.",
    "supply_chain:partners": "Which partner runs which place, and how they hand out.",
    "supply_chain:programme_map": "The same places on a map.",
    "supply_chain:distribution": "Handouts planned, and handouts given.",
    "supply_chain:stock": "How much of each product is where.",
    "supply_chain:plan": "What to issue each field worker next.",
    "supply_chain:workers": "Each field worker's stock, and how long it lasts.",
    "supply_chain:flow": "Where the stock went after delivery.",
    "supply_chain:issues": "Stock missing, damaged or expired, followed up.",
    "supply_chain:catalogue": "What you buy.",
    "supply_chain:organisations": "Every organisation in this program.",
    "supply_chain:alerts": "Who is told, and when.",
    "supply_chain:programme_team": "Who acts for this program. Partners use update links.",
    "supply_chain:update_links": "No-login links for suppliers and partners.",
}

# Words this domain uses in its own way. `{% term "trade item" %}` (supply_chain_extras)
# wraps one in a dotted underline with this as its hover note; standard supply
# words (incoterm, GRN, SKU...) are left to the reader.
GLOSSARY = {
    "trade item": (
        "One manufacturer's version of a product: their carton size, shelf life and barcode. "
        "Add one for each product you actually buy."
    ),
    "checks": (
        "Things the records cannot answer or that disagree with each other, such as an order "
        "with no invoice or a count that does not match the movements."
    ),
    "buyer of record": (
        "The organisation that places the order and brings the goods in. "
        "Import duty and tax fall on whoever that is."
    ),
    "update links": (
        "A private link you send to someone outside the program so they can record "
        "their own step (a shipment, a delivery) without signing in."
    ),
    "tender": (
        "A request for quotes: what you want to buy, how much, where it should be delivered, "
        "and by when suppliers should reply."
    ),
    "batch": "One delivery of a trade item, with its expiry date and certificate.",
}

# Pages that belong under a tab without being it, so the tab still reads as
# current when you are one level in.
TAB_FOR_VIEW = {
    # The old Sourcing and Orders tabs, now one (round 3): their pages sit under Purchases.
    "supply_chain:procurement_tender_board": "supply_chain:purchases",
    "supply_chain:orders": "supply_chain:purchases",
    "supply_chain:purchase": "supply_chain:purchases",
    # Overview at its own address, which its tab links to: /supply/ itself may
    # open on another tab (landing.py).
    "supply_chain:overview": "supply_chain:home",
    "supply_chain:programme_runner": "supply_chain:home",
    "supply_chain:programme_team_add": "supply_chain:programme_team",
    "supply_chain:programme_team_remove": "supply_chain:programme_team",
    "supply_chain:dispensing_rules": "supply_chain:stock",
    "supply_chain:worker_detail": "supply_chain:workers",
    "supply_chain:dispensing_rule_create": "supply_chain:stock",
    "supply_chain:dispensing_rule_edit": "supply_chain:stock",
    "supply_chain:procurement_tender_detail": "supply_chain:purchases",
    "supply_chain:procurement_comparison": "supply_chain:purchases",
    "supply_chain:procurement_quote_entry": "supply_chain:purchases",
    # A quote's own page had no entry, so landing on it un-highlighted every
    # tab and the nav read as though you had left the domain.
    "supply_chain:procurement_quote_detail": "supply_chain:purchases",
    "supply_chain:award_detail": "supply_chain:purchases",
    "supply_chain:award_orders_create": "supply_chain:purchases",
    "supply_chain:product_detail": "supply_chain:catalogue",
    "supply_chain:item_detail": "supply_chain:catalogue",
    "supply_chain:supplier_detail": "supply_chain:suppliers",
    "supply_chain:order_detail": "supply_chain:purchases",
    "supply_chain:shipment_detail": "supply_chain:purchases",
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
    "supply_chain:supplier_contacts": "supply_chain:suppliers",
    "supply_chain:supplier_qualifications": "supply_chain:suppliers",
    "supply_chain:org_create": "supply_chain:organisations",
    "supply_chain:org_edit": "supply_chain:organisations",
    "supply_chain:org_merge": "supply_chain:organisations",
    "supply_chain:procurement_tender_create": "supply_chain:purchases",
    "supply_chain:procurement_tender_edit": "supply_chain:purchases",
    "supply_chain:procurement_tender_splits": "supply_chain:purchases",
    "supply_chain:procurement_tender_sizing": "supply_chain:purchases",
    "supply_chain:procurement_tender_sizing_apply": "supply_chain:purchases",
    "supply_chain:procurement_tender_open": "supply_chain:purchases",
    "supply_chain:procurement_tender_close": "supply_chain:purchases",
    "supply_chain:procurement_reply_sent": "supply_chain:purchases",
    "supply_chain:procurement_tender_duty_terms": "supply_chain:purchases",
    "supply_chain:procurement_tender_import_estimates": "supply_chain:purchases",
    "supply_chain:procurement_tender_invite_org": "supply_chain:purchases",
    "supply_chain:procurement_tender_uninvite_org": "supply_chain:purchases",
    "supply_chain:procurement_outreach_log": "supply_chain:purchases",
    "supply_chain:procurement_outreach_reply": "supply_chain:purchases",
    "supply_chain:procurement_outreach_chase": "supply_chain:purchases",
    "supply_chain:procurement_outreach_delete": "supply_chain:purchases",
    "supply_chain:procurement_quote_void": "supply_chain:purchases",
    "supply_chain:procurement_quote_correct": "supply_chain:purchases",
    "supply_chain:quote_document_attach": "supply_chain:purchases",
    "supply_chain:tender_document_attach": "supply_chain:purchases",
    "supply_chain:program_document_attach": "supply_chain:purchases",
    # Asking for an approval, attaching its letter and answering it are all
    # read from the award, which sits under Sourcing.
    "supply_chain:approval_request": "supply_chain:purchases",
    "supply_chain:approval_document_attach": "supply_chain:purchases",
    "supply_chain:approval_decide": "supply_chain:purchases",
    # Placing the order is the first Orders screen, not the last Sourcing one.
    "supply_chain:contract_create": "supply_chain:purchases",
    "supply_chain:contract_edit": "supply_chain:purchases",
    "supply_chain:invoice_record": "supply_chain:purchases",
    "supply_chain:invoice_edit": "supply_chain:purchases",
    "supply_chain:invoice_dispute": "supply_chain:purchases",
    "supply_chain:charge_record": "supply_chain:purchases",
    "supply_chain:payment_record": "supply_chain:purchases",
    "supply_chain:advance_payment_record": "supply_chain:purchases",
    "supply_chain:contract_commitment_record": "supply_chain:purchases",
    "supply_chain:tender_commitment_record": "supply_chain:purchases",
    "supply_chain:commitment_resolve": "supply_chain:home",
    "supply_chain:payment_confirm": "supply_chain:purchases",
    "supply_chain:document_attach": "supply_chain:purchases",
    "supply_chain:shipment_record": "supply_chain:purchases",
    "supply_chain:shipment_status": "supply_chain:purchases",
    "supply_chain:shipment_document_attach": "supply_chain:purchases",
    "supply_chain:shipment_require_document": "supply_chain:purchases",
    "supply_chain:shipment_unrequire_document": "supply_chain:purchases",
    "supply_chain:receipt_record": "supply_chain:purchases",
    "supply_chain:supply_point_create": "supply_chain:network",
    "supply_chain:supply_point_edit": "supply_chain:network",
    "supply_chain:movement_record": "supply_chain:stock",
    "supply_chain:movement_remove": "supply_chain:stock",
    "supply_chain:consignment_dispatch": "supply_chain:stock",
    "supply_chain:consignment_receive": "supply_chain:stock",
    "supply_chain:stock_count_record": "supply_chain:stock",
    "supply_chain:distribution_record": "supply_chain:distribution",
    "supply_chain:distribution_plan": "supply_chain:distribution",
    "supply_chain:distribution_confirm_as_planned": "supply_chain:distribution",
    "supply_chain:distribution_from_message": "supply_chain:distribution",
    "supply_chain:distribution_received": "supply_chain:distribution",
    "supply_chain:distribution_received_all": "supply_chain:distribution",
    "supply_chain:issue_report": "supply_chain:issues",
    "supply_chain:issue_log": "supply_chain:issues",
    "supply_chain:plan_stores": "supply_chain:plan",
    "supply_chain:plan_programme": "supply_chain:plan",
    "supply_chain:plan_inputs": "supply_chain:plan",
    "supply_chain:plan_buffer_place": "supply_chain:plan",
    "supply_chain:issue_follow_up": "supply_chain:issues",
    "supply_chain:issue_resolve": "supply_chain:issues",
    "supply_chain:issue_evidence": "supply_chain:issues",
    "supply_chain:custodian_assign": "supply_chain:issues",
    "supply_chain:partner_plan": "supply_chain:partners",
    "supply_chain:partner_places": "supply_chain:partners",
    "supply_chain:partner_contact": "supply_chain:partners",
    "supply_chain:partner_deliveries": "supply_chain:partners",
    "supply_chain:alert_check_now": "supply_chain:alerts",
    "supply_chain:alert_delete": "supply_chain:alerts",
    "supply_chain:supplier_mark_reviewed": "supply_chain:suppliers",
    "supply_chain:supplier_market_invite": "supply_chain:suppliers",
}

# Addresses whose tab is decided per programme, not here: a workflow pinned into the
# supply navigation (workflow_views/) highlights its own tab, whichever it is.
VIEWS_WITH_PINNED_TABS = frozenset({"supply_chain:workflow_view"})

# Addresses that render no supply nav at all, so there is no tab to keep
# current. Held as a named set rather than left out, so the test that closes
# the set above can tell "deliberately has no nav" from "forgotten".
VIEWS_WITHOUT_TABS = frozenset(
    {
        # JSON, not a page.
        "supply_chain:api_operations",
        "supply_chain:api_operation",
        # A table cell's edit (cells.py): JSON back to the page that posted it.
        "supply_chain:cell_edit",
        # Streams a stored file back.
        "supply_chain:document_open",
        # A POST that saves the person's own "sign my emails as" name and redirects back to the drafts panel.
        "supply_chain:procurement_signed_as",
        # A supplier's own login-free page: deliberately no programme chrome,
        # because the person reading it is not in the programme.
        "supply_chain:update_link_public",
        # The same person's lean phone page and the files that make it work offline. Standalone documents
        # and JSON, no programme chrome, for the same reason.
        "supply_chain:update_link_field",
        "supply_chain:update_link_field_data",
        "supply_chain:update_link_field_count",
        "supply_chain:update_link_field_photo",
        "supply_chain:update_link_field_sw",
        "supply_chain:update_link_field_manifest",
        # Local sign-in shim, mounted only under DEBUG.
        "supply_chain:dev_login",
        # The portfolio spans programmes, and every tab above reverses to a
        # programme-scoped page. Highlighting one of them there would say
        # something untrue about where you are, and mapping the portfolio
        # under "Overview" would be worse -- the Overview is one programme.
        # So it renders no supply nav and offers its way back a row at a time,
        # each into its own programme's Overview.
        "supply_chain:portfolio",
        # The same portfolio laid out by place, for the same reason: it spans
        # programs, so no one program's tab is where you are.
        "supply_chain:portfolio_map",
        # JSON behind the map's Stock mode, not a page.
        "supply_chain:portfolio_map_cover",
        "supply_chain:programme_map_cover",
        # The supplier marketplace: its own shell, for suppliers, who have no
        # programs and so no program tabs to be on. It spans programs too.
        "supply_chain:market",
        "supply_chain:market_tender",
        "supply_chain:market_bid",
        "supply_chain:market_bids",
        "supply_chain:market_revise",
        "supply_chain:market_withdraw",
        "supply_chain:market_register",
        "supply_chain:market_organisation",
        "supply_chain:market_invite",
        "supply_chain:market_invite_accept",
        "supply_chain:market_tender_listing",
        "supply_chain:market_tender_manage",
    }
)


def _program_tabs(request) -> dict:
    """The programme in view's resolved `tabs` setting (supply_chain/config.py), read once per request."""
    cached = getattr(request, "_supply_tab_config", None)
    if cached is not None:
        return cached
    from connect_labs.supply_chain.config import tabs_for

    program_id = (getattr(request, "labs_context", None) or {}).get("program_id")
    tabs = tabs_for(request, program_id)
    request._supply_tab_config = tabs
    return tabs


def _pinned(request) -> list:
    """The workflows filling tabs in the programme in view (supply_chain/config.py), in tab order.

    Read once per request and kept on it. That matters for a past date: an
    `?as_of=` page renders after history/rewind.py has undone every later
    revision, and the tabs are navigation, not the programme's records --
    `as_of_view` reads them before it rewinds. (They now live in Settings,
    outside the supply records the rewind touches, but the read order stays.)
    """
    cached = getattr(request, "_supply_pins", None)
    if cached is not None:
        return cached
    from connect_labs.supply_chain.config import pins_from

    program_id = (getattr(request, "labs_context", None) or {}).get("program_id")
    pins = []
    if program_id:
        try:
            pins = pins_from(_program_tabs(request), int(program_id))
        except (TypeError, ValueError):
            pins = []
    request._supply_pins = pins
    return pins


def hidden_tabs(request) -> set[str]:
    """Built-in tabs the programme in view has turned off in Settings (never Overview)."""
    from connect_labs.supply_chain.config import hidden_from

    hidden = hidden_from(_program_tabs(request))
    # Sourcing and Orders became one Purchases tab: a programme that had turned off both has no use for it either.
    if LEGACY_BUYING_TABS <= hidden:
        hidden = hidden | {"supply_chain:purchases"}
    return hidden


def scoped_url(request, name: str, args=None, program_id=None) -> str:
    """`reverse(name)` naming the programme in view (or `program_id`), as `?program_id=`.

    A supply link names its programme rather than trusting the session to still
    hold it: switching programme in another browser tab, or opening any page that
    names only an opportunity, replaced the session's programme and the next click
    on an open supply page landed somewhere else -- or, for a pinned tab, on a 404.
    """
    url = reverse(name, args=args)
    program_id = program_id or (getattr(request, "labs_context", None) or {}).get("program_id")
    return f"{url}?program_id={program_id}" if program_id else url


def pinned_replacement(request, tab_name: str) -> dict | None:
    """The pinned workflow standing in for built-in tab `tab_name`, as {url, label}, or None.

    For a page that points at that tab in its own words ("see Workers") -- it
    should point where the header does.
    """
    for pin in _pinned(request):
        if pin.replaces == tab_name:
            url = scoped_url(request, "supply_chain:workflow_view", [pin.slug], pin.program_id)
            return {"url": url, "label": pin.label}
    return None


def supply_tabs(request) -> list[dict]:
    match = request.resolver_match
    own_view = match.view_name if match else ""
    current = TAB_FOR_VIEW.get(own_view, own_view)
    current_slug = (match.kwargs or {}).get("slug") if match and current == "supply_chain:workflow_view" else None
    pins = _pinned(request)
    replacing = {p.replaces: p for p in pins if p.replaces}

    def pin_tab(pin, also_active=False, group=""):
        return {
            "url": scoped_url(request, "supply_chain:workflow_view", [pin.slug], pin.program_id),
            "label": pin.label,
            "active": current_slug == pin.slug or also_active,
            "group": group,
        }

    hidden = hidden_tabs(request)
    tabs = []
    keys = []
    for name, label in SUPPLY_TABS:
        if name in hidden:
            continue
        pin = replacing.get(name)
        if pin is not None:
            # The pinned workflow stands in for this tab; its own page ("classic view") still counts as here.
            tabs.append(pin_tab(pin, also_active=current == name, group=GROUP_OF_TAB.get(name, "")))
        else:
            # Overview's tab names Overview, not the front door that may open elsewhere.
            href = scoped_url(request, "supply_chain:overview" if name == "supply_chain:home" else name)
            tab = {"url": href, "label": label, "active": name == current, "group": GROUP_OF_TAB.get(name, "")}
            # The page's one-line purpose, on the tab's own page only: a record one level
            # below it (a tender, an order) leads with itself.
            if (name == own_view or (name == HOME and own_view == OVERVIEW)) and name in PAGE_PURPOSE:
                tab["purpose"] = PAGE_PURPOSE[name]
            tabs.append(tab)
        keys.append(name)
    # Tabs a programme ADDS sit after the tab they name (`after`), by default with
    # the stock pages; one naming a tab that is not there goes at the end.
    for pin in (p for p in pins if not p.replaces):
        after = "supply_chain:purchases" if pin.after in LEGACY_BUYING_TABS else pin.after
        anchor = after if after in keys else f"pin:{after}"
        at = keys.index(anchor) + 1 if anchor in keys else len(tabs)
        # After any added tab already placed behind the same anchor, so position order holds.
        while at < len(keys) and keys[at].startswith("pin:"):
            at += 1
        # Same group as the tab it follows.
        tabs.insert(at, pin_tab(pin, group=tabs[at - 1]["group"] if at else ""))
        keys.insert(at, f"pin:{pin.slug}")
    return tabs


def tab_groups(tabs: list[dict]) -> list[dict]:
    """`supply_tabs` as [{label, tabs}], one entry per run of tabs in the same group.

    The header draws each group under its name. Consecutive tabs only, so a
    tab a programme pinned into the middle of a group stays in it and the
    order Settings chose is never rearranged.
    """
    groups: list[dict] = []
    for tab in tabs:
        label = tab.get("group", "")
        if groups and groups[-1]["label"] == label:
            groups[-1]["tabs"].append(tab)
        else:
            groups.append({"label": label, "tabs": [tab]})
    return groups


def page_purpose(tabs: list[dict]) -> str:
    """The one-line purpose of the page being viewed, or "" when it is not a tab's own page."""
    return next((t["purpose"] for t in tabs if t.get("purpose")), "")
