"""Write screens for the network tier: organisations and supply points.

Two registries that everything below the contract depends on, and neither had
a screen.

**An organisation is labs-wide, not programme-scoped.** `LabsOrg` is one
registry for the whole of labs — the correction for three tables that all
meant "an organisation in real life" and drifted apart. So these forms do not
filter by `scope_key`, and the screens say so: editing an organisation here
changes it in every programme it appears in.

**A supply point is anywhere stock can rest**, including a field worker's own
holding. `kind="user_held"` is the ruling the whole stock model rests on, and
the model refuses such a point that names no Connect user — because nothing
could ever post stock to it. `SupplyPointForm` asks for that user in the same
breath as the kind, so the refusal arrives as a field error rather than as a
validation exception from three layers down.

**`source` is a real question, not a hidden default.** Every model in this
tier inherits `SourcedModel`, whose `source` has no default so a record cannot
read as first-hand by omission. `stamp_provenance` fills it in — but the
schema validates *before* stamping, so an operation that requires `source` (as
`supply_point_upsert` does) refuses a payload without one. Rather than have a
view re-derive provenance, which is the one thing this domain centralises, the
screen asks: "How do you know?" That is the honest question anyway.
"""

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Field, Fieldset, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.forms import INPUT, SEARCHABLE, SELECT, ScopedForm, set_choices, to_payload
from connect_labs.supply_chain.models import SupplyPoint

__all__ = ["OrgForm", "OrgMergeForm", "SupplyPointForm"]


# What a person can honestly claim about a record they are creating on a
# screen. The other three sources -- commcare_form, connect_visit, document --
# are stamped by the thing that produced them and are not a person's to pick.
SOURCE_CHOICES = [
    ("we_recorded", _("We set this up ourselves")),
    ("partner_reported", _("A partner told us")),
    ("supplier_reported", _("A supplier told us")),
]


