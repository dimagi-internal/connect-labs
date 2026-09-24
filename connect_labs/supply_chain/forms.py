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
from crispy_forms.layout import Column, Field, Fieldset, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.models import AwardApproval, Commodity, Item, Outreach, Quote, Round, Supplier

# The house widget classes, as prod uses them and as the rest of labs does.
# `data-tomselect` is picked up by static/js/tomselect.js, which turns a plain
# select into a searchable one -- the same picker the tasks and opportunity
# screens use, so a supplier list that grows past a dozen stays usable.
INPUT = {"class": "base-input"}
# No `class` on a select. crispy_tailwind's select template emits its own
# `class` attribute BEFORE the widget's attrs, so passing one produces two
# `class` attributes on the tag -- invalid HTML, and the browser honours the
# first, which means the class here was silently dropped. An input is
# different: crispy MERGES `class` there, which is why `base-input` works.
SELECT: dict = {}
SEARCHABLE = {"data-tomselect": "1"}
TEXTAREA = {"class": "base-input !h-auto min-h-24 py-2 resize-y", "rows": 3}
DATE = {"class": "base-input", "type": "date"}
# `step="any"`: supply stores money to four decimal places, because a
# per-sachet price is routinely something like 0.3495 -- and a browser
# enforcing a two-decimal step refuses exactly that figure.
MONEY_INPUT = {**INPUT, "step": "any", "inputmode": "decimal"}


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


def set_choices(form, name, choices, required=None):
    """Give a select its options, whatever kind of field it came from.

    This exists because of a defect that shipped four times. A model
    `CharField` WITHOUT `choices=` becomes a `forms.CharField` on a ModelForm,
    and a `forms.CharField` has no `choices` — so

        set_choices(self, "channel", [...])

    silently set an attribute nothing reads, and the `forms.Select` from
    `Meta.widgets` then rendered **a dropdown with no options at all**. Nine
    fields across five forms were unusable that way, and every test passed:
    they asserted the field was on the page, which it was.

    Where the model DOES declare `choices=`, ModelForm builds a
    `TypedChoiceField` and the assignment works — which is why it looked
    fine in half the places and was broken in the other half.

    Replacing the field outright removes the distinction. `TestNoSelectIsEmpty`
    renders every write screen and fails on any select with no options, so this
    cannot come back quietly.
    """
    existing = form.fields[name]
    form.fields[name] = forms.ChoiceField(
        choices=choices,
        label=existing.label,
        help_text=existing.help_text,
        required=existing.required if required is None else required,
        initial=existing.initial,
        widget=existing.widget,
    )
    return form.fields[name]


# Offered in every currency picker, after the ones this programme already
# uses. ISO 4217. The currencies of the places supply programmes run, plus the
# ones suppliers and donors quote in.
COMMON_CURRENCIES = (
    "USD",
    "EUR",
    "GBP",
    "CHF",
    "NGN",
    "KES",
    "UGX",
    "TZS",
    "ETB",
    "GHS",
    "XOF",
    "XAF",
    "CDF",
    "MWK",
    "ZMW",
    "MZN",
    "RWF",
    "SLE",
    "LRD",
    "SSP",
    "SOS",
    "ZAR",
    "INR",
    "BDT",
    "PKR",
    "CNY",
)


def programme_currencies(program_id) -> list[str]:
    """Every currency this programme has already recorded a price or payment in."""
    from connect_labs.supply_chain.models import Charge, Contract, Invoice, Payment, Quote

    if not program_id:
        return []
    found = set()
    for queryset in (
        Contract.objects.filter(program_id=program_id).values_list("currency", flat=True),
        Invoice.objects.filter(contract__program_id=program_id).values_list("currency", flat=True),
        Payment.objects.filter(invoice__contract__program_id=program_id).values_list("currency", flat=True),
        Charge.objects.filter(shipment__contract__program_id=program_id).values_list("currency", flat=True),
        Quote.objects.filter(round__program_id=program_id).values_list("as_quoted_currency", flat=True),
    ):
        found.update(code.upper() for code in queryset.distinct() if code)
    return sorted(found)


def currency_select(form, name):
    """A currency as a pick-list rather than three letters typed free-hand.

    "usd", "US$" and "NGN " were all typed into the old text box. The list is
    the programme's own currencies first, then the common ones; the current
    value is kept even if it is on neither list, so an edit never silently
    changes a record's currency.
    """
    program_id = getattr(getattr(form, "access", None), "program_id", None)
    used = programme_currencies(program_id)
    common = [code for code in COMMON_CURRENCIES if code not in used]
    current = (form.initial.get(name) or getattr(form.instance, name, "") or "").upper()
    choices = []
    if used:
        choices.append((_("Used in this programme"), [(code, code) for code in used]))
    choices.append((_("Other currencies"), [(code, code) for code in common]))
    if current and current not in used and current not in COMMON_CURRENCIES:
        choices.insert(0, (current, current))
    existing = form.fields[name]
    form.fields[name] = CurrencyField(
        choices=choices,
        label=existing.label,
        help_text=existing.help_text,
        required=existing.required,
        initial=existing.initial,
        widget=forms.Select(attrs=SEARCHABLE),
    )
    return form.fields[name]


