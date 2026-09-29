"""The network tier in a browser: organisations and supply points.

Two directories and their write screens. Neither registry had a page at all —
an organisation could be created only through `org_upsert`, and the buyer of
record a contract requires had to be found by reading a list over the API.

**Why organisations sit under Suppliers rather than getting their own tab.**
They are infrastructure, not a daily destination: you go there to bind a
partner to Connect or to fold a duplicate away, and then you leave. The nav
stays the shape of the work.

**Supply points do get a tab**, between Sourcing and Stock, because that is
where they sit in the work: you need somewhere for stock to arrive before
there is any stock to look at.
"""

from decimal import Decimal

from django.http import Http404
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.models import SupplyPoint
from connect_labs.supply_chain.network.forms import OrgForm, OrgMergeForm, SupplyPointForm
from connect_labs.supply_chain.views import OperationBase

# The kinds, in the order a network reads top-down: siblings in the tree are
# ordered by it, so a store comes before the workers beside it.
KIND_ORDER = [
    ("central_store", "Central stores"),
    ("regional_store", "Regional stores"),
    ("facility", "Facilities"),
    ("user_held", "Field workers"),
    ("supplier_site", "Supplier sites"),
    ("in_transit", "In transit"),
    ("customs", "Customs"),
]


# ---- the two directories -----------------------------------------------


class NetworkView(OperationBase):
    """Where stock can rest in this programme, as one tree from the top store down to each worker.

    Every point appears once, in the tree, under the point it is resupplied
    from (design 2026-09-28 §6.1): the tree IS the directory. Each node carries
    what the old per-kind tables said (where, band, how it is known, Edit)
    and, for one item, what it holds -- from `network_tree`, one grouped read
    whatever the network's size. Inactive and in-transit points sit in the
    tree too, without figures (belief reads only active holdings).

    Siblings read top-down, stores before the workers they supply, in the
    order of KIND_ORDER and then by name.
    """

    template_name = "supply_chain/network.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context

        from connect_labs.supply_chain.stock.visit_views import as_of_payload, chosen_item, rule_items

        points = self.op("supply_point_list", include_inactive=True)
        context["total"] = len(points)

        # The item whose stock the tree shows: one a dispensing rule gives
        # out, else any item in the catalogue (a network with no rule yet
        # still has stores holding stock).
        items = rule_items(self.op)
        if not items and points:
            items = [{"id": i["id"], "name": i["name"]} for i in self.op("item_list")]
        item = chosen_item(self.request, items)
        figures, stock = {}, None
        if item is not None:
            stock = self.op("network_tree", item_id=item["id"], **as_of_payload(self.request))
            _flatten(stock["roots"], figures)
        context.update(
            tree_items=items,
            tree_item=item,
            stock=stock,
            roots=build_tree(points, figures),
        )
        return context


_KIND_RANK = {kind: n for n, (kind, _) in enumerate(KIND_ORDER)}


def _flatten(rows, into):
    for row in rows:
        into[row["supply_point_id"]] = row
        _flatten(row["children"], into)


def passes_stock_on(stock) -> bool:
    """Whether a point holds nothing BY DESIGN: it hands on everything it receives.

    True for a point that is not a worker, has points below it, holds
    exactly zero itself, and whose points below hold some of the item. Such
    a store's own empty holding is not a stock-out -- it is how it works --
    so the page says "passes stock on" in grey instead of a red alarm. A
    point meant to hold stock (a worker, a store with nothing below it) or
    a store whose whole subtree is empty keeps the alarm: there, empty is the
    finding.
    """
    if not stock or stock.get("kind") == "user_held" or not stock.get("subtree"):
        return False
    own, below = _amount(stock.get("on_hand")), _amount(stock["subtree"].get("on_hand"))
    return own is not None and below is not None and own == 0 and below > 0


def _amount(cell):
    try:
        return Decimal(cell["amount"]) if isinstance(cell, dict) and cell.get("amount") is not None else None
    except (ArithmeticError, TypeError, ValueError):
        return None


def band_of(stock) -> str | None:
    """The band a node's own holding shows, or None for a store that passes stock on.

    As the stock page has it, nothing left of stock that did arrive is a
    stock-out whatever the rate -- "cover unknown" beside a 0 read as if the
    point's state were unknown. A pass-through store is the exception: its
    empty shelf is by design (`passes_stock_on`).
    """
    if not stock or passes_stock_on(stock):
        return None
    if _amount(stock.get("on_hand")) == 0 and (_amount(stock.get("issued")) or 0) > 0:
        return "stockout"
    return stock.get("status")


