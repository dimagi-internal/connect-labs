"""Forms for funder_dashboard. Produces dicts via to_data_dict() for the data access layer."""
import json

from django import forms

STATUS_CHOICES = [
    ("active", "Active"),
    ("closed", "Closed"),
]

_INPUT_CLASSES = (
    "w-full px-3 py-2 border border-gray-300 rounded-md text-sm "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
)
_TEXTAREA_CLASSES = (
    "w-full px-3 py-2 border border-gray-300 rounded-md text-sm "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
)
_SELECT_CLASSES = (
    "w-full px-3 py-2 border border-gray-300 rounded-md text-sm bg-white "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
)


class FundForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        required=True,
        label="Fund Name",
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. Bloomberg Neonatal Emergency Care Fund",
                "class": _INPUT_CLASSES,
            }
        ),
    )
    description = forms.CharField(
        required=False,
        label="Description",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Describe the fund's purpose, target population, and strategic goals...",
                "class": _TEXTAREA_CLASSES,
            }
        ),
    )
    total_budget = forms.IntegerField(
        required=False,
        label="Total Budget (smallest currency unit)",
        widget=forms.NumberInput(
            attrs={
                "placeholder": "e.g. 3000000",
                "class": _INPUT_CLASSES,
            }
        ),
    )
    currency = forms.CharField(
        max_length=3,
        required=False,
        initial="USD",
        label="Currency Code",
        widget=forms.TextInput(
            attrs={
                "placeholder": "USD",
                "class": _INPUT_CLASSES,
            }
        ),
    )
    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=True,
        label="Status",
        widget=forms.Select(attrs={"class": _SELECT_CLASSES}),
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
    allocations_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        label="Allocations (JSON)",
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

        raw_allocations = self.cleaned_data.get("allocations_json", "")
        if raw_allocations:
            try:
                data["allocations"] = json.loads(raw_allocations)
            except (json.JSONDecodeError, TypeError):
                data["allocations"] = []
        else:
            data["allocations"] = []

        return data
