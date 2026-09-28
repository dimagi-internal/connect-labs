"""Screens for stock from visits: the rules (this task), the workers (Task 8)."""

from django.http import Http404
from django.urls import reverse

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.models import DispensingRule
from connect_labs.supply_chain.stock.dispensing_forms import DispensingRuleForm
from connect_labs.supply_chain.views import OperationBase


class DispensingRulesView(OperationBase):
    """Every rule in the programme, on and off, with the paths it reads."""

    template_name = "supply_chain/dispensing_rules.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if context["has_program_context"]:
            context["rules"] = self.op("dispensing_rule_list", include_inactive=True)
        return context


class _RuleScreen(OperationFormView):
    operation = "dispensing_rule_upsert"
    form_class = DispensingRuleForm
    submit_label = "Save rule"

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Stock", "href": reverse("supply_chain:stock")},
            {"label": "Dispensing rules", "href": reverse("supply_chain:dispensing_rules")},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:dispensing_rules")

    def redirect_to(self, result):
        return reverse("supply_chain:dispensing_rules")


class DispensingRuleCreateView(_RuleScreen):
    title = "New dispensing rule"
    intro = "What a visit on one opportunity gives out of one item, read from the form's own answers."
    footnote = "There is one rule per opportunity and item: saving one that exists edits it."


class DispensingRuleUpdateView(_RuleScreen):
    title = "Edit dispensing rule"
    intro = "Visits already posted are not read again; the change applies to visits read from now on."
    footnote = "The opportunity and item identify the rule, so they cannot change here."

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "editing": True}

    def get_initial(self):
        rule = DispensingRule.objects.filter(
            program_id=_access(self.request).program_id, pk=self.kwargs["rule_id"]
        ).first()
        if rule is None:
            raise Http404("no such rule in this programme")
        return {
            "opportunity_id": rule.opportunity_id,
            "item": rule.item_id,
            "resupply_point": rule.resupply_point_id,
            "active_from": rule.active_from,
            "lines": rule.lines,
            "form_names": "\n".join(rule.forms or []),
            "reports": rule.reports or None,
            "status": rule.status,
        }