class CurrencyField(forms.ChoiceField):
    """A currency choice that reads "usd" as "USD": an API-shaped caller, or a
    browser without the picker, still names a currency the list holds."""

    def to_python(self, value):
        return super().to_python(value).strip().upper()


def _plain_decimal(value: Decimal) -> Decimal:
    """The same number without trailing zeros, and never in exponent form ("3E+4")."""
    normal = value.normalize()
    return normal.quantize(Decimal(1)) if normal == normal.to_integral_value() else normal


class ScopedForm(forms.ModelForm):
    """A ModelForm that knows whose data it may offer in its dropdowns."""

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        # A stored decimal is shown as a person wrote it: "30000", not
        # "30000.0000" -- the column's scale printed four places on every
        # quantity and price an edit form opened with (the CHC render).
        for name, field in self.fields.items():
            value = self.initial.get(name)
            if isinstance(field, forms.DecimalField) and isinstance(value, Decimal) and value.is_finite():
                self.initial[name] = _plain_decimal(value)
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
        set_choices(self, "commodity_slug", [("", "—")] + list(commodities))


# `extra=0`, not `extra=1`. A formset renders `max(initial, min_num) + extra`
# rows, so min_num=1 with extra=1 opened a new round on TWO blank commodity
# rows -- one required, one not, and no way to tell which from looking. One
# row and an "add another" button is the same capability, said once.
RoundLineFormSet = forms.formset_factory(RoundLineForm, extra=0, min_num=1, validate_min=True, can_delete=True)


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
        set_choices(
            self,
            "channel",
            [
                ("manual", _("By hand (email, call)")),
                ("ses", _("Sent by the system")),
                ("api", _("Via the API")),
                ("mcp", _("Via an agent")),
            ],
        )
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
        set_choices(
            self,
            "response_kind",
            [
                ("", "—"),
                ("quote", _("A quote")),
                ("declined", _("Declined to quote")),
                ("needs_info", _("Asked us for more information")),
                ("no_reply", _("No reply")),
            ],
        )
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


# ---- approvals ---------------------------------------------------------


def _rests_on_field(form):
    """An optional picker of this programme's documents, registrations first.

    What a regulatory approval rests on is usually a product registration
    already on file; offering every document the programme holds, with the
    registrations at the top, lets the other cases through without making the
    common one hunt.
    """
    from django.db.models import Case, IntegerField, Value, When

    from connect_labs.supply_chain.models import Document
    from connect_labs.supply_chain.templatetags.supply_chain_extras import words

    field = form.fields["rests_on_document"]
    field.required = False
    field.queryset = (
        Document.objects.filter(program_id=form.access.program_id)
        .annotate(
            first=Case(When(kind="product_registration", then=Value(0)), default=Value(1), output_field=IntegerField())
        )
        .order_by("first", "kind", "title", "pk")
        if form.access is not None and form.access.program_id
        else Document.objects.none()
    )
    field.empty_label = _("None — it rests on nothing on file")
    field.label_from_instance = lambda document: " — ".join(
        part for part in (words(document.kind).capitalize(), document.title or document.filename) if part
    )