class OrgForm(ScopedForm):
    """An organisation, by slug.

    `aliases` stays out. It is the list a person curates when no string rule
    can reach a slug — pulse learned that a wrong parent name is worse than a
    visible slug — and it is not something to retype into a textarea while
    editing a country code.
    """

    class Meta:
        model = LabsOrg
        fields = ["slug", "name", "short_name", "country", "connect_organization_id", "connect_organization_slug"]
        widgets = {
            "slug": forms.TextInput(attrs={**INPUT, "placeholder": "nutriset"}),
            "name": forms.TextInput(attrs=INPUT),
            "short_name": forms.TextInput(attrs=INPUT),
            "country": forms.TextInput(attrs={**INPUT, "placeholder": "FR", "maxlength": 2}),
            "connect_organization_id": forms.NumberInput(attrs={**INPUT, "min": 1}),
            "connect_organization_slug": forms.TextInput(attrs=INPUT),
        }
        labels = {
            "slug": _("Short key"),
            "name": _("Name"),
            "short_name": _("Short name"),
            "country": _("Country"),
            "connect_organization_id": _("Connect organisation id"),
            "connect_organization_slug": _("Connect organisation slug"),
        }
        help_texts = {
            "slug": _("Unique across the whole of labs — this is one registry, not one per programme."),
            "country": _("Two letters, ISO 3166."),
            "connect_organization_id": _(
                "The identity, once Connect has one. Setting it is what lets that partner's own "
                "staff sign in and record their shipments, receipts and counts."
            ),
            "connect_organization_slug": _(
                "For an organisation Connect knows only by slug — a labs-synthetic one. A finding "
                "aid, not the identity."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # An organisation is located by slug when it has no Connect id, so
            # changing one mid-life is how a row goes missing. Editable only
            # before it exists.
            self.fields["slug"].disabled = True
            self.fields["slug"].help_text = _("Fixed once set — other rows find this organisation by it.")
        self.helper.layout = Layout(
            Row(Column("slug"), Column("name"), css_class="grid md:grid-cols-[1fr,2fr] gap-x-6"),
            Row(Column("short_name"), Column("country"), css_class="grid md:grid-cols-2 gap-x-6"),
            Fieldset(
                str(_("How it joins to Connect")),
                Row(
                    Column("connect_organization_id"),
                    Column("connect_organization_slug"),
                    css_class="grid md:grid-cols-2 gap-x-6",
                ),
                css_class="pt-2",
            ),
        )

    def clean_country(self):
        return (self.cleaned_data.get("country") or "").strip().upper()

    def clean_slug(self):
        slug = self.cleaned_data.get("slug")
        if slug and not (self.instance and self.instance.pk) and LabsOrg.objects.filter(slug=slug).exists():
            # `org_upsert` is an upsert keyed on slug, so this would have
            # rewritten an organisation rather than failing.
            raise forms.ValidationError(
                _("“%(name)s” already uses that key. Saving would overwrite it — edit that instead.")
                % {"name": LabsOrg.objects.get(slug=slug).name}
            )
        return slug


class OrgMergeForm(forms.Form):
    """Fold one organisation row into another.

    Destructive and irreversible: every reference moves, the merged-away slug
    survives as an alias, and the empty row is deleted. So the form asks which
    one SURVIVES first and names both in the confirmation, rather than
    offering two lookalike pickers and hoping.

    The operation refuses a merge of two rows linked to different Connect
    organisations, and that refusal is the point — two Connect ids means two
    organisations, whatever the names look like.
    """

    keep = forms.ModelChoiceField(
        label=_("Keep this one"),
        queryset=LabsOrg.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_("Everything ends up here. Its name, country and Connect binding survive."),
    )
    merge = forms.ModelChoiceField(
        label=_("Fold in this one"),
        queryset=LabsOrg.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_("This row is deleted. Its slug is kept as an alias so old references still resolve."),
    )

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        for name in ("keep", "merge"):
            self.fields[name].queryset = LabsOrg.objects.order_by("name")
            self.fields[name].empty_label = _("Select an organisation…")
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            Row(Column("keep"), Column("merge"), css_class="grid md:grid-cols-2 gap-x-6"),
        )

    # No self-merge check here. `merge_orgs` already refuses one, and the
    # refusal reaches the page as a non-field error through the same path every
    # other operation refusal takes. A second copy of the rule in the form
    # would be a second place for it to drift from the domain's.

    def payload(self) -> dict:
        return {"keep_id": self.cleaned_data["keep"].pk, "merge_id": self.cleaned_data["merge"].pk}


class SupplyPointForm(ScopedForm):
    """Anywhere stock can rest, including a field worker's own holding.

    Model validation arrives free: ModelForm's `_post_clean` runs the
    instance's `full_clean`, so `SupplyPoint.clean`'s rule -- that a
    `user_held` point must name the Connect user whose stock it is, because
    nothing could ever post stock to one that does not -- reaches the
    `connect_username` field as a field error without this form restating it.
    """

    source = forms.ChoiceField(
        label=_("How do you know?"),
        choices=SOURCE_CHOICES,
        initial="we_recorded",
        widget=forms.Select(attrs=SELECT),
        help_text=_("Recorded with the row. A record that does not say how it was known is weaker, not stronger."),
    )

    class Meta:
        model = SupplyPoint
        fields = [
            "slug",
            "name",
            "kind",
            "parent",
            "managed_by_org",
            "opportunity_id",
            "connect_username",
            "admin_area",
            "latitude",
            "longitude",
            "min_months_of_stock",
            "max_months_of_stock",
            "status",
        ]
        widgets = {
            "slug": forms.TextInput(attrs={**INPUT, "placeholder": "central-store-kano"}),
            "name": forms.TextInput(attrs={**INPUT, "placeholder": _("Central store, Kano")}),
            "kind": forms.Select(attrs=SELECT),
            "parent": forms.Select(attrs=SEARCHABLE),
            "managed_by_org": forms.Select(attrs=SEARCHABLE),
            "opportunity_id": forms.NumberInput(attrs={**INPUT, "min": 1}),
            "connect_username": forms.TextInput(attrs=INPUT),
            "admin_area": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. Kano State")}),
            "latitude": forms.NumberInput(attrs={**INPUT, "step": "any"}),
            "longitude": forms.NumberInput(attrs={**INPUT, "step": "any"}),
            "min_months_of_stock": forms.NumberInput(attrs={**INPUT, "step": "any", "placeholder": "1"}),
            "max_months_of_stock": forms.NumberInput(attrs={**INPUT, "step": "any", "placeholder": "3"}),
            "status": forms.Select(attrs=SELECT),
        }
        labels = {
            "slug": _("Short key"),
            "name": _("Name"),
            "kind": _("What it is"),
            "parent": _("Resupplied from"),
            "managed_by_org": _("Run by"),
            "opportunity_id": _("Opportunity"),
            "connect_username": _("Connect username"),
            "admin_area": _("Where"),
            "latitude": _("Latitude"),
            "longitude": _("Longitude"),
            "min_months_of_stock": _("Minimum months of stock"),
            "max_months_of_stock": _("Maximum months of stock"),
            "status": _("Status"),
        }
        help_texts = {
            "parent": _("Leave empty for a point at the top of the network."),
            "opportunity_id": _("Set for a point that belongs to one opportunity. Every worker's holding does."),
            "connect_username": _(
                "Required for a worker's own holding — without it nothing can ever be posted to it."
            ),
            "min_months_of_stock": _("Resupply reads this band rather than hardcoding a rule."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        program_id = getattr(self.access, "program_id", None) if self.access else None
        points = SupplyPoint.objects.filter(program_id=program_id) if program_id else SupplyPoint.objects.none()
        if self.instance and self.instance.pk:
            # A point cannot be resupplied from itself. The tree is enforced by
            # the FK's PROTECT, not by a cycle check, so the least a picker can
            # do is not offer the obvious one.
            points = points.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = points.order_by("name")
        self.fields["parent"].empty_label = _("Nothing above it")

        # Organisations are labs-wide, so this queryset is deliberately not
        # scoped — see the module docstring.
        self.fields["managed_by_org"].queryset = LabsOrg.objects.order_by("name")
        self.fields["managed_by_org"].empty_label = _("Not recorded")

        set_choices(
            self,
            "kind",
            [
                ("central_store", _("Central store")),
                ("regional_store", _("Regional store")),
                ("facility", _("Facility")),
                ("user_held", _("A field worker's own holding")),
                ("supplier_site", _("Supplier site")),
                ("in_transit", _("In transit")),
                ("customs", _("Customs")),
            ],
        )
        set_choices(self, "status", [("active", _("Active")), ("inactive", _("Inactive"))])

        if self.instance and self.instance.pk:
            self.fields["slug"].disabled = True
            self.fields["slug"].help_text = _("Fixed once set — a new key would create a second point.")
            self.fields["source"].initial = self.instance.source or "we_recorded"

        self.helper.layout = Layout(
            Row(Column("slug"), Column("name"), css_class="grid md:grid-cols-[1fr,2fr] gap-x-6"),
            Row(Column("kind"), Column("status"), css_class="grid md:grid-cols-2 gap-x-6"),
            Fieldset(
                str(_("Whose it is")),
                Row(
                    Column("parent"),
                    Column("managed_by_org"),
                    css_class="grid md:grid-cols-2 gap-x-6",
                ),
                Row(
                    Column("opportunity_id"),
                    Column("connect_username"),
                    css_class="grid md:grid-cols-2 gap-x-6",
                ),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("Where it is")),
                Row(
                    Column("admin_area"),
                    Column("latitude"),
                    Column("longitude"),
                    css_class="grid md:grid-cols-[2fr,1fr,1fr] gap-x-6",
                ),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("How much it should hold")),
                Row(
                    Column("min_months_of_stock"),
                    Column("max_months_of_stock"),
                    css_class="grid md:grid-cols-2 gap-x-6",
                ),
                css_class="pt-2",
            ),
            Field("source"),
        )

    def clean(self):
        # No `user_held` check here, though there was one. `SupplyPoint.clean`
        # already refuses a worker's holding that names no Connect user, and
        # ModelForm's `_post_clean` runs `full_clean` on the instance and
        # attaches a dict-keyed ValidationError to the field it names -- so the
        # error was already landing on `connect_username` without help. Deleting
        # the copy was the whole fix; mutation testing found it by removing the
        # copy and watching nothing go red.
        cleaned = super().clean()
        low, high = cleaned.get("min_months_of_stock"), cleaned.get("max_months_of_stock")
        if low is not None and high is not None and low > high:
            self.add_error("max_months_of_stock", _("Has to be at least the minimum."))
        return cleaned

    def payload(self) -> dict:
        data = to_payload(self.cleaned_data)
        # One rename. `to_payload` turns a model instance into `<field>_id`,
        # which already matches the schema for `managed_by_org_id` but not for
        # the parent: the schema calls that `parent_supply_point_id`, because
        # "parent" alone does not say parent of what once the payload has left
        # the form.
        #
        # `pop`, not a copy. Both spellings happen to reach the same column
        # today -- `_columns` passes `parent_id` straight through and which of
        # the two wins is Django kwarg-ordering, not a documented rule. Sending
        # one key means not depending on that. TestThePayloadShape pins it,
        # because a database round-trip cannot tell the two apart.
        if "parent_id" in data:
            data["parent_supply_point_id"] = data.pop("parent_id")
        return data
