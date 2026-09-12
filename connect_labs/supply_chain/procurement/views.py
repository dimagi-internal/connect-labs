"""Procurement screens. Read-only context comes from operations, and the one
POST path goes through an operation too.

`call_operation` and `_access` are imported directly into this module rather
than reached only via the domain shell's `OperationBase.op()`: a name that
lives one module up cannot be patched from here (or from a test that patches
"connect_labs.supply_chain.procurement.views.call_operation"), so a bare
`from connect_labs.supply_chain.views import OperationBase as _Base` would
leave this module able to run real operations against a fake session in
tests, hitting production APIs instead of the mock. Mirroring the tiny `op()`
helper locally keeps the same one-path-to-the-domain contract without that
trap.
"""

from datetime import date, datetime

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from connect_labs.supply_chain.api_views import _access
from connect_labs.supply_chain.operations import call_operation


def _days_waiting(sent_on):
    """Days since outreach went out, or None when there is nothing to count from.

    `sent_on` is a plain ISO date string on the record (LabsRecord JSON has no
    datetime type), so Django's `timesince` filter — which requires a real
    datetime — is the wrong tool here and raises on a string.
    """
    if not sent_on:
        return None
    try:
        sent = datetime.fromisoformat(sent_on).date()
    except (TypeError, ValueError):
        return None
    return (date.today() - sent).days


# The quote-entry form submits every field as a plain string, but the
# quote_record operation's schema declares these as JSON integers (see
# operations._ID / _NON_NEGATIVE_INT). jsonschema does not coerce "5" to 5,
# so passing request.POST straight through 400s on every real submission
# that names a round, supplier, or item — i.e. every one of them.
_QUOTE_INT_FIELDS = {
    "round_id",
    "supplier_id",
    "item_id",
    "base_per_pack_stated",
    "base_unit_grams_stated",
    "shelf_life_months_stated",
    "lead_time_days",
}


@method_decorator(login_required, name="dispatch")
class _Base(TemplateView):
    def op(self, name, **payload):
        return call_operation(name, _access(self.request), payload)


class RoundBoardView(_Base):
    template_name = "supply_chain/procurement/round_board.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rounds"] = self.op("round_list")
        return context


class RoundDetailView(_Base):
    template_name = "supply_chain/procurement/round_detail.html"

    def get_context_data(self, round_id, **kwargs):
        context = super().get_context_data(**kwargs)
        context["round"] = self.op("round_get", round_id=round_id)
        outreach = self.op("outreach_list", round_id=round_id)
        for o in outreach:
            o["days_waiting"] = None if o.get("responded") else _days_waiting(o.get("sent_on"))
        context["outreach"] = outreach
        context["quotes"] = self.op("quote_list", round_id=round_id)
        return context


class ComparisonView(_Base):
    template_name = "supply_chain/procurement/comparison.html"

    def get_context_data(self, round_id, **kwargs):
        context = super().get_context_data(**kwargs)
        commodity = self.request.GET.get("commodity")
        context["comparison"] = self.op("round_compare", round_id=round_id, commodity_slug=commodity)
        context["round_id"] = round_id
        context["commodity_slug"] = commodity
        return context


class QuoteEntryView(_Base):
    template_name = "supply_chain/procurement/quote_entry.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["commodities"] = self.op("commodity_list")
        context["suppliers"] = self.op("supplier_list")
        context["rounds"] = self.op("round_list")
        # For the pack_spec_source=trade_item_confirmed picker.
        context["items"] = self.op("item_list")
        return context

    def post(self, request, *args, **kwargs):
        data = {}
        for key, value in request.POST.items():
            if key == "csrfmiddlewaretoken" or value == "":
                continue
            data[key] = int(value) if key in _QUOTE_INT_FIELDS else value
        created = self.op("quote_record", data=data)
        url = reverse("supply_chain:procurement_comparison", args=[created["round_id"]])
        return redirect(f"{url}?commodity={created['commodity_slug']}")


class RegistriesView(_Base):
    template_name = "supply_chain/procurement/registries.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["commodities"] = self.op("commodity_list")
        context["suppliers"] = self.op("supplier_list")
        return context
