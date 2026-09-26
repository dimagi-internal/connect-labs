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
deliberately string-only so a JSON number can never tender a price silently,
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
from django import forms
from django.utils.translation import gettext_lazy as _

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
        Quote.objects.filter(tender__program_id=program_id).values_list("as_quoted_currency", flat=True),
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
