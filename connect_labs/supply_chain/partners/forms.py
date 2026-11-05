"""Write screens for a partner's distribution plan and for who runs which place."""

from datetime import date

from crispy_forms.layout import Column, Field, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.forms import DATE, INPUT, SEARCHABLE, SELECT, TEXTAREA
from connect_labs.supply_chain.fulfilment.forms import ProvenancedForm
from connect_labs.supply_chain.models import PartnerPlan, SupplyPoint
from connect_labs.supply_chain.partners.models import DISTRIBUTION_MODEL_LABELS, DISTRIBUTION_MODELS
from connect_labs.supply_chain.partners.services import places_without_partner


class PartnerPlanForm(ProvenancedForm):
    """How a partner gets stock to its field workers, from a day on."""

    distribution_model = forms.ChoiceField(
        label=_("How the partner distributes"),
        choices=[("", _("Select…"))] + [(m, DISTRIBUTION_MODEL_LABELS[m]) for m in DISTRIBUTION_MODELS],
        widget=forms.Select(attrs=SELECT),
    )
    build_network = forms.BooleanField(
        label=_("Set up the network from this plan"),
        required=False,
        initial=True,
        help_text=_(
            "Creates the partner's warehouse if it has none and gives every place it runs a sensible minimum and "
            "maximum stock (lower nearer the field worker)."
        ),
    )
    move_workers = forms.BooleanField(
        label=_("Also move field workers to match"),
        required=False,
        help_text=_(
            "Off by default: nobody is moved unless you say so. The change of plan is recorded either way, and "
            "anyone moved is named on it."
        ),
    )

    class Meta:
        model = PartnerPlan
        fields = ["org", "distribution_model", "starts_on", "note"]
        widgets = {
            "org": forms.Select(attrs=SEARCHABLE),
            "starts_on": forms.DateInput(attrs=DATE),
            "note": forms.Textarea(attrs={**TEXTAREA, "rows": 3}),
        }
        labels = {"org": _("Partner"), "starts_on": _("From"), "note": _("Why")}
        help_texts = {
            "starts_on": _("The plan before is recorded as ending on this day, so the history is kept."),
            "note": _("For example, why the partner changed how it distributes."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["org"].queryset = LabsOrg.objects.real().order_by("name")
        self.fields["org"].empty_label = _("Select a partner…")
        for name in ("org", "distribution_model", "starts_on"):
            self.fields[name].required = True
        self.fields["starts_on"].initial = date.today()
        self.helper.layout = Layout(
            Row(Column("org"), Column("starts_on"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("distribution_model"),
            Field("build_network"),
            Field("move_workers"),
            Field("note"),
            Field("source"),
        )


class AssignPlacesForm(forms.Form):
    """Say which partner runs the places that name no one."""

    org = forms.ModelChoiceField(
        label=_("Partner"), queryset=LabsOrg.objects.none(), widget=forms.Select(attrs=SEARCHABLE)
    )
    places = forms.ModelMultipleChoiceField(
        label=_("Places it runs"),
        queryset=SupplyPoint.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Everything below a place that names no one belongs to the partner above it."),
    )

    def __init__(self, *args, access=None, **kwargs):
        from crispy_forms.helper import FormHelper

        self.access = access
        super().__init__(*args, **kwargs)
        self.fields["org"].queryset = LabsOrg.objects.real().order_by("name")
        self.fields["org"].empty_label = _("Select a partner…")
        program_id = getattr(access, "program_id", None)
        free = [p.pk for p in places_without_partner(program_id)] if program_id else []
        self.fields["places"].queryset = SupplyPoint.objects.filter(pk__in=free).order_by("name")
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(Field("org"), Field("places"))

    def payload(self) -> dict:
        return {
            "org_id": self.cleaned_data["org"].pk,
            "supply_point_ids": [p.pk for p in self.cleaned_data["places"]],
        }


def parse_copy_to(text: str) -> list:
    """'Name <email>' or a bare email, one per line (or comma separated), as [{"name", "email"}]."""
    people = []
    for chunk in (text or "").replace(",", "\n").splitlines():
        chunk = chunk.strip()
        if not chunk:
            continue
        if "<" in chunk and chunk.endswith(">"):
            name, email = chunk[:-1].split("<", 1)
        else:
            name, email = "", chunk
        people.append({"name": name.strip(), "email": email.strip()})
    return people


def copy_to_text(people) -> str:
    return "\n".join(f"{p.get('name')} <{p['email']}>" if p.get("name") else p["email"] for p in people or [])


class PartnerProfileForm(forms.Form):
    """A partner's home: who to write to, who to copy, how it usually buys and hands out stock."""

    org = forms.ModelChoiceField(
        label=_("Partner"), queryset=LabsOrg.objects.none(), widget=forms.Select(attrs=SEARCHABLE)
    )
    contact_name = forms.CharField(label=_("Main contact"), required=False, widget=forms.TextInput(attrs=INPUT))
    contact_email = forms.EmailField(
        label=_("Email"),
        required=False,
        widget=forms.EmailInput(attrs=INPUT),
        help_text=_("Award emails and handovers are addressed here."),
    )
    contact_phone = forms.CharField(label=_("Phone"), required=False, widget=forms.TextInput(attrs=INPUT))
    contact_connect_username = forms.CharField(
        label=_("Connect username"),
        required=False,
        widget=forms.TextInput(attrs=INPUT),
        help_text=_("Where alerts sent as a Connect message go."),
    )
    copy_to = forms.CharField(
        label=_("People to copy"),
        required=False,
        widget=forms.Textarea(attrs={**TEXTAREA, "rows": 3}),
        help_text=_("One per line: Name <email>. Copied on what we send the partner."),
    )
    default_buying_model = forms.ChoiceField(
        label=_("How we usually buy for this partner"),
        required=False,
        choices=[
            ("", _("Not set")),
            ("programme_buys", _("We buy and ship to the partner")),
            ("partner_buys", _("The partner buys on our quote and invoices us")),
        ],
        widget=forms.Select(attrs=SELECT),
    )
    handout_every_days = forms.IntegerField(
        label=_("Hands out stock every (days)"),
        required=False,
        min_value=1,
        max_value=365,
        widget=forms.NumberInput(attrs=INPUT),
    )
    handout_from = forms.ModelChoiceField(
        label=_("Hands out from"),
        required=False,
        queryset=SupplyPoint.objects.none(),
        widget=forms.Select(attrs=SEARCHABLE),
    )

    def __init__(self, *args, access=None, **kwargs):
        from crispy_forms.helper import FormHelper

        super().__init__(*args, **kwargs)
        self.fields["org"].queryset = LabsOrg.objects.real().order_by("name")
        self.fields["org"].empty_label = _("Select a partner…")
        program_id = getattr(access, "program_id", None)
        places = SupplyPoint.objects.filter(program_id=program_id, status="active")
        self.fields["handout_from"].queryset = places.exclude(kind__in=("user_held", "in_transit")).order_by("name")
        self.fields["handout_from"].empty_label = _("Not set")
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            Field("org"),
            Row(Column("contact_name"), Column("contact_email"), css_class="grid md:grid-cols-2 gap-x-6"),
            Row(Column("contact_phone"), Column("contact_connect_username"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("copy_to"),
            Field("default_buying_model"),
            Row(Column("handout_every_days"), Column("handout_from"), css_class="grid md:grid-cols-2 gap-x-6"),
        )

    def clean_copy_to(self):
        people = parse_copy_to(self.cleaned_data["copy_to"])
        for person in people:
            forms.EmailField().clean(person["email"])
        return people

    def payload(self) -> dict:
        data = self.cleaned_data
        return {
            "org_id": data["org"].pk,
            "contact_name": data["contact_name"],
            "contact_email": data["contact_email"],
            "contact_phone": data["contact_phone"],
            "contact_connect_username": data["contact_connect_username"],
            "copy_to": data["copy_to"],
            "default_buying_model": data["default_buying_model"],
            "handout_every_days": data["handout_every_days"],
            "handout_from_id": data["handout_from"].pk if data["handout_from"] else None,
        }
