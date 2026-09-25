"""Write screens for the reference tier: the catalogue and the suppliers.

Separate from `forms.py` (the sourcing lifecycle) for the reason the tiers are
separate everywhere else in this domain: reference data is scoped to the
programme and reused across every tender, while a tender belongs to one moment
of buying. The two change for different reasons and at different rates.

Everything here follows `forms.py`'s contract and inherits its boundary:
`ModelForm` derives the fields, `to_payload` converts them, and
`call_operation` does the writing. Read that module's docstring first -- the
reasoning about `.save()`, money-as-string and scoped querysets applies
unchanged.

**Two things this tier has that sourcing does not.**

`slug` and `sku` are natural keys, and both operations are *upserts* keyed on
them. So a slug typed a second time silently edits the first row rather than
creating a second. The create screens therefore refuse a key already in use,
with a message naming what it would have overwritten, and the edit screens
render it read-only -- changing a slug through an upsert does not rename
anything, it forks a second row and leaves the original behind.

A commodity's `course_definition` is a JSONField and is the thing the whole
per-child cost figure hangs off. Rendered by a bare ModelForm it is a textarea
of raw JSON; asked for as three numbers it is answerable. `CommodityForm`
excludes it and reassembles it, the same way `TenderForm` handles
`delivery_point`.
"""

from decimal import Decimal, InvalidOperation

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Field, Fieldset, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain.forms import INPUT, SEARCHABLE, SELECT, TEXTAREA, ScopedForm, set_choices, to_payload
from connect_labs.supply_chain.models import Commodity, Item, Supplier

__all__ = [
    "CommodityForm",
    "ComponentLineFormSet",
    "ItemForm",
    "SupplierForm",
]


class KeyedUpsertForm(ScopedForm):
    """A form whose operation is an upsert keyed on one natural-key field.

    The key is `key_field`. On a create screen it is asked for and must be
    unused; on an edit screen it is shown and cannot be changed, because an
    upsert under a new key does not rename a row -- it writes a second one and
    leaves the first in the catalogue, unreferenced and identical.
    """

    key_field = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields[self.key_field]
        if self.instance and self.instance.pk:
            field.disabled = True
            field.help_text = _("Fixed once set — a new key would create a second row, not rename this one.")

    def clean(self):
        cleaned = super().clean()
        key = cleaned.get(self.key_field)
        if key and not (self.instance and self.instance.pk):
            model = self._meta.model
            clash = model.objects.filter(scope_key=self.access.scope_key, **{self.key_field: key}).first()
            if clash is not None:
                # Not a unique-constraint message: the operation is an upsert,
                # so this would not have raised. It would have quietly rewritten
                # the row named here.
                self.add_error(
                    self.key_field,
                    _("Already used by “%(name)s”. Saving would overwrite it — edit that instead.") % {"name": clash},
                )
        return cleaned


