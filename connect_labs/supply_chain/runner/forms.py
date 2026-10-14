"""The form behind /supply/runner/: platform support declares who runs a programme."""

from crispy_forms.helper import FormHelper
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.forms import SEARCHABLE


class _OrgChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} (labs only)" if obj.labs_only else obj.name


class ProgrammeRunnerForm(forms.Form):
    org = _OrgChoice(
        label=_("Organisation that runs this program"),
        queryset=LabsOrg.objects.order_by("name"),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_("Its members tender, award, order and pay for the program. Everyone else who can see it reads."),
    )
    note = forms.CharField(
        label=_("Why"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Kept with the declaration, e.g. who asked for it."),
    )

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True

    def payload(self) -> dict:
        return {"org_id": self.cleaned_data["org"].pk, "note": self.cleaned_data.get("note") or ""}
