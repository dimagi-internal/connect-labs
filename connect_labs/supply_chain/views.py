"""Django pages for the supply domain.

Every mutation goes through an operation, so the web UI can never do something
the API and MCP surfaces cannot. OperationBase.op() is the only way a view
reaches the domain.
"""

from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.navigation import supply_tabs
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services.compliance import spec_verdict


@method_decorator(login_required, name="dispatch")
class OperationBase(TemplateView):
    def op(self, name, **payload):
        return call_operation(name, _access(self.request), payload)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["supply_tabs"] = supply_tabs(self.request)
        return context


class CatalogueView(OperationBase):
    """What can be bought, at the two levels the supply chain actually uses.

    **Products** are spec-defined: "ready-to-use therapeutic food, 92 g
    sachet, 150 to the carton, 18 months minimum" is a specification, not a
    thing you can order. It carries the reference to the standard it comes
    from and, where one exists, the UNICEF Supply Division material number
    that names it across every prequalified manufacturer.

    **Trade items** are orderable: one manufacturer's product, with its own
    SKU and its own GTIN, checked against the product's requirements.

    This is not "item master" renamed. The two levels answer the question
    Jonathan asked -- how do you know every supplier's RUTF is really the same
    92 g sachet while still knowing their specific SKU -- and the answer has
    three identifier levels, of which this page shows two: the product says
    what it must be, the trade item says whose it is and how it is packed, and
    the batch (on the Stock page) says which physical lot arrived. Conflating
    any two of them is how a per-sachet comparison silently goes 4% wrong.
    """

    template_name = "supply_chain/catalogue.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = self.op("item_list")
        products = self.op("commodity_list")

        by_product: dict[str, list] = {}
        for item in items:
            by_product.setdefault(item["commodity_slug"], []).append(item)

        for product in products:
            own = by_product.get(product["slug"], [])
            packs = {i["base_per_pack"] for i in own if i.get("base_per_pack")}
            weights = {i["base_unit_grams"] for i in own if i.get("base_unit_grams")}

            for item in own:
                item["spec_verdict"] = spec_verdict(
                    item.get("spec_attributes"), product.get("spec_requirements") or []
                )
                # Two different kinds of disagreement, and they are not the
                # same finding. Differing from the product's nominal pack is
                # often legitimate -- a manufacturer may genuinely pack 144.
                # Two trade items under one product differing from EACH OTHER
                # is what makes a single per-sachet figure impossible.
                item["differs_from_product"] = bool(
                    item.get("base_per_pack")
                    and product.get("base_per_pack")
                    and item["base_per_pack"] != product["base_per_pack"]
                )
                item["pack_disagrees_with_siblings"] = len(packs) > 1
                item["weight_disagrees_with_siblings"] = len(weights) > 1
                item["gtins"] = [
                    {"level": level, "value": item.get(key)}
                    for level, key in (
                        ("base unit", "gtin_base"),
                        ("pack", "gtin_pack"),
                        ("case", "gtin_case"),
                    )
                    if item.get(key)
                ]

            product["items"] = own
            product["pack_values"] = sorted(packs)
            product["weight_values"] = sorted(weights)
            # The ration table is a programme decision, not part of the
            # specification, so its absence is stated rather than defaulted.
            product["has_course_definition"] = bool(
                (product.get("course_definition") or {}).get("base_units_per_course")
            )

        context["products"] = products
        context["orphan_items"] = [
            item for item in items if item["commodity_slug"] not in {p["slug"] for p in products}
        ]
        return context


