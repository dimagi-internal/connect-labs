"""Forms for update links: the one a programme member issues a link with, and
the small ones behind the link itself.

**The public forms offer only what the link covers.** Every picker is a
`ModelChoiceField` over the link's own scope (`service.scope_for`), so a row
outside it is not merely hidden -- a POST naming its id fails validation as
"not one of the available choices". `service.submit` then checks again, because
a queryset is one refactor away from being widened.

**Units are "packs or single units", never typed.** The unit comes off the
product's own ladder (`service._unit`). A supplier typing "ctn" where the
ledger says "carton" would split one balance into two that never add up.
"""

from decimal import Decimal

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Layout, Row
from django import forms
from django.core.validators import URLValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.forms import DATE, INPUT, SEARCHABLE, SELECT
from connect_labs.supply_chain.models import AwardApproval, Contract, Item, Payment, Shipment, SupplyPoint
from connect_labs.supply_chain.update_links.operations import DEFAULT_EXPIRY_DAYS, MAX_EXPIRY_DAYS
from connect_labs.supply_chain.update_links.service import CONFIRMABLE, SUPPLIER_SHIPMENT_STATUSES

__all__ = [
    "UpdateLinkIssueForm",
    "ConfirmOrderForm",
    "ConfirmPaymentForm",
    "RecordShipmentForm",
    "UpdateShipmentForm",
    "RecordReceiptForm",
    "RecordStockCountForm",
    "RecordReleaseForm",
    "RecordAnswerForm",
    "PUBLIC_FORMS",
]

QUANTITY_INPUT = {**INPUT, "step": "any", "inputmode": "decimal", "min": "0"}

UNIT_BASIS = [
    ("pack", _("Packs (cartons, boxes)")),
    ("base", _("Single units")),
]

# Dates that are almost always today, so the field starts there.
TODAY_FIELDS = {"received_on", "counted_on", "occurred_on", "dispatched_on"}


def _tidy(helper_owner, *rows):
    helper = FormHelper(helper_owner)
    helper.form_tag = False
    helper.disable_csrf = True
    helper.layout = Layout(*rows)
    return helper


def _pair(a, b):
    return Row(Column(a), Column(b), css_class="grid md:grid-cols-2 gap-x-6")


def _triple(a, b, c):
    return Row(Column(a), Column(b), Column(c), css_class="grid md:grid-cols-3 gap-x-6")


# ---- issuing a link (a programme member's screen) ---------------------------


class _ContractChoices(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        what = obj.item.name if obj.item_id else obj.commodity.name
        return f"{obj.reference or f'Order {obj.pk}'} — {obj.supplier.name}, {what} ({obj.status.replace('_', ' ')})"


class _ApprovalChoices(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        what = obj.award.quote.item.name if obj.award.quote.item_id else obj.award.commodity.name
        return f"{obj.approver_org.name} ({obj.role}) — the award to {obj.award.supplier.name}, {what}"


class _PointChoices(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} ({obj.kind.replace('_', ' ')})"


class UpdateLinkIssueForm(forms.Form):
    org = forms.ModelChoiceField(
        label=_("Organisation"),
        queryset=LabsOrg.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_(
            "Whoever will use the link — EHA, a local partner. Everything they record is marked as "
            "reported by this organisation."
        ),
    )
    contracts = _ContractChoices(
        label=_("Orders it covers"),
        queryset=Contract.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "They can confirm these orders and their payments, and record dispatches and receipts against "
            "them. No other order — not even another one with the same supplier."
        ),
    )
    supply_points = _PointChoices(
        label=_("Supply points it covers"),
        queryset=SupplyPoint.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "Where they can receive goods, count stock and release it. A release goes from one of these "
            "to another, so include the collecting partner's store if they hand stock over."
        ),
    )
    approvals = _ApprovalChoices(
        label=_("Approvals it can answer"),
        queryset=AwardApproval.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "For an approver — a technical partner confirming a product, a funder approving its use. "
            "Only approvals asked of this organisation are accepted; it answers them itself."
        ),
    )
    expires_in_days = forms.IntegerField(
        label=_("Stops working after (days)"),
        min_value=1,
        max_value=MAX_EXPIRY_DAYS,
        initial=DEFAULT_EXPIRY_DAYS,
        widget=forms.NumberInput(attrs={**INPUT, "min": 1, "max": MAX_EXPIRY_DAYS}),
        help_text=_("At most 90. You can revoke it sooner at any time."),
    )
    label = forms.CharField(
        label=_("What to call it"),
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. EHA — co-pack orders, 2026")}),
    )

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        self.fields["org"].queryset = LabsOrg.objects.order_by("name")
        self.fields["org"].empty_label = _("Select an organisation…")
        program_id = getattr(access, "program_id", None) if access else None
        if program_id:
            self.fields["contracts"].queryset = (
                Contract.objects.filter(program_id=program_id)
                .exclude(status__in=("closed", "cancelled"))
                .select_related("supplier", "item", "commodity")
                .order_by("-created_at")
            )
            self.fields["supply_points"].queryset = SupplyPoint.objects.filter(
                program_id=program_id, status="active"
            ).exclude(kind="user_held")
            self.fields["approvals"].queryset = (
                AwardApproval.objects.filter(award__round__program_id=program_id, status="requested")
                .select_related("approver_org", "award__supplier", "award__quote__item", "award__commodity")
                .order_by("-requested_on")
            )
        self.helper = _tidy(self, _pair("org", "label"), "contracts", "supply_points", "approvals", "expires_in_days")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("contracts") and not cleaned.get("supply_points") and not cleaned.get("approvals"):
            raise forms.ValidationError(
                _("Pick at least one order, supply point or approval — a link has to cover something.")
            )
        return cleaned

    def payload(self) -> dict:
        data = {
            "org_id": self.cleaned_data["org"].pk,
            "contract_ids": [c.pk for c in self.cleaned_data.get("contracts") or []],
            "supply_point_ids": [p.pk for p in self.cleaned_data.get("supply_points") or []],
            "approval_ids": [a.pk for a in self.cleaned_data.get("approvals") or []],
            "expires_in_days": self.cleaned_data["expires_in_days"],
        }
        if self.cleaned_data.get("label"):
            data["label"] = self.cleaned_data["label"]
        return data


