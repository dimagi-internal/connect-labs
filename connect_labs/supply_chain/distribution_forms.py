"""The last two write screens: a resupply run, and correcting a quote.

**A distribution is a batch header over many movements.** One run covers every
field worker served that day and emits one movement per line, so the ledger has
no special case for distribution and a worker's balance needs no separate
arithmetic. That is the point of a worker being a supply point rather than a
special kind of thing.

**Correcting a quote writes a new version rather than editing the old one.**
The original stays readable, so a comparison run last month still reproduces
the numbers it showed then. The screen therefore opens on the existing figures,
says plainly that saving supersedes rather than overwrites, and requires a
reason — because the version chain is only worth having if each link says why
it exists.
"""

from crispy_forms.layout import Column, Field, Fieldset, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain.forms import DATE, INPUT, SEARCHABLE, SELECT, TEXTAREA, ScopedForm, to_payload
from connect_labs.supply_chain.fulfilment_forms import MONEY_INPUT, ProvenancedForm
from connect_labs.supply_chain.models import Commodity, Distribution, Item, Quote, SupplyPoint

__all__ = ["DistributionForm", "DistributionLineFormSet", "QuoteCorrectionForm"]


class DistributionForm(ProvenancedForm):
    """One resupply run: where it went out from, on what day, for which product."""

    commodity = forms.ModelChoiceField(
        label=_("Product"),
        queryset=Commodity.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_("One run covers one product. Two products on one day are two runs."),
    )

    class Meta:
        model = Distribution
        fields = ["supply_point", "opportunity_id", "distributed_on", "reference"]
        widgets = {
            "supply_point": forms.Select(attrs=SEARCHABLE),
            "opportunity_id": forms.NumberInput(attrs={**INPUT, "min": 1}),
            "distributed_on": forms.DateInput(attrs=DATE),
            "reference": forms.TextInput(attrs=INPUT),
        }
        labels = {
            "supply_point": _("Out of"),
            "opportunity_id": _("Opportunity"),
            "distributed_on": _("On"),
            "reference": _("Reference"),
        }
        help_texts = {
            "supply_point": _("The store the stock left. Its balance goes down by the total of the lines."),
            "opportunity_id": _("Which opportunity's workers these are."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supply_point"].queryset = self.in_program(SupplyPoint).exclude(kind="user_held").order_by("name")
        self.fields["supply_point"].empty_label = _("Select a store…")
        self.fields["commodity"].queryset = self.scoped(Commodity).order_by("name")
        self.fields["commodity"].empty_label = _("Select a product…")
        self.fields["distributed_on"].required = True
        self.fields["opportunity_id"].required = True
        self.helper.layout = Layout(
            Row(
                Column("supply_point"),
                Column("commodity"),
                css_class="grid md:grid-cols-2 gap-x-6",
            ),
            Row(
                Column("distributed_on"),
                Column("opportunity_id"),
                Column("reference"),
                css_class="grid md:grid-cols-3 gap-x-6",
            ),
            Field("source"),
        )

    def payload(self) -> dict:
        data = to_payload(self.cleaned_data)
        commodity = self.cleaned_data.get("commodity")
        if commodity is not None:
            data["commodity_slug"] = commodity.slug
        data.pop("commodity_id", None)
        return data


class DistributionLineForm(forms.Form):
    """One worker's share of a run."""

    to_supply_point = forms.ModelChoiceField(
        label=_("To"),
        queryset=SupplyPoint.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_("The worker's own holding."),
    )
    item = forms.ModelChoiceField(
        label=_("Trade item"),
        queryset=Item.objects.none(),
        required=False,
        widget=forms.Select(attrs=SEARCHABLE),
    )
    batch = forms.CharField(label=_("Batch"), max_length=64, required=False, widget=forms.TextInput(attrs=INPUT))
    quantity = forms.DecimalField(
        label=_("Quantity"),
        min_value=0,
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs={**INPUT, "step": "any"}),
    )
    quantity_unit = forms.CharField(
        label=_("Unit"),
        max_length=32,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
    )

    def __init__(self, *args, workers=(), items=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["to_supply_point"].queryset = workers
        self.fields["to_supply_point"].empty_label = _("Select a worker…")
        self.fields["item"].queryset = items
        self.fields["item"].empty_label = _("Not recorded")


DistributionLineFormSet = forms.formset_factory(
    DistributionLineForm, extra=0, min_num=1, validate_min=True, can_delete=True
)


class QuoteCorrectionForm(ScopedForm):
    """A corrected version of a quote, superseding the one it came from.

    Not an edit. The original stays readable so a comparison run last month
    still reproduces the numbers it showed then — which is the whole reason
    quotes carry a version chain rather than being mutable rows.
    """

    reason = forms.CharField(
        label=_("What was wrong"),
        widget=forms.Textarea(
            attrs={**TEXTAREA, "placeholder": _("e.g. transcribed the per-carton price as per-sachet")}
        ),
        help_text=_("Kept on the new version. A chain of corrections is only worth having if each link says why."),
    )

    class Meta:
        model = Quote
        fields = [
            "as_quoted_amount",
            "as_quoted_unit",
            "as_quoted_currency",
            "quantity_basis",
            "quantity_basis_unit",
            "pack_spec_source",
            "base_per_pack_stated",
            "base_unit_grams_stated",
            "freight_basis",
            "freight_amount",
            "duties_basis",
            "duties_amount",
            "shelf_life_months_stated",
            "lead_time_days",
            "incoterm",
            "received_on",
        ]
        widgets = {
            "as_quoted_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "as_quoted_unit": forms.Select(attrs=SELECT),
            "as_quoted_currency": forms.TextInput(attrs={**INPUT, "maxlength": 3}),
            "quantity_basis": forms.NumberInput(attrs={**INPUT, "step": "any"}),
            "quantity_basis_unit": forms.TextInput(attrs=INPUT),
            "pack_spec_source": forms.Select(attrs=SELECT),
            "base_per_pack_stated": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "base_unit_grams_stated": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "freight_basis": forms.Select(attrs=SELECT),
            "freight_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "duties_basis": forms.Select(attrs=SELECT),
            "duties_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "shelf_life_months_stated": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "lead_time_days": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "incoterm": forms.TextInput(attrs=INPUT),
            "received_on": forms.DateInput(attrs=DATE),
        }
        labels = {
            "as_quoted_amount": _("Price, as they stated it"),
            "as_quoted_unit": _("Per"),
            "as_quoted_currency": _("Currency"),
            "quantity_basis": _("For a quantity of"),
            "quantity_basis_unit": _("Unit"),
            "pack_spec_source": _("Where the pack size came from"),
            "base_per_pack_stated": _("Units per pack, as stated"),
            "base_unit_grams_stated": _("Grams per unit, as stated"),
            "freight_basis": _("Freight"),
            "freight_amount": _("Freight amount"),
            "duties_basis": _("Duties"),
            "duties_amount": _("Duties amount"),
            "shelf_life_months_stated": _("Shelf life stated (months)"),
            "lead_time_days": _("Lead time (days)"),
            "incoterm": _("Incoterm"),
            "received_on": _("Received on"),
        }
        help_texts = {
            "as_quoted_amount": _("As the supplier wrote it. Normalisation happens downstream, not here."),
            "pack_spec_source": _(
                "A pack size we assumed and one the supplier stated are different evidence, "
                "so a comparison that mixes them says which is which."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        basis = [("not_specified", _("Not specified")), ("included", _("Included")), ("excluded", _("Excluded"))]
        self.fields["freight_basis"].choices = basis
        self.fields["duties_basis"].choices = basis
        self.fields["as_quoted_unit"].choices = [
            ("", "—"),
            ("per_base_unit", _("Per unit (sachet, tablet)")),
            ("per_pack", _("Per pack (carton)")),
            ("per_lot_total", _("Total for the lot")),
            ("per_metric_tonne", _("Per metric tonne")),
        ]
        self.fields["pack_spec_source"].choices = [
            ("not_stated", _("They did not say")),
            ("stated_on_quote", _("Stated on the quote")),
            ("trade_item_confirmed", _("Confirmed against the trade item")),
        ]
        self.helper.layout = Layout(
            Fieldset(
                str(_("What they quoted")),
                Row(
                    Column("as_quoted_amount"),
                    Column("as_quoted_unit"),
                    Column("as_quoted_currency"),
                    css_class="grid md:grid-cols-3 gap-x-6",
                ),
                Row(
                    Column("quantity_basis"),
                    Column("quantity_basis_unit"),
                    css_class="grid md:grid-cols-2 gap-x-6",
                ),
                css_class="pt-1",
            ),
            Fieldset(
                str(_("How it is packed")),
                Row(
                    Column("pack_spec_source"),
                    Column("base_per_pack_stated"),
                    Column("base_unit_grams_stated"),
                    css_class="grid md:grid-cols-3 gap-x-6",
                ),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("What else it includes")),
                Row(Column("freight_basis"), Column("freight_amount"), css_class="grid md:grid-cols-2 gap-x-6"),
                Row(Column("duties_basis"), Column("duties_amount"), css_class="grid md:grid-cols-2 gap-x-6"),
                Row(
                    Column("shelf_life_months_stated"),
                    Column("lead_time_days"),
                    Column("incoterm"),
                    Column("received_on"),
                    css_class="grid md:grid-cols-4 gap-x-6",
                ),
                css_class="pt-2",
            ),
            Field("reason"),
        )

    def clean_as_quoted_currency(self):
        return (self.cleaned_data.get("as_quoted_currency") or "").strip().upper()

    def payload(self) -> dict:
        # `reason` is a top-level argument of `quote_correct`, not part of the
        # quote's own data — it describes the correction, not the offer. The
        # view lifts it out; keeping the split here would mean the form knowing
        # the operation's argument shape, which is the view's job.
        return to_payload({k: v for k, v in self.cleaned_data.items() if k != "reason"})