class DomainHomeView(OperationBase):
    """The chain, end to end, and what the record cannot answer about it.

    Two panels, two different kinds of thing, deliberately not merged:

      the funnel   STATES -- how many records sit at each stage. "6 of 7
                   awaiting a reply" is a fact, and whether it is a problem
                   depends on when they were sent.
      the checks   GAPS and CONTRADICTIONS -- what the record cannot answer,
                   and where it disagrees with itself.

    The checks are grouped by AUDIENCE and not ranked (design doc section 22
    and 24). An earlier mockup had a "Needs you" banner with a priority
    order; that came out, because prioritising is a judgement about what
    matters today and the database does not contain what it would take to
    make it. Grouping by who can answer is routing, which IS a fact.
    """

    template_name = "supply_chain/home.html"

    AUDIENCE_ORDER = ("supplier", "partner", "internal")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            # Everything below is programme-scoped; SupplyDataAccess raises
            # rather than inventing a scope, and a half-rendered page is
            # worse than an explicit "choose a programme".
            return context

        commodity_slug = self.request.GET.get("commodity") or None
        commodities = self.op("commodity_list")
        if commodity_slug is None and len(commodities) == 1:
            commodity_slug = commodities[0]["slug"]

        context["commodities"] = commodities
        context["commodity_slug"] = commodity_slug
        context["summary"] = self.op("chain_summary", commodity_slug=commodity_slug)
        context["rounds"] = self.op("round_list")
        context["contracts"] = self.op("contract_list")

        checks = self.op("checks_list")
        context["checks"] = checks
        context["checks_by_audience"] = [
            {
                "audience": audience,
                "items": [c for c in checks["checks"] if c["audience"] == audience],
            }
            for audience in self.AUDIENCE_ORDER
        ]
        return context


class OrdersView(OperationBase):
    """Contracts, and who is buying under each.

    The buyer of record is a column and not a footnote, because it changes
    the landed cost: the same goods at the same price cost different amounts
    depending on who imports them.
    """

    template_name = "supply_chain/orders.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        context["contracts"] = self.op("contract_list")
        context["parties"] = {p["id"]: p for p in self.op("party_list")}
        context["suppliers"] = {s["id"]: s for s in self.op("supplier_list")}
        return context


class OrderDetailView(OperationBase):
    """One order, with the two derivations that decide whether to pay it.

    `contract_landed_cost` under each of the three buyers of record, so the
    tax consequence of the routing choice is visible rather than implied.
    `contract_match` for ordered against received against invoiced, whose
    `payable_now` is the value of what ARRIVED.
    """

    template_name = "supply_chain/order_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        contract_id = int(kwargs["contract_id"])
        context["contract"] = self.op("contract_get", contract_id=contract_id)
        context["landed"] = self.op("contract_landed_cost", contract_id=contract_id, compare_buyers=True)
        context["match"] = self.op("contract_match", contract_id=contract_id)
        context["shipments"] = self.op("shipment_list", contract_id=contract_id)
        context["receipts"] = self.op("receipt_list", contract_id=contract_id)
        context["invoices"] = self.op("invoice_list", contract_id=contract_id)
        context["documents"] = self.op("document_list", contract_id=contract_id)
        context["parties"] = {p["id"]: p for p in self.op("party_list")}
        context["suppliers"] = {s["id"]: s for s in self.op("supplier_list")}
        return context


class StockView(OperationBase):
    """Stock across the network: the ledger, what was reported, and the gap.

    Both figures, always. They disagree routinely and the disagreement is the
    finding; a screen showing one would destroy the only signal it had.
    """

    template_name = "supply_chain/stock.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context

        opportunity_id = self.request.GET.get("opportunity_id") or None
        item_id = self.request.GET.get("item_id") or None
        context["items"] = self.op("item_list")
        context["item_id"] = int(item_id) if item_id else None
        context["opportunity_id"] = opportunity_id
        context["network"] = self.op(
            "network_stock",
            **{
                k: v
                for k, v in (
                    ("opportunity_id", int(opportunity_id) if opportunity_id else None),
                    ("item_id", context["item_id"]),
                )
                if v is not None
            },
        )
        return context


class DistributionView(OperationBase):
    """Resupply runs out to field workers, and what each line moved."""

    template_name = "supply_chain/distribution.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        context["runs"] = self.op("distribution_list")
        context["points"] = {p["id"]: p for p in self.op("supply_point_list", include_inactive=True)}
        return context