class CommodityForm(KeyedUpsertForm):
    """A product: what it must be, not whose it is.

    `spec_requirements` stays out. It is a list of free-form clauses whose
    shape the compliance check reads, and a textarea of JSON is a worse way to
    edit it than the API. Leaving it alone here means an existing product's
    requirements survive an edit through this screen untouched, which is the
    behaviour to want until there is a real editor for them.
    """

    key_field = "slug"

    base_units_per_day = forms.DecimalField(
        label=_("Units per day"),
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=3,
        widget=forms.NumberInput(attrs={**INPUT, "step": "any", "placeholder": "2"}),
        help_text=_("Sachets, tablets or doses a child gets in a day."),
    )
    days_per_course = forms.IntegerField(
        label=_("Days in a course"),
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={**INPUT, "min": 0, "placeholder": "56"}),
    )
    course_source = forms.CharField(
        label=_("Where that came from"),
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. WHO 2013 CMAM guideline")}),
        help_text=_("A course figure with no source is a guess wearing a number."),
    )

    class Meta:
        model = Commodity
        fields = [
            "slug",
            "name",
            "category",
            "base_unit",
            "pack_unit",
            "base_per_pack",
            "base_unit_grams",
            "shelf_life_months_minimum",
            "spec_reference",
            "unicef_material_number",
        ]
        widgets = {
            "slug": forms.TextInput(attrs={**INPUT, "placeholder": "rutf"}),
            "name": forms.TextInput(attrs={**INPUT, "placeholder": _("Ready-to-use therapeutic food")}),
            "category": forms.Select(attrs=SELECT),
            "base_unit": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. sachet")}),
            "pack_unit": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
            "base_per_pack": forms.NumberInput(attrs={**INPUT, "min": 0, "placeholder": "150"}),
            "base_unit_grams": forms.NumberInput(attrs={**INPUT, "min": 0, "placeholder": "92"}),
            "shelf_life_months_minimum": forms.NumberInput(attrs={**INPUT, "min": 0, "placeholder": "24"}),
            "spec_reference": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. UNICEF S0000240")}),
            "unicef_material_number": forms.TextInput(attrs=INPUT),
        }
        labels = {
            "slug": _("Short key"),
            "name": _("Name"),
            "category": _("Category"),
            "base_unit": _("Smallest unit"),
            "pack_unit": _("Sold in"),
            "base_per_pack": _("Units per pack"),
            "base_unit_grams": _("Grams per unit"),
            "shelf_life_months_minimum": _("Minimum shelf life (months)"),
            "spec_reference": _("Specification reference"),
            "unicef_material_number": _("UNICEF material number"),
        }
        help_texts = {
            "slug": _("Lower case, no spaces. Every quote and contract names the product by this."),
            "base_per_pack": _("The default. A trade item may differ, and that difference is the point of items."),
            "shelf_life_months_minimum": _("Sea freight and clearance routinely eat four months of it."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The schema's enum, spelled for a reader. Kept here rather than on
        # the model because the model's own choices are the storage contract
        # and these are the wording, which changes more often.
        set_choices(
            self,
            "category",
            [
                ("", "—"),
                ("therapeutic_food", _("Therapeutic food (treats severe malnutrition)")),
                ("supplementary_food", _("Supplementary food (treats moderate)")),
                ("oral_rehydration", _("Oral rehydration")),
                ("micronutrient", _("Micronutrient")),
                ("antibiotic", _("Antibiotic")),
                ("antimalarial", _("Antimalarial")),
                ("anthelmintic", _("Anthelmintic")),
                ("diagnostic", _("Diagnostic")),
                ("equipment", _("Equipment")),
                ("consumable", _("Consumable")),
            ],
        )

        course = (self.instance.course_definition or {}) if self.instance else {}
        self.fields["base_units_per_day"].initial = course.get("base_units_per_day")
        self.fields["days_per_course"].initial = course.get("days_per_course")
        self.fields["course_source"].initial = course.get("source", "")

        self.helper.layout = Layout(
            Row(Column("slug"), Column("name"), css_class="grid md:grid-cols-[1fr,2fr] gap-x-6"),
            Field("category"),
            Fieldset(
                str(_("How it is packed")),
                Row(
                    Column("base_unit"),
                    Column("pack_unit"),
                    Column("base_per_pack"),
                    Column("base_unit_grams"),
                    css_class="grid md:grid-cols-4 gap-x-6",
                ),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("A course of treatment")),
                Row(
                    Column("base_units_per_day"),
                    Column("days_per_course"),
                    css_class="grid md:grid-cols-2 gap-x-6",
                ),
                Field("course_source"),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("The specification it is bought against")),
                Field("shelf_life_months_minimum"),
                Row(
                    Column("spec_reference"),
                    Column("unicef_material_number"),
                    css_class="grid md:grid-cols-2 gap-x-6",
                ),
                css_class="pt-2",
            ),
        )

    def clean(self):
        cleaned = super().clean()
        per_day = cleaned.get("base_units_per_day")
        days = cleaned.get("days_per_course")

        if per_day is not None and per_day <= 0:
            # The operation's QUANTITY schema refuses zero, and a course of
            # nothing a day is not a course. Said here so the message lands on
            # the field rather than arriving as a schema error.
            self.add_error("base_units_per_day", _("Has to be more than zero."))

        if per_day is not None and days is not None and (per_day * days) % 1 != 0:
            # `base_units_per_course` is a whole number of sachets: you cannot
            # ship two-thirds of one. Refused rather than rounded, because a
            # rounded course size is the invisible assumption every per-child
            # cost in this domain would then carry.
            self.add_error(
                "days_per_course",
                _("%(per_day)s a day for %(days)s days is %(total)s units — not a whole number.")
                % {"per_day": per_day, "days": days, "total": per_day * days},
            )
        return cleaned

    def payload(self) -> dict:
        course_keys = {"base_units_per_day", "days_per_course", "course_source"}
        data = to_payload({k: v for k, v in self.cleaned_data.items() if k not in course_keys})

        per_day = self.cleaned_data.get("base_units_per_day")
        days = self.cleaned_data.get("days_per_course")
        course = {}
        if per_day is not None:
            course["base_units_per_day"] = str(per_day)
        if days is not None:
            course["days_per_course"] = days
        if per_day is not None and days is not None:
            # Derived here rather than left to a reader, because every
            # per-child figure in the domain multiplies by it and two places
            # computing it is two places to disagree. `clean` has already
            # refused a pair that does not divide into whole units, so this is
            # exact -- it is never a rounded course size.
            course["base_units_per_course"] = int(per_day * days)
        if self.cleaned_data.get("course_source"):
            course["source"] = self.cleaned_data["course_source"]

        # Only when there is something to say. An empty course definition is
        # the honest starting state -- it is why per-course costs read
        # "Unconfirmed" rather than being guessed -- so writing `{}` over an
        # existing one must be a deliberate act, not the side effect of
        # editing a product's name.
        if course:
            data["course_definition"] = course
        return data


