"""The marketplace's forms, in a supplier's words rather than the program team's.

The bid form asks for exactly what `quote_record` stores, and nothing is
computed on the way in: a blank is "not stated", never zero. The comparison
asks for what is missing -- and the supplier sees those questions on its own
bids page, where answering is revising the bid.
"""

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain.forms import COMMON_CURRENCIES, DATE, INPUT, MONEY_INPUT, TEXTAREA
from connect_labs.supply_chain.models import SupplierOffering, SupplierProfile

# The supply write screens style a select through crispy; these pages render
# fields by hand, so the select carries the input class itself -- without it a
# select renders as the browser's bare default beside styled inputs.
PICK = {"class": "base-input"}

SUPPLIER_TYPES = [
    ("manufacturer", _("Manufacturer — we make it")),
    ("distributor", _("Distributor — we hold and deliver it")),
    ("trader", _("Trader — we buy and sell it on")),
]

BASIS = [
    ("not_specified", _("Not stated")),
    ("included", _("Included in the price")),
    ("excluded", _("Not included — charged separately")),
]

PRICE_PER = [
    ("per_pack", _("Per pack (carton, box)")),
    ("per_base_unit", _("Per unit (sachet, tablet, bottle)")),
    ("per_lot_total", _("Total for the whole quantity")),
    ("per_metric_tonne", _("Per metric tonne")),
]


def _list(text):
    return [part.strip() for part in (text or "").split(",") if part.strip()]


class RegisterForm(forms.Form):
    name = forms.CharField(
        label=_("Organisation name"),
        max_length=300,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("As it is registered")}),
    )
    country = forms.CharField(
        label=_("Country"),
        max_length=2,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "NG", "maxlength": 2}),
        help_text=_("Two letters, ISO 3166 — NG, KE, FR."),
    )
    type = forms.ChoiceField(label=_("What you are"), choices=SUPPLIER_TYPES, widget=forms.Select(attrs=PICK))
    city = forms.CharField(label=_("City"), max_length=128, required=False, widget=forms.TextInput(attrs=INPUT))
    website = forms.URLField(label=_("Website"), required=False, widget=forms.URLInput(attrs=INPUT))
    description = forms.CharField(
        label=_("What you do"),
        required=False,
        widget=forms.Textarea(attrs=TEXTAREA),
        help_text=_("A few sentences a buyer reads before your bid."),
    )
    contact_name = forms.CharField(label=_("Contact name"), max_length=255, widget=forms.TextInput(attrs=INPUT))
    contact_email = forms.EmailField(label=_("Contact email"), widget=forms.EmailInput(attrs=INPUT))
    contact_phone = forms.CharField(
        label=_("Contact phone"), max_length=64, required=False, widget=forms.TextInput(attrs=INPUT)
    )

    def clean_country(self):
        return (self.cleaned_data.get("country") or "").strip().upper()

    def clean_name(self):
        """Refuse an organisation already on file.

        Two rows for one organisation is the duplication `LabsOrg` exists to
        end, and a self-registration is the cheapest way to create one. The
        way into an organisation already on file is an invitation from
        someone who can vouch for you, not a second registration.
        """
        from connect_labs.marketplace.identity import taken_by

        name = (self.cleaned_data.get("name") or "").strip()
        if taken_by(name) is not None:
            raise forms.ValidationError(
                _(
                    "“%(name)s” is already on file. Ask someone there, or the buyer you work with, "
                    "to send you an invitation to join it."
                )
                % {"name": name}
            )
        return name

    def contact(self) -> dict:
        data = self.cleaned_data
        return {
            k: v
            for k, v in {
                "name": data.get("contact_name"),
                "email": data.get("contact_email"),
                "phone": data.get("contact_phone"),
            }.items()
            if v
        }


