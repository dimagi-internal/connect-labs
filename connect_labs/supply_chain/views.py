"""Django pages for the supply domain.

Every mutation goes through an operation, so the web UI can never do something
the API and MCP surfaces cannot. OperationBase.op() is the only way a view
reaches the domain.
"""

from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services.compliance import spec_verdict


@method_decorator(login_required, name="dispatch")
class OperationBase(TemplateView):
    def op(self, name, **payload):
        return call_operation(name, _access(self.request), payload)


class ItemMasterView(OperationBase):
    """The master item list, with the disagreement that justifies its existence.

    Two items under one commodity that disagree on pack configuration is the
    defect this layer exists to surface: it is invisible at commodity level and
    it silently corrupts every per-base-unit comparison.
    """

    template_name = "supply_chain/items.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = self.op("item_list")
        commodities = {c["slug"]: c for c in self.op("commodity_list")}

        packs_seen: dict[str, set] = {}
        for item in items:
            if item.get("base_per_pack"):
                packs_seen.setdefault(item["commodity_slug"], set()).add(item["base_per_pack"])

        for item in items:
            commodity = commodities.get(item["commodity_slug"], {})
            item["commodity_name"] = commodity.get("name", item["commodity_slug"])
            item["pack_disagrees"] = len(packs_seen.get(item["commodity_slug"], set())) > 1
            item["differs_from_commodity"] = bool(
                item.get("base_per_pack")
                and commodity.get("base_per_pack")
                and item["base_per_pack"] != commodity["base_per_pack"]
            )
            item["spec_verdict"] = spec_verdict(item.get("spec_attributes"), commodity.get("spec_requirements") or [])

        context["items"] = items
        context["commodities"] = commodities.values()
        return context


class DomainHomeView(OperationBase):
    """Landing page: the sub-components, and enough state to pick one."""

    template_name = "supply_chain/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        # round_list is programme-scoped (SupplyDataAccess.program_experiment
        # raises ValueError with no program_id); commodity_list is reference
        # tier and does not need this guard, but there is nothing useful to
        # show alongside an empty round list, so both wait for a programme.
        context["rounds"] = self.op("round_list") if context["has_program_context"] else []
        context["commodities"] = self.op("commodity_list") if context["has_program_context"] else []
        context["sub_components"] = [
            {
                "slug": "procurement",
                "label": "Procurement",
                "blurb": "Source commodities, compare quotes on one basis, award.",
                "url_name": "supply_chain:procurement_round_board",
                "available": True,
            },
            {
                "slug": "tracking",
                "label": "Tracking",
                "blurb": "Where consignments are, and what is on hand.",
                "url_name": None,
                "available": False,
            },
            {
                "slug": "distribution",
                "label": "Distribution",
                "blurb": "What reached which worker, and what it treated.",
                "url_name": None,
                "available": False,
            },
        ]
        return context