class ApprovalRequestForm(ScopedForm):
    """Ask a third party to agree to an award before it becomes an order."""

    class Meta:
        model = AwardApproval
        fields = ["approver_org", "role", "requested_on", "note", "rests_on_document"]
        widgets = {
            "rests_on_document": forms.Select(attrs=SEARCHABLE),
            "approver_org": forms.Select(attrs=SEARCHABLE),
            "role": forms.Select(attrs=SELECT),
            "requested_on": forms.DateInput(attrs=DATE),
            "note": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "approver_org": _("Who has to agree"),
            "role": _("As what"),
            "requested_on": _("Asked on"),
            "note": _("What was asked"),
            "rests_on_document": _("Rests on"),
        }
        help_texts = {
            "rests_on_document": _(
                "A document already on file it is granted against — for a regulatory approval, the "
                "product registration. The approver's own letter is attached to the approval itself."
            ),
            "approver_org": _("Not the person deciding the award — somebody whose agreement it needs."),
            "role": _("Technical: confirms the product. Funder: approves the use of funds. Regulatory: a licence."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Organisations are labs-wide, so this picker is deliberately unscoped.
        self.fields["approver_org"].queryset = LabsOrg.objects.order_by("name")
        self.fields["approver_org"].empty_label = _("Select an organisation…")
        self.fields["requested_on"].initial = date.today()
        set_choices(
            self,
            "role",
            [("technical", _("Technical")), ("funder", _("Funder")), ("regulatory", _("Regulatory"))],
        )
        self.helper.layout = Layout(
            Row(
                Column("approver_org"), Column("role"), Column("requested_on"), css_class="grid md:grid-cols-3 gap-x-6"
            ),
            Field("note"),
            Field("rests_on_document"),
        )
        _rests_on_field(self)


class ApprovalDecisionForm(ScopedForm):
    """The approver's answer. Final on its row: a reversal is a new request."""

    class Meta:
        model = AwardApproval
        fields = ["status", "decided_on", "note", "rests_on_document"]
        widgets = {
            "rests_on_document": forms.Select(attrs=SEARCHABLE),
            "status": forms.Select(attrs=SELECT),
            "decided_on": forms.DateInput(attrs=DATE),
            "note": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "status": _("Their answer"),
            "decided_on": _("Answered on"),
            "note": _("Note"),
            "rests_on_document": _("Rests on"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(self, "status", [("approved", _("Approved")), ("declined", _("Declined"))])
        self.fields["status"].initial = "approved"
        self.fields["decided_on"].initial = date.today()
        self.fields["note"].required = False
        self.helper.layout = Layout(
            Row(Column("status"), Column("decided_on"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("note"),
            Field("rests_on_document"),
        )
        _rests_on_field(self)
        if self.instance and self.instance.rests_on_document_id:
            self.fields["rests_on_document"].initial = self.instance.rests_on_document_id


# ---- quotes ------------------------------------------------------------


class QuoteForm(ScopedForm):
    """A quote, as the supplier stated it.

    The last screen in this domain to be built by hand, and the most used. It
    was a 281-line template, a set of integer field names to coerce, and a
    POST handler that rebuilt the page from `request.POST` on a rejection so
    the typist did not lose their work. Django does every one of those things:
    a bound form re-renders with what was typed, a `ModelChoiceField` coerces
    and validates an id, and a `DecimalField` reports a bad number on the field
    it came from rather than as a sentence about a key name.

    `QuoteCorrectionForm` inherits the whole field set, so a quote's figures
    are declared once. What a correction may not change, it removes.

    **Figures are recorded AS STATED, not normalised.** `as_quoted_unit` says
    what the price is per, and the comparison converts. A screen that
    normalised on entry would be throwing away the only record of what the
    supplier actually wrote.
    """

    class Meta:
        model = Quote
        fields = [
            "round",
            "supplier",
            "commodity",
            "item",
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
            "round": forms.Select(attrs=SEARCHABLE),
            "supplier": forms.Select(attrs=SEARCHABLE),
            "commodity": forms.Select(attrs=SEARCHABLE),
            "item": forms.Select(attrs=SEARCHABLE),
            "as_quoted_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "as_quoted_unit": forms.Select(attrs=SELECT),
            "as_quoted_currency": forms.TextInput(attrs={**INPUT, "placeholder": "USD", "maxlength": 3}),
            "quantity_basis": forms.NumberInput(attrs={**INPUT, "step": "any"}),
            "quantity_basis_unit": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
            "pack_spec_source": forms.Select(attrs=SELECT),
            "base_per_pack_stated": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "base_unit_grams_stated": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "freight_basis": forms.Select(attrs=SELECT),
            "freight_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "duties_basis": forms.Select(attrs=SELECT),
            "duties_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "shelf_life_months_stated": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "lead_time_days": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "incoterm": forms.TextInput(attrs={**INPUT, "placeholder": "CIF"}),
            "received_on": forms.DateInput(attrs=DATE),
        }
        labels = {
            "round": _("Against which round"),
            "supplier": _("Who quoted"),
            "commodity": _("For what"),
            "item": _("Their trade item"),
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
            "as_quoted_amount": _("As the supplier wrote it. Converting happens in the comparison, not here."),
            "as_quoted_unit": _("Without this a price cannot be compared with anything."),
            "item": _("Optional, and what fixes the pack size — two suppliers' cartons hold different numbers."),
            "pack_spec_source": _(
                "A pack size we assumed and one the supplier stated are different evidence, so a "
                "comparison that mixes them says which is which."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["round"].queryset = self.rounds()
        self.fields["supplier"].queryset = self.scoped_to_programme(Supplier)
        self.fields["commodity"].queryset = self.scoped_to_programme(Commodity)
        self.fields["item"].queryset = self.scoped_to_programme(Item)
        self.fields["round"].empty_label = _("Select a round\u2026")
        self.fields["supplier"].empty_label = _("Select a supplier\u2026")
        self.fields["commodity"].empty_label = _("Select a product\u2026")
        self.fields["item"].empty_label = _("Not stated")

        basis = [("not_specified", _("Not specified")), ("included", _("Included")), ("excluded", _("Excluded"))]
        set_choices(self, "freight_basis", basis)
        set_choices(self, "duties_basis", basis)
        set_choices(
            self,
            "as_quoted_unit",
            [
                ("", "\u2014"),
                ("per_base_unit", _("Per unit (sachet, tablet)")),
                ("per_pack", _("Per pack (carton)")),
                ("per_lot_total", _("Total for the lot")),
                ("per_metric_tonne", _("Per metric tonne")),
            ],
        )
        set_choices(
            self,
            "pack_spec_source",
            [
                ("not_stated", _("They did not say")),
                ("stated_on_quote", _("Stated on the quote")),
                ("trade_item_confirmed", _("Confirmed against the trade item")),
            ],
        )
        currency_select(self, "as_quoted_currency")
        self.helper.layout = self.build_layout()

    # ---- the two halves a correction reuses ----------------------------

    def rounds(self):
        program_id = getattr(self.access, "program_id", None) if self.access else None
        return Round.objects.filter(program_id=program_id).order_by("-id") if program_id else Round.objects.none()

    def scoped_to_programme(self, model):
        scope = getattr(self.access, "scope_key", None) if self.access else None
        return model.objects.filter(scope_key=scope).order_by("name") if scope else model.objects.none()

    def figures_layout(self):
        """Everything except who quoted, which a correction does not ask again."""
        return (
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
                css_class="pt-2",
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
        )

    def build_layout(self):
        return Layout(
            Fieldset(
                str(_("Whose quote this is")),
                Row(Column("round"), Column("supplier"), css_class="grid md:grid-cols-2 gap-x-6"),
                Row(Column("commodity"), Column("item"), css_class="grid md:grid-cols-2 gap-x-6"),
                css_class="pt-1",
            ),
            *self.figures_layout(),
        )

    def clean_as_quoted_currency(self):
        return (self.cleaned_data.get("as_quoted_currency") or "").strip().upper()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("as_quoted_amount") is not None and not cleaned.get("as_quoted_unit"):
            self.add_error("as_quoted_unit", _("A price has to say what it is per, or it compares with nothing."))
        item, commodity = cleaned.get("item"), cleaned.get("commodity")
        if item is not None and commodity is not None and item.commodity_id != commodity.pk:
            self.add_error(
                "item", _("That trade item is not a version of \u201c%(name)s\u201d.") % {"name": commodity.name}
            )
        return cleaned

    def payload(self) -> dict:
        data = to_payload(self.cleaned_data)
        # The schema names the product by slug and the round and supplier by
        # their own ids, which is what `to_payload` already produces for the
        # relations. Only the commodity needs saying differently.
        commodity = self.cleaned_data.get("commodity")
        if commodity is not None:
            data["commodity_slug"] = commodity.slug
        data.pop("commodity_id", None)
        return data


class QuoteCorrectionForm(QuoteForm):
    """A corrected version of a quote, superseding the one it came from.

    Inherits the field set rather than restating it, so the two screens can
    never drift into disagreeing about what a quote records.

    Not an edit. The original stays readable so a comparison run last month
    still reproduces the numbers it showed then \u2014 the whole reason quotes carry
    a version chain rather than being mutable rows.
    """

    reason = forms.CharField(
        label=_("What was wrong"),
        widget=forms.Textarea(
            attrs={**TEXTAREA, "placeholder": _("e.g. transcribed the per-carton price as per-sachet")}
        ),
        help_text=_("Kept on the new version. A chain of corrections is only worth having if each link says why."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Who quoted, against which round, for what: not a correction. Changing
        # any of them makes a different quote, not a corrected one, and leaving
        # them editable invites exactly that.
        for name in ("round", "supplier", "commodity", "item"):
            del self.fields[name]
        self.helper.layout = self.build_layout()

    def build_layout(self):
        return Layout(*self.figures_layout(), Field("reason"))

    def payload(self) -> dict:
        # `reason` is a top-level argument of `quote_correct`, describing the
        # correction rather than the offer, so the view lifts it out. Keeping
        # the split here would mean the form knowing the operation's shape.
        return to_payload({k: v for k, v in self.cleaned_data.items() if k != "reason"})
