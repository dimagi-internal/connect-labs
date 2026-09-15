"""Write screens for the physical chain: shipments, receipts, movements, counts.

Group 3b. Where the money-chain screens record what was committed and billed,
these record what physically happened — and the two disagree routinely, which
is the whole reason they are separate.

**A receipt is the only event that brings stock into existence.** Not a
contract, not a shipment: goods in transit are dispatched and not yet on hand,
and counting them as stock is how a network reports holdings it does not have.
So the receipt screen is the one with the most care in it.

**The ledger is append-only.** A correction is an adjustment movement naming
its cause, never an edit — that is what makes a balance on any past date
reproducible. There is deliberately no "edit a movement" screen, and the
adjustment form asks for a reference rather than offering to change history.

**An adjustment is the only kind that may be negative.** Every other movement
kind is a positive quantity whose sign is fixed by what the kind means, so a
quantity that is "counted the wrong way round" cannot silently double or zero
a balance. The form enforces that asymmetry where somebody can see it.

Lines are formsets, for the reason `RoundForm`'s are: a receipt or a shipment
carries several batches, each with its own expiry, and that is the repeating
row `formset_factory` exists for.
"""

from crispy_forms.layout import Column, Field, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain import records
from connect_labs.supply_chain.forms import DATE, INPUT, SEARCHABLE, SELECT, TEXTAREA, set_choices, to_payload
from connect_labs.supply_chain.fulfilment_forms import ProvenancedForm
from connect_labs.supply_chain.models import Commodity, Item, Movement, Receipt, Shipment, StockCount, SupplyPoint

__all__ = [
    "BatchLineFormSet",
    "MovementForm",
    "ReceiptForm",
    "ShipmentForm",
    "ShipmentStatusForm",
    "StockCountForm",
]