# ---- behind the link --------------------------------------------------------


class _OrderChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        what = obj.item.name if obj.item_id else obj.commodity.name
        quantity = f", {obj.quantity.normalize():f} {obj.quantity_unit}" if obj.quantity is not None else ""
        return f"{obj.reference or f'Order {obj.pk}'} — {what}{quantity}"


class _PointChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.name


class _ItemChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} ({obj.sku})"


class _ShipmentChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        reference = obj.reference or f"dispatch {obj.pk}"
        return f"{reference} — {obj.contract.reference or f'order {obj.contract_id}'} ({obj.status.replace('_', ' ')})"


class _PaymentChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        order = obj.invoice.contract.reference or f"order {obj.invoice.contract_id}"
        return f"{obj.amount.normalize():f} {obj.currency} paid {obj.paid_on.isoformat()} — {order}"


class _AnswerChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        what = obj.award.quote.item.name if obj.award.quote.item_id else obj.award.commodity.name
        return f"{what} — the award to {obj.award.supplier.name} (asked {obj.requested_on.isoformat()})"


class PublicForm(forms.Form):
    """One action on the link's page. Knows its own scope and its own prefix.

    The prefix is the action name, so seven forms can share one page and one
    POST names exactly which of them it is.
    """

    action = ""
    title = ""
    intro = ""
    submit_label = _("Save")
    # A form for the organisation an approval was asked of, rather than for a
    # supplier. A link shows the forms for what it covers and no others.
    for_approvers = False

    def __init__(self, *args, scope=None, **kwargs):
        kwargs.setdefault("prefix", self.action)
        super().__init__(*args, **kwargs)
        self.scope = scope
        self.limit_to_scope(scope)
        for name, field in self.fields.items():
            if isinstance(field, forms.ModelChoiceField):
                field.empty_label = _("Choose…")
                # One option is no choice at all: pick it, rather than make a
                # supplier open a dropdown to find the only thing in it.
                if scope is not None and not self.is_bound:
                    only = list(field.queryset[:2])
                    if len(only) == 1:
                        self.initial.setdefault(name, only[0].pk)
            elif name in TODAY_FIELDS and not self.is_bound:
                self.initial.setdefault(name, timezone.localdate())
        self.helper = _tidy(self, *self.rows())

    def limit_to_scope(self, scope):
        """Point every picker at the link's rows. Empty without a scope."""

    def rows(self):
        return list(self.fields)

    def is_available(self) -> bool:
        """Whether this action has anything to act on for this link."""
        return True

    def payload(self) -> dict:
        return dict(self.cleaned_data)