def build_tree(points, figures) -> list[dict]:
    """The points as a forest by `parent_supply_point_id`, each point exactly once.

    A point whose parent is not in the programme is a root; a parent loop is
    broken at the first point met again, and the rest of it shown under it.
    Each node gets `stock` (its `network_tree` row, or None), `passes_on`,
    `band` (`band_of`) and its children split into `store_children` (always shown) and
    `worker_children` (behind one "show", so a store of 200 stays one row).
    """
    by_id = {p["id"]: p for p in points}

    def order(p):
        return (_KIND_RANK.get(p["kind"], len(_KIND_RANK)), p["name"].lower(), p["id"])

    children: dict = {}
    for p in points:
        parent = p.get("parent_supply_point_id")
        if parent in by_id and parent != p["id"]:
            children.setdefault(parent, []).append(p)
    visited: set = set()

    def node(p):
        visited.add(p["id"])
        stock = figures.get(p["id"])
        below = [node(c) for c in sorted(children.get(p["id"], []), key=order) if c["id"] not in visited]
        return {
            **p,
            "stock": stock,
            "passes_on": passes_stock_on(stock),
            "band": band_of(stock),
            "store_children": [c for c in below if c["kind"] != "user_held"],
            "worker_children": [c for c in below if c["kind"] == "user_held"],
        }

    roots = [
        node(p)
        for p in sorted(points, key=order)
        if (p.get("parent_supply_point_id") not in by_id or p.get("parent_supply_point_id") == p["id"])
        and p["id"] not in visited
    ]
    for p in sorted(points, key=order):  # only a parent loop leaves anything unvisited
        if p["id"] not in visited:
            roots.append(node(p))
    return roots


class OrganisationDirectoryView(OperationBase):
    """Every organisation labs knows, and whether Connect knows it too.

    The unlinked ones are the backlog, and the page says so rather than
    showing a blank column: an organisation with no `connect_organization_id`
    is one whose own staff cannot sign in and record their own shipments.
    """

    template_name = "supply_chain/organisations.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        orgs = self.op("org_list")
        context["orgs"] = orgs
        context["linked"] = sum(1 for o in orgs if o.get("connect_organization_id"))
        return context


# ---- organisations -----------------------------------------------------


class _OrgScreen(OperationFormView):
    operation = "org_upsert"
    form_class = OrgForm

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Organisations", "href": reverse("supply_chain:organisations")},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:organisations")

    def redirect_to(self, result):
        return reverse("supply_chain:organisations")


class OrgCreateView(_OrgScreen):
    title = "New organisation"
    intro = (
        "Anyone labs needs to name: a manufacturer, an implementing partner, a procurement "
        "agency, us. One registry for the whole of labs — an organisation is the same body "
        "in every programme it appears in."
    )
    submit_label = "Add organisation"


class OrgUpdateView(_OrgScreen):
    title = "Edit organisation"
    submit_label = "Save changes"
    footnote = (
        "This registry is labs-wide, so a change here shows in every programme this organisation "
        "appears in. Aliases are left as they are — they are curated by hand for slugs no rule can reach."
    )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        found = LabsOrg.objects.filter(pk=self.kwargs["org_id"]).first()
        if found is None:
            raise Http404(f"no organisation {self.kwargs['org_id']}")
        kwargs["instance"] = found
        return kwargs


class OrgMergeView(OperationFormView):
    """Two rows that turn out to be one organisation."""

    operation = "org_merge"
    form_class = OrgMergeForm
    title = "Merge two organisations"
    intro = (
        "Every reference moves to the one you keep, the folded-away key survives as an alias so "
        "old references still resolve, and the empty row is deleted. This cannot be undone."
    )
    submit_label = "Merge them"
    danger = True
    footnote = (
        "Refused if the two are linked to different Connect organisations. Two Connect ids means two "
        "organisations, whatever the names look like."
    )

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Organisations", "href": reverse("supply_chain:organisations")},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:organisations")

    def redirect_to(self, result):
        return reverse("supply_chain:organisations")


# ---- supply points -----------------------------------------------------


class _SupplyPointScreen(OperationFormView):
    operation = "supply_point_upsert"
    form_class = SupplyPointForm

    def breadcrumb(self, **kwargs):
        return [{"label": "Network", "href": reverse("supply_chain:network")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:network")

    def redirect_to(self, result):
        return reverse("supply_chain:network")


class SupplyPointCreateView(_SupplyPointScreen):
    title = "New supply point"
    intro = (
        "Anywhere stock can rest — a central store, a facility, or one field worker's own "
        "holding. A worker is a supply point rather than a special case, which is what lets "
        "a delivery to one use the same ledger as a transfer between stores."
    )
    submit_label = "Add supply point"


class SupplyPointUpdateView(_SupplyPointScreen):
    title = "Edit supply point"
    submit_label = "Save changes"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        found = SupplyPoint.objects.filter(
            pk=self.kwargs["supply_point_id"], program_id=_access(self.request).program_id
        ).first()
        if found is None:
            raise Http404(f"no supply point {self.kwargs['supply_point_id']} in this programme")
        kwargs["instance"] = found
        return kwargs