class ItemForm(KeyedUpsertForm):
    """A trade item: one manufacturer's product, packed their way.

    The layer that exists because two suppliers' RUTF can be 144 and 150 to
    the carton. Every GTIN here is check-digit validated by the operation, so
    a typo is refused rather than stored and later failing to scan.
    """

    key_field = "sku"

    class Meta:
        model = Item
        fields = [
            "sku",
            "name",
            "commodity",
            "manufacturer",
            "status",
            "base_unit",
            "pack_unit",
            "base_per_pack",
            "pack_per_case",
            "base_unit_grams",
            "shelf_life_months",
            "gtin_base",
            "gtin_pack",
            "gtin_case",
            "gpc_brick",
            "one_course_is",
            "stock_class",
            "components_per",
        ]
        widgets = {
            "components_per": forms.Select(attrs=SELECT),
            "one_course_is": forms.Select(attrs=SELECT),
            "stock_class": forms.Select(attrs=SELECT),
            "sku": forms.TextInput(attrs={**INPUT, "placeholder": _("the manufacturer's own code")}),
            "name": forms.TextInput(attrs=INPUT),
            "commodity": forms.Select(attrs=SEARCHABLE),
            "manufacturer": forms.TextInput(attrs=INPUT),
            "status": forms.Select(attrs=SELECT),
            "base_unit": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. sachet")}),
            "pack_unit": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
            "base_per_pack": forms.NumberInput(attrs={**INPUT, "min": 0, "placeholder": "150"}),
            "pack_per_case": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "base_unit_grams": forms.NumberInput(attrs={**INPUT, "min": 0, "placeholder": "92"}),
            "shelf_life_months": forms.NumberInput(attrs={**INPUT, "min": 0, "placeholder": "24"}),
            "gtin_base": forms.TextInput(attrs={**INPUT, "inputmode": "numeric"}),
            "gtin_pack": forms.TextInput(attrs={**INPUT, "inputmode": "numeric"}),
            "gtin_case": forms.TextInput(attrs={**INPUT, "inputmode": "numeric"}),
            "gpc_brick": forms.TextInput(attrs=INPUT),
        }
        labels = {
            "sku": _("SKU"),
            "name": _("Name"),
            "commodity": _("Product it is"),
            "manufacturer": _("Made by"),
            "status": _("Status"),
            "base_unit": _("Smallest unit"),
            "pack_unit": _("Sold in"),
            "base_per_pack": _("Units per pack"),
            "pack_per_case": _("Packs per case"),
            "base_unit_grams": _("Grams per unit"),
            "shelf_life_months": _("Shelf life (months)"),
            "gtin_base": _("GTIN, unit"),
            "gtin_pack": _("GTIN, pack"),
            "gtin_case": _("GTIN, case"),
            "gpc_brick": _("GPC brick"),
            "one_course_is": _("Is one of these a full course?"),
            "stock_class": _("Used up, or kept?"),
            "components_per": _("The contents below are what is in one…"),
        }
        help_texts = {
            "components_per": _(
                "A co-pack's contents are what one co-pack holds; a test kit's are what one kit holds. "
                "Only matters for a kit."
            ),
            "base_per_pack": _("As the manufacturer states it, not as the product assumes. This is the whole point."),
            "gtin_base": _("Checked against its GS1 check digit. A mistyped one is refused, not stored."),
            "one_course_is": _(
                "Say so when the manufacturer packed a whole treatment course — a three-day packet, a "
                "co-pack. Cost per course then needs no ration table."
            ),
            "stock_class": _(
                "Durable equipment — a dispenser, a scale — still has a balance at each site, but no "
                "consumption rate, months of stock or resupply quantity, which would be made-up numbers."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["commodity"].queryset = (
            Commodity.objects.filter(scope_key=self.access.scope_key).order_by("name")
            if self.access
            else Commodity.objects.none()
        )
        self.fields["commodity"].empty_label = _("Select a product…")
        set_choices(
            self,
            "status",
            [
                ("active", _("Active")),
                ("discontinued", _("Discontinued")),
            ],
        )
        set_choices(
            self,
            "one_course_is",
            [
                ("", _("No, or not known")),
                ("base_unit", _("Yes — one unit is one full course")),
                ("pack", _("Yes — one pack is one full course")),
            ],
            required=False,
        )
        set_choices(
            self,
            "components_per",
            [
                ("base", _("Smallest unit (one co-pack, one packet)")),
                ("pack", _("Pack (one kit, one carton)")),
            ],
            # Not required: a post that omits it leaves the item as it was.
            required=False,
        )
        set_choices(
            self,
            "stock_class",
            [("consumable", _("Consumable — used up")), ("durable", _("Durable — kept and moved, not consumed"))],
            # Not required: a post that omits it leaves the item as it was.
            required=False,
        )
        self.helper.layout = Layout(
            Row(Column("sku"), Column("name"), css_class="grid md:grid-cols-[1fr,2fr] gap-x-6"),
            Row(Column("commodity"), Column("manufacturer"), css_class="grid md:grid-cols-2 gap-x-6"),
            Row(Column("status"), Column("stock_class"), css_class="grid md:grid-cols-2 gap-x-6"),
            Fieldset(
                str(_("How this one is packed")),
                Row(
                    Column("base_unit"),
                    Column("pack_unit"),
                    Column("base_per_pack"),
                    Column("pack_per_case"),
                    css_class="grid md:grid-cols-4 gap-x-6",
                ),
                Row(
                    Column("base_unit_grams"),
                    Column("shelf_life_months"),
                    Column("one_course_is"),
                    css_class="grid md:grid-cols-3 gap-x-6",
                ),
                Field("components_per"),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("Barcodes")),
                Row(
                    Column("gtin_base"),
                    Column("gtin_pack"),
                    Column("gtin_case"),
                    Column("gpc_brick"),
                    css_class="grid md:grid-cols-4 gap-x-6",
                ),
                css_class="pt-2",
            ),
        )

    def payload(self) -> dict:
        data = to_payload(self.cleaned_data)
        # The operation takes the product by SLUG, not by row id: a catalogue
        # is addressed by its natural keys everywhere else in this domain, so
        # `item_upsert` is the same shape whether it arrives from a screen, a
        # tracker import or an agent. `to_payload` also leaves the `commodity_id`
        # it derived from the ModelChoiceField in place, which is harmless --
        # `upsert_item` resolves the slug and overwrites the FK with what it
        # resolved, so the slug is what decides either way.
        commodity = self.cleaned_data.get("commodity")
        if commodity is not None:
            data["commodity_slug"] = commodity.slug
        # Sent even when blank. `to_payload` drops "", which is right for a
        # field nobody answered and wrong for this one: choosing "No" on an
        # item that was a course has to reach the upsert, or the item stays a
        # course and every per-course figure keeps its old basis.
        data["one_course_is"] = self.cleaned_data.get("one_course_is") or ""
        return data


class ComponentLineForm(forms.Form):
    """One product inside a kit, and how much of it one kit holds.

    A repeating row, so a formset, for the reason a tender's lines are one:
    a co-pack is two products and a test kit can be five.
    """

    commodity_slug = forms.ChoiceField(label=_("Product inside"), widget=forms.Select(attrs=SEARCHABLE))
    quantity = forms.DecimalField(
        label=_("How many"),
        # More than nothing: `item_upsert` refuses a zero component, and the
        # refusal belongs on this field rather than in a banner.
        min_value=Decimal("0.0001"),
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs={**INPUT, "step": "any", "placeholder": "10"}),
    )
    base_unit = forms.CharField(
        label=_("Of what"),
        max_length=32,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. tablet")}),
    )
    # Which of the item's stored components this row began as, so an edit
    # keeps that component's own stated specification -- a kit may hold two
    # formulations of one product, so the product alone cannot say which.
    source_index = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, commodities=(), **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(self, "commodity_slug", [("", "—")] + list(commodities))


