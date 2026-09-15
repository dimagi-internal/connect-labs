"""A resupply run out to field workers.

**A distribution is a batch header over many movements.** One run covers every
field worker served that day and emits one movement per line, so the ledger has
no special case for distribution and a worker's balance needs no separate
arithmetic. That is the point of a worker being a supply point rather than a
special kind of thing.

(`QuoteCorrectionForm` used to live here too; it moved to `forms.py`, beside
the `QuoteForm` whose field set it inherits.)
"""

from crispy_forms.layout import Column, Field, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain.forms import DATE, INPUT, SEARCHABLE, to_payload
from connect_labs.supply_chain.fulfilment_forms import ProvenancedForm
from connect_labs.supply_chain.models import Commodity, Distribution, Item, SupplyPoint

__all__ = ["DistributionForm", "DistributionLineFormSet"]


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
