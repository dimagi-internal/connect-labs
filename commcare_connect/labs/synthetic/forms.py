from django import forms

from commcare_connect.labs.synthetic.models import SyntheticOpportunity


class SyntheticOpportunityForm(forms.ModelForm):
    class Meta:
        model = SyntheticOpportunity
        fields = ["opportunity_id", "label", "gdrive_folder_id", "enabled", "notes"]
        widgets = {
            "opportunity_id": forms.HiddenInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
