"""The "Record a test" screens: one form, shaped by the kind of test.

The readings asked for are the product's own specification figures (`Commodity.spec_requirements`),
so a programme whose product is judged on pH and a double-dilution average is asked for pH and three
trials, and a product with other figures is asked for those. The trials' average is worked out for
the person (`quality.judge.readings_from`); they may also type it, if that is what they read off.
"""

from datetime import date

from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Field, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain import records
from connect_labs.supply_chain.forms import DATE, INPUT, SEARCHABLE, SELECT, TEXTAREA
from connect_labs.supply_chain.models import Commodity, Item, SupplyPoint
from connect_labs.supply_chain.procurement.services.compliance import requirement_line
from connect_labs.supply_chain.quality.judge import figure_words

KIND_TITLES = {
    "batch": _("Batch test at the store"),
    "dispenser_sample": _("Dispenser sample"),
    "storage": _("Storage check"),
}
SOURCE_CHOICES = [
    ("we_recorded", _("We did the test ourselves")),
    ("partner_reported", _("A partner did it and told us the readings")),
]
YES_NO = [("", _("Not checked")), ("yes", _("Yes")), ("no", _("No"))]
TRIALS = 3
NUMBER = {**INPUT, "step": "any", "inputmode": "decimal"}


def _reading_name(field: str) -> str:
    return f"reading__{field}"


