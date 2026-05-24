from django import forms

from commcare_connect.labs.synthetic.models import SyntheticOpportunity


class SyntheticOpportunityForm(forms.ModelForm):
    """Edit/wrap a real Connect opp with a fixture-backed registry entry."""

    class Meta:
        model = SyntheticOpportunity
        fields = ["opportunity_id", "label", "gdrive_folder_id", "enabled", "notes"]
        widgets = {
            "opportunity_id": forms.HiddenInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class LabsOnlySyntheticOpportunityForm(forms.ModelForm):
    """Create or edit a labs-only synthetic opp (no real Connect opp behind it).

    opportunity_id is auto-allocated on create and never editable thereafter.
    allowed_domains is presented as a comma-separated string for ergonomics.
    """

    allowed_domains_input = forms.CharField(
        required=False,
        label="Allowed domains",
        help_text="Comma-separated email-domain suffixes (e.g. @dimagi.com, @example.org). "
        "Empty = visible to any user who has view_synthetic_opps on.",
        widget=forms.TextInput(attrs={"placeholder": "@dimagi.com"}),
    )

    class Meta:
        model = SyntheticOpportunity
        fields = [
            "label",
            "org_name",
            "program_name",
            "gdrive_folder_id",
            "enabled",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-fill the comma-separated input from the underlying ArrayField.
        if self.instance and self.instance.pk and self.instance.allowed_domains:
            self.fields["allowed_domains_input"].initial = ", ".join(self.instance.allowed_domains)
        elif not (self.instance and self.instance.pk):
            self.fields["allowed_domains_input"].initial = "@dimagi.com"

    def clean_allowed_domains_input(self) -> list[str]:
        raw = (self.cleaned_data.get("allowed_domains_input") or "").strip()
        if not raw:
            return []
        domains = [d.strip().lower() for d in raw.split(",") if d.strip()]
        for d in domains:
            if not d.startswith("@") or len(d) < 3 or "." not in d:
                raise forms.ValidationError(f"Domain {d!r} must look like '@example.com'.")
        return domains

    def save(self, commit: bool = True) -> SyntheticOpportunity:
        instance: SyntheticOpportunity = super().save(commit=False)
        instance.labs_only = True
        instance.allowed_domains = self.cleaned_data.get("allowed_domains_input") or []
        if instance.opportunity_id is None or instance.opportunity_id == 0:
            instance.opportunity_id = SyntheticOpportunity.next_labs_only_opp_id()
        if commit:
            instance.save()
        return instance