# No minimum: an ordinary trade item has no components, and that is the
# common case. `extra=0` for the reason TenderLineFormSet gives.
ComponentLineFormSet = forms.formset_factory(ComponentLineForm, extra=0, min_num=0, can_delete=True)


def spec_value(text):
    """A typed figure as the number it is, or the text it is.

    Requirements and stated figures are compared as decimals
    (compliance._as_decimal), so either would check; a number is kept as a
    number so the API reads 50, not "50", the way the seeders and agents
    write it.
    """
    text = (text or "").strip()
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    if not number.is_finite():
        return text
    return int(number) if number == number.to_integral_value() and "." not in text else float(number)


def figure_choices(commodities=(), items=()) -> list[tuple[str, str]]:
    """Every figure this catalogue already knows, as (name, words) to pick from.

    A requirement is only checkable against a figure some trade item states,
    so the list is what the items (and the products inside kits) state, plus
    whatever requirements already name. Nobody should have to remember that the
    range is stored as `range_max_mg_per_l`.
    """
    from connect_labs.supply_chain.procurement.services.compliance import figure_label

    names = set()
    for commodity in commodities or ():
        names.update(r.get("field") for r in commodity.get("spec_requirements") or [] if r.get("field"))
    for item in items or ():
        names.update((item.get("spec_attributes") or {}).keys())
        for component in item.get("components") or []:
            names.update((component.get("spec_attributes") or {}).keys())
    choices = [(name, figure_label(name)) for name in names if name]
    return sorted(choices, key=lambda choice: choice[1].lower())


