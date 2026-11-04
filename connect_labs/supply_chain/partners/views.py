"""The Partners page: how each partner distributes, and who runs which place.

The page every partner-first screen starts from. A row per partner with its current way of distributing
and the history of changes, and the places that name no partner (the ones that read "Run by: not recorded"
elsewhere), each with a way to assign them.
"""

from django.urls import reverse

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.models import PartnerPlan, SupplyPoint
from connect_labs.supply_chain.partners import services
from connect_labs.supply_chain.partners.forms import AssignPlacesForm, PartnerPlanForm
from connect_labs.supply_chain.partners.models import DISTRIBUTION_MODEL_LABELS
from connect_labs.supply_chain.stock.services.issues import partner_by_point
from connect_labs.supply_chain.views import OperationBase


class PartnersView(OperationBase):
    template_name = "supply_chain/partners.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        from connect_labs.labs.models import LabsOrg

        program_id = _access(self.request).program_id
        owner = partner_by_point(program_id)
        plans = list(PartnerPlan.objects.filter(program_id=program_id).select_related("org"))
        org_ids = {pk for pk in owner.values() if pk} | {p.org_id for p in plans}
        names = dict(LabsOrg.objects.filter(pk__in=org_ids).values_list("pk", "name"))
        counts = {}
        for point in SupplyPoint.objects.filter(program_id=program_id).values("id", "kind"):
            org = owner.get(point["id"])
            if org:
                row = counts.setdefault(org, {"places": 0, "workers": 0})
                row["workers" if point["kind"] == "user_held" else "places"] += 1
        partners = []
        for org_id in sorted(org_ids, key=lambda pk: (names.get(pk) or "").lower()):
            history = [p for p in plans if p.org_id == org_id]
            current = next((p for p in history if p.ended_on is None), None)
            partners.append(
                {
                    "org_id": org_id,
                    "name": names.get(org_id) or f"Organisation {org_id}",
                    "current": current,
                    "current_label": DISTRIBUTION_MODEL_LABELS.get(current.distribution_model) if current else None,
                    "history": [
                        {"plan": p, "label": DISTRIBUTION_MODEL_LABELS[p.distribution_model]} for p in history
                    ],
                    **counts.get(org_id, {"places": 0, "workers": 0}),
                }
            )
        context.update(
            partners=partners,
            unassigned=services.places_without_partner(program_id),
            without_plan=[p for p in partners if p["current"] is None],
        )
        return context


class _PartnerScreen(OperationFormView):
    def breadcrumb(self, **kwargs):
        return [{"label": "Partners", "href": reverse("supply_chain:partners")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:partners")

    def redirect_to(self, result):
        return reverse("supply_chain:partners")


class PartnerPlanView(_PartnerScreen):
    operation = "partner_plan_set"
    form_class = PartnerPlanForm
    title = "How a partner distributes"
    intro = (
        "Does the partner send stock through health facilities (PHCs), straight to field workers who keep it at "
        "home, or a mix? The network is built from the answer: the right levels, a sensible minimum and maximum "
        "stock at each, and field workers under the right place. Changing the answer later is kept as a dated "
        "change of plan; nobody is moved unless you ask."
    )
    submit_label = "Save the plan"

    def get_initial(self):
        initial = super().get_initial()
        org = self.request.GET.get("org") or ""
        if org.isdigit():
            initial["org"] = int(org)
        return initial


class AssignPlacesView(_PartnerScreen):
    operation = "partner_places_assign"
    form_class = AssignPlacesForm
    title = "Say which partner runs these places"
    intro = (
        "Places with no partner read 'Run by: not recorded' on the Map and are left out of every partner's own "
        "pages. Pick the partner and tick the places it runs; places below them follow."
    )
    submit_label = "Assign"


class DeliverySuggestionsView(OperationBase):
    """A suggested delivery to each shared facility: a few days of what its field workers use, less what it holds.

    On request only (it runs the forecast). A suggestion, nothing is recorded; filter with `?partner=` like
    the other partner pages, `?days=` for the cover (7 to begin with).
    """

    template_name = "supply_chain/delivery_suggestions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        from connect_labs.supply_chain.partners.scope import partner_param, switcher
        from connect_labs.supply_chain.stock.visit_views import chosen_item, rule_items

        program_id = _access(self.request).program_id
        items = rule_items(self.op)
        item = chosen_item(self.request, items)
        days = self.request.GET.get("days") or ""
        cover = int(days) if days.isdigit() and 1 <= int(days) <= 60 else 7
        context.update(items=items, item=item, cover_days=cover, **switcher(self.request, program_id))
        if item is not None:
            context["suggestions"] = self.op(
                "stock_delivery_suggestion",
                item_id=item["id"],
                cover_days=cover,
                **({"org_id": partner_param(self.request)} if partner_param(self.request) else {}),
            )
        return context
