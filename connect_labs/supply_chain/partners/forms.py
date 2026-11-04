"""Write screens for a partner's distribution plan and for who runs which place."""

from datetime import date

from crispy_forms.layout import Column, Field, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.forms import DATE, SEARCHABLE, SELECT, TEXTAREA
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