class RequirementLineForm(forms.Form):
    """One requirement every trade item of a product is checked against.

    The rule the check runs (a figure, a comparison, a value and its unit) and
    the sentence a person reads (the rationale) -- "must still read at the
    2 mg/L dispenser dose" is what a technical partner actually wrote.

    The figure is picked from the ones the catalogue already knows, in words
    ("Range maximum (mg/L)"); a figure nothing states yet is named in the box
    beside it. The unit follows from the figure when its name says it.
    """

    field = forms.CharField(
        label=_("Figure"),
        max_length=64,
        required=False,
        widget=forms.Select(attrs=SELECT),
        help_text=_("What each trade item states."),
    )
    new_field = forms.SlugField(
        label=_("…or a new figure"),
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. tests_per_kit")}),
    )
    operator = forms.ChoiceField(label=_("Must be"), widget=forms.Select(attrs=SELECT))
    value = forms.CharField(label=_("Value"), max_length=64, widget=forms.TextInput(attrs=INPUT))
    unit = forms.CharField(
        label=_("Unit"), max_length=32, required=False, widget=forms.TextInput(attrs={**INPUT, "placeholder": "mg/L"})
    )
    rationale = forms.CharField(
        label=_("Why, in words"),
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. must still read at the 2 mg/L dose")}),
    )

    def __init__(self, *args, figures=(), **kwargs):
        super().__init__(*args, **kwargs)
        from connect_labs.supply_chain.procurement.services.compliance import OPERATORS, figure_label

        labels = {
            ">=": _("at least"),
            "<=": _("at most"),
            ">": _("more than"),
            "<": _("less than"),
            "==": _("exactly"),
        }
        set_choices(self, "operator", [(op, f"{op}  {labels.get(op, '')}") for op in OPERATORS])
        choices = list(figures)
        # The figure this row already holds stays pickable even when nothing
        # else in the catalogue states it -- an edit must not lose a requirement.
        current = (self.initial or {}).get("field") or (
            self.data.get(self.add_prefix("field")) if self.is_bound else ""
        )
        if current and current not in {name for name, _label in choices}:
            choices.append((current, figure_label(current)))
        self.fields["field"].widget.choices = [("", _("Choose a figure…"))] + choices

    def clean(self):
        cleaned = super().clean()
        from django.core.validators import validate_slug

        from connect_labs.supply_chain.procurement.services.compliance import figure_unit

        picked = (cleaned.get("field") or "").strip()
        named = (cleaned.get("new_field") or "").strip()
        if picked and named:
            self.add_error("new_field", _("Pick a figure or name a new one, not both."))
            return cleaned
        if not picked and not named:
            self.add_error("field", _("Choose the figure this requirement is about, or name a new one."))
            return cleaned
        figure = picked or named
        try:
            validate_slug(figure)
        except forms.ValidationError:
            self.add_error("field", _("Not a figure name."))
            return cleaned
        cleaned["field"] = figure
        if not (cleaned.get("unit") or "").strip():
            cleaned["unit"] = figure_unit(figure)
        return cleaned

    def requirement(self) -> dict:
        data = self.cleaned_data
        row = {"field": data["field"], "operator": data["operator"], "value": spec_value(data["value"])}
        if data.get("unit"):
            row["unit"] = data["unit"].strip()
        if data.get("rationale"):
            row["rationale"] = data["rationale"].strip()
        return row