def _quantity(label, required=True, allow_zero=False):
    return forms.DecimalField(
        label=label,
        required=required,
        # Positive unless zero is a real observation (a count of nothing is a
        # stockout). A negative movement is refused by the ledger's own
        # constraint, and that refusal should be a field error, not a 500.
        min_value=Decimal("0") if allow_zero else Decimal("0.0001"),
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs=QUANTITY_INPUT),
    )


def _unit_basis():
    return forms.ChoiceField(
        label=_("Counted in"), choices=UNIT_BASIS, initial="pack", widget=forms.Select(attrs=SELECT)
    )


class ConfirmOrderForm(PublicForm):
    action = "confirm_order"
    title = _("Confirm an order")
    intro = _("Tell the programme team you have accepted this order and will supply it.")
    submit_label = _("Confirm this order")

    contract = _OrderChoice(label=_("Order"), queryset=Contract.objects.none(), widget=forms.Select(attrs=SELECT))

    def limit_to_scope(self, scope):
        if scope is not None:
            self.fields["contract"].queryset = scope.contracts.filter(status__in=CONFIRMABLE)

    def is_available(self):
        return self.fields["contract"].queryset.exists()


class ConfirmPaymentForm(PublicForm):
    action = "confirm_payment"
    title = _("Confirm a payment was received")
    intro = _("Tell the programme team a payment they recorded has reached you.")
    submit_label = _("Confirm payment received")

    payment = _PaymentChoice(label=_("Payment"), queryset=Payment.objects.none(), widget=forms.Select(attrs=SELECT))
    received_on = forms.DateField(label=_("Received on"), widget=forms.DateInput(attrs=DATE))

    def limit_to_scope(self, scope):
        if scope is not None:
            # Only payments not yet confirmed: one already confirmed offered
            # again reads as if the confirmation had not been recorded.
            self.fields["payment"].queryset = scope.payments.filter(confirmed_by_payee_on__isnull=True)

    def rows(self):
        return ["payment", "received_on"]

    def is_available(self):
        return self.fields["payment"].queryset.exists()


class RecordShipmentForm(PublicForm):
    action = "record_shipment"
    title = _("Record a dispatch")
    intro = _("Goods that have left you for this order. They count as in transit until someone receives them.")
    submit_label = _("Record dispatch")

    contract = _OrderChoice(label=_("Order"), queryset=Contract.objects.none(), widget=forms.Select(attrs=SELECT))
    status = forms.ChoiceField(
        label=_("Where it is now"),
        choices=[(s, s.replace("_", " ").capitalize()) for s in SUPPLIER_SHIPMENT_STATUSES],
        initial="dispatched",
        widget=forms.Select(attrs=SELECT),
    )
    reference = forms.CharField(
        label=_("Waybill or dispatch note number"), required=False, max_length=64, widget=forms.TextInput(attrs=INPUT)
    )
    carrier = forms.CharField(label=_("Carrier"), required=False, max_length=255, widget=forms.TextInput(attrs=INPUT))
    dispatched_on = forms.DateField(label=_("Dispatched on"), required=False, widget=forms.DateInput(attrs=DATE))
    expected_on = forms.DateField(label=_("Expected to arrive"), required=False, widget=forms.DateInput(attrs=DATE))
    quantity = _quantity(_("Quantity"))
    unit_basis = _unit_basis()
    batch = forms.CharField(
        label=_("Batch or lot"), required=False, max_length=64, widget=forms.TextInput(attrs=INPUT)
    )
    expiry = forms.DateField(label=_("Expiry"), required=False, widget=forms.DateInput(attrs=DATE))

    def limit_to_scope(self, scope):
        if scope is not None:
            self.fields["contract"].queryset = scope.contracts.exclude(status__in=("closed", "cancelled"))

    def rows(self):
        return [
            _pair("contract", "status"),
            _pair("reference", "carrier"),
            _pair("dispatched_on", "expected_on"),
            _pair("quantity", "unit_basis"),
            _pair("batch", "expiry"),
        ]

    def is_available(self):
        return self.fields["contract"].queryset.exists()