class ProfileForm(forms.ModelForm):
    country = forms.CharField(
        label=_("Country"),
        max_length=2,
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "maxlength": 2}),
    )

    class Meta:
        model = SupplierProfile
        fields = ["type", "city", "website", "description"]
        widgets = {
            "type": forms.Select(attrs=PICK, choices=SUPPLIER_TYPES),
            "city": forms.TextInput(attrs=INPUT),
            "website": forms.URLInput(attrs=INPUT),
            "description": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "type": _("What you are"),
            "city": _("City"),
            "website": _("Website"),
            "description": _("What you do"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["type"] = forms.ChoiceField(
            label=_("What you are"), choices=SUPPLIER_TYPES, widget=forms.Select(attrs=PICK)
        )
        if self.instance and self.instance.pk:
            self.initial.setdefault("country", self.instance.org.country)

    def clean_country(self):
        return (self.cleaned_data.get("country") or "").strip().upper()


class OfferingForm(forms.ModelForm):
    countries_served_text = forms.CharField(
        label=_("Countries you deliver to"),
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "NG, NE, TD"}),
    )
    certifications_text = forms.CharField(
        label=_("Certifications"),
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. WHO PQ, NAFDAC, ISO 22000")}),
    )

    class Meta:
        model = SupplierOffering
        fields = [
            "category",
            "product_name",
            "unicef_material_number",
            "gtin",
            "pack_description",
            "base_per_pack",
            "typical_lead_time_days",
            "minimum_order",
        ]
        widgets = {
            "category": forms.Select(attrs=PICK),
            "product_name": forms.TextInput(attrs=INPUT),
            "unicef_material_number": forms.TextInput(attrs={**INPUT, "placeholder": "S0000240"}),
            "gtin": forms.TextInput(attrs={**INPUT, "maxlength": 14}),
            "pack_description": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton of 150 sachets")}),
            "base_per_pack": forms.NumberInput(attrs={**INPUT, "min": 1}),
            "typical_lead_time_days": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "minimum_order": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. 500 cartons")}),
        }
        labels = {
            "category": _("Kind of product"),
            "product_name": _("Product"),
            "unicef_material_number": _("UNICEF material number"),
            "gtin": _("GTIN (barcode)"),
            "pack_description": _("Pack"),
            "base_per_pack": _("Units per pack"),
            "typical_lead_time_days": _("Usual lead time (days)"),
            "minimum_order": _("Minimum order"),
        }
        help_texts = {
            "unicef_material_number": _("If you have one, buyers' products are matched to you exactly."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial["countries_served_text"] = ", ".join(self.instance.countries_served or [])
            self.initial["certifications_text"] = ", ".join(self.instance.certifications or [])

    def save(self, commit=True):
        offering = super().save(commit=False)
        offering.countries_served = [c.upper() for c in _list(self.cleaned_data.get("countries_served_text"))]
        offering.certifications = _list(self.cleaned_data.get("certifications_text"))
        if commit:
            offering.save()
        return offering


class BidForm(forms.Form):
    """One quote on one line of a round, as the supplier states it."""

    as_quoted_amount = forms.DecimalField(
        label=_("Price"), min_value=0, max_digits=18, decimal_places=4, widget=forms.NumberInput(attrs=MONEY_INPUT)
    )
    as_quoted_unit = forms.ChoiceField(label=_("The price is"), choices=PRICE_PER, widget=forms.Select(attrs=PICK))
    as_quoted_currency = forms.ChoiceField(
        label=_("Currency"), choices=[(c, c) for c in COMMON_CURRENCIES], widget=forms.Select(attrs=PICK)
    )
    quantity_basis = forms.DecimalField(
        label=_("For a quantity of"),
        required=False,
        min_value=0,
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs={**INPUT, "step": "any"}),
        help_text=_("How much the price covers. Leave blank if it is a unit price."),
    )
    quantity_basis_unit = forms.CharField(
        label=_("Unit"),
        required=False,
        max_length=32,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "carton"}),
    )
    base_per_pack_stated = forms.IntegerField(
        label=_("Units in one pack"), required=False, min_value=1, widget=forms.NumberInput(attrs=INPUT)
    )
    base_unit_grams_stated = forms.IntegerField(
        label=_("Grams in one unit"), required=False, min_value=1, widget=forms.NumberInput(attrs=INPUT)
    )
    freight_basis = forms.ChoiceField(label=_("Freight"), choices=BASIS, widget=forms.Select(attrs=PICK))
    freight_amount = forms.DecimalField(
        label=_("Freight charge"),
        required=False,
        min_value=0,
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs=MONEY_INPUT),
    )
    duties_basis = forms.ChoiceField(label=_("Import duties"), choices=BASIS, widget=forms.Select(attrs=PICK))
    duties_amount = forms.DecimalField(
        label=_("Duties charge"),
        required=False,
        min_value=0,
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs=MONEY_INPUT),
    )
    shelf_life_months_stated = forms.IntegerField(
        label=_("Shelf life on delivery (months)"), required=False, min_value=0, widget=forms.NumberInput(attrs=INPUT)
    )
    lead_time_days = forms.IntegerField(
        label=_("Lead time (days)"), required=False, min_value=0, widget=forms.NumberInput(attrs=INPUT)
    )
    moq = forms.DecimalField(
        label=_("Minimum order"),
        required=False,
        min_value=0,
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs={**INPUT, "step": "any"}),
    )
    moq_unit = forms.CharField(label=_("Unit"), required=False, max_length=32, widget=forms.TextInput(attrs=INPUT))
    validity_until = forms.DateField(label=_("Price valid until"), required=False, widget=forms.DateInput(attrs=DATE))
    incoterm = forms.CharField(
        label=_("Incoterm"),
        required=False,
        max_length=16,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "DAP"}),
    )
    notes = forms.CharField(label=_("Anything else"), required=False, widget=forms.Textarea(attrs=TEXTAREA))

    def __init__(self, *args, requirements=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.requirement_fields = []
        for requirement in requirements or []:
            key = requirement.get("field")
            if not key:
                continue
            from connect_labs.supply_chain.procurement.services.compliance import requirement_label

            name = f"spec__{key}"
            unit = requirement.get("unit") or ""
            self.fields[name] = forms.DecimalField(
                label=requirement_label(key, unit) + (f" ({unit})" if unit else ""),
                required=False,
                widget=forms.NumberInput(attrs={**INPUT, "step": "any"}),
            )
            self.requirement_fields.append(name)

    def clean(self):
        cleaned = super().clean()
        for basis, amount in (("freight_basis", "freight_amount"), ("duties_basis", "duties_amount")):
            if cleaned.get(basis) != "excluded" and cleaned.get(amount) is not None:
                cleaned[amount] = None
        return cleaned

    def payload(self, *, replacing=False) -> dict:
        """The quote as `quote_record` takes it. Blanks are "not stated".

        For a new bid a blank is simply left out. For a revision (`replacing`)
        every field is sent, a blank as null (or empty text): a correction
        merges onto the previous version, so a field left out would carry the
        old figure forward -- the supplier who cleared its freight charge would
        find it still there.
        """
        data = self.cleaned_data
        payload = {}
        for name, value in data.items():
            if name.startswith("spec__"):
                continue
            if value in (None, ""):
                if replacing:
                    payload[name] = "" if isinstance(self.fields[name], forms.CharField) else None
                continue
            if isinstance(value, Decimal):
                payload[name] = format(value, "f")
            elif hasattr(value, "isoformat"):
                payload[name] = value.isoformat()
            else:
                payload[name] = value
        payload["pack_spec_source"] = "stated_on_quote" if payload.get("base_per_pack_stated") else "not_stated"
        stated = {
            name[len("spec__") :]: format(data[name], "f")
            for name in self.requirement_fields
            if data.get(name) is not None
        }
        if stated or replacing:
            payload["stated_spec"] = stated
        return payload

    @classmethod
    def initial_from(cls, quote) -> dict:
        """What to show when revising: the live quote as it stands."""
        initial = {
            name: getattr(quote, name)
            for name in cls.base_fields
            if hasattr(quote, name) and getattr(quote, name) not in (None, "")
        }
        for key, value in (quote.stated_spec or {}).items():
            initial[f"spec__{key}"] = value
        return initial


class InviteForm(forms.Form):
    email = forms.EmailField(
        label=_("Their email"),
        required=False,
        widget=forms.EmailInput(attrs=INPUT),
        help_text=_("Only to remind you who it was for. The link itself is what lets them in."),
    )
