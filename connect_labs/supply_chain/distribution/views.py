"""The last two write screens: a resupply run, and correcting a quote.

With these, every operation the supply domain exposes has a screen.
"""

from django.http import Http404
from django.urls import reverse

from connect_labs.supply_chain.api_views import _access
from connect_labs.supply_chain.distribution.forms import DistributionForm, DistributionLineFormSet
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.forms import QuoteCorrectionForm
from connect_labs.supply_chain.models import Item, Quote, SupplyPoint


class DistributionRecordView(OperationFormView):
    """One resupply run out to field workers.

    A batch header over many movements: each line posts one, so the ledger has
    no special case for distribution and a worker's balance is a balance like
    any other. That is what the `kind="user_held"` ruling buys.
    """

    operation = "distribution_record"
    form_class = DistributionForm
    template_name = "supply_chain/distribution_form.html"
    title = "Record a resupply run"
    intro = (
        "One store, one day, one product, and a line per worker. Each line posts a movement, so "
        "the store's balance goes down and each worker's goes up — the same ledger as a transfer "
        "between stores."
    )
    submit_label = "Record the run"
    empty_message = "A run has to say who it went to."

    def workers(self):
        return SupplyPoint.objects.filter(program_id=_access(self.request).program_id, kind="user_held").order_by(
            "name"
        )

    def items(self):
        return Item.objects.filter(scope_key=_access(self.request).scope_key).order_by("name")

    def line_formset(self, data=None):
        return DistributionLineFormSet(
            data,
            prefix="lines",
            form_kwargs={"workers": self.workers(), "items": self.items()},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("has_program_context") and "lines" not in context:
            context["lines"] = self.line_formset(self.request.POST if self.request.method == "POST" else None)
            # Said on the page rather than discovered by an empty picker: a
            # programme with no user-held points has nobody to distribute to,
            # and the fix is on a different screen.
            context["no_workers"] = not self.workers().exists()
        return context

    def form_valid(self, form):
        lines = self.line_formset(self.request.POST)
        if not lines.is_valid():
            return self.render_to_response(self.get_context_data(form=form, lines=lines))

        kept = []
        for row in lines.cleaned_data:
            if not row or row.get("DELETE") or row.get("quantity") is None:
                continue
            line = {
                "to_supply_point_id": row["to_supply_point"].pk,
                "quantity": str(row["quantity"]),
                "quantity_unit": row["quantity_unit"],
            }
            if row.get("item"):
                line["item_id"] = row["item"].pk
            if row.get("batch"):
                line["batch"] = row["batch"]
            kept.append(line)

        if not kept:
            form.add_error(None, self.empty_message)
            return self.render_to_response(self.get_context_data(form=form, lines=lines))

        self._lines = kept
        return super().form_valid(form)

    def fixed(self, **kwargs):
        return {"data": {"lines": getattr(self, "_lines", [])}}

    def breadcrumb(self, **kwargs):
        return [{"label": "Distribution", "href": reverse("supply_chain:distribution")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:distribution")

    def redirect_to(self, result):
        return reverse("supply_chain:distribution")


class QuoteCorrectView(OperationFormView):
    """A corrected version of a quote, superseding the one it came from."""

    operation = "quote_correct"
    form_class = QuoteCorrectionForm
    title = "Correct a quote"
    intro = (
        "This writes a new version rather than editing this one. The original stays readable, so a "
        "comparison run last month still reproduces the numbers it showed then."
    )
    submit_label = "Save the correction"
    footnote = (
        "The old version stays on the supplier's record, marked as superseded, with your reason "
        "attached to the one that replaced it."
    )

    def quote(self):
        found = Quote.objects.filter(
            pk=self.kwargs["quote_id"], tender__program_id=_access(self.request).program_id
        ).first()
        if found is None:
            raise Http404(f"no quote {self.kwargs['quote_id']} in this programme")
        return found

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Opening on the existing figures: a correction is usually one wrong
        # number among fifteen right ones, and retyping the other fourteen is
        # how a second mistake gets in.
        kwargs["instance"] = self.quote()
        return kwargs

    def form_valid(self, form):
        # `reason` is a top-level argument of `quote_correct`, describing the
        # correction rather than the offer, so it is lifted out of the data
        # here — the form deliberately does not know the operation's shape.
        self._reason = form.cleaned_data["reason"]
        return super().form_valid(form)

    def fixed(self, **kwargs):
        return {"quote_id": int(kwargs["quote_id"]), "reason": getattr(self, "_reason", "")}

    def breadcrumb(self, **kwargs):
        quote = self.quote()
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_tender_board")},
            {
                "label": f"Tender {quote.tender_id}",
                "href": reverse("supply_chain:procurement_tender_detail", args=[quote.tender_id]),
            },
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_quote_detail", args=[kwargs["quote_id"]])

    def redirect_to(self, result):
        return reverse("supply_chain:procurement_quote_detail", args=[result["id"]])