class BatchLineForm(forms.Form):
    """One batch on a receipt or a shipment.

    Batch and expiry are on the LINE, not the header, because one consignment
    routinely carries two lots with different expiries — and a first-expiry-first
    issue policy cannot be run against a header that averaged them.
    """

    item = forms.ModelChoiceField(
        label=_("Trade item"),
        queryset=Item.objects.none(),
        required=False,
        widget=forms.Select(attrs=SEARCHABLE),
    )
    batch = forms.CharField(
        label=_("Batch"),
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("as printed on the carton")}),
    )
    expiry = forms.DateField(label=_("Expires"), required=False, widget=forms.DateInput(attrs=DATE))
    quantity = forms.DecimalField(
        label=_("Quantity"),
        min_value=0,
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs={**INPUT, "step": "any", "placeholder": "500"}),
    )
    quantity_unit = forms.CharField(
        label=_("Unit"),
        max_length=32,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
    )
    quantity_rejected = forms.DecimalField(
        label=_("Refused"),
        required=False,
        min_value=0,
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs={**INPUT, "step": "any"}),
    )
    rejection_reason = forms.CharField(
        label=_("Why refused"),
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs=INPUT),
    )

    def __init__(self, *args, items=(), show_rejection=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = items
        self.fields["item"].empty_label = _("Not recorded")
        if not show_rejection:
            # A dispatch has nothing to refuse yet — that happens on arrival.
            del self.fields["quantity_rejected"]
            del self.fields["rejection_reason"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_rejected") and not cleaned.get("rejection_reason"):
            # A refusal with no reason is an argument nobody can settle later,
            # and the supplier will ask.
            self.add_error("rejection_reason", _("Say why it was refused."))
        return cleaned


BatchLineFormSet = forms.formset_factory(BatchLineForm, extra=0, min_num=1, validate_min=True, can_delete=True)


class ShipmentForm(ProvenancedForm):
    """A dispatch. Its lines carry the batches a receipt later matches against."""

    class Meta:
        model = Shipment
        fields = ["reference", "sscc", "status", "dispatched_on", "expected_on", "carrier"]
        widgets = {
            "reference": forms.TextInput(attrs={**INPUT, "placeholder": _("their dispatch note number")}),
            "sscc": forms.TextInput(attrs={**INPUT, "inputmode": "numeric"}),
            "status": forms.Select(attrs=SELECT),
            "dispatched_on": forms.DateInput(attrs=DATE),
            "expected_on": forms.DateInput(attrs=DATE),
            "carrier": forms.TextInput(attrs=INPUT),
        }
        labels = {
            "reference": _("Reference"),
            "sscc": _("SSCC"),
            "status": _("Where it is"),
            "dispatched_on": _("Dispatched on"),
            "expected_on": _("Expected on"),
            "carrier": _("Carrier"),
        }
        help_texts = {
            "status": _("Dispatched and not yet received is never counted as stock on hand."),
            "sscc": _("The pallet's serial shipping container code, if there is one."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(
            self,
            "status",
            [(value, str(value).replace("_", " ").capitalize()) for value in records.SHIPMENT_STATUSES],
        )
        self.helper.layout = Layout(
            Row(Column("reference"), Column("sscc"), Column("carrier"), css_class="grid md:grid-cols-3 gap-x-6"),
            Row(
                Column("status"),
                Column("dispatched_on"),
                Column("expected_on"),
                css_class="grid md:grid-cols-3 gap-x-6",
            ),
            Field("source"),
        )


class ShipmentStatusForm(ProvenancedForm):
    """Where a consignment has got to.

    Its own screen because moving a shipment along is the thing that happens
    five times to one row, and asking for its whole detail again each time is
    how a carrier name gets wiped by somebody recording that it cleared customs.
    """

    class Meta:
        model = Shipment
        fields = ["status", "expected_on"]
        widgets = {"status": forms.Select(attrs=SELECT), "expected_on": forms.DateInput(attrs=DATE)}
        labels = {"status": _("Now"), "expected_on": _("Expected on")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(
            self,
            "status",
            [(value, str(value).replace("_", " ").capitalize()) for value in records.SHIPMENT_STATUSES],
        )
        self.helper.layout = Layout(
            Row(Column("status"), Column("expected_on"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("source"),
        )


class ReceiptForm(ProvenancedForm):
    """A goods received note — the event that brings stock into existence.

    Nothing before this counts as stock on hand. A contract is a promise and a
    shipment is in transit, and a network that counts either as holdings
    reports stock it does not have.
    """

    class Meta:
        model = Receipt
        fields = ["supply_point", "reference", "received_on"]
        widgets = {
            "supply_point": forms.Select(attrs=SEARCHABLE),
            "reference": forms.TextInput(attrs={**INPUT, "placeholder": _("GRN number")}),
            "received_on": forms.DateInput(attrs=DATE),
        }
        labels = {
            "supply_point": _("Received at"),
            "reference": _("Reference"),
            "received_on": _("Received on"),
        }
        help_texts = {
            "supply_point": _("Where the stock now is. This is what the balance is computed against."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supply_point"].queryset = self.in_program(SupplyPoint).order_by("name")
        self.fields["supply_point"].empty_label = _("Select a supply point…")
        self.fields["received_on"].required = True
        self.helper.layout = Layout(
            Row(
                Column("supply_point"),
                Column("received_on"),
                Column("reference"),
                css_class="grid md:grid-cols-3 gap-x-6",
            ),
            Field("source"),
        )


class MovementForm(ProvenancedForm):
    """One posting to the append-only ledger.

    No edit screen exists for this, and that is deliberate: a correction is
    another movement naming its cause, which is what makes a balance on any
    past date reproducible.
    """

    class Meta:
        model = Movement
        fields = [
            "kind",
            "occurred_on",
            "from_supply_point",
            "to_supply_point",
            "commodity",
            "item",
            "batch",
            "expiry",
            "quantity",
            "quantity_unit",
            "reference",
        ]
        widgets = {
            "kind": forms.Select(attrs=SELECT),
            "occurred_on": forms.DateInput(attrs=DATE),
            "from_supply_point": forms.Select(attrs=SEARCHABLE),
            "to_supply_point": forms.Select(attrs=SEARCHABLE),
            "commodity": forms.Select(attrs=SEARCHABLE),
            "item": forms.Select(attrs=SEARCHABLE),
            "batch": forms.TextInput(attrs=INPUT),
            "expiry": forms.DateInput(attrs=DATE),
            "quantity": forms.NumberInput(attrs={**INPUT, "step": "any"}),
            "quantity_unit": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
            "reference": forms.TextInput(attrs=INPUT),
        }
        labels = {
            "kind": _("What happened"),
            "occurred_on": _("On"),
            "from_supply_point": _("Out of"),
            "to_supply_point": _("Into"),
            "commodity": _("Product"),
            "item": _("Trade item"),
            "batch": _("Batch"),
            "expiry": _("Expires"),
            "quantity": _("Quantity"),
            "quantity_unit": _("Unit"),
            "reference": _("Reference"),
        }
        help_texts = {
            "quantity": _("Positive, except for an adjustment — that is the one kind allowed to be negative."),
            "reference": _("What this posting answers to: a stock count, a waybill, an incident."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        points = self.in_program(SupplyPoint).order_by("name")
        self.fields["from_supply_point"].queryset = points
        self.fields["to_supply_point"].queryset = points
        self.fields["commodity"].queryset = self.scoped(Commodity).order_by("name")
        self.fields["item"].queryset = self.scoped(Item).order_by("name")
        for name, label in (
            ("from_supply_point", _("Nowhere — it came into existence")),
            ("to_supply_point", _("Nowhere — it left")),
            ("item", _("Not recorded")),
        ):
            self.fields[name].empty_label = label
        self.fields["commodity"].empty_label = _("Select a product…")
        self.fields["commodity"].required = True
        self.fields["occurred_on"].required = True
        set_choices(
            self,
            "kind",
            [(value, str(value).replace("_", " ").capitalize()) for value in records.MOVEMENT_KINDS],
        )
        self.helper.layout = Layout(
            Row(Column("kind"), Column("occurred_on"), css_class="grid md:grid-cols-2 gap-x-6"),
            Row(
                Column("from_supply_point"),
                Column("to_supply_point"),
                css_class="grid md:grid-cols-2 gap-x-6",
            ),
            Row(Column("commodity"), Column("item"), css_class="grid md:grid-cols-2 gap-x-6"),
            Row(
                Column("quantity"),
                Column("quantity_unit"),
                Column("batch"),
                Column("expiry"),
                css_class="grid md:grid-cols-4 gap-x-6",
            ),
            Field("reference"),
            Field("source"),
        )

    def clean(self):
        cleaned = super().clean()
        kind, quantity = cleaned.get("kind"), cleaned.get("quantity")
        if quantity is not None and quantity < 0 and kind not in records.SIGNED_MOVEMENT_KINDS:
            # The sign of every other kind is fixed by what the kind MEANS, in
            # one place, precisely so a movement counted the wrong way round
            # cannot silently double or zero a balance.
            self.add_error(
                "quantity",
                _("Only an adjustment may be negative — every other kind's direction comes from what it is."),
            )
        if quantity == 0:
            self.add_error("quantity", _("A movement of nothing is not a movement."))
        if kind == "transfer" and not (cleaned.get("from_supply_point") and cleaned.get("to_supply_point")):
            self.add_error("to_supply_point", _("A transfer needs somewhere out of and somewhere into."))
        return cleaned

    def payload(self) -> dict:
        data = to_payload(self.cleaned_data)
        commodity = self.cleaned_data.get("commodity")
        if commodity is not None:
            data["commodity_slug"] = commodity.slug
        data.pop("commodity_id", None)
        return data


class StockCountForm(ProvenancedForm):
    """What somebody says is actually on hand.

    An observation, not a correction: recording one does not move the ledger.
    The variance between this and the balance is the finding, and turning it
    into an adjustment is a separate, deliberate act.
    """

    class Meta:
        model = StockCount
        fields = [
            "supply_point",
            "commodity",
            "item",
            "batch",
            "kind",
            "counted_on",
            "quantity",
            "quantity_unit",
            "reason",
        ]
        widgets = {
            "supply_point": forms.Select(attrs=SEARCHABLE),
            "commodity": forms.Select(attrs=SEARCHABLE),
            "item": forms.Select(attrs=SEARCHABLE),
            "batch": forms.TextInput(attrs=INPUT),
            "kind": forms.Select(attrs=SELECT),
            "counted_on": forms.DateInput(attrs=DATE),
            "quantity": forms.NumberInput(attrs={**INPUT, "step": "any", "min": 0}),
            "quantity_unit": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
            "reason": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "supply_point": _("Counted at"),
            "commodity": _("Product"),
            "item": _("Trade item"),
            "batch": _("Batch"),
            "kind": _("How it was counted"),
            "counted_on": _("Counted on"),
            "quantity": _("Found"),
            "quantity_unit": _("Unit"),
            "reason": _("Notes"),
        }
        help_texts = {
            "quantity": _("Zero is a real and important observation — it is a stockout, not a missing answer."),
            "kind": _("A self-report and a physical count are not the same evidence, so they are not the same kind."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supply_point"].queryset = self.in_program(SupplyPoint).order_by("name")
        self.fields["commodity"].queryset = self.scoped(Commodity).order_by("name")
        self.fields["item"].queryset = self.scoped(Item).order_by("name")
        self.fields["supply_point"].empty_label = _("Select a supply point…")
        self.fields["commodity"].empty_label = _("Select a product…")
        self.fields["item"].empty_label = _("Not recorded")
        self.fields["commodity"].required = True
        self.fields["counted_on"].required = True
        set_choices(
            self,
            "kind",
            [
                ("self_reported", _("Somebody reported it")),
                ("physical_count", _("Physically counted")),
                ("override", _("Set by hand, overriding both")),
            ],
        )
        self.helper.layout = Layout(
            Row(
                Column("supply_point"),
                Column("counted_on"),
                Column("kind"),
                css_class="grid md:grid-cols-3 gap-x-6",
            ),
            Row(Column("commodity"), Column("item"), Column("batch"), css_class="grid md:grid-cols-3 gap-x-6"),
            Row(Column("quantity"), Column("quantity_unit"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("reason"),
            Field("source"),
        )

    def payload(self) -> dict:
        data = to_payload(self.cleaned_data)
        commodity = self.cleaned_data.get("commodity")
        if commodity is not None:
            data["commodity_slug"] = commodity.slug
        data.pop("commodity_id", None)
        # Zero is a real observation, and `to_payload` keeps it -- it drops
        # None and "" only. Asserted directly in the tests, because a count of
        # nothing is a stockout and dropping it would report the point as
        # never having been counted.
        return data
