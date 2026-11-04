"""Write screens for stock issues and the custodian of a storage point."""

from datetime import date

from crispy_forms.layout import Column, Field, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain import records
from connect_labs.supply_chain.forms import DATE, INPUT, SEARCHABLE, SELECT, TEXTAREA, set_choices, to_payload
from connect_labs.supply_chain.fulfilment.forms import ProvenancedForm
from connect_labs.supply_chain.models import Commodity, Custodian, Item, StockIssue, SupplyPoint
from connect_labs.supply_chain.stock.services import issues

KIND_CHOICES = [
    ("", _("Select what happened…")),
    ("missing", _("Missing — cannot be found")),
    ("taken", _("Taken — someone moved it")),
    ("damaged", _("Damaged")),
    ("expired", _("Expired")),
    ("other", _("Something else")),
]
SECURITY_CHOICES = [
    ("locked_room", _("Locked room")),
    ("locked_box", _("Locked box or cupboard")),
    ("separate_shelf", _("Separate shelf, kept apart from other stock")),
    ("facility_agreement", _("Agreement with the facility")),
    ("none", _("None in place")),
]


def storage_points(form, partner_org_id=None):
    """The places stock rests (not a worker's own holding, not the road), optionally one partner's own."""
    points = form.in_program(SupplyPoint).exclude(kind="in_transit").order_by("name")
    if partner_org_id is None:
        return points
    owner = issues.partner_by_point(form.access.program_id)
    return points.filter(pk__in=[pk for pk, org in owner.items() if org == partner_org_id])


