"""Minimal stubs for opportunity forms.

The full opportunity forms module (~1900 lines) was removed during labs
simplification. These stubs provide just enough for program/forms.py
(ManagedOpportunityInitForm, ManagedOpportunityInitUpdateForm) to import
and compile. The forms themselves are production functionality that is not
used in the labs environment.
"""

import json

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Field, Layout, Row, Submit
from django import forms
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import (
    CommCareApp,
    Country,
    Currency,
    HQApiKey,
    Opportunity,
    OpportunityAccess,
)
from commcare_connect.organization.models import Organization

CHECKBOX_CLASS = "simple-toggle"

DOMAIN_PLACEHOLDER_CHOICE = ("", "Select a domain")
APP_PLACEHOLDER_CHOICE = ("", "Select an app")
API_KEY_PLACEHOLDER_CHOICE = ("", "Select an API Key")


class OpportunityInitForm(forms.ModelForm):
    managed_opp = False

    currency = forms.ModelChoiceField(
        label=_("Currency"),
        queryset=Currency.objects.order_by("code"),
        widget=forms.Select(attrs={"data-tomselect": "1"}),
        empty_label=_("Select a currency"),
    )
    country = forms.ModelChoiceField(
        label=_("Country"),
        queryset=Country.objects.order_by("name"),
        widget=forms.Select(attrs={"data-tomselect": "1"}),
        empty_label=_("Select a country"),
    )

    class Meta:
        model = Opportunity
        fields = [
            "name",
            "description",
            "short_description",
            "currency",
            "country",
            "hq_server",
        ]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", {})
        self.org_slug = kwargs.pop("org_slug", "")
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(
                Column(Field("name"), Field("short_description"), Field("description")),
                Column(Field("currency"), Field("country"), Field("hq_server")),
                css_class="grid grid-cols-2 gap-4 card_bg",
            ),
            Row(
                Column(
                    Field("learn_app_domain"),
                    Field("learn_app"),
                    Field("learn_app_description"),
                    Field("learn_app_passing_score"),
                ),
                Column(Field("deliver_app_domain"), Field("deliver_app")),
                css_class="grid grid-cols-2 gap-4 card_bg my-4",
            ),
            Row(Submit("submit", "Submit", css_class="button button-md primary-dark"), css_class="flex justify-end"),
        )

        self.fields["learn_app_domain"] = forms.Field(
            widget=forms.Select(choices=[DOMAIN_PLACEHOLDER_CHOICE]),
        )
        self.fields["learn_app"] = forms.Field(
            widget=forms.Select(choices=[(None, "Loading...")]),
        )
        self.fields["learn_app_description"] = forms.CharField(
            widget=forms.Textarea(attrs={"rows": 3}),
        )
        self.fields["learn_app_passing_score"] = forms.IntegerField(max_value=100, min_value=0)
        self.fields["deliver_app_domain"] = forms.Field(
            widget=forms.Select(choices=[DOMAIN_PLACEHOLDER_CHOICE]),
        )
        self.fields["deliver_app"] = forms.Field(
            widget=forms.Select(choices=[(None, "Loading...")]),
        )
        self.fields["api_key"] = forms.Field(
            widget=forms.Select(choices=[API_KEY_PLACEHOLDER_CHOICE]),
        )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data:
            try:
                cleaned_data["learn_app"] = json.loads(cleaned_data["learn_app"])
                cleaned_data["deliver_app"] = json.loads(cleaned_data["deliver_app"])
                if cleaned_data["learn_app"]["id"] == cleaned_data["deliver_app"]["id"]:
                    self.add_error("learn_app", "Learn app and Deliver app cannot be same")
                    self.add_error("deliver_app", "Learn app and Deliver app cannot be same")
            except KeyError:
                raise forms.ValidationError("Invalid app data")
            return cleaned_data

    def _build_commcare_app(self, *, app_type, organization, hq_server, created_by, update_existing=False):
        app_data = self.cleaned_data[f"{app_type}_app"]
        domain = self.cleaned_data[f"{app_type}_app_domain"]
        defaults = {
            "name": app_data["name"],
            "created_by": created_by,
            "modified_by": self.user.email,
        }
        if app_type == "learn":
            defaults.update(
                {
                    "description": self.cleaned_data["learn_app_description"],
                    "passing_score": self.cleaned_data["learn_app_passing_score"],
                }
            )
        app, created = CommCareApp.objects.get_or_create(
            cc_app_id=app_data["id"],
            cc_domain=domain,
            organization=organization,
            hq_server=hq_server,
            defaults=defaults,
        )
        if not created and update_existing:
            app.name = app_data["name"]
            app.hq_server = hq_server
            app.modified_by = self.user.email
            update_fields = ["name", "hq_server", "modified_by"]
            if app_type == "learn":
                app.description = self.cleaned_data["learn_app_description"]
                app.passing_score = self.cleaned_data["learn_app_passing_score"]
                update_fields.extend(["description", "passing_score"])
            app.save(update_fields=update_fields)
        return app

    def save(self, commit=True):
        opportunity = super().save(commit=False)
        organization = Organization.objects.get(slug=self.org_slug)
        hq_server = self.cleaned_data["hq_server"]

        opportunity.learn_app = self._build_commcare_app(
            app_type="learn",
            organization=organization,
            hq_server=hq_server,
            created_by=self.user.email,
        )
        opportunity.deliver_app = self._build_commcare_app(
            app_type="deliver",
            organization=organization,
            hq_server=hq_server,
            created_by=self.user.email,
        )

        if not getattr(opportunity, "created_by", None):
            opportunity.created_by = self.user.email
        opportunity.modified_by = self.user.email

        if self.managed_opp:
            opportunity.organization = self.cleaned_data.get("organization")
        else:
            opportunity.organization = organization

        opportunity.api_key, _ = HQApiKey.objects.get_or_create(
            id=self.cleaned_data["api_key"],
            defaults={"hq_server": hq_server, "user": self.user},
        )

        if commit:
            opportunity.save()
        return opportunity


