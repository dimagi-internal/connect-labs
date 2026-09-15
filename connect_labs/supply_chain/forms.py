"""Write screens for the supply domain.

`ModelForm`, and never `ModelForm.save()`.

The models are local and real -- unlike every other labs app, this DB is the
system of record -- so a ModelForm derives labels, choices, `max_length`,
decimal places and required-ness from the field definitions rather than from a
second description of them. That is the half Django is good at and there is no
reason to hand-roll it.

But `.save()` is out, because `call_operation` is not ceremony. It does three
things a direct ORM write skips:

  1. `jsonschema.validate` against the operation's published contract, which
     the HTTP API and MCP are also held to;
  2. `stamp_provenance`, which derives `recorded_by_org` and `source` from the
     session -- the control that stops a caller attributing a record to
     somebody else. A row saved through a ModelForm would carry no provenance
     at all;
  3. the data-access layer, which supplies programme scoping
     (`_require_program`), resolves slugs and foreign keys, and defaults
     status.

So: Django builds and validates the form, `cleaned_data` becomes the payload,
and the operation does the writing. `to_payload` is the boundary.

**Money must cross that boundary as a string.** `operations.MONEY` is
deliberately string-only so a JSON number can never round a price silently,
and a model `DecimalField` hands us a `Decimal`. `str(Decimal("52.42"))` is
exactly `"52.42"`, so model validation and wire precision both hold -- but
only if nothing in between turns it into a float.

**Querysets are scoped, always.** A `ModelChoiceField` over `Supplier.objects`
would offer every supplier in the database, which is a cross-programme leak
wearing a dropdown. Every relation field here is narrowed to the caller's
scope in `__init__`.
"""

from datetime import date
from decimal import Decimal

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Field, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain.models import Outreach, Round, Supplier

# The house widget classes, as prod uses them and as the rest of labs does.
# `data-tomselect` is picked up by static/js/tomselect.js, which turns a plain
# select into a searchable one -- the same picker the tasks and opportunity
# screens use, so a supplier list that grows past a dozen stays usable.
INPUT = {"class": "base-input"}
SELECT = {"class": "base-dropdown"}
SEARCHABLE = {"class": "base-dropdown", "data-tomselect": "1"}
TEXTAREA = {"class": "base-input !h-auto min-h-24 py-2 resize-y", "rows": 3}
DATE = {"class": "base-input", "type": "date"}


def to_payload(cleaned: dict) -> dict:
    """`cleaned_data` in the shape the operations accept.

    Three conversions, each because the operation contract is stricter than a
    Python object graph:

      Decimal -> str   money and quantity are strings on the wire (see above)
      date    -> ISO   JSON has no date type
      model   -> pk    a schema names `supplier_id`, not a Supplier

    Empty strings and None are dropped rather than sent. An optional field
    nobody filled in has not been answered, and "" is a value several schemas
    would accept and store as a real empty string.
    """
    payload = {}
    for name, value in cleaned.items():
        if value is None or value == "":
            continue
        if isinstance(value, Decimal):
            payload[name] = str(value)
        elif isinstance(value, date):
            payload[name] = value.isoformat()
        elif hasattr(value, "pk"):
            payload[f"{name}_id" if not name.endswith("_id") else name] = value.pk
        else:
            payload[name] = value
    return payload


class ScopedForm(forms.ModelForm):
    """A ModelForm that knows whose data it may offer in its dropdowns."""

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        # The page shell draws the card and the buttons, so crispy renders the
        # fields and nothing else.
        self.helper.form_tag = False
        self.helper.disable_csrf = True

    def payload(self) -> dict:
        return to_payload(self.cleaned_data)