class StockIssueReportForm(ProvenancedForm):
    """Stock that is missing, taken, damaged or expired."""

    class Meta:
        model = StockIssue
        fields = [
            "supply_point",
            "kind",
            "taken_by",
            "occurred_on",
            "commodity",
            "item",
            "batch",
            "quantity",
            "quantity_unit",
            "reported_by",
            "evidence",
        ]
        widgets = {
            "supply_point": forms.Select(attrs=SEARCHABLE),
            "kind": forms.Select(attrs=SELECT),
            "taken_by": forms.TextInput(attrs=INPUT),
            "occurred_on": forms.DateInput(attrs=DATE),
            "commodity": forms.Select(attrs=SEARCHABLE),
            "item": forms.Select(attrs=SEARCHABLE),
            "batch": forms.TextInput(attrs=INPUT),
            "quantity": forms.NumberInput(attrs={**INPUT, "step": "any", "min": "0"}),
            "quantity_unit": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
            "reported_by": forms.TextInput(attrs=INPUT),
            "evidence": forms.Textarea(attrs={**TEXTAREA, "rows": 3}),
        }
        labels = {
            "supply_point": _("Where"),
            "kind": _("What happened"),
            "taken_by": _("Taken by"),
            "occurred_on": _("Date it happened (or was found)"),
            "commodity": _("Product"),
            "item": _("Trade item"),
            "batch": _("Batch"),
            "quantity": _("How much"),
            "quantity_unit": _("Unit"),
            "reported_by": _("Reported by"),
            "evidence": _("Evidence"),
        }
        help_texts = {
            "taken_by": _("Only if known, and only for stock somebody moved. Leave it empty otherwise."),
            "quantity": _(
                "This comes off the place's stock now. If it is found again, resolving the issue puts it back."
            ),
            "evidence": _(
                "A photo or message link, or a few words on what shows it (for example, the WhatsApp message)."
            ),
            "reported_by": _("The person who told you or wrote it down."),
        }

    def __init__(self, *args, partner_org_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supply_point"].queryset = storage_points(self, partner_org_id)
        self.fields["commodity"].queryset = self.scoped(Commodity).order_by("name")
        self.fields["item"].queryset = self.scoped(Item).order_by("name")
        self.fields["item"].label_from_instance = lambda item: item.name
        self.fields["supply_point"].empty_label = _("Select a place…")
        self.fields["commodity"].empty_label = _("Select a product…")
        self.fields["item"].empty_label = _("Not recorded")
        for name in ("supply_point", "commodity", "occurred_on", "quantity", "quantity_unit"):
            self.fields[name].required = True
        self.fields["occurred_on"].initial = date.today()
        set_choices(self, "kind", KIND_CHOICES, required=True)
        self.helper.layout = Layout(
            Row(Column("supply_point"), Column("occurred_on"), css_class="grid md:grid-cols-2 gap-x-6"),
            Row(Column("kind"), Column("taken_by"), css_class="grid md:grid-cols-2 gap-x-6"),
            Row(Column("commodity"), Column("item"), Column("batch"), css_class="grid md:grid-cols-3 gap-x-6"),
            Row(Column("quantity"), Column("quantity_unit"), css_class="grid md:grid-cols-2 gap-x-6"),
            Row(Column("reported_by"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("evidence"),
            Field("source"),
        )

    def clean(self):
        cleaned = super().clean()
        quantity = cleaned.get("quantity")
        if quantity is not None and quantity <= 0:
            self.add_error("quantity", _("An issue is about some stock; enter more than zero."))
        if cleaned.get("kind") != "taken":
            cleaned["taken_by"] = ""
        return cleaned

    def payload(self) -> dict:
        data = to_payload(self.cleaned_data)
        commodity = self.cleaned_data.get("commodity")
        if commodity is not None:
            data["commodity_slug"] = commodity.slug
        data.pop("commodity_id", None)
        return data


class StockIssueFollowUpForm(ProvenancedForm):
    """What has been learned since: who took it, more evidence, and that it is being chased."""

    class Meta:
        model = StockIssue
        fields = ["status", "taken_by", "evidence", "note"]
        widgets = {
            "status": forms.Select(attrs=SELECT),
            "taken_by": forms.TextInput(attrs=INPUT),
            "evidence": forms.Textarea(attrs={**TEXTAREA, "rows": 3}),
            "note": forms.Textarea(attrs={**TEXTAREA, "rows": 3}),
        }
        labels = {
            "status": _("Where it stands"),
            "taken_by": _("Taken by"),
            "evidence": _("Evidence"),
            "note": _("Notes"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(
            self, "status", [("open", _("Open — not yet looked at")), ("following_up", _("Being followed up"))]
        )
        self.helper.layout = Layout(
            Field("status"), Field("taken_by"), Field("evidence"), Field("note"), Field("source")
        )


class StockIssueResolveForm(ProvenancedForm):
    """How it ended: recovered, replaced or written off."""

    quantity_recovered = forms.DecimalField(
        label=_("How much came back"),
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={**INPUT, "step": "any", "min": "0"}),
        help_text=_("For recovered or replaced. Leave empty if all of it did; whatever did not stays written off."),
    )

    class Meta:
        model = StockIssue
        fields = ["outcome", "resolved_on", "note"]
        widgets = {
            "outcome": forms.Select(attrs=SELECT),
            "resolved_on": forms.DateInput(attrs=DATE),
            "note": forms.Textarea(attrs={**TEXTAREA, "rows": 3}),
        }
        labels = {"outcome": _("How it ended"), "resolved_on": _("Date"), "note": _("Notes")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(
            self,
            "outcome",
            [
                ("", _("Select how it ended…")),
                ("recovered", _("Recovered — the stock was found or returned")),
                ("replaced", _("Replaced — new stock was provided")),
                ("written_off", _("Written off — it is gone")),
            ],
            required=True,
        )
        self.fields["resolved_on"].required = True
        self.fields["resolved_on"].initial = date.today()
        self.helper.layout = Layout(
            Row(Column("outcome"), Column("resolved_on"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("quantity_recovered"),
            Field("note"),
            Field("source"),
        )

    def payload(self) -> dict:
        data = to_payload(self.cleaned_data)
        data.pop("quantity_recovered", None)
        back = self.cleaned_data.get("quantity_recovered")
        if back is not None and self.cleaned_data.get("outcome") != "written_off":
            data["quantity_recovered"] = str(back)
        return data


class CustodianForm(ProvenancedForm):
    """Who is responsible for the stock at a storage point, and how it is kept safe."""

    security_measures = forms.MultipleChoiceField(
        label=_("Security in place"),
        required=False,
        choices=SECURITY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Custodian
        fields = ["supply_point", "name", "phone", "role", "starts_on", "note"]
        widgets = {
            "supply_point": forms.Select(attrs=SEARCHABLE),
            "name": forms.TextInput(attrs=INPUT),
            "phone": forms.TextInput(attrs=INPUT),
            "role": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. pharmacist in charge")}),
            "starts_on": forms.DateInput(attrs=DATE),
            "note": forms.Textarea(attrs={**TEXTAREA, "rows": 3}),
        }
        labels = {
            "supply_point": _("Storage point"),
            "name": _("Name"),
            "phone": _("Phone"),
            "role": _("Role"),
            "starts_on": _("Responsible from"),
            "note": _("Security details"),
        }
        help_texts = {
            "starts_on": _("The person before is recorded as ending on this day, so the history is kept."),
            "note": _("For example, who holds the key, or what the agreement with the facility says."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supply_point"].queryset = self.in_program(SupplyPoint).exclude(kind="in_transit").order_by("name")
        self.fields["supply_point"].empty_label = _("Select a place…")
        for name in ("supply_point", "name", "starts_on"):
            self.fields[name].required = True
        self.fields["starts_on"].initial = date.today()
        self.helper.layout = Layout(
            Row(Column("supply_point"), Column("starts_on"), css_class="grid md:grid-cols-2 gap-x-6"),
            Row(Column("name"), Column("phone"), Column("role"), css_class="grid md:grid-cols-3 gap-x-6"),
            Field("security_measures"),
            Field("note"),
            Field("source"),
        )

    def payload(self) -> dict:
        data = to_payload(self.cleaned_data)
        data["security_measures"] = [
            m for m in self.cleaned_data.get("security_measures") or [] if m in records.SECURITY_MEASURES
        ]
        return data
