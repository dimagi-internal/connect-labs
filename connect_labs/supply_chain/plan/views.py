"""The planning pages, one per person and rhythm, and the screens that change the plan.

  Supervisor, weekly          /supply/plan/            what to issue each field worker, who is running short,
                                                       stock follow-up (dosing), and the plan's three inputs
  LLO manager                 /supply/plan/stores/     store restocking; enough until the next delivery
  Running organisation        /supply/plan/programme/  enough across partners; when and how much to buy next;
                                                       can we account for every sachet?

All three read one operation (`stock_plan_view`), so a figure cannot differ between pages.
"""

from django.urls import reverse

from connect_labs.supply_chain.api_views import has_program_context
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.plan.forms import PlanBufferPlaceForm, PlanInputsForm
from connect_labs.supply_chain.stock.visit_views import as_of_payload, chosen_item, rule_items
from connect_labs.supply_chain.views import OperationBase

ROLES = (
    ("supervisor", "Supervisor, weekly", "supply_chain:plan"),
    ("stores", "Store manager", "supply_chain:plan_stores"),
    ("programme", "Running organisation", "supply_chain:plan_programme"),
)


class PlanView(OperationBase):
    template_name = "supply_chain/plan.html"
    role = "supervisor"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        context["role"] = self.role
        context["roles"] = [
            {"key": key, "label": label, "url": reverse(url), "active": key == self.role} for key, label, url in ROLES
        ]
        if not context["has_program_context"]:
            return context
        items = rule_items(self.op)
        item = chosen_item(self.request, items)
        context.update(items=items, item=item)
        if item is None:
            return context
        # The running organisation looks further ahead: when stock runs out may be months away.
        weeks = {"horizon_weeks": 26} if self.role == "programme" else {}
        picture = self.op("stock_plan_view", item_id=item["id"], **weeks, **as_of_payload(self.request))
        history = self.op("stock_plan_get", item_id=item["id"])["history"]
        fc_unit = (picture["break_even"] or {}).get("ration_per_visit")
        context.update(picture=picture, plan=picture["plan"], history=history, ration=fc_unit)
        if self.role == "programme" and not picture["scoped"]:
            flow = self.op("stock_flow", item_id=item["id"], **as_of_payload(self.request))
            context["account"] = flow.get("account")
        return context


class PlanStoresView(PlanView):
    role = "stores"


class PlanProgrammeView(PlanView):
    role = "programme"


class _PlanFormView(OperationFormView):
    operation = "stock_plan_set"
    submit_label = "Save"

    def item_id(self):
        wanted = self.request.GET.get("item_id") or ""
        if wanted.isdigit():
            return int(wanted)
        chosen = chosen_item(self.request, rule_items(self.op))
        return chosen["id"] if chosen else None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["item_id"] = self.item_id()
        return kwargs

    def breadcrumb(self, **kwargs):
        return [{"label": "Plan", "href": reverse("supply_chain:plan")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:plan")

    def redirect_to(self, result):
        return f"{reverse('supply_chain:plan')}?item_id={result['item_id']}"


class PlanInputsView(_PlanFormView):
    form_class = PlanInputsForm
    title = "Change the plan"
    intro = (
        "Three choices drive what the Plan pages say to issue and buy. Your change is saved as the plan with your "
        "name and the day, and the default stays beside it so anyone can see what was changed and from what."
    )


class PlanBufferPlaceView(_PlanFormView):
    form_class = PlanBufferPlaceForm
    title = "Extra days for one place"
    intro = (
        "Give one field worker or facility more (or fewer) days of buffer than everyone else. "
        "Leave the days empty to put it back on the buffer for everyone."
    )