RequirementLineFormSet = forms.formset_factory(RequirementLineForm, extra=0, min_num=0, can_delete=True)


class StatedFigureLineForm(forms.Form):
    """One figure a manufacturer states -- for the item, or a product inside it.

    What the specification check reads: the kit's own range, the reagent
    tablet's shelf life. A component's figure is held on that component, so a
    kit holding two formulations of one product cannot confuse them.
    """

    applies_to = forms.ChoiceField(label=_("Stated for"), required=False, widget=forms.Select(attrs=SELECT))
    field = forms.SlugField(
        label=_("Figure"),
        max_length=64,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. range_max_mg_per_l")}),
    )
    value = forms.CharField(label=_("Stated value"), max_length=64, widget=forms.TextInput(attrs=INPUT))

    def __init__(self, *args, targets=(), **kwargs):
        super().__init__(*args, **kwargs)
        # "item" rather than "": the item itself is a real answer, not an empty one.
        set_choices(self, "applies_to", [("item", _("This item itself"))] + list(targets), required=False)


StatedFigureLineFormSet = forms.formset_factory(StatedFigureLineForm, extra=0, min_num=0, can_delete=True)


class SupplierForm(ScopedForm):
    """A supplier: the company, and where this program has got to with it.

    Two things are edited on one screen. The company -- name, what it is,
    where it is, its Connect binding -- belongs to the organisation and is the
    same in every program that buys from it (`SupplierProfile`). The status
    and notes are this program's own. So the company fields are declared
    here rather than taken from the model, and the operation routes each to
    where it lives.

    There is a duplicate risk -- "Nutriset" and "Nutriset SAS" are one
    company -- so the create screen refuses an exact repeat and nothing else,
    because sometimes they really are two.

    `contacts` and `qualifications` are lists of objects and stay out, the
    same way `spec_requirements` does: an existing supplier's contacts survive
    an edit here untouched.
    """

    name = forms.CharField(
        label=_("Name"),
        max_length=300,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("as they write it themselves")}),
    )
    type = forms.ChoiceField(label=_("What they are"), required=False, widget=forms.Select(attrs=SELECT))
    country = forms.CharField(
        label=_("Country"),
        max_length=2,
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "NG", "maxlength": 2}),
        help_text=_("Two letters, ISO 3166 — NG, KE, FR."),
    )
    city = forms.CharField(label=_("City"), max_length=128, required=False, widget=forms.TextInput(attrs=INPUT))
    connect_organization_id = forms.IntegerField(
        label=_("Connect organisation id"),
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={**INPUT, "min": 1}),
        help_text=_(
            "Bind this supplier to a Connect organisation and their own staff can sign in "
            "and record their shipments. Leave empty and we record on their behalf."
        ),
    )

    class Meta:
        model = Supplier
        fields = ["status", "notes"]
        widgets = {
            "status": forms.Select(attrs=SELECT),
            "notes": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "status": _("Where we have got to"),
            "notes": _("Notes"),
        }
        help_texts = {
            "status": _("Moves as the relationship does. Nothing is computed from it — it is what we believe."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            for field in ("name", "type", "country", "city", "connect_organization_id"):
                self.initial.setdefault(field, getattr(self.instance, field))
        set_choices(
            self,
            "type",
            [
                ("", "—"),
                ("manufacturer", _("Manufacturer")),
                ("distributor", _("Distributor")),
                ("trader", _("Trader")),
                ("donor", _("Donor — supplies in kind, not paid for the goods")),
            ],
        )
        set_choices(
            self,
            "status",
            [
                ("identified", _("Identified — we know they exist")),
                ("contacted", _("Contacted")),
                ("responsive", _("Responsive")),
                ("quoting", _("Quoting")),
                ("awarded", _("Awarded")),
                ("declined", _("Declined")),
                ("unusable", _("Unusable")),
            ],
        )
        self.helper.layout = Layout(
            Field("name"),
            Row(Column("type"), Column("status"), css_class="grid md:grid-cols-2 gap-x-6"),
            Row(Column("country"), Column("city"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("connect_organization_id"),
            Field("notes"),
        )

    def clean_country(self):
        # Stored as two upper-case letters, so "ng" and "Ng" are the same
        # country rather than three.
        return (self.cleaned_data.get("country") or "").strip().upper()

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if name and not (self.instance and self.instance.pk):
            existing = Supplier.objects.filter(scope_key=self.access.scope_key, org__name__iexact=name).first()
            if existing is not None:
                # An exact repeat is a mistake often enough to refuse. A
                # near-match is not -- "Nutriset" and "Nutriset Nigeria" are
                # two real companies -- so only this one is an error.
                raise forms.ValidationError(
                    _("“%(name)s” is already on file. Edit that supplier instead of adding a second.")
                    % {"name": existing.name}
                )
        return name


class SupplierMarketInviteForm(forms.Form):
    """Who a marketplace invitation is for. Only a note: the link is what lets them in."""

    email = forms.EmailField(
        label=_("Their email"),
        required=False,
        widget=forms.EmailInput(attrs=INPUT),
        help_text=_("To remember who it was for. Anyone holding the link, signed in, can use it once."),
    )

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True

    def payload(self) -> dict:
        return to_payload(self.cleaned_data)
