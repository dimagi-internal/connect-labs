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

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import records
from connect_labs.supply_chain.forms import (
    DATE,
    INPUT,
    MONEY_INPUT,
    SEARCHABLE,
    SELECT,
    TEXTAREA,
    currency_select,
    set_choices,
    to_payload,
)
from connect_labs.supply_chain.fulfilment_forms import ProvenancedForm
from connect_labs.supply_chain.models import (
    Charge,
    Commodity,
    Item,
    Movement,
    Receipt,
    Shipment,
    StockCount,
    SupplyPoint,
)

__all__ = [
    "BatchLineFormSet",
    "ChargeForm",
    "MovementForm",
    "ReceiptForm",
    "RequiredDocumentForm",
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


class RequiredDocumentForm(ProvenancedForm):
    """Add one document to what a consignment needs to clear, and who owes it.

    One at a time, onto the list already there, because that is how the list
    grows in practice: the clearing agent asks for an import permit on
    Tuesday and a product registration on Thursday. The operation takes the
    whole list, so the form sends the existing one with the new line added.
    """

    kind = forms.ChoiceField(label=_("Document"), widget=forms.Select(attrs=SEARCHABLE))
    owed_by_org = forms.ModelChoiceField(
        label=_("Owed by"),
        queryset=LabsOrg.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_("Who has to produce it — the donor, the supplier, a clearing agent. They are who gets asked."),
    )

    class Meta:
        model = Shipment
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(
            self,
            "kind",
            [("", "—")] + [(value, str(value).replace("_", " ").capitalize()) for value in records.DOCUMENT_KINDS],
        )
        # Organisations are labs-wide, so this picker is deliberately unscoped.
        self.fields["owed_by_org"].queryset = LabsOrg.objects.order_by("name")
        self.fields["owed_by_org"].empty_label = _("Select an organisation…")
        # The list is being edited; who reported the shipment is not. Its
        # `source` is sent back unchanged, as the remove button does.
        del self.fields["source"]
        self.helper.layout = Layout(
            Row(Column("kind"), Column("owed_by_org"), css_class="grid md:grid-cols-2 gap-x-6"),
        )

    def clean_kind(self):
        kind = self.cleaned_data.get("kind")
        existing = (
            {entry.get("kind") for entry in (self.instance.required_documents or [])} if self.instance else set()
        )
        if kind in existing:
            raise forms.ValidationError(_("Already on the list for this consignment."))
        return kind

    def payload(self) -> dict:
        existing = list(self.instance.required_documents or []) if self.instance else []
        return {
            "source": self.instance.source,
            "required_documents": existing
            + [{"kind": self.cleaned_data["kind"], "owed_by_org_id": self.cleaned_data["owed_by_org"].pk}],
        }


def _programme_payees(access):
    """Organisations this programme has paid a landing charge to, most used first."""
    from django.db.models import Count

    program_id = getattr(access, "program_id", None)
    if not program_id:
        return []
    return list(
        LabsOrg.objects.filter(supply_charges__shipment__contract__program_id=program_id)
        .annotate(times=Count("supply_charges"))
        .order_by("-times", "name")
    )


class ChargeForm(ProvenancedForm):
    """Money paid to land a consignment, to somebody who is not the supplier."""

    class Meta:
        model = Charge
        fields = ["kind", "payee_org", "amount", "currency", "fx_rate_to_usd", "paid_on", "note"]
        widgets = {
            "kind": forms.Select(attrs=SELECT),
            "payee_org": forms.Select(attrs=SEARCHABLE),
            "amount": forms.NumberInput(attrs={**MONEY_INPUT, "placeholder": "250.00"}),
            "currency": forms.TextInput(attrs={**INPUT, "placeholder": "USD", "maxlength": 3}),
            "fx_rate_to_usd": forms.NumberInput(attrs={**MONEY_INPUT, "placeholder": "0.00065"}),
            "paid_on": forms.DateInput(attrs=DATE),
            "note": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "kind": _("What for"),
            "payee_org": _("Paid to"),
            "amount": _("Amount"),
            "currency": _("Currency"),
            "fx_rate_to_usd": _("Rate to USD"),
            "paid_on": _("Paid on"),
            "note": _("Note"),
        }
        help_texts = {
            "payee_org": _("Customs, a clearing agent, a haulier — never the supplier; that is the contract price."),
            "fx_rate_to_usd": _(
                "Dollars per one unit of the charge's currency (naira: about 0.00065). Only for a charge "
                "not in the order's currency; without it the landed total cannot add this charge, and says so."
            ),
            "paid_on": _("Leave empty if it has been assessed but not yet paid."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(
            self,
            "kind",
            [(value, str(value).replace("_", " ").capitalize()) for value in records.CHARGE_KINDS],
        )
        # Organisations are labs-wide -- the master registry, every org that
        # ever answered an EOI -- so the whole list is a long scroll for a
        # clearing agent. Whoever this programme has paid to land goods before
        # comes first; the rest stay reachable by typing, never removed.
        payees = _programme_payees(self.access)
        self.fields["payee_org"].queryset = LabsOrg.objects.order_by("name")
        self.fields["payee_org"].empty_label = _("Type to search organisations…")
        if payees:
            everyone = [
                (org.pk, org.name) for org in LabsOrg.objects.exclude(pk__in=[p.pk for p in payees]).order_by("name")
            ]
            self.fields["payee_org"].choices = [
                ("", self.fields["payee_org"].empty_label),
                (_("Paid before in this programme"), [(org.pk, org.name) for org in payees]),
                (_("Every organisation"), everyone),
            ]
        currency_select(self, "currency")
        self.helper.layout = Layout(
            Row(Column("kind"), Column("payee_org"), css_class="grid md:grid-cols-2 gap-x-6"),
            Row(
                Column("amount"),
                Column("currency"),
                Column("fx_rate_to_usd"),
                Column("paid_on"),
                css_class="grid md:grid-cols-4 gap-x-6",
            ),
            Field("note"),
            Field("source"),
        )

    def clean_currency(self):
        return (self.cleaned_data.get("currency") or "").strip().upper()


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


class DurableAwareSelect(forms.Select):
    """An item picker whose options say which items are durable equipment.

    A dispenser has no batch and no expiry. The movement form hides those two
    fields while a durable item is chosen (a few lines of script in
    operation_form.html, keyed on `data-durable-hides`), and clean() drops
    them regardless, so a durable movement never carries a batch someone
    typed out of habit.
    """

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instance = getattr(value, "instance", None)
        if instance is not None and getattr(instance, "is_durable", False):
            option["attrs"]["data-durable"] = "1"
        return option


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
            "item": DurableAwareSelect(attrs={**SEARCHABLE, "data-durable-hides": "batch expiry"}),
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
        item = cleaned.get("item")
        if item is not None and item.is_durable:
            # Equipment is not batched and does not expire: not asked for on the
            # screen, and not recorded if typed before the item was chosen.
            cleaned["batch"] = ""
            cleaned["expiry"] = None
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
        source, destination = cleaned.get("from_supply_point"), cleaned.get("to_supply_point")
        if source is not None and source == destination:
            self.add_error("to_supply_point", _("Pick somewhere other than where it came from."))
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
        # The trade item by its name. "(kpw-orszinc-copack)" after it wrapped
        # the chosen item onto three lines, and the ledger note beside "Found"
        # repeated the slug.
        self.fields["item"].label_from_instance = lambda item: item.name
        self.fields["supply_point"].empty_label = _("Select a supply point…")
        self.fields["commodity"].empty_label = _("Select a product…")
        self.fields["item"].empty_label = _("Not recorded")
        self.fields["commodity"].required = True
        self.fields["counted_on"].required = True
        set_choices(
            self,
            "kind",
            [
                ("self_reported", _("Reported to us, kept beside the ledger")),
                ("physical_count", _("Physically counted, kept beside the ledger")),
                ("override", _("Replace the ledger with this count")),
            ],
        )
        self.helper.layout = Layout(
            Row(Column("supply_point"), Column("counted_on"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("kind"),
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