class UpdateShipmentForm(PublicForm):
    action = "update_shipment"
    title = _("Move a dispatch along")
    intro = _("Where a consignment has got to — at customs, cleared, delivered.")
    submit_label = _("Update dispatch")

    shipment = _ShipmentChoice(
        label=_("Dispatch"), queryset=Shipment.objects.none(), widget=forms.Select(attrs=SELECT)
    )
    status = forms.ChoiceField(
        label=_("Now"),
        choices=[(s, s.replace("_", " ").capitalize()) for s in SUPPLIER_SHIPMENT_STATUSES],
        widget=forms.Select(attrs=SELECT),
    )
    expected_on = forms.DateField(label=_("Expected to arrive"), required=False, widget=forms.DateInput(attrs=DATE))

    def limit_to_scope(self, scope):
        if scope is not None:
            self.fields["shipment"].queryset = scope.shipments.exclude(status__in=("delivered", "lost"))

    def rows(self):
        return [_triple("shipment", "status", "expected_on")]

    def is_available(self):
        return self.fields["shipment"].queryset.exists()


class RecordReceiptForm(PublicForm):
    action = "record_receipt"
    title = _("Record goods received")
    intro = _(
        "Goods that arrived and were checked. What you accept becomes stock at that supply point; what you "
        "reject is kept on the record with the reason, and never counts as stock."
    )
    submit_label = _("Record receipt")

    contract = _OrderChoice(label=_("Order"), queryset=Contract.objects.none(), widget=forms.Select(attrs=SELECT))
    supply_point = _PointChoice(
        label=_("Received at"), queryset=SupplyPoint.objects.none(), widget=forms.Select(attrs=SELECT)
    )
    # Optional: goods can arrive that nobody recorded dispatching. When they do
    # match a dispatch, naming it is what stops that consignment reading "in
    # transit" beside the stock it became, and going overdue the day after.
    shipment = _ShipmentChoice(
        label=_("From the dispatch"),
        queryset=Shipment.objects.none(),
        required=False,
        empty_label=_("Not a recorded dispatch"),
        widget=forms.Select(attrs=SELECT),
    )
    received_on = forms.DateField(label=_("Received on"), widget=forms.DateInput(attrs=DATE))
    reference = forms.CharField(
        label=_("Goods received note number"), required=False, max_length=64, widget=forms.TextInput(attrs=INPUT)
    )
    quantity_accepted = _quantity(_("Accepted"), allow_zero=True)
    quantity_rejected = _quantity(_("Rejected"), required=False, allow_zero=True)
    unit_basis = _unit_basis()
    rejection_reason = forms.CharField(
        label=_("Why any were rejected"),
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. crushed cartons")}),
    )
    batch = forms.CharField(
        label=_("Batch or lot"), required=False, max_length=64, widget=forms.TextInput(attrs=INPUT)
    )
    expiry = forms.DateField(label=_("Expiry"), required=False, widget=forms.DateInput(attrs=DATE))

    def limit_to_scope(self, scope):
        if scope is not None:
            self.fields["contract"].queryset = scope.contracts.exclude(status__in=("closed", "cancelled"))
            self.fields["supply_point"].queryset = scope.supply_points
            self.fields["shipment"].queryset = scope.shipments.exclude(status__in=("delivered", "lost"))

    def rows(self):
        return [
            _pair("contract", "supply_point"),
            "shipment",
            _pair("received_on", "reference"),
            _triple("quantity_accepted", "quantity_rejected", "unit_basis"),
            "rejection_reason",
            _pair("batch", "expiry"),
        ]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_rejected") and not (cleaned.get("rejection_reason") or "").strip():
            self.add_error("rejection_reason", _("Say why, so the rejection can be followed up."))
        return cleaned

    def is_available(self):
        return self.fields["contract"].queryset.exists() and self.fields["supply_point"].queryset.exists()


class RecordStockCountForm(PublicForm):
    action = "record_stock_count"
    title = _("Record a stock count")
    intro = _(
        "What is physically on the shelf. It is kept beside what the records say, so any difference "
        "shows up rather than one figure quietly replacing the other."
    )
    submit_label = _("Record count")

    supply_point = _PointChoice(
        label=_("Where"), queryset=SupplyPoint.objects.none(), widget=forms.Select(attrs=SELECT)
    )
    item = _ItemChoice(label=_("Product"), queryset=Item.objects.none(), widget=forms.Select(attrs=SELECT))
    counted_on = forms.DateField(label=_("Counted on"), widget=forms.DateInput(attrs=DATE))
    quantity = _quantity(_("Quantity on hand"), allow_zero=True)
    unit_basis = _unit_basis()
    batch = forms.CharField(
        label=_("Batch or lot"), required=False, max_length=64, widget=forms.TextInput(attrs=INPUT)
    )

    def limit_to_scope(self, scope):
        if scope is not None:
            self.fields["supply_point"].queryset = scope.supply_points
            self.fields["item"].queryset = scope.items

    def rows(self):
        return [_pair("supply_point", "item"), _triple("counted_on", "quantity", "unit_basis"), "batch"]

    def is_available(self):
        return self.fields["supply_point"].queryset.exists() and self.fields["item"].queryset.exists()