class OpportunityInitUpdateForm(OpportunityInitForm):
    def __init__(self, *args, **kwargs):
        self._has_existing_accesses = False
        self._disabled_fields = ()
        opportunity = kwargs.get("instance")
        if opportunity and getattr(opportunity, "pk", None):
            self._has_existing_accesses = OpportunityAccess.objects.filter(opportunity=opportunity).exists()
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        opportunity = self.instance
        if self.managed_opp and self.cleaned_data.get("organization"):
            opportunity.organization = self.cleaned_data.get("organization")

        created_by = opportunity.created_by or self.user.email
        hq_server = self.cleaned_data["hq_server"]

        opportunity.learn_app = self._build_commcare_app(
            app_type="learn",
            organization=opportunity.organization,
            hq_server=hq_server,
            created_by=created_by,
            update_existing=True,
        )
        opportunity.deliver_app = self._build_commcare_app(
            app_type="deliver",
            organization=opportunity.organization,
            hq_server=hq_server,
            created_by=created_by,
            update_existing=True,
        )
        opportunity.modified_by = self.user.email
        opportunity.api_key, _ = HQApiKey.objects.get_or_create(
            id=self.cleaned_data["api_key"],
            defaults={"hq_server": hq_server, "user": self.user},
        )
        if commit:
            opportunity.save()
        return opportunity


class OpportunityFinalizeForm(forms.ModelForm):
    class Meta:
        model = Opportunity
        fields = ["start_date", "end_date", "total_budget"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        self.budget_per_user = kwargs.pop("budget_per_user")
        self.payment_units_max_total = kwargs.pop("payment_units_max_total", 0)
        self.cumulative_pu_budget_per_user = kwargs.pop("cumulative_pu_budget_per_user", 0)
        self.opportunity = kwargs.pop("opportunity")
        self.current_start_date = kwargs.pop("current_start_date")
        import datetime

        self.is_start_date_readonly = self.current_start_date < datetime.date.today()
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.fields["max_users"] = forms.IntegerField(
            label="Max Connect Workers",
            initial=int(self.instance.number_of_users),
        )
        self.fields["start_date"].disabled = self.is_start_date_readonly