class RoundForm(ScopedForm):
    """A quote round's own details. Its commodity lines are a formset.

    `lines` and `delivery_point` are JSONFields and are excluded: rendered by
    a ModelForm they would be a textarea of raw JSON, which is a worse way to
    ask for two numbers and a place name than asking for them.
    """

    delivery_name = forms.CharField(
        label=_("Deliver to"),
        max_length=255,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. Central store")}),
        help_text=_("Suppliers will not quote without knowing where the goods go."),
    )
    delivery_city = forms.CharField(
        label=_("City"), max_length=128, required=False, widget=forms.TextInput(attrs=INPUT)
    )
    delivery_country = forms.CharField(
        label=_("Country"),
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. Nigeria")}),
    )

    class Meta:
        model = Round
        fields = [
            "label",
            "response_deadline",
            "reminder_interval_days",
            "shelf_life_months_minimum",
            "notes_to_supplier",
        ]
        widgets = {
            "label": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. Round 1 — RUTF, 500 cartons")}),
            "response_deadline": forms.DateInput(attrs=DATE),
            "reminder_interval_days": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "shelf_life_months_minimum": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "notes_to_supplier": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "label": _("What to call this round"),
            "response_deadline": _("Replies wanted by"),
            "reminder_interval_days": _("Chase every (days)"),
            "shelf_life_months_minimum": _("Minimum shelf life (months)"),
            "notes_to_supplier": _("Anything else to tell suppliers"),
        }
        help_texts = {
            "shelf_life_months_minimum": _("Sea freight and clearance routinely eat four months of it."),
            "reminder_interval_days": _("Leave empty and nobody is chased automatically."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            point = self.instance.delivery_point or {}
            self.fields["delivery_name"].initial = point.get("name", "")
            self.fields["delivery_city"].initial = point.get("city", "")
            self.fields["delivery_country"].initial = point.get("country_name") or point.get("country", "")
        self.helper.layout = Layout(
            Field("label"),
            Row(
                Column("response_deadline"), Column("reminder_interval_days"), css_class="grid md:grid-cols-2 gap-x-6"
            ),
            Row(
                Column("delivery_name"),
                Column("delivery_city"),
                Column("delivery_country"),
                css_class="grid md:grid-cols-3 gap-x-6",
            ),
            Field("shelf_life_months_minimum"),
            Field("notes_to_supplier"),
        )

    def payload(self) -> dict:
        data = to_payload({k: v for k, v in self.cleaned_data.items() if not k.startswith("delivery_")})
        point = {
            "name": self.cleaned_data.get("delivery_name", ""),
            "city": self.cleaned_data.get("delivery_city", ""),
            "country_name": self.cleaned_data.get("delivery_country", ""),
        }
        data["delivery_point"] = {k: v for k, v in point.items() if v}
        return data


class RoundLineForm(forms.Form):
    """One commodity a round is asking for. Rendered as a formset.

    A round can ask for several commodities and each needs its own quantity
    and unit, which is a repeating row -- the thing `formset_factory` exists
    for and the thing I was about to hand-roll in Alpine.
    """

    commodity_slug = forms.ChoiceField(label=_("Commodity"), widget=forms.Select(attrs=SEARCHABLE))
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

    def __init__(self, *args, commodities=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["commodity_slug"].choices = [("", "—")] + list(commodities)


RoundLineFormSet = forms.formset_factory(RoundLineForm, extra=1, min_num=1, validate_min=True, can_delete=True)


class OutreachForm(ScopedForm):
    """Record that a quote request went out to a supplier."""

    class Meta:
        model = Outreach
        fields = ["supplier", "channel", "sent_on", "notes"]
        widgets = {
            "supplier": forms.Select(attrs=SEARCHABLE),
            "channel": forms.Select(attrs=SELECT),
            "sent_on": forms.DateInput(attrs=DATE),
            "notes": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "supplier": _("Who was asked"),
            "channel": _("How"),
            "sent_on": _("Sent on"),
            "notes": _("Notes"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Scoped, not `Supplier.objects.all()`: an unscoped queryset offers
        # every supplier in the database and is a cross-programme leak.
        self.fields["supplier"].queryset = (
            Supplier.objects.filter(scope_key=self.access.scope_key).order_by("name")
            if self.access
            else Supplier.objects.none()
        )
        self.fields["supplier"].empty_label = _("Select a supplier…")
        self.fields["channel"].choices = [
            ("manual", _("By hand (email, call)")),
            ("ses", _("Sent by the system")),
            ("api", _("Via the API")),
            ("mcp", _("Via an agent")),
        ]
        self.fields["sent_on"].initial = date.today
        self.helper.layout = Layout(
            Field("supplier"),
            Row(Column("sent_on"), Column("channel"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("notes"),
        )


class OutreachReplyForm(ScopedForm):
    """What came back, if anything."""

    class Meta:
        model = Outreach
        fields = ["responded", "response_kind", "last_reminder_on", "notes"]
        widgets = {
            "responded": forms.CheckboxInput(attrs={"class": "simple-toggle"}),
            "response_kind": forms.Select(attrs=SELECT),
            "last_reminder_on": forms.DateInput(attrs=DATE),
            "notes": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "responded": _("They replied"),
            "response_kind": _("What the reply was"),
            "last_reminder_on": _("Last chased on"),
            "notes": _("Notes"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["response_kind"].required = False
        self.fields["response_kind"].choices = [
            ("", "—"),
            ("quote", _("A quote")),
            ("declined", _("Declined to quote")),
            ("needs_info", _("Asked us for more information")),
            ("no_reply", _("No reply")),
        ]
        self.helper.layout = Layout(
            Field("responded"),
            Row(Column("response_kind"), Column("last_reminder_on"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("notes"),
        )


class ReasonForm(forms.Form):
    """The reason a destructive operation requires.

    `quote_void` and `outreach_delete` both refuse without one, and that
    refusal is the point: a record removed with no account of why is worse
    than the record. Asking for it as an ordinary required field beats a
    confirmation dialogue nobody reads.
    """

    reason = forms.CharField(
        label=_("Why"),
        widget=forms.Textarea(attrs={**TEXTAREA, "placeholder": _("e.g. recorded against the wrong supplier")}),
        help_text=_("Kept in the write log, so the record of the mistake outlives the row."),
    )

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True

    def payload(self) -> dict:
        return to_payload(self.cleaned_data)