class RecordReleaseForm(PublicForm):
    action = "record_release"
    title = _("Record a release")
    intro = _("Stock handed over from one place to another — a partner collecting from your warehouse.")
    submit_label = _("Record release")

    from_supply_point = _PointChoice(
        label=_("From"), queryset=SupplyPoint.objects.none(), widget=forms.Select(attrs=SELECT)
    )
    to_supply_point = _PointChoice(
        label=_("To"), queryset=SupplyPoint.objects.none(), widget=forms.Select(attrs=SELECT)
    )
    item = _ItemChoice(label=_("Product"), queryset=Item.objects.none(), widget=forms.Select(attrs=SELECT))
    occurred_on = forms.DateField(label=_("Released on"), widget=forms.DateInput(attrs=DATE))
    quantity = _quantity(_("Quantity"))
    unit_basis = _unit_basis()
    batch = forms.CharField(
        label=_("Batch or lot"), required=False, max_length=64, widget=forms.TextInput(attrs=INPUT)
    )
    reference = forms.CharField(
        label=_("Release note number"), required=False, max_length=64, widget=forms.TextInput(attrs=INPUT)
    )

    def limit_to_scope(self, scope):
        if scope is not None:
            self.fields["from_supply_point"].queryset = scope.supply_points
            self.fields["to_supply_point"].queryset = scope.supply_points
            self.fields["item"].queryset = scope.items

    def rows(self):
        return [
            _pair("from_supply_point", "to_supply_point"),
            _pair("item", "occurred_on"),
            _pair("quantity", "unit_basis"),
            _pair("batch", "reference"),
        ]

    def clean(self):
        cleaned = super().clean()
        source, destination = cleaned.get("from_supply_point"), cleaned.get("to_supply_point")
        if source is not None and source == destination:
            self.add_error("to_supply_point", _("Pick somewhere other than where it came from."))
        return cleaned

    def is_available(self):
        return self.fields["from_supply_point"].queryset.count() >= 2 and self.fields["item"].queryset.exists()


class RecordAnswerForm(PublicForm):
    action = "record_answer"
    title = _("Record your answer")
    intro = _(
        "Approve or decline what the programme asked you to confirm. Your answer is recorded as yours, "
        "and until you have given it no order can be placed against the award."
    )
    submit_label = _("Record my answer")
    for_approvers = True

    approval = _AnswerChoice(
        label=_("What you were asked"), queryset=AwardApproval.objects.none(), widget=forms.Select(attrs=SELECT)
    )
    status = forms.ChoiceField(
        label=_("Your answer"),
        choices=[("approved", _("Approve")), ("declined", _("Decline"))],
        widget=forms.Select(attrs=SELECT),
    )
    note = forms.CharField(
        label=_("Note"),
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={**INPUT, "rows": 2, "class": "base-input !h-auto min-h-16 py-2"}),
    )
    # What document_attach will take: http(s) only, and no longer than
    # Document.external_url holds.
    document_url = forms.URLField(
        label=_("Link to your signed confirmation (optional)"),
        required=False,
        max_length=1024,
        validators=[URLValidator(schemes=["http", "https"])],
        widget=forms.URLInput(attrs={**INPUT, "placeholder": "https://"}),
    )

    def limit_to_scope(self, scope):
        if scope is not None and scope.approvals is not None:
            self.fields["approval"].queryset = scope.approvals.filter(status="requested")

    def rows(self):
        return [_pair("approval", "status"), "note", "document_url"]

    def is_available(self):
        return self.fields["approval"].queryset.exists()


# In the order they appear on the page: the order, the money, the goods moving,
# then what is on the shelf. An approver's answer first: an approver link
# covers nothing else.
PUBLIC_FORMS = {
    form.action: form
    for form in (
        RecordAnswerForm,
        ConfirmOrderForm,
        ConfirmPaymentForm,
        RecordShipmentForm,
        UpdateShipmentForm,
        RecordReceiptForm,
        RecordStockCountForm,
        RecordReleaseForm,
    )
}
