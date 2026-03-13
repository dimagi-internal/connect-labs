"""Forms for funder_dashboard. Produces dicts via to_data_dict() for the data access layer."""
import json

from django import forms

STATUS_CHOICES = [
    ("active", "Active"),
    ("closed", "Closed"),
]


class FundForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        required=True,
        label="Fund Name",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Bloomberg Neonatal Emergency Care Fund"}),
    )
    description = forms.CharField(
        required=False,
        label="Description",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Describe the fund..."}),
    )
    total_budget = forms.IntegerField(
        required=False,
        label="Total Budget (smallest currency unit)",
        widget=forms.NumberInput(attrs={"placeholder": "e.g. 3000000"}),
    )
    currency = forms.CharField(
        max_length=3,
        required=False,
        initial="USD",
        label="Currency Code",
        widget=forms.TextInput(attrs={"placeholder": "USD"}),
    )
    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=True,
        label="Status",
    )
    program_ids_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        label="Program IDs (JSON)",
    )
    delivery_types_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        label="Delivery Types (JSON)",
    )

    def to_data_dict(self) -> dict:
        data = {
            "name": self.cleaned_data["name"],
            "description": self.cleaned_data.get("description", ""),
            "total_budget": self.cleaned_data.get("total_budget"),
            "currency": self.cleaned_data.get("currency", "USD"),
            "status": self.cleaned_data["status"],
        }
        raw_programs = self.cleaned_data.get("program_ids_json", "")
        if raw_programs:
            try:
                data["program_ids"] = json.loads(raw_programs)
            except (json.JSONDecodeError, TypeError):
                data["program_ids"] = []
        else:
            data["program_ids"] = []

        raw_types = self.cleaned_data.get("delivery_types_json", "")
        if raw_types:
            try:
                data["delivery_types"] = json.loads(raw_types)
            except (json.JSONDecodeError, TypeError):
                data["delivery_types"] = []
        else:
            data["delivery_types"] = []

        return data