class QualityTestForm(forms.Form):
    supply_point = forms.ModelChoiceField(
        label=_("Where it was tested"),
        queryset=SupplyPoint.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
        empty_label=_("Select the store…"),
    )
    tested_on = forms.DateField(label=_("Date tested"), widget=forms.DateInput(attrs=DATE))
    tested_by = forms.CharField(label=_("Tested by"), max_length=255, widget=forms.TextInput(attrs=INPUT))
    note = forms.CharField(label=_("Notes"), required=False, widget=forms.Textarea(attrs={**TEXTAREA, "rows": 2}))
    source = forms.ChoiceField(
        label=_("Who did the test"), choices=SOURCE_CHOICES, initial="we_recorded", widget=forms.Select(attrs=SELECT)
    )

    def __init__(self, *args, access=None, kind="batch", tested_by="", **kwargs):
        super().__init__(*args, **kwargs)
        self.access = access
        self.kind = kind
        program_id = getattr(access, "program_id", None)
        scope = getattr(access, "scope_key", None)
        points = (
            SupplyPoint.objects.filter(program_id=program_id, status="active")
            .exclude(kind__in=("in_transit", "user_held", "supplier_site", "customs"))
            .order_by("name")
            if program_id
            else SupplyPoint.objects.none()
        )
        self.fields["supply_point"].queryset = points
        if points.count() == 1:
            self.fields["supply_point"].initial = points.first().pk
        self.fields["tested_on"].initial = date.today()
        self.fields["tested_by"].initial = tested_by
        layout = [
            Row(
                Column("supply_point"),
                Column("tested_on"),
                Column("tested_by"),
                css_class="grid md:grid-cols-3 gap-x-6",
            )
        ]

        if kind in ("batch", "dispenser_sample"):
            items = (
                Item.objects.filter(scope_key=scope).exclude(status="discontinued").select_related("commodity")
                if scope
                else Item.objects.none()
            )
            self.fields["item"] = forms.ModelChoiceField(
                label=_("Product"),
                queryset=items.order_by("name"),
                widget=forms.Select(attrs=SEARCHABLE),
                empty_label=_("Select the product…"),
            )
            self.fields["item"].label_from_instance = lambda item: item.name
            if items.count() == 1:
                self.fields["item"].initial = items.first().pk
            if kind == "batch":
                self.fields["batch"] = forms.CharField(
                    label=_("Batch"), max_length=64, widget=forms.TextInput(attrs=INPUT)
                )
                self.fields["sample_ref"] = forms.CharField(
                    label=_("Sample bottle"),
                    max_length=64,
                    required=False,
                    widget=forms.TextInput(attrs={**INPUT, "placeholder": _("the label on the bottle")}),
                )
                layout.append(
                    Row(Column("item"), Column("batch"), Column("sample_ref"), css_class="grid md:grid-cols-3 gap-x-6")
                )
            else:
                self.fields["dispenser"] = forms.CharField(
                    label=_("Dispenser or water point"), max_length=255, widget=forms.TextInput(attrs=INPUT)
                )
                self.fields["collected_by"] = forms.CharField(
                    label=_("Collected by"),
                    max_length=255,
                    required=False,
                    widget=forms.TextInput(attrs={**INPUT, "placeholder": _("the rider who brought it")}),
                )
                self.fields["collected_on"] = forms.DateField(
                    label=_("Date collected"), required=False, widget=forms.DateInput(attrs=DATE)
                )
                self.fields["batch"] = forms.CharField(
                    label=_("Batch in the dispenser"),
                    max_length=64,
                    required=False,
                    widget=forms.TextInput(attrs=INPUT),
                )
                self.fields["sample_ref"] = forms.CharField(
                    label=_("Sample bottle"), max_length=64, required=False, widget=forms.TextInput(attrs=INPUT)
                )
                layout.append(Row(Column("item"), Column("dispenser"), css_class="grid md:grid-cols-2 gap-x-6"))
                layout.append(
                    Row(
                        Column("collected_by"),
                        Column("collected_on"),
                        Column("batch"),
                        Column("sample_ref"),
                        css_class="grid md:grid-cols-4 gap-x-6",
                    )
                )
            self.reading_fields = self._add_readings(scope)
            layout.extend(self.reading_layout)
        else:
            for name in records.STORAGE_CHECKS:
                self.fields[f"check__{name}"] = forms.ChoiceField(
                    label=records.STORAGE_CHECK_WORDS[name],
                    choices=YES_NO,
                    required=False,
                    widget=forms.Select(attrs=SELECT),
                )
            names = [f"check__{name}" for name in records.STORAGE_CHECKS]
            layout.append(Row(*[Column(n) for n in names[:3]], css_class="grid md:grid-cols-3 gap-x-6"))
            layout.append(Row(*[Column(n) for n in names[3:]], css_class="grid md:grid-cols-3 gap-x-6"))
        layout.extend([Field("note"), Field("source")])
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(*layout)

    def _add_readings(self, scope) -> list[str]:
        """One field per figure the programme's products are judged on; trials stand in for the average."""
        requirements = []
        for spec in (
            Commodity.objects.filter(scope_key=scope).values_list("spec_requirements", flat=True) if scope else []
        ):
            requirements.extend(r for r in (spec or []) if r.get("field"))
        fields, wanted = [], {}
        for requirement in requirements:
            wanted.setdefault(requirement["field"], []).append(requirement_line(requirement))
        for field, lines in wanted.items():
            help_text = "; ".join(dict.fromkeys(lines))
            if field == records.TRIALS_AVERAGE_FIELD:
                for i in range(1, TRIALS + 1):
                    self.fields[f"trial_{i}"] = forms.DecimalField(
                        label=_("Trial %(n)s (mg/L)") % {"n": i},
                        required=False,
                        widget=forms.NumberInput(attrs=NUMBER),
                    )
                self.fields[_reading_name(field)] = forms.DecimalField(
                    label=_("Average, if you read it directly"),
                    required=False,
                    widget=forms.NumberInput(attrs=NUMBER),
                    help_text=_("Leave empty and the average of the trials is used. Must be: %(rule)s.")
                    % {"rule": help_text},
                )
            else:
                self.fields[_reading_name(field)] = forms.DecimalField(
                    label=figure_words(field),
                    required=False,
                    widget=forms.NumberInput(attrs=NUMBER),
                    help_text=_("Must be: %(rule)s.") % {"rule": help_text},
                )
            fields.append(field)
        trial_names = [f"trial_{i}" for i in range(1, TRIALS + 1) if f"trial_{i}" in self.fields]
        others = [_reading_name(f) for f in fields if f != records.TRIALS_AVERAGE_FIELD]
        self.reading_layout = [HTML('<h3 class="text-sm font-semibold text-gray-900 mt-2 mb-1">Readings</h3>')]
        if not fields:
            self.reading_layout.append(
                HTML(
                    '<p class="text-sm text-amber-800 mb-3">This programme\'s products have no specification to '
                    "test against yet. Add their requirements in the Catalogue, then record the test.</p>"
                )
            )
        if others:
            self.reading_layout.append(Row(*[Column(n) for n in others], css_class="grid md:grid-cols-3 gap-x-6"))
        if trial_names:
            self.reading_layout.append(
                Row(
                    *[Column(n) for n in trial_names],
                    Column(_reading_name(records.TRIALS_AVERAGE_FIELD)),
                    css_class="grid md:grid-cols-4 gap-x-6",
                )
            )
        return fields

    def clean(self):
        cleaned = super().clean()
        if self.kind in ("batch", "dispenser_sample"):
            measured = [cleaned.get(_reading_name(f)) for f in getattr(self, "reading_fields", [])] + [
                cleaned.get(f"trial_{i}") for i in range(1, TRIALS + 1)
            ]
            if all(value is None for value in measured):
                raise forms.ValidationError(_("Enter at least one reading."))
            tested_on, collected_on = cleaned.get("tested_on"), cleaned.get("collected_on")
            if tested_on and collected_on and collected_on > tested_on:
                self.add_error("collected_on", _("The sample is collected before it is tested."))
        else:
            if not any(cleaned.get(f"check__{name}") for name in records.STORAGE_CHECKS):
                raise forms.ValidationError(_("Answer at least one check."))
        tested_on = cleaned.get("tested_on")
        if tested_on and tested_on > date.today():
            self.add_error("tested_on", _("Record a test once it has been done."))
        return cleaned

    def payload(self) -> dict:
        c = self.cleaned_data
        data = {
            "kind": self.kind,
            "supply_point_id": c["supply_point"].pk,
            "tested_on": c["tested_on"].isoformat(),
            "tested_by": c["tested_by"].strip(),
            "source": c["source"],
        }
        if c.get("note"):
            data["note"] = c["note"]
        if self.kind in ("batch", "dispenser_sample"):
            data["item_id"] = c["item"].pk
            for name in ("batch", "sample_ref", "dispenser", "collected_by"):
                if c.get(name):
                    data[name] = c[name].strip()
            if c.get("collected_on"):
                data["collected_on"] = c["collected_on"].isoformat()
            readings = {
                field: str(c[_reading_name(field)])
                for field in self.reading_fields
                if c.get(_reading_name(field)) is not None
            }
            trials = [str(c[f"trial_{i}"]) for i in range(1, TRIALS + 1) if c.get(f"trial_{i}") is not None]
            if readings:
                data["readings"] = readings
            if trials:
                data["trials"] = trials
        else:
            data["storage"] = {
                name: c[f"check__{name}"] == "yes" for name in records.STORAGE_CHECKS if c.get(f"check__{name}")
            }
        return data


class QualityTestWithdrawForm(forms.Form):
    reason = forms.CharField(
        label=_("Why is it being withdrawn"),
        min_length=5,
        max_length=500,
        widget=forms.Textarea(
            attrs={**TEXTAREA, "rows": 2, "placeholder": _("e.g. wrong batch typed; recorded again as EA-2610")}
        ),
    )

    def __init__(self, *args, access=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(Field("reason"))

    def payload(self) -> dict:
        return {"reason": self.cleaned_data["reason"].strip()}
